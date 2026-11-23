"""Wiring: which client, which model, which prices. Project code, not library code.

Two entry points, one wiring:

``build_guarded``
    The whole setup in one call, for ``qagent eval --spec
    examples.logistics_intake.runner:build_guarded``.
``build_agent`` / ``build_judge_client``
    The same agent and the same client as separate factories, for a ``qagent.toml`` that
    names them. The config file carries the rules; these two functions carry the choices a
    file cannot make (which provider, which key, which cassette).

Three sources, selected by ``QAGENT_LLM``:

``stub`` (default)
    The deterministic demo agent — no network, no key, no money. Only ``build_guarded``
    supports it: the demo is not a preset, so a config file cannot name it.
``replay``
    A committed cassette of real answers, replayed for free and without a key.
``live``
    A real OpenAI-compatible endpoint (DeepSeek by default), with a real bill. With
    ``QAGENT_RECORD=1`` every answer is also appended to the committed cassette, so the
    same run can later be replayed for free (``QAGENT_LLM=replay``).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from qagent import (
    AgentFn,
    GuardedAgent,
    JudgePolicy,
    LLMClient,
    ModelPrice,
    Pricing,
    QAConfig,
    ReplayClient,
    RunLimits,
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


def build_client() -> LLMClient:
    """The model client for this project: replay of the committed cassette, or live."""
    source = os.environ.get("QAGENT_LLM", "stub").strip().lower()
    if source == "replay":
        if not CASSETTE_PATH.exists():
            raise FileNotFoundError(
                f"no cassette at {CASSETTE_PATH}; record one with QAGENT_LLM=live first"
            )
        return ReplayClient(CASSETTE_PATH, mode="replay")
    if source == "live":
        # Imported here, not at module level: the demo and the tests must not need httpx.
        from qagent.llm import OpenAICompatibleClient

        client: LLMClient = OpenAICompatibleClient(
            model=DEFAULT_MODEL,
            base_url=os.environ.get("QAGENT_BASE_URL", DEFAULT_BASE_URL),
            api_key_env=API_KEY_ENV,
            # DeepSeek rejects the rich `json_schema` form with HTTP 400, so the agent asks
            # for a plain JSON object and carries the schema in the system prompt.
            structured_output="json_object",
            # A reasoning model can think for a minute before it writes the answer.
            timeout_s=180.0,
            # ...and the thinking costs more than the answer: ~9 000 hidden tokens per case
            # against ~600 for the extraction itself. A QA demo does not need the thinking,
            # so the example turns it off at the provider seam. Remove this line to get the
            # full reasoning behaviour, at roughly ten times the bill.
            extra_body={"reasoning_effort": "none"},
        )
        return _recording(client) if _record_requested() else client
    raise ValueError(
        f"QAGENT_LLM={source!r} has no model to build: the stub runs the demo agent, "
        "not a preset; use QAGENT_LLM=replay or QAGENT_LLM=live"
    )


def build_agent() -> AgentFn:
    """The agent function a config file points at: ``agent = "...:build_agent"``."""
    return make_llm_agent(
        llm=build_client(),
        model=DEFAULT_MODEL,
        prompt=AGENT_PROMPT,
        pricing=PRICING,
    )


def build_judge_client() -> LLMClient:
    """The judge's client, so a config file configures the judge like the agent."""
    return build_client()


def build_guarded() -> GuardedAgent[ExtractedIntake]:
    """Build the guarded agent the CLI runs, from the environment and nothing else."""
    if os.environ.get("QAGENT_LLM", "stub").strip().lower() == "stub":
        return build_demo_guarded()
    client = build_client()
    agent = make_llm_agent(
        llm=client,
        model=DEFAULT_MODEL,
        prompt=AGENT_PROMPT,
        pricing=PRICING,
    )
    # Budgets sized for these models: hidden thinking is billed against max_tokens (the judge
    # default of 900 is spent before the verdict is written) and one case easily takes a
    # minute, four times the default latency budget. Reasoning is switched off below (U37),
    # so the headroom is deliberate — removing that line must not break the run. The policy
    # is part of the example, not of the library defaults.
    config = QAConfig(
        judge=JudgePolicy(model=JUDGE_MODEL, max_tokens=8_000),
        limits=RunLimits(max_latency_ms=240_000, max_cost_usd=0.20),
    )
    return guard(agent, PRESET, llm=client, config=config, pricing=PRICING)


def _record_requested() -> bool:
    """True when the run should also write answers into the cassette."""
    return os.environ.get("QAGENT_RECORD", "").strip().lower() in {"1", "true", "yes"}


def _recording(client: LLMClient) -> ReplayClient:
    """Wrap a live client so every answer lands in the committed cassette as well."""
    return ReplayClient(CASSETTE_PATH, inner=client, mode="record")
