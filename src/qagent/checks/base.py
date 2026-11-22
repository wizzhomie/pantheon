"""Check protocol and the context a check is allowed to see.

A check is a pure function of ``(output, context)``: no network, no global state, and
the clock is injected. That is what makes a check testable with a table of
"output → expected codes" and keeps a gate reproducible in a bug report.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel

from ..config import DEFAULT_CONFIG, QAConfig
from ..contracts.base import StrictModel
from ..contracts.case import Case
from ..contracts.issues import Issue

Tier = Literal["fast", "deep"]
"""``fast`` checks run in microseconds and never touch the network; ``deep`` may."""


class CheckContext(StrictModel):
    """Everything a check may look at besides the output itself."""

    case: Case
    now: datetime
    config: QAConfig = DEFAULT_CONFIG

    @property
    def raw_text(self) -> str:
        """Searchable projection of the input, as seen by evidence checks."""
        return self.case.as_text()


class Check(Protocol):
    """Structural type every check satisfies.

    Deliberately not a base class: a check can be a dataclass, a closure or a class with
    ``__call__``, as long as it exposes an id, a tier and the call signature. ``id`` and
    ``tier`` are read-only here because every built-in check is a frozen dataclass, and a
    protocol that demanded a setter would reject exactly the checks the library ships.
    """

    @property
    def id(self) -> str: ...

    @property
    def tier(self) -> Tier: ...

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]: ...
