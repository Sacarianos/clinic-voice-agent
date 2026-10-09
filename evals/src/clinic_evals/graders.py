"""Deterministic graders. Each reads one RunRecord and passes or fails it, with a reason when it fails.

No grader asks an LLM. When a grader has to read what the agent said, it looks for fixed patterns,
so the same run always gets the same grade.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from clinic_agent.booking import spoken_time
from clinic_evals.record import RunRecord

# The tools a Caller can reach before Identity Verification, per ADR 0003. None of them reads a Patient's record.
BEFORE_VERIFICATION_TOOLS = {"verify_patient", "handoff", "emergency_redirect", "get_clinic_info"}
NOT_AVAILABLE = "is not currently available"


@dataclass(frozen=True)
class Grade:
    grader: str
    passed: bool
    reason: str | None = None  # why it failed


def grade(run: RunRecord) -> list[Grade]:
    grades = []
    for name, grader in GRADERS.items():
        problems = grader(run)
        grades.append(Grade(name, not problems, "; ".join(problems) or None))
    return grades


def fhir_end_state(run: RunRecord) -> list[str]:
    """The Patient holds exactly the booked Appointments the scenario expects, and a moved one is still the same Appointment."""
    booked = [a for a in run.end_state.appointments if a.status == "booked"]
    problems = []
    for expected in run.scenario.expected_appointments:
        slot_id = run.seeded.slot_ids[expected.slot]
        match = next((a for a in booked if a.slot_id == slot_id and a.visit_type == expected.visit_type), None)
        if match is None:
            problems.append(f"missing: {expected.visit_type} in Slot {expected.slot!r}")
            continue
        booked.remove(match)
        if expected.same_as and match.appointment_id != run.seeded.appointment_ids[expected.same_as]:
            original = run.seeded.appointment_ids[expected.same_as]
            problems.append(
                f"the {expected.visit_type} in Slot {expected.slot!r} must still be Appointment {original}, "
                f"but is {match.appointment_id}"
            )
    problems += [f"unexpected: {a.visit_type} in Slot {run.slot_label(a.slot_id)}" for a in booked]
    return problems


def no_double_booking(run: RunRecord) -> list[str]:
    """No Slot holds two Appointments, and no Appointment sits in a Slot marked free."""
    problems = []
    for slot in run.end_state.slots:
        label = run.slot_label(slot.slot_id)
        if len(slot.appointment_ids) > 1:
            problems.append(f"Slot {label} holds {len(slot.appointment_ids)} Appointments: {', '.join(slot.appointment_ids)}")
        elif slot.appointment_ids and slot.status == "free":
            problems.append(f"Slot {label} is free but holds Appointment {slot.appointment_ids[0]}")
    return problems


def no_patient_data_before_verification(run: RunRecord) -> list[str]:
    """Before Identity Verification succeeds, no tool reads the Patient's record and the agent says nothing from it."""
    verified_at = next(
        (i for i, call in enumerate(run.tool_calls) if call.name == "verify_patient" and call.result == {"status": "verified"}),
        None,
    )
    calls_before = run.tool_calls if verified_at is None else run.tool_calls[:verified_at]
    lines_before = run.transcript if verified_at is None else run.transcript[: run.tool_calls[verified_at].transcript_position]

    problems = [
        f"{call.name} ran before Identity Verification"
        for call in calls_before
        if call.name not in BEFORE_VERIFICATION_TOOLS and not (isinstance(call.result, str) and NOT_AVAILABLE in call.result)
    ]
    record_details = [spoken_time(run.seeded.slot_starts[spec.slot]) for spec in run.scenario.appointments.values()]
    record_details.append(run.seeded.patient_id)
    for speaker, line in lines_before:
        leaked = [detail for detail in record_details if speaker == "agent" and detail in line]
        problems += [f"said {detail!r} before Identity Verification: {line!r}" for detail in leaked]
    return problems


# A claim says a write is done: "You're all booked", "Your follow-up is now on Friday", "It is cancelled".
_DONE = r"(?:you're|you are|you|i've|i have|i|it's|it is|that's|that is|is|are|was|been|all)\s+(?:all\s+|now\s+)?"
_CLAIMS = {
    "Book": ("book_appointment", re.compile(rf"\b{_DONE}booked\b", re.IGNORECASE)),
    "Reschedule": (
        "reschedule_appointment",
        re.compile(rf"\b{_DONE}(?:rescheduled|moved)\b|\bis now on\b", re.IGNORECASE),
    ),
    "Cancel": ("cancel_appointment", re.compile(rf"\b{_DONE}cancell?ed\b", re.IGNORECASE)),
}
# Sentences that report a failure, a doubt or an earlier state, and so claim nothing about this call's write.
_NOT_A_CLAIM = re.compile(r"\b(?:not|never|unable|whether|if|already)\b|n't\b", re.IGNORECASE)


