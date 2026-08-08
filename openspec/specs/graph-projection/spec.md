# Graph Projection Specification

## Note

The existing Non-Goals section defers "persistence to `.openkos/openkos.db`".
This slice fulfills that: persistence is now in scope via the ADDED
requirement below, written only by `reindex`. The in-memory,
rebuild-per-run `build_graph(bundle_dir)` contract itself is unchanged for
any caller that does not go through `reindex`.

## Purpose

`graph/` is the first derived-layer package: a pure library that projects the
bundle's existing untyped markdown links into an in-memory SQLite node-edge
representation, exposes that projection through a `GraphStore` Protocol, and
converts it to an `nx.DiGraph` for analysis. It is a read-only derived cache
reconstructible from canonical markdown — never a mutator of bundle bytes.
It has no CLI command; its only consumers are future retrieval/lint slices.

## Non-Goals

This spec does not define: cross-source entity resolution or reversible
merge; hybrid vector retrieval; relation-type extraction/NLP (the projection
reads typed edges from `relations:` frontmatter but does not itself infer or
author relations); a CLI `graph` verb; persistence to `.openkos/openkos.db`; or
CI/import-linter layering enforcement (layering stays a followed convention).

## Requirements

### Requirement: On-Disk Persisted Graph Index Written By Reindex

The system MUST provide a persistence path that writes the node-edge
projection (nodes, edges, and `relation_type`) to on-disk SQLite storage
under `.openkos/`, invoked ONLY by `reindex`, using the SAME node/edge
extraction rules as in-memory `build_graph` (OKF concept ID node identity,
bundle-relative link edge extraction, `relations:` frontmatter typing). A
stored bundle-manifest hash MUST gate whether the persisted index is
rebuilt on a given `reindex` run.

#### Scenario: Reindex persists the graph index to disk

- GIVEN a bundle and an initialized workspace
- WHEN `openkos reindex` runs
- THEN an on-disk graph index exists under `.openkos/` containing the same
  nodes and edges `build_graph` would produce in memory over the same
  bundle

#### Scenario: Persisted index is read-only for non-reindex consumers

- GIVEN a persisted graph index already written by `reindex`
- WHEN `query`/`answer()` reads it
- THEN no write occurs to the on-disk graph index file

### Requirement: In-Memory SQLite Node-Edge Projection

The system MUST build an in-memory SQLite node-edge representation
(`sqlite3(":memory:")`, rebuild-per-run, context-managed) over every
non-reserved concept `.md` file in a bundle, enumerated via the existing
`okf._iter_docs` walk — mirroring `state/fts.py`'s build pattern. Calling
`build_graph(bundle_dir)` directly MUST NOT touch disk; disk persistence
exists ONLY via the dedicated on-disk writer path invoked by `reindex` (see
the new persisted-index requirement above).
(Previously: `build_graph` had no on-disk persistence concept at all; this
clarifies the in-memory call and the new `reindex`-only persistence path
remain distinct.)

#### Scenario: Projection builds one node per concept document

- GIVEN a bundle containing concept `.md` files
- WHEN the projection is built over that bundle
- THEN the resulting node set contains exactly one node per non-reserved
  document

#### Scenario: Projection never touches disk

