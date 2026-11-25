"""The reviewer prompt: a change, a budget, and a format strict enough to check.

The prompt is part of the contract, not a suggestion. It names the only three things the
model is allowed to do — report a defect it can point at, report a risk, or report a gap —
and it says that an empty list is an acceptable answer. That last sentence is doing real
work: a reviewer that must find something will find something.

Prompts are read byte for byte (no ``strip``, no re-wrapping), because a cassette key is a
hash of the request: an accidental change to whitespace is a changed experiment.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .contracts import ProposedFinding
from .diff import Diff

SYSTEM = """\
You review a code change. You do not review the whole repository: you see the changed files \
and the lines around the change.

Report only problems a reader of the change would have to fix or decide about:
- defect: the changed code is wrong and you can say why;
- risk: the change can break something in a condition you name;
- test-gap: the change is not covered by the tests that exist.

Cite the place exactly: file (as it appears in the diff), line (in the new file), quote (the \
line's text copied verbatim, without the diff marker). A finding whose quote is not on that \
line is discarded, so an invented quote wastes your answer.

You see a window, not the whole file: the lines of the diff and a little around them. A name \
the window does not define or import may be defined elsewhere in the file, and behaviour it \
does not show a test for may be tested outside the window. Report neither as missing.

Prefer three findings you can defend over ten guesses. Do not report style, naming or \
formatting. If the change looks right, return an empty list — that is a valid answer.

If a finding breaks one of the project's approved requirements, name its id in \
"requirement". Only ids from the list you are given are accepted; do not invent them.

Answer with JSON only, no prose and no code fences:
{"summary": "one sentence about the change", "findings": [{"file": "...", "line": 1, \
"quote": "...", "title": "...", "severity": "blocking|major|minor", "kind": \
"defect|risk|test-gap", "requirement": "R1 or null", "detail": "why it matters"}]}
"""

MARKER = {True: "+", False: " "}

SECOND_LOOK = """\
You are the second reader of a review. Another reviewer read a code change and made the claims \
below. Decide each claim on the change as shown, not on how confidently it is written.

Drop a claim when the code does not do what the claim says, when the claim's own reasoning \
reaches the opposite conclusion, or when it is about code the change does not show. Keep a \
claim when a reader of the change would still have to fix or decide something.

Answer with JSON only, no prose and no code fences:
{"verdicts": [{"index": 1, "keep": true, "reason": "one sentence"}]}
"""

MAX_REQUIREMENTS = 20
"""How many promises the prompt shows; the rest are counted, not pasted."""


@dataclass(frozen=True, slots=True)
class Question:
    """The prompt the reviewer gets, and which files it actually shows.

    A change above the ceiling is not shown whole, and the files that did not fit are the
    difference between "the reviewer found nothing" and "the reviewer was not shown".
    """

    text: str
    shown: tuple[str, ...]


def build_user_message(
    diff: Diff, *, max_bytes: int, requirements: Sequence[tuple[str, str]] = ()
) -> str:
    """The rendered question; ``render_question`` is the same text plus what it shows."""
    return render_question(diff, max_bytes=max_bytes, requirements=requirements).text


def render_question(
    diff: Diff, *, max_bytes: int, requirements: Sequence[tuple[str, str]] = ()
) -> Question:
    """Render the diff as numbered lines, stopping at the prompt's byte ceiling.

    Approved requirements come first: they are the vocabulary a finding uses to say *why*
    something is wrong, and a reviewer that never sees them can only report symptoms.
    """
    header: list[str] = []
    if requirements:
        header.append("Approved requirements and invariants (cite the id a finding violates):")
        for item_id, title in requirements[:MAX_REQUIREMENTS]:
            header.append(f"- {item_id}: {title[:200]}")
        if len(requirements) > MAX_REQUIREMENTS:
            header.append(f"- and {len(requirements) - MAX_REQUIREMENTS} more")
        header.append("")
    header.extend(
        [
            f"Changed files: {len(diff.files)}",
            ", ".join(change.path for change in diff.files) or "(none)",
            "",
        ]
    )
    body: list[str] = []
    shown: list[str] = []
    used = sum(len(line) + 1 for line in header)
    for change in diff.files:
        block = [f"--- {change.path} ({change.status}, removed {change.removed})"]
        for line in change.lines:
            block.append(f"{line.number:>5} {MARKER[line.added]} {line.text}")
        block.append("")
        size = sum(len(line) + 1 for line in block)
        if used + size > max_bytes:
            body.append(f"--- {change.path}: not shown, the diff budget ran out")
            continue
        body.extend(block)
        used += size
        shown.append(change.path)
    return Question(text="\n".join(header + body), shown=tuple(shown))


def system_message(instructions: str) -> str:
    """The system prompt, plus whatever the project insists on, in that order."""
    if not instructions:
        return SYSTEM
    return f"{SYSTEM}\nProject rules for this repository:\n{instructions}\n"


def second_look_message(diff_text: str, findings: Sequence[ProposedFinding]) -> str:
    """The first review's claims, numbered the way the second reader must answer them."""
    claims: list[str] = []
    for index, finding in enumerate(findings, start=1):
        claims.append(f"Claim {index} ({finding.kind}, {finding.severity}): {finding.title}")
        claims.append(f"  at {finding.file}:{finding.line} — {finding.quote}")
        if finding.detail:
            claims.append(f"  reasoning: {finding.detail}")
    return f"{diff_text}\n\nClaims to check:\n" + "\n".join(claims)