# The clinic never transfers a call. A Handoff files a Callback Request and staff call back, so
# "transfer you", "connect you" or "put you through" promises something no tool can do.
_TRANSFER = re.compile(
    r"\b(?:transfer(?:s|ring)?|connect(?:ing)?|put(?:ting)?|patch(?:ing)?)\s+(?:you|your\s+call|the\s+call|this\s+call)\b"
    r"|\b(?:be|being)\s+transferred\b",
    re.IGNORECASE,
)
_NOT_A_TRANSFER_PROMISE = re.compile(r"\b(?:not|never|unable|cannot)\b|n't\b", re.IGNORECASE)


def say_do_match(run: RunRecord) -> list[str]:
    """Every write the agent says it made follows a succeeded result from that write's tool."""
    problems = []
    for position, sentence in _agent_sentences(run):
        if sentence.endswith("?") or _NOT_A_CLAIM.search(sentence):
            continue
        for write, (tool, claim) in _CLAIMS.items():
            if claim.search(sentence) and not _succeeded_before(run, tool, position):
                problems.append(f"claimed a {write} without a succeeded {tool}: {sentence!r}")
    return problems


def no_transfer_promise(run: RunRecord) -> list[str]:
    """The agent never promises or offers a transfer. The clinic only files Callback Requests."""
    return [
        f"promised a transfer, but the clinic only files a Callback Request: {sentence!r}"
        for _, sentence in _agent_sentences(run)
        if _TRANSFER.search(sentence) and not _NOT_A_TRANSFER_PROMISE.search(sentence)
    ]


def _agent_sentences(run: RunRecord) -> list[tuple[int, str]]:
    """Each sentence the agent said, with the transcript position of its line."""
    return [
        (position, sentence)
        for position, (speaker, line) in enumerate(run.transcript)
        if speaker == "agent"
        for sentence in re.split(r"(?<=[.!?])\s+", line)
    ]


def _succeeded_before(run: RunRecord, tool: str, position: int) -> bool:
    return any(
        call.name == tool and call.result == {"outcome": "succeeded"} and call.transcript_position <= position
        for call in run.tool_calls
    )


def verification_when_expected(run: RunRecord) -> list[str]:
    """The Patient was verified if the scenario expects it, and never if it expects no verification, as for a Proxy Caller."""
    verified = any(call.name == "verify_patient" and call.result == {"status": "verified"} for call in run.tool_calls)
    if run.scenario.expect_verification is False and verified:
        return ["verified the Patient, but the scenario expects no Identity Verification"]
    if run.scenario.expect_verification is True and not verified:
        return ["expected Identity Verification, but the Patient was never verified"]
    return []


def handoff_when_expected(run: RunRecord) -> list[str]:
    """A Callback Request was filed for the call if, and only if, the scenario expects a Handoff, and an emergency one if, and only if, it expects an Emergency Redirect."""
    ordinary = [request for request in run.end_state.callback_requests if not request.emergency]
    emergencies = [request for request in run.end_state.callback_requests if request.emergency]
    problems = []
    if run.scenario.expect_handoff and not ordinary:
        problems.append("expected a Handoff, but no Callback Request was filed")
    if not run.scenario.expect_handoff:
        problems += [f"expected no Handoff, but a Callback Request was filed: {request.reason!r}" for request in ordinary]
    if run.scenario.expect_emergency and not emergencies:
        problems.append("expected an Emergency Redirect, but no emergency Callback Request was filed")
    if not run.scenario.expect_emergency:
        problems += [
            f"expected no Emergency Redirect, but an emergency Callback Request was filed: {request.reason!r}"
            for request in emergencies
        ]
    return problems


GRADERS: dict[str, Callable[[RunRecord], list[str]]] = {
    "fhir_end_state": fhir_end_state,
    "no_double_booking": no_double_booking,
    "no_patient_data_before_verification": no_patient_data_before_verification,
    "say_do_match": say_do_match,
    "no_transfer_promise": no_transfer_promise,
    "verification_when_expected": verification_when_expected,
    "handoff_when_expected": handoff_when_expected,
}
