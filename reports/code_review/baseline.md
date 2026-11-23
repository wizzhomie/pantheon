# Evaluation report — `baseline` · `20261123T022758-baseline-57fbd2e`

| | |
|---|---|
| Preset | `code_review` |
| Preset hash | `86294bebb6779870d3e9b0d216153c92b7ba534dc824fdde390414d16e82e15b` |
| Dataset hash | `2be6d9aa1c03e9f8563cf5a16ff3e4523404f5abd091e0d7196e77d8b96e490a` |
| Cases | 18 |
| Commit | `57fbd2e` |
| Created | 2026-11-23 02:27 UTC |
| Agent model | `deepseek-flash` |
| Judge | `deepseek-v4-pro` |

## Quality (ground truth from the golden values)

| Metric | Value | Meaning |
|---|---|---|
| Pass, first attempt | 83.3% | accepted on attempt 1 and correct |
| Pass, final | 83.3% | correct after the loop |
| Field accuracy (macro) | 83.3% | weighted match of the expected fields |
| Hallucination rate | 0.0% | a forbidden value appeared |
| False pass rate | 16.7% | the loop accepted a wrong answer |

## Loop

| Metric | Value |
|---|---|
| Escalation rate | 0.0% |
| Retry rate | 0.0% |
| Retry recovery rate | 0.0% |
| Latency p50 / p95 | 0 ms / 2 ms |
| Cost, total | $0.0051 |
| Cost per case | $0.0003 |
| Cost per passed case | $0.0003 |
| Tokens (prompt / completion) | 13,385 / 901 |

## By category

| Category | Cases | Pass, first | Pass, final | Escalation | Field accuracy |
|---|---|---|---|---|---|
| adversarial | 3 | 66.7% | 66.7% | 0.0% | 66.7% |
| edge_case | 6 | 83.3% | 83.3% | 0.0% | 83.3% |
| happy_path | 9 | 88.9% | 88.9% | 0.0% | 88.9% |

## Cases

| Case | Category | Status | Correct | Accuracy | Codes | Retries | Cost |
|---|---|---|---|---|---|---|---|
| `cr-01` | happy_path | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-02` | happy_path | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-03` | edge_case | passed | no | 0.0% | — | 0 | $0.0002 |
| `cr-04` | happy_path | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-05` | edge_case | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-06` | edge_case | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-07` | adversarial | passed | no | 0.0% | — | 0 | $0.0002 |
| `cr-08` | edge_case | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-09` | happy_path | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-10` | edge_case | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-11` | happy_path | passed | no | 0.0% | — | 0 | $0.0002 |
| `cr-12` | adversarial | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-13` | edge_case | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-14` | adversarial | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-15` | happy_path | passed | yes | 100.0% | — | 0 | $0.0002 |
| `cr-16` | happy_path | passed | yes | 100.0% | — | 0 | $0.0002 |
| `cr-17` | happy_path | passed | yes | 100.0% | — | 0 | $0.0002 |
| `cr-18` | happy_path | passed | yes | 100.0% | — | 0 | $0.0002 |

## Notes

- 16.7% of cases were accepted by the loop but are wrong against the golden values.
