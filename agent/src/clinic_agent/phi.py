"""Masking patient data: names, dates of birth and phone numbers never reach logs or traces.

Logs and trace exports pass everything they write through `PHI.mask`. It masks in four ways:

- What the Caller says before Identity Verification succeeds is masked whole. Until then there is no
  telling which words are a name, so every utterance is a phrase to mask wherever it shows up later:
  in the LLM context, in an STT span, or in a tool call.
- Values under keys that hold a name, date of birth or phone number (`given_name`, `birthDate`,
  `from_number` and so on) are masked in key-value text, JSON and FHIR-shaped data alike. Each value is
  also learned, so the same name is masked when the agent or the Caller says it later in free text.
- Dates with a year before this one are masked as dates of birth. Every Patient is an adult, and
  appointments are never in a past year, so scheduling dates stay readable in traces.
- Phone numbers are masked by their shape: E.164, US formats, and seven or more digits said as words.

The mask is process-wide because logs and the trace exporter are. What it learns from one call stays
for later calls too, up to a limit, which costs nothing but a little over-masking.
"""

import json
import re
import threading
from collections import OrderedDict
from datetime import date
from typing import Any

from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

NAME = "[NAME]"
DATE_OF_BIRTH = "[DOB]"
PHONE = "[PHONE]"
UNVERIFIED_CALLER = "[UNVERIFIED CALLER]"

# Keys whose values are patient data, in the agent's tool arguments, the adapter's API, Twilio's call
# data and FHIR resources. Matched case-sensitively, so a query parameter like `from` is not a phone.
PHI_KEYS = {
    "given_name": NAME,
    "family_name": NAME,
    "givenName": NAME,
    "familyName": NAME,
    "given": NAME,
    "family": NAME,
    "date_of_birth": DATE_OF_BIRTH,
    "dateOfBirth": DATE_OF_BIRTH,
    "birthDate": DATE_OF_BIRTH,
    "birthdate": DATE_OF_BIRTH,
    "phone": PHONE,
    "phone_number": PHONE,
    "phoneNumber": PHONE,
    "caller_phone": PHONE,
    "from_number": PHONE,
    "From": PHONE,
}
# A FHIR resource's phone numbers are the values of its telecom entries.
_TELECOM = "telecom"

MAX_LEARNED = 10_000

