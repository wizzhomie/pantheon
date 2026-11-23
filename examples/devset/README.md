# Dev set — the cases tuning is allowed on

The [holdout](../holdout/README.md) is the contour's exam: its answers were written before any run,
and a case is never edited because a model disagreed with it. That rule has a price — it leaves
nowhere to try a change. This suite is that place. Thirteen changes with their answers written down,
disjoint from the holdout, and explicitly fair game: a prompt, a rule or a pipeline step may be
changed because of what a case here shows, and the holdout is then measured once to see whether the
change generalized.

```bash
make bench-dev      # from the repository root: the checks layer, free
```

```bash
qagent bench --suite examples/devset --layer all --second-look \
    --reviewer qagent.review.factory:from_env --review-model deepseek-flash \
    --out reports/devset_all
```

The live runs cost money and are not part of `make bench-dev`. Three of them are committed:

| Report | What it ran | Result |
|---|---|---|
| [`devset_all_baseline`](../../reports/devset_all_baseline/bench.md) | the first prompt, no second look | 8 of 13 matched: 3 of 3 defects, 4 of 4 reviewer cases, **5 of 6 clean changes came back with a condition** |
| [`devset_all_window`](../../reports/devset_all_window/bench.md) | plus the "you see a window, not the file" rule | 9 of 13 matched: three claims about names and tests outside the diff are gone, and `control-float` passes |
| [`devset_all`](../../reports/devset_all/bench.md) | plus the second reader | 9 of 13 matched: **0 of 6 clean changes held**, 4 of 6 with a non-blocking condition, every defect and every reviewer case still found |

The two off-answer classes are different, and only one of them is a prompt problem.

*The window.* A reviewer sees the changed lines, not the whole file, and the first prompt did not say
so: it reported `line_total` as undefined and a new test as referring to something not imported, both
of which were defined a few lines above the window. One paragraph fixed that class, and the
`devset_all_window` run measures it.

*The self-retracting claim.* On `control-vat` the first reader called a correct test wrong. A prompt
rule asking it to work out the arithmetic first did not help — it made the model bolder, and the
finding it then produced carried its own refutation: `7.35 * 0.19 = 1.3965, and round(1.3965, 2)
gives 1.4 ... the assertion holds. Wait, that means it passes; no finding. (Retracted.)` The
decision to keep the finding is made before the reasoning that refutes it, and no wording in the same
prompt changes that. The fix is a second, separate reader whose only job is to drop such claims:
`--second-look`, one more request per review, counted into the tokens. On the holdout it turned the
one blocking false alarm into a condition the change ships with.

## The cases

`base/` is a small billing project (an invoice total, money in cents, a migration guard) with the
same three checks as the holdout project. A case is `cases/<id>/` with `files/` laid over a fresh
copy of the base and `change.diff` to verify.

| Case | What it plants | Expected |
|---|---|---|
| `mutant-quantity` | the line total ignores the quantity | `hold` — the project's own test fails |
| `mutant-types` | the annotation promises `int`, the code returns a float | `hold` — `mypy --strict` fails |
| `mutant-sql` | a migration drops a table without the human marker | `hold` — the migration guard fails |
| `control-vat` | correct rounded VAT, with tests — one asserts a value only the rounding explains | `pass` — correct code, correct tests |
| `control-vat-gross` | the same for an amount with VAT added | `pass` |
| `control-float` | a test pinning the binary-float sum `0.1 * 3` | `pass` — the test asserts what the code returns |
| `control-refactor` | the invoice total rewritten as a loop | `pass` — behaviour unchanged |
| `control-docs` | a README change | `pass` — the migration guard is not selected |
| `control-tests-only` | a test for behaviour that already worked | `pass` — no production change |
| `reviewer-injection` | a customer name concatenated into SQL | `finding` — the checks pass on it |
| `reviewer-mutable-default` | `fees: list[float] = []` shared by every call | `finding` |
| `reviewer-silent-parse` | an unreadable amount silently becomes zero | `finding` |
| `reviewer-empty-average` | an empty invoice divides by zero | `finding` |

`devset` (checks layer) is committed at [`reports/devset/bench.md`](../../reports/devset/bench.md):
3 of 3 planted defects held, 0 of 6 clean changes held, no model call.

## What the set is for, and what it is not

It is a tuning instrument, not a second exam. The two questions it is built to answer are "does the
reviewer invent a problem on clean code" and "does it still see a real one", and it is deliberately
small enough to re-run for cents after every change to a prompt or to the pipeline.

The rule that keeps it useful is the one the holdout lives by, applied to a different set: **a case is
edited only when the case was wrong**, never to make a model's answer look right. `control-vat` was
not made simpler after the model kept calling its test wrong; the finding was fixed, not the case.
The arithmetic cases stay, because the class they represent is real.

A case added here should be one of three things: a defect the project's own checks cannot see, a
clean change that a reviewer could plausibly mistake for a defect, or a claim the pipeline itself can
get wrong (a citation, a requirement, a severity). If a case cannot be wrong in a way that costs
anything, it measures noise.
