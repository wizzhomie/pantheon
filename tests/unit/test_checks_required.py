"""Required fields and honest abstention."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from agentqa.checks import CheckContext, RequiredCheck, RequiredRule


class Contact(BaseModel):
    email: str | None = None


class Toy(BaseModel):
    title: str | None = None
    contact: Contact | None = None
    tags: list[str] = []
    unresolved: list[str] = []


def check(*rules: RequiredRule, **kwargs: object) -> RequiredCheck:
    return RequiredCheck(rules=list(rules), unresolved_path="unresolved", **kwargs)  # type: ignore[arg-type]


def test_missing_required_field_is_an_error(make_ctx: Callable[..., CheckContext]) -> None:
    issues = check(RequiredRule("title"))(Toy(), make_ctx("text"))
    assert [issue.code for issue in issues] == ["G1_REQUIRED_MISSING"]
    assert issues[0].field_path == "title"
    assert issues[0].severity == "error"
    assert issues[0].hint is not None


def test_present_field_produces_nothing(make_ctx: Callable[..., CheckContext]) -> None:
    assert check(RequiredRule("title"))(Toy(title="ok"), make_ctx("text")) == []


def test_unresolved_marker_excuses_a_missing_field(
    make_ctx: Callable[..., CheckContext],
) -> None:
    output = Toy(unresolved=["title"])
    assert check(RequiredRule("title"))(output, make_ctx("text")) == []


def test_marking_a_filled_field_unresolved_is_reported(
    make_ctx: Callable[..., CheckContext],
) -> None:
    output = Toy(title="ok", unresolved=["title"])
    issues = check(RequiredRule("title"))(output, make_ctx("text"))
    assert [issue.code for issue in issues] == ["G1_UNRESOLVED_BUT_PRESENT"]
    assert issues[0].severity == "warning"


def test_conditional_rule_skips_when_it_does_not_apply(
    make_ctx: Callable[..., CheckContext],
) -> None:
    rule = RequiredRule("contact.email", when=lambda output: output.contact is not None)
    assert check(rule)(Toy(contact=None), make_ctx("text")) == []


def test_conditional_rule_applies_when_it_does(make_ctx: Callable[..., CheckContext]) -> None:
    rule = RequiredRule("contact.email", when=lambda output: output.contact is not None)
    issues = check(rule)(Toy(contact=Contact()), make_ctx("text"))
    assert [issue.code for issue in issues] == ["G1_REQUIRED_MISSING"]
    assert issues[0].field_path == "contact.email"


def test_blank_string_and_empty_list_count_as_missing(
    make_ctx: Callable[..., CheckContext],
) -> None:
    issues = check(RequiredRule("title"), RequiredRule("tags"))(
        Toy(title="   ", tags=[]), make_ctx("text")
    )
    assert sorted(issue.field_path or "" for issue in issues) == ["tags", "title"]


def test_custom_severity_and_hint_are_honoured(make_ctx: Callable[..., CheckContext]) -> None:
    rule = RequiredRule("title", hint="copy the subject line")
    issues = check(rule, severity="warning")(Toy(), make_ctx("text"))
    assert issues[0].severity == "warning"
    assert issues[0].hint == "copy the subject line"


def test_without_unresolved_path_abstention_is_not_excused(
    make_ctx: Callable[..., CheckContext],
) -> None:
    plain = RequiredCheck(rules=[RequiredRule("title")])
    issues = plain(Toy(unresolved=["title"]), make_ctx("text"))
    assert [issue.code for issue in issues] == ["G1_REQUIRED_MISSING"]
