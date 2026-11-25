# Verification report — PASS WITH CONDITIONS

| | |
|---|---|
| Verdict | **PASS WITH CONDITIONS** |
| Run | `20261123T034403-qagent-default` |
| Policy | `qagent-default` |
| Policy digest | `de31e738c4cf` |
| Profile digest | `55125591b46f` |
| Started | 2026-11-23 03:44 UTC |
| Checked | 3 of 3 planned |
| Spent | 2.5s |

## Why

- `non-blocking-finding` — R1 (minor): Byte ceiling measured in characters, not bytes
- `non-blocking-finding` — R2 (major): Anchor compares model path against diff paths without normalisation
- `non-blocking-finding` — R3 (major): Anchor reads the working tree, not the diff's new-file content
- `non-blocking-finding` — R4 (minor): No test evidence for anchoring and severity rules in the change

## Checks

| Check | Status | Exit | Time | Evidence |
|---|---|---|---|---|
| `tests` | pass | 0 | 2.26s | `checks/tests.stdout.txt` |
| `lint` | pass | 0 | 0.03s | `checks/lint.stdout.txt` |
| `types` | pass | 0 | 0.21s | `checks/types.stdout.txt` |

## Review

| | |
|---|---|
| Model | `deepseek-flash` |
| Diff | `208612dc9d32` |
| Files shown | 7 |
| Proposed | 4 |
| Confirmed by the anchor | 4 |
| Unverified | 0 |
| Tokens | 8827 in / 623 out |
| Time | 4.1s |

## Findings

| Finding | Severity | Status | Kind | Where |
|---|---|---|---|---|
| **R1** — Byte ceiling measured in characters, not bytes | minor | confirmed | defect | `src/qagent/review/diff.py:85` |
| **R2** — Anchor compares model path against diff paths without normalisation | major | confirmed | risk | `src/qagent/review/reviewer.py:130` |
| **R3** — Anchor reads the working tree, not the diff's new-file content | major | confirmed | risk | `src/qagent/review/reviewer.py:139` |
| **R4** — No test evidence for anchoring and severity rules in the change | minor | confirmed | test-gap | `src/qagent/review/reviewer.py:78` |

## Reviewer evidence

**R1** — `src/qagent/review/diff.py:85`

> truncated = len(text) > max_bytes

**R2** — `src/qagent/review/reviewer.py:130`

> if proposal.file not in diff.paths:

**R3** — `src/qagent/review/reviewer.py:139`

> actual = _normalise(lines[proposal.line - 1])

**R4** — `src/qagent/review/reviewer.py:78`

> anchor(index + 1, proposal, root, diff) for index, proposal in enumerate(proposed.findings)

## How this verdict was reached

The verdict is computed from the evidence above by code, not by a model: a failed or
timed-out required check and a confirmed blocking finding produce `HOLD`; a required
check that did not run, or a review that did not happen, produces `INCONCLUSIVE`; a
non-blocking or unconfirmed finding produces `PASS WITH CONDITIONS`.

A reviewer finding is `confirmed` only when the file is part of the change, the line
exists, and the quote is literally that line. Anything else stays `unverified`: it is
reported, and it cannot hold a release.

Markdown is a rendering of the JSON report next to it; the JSON is the record.
