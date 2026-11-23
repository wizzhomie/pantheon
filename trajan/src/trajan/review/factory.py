"""Build a live reviewer client from the environment.

The environment carries the three facts a library cannot know: which model, which endpoint,
and which variable holds the key. Defaults point at DeepSeek because that is what this
project is developed against; any OpenAI-compatible endpoint works the same way.

This module exists so a live run is an explicit choice (a factory name on the command line),
never a default that quietly costs money.
"""

from __future__ import annotations

import os

from ..llm.base import LLMClient
from ..llm.openai_compatible import OpenAICompatibleClient

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_KEY_ENV = "DEEPSEEK_API_KEY"


def from_env() -> LLMClient:
    """``trajan verify --reviewer trajan.review.factory:from_env``."""
    return OpenAICompatibleClient(
        model=os.environ.get("TRAJAN_REVIEW_MODEL", DEFAULT_MODEL),
        base_url=os.environ.get("TRAJAN_REVIEW_BASE_URL", DEFAULT_BASE_URL),
        api_key_env=os.environ.get("TRAJAN_REVIEW_KEY_ENV", DEFAULT_KEY_ENV),
        # DeepSeek rejects json_schema but honours json_object; the schema is in the prompt.
        structured_output="json_object",
        # No reasoning mode: the owner's rule is a review, not a meditation.
        extra_body={"reasoning_effort": "none"},
        timeout_s=120.0,
    )


def model_name() -> str:
    """What the factory will ask for; used by the CLI to plan before a call is made."""
    return os.environ.get("TRAJAN_REVIEW_MODEL", DEFAULT_MODEL)
