"""A stand-in for a model: the known bugs, with a few scripted mistakes.

This is **not** a model and the reports it produces are **not** a measurement of one. It
exists so the example runs the whole loop offline, deterministically and for free, and so
the sample report doubles as a catalogue of what the checks catch:

* ``cr-03`` — the review says "ok" and misses the bug; nothing deterministic can see it, so
  it ends as a false pass and shows why ground truth is the only honest pass rate;
* ``cr-05`` — an invented quote: caught by ``C1_QUOTE_NOT_IN_CODE``, fixed on the retry;
* ``cr-09`` — the right line with the wrong quote: caught by ``C3_QUOTE_NOT_ON_LINE``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from trajan import (
    Case,
    Dataset,
    Feedback,
    GuardedAgent,
    JudgePolicy,
    QAConfig,
    guard,
)

from .schema import Finding, Review

DEMO_MODEL = "demo-stub"
"""The model name that appears in a stub report: a reader cannot mistake it for a real one."""

DEFECTS: Mapping[str, str] = {
    "cr-03": "silent",
    "cr-05": "invented_quote",
    "cr-09": "off_line_quote",
}

FIXED_ON_RETRY = frozenset({"cr-05", "cr-09"})


class DemoAgent:
    """Return the planted bugs, with the scripted defect of the case applied."""

    def __init__(self, dataset: Dataset) -> None:
        self._payloads: dict[str, str] = {}
        self._bugs: dict[str, list[int]] = {}
        for case in dataset.cases:
            self._payloads[case.case_id] = str(case.payload)
            raw = case.expected.get("bug_lines", [])
            self._bugs[case.case_id] = (
                [line for line in raw if isinstance(line, int)] if isinstance(raw, list) else []
            )

    async def __call__(self, case: Case, feedback: Feedback | None) -> Review:
        lines = self._payloads[case.case_id].splitlines()
        defect = DEFECTS.get(case.case_id)
        if defect and (feedback is None or case.case_id not in FIXED_ON_RETRY):
            if defect == "silent":
                return Review(verdict="ok", findings=[])
            if defect == "invented_quote":
                return Review(
                    verdict="problems",
                    findings=[
                        Finding(
                            line=1,
                            message="this line is not in the snippet",
                            quote="def this_line_is_not_in_the_snippet():",
                        )
                    ],
                )
            if defect == "off_line_quote":
                bug = self._bugs[case.case_id][0]
                return Review(
                    verdict="problems",
                    findings=[
                        Finding(
                            line=bug,
                            message="the quote belongs to another line",
                            quote=lines[0],
                        )
                    ],
                )
        findings = [
            Finding(
                line=line,
                message="the planted defect is on this line",
                quote=lines[line - 1],
            )
            for line in self._bugs[case.case_id]
        ]
        return Review(verdict="problems" if findings else "ok", findings=findings)


def build_demo_guarded() -> GuardedAgent[Review]:
    """The guarded demo agent used by the stub reports and by ``make sample``."""
    from .runner import load_config

    config = load_config()
    dataset = Dataset.load(config.dataset)
    from trajan.project import build_preset

    # A stub model name in the report, so nobody can read it as a measurement of a model.
    preset = replace(build_preset(config), agent_model=DEMO_MODEL)
    return guard(
        DemoAgent(dataset),
        preset,
        config=QAConfig(judge=JudgePolicy(enabled=False)),
    )
