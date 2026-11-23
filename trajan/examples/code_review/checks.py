"""Deterministic checks for a review: every finding must point at code that is there.

These are the cheap half of the loop and, in this task, the important half. A model that
finds the real bug but reports it on line 41 of a 30-line file has not reviewed the code; a
quote that is not in the file is an invented line. Both are caught here, for free, before
any judge is asked and before a retry is spent.

The two checks are pure functions of ``(output, context)`` — no clock, no network, no
``golden`` file — which is exactly why they can run on live traffic too.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from qagent import CheckContext, Issue
from qagent.checks import Tier

from .schema import Review

CODE_QUOTE_INVENTED = "C1_QUOTE_NOT_IN_CODE"
CODE_LINE_OUT_OF_RANGE = "C2_LINE_OUT_OF_RANGE"
CODE_QUOTE_OFF_LINE = "C3_QUOTE_NOT_ON_LINE"
CODE_VERDICT_CONTRADICTS = "C4_VERDICT_CONTRADICTS_FINDINGS"


def _review(output: BaseModel) -> Review | None:
    """Narrow to this preset's schema; another model means "not mine" and passes."""
    return output if isinstance(output, Review) else None


def _normalise(line: str) -> str:
    return " ".join(line.split()).casefold()


class AnchorCheck:
    """The line exists, and the quote is that line (or at least in the file)."""

    id = "anchors"
    tier: Tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        review = _review(output)
        if review is None:
            return []
        lines = ctx.raw_text.splitlines()
        issues: list[Issue] = []
        for index, finding in enumerate(review.findings):
            path = f"findings.{index}"
            if finding.line > len(lines):
                issues.append(
                    Issue(
                        code=CODE_LINE_OUT_OF_RANGE,
                        severity="error",
                        source="check",
                        field_path=path,
                        message=(
                            f"finding points at line {finding.line}, but the code has "
                            f"{len(lines)} lines"
                        ),
                        hint="cite a line that exists in the snippet you were given",
                    )
                )
                continue
            actual = _normalise(lines[finding.line - 1])
            quoted = _normalise(finding.quote)
            if actual == quoted:
                continue
            in_file = any(_normalise(line) == quoted for line in lines)
            if in_file:
                issues.append(
                    Issue(
                        code=CODE_QUOTE_OFF_LINE,
                        severity="error",
                        source="check",
                        field_path=path,
                        message="the quoted line is in the code, but not on the stated line",
                        hint="the line number and the quote must describe the same line",
                    )
                )
            else:
                issues.append(
                    Issue(
                        code=CODE_QUOTE_INVENTED,
                        severity="error",
                        source="check",
                        field_path=path,
                        message="the quote does not appear anywhere in the code",
                        hint="copy the line verbatim from the snippet you were given",
                    )
                )
        return issues


class VerdictCheck:
    """``ok`` and a list of findings cannot both be true."""

    id = "verdict"
    tier: Tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        review = _review(output)
        if review is None:
            return []
        if review.verdict == "problems" and not review.findings:
            return [
                Issue(
                    code=CODE_VERDICT_CONTRADICTS,
                    severity="error",
                    source="check",
                    field_path="verdict",
                    message="the verdict says 'problems' but no finding was reported",
                    hint="either report the defect or answer 'ok'",
                )
            ]
        if review.verdict == "ok" and review.findings:
            return [
                Issue(
                    code=CODE_VERDICT_CONTRADICTS,
                    severity="warning",
                    source="check",
                    field_path="verdict",
                    message="the verdict says 'ok' while findings are listed",
                    hint="set the verdict to 'problems' when you report a defect",
                )
            ]
        return []


CHECKS: Sequence[object] = (AnchorCheck(), VerdictCheck())


def extra_checks() -> list[object]:
    """The factory ``qagent.toml`` names under ``[checks] extra``."""
    return list(CHECKS)
