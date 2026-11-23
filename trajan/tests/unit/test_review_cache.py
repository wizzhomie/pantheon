"""The review cache: the same question is paid for once, and the report says when it was.

The key is what the model is asked — prompt, model, temperature, ceiling — so the tests are
about the boundary: a changed instruction, requirement or model is a different question and
must reach the provider; a failed review is not an answer and must not be stored; and a
cached result keeps the tokens of the call that paid for it instead of pretending to be free.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from trajan.llm.fake import FakeClient
from trajan.review import ProposedFinding, ReviewerConfig, ReviewOutcome, anchor, parse_diff
from trajan.review.cache import ReviewCache
from trajan.review.reviewer import review_diff
from trajan.verify.report import _review_section

DIFF_TEXT = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1,3 +1,3 @@\n"
    " def total(items):\n"
    '     """Sum the prices."""\n'
    "-    return sum(item.price * item.quantity for item in items)\n"
    "+    return sum(item.price for item in items)\n"
)
APP = (
    "def total(items):\n"
    '    """Sum the prices."""\n'
    "    return sum(item.price for item in items)\n"
)
QUOTE = "return sum(item.price for item in items)"


def proposal() -> ProposedFinding:
    return ProposedFinding(
        file="app.py",
        line=3,
        quote=QUOTE,
        title="total ignores quantity",
        severity="major",
        kind="defect",
    )


def answer(*findings: ProposedFinding) -> str:
    return json.dumps({"summary": "a change", "findings": [f.model_dump() for f in findings]})


def project(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(APP, encoding="utf-8")
    return tmp_path


def config(**overrides: object) -> ReviewerConfig:
    base: dict[str, object] = {"model": "reviewer", "max_tokens": 800}
    base.update(overrides)
    return ReviewerConfig(**base)  # type: ignore[arg-type]


def review(
    tmp_path: Path,
    client: FakeClient,
    cache: ReviewCache | None,
    *,
    requirements: tuple[tuple[str, str], ...] = (),
    **overrides: object,
) -> ReviewOutcome:
    return asyncio.run(
        review_diff(
            parse_diff(DIFF_TEXT),
            project(tmp_path),
            client,
            config(**overrides),
            requirements=requirements,
            cache=cache,
        )
    )


def test_the_second_review_of_the_same_question_is_served_from_the_cache(tmp_path: Path) -> None:
    client = FakeClient([answer(proposal()), answer(proposal())])
    cache = ReviewCache(tmp_path)

    first = review(tmp_path, client, cache)
    second = review(tmp_path, client, cache)

    assert len(client.requests) == 1
    assert first.cached is False
    assert second.cached is True
    assert [finding.id for finding in second.findings] == [finding.id for finding in first.findings]
    assert second.prompt_tokens == first.prompt_tokens


def test_a_different_model_is_a_different_question(tmp_path: Path) -> None:
    client = FakeClient([answer(), answer()])
    cache = ReviewCache(tmp_path)

    review(tmp_path, client, cache, model="reviewer")
    review(tmp_path, client, cache, model="another-reviewer")

    assert len(client.requests) == 2


def test_editing_the_instructions_is_a_different_question(tmp_path: Path) -> None:
    client = FakeClient([answer(), answer()])
    cache = ReviewCache(tmp_path)

    review(tmp_path, client, cache, instructions="be brief")
    review(tmp_path, client, cache, instructions="be brief\nand cite ids")

    assert len(client.requests) == 2


def test_a_different_ceiling_is_a_different_question(tmp_path: Path) -> None:
    client = FakeClient([answer(), answer()])
    cache = ReviewCache(tmp_path)

    review(tmp_path, client, cache, max_tokens=800)
    review(tmp_path, client, cache, max_tokens=1200)

    assert len(client.requests) == 2


def test_a_new_requirement_is_a_different_question(tmp_path: Path) -> None:
    client = FakeClient([answer(), answer()])
    cache = ReviewCache(tmp_path)

    review(tmp_path, client, cache)
    review(tmp_path, client, cache, requirements=(("R1", "the verdict is code"),))

    assert len(client.requests) == 2


def test_a_failed_review_is_not_an_answer_to_reuse(tmp_path: Path) -> None:
    client = FakeClient([RuntimeError("provider down"), answer(), answer()])
    cache = ReviewCache(tmp_path)

    failed = review(tmp_path, client, cache)
    retried = review(tmp_path, client, cache)
    cached = review(tmp_path, client, cache)

    assert failed.status == "failed"
    assert (retried.status, retried.cached) == ("ok", False)
    assert cached.cached is True
    assert len(client.requests) == 2


def test_a_corrupt_entry_is_a_miss_not_an_error(tmp_path: Path) -> None:
    client = FakeClient([answer(), answer()])
    cache = ReviewCache(tmp_path)
    review(tmp_path, client, cache)
    (entry,) = cache.directory.glob("*.json")
    entry.write_text("{not json", encoding="utf-8")

    outcome = review(tmp_path, client, cache)

    assert outcome.status == "ok"
    assert outcome.cached is False
    assert len(client.requests) == 2


def test_the_cache_keeps_a_bounded_number_of_reviews(tmp_path: Path) -> None:
    cache = ReviewCache(tmp_path, limit=2)
    for index in range(4):
        cache.store(f"key-{index}", ReviewOutcome(status="ok", model="reviewer"))

    assert len(list(cache.directory.glob("*.json"))) == 2


def test_the_report_says_the_review_was_cached(tmp_path: Path) -> None:
    outcome = ReviewOutcome(
        status="ok",
        model="deepseek-flash",
        proposed=1,
        confirmed=1,
        prompt_tokens=8461,
        completion_tokens=557,
        cached=True,
    )

    section = _review_section(outcome)

    assert "deepseek-flash" in section
    assert "(cached)" in section
    assert "8461 in / 557 out (paid once, on the original review)" in section


def test_an_uncached_review_says_nothing_about_paying_once(tmp_path: Path) -> None:
    outcome = ReviewOutcome(status="ok", model="deepseek-flash", prompt_tokens=10)

    assert "paid once" not in _review_section(outcome)


def test_a_cached_finding_still_passes_the_anchor(tmp_path: Path) -> None:
    """Serving a review again must not skip the step that made it believable."""
    found = anchor(1, proposal(), project(tmp_path), parse_diff(DIFF_TEXT))

    assert found.status == "confirmed"
