# Testing notes

Rules that hold across the four packages: `adapter/`, `agent/`, `evals/` and `fhir/`. The README shows how to start the local EHR stack and run each package's tests. Checked against the code on 2026-10-12.

## Lint and types

`scripts/lint` runs ruff, pyright and the rule checks without Docker. The README's Lint and rule checks section has the steps. A new rule that review used to catch belongs in `scripts/check_rules.py` with a bad and a good case in its `--self-check`.

## Paid APIs

The default run costs nothing. A scripted LLM plays the model, fake Deepgram and Twilio stand in for the phone, and CI passes no keys. These runs call paid services, so run them only when the user has approved a number of calls:

- `pytest --llm haiku` or `--llm gemini` in `agent/`
- `clinic-evals`
- the voice server on real keys, and any script that loads `../.env`

The repo enforces this. The first two refuse to start without `ALLOW_PAID_CALLS=1`, `clinic-evals` takes `--max-dollars`, and a project hook blocks all three kinds of command from Bash and PowerShell unless the command sets the opt-in. Set it only after the user approves, and only on the one command. See the README's Paid calls section.

## Real HAPI, never a mock

Adapter, agent and eval tests talk to a real HAPI through the real adapter. Fault tests put a small proxy in front of the adapter or HAPI: `faulty_adapter` in the agent tests, and `held-ehr` and `delayed-ehr` in the adapter's `test/support/`. A proxy of this kind makes a race or a slow EHR happen on every run. The HAPI behaviour they exercise is in [hapi-fhir.md](hapi-fhir.md).

## Isolation

The local stack keeps its data between runs. CI starts with an empty HAPI.

- Each test creates the records it needs and deletes them afterwards. A helper that creates a record registers it for deletion: `create` in `adapter/test/support/ehr.ts` and `Ehr._create` in `agent/tests/ehr.py`. Seed tests find the clinic by its identifiers, so leftovers do not fail them, but they pile up on the local stack.
- The adapter books Appointments and files Callback Requests during a test. Those are not on the created list, and cleanup finds them through the test's Slots, Patients and phone number. A new kind of record that production code creates needs the same path.
- A test's Patients get a birth date no Patient holds, from `unused_birth_date` in the agent tests and `unusedBirthDate` in the adapter's, so the test's own Patients are the only candidates. A test's Provider gets a `test-<uuid>` key, and the agent tests add a random tag to its surname because the agent finds a Provider by name.
- Each `start_call` gets a phone number of its own and deletes the Callback Requests filed from it.
- OpenTelemetry installs one tracer per process. Tests that trace share the session `langfuse` fixture, which points at a local stand-in for Langfuse Cloud.

## Scripted LLM

`ScriptedLLM` in `agent/src/clinic_agent/scripted_llm.py` answers each LLM run with the next step. A tool call is one run, and the reply after its result is the next. A script that runs short ends in the line `(script exhausted)`, which shows up in the transcript.

- Scripted `verify_patient` calls need `caller_is_the_patient`. Use `scripts.verify`.
- A scripted write needs `answer_read_back("yes")` before it. The Read-back node offers no write tool.
- A test that forces a move a real model would not make gets `@pytest.mark.scripted_only`, and `--llm` skips it.
- A fake LLM service must call `await super().process_frame(...)` before anything else. That call is what matches the registered handlers to the tools the context offers.
- To check a safety rule, assert on `call.offered_tools` and on the refusal text a call to an unoffered tool gets. `test_tools_before_verification.py` is the pattern.

## The text transport

`TextCall` runs the production pipeline and flow with the audio ends removed. Caller lines enter as finished transcripts, so VAD, STT and turn detection do not run.

The agent's turn is over when two marker frames in a row cross the pipeline with nothing else happening. The first response end does not mark it, because the agent can say "let me look" and then run a tool. Keep `_agent_turn` in `text_call.py` waiting this way.

## Timeouts

Each agent test has 60 s, set in `agent/pyproject.toml`. A `TextCall` waits 10 s for a scripted reply and 30 s for a real model.
