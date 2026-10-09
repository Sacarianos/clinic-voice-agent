"""Handoff and Emergency Redirect: the two ways a call ends with clinic staff following up.

Both are offered in every conversation node, through the flow's global functions, so a Caller can
reach them before Identity Verification and from any state after it.

A Callback Request calls back the call's caller ID. A call without one, such as a browser call, asks
the Caller for a number instead. A Handoff reads that number back and files only once the Caller says
it is right. An Emergency Redirect says the 911 line first and only then asks, and files the number at
once, so a Caller in an emergency is never held up. Nothing is ever filed without a real number.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from loguru import logger
from pipecat.flows import FlowManager, FlowsFunctionSchema, NodeConfig
from pipecat.frames.frames import TTSSpeakFrame

from clinic_agent.audit import Write
from clinic_agent.ehr import EhrAdapter
from clinic_agent.holding import Handler, with_holding_line
from clinic_agent.phi import PHI, PHONE
from clinic_agent.timeouts import tool_timeout


@dataclass(frozen=True)
class HandoffReason:
    filed_as: str  # what clinic staff read on the Callback Request
    told_to_caller: str  # what the Caller hears


HANDOFF_REASONS = {
    "asked_for_person": HandoffReason(
        "Caller asked to speak to a person",
        "Of course. I'll have a member of our staff call you back at this number. Goodbye.",
    ),
    "proxy_caller": HandoffReason(
        "Caller is calling on behalf of someone else (Proxy Caller)",
        "Since you're calling for someone else, a member of our staff will need to help you. "
        "They'll call you back at this number. Goodbye.",
    ),
    "new_patient": HandoffReason(
        "Caller is not a patient yet and wants to register",
        "To get you set up as a new patient, a member of our staff will call you back at this number. Goodbye.",
    ),
    "clinical_question": HandoffReason(
        "Caller has a clinical question",
        "That's a question for our clinical team. I've asked them to call you back at this number. Goodbye.",
    ),
}

EMERGENCY_REASON = "Emergency: caller described an emergency and was told to hang up and dial 911"
EMERGENCY_REDIRECT = "This sounds like an emergency. Please hang up now and dial 911."

ASK_FOR_CALLBACK_NUMBER = "Before I let you go, what's the best phone number for our staff to call you back on?"
ASK_FOR_CALLBACK_NUMBER_AGAIN = "Sorry about that. What's the best number to reach you?"
# Said right after the 911 line. Giving a number is optional and never stands between the Caller and 911.
EMERGENCY_ASK_FOR_CALLBACK_NUMBER = (
    "If you can, tell me a phone number where our staff can reach you later. If not, please hang up and dial 911 now."
)
EMERGENCY_GOODBYE = "Thank you. Please hang up now and dial 911. Goodbye."
NOT_A_PHONE_NUMBER = "That is not a 10-digit phone number. Ask for it again."

CALLBACK_NUMBER_TASK = """\
This call shows no phone number, so the clinic has no number to call the caller back on. You asked for one.
When the caller says it, call record_callback_number with its digits, such as 5555550123. Don't read it
back yourself: the tool does. If they ask something else, answer briefly and ask for the number again.
"""

CONFIRM_CALLBACK_NUMBER_TASK = """\
You just read back the caller's phone number and asked if it is right. Call confirm_callback_number with
correct true when they clearly say yes, and false when they say no or give a different number.
"""

EMERGENCY_CALLBACK_NUMBER_TASK = """\
The caller described an emergency and was told to hang up and dial 911. You then asked, only if they can,
for a number where staff can reach them later. If they say one, call record_callback_number with its
digits at once, without reading it back. Otherwise tell them again to hang up and dial 911. Ask nothing else.
"""

# Said only when the Callback Request could not be saved, so the Caller is never told a callback is coming when it isn't.
COULD_NOT_FILE_CALLBACK_REQUEST = (
    "I'm sorry, I wasn't able to save your request. Please call us again in a few minutes. Goodbye."
)

# A write that times out may still have landed. A second Callback Request is better than none.
FILING_ATTEMPTS = 2
FILING_TIMEOUT_SECS = tool_timeout(FILING_ATTEMPTS)

EMERGENCY_SIGNS = (
    "chest pain, trouble breathing, signs of a stroke, severe bleeding, a possible overdose or poisoning, "
    "a severe allergic reaction, loss of consciousness, thoughts of suicide or self-harm, "
    "or anything else the caller says is an emergency"
)


def callback_request_tools(ehr: EhrAdapter) -> list[FlowsFunctionSchema]:
    return [_handoff_tool(ehr), _emergency_redirect_tool(ehr)]


def set_callback_number(flow_manager: FlowManager, phone_number: str) -> None:
    """The number a Callback Request on this call calls back: the caller ID, or one the Caller gave."""
    flow_manager.state["callback_number"] = phone_number
    PHI.learn(phone_number, PHONE)


async def handoff(ehr: EhrAdapter, flow_manager: FlowManager, reason: HandoffReason) -> NodeConfig:
    """Files the Callback Request, then returns the node that tells the Caller staff will call and hangs up.

    Without a number to call back, it returns the node that asks for one and files once it is confirmed.
    """
    if "callback_number" not in flow_manager.state:
        return _ending_at(flow_manager, _callback_number_node(ehr, reason, ASK_FOR_CALLBACK_NUMBER))
    filed = await _file_callback_request(ehr, flow_manager, reason.filed_as, emergency=False)
    goodbye = reason.told_to_caller if filed else COULD_NOT_FILE_CALLBACK_REQUEST
    return _ending_at(flow_manager, _closing_node("handoff", goodbye))


async def emergency_redirect(ehr: EhrAdapter, flow_manager: FlowManager) -> NodeConfig:
    """Tells the Caller to dial 911 first, files an emergency Callback Request, and returns the node that hangs up.

    Without a number to call back, it asks for one after the 911 line and files it as soon as it is given.
    """
    await flow_manager.worker.queue_frame(TTSSpeakFrame(EMERGENCY_REDIRECT))
    if "callback_number" not in flow_manager.state:
        return _ending_at(flow_manager, _emergency_callback_number_node(ehr))
    await _file_callback_request(ehr, flow_manager, EMERGENCY_REASON, emergency=True)
    return _ending_at(flow_manager, _closing_node("emergency_redirect", None))


def unless_the_call_is_ending(handler: Handler) -> Handler:
    """For every tool a node offers besides Handoff and Emergency Redirect.

    A model can call several tools in one reply, and the last one to finish picks the next node. Once a
    Handoff or Emergency Redirect has picked one, a tool called alongside it leads there too, so a
    verification or a slot search in the same reply can't carry the call on. One that comes after the
    Handoff doesn't run at all.
    """

    async def handle(args: dict, flow_manager: FlowManager):
        if _ENDING_AT in flow_manager.state:
            return {"status": "not_run", "reason": "the call is ending"}, flow_manager.state[_ENDING_AT]
        result, next_node = await handler(args, flow_manager)
        return result, flow_manager.state.get(_ENDING_AT, next_node)

    return handle


# The node a Handoff or Emergency Redirect leads to, once one has started.
_ENDING_AT = "ending_at"


def _ending_at(flow_manager: FlowManager, node: NodeConfig) -> NodeConfig:
    flow_manager.state[_ENDING_AT] = node
    return node


def callback_number(said: str) -> str | None:
    """A US phone number in E.164 form, or None when the digits aren't one."""
    digits = re.sub(r"\D", "", said)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # A US area code never starts with 0 or 1.
    if len(digits) != 10 or digits[0] in "01":
        return None
    return f"+1{digits}"


