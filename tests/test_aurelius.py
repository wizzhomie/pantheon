"""Aurelius: the model compiles into the tool's config, and a missing tool is not a clean pass.

The boundary tool is faked here on purpose. The harness has no dependencies, so the test
asserts what Aurelius itself owns — the config it writes, the environment the tool needs, how
it reads the answer, and that "not installed" never comes out as "architecture fine".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import aurelius
from harness import box as boxes
from harness.cli import main

MODEL = """\
schema_version = 1

[project]
root_packages = ["pkg"]

[architecture]
baseline = "one package"
target = "four layers"

[[layer]]
name = "app"
modules = ["pkg.app"]

[[layer]]
name = "domain"
modules = ["pkg.domain"]

[[forbidden]]
name = "the domain does not know the outside world"
source = ["pkg.domain"]
target = ["pkg.app", "requests"]
reason = "dependencies point inward; the domain is the thing that cannot be imported from"
"""

FAKE_TOOL = """\
#!/usr/bin/env python3
import os
import sys

if "--version" in sys.argv:
    print("import-linter 2.9 fake")
    sys.exit(0)
print(f"config {sys.argv[1]}")
print(f"PYTHONPATH {os.environ.get('PYTHONPATH', '')}")
if os.environ.get("FAKE_QUIET") == "1":
    print("the graph could not be built", file=sys.stderr)
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
print("Contracts: {kept} kept, {broken} broken.".format(
    kept=os.environ.get("FAKE_KEPT", "2"), broken=os.environ.get("FAKE_BROKEN", "0")))
print("")
print("pkg.domain is not allowed to import pkg.app:")
print("-   pkg.domain.service")
print("  - pkg.app.models")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    (project / ".pantheon").mkdir(parents=True)
    (project / ".pantheon" / "architecture.toml").write_text(MODEL, encoding="utf-8")
    (project / "pkg").mkdir()
    (project / "pkg" / "__init__.py").write_text("")
    return project


def make_tool(tmp_path: Path) -> Path:
    tool = tmp_path / "lint-imports"
    tool.write_text(FAKE_TOOL, encoding="utf-8")
    tool.chmod(0o755)
    return tool


def make_box(tmp_path: Path) -> tuple[Path, Path]:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    return project, box.root


def test_the_model_compiles_into_the_tools_own_config(tmp_path: Path) -> None:
    model = aurelius.read_model(make_project(tmp_path) / aurelius.MODEL_FILE)
    assert aurelius.compile_config(model) == (
        "[importlinter]\n"
        "root_packages =\n"
        "    pkg\n"
        "\n"
        "[importlinter:contract:layers]\n"
        "name = declared layers\n"
        "type = layers\n"
        "layers =\n"
        "    pkg.app\n"
        "    pkg.domain\n"
        "\n"
        "[importlinter:contract:forbidden-1]\n"
        "name = the domain does not know the outside world\n"
        "type = forbidden\n"
        "source_modules =\n"
        "    pkg.domain\n"
        "forbidden_modules =\n"
        "    pkg.app\n"
        "    requests\n"
    )


def test_a_model_that_cannot_be_enforced_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "model.toml"
    with pytest.raises(boxes.HarnessError, match="no architecture model"):
        aurelius.read_model(path)
    path.write_text(MODEL + 'surprise = "x"\n', encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="unknown key"):
        aurelius.read_model(path)
    path.write_text(
        MODEL.replace('reason = "dependencies', '# reason = "dependencies'), encoding="utf-8"
    )
    with pytest.raises(boxes.HarnessError, match="needs a reason"):
        aurelius.read_model(path)
    path.write_text(MODEL.replace('modules = ["pkg.domain"]\n', ""), encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="needs modules"):
        aurelius.read_model(path)
    path.write_text(MODEL.split("[[layer]]")[0], encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="cannot be broken"):
        aurelius.read_model(path)
    path.write_text(
        MODEL.replace('root_packages = ["pkg"]', "root_packages = []"), encoding="utf-8"
    )
    with pytest.raises(boxes.HarnessError, match="root_packages"):
        aurelius.read_model(path)


def test_a_clean_check_is_recorded_with_the_tool_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, box_root = make_box(tmp_path)
    monkeypatch.setenv(aurelius.TOOL_ENV, str(make_tool(tmp_path)))
    model = aurelius.read_model(project / aurelius.MODEL_FILE)
    result = aurelius.check(box_root, project, model)
    assert (result.status, result.kept, result.broken, result.exit_code) == ("ok", 2, 0, 0)
    assert result.version == "import-linter 2.9 fake"
    record = json.loads((result.directory / "result.json").read_text())
    assert record["status"] == "ok" and record["model_digest"].startswith("sha256:")
    assert (result.directory / "importlinter.ini").is_file()
    stdout = (result.directory / "lint.stdout.txt").read_text()
    assert "Contracts: 2 kept, 0 broken." in stdout
    assert f"PYTHONPATH {project / 'src'}" in stdout or str(project) in stdout
    events = (box_root / "fasti" / f"{record['at'][:10]}.jsonl").read_text().splitlines()
    assert json.loads(events[-1])["type"] == "arch.checked"


