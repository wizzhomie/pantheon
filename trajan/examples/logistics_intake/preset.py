"""The preset: one object carrying everything this domain brings to the core.

A reader can answer "what does this pipeline actually require?" by opening this file. The
rubric is data, the prompt is text, the scoring spec is data — none of it is spread across
code paths, which is also why ``preset.fingerprint()`` can tell two reports apart.
"""

from __future__ import annotations

from pathlib import Path

from trajan import Case, Criterion, Feedback, FieldSpec, Preset, Rubric, ScoringSpec

from .checks import CHECKS
from .schema import ExtractedIntake

PROMPTS = Path(__file__).with_name("prompts")


def _read_prompt(name: str) -> str:
    """Prompts are text files: the same file feeds the Python preset and the config file."""
    return (PROMPTS / name).read_text(encoding="utf-8")


AGENT_PROMPT = _read_prompt("agent.txt")

JUDGE_INSTRUCTIONS = _read_prompt("judge.txt")

RUBRIC = Rubric(
    criteria=[
        Criterion(
            id="grounding",
            title="Grounding",
            description=(
                "5: every filled value is stated in the email and its quote is verbatim. "
                "1: a value is invented, or its quote is not in the email."
            ),
            weight=0.5,
        ),
        Criterion(
            id="completeness",
            title="Completeness",
            description=(
                "5: every fact the email states is extracted, and what it does not state is "
                "listed in unresolved. 1: a stated fact is missing or an unstated one is "
                "filled instead of being marked unresolved."
            ),
            weight=0.3,
        ),
        Criterion(
            id="usability",
            title="Usability",
            description=(
                "5: the intake can be priced and booked without opening the email. "
                "1: a colleague would have to re-read the email to act on it."
            ),
            weight=0.2,
        ),
    ],
    pass_score=0.75,
)

SCORING = ScoringSpec(
    fields=[
        FieldSpec(path="request_type", comparator="exact"),
        FieldSpec(path="contact.name"),
        FieldSpec(path="contact.email"),
        FieldSpec(path="origin.city"),
        FieldSpec(path="origin.country_code", comparator="exact"),
        FieldSpec(path="destination.city"),
        FieldSpec(path="destination.country_code", comparator="exact"),
        # Only the first cargo line is scored: a dotted path cannot say "every item", and a
        # spec that silently scored a variable number of rows would make runs incomparable.
        FieldSpec(path="cargo.0.description", comparator="fuzzy", tolerance=0.85),
        FieldSpec(path="cargo.0.quantity", comparator="numeric"),
        FieldSpec(path="cargo.0.weight_kg", comparator="numeric", tolerance=0.5),
        FieldSpec(path="pickup_date", comparator="exact"),
        FieldSpec(path="delivery_date", comparator="exact"),
        FieldSpec(path="quote_amount", comparator="numeric", tolerance=0.01),
        FieldSpec(path="quote_currency", comparator="exact"),
        FieldSpec(path="cargo_requirements", comparator="set"),
        # `unresolved` is not scored as a value: a perfect extraction may honestly list a
        # field twice or skip a postcode nobody asked about, and set equality would punish
        # both the same way. Its honesty is a check instead: every path it names must be
        # empty in the output (`G1_UNRESOLVED_BUT_PRESENT`).
    ],
    # A case counts as correct only when every scored field matches: a report that rounds
    # "9 of 10 fields" up to a pass hides the failure that ships.
    min_field_accuracy=1.0,
)

_ADDENDA: dict[str, str] = {
    "L2_REEFFER_WITHOUT_TEMPERATURE": (
        "The reefer temperature is written in the email; read the whole body before "
        "marking cargo.0.temperature_c unresolved."
    ),
    "L3_TEMPERATURE_WITHOUT_REQUIREMENT": (
        "Do not infer a temperature from the cargo description: copy a temperature only "
        "when the sender wrote one, and add the handling requirement they wrote."
    ),
    "L4_NO_CONTACT_CHANNEL": (
        "The signature block usually carries the phone number; read it before concluding "
        "there is no way to reply."
    ),
    "L1_PICKUP_AFTER_DELIVERY": (
        "One of the two dates is probably a typo. Keep the dates as written and mark the "
        "suspicious one unresolved instead of swapping them silently."
    ),
}


def build_retry(case: Case, feedback: Feedback) -> Feedback:
    """Add domain guidance to the generic feedback before the next attempt.

    This hook exists because the core can say *what* is wrong but not *where a human would
    look*: "the temperature is missing" becomes "the temperature is written in the email,
    read the whole body". The cap of ten instructions is the contract's, so it is applied
    here rather than trusted to the caller.
    """
    addenda = [
        text
        for code, text in _ADDENDA.items()
        if any(issue.code == code for issue in feedback.issues)
    ]
    instructions = list(dict.fromkeys([*addenda, *feedback.instructions]))
    return feedback.model_copy(update={"instructions": instructions[:10]})


PRESET: Preset[ExtractedIntake] = Preset(
    name="logistics_intake",
    output_model=ExtractedIntake,
    rubric=RUBRIC,
    checks=CHECKS,
    scoring=SCORING,
    agent_prompt=AGENT_PROMPT,
    judge_instructions=JUDGE_INSTRUCTIONS,
    agent_model="deepseek-flash",
    build_retry=build_retry,
)
