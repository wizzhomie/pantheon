"""The regression dataset: JSONL on disk, cases plus a content hash in memory.

The hash is the point. A report is comparable only when the cases behind it are identical,
and the most common silent mistake in evaluation is comparing numbers produced by two
different datasets. ``digest()`` deliberately ignores ``notes`` and ``human_verdict``: they
document and calibrate, they do not change what the run measures.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import Field, field_validator

from ..contracts.base import StrictModel
from ..contracts.evals import EvalCase

HASH_VERSION = "dataset-v1"


class Dataset(StrictModel):
    """An ordered, non-empty set of labelled cases."""

    name: str = Field(default="", max_length=80)
    cases: list[EvalCase] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def _unique_ids(cls, cases: list[EvalCase]) -> list[EvalCase]:
        ids = [case.case_id for case in cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate case ids in the dataset: {duplicates}")
        return cases

    def digest(self) -> str:
        """Stable hash of everything that changes what a run measures."""
        payload = {
            "version": HASH_VERSION,
            "cases": [
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "tags": sorted(case.tags),
                    "payload": case.payload,
                    "expected": case.expected,
                    "traps": [trap.model_dump(mode="json") for trap in case.traps],
                }
                for case in sorted(self.cases, key=lambda item: item.case_id)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_jsonl(self) -> str:
        """Serialise one case per line, omitting empty optional fields."""
        lines = [case.model_dump_json(exclude_none=True) for case in self.cases]
        return "\n".join(lines) + "\n"

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_jsonl(), encoding="utf-8")
        return target

    @classmethod
    def from_jsonl(cls, text: str, *, name: str = "", source: str = "<text>") -> Dataset:
        cases: list[EvalCase] = []
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                cases.append(EvalCase.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(f"{source}:{lineno}: invalid case: {exc}") from exc
        if not cases:
            raise ValueError(f"{source}: the dataset is empty")
        return cls(name=name, cases=cases)

    @classmethod
    def load(cls, path: str | Path) -> Dataset:
        target = Path(path)
        return cls.from_jsonl(
            target.read_text(encoding="utf-8"), name=target.stem, source=str(target)
        )
