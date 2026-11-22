"""Usage accounting: tokens in, money out, unknown prices flagged."""

from __future__ import annotations

from datetime import date

import pytest

from qagent.config import ModelPrice, Pricing
from qagent.llm import LLMResponse, usage_of


def pricing() -> Pricing:
    return Pricing(
        prices={
            "test-model": ModelPrice(
                model="test-model",
                input_usd_per_mtok=2.0,
                output_usd_per_mtok=8.0,
                checked_at=date(2026, 11, 1),
            )
        }
    )


def response(model: str = "test-model", *, cached: bool = False) -> LLMResponse:
    return LLMResponse(
        text="x",
        model=model,
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
        latency_ms=1200.0,
        cached=cached,
    )


def test_cost_is_computed_from_the_price_table() -> None:
    usage = usage_of(response(), pricing())
    assert usage.cost_usd == pytest.approx(6.0)  # 1M * 2.0 + 0.5M * 8.0
    assert usage.price_known is True
    assert usage.latency_ms == 1200.0


def test_unknown_model_is_flagged_not_guessed() -> None:
    usage = usage_of(response(model="unknown/model"), pricing())
    assert usage.cost_usd == 0.0
    assert usage.price_known is False


def test_cached_flag_propagates() -> None:
    usage = usage_of(response(cached=True), pricing())
    assert usage.cached is True
