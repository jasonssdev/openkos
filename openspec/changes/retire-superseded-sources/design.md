# Design: retire-superseded-sources

Refs #1268, #1259, #1263. Inputs: `proposal.md`, ADR-0032 (the deprecated
status is an export, edges are the authority), ADR-0041 (a changed source
imports as a new version; the supersession is proposed, not written), and the
code at `111d8c39`.

## Technical approach

Two independent mechanisms that together close the lifecycle.

### A. Deprecation follows the provenance of a superseded Source

```
 walk bundle once (already done by deprecated_concept_ids)
   per doc: own status (human), outbound supersedes edges, provenance ids
        |
        v
 edge_superseded = targets of non-self supersedes edges        (unchanged, R2)
        |
        v
 roots   = { id in edge_superseded : id starts with "sources/" }
 orphans = provenance_closure(provenance_by_id, roots) - roots
        |
        v
 effective_deprecated = own_deprecated | edge_superseded | orphans
```

`provenance_closure` is the function `forget --scope source` calls: a
concept joins when its provenance is non-empty and a subset of the set so
far, iterated to a fixed point. Its non-empty guard is what stops a concept
with no recorded provenance (hand-written) from being swept, so the
over-deletion barrier already exists and is reused rather than restated.

### B. Retiring a superseded Source is a forget that cleans its own history

```
 forget sources/old --scope source
   Phase A:  purge set P = closure(old)           (concepts citing ONLY old)
             refs = find_inbound_references(P)    (unchanged scan)
             if P contains a Source S that another Source N supersedes:
                 historical(1) = refs that are N's "supersedes -> S" relation
                 historical(2) = refs that are a surviving concept C's
                                 generated "## Related" link to S
                 plan rewrites: N.relations -= (supersedes -> S)
                                C.provenance/sources -= S ; C Related -= bullet
             blocking refs = refs - historical
             refuse if blocking refs exist and not --force   (unchanged rule)
   preview:  "~ bundle/<N>.md (remove supersedes -> S)"
             "~ bundle/<C>.md (detach S from provenance)"
   confirm (count gate unchanged)
   Phase B:  existing order; rewrites use the same drift-guarded write the
             resurrection status withdrawals use; then delete P, catalog,
             tombstones.
```

## Decisions

### D1. One pure predicate, three consumers

