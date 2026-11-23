# Pantheon — a personal AI engineering harness

Pantheon is one person's engineering environment for working with coding agents: a box per project,
three contours around it, and a rule that a claim is not evidence.

Everything the agents produce passes a gate that computes its own answer. Verification is not an
afterthought here; it is the part that already works, and it is where the project started.

## What runs today

| Part | State |
|---|---|
| **Trajan** — independent verification (`trajan/`) | **works**, covered by tests and measured on a held-out set: 653 tests, 25 benchmark cases, verdicts computed by code, 3 of 3 planted defects held and 0 of 4 clean changes held on the live run |
| **Box** — one project, one container, one ledger | designed, not built |
| **Caesar** — execution gates: contracts, permissions, attempts | designed, not built |
| **Aurelius** — architecture model and deterministic drift check | designed, not built |
| **Augustus** — orchestration inside the box | designed, not built |

The table is the honest status, not a roadmap promise: only the first row is running code today.

## Why a box

A project gets its own box — a directory with its own container, its own ledger and its own memory —
so project context never mixes with another project's, and a box can be tarred up and reopened on
another machine. Your personal assistant agent stays outside every box and only reads summaries.

## Why trust the verdict

The verifier does not ask the agent how it went. It runs the project's own checks, reads the change
itself, and computes one of four answers:

| Verdict | Meaning |
|---|---|
| `PASS` | every required gate ran and passed, nothing was left unresolved |
| `PASS WITH CONDITIONS` | ships, but something is known and unresolved |
| `HOLD` | a required gate failed, or a defect is confirmed. Do not ship |
| `INCONCLUSIVE` | the run cannot answer: a gate did not run, the budget ran out, the run broke |

A model may propose findings; it cannot change the verdict. That is the whole point of the project.

## Documents

- [Concept](docs/concept.md) — the master context, in Russian, as written by the owner.
- [Architecture discovery](docs/README.md) — overview, responsibilities, integration, data model,
  ADRs, risks, MVP, roadmap, evaluation (Russian).
- [Trajan](trajan/README.md) — the verification contour: how it works, what it measures, what it
  refuses to claim.

## Layout

```
pantheon/
├── trajan/      the verification contour (Python, installable, runs on its own)
├── docs/        architecture discovery for the harness
└── boxes/       created per project by the box CLI (git-ignored)
```

`trajan/` is deliberately a self-contained package: it can be installed and run without the harness,
and nothing inside it imports the harness. A test enforces that boundary; independence is a property
of the tree, not a promise in a document.

## Licence

MIT.
