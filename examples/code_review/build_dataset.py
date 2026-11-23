"""Build the committed dataset from the fixtures, and refuse to write a lie.

Two guarantees are checked before anything is written:

* every ``# BUG`` marker becomes the golden line of that case, and the marker itself is
  removed from the code the model sees;
* every probe actually fails on the broken code. A "bug" that does not change behaviour is
  not a test case, it is a comment, and it would silently inflate the pass rate.

Run it after editing ``fixtures.py``: ``python -m examples.code_review.build_dataset``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from .fixtures import FIXTURES, Fixture

DATASET_PATH = Path(__file__).with_name("dataset.jsonl")
MARKER = "# BUG"


def strip_markers(source: str) -> tuple[str, list[int]]:
    """Remove the markers and return the lines they were on (1-based)."""
    lines = source.splitlines()
    bug_lines: list[int] = []
    cleaned: list[str] = []
    for number, line in enumerate(lines, start=1):
        if MARKER in line:
            bug_lines.append(number)
            cleaned.append(line.replace(MARKER, "").rstrip())
        else:
            cleaned.append(line.rstrip())
    return "\n".join(cleaned) + "\n", bug_lines


def function_name(source: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return node.name
    raise SystemExit("a fixture must define a function")


def verify(fixture: Fixture, source: str) -> None:
    """A probe that still passes means the 'bug' is not observable."""
    name = function_name(source)
    namespace: dict[str, object] = {}
    exec(compile(source, fixture.case_id, "exec"), namespace)
    function = namespace[name]
    if not callable(function):
        raise SystemExit(f"{fixture.case_id}: {name} is not callable")
    if fixture.probe is None:
        return
    args, expected = fixture.probe
    try:
        actual = function(*args)
    except Exception as exc:
        actual = f"{type(exc).__name__}: {exc}"
    if actual == expected:
        raise SystemExit(
            f"{fixture.case_id}: the probe passes on the broken code ({actual!r}); "
            "the bug is not observable and the case would measure nothing"
        )


def build() -> str:
    lines: list[str] = []
    for fixture in FIXTURES:
        source, bug_lines = strip_markers(fixture.source)
        verify(fixture, source)
        case = {
            "case_id": fixture.case_id,
            "category": fixture.category,
            "tags": list(fixture.tags),
            "payload": source,
            "expected": {"bug_lines": bug_lines},
        }
        lines.append(json.dumps(case, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def main() -> int:
    text = build()
    DATASET_PATH.write_text(text, encoding="utf-8")
    cases = [json.loads(line) for line in text.splitlines()]
    broken = sum(1 for case in cases if case["expected"]["bug_lines"])
    print(
        f"wrote {DATASET_PATH.name}: {len(cases)} cases, "
        f"{broken} with a planted bug, {len(cases) - broken} clean"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
