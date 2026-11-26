"""The container executor: the box's own agent produces the diff, the host still gates it.

Docker is faked (see conftest): these tests are about the protocol around the agent — what it
is asked, what its bill is read as, and what happens when the box has no container or the
agent fails. The live path is covered by the box's own record, not here.
"""

from __future__ import annotations

import hashlib
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
    assert "не запускай набор тестов" in prompt
    assert "пути в дифе всё равно от корня проекта" in prompt


def test_the_records_the_box_selected_reach_the_container_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pack is not only counted in the record: its lines are in the prompt the agent gets."""
    box = make_box(tmp_path)
    record = (
        "- t-0 | 2026-01-01T00:00:00Z | closed | pass | bugfix | прошлая задача про core | "
        "touches src/click/core.py | t-0/20260101T000000Z -> "
        "tabularium/t-0/dispatch-20260101T000000Z.json"
    )
    payload = request_for(box)
    payload["context"] = {
        "schema_version": 1,
        "records_digest": "sha256:" + "a" * 64,
        "selected": ["t-0"],
        "omitted": {},
        "lines": ["CONTEXT (assembled by code from the box's records, not by a search):", record],
    }
    monkeypatch.setenv("FAKE_DOCKER_AGENT", DIFF)
    code, _answer = run(monkeypatch, capsys, payload)
    assert code == 0
    asked = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert record in asked
    assert "touches src/click/core.py" in asked


def test_a_request_without_a_pack_has_no_context_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_AGENT", DIFF)
    code, _ = run(monkeypatch, capsys, request_for(box))
    assert code == 0
    asked = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "Контекст, собранный коробкой" not in asked


def test_the_context_head_says_where_the_pointers_resolve() -> None:
    """A pointer printed from the box root must name the container path, or it is a dead end."""
    assert "tabularium/<задача>/<метка>" in executor.CONTEXT_HEAD
    assert "/work/tabularium/<задача>/<метка>" in executor.CONTEXT_HEAD


def test_the_card_is_read_from_the_box_and_hashed(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    card = box.root / "persona" / "environment.md"
    card.write_text("# Среда\n- проект probe\n", encoding="utf-8")
    read = executor._card(box.root)
    assert read is not None
    text, digest = read
    assert text.startswith("# Среда") and "probe" in text
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_two_tasks_share_the_card_and_the_rules_before_the_task() -> None:
    card = "# Среда\n- проект probe\n- проверка: pytest\n"
    first = executor._prompt(
        {"goal": "первая", "kind": "docs", "paths": ["docs/**"], "expected": "раз"},
        card=card,
    )
    second = executor._prompt(
        {"goal": "вторая", "kind": "bugfix", "paths": ["src/**"], "expected": "два"},
        card=card,
    )
    head = first.split("Задача:", 1)[0]
    assert head == second.split("Задача:", 1)[0]
    assert card.strip() in head
    assert "первая" not in head and "вторая" not in head


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


def _session_store(box: boxes.Box, *, tokens: int, cents: float, started: float) -> Path:
    """A box whose agent left a bill in its own session store, as a killed turn does."""
    import sqlite3

    (box.root / "cella").mkdir(parents=True, exist_ok=True)
    path = box.root / "cella" / "state.db"
    db = sqlite3.connect(path)
    db.execute(
        "create table session_model_usage (session_id text, model text, billing_provider text, "
        "billing_base_url text, billing_mode text, task text, api_call_count integer, "
        "input_tokens integer, output_tokens integer, cache_read_tokens integer, "
        "cache_write_tokens integer, reasoning_tokens integer, estimated_cost_usd real, "
        "actual_cost_usd real, cost_status text, cost_source text, first_seen real, "
        "last_seen real)"
    )
    db.execute(
        "insert into session_model_usage (session_id, model, api_call_count, input_tokens, "
        "output_tokens, cache_read_tokens, estimated_cost_usd, first_seen, last_seen) "
        "values ('local-x', 'deepseek-flash', 3, ?, 40, 4000, ?, ?, ?)",
        (tokens, cents / 100, started, started),
    )
    db.commit()
    db.close()
    return path


def test_a_failed_call_names_what_it_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    box = make_box(tmp_path)
    _session_store(box, tokens=900, cents=0.05, started=time.time())
    monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")
    code, answer = run(monkeypatch, capsys, request_for(box))
    assert code == 1
    assert answer["billing_status"] == "billed"
    assert answer["usage"] == {
        "prompt_tokens": 900,
        "completion_tokens": 40,
        "cost_cents": 0.05,
    }


def test_a_failed_call_without_a_bill_is_still_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")
    code, answer = run(monkeypatch, capsys, request_for(box))
    assert code == 1
    assert answer["billing_status"] == "unknown"
    assert "usage" not in answer


def test_the_agents_own_usage_file_wins_over_the_session_store(tmp_path: Path) -> None:
    import time

    box = make_box(tmp_path)
    _session_store(box, tokens=900, cents=0.05, started=time.time())
    usage = box.root / "cella" / ".cache" / "executor-usage.json"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text(
        json.dumps({"input_tokens": 10, "output_tokens": 2, "estimated_cost_usd": 0.001}),
        encoding="utf-8",
    )
    bill, billing = executor._bill(usage, box.root, time.time())
    assert billing == "billed"
    assert bill == {"prompt_tokens": 10, "completion_tokens": 2, "cost_cents": 0.1}
