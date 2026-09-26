---
type: Decision
title: "ADR-0024: A refinement is a stored revises relation, and a resolved pair is never re-judged"
description: reconcile records a refinement as a directional revises edge that hides nothing, and any pair joined by supersedes, reconciled_with or revises leaves contradiction candidacy.
status: Accepted
date: 2026-09-25
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-25T00:00:00Z
sensitivity: public
---

# ADR-0024: A refinement is a stored revises relation, and a resolved pair is never re-judged

- **Status:** Accepted
- **Date:** 2026-09-25

## Context

`openkos reconcile` records a human's resolution of two conflicting concepts. It has two outcomes. The first is a symmetric `reconciled_with` edge on both concepts: both coexist, and there is no order. The second is a directional `supersedes` edge on the winner. The loser is then hidden as current everywhere that reads `lifecycle.deprecated_concept_ids`, and `list` shows it as `deprecated`.

Issue #1014 needs a third outcome. Decisions usually evolve by **refinement**: a later decision narrows or extends an earlier one, both stay in force, and the order between them matters. Neither existing outcome fits. Recording a refinement as `supersedes` hides a decision that is still valid. Recording it as `reconciled_with` loses the direction. The revision detector planned for #1014 needs a write target for exactly this case.

The forces:

- **The value is persisted in users' bundles.** A relation type in `relations:` frontmatter is an on-disk interface. Every tool reading the bundle sees the string, and renaming it later requires migrating every bundle that holds it. The CLI flag that writes it is a public interface too.
- **Hiding is a consequential act.** Reversal and refinement differ exactly in whether something disappears from retrieval. A wrong choice either hides live knowledge or keeps dead knowledge current.
- **A resolution must stay a human judgment.** Resolutions are written only by an explicit `reconcile` behind its confirm gate. An LLM edge suggester must never be able to propose one.
- **Reconciling a pair used to manufacture its own contradiction candidacy.** Contradiction candidates are drawn from typed graph edges, and a resolution edge is itself a typed edge. A symmetrically reconciled pair therefore stayed a candidate, and after `reconcile` rewrote its bytes the persisted finding went stale, so the next run judged the pair again. A `revises` pair would behave the same way. This contradicts `reconcile`'s stated purpose, which is that the decision is kept rather than repeated.

## Decision

We add a third resolution, **`revises`**. It is a relation type written by `reconcile --revision <id>`. The named pair member, the later and refining concept, holds one outbound `revises` edge pointing at its counterpart. No back-edge is written, which mirrors `supersedes`. Both concepts receive a `## Reconciliation` note naming their side of the relation (`revises` / `revised`), and **nothing is hidden**. `revises` is not special-cased by the deprecation predicate or by `list` STATUS, so both ends stay current.

The semantic line is fixed: **a reversal is `supersedes` and hides the old concept; a refinement is `revises` and hides nothing.**

The three resolution types are named once, as `RESOLUTION_RELATION_TYPES` in `model/relations.py`. They sit outside the seeded `REGISTRY`, so `SUGGESTABLE_RELATION_TYPES` can never offer them to an LLM. The same constant drives two consumers:

- `reconcile`'s state classifier. A pair carries at most one resolution. An exact repeat is an idempotent no-op, and any change of mode or direction refuses with zero writes. A pair carrying disagreeing resolution edges, which only hand editing can produce, refuses every request instead of being classified by precedence.
- Contradiction candidate generation. A pair joined by any resolution edge, in either direction, is not a candidate. It is dropped before the candidate count and the cap, so `curate`'s cost gate and the judged spend stay one number. The drop applies even under `--include-deprecated`. To have such a pair judged again, the human removes the relation.

## Consequences

Easier:

- A refinement is recorded without hiding anything, and the future revision detector has a write target that needs no new machinery.
- `reconcile` behaves as its help text promises: a recorded resolution is kept, not re-litigated on every contradiction run. Resolved pairs no longer spend LLM calls or cap slots.
- A single constant keeps the classifier and the candidate filter from drifting apart, and a test ties the classifier's type-to-mode table to it.

Harder, or accepted:

- `revises` is now part of the on-disk vocabulary. Renaming or removing it requires a bundle migration.
- This is a shipped-behavior change, confirmed by the owner on 2026-09-25. A pair joined by `reconciled_with` or `supersedes` is no longer re-judged, even after one of its concepts is edited. A real contradiction introduced by a later edit to a resolved pair goes undetected until the human removes the relation.
- A bundle whose only typed edges are resolution edges now reaches `contradictions`' vacuous-coverage warning, whose wording ("no applied relations") is then imprecise.
- Hand-edited pairs that carry two disagreeing resolutions were previously classified by precedence (`supersedes` first). They now refuse every `reconcile` request until they are fixed by hand.
- There is still no upgrade path between resolutions (for example `revises` to `supersedes`). The note anchor is keyed on the counterpart alone, so an in-place upgrade would leave a stale note. Changing a resolution remains a manual edit or a git revert.

## Alternatives considered

- **Record refinements as `supersedes` or `reconciled_with`.** Rejected. The first hides a decision still in force. The second loses the order that makes it a refinement.
- **A `revised_by` edge on the earlier concept, or edges on both ends.** Rejected. The newer concept asserts the relationship, as with `supersedes`, and one outbound edge per resolution keeps the state classifier simple.
- **CLI shape `--refines <id>`, or `--relation revises` combined with `--winner`.** Rejected. `--refines alpha` is ambiguous about direction, and a wrong direction is silent. `--winner` implies that something loses, which a refinement does not. `--revision` has the same shape as `--winner` (a noun naming the holder) and shares a root with the stored type.
- **Seed `revises` into `REGISTRY`.** Rejected. Seeding would make it suggestable by an LLM, and a resolution is a human judgment. `supersedes` and `reconciled_with` are already absent from the registry for the same reason.
- **Exclude only `revises` pairs from contradiction candidates, or rely on the decline ledger.** Rejected. The defect, that a resolution edge nominates its own pair, applies equally to all three types. `reconcile` never writes a decline record, and declines are a display filter rather than a candidate filter.
- **Filter resolved pairs after the cap, or with a separate frontmatter walk.** Rejected. A post-cap filter lets resolved pairs starve live ones of cap slots, the bug the deprecation filter already fixed. A separate walk adds a bundle read and bypasses the graph store that every caller already supplies.
- **An `--include-resolved` flag.** Rejected. Removing the relation already restores candidacy, and a flag would make the resolution optional at read time.
- **Classify mixed resolution states by precedence.** Rejected. An "idempotent" run over a contradictory state would log "already reconciled; no change" over a pair whose own edges disagree.
