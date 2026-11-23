# Report comparison

- **A:** `20261123T022758-baseline-57fbd2e`
- **B:** `20261123T022750-guarded-57fbd2e`

## Metrics

| Metric | A | B | Δ | Better |
|---|---|---|---|---|
| Pass, first attempt | 83.3% | 77.8% | -5.5 pp | worse |
| Pass, final | 83.3% | 83.3% | +0.0 pp | same |
| Field accuracy (macro) | 83.3% | 83.3% | +0.0 pp | same |
| Retry recovery | 0.0% | 100.0% | +100.0 pp | better |
| Hallucination rate | 0.0% | 0.0% | +0.0 pp | same |
| False pass rate | 16.7% | 0.0% | -16.7 pp | better |
| Cost, total | $0.0051 | $0.0354 | +0.0303 | worse |
| Cost per case | $0.0003 | $0.0020 | +0.0017 | worse |
| Cost per passed case | $0.0003 | $0.0024 | +0.0020 | worse |
| Latency p50 | 0 ms | 2,440 ms | +2,439 ms | worse |
| Latency p95 | 2 ms | 4,588 ms | +4,586 ms | worse |
| Retry rate | 0.0% | 5.6% | +5.6 pp | — |
| Escalation rate | 0.0% | 22.2% | +22.2 pp | — |

## Changed cases

| Case | From | To | Codes |
|---|---|---|---|
| `cr-03` | passed | escalated | critical_issue |
| `cr-07` | passed | escalated | critical_issue |
| `cr-08` | passed | escalated | C3_QUOTE_NOT_ON_LINE, no_progress |
| `cr-11` | passed | escalated | critical_issue |