_QUOTED = r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*\""""
_KEYED_VALUE = re.compile(
    rf"""(?P<key>(?<![\w])(?P<q>['"]?)(?P<name>{"|".join(PHI_KEYS)})(?P=q)\s*[:=]\s*)"""
    rf"""(?P<value>{_QUOTED}|\[(?:{_QUOTED}|[^\]'"])*\])"""
)
_QUOTED_STRING = re.compile(_QUOTED)

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_DAY = r"(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?"
_DATES_WITH_A_YEAR = [
    re.compile(r"(?<!\d)(?P<year>\d{4})-(?:0?[1-9]|1[0-2])-(?:0?[1-9]|[12]\d|3[01])(?!\d)"),
    re.compile(r"(?<!\d)\d{1,2}[/.-]\d{1,2}[/.-](?P<year>\d{4})(?!\d)"),
    re.compile(rf"\b{_MONTH}\s+{_DAY}(?:\s+of)?,?\s+(?P<year>\d{{4}})(?!\d)", re.IGNORECASE),
    re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH},?\s+(?P<year>\d{{4}})(?!\d)", re.IGNORECASE),
]
_NUMBER_WORDS = (
    r"(?:and|oh|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen"
    r"|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
)
# Said as words, as STT writes it without number formatting: "august sixteenth nineteen eighty four".
_SPOKEN_DATE_OF_BIRTH = re.compile(
    rf"\b{_MONTH}\s+(?:the\s+)?[a-z]+(?:[\s-][a-z]+)?,?\s+(?:nineteen|two\s+thousand)(?:[\s-]+{_NUMBER_WORDS}){{0,3}}\b",
    re.IGNORECASE,
)

_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_PHONES = [
    re.compile(r"(?<![\w+-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?![\w-])"),
    re.compile(r"(?<![\w+])\+\d{8,15}(?!\d)"),
    re.compile(rf"\b(?:{_DIGIT_WORD}[\s,.-]+){{6,}}{_DIGIT_WORD}\b", re.IGNORECASE),
]


class PhiMask:
    def __init__(self, max_learned: int = MAX_LEARNED):
        self._max_learned = max_learned
        self._lock = threading.Lock()
        # Learned phrases, longest masked first, and what each is masked as.
        self._unverified_speech: OrderedDict[str, str] = OrderedDict()
        self._values: OrderedDict[str, str] = OrderedDict()
        self._unverified_speech_pattern: re.Pattern | None = None
        self._values_pattern: re.Pattern | None = None

    def learn_unverified_speech(self, utterance: str) -> None:
        """Something the Caller said before Identity Verification succeeded. Masked whole from now on."""
        with self._lock:
            if _remember(self._unverified_speech, utterance, UNVERIFIED_CALLER, self._max_learned):
                self._unverified_speech_pattern = None

    def learn(self, value: str, placeholder: str) -> None:
        """A name, date of birth or phone number, masked as `placeholder` wherever it appears from now on."""
        with self._lock:
            if _remember(self._values, value, placeholder, self._max_learned):
                self._values_pattern = None

    def mask(self, text: str) -> str:
        # Keyed values first: they teach the phrases masked below, for the rest of this same text too.
        text = _KEYED_VALUE.sub(self._mask_keyed_value, text)
        speech, values = self._patterns()
        if speech:
            text = speech.sub(UNVERIFIED_CALLER, text)
        for pattern in _DATES_WITH_A_YEAR:
            text = pattern.sub(_mask_past_date, text)
        text = _SPOKEN_DATE_OF_BIRTH.sub(DATE_OF_BIRTH, text)
        for pattern in _PHONES:
            text = pattern.sub(PHONE, text)
        if values:
            text = values.sub(lambda match: self._values.get(match.group(0).casefold(), NAME), text)
        return text

    def mask_data(self, data: Any) -> Any:
        """Masks JSON-like data: dicts, lists and strings, such as tool arguments or a FHIR resource."""
        self._learn_keyed_values(data)
        return self._mask_data(data)

    def _mask_data(self, data: Any, placeholder: str | None = None) -> Any:
        if isinstance(data, dict):
            return {key: self._mask_data(value, _placeholder_for(key, placeholder)) for key, value in data.items()}
        if isinstance(data, list):
            return [self._mask_data(item, placeholder) for item in data]
        if isinstance(data, str):
            return placeholder or self.mask(data)
        return data

    def _learn_keyed_values(self, data: Any, placeholder: str | None = None) -> None:
        if isinstance(data, dict):
            for key, value in data.items():
                self._learn_keyed_values(value, _placeholder_for(key, placeholder))
        elif isinstance(data, list):
            for item in data:
                self._learn_keyed_values(item, placeholder)
        elif isinstance(data, str) and placeholder:
            self.learn(data, placeholder)

    def _mask_keyed_value(self, match: re.Match) -> str:
        placeholder = PHI_KEYS[match.group("name")]

        def mask_quoted(quoted: re.Match) -> str:
            text = quoted.group(0)
            self.learn(text[1:-1], placeholder)
            return f"{text[0]}{placeholder}{text[-1]}"

        return match.group("key") + _QUOTED_STRING.sub(mask_quoted, match.group("value"))

    def _patterns(self) -> tuple[re.Pattern | None, re.Pattern | None]:
        with self._lock:
            if self._unverified_speech_pattern is None and self._unverified_speech:
                self._unverified_speech_pattern = _phrases_pattern(self._unverified_speech)
            if self._values_pattern is None and self._values:
                self._values_pattern = _phrases_pattern(self._values)
            return self._unverified_speech_pattern, self._values_pattern


PHI = PhiMask()


class PhiRedactionProcessor(FrameProcessor):
    """Sits in the pipeline right after the Caller is heard, and teaches `PHI` what this call must mask.

    It can't mask the frames themselves: the LLM needs the real name and date of birth to verify the
    Caller. So it reads them as they pass, before the LLM logs or traces them. Everything the Caller
    says before Identity Verification succeeds is learned whole, and every tool argument that holds a
    name, date of birth or phone number is learned as a value.
    """

    def __init__(self):
        super().__init__()
        self._verified = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)) and not self._verified:
            PHI.learn_unverified_speech(frame.text)
        elif isinstance(frame, FunctionCallInProgressFrame):
            PHI.mask_data(frame.arguments)
        elif isinstance(frame, FunctionCallResultFrame) and frame.function_name == "verify_patient":
            result = frame.result if isinstance(frame.result, dict) else {}
            self._verified = self._verified or result.get("status") == "verified"
        await self.push_frame(frame, direction)


def _placeholder_for(key: Any, inherited: str | None) -> str | None:
    if inherited:
        return inherited
    if key == _TELECOM:
        return PHONE
    return PHI_KEYS.get(key) if isinstance(key, str) else None


def _remember(learned: OrderedDict[str, str], phrase: str, placeholder: str, limit: int) -> bool:
    """Returns whether the set of phrases changed."""
    phrase = phrase.strip()
    if len(phrase) < 2:
        return False
    variants = {phrase, json.dumps(phrase)[1:-1]}
    changed = False
    for variant in variants:
        key = variant.casefold()
        changed = changed or key not in learned
        learned[key] = placeholder
        learned.move_to_end(key)
    while len(learned) > limit:
        learned.popitem(last=False)
    return changed


def _phrases_pattern(phrases: OrderedDict[str, str]) -> re.Pattern:
    alternatives = "|".join(re.escape(phrase) for phrase in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


def _mask_past_date(match: re.Match) -> str:
    return DATE_OF_BIRTH if int(match.group("year")) < date.today().year else match.group(0)
