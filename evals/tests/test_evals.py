"""The whole eval command: every scenario, several times, graded, saved and pushed to Langfuse.

A local stand-in plays Langfuse: it records what would have been sent and answers as Langfuse does.
"""

import base64
import json
from datetime import date

import httpx

from clinic_agent.scripted_llm import CallTool, ScriptedLLM
from clinic_evals.caller import ScriptedCaller
from clinic_evals.evals import run_evals
from clinic_evals.graders import GRADERS
from clinic_evals.langfuse import Langfuse
from clinic_evals.noise import Confusion, NoiseInjector, NoisyCaller
from clinic_evals.record import Seeded
from clinic_evals.scenario import SCENARIOS_DIR, load_scenario


class LangfuseStandIn:
    """Answers as Langfuse Cloud does: OpenTelemetry spans make traces, and scores are posted one by one."""

    def __init__(self, score_status: int = 200, rate_limit_every: int = 0):
        self.spans: list[dict] = []
        self.scores: list[dict] = []
        self.credentials: set[str] = set()
        self.rate_limited = 0
        self._score_status = score_status
        self._rate_limit_every = rate_limit_every
        self._score_requests = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.credentials.add(request.headers["authorization"])
        body = json.loads(request.content)
        if request.url.path == "/api/public/otel/v1/traces":
            for resource in body["resourceSpans"]:
                for scope in resource["scopeSpans"]:
                    self.spans += [_with_attributes(span) for span in scope["spans"]]
            return httpx.Response(200, json={})
        assert request.url.path == "/api/public/scores"
        self._score_requests += 1
        if self._rate_limit_every and self._score_requests % self._rate_limit_every == 0:
            self.rate_limited += 1
            return httpx.Response(429, headers={"retry-after": "0"}, json={"message": "Too many requests"})
        if self._score_status != 200:
            return httpx.Response(self._score_status, json={"message": "Invalid request data"})
        self.scores.append(body)
        return httpx.Response(200, json={"id": f"score-{len(self.scores)}"})

    def client(self) -> Langfuse:
        return Langfuse("https://langfuse.test", "pk-test", "sk-test", transport=httpx.MockTransport(self.handle))


