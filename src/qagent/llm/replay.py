"""Record real answers once, replay them forever.

A cassette is a JSONL file keyed by a hash of the *whole* request (model, messages,
temperature, token budget and schema). That choice is the point: edit a prompt and the key
changes, so a stale cassette fails loudly instead of quietly feeding old answers into a
new experiment.

Modes:

* ``record`` — always call the inner client and append what came back;
* ``replay`` — never call the network, raise :class:`CassetteMiss` on a miss;
* ``auto`` — use the cassette when it has the key, otherwise call the inner client and
  record the result.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from .base import LLMClient, LLMRequest, LLMResponse
from .errors import CassetteMiss

CassetteMode = Literal["record", "replay", "auto"]


def request_key(request: LLMRequest) -> str:
    """Stable sha256 over the canonical request, used as the cassette key."""
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ReplayClient:
    """A client that wraps another one and persists its answers."""

    name = "replay"

    def __init__(
        self,
        path: str | Path,
        *,
        inner: LLMClient | None = None,
        mode: CassetteMode = "replay",
    ) -> None:
        self.path = Path(path)
        self.mode = mode
        self.inner = inner
        if mode in ("record", "auto") and inner is None:
            raise ValueError(f"mode={mode!r} needs an inner client to call")
        self.entries: dict[str, LLMResponse] = self._load()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self.entries)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        key = request_key(request)
        known = self.entries.get(key)
        if known is not None and self.mode != "record":
            self.hits += 1
            return known.model_copy(update={"cached": True})
        if self.mode == "replay" or self.inner is None:
            self.misses += 1
            raise CassetteMiss(
                f"no cassette entry for this request (key {key[:12]}…); "
                f"re-record with mode='record' after changing prompts",
                key=key,
                path=str(self.path),
            )
        response = await self.inner.complete(request)
        self.entries[key] = response
        self._append(key, response)
        return response

    def _load(self) -> dict[str, LLMResponse]:
        if not self.path.exists():
            return {}
        entries: dict[str, LLMResponse] = {}
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                entries[str(record["key"])] = LLMResponse.model_validate(record["response"])
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"{self.path}:{number}: malformed cassette line") from exc
        return entries

    def _append(self, key: str, response: LLMResponse) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "key": key,
            "response": response.model_dump(mode="json"),
        }
        line = json.dumps(record, sort_keys=True, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
