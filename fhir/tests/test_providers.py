def display_name(practitioner: dict) -> str:
    name = practitioner["name"][0]
    return " ".join([*name.get("prefix", []), *name["given"], name["family"]])


def role(practitioner: dict) -> str:
    return practitioner["qualification"][0]["code"]["coding"][0]["code"]


def test_clinic_has_two_physicians_and_one_nurse_practitioner(fhir):
    providers = fhir.seeded("Practitioner")

    assert sorted((display_name(p), role(p)) for p in providers) == [
        ("Dr. Marcus Whitfield", "MD"),
        ("Dr. Wojciech Szczepanski", "MD"),
        ("Siobhan Kowalczyk", "NP"),
    ]


def test_every_provider_has_exactly_one_schedule(fhir):
    providers = fhir.seeded("Practitioner")
    schedules = fhir.seeded("Schedule")

    assert len(providers) == 3
    assert sorted(s["actor"][0]["reference"] for s in schedules) == sorted(f"Practitioner/{p['id']}" for p in providers)
    assert all(len(s["actor"]) == 1 and s["active"] for s in schedules)
