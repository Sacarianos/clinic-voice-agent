"""The whole eval command: every scenario, several times, graded, saved and pushed to Langfuse.

A local stand-in plays Langfuse: it records what would have been sent and answers as Langfuse does.
"""

import base64
import json

import httpx
import pytest

from clinic_agent.scripted_llm import CallTool, ScriptedLLM
from clinic_evals.caller import ScriptedCaller
from clinic_evals.evals import run_evals
from clinic_evals.graders import GRADERS
from clinic_evals.langfuse import Langfuse, LangfuseError
from clinic_evals.scenario import SCENARIOS_DIR, load_scenario


class LangfuseStandIn:
    def __init__(self, errors: list | None = None):
        self.batches: list[dict] = []
        self.credentials: list[str] = []
        self._errors = errors or []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/public/ingestion"
        self.credentials.append(request.headers["authorization"])
        batch = json.loads(request.content)["batch"]
        self.batches.append(batch)
        successes = [{"id": event["id"], "status": 201} for event in batch]
        return httpx.Response(207, json={"successes": successes, "errors": self._errors})

    def client(self) -> Langfuse:
        return Langfuse("https://langfuse.test", "pk-test", "sk-test", transport=httpx.MockTransport(self.handle))

    def events(self, kind: str) -> list[dict]:
        return [event["body"] for batch in self.batches for event in batch if event["type"] == kind]


async def test_one_command_runs_each_scenario_three_times_and_pushes_scores_tagged_with_the_config(ehr_urls, tmp_path):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    langfuse = LangfuseStandIn()

    results = await run_evals(
        [scenario],
        config="scripted",
        repeats=3,
        ehr_urls=ehr_urls,
        agent=lambda seeded: ScriptedLLM([CallTool("handoff", {"reason": "asked_for_person"})]),
        caller=lambda scenario, seeded: ScriptedCaller(["Can I talk to a real person, please?"]),
        langfuse=langfuse.client(),
        results_dir=tmp_path,
    )

    assert [(result.scenario, result.repeat) for result in results] == [("asks_for_a_person", n) for n in (1, 2, 3)]
    assert all(grade.passed for result in results for grade in result.grades)

    traces = langfuse.events("trace-create")
    assert len(traces) == 3
    assert {trace["sessionId"] for trace in traces} == {results[0].batch_id}
    for trace in traces:
        assert "scripted" in trace["tags"]
        assert trace["metadata"]["config"] == "scripted"
    scores = langfuse.events("score-create")
    assert sorted(score["name"] for score in scores) == sorted(list(GRADERS) * 3)
    assert {score["traceId"] for score in scores} == {trace["id"] for trace in traces}
    assert {(score["dataType"], score["value"]) for score in scores} == {("BOOLEAN", 1)}
    assert langfuse.credentials[0] == "Basic " + base64.b64encode(b"pk-test:sk-test").decode()

    [saved] = tmp_path.glob("*.json")
    runs = json.loads(saved.read_text())["runs"]
    assert len(runs) == 3
    assert runs[0]["transcript"][-1] == ["agent", "Of course. I'll have a member of our staff call you back at this number. Goodbye."]


async def test_a_failed_grade_goes_to_langfuse_with_its_reason(ehr_urls, tmp_path):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    langfuse = LangfuseStandIn()

    await run_evals(
        [scenario],
        config="scripted",
        repeats=1,
        ehr_urls=ehr_urls,
        agent=lambda seeded: ScriptedLLM(["I can help with that. What is your name?"]),
        caller=lambda scenario, seeded: ScriptedCaller(["Can I talk to a real person, please?"]),
        langfuse=langfuse.client(),
        results_dir=tmp_path,
    )

    [handoff] = [score for score in langfuse.events("score-create") if score["name"] == "handoff_when_expected"]
    assert handoff["value"] == 0
    assert handoff["comment"] == "expected a Handoff, but no Callback Request was filed"


async def test_langfuse_refusing_a_run_does_not_stop_the_batch_or_lose_its_results(ehr_urls, tmp_path, capsys):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    langfuse = LangfuseStandIn(errors=[{"id": "x", "status": 400, "message": "Invalid request data"}])

    results = await run_evals(
        [scenario],
        config="scripted",
        repeats=2,
        ehr_urls=ehr_urls,
        agent=lambda seeded: ScriptedLLM([CallTool("handoff", {"reason": "asked_for_person"})]),
        caller=lambda scenario, seeded: ScriptedCaller(["Can I talk to a real person, please?"]),
        langfuse=langfuse.client(),
        results_dir=tmp_path,
    )

    assert len(results) == 2
    assert "Invalid request data" in capsys.readouterr().err
    [saved] = tmp_path.glob("*.json")
    assert len(json.loads(saved.read_text())["runs"]) == 2


def test_langfuse_refusing_an_event_is_an_error():
    langfuse = LangfuseStandIn(errors=[{"id": "x", "status": 400, "message": "Invalid request data"}])

    with pytest.raises(LangfuseError, match="Invalid request data"):
        langfuse.client().ingest([{"id": "x", "type": "trace-create", "timestamp": "2026-10-07T00:00:00Z", "body": {}}])
