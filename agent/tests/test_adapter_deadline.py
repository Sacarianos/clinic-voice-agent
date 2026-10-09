"""The agent waits on the adapter for the adapter's deadline and a little more, so the two must agree."""

import httpx

from clinic_agent.timeouts import ADAPTER_DEADLINE_SECS


def test_the_agent_counts_on_the_deadline_the_adapter_runs_with(ehr_adapter_url):
    health = httpx.get(f"{ehr_adapter_url}/healthz", timeout=5).json()

    assert health.get("requestDeadlineMs") == ADAPTER_DEADLINE_SECS * 1000
