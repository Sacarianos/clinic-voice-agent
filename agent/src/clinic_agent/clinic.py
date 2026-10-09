"""What the agent can say about the clinic itself. Static config: it never touches the EHR or a Patient's record.

The time zone, hours and Providers come from clinic.json at the repo root, which the seed and the adapter
read too. What each Provider focuses on is made up.
"""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from pipecat.flows import FlowManager, FlowsFunctionSchema

_CLINIC = json.loads((Path(__file__).resolve().parents[3] / "clinic.json").read_text(encoding="utf-8"))

CLINIC_TIMEZONE = ZoneInfo(_CLINIC["timezone"])

PROVIDER_NAMES = [f"{provider['given']} {provider['family']}" for provider in _CLINIC["providers"]]


# What each Provider does, by their key in clinic.json.
_FOCUS = {
    "whitfield": "a physician, sees adults for annual physicals and general primary care",
    "szczepanski": "a physician, focuses on chronic conditions such as diabetes and high blood pressure",
    "kowalczyk": "a nurse practitioner, handles sick visits and follow-ups, and often has the soonest openings",
}


def _spoken_hour(hour: int) -> str:
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def _spoken_name(provider: dict) -> str:
    return " ".join(part for part in (provider["prefix"], provider["given"], provider["family"]) if part)


_HOURS = f"{_spoken_hour(_CLINIC['openingHour'])} to {_spoken_hour(_CLINIC['closingHour'])} {_CLINIC['timezoneSpoken']}"

CLINIC_INFO = {
    "hours": f"Open Monday through Friday, {_HOURS}. Closed on weekends and holidays.",
    "address": "1420 Cedar Hollow Road, Suite 200, Maple Ridge. It is the brick building next to the pharmacy.",
    "parking": "Free parking in the lot behind the building. Accessible spaces are by the back entrance.",
    "providers": " ".join(f"{_spoken_name(p)}, {_FOCUS[p['key']]}." for p in _CLINIC["providers"]),
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
