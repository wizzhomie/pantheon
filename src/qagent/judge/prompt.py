"""Prompt construction for the judge.

The prompt is assembled from three explicit parts — standing rules, the preset's rubric and
its domain instructions — plus the artefacts under review. Keeping it data-driven means the
same rubric renders the prompt and the scoring formula, so the two cannot drift apart.
"""

from __future__ import annotations

from ..contracts.case import Case
from ..contracts.rubric import Rubric
from ..llm.base import Message

SYSTEM_RULES = """\
You are a strict quality reviewer for structured data produced by an extraction agent.
You receive the INPUT the agent saw and the OUTPUT it produced.

Rules:
1. Judge only what the INPUT supports. Plausible is not evidence.
2. Score every rubric criterion with an integer from 1 (wrong) to 5 (exactly right).
   Use 3 for "partly right".
3. Report each defect as an issue: the field path, what is wrong, and what to do instead.
4. A value present in the OUTPUT but absent from the INPUT is the most serious defect.
5. If the INPUT is contradictory or too thin to judge, set must_escalate=true and say why.
6. Never return a pass/fail verdict or an overall score: the pipeline computes them from
   your criterion scores.
7. Return only the JSON object described by the schema. No prose, no code fences.
"""

OUTPUT_LIMIT = 8_000
"""Characters of serialised output shown to the judge; longer reports are truncated."""


def render_rubric(rubric: Rubric) -> str:
    """Rubric as prompt text: id, weight and meaning of each criterion."""
    lines = ["Rubric:"]
    for criterion in rubric.criteria:
        lines.append(
            f"- {criterion.id} (weight {criterion.weight:g}, {criterion.title}): "
            f"{criterion.description}"
        )
    lines.append(f"Pass threshold: {rubric.pass_score:g} of the weighted score.")
    return "\n".join(lines)


def build_messages(
    case: Case,
    output_json: str,
    rubric: Rubric,
    instructions: str = "",
    max_input_chars: int = 20_000,
) -> list[Message]:
    """Assemble the system and user turns for one review."""
    sections = [SYSTEM_RULES]
    if instructions.strip():
        sections.append(f"Preset instructions:\n{instructions.strip()}")
    sections.append(render_rubric(rubric))

    user = "\n\n".join(
        [
            f"INPUT:\n{_clip(case.as_text(), max_input_chars, 'input')}",
            f"OUTPUT (JSON):\n{_clip(output_json, OUTPUT_LIMIT, 'output')}",
        ]
    )
    return [
        Message(role="system", content="\n\n".join(sections)),
        Message(role="user", content=user),
    ]


def _clip(text: str, limit: int, label: str) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[... {label} truncated at {limit} characters ...]"
