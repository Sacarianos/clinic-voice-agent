"""The conversation the Caller has with the agent, as a pipecat.flows state machine.

The phone and the text transport run the same flow. Per ADR 0003 each node offers the LLM only its
own tools, so nothing past Identity Verification can be reached before it succeeds. Never register a
tool on the LLM directly: a handler registered that way runs whatever the current node offers.
"""

from pipecat.flows import FlowManager, FlowsFunctionSchema, NodeConfig

from clinic_agent.appointments import list_appointments_tool
from clinic_agent.booking import Exits, find_slots_tool
from clinic_agent.clinic import clinic_info_tool
from clinic_agent.ehr import EhrAdapter
from clinic_agent.escalation import FILING_ATTEMPTS, HANDOFF_REASONS, HandoffReason, escalation_tools, handoff
from clinic_agent.holding import with_holding_line
from clinic_agent.phi import PHI, PHONE
from clinic_agent.pipeline import Call
from clinic_agent.timeouts import tool_timeout

CLINIC_NAME = "Cedar Hollow Family Medicine"

ROLE = f"""\
You are the receptionist answering the phone at {CLINIC_NAME}, a family medicine clinic.
You are on a live phone call. Everything you write is spoken aloud by a voice, so:
- Speak in short, natural sentences, one or two at a time, then let the caller talk.
- Never use lists, markdown, emoji or symbols that sound wrong when read aloud.
- Be warm, calm and plain-spoken.
Never give medical advice. If the caller mentions an emergency at any point, call emergency_redirect at once.
If they ask for a person, call for someone else, are not a patient yet, or ask a clinical question, call handoff.
The clinic never transfers or connects calls. Handoff means staff call the caller back later at the number they are calling from.
Call handoff without saying anything first, because the tool says the goodbye. Never say you will transfer, connect or put the caller through to anyone, and never ask them to hold.
A clinical question asks for medical advice: what a symptom means, what to take or stop taking, test
results, or how to treat something. A caller who feels unwell and wants to be seen is not asking that.
They want an appointment, usually a sick visit, and you help them book it.
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

# Never a guess between Patients. The Caller hears the same words as after a second failed attempt.
AMBIGUOUS_AFTER_SPELLING = HandoffReason(
    "Identity Verification matched more than one Patient, even with the last name spelled",
    HANDOFF_AFTER_FAILED_VERIFICATION,
)

VERIFY_IDENTITY_TASK = """\
Before you can help with anything about appointments, the caller must prove who they are.
Ask for their first and last name and their date of birth, if they haven't given them yet.
Once you have all three, call verify_patient right away, without reading them back.
Pass the date of birth as YYYY-MM-DD.
Never say whether a person is a patient here, and never use the caller's phone number as proof.

A caller who wants an appointment, including one who says they feel sick or describes symptoms and
wants to come in, needs verifying first like anyone else: ask for their name and date of birth.

Three things come before verification. When the caller's words fit one, act on it at once, without asking
what they need or for their details first:
- An emergency: call emergency_redirect at once.
- A request to speak to a person ("can I talk to a real person", "let me speak to a human"), a caller
  phoning for someone else, a caller who isn't a patient yet, or a request for medical advice ("should I
  take this", "what do my results mean"): call handoff right away. Don't try to help, and don't ask for
  their name first. Staff will call them back. "I need to see someone" means an appointment, not a person.
- A question about the clinic's hours, address, parking or providers: call get_clinic_info and answer
  only from what it returns. Never answer from memory. Then go back to asking for what you still need.
"""

# Without its own task, Haiku often took a spelled-out name for a caller who isn't a patient yet and handed off.
SPELLING_TASK = """\
The caller's name matched more than one of the clinic's records, so you asked them to spell their last name.
This is not a failed attempt, and it says nothing about whether they are a patient here.
As soon as they have spelled it, call verify_patient again with the same first name and date of birth, and
the last name written out from the letters they spelled (S, M, I, T, H is SMITH). Don't read it back first.
"""

INTENT_TASK = """\
The caller is now a Verified Patient. Help them with what they called about: booking,
rescheduling or cancelling an appointment, or a question about the clinic.
For a question about the clinic, call get_clinic_info and answer only from what it returns.
For a request for medical advice or for a person, call handoff. For an emergency, call emergency_redirect.
Feeling unwell and wanting to be seen is a booking, usually a sick visit.
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
    PHI.learn(caller_phone, PHONE)
    await flow.initialize(_verify_identity_node(GREETING, ehr))
    return flow


def _verify_identity_node(opening_line: str, ehr: EhrAdapter, task: str = VERIFY_IDENTITY_TASK) -> NodeConfig:
    return {
        "name": "verify_identity",
        "role_message": ROLE,
        "pre_actions": [{"type": "tts_say", "text": opening_line}],
        "task_messages": [{"role": "developer", "content": task}],
        "functions": [_verify_patient_tool(ehr)],
        "respond_immediately": False,
    }


def _verify_patient_tool(ehr: EhrAdapter) -> FlowsFunctionSchema:
    async def verify_patient(args: dict, flow_manager: FlowManager):
        caller_is_the_patient = args.get("caller_is_the_patient")
        if not isinstance(caller_is_the_patient, bool):
            return {"status": "error", "error": "caller_is_the_patient must be true or false"}, None
        if not caller_is_the_patient:
            # A Proxy Caller's details are someone else's. They never reach the EHR, so no record is verified or linked.
            return {"status": "proxy_caller"}, await handoff(ehr, flow_manager, HANDOFF_REASONS["proxy_caller"])
        # The flow, not the LLM, knows a spelling request came before this attempt (ADR 0003).
        spelled = flow_manager.state.pop("spelling_requested", False)
        verification = await ehr.verify_patient(
            given_name=args["given_name"],
            family_name=args["family_name"],
            date_of_birth=args["date_of_birth"],
            family_name_spelled=spelled,
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
        if spelled:
            return {"status": "ambiguous"}, await handoff(ehr, flow_manager, AMBIGUOUS_AFTER_SPELLING)
        flow_manager.state["spelling_requested"] = True
        return {"status": "ambiguous"}, _verify_identity_node(SPELL_LAST_NAME, ehr, SPELLING_TASK)

    return FlowsFunctionSchema(
        name="verify_patient",
        description="Check the caller's name and date of birth against the clinic's patients.",
        properties={
            "given_name": {"type": "string", "description": "The caller's first name"},
            "family_name": {"type": "string", "description": "The caller's last name, as said or spelled"},
            "date_of_birth": {"type": "string", "description": "Date of birth as YYYY-MM-DD"},
            "caller_is_the_patient": {
                "type": "boolean",
                "description": (
                    "False if the caller has said they are calling for someone else, such as a parent, child, "
                    "partner or a person they care for, and these are that person's details. True only when "
                    "the details are the caller's own."
                ),
            },
        },
        required=["given_name", "family_name", "date_of_birth", "caller_is_the_patient"],
        handler=with_holding_line(verify_patient),
        # A read: if the Caller talks over it, drop it rather than answer a question they moved past.
        cancel_on_interruption=True,
        # The verification, then either the Providers for the intent node or the Callback Request of a Handoff.
        timeout_secs=tool_timeout(1 + max(1, FILING_ATTEMPTS)),
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

    exits = Exits(back_to_intent=back_to_intent)
    functions = [find_slots_tool(ehr, providers, exits), list_appointments_tool(ehr, providers, exits)]
    return {
        "name": "intent",
        "task_messages": [{"role": "developer", "content": INTENT_TASK}],
        "functions": functions,
    }
