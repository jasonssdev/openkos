---
type: Decision
title: "ADR-0026: A superseded concept re-enters an answer only as labelled, citable history, and only when the workspace opts in"
description: query may attach the earlier versions a retrieved concept supersedes or revises as separately numbered, labelled history blocks under every send-time gate; a cited history block is filed by --save as ordinary provenance with a prose mark; the behavior is off by default until measured.
status: Accepted
date: 2026-09-26
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-26T00:00:00Z
sensitivity: public
---

# ADR-0026: A superseded concept re-enters an answer only as labelled, citable history, and only when the workspace opts in

- **Status:** Accepted
- **Date:** 2026-09-26

## Context

Status-aware retrieval hides a concept completely once another concept `supersedes` it, or once its own `status` is `deprecated` (`lifecycle.deprecated_concept_ids`). A `revises` target stays current (ADR-0024). The hiding is deliberate: an obsolete decision must not be presented as the one in force.

The cost is that `query` cannot answer "what did we decide about X, and did it change?". The chain that answers it is already canonical: `reconcile` wrote it into frontmatter as `supersedes` and `revises` edges on the later concept. The answer path simply never reads it.

The forces:

- **The hiding guarantee.** Until now, no path let a status-hidden concept reach the synthesis prompt. Any path that does is a trade-off against the guarantee, and it has to be narrow enough that the guarantee still means something.
- **A citation names the bytes shown (#753, #882).** Citations are decided from the model's `USED:` line over numbered blocks. Text spliced into another concept's block could not be attributed. A block that is never numbered could not be disclosed.
- **Sensitivity.** A confidential concept never leaves the device unless a gate admits it. Every block that reaches `llm.chat` must pass the same send-time gates a hit passes (ADR-0003, the `sensitivity.should_block` re-check).
- **`--save` makes provenance permanent.** A filed Insight's `provenance:` is written into the user's bundle, and later merges, forgets and purges read it. Anything this decision lets into a citation ends up on disk in users' bundles. That is the hard-to-reverse part.
- **OKF.** Links are untyped, and the kind of a relationship lives in the prose (§5.1, §5.3). A new frontmatter key is an extension that every reader must tolerate.
- **Unmeasured prompt behavior.** The model may present an earlier version as current. No harness measures that yet. The project does not ship unmeasured prompt changes as defaults (#812, #760).

## Decision

**A superseded or revised concept enters the prompt only as a history block attached to the retrieved concept that supersedes or revises it.** It is never a retrieval hit. It is never counted in `fused_count` or in the hit counts. It never enters when `--include-deprecated` is on, because there it would already be an ordinary hit. The walk follows only the retrieved concept's own outbound `supersedes` and `revises` edges. It is bounded in depth and in blocks per concept, and it is deterministic.

**Every history block passes exactly the gates a hit passes, through the same guarded read.** That means the confidential `blocked` set, the per-document `sensitivity.should_block` re-check, and skip-on-unreadable. A predecessor that fails a gate is a dead end: the walk does not pass through it, so a later version's label can never name a concept that was not admitted.

**A history block is its own numbered, citable block.** Its label states the relation, the concept that holds the edge, the event date, and whether it is still current. The system prompt does not change. `Citation` gains `history: "superseded" | "refined" | None`. `--save` files a cited history block in `provenance:` exactly like any other citation, and marks it only in the prose of the `## Related` bullet. No frontmatter key is added.

**History spends its own successor's budget, never anyone else's.** Every other retrieved concept keeps the share it has today.

**The behavior is opt-in and off by default.** It is controlled by the workspace key `revision_history`. When it is off, `query` makes no extra read and sends a byte-identical prompt. Neither the template nor the documentation recommends enabling it until a harness has measured it.

**Event dates are said, never guessed.** A history label shows the event date that the concept's own provenance resolves to, reading at most one hop beyond its direct provenance. When the date is unknown, the label says "unknown". It never falls back to ingest time (ADR-0023). The four-state date vocabulary lives in one package-root leaf, `openkos/event_dates.py`, which both `retrieval/` and `resolution/` import.

## Consequences

Easier:

- A question about how a decision changed can be answered from the recorded chain, and the answer attributes each earlier version it used.
- Filed answers keep honest provenance. The prose mark says which cited concepts were earlier versions, and old code reads the result as ordinary, legal OKF provenance.
- A workspace that does not opt in pays nothing, and every existing prompt measurement stays valid.

Harder, or accepted:

- For the first time, `provenance:` in users' bundles can point at superseded concepts. `forget`, `purge`, `merge` and `lint` already treat inbound provenance independently of type, so they need no change. But every future provenance consumer has to tolerate a deprecated target.
- The hiding guarantee is now narrower. The new statement is: a hidden concept is never a hit, and it reaches the prompt only as a labelled history block of a concept that is current and was retrieved. Any future path that re-admits hidden concepts needs its own decision.
- A successor with a long history pays for it in its own share of the window. Under a tight window, the successor can be excerpted to make room. The history is dropped entirely rather than cost the successor its whole body.
- The label names the edge holder by concept id inside the bracketed label. The existing #193 scaffold stripper removes it if the model copies it into prose.
- The bounded date resolver can report "unknown" where the detector's transitive resolver would find a date. The two share a vocabulary and an aggregation rule, not an implementation.
- Enabling the key by default, or recommending it, needs a measurement first: a labelled question set of "it used to be X" answers.

## Alternatives considered

- **Splicing the history into the successor's body.** Rejected: the model could not attribute it, and a citation of the successor would claim bytes the successor does not contain (#882, #753).
- **Sending history as an unnumbered block.** Rejected: the model could use it without being able to report doing so, so provenance would be silently incomplete.
- **Letting superseded concepts compete as ordinary hits**, with a status note. Rejected: it undoes status-aware retrieval for every query, not only for history questions, and it spends hit slots on obsolete material.
- **A `history_provenance:` frontmatter key on filed Insights.** Rejected: it is a new persisted schema that every merge, forget, purge and lint path would have to learn. A prose mark degrades gracefully under OKF §5.3.
- **`Citation.superseded: bool`.** Rejected: a refined predecessor is still current, and marking it superseded would be false.
- **A flat budget pool, where history blocks raise the shared divisor.** Rejected: an unrelated hit's excerpt would shrink because some other concept has history.
- **Enabling it by default.** Rejected: it is unmeasured prompt-affecting behavior.
- **Walking the typed graph, or computing dates with whole-bundle provenance ancestry.** Rejected: the cost would scale with the bundle rather than with the chain, and the graph is a derived cache that the answer path no longer reads (#434).
