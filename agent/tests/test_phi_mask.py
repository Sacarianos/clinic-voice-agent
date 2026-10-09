"""The forms of a date of birth or phone number the mask recognizes on its own, and what it leaves alone."""

from datetime import date

import pytest

from clinic_agent.phi import DATE_OF_BIRTH, NAME, PHONE, UNVERIFIED_CALLER, PhiMask

THIS_YEAR = date.today().year


@pytest.mark.parametrize(
    "said",
    [
        "1984-08-16",
        "08/16/1984",
        "8/16/1984",
        "16.08.1984",
        "August 16, 1984",
        "Aug 16th 1984",
        "august the 16th of 1984",
        "16 August 1984",
        "the 16th of August, 1984",
        "august sixteenth nineteen eighty four",
        "may third two thousand and one",
    ],
)
def test_a_date_of_birth_is_masked_in_any_common_form(said):
    assert PhiMask().mask(f"born {said}, thanks") == f"born {DATE_OF_BIRTH}, thanks"


@pytest.mark.parametrize(
    "said",
    [
        "+15558675309",
        "15558675309",
        "5558675309",
        "555-867-5309",
        "(555) 867-5309",
        "555.867.5309",
        "+1 555 867 5309",
        "%2B15558675309",
        "+442079460958",
        "five five five eight six seven five three oh nine",
    ],
)
def test_a_phone_number_is_masked_in_any_common_form(said):
    assert PhiMask().mask(f"call {said} back") == f"call {PHONE} back"


@pytest.mark.parametrize(
    "text",
    [
        "Thursday, October 8 at 9 AM",
        f"{THIS_YEAR}-10-08T09:00:00-04:00",
        f"from_date={THIS_YEAR}-10-08",
        "slot c2985429-3dc2-4b46-ab87-a1d6612f52b2",
        "Dr. Imogen Faraday",
        "metrics.ttfb 0.3121",
    ],
)
def test_scheduling_details_and_ids_are_left_readable(text):
    assert PhiMask().mask(text) == text


def test_a_keyed_name_is_masked_wherever_it_appears_in_the_same_text():
    mask = PhiMask()

    assert mask.mask("arguments {'given_name': 'Rosalind', 'family_name': 'Okonkwo'} for Rosalind") == (
        f"arguments {{'given_name': '{NAME}', 'family_name': '{NAME}'}} for {NAME}"
    )


def test_masking_teaches_the_mask_nothing_and_learning_a_keyed_name_masks_it_from_then_on():
    mask = PhiMask()
    line = "arguments {'given_name': 'Rosalind', 'family_name': 'Okonkwo'}"
    mask.mask(line)

    assert mask.mask("Thanks, Rosalind.") == "Thanks, Rosalind."

    mask.learn_keyed_values(line)

    assert mask.mask('Thanks, ROSALIND. "okonkwo" noted.') == f'Thanks, {NAME}. "{NAME}" noted.'


def test_fhir_shaped_data_is_masked_by_its_keys():
    mask = PhiMask()
    patient = {
        "resourceType": "Patient",
        "id": "p-1",
        "name": [{"use": "official", "family": "Okonkwo", "given": ["Rosalind"]}],
        "birthDate": "1984-08-16",
        "telecom": [{"system": "phone", "value": "555 867 5309"}],
    }

    assert mask.mask_data(patient) == {
        "resourceType": "Patient",
        "id": "p-1",
        "name": [{"use": "official", "family": NAME, "given": [NAME]}],
        "birthDate": DATE_OF_BIRTH,
        "telecom": [{"system": "phone", "value": PHONE}],
    }
    assert mask.mask("the official phone line") == "the official phone line"
    assert mask.mask("Rosalind Okonkwo") == "Rosalind Okonkwo"

    mask.learn_keyed_data(patient)

    assert mask.mask("Rosalind Okonkwo") == f"{NAME} {NAME}"
    assert mask.mask("the official phone line") == "the official phone line"


def test_what_the_caller_said_before_verification_is_masked_whole_even_when_escaped_as_json():
    mask = PhiMask()
    mask.learn_unverified_speech("It's Zoë Okonkwo, born in May.")

    assert mask.mask('{"content": "It\'s Zo\\u00eb Okonkwo, born in May."}') == f'{{"content": "{UNVERIFIED_CALLER}"}}'
    assert mask.mask("user said: it's zoë okonkwo, born in may.") == f"user said: {UNVERIFIED_CALLER}"
