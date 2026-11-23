# Bench — 3 of 3 planted defects held, 4 of 4 reviewer cases detected, 0 of 6 clean changes held

| | |
|---|---|
| Suite | `devset` |
| Reviewer | `deepseek-flash` |
| Cases | 3 of 3 planted defects held, 4 of 4 reviewer cases detected, 0 of 6 clean changes held |
| Started | 2026-11-23 15:46 UTC |
| Spent | 40.2s |
| Tokens | 6784 in / 2589 out |

## Cases

| Case | Expected | Actual | Result | Why |
|---|---|---|---|---|
| `control-docs` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `control-float` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `control-refactor` | pass | pass_with_conditions | **off the answer** | non-blocking-finding: F1 (minor): line_total is not defined in the visible window |
| `control-tests-only` | pass | pass_with_conditions | **off the answer** | non-blocking-finding: F1 (minor): Test assumes line_total returns float 0.0 for zero quantity |
| `control-vat` | pass | pass_with_conditions | **off the answer** | non-blocking-finding: F1 (minor): VAT uses binary float rounding, inconsistent with to_cents |
| `control-vat-gross` | pass | pass_with_conditions | **off the answer** | non-blocking-finding: F1 (minor): gross() rounds in floats, not to whole cents like to_cents() |
| `mutant-quantity` | hold | hold | ok | check-failed: required check tests failed: exit 1, expected 0 |
| `mutant-sql` | hold | hold | ok | check-failed: required check sql failed: exit 1, expected 0 |
| `mutant-types` | hold | hold | ok | check-failed: required check types failed: exit 1, expected 0 |
| `reviewer-empty-average` | finding | pass_with_conditions | ok | 2 confirmed finding(s); non-blocking-finding: F1 (major): Division by zero on empty line list |
| `reviewer-injection` | finding | hold | ok | 2 confirmed finding(s); blocking-finding: F1: SQL injection via concatenated customer name |
| `reviewer-mutable-default` | finding | hold | ok | 2 confirmed finding(s); blocking-finding: F1: Mutable default argument shared across calls |
| `reviewer-silent-parse` | finding | pass_with_conditions | ok | 3 confirmed finding(s); non-blocking-finding: F1 (major): Invalid input silently becomes zero |

Result: 9 of 13 cases matched the suite; `control-refactor`, `control-tests-only`, `control-vat`, `control-vat-gross` did not.

## What this measures

Each case is a real project copied from the suite's base, with one change applied;
the verdict is decided by the same code path a user runs, not by a bench-only path.
A `hold` case is a planted defect: `PASS WITH CONDITIONS` does not count as catching
it, because that verdict ships. A clean case that is held is the cost of catching
defects too eagerly, and it is reported with the same weight.
