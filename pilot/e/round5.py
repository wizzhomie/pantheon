"""E, round 5: the same level-2 task on the model that does not think first.

The protocol is in README.md, written before the call. Round 4 showed the call cut at the cap:
32 768 spent on thinking and no text at all. Round 5 asks one variable: the other model the
endpoint offers, ``deepseek-chat``, which answers without a reasoning pass. If it solves the
task, reasoning was the stand's workaround, not the only way; if it does not, the conclusion
stands and the thread closes. One call, five cents.
"""

from __future__ import annotations

import io
import json
import os
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
import round4 as r4  # noqa: E402

from harness import box as boxes  # noqa: E402
from harness import cli  # noqa: E402

WORK = Path("/tmp/e2")
PYTHON = r4.PYTHON
PRICE_CENTS_CEILING = 5.0
PROFILE = r4.base.PROFILE.replace('model = "deepseek-flash"', 'model = "deepseek-chat"')
CASE = r4.CASE


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
        f"{json.dumps('pytest')}, {json.dumps('-q')}, {json.dumps(r4.VISIBLE)}]\n"
        "max_steps = 3\n"
        "\n[budget]\n"
        "max_cents = 5\n"
        "max_seconds = 300\n"
    )


def record(payload: dict[str, object]) -> None:
    line = {"round": 5, "case": "e-r2", "mode": "L2-chat", **payload}
    out = WORK / "results-round5.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(json.dumps(line, ensure_ascii=False))


def main() -> int:
    if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("PANTHEON_MODEL_KEY")):
        print("no key: source castra/trajan/cella/.env first", file=sys.stderr)
        return 2
    tree, prepared = r4.prepare(CASE)
    dry: dict[str, object] = {"prepared": prepared}
    code, tail = r4.visible_check(tree)
    dry["visible_before"] = {"passed": code == 0, "tail": tail}
    hidden_before, hidden_before_note = r2.hidden_check(CASE, tree)
    dry["hidden_before"] = {"passed": hidden_before, "tail": hidden_before_note[-300:]}
    dry["changed_before"] = r4.base.diff_names(tree, prepared)
    print(json.dumps({"dry": dry}, ensure_ascii=False), file=sys.stderr)
    (WORK / "dry-round5.json").write_text(json.dumps(dry, ensure_ascii=False, indent=2) + "\n")
    if code == 0 or hidden_before or dry["changed_before"]:
        print("the stand is not ready: the visible check must fail on the base and the tree "
              "must be clean", file=sys.stderr)
        return 3

    work = Path(tempfile.mkdtemp(prefix="e5-e-r2-"))
    castra = work / "castra"
    box = boxes.new_box(castra, tree, name="e5-e-r2")
    (box.root / "persona" / "executor.toml").write_text(
        PROFILE.format(command=ROOT / "bin" / "pantheon-executor", cents=5), encoding="utf-8"
    )
    task_path = work / "e-r2-chat.toml"
    task_path.write_text(task_toml("e-r2-chat"), encoding="utf-8")

    buffer = io.StringIO()
    code = 0
    started = time.monotonic()
    saved = {
        key: os.environ.get(key)
        for key in ("PYTHON", "PYTHONPATH", "PATH", "PANTHEON_MODEL_THINKING")
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

    proposals = r4.proposal_records(box, "e-r2-chat")
    cost = round(sum(float((item.get("usage") or {}).get("cost_cents") or 0) for item in proposals), 4)
    solved, hidden_note = r2.hidden_check(CASE, tree)
    after_code, after_tail = r4.visible_check(tree)
    changed = r4.base.diff_names(tree, prepared)
    state = str(payload.get("state") or ("error" if code == 2 else "unknown"))
    result: dict[str, object] = {
        "solved": solved,
        "false_pass": after_code == 0 and not solved,
        "visible_before": dry["visible_before"],
        "visible_after": {"passed": after_code == 0, "tail": after_tail},
        "applied": bool(changed),
        "changed": changed,
        "cost_cents": cost,
        "seconds": seconds,
        "calls": len(proposals),
        "statuses": [item.get("status") for item in proposals],
        "model": [item.get("model") for item in proposals],
        "usage": [item.get("usage") for item in proposals],
        "level": payload.get("level"),
        "route": payload.get("route"),
        "engine": payload.get("engine"),
        "state": state,
        "exit_code": payload.get("exit_code", code),
        "stop_reason": payload.get("stop_reason"),
        "steps": payload.get("steps"),
        "note": hidden_note if solved else f"state {state}; {hidden_note}".strip("; "),
        "answer": raw[:400],
    }
    record(result)
    if cost > PRICE_CENTS_CEILING:
        raise RuntimeError(f"the round-5 ceiling is reached: {cost:.2f} cents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
