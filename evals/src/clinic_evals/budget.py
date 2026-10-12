"""What an eval batch costs: the agent's LLM plus the simulated Caller's. Speech and telephony are not counted."""

from clinic_agent.cost import TokenUsage, call_cost
from clinic_agent.llm import LLM_CONFIGS
from clinic_evals.caller import CALLER_MODEL
from clinic_evals.record import RunRecord

# A rough, generous guess at one run, to put a number on the opt-in prompt. The Haiku 4.5 baseline of 30 runs
# averaged $0.041 each for the agent alone, about 35,000 input tokens and 1,200 output. The Caller sees only
# the spoken lines, so its prompt stays small. Update both from the batch totals a real run prints.
ESTIMATED_AGENT_USAGE_PER_RUN = TokenUsage(input_tokens=40_000, output_tokens=2_000)
ESTIMATED_CALLER_USAGE_PER_RUN = TokenUsage(input_tokens=10_000, output_tokens=600)


def agent_model(config: str) -> str | None:
    known = LLM_CONFIGS.get(config)
    return known.model if known else None


def run_dollars(config: str, record: RunRecord) -> float | None:
    """What one run cost, or None when either model has no price."""
    agent = call_cost(agent_model(config), record.usage)
    caller = call_cost(CALLER_MODEL, record.caller_usage)
    return None if agent is None or caller is None else agent + caller


def estimated_dollars(config: str, runs: int) -> float | None:
    agent = call_cost(agent_model(config), ESTIMATED_AGENT_USAGE_PER_RUN)
    caller = call_cost(CALLER_MODEL, ESTIMATED_CALLER_USAGE_PER_RUN)
    return None if agent is None or caller is None else (agent + caller) * runs


def unpriced_models(config: str) -> list[str]:
    """The models of a batch that have no price, which a dollar limit can't count."""
    models = [agent_model(config) or config, CALLER_MODEL]
    return [model for model in models if call_cost(model, TokenUsage()) is None]


def spend_summary(config: str, records: list[RunRecord]) -> str:
    agent = call_cost(agent_model(config), sum((r.usage for r in records), TokenUsage()))
    caller = call_cost(CALLER_MODEL, sum((r.caller_usage for r in records), TokenUsage()))
    if agent is None or caller is None:
        return f"Spent on {len(records)} runs: no price for one of the models, so no dollar total."
    return f"Spent ${agent + caller:.4f} on {len(records)} runs: agent ${agent:.4f}, simulated Caller ${caller:.4f}."
