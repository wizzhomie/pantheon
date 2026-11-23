"""A project as a file: ``trajan.toml`` builds the same preset the code builds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from examples.logistics_intake.preset import PRESET
from trajan.cli import main
from trajan.project import ProjectConfig, build_preset, build_pricing

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "logistics_intake" / "trajan.toml"

MINIMAL: dict[str, Any] = {
    "name": "toy",
    "dataset": "dataset.jsonl",
    "schema": "tests.unit.test_project_config:Out",
    "agent": "tests.unit.test_project_config:build_agent",
    "rubric": {
        "criteria": [
            {"id": "grounding", "title": "Grounding", "description": "g", "weight": 1.0}
        ]
    },
    # The judge is on by default; the toy configs need a client or a model to build one.
    "judge": {"enabled": False},
}


class Out(BaseModel):
    """The output schema the toy configs name; only "is it a model" is checked here."""

    qty: int = 0


def _validated(**overrides: Any) -> ProjectConfig:
    data = dict(MINIMAL)
    data.update(overrides)
    return ProjectConfig.model_validate(data)


def test_the_example_file_describes_the_code_preset() -> None:
    """The file is not a lookalike: it builds the same preset, hash included."""
    config = ProjectConfig.load(EXAMPLE_CONFIG)
    preset = build_preset(config)

    assert preset.check_ids() == PRESET.check_ids()
    assert preset.fingerprint() == PRESET.fingerprint()
    assert preset.rubric == PRESET.rubric
    assert preset.scoring == PRESET.scoring
    assert preset.agent_prompt == PRESET.agent_prompt
    assert Path(config.dataset).is_file()
    assert build_pricing(config).price_for("deepseek-flash") is not None


def test_check_command_accepts_the_example(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--config", str(EXAMPLE_CONFIG)]) == 0
    assert "logistics_intake" in capsys.readouterr().out


def test_init_writes_a_starter_and_does_not_overwrite(tmp_path: Path) -> None:
    assert main(["init", "--dir", str(tmp_path)]) == 0
    config_path = tmp_path / "trajan.toml"
    assert config_path.is_file()
    assert (tmp_path / "dataset.jsonl").is_file()
    # A second run must not silently replace an edited file.
    assert main(["init", "--dir", str(tmp_path)]) == 1


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _validated(unexpected="value")


def test_judge_without_client_or_model_is_an_error() -> None:
    with pytest.raises(ValidationError):
        _validated(judge={"enabled": True})


def test_required_condition_needs_a_value() -> None:
    with pytest.raises(ValidationError):
        _validated(
            checks={"required": {"rules": [{"path": "a", "when_field": "kind"}]}},
        )


def test_prompt_file_is_read_with_its_bytes_intact(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("line one\nline two\n", encoding="utf-8")
    (tmp_path / "dataset.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "trajan.toml").write_text(
        "\n".join(
            [
                'name = "toy"',
                'dataset = "dataset.jsonl"',
                'schema = "tests.unit.test_project_config:Out"',
                'agent = "tests.unit.test_project_config:build_agent"',
                'agent_prompt_file = "prompt.txt"',
                "[judge]",
                "enabled = false",
                "[rubric]",
                '[[rubric.criteria]]',
                'id = "grounding"',
                'title = "Grounding"',
                'description = "g"',
                "weight = 1.0",
            ]
        ),
        encoding="utf-8",
    )
    config = ProjectConfig.load(tmp_path / "trajan.toml")
    assert config.agent_prompt == "line one\nline two\n"


def test_a_factory_that_returns_something_else_is_rejected(tmp_path: Path) -> None:
    module = tmp_path / "projectmod.py"
    module.write_text("def not_checks():\n    return 42\n", encoding="utf-8")
    import sys

    sys.path.insert(0, str(tmp_path))
    try:
        config = _validated(checks={"extra": ["projectmod:not_checks"]})
        with pytest.raises(ValueError, match="sequence of checks"):
            build_preset(config)
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("projectmod", None)


def build_agent() -> None:
    """Placeholder agent factory; only its callability is checked at load time."""
