"""An eval batch: every scenario, several times over, for one LLM config. Graded, saved locally and pushed to Langfuse."""

import json
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pipecat.services.llm_service import LLMService

from clinic_agent.llm import LLM_CONFIGS
from clinic_agent.phi import DATE_OF_BIRTH, NAME, PHI, PHONE
from clinic_evals.caller import Caller
from clinic_evals.graders import Grade, grade
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
    noise_rate: float = 0.0,  # only recorded: the caller factory is what applies noise
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
            result = RunResult(batch_id, scenario.name, repeat, record, _masked(grade(record), record), started, datetime.now(UTC))
            results.append(result)
            print(_run_line(result), flush=True)
            _save(results, config, noise_rate, results_dir / f"{batch_id}.json")
            if langfuse:
                _push(langfuse, result, config)
    return results


def _masked(grades: list[Grade], record: RunRecord) -> list[Grade]:
    """Grade reasons quote what the agent said, so they go through the agent's PHI mask before anything stores, prints or pushes them.

    The mask already learns what the Caller says on the call. It is also taught what the run seeded, and the
    surnames the noise injector garbled, so a name the agent repeats is masked even when the Caller never said it.
    """
    patient = record.scenario.patient
    for name in (patient.given, patient.family):
        PHI.learn(name, NAME)
    PHI.learn(record.seeded.birth_date, DATE_OF_BIRTH)
    PHI.learn(record.seeded.caller_phone, PHONE)
    for applied in record.noise:
        if applied.confusion.category == "surname":
            PHI.learn(applied.confusion.heard, NAME)
    return [replace(g, reason=PHI.mask(g.reason) if g.reason else None) for g in grades]


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


def _run_line(result: RunResult) -> str:
    failed = [g.grader for g in result.grades if not g.passed]
    verdict = "all graders passed" if not failed else f"failed {', '.join(failed)}"
    return f"{result.scenario} run {result.repeat}: {verdict} ({result.record.ending})"


def batch_document(results: list[RunResult], config: str, noise_rate: float) -> dict:
    """The saved form of a batch, which the report reads back."""
    runs = [
        {
            "scenario": r.scenario,
            "repeat": r.repeat,
            "ending": r.record.ending,
            "error": r.record.error,
            "grades": [{"grader": g.grader, "passed": g.passed, "reason": g.reason} for g in r.grades],
            "turn_secs": r.record.turn_secs,
            "usage": asdict(r.record.usage),
            "noise": [
                {"turn": n.turn, "category": n.confusion.category, "said": n.confusion.said, "heard": n.confusion.heard, "source": n.confusion.source}
                for n in r.record.noise
            ],
            "transcript": r.record.transcript,
            "tool_calls": [
                {"name": c.name, "arguments": c.arguments, "result": c.result, "duration_secs": c.duration_secs}
                for c in r.record.tool_calls
            ],
        }
        for r in results
    ]
    llm_config = LLM_CONFIGS.get(config)
    return {
        "batch_id": results[0].batch_id,
        "config": config,
        "model": llm_config.model if llm_config else None,
        "noise_rate": noise_rate,
        "runs": runs,
    }


def _save(results: list[RunResult], config: str, noise_rate: float, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(batch_document(results, config, noise_rate), indent=2, default=str), encoding="utf-8")
