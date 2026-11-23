# Bench — 3 of 3 planted defects held, 4 of 4 reviewer cases detected, 0 of 6 clean changes held

| | |
|---|---|
| Suite | `devset` |
| Reviewer | `deepseek-flash` |
| Cases | 3 of 3 planted defects held, 4 of 4 reviewer cases detected, 0 of 6 clean changes held |
| Started | 2026-11-23 15:53 UTC |
| Spent | 57.9s |
| Tokens | 12487 in / 4247 out |

## Cases

| Case | Expected | Actual | Result | Why |
|---|---|---|---|---|
| `control-docs` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `control-float` | pass | pass_with_conditions | **off the answer** | non-blocking-finding: F1 (major): Test pins float rounding artifact as expected behaviour |
| `control-refactor` | pass | pass_with_conditions | **off the answer** | non-blocking-finding: F1 (major): line_total is not defined in the visible window |
| `control-tests-only` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `control-vat` | pass | pass_with_conditions | **off the answer** | non-blocking-finding: F1 (minor): VAT uses float rounding, inconsistent with to_cents cent handling |
| `control-vat-gross` | pass | pass_with_conditions | **off the answer** | non-blocking-finding: F1 (minor): Float rounding can produce a value that is not an exact cent |
| `mutant-quantity` | hold | hold | ok | check-failed: required check tests failed: exit 1, expected 0 |
| `mutant-sql` | hold | hold | ok | check-failed: required check sql failed: exit 1, expected 0 |
| `mutant-types` | hold | hold | ok | check-failed: required check types failed: exit 1, expected 0 |
| `reviewer-empty-average` | finding | pass_with_conditions | ok | 2 confirmed finding(s); non-blocking-finding: F1 (major): Division by zero on an empty invoice |
| `reviewer-injection` | finding | hold | ok | 2 confirmed finding(s); blocking-finding: F1: SQL injection via concatenated customer name |
| `reviewer-mutable-default` | finding | hold | ok | 3 confirmed finding(s); blocking-finding: F1: Mutable default argument shared across calls |
| `reviewer-silent-parse` | finding | pass_with_conditions | ok | 3 confirmed finding(s); non-blocking-finding: F1 (major): Silently treating unparseable amounts as zero |

Result: 9 of 13 cases matched the suite; `control-float`, `control-refactor`, `control-vat`, `control-vat-gross` did not.

## What this measures

Each case is a real project copied from the suite's base, with one change applied;
the verdict is decided by the same code path a user runs, not by a bench-only path.
A `hold` case is a planted defect: `PASS WITH CONDITIONS` does not count as catching
it, because that verdict ships. A clean case that is held is the cost of catching
defects too eagerly, and it is reported with the same weight.
