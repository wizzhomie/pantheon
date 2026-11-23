"""The agent: one model call, feedback appended on a retry.

Same shape as the logistics example on purpose — this file is the whole integration
surface, and switching the task does not change it. What changes is the prompt and the
schema, both of which live next to this file.
"""

from __future__ import annotations

import json

from trajan import (
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

from .schema import Review

# A short review does not need a long answer, but a reasoning model spends this budget on
# hidden thinking before it writes anything. The example switches the thinking off at the
# provider seam; the cap stays generous so removing that line does not truncate the JSON.
MAX_OUTPUT_TOKENS = 8_000


def make_llm_agent(
    *,
    llm: LLMClient,
    model: str,
    prompt: str,
    pricing: Pricing,
) -> AgentFn:
    """Build ``async (case, feedback) -> AgentReply`` around one chat client."""
    schema = Review.model_json_schema()
    system = f"{prompt}\n\nJSON schema:\n{json.dumps(schema, ensure_ascii=False, sort_keys=True)}"

    async def review(case: Case, feedback: Feedback | None) -> AgentReply:
        messages = [
            Message(role="system", content=system),
            Message(role="user", content=_code_block(case)),
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

    return review


def _code_block(case: Case) -> str:
    """The snippet arrives as text; anything else is serialised deterministically."""
    source = (
        case.payload
        if isinstance(case.payload, str)
        else json.dumps(case.payload, ensure_ascii=False, sort_keys=True)
    )
    return f"Review this Python snippet:\n\n```python\n{source.rstrip()}\n```"
