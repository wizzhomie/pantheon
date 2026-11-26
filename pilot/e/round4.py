"""E, round 4: the level-2 boundary exercised live — steps on e-r2.

The protocol is in README.md, written before the call. The task is the one that failed as a
one-shot: three declared paths, ~86 changed lines in scope, and a provider cut at the token
cap. It is level 2 by the box's own policy only when it carries a visible check, so the stand
adds one: a test that fails on the base tree and passes only when the cut answer stops
reaching the project. The run records what the level did — the steps, the money, the verdict.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import round2 as r2  # noqa: E402
import run as base  # noqa: E402

from harness import box as boxes  # noqa: E402
from harness import cli  # noqa: E402

WORK = Path("/tmp/e2")
base.WORK = WORK
PYTHON = base.PYTHON
PRICE_CENTS_CEILING = 15.0
CALL_CEILING = 5.0

VISIBLE = "tests/test_executor.py::test_a_cut_answer_never_reaches_the_project"
VISIBLE_TEXT = (HERE / "fixtures" / "r2_visible_test.py").read_text(encoding="utf-8")

CASE: dict[str, object] = {
    "tree": WORK / "base-r2",
    "base": "eb3c917",
    "kind": "bugfix",
    "paths": [
        "harness/executor.py",
        "harness/executors/deepseek.py",
        "tests/test_executor.py",
    ],
    "goal": (
        "Когда провайдер обрывает ответ на потолке токенов (finish_reason=length), "
        "ответ нельзя применять: диф может примениться и пройти проверки, будучи "
        "половиной правки. Помечай такой ответ truncated=true; предложение записывай "
        "как incomplete, сохраняя патч и цену (usage), но не применяя его. Проверка "
        "прямой записи в проект по-прежнему важнее: исполнитель, писавший в проект мимо "
        "ворот, называется этим, а не обрезанным ответом. Добавь тесты на оба поведения."
    ),
    "expected": (
        "ответ с finish_reason=length помечен truncated; предложение incomplete; проект "
        "не изменён; цена сохранена"
    ),
    "budget": 15,
    "hidden_fix": "fececb2",
    "hidden_test": "tests/test_executor.py",
    "hidden_nodes": [
        "tests/test_executor.py::test_an_answer_cut_at_the_cap_is_recorded_not_applied",
        "tests/test_executor.py::test_a_diff_that_hit_the_cap_is_marked_truncated",
    ],
}


def run_tree(command: list[str], tree: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(tree),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(tree), "PYTHONDONTWRITEBYTECODE": "1"},
    )


def prepare(case: dict[str, object]) -> tuple[Path, str]:
    """Base commit plus the stand's visible check, committed so a diff means the model's."""
    tree = Path(case["tree"])
    base.reset(tree, str(case["base"]))
    venv = tree / "trajan" / ".venv"
    if (venv.is_symlink() or venv.exists()) and not (venv / "bin" / "python").exists():
        venv.unlink()
    if not venv.exists():
        venv.symlink_to(ROOT / "trajan" / ".venv")
    test = tree / "tests" / "test_executor.py"
    test.write_text(test.read_text(encoding="utf-8") + "\n\n" + VISIBLE_TEXT, encoding="utf-8")
    for args in (
        ("add", "-A"),
        ("commit", "-qm", "the stand's visible check"),
    ):
        subprocess.run(
            ["git", "-C", str(tree), "-c", "user.email=t@t", "-c", "user.name=t", *args],
            check=True,
        )
    head = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return tree, head


def visible_check(tree: Path) -> tuple[int, str]:
    done = run_tree([PYTHON, "-m", "pytest", "-q", VISIBLE], tree)
    tail = (done.stdout + done.stderr).strip().splitlines()[-1:]
    return done.returncode, (tail[0][:300] if tail else f"exit {done.returncode}")


def task_toml(name: str) -> str:
    paths = ", ".join(json.dumps(str(item), ensure_ascii=False) for item in CASE["paths"])  # type: ignore[union-attr]
    return (
        "schema_version = 1\n"
        f"id = {json.dumps(name, ensure_ascii=False)}\n"
        f"goal = {json.dumps(str(CASE['goal']), ensure_ascii=False)}\n"
        'kind = "bugfix"\n'
        f"paths = [{paths}]\n"
        f"expected = {json.dumps(str(CASE['expected']), ensure_ascii=False)}\n"
        'shape = "multi"\n'
        f"self_check = [{json.dumps('python')}, {json.dumps('-m')}, "
        f"{json.dumps('pytest')}, {json.dumps('-q')}, {json.dumps(VISIBLE)}]\n"
        "max_steps = 3\n"
        "\n[budget]\n"
        "max_cents = 15\n"
        "max_seconds = 900\n"
    )


def proposal_records(box: boxes.Box, task_id: str) -> list[dict[str, object]]:
    folder = box.root / "tabularium" / task_id
    records = []
    for path in sorted(folder.glob("proposal-*/proposal.json")):
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def record(payload: dict[str, object]) -> None:
    line = {"round": 4, "case": "e-r2", "mode": "L2", **payload}
    out = WORK / "results-round4.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(json.dumps(line, ensure_ascii=False))


SPENT = 0.0


def _charge(cents: float) -> None:
    global SPENT
    SPENT += max(0.0, cents)


def main() -> int:
    if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("PANTHEON_MODEL_KEY")):
        print("no key: source castra/trajan/cella/.env first", file=sys.stderr)
        return 2
    tree, prepared = prepare(CASE)

    dry: dict[str, object] = {"prepared": prepared}
    code, tail = visible_check(tree)
    dry["visible_before"] = {"passed": code == 0, "tail": tail}
    hidden_before, hidden_before_note = r2.hidden_check(CASE, tree)
    dry["hidden_before"] = {"passed": hidden_before, "tail": hidden_before_note[-300:]}
    changed_before = base.diff_names(tree, prepared)
    dry["changed_before"] = changed_before
    print(json.dumps({"dry": dry}, ensure_ascii=False), file=sys.stderr)
    (WORK / "dry-round4.json").write_text(json.dumps(dry, ensure_ascii=False, indent=2) + "\n")
    if code == 0 or hidden_before or changed_before:
        print("the stand is not ready: the visible check must fail on the base and the tree "
              "must be clean", file=sys.stderr)
        return 3

    work = Path(tempfile.mkdtemp(prefix="e4-e-r2-"))
    castra = work / "castra"
    box = boxes.new_box(castra, tree, name="e4-e-r2")
    (box.root / "persona" / "executor.toml").write_text(
        base.PROFILE.format(command=ROOT / "bin" / "pantheon-executor", cents=CASE["budget"]),
        encoding="utf-8",
    )
    task_path = work / "e-r2-l2.toml"
    task_path.write_text(task_toml("e-r2-l2"), encoding="utf-8")

    buffer = io.StringIO()
    code = 0
    started = time.monotonic()
    saved = {
        key: os.environ.get(key)
        for key in (
            "PYTHON",
            "PYTHONPATH",
            "PATH",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
            "PANTHEON_MODEL_THINKING",
        )
    }
    os.environ["PYTHON"] = PYTHON
    os.environ["PYTHONPATH"] = str(tree)
    os.environ["PATH"] = f"{ROOT / 'trajan' / '.venv' / 'bin'}:{os.environ['PATH']}"
    try:
        with redirect_stdout(buffer):
            code = cli.main(
                ["--castra", str(castra), "dispatch", box.name, "--task", str(task_path), "--json"]
            )
    except Exception as error:  # the experiment records failures
        code = 2
        buffer.write(f"error: {error}")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    seconds = round(time.monotonic() - started, 2)
    raw = buffer.getvalue()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    proposals = proposal_records(box, "e-r2-l2")
    cost = round(sum(float((item.get("usage") or {}).get("cost_cents") or 0) for item in proposals), 4)
    _charge(cost)
    solved, hidden_note = r2.hidden_check(CASE, tree)
    after_code, after_tail = visible_check(tree)
    changed = base.diff_names(tree, prepared)
    state = str(payload.get("state") or ("error" if code == 2 else "unknown"))
    result: dict[str, object] = {
        "solved": solved,
        "false_pass": after_code == 0 and not solved,
        "visible_before": dry["visible_before"],
        "visible_after": {"passed": after_code == 0, "tail": after_tail},
        "hidden_before": dry["hidden_before"],
        "applied": bool(changed),
        "changed": changed,
        "cost_cents": cost,
        "seconds": seconds,
        "calls": len(proposals),
        "statuses": [item.get("status") for item in proposals],
        "level": payload.get("level"),
        "route": payload.get("route"),
        "engine": payload.get("engine"),
        "state": state,
        "exit_code": payload.get("exit_code", code),
        "stop_reason": payload.get("stop_reason"),
        "steps": payload.get("steps"),
        "self_check": payload.get("self_check"),
        "note": hidden_note if solved else f"state {state}; {hidden_note}".strip("; "),
        "answer": raw[:400],
    }
    record(result)
    if SPENT > PRICE_CENTS_CEILING:
        raise RuntimeError(f"the round-4 ceiling is reached: {SPENT:.2f} cents")
    print(f"spent around {SPENT:.2f} cents", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