def _with_attributes(span: dict) -> dict:
    """The span, with its OpenTelemetry attributes as a plain dict."""
    attributes = {}
    for attribute in span["attributes"]:
        value = attribute["value"]
        attributes[attribute["key"]] = (
            [item["stringValue"] for item in value["arrayValue"]["values"]] if "arrayValue" in value else value["stringValue"]
        )
    return {**span, "attributes": attributes}


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

    traces = [span["attributes"] for span in langfuse.spans]
    assert len(traces) == 3
    assert {trace["langfuse.session.id"] for trace in traces} == {results[0].batch_id}
    for trace in traces:
        assert trace["langfuse.trace.tags"] == ["eval", "scripted", "asks_for_a_person"]
        assert trace["langfuse.trace.metadata.config"] == "scripted"
    scores = langfuse.scores
    assert sorted(score["name"] for score in scores) == sorted(list(GRADERS) * 3)
    assert {score["traceId"] for score in scores} == {span["traceId"] for span in langfuse.spans}
    assert {(score["dataType"], score["value"]) for score in scores} == {("BOOLEAN", 1)}
    assert langfuse.credentials == {"Basic " + base64.b64encode(b"pk-test:sk-test").decode()}

    [saved] = tmp_path.glob("*.json")
    document = json.loads(saved.read_text())
    runs = document["runs"]
    assert document["noise_rate"] == 0.0
    assert len(runs) == 3
    assert [len(run["turn_secs"]) for run in runs] == [1, 1, 1]
    assert runs[0]["tool_calls"][0]["duration_secs"] > 0
    assert runs[0]["usage"] == {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    assert runs[0]["transcript"][-1] ==["agent", "Of course. I'll have a member of our staff call you back at this number. Goodbye."]


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

    [handoff] = [score for score in langfuse.scores if score["name"] == "handoff_when_expected"]
    assert handoff["value"] == 0
    assert handoff["comment"] == "expected a Handoff, but no Callback Request was filed"


async def test_scores_that_langfuse_rate_limits_are_sent_again_once_it_allows(ehr_urls, tmp_path):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    langfuse = LangfuseStandIn(rate_limit_every=2)

    await run_evals(
        [scenario],
        config="scripted",
        repeats=1,
        ehr_urls=ehr_urls,
        agent=lambda seeded: ScriptedLLM([CallTool("handoff", {"reason": "asked_for_person"})]),
        caller=lambda scenario, seeded: ScriptedCaller(["Can I talk to a real person, please?"]),
        langfuse=langfuse.client(),
        results_dir=tmp_path,
    )

    assert sorted(score["name"] for score in langfuse.scores) == sorted(GRADERS)
    assert langfuse.rate_limited > 0


async def test_langfuse_refusing_a_run_does_not_stop_the_batch_or_lose_its_results(ehr_urls, tmp_path, capsys):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    langfuse = LangfuseStandIn(score_status=400)

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
    assert "Could not push asks_for_a_person run 2 to Langfuse" in capsys.readouterr().err
    [saved] = tmp_path.glob("*.json")
    assert len(json.loads(saved.read_text())["runs"]) == 2


async def test_a_grade_reason_never_carries_the_patients_name_date_of_birth_or_phone_number(ehr_urls, tmp_path, capsys):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    langfuse = LangfuseStandIn()
    seen: list[Seeded] = []

    def agent(seeded: Seeded) -> ScriptedLLM:
        seen.append(seeded)
        born = date.fromisoformat(seeded.birth_date)
        return ScriptedLLM(
            [
                f"Of course, Marguerite Villanueva, born {born:%B} {born.day}, {born.year}, I'll transfer you to the "
                f"number {seeded.caller_phone} on file, or {seeded.birth_date}, now.",
                "Goodbye.",
            ]
        )

    results = await run_evals(
        [scenario],
        config="scripted",
        repeats=1,
        ehr_urls=ehr_urls,
        agent=agent,
        caller=lambda scenario, seeded: ScriptedCaller(["Can I talk to a real person, please?"]),
        langfuse=langfuse.client(),
        results_dir=tmp_path,
    )

    [seeded] = seen
    born = date.fromisoformat(seeded.birth_date)
    private = ["Marguerite", "Villanueva", seeded.caller_phone, seeded.birth_date, f"{born:%B} {born.day}, {born.year}"]
    [transfer] = [score for score in langfuse.scores if score["name"] == "no_transfer_promise"]
    assert transfer["value"] == 0
    assert "promised a transfer" in transfer["comment"]
    pushed = json.dumps(langfuse.scores) + json.dumps(langfuse.spans)
    saved_reasons = [g.reason for result in results for g in result.grades]
    printed = capsys.readouterr()
    for secret in private:
        assert secret not in pushed, secret
        assert not any(secret in (reason or "") for reason in saved_reasons), secret
        assert secret not in printed.out + printed.err, secret


async def test_a_surname_the_noise_injector_garbled_is_masked_in_reasons_too(ehr_urls, tmp_path):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    langfuse = LangfuseStandIn()
    garble = NoiseInjector([Confusion("surname", "Villanueva", "Vianuevo", "hand-written")], rate=1)

    await run_evals(
        [scenario],
        config="scripted",
        repeats=1,
        ehr_urls=ehr_urls,
        agent=lambda seeded: ScriptedLLM(["Thanks Marguerite Vianuevo, I'll transfer you now.", "Goodbye."]),
        caller=lambda scenario, seeded: NoisyCaller(ScriptedCaller(["Marguerite Villanueva, a person please."]), garble),
        langfuse=langfuse.client(),
        results_dir=tmp_path,
    )

    [transfer] = [score for score in langfuse.scores if score["name"] == "no_transfer_promise"]
    assert "Vianuevo" not in transfer["comment"]
    assert "Villanueva" not in transfer["comment"]
