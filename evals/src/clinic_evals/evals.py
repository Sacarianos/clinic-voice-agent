"""An eval batch: every scenario, several times over, for one LLM config. Graded, saved locally and pushed to Langfuse."""

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pipecat.services.llm_service import LLMService

from clinic_evals.caller import Caller
from clinic_evals.graders import GRADERS, Grade, grade
from clinic_evals.langfuse import Langfuse, LangfuseError
from clinic_evals.record import RunRecord, Seeded
from clinic_evals.run import run_scenario
from clinic_evals.scenario import Scenario

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"


@dataclass(frozen=True)
class RunResult:
    batch_id: str
    scenario: str
    repeat: int  # 1 for the first run of the scenario
    record: RunRecord
    grades: list[Grade]
    started: datetime
    finished: datetime


async def run_evals(
    scenarios: list[Scenario],
    *,
    config: str,
    repeats: int,
    ehr_urls: tuple[str, str],
    agent: Callable[[Seeded], LLMService],
    caller: Callable[[Scenario, Seeded], Caller],
    langfuse: Langfuse | None,
    results_dir: Path = RESULTS_DIR,
    reply_timeout_secs: float = 10,
) -> list[RunResult]:
    """Runs one at a time, so runs never share the EHR. Each result is saved and pushed as soon as it is graded."""
    batch_id = f"eval-{config}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    results = []
    for scenario in scenarios:
        for repeat in range(1, repeats + 1):
            started = datetime.now(UTC)
            record = await run_scenario(
                scenario, *ehr_urls, agent=agent, caller=caller, reply_timeout_secs=reply_timeout_secs
            )
            result = RunResult(batch_id, scenario.name, repeat, record, grade(record), started, datetime.now(UTC))
            results.append(result)
            print(_run_line(result), flush=True)
            _save(results, config, results_dir / f"{batch_id}.json")
            if langfuse:
                _push(langfuse, result, config)
    return results


def _push(langfuse: Langfuse, result: RunResult, config: str) -> None:
    """A run already paid for stays in the results file even when Langfuse won't take it."""
    try:
        langfuse.push_run(
            batch_id=result.batch_id,
            config=config,
            scenario=result.scenario,
            repeat=result.repeat,
            ending=result.record.ending,
            grades=result.grades,
            started=result.started,
            finished=result.finished,
        )
    except (LangfuseError, httpx.HTTPError) as error:
        print(f"Could not push {result.scenario} run {result.repeat} to Langfuse: {error}", file=sys.stderr)


def summary(results: list[RunResult]) -> str:
    """Pass rate per grader, then every failure with its reason."""
    lines = ["Pass rate per grader:"]
    for name in GRADERS:
        passed = sum(g.passed for r in results for g in r.grades if g.grader == name)
        lines.append(f"  {name:<38} {passed}/{len(results)}")
    failures = [(r, g) for r in results for g in r.grades if not g.passed]
    if failures:
        lines.append("Failures:")
        lines += [f"  {r.scenario} run {r.repeat}, {g.grader}: {g.reason}" for r, g in failures]
    errors = [r for r in results if r.record.error]
    lines += [f"  {r.scenario} run {r.repeat} stopped early: {r.record.error}" for r in errors]
    return "\n".join(lines)


def _run_line(result: RunResult) -> str:
    failed = [g.grader for g in result.grades if not g.passed]
    verdict = "all graders passed" if not failed else f"failed {', '.join(failed)}"
    return f"{result.scenario} run {result.repeat}: {verdict} ({result.record.ending})"


def _save(results: list[RunResult], config: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    runs = [
        {
            "scenario": r.scenario,
            "repeat": r.repeat,
            "ending": r.record.ending,
            "error": r.record.error,
            "grades": [{"grader": g.grader, "passed": g.passed, "reason": g.reason} for g in r.grades],
            "transcript": r.record.transcript,
            "tool_calls": [{"name": c.name, "arguments": c.arguments, "result": c.result} for c in r.record.tool_calls],
        }
        for r in results
    ]
    document = {"batch_id": results[0].batch_id, "config": config, "runs": runs}
    path.write_text(json.dumps(document, indent=2, default=str), encoding="utf-8")
