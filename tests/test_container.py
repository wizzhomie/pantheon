"""The container: pinned image, read-only project, a lifecycle that says what it is.

Docker is faked here (see conftest): these tests are about the compose file the box writes,
the mounts it declares, and the honest difference between "started" and "did not start".
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from harness import box as boxes
from harness import container
from harness.cli import main

IMAGE = "nousresearch/hermes-agent@sha256:" + "3e9f" * 16
TASK = (
    'schema_version = 1\nid = "t-1"\ngoal = "x"\nkind = "docs"\n'
    'paths = ["docs/**"]\nexpected = "y"\n'
)


def make_box(tmp_path: Path) -> boxes.Box:
    project = tmp_path / "probe"
    (project / ".trajan").mkdir(parents=True)
    (project / ".trajan" / "policy.toml").write_text('name = "probe"\n', encoding="utf-8")
    return boxes.new_box(tmp_path / "boxes", project)


def docker_log(tmp_path: Path) -> list[str]:
    path = tmp_path / "docker.log"
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def test_the_compose_file_declares_the_mounts_and_the_limit(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    text = container.render_compose(box, IMAGE)
    assert IMAGE in text
    assert f"{box.project.as_posix()}:/work/project:ro" in text
    assert f"{(box.root / 'state').as_posix()}:/opt/data" in text
    assert f"{(box.root / 'artifacts').as_posix()}:/work/artifacts:ro" in text
    assert "mem_limit: 512m" in text
    assert 'entrypoint: ["sleep", "infinity"]' in text
    assert "no agent" not in text or "agent is not configured" in text


def test_up_needs_an_image_and_says_where_to_pin_one(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    with pytest.raises(boxes.HarnessError, match="no image"):
        container.up(box)
    assert docker_log(tmp_path) == []


def test_up_pins_the_image_writes_compose_and_reports_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = make_box(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_RUNNING", "1")
    result = container.up(box, IMAGE)
    assert result.running
    assert tomllib.loads(box.file.read_text())["image"] == IMAGE
    assert IMAGE in box.compose.read_text()
    log = docker_log(tmp_path)
    assert any("compose" in line and "up -d" in line for line in log), log
    assert any("-p pantheon-probe" in line for line in log), log


def test_up_that_does_not_produce_a_running_container_is_not_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    assert main(["--boxes", str(box.root.parent), "up", "probe", "--image", IMAGE]) == 2
    assert "did not come up" in capsys.readouterr().err


def test_down_removes_the_container_and_says_so(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    box.compose.write_text(container.render_compose(box, IMAGE), encoding="utf-8")
    result = container.down(box)
    assert not result.running
    assert "no container yet" in result.detail
    assert any("down --remove-orphans" in line for line in docker_log(tmp_path))


def test_down_on_a_box_without_a_service_does_nothing(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    result = container.down(box)
    assert "nothing to stop" in result.detail
    assert docker_log(tmp_path) == []


def test_status_shows_the_container_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    box = make_box(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_RUNNING", "1")
    container.up(box, IMAGE)
    capsys.readouterr()
    assert main(["--boxes", str(box.root.parent), "status", "probe", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["container"]["running"] is True
    assert payload["container"]["image"] == IMAGE
    assert main(["--boxes", str(box.root.parent), "status", "probe"]) == 0
    text = capsys.readouterr().out
    assert "container  running" in text and "mount only" in text


def test_without_docker_the_answer_is_a_reason_not_a_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = make_box(tmp_path)
    monkeypatch.setenv("PANTHEON_DOCKER", str(tmp_path / "gone"))
    with pytest.raises(boxes.HarnessError, match="docker is not usable"):
        container.up(box, IMAGE)
    facts = container.state(box)
    assert not facts.running and "cannot be inspected" in facts.detail
