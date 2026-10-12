"""The guard on paid calls: the opt-in, the estimate and the spend summary. Nothing here calls a paid API."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService

from clinic_agent.cost import Ledger, TokenUsage, call_cost, token_usage
from clinic_agent.paid_calls import OPT_IN, PaidCallsNotAllowed, require_opt_in

AGENT_DIR = Path(__file__).resolve().parents[1]


def test_the_ledger_sums_calls_tokens_and_dollars_per_model():
    ledger = Ledger()
    ledger.add(
        "claude-haiku-4-5", TokenUsage(input_tokens=10_000, output_tokens=1_000, cache_read_tokens=5_000), calls=3
    )
    ledger.add("claude-haiku-4-5", TokenUsage(input_tokens=2_000, output_tokens=200), calls=1)
    ledger.add("google/gemini-3.6-flash", TokenUsage(input_tokens=4_000, output_tokens=400), calls=2)

    # Haiku 4.5: 12,000 in at $1, 1,200 out at $5, 5,000 cached at $0.10, per million. Gemini: $0.75 and $3.75.
    assert ledger.dollars() == pytest.approx(0.012 + 0.006 + 0.0005 + 0.003 + 0.0015)
    assert ledger.summary() == (
        "Paid LLM calls: claude-haiku-4-5 4 calls, 17,000 tokens in (5,000 cached), 1,200 out, $0.0185; "
        "google/gemini-3.6-flash 2 calls, 4,000 tokens in (0 cached), 400 out, $0.0045; total $0.0230"
    )


def test_the_ledger_counts_cache_writes_as_input_tokens_and_prices_them():
    ledger = Ledger()
    ledger.add("claude-haiku-4-5", TokenUsage(input_tokens=1_000, cache_write_tokens=2_000))

    assert "3,000 tokens in (0 cached)" in ledger.summary()
    assert ledger.dollars() == pytest.approx(0.001 + 0.0025)


def test_a_model_without_a_price_shows_tokens_and_no_dollars_and_the_total_says_so():
    ledger = Ledger()
    ledger.add("claude-haiku-4-5", TokenUsage(input_tokens=1_000))
    ledger.add("some-new-model", TokenUsage(input_tokens=500, output_tokens=50))

    assert ledger.dollars() is None
    assert ledger.summary() == (
        "Paid LLM calls: claude-haiku-4-5 1 calls, 1,000 tokens in (0 cached), 0 out, $0.0010; "
        "some-new-model 1 calls, 500 tokens in (0 cached), 50 out, n/a; total n/a"
    )


def test_an_empty_ledger_has_nothing_to_report():
    assert Ledger().summary() == "Paid LLM calls: none"
    assert Ledger().dollars() == 0


def test_usage_reported_by_an_anthropic_service_adds_up_without_double_counting_the_cache():
    reported = [
        LLMTokenUsage(
            prompt_tokens=700,
            completion_tokens=20,
            cache_read_input_tokens=300,
            cache_creation_input_tokens=50,
            total_tokens=720,
        ),
        LLMTokenUsage(prompt_tokens=100, completion_tokens=5, total_tokens=105),
    ]
    llm = AnthropicLLMService(api_key="unused")

    assert token_usage(llm, reported) == TokenUsage(
        input_tokens=800, output_tokens=25, cache_read_tokens=300, cache_write_tokens=50
    )


def test_usage_reported_by_an_openai_compatible_service_counts_cached_input_once():
    reported = [
        LLMTokenUsage(prompt_tokens=1_000, completion_tokens=20, cache_read_input_tokens=300, total_tokens=1_020)
    ]
    llm = OpenRouterLLMService(api_key="unused")

    assert token_usage(llm, reported) == TokenUsage(input_tokens=700, output_tokens=20, cache_read_tokens=300)


def test_usages_add_up():
    usage = TokenUsage(input_tokens=1_000_000) + TokenUsage(output_tokens=1_000_000)
    assert call_cost("claude-haiku-4-5", usage) == pytest.approx(6.0)


def test_without_the_opt_in_the_guard_refuses_and_says_how_to_opt_in_and_what_it_would_cost():
    with pytest.raises(PaidCallsNotAllowed) as refusal:
        require_opt_in({}, what="pytest --llm haiku", estimate="about $0.40 for 12 tests")

    message = str(refusal.value)
    assert "pytest --llm haiku" in message
    assert "about $0.40 for 12 tests" in message
    assert f"{OPT_IN}=1" in message


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "2", " 1"])
def test_only_the_exact_opt_in_value_lets_paid_calls_through(value):
    with pytest.raises(PaidCallsNotAllowed):
        require_opt_in({OPT_IN: value}, what="x", estimate="y")


def test_with_the_opt_in_the_guard_lets_the_run_start():
    require_opt_in({OPT_IN: "1"}, what="x", estimate="y")


def run_pytest(*args: str) -> subprocess.CompletedProcess:
    """Runs this suite's pytest in a child process, with the opt-in removed from its environment."""
    env = {k: v for k, v in os.environ.items() if k != OPT_IN} | {"PYTHONUTF8": "1"}  # pipecat's banner isn't cp1252
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=AGENT_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def test_pytest_with_a_real_llm_exits_at_startup_without_the_opt_in():
    # The EHR is not needed to get this far: the run must end before any fixture, test or network call.
    result = run_pytest("--llm", "haiku", "tests/test_paid_calls.py")

    assert result.returncode == 4
    assert f"{OPT_IN}=1" in result.stderr
    assert "haiku" in result.stderr
    assert "passed" not in result.stdout and "failed" not in result.stdout


def test_pytest_with_the_scripted_llm_needs_no_opt_in():
    result = run_pytest("--llm", "scripted", "--collect-only", "-q", "tests/test_paid_calls.py")

    assert result.returncode == 0, result.stdout + result.stderr
