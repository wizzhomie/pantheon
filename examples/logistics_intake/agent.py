"""The agent the preset is meant for: one model call, feedback appended on a retry.

Nothing here knows about QA. It builds messages, calls a client and returns the raw text
plus what the call cost; the loop owns parsing, checking and deciding. That separation is
what lets the same agent run in ``baseline`` mode (no QA at all) and in ``guarded`` mode
without a branch.
"""

from __future__ import annotations

import json

from agentqa import (
    AgentFn,
    AgentReply,
    Case,
    Feedback,
    LLMClient,
    LLMRequest,
    Message,
    Pricing,
    render_feedback,
    usage_of,
)

from .schema import ExtractedIntake

MAX_OUTPUT_TOKENS = 2_000


def make_llm_agent(
    *,
    llm: LLMClient,
    model: str,
    prompt: str,
    pricing: Pricing,
) -> AgentFn:
    """Build ``async (case, feedback) -> AgentReply`` around one chat client.

    The JSON schema goes into the system message *and* into ``response_schema``: providers
    that support structured output get it enforced, providers that ignore the field still
    see the shape. Both paths end in the same Pydantic validation inside the loop.
    """
    schema = ExtractedIntake.model_json_schema()
    system = f"{prompt}\n\nJSON schema:\n{json.dumps(schema, ensure_ascii=False, sort_keys=True)}"

    async def extract(case: Case, feedback: Feedback | None) -> AgentReply:
        messages = [
            Message(role="system", content=system),
            Message(role="user", content=_payload_text(case)),
        ]
        if feedback is not None:
            messages.append(Message(role="user", content=render_feedback(feedback)))
        response = await llm.complete(
            LLMRequest(
                model=model,
                messages=messages,
                temperature=0.0,
                max_tokens=MAX_OUTPUT_TOKENS,
                response_schema=schema,
            )
        )
        return AgentReply(
            attempt=response.text,
            usages=(usage_of(response, pricing),),
            truncated=response.truncated,
        )

    return extract


def _payload_text(case: Case) -> str:
    """The email as text; the loop already rejected an empty payload before this runs."""
    if isinstance(case.payload, str):
        return case.payload
    return json.dumps(case.payload, ensure_ascii=False, sort_keys=True)
