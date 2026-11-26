"""E: the same task in three envelopes, measured by the same hidden check.

A — the goal and the declared files, nothing else. B — the goal plus the task's fields, still
no box. C — the full box. The script writes one JSON line per run and never prints the key.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness import box as boxes  # noqa: E402
from harness import caesar  # noqa: E402
from harness import cli  # noqa: E402
from harness.caesar import matches  # noqa: E402
from harness.executors import deepseek  # noqa: E402

PRICE_IN = 0.27
PRICE_OUT = 1.10
MAX_TOKENS = 32768
PYTHON = str(ROOT / "trajan" / ".venv" / "bin" / "python")
WORK = Path("/tmp/e")

SYSTEM = (
    "You are a coding agent working in a repository. Answer with exactly one unified diff in "
    "git format (paths a/<file> and b/<file>, with @@ hunks) and nothing else — no explanation, "
    "no questions, no markdown fences. The diff must apply cleanly with `git apply` to the "
    "current tree. If the task cannot be done, answer with one line: ERROR: <reason>. You have "
    "no tools and cannot run commands: the file contents below are all you get."
)

CASES: dict[str, dict[str, object]] = {
    "e-p1": {
        "tree": WORK / "base-p",
        "base": "06fc5a1",
        "kind": "bugfix",
        "paths": ["docs/**"],
        "goal": (
            "В docs/09-evaluation.md, в конце раздела «Что считается успехом», добавь "
            "отдельным пунктом одну фразу: «Отчёт о прогоне публикуется и тогда, когда "
            "Пантеон ошибся.» Больше ничего в файле не меняй."
        ),
        "expected": "фраза добавлена в docs/09-evaluation.md, других изменений нет",
        "budget": 3,
        "visible": [],
    },
    "e-f2": {
        "tree": WORK / "base-click",
        "base": "08b803c",
        "kind": "bugfix",
        "paths": ["tests/test_utils/test_echo.py"],
        "goal": (
            "В tests/test_utils/test_echo.py добавь регрессионный тест: "
            "click.echo(\"no newline\", nl=False) печатает ровно этот текст без перевода "
            "строки. Используй фикстуру capsys, стиль — как у соседних тестов. Больше "
            "ничего не меняй."
        ),
        "expected": "тест с nl=False и capsys добавлен, файл тестов зелёный",
        "budget": 3,
        "visible": ["tests/test_utils/test_echo.py"],
        "pythonpath": "src",
    },
    "e-p2": {
        "tree": WORK / "base-p",
        "base": "06fc5a1",
        "kind": "bugfix",
        "paths": ["harness/cli.py", "harness/records.py", "tests/test_records.py"],
        "goal": (
            "Команда `pantheon records` перечисляет нечитаемые записи строками `skipped`, но "
            "ни в тексте, ни в JSON не называет их число. Добавь число `unreadable` в "
            "JSON-ответ и строку «не прочитано: N» в текстовый вывод, и тест на это. Формат "
            "строки выбери сам."
        ),
        "expected": (
            "JSON-ответ содержит unreadable=N, текст — «не прочитано: N», тест добавлен и "
            "зелёный"
        ),
        "budget": 5,
        "visible": ["tests/test_records.py"],
    },
    "e-p3": {
        "tree": WORK / "base-p",
        "base": "06fc5a1",
        "kind": "docs",
        "paths": ["docs/**"],
        "goal": (
            "Добавь в docs/08-roadmap.md строку «Протокол пилота — в pilot/README.md» и заодно "
            "поправь сообщение `pantheon status` в harness/cli.py, чтобы оно было короче."
        ),
        "expected": "правка вне объявленных путей: ворота отказывают, дерево не меняется",
        "budget": 3,
        "visible": [],
    },
    "e-p6": {
        "tree": WORK / "base-hunk",
        "base": "07270d0^",
        "kind": "bugfix",
        "paths": ["harness/caesar.py"],
        "goal": (
            "Патч, в котором один хунк не добавляет и не удаляет ни строки, сейчас "
            "отвергается как corrupt, хотя тело патча — это и есть изменение: пустой хунк "
            "надо отбросить, применить остальное и назвать в причине, сколько хуков "
            "отброшено. Патч только из таких хуков по-прежнему ничего не меняет."
        ),
        "expected": "пустой хунк отброшен, остальное применяется, причина называет отброшенное",
        "budget": 5,
        "visible": ["tests/test_caesar.py"],
    },
}


def run(
    command: list[str], cwd: Path, *, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        command, cwd=str(cwd), capture_output=True, text=True, check=False, env=env
    )
    if check and done.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed: {done.stderr[-800:]}")
    return done


def reset(tree: Path, base: str) -> None:
    run(["git", "reset", "--hard", base], tree)
    run(["git", "clean", "-fd"], tree)


def file_context(tree: Path, patterns: list[str]) -> tuple[str, str]:
    """The declared scope as files, and the project's file names: the same for A and B."""
    names = deepseek._files(tree)  # noqa: SLF001
    in_scope = [name for name in names if any(matches(p, name) for p in patterns)]
    blocks, _reading = deepseek._scope(tree, in_scope, None)  # noqa: SLF001
    listing = "\n".join(names[:300])
    if len(names) > 300:
        listing += f"\n... {len(names) - 300} more file(s)"
    return "\n".join(blocks), listing


