"""The reviewer prompt: a change, a budget, and a format strict enough to check.

The prompt is part of the contract, not a suggestion. It names the only three things the
model is allowed to do — report a defect it can point at, report a risk, or report a gap —
and it says that an empty list is an acceptable answer. That last sentence is doing real
work: a reviewer that must find something will find something.

Prompts are read byte for byte (no ``strip``, no re-wrapping), because a cassette key is a
hash of the request: an accidental change to whitespace is a changed experiment.
"""

from __future__ import annotations

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

Prefer three findings you can defend over ten guesses. Do not report style, naming or \
formatting. If the change looks right, return an empty list — that is a valid answer.

Answer with JSON only, no prose and no code fences:
{"summary": "one sentence about the change", "findings": [{"file": "...", "line": 1, \
"quote": "...", "title": "...", "severity": "blocking|major|minor", "kind": \
"defect|risk|test-gap", "detail": "why it matters"}]}
"""

MARKER = {True: "+", False: " "}


def build_user_message(diff: Diff, *, max_bytes: int) -> str:
    """Render the diff as numbered lines, stopping at the prompt's byte ceiling."""
    header = [
        f"Changed files: {len(diff.files)}",
        ", ".join(change.path for change in diff.files) or "(none)",
        "",
    ]
    body: list[str] = []
    used = sum(len(line) + 1 for line in header)
    for change in diff.files:
        block = [f"--- {change.path} ({change.status}, removed {change.removed})"]
        for line in change.lines:
            block.append(f"{line.number:>5} {MARKER[line.added]} {line.text}")
        block.append("")
        size = sum(len(line) + 1 for line in block)
        if used + size > max_bytes:
            body.append(f"--- {change.path}: not shown, the diff budget ran out")
            break
        body.extend(block)
        used += size
    return "\n".join(header + body)


def system_message(instructions: str) -> str:
    """The system prompt, plus whatever the project insists on, in that order."""
    if not instructions:
        return SYSTEM
    return f"{SYSTEM}\nProject rules for this repository:\n{instructions}\n"
