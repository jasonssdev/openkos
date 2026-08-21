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

### Requirement: The Stacked Form Keeps One Document Root

The APPEND stacks the absorbed body under a
`## Merged content (<absorbed-id>)` delimiter. The absorbed body carries
its own `# ` title heading, so appending it verbatim produced a merged
document with TWO level-1 headings — two document roots in one file
(issue #803).

The absorbed body's LEADING `# ` heading MUST therefore be demoted to
`### ` before it is stacked. The delimiter directly above already names
the absorbed document, so that heading is both the redundant one and the
one creating the second root; `### ` nests it under the level-2 delimiter.
The demotion MUST fail closed, exactly as the reconciled-body heading pin
does: only an exact leading `# ` ATX heading is rewritten, and a body that
opens with prose is stacked unchanged — this demotes a heading that
exists, it never invents or relocates one.

The absorbed document's DEEPER sections MUST be left verbatim. Folding
them requires section-merging semantics this engine does not provide
(deduping `## Related` bullets, renumbering `[N]` citation markers), and
shifting them byte-wise would silently change meaning; `# Citations` is an
OKF §8 RESERVED heading and MUST NOT be demoted blind. Reconciliation is
what folds two documents into one; this requirement only stops the
unreconciled fallback from asserting two roots.

The demotion is PRESENTATION-ONLY and MUST NOT affect reversibility.
`unmerge` restores from the ledger's verbatim pre-merge snapshots, and
every other consumer of a merged body locates the absorbed segment by the
`## Merged content (` marker rather than by the absorbed body's bytes.

#### Scenario: The absorbed leading heading is demoted
- GIVEN an absorbed body opening with a `# ` heading
- WHEN the merged body is built
- THEN that heading is stacked as `### ` and the merged body carries
  exactly one level-1 heading, the survivor's

#### Scenario: Deeper absorbed sections are stacked verbatim
- GIVEN an absorbed body carrying a `## Related` section and a
  `# Citations` section below its title heading
- WHEN the merged body is built
- THEN both are present unchanged, at their original heading levels

#### Scenario: A heading-less absorbed body is stacked unchanged
- GIVEN an absorbed body that opens with prose
- WHEN the merged body is built
- THEN the absorbed text is stacked byte-identically, with no heading
  invented

#### Scenario: A demoted stack still unmerges to byte parity
- GIVEN a merge whose absorbed body carried a demoted leading heading
- WHEN `unmerge` reverses it
- THEN both documents are restored byte-for-byte from the ledger snapshots

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

`type_alternative` is EXCLUDED from the generic fill-the-gap branch: the
absorbed side's value MUST NEVER be imported into the merged document
(issue #803). It records ONE extraction's uncertainty about ONE document's
classification, not a property of the entity, so importing it manufactures
doubt no extraction ever expressed about the survivor — the reported case
came out of a merge newly flagged as possibly an `Organization`. The
exclusion also removes a latent hazard: the concept builder REFUSES
`type_alternative == type`, while the merge path has no such check, so
inheritance could leave a survivor carrying `type: X` plus
`type_alternative: X`, a state the builder will not produce. A survivor
carrying its OWN `type_alternative` MUST keep it; the absorbed document's
value MUST be restored to it unchanged by `unmerge`.

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

#### Scenario: The absorbed `type_alternative` does not cross the merge

- GIVEN a survivor with no `type_alternative` and an absorbed object
  declaring one
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document carries no `type_alternative`, and `unmerge`
  restores the absorbed document's own value unchanged

#### Scenario: The survivor keeps its own `type_alternative`

- GIVEN both sides declaring a DIFFERENT `type_alternative`
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document carries the survivor's value

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

Every merge MUST append an entry to a per-survivor sidecar file under
`bundle/.state/ledger/`, written and read only via `okf.dump_frontmatter`/
`load_frontmatter`. The survivor's own concept frontmatter MUST NOT gain a
`merged_from` key or any other ledger content. Each entry holds, per
absorbed object: `absorbed_snapshot`, `survivor_before`, `link_rewrites`,
`relation_rewrites` (v2), `provenance_rewrites` (v3),
`carried_content_ids` (v4), `index_restores` (v5), and
`sensitivity_before`/`sensitivity_after`. `unmerge` MUST restore EVERY
touched file — survivor, absorbed, and every file in `relation_rewrites`
and `provenance_rewrites` — byte-exact. The ledger schema is
`MERGE_LEDGER_SCHEMA_V5`.

An entry MUST NOT store a whole-file copy of `index.md` or `log.md`. A
sidecar's size MUST track the size of the MERGE, not the size of the
bundle: `index_restores` records only the catalog bullets that merge
removed, each with the preceding line and that line's occurrence index as
its positional anchor, and `log.md` gets no stored field at all because a
merge's only effect on it is one bullet derivable from the two ids and
`merged_at`. (Measured before the change: one merge's sidecar cost 1838
characters in a 10-document bundle and 21798 in a 200-document one, because
each entry photographed the whole catalog and log; on a real 33-document
workspace those two fields were 79.6% of the sidecar's bytes after a single
merge, and each successive merge photographed a larger catalog than the
one before it.)

Backward compatibility is by SHAPE, not by migration. An entry with no
`provenance_rewrites` key (v1 or v2) MUST still decode and unmerge exactly
as before; one with no `relation_rewrites` key (v1) MUST likewise still
decode; and a v1–v4 entry, which carries `index_before`/`log_before` and no
`index_restores`, MUST keep its whole-file catalog restore and its drift
warning unchanged. The reader MUST accept v1, v2, v3, v4, and v5 entries
regardless of storage location, and MUST NOT rewrite an older entry into
the newer shape — an entry already on disk records no delta, so converting
it would mean inventing reversal information nobody stored.
(Previously: entries were embedded directly in the survivor's own
`merged_from` frontmatter key, growing that file geometrically across
merges; then relocated to a sidecar under `bundle/.state/ledger/`, which
fixed the document corruption but not what the entry stored.)

#### Scenario: No `merged_from` key remains in survivor frontmatter
- GIVEN a merge that appends a new ledger entry
- WHEN the survivor's own concept frontmatter is inspected afterward
- THEN it contains no `merged_from` key, and the new entry instead exists
  under `bundle/.state/ledger/`

#### Scenario: Ledger sidecar embeds the snapshot set plus relation rewrites
- GIVEN a merge that rewrote one inbound link and retargeted one
  third-party relation
- WHEN the survivor's ledger sidecar is inspected
- THEN its entry has `absorbed_snapshot`, `survivor_before`,
  `index_restores`, `link_rewrites`, `relation_rewrites` (with that file's
  snapshot), and `sensitivity_before`/`sensitivity_after`, and NO
  `index_before` or `log_before` key

#### Scenario: A sidecar does not grow with the bundle
- GIVEN the same pair of concepts merged in a small bundle and in a much
  larger one
- WHEN each survivor's ledger sidecar is measured
- THEN the two sidecars are the same size, and neither contains any
  concept the merge did not touch

#### Scenario: Catalog work done after the merge survives the unmerge
- GIVEN a merge, followed by an `ingest` that adds a bullet to `index.md`
  and a line to `log.md`
- WHEN `unmerge <survivor> <absorbed>` is confirmed
- THEN the absorbed concept's bullet is back, this merge's `**Merge**` log
  line is gone, the bullet and line added in between are still there, and
  no discard warning is printed

#### Scenario: A pre-v5 snapshot entry keeps its old behavior
- GIVEN a ledger entry written before this change, carrying
  `index_before`/`log_before`
- WHEN `unmerge` reverses it after `index.md` changed since the merge
- THEN `index.md` is restored wholesale from the snapshot and the drift
  warning is printed, exactly as before

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

### Requirement: `merge` Refuses On A Doctor-Flagged Ledger, With `--force`

`openkos merge` MUST run the doctor merge-ledger-integrity check against
the survivor's sidecar before Phase A completes, and MUST refuse (exit
non-zero, write nothing) when that check reports the ledger as
post-merge-mutated, UNLESS `--force` is passed. The refusal message MUST
ALWAYS name the repair verb (for a clean, unmigrated ledger) and MUST
ALWAYS state that reversibility of merges made before this fix is not
guaranteed. The reset-and-replay remedy is conditional on the workspace
actually having one, exactly as the doctor check's own remediation is:
when the workspace is a git repository with a reachable reset point, the
message MUST name `git reset --hard <first-merge>~1` followed by `openkos
reindex` (for a corrupted ledger); when it is not — no repository, no
configured git identity, or no commit history — it MUST say so explicitly
and MUST NOT claim reset-and-replay is available. `--force` MUST be
orthogonal to the confirm-gate precedence, mirroring `forget`'s existing
refuse-plus-`--force` shape.

#### Scenario: Merge onto a flagged ledger refuses by default
- GIVEN the survivor's ledger sidecar is flagged by the doctor
  merge-ledger-integrity check, in a git repository with a reachable reset
  point
- WHEN `openkos merge <survivor> <absorbed>` runs without `--force`
- THEN it refuses in Phase A, exits non-zero, writes nothing, and prints
  both the repair-verb and reset-and-replay remediation paths plus the
  non-guaranteed-reversibility statement

#### Scenario: The refusal names no reset-and-replay path without a reset point
- GIVEN the same flagged ledger in a workspace with no reachable git reset
  point (no repository, no configured git identity, or no commit history)
- WHEN `openkos merge <survivor> <absorbed>` runs without `--force`
- THEN it still refuses, still names the repair verb, and still states
  reversibility is not guaranteed, but reports that no git reset point is
  available rather than naming the `git reset --hard`+`openkos reindex`
  path

#### Scenario: `--force` bypasses the refusal, not the confirm gate
- GIVEN the same flagged ledger
- WHEN `openkos merge <survivor> <absorbed> --force` runs on an interactive
  TTY without `--auto`
- THEN the ledger-integrity refusal is bypassed but the existing
  confirm-gate precedence still governs the write

### Requirement: Repair Verb Refuses On Any Sign Of Cross-Survivor Pollution Risk (Slice 1b)

The migration/repair verb (extracting a pre-fix, frontmatter-embedded
`merged_from` history into a `bundle/.state/ledger/` sidecar) MUST refuse
the WHOLE run (exit non-zero, write nothing) whenever ANY survivor in the
bundle — migrated or unmigrated — carries 2 or more merge-ledger entries,
rather than running the doctor merge-ledger-integrity check (Check B,
nested-prefix equality) per concept and refusing only the flagged ones.
This bundle-wide, entry-count gate is deliberately COARSER than Check B:
Check B has two honest false negatives it cannot see past — a
single-entry ledger has nothing nested to compare, and cross-survivor
pollution is invisible at any index, because `merge_core`'s `other_files`
scan touches every non-reserved bundle file, so a merge of X into Y can
rewrite bytes inside a THIRD survivor Z's embedded snapshot without Z's
own ledger ever showing a nested-prefix mismatch. A per-concept, Check-B-
only gate would let exactly that corruption through; the bundle-wide
≥2-entries gate does not, at the cost of also refusing some concepts Check
B alone would have cleared. A mechanical verbatim migration of a
corrupted, already-mutated ledger would convert a git-revertible bug into
permanent durable fact, so this refusal has no override flag of any kind.
The refusal message MUST state that the only path forward is `git reset
--hard <first-merge>~1` followed by `openkos reindex`, and that
reversibility of merges made before this fix is not guaranteed.

#### Scenario: Repair verb migrates a clean ledger verbatim
- GIVEN a bundle where no survivor (migrated or unmigrated) carries 2 or
  more merge-ledger entries, and a concept has an embedded `merged_from`
  history
- WHEN the repair verb runs
- THEN that concept's entries are extracted verbatim into a new
  `bundle/.state/ledger/` sidecar and the frontmatter `merged_from` key is
  removed

#### Scenario: Repair verb refuses the whole run, with no override
- GIVEN any survivor in the bundle — migrated or unmigrated — carries 2 or
  more merge-ledger entries
- WHEN the repair verb runs
- THEN it refuses the ENTIRE run, exits non-zero, writes nothing for any
  concept, and states the reset-and-replay path is the only remedy — no
  `--force` or equivalent flag bypasses this refusal, even for concepts
  whose own ledger Check B alone would have cleared

### Requirement: Unmerge Achieves Round-Trip Parity

`unmerge <survivor-id> <absorbed-id>` reverses ONLY the LIFO-tail entry in
the survivor's ledger sidecar; a non-tail `absorbed-id` refuses cleanly
with no write. It MUST restore the survivor from `survivor_before`, the
absorbed object from `absorbed_snapshot`, REVERSE every recorded link,
relation, and provenance rewrite, remove the entry from the sidecar, and
reverse this merge's own `index.md`/`log.md` edit then append an audit
line.

That catalog reversal MUST be SURGICAL for a v5 entry: put back exactly the
bullets in `index_restores`, remove exactly this merge's `**Merge**` log
line, and leave every other byte of both files alone, so catalog and log
work that landed between the merge and the unmerge SURVIVES. It MUST fail
closed rather than approximate — a recorded anchor that no longer occurs as
often as recorded, or a log bullet occurring more than once where the
reversal cannot tell which is its own, refuses with nothing written — and
it MUST be idempotent, so a run that died midway is safe to re-run. Where
several identical `**Merge**` bullets coexist the TOPMOST is reversed:
`log.md` is newest-first by construction and `unmerge` only ever reverses
the LIFO tail, so the two orderings agree. Byte-parity on an
otherwise-untouched bundle is unchanged and still required.

Because a v5 reversal discards nothing, it MUST NOT print the
catalog/log discard warning; a v1–v4 entry still restores wholesale and
still warns. For a file touched
by more than one rewrite kind, precedence is `provenance > relations >
links`: a `provenance_rewrites` snapshot restores exclusively (skipping
relation/link reversal); failing that, a `relation_rewrites` snapshot skips
link reversal; a file in neither reverses via link rewrites. Given the full
snapshot set, `merge` then `unmerge` MUST leave every bundle file —
including the survivor and the absorbed file — BYTE FOR BYTE identical to
their pre-merge state, and the sidecar entry MUST be gone. Unmerge does NOT
restore third-party derived objects' `sensitivity` — merge never wrote it
(propagation is `set-sensitivity`'s exclusive concern) and lowering is a
separate gated one-way operation (ADR-0008, ADR-0010); an explicit
non-requirement, not an oversight.
(Previously: reversed the entry embedded in the survivor's own
frontmatter; the parity and precedence contract is unchanged, only the
entry's storage location and removal target moved to the sidecar.)

Unwind ergonomics (#562): a non-tail `absorbed-id` that IS recorded deeper
in the ledger MUST refuse with the full LIFO unwind sequence — every id
from the tail down to and including the request, in execution order — and
name `--to` as the one-command alternative; an id recorded nowhere keeps a
plain not-merged refusal. `unmerge <survivor-id> --to <absorbed-id>`
unwinds the ledger tail-first, one complete single-step unmerge per entry
(Phase A recomputed from current disk state each step, every fail-closed
drift/collision check included, per-step audit line and sidecar pop
included), down to AND INCLUDING the entry that absorbed the target,
behind ONE whole-plan preview and ONE confirm gate (same precedence as the
two-arg form); the positional `absorbed-id` and `--to` are mutually
exclusive, and supplying both or neither refuses cleanly. A mid-chain
failure stops immediately, reports the failed step and that earlier steps
completed, and never rolls completed steps back — each intermediate state
is a consistent bundle. A `survivor-id` whose concept file does not exist
but which some OTHER survivor's ledger records absorbing MUST be refused
with an error naming that absorber and the exact unmerge command to run
first (an absorbed ex-survivor's own sidecar survives its absorption).

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
- THEN it exits non-zero with a clean error and writes nothing; when the
  absorbed-id is recorded deeper in the ledger, the error lists the full
  LIFO unwind sequence in execution order and names `--to` as the
  one-command alternative

#### Scenario: Unmerge of a non-merged pair
- GIVEN no sidecar entry for that absorbed-id
- WHEN `unmerge` runs
- THEN it exits non-zero and writes nothing

#### Scenario: A missing survivor names its absorber
- GIVEN a chained merge — `mid` absorbed `leaf`, then `top` absorbed `mid`
- WHEN `unmerge mid leaf` runs
- THEN it exits non-zero, writes nothing, and the "does not exist" error
  names `top` as the absorber plus the exact `openkos unmerge top mid`
  command to run first

#### Scenario: --to unwinds the ledger to the target behind one confirm gate
- GIVEN a survivor whose sidecar records multiple merges
- WHEN `unmerge <survivor> --to <buried-absorbed-id>` is confirmed once
- THEN every entry from the tail down to and including the target is
  reversed as its own complete single-step unmerge, in LIFO order, with
  the full per-step plan previewed before the single gate and no per-step
  prompt; `--to` naming the tail itself behaves exactly like the two-arg
  form

#### Scenario: --to with an unknown target refuses
- GIVEN a survivor whose ledger records no entry for the target id, or no
  ledger at all
- WHEN `unmerge <survivor> --to <target>` runs
- THEN it exits non-zero and writes nothing

#### Scenario: A mid-chain --to failure stops without rolling back
- GIVEN a `--to` unwind whose step N fails Phase A or Phase B
- WHEN the failure occurs
- THEN the chain stops immediately with exit non-zero, the report names
  the failed step and that steps 1..N-1 completed, and completed steps are
  NOT rolled back — each intermediate state is a consistent,
  git-recoverable bundle

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

### Requirement: Merged-Body Reconciliation Reaches Every Consenting Caller

The #645 merged-body reconciliation pass — one model call that rewrites a
merge's stacked body as a single coherent document — MUST be planned and
applied by EVERY caller that consents to a merge, not only the `merge`
command. The consenting callers are `merge`, `curate`'s Identity stage,
`adjudicate --apply`, and `adjudicate --apply-same`; the latter three drive
`_prepare_one_merge`/`_commit_one_merge` directly.

The decision MUST come from a single shared predicate that both the plan
DISCLOSURE and the APPLICATION read, so a caller can neither promise a pass
it does not run nor run one it did not disclose. The disclosure MUST ride
the shared merge preview line, on the same reasoning the stacked-share
guardrail warning does: a caller shows it without having to remember to.
Every consenting caller MUST expose the opt-out, and it MUST be the same
`--no-reconcile` lever rather than a second differently-named one:
`merge --no-reconcile`, `curate --no-reconcile`, and `adjudicate
--no-reconcile` (covering both `--apply` and `--apply-same`). Its default
MUST be reconciliation ON, per #645's opt-out ruling. A caller that plans
the pass without offering the opt-out would send both note bodies to the
model with no way for the operator to refuse.

Every consenting caller MUST likewise expose the OPT-IN, `--reconcile`,
under the same one-lever rule: `merge --reconcile`, `curate --reconcile`,
and `adjudicate --reconcile`. It MUST force the pass even when neither
threshold is met, because the thresholds are a heuristic tuned for the
unattended default while the flag is a human acting on a preview that has
just said "bodies were appended, not reconciled". Without it that
disclosure names a problem the operator has no way to act on: re-running
the merge produces the same stacked result (issue #803).

`--reconcile` and `--no-reconcile` together MUST be REFUSED, not silently
resolved, before any workspace gate or read. Either precedence rule would
carry out half of what the operator asked for and discard the other half
with no signal. The predicate MUST still read the opt-out first, so a
caller reaching it with both set makes no model call.

The absolute floor MUST measure the MERGED body, not the absorbed
contribution. Read against the absorbed side it is a stricter, unstated
second rule: a merged document below `floor / share-threshold` chars can
never clear it, however large the absorbed share, so short documents were
stacked no matter how much of the result the absorbed half was — the
reported case being two `Person` merges at 39% and 40% share that missed
by 11 and 19 characters (issue #803). Re-anchoring is monotone
(`merged_chars >= absorbed_chars` always), so it admits merges without
withdrawing any, and it preserves the floor's stated intent: two one-line
bodies stacking at a high share while carrying nothing worth a model call
still fall below the floor and are still skipped.

The application MUST run AFTER that caller's consent and BEFORE its drift
re-check, so the slow model call sits inside the window the drift guard
re-validates. Any failure MUST keep the stacked body and notice on stderr:
the merge itself never gains a new failure mode from an improvement pass.

This is the defect issue #688 reports: the planning lived in the `merge`
command body while `curate` — the path the product recommends and `next`
points at — stacked bodies at shares of 38%, 46% and 54%, above the very
thresholds at which the standalone verb offered the pass at 27%.

#### Scenario: Curate's Identity stage plans and applies the reconciliation
- GIVEN an Identity pair whose prepared merge clears the reconciliation
  thresholds
- WHEN the per-item preview is printed and the merge is accepted
- THEN the preview discloses the reconciliation before the consent prompt,
  and the accepted merge runs the pass

#### Scenario: The opt-out suppresses both halves
- GIVEN the same pair and `--no-reconcile`
- WHEN the preview is printed and the merge is accepted
- THEN the preview does not disclose the pass and no reconciliation call is
  made

#### Scenario: A short document above the share is reconciled
- GIVEN a pair whose absorbed body is a large share of the merged body but
  contributes fewer chars than the absolute floor, and whose merged body is
  at or above that floor
- WHEN the merge is planned
- THEN the reconciliation pass is disclosed and applied

#### Scenario: Two one-line bodies are still skipped
- GIVEN a pair whose merged body falls below the absolute floor, at any
  stacked share
- WHEN the merge is planned
- THEN the reconciliation pass is neither disclosed nor applied

#### Scenario: The opt-in forces the pass below both thresholds
- GIVEN a pair that clears NEITHER the share threshold nor the absolute
  floor, and `--reconcile`
- WHEN the preview is printed and the merge is accepted
- THEN the preview discloses the reconciliation and the accepted merge runs
  the pass

#### Scenario: The opt-in and the opt-out together are refused
- GIVEN `--reconcile` and `--no-reconcile` on the same invocation of any
  consenting caller
- WHEN the command runs
- THEN it refuses on stderr, before any workspace gate or read, with no
  write and no model call

### Requirement: The Reconciled Body's Leading Heading Names The Survivor

Both input notes carry their own `# ` heading, so a reconciled body may
open with either. The merged frontmatter `title:` is always the survivor's
(the survivor-wins scalar rule), and the two MUST NOT be allowed to
disagree: the frontmatter title is what `index.md`, `status`, `list` and
citations render, while the heading is what a human — or an OKF consumer
with no OpenKOS awareness — reads as the document's name.

The reconciled body's LEADING `# ` heading MUST therefore be pinned to the
survivor's title deterministically, after every refusal gate so the length
floor still scores the model's own reply. The pin MUST NOT invent a
heading where the body opens with prose, and MUST NOT rewrite a later `# `
heading, which is the model's own sectioning rather than the document's
name. Pinning is deterministic rather than prompt-asked because the
survivor's title is a fact already in hand; asking the model to echo it
would trade that fact for a probability (issue #695).

#### Scenario: A reconciled body headed with the absorbed title is corrected
- GIVEN a reconciled reply whose first line is the ABSORBED document's
  heading
- WHEN the reply passes every refusal gate
- THEN the returned body's leading heading is the survivor's title, the
  absorbed title does not appear as that heading, and the body's remaining
  content is unchanged

#### Scenario: A heading-less reconciled body keeps its shape
- GIVEN a reconciled reply that opens with prose
- WHEN the reply passes every refusal gate
- THEN the returned body is byte-identical to the reply

### Requirement: The Cross-Source Warning Reaches Every Merge Surface

Every surface that offers to merge two concepts MUST name the cross-source
class before the operator consents: members that each carry a non-empty
`provenance:` whose sets are DISJOINT (issue #796, extending #776).

That includes plain `merge`, not only the adjudication walks. `merge` is
the command `duplicates` and `adjudicate` both name in their closing hints,
so guarding only the batch and walk paths leaves the most-travelled door
open.

The wording MUST come from the one shared constant the other surfaces use,
so the four cannot drift apart. Absence of provenance MUST NOT be treated
as a risk signal: a hand-written concept gives no evidence either way, and
flagging on absence would mark every hand-authored concept forever.

This requirement governs DISCLOSURE only. It does not change any verdict,
and it does not block the merge.

#### Scenario: A cross-source merge is named before the gate

- GIVEN two concepts whose provenance sets are disjoint
- WHEN `openkos merge` runs
- THEN the cross-source note is printed after the change plan and before
  the confirmation gate

#### Scenario: A shared-source merge is not marked

- GIVEN two concepts that share a provenance entry
- WHEN `openkos merge` runs
- THEN no cross-source note is printed

#### Scenario: A hand-written concept is not marked

- GIVEN two concepts with no `provenance:` at all
- WHEN `openkos merge` runs
- THEN no cross-source note is printed
