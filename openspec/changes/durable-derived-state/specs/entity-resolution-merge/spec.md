# Delta for Entity-Resolution Merge

Slice 1a unless noted.

## ADDED Requirements

### Requirement: `merge` Refuses On A Doctor-Flagged Ledger, With `--force`

`openkos merge` MUST run the doctor merge-ledger-integrity check (Slice 1b)
against the survivor's sidecar before Phase A completes, and MUST refuse
(exit non-zero, write nothing) when that check reports the ledger as
post-merge-mutated, UNLESS `--force` is passed. The refusal message MUST
print BOTH remediation paths — the repair verb (for a clean, unmigrated
ledger) and `git reset --hard <first-merge>~1` followed by `openkos
reindex` (for a corrupted ledger) — and MUST state that reversibility of
merges made before this fix is not guaranteed. `--force` MUST be orthogonal
to the confirm-gate precedence, mirroring `forget`'s existing
refuse-plus-`--force` shape.

#### Scenario: Merge onto a flagged ledger refuses by default
- GIVEN the survivor's ledger sidecar is flagged by the doctor
  merge-ledger-integrity check
- WHEN `openkos merge <survivor> <absorbed>` runs without `--force`
- THEN it refuses in Phase A, exits non-zero, writes nothing, and prints
  both the repair-verb and reset-and-replay remediation paths plus the
  non-guaranteed-reversibility statement

#### Scenario: `--force` bypasses the refusal, not the confirm gate
- GIVEN the same flagged ledger
- WHEN `openkos merge <survivor> <absorbed> --force` runs on an interactive
  TTY without `--auto`
- THEN the ledger-integrity refusal is bypassed but the existing
  confirm-gate precedence still governs the write

### Requirement: Repair Verb Refuses To Migrate A Flagged Ledger (Slice 1b)

The migration/repair verb (extracting a pre-fix, frontmatter-embedded
`merged_from` history into a `bundle/.state/ledger/` sidecar) MUST run the
SAME doctor merge-ledger-integrity check against a concept's embedded
history before migrating it, and MUST refuse (exit non-zero, write nothing
for that concept) when the check flags it as post-merge-mutated. A
mechanical verbatim migration of a corrupted, already-mutated ledger would
convert a git-revertible bug into permanent durable fact, so this refusal
has no override flag. The refusal message MUST state that the only path
for a flagged ledger is `git reset --hard <first-merge>~1` followed by
`openkos reindex`, and that reversibility of merges made before this fix
is not guaranteed for that concept.

#### Scenario: Repair verb migrates a clean ledger verbatim
- GIVEN a concept whose embedded `merged_from` history passes the
  merge-ledger-integrity check
- WHEN the repair verb runs against it
- THEN its entries are extracted verbatim into a new
  `bundle/.state/ledger/` sidecar and the frontmatter `merged_from` key is
  removed

#### Scenario: Repair verb refuses a flagged ledger, with no override
- GIVEN a concept whose embedded `merged_from` history is flagged by the
  merge-ledger-integrity check as post-merge-mutated
- WHEN the repair verb runs against it
- THEN it refuses for that concept, exits non-zero, writes nothing for it,
  and states the reset-and-replay path is the only remedy — no `--force`
  or equivalent flag bypasses this refusal

## MODIFIED Requirements

### Requirement: Reversibility Ledger (`merged_from`)

Every merge MUST append an entry to a per-survivor sidecar file under
`bundle/.state/ledger/`, written and read only via `okf.dump_frontmatter`/
`load_frontmatter`. The survivor's own concept frontmatter MUST NOT gain a
`merged_from` key or any other ledger content. Each entry holds, per
absorbed object: `absorbed_snapshot`, `survivor_before`,
`index_before`/`log_before`, `link_rewrites`, `relation_rewrites` (v2),
`provenance_rewrites` (v3), and `sensitivity_before`/`sensitivity_after`.
`unmerge` MUST restore EVERY touched file — survivor, absorbed, and every
file in `relation_rewrites` and `provenance_rewrites` — byte-exact. The
ledger schema is `MERGE_LEDGER_SCHEMA_V3`. An entry with no
`provenance_rewrites` key (v1 or v2) MUST still decode and unmerge exactly
as before, and one with no `relation_rewrites` key (v1) MUST likewise still
decode; the reader MUST accept v1, v2, and v3 entries regardless of storage
location.
(Previously: entries were embedded directly in the survivor's own
`merged_from` frontmatter key, growing that file geometrically across
merges; the schema and round-trip contract are unchanged, only the
storage location moved to a sidecar under `bundle/.state/ledger/`.)

#### Scenario: No `merged_from` key remains in survivor frontmatter
- GIVEN a merge that appends a new ledger entry
- WHEN the survivor's own concept frontmatter is inspected afterward
- THEN it contains no `merged_from` key, and the new entry instead exists
  under `bundle/.state/ledger/`

#### Scenario: Ledger sidecar embeds the full snapshot set plus relation rewrites
- GIVEN a merge that rewrote one inbound link and retargeted one
  third-party relation
- WHEN the survivor's ledger sidecar is inspected
- THEN its entry has `absorbed_snapshot`, `survivor_before`,
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
- GIVEN a sidecar entry with no `relation_rewrites` key (v1)
- WHEN `unmerge` runs against it
- THEN it decodes successfully and restores survivor/absorbed/catalog
  exactly as before slice 2a

#### Scenario: A v1 and a v2 ledger entry are still readable after the v3 bump
- GIVEN one entry with neither `relation_rewrites` nor
  `provenance_rewrites` (v1), and one with `relation_rewrites` but no
  `provenance_rewrites` (v2)
- WHEN a v3-aware reader decodes each from the sidecar
- THEN both decode; missing fields default to empty on each

### Requirement: Unmerge Achieves Round-Trip Parity

`unmerge <survivor-id> <absorbed-id>` reverses ONLY the LIFO-tail entry in
the survivor's ledger sidecar; a non-tail `absorbed-id` refuses cleanly
with no write. It MUST restore the survivor from `survivor_before`, the
absorbed object from `absorbed_snapshot`, REVERSE every recorded link,
relation, and provenance rewrite, remove the entry from the sidecar, and
restore `index.md`/`log.md` then append an audit line. For a file touched
by more than one rewrite kind, precedence is `provenance > relations >
links`. Given the full snapshot set, `merge` then `unmerge` MUST leave
every bundle file — including the survivor and the absorbed file — BYTE
FOR BYTE identical to their pre-merge state, and the sidecar entry MUST be
gone. Unmerge does NOT restore third-party derived objects' `sensitivity`.
(Previously: reversed the entry embedded in the survivor's own
frontmatter; the parity and precedence contract is unchanged, only the
entry's storage location and removal target moved to the sidecar.)

#### Scenario: Merge then unmerge restores the pre-merge bundle byte-for-byte
- GIVEN a merge including a rewritten inbound link
- WHEN `unmerge <survivor> <absorbed>` is confirmed
- THEN the survivor's pre-merge frontmatter/body is restored from
  `survivor_before` byte-for-byte, the absorbed file from
  `absorbed_snapshot` byte-for-byte, every rewritten link is reversed,
  `index.md`/`log.md` are restored from their snapshots, and the sidecar
  entry is removed

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
- GIVEN a survivor whose latest sidecar entry absorbed a different id
- WHEN `unmerge <survivor> <absorbed>` names a non-tail absorbed-id
- THEN it exits non-zero with a clean error and writes nothing

#### Scenario: Unmerge of a non-merged pair
- GIVEN no sidecar entry for that absorbed-id
- WHEN `unmerge` runs
- THEN it exits non-zero and writes nothing
