"""Named LLM configs. LLM_CONFIG picks one; Claude Haiku 4.5 is the default."""

from collections.abc import Mapping
from dataclasses import dataclass

from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.llm_service import LLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService

DEFAULT_LLM_CONFIG = "haiku"


class LLMConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    service: type[AnthropicLLMService] | type[OpenRouterLLMService]
    model: str
    api_key_env: str


LLM_CONFIGS = {
    "haiku": LLMConfig(AnthropicLLMService, "claude-haiku-4-5", "ANTHROPIC_API_KEY"),
    "gemini": LLMConfig(OpenRouterLLMService, "google/gemini-3.6-flash", "OPENROUTER_API_KEY"),
}


def create_llm(env: Mapping[str, str], *, system_instruction: str) -> LLMService:
    name = env.get("LLM_CONFIG") or DEFAULT_LLM_CONFIG
    config = LLM_CONFIGS.get(name)
    if config is None:
        known = ", ".join(sorted(LLM_CONFIGS))
        raise LLMConfigError(f"Unknown LLM_CONFIG {name!r}. Known configs: {known}")
    api_key = env.get(config.api_key_env)
    if not api_key:
        raise LLMConfigError(f"LLM config {name!r} needs {config.api_key_env} to be set")
    return config.service(
        api_key=api_key,
        settings=config.service.Settings(model=config.model, system_instruction=system_instruction),
    )
