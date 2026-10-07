import os
import subprocess
import sys
from pathlib import Path

from conftest import FHIR_BASE_URL

FHIR_DIR = Path(__file__).resolve().parent.parent
RESOURCE_TYPES = ("Patient", "Practitioner", "Schedule", "Slot")


def run_seed() -> None:
    env = {**os.environ, "FHIR_BASE_URL": FHIR_BASE_URL, "PYTHONPATH": str(FHIR_DIR / "src")}
    env.setdefault("SYNTHEA_OUTPUT_DIR", str(FHIR_DIR / "output"))
    subprocess.run([sys.executable, "-m", "clinic_seed"], cwd=FHIR_DIR, env=env, check=True, capture_output=True)


def snapshot(fhir) -> dict:
    """Resource counts, plus every resource's version so an overwrite would show."""
    return {
        resource_type: sorted((r["id"], r["meta"]["versionId"]) for r in fhir.search(resource_type))
        for resource_type in RESOURCE_TYPES
    }


def test_running_the_seed_again_changes_no_resource(fhir):
    before = snapshot(fhir)
    assert {t: len(v) for t, v in before.items()}["Patient"] == 60

    run_seed()
    run_seed()

    assert snapshot(fhir) == before
    for resource_type in RESOURCE_TYPES:
        assert fhir.count(resource_type) == len(before[resource_type])
