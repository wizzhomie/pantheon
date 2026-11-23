# Bench — 5 of 5 reviewer cases detected

| | |
|---|---|
| Suite | `holdout` |
| Reviewer | `deepseek-flash` |
| Cases | 5 of 5 reviewer cases detected |
| Started | 2026-11-23 15:55 UTC |
| Spent | 23.4s |
| Tokens | 5405 in / 2333 out |

## Cases

| Case | Expected | Actual | Result | Why |
|---|---|---|---|---|
| `reviewer-falsy` | finding | pass_with_conditions | ok | 2 confirmed finding(s); non-blocking-finding: F1 (major): Falsy check treats quantity 0 as missing |
| `reviewer-injection` | finding | hold | ok | 2 confirmed finding(s); blocking-finding: F1: SQL injection via interpolated customer name |
| `reviewer-inplace` | finding | pass_with_conditions | ok | 3 confirmed finding(s); non-blocking-finding: F1 (major): cheapest_first mutates the caller's list despite its docstring |
| `reviewer-percent` | finding | hold | ok | 3 confirmed finding(s); blocking-finding: F1: Discount treated as an absolute amount, not a percentage |
| `reviewer-swallow` | finding | hold | ok | 3 confirmed finding(s); blocking-finding: F1: Broad exception handler turns scan failures into a passing check |

## What this measures

Each case is a real project copied from the suite's base, with one change applied;
the verdict is decided by the same code path a user runs, not by a bench-only path.
A `hold` case is a planted defect: `PASS WITH CONDITIONS` does not count as catching
it, because that verdict ships. A clean case that is held is the cost of catching
defects too eagerly, and it is reported with the same weight.
