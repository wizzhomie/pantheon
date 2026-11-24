"""The box, and the command that builds it: what ``pantheon new`` promises and what it refuses.

The cases are the mistakes a person makes once: a name that is not a directory name, an
image pinned to ``latest``, a second box for the same project, a project that moved away.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from harness import box as boxes
from harness.cli import main

CONTEXT = """\
[[requirements]]
id = "R1"
title = "the verdict is computed, not written"
status = "approved"

[[requirements]]
id = "R2"
title = "checks run without a shell"
status = "proposed"

[architecture]
baseline = "one contour"
target = "the whole harness"
"""

POLICY = """\
name = "probe-default"

[[checks]]
id = "tests"
argv = ["python", "-m", "pytest", "-q"]

[[checks]]
id = "lint"
argv = ["python", "-m", "ruff", "check", "."]
"""


def make_project(root: Path, context: str = CONTEXT, policy: str = POLICY) -> Path:
    """A project that looks like one the harness can serve."""
    project = root / "probe"
    (project / ".trajan").mkdir(parents=True)
    (project / ".trajan" / "context.toml").write_text(context, encoding="utf-8")
    (project / ".trajan" / "policy.toml").write_text(policy, encoding="utf-8")
    (project / "README.md").write_text("a project\n", encoding="utf-8")
    return project


def test_new_builds_the_layout_and_links_the_project(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    assert box.name == "probe"
    assert box.project == project.resolve()
    for directory in boxes.BOX_DIRS:
        assert (box.root / directory).is_dir()
    assert box.file.is_file() and box.compose.is_file()
    assert boxes.read_box(box.file) == box


def test_new_does_not_write_into_the_project(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    before = sorted(path.relative_to(project) for path in project.rglob("*"))
    boxes.new_box(tmp_path / "boxes", project)
    after = sorted(path.relative_to(project) for path in project.rglob("*"))
    assert before == after


def test_the_image_is_recorded_when_it_is_pinned(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project, image="nousresearch/hermes-agent:2026.11")
    assert boxes.read_box(box.file).image == "nousresearch/hermes-agent:2026.11"


def test_without_an_image_the_key_is_absent_and_status_says_so(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    assert "image" not in tomllib.loads(box.file.read_text(encoding="utf-8"))
    assert "not chosen" in "\n".join(boxes.render_status(boxes.status(box)))


@pytest.mark.parametrize("image", ["hermes-agent", "hermes-agent:latest", "nousresearch/x:latest"])
def test_a_moving_or_missing_tag_is_refused(tmp_path: Path, image: str) -> None:
    project = make_project(tmp_path)
    with pytest.raises(boxes.HarnessError, match=r"tag|latest"):
        boxes.new_box(tmp_path / "boxes", project, image=image)
    assert not (tmp_path / "boxes" / "probe").exists()


@pytest.mark.parametrize("name", ["Probe", "pro be", "-probe", "probe/", "x" * 41, ""])
def test_a_name_that_is_not_a_directory_name_is_refused(tmp_path: Path, name: str) -> None:
    project = make_project(tmp_path)
    with pytest.raises(boxes.HarnessError):
        boxes.new_box(tmp_path / "boxes", project, name=name)


def test_a_second_box_for_the_same_name_is_refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    boxes.new_box(tmp_path / "boxes", project)
    with pytest.raises(boxes.HarnessError, match="already exists"):
        boxes.new_box(tmp_path / "boxes", project)


def test_a_project_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(boxes.HarnessError, match="not a directory"):
        boxes.new_box(tmp_path / "boxes", tmp_path / "nowhere")


def test_a_box_file_with_an_unknown_key_is_refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    box.file.write_text(box.file.read_text() + 'surprise = "x"\n', encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="surprise"):
        boxes.read_box(box.file)


def test_a_box_file_of_another_schema_is_refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    box.file.write_text(
        box.file.read_text().replace("schema_version = 1", "schema_version = 2"), encoding="utf-8"
    )
    with pytest.raises(boxes.HarnessError, match="schema_version"):
        boxes.read_box(box.file)


def test_status_reads_policy_context_and_git(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    report = boxes.status(box)
    assert report.project.policy_name == "probe-default"
    assert report.project.checks == ("tests", "lint")
    assert [item.id for item in report.project.requirements] == ["R1", "R2"]
    assert (report.approved, report.proposed) == (1, 1)
    assert report.project.has_baseline and report.project.has_target
    assert report.missing_dirs == () and report.compose_services == ()
    text = "\n".join(boxes.render_status(report))
    assert "probe-default" in text and "tests, lint" in text
    assert "1 approved, 1 proposed" in text
    assert "baseline and target" in text
    assert "pantheon dispatch" in text


def test_status_json_is_one_object_with_stable_keys(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    payload = boxes.status_json(boxes.status(box))
    assert set(payload) == {"box", "project", "agent"}
    facts = payload["box"]
    assert isinstance(facts, dict)
    assert (facts["name"], facts["image"], facts["missing_dirs"]) == ("probe", None, [])
    view = payload["project"]
    assert isinstance(view, dict)
    assert view["checks"] == ["tests", "lint"]
    assert view["requirements"] == [
        {"id": "R1", "status": "approved"},
        {"id": "R2", "status": "proposed"},
    ]
    agent = payload["agent"]
    assert isinstance(agent, dict) and agent["running"] is False
    assert "pantheon dispatch" in str(agent["reason"])
    json.dumps(payload)


def test_status_names_what_the_project_does_not_have(tmp_path: Path) -> None:
    project = tmp_path / "bare"
    project.mkdir()
    box = boxes.new_box(tmp_path / "boxes", project)
    report = boxes.status(box)
    assert not report.project.checks and report.project.requirements == ()
    assert any("no .trajan/policy.toml" in note for note in report.project.notes)
    assert any("no .trajan/context.toml" in note for note in report.project.notes)


def test_a_broken_policy_is_described_not_fatal(tmp_path: Path) -> None:
    project = make_project(tmp_path, policy="this is not toml = = =\n")
    box = boxes.new_box(tmp_path / "boxes", project)
    report = boxes.status(box)
    assert report.project.policy_name is None
    assert any("policy.toml does not parse" in note for note in report.project.notes)


def test_a_missing_directory_is_named_in_the_layout(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    (box.root / "ledger").rmdir()
    report = boxes.status(box)
    assert report.missing_dirs == ("ledger",)
    assert "missing ledger" in "\n".join(boxes.render_status(report))


def test_compose_services_are_read_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "compose.yaml"
    assert boxes.compose_services(path) == ()
    path.write_text("services:\n  augustus:\n    image: x:1\n", encoding="utf-8")
    assert boxes.compose_services(path) == ("augustus",)


def test_a_box_is_found_by_name_by_cwd_or_as_the_only_one(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    boxes_root = tmp_path / "boxes"
    box = boxes.new_box(boxes_root, project)
    assert boxes.find_box(boxes_root, "probe", tmp_path) == box
    assert boxes.find_box(boxes_root, None, box.root / "ledger") == box
    assert boxes.find_box(boxes_root, None, tmp_path) == box


def test_an_ambiguous_or_unknown_box_is_an_error(tmp_path: Path) -> None:
    boxes_root = tmp_path / "boxes"
    boxes.new_box(boxes_root, make_project(tmp_path))
    second = make_project(tmp_path / "second")
    boxes.new_box(boxes_root, second, name="second")
    with pytest.raises(boxes.HarnessError, match="several boxes"):
        boxes.find_box(boxes_root, None, tmp_path)
    with pytest.raises(boxes.HarnessError, match="no box 'third'"):
        boxes.find_box(boxes_root, "third", tmp_path)
    with pytest.raises(boxes.HarnessError, match="no boxes"):
        boxes.find_box(tmp_path / "empty", None, tmp_path)


def test_the_cli_creates_and_describes_a_box(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    boxes_root = tmp_path / "boxes"
    assert main(["--boxes", str(boxes_root), "new", str(project)]) == 0
    assert "box probe created" in capsys.readouterr().out
    assert main(["--boxes", str(boxes_root), "status", "probe", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project"]["requirements"][0] == {"id": "R1", "status": "approved"}


def test_up_without_a_pinned_image_refuses_and_down_is_harmless(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    boxes_root = tmp_path / "boxes"
    main(["--boxes", str(boxes_root), "new", str(project)])
    capsys.readouterr()
    assert main(["--boxes", str(boxes_root), "up", "probe"]) == 2
    assert "no image" in capsys.readouterr().err
    assert main(["--boxes", str(boxes_root), "down", "probe"]) == 0
    assert "nothing to stop" in capsys.readouterr().out


def test_the_cli_answers_a_bad_request_with_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--boxes", str(tmp_path / "boxes"), "status", "nope"]) == 2
    assert "no box 'nope'" in capsys.readouterr().err


def test_the_cli_lists_boxes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = make_project(tmp_path)
    boxes_root = tmp_path / "boxes"
    main(["--boxes", str(boxes_root), "new", str(project)])
    capsys.readouterr()
    assert main(["--boxes", str(boxes_root), "list"]) == 0
    assert "probe" in capsys.readouterr().out
    assert main(["--boxes", str(tmp_path / "none"), "list"]) == 0
    assert "no boxes" in capsys.readouterr().out


def test_the_harness_does_not_import_the_contour() -> None:
    """The two halves are independent: the box does not need trajan to exist or to work."""
    root = Path(__file__).resolve().parent.parent
    for path in sorted((root / "harness").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "import trajan" not in source, path
        assert "from trajan" not in source, path


def test_the_command_runs_from_the_checkout() -> None:
    """`bin/pantheon` is what a person types: the shim, not the module, has to work."""
    root = Path(__file__).resolve().parent.parent
    done = subprocess.run(
        [str(root / "bin" / "pantheon"), "--help"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0
    assert "usage: pantheon" in done.stdout
