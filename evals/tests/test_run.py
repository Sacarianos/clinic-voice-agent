"""One eval run, end to end through the text transport, with a scripted agent LLM and a scripted Caller."""

from datetime import date

from pipecat.metrics.metrics import LLMTokenUsage

from clinic_agent.scripted_llm import CallTool, ScriptedLLM
from clinic_evals.caller import ScriptedCaller
from clinic_evals.graders import grade
from clinic_evals.noise import Confusion, NoiseInjector, NoisyCaller
from clinic_evals.record import Seeded, TokenUsage
from clinic_evals.run import run_scenario
from clinic_evals.scenario import SCENARIOS_DIR, Scenario, load_scenario


def spoken(iso_date: str) -> str:
    day = date.fromisoformat(iso_date)
    return f"{day:%B} {day.day}, {day.year}"


def verify(scenario: Scenario, seeded: Seeded) -> CallTool:
    patient = scenario.patient
    args = {
        "given_name": patient.given,
        "family_name": patient.family,
        "date_of_birth": seeded.birth_date,
        "caller_is_the_patient": True,
    }
    return CallTool("verify_patient", args)


def assert_gone(fhir, *references: str):
    for reference in references:
        assert fhir.get(reference).status_code in (404, 410), f"{reference} is still in the EHR"


async def test_a_scripted_plain_book_passes_every_grader_and_leaves_nothing_behind(ehr_urls, fhir):
    scenario = load_scenario(SCENARIOS_DIR / "plain_book.yaml")

    def agent(seeded: Seeded) -> ScriptedLLM:
        day = seeded.slot_starts["late"].date().isoformat()
        return ScriptedLLM(
            [
                "Sure. What is your full name and date of birth?",
                verify(scenario, seeded),
                "Thanks. Who would you like to see, and when?",
                CallTool("find_slots", {"provider": "Dr. Imogen Faraday", "from_date": day, "to_date": day}),
                "Dr. Faraday has 9 AM, 11 AM or 3 PM. Which works?",
                CallTool("choose_slot", {"slot_id": seeded.slot_ids["late"], "visit_type": "annual_physical"}),
                CallTool("record_read_back_answer", {"answer": "yes"}),
                CallTool("book_appointment"),
                "Thanks for calling. Goodbye.",
            ]
        )

    def caller(scenario: Scenario, seeded: Seeded) -> ScriptedCaller:
        return ScriptedCaller(
            [
                "Hi, I'd like to book my yearly checkup.",
                f"Rosalind Okonkwo, {spoken(seeded.birth_date)}.",
                "Dr. Faraday, in the morning.",
                "The latest morning one, please.",
                "Yes.",
                "No, that's all. Bye.",
            ]
        )

    run = await run_scenario(scenario, *ehr_urls, agent=agent, caller=caller)

    assert run.error is None
    assert run.ending == "caller_hung_up"
    assert run.transcript[-1] == ("agent", "Thanks for calling. Goodbye.")
    assert [(g.grader, g.passed, g.reason) for g in grade(run) if not g.passed] == []
    slots = [f"Slot/{slot_id}" for slot_id in run.seeded.slot_ids.values()]
    assert_gone(fhir, f"Patient/{run.seeded.patient_id}", *slots)
    tasks = fhir.get("Task", params={"_count": "200"}).json().get("entry", [])
    assert run.seeded.caller_phone not in str(tasks)


async def test_a_scripted_reschedule_moves_the_appointment_the_run_seeded(ehr_urls, fhir):
    scenario = load_scenario(SCENARIOS_DIR / "reschedule.yaml")

    def agent(seeded: Seeded) -> ScriptedLLM:
        day = seeded.slot_starts["new_afternoon"].date().isoformat()
        return ScriptedLLM(
            [
                "Sure. What is your full name and date of birth?",
                verify(scenario, seeded),
                CallTool("list_appointments"),
                "You have a follow-up with Dr. Faraday. Is that the one to move?",
                CallTool("choose_appointment_to_reschedule", {"appointment_id": seeded.appointment_ids["existing"]}),
                "When would you like to move it to?",
                CallTool("find_slots", {"provider": "Dr. Imogen Faraday", "from_date": day, "to_date": day}),
                "Dr. Faraday has 9:30 AM or 2 PM that day.",
                CallTool("choose_slot", {"slot_id": seeded.slot_ids["new_afternoon"]}),
                CallTool("reschedule_appointment"),
            ]
        )

    def caller(scenario: Scenario, seeded: Seeded) -> ScriptedCaller:
        return ScriptedCaller(
            [
                "I need to change an appointment.",
                f"Desmond Achterberg, {spoken(seeded.birth_date)}.",
                "Yes, that one.",
                "Later that week.",
                "The afternoon, please.",
                "Yes.",
            ]
        )

    run = await run_scenario(scenario, *ehr_urls, agent=agent, caller=caller)

    assert run.error is None
    assert [(g.grader, g.passed, g.reason) for g in grade(run) if not g.passed] == []
    assert_gone(fhir, f"Patient/{run.seeded.patient_id}", f"Appointment/{run.seeded.appointment_ids['existing']}")


async def test_noise_garbles_what_the_agent_hears_and_the_run_records_each_confusion(ehr_urls):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    garble = NoiseInjector([Confusion("surname", "Villanueva", "Vianueva", "hand-written")], rate=1)

    def agent(seeded: Seeded) -> ScriptedLLM:
        return ScriptedLLM([CallTool("handoff", {"reason": "asked_for_person"})])

    def caller(scenario: Scenario, seeded: Seeded) -> NoisyCaller:
        return NoisyCaller(ScriptedCaller(["I'm Marguerite Villanueva, let me talk to a person."]), garble)

    run = await run_scenario(scenario, *ehr_urls, agent=agent, caller=caller)

    assert run.error is None
    assert ("caller", "I'm Marguerite Vianueva, let me talk to a person.") in run.transcript
    [applied] = run.noise
    assert (applied.confusion.said, applied.confusion.source, applied.turn) == ("Villanueva", "hand-written", 1)
    assert applied.original == "I'm Marguerite Villanueva, let me talk to a person."


async def test_a_run_without_noise_records_none(ehr_urls):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")

    run = await run_scenario(
        scenario,
        *ehr_urls,
        agent=lambda seeded: ScriptedLLM([CallTool("handoff", {"reason": "asked_for_person"})]),
        caller=lambda scenario, seeded: ScriptedCaller(["Let me talk to a person."]),
    )

    assert run.noise == []


async def test_a_run_records_each_turns_latency_the_tools_time_and_the_agents_tokens(ehr_urls):
    scenario = load_scenario(SCENARIOS_DIR / "asks_for_a_person.yaml")
    per_llm_run = LLMTokenUsage(
        prompt_tokens=1000, completion_tokens=20, cache_read_input_tokens=300, cache_creation_input_tokens=50, total_tokens=1370
    )

    run = await run_scenario(
        scenario,
        *ehr_urls,
        agent=lambda seeded: ScriptedLLM([CallTool("handoff", {"reason": "asked_for_person"})], usage=per_llm_run),
        caller=lambda scenario, seeded: ScriptedCaller(["Let me talk to a person."]),
    )

    [turn] = run.turn_secs
    [handoff_call] = run.tool_calls
    assert 0 < handoff_call.duration_secs <= turn
    assert run.usage == TokenUsage(input_tokens=1000, output_tokens=20, cache_read_tokens=300, cache_write_tokens=50)
