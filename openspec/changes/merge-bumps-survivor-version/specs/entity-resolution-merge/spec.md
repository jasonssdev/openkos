# Delta for Entity-Resolution Merge

## MODIFIED Requirements

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

`type_alternative` is EXCLUDED from the generic fill-the-gap branch: the
absorbed side's value MUST NEVER be imported into the merged document.
It records ONE extraction's uncertainty about ONE document's
classification, not a property of the entity, so importing it manufactures
doubt no extraction ever expressed about the survivor — the reported case
came out of a merge newly flagged as possibly an `Organization`. The
exclusion also removes a latent hazard: the concept builder REFUSES
`type_alternative == type`, while the merge path has no such check, so
inheritance could leave a survivor carrying `type: X` plus
`type_alternative: X`, a state the builder will not produce. A survivor
carrying its OWN `type_alternative` MUST keep it; the absorbed document's
value MUST be restored to it unchanged by `unmerge`.

`event_date` is likewise EXCLUDED from the generic fill-the-gap branch:
the absorbed side's value MUST NEVER be imported onto a survivor that
lacks its own. It records evidence about WHEN a single
Source's event happened, not a property that generalizes to a merged
entity, so importing it would stamp a date onto a survivor whose own
content carries no such evidence. A survivor carrying its OWN `event_date`
MUST keep it, unaffected by the absorbed side's value, whatever that value
is. A survivor with none MUST remain without one after the merge;
`unmerge` MUST restore the absorbed document's own `event_date`, if it had
one, unchanged.

`source_frontmatter` is likewise EXCLUDED from the generic fill-the-gap
branch: the absorbed side's value MUST NEVER be imported onto a survivor
that lacks its own. It records the verbatim incoming frontmatter of ONE
raw file `ingest` parsed, not a property that generalizes to a merged
entity, so filling a survivor's gap with it would misattribute one file's
metadata as belonging to the merged object under the survivor's own
`resource`. A survivor carrying its OWN `source_frontmatter` MUST keep it,
unaffected by the absorbed side's value, whatever that value is. A
survivor with none MUST remain without one after the merge; `unmerge`
MUST restore the absorbed document's own `source_frontmatter`, if it had
one, unchanged.

`status_derived_from` (the deprecated-status export marker,
`deprecated-status-export`) is EXCLUDED from the generic fill-the-gap
branch, and so is the absorbed side's `status` whenever it carries a valid
marker: an export describes the ABSORBED concept's supersession, which the
merge's relation rewiring changes, so it never crosses to the survivor.
After relations are rewired, the merged survivor's `status` and marker MUST
be decided by the export projection over the survivor's post-merge
superseded state — the survivor is the only concept whose superseded-ness a
merge can change (an inbound retarget can newly supersede it; a dropped
self-loop can un-supersede it) — and any resulting status change MUST
appear in the Phase A preview. A human-authored absorbed `status` (no valid
marker) still follows the generic scalar rule, unchanged. `unmerge`
restores the absorbed document's own `status` and marker unchanged.

`version` is NOT a generic scalar. A merge revises the survivor, so the
merged document's `version` MUST be the SURVIVOR's own previous `version`
plus one, whatever the absorbed side's value is; a missing or non-integer
(boolean included) survivor value counts as 1, so the result is 2. This is
the same counter rule an ingest attach uses, so a concept that is merged and
later attached to keeps counting from the merged value. `unmerge` restores
the survivor from the ledger's verbatim `survivor_before`, which brings the
previous `version` back byte for byte.

#### Scenario: A merge increments the survivor's version

- GIVEN a survivor with `version: 5` and an absorbed concept with `version: 9`
- WHEN `openkos merge` completes
- THEN the merged survivor carries `version: 6`

#### Scenario: A survivor with no version counts as 1

- GIVEN a survivor with no `version` key
- WHEN `openkos merge` completes
- THEN the merged survivor carries `version: 2`

#### Scenario: Two merges increment twice

- GIVEN a survivor with `version: 1` that absorbs two concepts in turn
- WHEN both merges complete
- THEN the survivor carries `version: 3`

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

#### Scenario: The absorbed event_date does not cross the merge

- GIVEN a survivor with no `event_date` and an absorbed object declaring
  `event_date: 2026-07-14`
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document carries no `event_date`, and `unmerge` restores
  the absorbed document's `2026-07-14` value unchanged

#### Scenario: The survivor keeps its own event_date

- GIVEN a survivor declaring `event_date: 2026-07-14` and an absorbed
  object declaring a DIFFERENT `event_date: 2026-08-01`
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document's `event_date` is `2026-07-14`, the survivor's
  own value

#### Scenario: The absorbed source_frontmatter does not cross the merge

- GIVEN a survivor Source with no `source_frontmatter` and an absorbed
  Source declaring `source_frontmatter: {tags: [alpha]}`
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document carries no `source_frontmatter` key, and
  `unmerge` restores the absorbed document's `source_frontmatter` value
  unchanged

