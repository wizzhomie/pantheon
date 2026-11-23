# Evaluation report — `baseline` · `20261123T023104-baseline-528de59`

| | |
|---|---|
| Preset | `code_review` |
| Preset hash | `c9a511c59f183b67ed4bff3c24fe1bc74f62991e91d85a8436ed53060e6c7edc` |
| Dataset hash | `7f2d27466d490eafe26352117e8d00a5a6681125e5fa35235ce212f927a9d7e6` |
| Cases | 24 |
| Commit | `528de59` |
| Created | 2026-11-23 02:31 UTC |
| Agent model | `deepseek-flash` |
| Judge | `deepseek-v4-pro` |

## Quality (ground truth from the golden values)

| Metric | Value | Meaning |
|---|---|---|
| Pass, first attempt | 87.5% | accepted on attempt 1 and correct |
| Pass, final | 87.5% | correct after the loop |
| Field accuracy (macro) | 87.5% | weighted match of the expected fields |
| Hallucination rate | 0.0% | a forbidden value appeared |
| False pass rate | 12.5% | the loop accepted a wrong answer |

## Loop

| Metric | Value |
|---|---|
| Escalation rate | 0.0% |
| Retry rate | 0.0% |
| Retry recovery rate | 0.0% |
| Latency p50 / p95 | 0 ms / 0 ms |
| Cost, total | $0.0068 |
| Cost per case | $0.0003 |
| Cost per passed case | $0.0003 |
| Tokens (prompt / completion) | 17,843 / 1,229 |

## By category

| Category | Cases | Pass, first | Pass, final | Escalation | Field accuracy |
|---|---|---|---|---|---|
| adversarial | 4 | 75.0% | 75.0% | 0.0% | 75.0% |
| edge_case | 8 | 87.5% | 87.5% | 0.0% | 87.5% |
| happy_path | 12 | 91.7% | 91.7% | 0.0% | 91.7% |

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
| `cr-19` | happy_path | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-20` | edge_case | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-21` | happy_path | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-22` | adversarial | passed | yes | 100.0% | — | 0 | $0.0003 |
| `cr-23` | happy_path | passed | yes | 100.0% | — | 0 | $0.0002 |
| `cr-24` | edge_case | passed | yes | 100.0% | — | 0 | $0.0002 |

## Notes

- 12.5% of cases were accepted by the loop but are wrong against the golden values.
