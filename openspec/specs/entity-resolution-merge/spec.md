# Entity-Resolution Merge Specification

## Purpose

`entity-resolution-merge` is the first DESTRUCTIVE entity-resolution
capability: a confirm-gated, fully REVERSIBLE 2-way `merge` of two
concept-ids a human has confirmed are the same entity, plus a first-class
`unmerge` with round-trip parity.

## Non-Goals

Re-opening `entity-resolution`/`entity-resolution-adjudication`; embeddings;
automatic no-confirm merge; N-way single-shot merge (>2-member HIGH groups
need sequential pairwise merges); batch/`--from-adjudicate` mode; changes
to `forget`.

## Requirements

### Requirement: Merge Fuses Two Distinct Concept-IDs

`merge <survivor-id> <absorbed-id>` MUST take two explicit, distinct,
existing concept-ids. Survivor's id survives; absorbed file is removed;
survivor's body gains absorbed content by APPEND (never overwrite);
provenance is UNIONed; `index.md`/`log.md` are updated. Same-id or unknown
ids MUST be rejected with no write.

#### Scenario: Successful merge
- GIVEN two existing, distinct concept-ids
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN absorbed file is gone, survivor body has the appended content,
  provenance is unioned, `index.md`/`log.md` reflect it

#### Scenario: Same-id or unknown id rejected
- GIVEN `survivor-id == absorbed-id`, or one id has no file
- WHEN `merge` runs
- THEN it exits non-zero and writes nothing

### Requirement: Frontmatter-Conflict Resolution

| Field kind | Rule |
|---|---|
| Scalar | Survivor's value wins |
| List | Union, deduped, order-preserving |
| Freshness/`as of` | Most recent of the two |

Sensitivity is excluded (see next requirement). All conflicts MUST appear
in the Phase A preview. The `type` scalar follows the same survivor-wins
scalar rule as any other scalar field, including when survivor and
absorbed declare DIFFERENT OKF types (a cross-type merge): the merged
document's `type` MUST be the survivor's declared type, and the absorbed
object's `type` MUST be discarded without being surfaced as a "conflict"
requiring resolution — this is explicit, tested behavior, not an
incidental side effect of generic scalar-merge logic.
(Previously: the scalar-wins rule was stated generically; `type`'s
behavior on a cross-type merge was an implicit consequence never named or
pinned by a dedicated test.)

#### Scenario: Conflicting fields resolved and surfaced
- GIVEN differing scalar and list-field values on both sides
- WHEN `merge` runs
- THEN the merged scalar is the survivor's, the list is the union, and
  both conflicts were shown in the preview

#### Scenario: Survivor's type wins on a cross-type merge

- GIVEN a survivor declared `type: Concept` and an absorbed object declared
  `type: Entity`
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document's `type` is `Concept`, and the absorbed object's
  `Entity` type is discarded

### Requirement: Sensitivity High-Water-Mark Recomputation

Sensitivity MUST be RECOMPUTED via `combine_sensitivity`, never copied,
ordering `public < private < confidential`. Missing → `private`.
Unrecognized/malformed → fail-closed to `confidential`.

#### Scenario: Confidential + public → confidential
- GIVEN sensitivities `public` and `confidential`
- WHEN recomputed
- THEN the result is `confidential`

#### Scenario: Missing defaults private; malformed fails closed
- GIVEN one side missing sensitivity and the other malformed (e.g.
  `"unknown"`)
- WHEN recomputed
- THEN the missing side is treated as `private` and the result is
  `confidential`

### Requirement: Reversibility Ledger (`merged_from`)

The survivor MUST gain a `merged_from` key holding, per absorbed object:
`absorbed_snapshot`, `survivor_before`, `index_before`/`log_before`,
`link_rewrites`, `relation_rewrites` (v2, whole-file snapshots for
third-party `relations:` retargets/drops/dedupes), `provenance_rewrites`
(v3, NEW — whole-file snapshots for every third-party file whose
`provenance:` was retargeted or deduped), and
`sensitivity_before`/`sensitivity_after`.

`unmerge` MUST restore EVERY touched file — survivor, absorbed, and every
file in `relation_rewrites` and `provenance_rewrites` — byte-exact. The
ledger schema is `MERGE_LEDGER_SCHEMA_V3`. An entry with no
`provenance_rewrites` key (v1 or v2) MUST still decode and unmerge exactly
as before, and one with no `relation_rewrites` key (v1) MUST likewise still
decode; the reader MUST accept v1, v2, and v3 entries.
(Previously: v2 schema, no `provenance_rewrites` field — a provenance
retarget would have had no recorded snapshot to reverse from.)

#### Scenario: Ledger embeds the full snapshot set plus relation rewrites
- GIVEN a merge that rewrote one inbound link and retargeted one
  third-party relation
