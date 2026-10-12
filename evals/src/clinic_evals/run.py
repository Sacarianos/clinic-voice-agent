"""One eval run.

Seed the scenario's records, let the Caller talk to the agent through the text transport, read the EHR, clean up.
"""

import asyncio
import time
from collections.abc import Callable

from loguru import logger
from pipecat.services.llm_service import LLMService

from clinic_agent.cost import token_usage
from clinic_agent.ehr import EhrAdapter
from clinic_agent.text_call import TextCall
from clinic_evals.caller import Caller
from clinic_evals.ehr_records import EhrRecords
from clinic_evals.noise import NoisyCaller
from clinic_evals.record import Ending, RunRecord, Seeded
from clinic_evals.scenario import Scenario

# A call that hasn't ended after this many Caller lines is going in circles.
MAX_CALLER_LINES = 20


async def run_scenario(
    scenario: Scenario,
    fhir_base_url: str,
    ehr_adapter_url: str,
    *,
    agent: Callable[[Seeded], LLMService],
    caller: Callable[[Scenario, Seeded], Caller],
    reply_timeout_secs: float = 10,
) -> RunRecord:
    """agent makes the agent's LLM and caller the Caller, both once the run's records exist."""
    records = EhrRecords(fhir_base_url)
    try:
        seeded = await asyncio.to_thread(records.seed, scenario)
        llm = agent(seeded)
        call = TextCall(
            llm, EhrAdapter(ehr_adapter_url), caller_phone=seeded.caller_phone, reply_timeout_secs=reply_timeout_secs
        )
        error = None
        turn_secs: list[float] = []
        simulated = caller(scenario, seeded)
        try:
            async with call:
                ending = await _converse(call, simulated, turn_secs)
        except Exception as exc:  # a failed run is still graded on what happened
            logger.exception(f"Eval run of {scenario.name} failed")
            ending, error = "error", f"{type(exc).__name__}: {exc}"
        end_state = await asyncio.to_thread(records.end_state)
    finally:
        await asyncio.to_thread(records.clean_up)
    noise = simulated.noise if isinstance(simulated, NoisyCaller) else []
    return RunRecord(
        scenario,
        seeded,
        list(call.transcript),
        list(call.tool_calls),
        end_state,
        ending,
        error,
        list(noise),
        turn_secs,
        list(call.llm_ttfb_secs),
        token_usage(llm, call.llm_usage),
        simulated.usage,
    )


async def _converse(call: TextCall, caller: Caller, turn_secs: list[float]) -> Ending:
    agent_said = " ".join(call.agent_lines)  # the greeting
    for _ in range(MAX_CALLER_LINES):
        if call.ended:
            return "agent_ended"
        reply = await caller.reply(agent_said)
        if reply.line:
            started = time.perf_counter()
            agent_said = await call.say(reply.line)
            turn_secs.append(time.perf_counter() - started)
        if reply.hangs_up:
            return "caller_hung_up"
    return "agent_ended" if call.ended else "turn_limit"
