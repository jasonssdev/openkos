# Graph Projection Specification

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

### Requirement: In-Memory SQLite Node-Edge Projection

The system MUST build an in-memory SQLite node-edge representation
(`sqlite3(":memory:")`, rebuild-per-run, context-managed) over every
non-reserved concept `.md` file in a bundle, enumerated via the existing
`okf._iter_docs` walk — mirroring `state/fts.py`'s build pattern.

#### Scenario: Projection builds one node per concept document

- GIVEN a bundle containing concept `.md` files
- WHEN the projection is built over that bundle
- THEN the resulting node set contains exactly one node per non-reserved
  document

#### Scenario: Projection never touches disk

- GIVEN any bundle
- WHEN the projection is built
- THEN no `.openkos/` directory or `openkos.db` file is created; the
  projection exists only in memory for the caller's session

### Requirement: Node Identity Is The OKF Concept ID

Each node MUST be keyed by the OKF concept ID — the document's
bundle-relative path with the `.md` suffix removed — the same identity
`fts.py` and `forget` use.

#### Scenario: Node id matches the concept id convention

- GIVEN a concept document at `bundle/concepts/stoicism.md`
- WHEN the projection is built
- THEN the corresponding node's id is `concepts/stoicism`

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

### Requirement: Edge `relation_type` Populated From Frontmatter `relations:`

`build_graph` MUST populate an edge's `relation_type` from the source
document's `relations:` frontmatter entry whose `target` resolves to that
edge's target node id. WHEN no matching `relations:` entry exists for an
edge, `relation_type` MUST remain `NULL`, unchanged from before. The
existing untyped `[text](/id.md)` `_LINK_RE` edge-extraction path MUST
remain unchanged for objects without a `relations:` key.

#### Scenario: Typed relation edge carries its relation_type

- GIVEN a document with `relations: [{target: concepts/x, type:
  depends_on}]`
- WHEN the projection is built
- THEN the edge to `concepts/x` has `relation_type == "depends_on"`

#### Scenario: Untyped-link edge remains NULL relation_type

- GIVEN a document with no `relations:` key whose body contains a
  bundle-relative markdown link
- WHEN the projection is built
- THEN the resulting edge's `relation_type` is `NULL`, matching prior
  behavior

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
