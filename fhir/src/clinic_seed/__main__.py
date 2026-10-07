import os
from datetime import date
from pathlib import Path

from clinic_seed.clinic import PROVIDER_SYSTEM, SCHEDULE_SYSTEM
from clinic_seed.fhir_client import FhirClient
from clinic_seed.patients import identifier_token, load_patients
from clinic_seed.providers import practitioner_resources, schedule_resources
from clinic_seed.slots import slot_resources, today_at_clinic


def main() -> None:
    fhir = FhirClient(os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir"))
    synthea_dir = Path(os.environ.get("SYNTHEA_OUTPUT_DIR", "output")) / "fhir"

    fhir.wait_until_ready()

    patients = load_patients(synthea_dir)
    fhir.create_if_absent([(patient, identifier_token(patient)) for patient in patients])
    print(f"Seeded {len(patients)} patients.")

    fhir.create_if_absent(practitioner_resources())
    practitioner_ids = fhir.ids_by_identifier("Practitioner", PROVIDER_SYSTEM)
    fhir.create_if_absent(schedule_resources(practitioner_ids))
    print(f"Seeded {len(practitioner_ids)} providers with a schedule each.")

    schedule_ids = fhir.ids_by_identifier("Schedule", SCHEDULE_SYSTEM)
    start_date = os.environ.get("SEED_START_DATE")
    first_day = date.fromisoformat(start_date) if start_date else today_at_clinic()
    slots = slot_resources(schedule_ids, first_day)
    fhir.create_if_absent(slots)
    print(f"Seeded {len(slots)} slots starting {first_day}.")


if __name__ == "__main__":
    main()
