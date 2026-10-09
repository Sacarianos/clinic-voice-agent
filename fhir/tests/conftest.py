"""Tests drive the seeded clinic through HAPI's FHIR REST API.

Bring the stack up first (see README): `docker compose -f infra/compose.yaml up -d --wait --build`.
Set FHIR_BASE_URL when HAPI listens somewhere other than http://localhost:8080/fhir.

The other suites add records of their own to the same HAPI, sometimes while these tests run, and a
run that crashed can leave some behind. So these tests only look at what the seed made, which it
marks with the clinic's identifiers.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from clinic_seed.clinic import MRN_SYSTEM, PROVIDER_SYSTEM, PROVIDERS, SCHEDULE_SYSTEM, SLOT_SYSTEM

FHIR_BASE_URL = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir").rstrip("/")
FHIR_DIR = Path(__file__).resolve().parent.parent

# For each resource type, the identifiers that find only what the seed made. "system|" matches any value in
# the system. Other suites' test Providers share the seed's Provider system, so Practitioners go by the seed's keys.
SEEDED = {
    "Patient": [f"{MRN_SYSTEM}|"],
    "Practitioner": [f"{PROVIDER_SYSTEM}|{provider.key}" for provider in PROVIDERS],
    "Schedule": [f"{SCHEDULE_SYSTEM}|"],
    "Slot": [f"{SLOT_SYSTEM}|"],
}


class Fhir:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def get(self, path_or_url: str) -> dict:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}/{path_or_url}"
        request = urllib.request.Request(url, headers={"Accept": "application/fhir+json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    def search(self, query: str) -> list[dict]:
        """Every resource matching the search, following Bundle paging."""
        separator = "&" if "?" in query else "?"
        bundle = self.get(f"{query}{separator}_count=200")
        resources = []
        while True:
            resources += [entry["resource"] for entry in bundle.get("entry", [])]
            next_url = next((link["url"] for link in bundle.get("link", []) if link["relation"] == "next"), None)
            if next_url is None:
                return resources
            bundle = self.get(next_url)

    def count(self, resource_type: str) -> int:
        return self.get(f"{resource_type}?_summary=count")["total"]

    def seeded(self, resource_type: str, *conditions: str) -> list[dict]:
        """Every resource of the type that the seed made and that meets the conditions, such as "status=free"."""
        identifiers = urllib.parse.quote(",".join(SEEDED[resource_type]), safe=",")
        return self.search("&".join([f"{resource_type}?identifier={identifiers}", *conditions]))


@pytest.fixture(scope="session")
def fhir() -> Fhir:
    client = Fhir(FHIR_BASE_URL)
    try:
        client.get("metadata?_summary=true")
    except (urllib.error.URLError, OSError) as error:
        pytest.fail(
            f"HAPI is not reachable at {FHIR_BASE_URL} ({error}). "
            "Run: docker compose -f infra/compose.yaml up -d --wait --build",
            pytrace=False,
        )
    return client


def run_seed() -> None:
    env = {**os.environ, "FHIR_BASE_URL": FHIR_BASE_URL, "PYTHONPATH": str(FHIR_DIR / "src")}
    env.setdefault("SYNTHEA_OUTPUT_DIR", str(FHIR_DIR / "output"))
    subprocess.run([sys.executable, "-m", "clinic_seed"], cwd=FHIR_DIR, env=env, check=True, capture_output=True)


@pytest.fixture(scope="session")
def seeded_today(fhir) -> None:
    """Seeds again first, as `docker compose restart seed` does, so a stack seeded on an earlier day has today's
    Booking Window. Seeding only adds what is missing."""
    run_seed()
