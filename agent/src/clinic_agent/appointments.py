"""A Verified Patient's upcoming appointments: hearing them, then Rescheduling or Cancelling one.

Per ADR 0003 each write is reachable only one way, as in booking. The LLM picks an appointment that
list_appointments returned, and the agent itself reads back what will change. Only that Read-back node
offers the write, which takes no arguments: it changes what was read back, under an idempotency key
made for that Read-back. Choosing again leaves the node, and the tool with it.
"""

import uuid
from dataclasses import dataclass
from typing import ClassVar

from pipecat.flows import FlowManager, FlowsFunctionSchema, NodeConfig

from clinic_agent.booking import VISIT_TYPES, Booking, Exits, spoken_appointment, spoken_time
from clinic_agent.ehr import Appointment, EhrAdapter, Provider, Slot, WriteOutcome
from clinic_agent.escalation import handoff
from clinic_agent.holding import with_holding_line
from clinic_agent.writes import WRITE_TOOL_TIMEOUT_SECS, unsettled, write_until_settled

ANYTHING_ELSE = "Is there anything else I can help with?"

# What the Caller hears when the adapter rejects a change for a reason other than the new Slot.
CHANGE_REJECTED = {
    "appointment_in_past": f"I'm sorry, that appointment has already started, so I can't change it. {ANYTHING_ELSE}",
    "appointment_cancelled": f"I'm sorry, that appointment has already been cancelled. {ANYTHING_ELSE}",
    "appointment_not_found": f"I'm sorry, I can't find that appointment any more. {ANYTHING_ELSE}",
}

CHOOSE_APPOINTMENT_TASK = """\
Tell the caller the upcoming appointments list_appointments just returned, each with the Provider,
the day and the time. If there is more than one and the caller hasn't said which they mean, ask
which one. Never pick one for them.
Once you know the appointment and whether they want to reschedule or cancel it, call
choose_appointment_to_reschedule or choose_appointment_to_cancel.
Don't repeat the details or ask the caller to confirm them yourself: the agent reads them back.
"""

CANCEL_READ_BACK_TASK = """\
You just read back the appointment the caller wants to cancel and asked if that is right.
If the caller clearly says yes, call cancel_appointment. Never say it is cancelled before it returns.
If they mean another appointment, call choose_appointment_to_cancel with that one. If they would
rather move it, call choose_appointment_to_reschedule. If they are unsure, answer and ask again.
"""

RESCHEDULE_FIND_SLOT_TASK = """\
The caller wants to move the appointment they chose to a new time. If they haven't said when, ask.
Then call find_slots, and offer the open times it returns: two or three at most, each with the
Provider's name, the day and the time. If none came back, say so and suggest another day, part of
day or Provider. If they asked for a date past booking_window_ends, tell them how far out they can book.
As soon as the caller picks one of the offered times, call choose_slot.
Don't repeat the details or ask the caller to confirm them yourself: choose_slot reads them back.
"""

RESCHEDULE_READ_BACK_TASK = """\
You just read back the move of the caller's appointment and asked if that is right.
If the caller clearly says yes, call reschedule_appointment. Never say it is moved before it returns.
If they want a different time or Provider, call choose_slot for a time already offered, or find_slots
for new times. If they are unsure, answer their question and ask again.
"""


