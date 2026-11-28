"""The desk: the agent's contracts become tasks, and the gate's answer comes back.

The desk has one job — carry a contract written inside the box to the gate a person's task
passes, and carry the answer back. These tests are about that honesty: the bytes are not
rewritten, a refusal is the gate's own sentence, a taken id is not a slot to overwrite, and a
file still being written is not a mistake.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from harness import box as boxes
from harness import cli, desk
from harness.ui import actions, server

TASK = """\
schema_version = 1
id = "t-probe-1"
kind = "bugfix"
goal = "строку в отчёте не видно, потому что она теряется при сборке"
expected = "строка появляется в отчёте, и тест на неё зелёный"
paths = ["tests/test_probe.py"]
self_check = ["python", "-m", "pytest", "tests/test_probe.py", "-q"]

[budget]
max_cents = 3

[contract]
symptom = "поле теряется при сборке отчёта"
visible = "поле на месте, тест краснеет, если строку убрать"
boundaries = "только tests/test_probe.py"
"""


def make_box(tmp_path: Path) -> boxes.Box:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("demo\n", encoding="utf-8")
    return boxes.new_box(tmp_path / "castra", project)


def wait_for(path: Path, seconds: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.is_file():
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return data
        time.sleep(0.02)
    raise AssertionError(f"no file at {path} within {seconds} s")


def test_a_contract_becomes_a_task_word_for_word(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    desk.ensure(box)
    draft = box.root / desk.INBOX / "t-probe-1.toml"
    draft.write_text(TASK, encoding="utf-8")
    receipts = desk.sweep(box)
    assert len(receipts) == 1
    assert receipts[0].ok is True and receipts[0].id == "t-probe-1"
    assert (box.root / "mandata" / "t-probe-1.toml").read_text(encoding="utf-8") == TASK
    assert not draft.exists()
    assert (box.root / desk.TAKEN / "t-probe-1.toml").is_file()
    answer = wait_for(box.root / desk.OUTBOX / "t-probe-1.json")
    assert answer["ok"] is True
    assert "t-probe-1" in " ".join(answer["lines"])
    events = list((box.root / "fasti").glob("*.jsonl"))
    assert any("desk.taken" in path.read_text(encoding="utf-8") for path in events)


def test_a_contract_without_the_owner_words_is_refused(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    desk.ensure(box)
    (box.root / desk.INBOX / "t-probe-1.toml").write_text(
        TASK.replace("[contract]\n", "[other]\n"), encoding="utf-8"
    )
    receipts = desk.sweep(box)
    assert receipts[0].ok is False
    assert "contract" in " ".join(receipts[0].lines)
    assert not (box.root / "mandata" / "t-probe-1.toml").exists()
    assert (box.root / desk.REFUSED / "t-probe-1.toml").is_file()
    answer = wait_for(box.root / desk.OUTBOX / "t-probe-1.json")
    assert answer["ok"] is False


@pytest.mark.parametrize("gap", ["expected"])
def test_the_expected_behaviour_is_the_gate_s_question(tmp_path: Path, gap: str) -> None:
    box = make_box(tmp_path)
    desk.ensure(box)
    text = "\n".join(line for line in TASK.splitlines() if not line.startswith(gap + " ="))
    (box.root / desk.INBOX / "t-probe-1.toml").write_text(text, encoding="utf-8")
    receipts = desk.sweep(box)
    assert receipts[0].ok is False


def test_a_taken_id_is_not_a_slot_to_overwrite(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    desk.ensure(box)
    earlier = box.root / "mandata" / "t-probe-1.toml"
    earlier.parent.mkdir(parents=True, exist_ok=True)
    earlier.write_text("schema_version = 1\nid = 't-probe-1'\n", encoding="utf-8")
    (box.root / desk.INBOX / "t-probe-1.toml").write_text(TASK, encoding="utf-8")
    receipts = desk.sweep(box)
    assert receipts[0].ok is False
    assert "уже в коробке" in " ".join(receipts[0].lines)
    assert earlier.read_text(encoding="utf-8").startswith("schema_version")


def test_a_run_draft_asks_for_the_run(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    desk.ensure(box)
    (box.root / desk.RUN / "t-probe-1.toml").write_text(TASK, encoding="utf-8")
    receipts = desk.sweep(box)
    assert receipts[0].ok is True
    assert receipts[0].run is True
    assert receipts[0].task == "mandata/t-probe-1.toml"


def test_a_file_still_being_written_is_left_alone(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    desk.ensure(box)
    draft = box.root / desk.INBOX / "t-probe-1.toml"
    draft.write_text("schema_version = 1\nid = ", encoding="utf-8")
    assert desk.sweep(box) == ()
    assert draft.is_file()
    old = time.time() - desk.SETTLE_SECONDS - 5
    os.utime(draft, (old, old))
    receipts = desk.sweep(box)
    assert receipts[0].ok is False
    assert not draft.exists()


def test_the_skill_is_written_once_and_never_rewritten(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    (box.root / "cella").mkdir(parents=True, exist_ok=True)
    desk.install_skill(box)
    skill = box.root / desk.SKILL_FILE
    assert "desk/inbox/run" in skill.read_text(encoding="utf-8")
    skill.write_text("мои слова\n", encoding="utf-8")
    desk.install_skill(box)
    assert skill.read_text(encoding="utf-8") == "мои слова\n"


def test_a_run_verdict_lands_beside_the_contract(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    path = desk.record(
        box,
        "t-probe-1",
        ok=True,
        sentence="Задача t-probe-1: closed.",
        lines=("state closed",),
        cents=1.5,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["kind"] == "run" and data["ok"] is True and data["cents"] == 1.5
    assert desk.recent(box)[0]["id"] == "t-probe-1"


def test_the_command_reads_the_desk(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    box = make_box(tmp_path)
    desk.ensure(box)
    (box.root / desk.INBOX / "t-probe-1.toml").write_text(TASK, encoding="utf-8")
    assert cli.main(["--castra", str(box.root.parent), "desk", box.name]) == 0
    printed = capsys.readouterr().out
    assert "t-probe-1" in printed and "взято" in printed
    assert (box.root / "mandata" / "t-probe-1.toml").is_file()


def test_the_command_says_nothing_waits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    box = make_box(tmp_path)
    assert cli.main(["--castra", str(box.root.parent), "desk", box.name]) == 0
    assert "nothing waiting" in capsys.readouterr().out


def test_the_page_takes_a_run_draft_and_reports_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = make_box(tmp_path)
    desk.ensure(box)
    asked: list[tuple[str, dict[str, str]]] = []

    def fake_run(box: boxes.Box, action: str, params: dict[str, str]) -> actions.Result:
        asked.append((action, params))
        return actions.Result(
            action=action,
            sentence="Задача t-probe-1: closed.",
            lines=("state closed",),
            ok=True,
        )

    monkeypatch.setattr(actions, "run", fake_run)
    screen = server.build(tmp_path / "castra", box.name, "127.0.0.1", 0, "token")
    try:
        (box.root / desk.RUN / "t-probe-1.toml").write_text(TASK, encoding="utf-8")
        screen._sweep(box)
        verdict = wait_for(box.root / desk.OUTBOX / "t-probe-1.run.json")
        assert verdict["ok"] is True
        assert verdict["kind"] == "run"
        assert asked == [("task.run", {"task": "t-probe-1"})]
    finally:
        screen.server_close()


def test_a_task_run_without_a_task_file_says_which(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    with pytest.raises(boxes.HarnessError, match="no task 'nope'"):
        actions.run(box, "task.run", {"task": "nope"})
