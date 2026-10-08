"""Tokens to dollars, for the agent's LLM only. Speech, telephony and the simulated Caller are not counted."""

from dataclasses import dataclass

from clinic_evals.record import TokenUsage


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


# Anthropic's list prices. A config whose model isn't here reports tokens but no cost, rather than a guess.
PRICES = {
    "claude-haiku-4-5": Price(input=1.00, output=5.00, cache_read=0.10, cache_write=1.25),
}


def call_cost(model: str | None, usage: TokenUsage) -> float | None:
    price = PRICES.get(model or "")
    if price is None:
        return None
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    ) / 1_000_000
