"""PII leaking into free-text fields.

The pattern is directory-vs-description: an email inside ``contact.email`` is the point of
the field, the same email copied into a free-text ``notes`` is a leak that ends up in
logs, tickets and analytics. Defaults therefore scan every string leaf except explicitly
allowed paths.

The matched value is deliberately **not** put into the issue: an audit log that records a
card number is itself the incident.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from ..contracts.issues import Issue, Severity
from .base import CheckContext, Tier
from .paths import get_path, iter_leaves

PiiKind = Literal["email", "phone", "card", "iban"]

DEFAULT_SEVERITY: dict[PiiKind, Severity] = {
    "email": "warning",
    "phone": "warning",
    "card": "critical",
    "iban": "critical",
}

_PATTERNS: dict[PiiKind, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"\+?\d[\d ()\-]{6,18}\d"),
    "card": re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
}

_STRIP_NON_DIGITS = re.compile(r"\D")


def luhn_ok(digits: str) -> bool:
    """Checksum used to tell a card number from a long numeric id."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


@dataclass(frozen=True, slots=True)
class PiiCheck:
    """Detect personal data where the preset says it does not belong."""

    paths: Sequence[str] = ()
    """Paths to scan; empty means every string leaf."""
    kinds: frozenset[PiiKind] = frozenset({"email", "phone", "card", "iban"})
    allow_paths: Sequence[str] = ()
    """Paths where the data is expected, e.g. ``contact.email``."""
    id: str = "pii"
    tier: Tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        allowed = set(self.allow_paths)
        targets = (
            list(self.paths)
            if self.paths
            else [path for path, value in iter_leaves(output) if isinstance(value, str)]
        )
        issues: list[Issue] = []
        for path in targets:
            if path in allowed:
                continue
            value = get_path(output, path)
            if not isinstance(value, str) or not value.strip():
                continue
            issues.extend(self._scan(path, value))
        return issues

    def _scan(self, path: str, text: str) -> list[Issue]:
        found: list[Issue] = []
        for kind in self.kinds:
            pattern = _PATTERNS[kind]
            for match in pattern.finditer(text):
                candidate = match.group()
                if kind == "card" and not luhn_ok(_STRIP_NON_DIGITS.sub("", candidate)):
                    continue
                found.append(
                    Issue(
                        code="G5_PII_LEAK",
                        severity=DEFAULT_SEVERITY[kind],
                        source="check",
                        field_path=path,
                        message=f"a {kind} value appears in the free-text field {path}",
                        hint="keep personal data in its own structured field or redact it",
                        details={"kind": kind, "path": path},
                    )
                )
                break  # one report per kind and path is enough signal
        return found
