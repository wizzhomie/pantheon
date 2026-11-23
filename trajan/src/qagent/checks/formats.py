"""Format rules: ISO-ish shapes and closed enums.

The default for country and currency is *shape only* (``^[A-Z]{2}$`` / ``^[A-Z]{3}$``),
not a bundled registry: a stale list of 249 country codes inside a library is a
maintenance trap, and a preset that needs a whitelist passes ``allowed=``. That is a
deliberate trade — the check stays correct forever, the preset owns the knowledge.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel

from ..contracts.issues import Issue, Severity
from .base import CheckContext, Tier
from .paths import MISSING, get_path

FormatKind = Literal[
    "country_code",
    "currency",
    "iso_date",
    "email",
    "phone",
    "url",
    "language",
    "enum",
]

FORMAT_CODES: dict[FormatKind, str] = {
    "country_code": "G2_BAD_COUNTRY",
    "currency": "G2_BAD_CURRENCY",
    "iso_date": "G2_BAD_DATE",
    "email": "G2_BAD_EMAIL",
    "phone": "G2_BAD_PHONE",
    "url": "G2_BAD_URL",
    "language": "G2_BAD_LANGUAGE",
    "enum": "G2_BAD_ENUM",
}

HINTS: dict[FormatKind, str] = {
    "country_code": "use an ISO 3166-1 alpha-2 code such as PL or UA",
    "currency": "use an ISO 4217 code such as EUR",
    "iso_date": "use YYYY-MM-DD",
    "email": "use a single address, without display name or angle brackets",
    "phone": "use digits with an optional leading + and separators",
    "url": "use an http(s) URL",
    "language": "use a BCP-47 tag such as uk or en-GB",
    "enum": "use one of the allowed values",
}

_PATTERNS: dict[FormatKind, re.Pattern[str]] = {
    "country_code": re.compile(r"^[A-Z]{2}$"),
    "currency": re.compile(r"^[A-Z]{3}$"),
    "iso_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
    "phone": re.compile(r"^\+?[0-9][0-9 ()\-]{5,19}$"),
    "url": re.compile(r"^https?://[^\s]+$"),
    "language": re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$"),
}


@dataclass(frozen=True, slots=True)
class FormatRule:
    """``path`` must look like ``kind``; ``allowed`` turns the rule into a whitelist."""

    path: str
    kind: FormatKind
    allowed: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.kind == "enum" and self.allowed is None:
            raise ValueError("kind='enum' requires an explicit allowed set")

    def is_valid(self, text: str) -> bool:
        if self.allowed is not None:
            return text in self.allowed
        if self.kind == "enum":
            return False  # unreachable: __post_init__ rejects it
        pattern = _PATTERNS[self.kind]
        if not pattern.match(text):
            return False
        if self.kind == "iso_date":
            try:
                date.fromisoformat(text)
            except ValueError:
                return False
        return True


@dataclass(frozen=True, slots=True)
class FormatsCheck:
    """Shape checks over declared paths."""

    rules: Sequence[FormatRule]
    severity: Severity = "error"
    id: str = "formats"
    tier: Tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        issues: list[Issue] = []
        for rule in self.rules:
            value = get_path(output, rule.path)
            if value is MISSING or value is None:
                continue  # absence is the required check's business
            text = value if isinstance(value, str) else str(value)
            if rule.is_valid(text):
                continue
            issues.append(
                Issue(
                    code=FORMAT_CODES[rule.kind],
                    severity=self.severity,
                    source="check",
                    field_path=rule.path,
                    message=f"{text!r} is not a valid {rule.kind}",
                    hint=HINTS[rule.kind],
                    details={"value": text, "kind": rule.kind},
                )
            )
        return issues
