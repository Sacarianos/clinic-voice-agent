"""The guard on paid eval batches: the opt-in, the estimate and the dollar limit. Nothing here calls a paid API.

A scripted Caller and a scripted LLM report the usage a real one would, so the accounting runs against known numbers.
"""

import json

import pytest
from pipecat.metrics.metrics import LLMTokenUsage

from clinic_agent.cost import TokenUsage
from clinic_agent.llm import LLM_CONFIGS, LLMConfig
from clinic_agent.paid_calls import OPT_IN
from clinic_agent.scripted_llm import CallTool, ScriptedLLM
from clinic_evals import cli
from clinic_evals.caller import ScriptedCaller
from clinic_evals.evals import run_evals
from clinic_evals.scenario import SCENARIOS_DIR, load_scenario


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to build a client or start a run fails the test: the guard must stop first."""

    def forbidden(*args, **kwargs):
        raise AssertionError("a paid run started")

    monkeypatch.setattr(cli, "run_evals", forbidden)
    monkeypatch.setattr(cli, "AsyncAnthropic", forbidden)
    monkeypatch.setattr(cli, "create_llm", forbidden)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")


def test_without_the_opt_in_a_batch_exits_before_any_call_and_says_how_to_opt_in(no_network, monkeypatch, capsys):
    monkeypatch.delenv(OPT_IN, raising=False)

    assert cli.main(["--config", "haiku"]) == 2

    err = capsys.readouterr().err
    assert f"{OPT_IN}=1" in err
    assert "clinic-evals --config haiku" in err
    assert "about $" in err


def test_a_report_needs_no_opt_in(no_network, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(OPT_IN, raising=False)

    assert cli.main(["--report", "--results-dir", str(tmp_path)]) == 0


def test_with_the_opt_in_the_estimate_is_printed_before_the_first_run(monkeypatch, capsys):
    monkeypatch.setenv(OPT_IN, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    started = []

    async def first_run(*args, **kwargs):
        started.append(capsys.readouterr().out)
        return []

    monkeypatch.setattr(cli, "run_evals", first_run)

    assert cli.main(["--config", "haiku", "--scenario", "plain_book", "--repeats", "2"]) == 0

    [before_the_first_run] = started
    assert "Estimated cost" in before_the_first_run
    assert "2 runs" in before_the_first_run


def test_max_dollars_is_passed_on_and_must_be_positive(monkeypatch, capsys):
    monkeypatch.setenv(OPT_IN, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    seen = {}

    async def fake_run_evals(*args, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(cli, "run_evals", fake_run_evals)

    assert cli.main(["--config", "haiku", "--scenario", "plain_book", "--max-dollars", "1.5"]) == 0
    assert seen["max_dollars"] == 1.5
    with pytest.raises(SystemExit):
        cli.main(["--config", "haiku", "--max-dollars", "0"])


def test_max_dollars_is_refused_for_a_config_with_no_price(monkeypatch, capsys):
    monkeypatch.setenv(OPT_IN, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setitem(
        LLM_CONFIGS, "unpriced", LLMConfig(LLM_CONFIGS["haiku"].service, "model-with-no-price", "ANTHROPIC_API_KEY")
    )

    with pytest.raises(SystemExit):
        cli.main(["--config", "unpriced", "--max-dollars", "1"])

    assert "model-with-no-price" in capsys.readouterr().err


class CallerThatSpends(ScriptedCaller):
    """A scripted Caller that reports what a simulated one would have used."""

    def __init__(self, lines: list[str], usage: TokenUsage):
        super().__init__(lines)
        self.usage = usage


def handoff_agent(usage: LLMTokenUsage | None = None):
    return lambda seeded: ScriptedLLM([CallTool("handoff", {"reason": "asked_for_person"})], usage=usage)


async def test_a_batch_stops_when_the_callers_spending_reaches_the_limit_and_saves_what_ran(ehr_urls, tmp_path, capsys):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    # Claude Haiku 4.5 plays the Caller at $1 per million input tokens: one million is $1 a run.
    caller = lambda scenario, seeded: CallerThatSpends(["Can I talk to a real person, please?"], TokenUsage(input_tokens=1_000_000))

    results = await run_evals(
        [scenario], config="haiku", repeats=5, ehr_urls=ehr_urls, agent=handoff_agent(), caller=caller,
        langfuse=None, results_dir=tmp_path, max_dollars=1.5,
    )

    assert [r.repeat for r in results] == [1, 2]
    [saved] = tmp_path.glob("*.json")
    assert [run["repeat"] for run in json.loads(saved.read_text())["runs"]] == [1, 2]
    assert "Stopped after 2 of 5 runs" in capsys.readouterr().out


async def test_a_batch_stops_when_the_agents_spending_reaches_the_limit(ehr_urls, tmp_path):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    # Claude Haiku 5.5 runs the agent at $0.10 per million input tokens: each LLM run here is $0.10.
    per_llm_run = LLMTokenUsage(prompt_tokens=1_000_000, completion_tokens=0, total_tokens=1_000_000)

    results = await run_evals(
        [scenario], config="haiku", repeats=5, ehr_urls=ehr_urls, agent=handoff_agent(per_llm_run),
        caller=lambda scenario, seeded: ScriptedCaller(["Can I talk to a real person, please?"]),
        langfuse=None, results_dir=tmp_path, max_dollars=0.05,
    )

    assert [r.repeat for r in results] == [1]


async def test_without_a_limit_every_run_happens(ehr_urls, tmp_path):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")

    results = await run_evals(
        [scenario], config="haiku", repeats=2, ehr_urls=ehr_urls, agent=handoff_agent(),
        caller=lambda scenario, seeded: ScriptedCaller(["Can I talk to a real person, please?"]),
        langfuse=None, results_dir=tmp_path,
    )

    assert len(results) == 2


async def test_a_limit_cannot_be_enforced_for_a_config_with_no_price(ehr_urls, tmp_path):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")

    with pytest.raises(ValueError, match="no price"):
        await run_evals(
            [scenario], config="scripted", repeats=1, ehr_urls=ehr_urls, agent=handoff_agent(),
            caller=lambda scenario, seeded: ScriptedCaller(["hi"]), langfuse=None, results_dir=tmp_path, max_dollars=1,
        )


async def test_a_run_saves_what_the_simulated_caller_used(ehr_urls, tmp_path):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    spent = TokenUsage(input_tokens=1_234, output_tokens=56)

    await run_evals(
        [scenario], config="haiku", repeats=1, ehr_urls=ehr_urls, agent=handoff_agent(),
        caller=lambda scenario, seeded: CallerThatSpends(["Can I talk to a real person, please?"], spent),
        langfuse=None, results_dir=tmp_path,
    )

    [saved] = tmp_path.glob("*.json")
    [run] = json.loads(saved.read_text())["runs"]
    assert run["caller_usage"] == {"input_tokens": 1_234, "output_tokens": 56, "cache_read_tokens": 0, "cache_write_tokens": 0}
