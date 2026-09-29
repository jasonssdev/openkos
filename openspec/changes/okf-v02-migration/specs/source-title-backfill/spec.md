# Delta for Source Title Backfill

## MODIFIED Requirements

### Requirement: Exactly Two Byte-Level Edits Per Staged Source

For each staged Source, the command MUST change exactly two things in the
document: the frontmatter `title:` value, and the document body's literal
first line (from `# {current_title}` to `# {new_title}`), written as a
surgical patch to the original text rather than a full frontmatter
re-dump (which could re-sort keys or reshape a value's on-disk type). The
`description` field, the `## Source content` section, a legacy
`# Citations` section when one is present, every other frontmatter key —
including `generated` and `sources` on a v0.2-shaped Source — and every
other line of the body MUST remain byte-identical to before the run. A
v0.2-shaped Source carries no `# Citations` section at all, and this
requirement MUST NOT be read as expecting one to exist.
(Previously: named `# Citations` and "all other frontmatter keys" without
qualifying either for OKF v0.2 shape; a v0.2-shaped Source no longer has a
`# Citations` section, and the "other frontmatter keys" preserved now
include `generated`/`sources`.)

#### Scenario: Only title and first line change

- GIVEN a Source staged for a title change with a well-formed body
- WHEN the write completes
- THEN a byte-level diff of the document shows changes only to the
  frontmatter `title:` value and the first body line; `description`,
  `## Source content`, a legacy `# Citations` section when present, and all
  other frontmatter keys — including `generated`/`sources` — are unchanged,
  including their original key order and quoting/flow style

#### Scenario: An unrewritable `title:` scalar is skipped, not overwritten

- GIVEN a Source's frontmatter `title:` is a block scalar, anchor, alias, or spans multiple lines
- WHEN `openkos backfill-source-titles` reaches that Source
- THEN the write is refused, refusal is reported, and frontmatter/body stay byte-identical to before the run
