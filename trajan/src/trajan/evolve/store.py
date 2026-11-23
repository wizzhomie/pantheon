"""Where a decision is written down.

A proposal that is born from the history is not a decision; it becomes one when a person
writes it to the project, next to the policy it modifies, in a file that is reviewed with the
code (ADR-002). The file is the record: what was proposed, on which evidence, what the human
said and when. The command is not rewritten — accepting a rule never edits ``policy.toml``
behind anyone's back, it is merged over it when a run needs an effective policy.
"""

from __future__ import annotations

import re
from pathlib import Path

from .contracts import PROPOSAL_ID_PATTERN, Proposal

PROPOSAL_DIR = "proposals"
PROPOSAL_SUFFIX = ".json"


class ProposalError(ValueError):
    """A decision file that cannot be trusted, or a decision that cannot be made."""


def proposals_dir(root: str | Path) -> Path:
    return Path(root) / ".trajan" / PROPOSAL_DIR


def load_proposals(root: str | Path) -> list[Proposal]:
    """Every decision the project has recorded; a broken file is an error, not a silence."""
    directory = proposals_dir(root)
    if not directory.is_dir():
        return []
    found: list[Proposal] = []
    for path in sorted(directory.glob(f"*{PROPOSAL_SUFFIX}")):
        try:
            found.append(Proposal.model_validate_json(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            raise ProposalError(f"{path}: unreadable proposal: {exc}") from exc
    return sorted(found, key=lambda proposal: proposal.id)


def save_proposal(root: str | Path, proposal: Proposal) -> Path:
    """Write the decision where the project can review it; the id names the file."""
    if not _valid_id(proposal.id):
        raise ProposalError(f"refusing to write a proposal with an unusable id: {proposal.id}")
    directory = proposals_dir(root)
    path = directory / f"{proposal.id}{PROPOSAL_SUFFIX}"
    if path.resolve().parent != directory.resolve():
        raise ProposalError(f"refusing to write outside {directory}: {path}")
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(proposal.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _valid_id(identifier: str) -> bool:
    return re.fullmatch(PROPOSAL_ID_PATTERN, identifier) is not None
