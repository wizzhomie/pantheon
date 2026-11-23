# Verification report — HOLD

| | |
|---|---|
| Verdict | **HOLD** |
| Run | `verification_demo_plan` |
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
| Change | diff `change.diff` |
| Reviewer | `qagent.review.scripted:from_env` |

## Plan

Path-scoped checks run only when the change touches the paths they guard; a run without a change plans all of them.

| Check | Why it was not selected |
|---|---|
| `sql` | no changed path matches migrations/** |

## Why

- `check-failed` — required check tests failed: exit 1, expected 0

## Checks

| Check | Status | Exit | Time | Evidence |
|---|---|---|---|---|
| `tests` | fail (exit 1, expected 0) | 1 | 0.38s | `checks/tests.stdout.txt` |
| `sql` | not selected (no changed path matches migrations/**) | — | 0.00s | `—` |

## Review

| | |
|---|---|
| Model | `scripted (no model call)` |
| Diff | `ba3287847ebd` |
| Files reviewed | 1 |
| Proposed | 0 |
| Confirmed by the anchor | 0 |
| Unverified | 0 |
| Tokens | 0 in / 0 out |
| Time | 0.0s |

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