@dataclass(frozen=True)
class _Appointments:
    ehr: EhrAdapter
    providers: list[Provider]
    exits: Exits

    def list_appointments_tool(self) -> FlowsFunctionSchema:
        async def list_appointments(args: dict, flow_manager: FlowManager):
            appointments = await self.ehr.appointments(flow_manager.state["patient_id"])
            flow_manager.state["listed_appointments"] = {a.appointment_id: a for a in appointments}
            result = {
                "appointments": [
                    {
                        "appointment_id": a.appointment_id,
                        "provider": a.provider_name,
                        "time": spoken_time(a.start),
                        "visit_type": VISIT_TYPES[a.visit_type],
                    }
                    for a in appointments
                ]
            }
            # With nothing to change, stay where the Caller can book instead.
            return result, self.choose_appointment_node() if appointments else None

        return FlowsFunctionSchema(
            name="list_appointments",
            description="Look up the caller's upcoming appointments, to tell them or to reschedule or cancel one.",
            properties={},
            required=[],
            handler=with_holding_line(list_appointments),
            cancel_on_interruption=True,
            timeout_secs=8,
        )

    def choose_to_cancel_tool(self) -> FlowsFunctionSchema:
        async def choose_appointment_to_cancel(args: dict, flow_manager: FlowManager):
            appointment = _listed(args, flow_manager)
            if appointment is None:
                return _NOT_LISTED, None
            return {"status": "chosen"}, self.cancel_read_back_node(appointment)

        return FlowsFunctionSchema(
            name="choose_appointment_to_cancel",
            description="Pick the listed appointment the caller wants to cancel, before reading it back.",
            properties=_APPOINTMENT_ID,
            required=["appointment_id"],
            handler=choose_appointment_to_cancel,
            cancel_on_interruption=True,
        )

    def choose_to_reschedule_tool(self) -> FlowsFunctionSchema:
        async def choose_appointment_to_reschedule(args: dict, flow_manager: FlowManager):
            appointment = _listed(args, flow_manager)
            if appointment is None:
                return _NOT_LISTED, None
            rescheduling = _Rescheduling(self.ehr, self.providers, self.exits, appointment)
            return {"status": "chosen"}, rescheduling.find_slot_node()

        return FlowsFunctionSchema(
            name="choose_appointment_to_reschedule",
            description="Pick the listed appointment the caller wants to move to another time.",
            properties=_APPOINTMENT_ID,
            required=["appointment_id"],
            handler=choose_appointment_to_reschedule,
            cancel_on_interruption=True,
        )

    def cancel_tool(self, appointment: Appointment) -> FlowsFunctionSchema:
        # One key per Read-back: a retry of this Cancel is the same write.
        idempotency_key = str(uuid.uuid4())

        async def cancel_appointment(args: dict, flow_manager: FlowManager):
            patient_id = flow_manager.state["patient_id"]

            async def cancel() -> WriteOutcome:
                return await self.ehr.cancel(
                    patient_id=patient_id, appointment_id=appointment.appointment_id, idempotency_key=idempotency_key
                )

            async def is_cancelled() -> bool:
                listed = await self.ehr.appointments(patient_id)
                if any(a.appointment_id == appointment.appointment_id for a in listed):
                    return False
                return await self.ehr.slot_is_free(appointment.slot_id)

            written = await write_until_settled(cancel, is_cancelled)
            if written.outcome == "succeeded":
                cancelled = f"Your {_details(appointment)} is cancelled. {ANYTHING_ELSE}"
                return {"outcome": "succeeded"}, self.exits.back_to_intent(cancelled)
            if written.outcome == "rejected":
                result = {"outcome": "rejected", "reason": written.reason}
                return result, self.exits.back_to_intent(CHANGE_REJECTED[written.reason])
            reason = unsettled(written, verb="cancel", done="cancelled", details=_details(appointment))
            return {"outcome": written.outcome}, await handoff(self.ehr, flow_manager, reason)

        return FlowsFunctionSchema(
            name="cancel_appointment",
            description="Cancel exactly the appointment you read back. Call only after the caller clearly says yes to it.",
            properties={},
            required=[],
            handler=with_holding_line(cancel_appointment),
            # A write must not be dropped halfway because the Caller spoke.
            cancel_on_interruption=False,
            timeout_secs=WRITE_TOOL_TIMEOUT_SECS,
        )

    def choose_appointment_node(self) -> NodeConfig:
        return {
            "name": "choose_appointment",
            "task_messages": [{"role": "developer", "content": CHOOSE_APPOINTMENT_TASK}],
            "functions": [self.choose_to_reschedule_tool(), self.choose_to_cancel_tool()],
        }

    def cancel_read_back_node(self, appointment: Appointment) -> NodeConfig:
        line = f"Just to confirm, you'd like to cancel your {_details(appointment)}. Is that right?"
        return {
            "name": "cancel_read_back",
            "pre_actions": [{"type": "tts_say", "text": line}],
            "task_messages": [{"role": "developer", "content": CANCEL_READ_BACK_TASK}],
            "functions": [self.cancel_tool(appointment), self.choose_to_cancel_tool(), self.choose_to_reschedule_tool()],
            "respond_immediately": False,
        }


