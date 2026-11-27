"""The spec package: documents frozen by a copy, and the owner's mark that lets a feature start.

A package is not a plan and not a checklist: it is a directory with a manifest, a document and
tasks that carry their own ``repro``. These tests hold the import's two refusals, the record
that acceptance writes, the three standings, and the gate: a feature task without an imported,
accepted, unchanged package that names it does not reach the model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import augustus, spec
from harness import box as boxes
from harness.cli import main

PACKAGE_ID = "pkg-one"
TASK_ID = "t-feature"

MANIFEST = """\
schema_version = 1
id = "pkg-one"
milestone = "M1"
source = "spec-kit 0.0.4"
why = "the owner asked for a package before the work"
spec = "spec.md"

[[task]]
id = "t-feature"
kind = "feature"
goal = "add the value"
paths = ["src/**"]
expected = "the value is two"
contract = { symptom = "the value is one", visible = "app says two", boundaries = "src/**" }
repro = ["python", "-c", "assert 1 + 1 == 2"]
self_check = ["python", "-c", "print('visible')"]
"""

TASK = """\
schema_version = 1
id = "t-feature"
goal = "add the value"
kind = "feature"
paths = ["src/**"]
expected = "the value is two"
{spec}
repro = ["python", "-c", "assert 1 + 1 == 2"]

