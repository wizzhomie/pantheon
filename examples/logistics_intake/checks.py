"""The domain half of the preset: only logistics knows these rules.

The core ships checks that are true in any domain (required fields, ISO formats, ranges,
quoted evidence, PII). Everything that depends on *this* business is a predicate with its
own issue code, so it shows up in reports and in retry feedback exactly like a built-in
check. The rules are written as "return True when the rule holds": a false predicate is the
defect, which keeps each rule one sentence long.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from qagent import Check
from qagent.checks import (
    ConsistencyCheck,
    DuplicatesCheck,
    EvidenceCheck,
    FormatRule,
    FormatsCheck,
    NumberRange,
    PiiCheck,
    RangesCheck,
    RefusalCheck,
    RequiredCheck,
    RequiredRule,
    Rule,
    quotes_from_attribute,
)

from .schema import ExtractedIntake

COLD_CHAIN = frozenset({"reefer", "chilled", "frozen"})
MAX_ITEM_WEIGHT_KG = 30_000.0
MIN_TEMPERATURE_C = -30.0
MAX_TEMPERATURE_C = 25.0


def _intake(output: BaseModel) -> ExtractedIntake | None:
    """Narrow the protocol's ``BaseModel`` to this preset's schema.

    A check that receives another model returns ``True`` instead of crashing: composition of
    two presets is a supported way to use the library, not an error.
    """
    return output if isinstance(output, ExtractedIntake) else None


def _iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_booking(output: BaseModel) -> bool:
    intake = _intake(output)
    return intake is not None and intake.request_type == "booking"


def _requirements(intake: ExtractedIntake) -> frozenset[str]:
    return frozenset(item.casefold() for item in intake.cargo_requirements)


def pickup_not_after_delivery(output: BaseModel) -> bool:
    """A pickup after its delivery is a typo, not a plan."""
    intake = _intake(output)
    if intake is None:
        return True
    pickup, delivery = _iso(intake.pickup_date), _iso(intake.delivery_date)
    if pickup is None or delivery is None:
        return True
    return pickup <= delivery


def cold_cargo_has_a_temperature(output: BaseModel) -> bool:
    """A reefer request without a temperature cannot be priced or loaded."""
    intake = _intake(output)
    if intake is None or "reefer" not in _requirements(intake):
        return True
    return all(item.temperature_c is not None for item in intake.cargo)


def temperature_has_a_cold_requirement(output: BaseModel) -> bool:
    """The mirror rule: a temperature nobody asked for is an invented constraint."""
    intake = _intake(output)
    if intake is None:
        return True
    if not any(item.temperature_c is not None for item in intake.cargo):
        return True
    return bool(_requirements(intake) & COLD_CHAIN)


def contact_has_a_channel(output: BaseModel) -> bool:
    """A lead nobody can answer is not a lead."""
    intake = _intake(output)
    if intake is None:
        return True
    return bool(intake.contact.email or intake.contact.phone)


def cargo_is_plausible(output: BaseModel) -> bool:
    """Per-item bounds the generic ``RangesCheck`` cannot express over a list."""
    intake = _intake(output)
    if intake is None:
        return True
    for item in intake.cargo:
        if item.weight_kg is not None and not 1.0 <= item.weight_kg <= MAX_ITEM_WEIGHT_KG:
            return False
        if item.temperature_c is not None and not (
            MIN_TEMPERATURE_C <= item.temperature_c <= MAX_TEMPERATURE_C
        ):
            return False
    return True


CHECKS: list[Check] = [
    RequiredCheck(
        rules=[
            RequiredRule("request_type"),
            RequiredRule("contact.name"),
            RequiredRule("origin.city"),
            RequiredRule("origin.country_code"),
            RequiredRule("destination.city"),
            RequiredRule("destination.country_code"),
            RequiredRule("cargo"),
            RequiredRule(
                "pickup_date",
                when=_is_booking,
                hint="bookings need a loading date; a quote can stay unresolved",
            ),
        ],
        unresolved_path="unresolved",
    ),
    FormatsCheck(
        rules=[
            FormatRule("origin.country_code", "country_code"),
            FormatRule("destination.country_code", "country_code"),
            FormatRule("contact.email", "email"),
            FormatRule("contact.phone", "phone"),
            FormatRule("pickup_date", "iso_date"),
            FormatRule("delivery_date", "iso_date"),
            FormatRule("quote_currency", "currency"),
        ]
    ),
    RangesCheck(
        numbers=[NumberRange("quote_amount", minimum=0.0, maximum=500_000.0, unit="EUR-equivalent")]
    ),
    ConsistencyCheck(
        rules=[
            Rule(
                code="L1_PICKUP_AFTER_DELIVERY",
                message="the pickup date is after the delivery date",
                predicate=pickup_not_after_delivery,
                hint="swap the dates or ask the sender which one is right",
                field_path="pickup_date",
            ),
            Rule(
                code="L2_REEFFER_WITHOUT_TEMPERATURE",
                message="a reefer request has cargo without a temperature",
                predicate=cold_cargo_has_a_temperature,
                hint="extract the temperature the sender states, or ask for it",
                field_path="cargo",
            ),
            Rule(
                code="L3_TEMPERATURE_WITHOUT_REQUIREMENT",
                message="cargo has a temperature but no reefer/chilled/frozen requirement",
                predicate=temperature_has_a_cold_requirement,
                hint="drop the temperature or add the handling requirement the email states",
                field_path="cargo",
            ),
            Rule(
                code="L4_NO_CONTACT_CHANNEL",
                message="neither an email nor a phone number was extracted",
                predicate=contact_has_a_channel,
                hint="copy the signature block verbatim before giving up",
                field_path="contact",
            ),
            Rule(
                code="L5_CARGO_OUT_OF_RANGE",
                message="a cargo line is outside plausible weight or temperature bounds",
                predicate=cargo_is_plausible,
                hint="re-check the unit (tonnes vs kilograms) and the minus sign",
                field_path="cargo",
            ),
        ]
    ),
    DuplicatesCheck(path="cargo", keys=("description", "weight_kg")),
    EvidenceCheck(
        quotes=quotes_from_attribute("evidence"),
        required_paths=("origin.city", "destination.city"),
    ),
    PiiCheck(allow_paths=("contact.email", "contact.phone")),
    RefusalCheck(paths=("notes",)),
]