- GIVEN any bundle
- WHEN the projection is built directly via `build_graph` (not via
  `reindex`'s persistence path)
- THEN no `.openkos/` directory or `openkos.db` file is created; the
  projection exists only in memory for the caller's session

### Requirement: Node Identity Is The OKF Concept ID

Each node MUST be keyed by the OKF concept ID — the document's
bundle-relative path with the `.md` suffix removed, NFC-normalized — the same
identity `fts.py` and `forget` use. Because the id is NFC regardless of the
on-disk spelling, an edge whose `relations:` target is spelled NFC MUST match
a node whose filename a normalizing filesystem stored as NFD.
(Previously: the id was the raw relative path with no normalization, so a
node derived from an NFD filename could not be matched by an NFC-spelled
`relations:` target and the edge was dropped silently.)

#### Scenario: Node id matches the concept id convention

- GIVEN a concept document at `bundle/concepts/stoicism.md`
- WHEN the projection is built
- THEN the corresponding node's id is `concepts/stoicism`

#### Scenario: Typed edge survives a decomposed target filename

- GIVEN a target document whose filename is stored NFD and a source document
  whose `relations:` entry names it spelled NFC
- WHEN the projection is built
- THEN the typed edge is projected and the node id is the NFC spelling

### Requirement: Edges Extracted From Bundle-Relative Markdown Links

Edge extraction MUST use a scoped regex over bundle-relative
`[text](/path.md)` links in each document's body — matching `okf.py`'s link
shape — and MUST only create an edge when the link target resolves to a
known node id in the same projection. Links that are external, lack a
leading `/`, lack a `.md` suffix, or resolve to no known node MUST NOT
produce an edge.

#### Scenario: Bundle-relative link produces a directed edge

- GIVEN a concept document whose body contains
  `[stoicism](/concepts/stoicism.md)` and a node `concepts/stoicism` exists
- WHEN the projection is built
- THEN a directed edge from the source document's node to
  `concepts/stoicism` exists in the projection

#### Scenario: Non-bundle-relative or dangling links are ignored

- GIVEN a document body containing an external URL link and a
  bundle-relative link to a path with no matching node
- WHEN the projection is built
- THEN neither link produces an edge, and building does not raise

### Requirement: Edge `relation_type` Populated From Frontmatter `relations:` And Provenance-Mirror Synthesis

`build_graph` MUST populate an edge's `relation_type` from the source
document's `relations:` frontmatter entry whose `target` resolves to that
edge's target node id. WHEN no matching `relations:` entry exists for an
edge, the system MUST synthesize `relation_type = "derived_from"` at
projection read time IF AND ONLY IF the edge's target node id is a MEMBER
of the source document's decoded `provenance:` frontmatter list (exact id
match; membership only — never derived from link text or heading). This
synthesis MUST NOT write to `relations:` frontmatter, MUST NOT mutate
bundle bytes, and MUST NOT change ingest byte-identity. WHEN no matching
`relations:` entry exists AND the target is not a `provenance:` member,
`relation_type` MUST remain `NULL`, unchanged from before. The existing
untyped `[text](/id.md)` `_LINK_RE` edge-extraction path MUST remain
unchanged for objects without a `relations:` key.
(Previously: absent a `relations:` match, `relation_type` always stayed
`NULL` regardless of `provenance:` frontmatter; this adds provenance-mirror
synthesis as a second, projection-only typing source.)

#### Scenario: Typed relation edge carries its relation_type

- GIVEN a document with `relations: [{target: concepts/x, type:
  depends_on}]`
- WHEN the projection is built
- THEN the edge to `concepts/x` has `relation_type == "depends_on"`

#### Scenario: Untyped-link edge with no provenance match remains NULL

- GIVEN a document with no `relations:` key whose body contains a
  bundle-relative markdown link to a target that is NOT a member of the
  document's `provenance:` frontmatter list (or `provenance:` is missing or
  empty)
- WHEN the projection is built
- THEN the resulting edge's `relation_type` is `NULL`

#### Scenario: Provenance-mirror link to a source is synthesized as derived_from

- GIVEN a concept document with `provenance: [sources/foo]` and a
  `## Related` body link `[foo](/sources/foo.md)`
- WHEN the projection is built
- THEN the edge from the concept to `sources/foo` has `relation_type ==
  "derived_from"`, and no bundle file is modified

#### Scenario: Provenance-mirror link to a cited concept is also synthesized

- GIVEN a `query --save` concept document with `provenance: [concepts/bar]`
  and a `## Related` body link `[bar](/concepts/bar.md)`
- WHEN the projection is built
- THEN the edge from the concept to `concepts/bar` has `relation_type ==
  "derived_from"`, based on `provenance:` list membership, not the
  `sources/` prefix

#### Scenario: Genuine concept-to-concept link outside provenance stays untyped

- GIVEN a concept document whose `provenance:` list does not include a link
  target that the body also links to
- WHEN the projection is built
- THEN that edge's `relation_type` remains `NULL`

#### Scenario: Multi-entry provenance list types each matching target

- GIVEN a document with `provenance: [sources/a, sources/b]` and `##
  Related` links to both `sources/a` and `sources/b`
- WHEN the projection is built
- THEN both edges have `relation_type == "derived_from"`

### Requirement: GraphStore Protocol Defines The Derived-Layer Surface

`graph/base.py` MUST define a `GraphStore` Protocol — mirroring
`llm/base.py::LLMBackend` — exposing node, edge, and neighbor (adjacency)
queries only over the projection. Path finding is NOT part of the Protocol;
it is provided by the NetworkX conversion in `analysis.py`. Any concrete
implementation MUST satisfy it structurally, with no explicit inheritance
required.

#### Scenario: A concrete store satisfies GraphStore structurally

