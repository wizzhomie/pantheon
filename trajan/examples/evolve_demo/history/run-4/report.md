# Verification report — HOLD

| | |
|---|---|
| Verdict | **HOLD** |
| Run | `run-4` |
| Policy | `evolve-demo` |
| Policy digest | `d3483d185955` |
| Profile digest | `987f265b5273` |
| Started | 2026-11-23 04:20 UTC |
| Checked | 2 of 2 planned |
| Spent | 0.5s |
| Accepted rules | `p-promote-types-ece7fe` |

This run also used rules the project accepted from its own history: `p-promote-types-ece7fe`. Each one is a file in `.qagent/proposals/`, with the recorded runs
behind it; the declared policy was not edited.

## Request

| | |
|---|---|
| Name | ad-hoc |
| Policy | `policy.toml` |

## Why

- `check-failed` — required check types failed: exit 1, expected 0

## Checks

| Check | Status | Exit | Time | Evidence |
|---|---|---|---|---|
| `tests` | pass | 0 | 0.36s | `checks/tests.stdout.txt` |
| `types` | fail (exit 1, expected 0) | 1 | 0.15s | `checks/types.stdout.txt` |

## Findings

| Finding | Severity | Status | Kind | Where |
|---|---|---|---|---|
| **check-types** — mypy failed | blocking | confirmed | check-failure | — |

## How this verdict was reached

The verdict is computed from the evidence above by code, not by a model: a failed or
timed-out required check and a confirmed blocking finding produce `HOLD`; a required
check that did not run, or a review that did not happen, produces `INCONCLUSIVE`; a
non-blocking or unconfirmed finding produces `PASS WITH CONDITIONS`.

A reviewer finding is `confirmed` only when the file is part of the change, the line
exists, and the quote is literally that line. Anything else stays `unverified`: it is
reported, and it cannot hold a release.

Markdown is a rendering of the JSON report next to it; the JSON is the record.
