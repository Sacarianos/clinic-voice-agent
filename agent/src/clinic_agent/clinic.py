"""What the agent can say about the clinic itself. Static config: it never touches the EHR or a Patient's record.

The Providers match the seeded ones in fhir/src/clinic_seed/clinic.py. What each one focuses on is made up.
"""

from pipecat.flows import FlowManager, FlowsFunctionSchema

PROVIDER_NAMES = ["Marcus Whitfield", "Wojciech Szczepanski", "Siobhan Kowalczyk"]

CLINIC_INFO = {
    "hours": "Open Monday through Friday, 8 AM to 5 PM Eastern. Closed on weekends and holidays.",
    "address": "1420 Cedar Hollow Road, Suite 200, Maple Ridge. It is the brick building next to the pharmacy.",
    "parking": "Free parking in the lot behind the building. Accessible spaces are by the back entrance.",
    "providers": (
        "Dr. Marcus Whitfield, a physician, sees adults for annual physicals and general primary care. "
        "Dr. Wojciech Szczepanski, a physician, focuses on chronic conditions such as diabetes and high blood pressure. "
        "Siobhan Kowalczyk, a nurse practitioner, handles sick visits and follow-ups, and often has the soonest openings."
    ),
}


def clinic_info_tool() -> FlowsFunctionSchema:
    async def get_clinic_info(args: dict, flow_manager: FlowManager):
        return dict(CLINIC_INFO), None

    return FlowsFunctionSchema(
        name="get_clinic_info",
        description=(
            "Look up the clinic's hours, address, parking, and which provider does what. "
            "Use it to answer any question about the clinic itself, whether or not the caller has verified. "
            "Answer only what was asked, in a sentence or two."
        ),
        properties={},
        required=[],
        handler=get_clinic_info,
        cancel_on_interruption=True,
    )