- GIVEN a class implementing the projection's node, edge, and neighbor
  (adjacency) query methods with matching signatures, without inheriting
  `GraphStore`
- WHEN it is used where a `GraphStore` is expected
- THEN static type checking accepts it as a valid `GraphStore`, and path
  finding over the projection is obtained via `analysis.py`/nx rather than
  the Protocol

### Requirement: NetworkX Conversion Produces A Directed Graph

`graph/analysis.py` MUST convert a SQLite node-edge projection into an
`nx.DiGraph`, preserving every node and every directed edge.

#### Scenario: Conversion preserves nodes and edges

- GIVEN a built projection with nodes and edges
- WHEN it is converted via `analysis.py`
- THEN the resulting `nx.DiGraph` contains the same node ids and the same
  directed edges

#### Scenario: Empty projection converts cleanly

- GIVEN a projection built over a bundle with no markdown links
- WHEN it is converted via `analysis.py`
- THEN the resulting `nx.DiGraph` has nodes but zero edges, and conversion
  does not raise

### Requirement: Projection Is A Read-Only Derived Cache

Building the projection MUST NOT modify OKF bundle bytes, and MUST be fully
reconstructible from canonical markdown alone — rebuilding over an unchanged
bundle MUST yield an equivalent node-edge set.

#### Scenario: Building the projection writes nothing to the bundle

- GIVEN any bundle
- WHEN the projection is built
- THEN every file under the bundle is unchanged (bytes and mtime)

#### Scenario: Rebuild is deterministic over an unchanged bundle

- GIVEN a bundle that has not changed between two builds
- WHEN the projection is built twice
- THEN both builds yield the same node set and the same edge set

### Requirement: No CLI Surface, No Canonical-Layer Import

`graph/` MUST NOT introduce a CLI command or user-invocable entry point, and
MUST NOT be imported by `model`, `bundle`, or `state` (canonical layer never
depends on derived layer).

#### Scenario: No graph CLI verb exists

- GIVEN the current CLI command set
- WHEN it is enumerated
- THEN no `graph` command is present

#### Scenario: Canonical modules do not import graph

- GIVEN `src/openkos/model`, `bundle`, and `state` source
- WHEN their imports are inspected
- THEN none imports `openkos.graph`

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

### Requirement: Truncation Reporting Is Caller-Scoped

The counts a command renders for a truncation notice MUST be derived
from the candidate pairs the CALLER is entitled to see, never from the
projection's raw pre-cap totals. The projection applies only the
Source-type exclusion and the cross-pass deduplication; it applies no
sensitivity filter, while `resolution.edge_typing.candidate_edges`
removes every edge with a blocked endpoint from the list a command
prints. Rendering the raw totals beside that filtered list would
disclose an aggregate volume derived from material the same output
withholds. Both the produced count and the retained count MUST
therefore be re-derived by filtering the report's ranked pre-cap pairs
through the same `sensitivity.sensitive_concept_ids` walk, with the
same `include_confidential`/`local_exemption` arguments that command
threads into `candidate_edges`. Filtering only the truncated tail is
NOT sufficient: a blocked endpoint may sit inside the retained prefix,
so the retained prefix MUST be filtered independently. WHEN the
visible produced count does not exceed the visible retained count, no
notice MUST be rendered at all — a truncation composed entirely of
pairs the caller cannot see is invisible to that caller.

#### Scenario: Truncation invisible to the caller renders no notice

- GIVEN a bundle whose third pass truncates, where every dropped
  candidate has an endpoint the caller may not see
- WHEN a command renders its truncation notice without
  `--include-confidential`
- THEN no notice is printed, and the same command run with
  `--include-confidential` prints the full counts

#### Scenario: A blocked endpoint inside the retained prefix lowers the visible retained count

- GIVEN a truncated candidate set where one retained pair has an
  endpoint the caller may not see
- WHEN the notice is rendered for that caller
- THEN the retained count excludes that pair, so the rendered counts
  match the edge list printed beside them

### Requirement: Third Pass Degrades Cleanly Without Embeddings

WHEN `vectors.db` is absent or empty, the embedding-proximity pass MUST
yield zero candidate edges and MUST NOT raise; `build_graph()` MUST
remain a successful, non-fatal read, and the two existing passes MUST
still run and produce their normal output.

#### Scenario: Absent vectors.db yields zero candidates, no crash

