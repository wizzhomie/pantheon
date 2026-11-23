# Verification report — HOLD

| | |
|---|---|
| Verdict | **HOLD** |
| Run | `verification_demo` |
| Policy | `verification-demo` |
| Policy digest | `4fda65159630` |
| Profile digest | `94226da23fa7` |
| Started | 2026-11-23 04:25 UTC |
| Checked | 2 of 2 planned |
| Spent | 0.4s |

## Request

| | |
|---|---|
| Name | ad-hoc |
| Policy | `policy.toml` |

## Why

- `check-failed` — required check tests failed: exit 1, expected 0

## Checks

| Check | Status | Exit | Time | Evidence |
|---|---|---|---|---|
| `tests` | fail (exit 1, expected 0) | 1 | 0.39s | `checks/tests.stdout.txt` |
| `sql` | pass | 0 | 0.03s | `checks/sql.stdout.txt` |

## Findings

| Finding | Severity | Status | Kind | Where |
|---|---|---|---|---|
| **check-tests** — test suite failed | blocking | confirmed | check-failure | — |

## How this verdict was reached

The verdict is computed from the evidence above by code, not by a model: a failed or
timed-out required check and a confirmed blocking finding produce `HOLD`; a required
check that did not run, or a review that did not happen, produces `INCONCLUSIVE`; a
non-blocking or unconfirmed finding produces `PASS WITH CONDITIONS`.

A reviewer finding is `confirmed` only when the file is part of the change, the line
exists, and the quote is literally that line. Anything else stays `unverified`: it is
reported, and it cannot hold a release.

Markdown is a rendering of the JSON report next to it; the JSON is the record.
