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


# List prices. A config whose model isn't here reports tokens but no cost, rather than a guess.
# Haiku 5.5 has a second rate card, $0.50 / $2.50, for prompts over 100K tokens. A call here never gets near that.
# Gemini is OpenRouter's price, read from openrouter.ai/google/gemini-3.6-flash on 2026-10-09. That page lists no
# cache rates, so cached tokens count at the full input rate, which can only overstate the cost.
PRICES = {
    "claude-haiku-4-5": Price(input=1.00, output=5.00, cache_read=0.10, cache_write=1.25),
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "google/gemini-3.6-flash": Price(input=0.75, output=3.75, cache_read=0.75, cache_write=0.75),
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