A new pure function in `lifecycle.py` takes `provenance_by_id`, the superseded
id set, and returns the orphan ids. `deprecated_concept_ids`,
`bundle/listing.list_objects` (which replicates the rule to keep `list` to one
walk, #195) and nothing else call it. The retrieval filters, candidate
generation and `answer` already go through `deprecated_concept_ids`, so
"uniform enforcement across all retrieval inputs" needs no per-seam change.
`provenance` ids are normalized with `bundle.provenance.normalize_provenance_id`
(the form `forget` compares). A document that fails to read or parse
contributes no provenance and no edges, as today: failing to hide is the safe
direction.

### D2. The export stays edge-only (the key scope call)

Exporting propagated deprecation would make `relate` write `status:
deprecated` into N concepts it did not name, make `unrelate`, `forget`,
`merge` and `repair` each recompute and withdraw them, and go stale whenever
a later attach adds a live Source to one of those concepts. The engine
never reads its own export (ADR-0032), so correctness of retrieval does not
depend on it. The cost is that an external OKF reader sees a propagated
concept as `stable`. That is recorded as a limitation, not hidden:
`superseded_from_metadata` (the export's input) is unchanged, `repair` and
the `lint` drift scan stay edge-only, and the spec says so explicitly. If an
external consumer needs it, a later change can project the orphan set through
the same single projection function; nothing here blocks that.

### D3. Roots are superseded Sources only

The issue's rule is "concepts whose entire provenance is the superseded
Source". Rooting the closure at every superseded id would also propagate from
a superseded `Decision` to the `Insight`s citing only it, which is a
different rule the issue does not ask for. Roots are therefore the
edge-superseded ids under `sources/` (type `Source` by directory, the same
convention `provenance_source_ancestors` uses). The closure itself is
transitive, so an `Insight` that cites only an orphaned concept is also
deprecated, consistent with what `forget --scope source` would delete.

### D4. Attach and propagation compose without a special case

After `attach-at-ingest`, a revised concept lists `[old, new]`. When `old` is
superseded, the subset test fails on `new` (live), so the concept stays live;
concepts the new version no longer yields have provenance `[old]` only and
are deprecated. Nothing in this change mentions attach.

### D5. The retire path is scoped to the generated shapes

A reference is historical only when it is exactly one of: a `supersedes`
relation from a Source to a purge-set Source whose referrer is outside the
purge set; or, in a surviving concept, the entries the engine itself
generated for the forgotten Source (the `provenance`/`sources` entry, and the
`## Related` bullet in the builder's exact shape `- [sources/<slug>](/sources/
<slug>.md) — <phrase>`). Everything else is a blocking reference exactly as
today: a hand-written link, another relation type, a body mention, an
unverifiable referrer. This keeps `--force` meaningful and keeps the change
from becoming a general reference-rewriting feature. The detached concept's
`sensitivity` is never lowered and its `version` is not bumped (forget
authors no content).

### D6. `purge` is untouched

`purge` resolves its purge set with `forget`'s Phase A and its inbound
reference count is rail 1. Sharing Phase A must not share the new
classification: `prepare_purge` keeps its own reference computation (it
already duplicates `forget`'s rather than calling it), and the spec adds a
scenario that purge still refuses. An erasure that silently rewrote other
documents would also need those rewrites in the history scrub, which is out
of scope.

### D7. The vocabulary note

`relation_type_note` returns `None` for any type in
`RESOLUTION_RELATION_TYPES` in addition to the seeded set. The seeded set,
`SUGGESTABLE_RELATION_TYPES` and `validate_relation_type` are untouched, so
the "an LLM must never propose a resolution type" guarantee is intact; only
the human-facing advisory stops contradicting the engine's own hint.

## Code reality check

1. **The "not seeded" note is deliberate.** `supersedes` sits in
   `RESOLUTION_RELATION_TYPES`, outside `REGISTRY`, so an LLM suggester cannot
   propose it. The fix is in the note, not the registry.
2. **Three places compute effective status.** `lifecycle.deprecated_concept_ids`,
   `lifecycle.superseded_from_metadata` (the export input) and
   `bundle/listing.py` (a deliberate replica for one-walk). The change
   touches the first and third and leaves the second edge-only on purpose
   (D2). Parity between the first and third is pinned by a test that
   asserts they agree on one fixture.
3. **`forget` leaves multi-source children untouched, but not their links.**
   The closure keeps a concept with a live Source. Its `## Related` link to
   the forgotten Source is still an inbound link reference, so attach makes
   `forget` of a superseded Source refuse even when the `supersedes` edge is
   handled. That is why D5 covers surviving concepts, not only the edge.
4. **`--force` after the edge leaves `lint`'s "Dangling references".** The
   relation stays on the new Source. Removing it in the same forget is what
   leaves `lint` clean.
5. **`lifecycle.py` documents itself as importing only `model.okf`.** The
   closure is in `bundle.provenance`. Apply decides between importing it
   (canonical-layer direction, no derived dependency) and moving the pure
   closure to a leaf; either way an AST guard pins that `lifecycle` never
   imports `state`, `retrieval` or `graph`.
6. **Pending-work rows.** The watch's `relation_type` row proposes `relate`;
   after this change the same row resolves as today. No queue change.

## ADR gate

Decides a pattern (effective deprecation is no longer purely edge-derived) and
a trade-off (an external reader does not see it, D2), and it changes what
`forget` may rewrite. Hard to reverse once users rely on retired sources
disappearing from answers. An ADR is created at apply (next free number,
status Proposed): "Effective deprecation follows the provenance of a
superseded Source; its export stays edge-only". It extends ADR-0032 without
editing it and builds on ADR-0041.

## Testing approach

Strict TDD. The predicate is pure, so its tests are table-driven (live
Source in provenance, empty provenance, transitive insight, cycle, hand-
written concept). A parity test runs `deprecated_concept_ids` and
`list_objects` over one fixture. The forget tests build the E2E shape: two
Source versions, a sole-source concept, a shared concept, a hand-written
link. Mutations target the non-empty guard, the `sources/` root filter, the
generated-bullet matcher, and the purge-unchanged scenario.
