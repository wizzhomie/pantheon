# Holdout bench — 3 of 3 reviewer cases detected

| | |
|---|---|
| Suite | `holdout` |
| Reviewer | `deepseek-flash` |
| Cases | 3 of 3 reviewer cases detected |
| Started | 2026-11-23 07:01 UTC |
| Spent | 11.8s |

## Cases

| Case | Expected | Actual | Result | Why |
|---|---|---|---|---|
| `reviewer-injection` | finding | hold | ok | 3 confirmed finding(s); blocking-finding: R1: SQL injection via interpolated customer name |
| `reviewer-percent` | finding | hold | ok | 2 confirmed finding(s); blocking-finding: R1: Discount is subtracted as an absolute amount, not a percentage |
| `reviewer-swallow` | finding | hold | ok | 3 confirmed finding(s); blocking-finding: R1: Blanket exception handler turns scan failures into a passing check |

## What this measures

Each case is a real project copied from the suite's base, with one change applied;
the verdict is decided by the same code path a user runs, not by a bench-only path.
A `hold` case is a planted defect: `PASS WITH CONDITIONS` does not count as catching
it, because that verdict ships. A clean case that is held is the cost of catching
defects too eagerly, and it is reported with the same weight.
