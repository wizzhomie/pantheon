"""Duplicate list items — the classic double-counted cargo line."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from ..contracts.issues import Issue, Severity
from .base import CheckContext, Tier
from .paths import MISSING, get_path


@dataclass(frozen=True, slots=True)
class DuplicatesCheck:
    """Report items in a list that share the same identity."""

    path: str
    keys: Sequence[str] = ()
    """Fields composing the identity; empty means "the whole item"."""
    severity: Severity = "error"
    id: str = "duplicates"
    tier: Tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        value = get_path(output, self.path)
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return []
        seen: dict[str, int] = {}
        issues: list[Issue] = []
        for index, item in enumerate(value):
            identity = self._identity(item)
            if identity is None:
                continue
            first = seen.get(identity)
            if first is None:
                seen[identity] = index
                continue
            issues.append(
                Issue(
                    code="G7_DUPLICATE_ITEMS",
                    severity=self.severity,
                    source="check",
                    field_path=f"{self.path}.{index}",
                    message=f"item {index} in {self.path} duplicates item {first}",
                    hint="merge the duplicates or correct the quantity",
                    details={"first_index": first, "duplicate_index": index},
                )
            )
        return issues

    def _identity(self, item: object) -> str | None:
        if self.keys:
            parts: list[str] = []
            for key in self.keys:
                value = get_path(item, key)
                if value is MISSING:
                    return None
                parts.append(str(value).casefold().strip())
            return "|".join(parts)
        payload = item.model_dump(mode="json") if isinstance(item, BaseModel) else item
        try:
            dumped = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        except TypeError:
            return None
        return dumped.casefold()
