"""The report: pass rate per grader, latency P50 and P95, tool time and cost per call, for each LLM config."""

import json
import re

import pytest

from clinic_evals.cli import main
from clinic_evals.cost import call_cost
from clinic_evals.graders import GRADERS
from clinic_evals.record import TokenUsage
from clinic_evals.report import format_report, latest_batch_per_config, percentile


def test_percentiles_interpolate_between_the_two_nearest_values():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([1, 2, 3, 4], 95) == pytest.approx(3.85)
    assert percentile([7], 95) == 7
    assert percentile([], 50) is None


def test_haiku_cost_is_priced_per_million_tokens_with_cheaper_cache_reads_and_dearer_cache_writes():
    usage = TokenUsage(input_tokens=10_000, output_tokens=1_000, cache_read_tokens=5_000, cache_write_tokens=2_000)

    # 10k input at $1, 1k output at $5, 5k cache reads at $0.10, 2k cache writes at $1.25, per million.
    assert call_cost("claude-haiku-4-5", usage) == pytest.approx(0.018)


def test_a_model_without_a_known_price_has_no_cost():
    assert call_cost("some-new-model", TokenUsage(input_tokens=1_000)) is None
    assert call_cost(None, TokenUsage(input_tokens=1_000)) is None


def run(*, failed=(), turns=(), tools=(), usage=None, error=None):
    return {
        "scenario": "plain_book",
        "repeat": 1,
        "ending": "caller_hung_up",
        "error": error,
        "grades": [{"grader": name, "passed": name not in failed, "reason": "because" if name in failed else None} for name in GRADERS],
        "turn_secs": list(turns),
        "tool_calls": [{"name": name, "arguments": {}, "result": {}, "duration_secs": secs} for name, secs in tools],
        "usage": usage or {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0},
        "noise": [],
        "transcript": [],
    }


def batch(config, model, runs, noise_rate=0.2, batch_id=None):
    return {
        "batch_id": batch_id or f"eval-{config}-20261007T120000Z",
        "config": config,
        "model": model,
        "noise_rate": noise_rate,
        "runs": runs,
    }


HAIKU = batch(
    "haiku",
    "claude-haiku-4-5",
    [
        run(turns=[1, 2], tools=[("verify_patient", 0.1)], usage={"input_tokens": 10_000, "output_tokens": 1_000, "cache_read_tokens": 0, "cache_write_tokens": 0}),
        run(failed=["say_do_match"], turns=[3, 5], tools=[("find_slots", 0.3)], usage={"input_tokens": 20_000, "output_tokens": 2_000, "cache_read_tokens": 0, "cache_write_tokens": 0}),
    ],
)
OTHER = batch("gemini", "google/some-model", [run(turns=[10]), run(turns=[20]), run(turns=[30])])


def row(report: str, label: str) -> list[str]:
    [line] = [line for line in report.splitlines() if line.lstrip().startswith(label)]
    return re.split(r"\s{2,}", line.strip())[1:]


def test_the_report_has_a_column_per_config_with_each_graders_pass_rate():
    report = format_report([HAIKU, OTHER])

    assert row(report, "say_do_match") == ["1/2", "3/3"]
    for grader in GRADERS:
        if grader != "say_do_match":
            assert row(report, grader) == ["2/2", "3/3"]
    assert row(report, "config") == ["haiku", "gemini"]
    assert row(report, "runs") == ["2", "3"]


def test_the_report_shows_turn_latency_tool_time_and_cost_per_call_with_cost_unknown_for_an_unpriced_model():
    report = format_report([HAIKU, OTHER])

    assert row(report, "turn latency P50 / P95") == ["2.5 s / 4.7 s", "20.0 s / 29.0 s"]
    assert row(report, "tool time P50 / P95") == ["0.20 s / 0.29 s", "n/a"]
    # 15k input and 1.5k output tokens a call on average, at $1 and $5 per million.
    assert row(report, "cost per call") == ["$0.0225", "n/a"]
    assert row(report, "tokens per call (in / out)") == ["15000 / 1500", "0 / 0"]


def test_the_report_lists_each_failure_with_its_reason_and_each_run_that_stopped_early():
    broken = batch("haiku", "claude-haiku-4-5", [run(failed=["say_do_match"]), run(error="TimeoutError: slow")])

    report = format_report([broken])

    assert "haiku: plain_book run 1, say_do_match: because" in report
    assert "haiku: plain_book run 1 stopped early: TimeoutError: slow" in report


def test_the_report_names_the_noise_it_ran_with():
    report = format_report([HAIKU])

    assert row(report, "noise rate") == ["0.20"]


def test_the_report_of_a_batch_with_no_runs_does_not_fail():
    assert "runs" in format_report([batch("haiku", "claude-haiku-4-5", [])])


def test_the_latest_batch_of_each_config_is_the_one_reported(tmp_path):
    for config, stamp in [("haiku", "20261001T100000Z"), ("haiku", "20261003T100000Z"), ("gemini", "20261002T100000Z")]:
        document = batch(config, "m", [], batch_id=f"eval-{config}-{stamp}")
        (tmp_path / f"{document['batch_id']}.json").write_text(json.dumps(document))
    (tmp_path / "notes.json").write_text("{}")

    latest = latest_batch_per_config(tmp_path)

    assert [(b["config"], b["batch_id"]) for b in latest] == [
        ("gemini", "eval-gemini-20261002T100000Z"),
        ("haiku", "eval-haiku-20261003T100000Z"),
    ]


def test_the_report_command_prints_the_saved_batches_without_running_anything(tmp_path, capsys):
    (tmp_path / f"{HAIKU['batch_id']}.json").write_text(json.dumps(HAIKU))

    assert main(["--report", "--results-dir", str(tmp_path)]) == 0

    assert "say_do_match" in capsys.readouterr().out


def test_the_report_command_says_so_when_nothing_was_saved(tmp_path, capsys):
    assert main(["--report", "--results-dir", str(tmp_path)]) == 0

    assert "No saved eval results" in capsys.readouterr().out
