"""The public entry point: one call to wrap an agent, one call to run a case.

:func:`guard` composes; it does not hide. Every piece it builds (preset, judge, config,
pricing) stays readable on the object it returns, and the loop can be driven directly when
a caller needs to.

The judge is built here rather than required from the preset because the *policy* lives in
the configuration (mode, sampling, model, input cap) while the *rubric* lives in the preset.
Splitting those between two places is how a judge quietly stops matching the rubric.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

from ..config import DEFAULT_CONFIG, Pricing, QAConfig
from ..contracts.agent import AgentFn
from ..contracts.case import Case
from ..contracts.trace import Mode, RunResult
from ..judge.base import Judge
from ..judge.llm_judge import LLMJudge
from ..llm.base import LLMClient
from .loop import CaseRunner
from .preset import Preset

TOut = TypeVar("TOut", bound=BaseModel)


class GuardedAgent(Generic[TOut]):
    """An agent plus the QA layer around it, ready to run cases."""

    def __init__(
        self,
        *,
        agent: AgentFn,
        preset: Preset[TOut],
        config: QAConfig = DEFAULT_CONFIG,
        judge: Judge | None = None,
        pricing: Pricing | None = None,
    ) -> None:
        self.agent = agent
        self.preset = preset
        self.config = config
        self.judge = judge
        self.pricing = pricing or Pricing()
        self._runner: CaseRunner[TOut] = CaseRunner(
            agent=agent,
            preset=preset,
            config=config,
            judge=judge,
            pricing=self.pricing,
        )

    @property
    def mode(self) -> Mode:
        return self.config.mode

    @property
    def preset_hash(self) -> str:
        """Hash of everything in the preset that changes behaviour."""
        return self.preset.fingerprint()

    async def run(self, case: Case) -> RunResult[TOut]:
        """Run one case through the loop and return the evidence, not just a verdict."""
        return await self._runner.run(case)

    def with_mode(self, mode: Mode) -> GuardedAgent[TOut]:
        """The same setup in another mode: baseline for the comparison, shadow to observe."""
        return GuardedAgent(
            agent=self.agent,
            preset=self.preset,
            config=self.config.model_copy(update={"mode": mode}),
            judge=self.judge,
            pricing=self.pricing,
        )


def guard(
    agent: AgentFn,
    preset: Preset[TOut],
    *,
    llm: LLMClient | None = None,
    config: QAConfig | None = None,
    judge: Judge | None = None,
    pricing: Pricing | None = None,
) -> GuardedAgent[TOut]:
    """Wrap ``agent`` with the QA loop described by ``preset`` and ``config``.

    ``llm`` is only needed when the judge is enabled and the preset does not carry its own
    :class:`Judge`; a missing client is a configuration error, raised here rather than
    discovered halfway through the first case.
    """
    if not callable(agent):
        raise TypeError("agent must be callable: async (case, feedback | None) -> attempt")
    cfg = config or QAConfig()
    chosen = judge or preset.judge
    if chosen is None and cfg.judge.enabled:
        if llm is None:
            raise ValueError(
                "the judge is enabled but neither llm nor preset.judge was provided; "
                "pass llm=... or set config.judge.enabled=False"
            )
        model = cfg.judge.model or preset.agent_model
        if model is None:
            raise ValueError(
                "the judge needs a model name: set config.judge.model or preset.agent_model"
            )
        chosen = LLMJudge(
            llm,
            preset.rubric,
            model=model,
            instructions=preset.judge_instructions,
            pricing=pricing,
            max_input_chars=cfg.judge.max_input_chars,
            max_tokens=cfg.judge.max_tokens,
        )
    return GuardedAgent(
        agent=agent, preset=preset, config=cfg, judge=chosen, pricing=pricing
    )
