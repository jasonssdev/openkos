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
in the Phase A preview.

#### Scenario: Conflicting fields resolved and surfaced
- GIVEN differing scalar and list-field values on both sides
- WHEN `merge` runs
- THEN the merged scalar is the survivor's, the list is the union, and
  both conflicts were shown in the preview

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

The survivor MUST gain a `merged_from` frontmatter key (an ordinary OKF
data key, not a new file type). Round-trip parity is logically impossible
from the absorbed snapshot alone — union/high-water-mark/freshness
recomputation and typed-relation rewiring are all lossy — so each entry
MUST hold, per absorbed object, ALL of:
- `absorbed_snapshot` — verbatim pre-merge absorbed frontmatter+body;
- `survivor_before` — survivor's full verbatim bytes immediately prior to
  THIS merge, retaining prior `merged_from` entries (it excludes ONLY the
  new entry being created by this merge, which does not yet exist at
  snapshot time; it does NOT strip the whole `merged_from` key);
- `index_before` / `log_before` — verbatim pre-merge catalog state;
- `link_rewrites` — the body-link `{file, old_link, new_link}` rewrites;
- `relation_rewrites` (v2, NEW) — for every third-party file whose
  `relations:` were retargeted, dropped as a self-loop, or deduped, a
  whole-file verbatim pre-merge snapshot, sufficient to reverse it
  exactly;
- `sensitivity_before` / `sensitivity_after`.

`unmerge` MUST restore EVERY touched file — survivor, absorbed, and every
file in `relation_rewrites` — byte-exact, re-materializing any dropped
self-loop or deduped edge. Sequential merges touching OVERLAPPING
third-party files MUST unmerge LIFO to each exact historical state. An
entry with no `relation_rewrites` key (v1, pre-slice-2a) MUST still decode
and unmerge exactly as before; the reader MUST accept both v1 and v2.
(Previously: v1 schema, no relation-rewrite field; relations were
guarded/refused, never rewired.)

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

`unmerge <survivor-id> <absorbed-id>` reverses ONLY the most-recent
unreversed `merged_from` entry (the LIFO tail); the supplied `absorbed-id`
MUST equal that tail entry's `absorbed_id`, else the command refuses with a
clean error and no write (reversing a non-tail entry is unsafe due to
nested snapshots / overlapping rewrites). It MUST restore the survivor's
pre-merge frontmatter/body from `survivor_before`, restore the absorbed
object from `absorbed_snapshot`, REVERSE every recorded link rewrite,
remove that `merged_from` entry, and restore `index.md`/`log.md` from
`index_before`/`log_before` then append an unmerge audit line to `log.md`.
Given this full snapshot set, `merge` then `unmerge` of the same pair
leaves every bundle file byte-identical to before; the append-only `log.md`
audit trail net-grows by the merge+unmerge record. Limitation: `unmerge`
restores `index.md`/`log.md` to their exact pre-merge snapshot, not a merge
of that snapshot with the current on-disk state; if `index.md`/`log.md`
changed since the merge (another `ingest`/`forget`/unrelated `merge` ran in
between), `unmerge`'s preview warns of the discard before the confirm
gate but does not refuse -- round-trip parity assumes a prompt unmerge.

#### Scenario: Merge then unmerge restores the pre-merge bundle
- GIVEN a merge including a rewritten inbound link
- WHEN `unmerge <survivor> <absorbed>` is confirmed
- THEN the survivor's pre-merge frontmatter/body is restored from
  `survivor_before`, the absorbed file from `absorbed_snapshot`, every
  rewritten link is reversed, and `index.md`/`log.md` are restored from
  their snapshots (then a single unmerge audit line is appended to `log.md`)

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
