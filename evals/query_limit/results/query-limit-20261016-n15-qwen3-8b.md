# Query limit measurement (#1377), `qwen3:8b`, 15 runs per arm

| k | multi complete (of 6) | multi fact recall | small | single | false refusals | adjacent mishandled | median s | median ctx chars | omitted | excerpted |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 0 of 6 | 0.323 (0.32-0.32) | 0.838 | 1.000 | 15 of 240 | 0 of 45 | 2.3 | 1364 | 0 | 0 |
| 5 | 0 of 6 | 0.641 (0.64-0.64) | 0.950 | 1.000 | 0 of 240 | 0 of 45 | 3.3 | 2275 | 0 | 0 |
| 8 | 6 of 6 | 0.989 (0.83-1.00) | 1.000 | 1.000 | 1 of 240 | 0 of 45 | 3.9 | 3625 | 0 | 0 |
| 10 | 6 of 6 | 1.000 (1.00-1.00) | 1.000 | 1.000 | 0 of 240 | 0 of 45 | 3.5 | 4340 | 0 | 0 |

Retrieval identical across all runs: 76 of 76 (k, question) cells.

## Bars

### k = 8
- **R** PASS: 6 of 6 multi questions fully retrieved (need 4)
- **C** PASS: multi fact recall 0.989 vs 0.641: gap +0.348 (need >= 0.10 and > spread 0.167)
- **N1** PASS: small +0.050, single +0.000, pooled +0.020 (floors -0.05/-0.05/-0.03)
- **N2** PASS: adjacent mishandled 0 of 45 vs 0 of 45 (UNFALSIFIED: both zero)
- **N3** PASS: false refusals 1 of 240 vs 0 of 240
- **B** PASS: median latency 3.9s vs 3.3s (x1.16, max 1.5), median context 3625 vs 2275 chars (x1.59, max 2.0), omitted blocks 0 (need 0)

### k = 10
- **R** PASS: 6 of 6 multi questions fully retrieved (need 4)
- **C** PASS: multi fact recall 1.000 vs 0.641: gap +0.359 (need >= 0.10 and > spread 0.000)
- **N1** PASS: small +0.050, single +0.000, pooled +0.020 (floors -0.05/-0.05/-0.03)
- **N2** PASS: adjacent mishandled 0 of 45 vs 0 of 45 (UNFALSIFIED: both zero)
- **N3** PASS: false refusals 0 of 240 vs 0 of 240
- **B** PASS: median latency 3.5s vs 3.3s (x1.05, max 1.5), median context 4340 vs 2275 chars (x1.91, max 2.0), omitted blocks 0 (need 0)

## Decision: default `limit` = **8**