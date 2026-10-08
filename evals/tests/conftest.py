"""The eval harness's own tests. Graders need nothing; runs need the local EHR stack, as the agent's tests do.

FHIR_BASE_URL and EHR_ADAPTER_URL point at it. A scripted fake plays the agent's LLM and a scripted
Caller plays the Patient, so no test here costs anything.
"""

import os

import httpx
import pytest

FHIR_BASE_URL = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir").rstrip("/")
EHR_ADAPTER_URL = os.environ.get("EHR_ADAPTER_URL", "http://localhost:3000").rstrip("/")


@pytest.fixture(scope="session")
def ehr_urls() -> tuple[str, str]:
    """(FHIR base URL, EHR adapter URL), once both answer."""
    for name, url in [("HAPI", f"{FHIR_BASE_URL}/metadata?_summary=true"), ("the EHR adapter", f"{EHR_ADAPTER_URL}/healthz")]:
        try:
            httpx.get(url, timeout=10).raise_for_status()
        except httpx.HTTPError:
            pytest.fail(
                f"{name} not reachable at {url}. Run: docker compose -f infra/compose.yaml up -d --wait --build, "
                "and set FHIR_BASE_URL and EHR_ADAPTER_URL if they don't listen on ports 8080 and 3000.",
                pytrace=False,
            )
    return FHIR_BASE_URL, EHR_ADAPTER_URL


@pytest.fixture
def fhir(ehr_urls):
    with httpx.Client(base_url=ehr_urls[0], headers={"accept": "application/fhir+json"}, timeout=30) as client:
        yield client
