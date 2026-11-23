"""The LLM judge: scoring in code, bounded repair, explicit failure."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel

from trajan.config import ModelPrice, Pricing
from trajan.contracts import Case, Issue, Rubric
from trajan.judge import JudgeReport, LLMJudge, normalise_code
from trajan.llm import FakeClient, JudgeError, LLMResponse


class Output(BaseModel):
    cargo: str = "4 pallets"


def case() -> Case:
    return Case(case_id="case-1", payload="cargo: 4 pallets")


def report(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "criterion_scores": {"grounding": 5, "completeness": 5},
        "issues": [],
        "fix_instructions": [],
        "must_escalate": False,
        "confidence": 0.9,
    }
    payload.update(overrides)
    return json.dumps(payload)


def pricing() -> Pricing:
    return Pricing(
        prices={
            "judge-model": ModelPrice(
                model="judge-model",
                input_usd_per_mtok=2.0,
                output_usd_per_mtok=8.0,
                checked_at=date(2026, 11, 1),
            )
        }
    )


def judge(client: FakeClient, rubric: Rubric, **kwargs: Any) -> LLMJudge:
    return LLMJudge(
        client,
        rubric,
        model="judge-model",
        pricing=pricing(),
        **kwargs,
    )


async def test_clean_report_passes(rubric: Rubric) -> None:
    answer = LLMResponse(
        text=report(), model="judge-model", prompt_tokens=1_000, completion_tokens=500
    )
    client = FakeClient([answer])
    result = await judge(client, rubric).review(case(), Output())
    assert result.verdict.passed is True
    assert result.verdict.score == 1.0
    assert result.verdict.confidence == 0.9
    assert len(result.usages) == 1
    assert result.usages[0].cost_usd == pytest.approx(0.006)  # 1k*2.0 + 0.5k*8.0 per Mtok
    assert result.usages[0].price_known is True


async def test_the_model_cannot_pass_itself(rubric: Rubric) -> None:
    """High scores plus a critical issue must still fail: the code decides."""
    blocking = {
        "code": "FAKE_QUOTE",
        "severity": "critical",
        "field_path": "cargo",
        "message": "the cargo is not in the input",
    }
    client = FakeClient([report(issues=[blocking])])
    result = await judge(client, rubric).review(case(), Output())
    assert result.verdict.score == 1.0
    assert result.verdict.passed is False
    assert result.verdict.blocking_issues[0].code == "FAKE_QUOTE"
    assert result.verdict.blocking_issues[0].source == "judge"


async def test_low_scores_fail_without_any_issue(rubric: Rubric) -> None:
    client = FakeClient([report(criterion_scores={"grounding": 1, "completeness": 1})])
    result = await judge(client, rubric).review(case(), Output())
    assert result.verdict.passed is False
    assert result.verdict.score == 0.0


async def test_broken_json_is_repaired_once(rubric: Rubric) -> None:
    client = FakeClient(["not json at all", report()])
    result = await judge(client, rubric).review(case(), Output())
    assert result.verdict.passed is True
    assert len(client.requests) == 2
    assert len(result.usages) == 2
    # the repair turn includes the bad answer and a pointed instruction
    assert client.requests[1].messages[-1].role == "user"
    assert "did not match the required JSON schema" in client.requests[1].messages[-1].content


async def test_unrepairable_json_raises(rubric: Rubric) -> None:
    client = FakeClient(["nope", "still nope"])
    with pytest.raises(JudgeError, match="no usable verdict"):
        await judge(client, rubric).review(case(), Output())
    assert len(client.requests) == 2


async def test_invented_criterion_is_rejected(rubric: Rubric) -> None:
    client = FakeClient(
        [
            report(criterion_scores={"grounding": 5, "completeness": 5, "vibes": 4}),
            report(),
        ]
    )
    result = await judge(client, rubric).review(case(), Output())
    assert result.verdict.passed is True
    assert len(client.requests) == 2


async def test_missing_criterion_raises_after_repair_attempts(rubric: Rubric) -> None:
    client = FakeClient(
        [
            report(criterion_scores={"grounding": 5}),
            report(criterion_scores={"grounding": 5}),
        ]
    )
    with pytest.raises(JudgeError, match="no usable verdict"):
        await judge(client, rubric).review(case(), Output())


async def test_out_of_range_score_is_rejected(rubric: Rubric) -> None:
    client = FakeClient(
        [report(criterion_scores={"grounding": 9, "completeness": 5}), report()]
    )
    result = await judge(client, rubric).review(case(), Output())
    assert result.verdict.passed is True
    assert len(client.requests) == 2


async def test_fenced_json_is_accepted(rubric: Rubric) -> None:
    client = FakeClient([f"```json\n{report()}\n```"])
    result = await judge(client, rubric).review(case(), Output())
    assert result.verdict.passed is True
    assert len(client.requests) == 1


async def test_the_schema_does_not_ask_for_a_verdict(rubric: Rubric) -> None:
    properties = JudgeReport.model_json_schema()["properties"]
    assert "passed" not in properties
    assert "score" not in properties
    assert "criterion_scores" in properties


def test_codes_are_normalised_onto_the_taxonomy() -> None:
    assert normalise_code("fake quote!") == "FAKE_QUOTE"
    assert normalise_code("omission") == "OMISSION"
    assert normalise_code(None) == "C_UNSPECIFIED"
    assert normalise_code("!!!") == "C_UNSPECIFIED"
    assert normalise_code("x") == "C_UNSPECIFIED"


async def test_judge_issues_are_marked_as_judge_sourced(rubric: Rubric) -> None:
    client = FakeClient([report(issues=[{"code": "omission", "message": "cargo missing"}])])
    result = await judge(client, rubric).review(case(), Output())
    issue: Issue = result.verdict.issues[0]
    assert issue.source == "judge"
    assert issue.code == "OMISSION"
    assert issue.severity == "warning"
