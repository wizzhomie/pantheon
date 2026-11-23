"""A stand-in for a model: replay the golden answer, with scripted defects.

This is **not** a model and the reports it produces are **not** a measurement of one. It
exists so the example runs the real loop offline, deterministically and for free: each case
gets its golden answer, and a handful of cases get one deliberate defect, which is what the
checks, the retry and the escalation paths react to.

Every defect maps to a check code, so the sample report doubles as a catalogue of what the
preset actually catches. ``on_retry_too`` marks the defects that stay broken: those are the
cases that end in escalation (``no_progress``) or slip through as a false pass.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from qagent import (
    Case,
    Dataset,
    Feedback,
    GuardedAgent,
    JudgePolicy,
    QAConfig,
    guard,
)

from .build_dataset import DATASET_PATH
from .preset import PRESET
from .schema import ExtractedIntake

DEMO_MODEL = "demo-stub"
"""The model name that appears in a demo report: a reader cannot mistake it for a real one."""

DEMO_PRESET = replace(PRESET, agent_model=DEMO_MODEL)

Golden = dict[str, Any]
Mutator = Callable[[Golden], None]


@dataclass(frozen=True, slots=True)
class Defect:
    """One scripted mistake, shaped to trip one specific check."""

    mutate: Mutator
    on_retry_too: bool = False
    """``True`` for a mistake the agent does not fix: escalate or pass with a false value."""


def _bad_country(payload: Golden) -> None:
    """L1: a country written as a name, which the format check rejects."""
    payload["origin"]["country_code"] = "Lithuania"


def _weight(value: float) -> Mutator:
    def mutate(payload: Golden) -> None:
        payload["cargo"][0]["weight_kg"] = value

    return mutate


def _drop_cargo(payload: Golden) -> None:
    payload["cargo"] = []


def _bad_date(payload: Golden) -> None:
    payload["pickup_date"] = "03.12.2026"


def _duplicate_cargo(payload: Golden) -> None:
    payload["cargo"].append(copy.deepcopy(payload["cargo"][0]))


def _fake_quote(payload: Golden) -> None:
    for entry in payload["evidence"]:
        if entry["field_path"] == "origin.city":
            entry["quote"] = "Gliwice"


def _swap_dates(payload: Golden) -> None:
    payload["pickup_date"], payload["delivery_date"] = (
        payload["delivery_date"],
        payload["pickup_date"],
    )


def _drop_destination_city(payload: Golden) -> None:
    payload["destination"]["city"] = None


def _refuse(payload: Golden) -> None:
    payload["notes"] = "I cannot assist with this request."


DEFECTS: dict[str, Defect] = {
    "hq-03": Defect(_bad_country),  # G2_BAD_COUNTRY -> retry fixes it
    "hq-05": Defect(_weight(45_000.0)),  # L5_CARGO_OUT_OF_RANGE -> retry fixes it
    "hq-07": Defect(_drop_cargo),  # G1_REQUIRED_MISSING -> retry fixes it
    "hq-11": Defect(_bad_date),  # G2_BAD_DATE -> retry fixes it
    "hq-15": Defect(_duplicate_cargo),  # G7_DUPLICATE_ITEMS -> retry fixes it
    "ed-02": Defect(_fake_quote),  # G4_FAKE_QUOTE -> retry fixes it
    "ed-04": Defect(_bad_country, on_retry_too=True),  # same answer twice -> no_progress
    "ed-07": Defect(_swap_dates),  # L1_PICKUP_AFTER_DELIVERY -> retry fixes it
    "ed-09": Defect(_drop_destination_city),  # G1_REQUIRED_MISSING -> retry fixes it
    "adv-01": Defect(_weight(800.0), on_retry_too=True),  # gate passes: a false pass
    "adv-05": Defect(_refuse),  # G6_REFUSAL (critical) -> escalate, no retry
}


class DemoAgent:
    """Deterministic agent: golden answer in, one scripted mistake out."""

    def __init__(self, golden: Mapping[str, Golden]) -> None:
        self._golden = golden
        self.calls: list[str] = []

    async def __call__(self, case: Case, feedback: Feedback | None) -> ExtractedIntake:
        self.calls.append(case.case_id)
        payload = copy.deepcopy(self._golden[case.case_id])
        defect = DEFECTS.get(case.case_id)
        if defect is not None and (feedback is None or defect.on_retry_too):
            defect.mutate(payload)
        return ExtractedIntake.model_validate(payload)


def load_golden(dataset: Dataset | None = None) -> dict[str, Golden]:
    """The dataset's golden answers, keyed by case id: the only thing the demo needs."""
    data = dataset if dataset is not None else Dataset.load(DATASET_PATH)
    return {case.case_id: case.expected for case in data.cases}


def build_demo_guarded() -> GuardedAgent[ExtractedIntake]:
    """The guarded demo agent used by the sample report and by ``make demo``."""
    return guard(
        DemoAgent(load_golden()),
        DEMO_PRESET,
        config=QAConfig(judge=JudgePolicy(enabled=False)),
    )
