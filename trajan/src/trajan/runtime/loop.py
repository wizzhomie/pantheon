"""One case through the whole loop: agent, parse, gate, judge, retry, escalation.

The loop is deliberately explicit rather than a state machine: every branch is a place
where money is spent, and reading the code top to bottom should be enough to know what a
run costs. Three properties are load-bearing:

* **the gate runs first** — a deterministic check that costs microseconds can save a judge
  call that costs a cent, and a preset's ``deep`` checks are still ordered after ``fast``
  ones by the runner;
* **nothing is spent after the answer is known to be identical** — a repeated output is
  compared by hash and stops the loop as ``no_progress`` instead of buying a third copy;
* **exceptions become facts, not crashes** — an agent that raises, a judge that breaks
  and an input that cannot be used all leave a trace event and a named escalation reason.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from time import perf_counter
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

from ..checks.base import CheckContext
from ..checks.runner import run_checks
from ..config import Pricing, QAConfig
from ..contracts.agent import AgentFn, AgentReply, Attempt, Feedback
from ..contracts.case import Case
from ..contracts.issues import CheckResult, GateResult, Issue
from ..contracts.trace import (
    EscalationReason,
    RunResult,
    Stage,
    Status,
    Totals,
    TraceEvent,
    Usage,
)
from ..contracts.verdict import JudgeVerdict
from ..judge.base import Judge
from ..parsing import parse_attempt
from .budget import Budget
from .correction import build_feedback
from .escalation import Outcome, budget_outcome, decide
from .preset import Preset

TOut = TypeVar("TOut", bound=BaseModel)
FailureKind = Literal["agent_error", "parse", "gate", "judge"]


class CaseRunner(Generic[TOut]):
    """Executes one case for one agent/preset/config combination."""

    def __init__(
        self,
        *,
        agent: AgentFn,
        preset: Preset[TOut],
        config: QAConfig,
        judge: Judge | None,
        pricing: Pricing,
    ) -> None:
        self.agent = agent
        self.preset = preset
        self.config = config
        self.judge = judge
        self.pricing = pricing

    async def run(self, case: Case) -> RunResult[TOut]:
        config = self.config
        budget = Budget(config.limits)
        trace: list[TraceEvent] = []
        ctx = CheckContext(case=case, now=datetime.now(UTC), config=config)

        input_issues = self._validate_input(case)
        trace.append(
            _event(
                1,
                "input",
                ok=not input_issues,
                payload=case.payload,
                capture=config.capture_payloads,
                notes=_codes(input_issues) or "input accepted",
            )
        )
        if input_issues:
            return self._finish(
                case,
                status="escalated",
                reason="input_invalid",
                trace=trace,
                budget=budget,
                attempts=0,
                notes=f"input rejected: {_codes(input_issues)}",
            )

        if config.mode == "baseline":
            return await self._baseline(case, budget, trace)

        attempts = 0
        feedback: Feedback | None = None
        previous_signature: str | None = None
        critical_retries = 0
        output: TOut | None = None
        gate: GateResult | None = None
        verdict: JudgeVerdict | None = None
        issues: list[Issue] = []
        failure: FailureKind = "gate"

        while attempts < config.retry.max_attempts:
            if (breach := budget.breach()) is not None:
                return self._budget_stop(
                    case, breach, output, gate, verdict, trace, budget, attempts
                )
            attempts += 1
            started = perf_counter()
            try:
                raw: Attempt | AgentReply = await self.agent(case, feedback)
            except Exception as exc:  # the loop must survive the agent
                duration = _ms_since(started)
                trace.append(
                    _event(
                        attempts,
                        "agent",
                        ok=False,
                        duration_ms=duration,
                        notes=f"{type(exc).__name__}: {exc}",
                    )
                )
                issue = _agent_error(exc)
                issues = [issue]
                failure = "agent_error"
                if attempts < config.retry.max_attempts:
                    feedback = self._feedback(case, attempts, issues, None, None)
                    trace.append(
                        _event(attempts, "retry", ok=True, notes="agent failed; asking again")
                    )
                    continue
                return self._finish(
                    case,
                    status="escalated",
                    reason="agent_error",
                    trace=trace,
                    budget=budget,
                    attempts=attempts,
                    output=output,
                    gate=gate,
                    verdict=verdict,
                    notes=f"agent raised {type(exc).__name__}",
                )

            attempt_value, usages, truncated = _unwrap(raw)
            for usage in usages:
                budget.spend(usage)
                trace.append(
                    _event(
                        attempts,
                        "agent",
                        ok=True,
                        duration_ms=usage.latency_ms,
                        usage=usage,
                        payload=attempt_value,
                        capture=config.capture_payloads,
                        notes="model call",
                    )
                )
            if not usages:
                trace.append(
                    _event(
                        attempts,
                        "agent",
                        ok=True,
                        duration_ms=_ms_since(started),
                        payload=attempt_value,
                        capture=config.capture_payloads,
                        notes="no usage reported by the agent",
                    )
                )

            parsed = parse_attempt(attempt_value, self.preset.output_model, truncated=truncated)
            trace.append(
                _event(
                    attempts,
                    "parse",
                    ok=parsed.output is not None,
                    payload=attempt_value,
                    capture=config.capture_payloads,
                    notes=_codes(parsed.issues) or "parsed",
                )
            )

            if parsed.output is None:
                output = None
                verdict = None
                gate = _gate_from_parse(parsed.issues)
                issues = list(parsed.issues)
                failure = "parse"
                signature = _sha(attempt_value)
            else:
                output = parsed.output
                signature = _sha(output)
                gate = run_checks(self.preset.checks, output, ctx)
                trace.append(
                    _event(
                        attempts,
                        "check",
                        ok=gate.passed,
                        duration_ms=sum(result.duration_ms for result in gate.results),
                        notes=_codes(gate.issues) or "clean",
                    )
                )
                issues = list(gate.issues)
                failure = "gate"

            if (
                attempts > 1
                and previous_signature is not None
                and signature == previous_signature
                and config.retry.stop_on_no_progress
            ):
                return self._finish(
                    case,
                    status="escalated",
                    reason="no_progress",
                    trace=trace,
                    budget=budget,
                    attempts=attempts,
                    output=output,
                    gate=gate,
                    notes="the new answer is identical to the rejected one",
                )
            previous_signature = signature

            judge = self.judge
            if judge is not None and output is not None and self._judge_due(case, gate):
                judge_breach: str | None = budget.breach()
                if judge_breach is None and not budget.judge_allowed():
                    judge_breach = "judge_calls"
                if judge_breach is not None:
                    return self._budget_stop(
                        case, judge_breach, output, gate, None, trace, budget, attempts
                    )
                judge_started = perf_counter()
                try:
                    reviewed = await judge.review(case, output, gate)
                except Exception as exc:  # a broken judge is a finding, not a crash
                    trace.append(
                        _event(
                            attempts,
                            "judge",
                            ok=False,
                            duration_ms=_ms_since(judge_started),
                            notes=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    return self._finish(
                        case,
                        status="escalated",
                        reason="judge_escalate",
                        trace=trace,
                        budget=budget,
                        attempts=attempts,
                        output=output,
                        gate=gate,
                        notes="the judge produced no verdict; quality is unknown",
                    )
                budget.note_judge_call()
                for usage in reviewed.usages:
                    budget.spend(usage)
                    trace.append(
                        _event(
                            attempts,
                            "judge",
                            ok=True,
                            duration_ms=usage.latency_ms,
                            usage=usage,
                            notes="judge model call",
                        )
                    )
                verdict = reviewed.verdict
                trace.append(
                    _event(
                        attempts,
                        "judge",
                        ok=verdict.passed,
                        duration_ms=_ms_since(judge_started),
                        notes=f"score {verdict.score:.2f}",
                    )
                )
                issues = [*issues, *verdict.issues]
                failure = "judge"

            decision = decide(
                issues=issues,
                verdict=verdict,
                attempts_used=attempts,
                retry=config.retry,
                escalation=config.escalation,
                critical_retries=critical_retries,
            )
            if decision.action == "stop":
                return self._finish(
                    case,
                    status="passed" if attempts == 1 else "passed_after_retry",
                    trace=trace,
                    budget=budget,
                    attempts=attempts,
                    output=output,
                    gate=gate,
                    verdict=verdict,
                )
            if decision.action == "retry":
                if any(issue.severity == "critical" for issue in issues):
                    critical_retries += 1
                feedback = self._feedback(case, attempts, issues, verdict, attempt_value)
                trace.append(
                    _event(
                        attempts,
                        "retry",
                        ok=True,
                        notes=f"{len(issues)} issue(s); asking again",
                    )
                )
                continue
            return self._finish(
                case,
                status="escalated" if decision.action == "escalate" else "failed",
                reason=decision.reason,
                trace=trace,
                budget=budget,
                attempts=attempts,
                output=output,
                gate=gate,
                verdict=verdict,
                notes=_failure_note(failure, decision),
            )

        return self._finish(
            case,
            status="escalated",
            reason="max_attempts_exhausted",
            trace=trace,
            budget=budget,
            attempts=attempts,
            output=output,
            gate=gate,
            verdict=verdict,
        )

    async def _baseline(
        self, case: Case, budget: Budget, trace: list[TraceEvent]
    ) -> RunResult[TOut]:
        """One call, parse only: the number every guarded run is compared with."""
        started = perf_counter()
        try:
            raw: Attempt | AgentReply = await self.agent(case, None)
        except Exception as exc:  # baseline must report, not raise
            trace.append(
                _event(
                    1,
                    "agent",
                    ok=False,
                    duration_ms=_ms_since(started),
                    notes=f"{type(exc).__name__}: {exc}",
                )
            )
            return self._finish(
                case,
                status="failed",
                trace=trace,
                budget=budget,
                attempts=1,
                notes=f"agent raised {type(exc).__name__}",
            )

        attempt_value, usages, truncated = _unwrap(raw)
        for usage in usages:
            budget.spend(usage)
            trace.append(
                _event(
                    1,
                    "agent",
                    ok=True,
                    duration_ms=usage.latency_ms,
                    usage=usage,
                    payload=attempt_value,
                    capture=self.config.capture_payloads,
                    notes="model call",
                )
            )
        if not usages:
            trace.append(
                _event(
                    1,
                    "agent",
                    ok=True,
                    duration_ms=_ms_since(started),
                    payload=attempt_value,
                    capture=self.config.capture_payloads,
                    notes="no usage reported by the agent",
                )
            )
        parsed = parse_attempt(attempt_value, self.preset.output_model, truncated=truncated)
        trace.append(
            _event(
                1,
                "parse",
                ok=parsed.output is not None,
                payload=attempt_value,
                capture=self.config.capture_payloads,
                notes=_codes(parsed.issues) or "parsed",
            )
        )
        if parsed.output is None:
            return self._finish(
                case,
                status="failed",
                trace=trace,
                budget=budget,
                attempts=1,
                gate=_gate_from_parse(parsed.issues),
                notes=_codes(parsed.issues),
            )
        return self._finish(
            case,
            status="passed",
            trace=trace,
            budget=budget,
            attempts=1,
            output=parsed.output,
        )

    def _validate_input(self, case: Case) -> list[Issue]:
        text = case.as_text()
        issues: list[Issue] = []
        if not text.strip():
            issues.append(
                Issue(
                    code="I_EMPTY_INPUT",
                    severity="error",
                    source="input",
                    message="the case payload is empty",
                    hint="send the raw input the agent is supposed to process",
                )
            )
        elif len(text) > self.config.limits.max_input_chars:
            issues.append(
                Issue(
                    code="I_INPUT_TOO_LONG",
                    severity="error",
                    source="input",
                    message=f"the input has {len(text)} characters, above the limit",
                    hint="split the document or raise limits.max_input_chars",
                    details={"chars": len(text), "limit": self.config.limits.max_input_chars},
                )
            )
        if self.preset.validate_input is not None:
            issues.extend(self.preset.validate_input(case))
        return issues

    def _judge_due(self, case: Case, gate: GateResult) -> bool:
        policy = self.config.judge
        if self.judge is None or not policy.enabled:
            return False
        if policy.mode == "always":
            return True
        if policy.mode == "on_gate_pass":
            return gate.passed
        if policy.mode == "gates_failed_only":
            return not gate.passed
        # Deterministic sampling: the same case is always reviewed or always skipped, so a
        # regression run does not move its judge coverage between runs.
        digest = hashlib.sha256(case.case_id.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % 10_000
        return bucket < round(policy.sample_rate * 10_000)

    def _feedback(
        self,
        case: Case,
        attempt: int,
        issues: list[Issue],
        verdict: JudgeVerdict | None,
        attempt_value: Attempt | None,
    ) -> Feedback:
        feedback = build_feedback(
            attempt=attempt,
            issues=issues,
            verdict=verdict,
            previous_output=_text(attempt_value) if attempt_value is not None else None,
            include_previous_output=self.config.retry.include_previous_output,
        )
        if self.preset.build_retry is None:
            return feedback
        adjusted = self.preset.build_retry(case, feedback)
        if not isinstance(adjusted, Feedback):
            raise TypeError("preset.build_retry must return a Feedback instance")
        return adjusted

    def _budget_stop(
        self,
        case: Case,
        breach: str,
        output: TOut | None,
        gate: GateResult | None,
        verdict: JudgeVerdict | None,
        trace: list[TraceEvent],
        budget: Budget,
        attempts: int,
    ) -> RunResult[TOut]:
        outcome: Outcome = budget_outcome(self.config.escalation)
        return self._finish(
            case,
            status="escalated" if outcome.action == "escalate" else "failed",
            reason=outcome.reason,
            trace=trace,
            budget=budget,
            attempts=attempts,
            output=output,
            gate=gate,
            verdict=verdict,
            notes=f"budget exhausted: {breach}",
        )

    def _finish(
        self,
        case: Case,
        *,
        status: Status,
        budget: Budget,
        trace: list[TraceEvent],
        attempts: int,
        output: TOut | None = None,
        gate: GateResult | None = None,
        verdict: JudgeVerdict | None = None,
        reason: EscalationReason | None = None,
        notes: str | None = None,
    ) -> RunResult[TOut]:
        mode = self.config.mode
        trace.append(
            _event(
                max(1, attempts),
                "escalate" if status == "escalated" else "done",
                ok=status in ("passed", "passed_after_retry"),
                duration_ms=budget.elapsed_ms(),
                notes=notes or reason or status,
            )
        )
        return RunResult[TOut](
            case_id=case.case_id,
            mode=mode,
            status=status,
            output=output,
            gate=gate,
            verdict=verdict,
            escalation_reason=reason,
            would_have_blocked=(
                status in ("escalated", "failed") if mode == "shadow" else None
            ),
            trace=trace,
            totals=Totals(
                latency_ms=budget.elapsed_ms(),
                attempts=attempts,
                prompt_tokens=budget.prompt_tokens,
                completion_tokens=budget.completion_tokens,
                cost_usd=budget.cost_usd,
                judge_calls=budget.judge_calls,
                price_complete=budget.price_complete,
            ),
        )


def _unwrap(raw: Attempt | AgentReply) -> tuple[Attempt, list[Usage], bool]:
    if isinstance(raw, AgentReply):
        return raw.attempt, list(raw.usages), raw.truncated
    return raw, [], False


def _agent_error(exc: Exception) -> Issue:
    return Issue(
        code="A_AGENT_ERROR",
        severity="error",
        source="agent",
        message=f"the agent raised {type(exc).__name__}",
        hint="the call failed before producing an answer; fix the agent or its input",
        details={"error": str(exc)[:200]},
    )


def _gate_from_parse(issues: list[Issue]) -> GateResult:
    blocking = any(issue.blocking for issue in issues)
    return GateResult(
        passed=not blocking,
        results=[CheckResult(check_id="parse", passed=not blocking, issues=list(issues))],
        issues=list(issues),
    )


def _failure_note(failure: FailureKind, decision: Outcome) -> str:
    if decision.action == "give_up":
        return f"last answer returned as failed ({failure})"
    return f"stopped after {failure} failure"


def _codes(issues: list[Issue]) -> str:
    return ", ".join(sorted({issue.code for issue in issues}))


def _event(
    attempt: int,
    stage: Stage,
    *,
    ok: bool,
    duration_ms: float = 0.0,
    usage: Usage | None = None,
    payload: object | None = None,
    notes: str | None = None,
    capture: bool = False,
) -> TraceEvent:
    text = notes
    if capture and payload is not None:
        preview = _text(payload)[:300]
        text = f"{text}; payload={preview}" if text else f"payload={preview}"
    return TraceEvent(
        attempt=max(1, attempt),
        stage=stage,
        ok=ok,
        duration_ms=round(max(0.0, duration_ms), 3),
        usage=usage,
        payload_sha256=_sha(payload) if payload is not None else None,
        notes=text[:500] if text else None,
    )


def _text(value: object) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _sha(value: object) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


def _ms_since(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)