def _spoken(phone_number: str) -> str:
    digits = phone_number.removeprefix("+1")
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def _callback_number_node(ehr: EhrAdapter, reason: HandoffReason, line: str) -> NodeConfig:
    async def read_back(phone_number: str, flow_manager: FlowManager) -> NodeConfig:
        return _confirm_callback_number_node(ehr, reason, phone_number)

    return {
        "name": "callback_number",
        "pre_actions": [{"type": "tts_say", "text": line}],
        "task_messages": [{"role": "developer", "content": CALLBACK_NUMBER_TASK}],
        "functions": [_record_callback_number_tool(read_back)],
        "respond_immediately": False,
    }


def _confirm_callback_number_node(ehr: EhrAdapter, reason: HandoffReason, phone_number: str) -> NodeConfig:
    async def confirm_callback_number(args: dict, flow_manager: FlowManager):
        correct = args.get("correct")
        if not isinstance(correct, bool):
            return {"status": "error", "error": "correct must be true or false"}, None
        if not correct:
            return {"status": "asking_again"}, _callback_number_node(ehr, reason, ASK_FOR_CALLBACK_NUMBER_AGAIN)
        set_callback_number(flow_manager, phone_number)
        return {"status": "confirmed"}, await handoff(ehr, flow_manager, reason)

    confirm_tool = FlowsFunctionSchema(
        name="confirm_callback_number",
        description="Record whether the caller said the phone number you just read back is right.",
        properties={"correct": {"type": "boolean", "description": "True only when the caller clearly said yes"}},
        required=["correct"],
        handler=with_holding_line(confirm_callback_number),
        cancel_on_interruption=False,
        timeout_secs=FILING_TIMEOUT_SECS,
    )
    return {
        "name": "confirm_callback_number",
        "pre_actions": [{"type": "tts_say", "text": f"I have {_spoken(phone_number)}. Is that right?"}],
        "task_messages": [{"role": "developer", "content": CONFIRM_CALLBACK_NUMBER_TASK}],
        "functions": [confirm_tool],
        "respond_immediately": False,
    }


