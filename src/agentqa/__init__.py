"""agentqa — embedded QA for LLM agents.

A preset-driven quality layer for any step that must return structured data:
deterministic checks, an independent LLM judge with a data-driven rubric, a bounded
self-correction loop under an explicit budget, and an offline regression suite that
reuses the same preset.

Phase 1 exposes contracts and configuration only. The runtime loop, checks, judge and
CLI land in later phases — see ``docs/design.md``.
"""

from __future__ import annotations

from .config import (
    DEFAULT_CONFIG,
    EscalationPolicy,
    JudgePolicy,
    ModelPrice,
    Pricing,
    QAConfig,
    RetryPolicy,
    RunLimits,
)
from .contracts import (
    AgentFn,
    Attempt,
    Case,
    CaseCategory,
    CaseChange,
    CaseResult,
    CategoryMetrics,
    CheckResult,
    Comparator,
    Criterion,
    EscalationReason,
    EvalCase,
    EvalReport,
    Feedback,
    FieldSpec,
    GateResult,
    Issue,
    IssueSource,
    JsonValue,
    JudgeVerdict,
    MetaEval,
    MetricDelta,
    Metrics,
    Mode,
    ReportDiff,
    Rubric,
    RunResult,
    ScoringSpec,
    Severity,
    Stage,
    Status,
    StrictModel,
    Totals,
    TraceEvent,
    Trap,
    Usage,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_CONFIG",
    "AgentFn",
    "Attempt",
    "Case",
    "CaseCategory",
    "CaseChange",
    "CaseResult",
    "CategoryMetrics",
    "CheckResult",
    "Comparator",
    "Criterion",
    "EscalationPolicy",
    "EscalationReason",
    "EvalCase",
    "EvalReport",
    "Feedback",
    "FieldSpec",
    "GateResult",
    "Issue",
    "IssueSource",
    "JsonValue",
    "JudgePolicy",
    "JudgeVerdict",
    "MetaEval",
    "MetricDelta",
    "Metrics",
    "Mode",
    "ModelPrice",
    "Pricing",
    "QAConfig",
    "ReportDiff",
    "RetryPolicy",
    "Rubric",
    "RunLimits",
    "RunResult",
    "ScoringSpec",
    "Severity",
    "Stage",
    "Status",
    "StrictModel",
    "Totals",
    "TraceEvent",
    "Trap",
    "Usage",
    "__version__",
]
