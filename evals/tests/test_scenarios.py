"""Scenarios are data files. Loading checks them, so a broken file fails before any call is paid for."""

import pytest

from clinic_evals.scenario import SCENARIOS_DIR, ExpectedAppointment, ScenarioError, load_scenario, load_scenarios


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
