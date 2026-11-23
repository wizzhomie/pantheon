# Holdout bench — the contour graded on answers written down before the run

A contour that grades itself on the changes it was built for measures nothing: those cases are
already in its head. This suite is the opposite — twelve changes, each with the answer written down
in `case.toml` before any run, so a regression shows up as a number instead of an opinion.

```bash
make bench          # from the repository root: the checks layer, free
```

```bash
trajan bench --suite examples/holdout \
    --reviewer trajan.review.scripted:from_env \
    --review-model "scripted (no model call)" \
    --out reports/holdout
```

The committed result is [`reports/holdout/bench.md`](../../reports/holdout/bench.md):
**3 of 3 planted defects held, 0 of 4 clean changes held**, in a few seconds and without a model
call. The live half of the suite costs cents and is described under [the reviewer
layer](#the-reviewer-layer).

The bench runs the project's own tools, so it needs what that project needs: `pytest` and `mypy`
on `PATH`. Without them a check cannot start, the run reports it as a failure, and every case
comes out `HOLD` — which is why CI installs both before running the suite.

## How a case is built

`base/` is a whole little project: an invoice total, its tests, a migration guard and the policy
that runs them. A case is `cases/<id>/` with two parts — `files/`, laid over a fresh copy of the
base, and `change.diff`, the change that is being verified. The runner copies the base to a
temporary directory, applies the case, and calls the ordinary `trajan verify` path. The bench has
no verification path of its own, on purpose: otherwise it would be measuring that path.

| Case | What it plants | Expected |
|---|---|---|
| `mutant-arithmetic` | the total ignores the quantity | `hold` — the project's own test fails |
| `mutant-types` | the annotation promises `int`, the code returns a float | `hold` — `mypy --strict` fails |
| `mutant-sql` | a migration drops a table without the human marker | `hold` — the migration guard fails |
| `control-clean` | a new function and its boundaries, with a test for each | `pass` — every gate passes |
| `control-unrelated` | a change to the README | `pass` — the migration guard is not selected |
| `control-rounding` | a correct `round()` VAT helper, with its tests | `pass` — correct code, correct tests |
| `control-refactor` | the same total written as a loop | `pass` — behaviour unchanged, tests unchanged |
| `reviewer-injection` | a customer name pasted into SQL | `finding` — only the reviewer can see it |
| `reviewer-percent` | the discount subtracts percentage points | `finding` — no test covers it |
| `reviewer-swallow` | the migration guard swallows every error | `finding` — the checks pass silently |
| `reviewer-falsy` | `if not quantity` turns an explicit `quantity=0` into one item | `finding` — no test passes zero |
| `reviewer-inplace` | a function "leaving the caller's list as it was" sorts it in place | `finding` — the caller's list is invisible to tests |

The verdicts are counted differently, and the difference is the point. A `hold` case that comes out
`PASS WITH CONDITIONS` is a **miss**: that verdict ships. A clean change that comes out `HOLD` is a
**false alarm**: it is the price of catching defects too eagerly, and it is reported with the same
weight. A clean change that comes back `PASS WITH CONDITIONS` blocks nothing and is therefore not a
false alarm — but it is not the answer the suite committed to either, so the report names it on a
`Result:` line instead of letting the headline ("0 of 4 clean changes held") stand in for it.

## Adding a case

1. Write `cases/<id>/files/...` with the change already applied, and `cases/<id>/change.diff`
   showing the same change.
2. Say the expected verdict in `cases/<id>/case.toml`, with a note explaining why:

```toml
expected = "hold"
note = "the total ignores the quantity, and the project's own test says so"
```

3. Run `make bench` (checks layer) or `trajan bench --layer reviewer` (only a model can pass
   those). If the answer changes, that is a decision about the contour, not a test to adjust:
   either the contour broke, or the case was wrong when it was written.

## The reviewer layer

Five cases carry `needs_reviewer = true` and `expected = "finding"`: a query that pastes a
customer name into SQL, a discount that subtracts percentage points, a migration guard that
swallows every error, a `if not quantity` that turns zero into one item, and a function documented
as not touching its argument that sorts it in place. The project's own checks pass on all five —
that is the point. Their answer
is a *confirmed finding*, not a verdict: grading the reviewer on `HOLD` would measure how severe
the model felt like calling the defect, when the question is whether it saw it at all.
`trajan bench --layer reviewer` runs just them, and with a scripted reviewer the honest result is
**0 of 5 caught**: nothing but a model can see these defects, so the number is the reviewer's, not
the checks'.

```bash
DEEPSEEK_API_KEY=... TRAJAN_REVIEW_MODEL=deepseek-flash \
trajan bench --suite examples/holdout --layer all --second-look \
    --reviewer trajan.review.factory:from_env --review-model deepseek-flash \
    --out reports/holdout_all
```

That run costs money and is therefore not part of `make bench`. `--layer all` reviews every case,
so one report answers both questions a reviewer is asked — does it see the planted defects, and
does it invent anything on clean code. The committed result,
[`reports/holdout_all/bench.md`](../../reports/holdout_all/bench.md), is from 2026-11-23,
`deepseek-flash` with `--second-look`, 12 cases: **3 of 3 planted defects held, 5 of 5 reviewer cases
detected, 0 of 4 clean changes held** — 11 of 12 matched the suite, 54.1 s, 11 045 tokens in and
3 759 out. One clean case, `control-rounding`, came back `PASS WITH CONDITIONS` on two minor
findings, which ships and is therefore not a false alarm — but it is not the answer the suite
committed to either, so the `Result:` line names it. Two of the five reviewer cases (`falsy`,
`inplace`) were confirmed as *non-blocking* findings: the contour saw the defect and shipped it with
a condition, which the bench counts as detected — grading the severity is a separate question, and
`reviewer-percent` remains the case where the model chose to block. The reviewer-only run is
[`reports/holdout_reviewer/bench.md`](../../reports/holdout_reviewer/bench.md): 5 of 5 detected,
23.4 s, 5 405 tokens in and 2 333 out.

### What live runs found

The first live run found something in the suite, not in the contour. On `control-clean` the
reviewer returned one **minor** finding — the error paths of the new function had no tests — and
the verdict became `PASS WITH CONDITIONS` instead of `PASS`. The finding was true: the case added a
function, a happy-path test, and nothing for `percent < 0`, `percent > 100`, or the two boundaries.
So the case was made genuinely clean (four tests added, and the diff regenerated to match) rather
than the grading being loosened to accept a condition on a "clean" change.

The run over the expanded suite found the opposite kind of thing: on `control-rounding` the reviewer
invented a blocking contradiction out of correct code and a correct test — `assert vat(19.99, 0.2) ==
4.0`, where `round(3.998, 2)` is `4.0`. Two clean controls was too small a sample to see that; four
is not, and the report said `1 of 4 clean changes held` rather than rounding it away. Nothing in
this suite was tuned in response, and the case was not edited: a holdout that gets an answer changed
because a model disagreed with it stops being a holdout.

That blocking claim is now a condition rather than a hold, and the change that produced could not be
tuned here. The prompt rule for it was tried on the dev set — the suite you are allowed to tune on,
[`examples/devset`](../devset/README.md), disjoint from this one — and it made the model *more*
willing to call a correct test wrong; the model's own reasoning retracted the claim inside the
finding's `detail` while the finding still shipped. The fix was a second reader, `--second-look`,
which drops a claim it cannot stand behind before the anchor ever sees it. This run is the holdout's
one measurement after that work, not a case that was adjusted: the same diff, the same expected
answer, a different pipeline.

## What is not measured here

A general mutator is deliberately not here. Mutation testing rewrites the project's code and asks
whether its own suite notices — that is an adequacy audit of the whole project, not a gate on one
change: it mutates the tree, takes minutes, and fits neither a check's timeout nor a run's budget.
The three `mutant-*` cases answer the same question at the scale of a change, one planted defect per
tool. If a project wants the full audit, it is `mutmut` or `cosmic-ray` run against that project,
not a feature of the contour — trajan runs a project's tooling, it does not reimplement it.

The prompts are not tuned against this suite — that happens on [`examples/devset`](../devset/README.md),
whose cases are disjoint from these, and this suite is run afterwards to see whether the change
generalized. Nothing here measures whether the reviewer stays accurate as the diff grows; the cases
are deliberately small enough to read in one screen.
