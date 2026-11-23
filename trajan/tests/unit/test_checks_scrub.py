"""PII, refusals and duplicate list items."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from trajan.checks import (
    CheckContext,
    DuplicatesCheck,
    PiiCheck,
    RefusalCheck,
)

CtxFactory = Callable[..., CheckContext]


class Item(BaseModel):
    description: str
    weight: float


class Toy(BaseModel):
    email: str | None = None
    notes: str = ""
    items: list[Item] = []


def test_email_in_free_text_is_reported(make_ctx: CtxFactory) -> None:
    output = Toy(notes="write to marina@example.com please")
    issues = PiiCheck()(output, make_ctx("text"))
    assert [issue.code for issue in issues] == ["G5_PII_LEAK"]
    assert issues[0].field_path == "notes"
    assert issues[0].severity == "warning"


def test_allowed_path_is_not_a_leak(make_ctx: CtxFactory) -> None:
    output = Toy(email="marina@example.com", notes="marina@example.com")
    issues = PiiCheck(allow_paths=["email"])(output, make_ctx("text"))
    assert [issue.field_path for issue in issues] == ["notes"]


def test_card_number_is_critical_but_needs_a_valid_checksum(make_ctx: CtxFactory) -> None:
    valid = Toy(notes="card 4111 1111 1111 1111")
    issues = PiiCheck(kinds=frozenset({"card"}))(valid, make_ctx("text"))
    assert [issue.code for issue in issues] == ["G5_PII_LEAK"]
    assert issues[0].severity == "critical"

    invalid = Toy(notes="order 1234567890123456")
    assert PiiCheck(kinds=frozenset({"card"}))(invalid, make_ctx("text")) == []


def test_no_pii_no_issue(make_ctx: CtxFactory) -> None:
    output = Toy(notes="four pallets, keep cool")
    assert PiiCheck()(output, make_ctx("text")) == []


def test_a_phone_in_free_text_is_reported(make_ctx: CtxFactory) -> None:
    output = Toy(notes="call +48 601 234 567 before dispatch")
    issues = PiiCheck(kinds=frozenset({"phone"}))(output, make_ctx("text"))
    assert [issue.code for issue in issues] == ["G5_PII_LEAK"]
    assert issues[0].field_path == "notes"


def test_iso_dates_are_not_phones(make_ctx: CtxFactory) -> None:
    """The phone pattern is loose enough to match a date; eight digits are not a phone."""
    output = Toy(notes="pickup 2026-12-03 at 2026-12-03 09:00")
    assert PiiCheck(kinds=frozenset({"phone"}))(output, make_ctx("text")) == []


def test_refusal_is_critical(make_ctx: CtxFactory) -> None:
    output = Toy(notes="I'm sorry, I cannot assist with that request.")
    issues = RefusalCheck()(output, make_ctx("text"))
    assert [issue.code for issue in issues] == ["G6_REFUSAL"]
    assert issues[0].severity == "critical"


def test_refusal_markers_are_configurable(make_ctx: CtxFactory) -> None:
    output = Toy(notes="sorry, cannot assist")
    assert RefusalCheck(markers=["cannot assist"])(output, make_ctx("text")) != []
    assert RefusalCheck(markers=["refused"])(output, make_ctx("text")) == []


def test_duplicate_items_are_reported(make_ctx: CtxFactory) -> None:
    output = Toy(
        items=[Item(description="Pallet", weight=100.0), Item(description="Pallet", weight=100.0)]
    )
    issues = DuplicatesCheck(path="items")(output, make_ctx("text"))
    assert [issue.code for issue in issues] == ["G7_DUPLICATE_ITEMS"]
    assert issues[0].details["first_index"] == 0
    assert issues[0].details["duplicate_index"] == 1


def test_distinct_items_pass(make_ctx: CtxFactory) -> None:
    output = Toy(
        items=[Item(description="Pallet", weight=100.0), Item(description="Box", weight=20.0)]
    )
    assert DuplicatesCheck(path="items")(output, make_ctx("text")) == []


def test_identity_can_be_built_from_selected_keys(make_ctx: CtxFactory) -> None:
    output = Toy(
        items=[Item(description="Pallet", weight=100.0), Item(description="Pallet", weight=110.0)]
    )
    check = DuplicatesCheck(path="items", keys=("description",), severity="warning")
    issues = check(output, make_ctx("text"))
    assert [issue.code for issue in issues] == ["G7_DUPLICATE_ITEMS"]
    assert issues[0].severity == "warning"


def test_short_lists_are_skipped(make_ctx: CtxFactory) -> None:
    assert DuplicatesCheck(path="items")(Toy(items=[]), make_ctx("text")) == []
    assert DuplicatesCheck(path="notes")(Toy(), make_ctx("text")) == []


def test_an_allow_list_entry_covers_a_nested_path(make_ctx: CtxFactory) -> None:
    class Quoted(BaseModel):
        evidence: list[dict[str, str]] = []
        notes: str | None = None

    check = PiiCheck(allow_paths=("evidence",))
    output = Quoted(
        evidence=[{"quote": "write to marta@example.com"}],
        notes="copy to marta@example.com",
    )

    codes = [issue.field_path for issue in check(output, make_ctx("text"))]
    assert "evidence.0.quote" not in codes
    assert "notes" in codes
