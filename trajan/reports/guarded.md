# Evaluation report — `guarded` · `20261122T165644-guarded-394398a`

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
| Pass, first attempt | 68.8% | accepted on attempt 1 and correct |
| Pass, final | 81.2% | correct after the loop |
| Field accuracy (macro) | 98.1% | weighted match of the expected fields |
| Hallucination rate | 0.0% | a forbidden value appeared |
| False pass rate | 18.8% | the loop accepted a wrong answer |

## Loop

| Metric | Value |
|---|---|
| Escalation rate | 0.0% |
| Retry rate | 18.8% |
| Retry recovery rate | 66.7% |
| Latency p50 / p95 | 3,020 ms / 6,164 ms |
| Cost, total | $0.1076 |
| Cost per case | $0.0034 |
| Cost per passed case | $0.0041 |
| Tokens (prompt / completion) | 112,993 / 14,217 |

## By category

| Category | Cases | Pass, first | Pass, final | Escalation | Field accuracy |
|---|---|---|---|---|---|
| adversarial | 5 | 60.0% | 60.0% | 0.0% | 96.0% |
| edge_case | 9 | 77.8% | 88.9% | 0.0% | 99.3% |
| happy_path | 18 | 66.7% | 83.3% | 0.0% | 98.2% |

## Judge calibration (hand labels)

| Metric | Value |
|---|---|
| Labelled cases | 18 |
| Agreement | 83.3% |
| Cohen's kappa | 0.000 |
| Judge precision | 83.3% |
| Judge recall | 100.0% |

**False passes (judge approved, human rejected):** `ed-03`, `adv-01`, `adv-04`

## Cases

| Case | Category | Status | Correct | Accuracy | Codes | Retries | Cost |
|---|---|---|---|---|---|---|---|
| `hq-01` | happy_path | passed | yes | 100.0% | — | 0 | $0.0029 |
| `hq-02` | happy_path | passed | yes | 100.0% | — | 0 | $0.0039 |
| `hq-03` | happy_path | passed_after_retry | yes | 100.0% | G4_ORPHAN_EVIDENCE | 1 | $0.0089 |
| `hq-04` | happy_path | passed | yes | 100.0% | — | 0 | $0.0030 |
| `hq-05` | happy_path | passed | no | 93.3% | — | 0 | $0.0029 |
| `hq-06` | happy_path | passed | yes | 100.0% | — | 0 | $0.0029 |
| `hq-07` | happy_path | passed | yes | 100.0% | — | 0 | $0.0030 |
| `hq-08` | happy_path | passed | yes | 100.0% | — | 0 | $0.0047 |
| `hq-09` | happy_path | passed | yes | 100.0% | — | 0 | $0.0028 |
| `hq-10` | happy_path | passed | yes | 100.0% | — | 0 | $0.0028 |
| `hq-11` | happy_path | passed | yes | 100.0% | — | 0 | $0.0029 |
| `hq-12` | happy_path | passed_after_retry | yes | 100.0% | — | 1 | $0.0038 |
| `hq-13` | happy_path | passed | yes | 100.0% | — | 0 | $0.0028 |
| `hq-14` | happy_path | passed_after_retry | no | 86.7% | — | 1 | $0.0039 |
| `hq-15` | happy_path | passed | yes | 100.0% | — | 0 | $0.0028 |
| `hq-16` | happy_path | passed_after_retry | yes | 100.0% | — | 1 | $0.0037 |
| `hq-17` | happy_path | passed | no | 86.7% | — | 0 | $0.0043 |
| `hq-18` | happy_path | passed | yes | 100.0% | — | 0 | $0.0028 |
| `ed-01` | edge_case | passed | yes | 100.0% | — | 0 | $0.0029 |
| `ed-02` | edge_case | passed | no | 93.3% | — | 0 | $0.0029 |
| `ed-03` | edge_case | passed | yes | 100.0% | — | 0 | $0.0029 |
| `ed-04` | edge_case | passed | yes | 100.0% | — | 0 | $0.0028 |
| `ed-05` | edge_case | passed | yes | 100.0% | — | 0 | $0.0028 |
| `ed-06` | edge_case | passed | yes | 100.0% | — | 0 | $0.0028 |
| `ed-07` | edge_case | passed | yes | 100.0% | — | 0 | $0.0028 |
| `ed-08` | edge_case | passed_after_retry | yes | 100.0% | — | 1 | $0.0039 |
| `ed-09` | edge_case | passed | yes | 100.0% | — | 0 | $0.0028 |
| `adv-01` | adversarial | passed | no | 93.3% | — | 0 | $0.0029 |
| `adv-02` | adversarial | passed | yes | 100.0% | — | 0 | $0.0029 |
| `adv-03` | adversarial | passed_after_retry | no | 86.7% | — | 1 | $0.0039 |
| `adv-04` | adversarial | passed | yes | 100.0% | — | 0 | $0.0037 |
| `adv-05` | adversarial | passed | yes | 100.0% | — | 0 | $0.0028 |

## Notes

- 18.8% of cases were accepted by the loop but are wrong against the golden values.