#### Scenario: The survivor keeps its own source_frontmatter

- GIVEN a survivor Source declaring `source_frontmatter: {tags: [alpha]}`
  and an absorbed Source declaring a DIFFERENT
  `source_frontmatter: {tags: [beta]}`
- WHEN `merge <survivor> <absorbed>` is confirmed
- THEN the merged document's `source_frontmatter` is the survivor's own
  `{tags: [alpha]}`, unaffected by the absorbed value

#### Scenario: An absorbed export does not cross the merge

- GIVEN a survivor with no `status` key, and an absorbed object carrying
  `status: deprecated` and `status_derived_from: supersedes`
- WHEN `merge <survivor> <absorbed>` is confirmed and no `supersedes` edge
  targets the survivor afterwards
- THEN the merged document carries neither `status: deprecated` nor
  `status_derived_from`

#### Scenario: A survivor newly superseded by the merge is exported

- GIVEN a third-party concept holds a `supersedes` edge to the absorbed
  object, and the survivor carries `status: stable`
- WHEN `merge <survivor> <absorbed>` is confirmed and that edge is
  retargeted to the survivor
- THEN the merged survivor carries `status: deprecated` and
  `status_derived_from: supersedes`, and the preview named that change

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

Unwind ergonomics: a non-tail `absorbed-id` that IS recorded deeper
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
exclusive, and supplying both or neither refuses cleanly.

The absorbed id MUST stay explicit: `unmerge` MUST NOT default to the
survivor's most recent ledger entry. It is a
destructive restore, and a consequential change stays reviewable rather
than silently automatic — an implicit target would let an `--auto` run
reverse a merge the operator never named. Because that makes the argument
required, the command's PUBLISHED help MUST say so: the one-liner, the
positional `absorbed-id`'s help, and `--to`'s help MUST each state that
exactly one of the two is required, in ONE shared spelling, and MUST NOT
describe the command in terms that imply the survivor alone identifies the
merge. A mid-chain
failure stops immediately, reports the failed step and that earlier steps
completed, and never rolls completed steps back — each intermediate state
is a consistent bundle. A `survivor-id` whose concept file does not exist
but which some OTHER survivor's ledger records absorbing MUST be refused
with an error naming that absorber and the exact unmerge command to run
first (an absorbed ex-survivor's own sidecar survives its absorption).

After restoring, `unmerge` MUST evaluate the deprecated-status export
projection (`deprecated-status-export`) for the restored survivor and the
restored absorbed document over the post-unmerge bundle, and write any
resulting change in the same Phase B, named in the preview. On a bundle
whose pre-merge state was export-consistent for those two documents and
whose `supersedes` edges touching them are unchanged since the merge, the
projection is the identity, so byte-for-byte parity holds exactly as
stated above. Only when those preconditions fail — the pre-merge state
already carried export drift on either document, or a later write changed
a `supersedes` edge touching them — MAY either document's `status` and
`status_derived_from` differ from its snapshot, and then only as the
projection dictates. This is the only permitted deviation from
byte-for-byte parity.

A merge increments the survivor's `version` (see "Frontmatter-Conflict
Resolution"); because the survivor is restored from `survivor_before`,
`unmerge` MUST leave it with its pre-merge `version`, and the byte-for-byte
parity above includes that field.

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

#### Scenario: Published help states that an absorbed id is required
- GIVEN `openkos unmerge --help`
- WHEN the rendered page is read
- THEN the command one-liner, the positional `absorbed-id`'s help, and
  `--to`'s help each state in one shared spelling that exactly one of the
  two is required, and no text describes the command as reversing "the
  most recent merge on a concept"

#### Scenario: A mid-chain --to failure stops without rolling back
- GIVEN a `--to` unwind whose step N fails Phase A or Phase B
- WHEN the failure occurs
- THEN the chain stops immediately with exit non-zero, the report names
  the failed step and that steps 1..N-1 completed, and completed steps are
  NOT rolled back — each intermediate state is a consistent,
  git-recoverable bundle

#### Scenario: Unmerge parity holds on an export-consistent bundle

- GIVEN an export-consistent bundle where a third-party concept supersedes
  the absorbed object, which carries a valid export
- WHEN `merge <survivor> <absorbed>` then `unmerge <survivor> <absorbed>`
  are confirmed with no write in between
- THEN every bundle file, including both documents' `status` and
  `status_derived_from`, is byte-for-byte identical to its pre-merge state

#### Scenario: Unmerge exports a restored document whose pre-merge state had drift

- GIVEN, before the merge, a third-party concept held a hand-written
  `supersedes` edge to the absorbed object, which carried `status: stable`
  (export drift)
- WHEN `merge <survivor> <absorbed>` then `unmerge <survivor> <absorbed>`
  are confirmed
- THEN the restored absorbed document carries `status: deprecated` and
  `status_derived_from: supersedes`, the preview named that change, and
  every other restored byte matches its snapshot
