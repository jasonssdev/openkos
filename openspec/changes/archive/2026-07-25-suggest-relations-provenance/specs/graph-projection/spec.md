# Delta for Graph Projection

## MODIFIED Requirements

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
