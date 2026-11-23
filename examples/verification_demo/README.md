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
    --policy examples/verification_demo/policy.toml \
    --out reports/verification_demo
```

The committed result is [`reports/verification_demo/report.md`](../../reports/verification_demo/report.md):
verdict `HOLD`, one reason (`check-failed`), one finding (`check-tests`), and the artifact
[`checks/tests.stdout.txt`](../../reports/verification_demo/checks/tests.stdout.txt) holding the
failing assertion. Exit code is `1`, so a pipeline can stop on it without parsing the report.

Where the defect is, for the curious: [`app.py`](app.py) ignores `Item.quantity`, and
[`test_app.py`](test_app.py) is the project's own test that says so. Nothing in the demo was
written to make the verification look good — the suite fails on the first run, exactly as it
would in a real repository.

The report pair is generated, not hand-written: `report.md` is the rendering of `report.json`,
and a test in `tests/unit/test_verify_verdict.py` re-renders every committed pair.

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
