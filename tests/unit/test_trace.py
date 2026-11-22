"""Trace and result invariants: a result is evidence, so it cannot be self-contradictory."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from agentqa.contracts import RunResult, Totals, TraceEvent, Usage


class Output(BaseModel):
    value: int


def totals(attempts: int = 1) -> Totals:
    return Totals(
        latency_ms=10.0,
        attempts=attempts,
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.0002,
    )


def test_run_result_carries_the_preset_type() -> None:
    result = RunResult[Output](
        case_id="case-1",
        mode="guarded",
        status="passed",
        output=Output(value=1),
        totals=totals(),
    )
    assert result.output is not None
    assert result.output.value == 1


def test_escalated_result_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="escalation_reason"):
        RunResult[Output](
            case_id="case-1",
            mode="guarded",
            status="escalated",
            output=Output(value=1),
            totals=totals(),
        )


def test_reason_is_only_for_escalations() -> None:
    with pytest.raises(ValidationError, match="only meaningful"):
        RunResult[Output](
            case_id="case-1",
            mode="guarded",
            status="passed",
            output=Output(value=1),
            escalation_reason="critical_issue",
            totals=totals(),
        )


def test_passed_result_needs_output() -> None:
    with pytest.raises(ValidationError, match="must carry output"):
        RunResult[Output](case_id="case-1", mode="guarded", status="passed", totals=totals())


def test_failed_result_may_have_no_output() -> None:
    result = RunResult[Output](
        case_id="case-1", mode="guarded", status="failed", totals=totals(attempts=2)
    )
    assert result.output is None


def test_usage_rejects_negative_tokens() -> None:
    with pytest.raises(ValidationError):
        Usage(model="m", prompt_tokens=-1, completion_tokens=0, cost_usd=0.0, latency_ms=1.0)


def test_trace_event_hash_is_strict() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(attempt=1, stage="agent", ok=True, duration_ms=1.0, payload_sha256="not-a-hash")
    event = TraceEvent(
        attempt=1,
        stage="agent",
        ok=True,
        duration_ms=1.0,
        payload_sha256="a" * 64,
        usage=Usage(model="m", prompt_tokens=1, completion_tokens=1, cost_usd=0.0, latency_ms=1.0),
    )
    assert event.usage is not None
    assert event.usage.price_known is True


def test_totals_require_at_least_one_attempt() -> None:
    with pytest.raises(ValidationError):
        Totals(latency_ms=1.0, attempts=0, prompt_tokens=0, completion_tokens=0, cost_usd=0.0)


def test_result_survives_a_json_round_trip() -> None:
    result = RunResult[Output](
        case_id="case-1",
        mode="guarded",
        status="passed_after_retry",
        output=Output(value=7),
        totals=totals(attempts=2),
        trace=[
            TraceEvent(attempt=1, stage="parse", ok=False, duration_ms=0.5, notes="P1_BAD_JSON")
        ],
    )
    restored = RunResult[Output].model_validate_json(result.model_dump_json())
    assert restored == result
