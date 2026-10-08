"""Conversation tests drive the real flow through the text transport, against the real adapter and HAPI.

Bring the local EHR up first (see README). FHIR_BASE_URL and EHR_ADAPTER_URL point the tests at it.
By default a scripted fake plays the LLM. `pytest --llm haiku` (or any named LLM config) runs the same
calls against that real model instead, with its API key in the environment.
"""

import os
import time

import httpx
import pytest
from ehr import Ehr
from fakes import ScriptedLLM

from clinic_agent.conversation import ROLE
from clinic_agent.ehr import EhrAdapter
from clinic_agent.llm import create_llm
from clinic_agent.text_call import TextCall

FHIR_BASE_URL = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir").rstrip("/")
EHR_ADAPTER_URL = os.environ.get("EHR_ADAPTER_URL", "http://localhost:3000").rstrip("/")
EHR_READY_WITHIN_SECS = 180


def pytest_addoption(parser):
    parser.addoption(
        "--llm",
        default="scripted",
        help="LLM for conversation tests: 'scripted' (the fake, default) or a named LLM config such as haiku",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "scripted_only: needs the scripted fake LLM to force a move a real one wouldn't make"
    )


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


@pytest.fixture
def ehr(_ehr_ready):
    records = Ehr(FHIR_BASE_URL)
    yield records
    records.delete_created_records()


@pytest.fixture
def start_call(_ehr_ready, llm_config):
    """start_call(script) opens a TextCall. The script is what the fake LLM does, one step per LLM run.

    With --llm set, the script is ignored and the named model decides instead.
    """

    def start(script: list) -> TextCall:
        if llm_config == "scripted":
            return TextCall(ScriptedLLM(script), EhrAdapter(EHR_ADAPTER_URL))
        llm = create_llm({**os.environ, "LLM_CONFIG": llm_config}, system_instruction=ROLE)
        return TextCall(llm, EhrAdapter(EHR_ADAPTER_URL), reply_timeout_secs=30)

    return start