@dataclass(frozen=True)
class _Rescheduling(Booking):
    """Find slot for a new time for an existing Appointment, then its own Read-back and write."""

    appointment: Appointment

    find_slot_task: ClassVar[str] = RESCHEDULE_FIND_SLOT_TASK
    choose_slot_description: ClassVar[str] = (
        "Pick the offered time the caller wants to move the appointment to, before reading it back."
    )

    @property
    def choice_properties(self) -> dict:
        return {}

    def chosen(self, slot: Slot, args: dict) -> tuple[dict, NodeConfig | None]:
        return {"status": "chosen"}, self.reschedule_read_back_node(slot)

    def reschedule_tool(self, slot: Slot) -> FlowsFunctionSchema:
        # One key per Read-back: a retry of this Reschedule is the same write.
        idempotency_key = str(uuid.uuid4())

        async def reschedule_appointment(args: dict, flow_manager: FlowManager):
            patient_id = flow_manager.state["patient_id"]
            appointment_id = self.appointment.appointment_id

            async def reschedule() -> WriteOutcome:
                return await self.ehr.reschedule(
                    patient_id=patient_id, appointment_id=appointment_id, slot_id=slot.slot_id, idempotency_key=idempotency_key
                )

            async def is_moved() -> bool:
                listed = await self.ehr.appointments(patient_id)
                return any(a.appointment_id == appointment_id and a.slot_id == slot.slot_id for a in listed)

            written = await write_until_settled(reschedule, is_moved)
            if written.outcome == "succeeded":
                moved = (
                    f"Done. Your {VISIT_TYPES[self.appointment.visit_type]} is now on {spoken_time(slot.start)} "
                    f"with {slot.provider_name}. {ANYTHING_ELSE}"
                )
                return {"outcome": "succeeded"}, self.exits.back_to_intent(moved)
            if written.outcome == "rejected" and written.reason in CHANGE_REJECTED:
                result = {"outcome": "rejected", "reason": written.reason}
                return result, self.exits.back_to_intent(CHANGE_REJECTED[written.reason])
            if written.outcome == "rejected":
                return await self.slot_lost(slot, written.reason, flow_manager)
            details = f"{_details(self.appointment)}, to {spoken_time(slot.start)} with {slot.provider_name}"
            reason = unsettled(written, verb="move", done="moved", details=details)
            return {"outcome": written.outcome}, await handoff(self.ehr, flow_manager, reason)

        return FlowsFunctionSchema(
            name="reschedule_appointment",
            description="Move the appointment exactly as you read back. Call only after the caller clearly says yes to it.",
            properties={},
            required=[],
            handler=with_holding_line(reschedule_appointment),
            # A write must not be dropped halfway because the Caller spoke.
            cancel_on_interruption=False,
            timeout_secs=WRITE_TOOL_TIMEOUT_SECS,
        )

    def reschedule_read_back_node(self, slot: Slot) -> NodeConfig:
        line = (
            f"Just to confirm, you'd like to move your {_details(self.appointment)} "
            f"to {spoken_time(slot.start)} with {slot.provider_name}. Is that right?"
        )
        return {
            "name": "reschedule_read_back",
            "pre_actions": [{"type": "tts_say", "text": line}],
            "task_messages": [{"role": "developer", "content": RESCHEDULE_READ_BACK_TASK}],
            "functions": [self.reschedule_tool(slot), self.choose_slot_tool(), self.find_slots_tool()],
            "respond_immediately": False,
        }


def list_appointments_tool(ehr: EhrAdapter, providers: list[Provider], exits: Exits) -> FlowsFunctionSchema:
    """The way into Reschedule and Cancel. Offer it once the Caller is a Verified Patient."""
    return _Appointments(ehr, providers, exits).list_appointments_tool()


_APPOINTMENT_ID = {
    "appointment_id": {"type": "string", "description": "The appointment_id list_appointments returned for it"}
}

_NOT_LISTED = {"status": "not_listed", "error": "Pick an appointment_id that list_appointments returned"}


def _listed(args: dict, flow_manager: FlowManager) -> Appointment | None:
    return flow_manager.state.get("listed_appointments", {}).get(args.get("appointment_id"))


def _details(appointment: Appointment) -> str:
    return spoken_appointment(appointment.visit_type, appointment.provider_name, appointment.start)
