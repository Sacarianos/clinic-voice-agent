"""Scenarios are data files. Loading checks them, so a broken file fails before any call is paid for."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from clinic_evals.caller import persona
from clinic_evals.record import Seeded
from clinic_evals.scenario import SCENARIOS_DIR, ExpectedAppointment, ScenarioError, load_scenario, load_scenarios

CLINIC = ZoneInfo("America/New_York")


def test_the_bundled_scenarios_cover_a_plain_book_a_reschedule_and_a_request_for_a_person():
    scenarios = {scenario.name: scenario for scenario in load_scenarios(SCENARIOS_DIR)}

    book = scenarios["plain_book"]
    assert book.appointments == {}
    assert [expected.same_as for expected in book.expected_appointments] == [None]
    assert not book.expect_handoff

    reschedule = scenarios["reschedule"]
    [moved] = reschedule.expected_appointments
    assert moved.same_as in reschedule.appointments
    assert moved.slot != reschedule.appointments[moved.same_as].slot
    assert not reschedule.expect_handoff

    person = scenarios["asks_for_a_person"]
    assert person.expected_appointments == []
    assert person.expect_handoff

    for scenario in scenarios.values():
        assert scenario.patient.given and scenario.patient.family
        assert scenario.goal and scenario.twist


def test_the_bundled_scenarios_cover_the_twists_a_real_call_brings():
    scenarios = {scenario.name: scenario for scenario in load_scenarios(SCENARIOS_DIR)}

    assert set(scenarios) == {
        "plain_book",
        "reschedule",
        "cancel",
        "asks_for_a_person",
        "wrong_dob_first",
        "changes_mind",
        "interrupts",
        "mentions_chest_pain",
        "proxy_caller",
        "garbled_provider_name",
    }
    assert "{wrong_birth_date}" in scenarios["wrong_dob_first"].twist
    assert scenarios["wrong_dob_first"].expected_appointments
    [kept] = scenarios["changes_mind"].expected_appointments
    assert kept.slot == "second"
    assert scenarios["cancel"].appointments and scenarios["cancel"].expected_appointments == []
    assert scenarios["mentions_chest_pain"].expect_emergency
    assert not scenarios["mentions_chest_pain"].expect_handoff
    assert scenarios["proxy_caller"].expect_handoff
    assert scenarios["proxy_caller"].expect_verification is False
    assert scenarios["proxy_caller"].expected_appointments == []
    assert scenarios["interrupts"].expected_appointments
    assert scenarios["garbled_provider_name"].expected_appointments


def test_every_bundled_scenario_gives_the_simulated_caller_a_complete_persona():
    for scenario in load_scenarios(SCENARIOS_DIR):
        starts = {label: datetime.combine(date(2026, 10, 9), spec.at, CLINIC) for label, spec in scenario.slots.items()}
        seeded = Seeded("patient-1", "1961-03-03", "+15550000001", {}, starts, {})

        system = persona(scenario, seeded)

        assert f"{scenario.patient.given} {scenario.patient.family}, born March 3, 1961" in system
        assert "{" not in system, scenario.name


def test_a_scenario_reads_its_slots_appointments_and_expected_end_state(tmp_path):
    path = tmp_path / "move_it.yaml"
    path.write_text(
        """
summary: Moves a follow-up to the afternoon.
patient: {given: Desmond, family: Achterberg}
provider: {given: Imogen, family: Faraday}
slots:
  current: {weekday: 1, at: "10:00"}
  later: {weekday: 2, at: "14:30"}
appointments:
  existing: {slot: current, visit_type: follow_up}
caller:
  goal: Move your follow-up on {current}.
  twist: You prefer {later_day}.
expect:
  appointments:
    - {slot: later, visit_type: follow_up, same_as: existing}
  handoff: false
"""
    )

    scenario = load_scenario(path)

    assert scenario.name == "move_it"
    assert scenario.provider.family == "Faraday"
    assert scenario.slots["later"].weekday == 2
    assert scenario.slots["later"].at.isoformat() == "14:30:00"
    assert scenario.appointments["existing"].slot == "current"
    assert scenario.expected_appointments == [ExpectedAppointment("later", "follow_up", same_as="existing")]


@pytest.mark.parametrize(
    "broken, complaint",
    [
        ("expect: {appointments: [{slot: nowhere, visit_type: follow_up}], handoff: false}", "nowhere"),
        ("expect: {appointments: [{slot: current, visit_type: checkup}], handoff: false}", "checkup"),
        ("expect: {appointments: [], handoff: maybe}", "handoff"),
    ],
)
def test_a_scenario_that_names_something_it_does_not_define_does_not_load(tmp_path, broken, complaint):
    path = tmp_path / "broken.yaml"
    path.write_text(
        f"""
summary: Broken on purpose.
patient: {{given: Desmond, family: Achterberg}}
provider: {{given: Imogen, family: Faraday}}
slots:
  current: {{weekday: 1, at: "10:00"}}
caller: {{goal: Book something., twist: None.}}
{broken}
"""
    )

    with pytest.raises(ScenarioError, match=complaint):
        load_scenario(path)


def test_a_caller_line_that_names_a_slot_the_scenario_lacks_does_not_load(tmp_path):
    path = tmp_path / "typo.yaml"
    path.write_text(
        """
summary: Names a Slot it doesn't have.
patient: {given: Desmond, family: Achterberg}
provider: {given: Imogen, family: Faraday}
slots:
  current: {weekday: 1, at: "10:00"}
caller: {goal: "Book something on {curent_day}.", twist: None.}
expect: {appointments: [], handoff: false}
"""
    )

    with pytest.raises(ScenarioError, match="curent_day"):
        load_scenario(path)


def test_a_scenario_can_expect_an_emergency_redirect_and_the_wrong_birth_date_in_the_twist(tmp_path):
    path = tmp_path / "chest_pain.yaml"
    path.write_text(
        """
summary: Chest pain.
patient: {given: Desmond, family: Achterberg}
provider: null
caller:
  goal: Say you have chest pain.
  twist: Give {wrong_birth_date} as your date of birth first.
expect: {appointments: [], handoff: false, emergency: true}
"""
    )

    scenario = load_scenario(path)

    assert scenario.expect_emergency
    assert not scenario.expect_handoff


def test_a_scenario_that_does_not_mention_emergency_expects_none(tmp_path):
    path = tmp_path / "calm.yaml"
    path.write_text(
        """
summary: Calm.
patient: {given: Desmond, family: Achterberg}
provider: null
caller: {goal: Talk., twist: None.}
expect: {appointments: [], handoff: false}
"""
    )

    assert not load_scenario(path).expect_emergency
