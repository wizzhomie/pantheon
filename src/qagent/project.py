"""A project described by one file: ``qagent.toml``.

The library already separates "the loop" from "the cartridge". This module closes the last
gap: the cartridge itself can be data, so a new project is a settings file plus two small
factories, not a second copy of the library's wiring.

What lives in the file and what cannot:

* the output schema, rubric, scoring rules, checks, prompts, budgets and prices are data;
* the *agent* and the *judge client* stay factories, because they choose a provider, read a
  key and find a cassette — decisions that belong to the project and are the whole reason
  this module exists next to, not instead of, ``guard()``;
* a check that needs a Python predicate (``ConsistencyCheck``) cannot be written as TOML.
  It is named under ``[checks] extra`` instead, so a config project keeps every rule it had.

Two rules the file enforces rather than documents: unknown keys are rejected, and a judge
without a client or a model name is a configuration error found at load time, not halfway
through the first case.
"""

from __future__ import annotations

import importlib
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field, model_validator

from .checks import (
    DEFAULT_MARKERS,
    DateRule,
    DuplicatesCheck,
    EvidenceCheck,
    FormatKind,
    FormatRule,
    FormatsCheck,
    NumberRange,
    PiiCheck,
    PiiKind,
    RangesCheck,
    RefusalCheck,
    RequiredCheck,
    RequiredRule,
    quotes_from_attribute,
)
from .checks.base import Check
from .checks.paths import MISSING, get_path
from .config import (
    EscalationPolicy,
    JudgePolicy,
    ModelPrice,
    Pricing,
    QAConfig,
    RetryPolicy,
    RunLimits,
)
from .contracts.base import JsonValue, StrictModel
from .contracts.evals import ScoringSpec
from .contracts.issues import Severity
from .contracts.rubric import Rubric
from .contracts.trace import Mode
from .runtime.facade import GuardedAgent, guard
from .runtime.preset import Preset


class RequiredRuleConfig(StrictModel):
    """One mandatory field; ``when_field`` makes it mandatory only for one value.

    The condition is deliberately a single equality: a config file that could express
    arbitrary predicates would be a programming language with no debugger.
    """

    path: str = Field(min_length=1)
    hint: str | None = None
    when_field: str | None = None
    when_equals: JsonValue | None = None

    @model_validator(mode="after")
    def _condition_needs_a_value(self) -> RequiredRuleConfig:
        if self.when_field is not None and self.when_equals is None:
            raise ValueError(f"{self.path}: when_field needs when_equals")
        return self


class RequiredSection(StrictModel):
    rules: list[RequiredRuleConfig] = Field(min_length=1)
    unresolved_path: str | None = None
    severity: Severity = "error"


class FormatRuleConfig(StrictModel):
    path: str = Field(min_length=1)
    kind: FormatKind = Field(min_length=1)
    allowed: list[str] | None = None


class FormatsSection(StrictModel):
    rules: list[FormatRuleConfig] = Field(min_length=1)
    severity: Severity = "error"


class NumberRuleConfig(StrictModel):
    path: str = Field(min_length=1)
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None


class DateRuleConfig(StrictModel):
    path: str = Field(min_length=1)
    not_before_today: bool = False
    not_after_today: bool = False


class RangesSection(StrictModel):
    numbers: list[NumberRuleConfig] = Field(default_factory=list)
    dates: list[DateRuleConfig] = Field(default_factory=list)
    severity: Severity = "error"

    @model_validator(mode="after")
    def _at_least_one_rule(self) -> RangesSection:
        if not self.numbers and not self.dates:
            raise ValueError("ranges needs numbers or dates")
        return self


class EvidenceSection(StrictModel):
    quotes_attribute: str = "evidence"
    required_paths: list[str] = Field(default_factory=list)
    min_similarity: float | None = Field(default=None, ge=0, le=1)
    severity: Severity = "error"


class PiiSection(StrictModel):
    paths: list[str] = Field(default_factory=list)
    kinds: tuple[PiiKind, ...] = ("email", "phone", "card", "iban")
    allow_paths: list[str] = Field(default_factory=list)


class RefusalSection(StrictModel):
    paths: list[str] = Field(default_factory=list)
    markers: list[str] | None = None


class DuplicatesSection(StrictModel):
    path: str = Field(min_length=1)
    keys: list[str] = Field(default_factory=list)


class ChecksSection(StrictModel):
    """Which built-in checks are on, and the project's own checks by factory reference."""

    required: RequiredSection | None = None
    formats: FormatsSection | None = None
    ranges: RangesSection | None = None
    evidence: EvidenceSection | None = None
    pii: PiiSection | None = None
    refusal: RefusalSection | None = None
    duplicates: DuplicatesSection | None = None
    extra: list[str] = Field(
        default_factory=list,
        description="module:factory returning Sequence[Check], for checks a file cannot express",
    )


