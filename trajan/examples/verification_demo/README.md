# Verification demo — a claim that the verdict does not believe

A tiny invoice module with one planted defect and a test suite that already catches it. The
point is not the bug; it is what `qagent verify` does with it.

The coding agent's report for this change would say: *done, the happy path works, tests pass.*
One of those is false, and the verification run does not have to trust any of it — it runs the
project's own tests, keeps the output, and computes the verdict from the exit code.

```bash
make verify-demo          # from the repository root
```

```bash
qagent verify --repo examples/verification_demo \
    --policy .qagent/policy.toml \
    --out reports/verification_demo
```

The committed result is [`reports/verification_demo/report.md`](../../reports/verification_demo/report.md):
verdict `HOLD`, one reason (`check-failed`), one finding (`check-tests`), a traceability row for
`R1` and `I1`, and the artifact
[`checks/tests.stdout.txt`](../../reports/verification_demo/checks/tests.stdout.txt) holding the
failing assertion. Exit code is `1`, so a pipeline can stop on it without parsing the report.

Where the defect is, for the curious: [`app.py`](app.py) ignores `Item.quantity`, and
[`test_app.py`](test_app.py) is the project's own test that says so. Nothing in the demo was
written to make the verification look good — the suite fails on the first run, exactly as it
would in a real repository.

The report pair is generated, not hand-written: `report.md` is the rendering of `report.json`,
and a test in `tests/unit/test_verify_verdict.py` re-renders every committed pair.

## The arrow: a finding that cites the requirement it breaks

The project also carries its own knowledge — [`.qagent/context.toml`](.qagent/context.toml) —
with one requirement and one invariant: `R1` says a line is counted as `price × quantity` and is
guarded by the `tests` check; `I1` says a destructive migration is marked by a human and is guarded
by `sql`. Both are `approved`, which is the part that matters: only approved promises bind.

Run the demo with a live reviewer and the arrow becomes visible:

```bash
qagent verify --repo examples/verification_demo --policy .qagent/policy.toml \
    --diff change.diff --reviewer qagent.review.factory:from_env \
    --out reports/verification_demo_review
```

The committed result is
[`reports/verification_demo_review/report.md`](../../reports/verification_demo_review/report.md):
the reviewer's finding `F1` names `R1`, the traceability row `R1` is `failed`, and it lists both
the check that failed for the same reason (`tests`) and the finding that cites it. Nothing in that
sentence is taken from the model: the model proposed "this ignores quantity", the anchor confirmed
the quote, and the report put the three facts in one row.

That run is the answer to a fair question a reader should ask — if the reviewer never cites a
requirement, is the traceability real? A first live run on a different change cited nothing; the
findings it made were about implementation risks that no approved promise covered, so citing one
would have been a lie. The demo shows the other side: where the promise *is* broken, the citation
appears. Both outcomes are computed by code (`Finding.requirement` is kept only when the id is
approved), and both are visible in the report.

## The plan under a change: a gate that was not needed

The policy declares a third check, `sql` (`check_migrations.py`), and ties it to the migrations
with `when_paths = ["migrations/**"]`. It is a real guard — a `DROP TABLE` without a
`-- destructive: ok` marker fails it — but it only matters to changes that touch the migrations.

`make verify-demo` runs without a change, so the plan keeps the gate: an unknown change may
touch anything. `make plan-demo` runs the same project with `change.diff` (it touches only
`app.py`) and the scripted reviewer, so the run costs nothing:

```bash
make plan-demo
```

The committed result is
[`reports/verification_demo_plan/report.md`](../../reports/verification_demo_plan/report.md):
the verdict is still `HOLD` (the planted defect is still there), and the report now carries a
`## Plan` section saying that `sql` was **not selected** because no changed path matches
`migrations/**`. The check is not silently missing and it is not a gap: it is a decision the
policy made in advance, and the report says so. A run whose every gate was not selected cannot
come out `PASS` — it is `INCONCLUSIVE`, because a change outside the net is not a verified one.

The reviewer in that run is `qagent.review.scripted:from_env`, labelled `scripted (no model
call)` in the report: the demo is about the plan, and a demo that quietly spends money is a bad
demo.

## Limits, and what they do not cover

The policy declares `[sandbox]`: 1 GB of address space and 16 MB per file for every check. The
committed reports show the limits in the header and carry a `## Sandbox` section that says what
they are *not*: a ceiling is not a boundary, the network and the file system are not isolated,
and a check can still write anywhere the user can. On a platform that cannot apply the limits,
the checks are recorded as errors rather than run without the ceiling they declared.
