"""Turns Synthea's FHIR bundles into demographics-only Patients."""

import json
from pathlib import Path

from clinic_seed.clinic import MRN_SYSTEM


def load_patients(synthea_fhir_dir: Path) -> list[dict]:
    files = sorted(synthea_fhir_dir.glob("*.json"))
    patients = []
    for file in files:
        bundle = json.loads(file.read_text(encoding="utf-8"))
        for entry in bundle.get("entry", []):
            if entry["resource"]["resourceType"] == "Patient":
                patients.append(to_clinic_patient(entry["resource"]))
    if not patients:
        raise FileNotFoundError(f"No Synthea Patient bundles in {synthea_fhir_dir}. Run the synthea service first.")
    return patients


def to_clinic_patient(synthea_patient: dict) -> dict:
    official = next(name for name in synthea_patient["name"] if name.get("use") == "official")
    address = synthea_patient["address"][0]
    return {
        "resourceType": "Patient",
        "identifier": [{"system": MRN_SYSTEM, "value": synthea_patient["id"]}],
        "active": True,
        "name": [{"use": "official", "family": official["family"], "given": official["given"]}],
        "telecom": [t for t in synthea_patient.get("telecom", []) if t["system"] == "phone"],
        "gender": synthea_patient["gender"],
        "birthDate": synthea_patient["birthDate"],
        "address": [{k: address[k] for k in ("line", "city", "state", "postalCode", "country") if k in address}],
    }


def identifier_token(patient: dict) -> str:
    identifier = patient["identifier"][0]
    return f"{identifier['system']}|{identifier['value']}"
