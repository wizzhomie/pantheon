"""Wiring: which client, which model, which prices. Project code, not library code.

Same seam as the logistics example: ``build_agent`` and ``build_judge_client`` are the two
factories ``qagent.toml`` names, and ``build_guarded`` is the one-call entry point for
``qagent eval --spec``. The preset itself is not written in Python here — it is built from
the config file, which is the point of this example.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from qagent import AgentFn, GuardedAgent, LLMClient, ModelPrice, Pricing, ReplayClient
from qagent.project import ProjectConfig, build_preset

from .agent import make_llm_agent
from .demo import build_demo_guarded
from .schema import Review

DEFAULT_MODEL = "deepseek-flash"
JUDGE_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
API_KEY_ENV = "DEEPSEEK_API_KEY"
CONFIG_PATH = Path(__file__).with_name("qagent.toml")
CASSETTE_PATH = Path(__file__).with_name("cassettes") / "deepseek-flash.jsonl"

PRICING = Pricing(
    prices={
        DEFAULT_MODEL: ModelPrice(
            model=DEFAULT_MODEL,
            input_usd_per_mtok=0.30,
            output_usd_per_mtok=1.20,
            checked_at=date(2026, 11, 23),
            source="https://api-docs.deepseek.com/quick_start/pricing",
        ),
        JUDGE_MODEL: ModelPrice(
            model=JUDGE_MODEL,
            input_usd_per_mtok=1.32,
            output_usd_per_mtok=3.96,
            checked_at=date(2026, 11, 23),
            source="https://api-docs.deepseek.com/quick_start/pricing",
        ),
    }
)


def load_config() -> ProjectConfig:
    """The project's cartridge, read from the file next to this module."""
    return ProjectConfig.load(CONFIG_PATH)


def build_client() -> LLMClient:
    """Replay of the committed cassette, or a live endpoint."""
    source = os.environ.get("QAGENT_LLM", "stub").strip().lower()
    if source == "replay":
        if not CASSETTE_PATH.exists():
            raise FileNotFoundError(
                f"no cassette at {CASSETTE_PATH}; record one with QAGENT_LLM=live first"
            )
        return ReplayClient(CASSETTE_PATH, mode="replay")
    if source == "live":
        from qagent.llm import OpenAICompatibleClient

        client: LLMClient = OpenAICompatibleClient(
            model=DEFAULT_MODEL,
            base_url=os.environ.get("QAGENT_BASE_URL", DEFAULT_BASE_URL),
            api_key_env=API_KEY_ENV,
            structured_output="json_object",
            timeout_s=180.0,
            # The thinking costs more than the answer; this example turns it off at the
            # provider seam. Remove the line to get the reasoning behaviour, at a multiple
            # of the bill.
            extra_body={"reasoning_effort": "none"},
        )
        return _recording(client) if _record_requested() else client
    raise ValueError(
        f"QAGENT_LLM={source!r} has no model to build: the stub runs the demo agent; "
        "use QAGENT_LLM=replay or QAGENT_LLM=live"
    )


def build_agent() -> AgentFn:
    """The agent function ``qagent.toml`` names."""
    return make_llm_agent(
        llm=build_client(),
        model=DEFAULT_MODEL,
        prompt=load_config().agent_prompt,
        pricing=PRICING,
    )


def build_judge_client() -> LLMClient:
    """The judge's client, so the file can configure the judge like the agent."""
    return build_client()


def build_guarded() -> GuardedAgent[Review]:
    """One-call entry point for ``qagent eval --spec``; the stub path is the demo."""
    if os.environ.get("QAGENT_LLM", "stub").strip().lower() == "stub":
        return build_demo_guarded()
    from qagent import guard
    from qagent.project import build_pricing, build_qa_config

    config = load_config()
    return guard(
        build_agent(),
        build_preset(config),
        llm=build_client(),
        config=build_qa_config(config),
        pricing=build_pricing(config),
    )


def _record_requested() -> bool:
    return os.environ.get("QAGENT_RECORD", "").strip().lower() in {"1", "true", "yes"}


def _recording(client: LLMClient) -> ReplayClient:
    return ReplayClient(CASSETTE_PATH, inner=client, mode="record")