[contract]
symptom = "the value is one"
visible = "src/app.py says value = 2"
boundaries = "src/**"
missing = ""
"""


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    return project


def make_box(tmp_path: Path) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", make_project(tmp_path))


def write_package(
    tmp_path: Path,
    *,
    manifest: str = MANIFEST,
    name: str = "source",
) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    (directory / "manifest.toml").write_text(manifest, encoding="utf-8")
    (directory / "spec.md").write_text("# the intent\n\nA value becomes two.\n", encoding="utf-8")
    return directory


def imported(box: boxes.Box, tmp_path: Path, **kwargs: object) -> spec.Package:
    package = spec.read_package(write_package(tmp_path, **kwargs))  # type: ignore[arg-type]
    spec.import_package(box, package)
    return package


def write_task(tmp_path: Path, task_spec: str = "") -> Path:
    text = TASK.format(spec=f'spec = "{task_spec}"' if task_spec else "")
    path = tmp_path / "task.toml"
    path.write_text(text, encoding="utf-8")
    return path


def dispatched(box: boxes.Box, task: augustus.Task) -> augustus.Dispatch:
    """Run the gate directly: every test here stops before a contour or a model is reached."""
    return augustus.dispatch(
        box,
        task,
        routes=augustus.DEFAULT_ROUTES,
        trajan_root=Path("/nonexistent/trajan"),
        trajan_python=Path("/nonexistent/python"),
    )


def test_import_writes_task_files_that_carry_the_package(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    package = imported(box, tmp_path)
    written = box.root / "mandata" / f"{TASK_ID}.toml"
    assert written.is_file()
    task = augustus.read_task(written)
    assert task.spec == PACKAGE_ID
    assert task.kind == "feature"
    assert task.repro == ("python", "-c", "assert 1 + 1 == 2")
    assert task.self_check == ("python", "-c", "print('visible')")
    contract = task.contract
    assert contract is not None
    assert contract.symptom == "the value is one"
    assert contract.boundaries == "src/**"
    assert contract.missing is None
    assert augustus.intake(task).ready, (
        "an imported task must pass the contract gate: a package that cannot be handed on is "
        "not imported"
    )
    assert f'spec = "{PACKAGE_ID}"' in written.read_text(encoding="utf-8")
    frozen = box.root / spec.PACKAGES / package.id
    assert (frozen / "manifest.toml").is_file() and (frozen / "spec.md").is_file()


def test_import_refuses_a_task_without_repro(tmp_path: Path) -> None:
    text = MANIFEST.replace('repro = ["python", "-c", "assert 1 + 1 == 2"]\n', "")
    with pytest.raises(boxes.HarnessError, match="declares no repro"):
        spec.read_package(write_package(tmp_path, manifest=text))


def test_import_refuses_a_task_without_contract(tmp_path: Path) -> None:
    text = MANIFEST.replace(
        'contract = { symptom = "the value is one", visible = "app says two", '
        'boundaries = "src/**" }\n',
        "",
    )
    with pytest.raises(boxes.HarnessError, match="declares no contract"):
        spec.read_package(write_package(tmp_path, manifest=text))


def test_an_acceptance_leaves_a_ledger_event(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    imported(box, tmp_path)
    spec.accept(box, PACKAGE_ID, note="accepted")
    events = list((box.root / "fasti").glob("*.jsonl"))
    assert events, "an acceptance the timeline cannot find is half a record"
    lines = [json.loads(line) for path in events for line in path.read_text().splitlines()]
    assert [item["type"] for item in lines] == ["spec.accepted"]
    assert lines[0]["data"]["digest"].startswith("sha256:")


def test_import_refuses_an_unknown_manifest_key(tmp_path: Path) -> None:
    text = MANIFEST.replace('why = "the owner asked for a package before the work"',
                            'why = "the owner asked for a package before the work"\ncolour = "red"')
    with pytest.raises(boxes.HarnessError, match="unknown key"):
        spec.read_package(write_package(tmp_path, manifest=text))


def test_accept_then_standing_is_accepted(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    package = imported(box, tmp_path)
    approval = spec.accept(box, PACKAGE_ID, note="the owner says this is the one")
    assert approval.digest == package.digest
    assert approval.tasks == (TASK_ID,)
    record = box.root / "tabularium" / "spec" / f"{PACKAGE_ID}.json"
    assert approval.path == record and record.is_file()
    stored = json.loads(record.read_text(encoding="utf-8"))
    assert stored["schema_version"] == 1
    assert stored["digest"] == package.digest
    assert stored["tasks"] == [TASK_ID]
    assert stored["by"] == "owner"
    assert stored["note"] == "the owner says this is the one"
    assert {item["path"] for item in stored["files"]} >= {"manifest.toml", "spec.md"}
    current = spec.standing(box, PACKAGE_ID)
    assert current.state == spec.ACCEPTED
    assert current.refusal is None
    assert spec.accept(box, PACKAGE_ID, note="the owner says this is the one").at == approval.at


def test_standing_is_never_then_changed_when_the_copy_is_edited(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    imported(box, tmp_path)
    assert spec.standing(box, PACKAGE_ID).state == spec.NEVER
    assert "was never accepted" in (spec.standing(box, PACKAGE_ID).refusal or "")
    spec.accept(box, PACKAGE_ID, note="accepted")
    contract = box.root / spec.PACKAGES / PACKAGE_ID / "spec.md"
    contract.write_text("# a different intent\n", encoding="utf-8")
    current = spec.standing(box, PACKAGE_ID)
    assert current.state == spec.CHANGED
    refusal = current.refusal
    assert refusal is not None
    assert "changed after it was accepted" in refusal
    assert "spec.md" in refusal
    assert spec.check(box, augustus.read_task(write_task(tmp_path, PACKAGE_ID))) is not None


def test_dispatch_refuses_a_feature_with_no_spec(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    result = dispatched(box, augustus.read_task(write_task(tmp_path)))
    assert result.state == "refused"
    assert result.stages == ()
    assert result.exit_code == 1
    assert "declares no spec" in result.notes[0]
    assert not list((box.root / "tabularium" / TASK_ID).glob("dispatch-*.json")), (
        "nothing was dispatched, so no model was reached"
    )


def test_dispatch_refuses_a_package_that_was_never_accepted(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    imported(box, tmp_path)
    result = dispatched(box, augustus.read_task(write_task(tmp_path, PACKAGE_ID)))
    assert result.state == "refused"
    assert result.stages == ()
    assert "was never accepted" in result.notes[0]
    assert "--accept" in result.notes[0]


def test_dispatch_refuses_a_task_the_package_does_not_name(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    text = MANIFEST.replace('id = "t-feature"', 'id = "t-other"')
    imported(box, tmp_path, manifest=text)
    spec.accept(box, PACKAGE_ID, note="accepted")
    result = dispatched(box, augustus.read_task(write_task(tmp_path, PACKAGE_ID)))
    assert result.state == "refused"
    assert result.stages == ()
    assert f"the task {TASK_ID} is not in the spec package {PACKAGE_ID}" in result.notes[0]
    assert "t-other" in result.notes[0]


def test_the_cli_imports_accepts_and_shows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    source = write_package(tmp_path)
    root = str(box.root.parent)
    imported_code = main(
        ["--castra", root, "spec", box.name, PACKAGE_ID, "--from", str(source), "--json"]
    )
    assert imported_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == PACKAGE_ID and payload["tasks"] == [TASK_ID]
    assert (box.root / "mandata" / f"{TASK_ID}.toml").is_file()
    assert main(["--castra", root, "spec", PACKAGE_ID]) == 1
    assert "never accepted" in capsys.readouterr().out
    assert main(["--castra", root, "spec", PACKAGE_ID, "--accept", "--note", "proceed"]) == 0
    accepted = capsys.readouterr().out
    assert "accepted" in accepted and "proceed" in accepted
    assert main(["--castra", root, "spec", PACKAGE_ID]) == 0
    assert "accepted" in capsys.readouterr().out
    record = box.root / "tabularium" / "spec" / f"{PACKAGE_ID}.json"
    assert record.is_file()
