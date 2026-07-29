# Delta for entity-resolution-merge

## ADDED Requirements

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

## MODIFIED Requirements

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

#### Scenario: A v1 and a v2 ledger entry are still readable after the v3 bump
- GIVEN one entry with neither `relation_rewrites` nor
  `provenance_rewrites` (v1), and one with `relation_rewrites` but no
  `provenance_rewrites` (v2)
- WHEN a v3-aware reader decodes each
- THEN both decode; missing fields default to empty on each

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
