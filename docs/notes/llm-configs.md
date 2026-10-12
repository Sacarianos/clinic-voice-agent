# LLM config notes

`LLM_CONFIG` picks an entry of `LLM_CONFIGS` in `agent/src/clinic_agent/llm.py`. The README's Voice calls section lists the three configs and their keys. Checked against Pipecat 1.12.0 and the code on 2026-10-12.

## Model behaviour

- Pipecat's Anthropic service defaults to `claude-sonnet-4-6`. `create_llm` always passes the config's model.
- Pipecat turns thinking off only for Sonnet 5 and later. Haiku 5.5 thinks whenever a request omits `thinking`, and that adds 0.3 to 0.4 s before the first word. The `haiku` config sets `thinking` to disabled. The comment above `HAIKU_5_5_VOICE_SETTINGS` has the timings and the sampling parameters Haiku 5.5 rejects. Check a new Claude model for both before its first call.
- Pipecat adds an ASYNC TOOLS paragraph to the system prompt whenever a registered tool has `cancel_on_interruption=False`, and every write, `handoff` and `emergency_redirect` does. `AnthropicLLMWithoutAsyncToolGuidance` leaves the paragraph out for Haiku 5.5, where it made the model talk before `handoff`. Haiku 4.5 keeps the paragraph, because without it the model answered a node's task text as if the Caller had said it. The class docstring has the details. Either choice shifts what the model says, so measure with the evals before you change one. They are paid runs, see [testing.md](testing.md).
- The OpenRouter service rewrites every system message after the first into a user message when the model name contains `gemini`.

## Adding a config

1. Add an `LLMConfig` to `LLM_CONFIGS` with its model, key variable and settings.
2. Test the request it builds without the network, as `test_llm_config.py` does for Anthropic with `request_sent_to_anthropic`.
3. Add its price to `PRICES` in `evals/src/clinic_evals/cost.py`, with the source and date. Without one the report shows tokens and `n/a`.
