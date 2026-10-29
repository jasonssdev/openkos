---
type: Decision
title: "ADR-0086: The series boost stays off because it removed too few foreign blocks"
description: Records that the pre-registered series-boost measurement was REJECTED on bar C1 at phase 1, so the boost proposed by ADR-0085 is not implemented and nothing ships, and states what a next registration would have to change.
status: Proposed
date: 2026-10-29
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-29T00:00:00Z
sensitivity: public
---

# ADR-0086: The series boost stays off because it removed too few foreign blocks

- **Status:** Proposed
- **Date:** 2026-10-29
- **Supersedes:** [ADR-0085](0085-a-question-that-names-a-meeting-series-boosts-that-series-and-does-not-filter-to-it.md)

## Context

ADR-0085 proposed that a question naming a meeting series moves that series' documents ahead of the others in the fused ranking (a boost, not a filter), off by default until a pre-registered measurement licensed it. The registration (`evals/series_boost/PREREGISTRATION.md`) set a phase 1 futility stop: the foreign blocks in the prompts of treated questions must fall by at least half (bar C1), or the run ends `REJECTED` with no chat call.

The run is recorded in `evals/series_boost/RESULTS.md`. Phase 1 recognised and treated 5 of 20 questions, with the identity and the wrong-series properties holding (P1, P2). The foreign blocks fell from 5 to 3, a 40% fall against the registered 50% minimum. C1 failed, the verdict is `REJECTED`, and no model answered anything.

## Decision

1. **The series boost is not implemented.** No setting, no code path and no default changes. ADR-0085's decision is not adopted.
2. **ADR-0085 is superseded.** Its reasoning for a boost over a filter (a misidentified series reorders and cannot empty) is not contradicted by the result: P2 held. What failed is the size of the effect on this population.
3. **A next registration changes the population, not the bars.** The bars were fixed before any result and are not relaxed to fit it. A new registration would have to say, before running, what makes the effect measurable: a population in which the named series has enough matching documents in the retrieval pool to fill the slots (the measured bundle's pool held fewer than the limit for the named series on some questions), and treated questions in a number that supports more than a count of single blocks. It would also state whether the boost is the mechanism under test or one of the alternatives ADR-0085 deferred.

## Consequences

- Retrieval and answers are unchanged for every workspace.
- Issue #1702 stays open as a problem report; this result does not say the mixing it reports is absent, only that this boost, on this bundle, did not remove enough of it at the prompt.
- The result is narrow: 5 treated questions, synthetic titles of the shape `Name: date`, one window. It supports "not licensed here", not "a boost cannot work".

## Alternatives considered

- **Lower the bar to 40%.** Rejected: a bar moved after the result is not a bar.
- **Run phase 2 anyway.** Rejected by the registration: a futility stop exists so a model is not run on a prompt the mechanism barely changed.
- **Keep ADR-0085 Proposed.** Rejected: it would read as a pending decision when the registered closing rule has resolved it.
