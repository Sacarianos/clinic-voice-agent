"""The Caller in an eval run: an LLM playing the scenario's Patient, or a script for the harness's own tests."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from anthropic import AsyncAnthropic

from clinic_agent.booking import spoken_time
from clinic_agent.conversation import CLINIC_NAME
from clinic_evals.record import Seeded
from clinic_evals.scenario import WRONG_BIRTH_DATE, Scenario

# The simulated Caller is the same model whatever LLM config the agent runs, so configs are compared fairly.
CALLER_MODEL = "claude-haiku-4-5"
HANG_UP = "HANG UP"


@dataclass(frozen=True)
class Reply:
    line: str  # what the Caller says, empty when they hang up without a word
    hangs_up: bool = False  # after saying the line


class Caller(Protocol):
    async def reply(self, agent_said: str) -> Reply: ...


class ScriptedCaller:
    """Says its lines in order, one per agent turn, and hangs up once they run out."""

    def __init__(self, lines: list[str]):
        self._lines = list(lines)

    async def reply(self, agent_said: str) -> Reply:
        return Reply(self._lines.pop(0)) if self._lines else Reply("", hangs_up=True)


class SimulatedCaller:
    """An LLM playing the scenario's Patient. It hears what the agent said and answers as the Caller."""

    def __init__(self, scenario: Scenario, seeded: Seeded, *, client: AsyncAnthropic, model: str = CALLER_MODEL):
        self.persona = persona(scenario, seeded)
        self._client = client
        self._model = model
        self._messages: list[dict] = []

    async def reply(self, agent_said: str) -> Reply:
        # The agent is the "user" here: the Caller answers it.
        self._messages.append({"role": "user", "content": agent_said or "(silence)"})
        response = await self._client.messages.create(
            model=self._model, max_tokens=300, system=self.persona, messages=self._messages
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        self._messages.append({"role": "assistant", "content": text or HANG_UP})
        return Reply(text.replace(HANG_UP, "").strip(), hangs_up=HANG_UP in text or not text)


def persona(scenario: Scenario, seeded: Seeded) -> str:
    """The simulated Caller's instructions, with the Patient's details and the scenario's Slots filled in."""
    names = {label: spoken_time(start) for label, start in seeded.slot_starts.items()}
    names |= {f"{label}_day": spoken_time(start).split(" at ")[0] for label, start in seeded.slot_starts.items()}
    born = date.fromisoformat(seeded.birth_date)
    wrong = born.replace(year=born.year + 1, day=28 if (born.month, born.day) == (2, 29) else born.day)
    names[WRONG_BIRTH_DATE] = f"{wrong:%B} {wrong.day}, {wrong.year}"
    return f"""\
You are playing a patient who is phoning {CLINIC_NAME}, to test its automated receptionist.
Stay in character as the caller for the whole call.

Who you are: {scenario.patient.given} {scenario.patient.family}, born {born:%B} {born.day}, {born.year}.
What you want: {scenario.goal.format_map(names)}
How the call goes: {scenario.twist.format_map(names)}

How to talk: you are on the phone. Say one or two short sentences at a time, the way people talk.
Answer what the receptionist asks. Give your name and date of birth only when asked. Don't make up
facts about yourself beyond these; if asked something you don't know, answer briefly and plausibly.
Never play the receptionist, and never describe what you are doing.

Ending: once you have what you called for and nothing else to ask, or the receptionist says goodbye,
say a short goodbye and end your message with {HANG_UP}. If the call is going nowhere after several
tries, say goodbye and {HANG_UP}.
"""
