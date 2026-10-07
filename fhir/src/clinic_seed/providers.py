from clinic_seed.clinic import PROVIDER_SYSTEM, PROVIDERS, SCHEDULE_SYSTEM, Provider


def practitioner(provider: Provider) -> dict:
    name = {"use": "official", "family": provider.family, "given": [provider.given]}
    if provider.prefix:
        name["prefix"] = [provider.prefix]
    return {
        "resourceType": "Practitioner",
        "identifier": [{"system": PROVIDER_SYSTEM, "value": provider.key}],
        "active": True,
        "name": [name],
        "qualification": [
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0360",
                            "code": provider.role_code,
                            "display": provider.role_display,
                        }
                    ]
                }
            }
        ],
    }


def schedule(provider: Provider, practitioner_id: str) -> dict:
    return {
        "resourceType": "Schedule",
        "identifier": [{"system": SCHEDULE_SYSTEM, "value": provider.key}],
        "active": True,
        "actor": [{"reference": f"Practitioner/{practitioner_id}"}],
    }


def practitioner_resources() -> list[tuple[dict, str]]:
    return [(practitioner(p), f"{PROVIDER_SYSTEM}|{p.key}") for p in PROVIDERS]


def schedule_resources(practitioner_ids: dict[str, str]) -> list[tuple[dict, str]]:
    return [(schedule(p, practitioner_ids[p.key]), f"{SCHEDULE_SYSTEM}|{p.key}") for p in PROVIDERS]
