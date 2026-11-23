"""A review already paid for, served again for exactly the same question.

The key is the question itself — the rendered system and user messages, the model, the
temperature and the token ceiling — so editing an instruction, adding a requirement or
changing the diff produces a different key and a fresh call. Nothing about the *answer* is
in the key: two runs that ask the same thing may legitimately get different reviews, and
serving the first one again is a caching decision, not a claim that the model is stable.

That is why a cached outcome says so, and keeps the numbers of the call that actually
happened: the report may show "8 461 tokens" for a run that spent nothing, as long as it
also says the tokens were paid once, on the original review.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..llm.base import LLMRequest
from .contracts import ReviewOutcome

CACHE_VERSION = 1
"""Bumped when the stored shape changes; a miss is cheaper than a wrong hit."""

CACHE_DIR = Path(".qagent") / "cache" / "reviews"

MAX_ENTRIES = 50
"""How many reviews are kept; the oldest are dropped, because the cache is not evidence."""


class ReviewCache:
    """Reviews stored next to the project they belong to, keyed by the request."""

    def __init__(self, root: Path, *, limit: int = MAX_ENTRIES) -> None:
        self.directory = Path(root) / CACHE_DIR
        self.limit = limit

    @staticmethod
    def key(request: LLMRequest, *, extra: str = "") -> str:
        """A digest of everything that decides what the model is asked and how.

        ``extra`` is for a mode the messages do not show — the second look asks a second
        question, so its answers must not be served from a cache of first answers.
        """
        payload = {
            "version": CACHE_VERSION,
            "extra": extra,
            "model": request.model,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "messages": [[message.role, message.content] for message in request.messages],
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def load(self, key: str) -> ReviewOutcome | None:
        """The stored review, marked as cached; ``None`` when there is nothing to serve."""
        path = self.directory / f"{key}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        if data.get("version") != CACHE_VERSION or data.get("key") != key:
            return None
        try:
            outcome = ReviewOutcome.model_validate(data["outcome"])
        except (KeyError, ValueError):
            return None
        if outcome.status != "ok":
            return None
        return outcome.model_copy(update={"cached": True})

    def store(self, key: str, outcome: ReviewOutcome) -> None:
        """Keep a successful review; a failure is a fact about one moment, not a result."""
        if outcome.status != "ok":
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "key": key,
            "outcome": outcome.model_dump(mode="json"),
        }
        (self.directory / f"{key}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self._prune()

    def _prune(self) -> None:
        entries = sorted(
            self.directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
        )
        for stale in entries[self.limit :]:
            stale.unlink(missing_ok=True)
