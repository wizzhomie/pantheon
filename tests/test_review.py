"""The box's agent as the reviewer: the same container, a different question.

These tests are about the boundary — what the agent is asked, what its answer becomes, and
what happens when the box or the agent is not there. The live path is proved by a run on the
box, not here; what is proved here is that a review cannot silently turn into an empty one.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness import box as boxes
from harness import container, contour
from harness.cli import main
from harness.executors import review

IMAGE = "nousresearch/hermes-agent@sha256:" + "3e9f" * 16
REVIEW = '{"summary": "adds a constant", "findings": []}'

PASSING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "print('ok')"]
purpose = "a check that always passes"
"""


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_box(tmp_path: Path) -> tuple[Path, boxes.Box]:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    box.compose.write_text(container.render_compose(box, IMAGE), encoding="utf-8")
    return project, box


def make_patch(tmp_path: Path, project: Path) -> Path:
    (project / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    return patch


def request(model: str = "deepseek-flash") -> dict[str, object]:
    return {
        "schema_version": 1,
        "model": model,
        "messages": [
            {"role": "system", "content": "You review a code change."},
            {"role": "user", "content": "Changed files: 1\nsrc/app.py"},
        ],
        "temperature": 0.0,
        "max_tokens": 1500,
        "response_schema": None,
    }


def run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    box: boxes.Box | None,
    payload: dict[str, object],
) -> tuple[int, dict[str, object]]:
    if box is None:
        monkeypatch.delenv(review.BOX_ENV, raising=False)
    else:
        monkeypatch.setenv(review.BOX_ENV, str(box.root))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code = review.main()
    return code, json.loads(capsys.readouterr().out)


def test_the_prompt_keeps_the_roles_and_adds_the_read_only_rule() -> None:
    prompt = review._prompt(request())
    assert prompt.startswith("SYSTEM:\nYou review a code change.")
    assert "REQUEST:\nChanged files: 1" in prompt
    assert "только на чтение" in prompt
    assert "JSON" in prompt


def test_a_model_placeholder_is_not_a_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(review.SECONDS_ENV, "5")
    assert review._model(request(model="reviewer")) == review.DEFAULT_MODEL
    assert review._model(request(model="deepseek-chat")) == "deepseek-chat"


def test_a_box_without_compose_is_refused_before_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    box.compose.unlink()
    code, answer = run(monkeypatch, capsys, box, request())
    assert code == 1
    assert "no compose file" in str(answer["error"])


def test_without_a_box_the_command_says_what_it_lacks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, answer = run(monkeypatch, capsys, None, request())
    assert code == 1
    assert review.BOX_ENV in str(answer["error"])


def test_the_agent_answer_becomes_trajans_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    code, answer = run(monkeypatch, capsys, box, request())
    assert code == 0
    assert answer["text"] == REVIEW
    assert answer["model"] == "deepseek-flash"
    assert "cost_cents" not in answer, "a bill nobody reported is not a bill of zero"


def test_the_bill_is_read_and_written_next_to_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    usage = box.root / review.USAGE_IN_BOX
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text(
        json.dumps({"input_tokens": 900, "output_tokens": 60, "estimated_cost_usd": 0.004}),
        encoding="utf-8",
    )
    log = tmp_path / "run" / "review-usage.jsonl"
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    monkeypatch.setenv(review.LOG_ENV, str(log))
    code, answer = run(monkeypatch, capsys, box, request())
    assert code == 0
    assert answer["cost_cents"] == 0.4
    assert (answer["prompt_tokens"], answer["completion_tokens"]) == (900, 60)
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["cost_cents"] == 0.4 and entry["model"] == "deepseek-flash"


def test_a_failing_agent_is_reported_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")
    code, answer = run(monkeypatch, capsys, box, request())
    assert code == 1
    assert "agent in the container failed" in str(answer["error"])


def test_the_box_reviewer_is_the_command_the_box_can_run(tmp_path: Path) -> None:
    """The spec and the environment are one object: trajan only needs the pair."""
    reviewer = contour.box_reviewer(tmp_path / "box")
    assert reviewer.spec == contour.REVIEW_SPEC
    assert reviewer.env[contour.REVIEW_COMMAND_ENV].endswith("bin/pantheon-review")
    assert reviewer.env[contour.REVIEW_BOX_ENV] == str(tmp_path / "box")
    assert reviewer.env[contour.REVIEW_MODEL_ENV] == contour.REVIEW_DEFAULT_MODEL
    assert int(reviewer.env[contour.REVIEW_TIMEOUT_ENV]) > contour.REVIEW_DEFAULT_SECONDS


def test_a_chosen_model_and_wait_replace_the_defaults(tmp_path: Path) -> None:
    reviewer = contour.box_reviewer(tmp_path / "box", model="deepseek-chat", seconds=30)
    assert reviewer.env[contour.REVIEW_MODEL_ENV] == "deepseek-chat"
    assert reviewer.env[contour.REVIEW_SECONDS_ENV] == "30"
    assert reviewer.env[contour.REVIEW_TIMEOUT_ENV] == "90"


@pytest.mark.skipif(
    not Path(contour.REPO_ROOT / "bin" / "pantheon-review").is_file(),
    reason="the review command is not in this checkout",
)
def test_verify_with_review_puts_a_reader_in_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole seam: `pantheon verify --review` → trajan → the box's agent → a report."""
    if not _trajan_runnable():
        pytest.skip("trajan cannot run here")
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    code = main(
        ["--castra", str(box.root.parent), "verify", box.name, "--diff", str(patch), "--review"]
    )
    out = capsys.readouterr().out
    assert code in (0, 1), out
    assert "review" in out and "deepseek-flash" in out
    runs = sorted((box.root / "tabularium").glob("verify-*/report.json"))
    report = json.loads(runs[-1].read_text(encoding="utf-8"))
    assert report["request"]["reviewer"] == contour.REVIEW_SPEC
    assert report["review"]["status"] == "ok"


def _trajan_runnable() -> bool:
    try:
        root = contour.trajan_root()
    except boxes.HarnessError:
        return False
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(root / "src")
    done = subprocess.run(
        [str(contour.trajan_python(root)), "-m", "trajan", "--help"],
        capture_output=True,
        check=False,
        env=env,
    )
    return done.returncode == 0
