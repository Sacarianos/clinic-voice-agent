"""The simulated Caller, against a local stand-in for the Anthropic Messages API."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2
from anthropic import AsyncAnthropic

from clinic_evals.caller import Reply, SimulatedCaller, persona
from clinic_evals.record import Seeded
from clinic_evals.scenario import SCENARIOS_DIR, load_scenario

CLINIC = ZoneInfo("America/New_York")


def seeded() -> Seeded:
    return Seeded(
        patient_id="patient-1",
        birth_date="1961-03-03",
        caller_phone="+15550000001",
        slot_ids={"early": "s1", "late": "s2", "afternoon": "s3"},
        slot_starts={
            "early": datetime(2026, 10, 9, 9, 0, tzinfo=CLINIC),
            "late": datetime(2026, 10, 9, 11, 0, tzinfo=CLINIC),
            "afternoon": datetime(2026, 10, 9, 15, 0, tzinfo=CLINIC),
        },
        appointment_ids={},
    )


class MessagesStandIn:
    def __init__(self, replies: list[str]):
        self.requests: list[dict] = []
        self._replies = list(replies)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        text = self._replies.pop(0)
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-4-5",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    def client(self) -> AsyncAnthropic:
        return AsyncAnthropic(api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle)))


def test_the_wrong_birth_date_is_the_real_one_in_the_wrong_year(tmp_path):
    path = tmp_path / "wrong_dob.yaml"
    path.write_text(
        """
summary: Wrong date of birth first.
patient: {given: Desmond, family: Achterberg}
provider: null
caller:
  goal: Speak to a person.
  twist: First say you were born on {wrong_birth_date}.
expect: {appointments: [], handoff: true}
"""
    )

    system = persona(load_scenario(path), seeded())

    assert "Desmond Achterberg, born March 3, 1961" in system
    assert "First say you were born on March 3, 1962." in system


async def test_the_caller_plays_the_scenario_patient_and_hangs_up_when_done():
    scenario = load_scenario(SCENARIOS_DIR / "plain_book.yaml")
    messages = MessagesStandIn(["Hi, I'd like to book my yearly checkup.", "Great, thanks. Bye! HANG UP"])
    caller = SimulatedCaller(scenario, seeded(), client=messages.client())

    first = await caller.reply("Thank you for calling. How can I help you today?")
    last = await caller.reply("You're all booked. Anything else?")

    assert first == Reply("Hi, I'd like to book my yearly checkup.")
    assert last == Reply("Great, thanks. Bye!", hangs_up=True)
    system = messages.requests[0]["system"]
    assert "Rosalind Okonkwo, born March 3, 1961" in system
    assert "Book your yearly checkup with Dr. Imogen Faraday on Friday, October 9, in the morning." in system
    assert [m["role"] for m in messages.requests[1]["messages"]] == ["user", "assistant", "user"]
    assert messages.requests[1]["model"] == "claude-haiku-4-5"
