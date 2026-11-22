"""The public surface stays importable and versioned."""

from __future__ import annotations

import agentqa


def test_version_is_set() -> None:
    assert agentqa.__version__ == "0.1.0"


def test_every_public_name_exists() -> None:
    missing = [name for name in agentqa.__all__ if not hasattr(agentqa, name)]
    assert missing == []


def test_contracts_are_importable_from_the_package_root() -> None:
    from agentqa import Case, Issue, JudgeVerdict, Rubric  # noqa: F401
