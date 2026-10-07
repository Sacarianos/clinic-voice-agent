"""Booking, the part of the conversation from Find slot through Read-back to the Book write.

Per ADR 0003 the write is reachable only one way. choose_slot fixes the exact Slot and Visit Type and
moves to the Read-back node, which speaks those details itself. Only that node offers
book_appointment, which takes no arguments: it books what was read back, under an idempotency key
made for that Read-back. Choosing again or searching again leaves the node, and the tool with it.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pipecat.flows import FlowManager, FlowsFunctionSchema, NodeConfig

from clinic_agent.ehr import EhrAdapter, Provider, Slot, SlotSearch

CLINIC_TIMEZONE = ZoneInfo("America/New_York")

VISIT_TYPES = {"annual_physical": "an annual physical", "sick_visit": "a sick visit", "follow_up": "a follow-up"}

SLOT_GONE = "I'm sorry, that time was just taken."

# TODO(#10): retry a failed Book once with the same key, reconcile an unknown one, and file the Callback Request.
BOOKING_FAILED = (
    "I'm sorry, I wasn't able to finish booking that appointment. "
    "A member of our staff will call you back to help. Goodbye."
)

FIND_SLOT_TASK = """\
Offer the caller the open times find_slots just returned: two or three at most, each with the
Provider's name, the day and the time. If none came back, say so and suggest another day, part of
day or Provider. If they asked for a date past booking_window_ends, tell them how far out they can book.
Find out what the visit is for: an annual physical, a sick visit or a follow-up.
Once the caller has picked one of the offered times and you know the visit type, call choose_slot.
If they want other times, call find_slots again.
"""

READ_BACK_TASK = """\
You just read the appointment details back to the caller and asked if they are right.
If the caller clearly says yes, call book_appointment. Never say the appointment is booked before it returns.
If they want a different time, Provider or visit type, call choose_slot for a time already offered,
or find_slots for new times. If they are unsure, answer their question and ask again.
"""


@dataclass(frozen=True)
class Exits:
    """Where booking hands the call back to the rest of the conversation."""

    booked: Callable[[str], NodeConfig]  # back to Intent, after saying this line
    handoff: Callable[[str], NodeConfig]  # Handoff with this message


@dataclass(frozen=True)
class _Booking:
    ehr: EhrAdapter
    providers: list[Provider]
    exits: Exits

    def find_slots_tool(self) -> FlowsFunctionSchema:
        async def find_slots(args: dict, flow_manager: FlowManager):
            provider = _provider_named(args.get("provider"), self.providers)
            if args.get("provider") and provider is None:
                return {"status": "unknown_provider", "providers": [p.name for p in self.providers]}, None
            search_args = {
                "provider_id": provider.provider_id if provider else None,
                "from_date": args.get("from_date"),
                "to_date": args.get("to_date"),
                "part_of_day": args.get("part_of_day"),
            }
            flow_manager.state["last_slot_search"] = search_args
            search = await self.ehr.find_slots(**search_args)
            return self._offer(search, flow_manager), self.find_slot_node()

        today = datetime.now(CLINIC_TIMEZONE).date()
        return FlowsFunctionSchema(
            name="find_slots",
            description=(
                "Find open times to book an appointment, by Provider, days and part of day. "
                f"Today is {_spoken_date(today)}, {today.isoformat()}. "
                "Leave out any filter the caller didn't ask for."
            ),
            properties={
                "provider": {
                    "type": "string",
                    "enum": [p.name for p in self.providers],
                    "description": "The Provider the caller asked for, if any",
                },
                "from_date": {"type": "string", "description": "First day to search, YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "Last day to search, YYYY-MM-DD"},
                "part_of_day": {"type": "string", "enum": ["morning", "afternoon"]},
            },
            required=[],
            handler=find_slots,
            cancel_on_interruption=True,
            timeout_secs=8,
        )

    def choose_slot_tool(self) -> FlowsFunctionSchema:
        async def choose_slot(args: dict, flow_manager: FlowManager):
            slot = flow_manager.state.get("offered_slots", {}).get(args.get("slot_id"))
            if slot is None:
                return {"status": "not_offered", "error": "Pick a slot_id that find_slots returned"}, None
            if args.get("visit_type") not in VISIT_TYPES:
                return {"status": "unknown_visit_type", "visit_types": list(VISIT_TYPES)}, None
            return {"status": "chosen"}, self.read_back_node(slot, args["visit_type"])

        return FlowsFunctionSchema(
            name="choose_slot",
            description="Pick the offered time the caller wants and the visit type, before reading them back.",
            properties={
                "slot_id": {"type": "string", "description": "The slot_id of the time the caller picked"},
                "visit_type": {"type": "string", "enum": list(VISIT_TYPES)},
            },
            required=["slot_id", "visit_type"],
            handler=choose_slot,
            cancel_on_interruption=True,
        )

    def book_tool(self, slot: Slot, visit_type: str) -> FlowsFunctionSchema:
        # One key per Read-back: a retry of this Book is the same write, never a second Appointment.
        idempotency_key = str(uuid.uuid4())

        async def book_appointment(args: dict, flow_manager: FlowManager):
            written = await self.ehr.book(
                patient_id=flow_manager.state["patient_id"],
                slot_id=slot.slot_id,
                visit_type=visit_type,
                idempotency_key=idempotency_key,
            )
            if written.outcome == "succeeded":
                booked = f"You're all booked: {_details(slot, visit_type)}. Is there anything else I can help with?"
                return {"outcome": "succeeded"}, self.exits.booked(booked)
            if written.outcome == "rejected":
                flow_manager.state.get("offered_slots", {}).pop(slot.slot_id, None)
                search = await self.ehr.find_slots(**flow_manager.state.get("last_slot_search", {}))
                result = {"outcome": "rejected", "reason": written.reason, **self._offer(search, flow_manager)}
                return result, self.find_slot_node(opening_line=SLOT_GONE)
            return {"outcome": written.outcome}, self.exits.handoff(BOOKING_FAILED)

        return FlowsFunctionSchema(
            name="book_appointment",
            description="Book exactly what you read back. Call only after the caller clearly says yes to it.",
            properties={},
            required=[],
            handler=book_appointment,
            # A write must not be dropped halfway because the Caller spoke.
            cancel_on_interruption=False,
            timeout_secs=10,
        )

    def find_slot_node(self, opening_line: str | None = None) -> NodeConfig:
        node: NodeConfig = {
            "name": "find_slot",
            "task_messages": [{"role": "developer", "content": FIND_SLOT_TASK}],
            "functions": [self.find_slots_tool(), self.choose_slot_tool()],
        }
        if opening_line:
            node["pre_actions"] = [{"type": "tts_say", "text": opening_line}]
        return node

    def read_back_node(self, slot: Slot, visit_type: str) -> NodeConfig:
        return {
            "name": "read_back",
            "pre_actions": [{"type": "tts_say", "text": f"Just to confirm, {_details(slot, visit_type)}. Is that right?"}],
            "task_messages": [{"role": "developer", "content": READ_BACK_TASK}],
            "functions": [self.book_tool(slot, visit_type), self.choose_slot_tool(), self.find_slots_tool()],
            "respond_immediately": False,
        }

    def _offer(self, search: SlotSearch, flow_manager: FlowManager) -> dict:
        offered = flow_manager.state.setdefault("offered_slots", {})
        offered.update({slot.slot_id: slot for slot in search.slots})
        return {
            "slots": [
                {"slot_id": slot.slot_id, "provider": slot.provider_name, "time": _spoken_time(slot.start)}
                for slot in search.slots
            ],
            "booking_window_ends": _spoken_date(search.booking_window_last_day),
        }


def find_slots_tool(ehr: EhrAdapter, providers: list[Provider], exits: Exits) -> FlowsFunctionSchema:
    """The way into booking: searching for open times. Offer it once the Caller is a Verified Patient."""
    return _Booking(ehr, providers, exits).find_slots_tool()


def _provider_named(name: str | None, providers: list[Provider]) -> Provider | None:
    if not name:
        return None
    wanted = name.casefold()
    exact = [p for p in providers if p.name.casefold() == wanted]
    by_surname = [p for p in providers if p.name.split()[-1].casefold() in wanted]
    return (exact or by_surname or [None])[0]


def _details(slot: Slot, visit_type: str) -> str:
    return f"{VISIT_TYPES[visit_type]} with {slot.provider_name} on {_spoken_time(slot.start)}"


def _spoken_date(day: date) -> str:
    return f"{day:%A}, {day:%B} {day.day}"


def _spoken_time(start: datetime) -> str:
    hour = start.hour % 12 or 12
    minutes = f":{start:%M}" if start.minute else ""
    return f"{_spoken_date(start)} at {hour}{minutes} {'AM' if start.hour < 12 else 'PM'}"
