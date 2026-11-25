# Pantheon — a personal AI engineering harness

Pantheon is one person's engineering environment for working with coding agents: a box per project,
three contours around it, and a rule that a claim is not evidence.

Everything the agents produce passes a gate that computes its own answer. Verification is not an
afterthought here; it is the part that already works, and it is where the project started.

## What runs today

| Part | State |
|---|---|
| **Trajan** — independent verification (`trajan/`) | **works**, covered by tests and measured on a held-out set: 690 tests, 25 benchmark cases, verdicts computed by code, 3 of 3 planted defects held and 0 of 4 clean changes held on the live run |
| **Box** — one project, one directory, one record | **works up to the agent**: `pantheon new` creates the box, `status` describes project, policy and requirements, `verify` runs trajan and keeps the report (a settled verdict about an unchanged tree is reused, with `reused from` in the record), `run` gates a patch by a contract, `dispatch` takes a task through its contours, and `up`/`down` start and stop the box's container — the image pinned by digest, the project mounted read-only, the state writable, 512 MB. The container runs the agent (`gateway run` under the image's own supervisor), and `status` asks it how it is instead of assuming |
| **Caesar** — execution gates: contracts, permissions, attempts | **works**: `pantheon run` reads `mandata/<id>.contract.toml` and applies a patch only when every path it touches is inside the declared scope; a delete the contract forbids is refused, `.git/` can never be authorised, and the attempt, its paths and the refusal land in the box either way |
| **Aurelius** — architecture model and deterministic drift check | **works**: `.pantheon/architecture.toml` compiles into one `import-linter` layers contract plus a forbidden contract per rule, and `pantheon arch` runs it. Two models are in the repository (trajan, the harness) and both hold; a third project was checked by hand. Without the tool the answer is `unchecked` with exit 2, never a silent pass |
| **Augustus** — orchestration inside the box | **works with a declared executor**: `pantheon dispatch` validates a task, routes it by its declared kind through a table, has the box's executor produce the change when the task carries none, forces every change through the write gate, calls the contours, and writes the record, the events in `fasti/`, one `acta/` summary and the task's state in `status` — including what the run did not check. A run that stopped before its verdict — the executor killed, the budget spent, the machine rebooted — is continued by `pantheon resume`, which reuses the recorded change instead of asking the model again and refuses to verify a tree that no longer holds it |
| **Executor** — the command that produces the change | **works**: the box declares `persona/executor.toml` — a command, a model name, a time and a cent limit. The harness hands it the task as JSON, checks the project was not written behind the gate, applies the returned diff through Caesar, and records tokens and money in the event, the dispatch record and the `acta/` summary. `bin/pantheon-executor-container` asks the agent inside the box; `bin/pantheon-executor` is the host adapter for an OpenAI-compatible endpoint, kept as a fallback for a box without an agent. A subscription CLI adapter is the next one |

The table is the honest status, not a roadmap promise: five rows are running code today, and the
box's container now runs its own agent with a profile and a model. The change is still produced as
a proposal and applied on the host through Caesar: the project is read-only inside the container,
so the boundary is a mount, not an instruction.

## Using the box

```console
$ ./bin/pantheon new trajan          # a box next to the project, project mounted by path
$ ./bin/pantheon status              # what the project promises, read, never executed
$ ./bin/pantheon verify               # trajan runs the project's checks, report stays in the box
$ ./bin/pantheon run <box> --contract mandata/T1.contract.toml --patch change.diff
$ ./bin/pantheon dispatch <box> --task mandata/t-1.toml   # the whole path: arch, gate, verdict, acta
$ ./bin/pantheon dispatch <box> --task mandata/t-2.toml   # with persona/executor.toml: it produces the patch
$ ./bin/pantheon resume <box> --task t-2                # continue a run that stopped before its verdict
$ ./bin/pantheon up <box> --image <pinned-digest>   # starts the box's container (mount only)
$ ./bin/pantheon down <box>          # stops it; the box's tasks and reports stay
```

`run` is Caesar, the gate: the contract declares which paths the change may touch and what it
must not do, and a patch that steps outside them is never applied — the attempt, its paths and
the refusal land in `tabularium/<task>/<n>/` and one event in `fasti/`. A refusal that is only
printed is a refusal nobody can audit.

A task can carry its own patch or let the box produce one: `persona/executor.toml` names a command
that gets the task as JSON and answers with a diff and what it spent. The harness stops it at its
deadline, catches it writing to the project behind the gate, and does not apply a change that cost
more than its budget. The protocol and the reference adapter are in [docs/10-executor.md](docs/10-executor.md);
without a profile the task must carry its patch, and the `acta/` summary says so.

`verify` runs the checks by default and costs nothing; a change is read only when someone is
asked to read it, because trajan refuses a change that nobody does. When the change is read, the
policy's `when_paths` decide which checks a change can even affect: a docs-only change is not
made to wait for the suite, and the report names the checks the plan passed over. `--review` asks the box's own
agent (`bin/pantheon-review`, the same container as the executor), `persona/reviewer.toml` declares
that every dispatch reads its change, and `--reviewer MODULE:FACTORY` names your own reader. What the reading cost is kept next to the run, and `dispatch` reports it in the
`acta/` summary next to the executor's bill. The exit code is trajan's own — 0 pass, 1 hold, 2 inconclusive —
and a test compares the two runs instead of trusting the wiring.

The harness is standard library only and needs no install: `bin/pantheon` runs from the checkout on
the system Python. Its tests are the other half of `make check`.

## Why a box

A project gets its own box — a directory with its own container, its own records (`fasti/`) and its own memory —
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
└── castra/       one per project, created by `pantheon new` (git-ignored)
```

`trajan/` is deliberately a self-contained package: it can be installed and run without the harness,
and nothing inside it imports the harness. A test enforces that boundary; independence is a property
of the tree, not a promise in a document.

## Licence

MIT.
