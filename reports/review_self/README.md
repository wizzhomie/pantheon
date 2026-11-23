# The reviewer, reviewing its own commit

This is a real run, not a stub: on 2026-11-23 the reviewer read the diff of the commit that
introduced it (`35785e5..ec691de`, seven files under `src/qagent/review/`), proposed four
findings, and the anchor confirmed all four quotes are literally on the cited lines.

```bash
qagent verify --repo . --policy .qagent/policy.toml --diff <phase-4.diff> \
    --reviewer qagent.review.factory:from_env \
    --review-instructions .qagent/review.md
```

| | |
|---|---|
| Model | `deepseek-flash` |
| Diff | `208612dc9d32` |
| Files reviewed | 7 |
| Proposed / confirmed / unverified | 4 / 4 / 0 |
| Tokens | 8 827 in / 623 out |
| Time | 4.1 s |
| Verdict | **PASS WITH CONDITIONS** |

The four findings, and what happened to them:

| Finding | What it said | Outcome |
|---|---|---|
| R1 | the byte ceiling is measured in characters | real, fixed: the diff is cut on a UTF-8 byte boundary now |
| R2 | a path like `./src/app.py` is compared to the diff's path without normalisation | real, fixed: both sides are normalised before the comparison |
| R3 | the anchor reads the working tree, not the diff's new-file content | accepted and made harmless: a cited line must also be a line the diff actually showed |
| R4 | the change has no test evidence | a scope mismatch: the diff was limited to `src/`, while the tests live in `tests/` |

Three of four were real problems in the code that had just been written, and all three quotes
were genuine lines from that code. Which is the point of the anchor: it does not make the model
right, it makes a claim cheap to check — and a confirmed quote is what lets a reader go and
decide for themselves.

This report is a snapshot. The diff is the phase-4 commit, while the files have moved since
(R1–R3 are fixed), so the quoted lines no longer sit at the same line numbers. The JSON next to
it is the record; the markdown is its rendering.
