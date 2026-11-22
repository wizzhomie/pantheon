"""agentqa — embedded QA for LLM agents.

A preset-driven quality layer for any step that must return structured data:
deterministic checks, an independent LLM judge with a data-driven rubric, a bounded
self-correction loop under an explicit budget, and an offline regression suite that
reuses the same preset.

``guard(agent, preset, llm=...)`` is the entry point: it returns a
:class:`GuardedAgent` whose ``run`` executes the loop, and the same preset drives the
offline regression run. See ``docs/design.md`` for the design and ``docs/how-it-works.md``
for the plain-language version.
"""

from __future__ import annotations

from .checks import Check, CheckContext, run_checks
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
    AgentReply,
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
from .judge import (
    FunctionJudge,
    Judge,
    JudgeResult,
    LLMJudge,
    calibrate,
    score_calibration,
)
from .llm import (
    FakeClient,
    LLMClient,
    LLMRequest,
    LLMResponse,
    Message,
    ReplayClient,
    usage_of,
)
from .parsing import ParseOutcome, parse_attempt
from .runtime import GuardedAgent, Preset, guard, render_feedback

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_CONFIG",
    "AgentFn",
    "AgentReply",
    "Attempt",
    "Case",
    "CaseCategory",
    "CaseChange",
    "CaseResult",
    "CategoryMetrics",
    "Check",
    "CheckContext",
    "CheckResult",
    "Comparator",
    "Criterion",
    "EscalationPolicy",
    "EscalationReason",
    "EvalCase",
    "EvalReport",
    "FakeClient",
    "Feedback",
    "FieldSpec",
    "FunctionJudge",
    "GateResult",
    "GuardedAgent",
    "Issue",
    "IssueSource",
    "JsonValue",
    "Judge",
    "JudgePolicy",
    "JudgeResult",
    "JudgeVerdict",
    "LLMClient",
    "LLMJudge",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "MetaEval",
    "MetricDelta",
    "Metrics",
    "Mode",
    "ModelPrice",
    "ParseOutcome",
    "Preset",
    "Pricing",
    "QAConfig",
    "ReplayClient",
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
    "calibrate",
    "guard",
    "parse_attempt",
    "render_feedback",
    "run_checks",
    "score_calibration",
    "usage_of",
]
