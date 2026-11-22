"""Running a dataset through a guarded agent.

One function, because everything interesting is already built: the same loop that guards
production runs the regression, so the report describes the system people actually ship.

Concurrency defaults to four calls. Scripted fakes and cassette clients are often ordered,
so tests and deterministic offline runs should pass ``concurrency=1``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import TypeVar

from pydantic import BaseModel

from ..contracts.case import Case
from ..contracts.evals import EvalCase, EvalReport, MetaEval
from ..judge.calibration import CalibrationSample, score_calibration
from ..runtime.facade import GuardedAgent
from .dataset import Dataset
from .metrics import CaseRecord
from .report import build_report
from .scoring import score_case

TOut = TypeVar("TOut", bound=BaseModel)


async def evaluate(
    guarded: GuardedAgent[TOut],
    dataset: Dataset | Sequence[EvalCase],
    *,
    concurrency: int = 4,
    created_at: datetime | None = None,
    revision: str | None = None,
) -> EvalReport:
    """Run every case, score it against the golden values and build the report."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    spec = guarded.preset.scoring
    if spec is None:
        raise ValueError(
            "preset.scoring is required: without golden fields there is nothing to score, "
            "and a report of loop decisions alone would measure the gate, not the quality"
        )
    prepared = dataset if isinstance(dataset, Dataset) else Dataset(cases=list(dataset))
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(eval_case: EvalCase) -> CaseRecord:
        async with semaphore:
            result = await guarded.run(
                Case(
                    case_id=eval_case.case_id,
                    payload=eval_case.payload,
                    meta={"category": eval_case.category},
                )
            )
        return CaseRecord(
            case=eval_case,
            result=result,
            score=score_case(result.output, eval_case, spec),
        )

    records = list(await asyncio.gather(*(run_one(case) for case in prepared.cases)))
    return build_report(
        records,
        guarded=guarded,
        dataset=prepared,
        created_at=created_at,
        revision=revision,
        meta_eval=_meta_eval(records),
    )


def _meta_eval(records: Sequence[CaseRecord]) -> MetaEval | None:
    """Calibrate the judge from hand labels, when the dataset carries them."""
    samples = [
        CalibrationSample(
            case_id=record.case.case_id,
            judge_passed=record.result.verdict.passed,
            human_passed=bool(record.case.human_verdict),
        )
        for record in records
        if record.case.human_verdict is not None and record.result.verdict is not None
    ]
    return score_calibration(samples) if samples else None
