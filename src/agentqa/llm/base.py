"""The model boundary: one request type in, one response type out.

Deliberately tiny. The core needs exactly three facts from a provider — the text, why it
stopped, and how much it cost — because everything else (retries, feedback, escalation)
is semantic and belongs to the QA loop, not to the transport.
"""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import Field

from ..contracts.base import JsonValue, StrictModel

Role = Literal["system", "user", "assistant"]
FinishReason = Literal["stop", "length", "content_filter", "tool_calls", "unknown"]


class Message(StrictModel):
    """One turn of the conversation sent to the model."""

    role: Role
    content: str = Field(min_length=1)


class LLMRequest(StrictModel):
    """Everything a client needs, in one immutable object.

    Immutability matters for replay: the cassette key is a hash of this object, so a
    changed prompt produces a different key instead of silently reusing an old answer.
    """

    model: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=1500, ge=1, le=100_000)
    response_schema: dict[str, JsonValue] | None = Field(
        default=None, description="JSON Schema for structured output, when the provider supports it"
    )
    timeout_s: Annotated[float, Field(gt=0, le=600)] = 60.0


class LLMResponse(StrictModel):
    """What came back, normalised across providers."""

    text: str
    model: str
    finish_reason: FinishReason = "stop"
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    request_id: str | None = None
    cached: bool = Field(default=False, description="served from a cassette, not the provider")

    @property
    def truncated(self) -> bool:
        """True when the provider stopped at the token limit, not because it was done."""
        return self.finish_reason == "length"


class LLMClient(Protocol):
    """Structural type for anything that can answer an :class:`LLMRequest`."""

    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