- GIVEN a bundle with no `.openkos/vectors.db` file
- WHEN `build_graph()` runs
- THEN it completes successfully with zero candidate edges, and the
  bundle-link and typed-relation passes still produce their normal
  output

#### Scenario: Empty vectors.db yields zero candidates, no crash

- GIVEN a `.openkos/vectors.db` file that exists but has no stored
  embeddings
- WHEN `build_graph()` runs
- THEN it completes successfully with zero candidate edges

### Requirement: Caller-Supplied Store Reuse Within One Invocation

Derived-layer readers built on `build_graph` (`graph_edge_summary`,
`candidate_edges`, `find_contradictions`) MUST accept an optional
`store: GraphStore | None = None` keyword. WHEN a caller supplies an
already-open `GraphStore`, the reader MUST operate on that store and MUST
NOT open its own `build_graph` projection. WHEN `store` is omitted
(`None`, the default), the reader's own build and its returned values
MUST be identical to today: it opens its own `with build_graph(...)`
block, computes over that projection, and closes it before returning.
Store open/close ownership stays with whichever caller opened it — a
reader operating on a caller-supplied store MUST NOT close it. For a
given store, a reader's returned values MUST be deterministic and
identical regardless of who built that store — the reader has no
knowledge of, and MUST NOT alter its computation based on, which caller
opened it. Rebuild-per-run for `build_graph` itself is unchanged: this
requirement governs reuse of one already-open store within a single
invocation, not cross-invocation caching.

#### Scenario: Reader uses a caller-supplied store instead of opening its own

- GIVEN a caller has already opened a `GraphStore` via `build_graph`
- WHEN the caller invokes `graph_edge_summary`, `candidate_edges`, or
  `find_contradictions` with that store passed as `store=`
- THEN the reader operates on the supplied store, does not call
  `build_graph` itself, and returns the same result it would have
  produced by directly computing over that same store's projection

#### Scenario: Omitting the store keyword preserves today's behavior

- GIVEN a caller invokes `graph_edge_summary`, `candidate_edges`, or
  `find_contradictions` without passing `store`
- WHEN the reader runs
- THEN it opens and closes its own `build_graph` projection exactly as it
  did before this change, and its return value and output are unchanged

#### Scenario: Reader never closes a store it did not open

- GIVEN a caller supplies an already-open store to a reader
- WHEN the reader finishes and returns
- THEN the supplied store remains open, and only the caller that opened
  it is responsible for closing it

#### Scenario: Zero-result path shares one build per invocation

- GIVEN a bundle where a reader's candidate/pair generation yields zero
  results
- WHEN a caller opens one store, passes it to both the primary reader
  call and any zero-result-state summary call, instead of letting each
  call open its own store
- THEN `build_graph` is invoked exactly once for the whole invocation,
  and the summary reflects that single shared store's projection

### Requirement: Summary Over A Caller-Supplied Store Reflects That Store's Projection

WHEN `graph_edge_summary` is called with a caller-supplied `store` that was
built with proximity candidates included (e.g. `build_graph(bundle_dir,
candidates=...)`), its returned totals and typed/untyped counts MUST
describe that exact store's projection, including any proximity-seeded
rows the caller's build produced. This MAY differ from the counts a
candidates-free `build_graph(bundle_dir)` call would yield over the same
bundle, because the two calls build different projections. The reader
MUST NOT filter out or otherwise hide candidate-seeded rows to make its
output match a candidates-free build — the invariant is fidelity to the
supplied store, not to any particular build configuration.

#### Scenario: suggest-relations zero-result summary reflects the candidates-seeded store

- GIVEN a bundle where `candidate_edges` finds zero suggestable edges, and
  the caller built its store with `candidates=` proximity seeding enabled
- WHEN `suggest_relations_cmd`'s zero-result branch calls
  `graph_edge_summary` with that same seeded store
- THEN the reported `total` and untyped counts include the
  proximity-seeded (`relation_type = NULL`) rows, and MAY be higher than
  the counts a candidates-free `build_graph(bundle_dir)` call would report
  over the identical bundle

#### Scenario: contradictions' typed-count path is unaffected by proximity seeding

- GIVEN a bundle whose graph includes proximity-seeded rows carrying
  `relation_type = NULL`, alongside genuinely typed edges
- WHEN `contradictions`' zero-result branch calls `graph_edge_summary`
  with a caller-supplied store built with the same proximity seeding
- THEN the reported typed-edge count is unaffected by the presence of the
  untyped proximity-seeded rows, since `contradictions` reads only the
  typed count and proximity rows are never typed