def test_a_broken_contract_is_a_failure_with_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, box_root = make_box(tmp_path)
    monkeypatch.setenv(aurelius.TOOL_ENV, str(make_tool(tmp_path)))
    monkeypatch.setenv("FAKE_EXIT", "1")
    monkeypatch.setenv("FAKE_BROKEN", "1")
    model = aurelius.read_model(project / aurelius.MODEL_FILE)
    result = aurelius.check(box_root, project, model)
    assert (result.status, result.broken, result.exit_code) == ("broken", 1, 1)
    assert "pkg.domain.service" in (result.directory / "lint.stdout.txt").read_text()
    record = json.loads((result.directory / "result.json").read_text())
    events = (box_root / "fasti" / f"{record['at'][:10]}.jsonl").read_text()
    assert "arch.violated" in events


def test_a_tool_that_crashes_is_not_a_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, box_root = make_box(tmp_path)
    monkeypatch.setenv(aurelius.TOOL_ENV, str(make_tool(tmp_path)))
    monkeypatch.setenv("FAKE_EXIT", "3")
    monkeypatch.setenv("FAKE_QUIET", "1")
    model = aurelius.read_model(project / aurelius.MODEL_FILE)
    result = aurelius.check(box_root, project, model)
    assert (result.status, result.exit_code) == ("error", 2)
    assert result.notes and "summary line" in result.notes[0]


def test_a_missing_tool_says_so_instead_of_passing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, box_root = make_box(tmp_path)
    monkeypatch.delenv(aurelius.TOOL_ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    model = aurelius.read_model(project / aurelius.MODEL_FILE)
    result = aurelius.check(box_root, project, model)
    assert (result.status, result.exit_code) == ("unchecked", 2)
    assert "not installed" in result.notes[0]
    text = "\n".join(aurelius.render_result(result, model))
    assert "result     unchecked" in text
    assert "the boundary tool is not installed" in text
    record = json.loads((result.directory / "result.json").read_text())
    day = record["at"][:10]
    assert "arch.unchecked" in (box_root / "fasti" / f"{day}.jsonl").read_text()


def test_the_cli_runs_the_check_and_maps_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box_root = make_box(tmp_path)
    monkeypatch.setenv(aurelius.TOOL_ENV, str(make_tool(tmp_path)))
    boxes_root = box_root.parent
    assert main(["--castra", str(boxes_root), "arch", "probe", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok" and payload["kept"] == 2
    monkeypatch.setenv("FAKE_EXIT", "1")
    monkeypatch.setenv("FAKE_BROKEN", "1")
    assert main(["--castra", str(boxes_root), "arch", "probe"]) == 1
    assert "result     broken" in capsys.readouterr().out


def test_all_layers_are_one_contract_not_one_contract_per_layer(tmp_path: Path) -> None:
    """A one-layer contract forbids nothing: compiling each layer separately always passes."""
    project = make_project(tmp_path)
    text = aurelius.compile_config(aurelius.read_model(project / aurelius.MODEL_FILE))
    assert text.count("type = layers") == 1
    assert "    pkg.app\n    pkg.domain\n" in text


def test_type_only_imports_can_be_excluded_and_are_not_by_default(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    path = project / aurelius.MODEL_FILE
    assert "exclude_type_checking_imports" not in aurelius.compile_config(
        aurelius.read_model(path)
    )
    path.write_text(
        MODEL.replace(
            'root_packages = ["pkg"]',
            'root_packages = ["pkg"]\nexclude_type_checking_imports = true',
        ),
        encoding="utf-8",
    )
    model = aurelius.read_model(path)
    assert model.exclude_type_checking
    assert "exclude_type_checking_imports = true" in aurelius.compile_config(model)


def test_independent_peers_are_written_with_a_bar(tmp_path: Path) -> None:
    """`independent = true` says "these are alternatives": the tool gets `|`, not `:`."""
    project = make_project(tmp_path)
    path = project / aurelius.MODEL_FILE
    path.write_text(
        MODEL.replace(
            'modules = ["pkg.app"]',
            'modules = ["pkg.alpha", "pkg.beta"]\nindependent = true',
        ),
        encoding="utf-8",
    )
    text = aurelius.compile_config(aurelius.read_model(path))
    assert "    pkg.alpha | pkg.beta\n" in text
    path.write_text(
        MODEL.replace('modules = ["pkg.app"]', 'modules = ["pkg.alpha", "pkg.beta"]'),
        encoding="utf-8",
    )
    text = aurelius.compile_config(aurelius.read_model(path))
    assert "    pkg.alpha : pkg.beta\n" in text
