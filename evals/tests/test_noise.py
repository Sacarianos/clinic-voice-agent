"""The noise injector garbles Caller lines the way STT does, from a hand-written list of confusions."""

import random

import pytest

from clinic_evals.caller import Reply, ScriptedCaller
from clinic_evals.noise import CONFUSIONS_PATH, Confusion, ConfusionsError, NoiseInjector, NoisyCaller, load_confusions

TUESDAY = Confusion("weekday", "Tuesday", "Thursday", "hand-written")
BRENNAN = Confusion("surname", "Brennan", "Brendan", "hand-written")
FIFTEEN = Confusion("number", "fifteen", "fifty", "call-4f2a")


def injector(*confusions: Confusion, rate: float = 1.0, seed: int = 0) -> NoiseInjector:
    return NoiseInjector(list(confusions), rate=rate, rng=random.Random(seed))


def test_at_a_rate_of_zero_no_line_changes():
    line = "I'm Callum Brennan and I'd like Tuesday."

    garbled = injector(TUESDAY, BRENNAN, rate=0).garble(line)

    assert garbled.line == line
    assert garbled.applied == []


def test_at_a_rate_of_one_every_confusion_in_the_line_applies_and_is_tagged_with_its_source():
    garbled = injector(TUESDAY, BRENNAN, FIFTEEN, rate=1).garble("Callum Brennan, Tuesday the fifteen.")

    assert garbled.line == "Callum Brendan, Thursday the fifty."
    assert [(a.confusion.category, a.confusion.source) for a in garbled.applied] == [
        ("surname", "hand-written"),
        ("weekday", "hand-written"),
        ("number", "call-4f2a"),
    ]


def test_only_whole_words_match_and_the_case_follows_the_line():
    garbled = injector(TUESDAY, BRENNAN, rate=1).garble("tuesday, TUESDAY, Tuesdays, and Brennan's.")

    assert garbled.line == "thursday, THURSDAY, Tuesdays, and Brendan's."


def test_the_rate_is_the_share_of_matches_that_garble_and_the_same_seed_garbles_the_same_ones():
    line = " ".join(["Tuesday"] * 400)

    garbled = injector(TUESDAY, rate=0.25, seed=7).garble(line)
    again = injector(TUESDAY, rate=0.25, seed=7).garble(line)

    assert 70 <= len(garbled.applied) <= 130
    assert garbled.line == again.line


def test_when_several_confusions_share_a_word_one_of_them_applies():
    other = Confusion("weekday", "Tuesday", "Choose day", "hand-written")

    heard = {injector(TUESDAY, other, rate=1, seed=seed).garble("Tuesday").line for seed in range(30)}

    assert heard == {"Thursday", "Choose day"}


def test_a_rate_outside_zero_to_one_is_refused():
    with pytest.raises(ValueError, match="rate"):
        injector(TUESDAY, rate=1.5)


def test_the_bundled_confusions_cover_numbers_weekdays_provider_names_and_surnames_each_with_a_source():
    confusions = load_confusions(CONFUSIONS_PATH)

    assert {c.category for c in confusions} == {"number", "weekday", "provider", "surname"}
    assert all(c.source for c in confusions)
    assert all(c.said.casefold() != c.heard.casefold() for c in confusions)
    said = {c.said for c in confusions}
    assert {"Tuesday", "Whitfield", "Szczepanski", "Kowalczyk", "Brennan"} <= said


def test_the_bundled_surname_confusions_cover_every_bundled_scenarios_patient():
    from clinic_evals.scenario import SCENARIOS_DIR, load_scenarios

    surnames = {c.said for c in load_confusions(CONFUSIONS_PATH) if c.category == "surname"}

    assert {s.patient.family for s in load_scenarios(SCENARIOS_DIR)} <= surnames


@pytest.mark.parametrize(
    "entry, complaint",
    [
        ("- {category: weekday, said: Tuesday, heard: Thursday}", "source"),
        ("- {category: weekday, said: Tuesday, heard: Thursday, source: ''}", "source"),
        ("- {category: colour, said: Tuesday, heard: Thursday, source: hand-written}", "category"),
        ("- {category: weekday, said: Tuesday, heard: tuesday, source: hand-written}", "same"),
    ],
)
def test_a_confusion_without_a_source_or_a_known_category_does_not_load(tmp_path, entry, complaint):
    path = tmp_path / "confusions.yaml"
    path.write_text(entry)

    with pytest.raises(ConfusionsError, match=complaint):
        load_confusions(path)


async def test_a_noisy_caller_says_the_garbled_line_and_keeps_what_it_garbled():
    caller = NoisyCaller(ScriptedCaller(["Brennan, Tuesday.", "Thanks, bye."]), injector(TUESDAY, BRENNAN, rate=1))

    first = await caller.reply("How can I help?")
    second = await caller.reply("Anything else?")

    assert first == Reply("Brendan, Thursday.")
    assert second == Reply("Thanks, bye.")
    assert [(a.confusion.said, a.turn) for a in caller.noise] == [("Brennan", 1), ("Tuesday", 1)]
    assert caller.noise[0].original == "Brennan, Tuesday."


async def test_a_noisy_caller_still_hangs_up_when_the_caller_does():
    caller = NoisyCaller(ScriptedCaller([]), injector(TUESDAY, rate=1))

    assert await caller.reply("Goodbye.") == Reply("", hangs_up=True)
