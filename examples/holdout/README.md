# Holdout bench — the contour graded on answers written down before the run

A contour that grades itself on the changes it was built for measures nothing: those cases are
already in its head. This suite is the opposite — eight changes, each with the answer written down
in `case.toml` before any run, so a regression shows up as a number instead of an opinion.

```bash
make bench          # from the repository root: the checks layer, free
```

```bash
qagent bench --suite examples/holdout \
    --reviewer qagent.review.scripted:from_env \
    --review-model "scripted (no model call)" \
    --out reports/holdout
```

The committed result is [`reports/holdout/bench.md`](../../reports/holdout/bench.md):
**3 of 3 planted defects held, 0 of 2 clean changes held**, in about six seconds and without a
model call. The live half of the suite costs cents and is described under [the reviewer
layer](#the-reviewer-layer).

The bench runs the project's own tools, so it needs what that project needs: `pytest` and `mypy`
on `PATH`. Without them a check cannot start, the run reports it as a failure, and every case
comes out `HOLD` — which is why CI installs both before running the suite.

## How a case is built

`base/` is a whole little project: an invoice total, its tests, a migration guard and the policy
that runs them. A case is `cases/<id>/` with two parts — `files/`, laid over a fresh copy of the
base, and `change.diff`, the change that is being verified. The runner copies the base to a
temporary directory, applies the case, and calls the ordinary `qagent verify` path. The bench has
no verification path of its own, on purpose: otherwise it would be measuring that path.

| Case | What it plants | Expected |
|---|---|---|
| `mutant-arithmetic` | the total ignores the quantity | `hold` — the project's own test fails |
| `mutant-types` | the annotation promises `int`, the code returns a float | `hold` — `mypy --strict` fails |
| `mutant-sql` | a migration drops a table without the human marker | `hold` — the migration guard fails |
| `control-clean` | a new function and its boundaries, with a test for each | `pass` — every gate passes |
| `control-unrelated` | a change to the README | `pass` — the migration guard is not selected |
| `reviewer-injection` | a customer name pasted into SQL | `finding` — only the reviewer can see it |
| `reviewer-percent` | the discount subtracts percentage points | `finding` — no test covers it |
| `reviewer-swallow` | the migration guard swallows every error | `finding` — the checks pass silently |

The verdicts are counted differently, and the difference is the point. A `hold` case that comes out
`PASS WITH CONDITIONS` is a **miss**: that verdict ships. A clean change that comes out `HOLD` is a
**false alarm**: it is the price of catching defects too eagerly, and it is reported with the same
weight. A clean change that comes back `PASS WITH CONDITIONS` blocks nothing and is therefore not a
false alarm — but it is not the answer the suite committed to either, so the report names it on a
`Result:` line instead of letting the headline ("0 of 2 clean changes held") stand in for it.

## Adding a case

1. Write `cases/<id>/files/...` with the change already applied, and `cases/<id>/change.diff`
   showing the same change.
2. Say the expected verdict in `cases/<id>/case.toml`, with a note explaining why:

```toml
expected = "hold"
note = "the total ignores the quantity, and the project's own test says so"
```

3. Run `make bench` (checks layer) or `qagent bench --layer reviewer` (only a model can pass
   those). If the answer changes, that is a decision about the contour, not a test to adjust:
   either the contour broke, or the case was wrong when it was written.

## The reviewer layer

Three cases carry `needs_reviewer = true` and `expected = "finding"`: a query that pastes a
customer name into SQL, a discount that subtracts percentage points, and a migration guard that
swallows every error. The project's own checks pass on all three — that is the point. Their answer
is a *confirmed finding*, not a verdict: grading the reviewer on `HOLD` would measure how severe
the model felt like calling the defect, when the question is whether it saw it at all.
`qagent bench --layer reviewer` runs just them, and with a scripted reviewer the honest result is
**0 of 3 caught**: nothing but a model can see these defects, so the number is the reviewer's, not
the checks'.

```bash
DEEPSEEK_API_KEY=... QAGENT_REVIEW_MODEL=deepseek-flash \
qagent bench --suite examples/holdout --layer all \
    --reviewer qagent.review.factory:from_env --review-model deepseek-flash \
    --out reports/holdout_all
```

That run costs money and is therefore not part of `make bench`. `--layer all` reviews every case,
so one report answers both questions a reviewer is asked — does it see the planted defects, and
does it invent anything on clean code. The committed result,
[`reports/holdout_all/bench.md`](../../reports/holdout_all/bench.md), is from 2026-11-23,
`deepseek-flash`, 8 of 8 cases: **3 of 3 planted defects held, 3 of 3 reviewer cases detected,
0 of 2 clean changes held**, 26.7 s, 3 798 tokens in and 1 615 out. The earlier reviewer-only run
is [`reports/holdout_reviewer/bench.md`](../../reports/holdout_reviewer/bench.md): 3 of 3, all
blocking, 11.8 s.

### What the first live run found

It found something in the suite, not in the contour. On `control-clean` the reviewer returned one
**minor** finding — the error paths of the new function had no tests — and the verdict became
`PASS WITH CONDITIONS` instead of `PASS`. The finding was true: the case added a function, a happy
path test, and nothing for `percent < 0`, `percent > 100`, or the two boundaries. So the case was
made genuinely clean (four tests added, and the diff regenerated to match) rather than the grading
being loosened to accept a condition on a "clean" change. The second run matched all eight. The
report keeps the story: `Result:` names every case that does not match, so a condition on a clean
change can never be hidden behind the headline.

## What is not measured here

A general mutator is deliberately not here. Mutation testing rewrites the project's code and asks
whether its own suite notices — that is an adequacy audit of the whole project, not a gate on one
change: it mutates the tree, takes minutes, and fits neither a check's timeout nor a run's budget.
The three `mutant-*` cases answer the same question at the scale of a change, one planted defect per
tool. If a project wants the full audit, it is `mutmut` or `cosmic-ray` run against that project,
not a feature of the contour — qagent runs a project's tooling, it does not reimplement it.

The prompts are not tuned against this suite — a case whose answer was adjusted because a model
disagreed with it stops being a holdout. Nothing here measures whether the reviewer stays accurate
as the diff grows; the cases are deliberately small enough to read in one screen.
