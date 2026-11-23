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
