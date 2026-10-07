"""The conversation the Caller has with the agent, as a pipecat.flows state machine.

The phone and the text transport run the same flow. Per ADR 0003 each node offers the LLM only its
own tools, so nothing past Identity Verification can be reached before it succeeds. Never register a
tool on the LLM directly: a handler registered that way runs whatever the current node offers.
"""

from pipecat.flows import FlowManager, FlowsFunctionSchema, NodeConfig

from clinic_agent.booking import Exits, find_slots_tool
from clinic_agent.clinic import clinic_info_tool
from clinic_agent.ehr import EhrAdapter
from clinic_agent.escalation import HandoffReason, escalation_tools, handoff
from clinic_agent.pipeline import Call

CLINIC_NAME = "Cedar Hollow Family Medicine"

ROLE = f"""\
You are the receptionist answering the phone at {CLINIC_NAME}, a family medicine clinic.
You are on a live phone call. Everything you write is spoken aloud by a voice, so:
- Speak in short, natural sentences, one or two at a time, then let the caller talk.
- Never use lists, markdown, emoji or symbols that sound wrong when read aloud.
- Be warm, calm and plain-spoken.
Never give medical advice. If the caller mentions an emergency at any point, call emergency_redirect at once.
If they ask for a person, call for someone else, are not a patient yet, or ask a clinical question, call handoff.
For questions about the clinic itself, call get_clinic_info and answer from what it returns.
"""

GREETING = f"Thank you for calling {CLINIC_NAME}. How can I help you today?"

# The same words whatever didn't match, so the line can't be used to learn who is a Patient here.
VERIFICATION_FAILED = (
    "I'm sorry, I couldn't verify those details. "
    "Could you tell me your full name and date of birth one more time?"
)

# Several Patients matched. Spelling settles it, and it doesn't count as a failed attempt.
SPELL_LAST_NAME = "Thanks. To be sure I find the right record, could you spell your last name for me?"

HANDOFF_AFTER_FAILED_VERIFICATION = (
    "I'm sorry, I wasn't able to verify your identity. "
    "A member of our staff will call you back to help. Goodbye."
)

MAX_FAILED_VERIFICATIONS = 2

VERIFY_IDENTITY_TASK = """\
Before you can help with anything about appointments, the caller must prove who they are.
Ask for their first and last name and their date of birth, if they haven't given them yet.
Once you have all three, call verify_patient right away, without reading them back.
Pass the date of birth as YYYY-MM-DD.
Never say whether a person is a patient here, and never use the caller's phone number as proof.

Three things come before verification. When the caller's words fit one, act on it at once, without asking
what they need or for their details first:
- An emergency: call emergency_redirect at once.
- A request to speak to a person ("can I talk to someone", "let me speak to a human"), a caller phoning
  for someone else, a caller who isn't a patient yet, or a clinical question: call handoff right away.
  Don't try to help, and don't ask for their name first. Staff will call them back.
- A question about the clinic's hours, address, parking or providers: call get_clinic_info and answer
  only from what it returns. Never answer from memory. Then go back to asking for what you still need.
"""

INTENT_TASK = """\
The caller is now a Verified Patient. Help them with what they called about: booking,
rescheduling or cancelling an appointment, or a question about the clinic.
For a question about the clinic, call get_clinic_info and answer only from what it returns.
For a clinical question or a request for a person, call handoff. For an emergency, call emergency_redirect.
"""


async def start_conversation(call: Call, ehr: EhrAdapter, caller_phone: str) -> FlowManager:
    """Greet the Caller and wait in Identity Verification. Call once the pipeline is running.

    The phone number is where a Callback Request calls back. It is never used to verify anyone.
    """
    flow = FlowManager(
        llm=call.llm,
        context_aggregator=call.aggregators,
        worker=call.worker,
        global_functions=[*escalation_tools(ehr), clinic_info_tool()],
    )
    flow.state["caller_phone"] = caller_phone
    await flow.initialize(_verify_identity_node(GREETING, ehr))
    return flow


def _verify_identity_node(opening_line: str, ehr: EhrAdapter) -> NodeConfig:
    return {
        "name": "verify_identity",
        "role_message": ROLE,
        "pre_actions": [{"type": "tts_say", "text": opening_line}],
        "task_messages": [{"role": "developer", "content": VERIFY_IDENTITY_TASK}],
        "functions": [_verify_patient_tool(ehr)],
        "respond_immediately": False,
    }


def _verify_patient_tool(ehr: EhrAdapter) -> FlowsFunctionSchema:
    async def verify_patient(args: dict, flow_manager: FlowManager):
        verification = await ehr.verify_patient(
            given_name=args["given_name"],
            family_name=args["family_name"],
            date_of_birth=args["date_of_birth"],
        )
        if verification.status == "verified":
            flow_manager.state["patient_id"] = verification.patient_id
            return {"status": "verified"}, await _intent_node(ehr)
        if verification.status == "not_verified":
            failed = flow_manager.state.get("failed_verifications", 0) + 1
            flow_manager.state["failed_verifications"] = failed
            if failed >= MAX_FAILED_VERIFICATIONS:
                reason = HandoffReason("Identity Verification failed twice", HANDOFF_AFTER_FAILED_VERIFICATION)
                return {"status": "not_verified"}, await handoff(ehr, flow_manager, reason)
            return {"status": "not_verified"}, _verify_identity_node(VERIFICATION_FAILED, ehr)
        return {"status": "ambiguous"}, _verify_identity_node(SPELL_LAST_NAME, ehr)

    return FlowsFunctionSchema(
        name="verify_patient",
        description="Check the caller's name and date of birth against the clinic's patients.",
        properties={
            "given_name": {"type": "string", "description": "The caller's first name"},
            "family_name": {"type": "string", "description": "The caller's last name, as said or spelled"},
            "date_of_birth": {"type": "string", "description": "Date of birth as YYYY-MM-DD"},
        },
        required=["given_name", "family_name", "date_of_birth"],
        handler=verify_patient,
        # A read: if the Caller talks over it, drop it rather than answer a question they moved past.
        cancel_on_interruption=True,
        timeout_secs=16,  # a second failure also files the Callback Request
    )


async def _intent_node(ehr: EhrAdapter) -> NodeConfig:
    providers = await ehr.providers()

    def back_to_intent(line: str) -> NodeConfig:
        return {
            "name": "intent",
            "pre_actions": [{"type": "tts_say", "text": line}],
            "task_messages": [{"role": "developer", "content": INTENT_TASK}],
            "functions": functions,
            "respond_immediately": False,
        }

    functions = [find_slots_tool(ehr, providers, Exits(booked=back_to_intent, handoff=_handoff_node))]
    return {
        "name": "intent",
        "task_messages": [{"role": "developer", "content": INTENT_TASK}],
        "functions": functions,
    }


# TODO(#10): Booking's Handoff still only says its message. File the Callback Request with escalation.handoff.
def _handoff_node(message: str) -> NodeConfig:
    return {
        "name": "handoff",
        "task_messages": [],
        "functions": [],
        "pre_actions": [{"type": "end_conversation", "text": message}],
        "respond_immediately": False,
    }
