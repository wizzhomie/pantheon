# Verification report — HOLD

| | |
|---|---|
| Verdict | **HOLD** |
| Run | `verification_demo_review` |
| Policy | `verification-demo` |
| Policy digest | `47540c3f1954` |
| Profile digest | `798ebe72f213` |
| Started | 2026-11-23 07:28 UTC |
| Checked | 2 of 2 planned |
| Spent | 0.3s |
| Sandbox | 1024 MB memory, 16 MB per file |

## Request

| | |
|---|---|
| Name | ad-hoc |
| Policy | `.qagent/policy.toml` |
| Change | diff `change.diff` |
| Reviewer | `qagent.review.factory:from_env` |

## Sandbox

Every check ran with 1024 MB memory, 16 MB per file, and core dumps were disabled.

These are resource ceilings, not isolation: a check can still reach the network and read or write any path the user running the agent can. trajan limits what a check may consume; it does not yet confine where it may go, and this report does not claim otherwise.

## Plan

Path-scoped checks run only when the change touches the paths they guard; a run without a change plans all of them.

| Check | Why it was not selected |
|---|---|
| `sql` | no changed path matches migrations/** |

## Why

- `check-failed` — required check tests failed: exit 1, expected 0
- `blocking-finding` — F1: Invoice total ignores item quantity (requirement R1)

## Checks

| Check | Status | Exit | Time | Evidence |
|---|---|---|---|---|
| `tests` | fail (exit 1, expected 0) | 1 | 0.34s | `checks/tests.stdout.txt` |
| `sql` | not selected (no changed path matches migrations/**) | — | 0.00s | `—` |

## Review

| | |
|---|---|
| Model | `deepseek-flash` |
| Diff | `ba3287847ebd` |
| Files shown | 1 |
| Proposed | 1 |
| Confirmed by the anchor | 1 |
| Unverified | 0 |
| Tokens | 416 in / 153 out |
| Time | 1.5s |

## Findings

| Finding | Severity | Status | Kind | Where |
|---|---|---|---|---|
| **check-tests** — test suite failed | blocking | confirmed | check-failure | — |
| **F1** — Invoice total ignores item quantity | blocking | confirmed | defect | `app.py:21` |

## Reviewer evidence

**F1** — `app.py:21`

> return float(sum(item.price for item in items))

## Traceability

2 approved, 1 failed, 1 not-selected.

| Requirement | Status | Checks | Findings |
|---|---|---|---|
| **R1** — Invoice total counts every item as price × quantity | failed | `tests` | F1 |
| **I1** — A destructive migration must be marked by a human | not-selected | `sql` | — |

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
