"""Public data contracts.

Everything here is a frozen Pydantic model: results are evidence, so they are immutable,
closed to unknown fields, and safe to serialise into a report.
"""

from .agent import AgentFn, AgentReply, Attempt, Feedback
from .base import JsonValue, StrictModel
from .case import Case
from .evals import (
    CaseCategory,
    CaseChange,
    CaseResult,
    CategoryMetrics,
    Comparator,
    EvalCase,
    EvalReport,
    FieldSpec,
    MetaEval,
    MetricDelta,
    Metrics,
    ReportDiff,
    ScoringSpec,
    Trap,
)
from .issues import (
    CheckResult,
    GateResult,
    Issue,
    IssueSource,
    Severity,
)
from .rubric import Criterion, Rubric
from .trace import (
    EscalationReason,
    Mode,
    RunResult,
    Stage,
    Status,
    Totals,
    TraceEvent,
    Usage,
)
from .verdict import JudgeVerdict

__all__ = [
    "AgentFn",
    "AgentReply",
    "Attempt",
    "Case",
    "CaseCategory",
    "CaseChange",
    "CaseResult",
    "CategoryMetrics",
    "CheckResult",
    "Comparator",
    "Criterion",
    "EscalationReason",
    "EvalCase",
    "EvalReport",
    "Feedback",
    "FieldSpec",
    "GateResult",
    "Issue",
    "IssueSource",
    "JsonValue",
    "JudgeVerdict",
    "MetaEval",
    "MetricDelta",
    "Metrics",
    "Mode",
    "ReportDiff",
    "Rubric",
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
]
