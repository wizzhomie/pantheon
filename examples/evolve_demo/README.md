# The evolution demo: a check that kept failing and never blocked

This directory is a small project with a real defect, three real verification runs, and the
proposal those runs produced. Nothing here is hand-written evidence: every file under
`history/` was written by `qagent verify` against this directory, and the proposal was mined
from those files.

## The defect

`app.py` says `total(...) -> int` and returns `sum(...) + 0.0`, so it returns a float. The
test suite is green — `14.0 == 14` — and stays green. A caller that formats the number prints
`14.0`; a caller that assumes integer money gets a float. The only thing in the project that
sees this is the type check.

## Why it kept shipping

`policy.toml` declares two checks: `tests` is required, `types` is **optional**. An optional
check never blocks; it produces a finding and the verdict becomes `PASS WITH CONDITIONS`. That
is a deliberate design (a contour that blocks on everything gets turned off), and it is exactly
how a real team ships the same failure three times: the report said something, nobody read it.

The three runs in `history/` are the evidence of that:

| Run | Verdict | `tests` | `types` |
|---|---|---|---|
| `run-1` | PASS WITH CONDITIONS | passed | failed |
| `run-2` | PASS WITH CONDITIONS | passed | failed |
| `run-3` | PASS WITH CONDITIONS | passed | failed |

## What the contour proposed

```bash
qagent learn --repo examples/evolve_demo \
  --policy examples/evolve_demo/policy.toml \
  --history examples/evolve_demo/history
```

```
history: 3 runs (2026-11-23 04:17 → 04:17); verdicts: pass_with_conditions 3
policy: evolve-demo, 2 checks, digest 33dd699f5016
proposed
  p-promote-types-ece7fe [proposed] Make the types check required
    change: types: optional -> required
    evidence: 3 of 3 recorded runs failed it; 3 still shipped, 0 were already held
```

The number that matters is `3 still shipped`: `qagent learn` took each recorded run and
re-decided it with the one field changed — `types` required — using the same `decide()`
function that will judge the next run. This is a counterfactual over evidence that already
exists, not a model's opinion and not a new run: nothing was executed and nothing was spent.

## The decision, and what it changed

Approving is a separate, deliberate command. It writes a file in the project (`.qagent/
proposals/`, reviewed like code); it does not edit `policy.toml`.

```bash
qagent learn --repo examples/evolve_demo --policy examples/evolve_demo/policy.toml \
  --history examples/evolve_demo/history \
  --approve p-promote-types-ece7fe --note "three green runs shipped this"
```

`history/run-4` is the next run, with the accepted rule in force. Same code, same defect,
same evidence — and the verdict is `HOLD`, because the check that sees the defect is now a
gate. The report names the rule it used (`Accepted rules` in the header), and the declared
policy is still exactly as a human wrote it, comment and all.

`qagent learn` on the four runs then reports the decision and what it has done since:

```
decided
  p-promote-types-ece7fe [approved, applied] Make the types check required
    change: types: optional -> required
    evidence: 3 of 3 recorded runs failed it; 3 still shipped, 0 were already held
    since the decision: 1 run, 1 failed, 1 held
```

## Reproducing it from scratch

The history is committed, so nothing has to be regenerated to read it. To rebuild the whole
demo (the tools come from the qagent checkout's environment):

```bash
cd examples/evolve_demo
rm -rf history .qagent
for n in 1 2 3; do
  (cd ../.. && PYTHONPATH=src PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m qagent verify \
    --repo examples/evolve_demo --policy policy.toml --out examples/evolve_demo/history/run-$n)
done
```

then `learn`, `--approve`, and one more `verify` into `history/run-4`.

Two details worth knowing, because they are the demo's own honest limits:

- `mypy` runs with `--explicit-package-bases` because this demo lives inside the qagent
  checkout's `examples` package and mypy would otherwise take the wrong root. The flag is
  about the demo's location, not about the defect.
- Three runs are enough to argue for a rule, not enough to prove the rule was right. That
  judgement is why approving is a human's step. The point of the demo is the loop, not the
  sample size.
