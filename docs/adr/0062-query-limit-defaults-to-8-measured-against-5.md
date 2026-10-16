---
type: Decision
title: "ADR-0062: The query limit defaults to 8, measured against 5"
description: openkos query hands the model at most 8 concepts by default instead of an unexplained 5, because a pre-registered measurement showed 5 cannot hold the gold set of an enumeration question and 8 recovered it without hurting the questions that already worked.
status: Proposed
date: 2026-10-16
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-16T00:00:00Z
sensitivity: public
---

# ADR-0062: The query limit defaults to 8, measured against 5

- **Status:** Proposed
- **Date:** 2026-10-16

## Context

`answer()` sent the model at most five concepts. Fusion truncates to `limit` and only those concepts reach context assembly and can be cited, so a question whose answer needs a sixth concept is answered without it and the citations cannot recover it. Five entered with the first cited-answer library and was never measured (#1377). The MVP 6 desktop app inherits the default, and a non-technical user never passes a flag.

## Decision

The default is `DEFAULT_QUERY_LIMIT = 8`, one constant in `retrieval/answer.py` read by `answer()`, `openkos query --limit` and the MCP `query` tool.

It was chosen by a bar written before the run ([`PREREGISTRATION-1377.md`](../../evals/query_limit/PREREGISTRATION-1377.md)): 15 runs per arm over `k` in 3, 5, 8 and 10, on a committed synthetic corpus with gold sets of six to eight concepts, smaller gold sets, single-concept hard negatives among near-identical siblings, and unanswerable questions. The smallest `k` passing every bar is adopted. Result, `qwen3:8b` with `bge-m3`, 19 questions, 285 queries per arm:

| k | multi fully retrieved | multi fact recall | small | single | false refusals | median s | median context chars |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 0 of 6 | 0.323 | 0.838 | 1.000 | 15 of 240 | 2.3 | 1364 |
| 5 | 0 of 6 | 0.641 | 0.950 | 1.000 | 0 of 240 | 3.3 | 2275 |
| 8 | 6 of 6 | 0.989 | 1.000 | 1.000 | 1 of 240 | 3.9 | 3625 |
| 10 | 6 of 6 | 1.000 | 1.000 | 1.000 | 0 of 240 | 3.5 | 4340 |

Both 8 and 10 passed; 8 is adopted as the smaller. Latency stayed within 1.16x and context within 1.59x of `k = 5`, and the context budget omitted no block.

## Consequences

- Enumeration-style questions can now find their sixth to eighth concept. The cost is about 1.6x the context characters per query.
- The measurement is narrow and says so: one chat model, one embedding model, one synthetic corpus of the question class most favourable to a larger `k`. The adjacent-question detector saw zero mishandled answers at both 5 and 8, so that bar is unfalsified here, not proven. A real prose-heavy bundle can still move the answer.
- `--limit` still overrides it per call. The candidate-pool floor (`max(limit, 10)`) is unchanged.
- `evals/retrieval_stability/` keeps `k = 5` on purpose: it varies the pool floor and its stored results were measured at 5.

## Alternatives considered

- **Keep 5.** Rejected: it cannot retrieve a gold set larger than five, which the measurement shows as 0 of 6 complete.
- **10.** Passed too, with no gain over 8 on fact recall beyond noise and 1.9x the context; the smallest passing `k` was the registered rule.
- **Adaptive limit** (by score gap or by the sufficiency check). Possibly better, but it should be compared against a measured fixed `k` first, which this is.
