# Report comparison

- **A:** `20261122T165611-baseline-394398a`
- **B:** `20261122T165644-guarded-394398a`

## Metrics

| Metric | A | B | Δ | Better |
|---|---|---|---|---|
| Pass, first attempt | 59.4% | 68.8% | +9.4 pp | better |
| Pass, final | 59.4% | 81.2% | +21.9 pp | better |
| Field accuracy (macro) | 96.2% | 98.1% | +1.9 pp | better |
| Retry recovery | 0.0% | 66.7% | +66.7 pp | better |
| Hallucination rate | 0.0% | 0.0% | +0.0 pp | same |
| False pass rate | 40.6% | 18.8% | -21.9 pp | better |
| Cost, total | $0.0266 | $0.1076 | +0.0809 | worse |
| Cost per case | $0.0008 | $0.0034 | +0.0025 | worse |
| Cost per passed case | $0.0014 | $0.0041 | +0.0027 | worse |
| Latency p50 | 1,361 ms | 3,020 ms | +1,659 ms | worse |
| Latency p95 | 1,862 ms | 6,164 ms | +4,301 ms | worse |
| Retry rate | 0.0% | 18.8% | +18.8 pp | — |
| Escalation rate | 0.0% | 0.0% | +0.0 pp | — |

## Changed cases

| Case | From | To | Codes |
|---|---|---|---|
| `hq-03` | passed | passed_after_retry | G4_ORPHAN_EVIDENCE |
| `hq-12` | passed | passed_after_retry | — |
| `hq-14` | passed | passed_after_retry | — |
| `hq-16` | passed | passed_after_retry | — |
| `ed-08` | passed | passed_after_retry | — |
| `adv-03` | passed | passed_after_retry | — |
