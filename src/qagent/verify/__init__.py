"""Running a project's own checks, deciding what they mean, and writing it down.

This is the deterministic half of verification: no model, no interpretation, just "run the
thing the project already has, keep the evidence, and let code decide what it adds up to".
The rules that make it trustworthy are deliberately few and boring:

* a check is an argv list, never a shell string — quoting bugs and injection both disappear;
* the environment is a whitelist, so a check does not inherit the reviewer's secrets;
* every outcome is a status, including the ones that are not a verdict: ``timeout``,
  ``error`` (could not run), ``skipped`` (budget or policy) and ``not_applicable`` (the tool
  the check needs is not in the project);
* the output is kept as an artifact next to the run, and a short tail goes into the record;
* the verdict is a pure function of the record and the findings — never a model's score;
* the report is a rendering of the JSON next to it, so the two cannot disagree.

The layers are separate on purpose: :mod:`qagent.verify.engine` runs and records,
:mod:`qagent.verify.verdict` decides, :mod:`qagent.verify.report` presents. Mixing them
would let the evidence argue for its own conclusion.
"""

from __future__ import annotations

from .contracts import (
    Budget,
    CheckRun,
    CheckStatus,
    CommandSpec,
    PlanEntry,
    Sandbox,
    SandboxRecord,
    VerificationPolicy,
    VerificationRequest,
    VerificationRun,
)
from .engine import (
    build_plan,
    describe_limits,
    describe_plan,
    describe_sandbox,
    read_policy,
    run_policy,
    write_run,
)
from .findings import (
    Finding,
    FindingKind,
    FindingSeverity,
    FindingSource,
    FindingStatus,
    Location,
    findings_from_run,
)
from .report import (
    VerificationReport,
    build_report,
    read_report,
    render_markdown,
    write_report,
)
from .verdict import Verdict, VerdictCode, VerdictReason, VerdictStatus, decide

__all__ = [
    "Budget",
    "CheckRun",
    "CheckStatus",
    "CommandSpec",
    "Finding",
    "FindingKind",
    "FindingSeverity",
    "FindingSource",
    "FindingStatus",
    "Location",
    "PlanEntry",
    "Sandbox",
    "SandboxRecord",
    "Verdict",
    "VerdictCode",
    "VerdictReason",
    "VerdictStatus",
    "VerificationPolicy",
    "VerificationReport",
    "VerificationRequest",
    "VerificationRun",
    "build_plan",
    "build_report",
    "decide",
    "describe_limits",
    "describe_plan",
    "describe_sandbox",
    "findings_from_run",
    "read_policy",
    "read_report",
    "render_markdown",
    "run_policy",
    "write_report",
    "write_run",
]
