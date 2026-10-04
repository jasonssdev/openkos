# Typed Relationships Specification

## Purpose

`typed-relationships` covers the typed-graph foundation: the `relations:`
OKF frontmatter field, the `openkos relate <source> <rel> <target>` CLI
verb that writes it deterministically (no LLM), and a seeded-but-extensible
relation-type vocabulary.

## Non-Goals

This spec does NOT define: LLM propose-then-adjudicate edge production;
full reversible frontmatter-edge rewiring through merge/unmerge (deferred
ledger extension); a user-facing relations query/graph-read CLI surface;
inverse/symmetric-relation bookkeeping; embeddings/hybrid retrieval; or any
change to the existing untyped `[text](/id.md)` link behavior — all
deferred.

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
related_to, caused_by, part_of, member_of, produced_by}`. The lifecycle
relation types `supersedes`, `revises` and `reconciled_with` (the resolution
types, which an LLM suggester MUST NOT propose) are recognized by the write
path and MUST NOT produce the unknown-type warning, but they are not part of
the seeded default set. Any non-empty, non-whitespace-only `rel` string MUST
be accepted for write. WHEN `rel` is neither in the known set nor a
lifecycle relation type, `relate` MUST emit a WARN to stderr but MUST NOT
reject. WHEN `rel` is empty or whitespace-only, `relate` MUST reject with no
write.

#### Scenario: Known type accepted silently

- GIVEN `rel` is `depends_on`
- WHEN `relate` runs
- THEN it writes the relation with no WARN emitted

#### Scenario: A lifecycle type is accepted silently

- GIVEN `rel` is `supersedes`
- WHEN `relate sources/v2 supersedes sources/v1` runs
- THEN it writes the relation and no "not a seeded relation type" note is
  emitted

#### Scenario: Unknown type accepted with WARN

- GIVEN `rel` is `inspired_by` (not in the known set)
- WHEN `relate` runs
- THEN it writes the relation AND emits a WARN to stderr naming the unknown
  type

#### Scenario: Empty or whitespace type rejected

- GIVEN `rel` is empty or `"   "`
- WHEN `relate` runs
- THEN it exits non-zero with a clear error and writes nothing

#### Scenario: A lifecycle type is still never suggested

- GIVEN the edge-type suggester's allowed vocabulary
- WHEN it is listed
- THEN it contains none of `supersedes`, `revises`, `reconciled_with`
### Requirement: Target Containment Consistent With Existing Verbs

`source` and `target` resolution MUST use the same concept-id containment
rules as other write verbs (existing on-disk concept id, no directory
traversal or path escape).

#### Scenario: Traversal-shaped id is refused

- GIVEN `target` is a path-traversal-shaped string (e.g. `../../evil`)
- WHEN `relate` runs
- THEN it refuses with a clear error and writes nothing
### Requirement: `relate` Of A `supersedes` Edge Writes The Deprecated-Status Export

WHEN `openkos relate <source> supersedes <target>` ADDS a new edge (not the
idempotent already-present case) and `source` and `target` differ, the
system MUST evaluate the deprecated-status export projection
(`deprecated-status-export`) for `target` over its post-write superseded
state, and write its outcome into `target`'s document in the SAME Phase B
as the edge, under the SAME drift guard (with `target`'s bytes added to its
baselines) and the SAME autocommit. The preview MUST name `target`'s status
change, or state that its own human-authored `status` is preserved when the
outcome is BLOCKED. No other relation type MUST cause `relate` to write any
`status` or `status_derived_from` field, and the vocabulary itself is
unchanged (`supersedes` stays an accepted, advisory-warned type).

#### Scenario: relate supersedes exports the target's status

- GIVEN concepts `a` and `b`, with `b` carrying `status: stable`
- WHEN `openkos relate a supersedes b --auto` runs
- THEN `a` gains `{target: b, type: supersedes}` and `b` carries
  `status: deprecated` and `status_derived_from: supersedes`, in the same
  commit

#### Scenario: relate of any other type writes no status

- GIVEN concepts `a` and `b`, with `b` carrying `status: stable`
- WHEN `openkos relate a references b --auto` runs
- THEN `b`'s bytes are unchanged

#### Scenario: An idempotent relate supersedes writes no status

- GIVEN `a` already holds `{target: b, type: supersedes}` and `b` carries
  `status: stable` (pre-existing drift)
- WHEN `openkos relate a supersedes b --auto` runs
- THEN `b`'s bytes are unchanged; the drift stays `repair`'s concern

### Requirement: `unrelate` CLI Verb Removes A Typed Relation

`openkos unrelate <source> <rel> <target>` MUST resolve `source` and `target`
exactly as `relate` does (both existing, distinct concept ids, fail-closed
before any read) and validate `rel` as `relate` does. It MUST remove every
`{target, type: rel}` entry from `source`'s `relations:` list, remove the
`relations:` key when no entry remains, append an `**Unrelate**` entry to
`log.md`, auto-commit, and refresh the derived stores, following the same
review-gated flow as `relate`: Phase A compute-no-write, preview, then confirm;
`--auto` and `review: false` skip the prompt; non-TTY without `--auto` refuses
to write; a document changed since Phase A refuses the whole run (exit 3).
When `source` holds no such relation, it MUST exit non-zero with a clear error
and write nothing.

When the removed edge is a `supersedes` edge, the system MUST evaluate the
deprecated-status export projection for `target` over the post-removal
superseded state and, when that withdraws the export, write it in the SAME
Phase B as the edge removal, under the same drift guard. A `target` still
superseded by another concept, or a walk that cannot read every document, MUST
leave `target`'s bytes unchanged.

#### Scenario: Successful unrelate removes the edge

- GIVEN `a` holds `{target: b, type: references}` and `{target: c, type: references}`
- WHEN `openkos unrelate a references b --auto` runs
- THEN `a` keeps only `{target: c, type: references}` and `log.md` gains an
  `**Unrelate**` entry

#### Scenario: Unrelating an absent relation refuses

- GIVEN `a` holds no `depends_on` relation to `b`
- WHEN `openkos unrelate a depends_on b --auto` runs
- THEN it exits non-zero naming the missing relation and writes nothing

#### Scenario: Non-TTY without --auto refuses

- GIVEN `review: true`, non-TTY stdin, no `--auto`
- WHEN `unrelate` runs
- THEN it refuses to write, exits non-zero, and nothing is written

#### Scenario: Unrelating supersedes withdraws the target's status

- GIVEN `a` supersedes `b` and `b` carries `status: deprecated` with the
  engine's export marker
- WHEN `openkos unrelate a supersedes b --auto` runs
- THEN `b` carries `status: stable` and no marker, in the same commit

#### Scenario: A still-superseded target keeps its status

- GIVEN `a` and `c` both supersede `b`
- WHEN `openkos unrelate a supersedes b --auto` runs
- THEN `b`'s bytes are unchanged

### Requirement: A Supersession Proposed By Import Is Written Only By `relate`

An import that proposes a Source supersedes an earlier Source (a changed
watched file, or a replacement for a Source with no extractable text,
ADR-0041) MUST NOT write the edge or the deprecated-status export itself. The
edge and the export are written by `openkos relate <new> supersedes <old>` under
the existing requirement above, and the proposal is an ordinary pending-work
`relation_type` row that this `relate` resolves.

#### Scenario: Nothing is deprecated until a person confirms

- GIVEN a watched file imported as a second version
- WHEN no `relate` has run
- THEN the earlier Source carries no deprecated status and neither Source
  carries a `supersedes` relation
