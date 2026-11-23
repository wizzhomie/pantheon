# Holdout bench — the contour graded on answers written down before the run

A contour that grades itself on the changes it was built for measures nothing: those cases are
already in its head. This suite is the opposite — five changes, each with the verdict written
down in `case.toml` before any run, so a regression shows up as a number instead of an opinion.

```bash
make bench          # from the repository root
```

```bash
qagent bench --suite examples/holdout \
    --reviewer qagent.review.scripted:from_env \
    --review-model "scripted (no model call)" \
    --out reports/holdout
```

The committed result is [`reports/holdout/bench.md`](../../reports/holdout/bench.md):
**3 of 3 planted defects held, 0 of 2 clean changes held**, in about six seconds and without a
model call.

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
| `control-clean` | a new function and its test | `pass` — every gate passes |
| `control-unrelated` | a change to the README | `pass` — the migration guard is not selected |
| `reviewer-injection` | a customer name pasted into SQL | `finding` — only the reviewer can see it |
| `reviewer-percent` | the discount subtracts percentage points | `finding` — no test covers it |
| `reviewer-swallow` | the migration guard swallows every error | `finding` — the checks pass silently |

The two verdicts are counted differently, and the difference is the point. A `hold` case that
comes out `PASS WITH CONDITIONS` is a **miss**: that verdict ships. A clean change that comes out
`HOLD` is a **false alarm**: it is the price of catching defects too eagerly, and it is reported
with the same weight.

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

Three more cases carry `needs_reviewer = true` and `expected = "finding"`: a query that pastes a
customer name into SQL, a discount that subtracts percentage points, and a migration guard that
swallows every error. The project's own checks pass on all three — that is the point. Their answer
is a *confirmed finding*, not a verdict: grading the reviewer on `HOLD` would measure how severe
the model felt like calling the defect, when the question is whether it saw it at all. `qagent bench --layer reviewer` runs
just them, and with a scripted reviewer the honest result is **0 of 3 caught**: nothing but a
model can see these defects, so the number is the reviewer's, not the checks'.

```bash
DEEPSEEK_API_KEY=... QAGENT_REVIEW_MODEL=deepseek-flash \
qagent bench --suite examples/holdout --layer reviewer \
    --reviewer qagent.review.factory:from_env --review-model deepseek-flash
```

That run costs money (~9k prompt tokens per case) and is therefore not part of `make bench`.
`--layer all` measures both halves in one report and keeps them apart in the summary; the cache
means a re-run of the same cases is free.

## What is not measured here

The reviewer is scripted in `make bench` (it proposes nothing, so the verdict comes from
deterministic checks only) — the honest reason is that a live reviewer costs money and is not
deterministic. A live holdout, where the planted defect is invisible to the project's own suite
and only the reviewer can catch it, is the next step, not this one. Mutation testing proper
(mutating the project's code and checking that its own suite notices) is also still ahead.
