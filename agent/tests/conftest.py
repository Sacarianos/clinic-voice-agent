"""Conversation tests drive the real flow through the text transport, against the real adapter and HAPI.

Bring the local EHR up first, as the README says. FHIR_BASE_URL and EHR_ADAPTER_URL point the tests at it.
By default a scripted fake plays the LLM. `pytest --llm haiku`, or any other named LLM config, runs the
same calls against that real model instead, with its API key in the environment. That spends real money,
so it refuses to start without ALLOW_PAID_CALLS=1, prints an estimate before the first call and a spend summary
at the end.
"""

import os
import random
import time

import httpx
import pytest
from ehr import Ehr
from fakes import ScriptedLLM
from faulty_adapter import FaultyAdapter
from langfuse_stand_in import FakeLangfuse
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from clinic_agent.audit import AuditLog
from clinic_agent.conversation import ROLE
from clinic_agent.cost import Ledger, TokenUsage, call_cost, token_usage
from clinic_agent.ehr import EhrAdapter
from clinic_agent.llm import LLM_CONFIGS, create_llm
from clinic_agent.paid_calls import PaidCallsNotAllowed, require_opt_in
from clinic_agent.text_call import TextCall
from clinic_agent.tracing import configure_tracing

FHIR_BASE_URL = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir").rstrip("/")
EHR_ADAPTER_URL = os.environ.get("EHR_ADAPTER_URL", "http://localhost:3000").rstrip("/")
EHR_READY_WITHIN_SECS = 180
# A rough, generous guess at what one conversation test uses with a real model, to put a number on the
# opt-in prompt. Update it from the spend summary a real run prints.
ESTIMATED_USAGE_PER_TEST = TokenUsage(input_tokens=25_000, output_tokens=1_500)
SPEND = Ledger()


def pytest_addoption(parser):
    parser.addoption(
        "--llm",
        default="scripted",
        help="LLM for conversation tests: 'scripted' (the fake, default) or a named LLM config such as haiku",
    )


def _estimated_dollars_per_test(llm_config: str) -> float | None:
    known = LLM_CONFIGS.get(llm_config)
    return call_cost(known.model, ESTIMATED_USAGE_PER_TEST) if known else None


def _describe(dollars: float | None) -> str:
    if dollars is None:
        return "an unknown amount"
    return f"about ${dollars:.2f}" if dollars >= 0.1 else f"about ${dollars:.4f}"


def pytest_configure(config):
    llm_config = config.getoption("--llm")
    if llm_config != "scripted":
        per_test = _estimated_dollars_per_test(llm_config)
        try:
            require_opt_in(
                os.environ,
                what=f"pytest --llm {llm_config}",
                estimate=f"{_describe(per_test)} for each conversation test it runs",
            )
        except PaidCallsNotAllowed as refusal:
            raise pytest.UsageError(str(refusal)) from None
    config.addinivalue_line(
        "markers", "scripted_only: needs the scripted fake LLM to force a move a real one wouldn't make"
    )


def pytest_collection_finish(session):
    llm_config = session.config.getoption("--llm")
    if llm_config == "scripted":
        return
    tests = [item for item in session.items if not item.get_closest_marker("scripted_only")]
    per_test = _estimated_dollars_per_test(llm_config)
    total = None if per_test is None else per_test * len(tests)
    session.config.get_terminal_writer().line(
        f"Paid calls: {len(tests)} tests against {llm_config}, estimated cost {_describe(total)}.", yellow=True
    )


def pytest_terminal_summary(terminalreporter, config):
    if config.getoption("--llm") != "scripted":
        terminalreporter.write_line(SPEND.summary())


@pytest.fixture(scope="session")
def llm_config(request) -> str:
    return request.config.getoption("--llm")


@pytest.fixture(autouse=True)
def _skip_scripted_only_with_a_real_llm(request, llm_config):
    if request.node.get_closest_marker("scripted_only") and llm_config != "scripted":
        pytest.skip("drives the scripted fake LLM")


