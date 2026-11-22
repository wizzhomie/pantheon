"""Runtime configuration: limits, retry and escalation policy, judge policy, pricing.

Defaults are deliberately conservative: two attempts, a hard cost and latency budget,
and the judge enabled only for attempts that already passed the deterministic checks.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .contracts.base import StrictModel
from .contracts.issues import Severity
from .contracts.trace import Mode

TOKENS_PER_MILLION = 1_000_000


class RunLimits(StrictModel):
    """Hard budget for one case. Exhausting it escalates instead of raising."""

    max_cost_usd: float | None = Field(default=0.05, ge=0)
    max_latency_ms: float | None = Field(default=30_000, gt=0)
    max_judge_calls: int = Field(default=2, ge=0, le=10)


class RetryPolicy(StrictModel):
    """How many times, and for which failures, the agent is asked again."""

    max_attempts: int = Field(
        default=2,
        ge=1,
        le=3,
        description="total attempts including the first one; 1 disables retries",
    )
    retry_on: frozenset[Severity] = frozenset({"error", "critical"})
    include_previous_output: bool = True
    include_failing_quotes: bool = True
    stop_on_no_progress: bool = Field(
        default=True,
        description="do not retry if the new attempt is identical to the previous one",
    )


class JudgePolicy(StrictModel):
    """When the LLM judge is consulted.

    ``sampled`` exists for volume: gates stay on 100% of traffic, the judge reviews a
    fraction, and the report states the coverage instead of hiding it.
    """

    enabled: bool = True
    mode: Literal["always", "on_gate_pass", "sampled", "gates_failed_only"] = "on_gate_pass"
    sample_rate: float = Field(default=1.0, gt=0, le=1)
    model: str | None = Field(
        default=None, description="None reuses the agent's model; set it to use a stronger judge"
    )
    max_input_chars: int = Field(default=20_000, ge=1_000)

    @model_validator(mode="after")
    def _sample_mode_needs_a_rate(self) -> JudgePolicy:
        if self.mode == "sampled" and self.sample_rate >= 1.0:
            raise ValueError("mode='sampled' with sample_rate=1.0 is just mode='always'")
        return self


class EscalationPolicy(StrictModel):
    """What happens when the loop cannot win."""

    on_exhausted: Literal["escalate", "return_last"] = "escalate"
    on_critical: Literal["escalate", "retry_once"] = "escalate"
    on_budget: Literal["escalate", "return_last"] = "escalate"


class QAConfig(StrictModel):
    """Everything tunable without writing a check."""

    mode: Mode = "guarded"
    limits: RunLimits = Field(default_factory=RunLimits)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    judge: JudgePolicy = Field(default_factory=JudgePolicy)
    escalation: EscalationPolicy = Field(default_factory=EscalationPolicy)
    evidence_min_similarity: float = Field(
        default=0.92,
        ge=0,
        le=1,
        description="similarity floor for evidence quotes (tolerates OCR noise)",
    )
    capture_payloads: bool = Field(
        default=False,
        description="store step payloads in the trace; off by default to avoid PII in logs",
    )


class ModelPrice(StrictModel):
    """Price of one model, with the date it was checked.

    The table is empty by default on purpose: a stale hard-coded price produces numbers
    that look authoritative and are wrong.
    """

    model: str = Field(min_length=1)
    input_usd_per_mtok: float = Field(ge=0)
    output_usd_per_mtok: float = Field(ge=0)
    checked_at: date
    source: str | None = Field(default=None, description="where the price came from")


class Pricing(StrictModel):
    """Price lookup and cost estimation."""

    prices: dict[str, ModelPrice] = Field(default_factory=dict)

    def price_for(self, model: str) -> ModelPrice | None:
        """Exact match first, then the longest matching prefix."""
        exact = self.prices.get(model)
        if exact is not None:
            return exact
        prefixes = [name for name in self.prices if model.startswith(name)]
        if not prefixes:
            return None
        return self.prices[max(prefixes, key=len)]

    def estimate(
        self, model: str, prompt_tokens: int, completion_tokens: int
    ) -> tuple[float, bool]:
        """Return ``(cost_usd, price_known)``; unknown models cost 0 and report why."""
        price = self.price_for(model)
        if price is None:
            return 0.0, False
        cost = (
            prompt_tokens / TOKENS_PER_MILLION * price.input_usd_per_mtok
            + completion_tokens / TOKENS_PER_MILLION * price.output_usd_per_mtok
        )
        return round(cost, 8), True


DEFAULT_CONFIG = QAConfig()
