"""A scripted reviewer, for tests and for demos that must not spend money.

``QAGENT_REVIEW_RESPONSE`` holds one answer the model would have given; the client returns
it once. This exists so the whole review path — prompt, parsing, anchoring, verdict, report
— can be exercised end to end without a provider, which is also how the tests stay free.
"""

from __future__ import annotations

import os

from ..llm.base import LLMClient
from ..llm.fake import FakeClient


def from_env() -> LLMClient:
    """The factory form ``qagent verify --reviewer qagent.review.scripted:from_env``."""
    text = os.environ.get("QAGENT_REVIEW_RESPONSE")
    if not text:
        raise ValueError(
            "QAGENT_REVIEW_RESPONSE is not set; the scripted reviewer needs one answer to give"
        )
    return FakeClient([text])
