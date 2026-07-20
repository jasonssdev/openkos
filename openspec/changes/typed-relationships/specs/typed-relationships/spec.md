# Typed Relationships Specification

## Purpose

`typed-relationships` is slice 1 of the typed-graph work: the `relations:`
OKF frontmatter field, the `openkos relate <source> <rel> <target>` CLI
verb that writes it deterministically (no LLM), and a seeded-but-extensible
relation-type vocabulary.

## Non-Goals

This spec does NOT define: LLM propose-then-adjudicate edge production;
full reversible frontmatter-edge rewiring through merge/unmerge (deferred
ledger extension); a user-facing relations query/graph-read CLI surface;
inverse/symmetric-relation bookkeeping; embeddings/hybrid retrieval; or any
change to the existing untyped `[text](/id.md)` link behavior — all
deferred to later slices.

## Requirements

### Requirement: `relations:` Frontmatter Field Shape

`relations:` MUST be an optional frontmatter key holding a list of typed
edges. Each entry MUST be a mapping with `target` (an existing concept
id/slug, single-line string) and `type` (a non-empty, single-line string).
`target` and `type` MUST NOT contain `\n` or `\r`, mirroring the existing
index/log newline-injection guards. An absent `relations:` key or an empty
list is valid and means no relations.

#### Scenario: Well-formed relations entry parses

- GIVEN an object with `relations: [{target: concepts/x, type: references}]`
- WHEN its frontmatter is parsed
- THEN one relation entry with that `target` and `type` is returned

#### Scenario: Newline in target or type is rejected

- GIVEN a `target` or `type` value containing `\n` or `\r`
- WHEN the value is written via `relate`
- THEN it is rejected before any write, preventing a forged frontmatter/YAML
  structure

#### Scenario: Absent relations key is valid

- GIVEN an object with no `relations:` key
- WHEN it is parsed
- THEN it is treated as having zero relations, no error raised

### Requirement: `relate` CLI Verb Writes A Typed Relation

`openkos relate <source> <rel> <target>` MUST validate that both `source`
and `target` are existing concept ids before any write (fail-closed); on
success it MUST append `{target, type: rel}` to `source`'s `relations:`
list, catalog and log the change, and follow the same review-gated flow as
other write verbs: Phase A compute-no-write, preview, then confirm; `--auto`
and `review: false` skip the prompt; non-TTY without `--auto` refuses to
write.

#### Scenario: Successful relate writes into source frontmatter

- GIVEN existing concept ids `a` and `b`
- WHEN `openkos relate a references b` is confirmed (or run with `--auto`)
- THEN `a`'s frontmatter gains `{target: b, type: references}` under
  `relations:`, and `index.md`/`log.md` reflect the change

#### Scenario: Missing target fails closed

- GIVEN `target` has no corresponding concept id
- WHEN `openkos relate <source> <rel> <target>` runs
- THEN it exits non-zero with a clear error and writes nothing

#### Scenario: Missing source fails closed

- GIVEN `source` has no corresponding concept id
- WHEN `openkos relate <source> <rel> <target>` runs
- THEN it exits non-zero with a clear error and writes nothing

#### Scenario: Non-TTY without --auto refuses

- GIVEN `review: true`, non-TTY stdin, no `--auto`
- WHEN `relate` runs
- THEN it refuses to write, exits non-zero, and nothing is written

### Requirement: Seeded-But-Extensible Relation Vocabulary

The known-default vocabulary is `{references, depends_on, derived_from,
related_to, caused_by, part_of, member_of, produced_by}`. Any non-empty,
non-whitespace-only `rel` string MUST be accepted for write. WHEN `rel` is
not in the known set, `relate` MUST emit a WARN to stderr but MUST NOT
reject. WHEN `rel` is empty or whitespace-only, `relate` MUST reject with no
write.

#### Scenario: Known type accepted silently

- GIVEN `rel` is `depends_on`
- WHEN `relate` runs
- THEN it writes the relation with no WARN emitted

#### Scenario: Unknown type accepted with WARN

- GIVEN `rel` is `inspired_by` (not in the known set)
- WHEN `relate` runs
- THEN it writes the relation AND emits a WARN to stderr naming the unknown
  type

#### Scenario: Empty or whitespace type rejected

- GIVEN `rel` is empty or `"   "`
- WHEN `relate` runs
- THEN it exits non-zero with a clear error and writes nothing

### Requirement: Target Containment Consistent With Existing Verbs

`source` and `target` resolution MUST use the same concept-id containment
rules as other write verbs (existing on-disk concept id, no directory
traversal or path escape).

#### Scenario: Traversal-shaped id is refused

- GIVEN `target` is a path-traversal-shaped string (e.g. `../../evil`)
- WHEN `relate` runs
- THEN it refuses with a clear error and writes nothing
