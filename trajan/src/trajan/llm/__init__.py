"""Model clients behind one protocol.

The base types, the fake client and the cassette client import nothing beyond Pydantic, so
the core stays a one-dependency install. Provider clients need ``httpx`` and
are therefore resolved lazily: importing this package must not require the extra.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .base import FinishReason, LLMClient, LLMRequest, LLMResponse, Message, Role
from .command import CommandClient
from .errors import (
    CassetteMiss,
    JudgeError,
    LLMAuthError,
    LLMBadResponse,
    LLMError,
    LLMRateLimited,
    LLMTimeout,
)
from .fake import FakeClient, Scripted
from .pricing import usage_of
from .replay import CassetteMode, ReplayClient, request_key

if TYPE_CHECKING:  # pragma: no cover — typing only, resolved on demand at runtime
    from .anthropic import AnthropicClient
    from .openai_compatible import OpenAICompatibleClient

_LAZY: dict[str, tuple[str, str]] = {
    "AnthropicClient": ("anthropic", "AnthropicClient"),
    "OpenAICompatibleClient": ("openai_compatible", "OpenAICompatibleClient"),
}

_INSTALL_HINTS = {"AnthropicClient": "anthropic", "OpenAICompatibleClient": "openai"}


def __getattr__(name: str) -> Any:
    """Import provider clients on first use, with an actionable error without ``httpx``."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    try:
        module = import_module(f"{__name__}.{module_name}")
    except ImportError as exc:  # pragma: no cover — depends on the install
        hint = _INSTALL_HINTS[name]
        raise ImportError(
            f"{name} needs httpx: pip install 'httpx>=0.27' "
            f"(or pip install -e '.[{hint}]' in a checkout)"
        ) from exc
    return getattr(module, attribute)


__all__ = [
    "AnthropicClient",
    "CassetteMiss",
    "CassetteMode",
    "CommandClient",
    "FakeClient",
    "FinishReason",
    "JudgeError",
    "LLMAuthError",
    "LLMBadResponse",
    "LLMClient",
    "LLMError",
    "LLMRateLimited",
    "LLMRequest",
    "LLMResponse",
    "LLMTimeout",
    "Message",
    "OpenAICompatibleClient",
    "ReplayClient",
    "Role",
    "Scripted",
    "request_key",
    "usage_of",
]
