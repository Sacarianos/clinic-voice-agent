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


def give_number(digits: str) -> CallTool:
    """The LLM records the callback number the Caller said, on a call without caller ID."""
    return CallTool("record_callback_number", {"phone_number": digits})


def confirm_number(correct: bool) -> CallTool:
    """The LLM records whether the Caller said the number read back to them is right."""
    return CallTool("confirm_callback_number", {"correct": correct})


def answer_read_back(answer: str) -> CallTool:
    """The LLM records what the Caller said to a Read-back: yes, no or change."""
    return CallTool("record_read_back_answer", {"answer": answer})


# Offered in every node. A node's own tools are what it offers besides these.
SHARED_TOOLS = {"handoff", "emergency_redirect", "get_clinic_info"}


def own_tools(offered: list[str]) -> set[str]:
    return set(offered) - SHARED_TOOLS
