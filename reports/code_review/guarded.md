# Evaluation report — `guarded` · `20261123T022958-guarded-fdc764f`

| | |
|---|---|
| Preset | `code_review` |
| Preset hash | `bb26c7b3ded189d4667251cea8705d12b0850dc2fe4f0330dd4b625c8acd72a1` |
| Dataset hash | `2be6d9aa1c03e9f8563cf5a16ff3e4523404f5abd091e0d7196e77d8b96e490a` |
| Cases | 18 |
| Commit | `fdc764f` |
| Created | 2026-11-23 02:29 UTC |
| Agent model | `deepseek-flash` |
| Judge | `deepseek-v4-pro` |

## Quality (ground truth from the golden values)

| Metric | Value | Meaning |
|---|---|---|
| Pass, first attempt | 77.8% | accepted on attempt 1 and correct |
| Pass, final | 83.3% | correct after the loop |
| Field accuracy (macro) | 83.3% | weighted match of the expected fields |
| Hallucination rate | 0.0% | a forbidden value appeared |
| False pass rate | 0.0% | the loop accepted a wrong answer |

## Loop

| Metric | Value |
|---|---|
| Escalation rate | 22.2% |
| Retry rate | 5.6% |
| Retry recovery rate | 100.0% |
| Latency p50 / p95 | 2 ms / 4 ms |
| Cost, total | $0.0354 |
| Cost per case | $0.0020 |
| Cost per passed case | $0.0024 |
| Tokens (prompt / completion) | 32,473 / 2,483 |

## By category

| Category | Cases | Pass, first | Pass, final | Escalation | Field accuracy |
|---|---|---|---|---|---|
| adversarial | 3 | 66.7% | 66.7% | 33.3% | 66.7% |
| edge_case | 6 | 66.7% | 83.3% | 33.3% | 83.3% |
| happy_path | 9 | 88.9% | 88.9% | 11.1% | 88.9% |

## Cases

| Case | Category | Status | Correct | Accuracy | Codes | Retries | Cost |
|---|---|---|---|---|---|---|---|
| `cr-01` | happy_path | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-02` | happy_path | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-03` | edge_case | escalated | no | 0.0% | critical_issue | 0 | $0.0025 |
| `cr-04` | happy_path | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-05` | edge_case | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-06` | edge_case | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-07` | adversarial | escalated | no | 0.0% | critical_issue | 0 | $0.0026 |
| `cr-08` | edge_case | escalated | yes | 100.0% | C3_QUOTE_NOT_ON_LINE, no_progress | 1 | $0.0007 |
| `cr-09` | happy_path | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-10` | edge_case | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-11` | happy_path | escalated | no | 0.0% | critical_issue | 0 | $0.0025 |
| `cr-12` | adversarial | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-13` | edge_case | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-14` | adversarial | passed | yes | 100.0% | — | 0 | $0.0020 |
| `cr-15` | happy_path | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-16` | happy_path | passed | yes | 100.0% | — | 0 | $0.0019 |
| `cr-17` | happy_path | passed | yes | 100.0% | — | 0 | $0.0018 |
| `cr-18` | happy_path | passed | yes | 100.0% | — | 0 | $0.0019 |
