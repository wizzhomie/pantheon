"""Running a project's own checks and keeping what they said.

This is the deterministic half of verification: no model, no interpretation, just "run the
thing the project already has and keep the evidence". The rules that make it trustworthy are
deliberately few and boring:

* a check is an argv list, never a shell string — quoting bugs and injection both disappear;
* the environment is a whitelist, so a check does not inherit the reviewer's secrets;
* every outcome is a status, including the ones that are not a verdict: ``timeout``,
  ``error`` (could not run), ``skipped`` (budget or policy) and ``not_applicable`` (the tool
  the check needs is not in the project);
* the output is kept as an artifact next to the run, and a short tail goes into the record.

The engine deliberately knows nothing about verdicts: deciding what a failed test means is a
later layer (:mod:`qagent.verify` will grow one), and mixing the two would make the evidence
argue for its own conclusion.
"""

from __future__ import annotations

from .contracts import (
    Budget,
    CheckRun,
    CheckStatus,
    CommandSpec,
    PlanEntry,
    VerificationPolicy,
    VerificationRun,
)
from .engine import build_plan, describe_plan, read_policy, run_policy, write_run

__all__ = [
    "Budget",
    "CheckRun",
    "CheckStatus",
    "CommandSpec",
    "PlanEntry",
    "VerificationPolicy",
    "VerificationRun",
    "build_plan",
    "describe_plan",
    "read_policy",
    "run_policy",
    "write_run",
]
