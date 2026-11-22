"""Evidence: every claimed value must be traceable to the input.

This is the cheapest non-probabilistic anti-hallucination check available. The agent is
asked to attach a verbatim fragment for each field; the check verifies the fragment is
really there. Two properties matter:

* it costs zero tokens at check time and is fully explainable (a report can show the
  quote and its position);
* it needs no domain knowledge, which is why it can live in the core while staying
  optional: a preset enables it by supplying a quote provider.

Matching is forgiving on purpose — case, whitespace, ё/е, dash variants and OCR-level
noise are normalised away. For anything longer than a short fragment the fallback is
longest-common-substring coverage, not a substring test, so a truncated quote does not
fail the run.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from pydantic import BaseModel

from ..contracts.issues import Issue, Severity
from .base import CheckContext, Tier
from .paths import MISSING, get_path

QuoteProvider = Callable[[BaseModel], Mapping[str, str]]
"""Maps a field path to the verbatim quote the agent claims supports it."""

MAX_FUZZY_CHARS = 20_000
"""Above this, only exact matching runs: sequence matching is quadratic in the worst case."""

_DASHES = re.compile(r"[\u2010-\u2015\u2212]")
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lower-case, unify dashes and ё, collapse whitespace, drop wrapping quotes."""
    lowered = text.casefold().replace("ё", "е")
    dashed = _DASHES.sub("-", lowered)
    collapsed = _SPACES.sub(" ", dashed).strip()
    return collapsed.strip("\"'«»“”")


def coverage(quote: str, source: str) -> float:
    """How much of ``quote`` is found in ``source``: 1.0 = exact substring."""
    needle = normalise(quote)
    if not needle:
        return 0.0
    haystack = normalise(source)
    if needle in haystack:
        return 1.0
    if len(haystack) > MAX_FUZZY_CHARS:
        return 0.0
    matcher = SequenceMatcher(None, needle, haystack, autojunk=False)
    match = matcher.find_longest_match(0, len(needle), 0, len(haystack))
    return match.size / len(needle)


def quotes_from_attribute(attribute: str) -> QuoteProvider:
    """Provider for the common shape: ``citations: list[Evidence(field_path, quote)]``.

    Duck-typed on purpose: the preset may use a Pydantic model, a dataclass or plain
    dicts, as long as items expose ``field_path`` and ``quote``.
    """

    def provider(output: BaseModel) -> Mapping[str, str]:
        raw: object = getattr(output, attribute, None)
        if not isinstance(raw, (list, tuple)):
            return {}
        mapping: dict[str, str] = {}
        for item in raw:
            path = _attr(item, "field_path")
            quote = _attr(item, "quote")
            if isinstance(path, str) and isinstance(quote, str):
                mapping[path] = quote
        return mapping

    return provider


def _attr(item: object, name: str) -> object:
    if isinstance(item, BaseModel):
        return getattr(item, name, None)
    if isinstance(item, Mapping):
        mapping: Mapping[str, object] = item
        return mapping.get(name)
    return None


@dataclass(frozen=True, slots=True)
class EvidenceCheck:
    """Verify quotes against the case text."""

    quotes: QuoteProvider
    required_paths: Sequence[str] = ()
    min_similarity: float | None = None
    severity: Severity = "error"
    id: str = "evidence"
    tier: Tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        mapping = self.quotes(output)
        source = ctx.raw_text
        floor = (
            self.min_similarity
            if self.min_similarity is not None
            else ctx.config.evidence_min_similarity
        )
        issues: list[Issue] = [
            self._missing(path) for path in self.required_paths if not mapping.get(path, "").strip()
        ]
        for path, quote in mapping.items():
            if get_path(output, path) is MISSING:
                issues.append(
                    Issue(
                        code="G4_ORPHAN_EVIDENCE",
                        severity="warning",
                        source="check",
                        field_path=path,
                        message=f"evidence for {path} points at a field that does not exist",
                        hint="drop the citation or fill the field",
                    )
                )
                continue
            if not quote.strip():
                issues.append(self._missing(path))
                continue
            score = coverage(quote, source)
            if score < floor:
                issues.append(
                    Issue(
                        code="G4_FAKE_QUOTE",
                        severity=self.severity,
                        source="check",
                        field_path=path,
                        message=(
                            f"the quote for {path} is not found in the input "
                            f"(coverage {score:.0%})"
                        ),
                        hint="quote the exact fragment that supports this value, or drop the value",
                        details={"quote": quote, "coverage": round(score, 3)},
                    )
                )
        return issues

    def _missing(self, path: str) -> Issue:
        return Issue(
            code="G4_NO_EVIDENCE",
            severity=self.severity,
            source="check",
            field_path=path,
            message=f"{path} has no supporting quote",
            hint="attach the verbatim fragment this value came from",
        )
