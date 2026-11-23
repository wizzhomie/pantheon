# Verification report — PASS WITH CONDITIONS

| | |
|---|---|
| Verdict | **PASS WITH CONDITIONS** |
| Run | `run-1` |
| Policy | `evolve-demo` |
| Policy digest | `33dd699f5016` |
| Profile digest | `faae798231af` |
| Started | 2026-11-23 04:19 UTC |
| Checked | 2 of 2 planned |
| Spent | 1.3s |

## Request

| | |
|---|---|
| Name | ad-hoc |
| Policy | `policy.toml` |

## Why

- `non-blocking-finding` — check-types (major): mypy failed

## Checks

| Check | Status | Exit | Time | Evidence |
|---|---|---|---|---|
| `tests` | pass | 0 | 0.33s | `checks/tests.stdout.txt` |
| `types` | fail (exit 1, expected 0) | 1 | 0.99s | `checks/types.stdout.txt` |

## Findings

| Finding | Severity | Status | Kind | Where |
|---|---|---|---|---|
| **check-types** — mypy failed | major | confirmed | check-failure | — |

## How this verdict was reached

The verdict is computed from the evidence above by code, not by a model: a failed or
timed-out required check and a confirmed blocking finding produce `HOLD`; a required
check that did not run, or a review that did not happen, produces `INCONCLUSIVE`; a
non-blocking or unconfirmed finding produces `PASS WITH CONDITIONS`.

A reviewer finding is `confirmed` only when the file is part of the change, the line
exists, and the quote is literally that line. Anything else stays `unverified`: it is
reported, and it cannot hold a release.

Markdown is a rendering of the JSON report next to it; the JSON is the record.
