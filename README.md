# qagent

[![ci](https://github.com/wizzhomie/qagent/actions/workflows/ci.yml/badge.svg)](https://github.com/wizzhomie/qagent/actions/workflows/ci.yml)

**Independent verification for AI-assisted development.** You hand it a change — a commit, a diff,
a dataset run — and it returns findings and a verdict backed by evidence it collected itself, not
by what the coding agent said about its own work.

*[Читать по-русски →](README.ru.md)*

> **Where the project is right now.** The evaluation core works and is measured: deterministic
> checks, a rubric judge, a bounded correction loop, offline regression with report diff, two
> examples with committed live runs and cassettes. The verification loop is being built phase by
> phase; the plan and the decisions are in [`docs/roadmap.md`](docs/roadmap.md). What works
> already: the project profile (`qagent inspect`), the deterministic engine (`qagent run`), the
> end-to-end pass — checks → findings → verdict → report (`qagent verify`) — the independent
> reviewer, which proposes findings about a diff and has each one checked against the file, and
> evolution of the rules (`qagent learn`): the loop proposes a change to its own checks, proves it
> on the recorded runs, and waits for a human's approval.

---

## Why

An AI agent finishes a task and reports: done, tests pass, requirements met, no regressions.
None of that is evidence. The failure modes are boring and expensive:

| Claim | What actually happens | Cost |
|---|---|---|
| "Tests pass" | they pass on a subset, or were weakened to pass | a regression ships |
| "Done" | the happy path is done, the edge cases are not | found by a user |
| "Requirements met" | something plausible was built, not the requested thing | rework |
| "No regressions" | nobody ran the paths that regressed | incident |
| "Architecture respected" | the layer was imported directly, tomorrow it is load-bearing | slow rot |

The principle this project is built around:

> **AI-generated claims are not verification evidence.**

Tool output, reproducible checks and findings that can be re-derived are evidence. Text is not. A
verification system has to get its own access to the data, run the available tools itself, and
conclude from what it saw — including saying "I could not check this".

## What it is / isn't

| It is | It isn't |
|---|---|
| An independent verification loop around a project: context → plan → evidence → findings → verdict | A coding agent, an IDE, or a CI platform |
| A library and a CLI (`pip install -e .` for now, not on PyPI) | A service, a dashboard, or a multi-tenant platform |
| A universal core: the same mechanics for any repository | A collection of hardcoded rules about specific projects |
| Measured against planted defects, with its own evaluation loop | A prompt library, or a wrapper around one vendor SDK |

Deliberately out of scope: writing or changing the verified project, autonomous self-improvement,
reimplementing static analyzers that already exist, and any "run everything by default" mode —
verification is selective and budgeted by design ([why](docs/roadmap.md)).

## How it works (target)

```mermaid
flowchart LR
    R[request<br/>commit · diff · dataset] --> P[plan<br/>what to check and what it costs]
    P --> D[deterministic tools<br/>tests · lint · types · custom]
    D --> C[context<br/>requirements · invariants · milestones]
    C --> V[independent reviewer<br/>diff + evidence]
    V --> F[findings<br/>file · line · quote · severity]
    F --> D2{verdict by code}
    D2 -->|PASS| OK[ship]
    D2 -->|WITH CONDITIONS · HOLD · INCONCLUSIVE| HU[human decides]
    OK --> REP[report: what ran, what it cost, what was not checked]
    HU --> REP
```

1. **Context.** What this project is, how it is built, what it promises, what must not break.
2. **Plan.** Cheap deterministic checks first, money spent only where they leave a question.
3. **Evidence.** Every check produces a structured result with captured output; a check that did
   not run is `error` or `not applicable`, never `ok`. Checks run under the resource ceilings the
   policy declares, and the report calls those ceilings — not isolation.
4. **Review.** A model reads the diff and the evidence and proposes findings — but each finding
   must be anchored: the file exists, the line is in range, the quote is verbatim on that line.
   A finding that fails the anchor is reported as unverified, not silently dropped.
5. **Verdict.** The code computes it from gates, evidence and policy — never from a score the
   model produced: `PASS`, `PASS WITH CONDITIONS`, `HOLD`, `INCONCLUSIVE`.
6. **Report.** Scope, checks, findings, evidence, limitations, what was skipped and why, what it
   cost.

## Two levels: core and project

The core knows how to verify; it knows nothing about your project. Knowledge about the project
lives in the project itself, in `.qagent/`: profile, architecture baseline and target, milestones,
requirements, invariants, verification policies. It is committed with the code and reviewed like
the code, because it goes stale at the same speed. QAgent stays a tool, not a store of other
people's projects.

Verification modes (`pre-commit`, `change review`, `feature evaluation`, `architecture review`,
`security audit`, `regression`, `dataset evaluation`, `full audit`) are **data**: each is a policy
file naming mandatory gates, depth and cost ceiling. The code path is one.

## Selective and budgeted

There is no mode that runs everything. A run starts from a request and a budget, and the plan
orders work from cheap to expensive: project commands (tests, lint, types) cost seconds and no
tokens; the reviewer costs money, so it gets the changed files and the evidence, after the
deterministic tools have spoken, and only if there is still a question. When the budget runs out
before the questions do, the verdict says `INCONCLUSIVE` — never a quiet `PASS`. The report lists
what was not checked and why; a second run on the same inputs is cached.

A check can also say what it guards: `when_paths` makes it a gate for part of the repository, so a
change to `app.py` does not pay for the migration guard. The plan is built from the change, every
check that was not selected is listed with its reason, and a run with **no** change plans
everything — an unknown change may touch anything, and a cheap plan that drops a gate is not cheap.

## What runs today

The table below is the honest part: what is built, what it does, and how to see it work. The
evaluation core provides the evidence and the ability to measure the loop itself; the verification
engine turns that evidence into a verdict.

| Piece | What it does | Proof |
|---|---|---|
| Deterministic checks | required fields, formats, ranges, cross-field rules, quoted evidence, PII, refusals, duplicates | 9 built-in checks, extendable by config or code |
| Independent judge | a model scores a data-driven rubric; **the code** recomputes pass/score from weights | judge cannot grade itself |
| Bounded loop | structured feedback, at most N attempts, cost and latency budget, escalation with a reason | every step recorded in the trace |
| Preset as a file | schema, checks, rubric, prompts, limits, prices, report — one `qagent.toml` | config and code build the same preset, fingerprint-tested |
| Offline regression | dataset → run → scoring → metrics → report → diff of two runs | comparable only with matching hashes |
| Reproducibility | committed cassettes replay a live run with no key and no bill | `make replay` |
| Project profile | reads a repository from disk — languages, package managers, test/lint/type commands, entry points — with no model and no network | `qagent inspect`, deterministic and digest-stable |
| Command execution | runs the project's own checks — argv only, whitelisted environment, timeout, budget — and keeps stdout/stderr as artifacts | `qagent run`; a missing tool is `not_applicable`, not a pass |
| Resource limits | every check runs under a memory and file-size ceiling from `[sandbox]`, and the report states in the same breath what is *not* isolated: the network and the file system | `make verify` on this repo; a check over the ceiling is named as that, and a platform that cannot apply limits refuses to run the check ungated |
| Independent reviewer | a model reads the diff and proposes findings; the anchor confirms only a file in the change, a line that exists, and a verbatim quote | `qagent verify --diff ... --reviewer ...`; an unverified finding never blocks |
| Review cache | a review already paid for is reused only when the question is byte-identical — prompt, model, temperature, token ceiling — and the report says the answer is cached | a second run on the same change calls no model; `--no-cache` forces a fresh one |
| Project knowledge | requirements, invariants, milestones and the architecture baseline/target in `.qagent/context.toml`, each with `approved` / `proposed` / `superseded` | `qagent context --check`; only approved items bind |
| Orchestrator | one request (policy, change, reviewer, budget) becomes one stored run, and the run can be read back and explained | `qagent verify --request request.toml`, `qagent show <run>` |
| Traceability | each approved requirement or invariant is traced to its checks, the findings that cite it, and one of four states: covered, failed, not-run, unchecked | a `## Traceability` section in every report; the reviewer may only cite approved ids; the demo's live run has finding `F1` citing `R1`, and the row is `failed` for both reasons |
| Plan under the change | a check can declare the paths it guards; a run plans it only when the change touches them, and the report says why it did not run | `make plan-demo`: the SQL gate is not selected for an `app.py` change |
| Findings and verdict | failures become structured findings; four verdicts computed by code from the checks, with the reason for each | `qagent verify`; `HOLD` is exit 1, `INCONCLUSIVE` is exit 2 |
| Verification report | verdict, reasons, every check with its evidence, findings, and the list of what was **not** checked | `report.md`, rendered from `report.json` |
| Evolution of the rules | reads the decided runs, proposes making an optional check required, and re-decides past verdicts with the same `decide()`; an accepted rule is a file in the project, the policy is not rewritten | `make evolve-demo`: three runs, a proposal, `HOLD` after approval |
| Holdout bench | a fixed suite of changes whose verdict is written down before the run: planted defects must be held, clean changes must not be, and a miss is counted apart from a false alarm; three cases carry defects only a reviewer can see | `make bench`: 3 of 3 checks-layer defects held, 0 of 2 clean changes held, no model call; live `--layer all`: 3 of 3 held, 3 of 3 reviewer cases detected, 0 of 2 clean changes held |

```bash
qagent inspect --repo . --write            # describe the repository: stack, checks, layout
qagent context --repo . --check            # what the project promises, and the links to checks
qagent run --repo . --policy .qagent/policy.toml   # run its checks, keep the evidence
qagent verify --repo .                     # run, decide a verdict, write the report pair
qagent verify --repo . --diff change.diff --reviewer qagent.review.factory:from_env  # + review
qagent verify --repo . --request request.toml      # the same, described in a file
qagent show .qagent/runs/<run id>                  # explain a stored run, run nothing
qagent bench --suite examples/holdout --reviewer qagent.review.scripted:from_env  # holdout the contour is graded on
qagent learn --repo .                      # what the recorded runs argue should change, and why
qagent learn --repo . --approve <id> --note "why"     # record the decision on a proposal
qagent init --dir ./qa                     # starter config plus a one-case dataset
qagent check --config qagent.toml          # validate config and dataset, run nothing
qagent eval  --config qagent.toml --out reports/guarded.json --markdown reports/guarded.md
qagent record --config qagent.toml         # live model, answers written to the cassette
qagent replay --config qagent.toml         # same numbers from the cassette, free
qagent compare a.json b.json               # what changed between two runs
```

`qagent run` and `qagent verify` execute the commands exactly as the policy writes them. The
policy in this repo expects the checkout's `.venv/bin` on `PATH`, the same assumption the Makefile
makes with `PYTHON=.venv/bin/python`.

`qagent verify` is the first end-to-end pass of the loop: it runs the policy, turns failures into
findings, computes the verdict in code, and writes `report.json` + `report.md` into the run
directory. The exit code is `0` for `PASS` / `PASS WITH CONDITIONS`, `1` for `HOLD`, `2` for
`INCONCLUSIVE` — a pipeline can stop on it without parsing prose. On this repository, `make verify`
gives `PASS` with three checks in a few seconds, under the limits its own policy declares, and
without a model call.

`qagent learn` is the other half: the loop not only verifies, it proposes **what** to verify. It
reads the decided runs, finds an optional check that failed in runs that shipped anyway, and
re-decides those same runs with that check required — same verdict code, same recorded evidence.
Nothing is executed and nothing is spent. The proposal is data: what would change, which runs
justify it, how many verdicts it would have stopped. Only a human can accept it: `--approve`
writes a file in the project, and `policy.toml` stays as its author wrote it. Details in
[`docs/roadmap.md`](docs/roadmap.md), and the demo is
[`examples/evolve_demo/`](examples/evolve_demo/README.md).

### Example 0 — a claim the verdict does not believe (HOLD)

A tiny invoice module with a planted defect: it ignores `Item.quantity`, so the happy path looks
right and one of the project's own tests fails. `make verify-demo` runs the policy, gets exit 1 from
the test suite, and produces [`reports/verification_demo/report.md`](reports/verification_demo/report.md)
— verdict `HOLD`, one reason, one blocking finding, and the failing assertion kept as an artifact.
No model was asked whether the tests pass: the exit code is the evidence. The project also states
its own requirement, so the same report carries a traceability row: `R1` is `failed`, with the check
that failed and — in the live run of the same demo — the finding that cites it. The demo project is
[`examples/verification_demo/`](examples/verification_demo/README.md).

### Example 0b — the reviewer on its own commit

On 2026-11-23 the reviewer read the diff of the commit that introduced it: four proposals, all
four quotes confirmed by the anchor, and three of the four were real problems in code written
minutes earlier — a byte ceiling counted in characters, a path comparison without normalisation,
and an anchor that read the working tree without asking whether the diff had shown that line. The
report is [`reports/review_self/report.md`](reports/review_self/report.md), and
[its README](reports/review_self/README.md) says what happened to each finding. A real model call,
8 827 input tokens, 4.1 s.

### Example 0c — a rule the history earned

[`examples/evolve_demo/`](examples/evolve_demo/README.md) is a project with a real defect: the
annotation promises an `int`, the function returns a `float`, the tests are green, and the type
check sees it — but that check is optional in the policy. Three real runs in `history/` all look the
same: `PASS WITH CONDITIONS`, the type check failed, the change shipped. `make evolve-demo` proposes
making it required and says which three verdicts that would have stopped. After `--approve`, the
next run over the same defect is `HOLD` — same evidence, a different rule, and the report's header
names the rule that was in force.

### Example 1 — logistics intake (32 cases)

An email → structured fields step, guarded: 59.4% correct on the first attempt, **81.2% after the
loop**, false passes 40.6% → 18.8%, $0.108 for the run. The report, the diff against the baseline
run and the cassette are committed in [`reports/`](reports/README.md) and
[`examples/logistics_intake/`](examples/logistics_intake/README.md).

### Example 2 — code review on seeded bugs (24 cases)

A reviewer that must point at the planted bug by line and verbatim quote. Cheap anchor checks
reject an invented quote, a line outside the file and a verdict that contradicts the findings, for
free, before any judge is asked. Live run: **87.5% final, 0 false passes** (12.5% before), $0.0477
for 24 cases, p50 2.9 s. This example is also the template the verification loop now uses to
validate findings: [`examples/code_review/`](examples/code_review/README.md).

### A request is a file

A run is one request: which policy, which change, which reviewer, how many tokens. The command
line can name those, or a file can:

```toml
name = "review the checkout fix"
policy = ".qagent/policy.toml"
since = "HEAD~1"                                  # or: diff = "change.patch"
reviewer = "qagent.review.factory:from_env"
instructions = ".qagent/review.md"
max_tokens = 1500
```

`qagent verify --repo . --request request.toml` stores the request inside the report, so
`qagent show` can answer "what was asked, what happened, what does it mean" months later —
without running a check or calling a model. That is the whole point of the orchestrator: a
verdict you cannot reconstruct is a sentence, not a record.

## How the two halves fit

The evaluation core does not sit beside the verification loop — it gets absorbed into it. Concrete
plan, in order:

1. A dataset run becomes a **check type** inside the verification engine: the policy names a
   dataset and a config, the engine runs it as a tool with a timeout and attaches its report as
   evidence.
2. The rubric judge becomes one of the **reviewers** — it can read a change, not only a single
   answer.
3. The loop's checks and retries become how the behaviour of a step is **measured**, which is what
   makes the verification loop itself measurable.

Nothing already built is deleted or renamed for elegance; the old modules change only when their
new role is actually used.

## Roadmap

| Phase | What | Status |
|---|---|---|
| 0 | Architecture assessment: what exists, what the gap is, decisions | done — [`docs/roadmap.md`](docs/roadmap.md) |
| 1 | Project context: profile, requirements, invariants, baseline | done — `qagent inspect` and `qagent context` |
| 2 | Verification orchestrator: request, plan, run, persistence | done — `--request` and `qagent show` |
| 3 | Deterministic engine: run project tools, capture evidence, statuses | done — `qagent run` |
| 4 | Independent reviewer: change analysis, architecture, tests, risk | done — `qagent verify --diff` with a reviewer |
| 5 | Findings, verdict, verification report | done — `qagent verify` |
| 6 | Architecture contracts and traceability | done — requirement → check → finding → verdict in the report |
| 7 | Verification evolution: proposed rules, validation, promotion | done — `qagent learn`, decisions in `.qagent/proposals/` |
| 8 | Risk-based plans, resource limits, holdout bench, mutation testing | done — plan under the change, `[sandbox]` limits, the holdout bench and its live reviewer layer, false alarms included (0 of 2 clean changes held on a live run); a general mutator is deliberately outside the contour: it rewrites the tree and takes minutes, so it is a project audit, not a gate on a change; real isolation needs an environment this machine does not have, and the contour says so instead of pretending |

## Docs

- [`docs/roadmap.md`](docs/roadmap.md) — vision, principles, phases, decisions, non-goals.
- [`docs/design.md`](docs/design.md) — why the core is built the way it is, with the alternatives.
- [`docs/how-it-works.md`](docs/how-it-works.md) — the same, in plain language, step by step.
- [`reports/README.md`](reports/README.md) — the committed runs and how to read them.

## License

MIT.
