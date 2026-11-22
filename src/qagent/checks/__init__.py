"""Deterministic checks: the free, fast half of embedded QA.

Every check is a pure callable ``(output, context) -> list[Issue]``. Import them from here
and compose a preset; there is no registry and no name-based magic, because an explicit
list is what a reader needs when asking "what is actually checked here?".
"""

from .base import Check, CheckContext, Tier
from .consistency import ConsistencyCheck, Rule
from .duplicates import DuplicatesCheck
from .evidence import EvidenceCheck, QuoteProvider, coverage, quotes_from_attribute
from .formats import FORMAT_CODES, FormatKind, FormatRule, FormatsCheck
from .pii import PiiCheck, PiiKind
from .ranges import DateRule, NumberRange, RangesCheck
from .refusal import DEFAULT_MARKERS, RefusalCheck
from .required import RequiredCheck, RequiredRule, read_unresolved
from .runner import run_checks

__all__ = [
    "DEFAULT_MARKERS",
    "FORMAT_CODES",
    "Check",
    "CheckContext",
    "ConsistencyCheck",
    "DateRule",
    "DuplicatesCheck",
    "EvidenceCheck",
    "FormatKind",
    "FormatRule",
    "FormatsCheck",
    "NumberRange",
    "PiiCheck",
    "PiiKind",
    "QuoteProvider",
    "RangesCheck",
    "RefusalCheck",
    "RequiredCheck",
    "RequiredRule",
    "Rule",
    "Tier",
    "coverage",
    "quotes_from_attribute",
    "read_unresolved",
    "run_checks",
]