- WHEN survivor frontmatter is inspected
- THEN `merged_from` has `absorbed_snapshot`, `survivor_before`,
  `index_before`, `log_before`, `link_rewrites`, `relation_rewrites`
  (with that file's snapshot), and `sensitivity_before`/`sensitivity_after`

#### Scenario: Unmerge restores every touched file, including drops/dedupes
- GIVEN a merge that dropped a self-loop and deduped a collision on a
  third-party file
- WHEN `unmerge <survivor> <absorbed>` is confirmed
- THEN survivor, absorbed, and that third-party file are all restored
  byte-exact, with the drop and dedupe re-materialized

#### Scenario: LIFO unmerge across overlapping third-party files
- GIVEN two sequential merges that both retargeted relations on the same
  third-party file
- WHEN each merge is unmerged in reverse (LIFO) order
- THEN the file is restored to its exact byte state at each step

#### Scenario: Pre-slice-2a v1 ledger entry still unmerges exactly
- GIVEN a `merged_from` entry with no `relation_rewrites` key (v1)
- WHEN `unmerge` runs against it
- THEN it decodes successfully and restores survivor/absorbed/catalog
  exactly as before slice 2a

#### Scenario: A v1 and a v2 ledger entry are still readable after the v3 bump
- GIVEN one entry with neither `relation_rewrites` nor
  `provenance_rewrites` (v1), and one with `relation_rewrites` but no
  `provenance_rewrites` (v2)
- WHEN a v3-aware reader decodes each
- THEN both decode; missing fields default to empty on each

### Requirement: Inbound-Link Rewrite

`merge` MUST rewrite bundle-relative links to the absorbed object
(`[text](/absorbed-id.md)`, anchor preserved) to point at the survivor,
recording each rewrite. Links inside fenced code blocks (fence-masking,
e.g. `_mask_fenced_code_blocks`) MUST NOT be rewritten.

#### Scenario: Link rewritten, anchor preserved
- GIVEN `[x](/absorbed-id.md#section)` elsewhere
- WHEN `merge` runs
- THEN it becomes `[x](/survivor-id.md#section)` and is recorded

#### Scenario: Fenced-code link untouched
- GIVEN `(/absorbed-id.md)` only inside a fenced code block
- WHEN `merge` runs
- THEN it is unchanged and not recorded

### Requirement: Confirm-Gated Two-Phase Execution

Phase A computes all changes without writing and previews the recomputed
sensitivity outcome and every link to rewrite. Gate precedence mirrors
`forget`: `--auto` > `review: false` > TTY prompt > non-TTY refusal.
Declining leaves the bundle unchanged. Phase B updates catalog/log before
removing the absorbed file.

#### Scenario: Decline leaves bundle unchanged
- GIVEN a TTY prompt is declined
- WHEN `merge` runs
- THEN no file, `index.md`, or `log.md` is modified

#### Scenario: Non-TTY without --auto refuses
- GIVEN `review: true`, non-TTY stdin, no `--auto`
- WHEN `merge` runs
- THEN it refuses to write and exits non-zero

### Requirement: Unmerge Achieves Round-Trip Parity

`unmerge <survivor-id> <absorbed-id>` reverses ONLY the LIFO-tail
`merged_from` entry; a non-tail `absorbed-id` refuses cleanly with no
write. It MUST restore the survivor from `survivor_before`, the absorbed
object from `absorbed_snapshot`, REVERSE every recorded link, relation, and
provenance rewrite, remove the entry, and restore `index.md`/`log.md` then
append an audit line. For a file touched by more than one rewrite kind,
precedence is `provenance > relations > links`: a `provenance_rewrites`
snapshot restores exclusively (skipping relation/link reversal); failing
that, a `relation_rewrites` snapshot skips link reversal; a file in neither
reverses via link rewrites. Given the full snapshot set, `merge` then
`unmerge` leaves every bundle file byte-identical to before. Unmerge does
NOT restore third-party derived objects' `sensitivity` — merge never wrote
it (propagation is `set-sensitivity`'s exclusive concern) and lowering is a
separate gated one-way operation (ADR-0008, ADR-0010); an explicit
non-requirement, not an oversight.
(Previously: reversed only link and relation rewrites, relation taking
precedence over link on a shared file; no provenance rewrite to reverse.)

#### Scenario: Merge then unmerge restores the pre-merge bundle
- GIVEN a merge including a rewritten inbound link
- WHEN `unmerge <survivor> <absorbed>` is confirmed
- THEN the survivor's pre-merge frontmatter/body is restored from
  `survivor_before`, the absorbed file from `absorbed_snapshot`, every
  rewritten link is reversed, and `index.md`/`log.md` are restored from
  their snapshots (then a single unmerge audit line is appended to `log.md`)

#### Scenario: Unmerge restores the pre-merge provenance exactly
- GIVEN a merge that retargeted a third-party object's provenance
- WHEN `unmerge <survivor> <absorbed>` is confirmed
- THEN that object's `provenance` is restored to its exact pre-merge value

#### Scenario: A file touched by all three rewrite kinds reverses correctly under precedence
- GIVEN one third-party file with a link rewrite, a relation retarget, AND
  a provenance retarget from the same merge
- WHEN `unmerge` runs
- THEN the file is restored exclusively from its `provenance_rewrites`
  snapshot, byte-identical to its pre-merge state

#### Scenario: Absorbed-id is not the LIFO tail
- GIVEN a survivor whose latest `merged_from` entry absorbed a different id
- WHEN `unmerge <survivor> <absorbed>` names a non-tail absorbed-id
- THEN it exits non-zero with a clean error and writes nothing

#### Scenario: Unmerge of a non-merged pair
- GIVEN no `merged_from` entry for that absorbed-id
- WHEN `unmerge` runs
- THEN it exits non-zero and writes nothing

### Requirement: Reversible Typed-Relation Rewiring

`merge` MUST succeed regardless of typed relations on the absorbed object —
never refuse or block on outbound `relations:` or inbound targeting. Phase
A MUST: (a) OUTBOUND move — union absorbed's `relations:` onto the
survivor's; (b) INBOUND retarget — rewrite every other bundle file's
`relations:` targeting the absorbed id to target the survivor id; (c)
SELF-LOOP drop — drop any resulting survivor→survivor edge; (d) COLLISION
dedupe — collapse a retarget duplicating an edge a third-party file already
holds to one entry. Drops and dedupes MUST appear in the confirm preview
(ADR-0004) before any write; a silent relation change is a violation.

#### Scenario: Merge of an edge-bearing object always succeeds
- GIVEN the absorbed object bears outbound `relations:` or is an inbound
  relation target
- WHEN `merge <survivor> <absorbed>` runs
- THEN it proceeds and writes; it never refuses or blocks on relations

#### Scenario: Outbound relations move to the survivor
- GIVEN the absorbed object has an outbound `relations:` entry
- WHEN `merge` runs
- THEN the entry is unioned onto the survivor's `relations:`

#### Scenario: Third-party inbound relations retarget to the survivor
- GIVEN another bundle file's `relations:` entry targets the absorbed id
- WHEN `merge` runs
- THEN that entry is rewritten to target the survivor id

#### Scenario: Resulting self-loop is dropped, non-silently
- GIVEN a rewrite would produce a survivor→survivor edge
- WHEN `merge` runs
- THEN the edge is dropped and the drop appears in the confirm preview

#### Scenario: Duplicate edge is deduped, non-silently
- GIVEN a retarget would duplicate an edge a third-party file already holds
- WHEN `merge` runs
- THEN one edge entry remains and the dedupe appears in the preview

### Requirement: Reversible Inbound-Provenance Rewiring

`merge` MUST retarget every third-party `provenance:` entry naming the
absorbed id to the survivor id, as a THIRD rewrite pass (after link and
`relation:` rewriting) over the SAME pre-merge `other_files` snapshot
already built for those scanners — no additional bundle walk. This scan
MUST NOT be gated on the absorbed concept's `type`: `query --save` can file
any cited concept id, Source or not, as another object's `provenance`, so a
non-Source absorbed concept can orphan third-party provenance too.
Retargeting is retarget-then-dedupe, first-occurrence-wins: a list already
naming BOTH survivor and absorbed collapses to one `survivor_id` entry at
the EARLIER of the two positions; every other entry keeps its relative
order (mirrors `apply_relation_rewrites`).

#### Scenario: Merge absorbing a Source retargets a derived object's provenance to the survivor
- GIVEN a derived object whose `provenance` names the absorbed Source id
- WHEN `merge <survivor> <absorbed>` runs
- THEN that entry is rewritten to the survivor id

#### Scenario: A merge absorbing a NON-Source concept also retargets third-party provenance
- GIVEN a non-Source concept absorbed into a survivor, and a third-party
  object whose `provenance` (filed via `query --save`) names that id
- WHEN `merge <survivor> <absorbed>` runs
- THEN that entry is rewritten to the survivor id regardless of type

#### Scenario: A third-party file naming both ids collapses to one entry at the earlier position
- GIVEN a third-party file's `provenance` names both survivor and absorbed,
  in either order, alongside other entries
- WHEN `merge` runs
- THEN the list has one `survivor_id` entry at whichever position was
  earlier, and every other entry keeps its relative order

#### Scenario: Merge performs no additional bundle walk when the provenance pass is added
- GIVEN a merge that runs the link and relation scanners over one `rglob`
  snapshot
- WHEN `merge` runs with provenance rewriting enabled
- THEN the provenance scanner reads that SAME snapshot; no new walk occurs

### Requirement: Retargeted Provenance Reaches Later Sensitivity Propagation

After `merge` retargets a third-party object's `provenance` to the
survivor, a later `set-sensitivity` RAISE on the survivor MUST resolve that
object as a provenance descendant and propagate to it (existing
`sensitivity-config` raise-only propagation). This is the functional defect
#230 fixes: pre-retarget, the object was unreachable and silently skipped.

#### Scenario: A raise on the survivor reaches a provenance-retargeted descendant
- GIVEN a merge retargeted a third-party object's `provenance` from
  absorbed to survivor
- WHEN `set-sensitivity <survivor> <higher-level>` runs and is confirmed
- THEN that object is resolved as a descendant, raised via
  `combine_sensitivity`, and appears in the preview/success message
