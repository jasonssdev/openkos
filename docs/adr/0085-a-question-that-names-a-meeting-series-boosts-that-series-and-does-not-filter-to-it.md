---
type: Decision
title: "ADR-0085: A question that names a meeting series boosts that series and does not filter to it"
description: Proposes that when a question names a meeting series, retrieval ranks that series' documents first without removing the others, identifying the series from the Source title and provenance, off by default until a pre-registered measurement licenses it.
status: Superseded by ADR-0086
date: 2026-10-29
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-29T00:00:00Z
sensitivity: public
---

# ADR-0085: A question that names a meeting series boosts that series and does not filter to it

- **Status:** Superseded by ADR-0086
- **Date:** 2026-10-29

## Context

Issue #1702 reported answers that mix two meeting series about the same product, even when the question names the series. The series identity lives in the Source title, and retrieval ranks documents without looking at it. Near-duplicate and cross-series pages compete for the eight slots.

Two ways to use the series a question names:

- A **filter** keeps only that series' documents. It is exact when the series is identified correctly and wrong in a way the person cannot see when it is not: a misidentified series (a title that does not match, a question that names the wrong one, a paraphrase read as another series) leaves nothing to answer from, so the person gets an empty answer to a question the library could have answered.
- A **boost** ranks that series' documents first and leaves the rest in the list behind them. A misidentified series reorders; it cannot empty.

The owner decided on 2026-10-29: scope by boosting, not by filtering; other series can still appear when relevant; measure before switching it on.

Retrieval already holds what a deterministic signal needs. A Source's `title` is in its frontmatter, and every derived document (a Decision, a Concept) carries `provenance`, the list of Source ids it came from. A fused ranking is a list of concept ids that `fusion.select_top` cuts to the limit. No new field, index or model call is required.

## Decision

1. **Series scoping is a soft boost.** When a question names a series, the documents of that series are moved ahead of the others in the fused ranking, before the cut to the limit. The rest keep their order behind them. Nothing is removed and nothing is added; the retrieved pool is the same set. A question that names no series is untouched.
2. **A series is identified from the Source title and `provenance`, deterministically.**
   - A Source's series key is its `title` with a trailing date and the separators before it removed (ISO, `10 de marzo de 2026`, `March 10, 2026`, `10 March 2026`), then case- and accent-folded. A key needs at least two words.
   - A document that is not a Source takes the keys of the Sources in its `provenance`.
   - A question names a series when a key occurs in it as whole words after the same folding.
   - This is a pure function of the bundle and the question text: no model, no stored state, no new frontmatter, so it stays reconstructible from the canonical files.
3. **What is not handled.** A paraphrase or translation of the series name (a question in English about Sources titled in Spanish); a series whose Sources share no title prefix; a one-word series; a document with no `provenance` (it is never boosted); a title that is not stable across the series' meetings. Each of these degrades to no boost, which is today's behavior. Two series named in one question are both boosted.
4. **Off by default until measured.** The boost ships behind a setting that defaults to off. It is switched on only after `evals/series_boost/PREREGISTRATION.md` is registered and its verdict is `LICENSED`, and then by an OpenSpec change. The measurement runs the boost in the eval harness as a re-rank of the fused ranking, so the mechanism measured is the one that would ship.

## Consequences

- A wrong or missing series identification costs nothing worse than today's ranking: the same candidates, a different order. This is the reason for choosing a boost.
- A boost is not a guarantee. When a series has more matching documents in the retrieval pool than the limit, the top slots are all that series and the boost behaves like a filter on that question. The pool is about twenty candidates, so this bounds the effect; it is also why the other series can still appear only when the matching series is thin.
- Series identity rests on title text. Source titles come from the first heading or the file name, so a library whose titles do not repeat a prefix gets no boost. The measurement uses a synthetic bundle whose titles fit the rule, which is a limit it states.
- Cross-lingual questions are not recognised, by design of the signal; a translation rule would need its own measurement.
- Nothing changes for any workspace until the setting is turned on.

## Alternatives considered

- **Hard filter to the named series.** Rejected by the owner: a misidentified series becomes an empty answer.
- **A series field written at ingest.** Exact, but a new frontmatter extension and a migration for every existing library, and it still needs a way to decide the name at ingest. Deferred until the title signal is shown insufficient.
- **A model call to classify the question's series.** Adds a call, a failure mode and latency to every question for a problem a string match solves when titles are regular. Rejected for now.
- **A fixed score bonus inside reciprocal rank fusion instead of a partition.** Softer when many documents match, but it adds a tuned constant that the measurement would also have to fit. The partition has no parameter; a bonus can follow if the partition proves too strong.
