"""The LLM judge: one review, with a bounded repair of its own output.

Two failure modes are handled differently on purpose:

* the model returned something unparseable — cheap to repair, so it gets one retry with a
  pointed message;
* the model produced a well-formed report that cannot be scored (unknown criterion ids) —
  same treatment, because it is still a formatting mistake.

If neither works, this raises :class:`JudgeError`. It must not fabricate a passing verdict:
"the judge was broken" and "the output was bad" lead to different decisions downstream.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from ..config import Pricing
from ..contracts.case import Case
from ..contracts.issues import CODE_PATTERN, GateResult, Issue
from ..contracts.rubric import Rubric
from ..contracts.trace import Usage
from ..contracts.verdict import JudgeVerdict
from ..llm.base import LLMClient, LLMRequest, Message
from ..llm.errors import JudgeError
from ..llm.pricing import usage_of
from .base import JudgeResult
from .prompt import build_messages
from .report import JudgeIssue, JudgeReport

REPAIR_HINT = "Your answer did not match the required JSON schema. Return only the JSON object."

_FENCES = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
_CODE_JUNK = re.compile(r"[^A-Z0-9_]+")


class LLMJudge:
    """Ask a model to fill the rubric, then score it in code."""

    name = "llm"

    def __init__(
        self,
        llm: LLMClient,
        rubric: Rubric,
        *,
        model: str,
        instructions: str = "",
        pricing: Pricing | None = None,
        max_input_chars: int = 20_000,
        max_tokens: int = 900,
        parse_retries: int = 1,
    ) -> None:
        self.llm = llm
        self.rubric = rubric
        self.model = model
        self.instructions = instructions
        self.pricing = pricing or Pricing()
        self.max_input_chars = max_input_chars
        self.max_tokens = max_tokens
        self.parse_retries = parse_retries

    async def review(
        self,
        case: Case,
        output: BaseModel,
        gate: GateResult | None = None,
    ) -> JudgeResult:
        schema = JudgeReport.model_json_schema()
        messages = build_messages(
            case,
            output.model_dump_json(),
            self.rubric,
            self.instructions,
            self.max_input_chars,
            schema=schema,
        )
        usages: list[Usage] = []
        last_error: Exception | None = None

        for _ in range(self.parse_retries + 1):
            response = await self.llm.complete(
                LLMRequest(
                    model=self.model,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=self.max_tokens,
                    response_schema=schema,
                )
            )
            usages.append(usage_of(response, self.pricing))
            try:
                report = _load(response.text)
                verdict = self._verdict(report)
            except (ValueError, ValidationError) as exc:
                last_error = exc
                messages = [
                    *messages,
                    Message(role="assistant", content=response.text[:2_000] or "(empty answer)"),
                    Message(role="user", content=REPAIR_HINT),
                ]
                continue
            return JudgeResult(verdict=verdict, usages=usages, model=response.model)

        raise JudgeError(f"the judge produced no usable verdict: {last_error}")

    def _verdict(self, report: JudgeReport) -> JudgeVerdict:
        expected = {criterion.id for criterion in self.rubric.criteria}
        scored = set(report.criterion_scores)
        if missing := expected - scored:
            raise ValueError(f"judge scored nothing for criteria {sorted(missing)}")
        if unknown := scored - expected:
            raise ValueError(f"judge invented criteria {sorted(unknown)}")
        return JudgeVerdict.from_rubric(
            self.rubric,
            dict(report.criterion_scores),
            [_to_issue(item) for item in report.issues],
            report.fix_instructions,
            must_escalate=report.must_escalate,
            confidence=report.confidence,
        )


def _load(text: str) -> JudgeReport:
    """Parse the answer, tolerating the occasional code fence."""
    candidate = text.strip()
    fenced = _FENCES.search(candidate)
    if fenced is not None:
        candidate = fenced.group(1)
    payload: Any = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError("judge answer is not a JSON object")
    return JudgeReport.model_validate(payload)


def normalise_code(raw: str | None) -> str:
    """Map a model-written code onto the issue taxonomy, or fall back to a generic one."""
    if not raw:
        return "C_UNSPECIFIED"
    cleaned = _CODE_JUNK.sub("_", raw.upper()).strip("_")
    if not cleaned or not re.fullmatch(CODE_PATTERN, cleaned):
        return "C_UNSPECIFIED"
    return cleaned


def _to_issue(item: JudgeIssue) -> Issue:
    return Issue(
        code=normalise_code(item.code),
        severity=item.severity,
        source="judge",
        field_path=item.field_path,
        message=item.message,
        hint=item.hint,
    )

