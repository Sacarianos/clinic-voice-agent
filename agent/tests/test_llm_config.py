import pytest
from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService

from pipecat.processors.aggregators.llm_context import NOT_GIVEN, LLMContext

from clinic_agent.config import ConfigError
from clinic_agent.llm import create_llm

PROMPT = "You are the clinic receptionist."


async def request_sent_to_anthropic(llm, tools: ToolsSchema = NOT_GIVEN) -> dict:
    """Runs one turn against a stand-in for Anthropic and returns the request pipecat built."""
    sent = {}
    context = LLMContext(messages=[{"role": "user", "content": "Hello"}], tools=tools)

    async def capture(api_call, params):
        sent.update(params)

        async def no_events():
            return
            yield

        return no_events()

    llm._create_message_stream = capture
    # What the service does with every context frame before it runs inference.
    llm._sync_registered_tool_handlers(context.tools)
    await llm._process_context(context)
    return sent


def test_claude_haiku_5_5_is_the_default_llm():
    llm = create_llm({"ANTHROPIC_API_KEY": "placeholder"}, system_instruction=PROMPT)

    assert isinstance(llm, AnthropicLLMService)
    assert llm.settings.model == "claude-haiku-5-5"
    assert llm.settings.system_instruction == PROMPT


def test_haiku_4_5_stays_reachable_as_its_own_config():
    env = {"LLM_CONFIG": "haiku-4-5", "ANTHROPIC_API_KEY": "placeholder"}

    llm = create_llm(env, system_instruction=PROMPT)

    assert isinstance(llm, AnthropicLLMService)
    assert llm.settings.model == "claude-haiku-4-5"
    assert llm.settings.system_instruction == PROMPT


async def test_haiku_5_5_turns_thinking_off_in_the_request_it_sends():
    llm = create_llm({"ANTHROPIC_API_KEY": "placeholder"}, system_instruction=PROMPT)

    request = await request_sent_to_anthropic(llm)

    assert request["model"] == "claude-haiku-5-5"
    assert request["thinking"] == {"type": "disabled"}


async def test_haiku_5_5_requests_carry_no_sampling_parameters_it_would_reject():
    llm = create_llm({"ANTHROPIC_API_KEY": "placeholder"}, system_instruction=PROMPT)

    request = await request_sent_to_anthropic(llm)

    sent = request.keys() | request.get("extra_body", {}).keys()
    assert not {"temperature", "top_p", "top_k"} & sent


def tools_with_one_that_survives_interruption() -> ToolsSchema:
    @tool_options(cancel_on_interruption=False)
    async def handoff(params):
        pass

    return ToolsSchema(standard_tools=[FunctionSchema("handoff", "End the call", {}, [], handler=handoff)])


async def test_haiku_5_5_gets_no_async_tool_guidance_for_a_tool_that_survives_interruption():
    llm = create_llm({"ANTHROPIC_API_KEY": "placeholder"}, system_instruction=PROMPT)

    request = await request_sent_to_anthropic(llm, tools_with_one_that_survives_interruption())

    assert request["system"] == PROMPT


async def test_haiku_4_5_keeps_the_async_tool_guidance_it_was_tuned_with():
    llm = create_llm({"LLM_CONFIG": "haiku-4-5", "ANTHROPIC_API_KEY": "placeholder"}, system_instruction=PROMPT)

    request = await request_sent_to_anthropic(llm, tools_with_one_that_survives_interruption())

    assert request["system"].startswith(PROMPT)
    assert "ASYNC TOOLS" in request["system"]


def test_gemini_config_uses_gemini_3_6_flash_through_openrouter():
    env = {"LLM_CONFIG": "gemini", "OPENROUTER_API_KEY": "placeholder"}

    llm = create_llm(env, system_instruction=PROMPT)

    assert isinstance(llm, OpenRouterLLMService)
    assert llm.settings.model == "google/gemini-3.6-flash"
    assert llm.settings.system_instruction == PROMPT


def test_unknown_llm_config_names_the_known_ones():
    with pytest.raises(ConfigError, match="'gpt'.*gemini, haiku, haiku-4-5"):
        create_llm({"LLM_CONFIG": "gpt"}, system_instruction=PROMPT)


def test_missing_api_key_names_the_variable_to_set():
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        create_llm({"LLM_CONFIG": "gemini", "ANTHROPIC_API_KEY": "placeholder"}, system_instruction=PROMPT)
