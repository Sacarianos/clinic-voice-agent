"""Scenarios: what each eval run sets up in the EHR, what the simulated Caller wants, and the end state to expect.

Each scenario is a YAML file in `evals/scenarios/`, named after the scenario. Times are relative, so a
scenario works on any day: `weekday: 1` is the first clinic weekday after today. The Caller's goal and
twist may name a Slot by its label: `{late}` reads as "Thursday, October 8 at 11 AM" and `{late_day}`
as "Thursday, October 8".
"""

from dataclasses import dataclass
from datetime import time
from pathlib import Path
from string import Formatter

import yaml

from clinic_agent.booking import VISIT_TYPES

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "scenarios"


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class Person:
    given: str
    family: str


@dataclass(frozen=True)
class SlotSpec:
    weekday: int  # clinic weekdays after today: 1 is the next one
    at: time  # clinic wall-clock time


@dataclass(frozen=True)
class AppointmentSpec:
    """An Appointment the Patient already holds when the call starts."""

    slot: str
    visit_type: str


@dataclass(frozen=True)
class ExpectedAppointment:
    """A booked Appointment the Patient must hold after the call. same_as names the existing one it must still be."""

    slot: str
    visit_type: str
    same_as: str | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    summary: str
    patient: Person
    provider: Person | None  # the run's own Provider, who owns every Slot below
    slots: dict[str, SlotSpec]
    appointments: dict[str, AppointmentSpec]
    goal: str
    twist: str
    expected_appointments: list[ExpectedAppointment]  # all the Patient's booked Appointments, nothing more
    expect_handoff: bool


def load_scenarios(directory: Path) -> list[Scenario]:
    return [load_scenario(path) for path in sorted(directory.glob("*.yaml"))]


def load_scenario(path: Path) -> Scenario:
    try:
        return _parse(path.stem, yaml.safe_load(path.read_text(encoding="utf-8")))
    except (KeyError, TypeError, ValueError) as error:
        raise ScenarioError(f"{path.name}: {error}") from error


def _parse(name: str, data: dict) -> Scenario:
    slots = {label: SlotSpec(int(spec["weekday"]), time.fromisoformat(spec["at"])) for label, spec in (data.get("slots") or {}).items()}
    appointments = {
        label: AppointmentSpec(_slot(spec["slot"], slots), _visit_type(spec["visit_type"]))
        for label, spec in (data.get("appointments") or {}).items()
    }
    expect = data["expect"]
    expected = [
        ExpectedAppointment(
            _slot(spec["slot"], slots), _visit_type(spec["visit_type"]), _existing(spec.get("same_as"), appointments)
        )
        for spec in expect.get("appointments") or []
    ]
    if not isinstance(expect.get("handoff"), bool):
        raise ScenarioError(f"expect.handoff must be true or false, not {expect.get('handoff')!r}")
    if slots and not data.get("provider"):
        raise ScenarioError("Slots need a provider to belong to")
    names = set(slots) | {f"{label}_day" for label in slots}
    for line in (data["caller"]["goal"], data["caller"]["twist"]):
        for _, field, _, _ in Formatter().parse(line):
            if field is not None and field not in names:
                raise ScenarioError(f"the Caller's goal or twist names {{{field}}}, but no Slot is labelled that")
    return Scenario(
        name=name,
        summary=data["summary"],
        patient=Person(**data["patient"]),
        provider=Person(**data["provider"]) if data.get("provider") else None,
        slots=slots,
        appointments=appointments,
        goal=data["caller"]["goal"],
        twist=data["caller"]["twist"],
        expected_appointments=expected,
        expect_handoff=expect["handoff"],
    )


def _slot(label: str, slots: dict[str, SlotSpec]) -> str:
    if label not in slots:
        raise ScenarioError(f"no Slot labelled {label!r}")
    return label


def _visit_type(visit_type: str) -> str:
    if visit_type not in VISIT_TYPES:
        raise ScenarioError(f"unknown Visit Type {visit_type!r}, expected one of {', '.join(VISIT_TYPES)}")
    return visit_type


def _existing(label: str | None, appointments: dict[str, AppointmentSpec]) -> str | None:
    if label is not None and label not in appointments:
        raise ScenarioError(f"no existing Appointment labelled {label!r}")
    return label
