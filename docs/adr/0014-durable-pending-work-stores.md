---
type: Decision
title: "ADR-0014: Durable pending-work stores -- findings in .openkos/, decisions in bundle/.state/"
description: Contradiction findings persist in .openkos/findings.db (recomputable, delete-without-rebuild); operator decisions extend bundle/.state/ by exactly one kind; no two-phase write is needed because the join is computed at read time.
status: Accepted
date: 2026-08-12
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-08-12T00:00:00Z
sensitivity: public
---

# ADR-0014: Durable pending-work stores -- findings in `.openkos/`, decisions in `bundle/.state/`

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

`curate`'s Contradictions stage runs an LLM over candidate pairs -- measured
at 64 calls / 3m59s for three contradictions -- and produces
`ContradictionVerdict` objects whose own docstring stated the outcome
plainly: "Ephemeral -- never a persisted OKF type or `bundle`/`state`
file." The verdicts were echoed to the terminal and discarded with the
process. A lost finding costs money and minutes to recompute. A lost
*decision* (an operator declining a specific contradiction) is
unrecoverable and user-visible as a rejected proposal reappearing.

Those two things -- a machine finding and a human decision -- have opposite
requirements: findings are recomputable machine inference, bulky, and their
rationale embeds concept text; decisions are irreplaceable human judgment,
tiny (ids and a verdict), and carry no confidential content. Conflating
them into one store forces the worse policy onto one of the two natures.
ADR-0013 already established `bundle/.state/` as the mechanism for
irreplaceable, small, git-tracked sidecar state (the merge ledger); the
question this ADR settles is whether findings belong there too, and
whether a decision write needs the same two-phase crash-safety machinery
ADR-0013 needed for the merge ledger.

## Decision

We split storage along the two natures, and extend `bundle/.state/` by
**exactly one kind**.

**Findings** (contradiction verdicts) live in `.openkos/findings.db`, a
SQLite store opened through the same `state.derived.open_derived_connection`
opener every other persisted derived index (`fts.db`, `graph.db`) already
uses. `purge`'s existing delete-and-rebuild already physically deletes
`.openkos/*.db`, so this adds no new privacy surface. Unlike `fts.db`/
`graph.db`, `findings.db` is **not** rebuilt in-line by `purge` -- it
shares `vectors.db`'s posture (delete, then lazily re-derive at real LLM
cost on the next `curate` run), because regenerating a finding is not free.
It also does **not** participate in `state.derived.MANIFEST_HASH_KEY`
gating: a findings row is not derivable by a whole-store rebuild, so a
whole-store staleness gate would be a lie. Staleness is decided **per
finding** instead, from an ordered list of `(input_ref, sha256)` digest
rows recorded alongside the verdict -- the finding is stale iff any row's
current digest no longer matches.

**Decisions** (declined/re-opened) extend `bundle/.state/` by exactly one
kind: `bundle/.state/decisions/<pair_ids[0]>.decisions.okf`, one file per
sorted-first concept id, reusing ADR-0013's mechanism verbatim --
`okf.dump_frontmatter`/`load_frontmatter` over an empty body (ADR-0002
invariant 3), a non-`.md` suffix for free structural exclusion from every
`rglob("*.md")` walk, and `okf.concept_path_for`'s `(root, suffix)`
generalization for id-to-path mapping. A record holds ids and a verdict
only -- no rationale, no body text -- which is what keeps this store
non-confidential. Machine findings are deliberately excluded from
`bundle/.state/`: they are exactly the kind of bulky, confidential-content
state ADR-0013's mechanism was never meant to carry.

**No two-phase write.** ADR-0013 needed a hash-bound intent marker because
one merge spans two files (survivor + ledger sidecar) that must agree, and
disagreement is silently irreversible. This design has no such pair: the
two stores are joined only at **read time**, by a `decision_key` derived
from the proposal (`sha256("contradiction/v1\n" + pair_ids[0] + "\n" +
pair_ids[1] + "\n" + (merged_absorbed_id or ""))[:32]`) -- never a findings
row id, since a findings row is recomputed (and its row id changes) on
every `curate` run. Neither store holds a pointer into the other, so a torn
write to one store can never orphan the other: a torn findings write costs
LLM calls on the next run (findings are recomputable by definition), and a
decision write is one `fsio.write_atomic` to one file with no second file
it must agree with.

## Consequences

Easier: a `curate` run's judgment is not thrown away; `next`/`status` can
read persisted findings without a new LLM call; declining a contradiction
is durable and survives full recomputation of the finding it responds to.

Harder: `.openkos/findings.db` is a fourth path `_purge_rebuild_indexes`'s
explicit delete tuple must name (a named hazard, not a silent one --
tracked by the sweep work that lands alongside the first CLI verb able to
write a decision file, per this change's maintainer decision D6);
`bundle/.state/decisions/**` is a new subtree `purge`/`forget`'s privacy
sweep must reach, and `_autocommit`'s scoped `git add` must explicitly
stage.

Accepted going forward: this ADR governs the contradictions advisor only
(#556). Replicating the same split to the other three advisor kinds
(candidate edges, duplicate groups, volatility proposals) is named
follow-on work, not decided here.

## Alternatives considered

- **Frontmatter sidecars under `.openkos/` for findings.** Rationale text
  quotes concept bodies, so each sidecar would be a confidential-content
  file, and `.openkos/` has no INCLUDE-walk sweep today -- a new privacy
  surface the proposal explicitly promised not to create.
- **Decisions in `.openkos/` too.** Deleted by `purge`'s existing
  delete-and-rebuild -- exactly the loss this ADR exists to prevent for an
  irreplaceable human decision.
- **A two-phase write with a hash-bound intent marker for decisions.** No
  cross-file invariant exists here (Decision 7 above); the marker would add
  a refusal state with nothing to protect.
- **Keying a decision on a findings row id.** The row id changes on every
  recompute, so every declination would silently evaporate on the next
  `curate` run.
