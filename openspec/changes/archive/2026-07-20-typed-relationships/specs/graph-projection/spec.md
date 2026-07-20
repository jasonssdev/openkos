# Delta for Graph Projection

## RENAMED Requirements

### Requirement: Edge Schema Reserves `relation_type` As Nullable → Edge `relation_type` Populated From Frontmatter `relations:`

(Reason: slice 1 stops reserving `relation_type` as always-`NULL` and
populates it from the source document's `relations:` frontmatter)
(Migration: None — additive; edges with no matching `relations:` entry keep
`relation_type` `NULL`, same as before)

## MODIFIED Requirements

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
