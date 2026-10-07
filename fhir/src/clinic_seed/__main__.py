import os
from pathlib import Path

from clinic_seed.fhir_client import FhirClient
from clinic_seed.patients import identifier_token, load_patients


def main() -> None:
    fhir = FhirClient(os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir"))
    synthea_dir = Path(os.environ.get("SYNTHEA_OUTPUT_DIR", "output")) / "fhir"

    fhir.wait_until_ready()
    patients = load_patients(synthea_dir)
    fhir.create_if_absent([(patient, identifier_token(patient)) for patient in patients])
    print(f"Seeded {len(patients)} patients.")


if __name__ == "__main__":
    main()
