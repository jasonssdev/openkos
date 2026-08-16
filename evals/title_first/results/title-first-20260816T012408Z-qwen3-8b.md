# Title-first phase 1, measured (#728 option 2)

`qwen3:8b`, fixture `medium-10-reunion-plataforma`, 6 runs per arm.

| metric | baseline | title-first |
| --- | --- | --- |
| **wall clock (s)** | 165.7 ±24.0 [130.3-202.0] n=6 | 87.0 ±7.5 [79.8-97.5] n=6 |
| recall | 0.82 ±0.11 [0.64-0.91] n=6 | 0.62 ±0.09 [0.55-0.73] n=6 |
| precision | 0.95 ±0.08 [0.80-1.00] n=6 | 0.62 ±0.13 [0.43-0.78] n=6 |
| retained objects | 10.5 ±2.0 [7.0-12.0] n=6 | 11.5 ±2.1 [9.0-14.0] n=6 |
| errored runs | 0 | 0 |
| chat calls in `_extract_once` | 0 (unwrapped) | 24 survey + 24 hydrate |
| framing objects killed before a body | 0 (killed after) | 16 |
| survivors the hydration lost | n/a | 3 |

**Title overlap between arms:** 0.29 (10 shared of 35 distinct titles; 6 baseline-only, 19 treatment-only)

The #694 oracle band on this fixture is recall 0.80 ±0.12, precision 0.95 ±0.08. The baseline measured here is printed above so a drifted baseline is visible rather than compared against a stale number.

Saved raw runs: /Users/jasonssdev/Dev/Projects/openkos/evals/title_first/results/title-first-20260816T012408Z-qwen3-8b.json
