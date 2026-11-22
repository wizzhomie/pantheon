"""The preset: everything domain-specific, in one object the core can read.

A preset is a plain frozen container, not a Pydantic model, because two of its fields are
deliberately unvalidatable: a Pydantic *class* and callables (a retry hook, an input
validator). Validating the outer shell while accepting those as opaque would be
half a contract; here the constructor validates what it can and says so.

The core never imports a preset by name: it receives one through :func:`agentqa.guard`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

from pydantic import BaseModel

from ..checks.base import Check
from ..contracts.agent import Feedback
from ..contracts.case import Case
from ..contracts.evals import ScoringSpec
from ..contracts.issues import Issue
from ..contracts.rubric import Rubric
from ..judge.base import Judge

TOut = TypeVar("TOut", bound=BaseModel)
"""The preset's output type; it flows through :class:`agentqa.RunResult`."""

RetryBuilder: TypeAlias = Callable[[Case, Feedback], Feedback]
"""Domain hook: adjust the feedback before the next attempt (filter issues, add rules)."""

InputValidator: TypeAlias = Callable[[Case], list[Issue]]
"""Domain hook: reject inputs the agent must never see (empty scan, wrong channel)."""

HASH_VERSION = "preset-v1"


@dataclass(frozen=True, slots=True)
class Preset(Generic[TOut]):
    """A cartridge: output schema, checks, rubric, prompts and scoring rules.

    ``checks`` contribute to :meth:`fingerprint` only by id and tier: the protocol does
    not expose a check's configuration, so a preset whose custom check changes behaviour
    without changing its id must override :meth:`fingerprint` to keep reports honest.
    """

    name: str
    output_model: type[TOut]
    rubric: Rubric
    checks: Sequence[Check] = ()
    judge: Judge | None = None
    scoring: ScoringSpec | None = None
    agent_prompt: str = ""
    judge_instructions: str = ""
    agent_model: str | None = None
    build_retry: RetryBuilder | None = None
    validate_input: InputValidator | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a preset needs a non-empty name")
        if not (isinstance(self.output_model, type) and issubclass(self.output_model, BaseModel)):
            raise TypeError("preset.output_model must be a Pydantic BaseModel subclass")
        object.__setattr__(self, "checks", tuple(self.checks))
        ids = [check.id for check in self.checks]
        if duplicate := sorted({check_id for check_id in ids if ids.count(check_id) > 1}):
            raise ValueError(f"duplicate check ids in preset {self.name!r}: {duplicate}")

    @property
    def output_schema(self) -> dict[str, object]:
        """JSON Schema of the output model, used by prompts and by the fingerprint."""
        return self.output_model.model_json_schema()

    def check_ids(self) -> list[str]:
        """Check ids in declaration order: the ones reports and traces refer to."""
        return [check.id for check in self.checks]

    def fingerprint(self) -> str:
        """Stable hash of everything that changes behaviour, for run comparisons.

        Two reports are comparable only when this hash matches; without it, a change in
        the rubric or the prompt looks like model noise.
        """
        payload = {
            "version": HASH_VERSION,
            "name": self.name,
            "agent_model": self.agent_model,
            "output_model": f"{self.output_model.__module__}.{self.output_model.__qualname__}",
            "schema": self.output_schema,
            "checks": [[check.id, check.tier] for check in self.checks],
            "rubric": self.rubric.model_dump(mode="json"),
            "judge_instructions": self.judge_instructions,
            "agent_prompt": self.agent_prompt,
            "scoring": self.scoring.model_dump(mode="json") if self.scoring else None,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
