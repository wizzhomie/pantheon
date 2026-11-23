"""The independent reviewer: a model proposes findings about a diff, code checks them.

This package is the phase-4 half of verification. It knows three things the deterministic
engine does not:

* how to read a change (:mod:`qagent.review.diff`) — only changed lines, with the new file's
  line numbers, under a size ceiling;
* how to ask a model about it (:mod:`qagent.review.prompt`, :mod:`qagent.review.reviewer`);
* how to refuse an answer that the repository does not confirm (:func:`anchor`).

The model's answer is a proposal, never a result. A finding becomes ``confirmed`` only when
the file is part of the change, the line exists and the quote is literally that line. A
proposal that fails is kept as ``unverified``: visible, non-blocking, and honest about the
fact that nothing in the repository agreed with it.
"""

from __future__ import annotations

from .contracts import (
    ProposedFinding,
    ProposedKind,
    ProposedReview,
    ProposedSeverity,
    ReviewerConfig,
    ReviewOutcome,
    ReviewStatus,
)
from .diff import Diff, DiffLine, FileChange, FileStatus, parse_diff
from .prompt import build_user_message, system_message
from .reviewer import anchor, review_diff

__all__ = [
    "Diff",
    "DiffLine",
    "FileChange",
    "FileStatus",
    "ProposedFinding",
    "ProposedKind",
    "ProposedReview",
    "ProposedSeverity",
    "ReviewOutcome",
    "ReviewStatus",
    "ReviewerConfig",
    "anchor",
    "build_user_message",
    "parse_diff",
    "review_diff",
    "system_message",
]
