"""The public surface stays importable and versioned."""

from __future__ import annotations

import qagent


def test_version_is_set() -> None:
    assert qagent.__version__ == "0.1.0"


def test_every_public_name_exists() -> None:
    missing = [name for name in qagent.__all__ if not hasattr(qagent, name)]
    assert missing == []


def test_contracts_are_importable_from_the_package_root() -> None:
    from qagent import Case, Issue, JudgeVerdict, Rubric  # noqa: F401


def test_the_facade_is_importable_from_the_package_root() -> None:
    from qagent import GuardedAgent, Preset, guard, render_feedback  # noqa: F401

    assert callable(guard)
