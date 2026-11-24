# Pantheon — a personal AI engineering harness

Pantheon is one person's engineering environment for working with coding agents: a box per project,
three contours around it, and a rule that a claim is not evidence.

Everything the agents produce passes a gate that computes its own answer. Verification is not an
afterthought here; it is the part that already works, and it is where the project started.

## What runs today

| Part | State |
|---|---|
| **Trajan** — independent verification (`trajan/`) | **works**, covered by tests and measured on a held-out set: 675 tests, 25 benchmark cases, verdicts computed by code, 3 of 3 planted defects held and 0 of 4 clean changes held on the live run |
| **Box** — one project, one directory, one ledger | **half works**: `pantheon new` creates the box, `pantheon status` describes project, policy and requirements, `pantheon verify` runs trajan and keeps the report, `pantheon run` gates a patch by a contract and writes the attempt and the refusal into the box ledger. No container, no agent yet — `pantheon up` refuses |
| **Caesar** — execution gates: contracts, permissions, attempts | designed, not built |
| **Aurelius** — architecture model and deterministic drift check | **works**: `.pantheon/architecture.toml` compiles into one `import-linter` layers contract plus a forbidden contract per rule, and `pantheon arch` runs it. Two models are in the repository (trajan, the harness) and both hold; a third project was checked by hand. Without the tool the answer is `unchecked` with exit 2, never a silent pass |
| **Augustus** — orchestration inside the box | designed, not built |

The table is the honest status, not a roadmap promise: the first two rows are running code today,
and the box row is only the half of it that can be built before there is an agent to put inside.

## Using the box

```console
$ ./bin/pantheon new trajan          # a box next to the project, project mounted by path
$ ./bin/pantheon status              # what the project promises, read, never executed
$ ./bin/pantheon verify               # trajan runs the project's checks, report stays in the box
$ ./bin/pantheon run <box> --contract tasks/T1.contract.toml --patch change.diff
$ ./bin/pantheon up                  # refuses: stage 1.1 has no container to start
```

`run` is Caesar, the gate: the contract declares which paths the change may touch and what it
must not do, and a patch that steps outside them is never applied — the attempt, its paths and
the refusal land in `artifacts/<task>/<n>/` and one event in `ledger/`. A refusal that is only
printed is a refusal nobody can audit.

`verify` runs the checks by default and costs nothing; a diff is reviewed only when a reviewer
is named with `--reviewer`, because trajan refuses a change that nobody reads. The exit code is
trajan's own — 0 pass, 1 hold, 2 inconclusive — and a test compares the two runs instead of
trusting the wiring.

The harness is standard library only and needs no install: `bin/pantheon` runs from the checkout on
the system Python. Its tests are the other half of `make check`.

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
├── harness/     the box: create it, describe it; standard library only
├── bin/pantheon the command a person types
├── tests/       the harness's own tests
├── trajan/      the verification contour (Python, installable, runs on its own)
├── docs/        architecture discovery for the harness
└── boxes/       one per project, created by `pantheon new` (git-ignored)
```

`trajan/` is deliberately a self-contained package: it can be installed and run without the harness,
and nothing inside it imports the harness. A test enforces that boundary; independence is a property
of the tree, not a promise in a document.

## Licence

MIT.
