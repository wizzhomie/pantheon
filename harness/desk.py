"""The desk: where the box's agent hands work over, and where the verdict comes back.

The agent inside the container can read the project and the evidence, and it writes its own
memory and skills. It cannot write the project, because the only writer there is Caesar's gate,
and it cannot run the harness, because the harness is the host's. What it can do is *write a
task*: the same five-point contract a person writes, in the same file, in the box's own
``mandata``. This module is the small, honest corridor between those two facts.

A draft is a task file, nothing added: ``desk/inbox/<id>.toml`` is read with the harness's own
``read_task``, so an unknown key, a missing scope or a made-up kind is refused by the same code
that refuses a person's task, in the same words. A draft that passes intake is copied byte for
byte into ``mandata/`` — the agent's words reach the gate unrewritten — and the draft moves to
``desk/taken/``. A draft in ``desk/inbox/run/`` asks for the work to be dispatched at once,
through the page's own lane: the same ``task.run`` act a button would press.

Every answer is written back to ``desk/outbox/`` so the agent reads the verdict in the same
place it wrote the contract. The desk never judges work: the intake gate refuses, the box's
contours decide, and the record says what happened. Nothing here can widen a task's scope, and
nothing here runs a task the owner's gate would not have run.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import augustus
from . import box as boxes

DIR = Path("desk")
INBOX = DIR / "inbox"
RUN = INBOX / "run"
OUTBOX = DIR / "outbox"
TAKEN = DIR / "taken"
REFUSED = DIR / "refused"
"""The desk's five places: what waits, what asks to run, what was answered, taken, refused."""

SWEEP_SECONDS = 3.0
"""How often the page's watcher reads the desk: a contract becomes a task within seconds."""

SETTLE_SECONDS = 10.0
"""A draft younger than this that does not parse is a file still being written, not a mistake:
wait for it instead of turning a half-written contract into a refusal."""

MAX_LINES = 6
"""How much of a refusal a receipt keeps: the gate says more, the record keeps the rest."""

SKILL_DIR = Path("cella") / "skills" / "pantheon"
SKILL_FILE = SKILL_DIR / "contracts" / "SKILL.md"
SKILL_ABOUT = SKILL_DIR / "DESCRIPTION.md"


@dataclass(frozen=True, slots=True)
class Receipt:
    """What the desk answered about one draft: the gate's words, and where the task went."""

    id: str
    at: str
    ok: bool
    run: bool
    draft: str
    lines: tuple[str, ...]
    task: str | None = None

    def json(self) -> dict[str, Any]:
        return {
            "kind": "contract",
            "id": self.id,
            "at": self.at,
            "ok": self.ok,
            "run": self.run,
            "draft": self.draft,
            "lines": list(self.lines),
            "task": self.task,
        }


def ensure(box: boxes.Box) -> None:
    """Make the five places, so the agent's mount has somewhere to write before it does."""
    for place in (INBOX, RUN, OUTBOX, TAKEN, REFUSED):
        (box.root / place).mkdir(parents=True, exist_ok=True)


def sweep(box: boxes.Box) -> tuple[Receipt, ...]:
    """Read every waiting draft, promote what the gate accepts, answer all of them."""
    ensure(box)
    found: list[Receipt] = []
    for path, wants_run in _drafts(box):
        receipt = _take(box, path, wants_run)
        if receipt is None:
            continue
        found.append(receipt)
    return tuple(found)


