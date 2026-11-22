"""Format rules: shapes, whitelists and enums."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from pydantic import BaseModel

from qagent.checks import CheckContext, FormatRule, FormatsCheck


class Toy(BaseModel):
    country: str | None = None
    currency: str | None = None
    when: object | None = None
    email: str | None = None
    phone: str | None = None
    kind: str | None = None


VALID = Toy(
    country="PL",
    currency="EUR",
    when=date(2026, 11, 26),
    email="marina@example.com",
    phone="+38 067 123 45 67",
    kind="quote",
)

CtxFactory = Callable[..., CheckContext]


def codes(check: FormatsCheck, output: BaseModel, ctx: CheckContext) -> list[str]:
    return [issue.code for issue in check(output, ctx)]


def test_valid_values_pass(make_ctx: CtxFactory) -> None:
    check = FormatsCheck(
        rules=[
            FormatRule("country", "country_code"),
            FormatRule("currency", "currency"),
            FormatRule("email", "email"),
            FormatRule("phone", "phone"),
            FormatRule("kind", "enum", allowed=frozenset({"quote", "booking"})),
        ]
    )
    assert codes(check, VALID, make_ctx("text")) == []


def test_bad_country_reports_code_and_hint(make_ctx: CtxFactory) -> None:
    check = FormatsCheck(rules=[FormatRule("country", "country_code")])
    bad = VALID.model_copy(update={"country": "Poland"})
    issues = check(bad, make_ctx("text"))
    assert [issue.code for issue in issues] == ["G2_BAD_COUNTRY"]
    assert issues[0].field_path == "country"
    assert issues[0].hint is not None


def test_whitelist_overrides_shape(make_ctx: CtxFactory) -> None:
    check = FormatsCheck(
        rules=[FormatRule("country", "country_code", allowed=frozenset({"XX"}))]
    )
    weird = VALID.model_copy(update={"country": "XX"})
    assert codes(check, weird, make_ctx("text")) == []


def test_impossible_calendar_date_is_rejected(make_ctx: CtxFactory) -> None:
    check = FormatsCheck(rules=[FormatRule("when", "iso_date")])
    assert codes(check, VALID, make_ctx("text")) == []  # a real date is valid

    class TextDate(BaseModel):
        when: str | None = None

    issues = check(TextDate(when="2026-02-30"), make_ctx("text"))
    assert [issue.code for issue in issues] == ["G2_BAD_DATE"]


def test_lowercase_currency_fails(make_ctx: CtxFactory) -> None:
    check = FormatsCheck(rules=[FormatRule("currency", "currency")])
    bad = VALID.model_copy(update={"currency": "eur"})
    assert codes(check, bad, make_ctx("text")) == ["G2_BAD_CURRENCY"]


def test_enum_requires_allowed_values() -> None:
    with pytest.raises(ValueError, match="allowed"):
        FormatRule("kind", "enum")


def test_enum_rejects_unknown_value(make_ctx: CtxFactory) -> None:
    check = FormatsCheck(
        rules=[FormatRule("kind", "enum", allowed=frozenset({"quote"}))]
    )
    bad = VALID.model_copy(update={"kind": "complaint"})
    assert codes(check, bad, make_ctx("text")) == ["G2_BAD_ENUM"]


def test_missing_values_are_skipped(make_ctx: CtxFactory) -> None:
    check = FormatsCheck(
        rules=[FormatRule("country", "country_code"), FormatRule("email", "email")]
    )
    assert codes(check, Toy(), make_ctx("text")) == []