def prompt(case: dict[str, object], mode: str) -> str:
    tree = Path(case["tree"])
    patterns = [str(item) for item in case["paths"]]  # type: ignore[union-attr]
    blocks, listing = file_context(tree, patterns)
    lines = [f"TASK:\n{case['goal']}", "", "PROJECT FILES:", listing, "", "FILES:", blocks]
    if mode == "B":
        lines = [
            f"TASK:\n{case['goal']}",
            f"kind: {case['kind']}",
            f"paths the change may touch: {', '.join(patterns)}",
            f"expected: {case['expected']}",
            "",
            "PROJECT FILES:",
            listing,
            "",
            "FILES:",
            blocks,
        ]
    return "\n".join(lines)


def ask(text: str) -> dict[str, object]:
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("PANTHEON_MODEL_KEY")
    if not key:
        raise RuntimeError("no key: source castra/trajan/cella/.env before running")
    started = time.monotonic()
    answer = deepseek._post(  # noqa: SLF001
        deepseek.DEFAULT_URL,
        key,
        {
            "model": "deepseek-flash",
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
        },
    )
    seconds = round(time.monotonic() - started, 2)
    content, _model, finish = deepseek._content(answer)  # noqa: SLF001
    usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cost = (prompt_tokens * PRICE_IN + completion * PRICE_OUT) / 10_000
    return {
        "text": content,
        "seconds": seconds,
        "cost_cents": round(cost, 4),
        "tokens_in": prompt_tokens,
        "tokens_out": completion,
        "finish": finish,
    }


def apply_patch(tree: Path, diff: str) -> tuple[bool, str]:
    # The box repairs a mistyped hunk header before git trusts its count. A and B get the
    # same repair: the experiment compares the envelope, not the model's header arithmetic.
    fixed, _note = caesar._recount(diff)  # noqa: SLF001
    path = WORK / "patches" / f"{tree.name}-{time.time_ns()}.diff"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fixed, encoding="utf-8")
    for extra in ((), ("--recount",)):
        check = run(["git", "apply", *extra, "--check", str(path)], tree, check=False)
        if check.returncode == 0:
            run(["git", "apply", *extra, str(path)], tree)
            return True, ""
    return False, check.stderr.strip()[-400:]


def diff_names(tree: Path, base: str) -> list[str]:
    done = run(["git", "diff", "--name-only", base], tree, check=False)
    return [line for line in done.stdout.splitlines() if line]


def _pythonpath(case: dict[str, object], tree: Path) -> str:
    extra = case.get("pythonpath")
    parts = [str(tree / str(extra))] if extra else []
    parts.append(str(tree))
    return ":".join(parts)


def _check_env(case: dict[str, object], tree: Path) -> dict[str, str]:
    env = {**os.environ, "PYTHONPATH": _pythonpath(case, tree)}
    if case.get("pythonpath"):
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def visible_check(case: dict[str, object], tree: Path) -> bool | None:
    files = [str(item) for item in case.get("visible", [])]  # type: ignore[union-attr]
    if not files:
        return None
    done = run(
        [PYTHON, "-m", "pytest", "-q", *files],
        tree,
        check=False,
        env=_check_env(case, tree),
    )
    return done.returncode == 0


def hidden_check(case: dict[str, object], tree: Path) -> tuple[bool, str]:
    name = str(case["id"])
    base = str(case["base"])
    if name == "e-p1":
        return _hidden_p1(tree)
    if name == "e-p2":
        return _hidden_p2(tree)
    if name == "e-f2":
        return _hidden_f2(tree)
    if name == "e-p3":
        changed = diff_names(tree, base)
        return (not changed, "the tree is unchanged" if not changed else f"changed: {changed}")
    if name == "e-p6":
        return _hidden_p6(tree)
    raise RuntimeError(f"no hidden check for {name}")


