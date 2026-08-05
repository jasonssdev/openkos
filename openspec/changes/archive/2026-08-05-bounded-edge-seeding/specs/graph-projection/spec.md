# Delta for Graph Projection

## MODIFIED Requirements

### Requirement: Third Pass — Embedding-Proximity Candidate Edges

`_populate_graph_tables` MUST run a third, embedding-proximity edge
pass on every `build_graph()` call, alongside the existing bundle-link
pass and the `relations:`/provenance-mirror typing pass. Before
invoking the candidate source's `pairs(...)`, the node set handed to
it MUST exclude every document whose OKF `type` is `Source` — as
SOURCE and as TARGET: a `Source` document MUST NOT propose a candidate
edge and MUST NOT receive one. For each remaining (non-`Source`)
concept with a stored embedding in `vectors.db`, it MUST query nearest
neighbors and emit a candidate concept-to-concept edge for neighbors
above a fixed distance/similarity cutoff, up to a fixed top-K. This
exclusion applies ONLY to the third, embedding-proximity pass: the
first pass (bundle-relative markdown links) and the second pass
(`relations:` frontmatter typing and provenance-mirror `derived_from`
synthesis) MUST remain unaffected, including body links and
`relations:` entries that reference a `Source` document — the
Concept→Source `derived_from` provenance mirror MUST continue to work
exactly as before this change. This pass is projection-ephemeral: it
MUST NOT write to `relations:` frontmatter or any bundle file, and
MUST be fully recomputed on every `build_graph()` call with no
cross-run cache. Candidate edges MUST NOT alter the node/edge output
of the existing two passes, and existing
`tests/unit/graph/test_sqlite_graph.py` behavior for those two passes
MUST pass unchanged. The concrete row typing (whether a candidate edge
carries `relation_type = NULL` or a new synthesized type) is
unspecified by this requirement; either representation MUST satisfy
it.
(Previously: the node set handed to `pairs(...)` was the full
`okf._iter_docs` walk with no type filter, so a `Source` document
could both propose and receive a candidate edge; this excludes
`Source` documents from the seeding node set on both ends, leaving
passes 1 and 2 — including the Concept→Source `derived_from` mirror —
unaffected.)

#### Scenario: Nearby concepts produce a candidate edge

- GIVEN two concepts with stored embeddings whose distance is at or
  below the cutoff
- WHEN `build_graph()` runs
- THEN a candidate concept-to-concept edge between them exists in the
  projection

#### Scenario: Distant concepts produce no candidate edge

- GIVEN two concepts with stored embeddings whose distance exceeds the
  cutoff
- WHEN `build_graph()` runs
- THEN no candidate edge is produced between them

#### Scenario: Deterministic and non-destructive to existing passes

- GIVEN an unchanged bundle and unchanged `vectors.db`
- WHEN `build_graph()` runs twice
- THEN both runs yield the same candidate-edge set in the same order,
  and the node/edge output of the two existing passes is unchanged

#### Scenario: Source document does not propose a candidate edge

- GIVEN a `Source` document with a stored embedding whose nearest
  neighbor within cutoff is a `Concept` document
- WHEN `build_graph()` runs
- THEN no candidate edge originates FROM that Source document

#### Scenario: Source document does not receive a candidate edge

- GIVEN a `Concept` document with a stored embedding whose nearest
  neighbor within cutoff is a `Source` document
- WHEN `build_graph()` runs
- THEN no candidate edge targets that Source document

#### Scenario: Concept-to-Concept candidates are unaffected by Source exclusion

- GIVEN two `Concept` documents with stored embeddings within cutoff
- WHEN `build_graph()` runs
- THEN a candidate edge exists between them, unaffected by the
  Source-exclusion filter

#### Scenario: Provenance-mirror derived_from edges to a Source are unaffected

- GIVEN a concept document with `provenance: [sources/foo]` and a
  `## Related` body link `[foo](/sources/foo.md)`
- WHEN `build_graph()` runs
- THEN the edge from the concept to `sources/foo` still has
  `relation_type == "derived_from"`, produced by the first and second
  passes, unaffected by the third pass's Source exclusion

## ADDED Requirements

### Requirement: Third Pass — Bounded Candidate Output Per Run

The third pass's candidate-edge output per `build_graph()` call MUST
be bounded by a fixed ceiling, expressed as a private `Final` module
constant (proposed default: `50`; the exact value is confirmed at
design time). Candidates MUST be ranked by `ProximityPair.distance`
ascending (closest first) before truncation, with ties broken by
`(source_id, target_id)` lexicographic ordering — matching `pairs()`'s
existing `sorted(best)` determinism guarantee. Truncation to the
ceiling MUST be applied AFTER the Source-exclusion filter (see the
Third Pass requirement above) and AFTER deduplication against the
edges already produced by the first (bundle-link) and second
(`relations:`/provenance-mirror) passes: a candidate that duplicates
an edge already present from those passes MUST be dropped from the
ranked set BEFORE the ceiling is applied, never after — a dropped
duplicate MUST NOT consume a ceiling slot that an otherwise-eligible
candidate would have filled. WHEN the ranked, deduplicated,
Source-excluded candidate set exceeds the ceiling, `build_graph()`
MUST truncate it to exactly the ceiling and MUST report that
truncation occurred, including at minimum the number of candidates
produced before truncation and the number retained, through an
observable channel available to callers of
`_populate_graph_tables`/`build_graph()`. WHEN the set is at or under
the ceiling, no truncation notice MUST appear. The exact reporting
channel and its wording are left to design.

#### Scenario: Under-cap bundle produces no truncation notice

- GIVEN a bundle where the third pass, after Source exclusion and
  dedup against passes 1 and 2, produces 25 candidate edges (the
  reported bundle's post-filter volume) and the ceiling is 50
- WHEN `build_graph()` runs
- THEN all 25 candidate edges are retained and no truncation notice is
  reported

#### Scenario: Over-cap bundle truncates to the ceiling and reports it

- GIVEN a bundle of 300 objects where the third pass, after Source
  exclusion and dedup against passes 1 and 2, produces approximately
  1500 candidate edges (`TOP_K = 5` union-of-k over 300 nodes) and the
  ceiling is 50
- WHEN `build_graph()` runs
- THEN exactly 50 candidate edges are retained — the 50 with the
  smallest `distance`, tie-broken by `(source_id, target_id)` — and a
  truncation notice is reported naming the produced count and the
  retained count

#### Scenario: Deduplication runs before the ceiling, never after

- GIVEN a ranked candidate set where some of the closest-distance
  candidates duplicate an edge already produced by passes 1 or 2, and
  the ceiling is smaller than the pre-dedup candidate count
- WHEN `build_graph()` runs
- THEN the duplicate candidates are dropped before the ceiling is
  applied, and do not consume a ceiling slot that an otherwise-
  eligible, non-duplicate candidate would have filled

#### Scenario: Deterministic ranking and truncation across builds

- GIVEN a bundle and `vectors.db` unchanged between two builds, with a
  candidate set exceeding the ceiling
- WHEN `build_graph()` runs twice
- THEN both runs retain the same candidate edges, in the same order,
  ranked by ascending `distance` with `(source_id, target_id)`
  tie-break, and the truncation notice is identical across both runs
