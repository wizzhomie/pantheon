"""Configuration defaults, bounds and pricing."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from agentqa import DEFAULT_CONFIG
from agentqa.config import JudgePolicy, ModelPrice, Pricing, QAConfig, RetryPolicy


def test_defaults_are_conservative() -> None:
    config = QAConfig()
    assert config.mode == "guarded"
    assert config.retry.max_attempts == 2
    assert config.judge.enabled is True
    assert config.judge.mode == "on_gate_pass"
    assert config.limits.max_cost_usd == 0.05
    assert config.capture_payloads is False


@pytest.mark.parametrize("attempts", [0, 4])
def test_attempt_bounds(attempts: int) -> None:
    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=attempts)


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_attempt_range_is_accepted(attempts: int) -> None:
    assert RetryPolicy(max_attempts=attempts).max_attempts == attempts


def test_sampled_mode_must_actually_sample() -> None:
    with pytest.raises(ValidationError):
        JudgePolicy(mode="sampled", sample_rate=1.0)
    policy = JudgePolicy(mode="sampled", sample_rate=0.1)
    assert policy.sample_rate == pytest.approx(0.1)


def test_config_is_frozen() -> None:
    with pytest.raises(ValidationError):
        QAConfig().mode = "baseline"  # type: ignore[misc]


def test_baseline_mode_is_allowed() -> None:
    assert QAConfig(mode="baseline").mode == "baseline"
    assert QAConfig(mode="shadow").mode == "shadow"


def test_pricing_unknown_model_is_reported_not_guessed() -> None:
    pricing = Pricing()
    assert pricing.price_for("unknown/model") is None
    assert pricing.estimate("unknown/model", 1_000, 1_000) == (0.0, False)


def test_pricing_estimate_uses_tokens_per_million() -> None:
    price = ModelPrice(
        model="gpt-4o",
        input_usd_per_mtok=2.5,
        output_usd_per_mtok=10.0,
        checked_at=date(2026, 11, 1),
    )
    pricing = Pricing(prices={"gpt-4o": price})
    cost, known = pricing.estimate("gpt-4o", 1_000_000, 1_000_000)
    assert known is True
    assert cost == pytest.approx(12.5)


def test_prefix_match_and_exact_precedence() -> None:
    generic = ModelPrice(
        model="gpt-4o",
        input_usd_per_mtok=2.5,
        output_usd_per_mtok=10.0,
        checked_at=date(2026, 11, 1),
    )
    specific = ModelPrice(
        model="gpt-4o-mini",
        input_usd_per_mtok=0.15,
        output_usd_per_mtok=0.6,
        checked_at=date(2026, 11, 1),
    )
    pricing = Pricing(prices={"gpt-4o": generic, "gpt-4o-mini": specific})
    # exact match wins over the shorter prefix
    assert pricing.price_for("gpt-4o-mini") is specific
    assert pricing.price_for("gpt-4o-2026-01-01") is generic


def test_default_config_matches_factory() -> None:
    assert DEFAULT_CONFIG == QAConfig()
