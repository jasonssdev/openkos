# Delta for Entity-Resolution Merge

## Non-Goals (this delta)

LLM edge production/adjudication (slice 2b, deferred); `relate`
self-id refusal, graph-projection, and body-link changes; relation-
type vocabulary validation.

## REMOVED Requirements

### Requirement: Non-Silent Guard For Edge-Bearing Merge

(Reason: the refuse-or-warn stopgap blocked legitimate edge-bearing
merges; slice 2a rewires edges instead of refusing.)
(Migration: replaced by "Reversible Typed-Relation Rewiring" below.)

## ADDED Requirements

### Requirement: Reversible Typed-Relation Rewiring

`merge` MUST succeed regardless of typed relations on the absorbed
object — never refuse or block on outbound `relations:` or inbound
targeting. Phase A MUST: (a) OUTBOUND move — union absorbed's
`relations:` onto the survivor's; (b) INBOUND retarget — rewrite every
other bundle file's `relations:` targeting the absorbed id to target
the survivor id; (c) SELF-LOOP drop — drop any resulting
survivor→survivor edge; (d) COLLISION dedupe — collapse a retarget
duplicating an edge a third-party file already holds to one entry.
Drops and dedupes MUST appear in the confirm preview (ADR-0004) before
any write; a silent relation change is a violation.

#### Scenario: Merge of an edge-bearing object always succeeds
- GIVEN the absorbed object bears outbound `relations:` or is an
  inbound relation target
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

## MODIFIED Requirements

### Requirement: Reversibility Ledger (`merged_from`)

The survivor MUST gain a `merged_from` frontmatter key (an ordinary
OKF data key, not a new file type). Round-trip parity is logically
impossible from the absorbed snapshot alone — union/high-water-mark/
freshness recomputation and typed-relation rewiring are all lossy —
so each entry MUST hold, per absorbed object, ALL of:
- `absorbed_snapshot` — verbatim pre-merge absorbed frontmatter+body;
- `survivor_before` — survivor's full verbatim bytes immediately prior
  to THIS merge, retaining prior `merged_from` entries;
- `index_before` / `log_before` — verbatim pre-merge catalog state;
- `link_rewrites` — the body-link `{file, old_link, new_link}` rewrites;
- `relation_rewrites` (v2, NEW) — for every third-party file whose
  `relations:` were retargeted, dropped as a self-loop, or deduped, a
  whole-file verbatim pre-merge snapshot, sufficient to reverse it
  exactly;
- `sensitivity_before` / `sensitivity_after`.

`unmerge` MUST restore EVERY touched file — survivor, absorbed, and
every file in `relation_rewrites` — byte-exact, re-materializing any
dropped self-loop or deduped edge. Sequential merges touching
OVERLAPPING third-party files MUST unmerge LIFO to each exact
historical state. An entry with no `relation_rewrites` key (v1,
pre-slice-2a) MUST still decode and unmerge exactly as before; the
reader MUST accept both v1 and v2.
(Previously: v1 schema, no relation-rewrite field; relations were
guarded/refused, never rewired.)

#### Scenario: Ledger embeds the full snapshot set plus relation rewrites
- GIVEN a merge that rewrote one inbound link and retargeted one
  third-party relation
- WHEN survivor frontmatter is inspected
- THEN `merged_from` has `absorbed_snapshot`, `survivor_before`,
  `index_before`, `log_before`, `link_rewrites`, `relation_rewrites`
  (with that file's snapshot), and `sensitivity_before`/`_after`

#### Scenario: Unmerge restores every touched file, including drops/dedupes
- GIVEN a merge that dropped a self-loop and deduped a collision on a
  third-party file
- WHEN `unmerge <survivor> <absorbed>` is confirmed
- THEN survivor, absorbed, and that third-party file are all restored
  byte-exact, with the drop and dedupe re-materialized

#### Scenario: LIFO unmerge across overlapping third-party files
- GIVEN two sequential merges that both retargeted relations on the
  same third-party file
- WHEN each merge is unmerged in reverse (LIFO) order
- THEN the file is restored to its exact byte state at each step

#### Scenario: Pre-slice-2a v1 ledger entry still unmerges exactly
- GIVEN a `merged_from` entry with no `relation_rewrites` key (v1)
- WHEN `unmerge` runs against it
- THEN it decodes successfully and restores survivor/absorbed/catalog
  exactly as before slice 2a
