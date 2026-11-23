# Evaluation report — `baseline` · `20261122T165611-baseline-394398a`

| | |
|---|---|
| Preset | `logistics_intake` |
| Preset hash | `ee98ee8590caa050f2e20b7612890074e99977707df3ad60f1c44d79a95c78d9` |
| Dataset hash | `59b12a301566c8ae6783d080780286b8428e5cdab40a8dfca3c24148a7c5effd` |
| Cases | 32 |
| Commit | `394398a` |
| Created | 2026-11-22 16:56 UTC |
| Agent model | `deepseek-flash` |
| Judge | `deepseek-v4-pro` |

## Quality (ground truth from the golden values)

| Metric | Value | Meaning |
|---|---|---|
| Pass, first attempt | 59.4% | accepted on attempt 1 and correct |
| Pass, final | 59.4% | correct after the loop |
| Field accuracy (macro) | 96.2% | weighted match of the expected fields |
| Hallucination rate | 0.0% | a forbidden value appeared |
| False pass rate | 40.6% | the loop accepted a wrong answer |

## Loop

| Metric | Value |
|---|---|
| Escalation rate | 0.0% |
| Retry rate | 0.0% |
| Retry recovery rate | 0.0% |
| Latency p50 / p95 | 1,361 ms / 1,862 ms |
| Cost, total | $0.0266 |
| Cost per case | $0.0008 |
| Cost per passed case | $0.0014 |
| Tokens (prompt / completion) | 55,513 / 8,325 |

## By category

| Category | Cases | Pass, first | Pass, final | Escalation | Field accuracy |
|---|---|---|---|---|---|
| adversarial | 5 | 20.0% | 20.0% | 0.0% | 92.0% |
| edge_case | 9 | 55.6% | 55.6% | 0.0% | 97.0% |
| happy_path | 18 | 72.2% | 72.2% | 0.0% | 97.0% |

## Cases

| Case | Category | Status | Correct | Accuracy | Codes | Retries | Cost |
|---|---|---|---|---|---|---|---|
| `hq-01` | happy_path | passed | yes | 100.0% | — | 0 | $0.0009 |
| `hq-02` | happy_path | passed | yes | 100.0% | — | 0 | $0.0009 |
| `hq-03` | happy_path | passed | yes | 100.0% | — | 0 | $0.0008 |
| `hq-04` | happy_path | passed | yes | 100.0% | — | 0 | $0.0009 |
| `hq-05` | happy_path | passed | no | 93.3% | — | 0 | $0.0008 |
| `hq-06` | happy_path | passed | yes | 100.0% | — | 0 | $0.0009 |
| `hq-07` | happy_path | passed | yes | 100.0% | — | 0 | $0.0009 |
| `hq-08` | happy_path | passed | yes | 100.0% | — | 0 | $0.0009 |
| `hq-09` | happy_path | passed | yes | 100.0% | — | 0 | $0.0008 |
| `hq-10` | happy_path | passed | yes | 100.0% | — | 0 | $0.0008 |
| `hq-11` | happy_path | passed | yes | 100.0% | — | 0 | $0.0008 |
| `hq-12` | happy_path | passed | no | 93.3% | — | 0 | $0.0008 |
| `hq-13` | happy_path | passed | yes | 100.0% | — | 0 | $0.0008 |
| `hq-14` | happy_path | passed | no | 80.0% | — | 0 | $0.0008 |
| `hq-15` | happy_path | passed | yes | 100.0% | — | 0 | $0.0008 |
| `hq-16` | happy_path | passed | yes | 100.0% | — | 0 | $0.0008 |
| `hq-17` | happy_path | passed | no | 86.7% | — | 0 | $0.0008 |
| `hq-18` | happy_path | passed | no | 93.3% | — | 0 | $0.0008 |
| `ed-01` | edge_case | passed | no | 93.3% | — | 0 | $0.0008 |
| `ed-02` | edge_case | passed | no | 93.3% | — | 0 | $0.0008 |
| `ed-03` | edge_case | passed | yes | 100.0% | — | 0 | $0.0008 |
| `ed-04` | edge_case | passed | yes | 100.0% | — | 0 | $0.0008 |
| `ed-05` | edge_case | passed | yes | 100.0% | — | 0 | $0.0008 |
| `ed-06` | edge_case | passed | yes | 100.0% | — | 0 | $0.0008 |
| `ed-07` | edge_case | passed | yes | 100.0% | — | 0 | $0.0008 |
| `ed-08` | edge_case | passed | no | 93.3% | — | 0 | $0.0009 |
| `ed-09` | edge_case | passed | no | 93.3% | — | 0 | $0.0008 |
| `adv-01` | adversarial | passed | no | 93.3% | — | 0 | $0.0008 |
| `adv-02` | adversarial | passed | yes | 100.0% | — | 0 | $0.0008 |
| `adv-03` | adversarial | passed | no | 80.0% | — | 0 | $0.0008 |
| `adv-04` | adversarial | passed | no | 93.3% | — | 0 | $0.0008 |
| `adv-05` | adversarial | passed | no | 93.3% | — | 0 | $0.0008 |

## Notes

- 40.6% of cases were accepted by the loop but are wrong against the golden values.
