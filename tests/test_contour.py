"""The box calls trajan: the answer has to be trajan's answer, not the box's guess.

The last test here is the stage 1.2 acceptance criterion written as code: run the box's
`verify` and trajan directly on the same project, and require the same verdict and the same
exit code. It runs only where trajan can run at all — the harness itself has no dependencies.
"""

from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from harness import box as boxes
from harness import contour
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "print('ok')"]
purpose = "a check that always passes"
"""

FAILING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "raise SystemExit(1)"]
purpose = "a check that always fails, so the verdict is HOLD"
"""


def make_project(tmp_path: Path, policy: str) -> Path:
    project = tmp_path / "probe"
    (project / ".trajan").mkdir(parents=True)
    (project / ".trajan" / "policy.toml").write_text(policy, encoding="utf-8")
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    return project


def report(status: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run": {"run_id": "r1", "checks": [{"check_id": "smoke", "status": "passed"}]},
        "findings": [{"id": "F1"}],
        "verdict": {
            "status": status,
            "reasons": [{"code": "checks-passed", "detail": "all good"}],
            "blocking_findings": [],
        },
    }


def test_a_report_is_read_not_inferred(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report("hold")), encoding="utf-8")
    result = contour.read_report(path, exit_code=1)
    assert (result.run_id, result.status, result.checks) == ("r1", "hold", 1)
    assert result.reasons == ("checks-passed: all good",)
    assert (result.findings, result.blocking) == (1, 0)
    assert not result.passed


def test_an_unreadable_report_is_an_error_not_a_pass(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="not readable json"):
        contour.read_report(path)
    path.write_text('{"no": "verdict"}', encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="no verdict"):
        contour.read_report(path)
    path.write_text('{"verdict": {}, "run": {"run_id": "r1"}}', encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="not an answer"):
        contour.read_report(path)


def test_the_trajan_checkout_is_found_by_flag_then_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "elsewhere"
    (fake / "src" / "trajan").mkdir(parents=True)
    (fake / "src" / "trajan" / "__init__.py").write_text("")
    monkeypatch.setenv(contour.TRAJAN_ENV, str(fake))
    assert contour.trajan_root() == fake.resolve()
    monkeypatch.delenv(contour.TRAJAN_ENV)
    assert contour.trajan_root(str(fake)) == fake.resolve()
    # An environment variable pointing nowhere is not fatal: the sibling checkout is the
    # last answer, and that is the one someone forgot they could pass.
    monkeypatch.setenv(contour.TRAJAN_ENV, str(tmp_path / "nothing"))
    assert contour.trajan_root() == (contour.REPO_ROOT / "trajan").resolve()
    monkeypatch.setattr(contour, "REPO_ROOT", tmp_path)
    with pytest.raises(boxes.HarnessError, match="no trajan checkout"):
        contour.trajan_root()


def test_a_checkout_without_a_venv_runs_on_this_interpreter(tmp_path: Path) -> None:
    root = tmp_path / "trajan"
    root.mkdir()
    assert contour.trajan_python(root).name.startswith("python")
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("")
    assert contour.trajan_python(root) == venv / "python"
    chosen = tmp_path / "other-python"
    chosen.write_text("")
    assert contour.trajan_python(root, str(chosen)) == chosen.resolve()


def test_a_change_without_a_reader_is_refused_before_any_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path, PASSING)
    boxes_root = tmp_path / "castra"
    main(["--castra", str(boxes_root), "new", str(project)])
    capsys.readouterr()
    assert main(["--castra", str(boxes_root), "verify", "probe", "--since", "HEAD"]) == 2
    message = capsys.readouterr().err
    assert "a change needs a reader" in message
    assert not list((boxes_root / "probe" / "tabularium").iterdir())


def test_a_project_without_a_policy_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "bare"
    project.mkdir()
    boxes_root = tmp_path / "castra"
    main(["--castra", str(boxes_root), "new", str(project), "--name", "probe"])
    capsys.readouterr()
    assert main(["--castra", str(boxes_root), "verify", "probe"]) == 2
    assert "nothing to verify against" in capsys.readouterr().err


@lru_cache(maxsize=1)
def trajan_is_runnable() -> bool:
    """Whether a real trajan can run here: the harness has no dependencies of its own."""
    try:
        root = contour.trajan_root()
    except boxes.HarnessError:
        return False
    python = contour.trajan_python(root)
    done = subprocess.run(
        [str(python), "-m", "trajan", "--help"],
        capture_output=True,
        check=False,
        env=_env(root, python),
        timeout=120,
    )
    return done.returncode == 0


def _env(root: Path, python: Path) -> dict[str, str]:
    agent = os.environ.copy()
    agent["PYTHONPATH"] = str(root / "src")
    agent["PATH"] = f"{Path(python).parent}:{agent.get('PATH', '')}"
    return agent


def _direct(root: Path, project: Path, out: Path) -> subprocess.CompletedProcess[str]:
    python = contour.trajan_python(root)
    return subprocess.run(
        [
            str(python),
            "-m",
            "trajan",
            "verify",
            "--repo",
            str(project),
            "--policy",
            str(project / ".trajan" / "policy.toml"),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_env(root, python),
        timeout=300,
    )


@pytest.mark.skipif(not trajan_is_runnable(), reason="trajan cannot run in this environment")
@pytest.mark.parametrize(("policy", "expected"), [(PASSING, "pass"), (FAILING, "hold")])
def test_the_box_answer_is_trajans_answer(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    policy: str,
    expected: str,
) -> None:
    project = make_project(tmp_path, policy)
    boxes_root = tmp_path / "castra"
    main(["--castra", str(boxes_root), "new", str(project)])
    capsys.readouterr()
    box_run = tmp_path / "box-run"
    code = main(
        ["--castra", str(boxes_root), "verify", "probe", "--json", "--out", str(box_run)]
    )
    payload = json.loads(capsys.readouterr().out)
    direct_run = tmp_path / "direct-run"
    done = _direct(contour.trajan_root(), project, direct_run)
    direct = json.loads((direct_run / "report.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == expected
    assert payload["verdict"] == direct["verdict"]["status"]
    assert code == done.returncode == payload["exit_code"]
    assert Path(str(payload["report"])).is_file()