class ReportSection(StrictModel):
    out: str | None = None
    markdown: str | None = None


class ProjectConfig(StrictModel):
    """One file, one project: what to run, what to check and how to score it."""

    name: str = Field(min_length=1)
    dataset: str = Field(min_length=1, description="dataset JSONL, relative to this file")
    output_schema: str = Field(
        min_length=1,
        alias="schema",
        description="module:attr, a Pydantic BaseModel subclass",
    )
    agent: str = Field(min_length=1, description="module:factory returning the agent function")
    judge_client: str | None = Field(
        default=None, description="module:factory returning an LLMClient for the judge"
    )
    retry_builder: str | None = Field(
        default=None,
        description="module:function(case, feedback) -> feedback, for domain retry hints",
    )
    mode: Mode = "guarded"
    agent_model: str | None = None
    agent_prompt: str = ""
    agent_prompt_file: str | None = None
    judge_instructions: str = ""
    judge_instructions_file: str | None = None
    rubric: Rubric
    scoring: ScoringSpec | None = None
    checks: ChecksSection = Field(default_factory=ChecksSection)
    limits: RunLimits = Field(default_factory=RunLimits)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    judge: JudgePolicy = Field(default_factory=JudgePolicy)
    escalation: EscalationPolicy = Field(default_factory=EscalationPolicy)
    evidence_min_similarity: float = Field(default=0.92, ge=0, le=1)
    capture_payloads: bool = False
    pricing: list[ModelPrice] = Field(default_factory=list)
    report: ReportSection = Field(default_factory=ReportSection)

    @model_validator(mode="after")
    def _judge_can_be_built(self) -> ProjectConfig:
        if not self.judge.enabled:
            return self
        if self.judge_client is None and self.judge.model is None and self.agent_model is None:
            raise ValueError(
                "the judge is enabled but there is no judge_client, judge.model or agent_model"
            )
        return self

    @classmethod
    def load(cls, path: Path) -> ProjectConfig:
        """Read the file and resolve every relative path against the file's directory."""
        path = Path(path)
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        config = cls.model_validate(raw)
        base = path.resolve().parent

        def resolved(name: str) -> str:
            candidate = Path(name)
            return str(candidate if candidate.is_absolute() else (base / candidate))

        updates: dict[str, Any] = {"dataset": resolved(config.dataset)}
        if config.report.out:
            updates["report"] = config.report.model_copy(
                update={"out": resolved(config.report.out)}
            )
        if config.report.markdown:
            report = updates.get("report", config.report)
            updates["report"] = report.model_copy(
                update={"markdown": resolved(config.report.markdown)}
            )
        if config.agent_prompt_file:
            updates["agent_prompt"] = (base / config.agent_prompt_file).read_text(
                encoding="utf-8"
            )
        if config.judge_instructions_file:
            updates["judge_instructions"] = (base / config.judge_instructions_file).read_text(
                encoding="utf-8"
            )
        return config.model_copy(update=updates)


def import_object(spec: str) -> Any:
    """Resolve ``package.module:attribute`` with an error that names the spec."""
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"expected package.module:attribute, got {spec!r}")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise ValueError(f"{module_name} has no attribute {attribute!r}") from exc


def _call(spec: str) -> Any:
    factory = import_object(spec)
    if not callable(factory):
        raise ValueError(f"{spec} is not callable")
    return factory()


def _when(rule: RequiredRuleConfig) -> Callable[[BaseModel], bool] | None:
    if rule.when_field is None:
        return None
    field_path = rule.when_field
    expected = rule.when_equals

    def predicate(output: BaseModel) -> bool:
        actual = get_path(output, field_path)
        return actual is not MISSING and actual == expected

    return predicate