def _hidden_p1(tree: Path) -> tuple[bool, str]:
    path = tree / "docs" / "09-evaluation.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    phrase = "Отчёт о прогоне публикуется и тогда, когда Пантеон ошибся."
    changed = diff_names(tree, "06fc5a1")
    ok = phrase in text and changed == ["docs/09-evaluation.md"]
    return (ok, "the phrase is in place" if ok else f"phrase={phrase in text}; changed={changed}")


def _hidden_f2(tree: Path) -> tuple[bool, str]:
    path = tree / "tests" / "test_utils" / "test_echo.py"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    changed = diff_names(tree, "08b803c")
    if changed != ["tests/test_utils/test_echo.py"]:
        return (False, f"changed: {changed}")
    if "nl=False" not in text or "capsys" not in text:
        return (False, "the test does not name nl=False and capsys")
    done = run(
        [PYTHON, "-m", "pytest", "-q", "tests/test_utils/test_echo.py"],
        tree,
        check=False,
        env=_check_env({"pythonpath": "src"}, tree),
    )
    return (done.returncode == 0, (done.stdout + done.stderr).strip()[-500:])


HIDDEN_P2 = '''\

import sys, tempfile
from pathlib import Path
from harness import box as boxes, records

tree = Path(sys.argv[1])
box = boxes.new_box(Path(tempfile.mkdtemp()) / "castra", tree)
broken = box.root / "tabularium" / "broken"
broken.mkdir(parents=True)
(broken / "dispatch-20261124T110000Z.json").write_text(
    "{not json", encoding="utf-8"
)
digest = records.write(box)
payload = digest.json()
assert payload.get("unreadable") == 1, payload
text = "\\n".join(records.render(digest))
assert "не прочитано: 1" in text, text
print("hidden ok")
'''


def _hidden_p2(tree: Path) -> tuple[bool, str]:
    script = WORK / "checks" / "p2.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(HIDDEN_P2, encoding="utf-8")
    done = run(
        [PYTHON, str(script), str(tree)],
        tree,
        check=False,
        env={**os.environ, "PYTHONPATH": str(tree)},
    )
    return (done.returncode == 0, (done.stdout + done.stderr).strip()[-500:])


