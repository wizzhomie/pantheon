"""Turning a failed attempt into the feedback the agent gets next.

The default text is deliberately dry and specific: "field ``cargo.weight`` is not supported
by the input" changes behaviour, while "please be more careful" does not. Field hints and
the judge's own instructions come first; the three non-negotiables of the loop — fix only
what is listed, never invent values, answer with one JSON object — are always appended.

:func:`render_feedback` is the other half: it turns the structured contract into prompt
text for agents that build their own messages. Presets that need a different layout write
their own renderer or their own agent; the contract in between stays identical.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..contracts.agent import Feedback
from ..contracts.issues import SEVERITY_RANK, Issue
from ..contracts.verdict import JudgeVerdict

DEFAULT_IMPERATIVES: tuple[str, ...] = (
    "Fix only the problems listed here; keep the fields that were already correct.",
    "Do not add values the input does not support. If a value is unknown, mark it as "
    "unresolved instead of guessing.",
    "Return a single JSON object that matches the schema, without prose around it.",
)

MAX_FEEDBACK_ISSUES = 12
MAX_INSTRUCTION_CHARS = 300
MAX_PREVIOUS_OUTPUT_CHARS = 20_000


def select_issues(issues: Iterable[Issue], limit: int = MAX_FEEDBACK_ISSUES) -> list[Issue]:
    """Keep the most severe issues, deterministically ordered.

    Retry prompts have a size budget too: twelve concrete defects are actionable, forty
    are noise the model will skim.
    """
    ordered = sorted(
        issues,
        key=lambda issue: (
            -SEVERITY_RANK[issue.severity],
            issue.code,
            issue.field_path or "",
        ),
    )
    return ordered[:limit]


def distill_instructions(
    issues: Sequence[Issue], verdict: JudgeVerdict | None
) -> list[str]:
    """Build at most ten imperative instructions, without duplicates."""
    candidates: list[str] = []
    if verdict is not None:
        candidates.extend(verdict.fix_instructions)
    candidates.extend(DEFAULT_IMPERATIVES)
    candidates.extend(issue.hint for issue in select_issues(issues) if issue.hint)

    seen: set[str] = set()
    result: list[str] = []
    for raw in candidates:
        text = " ".join(raw.split())[:MAX_INSTRUCTION_CHARS]
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        result.append(text)
        if len(result) == 10:
            break
    return result


def build_feedback(
    *,
    attempt: int,
    issues: Sequence[Issue],
    verdict: JudgeVerdict | None,
    previous_output: str | None,
    include_previous_output: bool = True,
) -> Feedback:
    """Assemble the retry payload, honouring the policy's size and content choices."""
    kept = select_issues(issues)
    carried = None
    if include_previous_output and previous_output:
        carried = previous_output[:MAX_PREVIOUS_OUTPUT_CHARS]
    return Feedback(
        attempt=attempt,
        issues=kept,
        judge=verdict,
        instructions=distill_instructions(kept, verdict),
        previous_output=carried,
    )


def render_feedback(feedback: Feedback) -> str:
    """Plain-text rendering of :class:`Feedback`, ready to append to a prompt."""
    lines = [f"Attempt {feedback.attempt} did not pass QA."]
    if feedback.judge is not None:
        verdict = "passed" if feedback.judge.passed else "failed"
        lines.append(f"Judge verdict: {verdict}, score {feedback.judge.score:.2f}.")
    if feedback.issues:
        lines.append("Problems found:")
        for issue in feedback.issues:
            field = f" at {issue.field_path}" if issue.field_path else ""
            lines.append(f"- {issue.severity} {issue.code}{field}: {issue.message}")
            if issue.hint:
                lines.append(f"  fix: {issue.hint}")
    if feedback.instructions:
        lines.append("Instructions:")
        lines.extend(f"- {instruction}" for instruction in feedback.instructions)
    if feedback.previous_output:
        lines.append("Your previous answer:")
        lines.append(feedback.previous_output)
    return "\n".join(lines)