def build_checks(section: ChecksSection) -> list[Check]:
    """Turn the config tables into the built-in checks, in a stable, documented order."""
    checks: list[Check] = []
    if section.required is not None:
        checks.append(
            RequiredCheck(
                rules=[
                    RequiredRule(rule.path, when=_when(rule), hint=rule.hint)
                    for rule in section.required.rules
                ],
                unresolved_path=section.required.unresolved_path,
                severity=section.required.severity,
            )
        )
    if section.formats is not None:
        checks.append(
            FormatsCheck(
                rules=[
                    FormatRule(
                        rule.path,
                        rule.kind,
                        frozenset(rule.allowed) if rule.allowed else None,
                    )
                    for rule in section.formats.rules
                ],
                severity=section.formats.severity,
            )
        )
    if section.ranges is not None:
        checks.append(
            RangesCheck(
                numbers=[
                    NumberRange(rule.path, rule.minimum, rule.maximum, rule.unit)
                    for rule in section.ranges.numbers
                ],
                dates=[
                    DateRule(rule.path, rule.not_before_today, rule.not_after_today)
                    for rule in section.ranges.dates
                ],
                severity=section.ranges.severity,
            )
        )
    # Custom checks sit between the generic field checks and the structural ones: that is
    # the only position the file can express, and it is fixed so two projects cannot end up
    # with the same rules in a different order and therefore a different preset hash.
    for spec in section.extra:
        produced = _call(spec)
        if not isinstance(produced, Sequence):
            raise ValueError(f"{spec} must return a sequence of checks")
        checks.extend(produced)
    if section.duplicates is not None:
        checks.append(DuplicatesCheck(path=section.duplicates.path, keys=section.duplicates.keys))
    if section.evidence is not None:
        checks.append(
            EvidenceCheck(
                quotes=quotes_from_attribute(section.evidence.quotes_attribute),
                required_paths=section.evidence.required_paths,
                min_similarity=section.evidence.min_similarity,
                severity=section.evidence.severity,
            )
        )
    if section.pii is not None:
        checks.append(
            PiiCheck(
                paths=section.pii.paths,
                kinds=frozenset(section.pii.kinds),
                allow_paths=section.pii.allow_paths,
            )
        )
    if section.refusal is not None:
        checks.append(
            RefusalCheck(
                paths=section.refusal.paths,
                markers=(
                    DEFAULT_MARKERS
                    if section.refusal.markers is None
                    else section.refusal.markers
                ),
            )
        )
    return checks


def build_preset(config: ProjectConfig) -> Preset[Any]:
    """Build the cartridge from the file: schema, checks, rubric, prompts, scoring."""
    output_model = import_object(config.output_schema)
    if not (isinstance(output_model, type) and issubclass(output_model, BaseModel)):
        raise ValueError(
            f"{config.output_schema} must be a Pydantic BaseModel subclass, got {output_model!r}"
        )
    return Preset(
        name=config.name,
        output_model=output_model,
        rubric=config.rubric,
        checks=build_checks(config.checks),
        scoring=config.scoring,
        agent_prompt=config.agent_prompt,
        judge_instructions=config.judge_instructions,
        agent_model=config.agent_model,
        build_retry=_callable_or_none(config.retry_builder),
    )


def build_qa_config(config: ProjectConfig) -> QAConfig:
    """The run policy from the same file, with the library defaults kept for the rest."""
    return QAConfig(
        mode=config.mode,
        limits=config.limits,
        retry=config.retry,
        judge=config.judge,
        escalation=config.escalation,
        evidence_min_similarity=config.evidence_min_similarity,
        capture_payloads=config.capture_payloads,
    )


def build_pricing(config: ProjectConfig) -> Pricing:
    """Prices stated in the file; an empty table means unknown prices, not free ones."""
    return Pricing(prices={price.model: price for price in config.pricing})


def build_guarded(config: ProjectConfig) -> GuardedAgent[Any]:
    """The ready-to-run agent: everything from the file, two factories from the project."""
    preset = build_preset(config)
    agent = _call(config.agent)
    llm = _call(config.judge_client) if config.judge_client else None
    return guard(
        agent,
        preset,
        llm=llm,
        config=build_qa_config(config),
        pricing=build_pricing(config),
    )


def validate(config: ProjectConfig) -> list[str]:
    """Fail early on what a run would discover late; return notes worth printing."""
    if not Path(config.dataset).exists():
        raise ValueError(f"dataset not found: {config.dataset}")
    build_preset(config)
    _callable_or_raise(config.agent, "agent")
    if config.judge_client is not None and config.judge.enabled:
        _callable_or_raise(config.judge_client, "judge_client")
    if config.retry_builder is not None:
        _callable_or_raise(config.retry_builder, "retry_builder")
    notes: list[str] = []
    if not config.pricing:
        notes.append("no prices: cost columns will report zero, not a free run")
    if config.scoring is None:
        notes.append("no scoring: pass rate will describe the gate, not correctness")
    return notes


def _callable_or_none(spec: str | None) -> Callable[..., Any] | None:
    if spec is None:
        return None
    target = import_object(spec)
    if not callable(target):
        raise ValueError(f"retry_builder {spec!r} is not callable")
    return cast(Callable[..., Any], target)


def _callable_or_raise(spec: str, what: str) -> None:
    factory = import_object(spec)
    if not callable(factory):
        raise ValueError(f"{what} {spec!r} is not callable")
