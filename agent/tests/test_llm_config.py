import pytest
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService

from clinic_agent.llm import LLMConfigError, create_llm

PROMPT = "You are the clinic receptionist."


def test_claude_haiku_4_5_is_the_default_llm():
    llm = create_llm({"ANTHROPIC_API_KEY": "placeholder"}, system_instruction=PROMPT)

    assert isinstance(llm, AnthropicLLMService)
    assert llm.settings.model == "claude-haiku-4-5"
    assert llm.settings.system_instruction == PROMPT


def test_gemini_config_uses_gemini_3_6_flash_through_openrouter():
    env = {"LLM_CONFIG": "gemini", "OPENROUTER_API_KEY": "placeholder"}

    llm = create_llm(env, system_instruction=PROMPT)

    assert isinstance(llm, OpenRouterLLMService)
    assert llm.settings.model == "google/gemini-3.6-flash"
    assert llm.settings.system_instruction == PROMPT


def test_unknown_llm_config_names_the_known_ones():
    with pytest.raises(LLMConfigError, match="'gpt'.*gemini, haiku"):
        create_llm({"LLM_CONFIG": "gpt"}, system_instruction=PROMPT)


def test_missing_api_key_names_the_variable_to_set():
    with pytest.raises(LLMConfigError, match="OPENROUTER_API_KEY"):
        create_llm({"LLM_CONFIG": "gemini", "ANTHROPIC_API_KEY": "placeholder"}, system_instruction=PROMPT)
