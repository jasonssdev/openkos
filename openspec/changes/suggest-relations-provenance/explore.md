# Exploration: suggest-relations provenance duplication (issue #135)

Design/discussion issue. `suggest-relations` only ever has concept→source
PROVENANCE edges to type, because extraction writes a concept→source body-link
("## Related — source this was extracted from") for every derived object and never
a concept→concept link. These provenance edges duplicate the `provenance:`
frontmatter, so the command asks the user to type a fact the bundle already knows
(always `derived_from`), while the genuinely useful concept↔concept relations do
not exist to be suggested. Observed on a 33-concept bundle: concept→concept
body-links = 0, concept→source = 33.

## Current State (file:line)

- `untyped_edges()` / `_candidate_edges()` (`src/openkos/resolution/edge_typing.py:102-138`):
  `untyped_edges` is a row-level filter (`relation_type is None`); `_candidate_edges`
  adds pair-level exclusion of already-typed pairs. Neither has any signal to
  recognize a provenance-mirroring link — `Edge` (`graph/base.py:20-35`) is just
  `(source_id, target_id, relation_type)`.
- Edge extraction (`graph/sqlite_graph.py:86`, `_LINK_RE`): walks every
  bundle-relative `[text](/….md)` body link uniformly — no comparison against
  `provenance:` frontmatter anywhere.
- The concept→source `## Related` link is rendered by `okf.build_concept()`
  (`model/okf.py:140-206`, render at 201/205). Ingest's call site
  (`cli/main.py:1122-1131`) always passes `provenance=[f"sources/{source_slug}"]`
  — confirms the 33/0 pattern.
- `derived_from` is already seeded in the vocabulary (`model/relations.py:32`).
  Typed edges live in `relations:` frontmatter and can coexist with an untyped
  body-link row for the same pair (`sqlite_graph.py` docstring 24-38) — the exact
  shape an accepted `relate` produces. So the machinery already fully supports
  concept↔concept typed edges; only the untyped concept↔concept CANDIDATE input
  is missing (option b's real scope).

## Correction to the issue's framing

Extraction is NOT the sole producer of `## Related` links. `query --save`
(`cli/main.py:5312-5320`) also calls `build_concept`, with
`provenance=[citation.concept_id …]` (arbitrary cited CONCEPTS, not sources).
So concept↔concept `## Related` links backed by `provenance:` frontmatter already
exist for filed answers; ingest just never produces them. **The reliable
"mirrors provenance" signal is `provenance:` frontmatter membership, NOT a
`sources/`-prefix heuristic** (which would miss `query --save`'s citation links).

## Options

- **(a) Exclude provenance-mirroring edges** from candidates by `provenance:`
  frontmatter match. Low effort, deterministic; alone it leaves `suggest-relations`
  a permanent no-op until (b) exists.
- **(b) Emit concept↔concept candidate edges** (extraction LLM, a mining pass, or
  manual-`relate`-only) — the genuinely missing input. Open scope: candidate
  source, combinatorial bound, determinism story all undecided. High effort,
  separate follow-up. Relates to #131 (stable ids) as a prerequisite, not overlap.
- **(c) Auto-type provenance edges as `derived_from`.** Two flavors:
  - **(c-projection)** — synthesize `relation_type="derived_from"` at graph-build
    time in `sqlite_graph.py`. No frontmatter write, retroactive on existing
    bundles, no migration, does not touch ingest's byte-identity golden test.
  - (c-frontmatter) — write real `relations:` at ingest. Breaks the ingest
    byte-identity golden test, needs migration for old bundles. Not recommended.

## Recommendation

**(a)+(c-projection) combined**, keyed on `provenance:` frontmatter membership
(never on `related_note` text, which would mistype `query --save` citation edges).
Implement as ONE shared predicate used in `sqlite_graph.py` to synthesize
`relation_type="derived_from"` for provenance-mirror rows at projection time —
this makes them typed, so they fall through `_candidate_edges`'s existing
pair-level exclusion "for free," and (a) follows from (c) without a separate
filter. Low effort, retroactive (no migration), removes an LLM call from a
deterministic fact path, keeps provenance authoritative in one place. **(b) should
be a separate, larger follow-up change** — its candidate-source design is open and
unbounded.

Consequence to accept: on today's bundles, (a)+(c) makes `suggest-relations`
return NOTHING (everything is now typed `derived_from`). This is honest — there is
nothing to type yet — but the command stays inert until (b) provides real
concept↔concept candidates.

## Risks

- Multi-entry `provenance:` semantics undefined (no current caller passes >1 —
  confirm before implementing).
- (c-projection) flips previously-`None` `relation_type` rows to `"derived_from"`
  — check no downstream code relies on that being `None`.
- The vocabulary-warning-spam + one-LLM-call-per-edge PERF concern is a SEPARATE
  companion issue — out of scope here.

## Ready for proposal

Yes — once the maintainer picks: (a)+(c-projection) now with (b) deferred
[recommended], vs including (b) now, vs a subset.
