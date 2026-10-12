# Pipecat notes

The agent runs Pipecat 1.12.0, pinned in `agent/pyproject.toml`. Examples and training data for earlier versions point at names and options that 1.12 dropped. Checked against the installed 1.12.0 source on 2026-10-12. The pipeline is in `agent/src/clinic_agent/pipeline.py`, the flow in `conversation.py`, the voice server in `server.py`.

## Names in 1.12

- Pipelines run on workers. Use `PipelineWorker` and `PipelineParams` from `pipecat.pipeline.worker`, and `WorkerRunner` from `pipecat.workers.runner`. `PipelineTask` and `PipelineRunner` are deprecated aliases. `FlowManager` takes `worker=` and deprecates its `task=` argument.
- Flows ships inside Pipecat as `pipecat.flows`. There is no `pipecat-ai-flows` package to install.
- There is no `allow_interruptions`. The user turn start strategies decide whether a Caller can cut the agent off, through `enable_interruptions`, which defaults to true. The VAD analyzer goes on `LLMUserAggregatorParams(vad_analyzer=...)`, never on the transport.
- The voice server leaves the turn strategies at Pipecat's defaults. VAD and transcription start a turn, and Smart Turn v3, a bundled model that runs on the CPU, ends it. The text transport swaps in `ExternalUserTurnStrategies` so typed lines need no audio.
- Silero VAD takes 8 kHz audio or a multiple of 16 kHz.
- Importing Pipecat raises a deprecation warning for Python's `audioop`, and its own services raise two more. The `filterwarnings` list in `agent/pyproject.toml` ignores all three. Run pytest without `-W error`, which fails at import.

## Tools and gating

ADR 0003 puts the safety rules in which tools a node offers. These facts hold that up.

- Attach tools through a node's `functions` or `FlowManager(global_functions=...)`. Pipecat registers a handler when its tool is offered and drops it when the tool is gone. A handler added with `llm.register_function` is never dropped, so it runs in every node, whatever the node offers. The module docstring of `conversation.py` says the same.
- A call to a tool the node does not offer never reaches a handler. Pipecat answers with the tool result ``The function `name` is not currently available.`` and logs a warning. The tests assert that text, and a scripted LLM can try it with `CallTool`.
- `global_functions` are offered in every node, closing nodes included. `handoff`, `emergency_redirect` and `get_clinic_info` rely on that.
- `FlowsFunctionSchema.cancel_on_interruption` defaults to false, which Pipecat reads as an async tool. Set it on every schema. Reads use true, so a Caller who talks over them drops the answer. Writes, `handoff` and `emergency_redirect` use false, so an interruption cannot cut them off half done. The prompt side effect of false is in [llm-configs.md](llm-configs.md).
- Pipecat cancels a handler that runs past its `timeout_secs`. Size it with `tool_timeout(n)` from `timeouts.py`, where n is the number of adapter requests the tool can make in a row. `ADAPTER_DEADLINE_SECS` there must equal the adapter's `REQUEST_DEADLINE_MS`, and `test_adapter_deadline` fails when they differ. Change both together.
- A node's `pre_actions` run when the node is entered. A tool that keeps the flow in its node cannot use one for a holding line, so `holding.with_holding_line` queues the `TTSSpeakFrame` from inside the handler.
- Node prompts and `tts_say` text pass through `{{ key }}` templating on every entry. A key missing from `flow_manager.state` raises `FlowError`. Write a literal pair of braces as `\{{`.

## Phone transport

- `TwilioFrameSerializer` defaults to `auto_hang_up=True`. It then needs a call SID, account SID and auth token, raises `ValueError` without them, and calls the Twilio REST API to end the call. Tests build the app with `hang_up_through_twilio=False`.
- Twilio's `start` message carries no caller number. `/voice` puts it in a `from_number` stream parameter, and `parse_telephony_websocket` returns it as `call_data.from_number`. The README's Voice calls section covers the rest of the phone path.

## Tracing

- Pipecat's spans carry the raw conversation: transcripts, LLM input and output, tool calls and spoken text. `MaskingSpanExporter` in `tracing.py` rebuilds every span with masked attributes before export, because a span cannot change once it has ended. A new place where Caller text reaches a span or a log needs masking too. [docs/phi-policy.md](../phi-policy.md) lists what is covered.
