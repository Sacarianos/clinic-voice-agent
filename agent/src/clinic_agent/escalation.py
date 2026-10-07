"""Handoff and Emergency Redirect: the two ways a call ends with clinic staff following up.

Both are offered in every conversation node, through the flow's global functions, so a Caller can
reach them before Identity Verification and from any state after it.
"""

from dataclasses import dataclass

import httpx
from loguru import logger
from pipecat.flows import FlowManager, FlowsFunctionSchema, NodeConfig
from pipecat.frames.frames import TTSSpeakFrame

from clinic_agent.ehr import EhrAdapter


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

# Said only when the Callback Request could not be saved, so the Caller is never told a callback is coming when it isn't.
COULD_NOT_FILE_CALLBACK_REQUEST = (
    "I'm sorry, I wasn't able to save your request. Please call us again in a few minutes. Goodbye."
)

# A write that times out may still have landed. A second Callback Request is better than none.
FILING_ATTEMPTS = 2
FILING_TIMEOUT_SECS = 15  # the adapter times out after 5 s, so two attempts fit

EMERGENCY_SIGNS = (
    "chest pain, trouble breathing, signs of a stroke, severe bleeding, a possible overdose or poisoning, "
    "a severe allergic reaction, loss of consciousness, thoughts of suicide or self-harm, "
    "or anything else the caller says is an emergency"
)


def escalation_tools(ehr: EhrAdapter) -> list[FlowsFunctionSchema]:
    return [_handoff_tool(ehr), _emergency_redirect_tool(ehr)]


async def handoff(ehr: EhrAdapter, flow_manager: FlowManager, reason: HandoffReason) -> NodeConfig:
    """Files the Callback Request, then returns the node that tells the Caller staff will call and hangs up."""
    filed = await _file_callback_request(ehr, flow_manager, reason.filed_as, emergency=False)
    return _closing_node("handoff", reason.told_to_caller if filed else COULD_NOT_FILE_CALLBACK_REQUEST)


async def emergency_redirect(ehr: EhrAdapter, flow_manager: FlowManager) -> NodeConfig:
    """Tells the Caller to dial 911 first, files an emergency Callback Request, and returns the node that hangs up."""
    await flow_manager.worker.queue_frame(TTSSpeakFrame(EMERGENCY_REDIRECT))
    await _file_callback_request(ehr, flow_manager, EMERGENCY_REASON, emergency=True)
    return _closing_node("emergency_redirect", None)


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
            "End the call and have clinic staff call the caller back. Use it when the caller asks for a person (a human, staff, the front desk), "
            "is calling for someone else, is not a patient yet, or asks a clinical or medical question "
            "(symptoms, medication, test results, treatment). Do not answer those yourself."
        ),
        properties={
            "reason": {
                "type": "string",
                "enum": list(HANDOFF_REASONS),
                "description": "Why the caller needs staff",
            }
        },
        required=["reason"],
        handler=handle_handoff,
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
    for attempt in range(1, FILING_ATTEMPTS + 1):
        try:
            await ehr.create_callback_request(
                phone_number=flow_manager.state["caller_phone"],
                reason=reason,
                emergency=emergency,
                patient_id=flow_manager.state.get("patient_id"),
            )
            return True
        except httpx.HTTPError as error:
            logger.warning(f"Callback Request attempt {attempt} failed: {type(error).__name__}")
    logger.error("Callback Request could not be filed")
    return False
