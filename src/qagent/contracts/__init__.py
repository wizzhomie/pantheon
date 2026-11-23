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
from .findings import (
    FINDING_ID_PATTERN,
    Finding,
    FindingKind,
    FindingSeverity,
    FindingSource,
    FindingStatus,
    Location,
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
    "FINDING_ID_PATTERN",
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
    "Finding",
    "FindingKind",
    "FindingSeverity",
    "FindingSource",
    "FindingStatus",
    "GateResult",
    "Issue",
    "IssueSource",
    "JsonValue",
    "JudgeVerdict",
    "Location",
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
