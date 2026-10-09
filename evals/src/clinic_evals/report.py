"""The eval report, built from saved batch documents: one column per LLM config.

A batch document is what `evals.py` saves for a run: the config, its model, the noise rate and every
run's grades, per-turn times, the LLM's time to first token on each of its runs, tool times and token usage.
"""

import json
from pathlib import Path
from statistics import fmean

from clinic_evals.cost import call_cost
from clinic_evals.graders import GRADERS
from clinic_evals.record import TokenUsage

LABEL_WIDTH = 38
COLUMN_WIDTH = 20


def percentile(values: list[float], pct: float) -> float | None:
    """The value pct percent of the way up the sorted list, interpolating between neighbours."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100
    below = int(position)
    above = min(below + 1, len(ordered) - 1)
    return ordered[below] + (ordered[above] - ordered[below]) * (position - below)


def latest_batch_per_config(results_dir: Path) -> list[dict]:
    """The newest saved batch of each config, by the timestamp in its id, ordered by config name."""
    latest: dict[str, dict] = {}
    for path in sorted(results_dir.glob("eval-*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        config = document["config"]
        if config not in latest or document["batch_id"] > latest[config]["batch_id"]:
            latest[config] = document
    return [latest[config] for config in sorted(latest)]


def format_report(documents: list[dict]) -> str:
    rows: list[tuple[str, list[str]]] = [
        ("config", [d["config"] for d in documents]),
        ("runs", [str(len(d["runs"])) for d in documents]),
        ("noise rate", [f"{d.get('noise_rate', 0):.2f}" for d in documents]),
    ]
    rows += [(name, [_pass_rate(d, name) for d in documents]) for name in GRADERS]
    rows += [
        ("turn latency P50 / P95", [_range(_turns(d), "{:.1f} s") for d in documents]),
        ("LLM time to first token P50 / P95", [_range(_first_tokens(d), "{:.2f} s") for d in documents]),
        ("tool time P50 / P95", [_range(_tool_times(d), "{:.2f} s") for d in documents]),
        ("cost per call", [_cost_per_call(d) for d in documents]),
        ("tokens per call (in / out)", [_tokens_per_call(d) for d in documents]),
    ]
    lines = ["Pass rate per grader, latency and cost, per LLM config:"]
    lines += [f"  {label:<{LABEL_WIDTH}}" + "".join(f"{value:<{COLUMN_WIDTH}}" for value in values).rstrip() for label, values in rows]
    lines += [f"  batch {d['config']}: {d['batch_id']}" for d in documents]
    lines += _problems(documents)
    return "\n".join(lines)


def _pass_rate(document: dict, grader: str) -> str:
    grades = [g for run in document["runs"] for g in run["grades"] if g["grader"] == grader]
    return f"{sum(g['passed'] for g in grades)}/{len(document['runs'])}"


def _turns(document: dict) -> list[float]:
    return [secs for run in document["runs"] for secs in run.get("turn_secs", [])]


def _first_tokens(document: dict) -> list[float]:
    return [secs for run in document["runs"] for secs in run.get("llm_ttfb_secs", [])]


def _tool_times(document: dict) -> list[float]:
    return [call["duration_secs"] for run in document["runs"] for call in run["tool_calls"] if "duration_secs" in call]


def _range(values: list[float], template: str) -> str:
    if not values:
        return "n/a"
    return f"{template.format(percentile(values, 50))} / {template.format(percentile(values, 95))}"


def _usages(document: dict) -> list[TokenUsage]:
    return [TokenUsage(**run["usage"]) for run in document["runs"] if "usage" in run]


def _cost_per_call(document: dict) -> str:
    costs = [call_cost(document.get("model"), usage) for usage in _usages(document)]
    if not costs or None in costs:
        return "n/a"
    return f"${fmean(costs):.4f}"


def _tokens_per_call(document: dict) -> str:
    usages = _usages(document)
    if not usages:
        return "n/a"
    return f"{fmean(u.input_tokens for u in usages):.0f} / {fmean(u.output_tokens for u in usages):.0f}"


def _problems(documents: list[dict]) -> list[str]:
    lines = []
    for document in documents:
        config = document["config"]
        for run in document["runs"]:
            lines += [
                f"  {config}: {run['scenario']} run {run['repeat']}, {g['grader']}: {g['reason']}"
                for g in run["grades"]
                if not g["passed"]
            ]
            if run.get("error"):
                lines.append(f"  {config}: {run['scenario']} run {run['repeat']} stopped early: {run['error']}")
    return ["Failures:", *lines] if lines else []
