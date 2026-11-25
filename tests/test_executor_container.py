"""The container executor: the box's own agent produces the diff, the host still gates it.

Docker is faked (see conftest): these tests are about the protocol around the agent — what it
is asked, what its bill is read as, and what happens when the box has no container or the
agent fails. The live path is covered by the box's own record, not here.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from harness import box as boxes
from harness import container
from harness.executors import container as executor

IMAGE = "nousresearch/hermes-agent@sha256:" + "3e9f" * 16
DIFF = "--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1 +1 @@\n-a\n+b\n"


def make_box(tmp_path: Path) -> boxes.Box:
    project = tmp_path / "probe"
    (project / ".trajan").mkdir(parents=True)
    (project / ".trajan" / "policy.toml").write_text('name = "probe"\n', encoding="utf-8")
    box = boxes.new_box(tmp_path / "castra", project)
    box.compose.write_text(container.render_compose(box, IMAGE), encoding="utf-8")
    return box


def request_for(box: boxes.Box) -> dict[str, object]:
    return {
        "task": {
            "id": "t-1",
            "goal": "add a line",
            "kind": "docs",
            "paths": ["docs/**"],
            "expected": "the line exists",
            "forbidden": ["delete"],
        },
        "box": {"name": box.name, "root": str(box.root)},
        "project": {"path": str(box.project)},
        "limits": {"max_seconds": 5, "max_cents": 2},
        "model": "deepseek-flash",
    }


def run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: dict[str, object]
) -> tuple[int, dict[str, object]]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code = executor.main()
    return code, json.loads(capsys.readouterr().out)


def test_the_prompt_says_read_only_scope_and_one_diff_only() -> None:
    prompt = executor._prompt(
        {
            "goal": "add a line",
            "kind": "docs",
            "paths": ["docs/**"],
            "expected": "the line exists",
            "forbidden": ["delete"],
        }
    )
    assert "add a line" in prompt and "docs/**" in prompt
    assert "только на чтение" in prompt
    assert "git apply" in prompt and "ERROR:" in prompt
    assert "Запрещено: delete" in prompt


def test_the_agent_bill_is_read_in_cents(tmp_path: Path) -> None:
    bill = tmp_path / "usage.json"
    bill.write_text(
        json.dumps(
            {"input_tokens": 1000, "output_tokens": 40, "estimated_cost_usd": 0.0123}
        ),
        encoding="utf-8",
    )
    assert executor._usage(bill) == {
        "prompt_tokens": 1000,
        "completion_tokens": 40,
        "cost_cents": 1.23,
    }
    assert executor._usage(tmp_path / "absent.json") is None


def test_a_box_without_a_compose_file_is_refused_before_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    box.compose.unlink()
    payload = request_for(box)
    code, answer = run(monkeypatch, capsys, payload)
    assert code == 1
    assert "no compose file" in str(answer["error"])


def test_the_agent_answer_becomes_the_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_AGENT", DIFF)
    code, answer = run(monkeypatch, capsys, request_for(box))
    assert code == 0
    assert answer["patch"] == DIFF
    assert answer["model"] == "deepseek-flash"
    assert "hermes in the box's container" in str(answer["notes"])


def test_a_failing_agent_is_reported_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")
    code, answer = run(monkeypatch, capsys, request_for(box))
    assert code == 1
    assert "agent in the container failed" in str(answer["error"])
