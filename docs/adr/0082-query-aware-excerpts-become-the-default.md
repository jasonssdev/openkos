---
type: Decision
title: "ADR-0082: Query-aware excerpts become the default"
description: Proposes that query choose the passages of an over-long retrieved document by their BM25 match to the question by default, because the pre-registered comparison on the retrieval gold set raised delivered gold evidence from 13 to 25 of 54 questions at 5 documents and from 6 to 18 at 8, with no language or kind regressing.
status: Proposed
date: 2026-10-28
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-28T00:00:00Z
sensitivity: public
---

# ADR-0082: Query-aware excerpts become the default

- **Status:** Proposed
- **Date:** 2026-10-28

## Context

`query` fits its retrieved documents into the model's context window with a fair-share split. A document longer than its share is cut to evenly spaced 800-character windows regardless of the question, so the only passage that answers it can be elided. Issue #1647 added an opt-in alternative, `query_aware_excerpts`, shipped default off: the windows are ranked by Okapi BM25 against the question over that document's own windows, model-free, with the same budget, the same split and the same elision marker. A document that fits its share is sent byte-identical either way.

The default was left off until the choice was measured. [`PREREGISTRATION-1647.md`](../../evals/retrieval_gold/PREREGISTRATION-1647.md) fixed the question, the metric (`delivered`: every annotated gold document has a gold span in its final context block), the test (paired bootstrap, one-sided 95%, `delivered@5` and `delivered@8`, no language below -0.03) and an exposure rule before any `delivered` number existed. [`RESULTS-1647.md`](../../evals/retrieval_gold/RESULTS-1647.md) records the run, under umbrella #1652:

| k | Baseline | Query-aware | Lower bound |
| --- | --- | --- | --- |
| 5 | 13 of 54 | 25 of 54 | +0.130 |
| 8 | 6 of 54 | 18 of 54 | +0.111 |
| 10 | 2 of 54 | 16 of 54 | +0.148 |

The verdict is SUCCESS, with real exposure (31 of 54 baseline questions lost to trimming at k=8). Retrieval is identical in both arms; `not_retrieved` is unchanged and `omitted` stays 0. Both languages gain, no question kind regresses, and the vocabulary-gap stratum gains too. Two questions regress at k=8 (`q24`, `q29`).

What the evidence is not: the passages were written by Claude models and never reviewed by a person, list-style questions are credited for any one part, no chat model is in the loop (so this is delivery, not answer quality), and it is one bundle and one window setting.

## Decision

Proposed: **`query_aware_excerpts` defaults to `true`.** `config.DEFAULT_QUERY_AWARE_EXCERPTS` becomes `True`, so a workspace that never set the key, or set it to null, gets the question-aware excerpt. `false` in `openkos.yaml` restores the even-coverage excerpt exactly. `answer()` keeps `query_aware_excerpts=False` as its parameter default: the application layer passes the configured value explicitly, so library and eval callers are unchanged unless they opt in. The shipped template and the demo workspace show the opt-out line.

## Consequences

- **Output differs only where it was already trimmed.** When every retrieved document fits its share, the prompt is byte-identical to today. The change reaches only prompts that were being elided.
- **BM25 windows can drop context a reader expected.** Even coverage always kept the start and end of a long document; the question-aware excerpt keeps the windows that share the question's words and can discard the opening or closing of a document. A question whose answer depends on a document's framing, not on its words, can lose that framing. A question with no usable term, or matching no window, falls back to even coverage.
- **The old behaviour is one config line away:** `query_aware_excerpts: false`.
- **Interaction with the default limit of 8 ([ADR-0062](0062-query-limit-defaults-to-8-measured-against-5.md)).** The comparison shows, as an observation and not a registered result, that `delivered` falls as the limit rises: with even coverage 13 of 54 at k=5 but 6 at k=8, because more blocks share one budget; the query-aware arm keeps the shape at a higher level (25, 18, 16). Making excerpts query-aware recovers much of the loss at 8 but not all of it (18 of 54 at k=8 against 25 at k=5). ADR-0062 chose 8 on retrieval recall; whether the limit should move now is a separate question this ADR does not decide.
- **No new cost.** No model call, no embedding call; the scorer is deterministic.
- **The evidence is delivery, not answers.** A delivered passage can still be misread. A regression in answer quality would show only in an end-to-end evaluation not run here.

## Alternatives considered

- **Keep it off.** Zero risk of changing any prompt, but it leaves the measured loss in place for every user who never reads the config, and the desktop app's user passes no flag. The gain is large against the exposure and the opt-out is a single line.
- **On only above a size.** The key already acts only on documents above their share, so a size threshold would re-implement the fits-or-not test the excerpt already applies, and would add a second knob with no measurement behind it.
- **Dense or hybrid window scoring.** The embedding variant of #1647 was not under test and needs an embedding call per window; BM25 already clears the registered bar.
