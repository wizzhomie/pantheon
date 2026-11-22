"""The spending cap for one run.

Three rules, each of which exists because the alternative is worse:

* the cap gates **new** calls, it does not retroactively reject a result that was already
  paid for — throwing away a good answer because it arrived late helps nobody;
* time is measured on the wall clock, not summed over model calls, because the agent's own
  retries and tool calls are part of the latency the user experiences;
* an exhausted judge budget is a separate fact from an exhausted money budget, so the run
  can say which one stopped it.
"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Literal

from ..config import RunLimits
from ..contracts.trace import Usage

Breach = Literal["cost_usd", "latency_ms"]


class Budget:
    """Mutable spend tracker; deliberately not a contract (it is a running total)."""

    def __init__(self, limits: RunLimits, *, clock: Callable[[], float] = perf_counter) -> None:
        self.limits = limits
        self._clock = clock
        self._started_at = clock()
        self.cost_usd = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.judge_calls = 0
        self.price_complete = True

    def spend(self, usage: Usage) -> None:
        """Account for one model call."""
        self.cost_usd = round(self.cost_usd + usage.cost_usd, 10)
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.price_complete = self.price_complete and usage.price_known

    def note_judge_call(self, *, calls: int = 1) -> None:
        self.judge_calls += calls

    def elapsed_ms(self) -> float:
        return round((self._clock() - self._started_at) * 1000, 3)

    def breach(self) -> Breach | None:
        """Which spending cap is already used up, if any."""
        max_cost = self.limits.max_cost_usd
        if max_cost is not None and self.cost_usd >= max_cost:
            return "cost_usd"
        max_latency = self.limits.max_latency_ms
        if max_latency is not None and self.elapsed_ms() >= max_latency:
            return "latency_ms"
        return None

    def judge_allowed(self) -> bool:
        """Whether another judge call fits the call budget."""
        return self.judge_calls < self.limits.max_judge_calls
