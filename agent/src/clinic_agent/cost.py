"""Tokens to dollars for LLM calls. Speech and telephony are not counted."""

from collections import defaultdict
from dataclasses import dataclass, replace

from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.services.llm_service import LLMService
from pipecat.services.openai.llm import OpenAILLMService


@dataclass(frozen=True)
class TokenUsage:
    """What an LLM used over some calls. Input is only the part that did not come from the prompt cache."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


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


def token_usage(llm: LLMService, reported: list[LLMTokenUsage]) -> TokenUsage:
    """Adds up what the LLM reported. OpenAI-compatible services count cached input inside prompt_tokens, the rest outside it."""
    gross = isinstance(llm, OpenAILLMService)
    cache_read = sum(usage.cache_read_input_tokens or 0 for usage in reported)
    prompt = sum(usage.prompt_tokens for usage in reported)
    return TokenUsage(
        input_tokens=prompt - cache_read if gross else prompt,
        output_tokens=sum(usage.completion_tokens for usage in reported),
        cache_read_tokens=cache_read,
        cache_write_tokens=sum(usage.cache_creation_input_tokens or 0 for usage in reported),
    )
