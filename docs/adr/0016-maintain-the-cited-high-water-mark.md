---
type: Decision
title: "ADR-0016: Maintain the cited high-water mark, not only apply it at birth"
description: Why the backfill sweep gained a second producer that repairs multi-source documents.
status: Accepted
date: 2026-08-16
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-08-16T00:00:00Z
sensitivity: public
---

# ADR-0016: Maintain the cited high-water mark, not only apply it at birth

- **Status:** Accepted
- **Date:** 2026-08-16
- **Amends:** [ADR-0012](0012-sensitivity-backfill-per-source-sweep.md)
- **Issue:** [#697](https://github.com/jasonssdev/openkos/issues/697)

## Context

ADR-0003 defines sensitivity as a high-water mark: a derived object is at
least as restrictive as everything it draws on. `query --save` applies that
rule at birth — `_stage_filed_answer` folds `okf.combine_sensitivity` over
each cited concept's own level — and nothing applied it again afterwards.

ADR-0012 built `backfill-sensitivity` on ONE producer: for every `type:
Source`, propagate that Source's level down its provenance closure. That
closure is `provenance_closure`'s conservative non-empty SUBSET rule, and the
rule is correct for what it was built for — `forget --scope source` deletes
and `set-sensitivity` re-classifies what it returns, so a concept still
holding one surviving source must stay out, or the sweep over-deletes and
over-classifies.

For a high-water mark that reasoning inverts. A document citing a
confidential Source and a public one IS confidential, and the subset rule
keeps it out of both closures. ADR-0012 saw this and deferred it explicitly:
such a document "stays reported, not resolved, until a human acts", surfaced
by a `multi-source-uncovered` finding "explicitly marking the finding as not
covered by `backfill-sensitivity`", with combining across Sources "deferred to
MVP-2/3 per ADR-0009".

Every filed insight is in that category, because a synthesis over several
sources closes no single Source's provenance closure. So raising one cited
Source left every insight derived from it below its own inputs, with manual
`set-sensitivity` per document as the only repair.

That is not cosmetic. `sensitivity` is an enforcement boundary:
`sensitivity.blocks_llm_send` is the fail-closed authority that withholds a
document from an `llm.chat` send, and `retrieval/answer.py` re-checks it per
document at assemble time. An operator who raises a Source is performing a
deliberate confidentiality action; leaving the derived documents behind means
that content keeps being sent to the model and keeps appearing in answers,
which is the outcome the raise was meant to prevent. `lint` did detect it and
explain it, so the gap was visible rather than silent — but the repair was
manual and per document, and nothing made the operator perform it.

## Decision

`backfill-sensitivity` gains a **second producer**,
`bundle.provenance.resolve_cited_high_water_raises`, merged into
`resolve_backfill_raises` by the same max rule that already merges overlapping
Source closures. The per-Source closure producer is unchanged, and so is
`set-sensitivity`.

**The fold mirrors birth, not the closure walk.** It recomputes
`okf.combine_sensitivity` over each document's own DIRECT `provenance`
entries — the same set, and the same fold, `_stage_filed_answer` uses — so a
maintained level is the level that document's own birth rule would produce
today. Reading Source ANCESTORS instead (`provenance_source_ancestors`, #628)
was rejected: it would miss a cited intermediate concept raised by hand
without its Source, and it would answer a different question from the one
ADR-0003 asks.

**It is a fixpoint, not a pass.** Raising an intermediate concept changes the
mark for everything citing it. Termination is `provenance_closure`'s own
argument: `combine_sensitivity` is monotone and `SENSITIVITY_ORDER` is finite,
so levels only rise and the loop halts, including on a provenance cycle.

**Raise-only, and a Source is never staged.** A document deliberately
classified above everything it cites keeps its level. A Source's level is
operator-set truth, never derived from what it cites.

**A dangling citation leaves a document unstaged.** `_stage_filed_answer`
folds an unreadable citation to `confidential`, fail-closed, and that is right
when guarding ONE document the operator is creating. It is wrong for a
bundle-wide sweep, where it would raise every descendant of a single dangling
reference — a blast radius no preview makes safe — and it would contradict the
`lint` finding that sent the operator to the verb. `check_dangling_provenance`
owns that signal, per ADR-0012's own design D8.

`lint`'s `multi-source-uncovered` finding SURVIVES rather than being retired.
It still answers a question the other finding does not — which single-Source
closure a document belongs to, and therefore whether `set-sensitivity <source>`
alone would have reached it — and the two remedies differ in blast radius. Its
detail no longer marks the document as uncovered; it now offers both remedies.
The command-span rule from #693 is unchanged: exactly one runnable command
inside a backtick span, and it is still the per-document one, so `next`'s
reason line cannot put a bundle-wide sweep in copy-paste shape under a finding
that names one document.

## Consequences

Easier: an operator who raises a Source has one command that brings every
derived document — single-Source and multi-source alike — back to compliance,
with the preview, confirm gate, drift re-check, single log entry and single
commit the sweep already had. The `multi-source-uncovered` finding stops being
a list of things the tooling admits it cannot fix.

Harder: `backfill-sensitivity`'s blast radius is genuinely larger than it was,
because the second producer reaches documents the first could not. The preview
is what keeps that honest — it lists every staged `(concept_id, current ->
new_level)` raise before the confirm gate, and declining is still the dry run.
A behavior change also landed in a pinned test: `resolve_backfill_raises` now
raises a descendant citing two unrelated Sources, where the ADR-0012-era test
asserted `raises == []`.

`next`'s tier 7 is deliberately NOT re-pointed at the sweep. It still
recommends `set-sensitivity <id> <level>`, the narrowest repair for the one
document the finding names; both commands now work, and re-ranking tiers 6 and
7 against each other is a separate question about `next`'s ordering, not about
this gap.

## Alternatives considered

- **Propagate at raise time, from `set-sensitivity`.** Rejected: it would
  duplicate the sweep's preview, drift-check and logging inside a much more
  frequently used command, and widen the blast radius of every single-document
  raise an operator performs.
- **Recompute the mark on read.** Rejected: sensitivity is written into
  frontmatter and read by tools that never call our code; a value that only
  exists at read time is not a value the bundle carries.
- **Loosen `provenance_closure`'s subset rule.** Rejected outright: that rule
  is a shared write gate, and `forget --scope source` depends on its
  conservatism to avoid over-deleting. The high-water mark needed a different
  relation, not a weakened version of that one.
- **Retire the `multi-source-uncovered` finding.** Rejected: it still reports
  a real, distinct property, and deleting a detection because a repair now
  exists would leave nothing naming documents in that state.
