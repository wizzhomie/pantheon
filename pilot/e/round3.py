"""E, round 2b: the same C cases with thinking disabled for the call.

The protocol is in README.md, written before the calls. The question is narrow: in round 2,
C's failed cases (e-r2, e-r3) spent the full 32 768-token room on reasoning and returned
nothing. Does asking the provider not to think leave room for an answer that applies? e-r1 is
rerun as the control: it solved with thinking, so losing it would show the price of the switch.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import round2  # noqa: E402

CASES = {
    "e-t1": "e-r1",
    "e-t2": "e-r2",
    "e-t3": "e-r3",
}


def main() -> int:
    round2.WORK = Path("/tmp/e2-thinking")
    for name, source in CASES.items():
        case = dict(round2.CASES[source])
        case["id"] = name
        case["thinking"] = "disabled"
        round2.run_c(name, case)
    return 0


if __name__ == "__main__":
    sys.exit(main())
