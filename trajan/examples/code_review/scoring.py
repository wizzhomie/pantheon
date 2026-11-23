"""Whole-answer scoring: the planted bugs must be found, and nothing else invented.

Field-by-field comparison cannot express this: "did the review point at line 7" is a
relation between the answer and a golden line, not a field value. The project therefore
owns the rule, and ``[scoring] custom`` in ``trajan.toml`` names it.

Two numbers matter and they pull in opposite directions:

* ``field_accuracy`` here is recall — the share of planted bugs that was reported;
* a single spurious finding fails the case even when every bug was found. A reviewer that
  reports the whole file as broken would otherwise score perfectly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from trajan.evals.scoring import CaseScore

from .schema import Review

LINE_SLACK = 1
"""A finding one line off still counts: the bug is often on the line after the anchor."""


def score_review(output: BaseModel | None, expected: dict[str, Any]) -> CaseScore:
    """Compare a review with the planted bug lines of one case."""
    bugs = [int(line) for line in expected.get("bug_lines", [])]
    review = output if isinstance(output, Review) else None
    findings = (
        [] if review is None else [(finding.line, finding.quote) for finding in review.findings]
    )

    def near(line: int, target: int) -> bool:
        return abs(line - target) <= LINE_SLACK

    found = [bug for bug in bugs if any(near(line, bug) for line, _ in findings)]
    spurious = [
        (line, quote)
        for line, quote in findings
        if not any(near(line, bug) for bug in bugs)
    ]
    missing = [f"line {bug}" for bug in bugs if bug not in found]
    wrong = [f"line {line}" for line, _ in spurious]

    if bugs:
        accuracy = len(found) / len(bugs)
    elif findings:
        accuracy = 0.0
    else:
        accuracy = 1.0
    passed = len(found) == len(bugs) and not spurious
    return CaseScore(
        field_accuracy=round(accuracy, 4),
        passed=passed,
        missing=missing,
        wrong=wrong,
        hallucinated=[],
    )
