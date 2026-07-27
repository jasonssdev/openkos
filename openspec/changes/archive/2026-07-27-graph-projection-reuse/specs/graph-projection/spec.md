# Delta for Graph Projection

## ADDED Requirements

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
