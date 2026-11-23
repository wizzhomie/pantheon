"""Evolution of the project's own checks, from evidence and with a human's signature.

The loop this package implements, end to end:

1. read the decided runs the project already has (:mod:`qagent.evolve.history`);
2. find a rule the history argues for — a check whose failures kept shipping — and replay it
   over those verdicts with the real :func:`qagent.verify.decide` (:mod:`qagent.evolve.rules`);
3. write the argument down and wait: nothing changes until a human approves it
   (:mod:`qagent.evolve.store`);
4. merge the accepted rule over the policy at run time, recording which rule was used
   (:mod:`qagent.evolve.apply`).

What this is deliberately not: a self-improving agent. The tool can widen its own net only
through a decision that is written in the repository and reviewed like code, and the evidence
for that decision is a fact about the past, not a claim about the future.
"""

from .apply import (
    IGNORED_REASON,
    Applicability,
    AppliedPolicy,
    Ignored,
    applicability,
    apply_proposals,
)
from .contracts import (
    PROPOSAL_ID_PATTERN,
    PROPOSAL_VERSION,
    Audit,
    Change,
    Evidence,
    Proposal,
    ProposalKind,
    ProposalStatus,
    Retrospective,
    check_digest,
    proposal_id,
)
from .history import History, RunFacts, Skipped, load_history
from .rules import MIN_FAILING_RUNS, audit, evidence_for, mine, promote, replay, retrospective
from .store import (
    PROPOSAL_DIR,
    ProposalError,
    load_proposals,
    proposals_dir,
    save_proposal,
)

__all__ = [
    "IGNORED_REASON",
    "MIN_FAILING_RUNS",
    "PROPOSAL_DIR",
    "PROPOSAL_ID_PATTERN",
    "PROPOSAL_VERSION",
    "Applicability",
    "AppliedPolicy",
    "Audit",
    "Change",
    "Evidence",
    "History",
    "Ignored",
    "Proposal",
    "ProposalError",
    "ProposalKind",
    "ProposalStatus",
    "Retrospective",
    "RunFacts",
    "Skipped",
    "applicability",
    "apply_proposals",
    "audit",
    "check_digest",
    "evidence_for",
    "load_history",
    "load_proposals",
    "mine",
    "promote",
    "proposal_id",
    "proposals_dir",
    "replay",
    "retrospective",
    "save_proposal",
]
