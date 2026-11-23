# Holdout bench — 3 of 3 planted defects held, 3 of 3 reviewer cases detected, 0 of 2 clean changes held

| | |
|---|---|
| Suite | `holdout` |
| Reviewer | `deepseek-flash` |
| Cases | 3 of 3 planted defects held, 3 of 3 reviewer cases detected, 0 of 2 clean changes held |
| Started | 2026-11-23 07:16 UTC |
| Spent | 26.7s |
| Tokens | 3798 in / 1615 out |

## Cases

| Case | Expected | Actual | Result | Why |
|---|---|---|---|---|
| `control-clean` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `control-unrelated` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `mutant-arithmetic` | hold | hold | ok | check-failed: required check tests failed: exit 1, expected 0 |
| `mutant-sql` | hold | hold | ok | check-failed: required check sql failed: exit 1, expected 0 |
| `mutant-types` | hold | hold | ok | check-failed: required check types failed: exit 1, expected 0 |
| `reviewer-injection` | finding | hold | ok | 3 confirmed finding(s); blocking-finding: R1: SQL injection via interpolated customer name |
| `reviewer-percent` | finding | hold | ok | 2 confirmed finding(s); blocking-finding: R1: Discount subtracts percent as an absolute amount, not a percentage |
| `reviewer-swallow` | finding | hold | ok | 3 confirmed finding(s); blocking-finding: R1: Blanket exception handler turns scan failures into a passing check |

## What this measures

Each case is a real project copied from the suite's base, with one change applied;
the verdict is decided by the same code path a user runs, not by a bench-only path.
A `hold` case is a planted defect: `PASS WITH CONDITIONS` does not count as catching
it, because that verdict ships. A clean case that is held is the cost of catching
defects too eagerly, and it is reported with the same weight.
