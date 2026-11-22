"""Dotted-path access over Pydantic models, mappings and lists.

Presets describe fields as paths (``shipment.origin.country_code``, ``cargo.0.weight``)
because paths survive schema changes better than positional lookups, and a path is what
a human reads in a report.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Final

from pydantic import BaseModel

MISSING: Final[object] = object()
"""Sentinel returned by :func:`get_path` when a path does not resolve.

Distinct from ``None`` on purpose: an absent field and a field deliberately set to null
require different messages, and conflating them produces confusing reports.
"""


def get_path(value: object, path: str) -> object:
    """Resolve ``a.b.0.c`` against models, dicts and sequences; ``MISSING`` if absent."""
    current: object = value
    for part in path.split("."):
        if current is None or current is MISSING:
            return MISSING
        if isinstance(current, BaseModel):
            if part not in type(current).model_fields:
                return MISSING
            current = getattr(current, part)
        elif isinstance(current, Mapping):
            mapping: Mapping[str, Any] = current
            if part not in mapping:
                return MISSING
            current = mapping[part]
        elif isinstance(current, (list, tuple)):
            if not part.isdigit():
                return MISSING
            index = int(part)
            if index >= len(current):
                return MISSING
            current = current[index]
        else:
            return MISSING
    return current


def iter_leaves(value: object, prefix: str = "") -> Iterator[tuple[str, object]]:
    """Yield ``(path, value)`` for every scalar leaf, in a stable order."""
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            child = prefix + name if not prefix else f"{prefix}.{name}"
            yield from iter_leaves(getattr(value, name), child)
    elif isinstance(value, Mapping):
        mapping: Mapping[str, Any] = value
        for key in sorted(mapping, key=str):
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from iter_leaves(mapping[key], child)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from iter_leaves(item, f"{prefix}.{index}" if prefix else str(index))
    else:
        yield prefix, value


def is_present(value: object) -> bool:
    """Whether a resolved path counts as filled.

    Empty containers and blank strings are absent: an empty list of cargo items answers
    "did you extract the cargo?" with no.
    """
    if value is MISSING or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, Mapping)):
        return len(value) > 0
    return True
