"""What one eval run leaves behind for the graders: the call as it happened and the EHR afterwards."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from clinic_agent.text_call import ToolCall
from clinic_evals.scenario import Scenario

Ending = Literal["caller_hung_up", "agent_ended", "turn_limit", "error"]


@dataclass(frozen=True)
class Seeded:
    """What the run put in the EHR before the call, keyed by the scenario's labels."""

    patient_id: str
    birth_date: str  # YYYY-MM-DD, picked so no other Patient has it
    caller_phone: str  # the number the call comes from, its own so the run finds its Callback Requests
    slot_ids: dict[str, str]
    slot_starts: dict[str, datetime]
    appointment_ids: dict[str, str]


@dataclass(frozen=True)
class AppointmentState:
    appointment_id: str
    slot_id: str
    visit_type: str
    status: str  # FHIR Appointment status: booked, cancelled


@dataclass(frozen=True)
class SlotState:
    slot_id: str
    status: str  # free or busy
    appointment_ids: list[str]  # the Appointments that point at it and aren't cancelled


@dataclass(frozen=True)
class CallbackRequest:
    reason: str
    emergency: bool


@dataclass(frozen=True)
class EndState:
    """The EHR after the call: the Patient's Appointments, every Slot they or the scenario touch, and the Callback Requests from the call's number."""

    appointments: list[AppointmentState]
    slots: list[SlotState]
    callback_requests: list[CallbackRequest]


@dataclass(frozen=True)
class RunRecord:
    scenario: Scenario
    seeded: Seeded
    transcript: list[tuple[str, str]]  # ("caller" | "agent", line), in order
    tool_calls: list[ToolCall]
    end_state: EndState
    ending: Ending
    error: str | None = None

    def slot_label(self, slot_id: str) -> str:
        """The scenario's name for a Slot, quoted, or the Slot's id when the scenario didn't make it."""
        for label, seeded_id in self.seeded.slot_ids.items():
            if seeded_id == slot_id:
                return repr(label)
        return slot_id
