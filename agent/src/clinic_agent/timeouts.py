"""How long the agent waits on the EHR adapter, built from named parts so the numbers can't drift apart.

The adapter answers every request within its deadline, so the agent waits that long plus the time
the answer takes to arrive. A write then always comes back settled or unknown before the agent
stops waiting, and the agent never takes a write it gave up on for one that failed.
"""

# The adapter's REQUEST_DEADLINE_MS (see adapter/src/config.ts). Its health check reports the value, and
# test_adapter_deadline fails when the two differ.
ADAPTER_DEADLINE_SECS = 4.0

# How long an answer the adapter sends at its deadline may take to reach the agent.
ANSWER_TRAVEL_SECS = 1.0

# How long the agent waits for any one adapter request.
ADAPTER_REQUEST_TIMEOUT_SECS = ADAPTER_DEADLINE_SECS + ANSWER_TRAVEL_SECS

# A tool's own work around its adapter requests: building nodes, writing the audit log.
TOOL_OVERHEAD_SECS = 1.0


def tool_timeout(adapter_requests: int) -> float:
    """A tool timeout that fits `adapter_requests` adapter requests one after another, each running to its timeout.

    Pipecat abandons a tool that runs past its timeout, so it must cover the slowest path through the tool.
    """
    return adapter_requests * ADAPTER_REQUEST_TIMEOUT_SECS + TOOL_OVERHEAD_SECS
