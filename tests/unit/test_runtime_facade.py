"""The facade: one call to wrap an agent, with configuration errors raised up front."""

from __future__ import annotations

import pytest
from support import ScriptedAgent, ToyOutput, toy_preset, verdict

from agentqa import (
    Case,
    FakeClient,
    FunctionJudge,
    GuardedAgent,
    JudgePolicy,
    LLMJudge,
    Preset,
    QAConfig,
    Rubric,
    guard,
)


async def agent(case: object, feedback: object) -> dict[str, int]:
    return {"qty": 1}


def test_a_non_callable_agent_is_rejected(rubric: Rubric) -> None:
    with pytest.raises(TypeError, match="agent must be callable"):
        guard("not an agent", toy_preset(rubric))  # type: ignore[arg-type]


def test_an_enabled_judge_without_a_client_is_a_configuration_error(rubric: Rubric) -> None:
    with pytest.raises(ValueError, match=r"neither llm nor preset\.judge"):
        guard(agent, toy_preset(rubric))


def test_an_enabled_judge_without_a_model_name_is_rejected(rubric: Rubric) -> None:
    preset = Preset(name="toy", output_model=ToyOutput, rubric=rubric)
    with pytest.raises(ValueError, match="needs a model name"):
        guard(agent, preset, llm=FakeClient())


def test_the_default_judge_is_built_from_the_policy_and_the_rubric(rubric: Rubric) -> None:
    preset = toy_preset(rubric)
    guarded = guard(
        agent,
        preset,
        llm=FakeClient(),
        config=QAConfig(judge=JudgePolicy(model="judge-model")),
    )

    assert isinstance(guarded.judge, LLMJudge)
    assert guarded.judge.model == "judge-model"
    assert guarded.judge.rubric is rubric


def test_the_judge_model_falls_back_to_the_presets_agent_model(rubric: Rubric) -> None:
    guarded = guard(agent, toy_preset(rubric), llm=FakeClient())

    assert isinstance(guarded.judge, LLMJudge)
    assert guarded.judge.model == "toy-model"


def test_a_preset_judge_wins_over_building_one(rubric: Rubric) -> None:
    custom = FunctionJudge(lambda case, output: verdict(rubric))
    preset = toy_preset(rubric, judge=custom)

    guarded = guard(agent, preset, llm=FakeClient())

    assert guarded.judge is custom


def test_a_disabled_judge_needs_no_client(rubric: Rubric) -> None:
    guarded = guard(agent, toy_preset(rubric), config=QAConfig(judge=JudgePolicy(enabled=False)))

    assert guarded.judge is None


def test_switching_mode_keeps_the_setup(rubric: Rubric) -> None:
    custom = FunctionJudge(lambda case, output: verdict(rubric))
    preset = toy_preset(rubric, judge=custom)
    guarded = guard(agent, preset, config=QAConfig(mode="shadow"))
    baseline = guarded.with_mode("baseline")

    assert isinstance(baseline, GuardedAgent)
    assert guarded.mode == "shadow"
    assert baseline.mode == "baseline"
    assert baseline.judge is guarded.judge
    assert baseline.preset is guarded.preset
    assert baseline.pricing is guarded.pricing
    assert guarded.preset_hash == preset.fingerprint()


async def test_the_facade_runs_a_case(rubric: Rubric) -> None:
    guarded = guard(
        ScriptedAgent({"qty": 4}),
        toy_preset(rubric),
        config=QAConfig(judge=JudgePolicy(enabled=False)),
    )

    result = await guarded.run(Case(case_id="facade", payload="four bolts"))

    assert result.status == "passed"
    assert result.output == ToyOutput(qty=4)
