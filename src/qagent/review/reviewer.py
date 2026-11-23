"""The independent reviewer: a model proposes, the repository decides.

The order is the whole design. The model reads the change and writes findings; then every
finding is put against the repository — the file exists, the file is part of the change, the
line is in range, and the quote is *that* line. Only then may a finding say ``confirmed``.

Anchoring is cheap, deterministic and does not need the model to be right: a quote copied
from the diff is verifiable, a quote written from the model's impression of the diff is not.
A proposal that fails the anchor is kept as ``unverified`` — visible in the report, never
blocking. That is what stops a confident paragraph from becoming a defect.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path

from ..contracts.findings import Finding, FindingSeverity, Location
from ..llm.base import LLMClient, LLMRequest, Message
from .cache import ReviewCache
from .contracts import ProposedFinding, ProposedReview, ReviewerConfig, ReviewOutcome
from .diff import Diff, normalise_path
from .prompt import build_user_message, system_message

MAX_REPO_FILES = 50_000


async def review_diff(
    diff: Diff,
    root: Path,
    client: LLMClient,
    config: ReviewerConfig,
    *,
    requirements: Sequence[tuple[str, str]] = (),
    cache: ReviewCache | None = None,
) -> ReviewOutcome:
    """Ask the model about the change, then check every answer against the repository.

    A failure of the review itself (provider error, unparseable answer, timeout) is an
    outcome, not an exception: the run continues, and the verdict learns that the review did
    not happen.
    """
    started = time.monotonic()
    user_message = build_user_message(
        diff, max_bytes=config.max_diff_bytes, requirements=requirements
    )
    request = LLMRequest(
        model=config.model,
        messages=[
            Message(role="system", content=system_message(config.instructions)),
            Message(role="user", content=user_message),
        ],
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
    cache_key = cache.key(request) if cache is not None else None
    if cache is not None and cache_key is not None:
        stored = cache.load(cache_key)
        if stored is not None:
            return stored

    try:
        response = await client.complete(request)
    except Exception as exc:  # a reviewer failure must not take the run down
        return ReviewOutcome(
            status="failed",
            model=config.model,
            diff_digest=diff.digest(),
            files_reviewed=len(diff.files),
            error=f"{type(exc).__name__}: {exc}"[:400],
            spent_seconds=round(time.monotonic() - started, 3),
        )

    try:
        proposed = _parse(response.text)
    except ValueError as exc:
        return ReviewOutcome(
            status="failed",
            model=response.model,
            diff_digest=diff.digest(),
            files_reviewed=len(diff.files),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            error=f"unparseable review answer: {exc}"[:400],
            spent_seconds=round(time.monotonic() - started, 3),
        )

    approved = {item_id for item_id, _ in requirements}
    findings = [
        anchor(index + 1, proposal, root, diff, approved=approved)
        for index, proposal in enumerate(proposed.findings)
    ]
    outcome = ReviewOutcome(
        status="ok",
        model=response.model,
        diff_digest=diff.digest(),
        files_reviewed=len(diff.files),
        proposed=len(findings),
        confirmed=sum(1 for finding in findings if finding.status == "confirmed"),
        unverified=sum(1 for finding in findings if finding.status == "unverified"),
        linked=sum(1 for finding in findings if finding.requirement is not None),
        findings=findings,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        spent_seconds=round(time.monotonic() - started, 3),
    )
    if cache is not None and cache_key is not None:
        cache.store(cache_key, outcome)
    return outcome


def anchor(
    index: int,
    proposal: ProposedFinding,
    root: Path,
    diff: Diff,
    *,
    approved: set[str] | None = None,
) -> Finding:
    """Turn a proposal into a finding, and say how much of it the repository confirms.

    ``approved`` is the vocabulary of requirements the model was allowed to cite. A link to
    anything else is dropped, not trusted: the contour may only trace to promises a human
    approved (ADR-004).
    """
    root = Path(root).resolve()
    severity, adjusted = _severity(proposal)
    location = Location(file=proposal.file, line=proposal.line, quote=proposal.quote)
    problem = _anchor_problem(proposal, root, diff)
    requirement, link_note = _requirement(proposal, approved)
    detail = proposal.detail
    if adjusted:
        detail = f"{detail} (severity lowered: a {proposal.kind} cannot block)".strip()
    if problem is not None:
        detail = f"{detail} (anchor: {problem})".strip()
    if link_note is not None:
        detail = f"{detail} ({link_note})".strip()
    return Finding(
        id=f"R{index}",
        title=proposal.title,
        kind=proposal.kind,
        status="confirmed" if problem is None else "unverified",
        severity=severity,
        source="reviewer",
        detail=detail,
        location=location,
        requirement=requirement,
    )


def _requirement(
    proposal: ProposedFinding, approved: set[str] | None
) -> tuple[str | None, str | None]:
    """Keep a requirement link only when it names an approved promise."""
    if proposal.requirement is None:
        return None, None
    if approved is None:
        return None, "requirement link dropped: no project context"
    if proposal.requirement not in approved:
        return None, f"requirement {proposal.requirement} is not approved"
    return proposal.requirement, None


def _severity(proposal: ProposedFinding) -> tuple[FindingSeverity, bool]:
    """A risk or a test gap may be serious, but it cannot stop a release on its own."""
    if proposal.severity == "blocking" and proposal.kind != "defect":
        return "major", True
    return proposal.severity, False


def _anchor_problem(proposal: ProposedFinding, root: Path, diff: Diff) -> str | None:
    """Why the claim does not hold, or ``None`` when the repository agrees with it.

    The cited line must be one the reviewer was actually shown: a finding about a line the
    diff never contained is a finding about code the model did not read.
    """
    path = _resolve(root, proposal.file)
    if path is None:
        return "path is outside the project"
    change = next(
        (item for item in diff.files if item.path == normalise_path(proposal.file)), None
    )
    if change is None:
        return "file is not part of the change"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "file does not exist"
    lines = text.splitlines()
    if proposal.line > len(lines):
        return f"line {proposal.line} is outside the file ({len(lines)} lines)"
    if not any(line.number == proposal.line for line in change.lines):
        return f"line {proposal.line} is not part of the change"
    actual = _normalise(lines[proposal.line - 1])
    quoted = _normalise(proposal.quote)
    if actual == quoted:
        return None
    elsewhere = [number for number, line in enumerate(lines, 1) if _normalise(line) == quoted]
    if elsewhere:
        return f"the quote is on line {elsewhere[0]}, not {proposal.line}"
    return "the quote is not in the file"


def _resolve(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _normalise(line: str) -> str:
    """Whitespace is formatting, not evidence; case and punctuation are content."""
    return " ".join(line.split())


def _parse(text: str) -> ProposedReview:
    """Read the model's answer, tolerating fences, rejecting everything else."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[: -len("```")]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the answer")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("the answer is not a JSON object")
    return ProposedReview.model_validate(data)