def _emergency_callback_number_node(ehr: EhrAdapter) -> NodeConfig:
    async def file_at_once(phone_number: str, flow_manager: FlowManager) -> NodeConfig:
        set_callback_number(flow_manager, phone_number)
        await _file_callback_request(ehr, flow_manager, EMERGENCY_REASON, emergency=True)
        return _closing_node("emergency_redirect", EMERGENCY_GOODBYE)

    return {
        "name": "emergency_callback_number",
        "pre_actions": [{"type": "tts_say", "text": EMERGENCY_ASK_FOR_CALLBACK_NUMBER}],
        "task_messages": [{"role": "developer", "content": EMERGENCY_CALLBACK_NUMBER_TASK}],
        "functions": [_record_callback_number_tool(file_at_once, timeout_secs=FILING_TIMEOUT_SECS)],
        "respond_immediately": False,
    }


def _record_callback_number_tool(
    then: Callable[[str, FlowManager], Awaitable[NodeConfig]], *, timeout_secs: float | None = None
) -> FlowsFunctionSchema:
    async def record_callback_number(args: dict, flow_manager: FlowManager):
        phone_number = callback_number(str(args.get("phone_number", "")))
        if phone_number is None:
            return {"status": "error", "error": NOT_A_PHONE_NUMBER}, None
        return {"status": "recorded"}, await then(phone_number, flow_manager)

    return FlowsFunctionSchema(
        name="record_callback_number",
        description="Record the phone number the caller gave for staff to call them back on.",
        properties={"phone_number": {"type": "string", "description": "The number's digits, such as 5555550123"}},
        required=["phone_number"],
        handler=with_holding_line(record_callback_number),
        cancel_on_interruption=False,
        timeout_secs=timeout_secs,
    )


def _closing_node(name: str, goodbye: str | None) -> NodeConfig:
    return {
        "name": name,
        "task_messages": [],
        "functions": [],
        "pre_actions": [{"type": "end_conversation", **({"text": goodbye} if goodbye else {})}],
        "respond_immediately": False,
    }


def _handoff_tool(ehr: EhrAdapter) -> FlowsFunctionSchema:
    async def handle_handoff(args: dict, flow_manager: FlowManager):
        reason = HANDOFF_REASONS.get(args["reason"])
        if reason is None:
            return {"status": "error", "error": f"unknown reason {args['reason']!r}"}, None
        return {"status": "handing_off"}, await handoff(ehr, flow_manager, reason)

    return FlowsFunctionSchema(
        name="handoff",
        description=(
            "End the call and have clinic staff call the caller back later. The clinic never transfers a call, so say nothing "
            "before calling it: the tool tells the caller that staff will call them back. Use it when the caller asks for a person (a human, staff, the front desk), "
            "is calling for someone else, is not a patient yet, or asks for medical advice "
            "(what a symptom means, medication, test results, treatment). Do not answer those yourself. "
            "Not for a caller who feels unwell and wants an appointment: help them book a sick visit instead."
        ),
        properties={
            "reason": {
                "type": "string",
                "enum": list(HANDOFF_REASONS),
                "description": "Why the caller needs staff",
            }
        },
        required=["reason"],
        handler=with_holding_line(handle_handoff),
        cancel_on_interruption=False,
        timeout_secs=FILING_TIMEOUT_SECS,
    )


def _emergency_redirect_tool(ehr: EhrAdapter) -> FlowsFunctionSchema:
    async def redirect(args: dict, flow_manager: FlowManager):
        return {"status": "redirecting_to_911"}, await emergency_redirect(ehr, flow_manager)

    return FlowsFunctionSchema(
        name="emergency_redirect",
        description=(
            f"Use at once, at any point in the call, when the caller mentions an emergency: {EMERGENCY_SIGNS}. "
            "It tells them to hang up and dial 911 and ends the call. Do not ask them anything first."
        ),
        properties={},
        required=[],
        handler=redirect,
        cancel_on_interruption=False,
        timeout_secs=FILING_TIMEOUT_SECS,
    )


async def _file_callback_request(ehr: EhrAdapter, flow_manager: FlowManager, reason: str, *, emergency: bool) -> bool:
    patient_id = flow_manager.state.get("patient_id")
    write = Write("emergency_callback_request" if emergency else "callback_request", patient_id)
    for attempt in range(1, FILING_ATTEMPTS + 1):
        outcome, error = "error", None
        try:
            await ehr.create_callback_request(
                phone_number=flow_manager.state["callback_number"],
                reason=reason,
                emergency=emergency,
                patient_id=patient_id,
            )
            outcome = "succeeded"
            return True
        except httpx.HTTPError as failure:
            outcome, error = "failed", type(failure).__name__
            logger.warning(f"Callback Request attempt {attempt} failed: {error}")
        except BaseException as raised:
            error = type(raised).__name__
            raise
        finally:
            ehr.audit_log.record(write, attempt=attempt, outcome=outcome, reason=error)
    logger.error("Callback Request could not be filed")
    return False
