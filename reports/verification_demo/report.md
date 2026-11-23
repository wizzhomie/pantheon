# Verification report — HOLD

| | |
|---|---|
| Verdict | **HOLD** |
| Run | `20261123T033423-verification-demo` |
| Policy | `verification-demo` |
| Policy digest | `3cc9da588dc0` |
| Profile digest | `d577bf110964` |
| Started | 2026-11-23 03:34 UTC |
| Checked | 1 of 1 planned |
| Spent | 0.3s |

## Why

- `check-failed` — required check tests failed: exit 1, expected 0

## Checks

| Check | Status | Exit | Time | Evidence |
|---|---|---|---|---|
| `tests` | fail (exit 1, expected 0) | 1 | 0.33s | `checks/tests.stdout.txt` |

## Findings

| Finding | Severity | Status | Kind | Where |
|---|---|---|---|---|
| **check-tests** — test suite failed | blocking | confirmed | check-failure | — |

## How this verdict was reached

The verdict is computed from the checks above by code, not by a model: a failed or
timed-out required check and a confirmed blocking finding produce `HOLD`; a required
check that did not run produces `INCONCLUSIVE`; a non-blocking or unconfirmed finding
produces `PASS WITH CONDITIONS`. Nothing in this document overrides that rule.

Findings here come from the checks themselves; an independent reviewer that proposes
findings about a diff is the next phase and does not exist yet.

Markdown is a rendering of the JSON report next to it; the JSON is the record.
