# Report comparison

- **A:** `20261123T023104-baseline-528de59`
- **B:** `20261123T023057-guarded-528de59`

## Metrics

| Metric | A | B | Δ | Better |
|---|---|---|---|---|
| Pass, first attempt | 87.5% | 79.2% | -8.3 pp | worse |
| Pass, final | 87.5% | 87.5% | +0.0 pp | same |
| Field accuracy (macro) | 87.5% | 87.5% | +0.0 pp | same |
| Retry recovery | 0.0% | 100.0% | +100.0 pp | better |
| Hallucination rate | 0.0% | 0.0% | +0.0 pp | same |
| False pass rate | 12.5% | 0.0% | -12.5 pp | better |
| Cost, total | $0.0068 | $0.0477 | +0.0409 | worse |
| Cost per case | $0.0003 | $0.0020 | +0.0017 | worse |
| Cost per passed case | $0.0003 | $0.0023 | +0.0019 | worse |
| Latency p50 | 0 ms | 2,922 ms | +2,921 ms | worse |
| Latency p95 | 0 ms | 4,716 ms | +4,716 ms | worse |
| Retry rate | 0.0% | 4.2% | +4.2 pp | — |
| Escalation rate | 0.0% | 20.8% | +20.8 pp | — |

## Changed cases

| Case | From | To | Codes |
|---|---|---|---|
| `cr-03` | passed | escalated | critical_issue |
| `cr-07` | passed | escalated | critical_issue |
| `cr-08` | passed | escalated | C3_QUOTE_NOT_ON_LINE, no_progress |
| `cr-11` | passed | escalated | critical_issue |
| `cr-23` | passed | escalated | critical_issue |
