"""The public surface stays importable and versioned."""

from __future__ import annotations

import tomllib
from pathlib import Path

import qagent

ROOT = Path(__file__).resolve().parents[2]


def test_version_is_set() -> None:
    assert qagent.__version__ == "0.1.0"


def test_the_distribution_name_is_not_the_import_name() -> None:
    """`pip install qagent-qa`, but `import qagent`: the split is deliberate (ADR-010)."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "qagent-qa"
    assert pyproject["project"]["urls"]["Homepage"].endswith("/qagent")
    assert (ROOT / "src" / "qagent" / "__init__.py").is_file()


def test_every_public_name_exists() -> None:
    missing = [name for name in qagent.__all__ if not hasattr(qagent, name)]
    assert missing == []


def test_contracts_are_importable_from_the_package_root() -> None:
    from qagent import Case, Issue, JudgeVerdict, Rubric  # noqa: F401


def test_the_facade_is_importable_from_the_package_root() -> None:
    from qagent import GuardedAgent, Preset, guard, render_feedback  # noqa: F401

    assert callable(guard)