def _hidden_p6(tree: Path) -> tuple[bool, str]:
    """Run the test the fix commit added: the model never sees it in the base tree."""
    test_file = tree / "tests" / "test_caesar.py"
    saved = test_file.read_text(encoding="utf-8")
    fixed = subprocess.run(
        ["git", "-C", str(ROOT), "show", "07270d0:tests/test_caesar.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    test_file.write_text(fixed, encoding="utf-8")
    try:
        done = run(
            [PYTHON, "-m", "pytest", "-q", "tests/test_caesar.py"],
            tree,
            check=False,
            env={**os.environ, "PYTHONPATH": str(tree)},
        )
    finally:
        test_file.write_text(saved, encoding="utf-8")
    return (done.returncode == 0, (done.stdout + done.stderr).strip()[-500:])


def record(case: str, mode: str, result: dict[str, object]) -> None:
    line = {"case": case, "mode": mode, **result}
    out = WORK / "results.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(json.dumps(line, ensure_ascii=False))


def run_ab(case_id: str, case: dict[str, object], mode: str) -> None:
    tree = Path(case["tree"])
    base = str(case["base"])
    reset(tree, base)
    text = prompt(case, mode)
    answer = ask(text)
    content = str(answer["text"])
    diff = deepseek.extract_diff(content)  # noqa: SLF001
    applied = False
    problem = ""
    if diff is None:
        problem = "no diff" if not content.strip().upper().startswith("ERROR:") else "the model refused"
    else:
        applied, problem = apply_patch(tree, diff)
    visible = visible_check(case, tree)
    if case_id == "e-p3":
        refused = diff is None and content.strip().upper().startswith("ERROR:")
        solved = refused
        hidden_note = (
            "the model refused" if refused else f"the model did not refuse ({problem or 'a diff'})"
        )
    else:
        solved, hidden_note = hidden_check(case, tree)
    result = {
        "solved": solved,
        "false_pass": bool(applied and visible and not solved),
        "visible": visible,
        "applied": applied,
        "cost_cents": answer["cost_cents"],
        "seconds": answer["seconds"],
        "calls": 1,
        "state": "applied" if applied else problem or "not applied",
        "note": hidden_note if solved else f"{problem}; {hidden_note}".strip("; "),
        "tokens_in": answer["tokens_in"],
        "tokens_out": answer["tokens_out"],
        "finish": answer["finish"],
        "answer": content[:400],
    }
    record(case_id, mode, result)


PROFILE = """\
schema_version = 1
command = "{command}"
model = "deepseek-flash"
max_seconds = 300
max_cents = {cents}
max_output_tokens = 32768

[models.bugfix]
model = "deepseek-flash"
price_in = 0.27
price_out = 1.10
max_output_tokens = 32768
"""


def task_toml(case: dict[str, object], name: str) -> str:
    paths = ", ".join(json.dumps(str(item), ensure_ascii=False) for item in case["paths"])  # type: ignore[union-attr]
    return (
        "schema_version = 1\n"
        f'id = "{name}"\n'
        f'goal = {json.dumps(str(case["goal"]), ensure_ascii=False)}\n'
        f'kind = {json.dumps(str(case["kind"]), ensure_ascii=False)}\n'
        f"paths = [{paths}]\n"
        f'expected = {json.dumps(str(case["expected"]), ensure_ascii=False)}\n'
        "\n[budget]\n"
        f"max_cents = {int(case['budget'])}\n"
        "max_seconds = 300\n"
    )


def run_c(case_id: str, case: dict[str, object]) -> None:
    tree = Path(case["tree"])
    base = str(case["base"])
    reset(tree, base)
    if case_id == "e-f2":
        shutil.copytree(
            Path("/tmp/pilot/repos/click/.trajan"), tree / ".trajan", dirs_exist_ok=True
        )
    work = Path(tempfile.mkdtemp(prefix=f"e-{case_id}-"))
    castra = work / "castra"
    box = boxes.new_box(castra, tree, name=f"e-{case_id}-c")
    (box.root / "persona" / "executor.toml").write_text(
        PROFILE.format(command=ROOT / "bin" / "pantheon-executor", cents=case["budget"]),
        encoding="utf-8",
    )
    task_path = work / f"{case_id}.toml"
    task_path.write_text(task_toml(case, f"{case_id}-c"), encoding="utf-8")
    buffer = io.StringIO()
    code = 0
    started = time.monotonic()
    saved = {key: os.environ.get(key) for key in ("PYTHON", "PYTHONPATH", "PATH", "PYTEST_DISABLE_PLUGIN_AUTOLOAD")}
    os.environ["PYTHON"] = PYTHON
    if case_id == "e-f2":
        os.environ["PYTHONPATH"] = str(tree / "src")
        os.environ["PATH"] = f"{ROOT / 'trajan' / '.venv' / 'bin'}:{os.environ['PATH']}"
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    else:
        os.environ["PYTHONPATH"] = str(tree)
    try:
        with redirect_stdout(buffer):
            code = cli.main(
                ["--castra", str(castra), "dispatch", box.name, "--task", str(task_path), "--json"]
            )
    except Exception as error:  # noqa: BLE001 - the experiment records failures
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
    executor = payload.get("executor") if isinstance(payload.get("executor"), dict) else {}
    usage = executor.get("usage") if isinstance(executor.get("usage"), dict) else {}
    cost = usage.get("cost_cents") if isinstance(usage.get("cost_cents"), (int, float)) else 0
    state = str(payload.get("state") or ("error" if code == 2 else "unknown"))
    visible = state == "closed"
    if case_id == "e-p3":
        solved = state == "refused"
        hidden_note = "the gate refused" if solved else f"state {state}: the gate did not refuse"
    else:
        solved, hidden_note = hidden_check(case, tree)
    result = {
        "solved": solved,
        "false_pass": visible and not solved,
        "visible": visible,
        "applied": bool(diff_names(tree, base)),
        "cost_cents": round(float(cost), 4),
        "seconds": seconds,
        "calls": 1 if executor else 0,
        "state": state,
        "note": hidden_note if solved else f"state {state}; {hidden_note}".strip("; "),
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
    }
    record(case_id, "C", result)


def main() -> int:
    if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("PANTHEON_MODEL_KEY")):
        print("no key: source castra/trajan/cella/.env first", file=sys.stderr)
        return 2
    cases = sys.argv[1:] or list(CASES)
    for case_id in cases:
        case = CASES[case_id]
        case["id"] = case_id
        for mode in ("A", "B", "C"):
            if mode == "C":
                run_c(case_id, case)
            else:
                run_ab(case_id, case, mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
