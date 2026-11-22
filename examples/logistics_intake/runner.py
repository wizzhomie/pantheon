"""Wiring: which client, which model, which prices. Project code, not library code.

This module is the seam the CLI is designed around: ``qagent eval --spec
examples.logistics_intake.runner:build_guarded`` calls :func:`build_guarded` and knows
nothing else. Choosing a provider, reading a key and finding a cassette are decisions that
belong to a project, and putting them here keeps the library free of a second configuration
system.

Three sources, selected by ``QAGENT_LLM``:

``stub`` (default)
    The deterministic demo agent — no network, no key, no money.
``replay``
    A committed cassette of real answers, replayed for free and without a key.
``live``
    A real OpenAI-compatible endpoint (DeepSeek by default), with a real bill.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from qagent import (
    GuardedAgent,
    JudgePolicy,
    LLMClient,
    ModelPrice,
    Pricing,
    QAConfig,
    ReplayClient,
    guard,
)

from .agent import make_llm_agent
from .demo import build_demo_guarded
from .preset import AGENT_PROMPT, PRESET
from .schema import ExtractedIntake

DEFAULT_MODEL = "deepseek-flash"
JUDGE_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
API_KEY_ENV = "DEEPSEEK_API_KEY"
CASSETTE_PATH = Path(__file__).resolve().parent / "cassettes" / "deepseek-flash.jsonl"

PRICING = Pricing(
    prices={
        # Peak rates from the provider's pricing page, checked on the date below. Off-peak
        # is half of this; one price per model cannot model time of day, so the conservative
        # (peak) rate is used and a report is never flattering by accident.
        DEFAULT_MODEL: ModelPrice(
            model=DEFAULT_MODEL,
            input_usd_per_mtok=0.30,
            output_usd_per_mtok=1.20,
            checked_at=date(2026, 11, 22),
            source="https://api-docs.deepseek.com/quick_start/pricing",
        ),
        JUDGE_MODEL: ModelPrice(
            model=JUDGE_MODEL,
            input_usd_per_mtok=1.32,
            output_usd_per_mtok=3.96,
            checked_at=date(2026, 11, 22),
            source="https://api-docs.deepseek.com/quick_start/pricing",
        ),
    }
)


def build_guarded() -> GuardedAgent[ExtractedIntake]:
    """Build the guarded agent the CLI runs, from the environment and nothing else."""
    source = os.environ.get("QAGENT_LLM", "stub").strip().lower()
    if source == "stub":
        return build_demo_guarded()
    if source == "replay":
        if not CASSETTE_PATH.exists():
            raise FileNotFoundError(
                f"no cassette at {CASSETTE_PATH}; record one with QAGENT_LLM=live first"
            )
        return _llm_guarded(ReplayClient(CASSETTE_PATH, mode="replay"))
    if source == "live":
        # Imported here, not at module level: the demo and the tests must not need httpx.
        from qagent.llm import OpenAICompatibleClient

        return _llm_guarded(
            OpenAICompatibleClient(
                model=DEFAULT_MODEL,
                base_url=os.environ.get("QAGENT_BASE_URL", DEFAULT_BASE_URL),
                api_key_env=API_KEY_ENV,
            )
        )
    raise ValueError(f"QAGENT_LLM must be stub, replay or live, got {source!r}")


def _llm_guarded(client: LLMClient) -> GuardedAgent[ExtractedIntake]:
    agent = make_llm_agent(
        llm=client,
        model=DEFAULT_MODEL,
        prompt=AGENT_PROMPT,
        pricing=PRICING,
    )
    config = QAConfig(judge=JudgePolicy(model=JUDGE_MODEL))
    return guard(agent, PRESET, llm=client, config=config, pricing=PRICING)
