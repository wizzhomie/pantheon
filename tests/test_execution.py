"""The boundary between one call, a loop, and an agent, before any model is asked."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from harness import executor

PROFILE = """\
schema_version = 1
command = "/bin/true"

[execution]
one_shot_max_files = 2
one_shot_max_changed_lines = 30
steps_max_files = 3
steps_max_changed_lines = 100

[shapes.patch]
max_changed_lines = 30
max_files = 2

[shapes.multi]
max_changed_lines = 100
max_files = 3
"""


def config(tmp_path: Path, extra: str = "") -> executor.ExecutorConfig:
    path = tmp_path / "executor.toml"
    path.write_text(PROFILE + extra, encoding="utf-8")
    return executor.read_config(path)


def task(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": "t",
        "goal": "g",
        "kind": "bugfix",
        "paths": ("src/a.py",),
        "expected": "e",
        "self_check": (),
        "shape": "patch",
        "forbidden": (),
        "budget": {},
        "max_steps": None,
        "role": "executor",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_a_small_patch_without_a_visible_check_is_one_call(tmp_path: Path) -> None:
    route = executor.route_for(config(tmp_path), task())
    assert route.level == 1
    assert route.engine == "one-shot"
    assert route.refusal is None
    assert "level 1" in route.reason


def test_a_declared_steps_kind_still_runs_steps_for_a_small_task(tmp_path: Path) -> None:
    profile = config(tmp_path, '\n[engines]\nbugfix = "steps"\n')
    route = executor.route_for(profile, task())
    assert route.level == 1
    assert route.engine == "steps"
    assert "the box declares steps" in route.reason


def test_a_visible_check_puts_the_task_in_a_loop(tmp_path: Path) -> None:
    route = executor.route_for(
        config(tmp_path),
        task(shape="multi", self_check=("python", "-c", "raise SystemExit(1)"), max_steps=3),
    )
    assert route.level == 2
    assert route.engine == "steps"
    assert route.refusal is None


def test_a_declared_one_shot_is_escalated_by_the_policy(tmp_path: Path) -> None:
    profile = config(tmp_path, '\n[engines]\nbugfix = "one-shot"\n')
    route = executor.route_for(
        profile,
        task(shape="multi", self_check=("python", "-c", "raise SystemExit(1)"), max_steps=3),
    )
    assert route.level == 2
    assert route.engine == "steps"
    assert "escalated" in route.reason


def test_a_loop_without_a_call_ceiling_is_refused(tmp_path: Path) -> None:
    route = executor.route_for(
        config(tmp_path),
        task(shape="multi", self_check=("python", "-c", "raise SystemExit(1)")),
    )
    assert route.level == 2
    assert route.refusal is not None
    assert "no call ceiling" in route.refusal


def test_multi_without_a_visible_check_needs_an_agent(tmp_path: Path) -> None:
    route = executor.route_for(config(tmp_path), task(shape="multi"))
    assert route.level == 3
    assert route.engine == "agent"
    assert route.refusal is not None
    assert "no visible check" in route.refusal


def test_more_than_three_paths_needs_an_agent(tmp_path: Path) -> None:
    route = executor.route_for(
        config(tmp_path),
        task(
            paths=("a", "b", "c", "d"),
            shape="multi",
            self_check=("python", "-c", "raise SystemExit(1)"),
            max_steps=3,
        ),
    )
    assert route.level == 3
    assert "above the step limit" in (route.refusal or "")


def test_a_shape_above_the_one_shot_line_is_not_one_shot(tmp_path: Path) -> None:
    profile = config(tmp_path, "\n[shapes.file]\nmax_changed_lines = 80\nmax_files = 2\n")
    route = executor.route_for(profile, task(shape="file"))
    assert route.level == 3
    assert route.engine == "agent"


def test_a_box_without_a_policy_keeps_the_old_engine_table(tmp_path: Path) -> None:
    path = tmp_path / "legacy.toml"
    path.write_text(
        'schema_version = 1\ncommand = "/bin/true"\n[engines]\nbugfix = "steps"\n',
        encoding="utf-8",
    )
    route = executor.route_for(executor.read_config(path), task())
    assert route.level is None
    assert route.engine == "steps"
    assert "no execution policy" in route.reason
