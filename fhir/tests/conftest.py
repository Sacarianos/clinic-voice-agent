"""Tests drive the seeded clinic through HAPI's FHIR REST API.

Bring the stack up first (see README): `docker compose -f infra/compose.yaml up -d --wait --build`.
Set FHIR_BASE_URL when HAPI listens somewhere other than http://localhost:8080/fhir.
"""

import json
import os
import urllib.error
import urllib.request

import pytest

FHIR_BASE_URL = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir").rstrip("/")


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
