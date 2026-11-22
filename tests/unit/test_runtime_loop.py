"""The loop end to end: pass, retry, escalate, budget, shadow, baseline.

Everything here runs on scripted agents and callable judges: no network, no tokens, no
sleeps. If a test needs a real model to pass, the test is describing the model, not the loop.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from support import (
    QtyCheck,
    ScriptedAgent,
    ToyOutput,
    failing_judge,
    paid_usage,
    toy_preset,
    verdict,
)

from agentqa import (
    AgentReply,
    Case,
    EscalationPolicy,
    FunctionJudge,
    Issue,
    JudgePolicy,
    QAConfig,
    RetryPolicy,
    RunLimits,
    guard,
)
from agentqa.contracts import JudgeVerdict

NO_JUDGE = QAConfig(judge=JudgePolicy(enabled=False))
JUDGE_ON = QAConfig()
CASE = Case(case_id="c1", payload="two boxes of bolts")


async def run(agent, preset, *, judge=None, config=NO_JUDGE, case=CASE):
    guarded = guard(agent, preset, judge=judge, config=config)
    return await guarded.run(case)


def stages(result) -> list[str]:
    return [event.stage for event in result.trace]


async def test_a_good_answer_passes_on_the_first_attempt(rubric: object) -> None:
    check = QtyCheck()
    agent = ScriptedAgent({"name": "bolts", "qty": 2})
    result = await run(agent, toy_preset(rubric, checks=[check]))

    assert result.status == "passed"
    assert result.output == ToyOutput(name="bolts", qty=2)
    assert result.gate is not None and result.gate.passed
    assert result.escalation_reason is None
    assert result.blocking is False
    assert agent.calls == [None]
    assert check.calls == 1
    assert result.totals.attempts == 1
    assert stages(result) == ["input", "agent", "parse", "check", "done"]


async def test_the_trace_hashes_the_same_payload_for_agent_and_parse(rubric: object) -> None:
    agent = ScriptedAgent({"qty": 2})
    result = await run(agent, toy_preset(rubric))

    agent_event, parse_event = result.trace[1], result.trace[2]
    assert agent_event.payload_sha256 == parse_event.payload_sha256
    assert agent_event.payload_sha256 is not None
    assert len(agent_event.payload_sha256) == 64


async def test_a_failed_gate_retries_with_precise_feedback(rubric: object) -> None:
    agent = ScriptedAgent({"name": "bolts", "qty": 0}, {"name": "bolts", "qty": 2})
    result = await run(agent, toy_preset(rubric))

    assert result.status == "passed_after_retry"
    assert result.totals.attempts == 2
    feedback = agent.calls[1]
    assert feedback is not None
    assert [issue.code for issue in feedback.issues] == ["G3_OUT_OF_RANGE"]
    assert feedback.previous_output is not None
    assert '"qty": 0' in feedback.previous_output
    assert any("positive quantity" in item for item in feedback.instructions)
    assert stages(result).count("retry") == 1


async def test_exhausted_attempts_escalate_and_keep_the_last_answer(rubric: object) -> None:
    agent = ScriptedAgent({"qty": 0}, {"qty": -1})
    result = await run(agent, toy_preset(rubric), config=QAConfig(judge=JudgePolicy(enabled=False)))

    assert result.status == "escalated"
    assert result.escalation_reason == "max_attempts_exhausted"
    assert result.output == ToyOutput(qty=-1)
    assert result.gate is not None and not result.gate.passed
    assert result.blocking is True
    assert len(agent.calls) == 2


async def test_an_identical_answer_stops_the_loop_before_the_judge(rubric: object) -> None:
    judge = FunctionJudge(failing_judge(rubric))
    agent = ScriptedAgent({"qty": 2}, {"qty": 2})
    config = QAConfig(judge=JudgePolicy(mode="on_gate_pass"))

    result = await run(agent, toy_preset(rubric), judge=judge, config=config)

    assert result.escalation_reason == "no_progress"
    assert len(agent.calls) == 2
    assert judge.calls == 1  # the rejected attempt was judged; the repeat was not
    judge_events = [event for event in result.trace if event.stage == "judge"]
    assert len(judge_events) == 1


async def test_critical_escalates_without_retrying(rubric: object) -> None:
    class PiiCheck:
        id = "pii"
        tier = "fast"

        def __call__(self, output: BaseModel, ctx: object) -> list[Issue]:
            return [
                Issue(
                    code="G5_PII_LEAK",
                    severity="critical",
                    source="check",
                    message="an email address leaked into the output",
                )
            ]

    judge = FunctionJudge(lambda case, output: verdict(rubric))
    agent = ScriptedAgent({"qty": 1}, {"qty": 2})
    config = QAConfig()

    result = await run(agent, toy_preset(rubric, checks=[PiiCheck()]), judge=judge, config=config)

    assert result.status == "escalated"
    assert result.escalation_reason == "critical_issue"
    assert len(agent.calls) == 1
    assert judge.calls == 0  # a critical gate failure stops before the judge too


async def test_critical_can_be_retried_once_by_policy(rubric: object) -> None:
    class PiiCheck:
        id = "pii"
        tier = "fast"

        def __call__(self, output: BaseModel, ctx: object) -> list[Issue]:
            return [
                Issue(code="G5_PII_LEAK", severity="critical", source="check", message="leak")
            ]

    agent = ScriptedAgent({"qty": 1}, {"qty": 2}, {"qty": 3})
    config = QAConfig(
        judge=JudgePolicy(enabled=False),
        escalation=EscalationPolicy(on_critical="retry_once"),
    )
    result = await run(agent, toy_preset(rubric, checks=[PiiCheck()]), config=config)

    assert result.escalation_reason == "critical_issue"
    assert len(agent.calls) == 2


async def test_cost_budget_stops_before_the_next_attempt(rubric: object) -> None:
    reply = AgentReply(attempt={"qty": 0}, usages=(paid_usage(0.2),))
    agent = ScriptedAgent(reply, {"qty": 2})
    config = QAConfig(judge=JudgePolicy(enabled=False), limits=RunLimits(max_cost_usd=0.05))

    result = await run(agent, toy_preset(rubric), config=config)

    assert result.escalation_reason == "budget_exhausted"
    assert result.totals.cost_usd == pytest.approx(0.2)
    assert len(agent.calls) == 1
    assert result.output == ToyOutput(qty=0)


async def test_a_zero_budget_never_calls_the_agent(rubric: object) -> None:
    agent = ScriptedAgent({"qty": 1})
    config = QAConfig(judge=JudgePolicy(enabled=False), limits=RunLimits(max_cost_usd=0))

    result = await run(agent, toy_preset(rubric), config=config)

    assert result.escalation_reason == "budget_exhausted"
    assert agent.calls == []
    assert result.totals.attempts == 0


async def test_an_exhausted_judge_budget_escalates(rubric: object) -> None:
    judge = FunctionJudge(lambda case, output: verdict(rubric))
    agent = ScriptedAgent({"qty": 2})
    config = QAConfig(
        judge=JudgePolicy(mode="on_gate_pass"), limits=RunLimits(max_judge_calls=0)
    )

    result = await run(agent, toy_preset(rubric), judge=judge, config=config)

    assert result.escalation_reason == "budget_exhausted"
    assert judge.calls == 0
    assert result.output is not None


async def test_a_rejected_verdict_retries_the_agent(rubric: object) -> None:
    agent = ScriptedAgent({"qty": 2}, {"qty": 3})
    result = await run(
        agent, toy_preset(rubric), judge=FunctionJudge(failing_judge(rubric)), config=JUDGE_ON
    )

    assert result.status == "escalated"
    assert result.escalation_reason == "max_attempts_exhausted"
    assert len(agent.calls) == 2
    second = agent.calls[1]
    assert second is not None and second.judge is not None
    assert second.judge.passed is False
    assert result.verdict is not None and result.verdict.passed is False


async def test_a_rejected_verdict_can_be_recovered(rubric: object) -> None:
    calls = {"n": 0}

    def producer(case: Case, output: BaseModel) -> JudgeVerdict:
        calls["n"] += 1
        return verdict(rubric, good=calls["n"] > 1)

    agent = ScriptedAgent({"qty": 2}, {"qty": 2, "name": "again"})
    result = await run(
        agent, toy_preset(rubric), judge=FunctionJudge(producer), config=JUDGE_ON
    )

    assert result.status == "passed_after_retry"
    assert calls["n"] == 2


async def test_a_broken_judge_escalates_instead_of_passing(rubric: object) -> None:
    def broken(case: Case, output: BaseModel) -> JudgeVerdict:
        raise RuntimeError("judge is down")

    agent = ScriptedAgent({"qty": 2})
    result = await run(agent, toy_preset(rubric), judge=FunctionJudge(broken), config=JUDGE_ON)

    assert result.escalation_reason == "judge_escalate"
    assert result.output == ToyOutput(qty=2)
    assert result.gate is not None and result.gate.passed
    judge_event = [event for event in result.trace if event.stage == "judge"][-1]
    assert judge_event.ok is False
    assert judge_event.notes is not None and "judge is down" in judge_event.notes


async def test_the_judge_can_ask_for_a_human(rubric: object) -> None:
    judge = FunctionJudge(
        lambda case, output: JudgeVerdict.from_rubric(
            rubric, {"grounding": 4, "completeness": 4}, must_escalate=True
        )
    )
    result = await run(ScriptedAgent({"qty": 2}), toy_preset(rubric), judge=judge, config=JUDGE_ON)

    assert result.escalation_reason == "judge_escalate"
    assert result.verdict is not None and result.verdict.must_escalate is True


async def test_shadow_escalates_in_the_trace_but_never_blocks(rubric: object) -> None:
    config = QAConfig(
        mode="shadow", retry=RetryPolicy(max_attempts=1), judge=JudgePolicy(enabled=False)
    )
    result = await run(ScriptedAgent({"qty": 0}), toy_preset(rubric), config=config)

    assert result.status == "escalated"
    assert result.would_have_blocked is True
    assert result.blocking is False


async def test_shadow_marks_a_clean_result_as_not_blocking(rubric: object) -> None:
    config = QAConfig(mode="shadow", judge=JudgePolicy(enabled=False))
    result = await run(ScriptedAgent({"qty": 2}), toy_preset(rubric), config=config)

    assert result.status == "passed"
    assert result.would_have_blocked is False
    assert result.blocking is False


async def test_baseline_calls_the_agent_once_and_runs_no_checks(rubric: object) -> None:
    check = QtyCheck()
    agent = ScriptedAgent({"qty": 0}, {"qty": 2})
    config = QAConfig(
        mode="baseline", limits=RunLimits(max_cost_usd=1.0), judge=JudgePolicy(enabled=False)
    )

    result = await run(agent, toy_preset(rubric, checks=[check]), config=config)

    assert result.status == "passed"  # parseable is all baseline asks
    assert result.output == ToyOutput(qty=0)
    assert result.gate is None
    assert result.verdict is None
    assert check.calls == 0
    assert len(agent.calls) == 1
    assert result.totals.attempts == 1
    assert stages(result) == ["input", "agent", "parse", "done"]


async def test_baseline_reports_an_unparseable_answer_as_failed(rubric: object) -> None:
    result = await run(
        ScriptedAgent("not json at all"),
        toy_preset(rubric),
        config=QAConfig(mode="baseline", judge=JudgePolicy(enabled=False)),
    )

    assert result.status == "failed"
    assert result.output is None
    assert result.gate is not None
    assert result.gate.codes() == ["P1_BAD_JSON"]
    assert result.blocking is True


async def test_an_empty_input_escalates_before_the_agent(rubric: object) -> None:
    agent = ScriptedAgent({"qty": 2})
    result = await run(
        agent,
        toy_preset(rubric),
        case=Case(case_id="empty", payload="   "),
    )

    assert result.status == "escalated"
    assert result.escalation_reason == "input_invalid"
    assert agent.calls == []
    assert result.totals.attempts == 0
    assert result.gate is None


async def test_an_oversized_input_escalates_before_the_agent(rubric: object) -> None:
    config = QAConfig(judge=JudgePolicy(enabled=False), limits=RunLimits(max_input_chars=5))
    agent = ScriptedAgent({"qty": 2})
    result = await run(
        agent, toy_preset(rubric), config=config, case=Case(case_id="long", payload="0123456789")
    )

    assert result.escalation_reason == "input_invalid"
    assert agent.calls == []
    assert "I_INPUT_TOO_LONG" in (result.trace[0].notes or "")


async def test_an_agent_exception_escalates_after_a_retry(rubric: object) -> None:
    agent = ScriptedAgent(RuntimeError("socket closed"), RuntimeError("socket closed"))
    result = await run(agent, toy_preset(rubric))

    assert result.escalation_reason == "agent_error"
    assert len(agent.calls) == 2
    second = agent.calls[1]
    assert second is not None
    assert [issue.code for issue in second.issues] == ["A_AGENT_ERROR"]
    assert result.gate is None


async def test_the_last_failure_names_the_reason(rubric: object) -> None:
    agent = ScriptedAgent({"qty": 0}, RuntimeError("boom"))
    result = await run(agent, toy_preset(rubric))

    assert result.escalation_reason == "agent_error"
    assert result.output == ToyOutput(qty=0)


async def test_a_preset_can_adjust_the_retry_feedback(rubric: object) -> None:
    def build_retry(case: Case, feedback: object) -> object:
        return feedback.model_copy(update={"instructions": ["DOMAIN: use the packing list"]})

    preset = toy_preset(rubric, build_retry=build_retry)
    agent = ScriptedAgent({"qty": 0}, {"qty": 1})
    result = await run(agent, preset)

    assert result.status == "passed_after_retry"
    second = agent.calls[1]
    assert second is not None
    assert second.instructions == ["DOMAIN: use the packing list"]


async def test_a_broken_retry_hook_fails_loudly(rubric: object) -> None:
    def build_retry(case: Case, feedback: object) -> None:
        return None

    agent = ScriptedAgent({"qty": 0}, {"qty": 1})
    with pytest.raises(TypeError, match="must return a Feedback"):
        await run(agent, toy_preset(rubric, build_retry=build_retry))


async def test_a_truncated_agent_reply_escalates_as_critical(rubric: object) -> None:
    agent = ScriptedAgent(AgentReply(attempt='{"qty": 2', truncated=True))
    result = await run(agent, toy_preset(rubric))

    assert result.escalation_reason == "critical_issue"
    assert result.gate is not None and result.gate.codes() == ["P3_TRUNCATED"]
    assert len(agent.calls) == 1


async def test_payload_capture_is_opt_in(rubric: object) -> None:
    quiet = await run(ScriptedAgent({"qty": 2}), toy_preset(rubric))
    assert not any("payload=" in (event.notes or "") for event in quiet.trace)

    loud = await run(
        ScriptedAgent({"qty": 2}),
        toy_preset(rubric),
        config=QAConfig(judge=JudgePolicy(enabled=False), capture_payloads=True),
    )
    notes = " ".join(event.notes or "" for event in loud.trace)
    assert "payload=" in notes
    assert "two boxes of bolts" in notes


async def test_one_attempt_disables_retries(rubric: object) -> None:
    agent = ScriptedAgent({"qty": 0})
    config = QAConfig(judge=JudgePolicy(enabled=False), retry=RetryPolicy(max_attempts=1))
    result = await run(agent, toy_preset(rubric), config=config)

    assert result.escalation_reason == "max_attempts_exhausted"
    assert len(agent.calls) == 1


async def test_sampling_reviews_the_same_cases_on_every_run(rubric: object) -> None:
    preset = toy_preset(rubric)
    config = QAConfig(judge=JudgePolicy(mode="sampled", sample_rate=0.5))

    async def judged_ids() -> set[str]:
        judged: set[str] = set()
        for index in range(20):
            judge = FunctionJudge(lambda case, output: verdict(rubric))
            guarded = guard(
                ScriptedAgent({"qty": 2}), preset, judge=judge, config=config
            )
            await guarded.run(Case(case_id=f"case-{index}", payload="x"))
            if judge.calls:
                judged.add(f"case-{index}")
        return judged

    first = await judged_ids()
    assert first == await judged_ids()
    assert 0 < len(first) < 20
