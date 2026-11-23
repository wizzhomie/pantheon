# Verification report — HOLD

| | |
|---|---|
| Verdict | **HOLD** |
| Run | `verification_demo` |
| Policy | `verification-demo` |
| Policy digest | `47540c3f1954` |
| Profile digest | `798ebe72f213` |
| Started | 2026-11-23 07:28 UTC |
| Checked | 2 of 2 planned |
| Spent | 0.4s |
| Sandbox | 1024 MB memory, 16 MB per file |

## Request

| | |
|---|---|
| Name | ad-hoc |
| Policy | `.qagent/policy.toml` |

## Sandbox

Every check ran with 1024 MB memory, 16 MB per file, and core dumps were disabled.

These are resource ceilings, not isolation: a check can still reach the network and read or write any path the user running the agent can. trajan limits what a check may consume; it does not yet confine where it may go, and this report does not claim otherwise.

## Why

- `check-failed` — required check tests failed: exit 1, expected 0

## Checks

| Check | Status | Exit | Time | Evidence |
|---|---|---|---|---|
| `tests` | fail (exit 1, expected 0) | 1 | 0.34s | `checks/tests.stdout.txt` |
| `sql` | pass | 0 | 0.03s | `checks/sql.stdout.txt` |

## Findings

| Finding | Severity | Status | Kind | Where |
|---|---|---|---|---|
| **check-tests** — test suite failed | blocking | confirmed | check-failure | — |

## Traceability

2 approved, 1 covered, 1 failed.

| Requirement | Status | Checks | Findings |
|---|---|---|---|
| **R1** — Invoice total counts every item as price × quantity | failed | `tests` | — |
| **I1** — A destructive migration must be marked by a human | covered | `sql` | — |

Confirmed findings with no approved requirement: **check-tests**.

## How this verdict was reached

The verdict is computed from the evidence above by code, not by a model: a failed or
timed-out required check and a confirmed blocking finding produce `HOLD`; a required
check that did not run, or a review that did not happen, produces `INCONCLUSIVE`; a
non-blocking or unconfirmed finding produces `PASS WITH CONDITIONS`.

A reviewer finding is `confirmed` only when the file is part of the change, the line
exists, and the quote is literally that line. Anything else stays `unverified`: it is
reported, and it cannot hold a release.

Markdown is a rendering of the JSON report next to it; the JSON is the record.
