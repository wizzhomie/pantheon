"""Turn a provider response into usage, with money attached.

Pricing lives outside the clients on purpose: a client should not need a price table to do
its job, and tests should not need real prices to check token accounting.
"""

from __future__ import annotations

from ..config import Pricing
from ..contracts.trace import Usage
from .base import LLMResponse


def usage_of(response: LLMResponse, pricing: Pricing) -> Usage:
    """Build a :class:`Usage` for one call, flagging unknown models instead of guessing."""
    cost, price_known = pricing.estimate(
        response.model, response.prompt_tokens, response.completion_tokens
    )
    return Usage(
        model=response.model,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        cost_usd=cost,
        latency_ms=response.latency_ms,
        cached=response.cached,
        price_known=price_known,
    )
