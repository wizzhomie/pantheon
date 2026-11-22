"""Run trace and result: what happened, in what order, at what price.

The trace is a flat event list rather than a tree: it appends cleanly to JSONL, can be
aggregated without a visitor, and renders as a timeline in reports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, model_validator

from .base import StrictModel
from .issues import GateResult
from .verdict import JudgeVerdict

Stage = Literal["input", "agent", "parse", "check", "judge", "retry", "escalate", "done"]
Mode = Literal["baseline", "guarded", "shadow"]
Status = Literal["passed", "passed_after_retry", "escalated", "failed"]

EscalationReason = Literal[
    "max_attempts_exhausted",
    "critical_issue",
    "budget_exhausted",
    "no_progress",
    "input_invalid",
    "judge_escalate",
    "agent_error",
    "not_retryable",
]

TOut = TypeVar("TOut", bound=BaseModel)


class Usage(StrictModel):
    """Tokens, money and time spent on one model call."""

    model: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    latency_ms: float = Field(ge=0)
    cached: bool = Field(default=False, description="served from a replay/cache")
    price_known: bool = Field(
        default=True,
        description="False when the model is missing from the price table (cost is then 0)",
    )


class TraceEvent(StrictModel):
    """One step of one attempt."""

    attempt: int = Field(ge=1)
    stage: Stage
    ok: bool
    duration_ms: float = Field(ge=0)
    usage: Usage | None = None
    payload_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="hash of the step payload; bodies are not stored unless explicitly captured",
    )
    notes: str | None = Field(default=None, max_length=500)
    at: datetime | None = Field(
        default=None,
        description="wall clock when the step ran; None keeps test traces deterministic",
    )


class Totals(StrictModel):
    """Aggregated cost of a run."""

    latency_ms: float = Field(ge=0)
    attempts: int = Field(
        ge=0, description="agent calls made; 0 when the input was rejected before the agent ran"
    )
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    judge_calls: int = Field(default=0, ge=0)
    price_complete: bool = Field(
        default=True, description="False if any call used a model missing from the price table"
    )


class RunResult(StrictModel, Generic[TOut]):
    """The outcome of guarding one case.

    ``output`` is kept even when the run escalated: a human reviewer needs to see what
    the agent produced, and silently dropping it would make the trace useless.
    """

    case_id: str
    mode: Mode
    status: Status
    output: TOut | None = None
    gate: GateResult | None = None
    verdict: JudgeVerdict | None = None
    escalation_reason: EscalationReason | None = None
    would_have_blocked: bool | None = Field(
        default=None,
        description="shadow mode only: whether a guarded run would have held this back",
    )
    trace: list[TraceEvent] = Field(default_factory=list)
    totals: Totals

    @model_validator(mode="after")
    def _consistent(self) -> RunResult[TOut]:
        if self.status == "escalated" and self.escalation_reason is None:
            raise ValueError("an escalated result must carry escalation_reason")
        if self.status != "escalated" and self.escalation_reason is not None:
            raise ValueError("escalation_reason is only meaningful for escalated results")
        if self.status in ("passed", "passed_after_retry") and self.output is None:
            raise ValueError("a passed result must carry output")
        if self.mode == "shadow":
            if self.would_have_blocked is None:
                raise ValueError("a shadow result must state would_have_blocked")
            if self.would_have_blocked != (self.status in ("escalated", "failed")):
                raise ValueError("would_have_blocked must match the status of the run")
        elif self.would_have_blocked is not None:
            raise ValueError("would_have_blocked is only meaningful in shadow mode")
        return self

    @property
    def blocking(self) -> bool:
        """Whether the caller should hold this result back.

        The loop status stays honest in shadow mode — the trace says what *would* have
        happened — while this property answers the operational question. Sampling the
        loop on live traffic is the cheap way to earn the right to block later.
        """
        return self.mode != "shadow" and self.status in ("escalated", "failed")
