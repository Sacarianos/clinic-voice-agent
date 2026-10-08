"""The noise injector: garbles some of the simulated Caller's lines the way STT does.

A phone's speech-to-text mishears numbers, weekdays, Provider names and surnames, and the agent has
to cope. The simulated Caller types perfect text, so without this an eval run never tests that.
Confusions come from `evals/confusions.yaml`. Each is tagged with its source, `hand-written` for the
first list and something else (a call id, say) for an error heard on a real call, so those can be
added later and told apart.
"""

import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from clinic_evals.caller import Caller, Reply

CONFUSIONS_PATH = Path(__file__).resolve().parents[2] / "confusions.yaml"
CATEGORIES = ("number", "weekday", "provider", "surname")


class ConfusionsError(ValueError):
    pass


@dataclass(frozen=True)
class Confusion:
    category: str
    said: str  # what the Caller says
    heard: str  # what the STT writes down
    source: str


@dataclass(frozen=True)
class AppliedConfusion:
    confusion: Confusion
    turn: int  # which of the Caller's lines, counting from 1
    original: str  # that line before it was garbled


@dataclass(frozen=True)
class Garbled:
    line: str
    applied: list[AppliedConfusion]


def load_confusions(path: Path = CONFUSIONS_PATH) -> list[Confusion]:
    try:
        entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        return [_confusion(entry) for entry in entries]
    except (KeyError, TypeError) as error:
        raise ConfusionsError(f"{path.name}: a confusion needs a category, said, heard and source ({error!r})") from error
    except ConfusionsError as error:
        raise ConfusionsError(f"{path.name}: {error}") from error


def _confusion(entry: dict) -> Confusion:
    confusion = Confusion(entry["category"], entry["said"], entry["heard"], entry.get("source") or "")
    if confusion.category not in CATEGORIES:
        raise ConfusionsError(f"unknown category {confusion.category!r}, expected one of {', '.join(CATEGORIES)}")
    if not confusion.source:
        raise ConfusionsError(f"{confusion.said!r} -> {confusion.heard!r} has no source")
    if confusion.said.casefold() == confusion.heard.casefold():
        raise ConfusionsError(f"{confusion.said!r} would be heard as the same word")
    return confusion


class NoiseInjector:
    """Each match of a confusion's `said` in a line is garbled with probability `rate`."""

    def __init__(self, confusions: Sequence[Confusion], *, rate: float, rng: random.Random | None = None):
        if not 0 <= rate <= 1:
            raise ValueError(f"the noise rate must be between 0 and 1, not {rate}")
        self._rate = rate
        self._rng = rng or random.Random()
        self._by_word: dict[str, list[Confusion]] = {}
        for confusion in confusions:
            self._by_word.setdefault(confusion.said.casefold(), []).append(confusion)
        words = sorted(self._by_word, key=len, reverse=True)
        self._pattern = re.compile(rf"\b(?:{'|'.join(map(re.escape, words))})\b", re.IGNORECASE) if words else None

    def garble(self, line: str, turn: int = 1) -> Garbled:
        applied: list[AppliedConfusion] = []

        def replace(match: re.Match) -> str:
            spoken = match.group(0)
            if self._rng.random() >= self._rate:
                return spoken
            confusion = self._rng.choice(self._by_word[spoken.casefold()])
            applied.append(AppliedConfusion(confusion, turn, line))
            return _in_case_of(spoken, confusion.heard)

        garbled = self._pattern.sub(replace, line) if self._pattern else line
        return Garbled(garbled, applied)


def _in_case_of(spoken: str, heard: str) -> str:
    if spoken.isupper() and len(spoken) > 1:
        return heard.upper()
    if spoken[0].isupper():
        return heard[0].upper() + heard[1:]
    return heard.lower()


class NoisyCaller:
    """Wraps a Caller so that what it says reaches the agent garbled. `noise` lists every confusion applied."""

    def __init__(self, caller: Caller, injector: NoiseInjector):
        self._caller = caller
        self._injector = injector
        self._turns = 0
        self.noise: list[AppliedConfusion] = []

    async def reply(self, agent_said: str) -> Reply:
        reply = await self._caller.reply(agent_said)
        self._turns += 1
        garbled = self._injector.garble(reply.line, self._turns)
        self.noise += garbled.applied
        return Reply(garbled.line, reply.hangs_up)
