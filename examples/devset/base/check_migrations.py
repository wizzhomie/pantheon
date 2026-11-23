"""Fail when a migration drops something without a human saying it is fine."""

from pathlib import Path

DESTRUCTIVE = ("DROP TABLE", "DROP COLUMN", "TRUNCATE")


def main() -> int:
    problems: list[str] = []
    for path in sorted(Path("migrations").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        if "-- destructive: ok" in text:
            continue
        for line in text.splitlines():
            if any(word in line.upper() for word in DESTRUCTIVE):
                problems.append(f"{path}: {line.strip()}")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
