"""A path-scoped guard: a destructive migration has to say so.

This is a real check, not a prop. It runs only when the change touches `migrations/**`
(the policy says so), and it fails when a migration drops something without a marker that a
human wrote on purpose.
"""

from __future__ import annotations

import pathlib

MARKER = "-- destructive: ok"


def main() -> int:
    offenders: list[str] = []
    for path in sorted(pathlib.Path("migrations").glob("*.sql")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "drop table" in line.lower() and MARKER not in line.lower():
                offenders.append(f"{path}:{number}: {line.strip()}")
    if offenders:
        print("\n".join(offenders))
        print(f"a destructive migration needs a `{MARKER}` marker on the same line")
        return 1
    print("migrations: no destructive statements without a marker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
