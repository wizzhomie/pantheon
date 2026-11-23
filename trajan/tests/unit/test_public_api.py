"""The public surface stays importable and versioned."""

from __future__ import annotations

import tomllib
from pathlib import Path

import trajan

ROOT = Path(__file__).resolve().parents[2]


def test_version_is_set() -> None:
    assert trajan.__version__ == "0.1.0"


def test_the_distribution_name_is_not_the_import_name() -> None:
    """`pip install pantheon-trajan`, but `import trajan`: the split is deliberate (ADR-010)."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "pantheon-trajan"
    assert pyproject["project"]["urls"]["Homepage"].endswith("/trajan")
    assert (ROOT / "src" / "trajan" / "__init__.py").is_file()


def test_every_public_name_exists() -> None:
    missing = [name for name in trajan.__all__ if not hasattr(trajan, name)]
    assert missing == []


def test_contracts_are_importable_from_the_package_root() -> None:
    from trajan import Case, Issue, JudgeVerdict, Rubric  # noqa: F401


def test_the_facade_is_importable_from_the_package_root() -> None:
    from trajan import GuardedAgent, Preset, guard, render_feedback  # noqa: F401

    assert callable(guard)
