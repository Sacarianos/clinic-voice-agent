from datetime import date


def age_in_years(birth_date: str, today: date) -> int:
    born = date.fromisoformat(birth_date)
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def test_searching_by_family_name_returns_the_seeded_synthea_patient(fhir):
    patients = fhir.seeded("Patient", "family=Hudson")

    assert [(p["name"][0]["given"][0], p["name"][0]["family"], p["birthDate"]) for p in patients] == [
        ("Alvaro", "Hudson", "1983-12-25"),
    ]


def test_clinic_holds_about_sixty_patients(fhir):
    assert len(fhir.seeded("Patient")) == 60


def test_no_patient_is_under_eighteen(fhir):
    patients = fhir.seeded("Patient")

    assert len(patients) == 60
    assert min(age_in_years(p["birthDate"], date.today()) for p in patients) >= 18


def test_patients_carry_demographics_only(fhir):
    patients = fhir.seeded("Patient")

    assert patients
    for patient in patients:
        assert patient["name"][0]["family"]
        assert patient["name"][0]["given"]
        assert patient["birthDate"]
        assert patient["telecom"][0]["system"] == "phone"
        assert not any(i["system"] == "http://hl7.org/fhir/sid/us-ssn" for i in patient["identifier"])
    for resource_type in ("Encounter", "Condition", "Observation", "MedicationRequest", "Immunization"):
        assert fhir.count(resource_type) == 0, resource_type
