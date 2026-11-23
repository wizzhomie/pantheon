"""Minimal HTTP POST with bounded retries, shared by the provider clients.

Scope is deliberately narrow: this handles *transport* trouble (timeouts, throttling,
5xx), never semantic retries. Re-asking a model because its answer failed a check is the
QA loop's job, and mixing the two makes cost accounting impossible to reason about.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .errors import LLMAuthError, LLMError, LLMRateLimited, LLMTimeout

RETRY_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
MAX_BACKOFF_S = 30.0
SNIPPET_CHARS = 200


async def post_json(
    http: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
    max_retries: int,
    backoff_s: float,
) -> dict[str, Any]:
    """POST ``payload`` and return the decoded JSON object, or raise :class:`LLMError`."""
    delay = backoff_s
    for attempt in range(max_retries + 1):
        try:
            response = await http.post(url, headers=headers, json=payload, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            if attempt >= max_retries:
                raise LLMTimeout(f"provider timed out after {timeout_s:g}s") from exc
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_S)
            continue
        except httpx.HTTPError as exc:
            raise LLMError(f"transport error: {type(exc).__name__}: {exc}") from exc

        if response.status_code in RETRY_STATUS and attempt < max_retries:
            await asyncio.sleep(_retry_after(response) or min(delay, MAX_BACKOFF_S))
            delay = min(delay * 2, MAX_BACKOFF_S)
            continue
        return _as_payload(response)
    raise LLMError("retries exhausted")  # pragma: no cover — loop always returns or raises


def _as_payload(response: httpx.Response) -> dict[str, Any]:
    status = response.status_code
    if status in (401, 403):
        raise LLMAuthError(f"provider rejected the credentials (HTTP {status})")
    if status == 429:
        raise LLMRateLimited("provider is rate limiting", retry_after_s=_retry_after(response))
    if status >= 500:
        raise LLMError(f"provider error HTTP {status}: {_snippet(response)}")
    if status >= 400:
        raise LLMError(f"request rejected, HTTP {status}: {_snippet(response)}")
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError("provider returned a non-JSON body") from exc
    if not isinstance(data, dict):
        raise LLMError("provider returned a JSON body that is not an object")
    return data


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(tz=UTC)).total_seconds())


def _snippet(response: httpx.Response) -> str:
    """Short, single-line body excerpt: enough to debug, small enough to log."""
    try:
        text = response.text
    except Exception:  # pragma: no cover — httpx can fail decoding binary bodies
        return ""
    return " ".join(text[:SNIPPET_CHARS].split())
