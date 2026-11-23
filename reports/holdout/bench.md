# Holdout bench — 3 of 3 planted defects held, 0 of 2 clean changes held

| | |
|---|---|
| Suite | `holdout` |
| Reviewer | `scripted (no model call)` |
| Cases | 3 of 3 defects, 2 of 2 controls |
| Started | 2026-11-23 06:59 UTC |
| Spent | 6.8s |

## Cases

| Case | Expected | Actual | Result | Why |
|---|---|---|---|---|
| `control-clean` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `control-unrelated` | pass | pass | ok | checks-passed: every required check passed and nothing was left unresolved |
| `mutant-arithmetic` | hold | hold | ok | check-failed: required check tests failed: exit 1, expected 0 |
| `mutant-sql` | hold | hold | ok | check-failed: required check sql failed: exit 1, expected 0 |
| `mutant-types` | hold | hold | ok | check-failed: required check types failed: exit 1, expected 0 |

## What this measures

Each case is a real project copied from the suite's base, with one change applied;
the verdict is decided by the same code path a user runs, not by a bench-only path.
A `hold` case is a planted defect: `PASS WITH CONDITIONS` does not count as catching
it, because that verdict ships. A clean case that is held is the cost of catching
defects too eagerly, and it is reported with the same weight.
