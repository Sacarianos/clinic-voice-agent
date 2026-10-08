"""Named LLM configs. LLM_CONFIG picks one; Claude Haiku 5.5 is the default."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.llm_service import LLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService

from clinic_agent.config import ConfigError, require

DEFAULT_LLM_CONFIG = "haiku"


@dataclass(frozen=True)
class LLMConfig:
    service: type[AnthropicLLMService] | type[OpenRouterLLMService]
    model: str
    api_key_env: str
    settings: Mapping[str, Any] = field(default_factory=dict)


# Haiku 5.5 runs adaptive thinking when a request omits `thinking` (Haiku 4.5 never thinks unless asked), and
# Pipecat 1.12 only switches thinking off for Sonnet 5 and later. Thinking costs 0.3 to 0.4 s of median time to
# first output on a real turn (0.54 s disabled, 0.89 s adaptive at default effort), and a Caller waits through
# that silence. So the voice config turns it off. Haiku 5.5 accepts `disabled` at effort `high` or below; the
# effort default is `medium`, which is fine. The Messages API also rejects non-default `temperature`, `top_p`
# and `top_k` and `budget_tokens` on this model. Pipecat sends none of them unless a Setting names them.
HAIKU_5_5_VOICE_SETTINGS = {"thinking": AnthropicLLMService.ThinkingConfig(type="disabled")}

LLM_CONFIGS = {
    "haiku": LLMConfig(AnthropicLLMService, "claude-haiku-5-5", "ANTHROPIC_API_KEY", HAIKU_5_5_VOICE_SETTINGS),
    # The previous default, kept so #14 can compare the two. Haiku 4.5 does not think unless asked.
    "haiku-4-5": LLMConfig(AnthropicLLMService, "claude-haiku-4-5", "ANTHROPIC_API_KEY"),
    "gemini": LLMConfig(OpenRouterLLMService, "google/gemini-3.6-flash", "OPENROUTER_API_KEY"),
}


def create_llm(env: Mapping[str, str], *, system_instruction: str) -> LLMService:
    name = env.get("LLM_CONFIG") or DEFAULT_LLM_CONFIG
    config = LLM_CONFIGS.get(name)
    if config is None:
        known = ", ".join(sorted(LLM_CONFIGS))
        raise ConfigError(f"Unknown LLM_CONFIG {name!r}. Known configs: {known}")
    api_key = require(env, config.api_key_env, f"LLM config {name!r}")
    return config.service(
        api_key=api_key,
        settings=config.service.Settings(
            model=config.model, system_instruction=system_instruction, **config.settings
        ),
    )