def recent(box: boxes.Box, limit: int = 12) -> list[dict[str, Any]]:
    """The last answers, newest first: the page shows these, the agent reads the same files."""
    if not (box.root / OUTBOX).is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted((box.root / OUTBOX).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            data["file"] = path.name
            rows.append(data)
    rows.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    return rows[:limit]


def record(
    box: boxes.Box,
    task_id: str,
    *,
    ok: bool,
    sentence: str,
    lines: Sequence[str],
    cents: float | None = None,
) -> Path:
    """Write the verdict of a delegated run where the agent wrote the contract."""
    payload = {
        "kind": "run",
        "id": task_id,
        "at": _stamp(),
        "ok": ok,
        "sentence": sentence,
        "lines": list(lines)[:MAX_LINES],
        "cents": cents,
    }
    ensure(box)
    path = box.root / OUTBOX / f"{task_id}.run.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def install_skill(box: boxes.Box) -> None:
    """Give the agent the one thing it cannot guess: where to put a contract, and in what shape.

    Written once, never overwritten: the skill is the agent's own to edit, and re-running ``up``
    must not undo what it learned to say.
    """
    if (box.root / SKILL_FILE).is_file():
        return
    (box.root / SKILL_DIR / "contracts").mkdir(parents=True, exist_ok=True)
    (box.root / SKILL_ABOUT).write_text(SKILL_ABOUT_TEXT, encoding="utf-8")
    (box.root / SKILL_FILE).write_text(SKILL_TEXT, encoding="utf-8")


def _drafts(box: boxes.Box) -> list[tuple[Path, bool]]:
    """Every waiting draft: ``inbox/run`` asks for the work, ``inbox`` only for the task."""
    found: list[tuple[Path, bool]] = []
    for path in sorted((box.root / RUN).glob("*.toml")):
        found.append((path, True))
    for path in sorted((box.root / INBOX).glob("*.toml")):
        found.append((path, False))
    return found


def _take(box: boxes.Box, path: Path, wants_run: bool) -> Receipt | None:
    at = _stamp()
    if not _parses(path) and _fresh(path):
        # A file that does not parse yet is a file still being written: wait for it.
        return None
    try:
        task = augustus.read_task(path)
    except boxes.HarnessError as error:
        return _refused(box, path, at, wants_run, path.stem, [str(error)])
    target = box.root / "mandata" / f"{task.id}.toml"
    if target.exists():
        return _refused(
            box,
            path,
            at,
            wants_run,
            task.id,
            [
                f"задача {task.id} уже в коробке ({_relative(box, target)}): id занят. "
                "Задача замораживается при записи, поэтому правка — это новая задача с новым id "
                "и полем parent (через `pantheon amend`), а не перезапись старой."
            ],
        )
    readiness = augustus.intake(task)
    if not readiness.ready:
        return _refused(box, path, at, wants_run, task.id, list(readiness.refusals))
    target.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, not a re-render: what the agent wrote is what the gate reads.
    target.write_bytes(path.read_bytes())
    _move(path, box.root / TAKEN)
    lines = [f"задача {task.id} записана: {_relative(box, target)}"]
    if readiness.gaps:
        lines.extend("пробел: " + gap for gap in readiness.gaps)
    lines.append("запуск попрошен" if wants_run else "запуск — отдельный гейт (task.run)")
    receipt = Receipt(
        id=task.id,
        at=at,
        ok=True,
        run=wants_run,
        draft=path.name,
        lines=tuple(lines[:MAX_LINES]),
        task=_relative(box, target),
    )
    _write(box, receipt)
    _event(box, task.id, "desk.taken", f"контракт агента принят: {_relative(box, target)}")
    return receipt


def _refused(
    box: boxes.Box, path: Path, at: str, wants_run: bool, identifier: str, lines: Sequence[str]
) -> Receipt:
    """A draft the gate will not hand on: say why, keep it, and let the agent write the next one."""
    _move(path, box.root / REFUSED)
    receipt = Receipt(
        id=identifier,
        at=at,
        ok=False,
        run=wants_run,
        draft=path.name,
        lines=tuple(lines[:MAX_LINES]),
    )
    _write(box, receipt)
    _event(
        box,
        identifier,
        "desk.refused",
        "контракт агента отклонён до записи: " + "; ".join(lines[:2]),
    )
    return receipt


def _write(box: boxes.Box, receipt: Receipt) -> None:
    path = box.root / OUTBOX / f"{receipt.id}.json"
    path.write_text(
        json.dumps(receipt.json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _event(box: boxes.Box, task_id: str, kind: str, detail: str) -> None:
    """One line in the ledger: the desk is an actor, and an actor leaves a trace."""
    try:
        augustus.event(
            box.root,
            datetime.now(UTC).replace(microsecond=0),
            kind,
            task=task_id,
            detail=detail,
            actor="desk",
            links=(f"desk/outbox/{task_id}.json",),
        )
    except Exception:  # the ledger must never cost the contract it was recording
        return


def _parses(path: Path) -> bool:
    try:
        tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    return True


def _fresh(path: Path) -> bool:
    try:
        age = datetime.now(UTC).timestamp() - path.stat().st_mtime
    except OSError:
        return False
    return age < SETTLE_SECONDS


def _move(path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    try:
        shutil.move(str(path), str(target))
    except OSError:
        return


def _relative(box: boxes.Box, path: Path) -> str:
    try:
        return path.relative_to(box.root).as_posix()
    except ValueError:
        return str(path)


def _stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


SKILL_ABOUT_TEXT = """\
# Пантеон

Как этот агент передаёт работу харнессу Пантеона: задачами-контрактами, а не прозой.
"""

SKILL_TEXT = """\
---
name: pantheon-contracts
description: Передать работу харнессу Пантеона контрактом.
---

# Контракты Пантеона

Ты живёшь в коробке Пантеона. Проект (`/work/project`) ты только читаешь, и это не
ограничение свободы, а устройство: в проект пишет единственный гейт (Caesar), а не ты.
Твои руки снаружи коробки — задачи. Задача — это файл-контракт, который харнесс принимает
по своим правилам и исполняет в своём контуре.

## Куда класть

- `/work/desk/inbox/<id>.toml` — «запиши задачу». Харнесс проверит контракт, положит его
  в `mandata/` коробки и ответит в `/work/desk/outbox/<id>.json`. Запуска не будет.
- `/work/desk/inbox/run/<id>.toml` — «запиши и запусти». Если контракт полный, задача
  уходит в контуры сразу (это платный ход: модель-исполнитель получит контракт).
- `/work/desk/outbox/` — ответы. Контракт и его вердикт (`<id>.run.json`) читаются здесь.
- `/work/desk/taken/` — принятые контракты; `/work/desk/refused/` — отклонённые.

## Формат

Ровно формат задачи Пантеона, без своих полей. Файл становится задачей только если проходит
`intake`: контракт назван, ожидание названо.

```toml
schema_version = 1
id = "t-20261128-short-name"
kind = "feature"                  # docs | hotfix | bugfix | feature | architecture | security
goal = "что нужно сделать, одним абзацем"
expected = "что будет видно, когда сделано, и как это проверить"
paths = ["tests/test_name.py"]    # что входит в область; пусто — отказ
self_check = ["python", "-m", "pytest", "tests/test_name.py", "-q"]
# repro = [...]                   # скрытая проверка: красная до, зелёная после; без неё — пробел

[budget]
max_cents = 5
max_seconds = 900

[contract]
symptom = "что происходит сейчас и почему это плохо"
visible = "что человек увидит, когда станет правильно"
boundaries = "что не меняем: файлы, поведение, данные"
```

`missing` — только если факт действительно неизвестен: контракт с `missing` будет отклонён
с требованием спросить владельца у него в чате. Не выдумывай проверок и не расширяй область:
область — это обещание, а задача замораживается при записи.

`id` свободный, но не занятый: `mandata/<id>.toml` уже есть — отказ, правка делается новой
задачей с полем `parent` и `why`.

## Что отвечает харнесс

В `desk/outbox/<id>.json`: `{"ok": true|false, "lines": [...], "task": "mandata/<id>.toml"}`.
`ok: false` — гейт отказал, в `lines` его собственные слова (обычно чего не хватает).
Запущенная задача пишет вердикт в `desk/outbox/<id>.run.json`: `ok`, `sentence`, `lines`,
`cents`. Между записью и вердиктом проходит время: не спрашивай одно и то же дважды, ответ
появится в файле.

## Как работать

1. Прочитал проект и доктрину (`/work/docs`, `/work/harness`) — сформулировал контракт.
2. Написал файл в `inbox/` (или `inbox/run/`, если нужен запуск).
3. Прочитал ответ в `outbox/`; при отказе — исправил контракт и написал новый файл.
4. Вердикт вернулся — он и есть результат субагента; дальше решение твоё.
"""
