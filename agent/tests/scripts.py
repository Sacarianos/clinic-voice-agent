"""Building blocks for scripted calls: what the Caller says and what the fake LLM does."""

from datetime import date

from fakes import CallTool


def spoken(iso_date: str) -> str:
    """A date the way a Caller says it."""
    day = date.fromisoformat(iso_date)
    return f"{day:%B} {day.day}, {day.year}"


def verify(given_name: str, family_name: str, date_of_birth: str, *, caller_is_the_patient: bool = True) -> CallTool:
    return CallTool(
        "verify_patient",
        {
            "given_name": given_name,
            "family_name": family_name,
            "date_of_birth": date_of_birth,
            "caller_is_the_patient": caller_is_the_patient,
        },
    )


def handoff(reason: str) -> CallTool:
    return CallTool("handoff", {"reason": reason})


def emergency_redirect() -> CallTool:
    return CallTool("emergency_redirect")
