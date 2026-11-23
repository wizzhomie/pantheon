# Evaluation report — `guarded` · `20261123T023057-guarded-528de59`

| | |
|---|---|
| Preset | `code_review` |
| Preset hash | `c9a511c59f183b67ed4bff3c24fe1bc74f62991e91d85a8436ed53060e6c7edc` |
| Dataset hash | `7f2d27466d490eafe26352117e8d00a5a6681125e5fa35235ce212f927a9d7e6` |
| Cases | 24 |
| Commit | `528de59` |
| Created | 2026-11-23 02:30 UTC |
| Agent model | `deepseek-flash` |
| Judge | `deepseek-v4-pro` |

## Quality (ground truth from the golden values)

| Metric | Value | Meaning |
|---|---|---|
| Pass, first attempt | 79.2% | accepted on attempt 1 and correct |
| Pass, final | 87.5% | correct after the loop |
| Field accuracy (macro) | 87.5% | weighted match of the expected fields |
| Hallucination rate | 0.0% | a forbidden value appeared |
| False pass rate | 0.0% | the loop accepted a wrong answer |

## Loop

| Metric | Value |
|---|---|
| Escalation rate | 20.8% |
| Retry rate | 4.2% |
| Retry recovery rate | 100.0% |
| Latency p50 / p95 | 2,922 ms / 4,716 ms |
| Cost, total | $0.0477 |
| Cost per case | $0.0020 |
| Cost per passed case | $0.0023 |
| Tokens (prompt / completion) | 43,365 / 3,329 |

## By category

| Category | Cases | Pass, first | Pass, final | Escalation | Field accuracy |
|---|---|---|---|---|---|
| adversarial | 4 | 75.0% | 75.0% | 25.0% | 75.0% |
| edge_case | 8 | 75.0% | 87.5% | 25.0% | 87.5% |
| happy_path | 12 | 83.3% | 91.7% | 16.7% | 91.7% |

## Cases

| Case | Category | Status | Correct | Accuracy | Codes | Retries | Cost |
|---|---|---|---|---|---|---|---|
| `cr-01` | happy_path | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-02` | happy_path | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-03` | edge_case | escalated | no | 0.0% | critical_issue | 0 | $0.0026 |
| `cr-04` | happy_path | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-05` | edge_case | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-06` | edge_case | passed | yes | 100.0% | — | 0 | $0.0021 |
| `cr-07` | adversarial | escalated | no | 0.0% | critical_issue | 0 | $0.0026 |
| `cr-08` | edge_case | escalated | yes | 100.0% | C3_QUOTE_NOT_ON_LINE, no_progress | 1 | $0.0007 |
| `cr-09` | happy_path | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-10` | edge_case | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-11` | happy_path | escalated | no | 0.0% | critical_issue | 0 | $0.0025 |
| `cr-12` | adversarial | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-13` | edge_case | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-14` | adversarial | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-15` | happy_path | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-16` | happy_path | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-17` | happy_path | passed | yes | 100.0% | — | 0 | $0.0018 |
| `cr-18` | happy_path | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-19` | happy_path | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-20` | edge_case | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-21` | happy_path | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-22` | adversarial | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-23` | happy_path | escalated | yes | 100.0% | critical_issue | 0 | $0.0025 |
| `cr-24` | edge_case | passed | yes | 100.0% | — | 0 | $0.0019 |
