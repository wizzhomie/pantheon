"""Evidence checks: the cheap, deterministic anti-hallucination layer."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from trajan.checks import CheckContext, EvidenceCheck, coverage, quotes_from_attribute

SOURCE = (
    "Добрый день, это Марина из ООО Северный ветер. Заберите 4 паллеты в Гданьске, "
    "загрузка в среду, у клиента нужно быть 30.11. Инкотермс DAP. Тел. +38 067 123 45 67."
)

CtxFactory = Callable[..., CheckContext]


class Citation(BaseModel):
    field_path: str
    quote: str


class Toy(BaseModel):
    origin: str = ""
    cargo: str = ""
    notes: str = ""
    citations: list[Citation] = []


def make_check(required: list[str] | None = None, **kwargs: object) -> EvidenceCheck:
    return EvidenceCheck(
        quotes=quotes_from_attribute("citations"),
        required_paths=required or [],
        **kwargs,  # type: ignore[arg-type]
    )


def test_exact_quote_passes(make_ctx: CtxFactory) -> None:
    output = Toy(
        origin="Гданьск",
        citations=[Citation(field_path="origin", quote="Заберите 4 паллеты в Гданьске")],
    )
    assert make_check()(output, make_ctx(SOURCE)) == []


def test_case_and_dash_and_yo_variants_are_normalised(make_ctx: CtxFactory) -> None:
    output = Toy(
        origin="Гданьск",
        citations=[Citation(field_path="origin", quote="ЗАБЕРИТЕ 4 ПАЛЛЕТЫ  В ГДАНЬСКЕ")],
    )
    assert make_check()(output, make_ctx(SOURCE)) == []
    assert coverage("мёд", "МЕД") == 1.0


def test_invented_quote_is_flagged(make_ctx: CtxFactory) -> None:
    output = Toy(
        origin="Варшава",
        citations=[Citation(field_path="origin", quote="заберите груз в Варшаве")],
    )
    issues = make_check()(output, make_ctx(SOURCE))
    assert [issue.code for issue in issues] == ["G4_FAKE_QUOTE"]
    assert issues[0].field_path == "origin"
    assert issues[0].details["coverage"] < 0.92


def test_required_path_without_a_quote_is_flagged(make_ctx: CtxFactory) -> None:
    issues = make_check(required=["cargo"])(Toy(), make_ctx(SOURCE))
    assert [issue.code for issue in issues] == ["G4_NO_EVIDENCE"]
    assert issues[0].field_path == "cargo"


def test_empty_quote_is_flagged(make_ctx: CtxFactory) -> None:
    output = Toy(citations=[Citation(field_path="origin", quote="   ")])
    issues = make_check()(output, make_ctx(SOURCE))
    assert [issue.code for issue in issues] == ["G4_NO_EVIDENCE"]


def test_orphan_citation_is_a_warning(make_ctx: CtxFactory) -> None:
    output = Toy(citations=[Citation(field_path="ghost", quote="Гданьске")])
    issues = make_check()(output, make_ctx(SOURCE))
    assert [issue.code for issue in issues] == ["G4_ORPHAN_EVIDENCE"]
    assert issues[0].severity == "warning"


def test_truncated_quote_still_passes_above_the_floor(make_ctx: CtxFactory) -> None:
    output = Toy(
        origin="Гданьск",
        citations=[Citation(field_path="origin", quote="Заберите 4 паллеты в Гданьск")],
    )
    assert make_check(min_similarity=0.9)(output, make_ctx(SOURCE)) == []


def test_short_fragment_matches_over_big_distance(make_ctx: CtxFactory) -> None:
    output = Toy(
        cargo="4 pallets",
        citations=[Citation(field_path="cargo", quote="4 паллет")],
    )
    assert make_check()(output, make_ctx(SOURCE)) == []


def test_severity_is_configurable(make_ctx: CtxFactory) -> None:
    output = Toy(origin="X", citations=[Citation(field_path="origin", quote="nothing like it")])
    issues = make_check(severity="warning")(output, make_ctx(SOURCE))
    assert issues[0].severity == "warning"
