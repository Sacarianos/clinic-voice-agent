"""`clinic-evals --config haiku`: every scenario three times against a real LLM config, with a simulated Caller.
`clinic-evals --report` prints the latest saved batch of each config side by side, without running anything.

Needs the local EHR stack at FHIR_BASE_URL and EHR_ADAPTER_URL, ANTHROPIC_API_KEY for the simulated
Caller, the agent's LLM key, and the Langfuse keys to push scores. Costs money: never run it in CI.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from loguru import logger

from clinic_agent.config import ConfigError, require
from clinic_agent.conversation import ROLE
from clinic_agent.llm import LLM_CONFIGS, create_llm
from clinic_evals.caller import SimulatedCaller
from clinic_evals.evals import RESULTS_DIR, batch_document, run_evals
from clinic_evals.langfuse import Langfuse
from clinic_evals.noise import NoiseInjector, NoisyCaller, load_confusions
from clinic_evals.report import format_report, latest_batch_per_config
from clinic_evals.scenario import SCENARIOS_DIR, load_scenarios

DEFAULT_FHIR_BASE_URL = "http://localhost:8080/fhir"
DEFAULT_EHR_ADAPTER_URL = "http://localhost:3000"
# A real LLM and a slow tool can take a while on one turn. The text transport's default is for fakes.
REPLY_TIMEOUT_SECS = 30
DEFAULT_NOISE_RATE = 0.2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clinic-evals", description=__doc__.splitlines()[0])
    parser.add_argument("--config", choices=sorted(LLM_CONFIGS), help="the LLM config the agent runs")
    parser.add_argument("--report", action="store_true", help="print the latest saved batch of each config and exit")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help="where batches are saved and read from")
    parser.add_argument(
        "--noise-rate",
        type=float,
        default=DEFAULT_NOISE_RATE,
        help=f"share of confusable words in the Caller's lines that STT-style noise garbles (default {DEFAULT_NOISE_RATE}, 0 for none)",
    )
    parser.add_argument("--repeats", type=int, default=3, help="runs per scenario (default 3)")
    parser.add_argument("--scenario", action="append", help="run only this scenario; repeat to pick several")
    args = parser.parse_args(argv)
    if args.report:
        batches = latest_batch_per_config(args.results_dir)
        print(format_report(batches) if batches else f"No saved eval results in {args.results_dir}.")
        return 0
    if not args.config:
        parser.error("--config is required to run evals")
    if not 0 <= args.noise_rate <= 1:
        parser.error("--noise-rate must be between 0 and 1")

    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("LOG_LEVEL", "WARNING"))
    env = dict(os.environ)
    scenarios = load_scenarios(SCENARIOS_DIR)
    if args.scenario:
        unknown = set(args.scenario) - {s.name for s in scenarios}
        if unknown:
            parser.error(f"unknown scenario {', '.join(sorted(unknown))}; known: {', '.join(s.name for s in scenarios)}")
        scenarios = [s for s in scenarios if s.name in args.scenario]

    noise = NoiseInjector(load_confusions(), rate=args.noise_rate)
    agent_env = {**env, "LLM_CONFIG": args.config}
    try:
        create_llm(agent_env, system_instruction=ROLE)  # fails now, not after the first run, when a key is missing
        callers = AsyncAnthropic(api_key=require(env, "ANTHROPIC_API_KEY", "The simulated Caller"))
    except ConfigError as error:
        parser.error(str(error))
    langfuse = Langfuse.from_env(env)
    if langfuse is None:
        print("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set: scores are saved locally only.", file=sys.stderr)

    results = asyncio.run(
        run_evals(
            scenarios,
            config=args.config,
            repeats=args.repeats,
            ehr_urls=(
                env.get("FHIR_BASE_URL") or DEFAULT_FHIR_BASE_URL,
                env.get("EHR_ADAPTER_URL") or DEFAULT_EHR_ADAPTER_URL,
            ),
            agent=lambda seeded: create_llm(agent_env, system_instruction=ROLE),
            caller=lambda scenario, seeded: NoisyCaller(SimulatedCaller(scenario, seeded, client=callers), noise),
            langfuse=langfuse,
            results_dir=args.results_dir,
            reply_timeout_secs=REPLY_TIMEOUT_SECS,
            noise_rate=args.noise_rate,
        )
    )
    if results:
        print(format_report([batch_document(results, args.config, args.noise_rate)]))
        print(f"Transcripts: {args.results_dir / results[0].batch_id}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
