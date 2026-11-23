# Bench — 3 of 3 planted defects held, 5 of 5 reviewer cases detected, 0 of 4 clean changes held

| | |
|---|---|
| Suite | `holdout` |
| Reviewer | `deepseek-flash` |
| Cases | 3 of 3 planted defects held, 5 of 5 reviewer cases detected, 0 of 4 clean changes held |
| Started | 2026-11-23 18:49 UTC |
| Spent | 47.3s |
| Tokens | 10812 in / 3411 out |

## Cases

| Case | Expected | Actual | Result | Why |
|---|---|---|---|---|
| `control-clean` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `control-refactor` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `control-rounding` | pass | pass_with_conditions | **off the answer** | non-blocking-finding: F2 (minor): Binary float rounding gives surprising results at half-cent boundaries |
| `control-unrelated` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `mutant-arithmetic` | hold | hold | ok | check-failed: required check tests failed: exit 1, expected 0 |
| `mutant-sql` | hold | hold | ok | check-failed: required check sql failed: exit 1, expected 0 |
| `mutant-types` | hold | hold | ok | check-failed: required check types failed: exit 1, expected 0 |
| `reviewer-falsy` | finding | pass_with_conditions | ok | 2 confirmed finding(s); non-blocking-finding: F1 (major): Falsy check treats quantity 0 as missing |
| `reviewer-injection` | finding | hold | ok | 2 confirmed finding(s); blocking-finding: F1: SQL injection via interpolated customer name |
| `reviewer-inplace` | finding | pass_with_conditions | ok | 3 confirmed finding(s); non-blocking-finding: F1 (major): In-place sort mutates the caller's list despite the docstring |
| `reviewer-percent` | finding | hold | ok | 2 confirmed finding(s); blocking-finding: F1: Discount subtracts percent as an absolute amount, not a percentage |
| `reviewer-swallow` | finding | hold | ok | 3 confirmed finding(s); blocking-finding: F1: Blanket exception handler makes the check pass on any error |

Result: 11 of 12 cases matched the suite; `control-rounding` did not.

## What this measures

Each case is a real project copied from the suite's base, with one change applied;
the verdict is decided by the same code path a user runs, not by a bench-only path.
A `hold` case is a planted defect: `PASS WITH CONDITIONS` does not count as catching
it, because that verdict ships. A clean case that is held is the cost of catching
defects too eagerly, and it is reported with the same weight.
