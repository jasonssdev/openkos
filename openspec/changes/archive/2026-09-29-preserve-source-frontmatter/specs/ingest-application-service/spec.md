# Delta for Ingest Application Service

## ADDED Requirements

### Requirement: compose_source_document Accepts Parsed Incoming Frontmatter Lift Results

`compose_source_document` MUST accept optional parameters carrying the
incoming source's parsed frontmatter mapping (for `source_frontmatter`)
and its lifted tag candidate (for the Source's tag union), and forward
them to `okf.build_source_concept` unchanged. It MUST NOT parse the
incoming source's frontmatter itself, MUST NOT decide the tag lift or
union outcome itself, and MUST NOT read any on-disk tags for the union —
the caller supplies the already-parsed mapping and the already-resolved
tag list that the `ingestion` capability's parse and lift rules produced.
WHEN both parameters are absent (`None`/empty), the generated Source
concept's frontmatter MUST omit `source_frontmatter`, and its `tags` MUST
be byte-identical to `compose_source_document`'s output before these
parameters existed.

#### Scenario: Absent frontmatter parameters produce a byte-identical Source

- GIVEN a call to `compose_source_document` with no incoming frontmatter
  mapping and no lifted tags, and inputs otherwise identical to a call made
  before these parameters existed
- WHEN the two calls' output is compared
- THEN the generated Source concept document is byte-identical, and
  neither carries a `source_frontmatter` key

#### Scenario: A given frontmatter mapping and tag list reach the generated document

- GIVEN a call to `compose_source_document` with a parsed incoming
  frontmatter mapping and a resolved tag list
- WHEN the Source concept is composed
- THEN its frontmatter carries `source_frontmatter` equal to the given
  mapping and `tags` equal to the given list

#### Scenario: The service performs no parsing or lift decisions of its own

- GIVEN the ingest application service module
- WHEN its handling of the incoming-frontmatter parameters is inspected
- THEN it contains no YAML parsing, no tag-shape normalization, and no
  on-disk tag read — it only forwards the values it received