@pytest.fixture(scope="session")
def _ehr_ready():
    """Waits for HAPI and the adapter, so a cold container doesn't fail the first test."""
    deadline = time.monotonic() + EHR_READY_WITHIN_SECS
    pending = {"HAPI": f"{FHIR_BASE_URL}/metadata?_summary=true", "the EHR adapter": f"{EHR_ADAPTER_URL}/healthz"}
    while pending:
        for name, url in list(pending.items()):
            try:
                if httpx.get(url, timeout=5).is_success:
                    del pending[name]
            except httpx.TransportError:
                pass
        if pending and time.monotonic() > deadline:
            pytest.fail(
                f"{' and '.join(pending)} not reachable ({', '.join(pending.values())}). "
                "Run: docker compose -f infra/compose.yaml up -d --wait --build, and set FHIR_BASE_URL "
                "and EHR_ADAPTER_URL if they don't listen on ports 8080 and 3000.",
                pytrace=False,
            )
        if pending:
            time.sleep(2)


@pytest.fixture(scope="session", autouse=True)
def _audit_log_path(tmp_path_factory):
    """Keeps every test's audit log entries out of the working directory. Tests tell theirs apart by patient id."""
    path = tmp_path_factory.mktemp("audit") / "audit-log.jsonl"
    os.environ["AUDIT_LOG_PATH"] = str(path)
    yield
    del os.environ["AUDIT_LOG_PATH"]


@pytest.fixture
def audit_log() -> AuditLog:
    return AuditLog.from_env(os.environ)


@pytest.fixture(scope="session")
def langfuse():
    """Traces every call that turns tracing on to a local stand-in for Langfuse Cloud.

    OpenTelemetry's tracer provider is process-wide and can be installed only once, so every test that
    traces shares this one.
    """
    server = FakeLangfuse()
    env = {"LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test", "LANGFUSE_BASE_URL": server.url}
    assert configure_tracing(env) is True
    yield server
    # Flush and stop the exporter while the server can still answer it.
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)  # configure_tracing installed the SDK's
    provider.shutdown()
    server.close()


@pytest.fixture
def ehr_adapter_url(_ehr_ready) -> str:
    return EHR_ADAPTER_URL


@pytest.fixture
async def faulty_adapter(ehr_adapter_url):
    """Sits in front of the adapter. Point start_call at `faulty_adapter.url` and inject faults into it."""
    adapter = FaultyAdapter(ehr_adapter_url)
    await adapter.start()
    yield adapter
    await adapter.close()


@pytest.fixture
def ehr(_ehr_ready):
    records = Ehr(FHIR_BASE_URL)
    yield records
    records.delete_created_records()


@pytest.fixture
def start_call(ehr, llm_config):
    """start_call(script) opens a TextCall. The script is what the fake LLM does, one step per LLM run.

    With --llm set, the script is ignored and the named model decides instead. Each call comes from its
    own phone number, so a test finds its Callback Requests by `call.caller_phone`. They are deleted
    afterwards, before the Patients they point to. With caller_id=False the call carries no number, as a
    browser call doesn't, and the test's Caller says `caller_phone` when asked for one.
    """
    phones = []
    real_calls = []

    def start(
        script: list,
        *,
        adapter_url: str = EHR_ADAPTER_URL,
        caller_phone: str | None = None,
        caller_id: bool = True,
        tracing: bool = False,
    ) -> TextCall:
        phone = caller_phone or f"+1555{random.randrange(10**7):07d}"
        phones.append(phone)
        caller_id_phone = phone if caller_id else None
        if llm_config == "scripted":
            return TextCall(ScriptedLLM(script), EhrAdapter(adapter_url), caller_phone=caller_id_phone, tracing=tracing)
        llm = create_llm({**os.environ, "LLM_CONFIG": llm_config}, system_instruction=ROLE)
        call = TextCall(
            llm, EhrAdapter(adapter_url), caller_phone=caller_id_phone, reply_timeout_secs=30, tracing=tracing
        )
        real_calls.append((llm, call))
        return call

    yield start
    for llm, call in real_calls:
        SPEND.add(LLM_CONFIGS[llm_config].model, token_usage(llm, call.llm_usage), calls=len(call.llm_usage))
    for phone in phones:
        ehr.delete_callback_requests_from(phone)
