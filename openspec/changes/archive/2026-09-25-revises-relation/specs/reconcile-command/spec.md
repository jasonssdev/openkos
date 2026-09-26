# Delta for Reconcile Command

## ADDED Requirements

### Requirement: Refinement Reconciliation via --revision

When `--revision <id>` is passed, the system MUST write a DIRECTIONAL
`revises` typed edge from `<id>` (the refining concept) to the other pair
member — one outbound edge on the holder only; the other member gains no
outbound edge. `<id>` MUST equal exactly one of the two pair members; any
other value MUST error before any write occurs. `--revision` MUST refuse
with exit code 1 and zero writes when combined with `--winner`, and MUST
refuse with exit code 1 and zero writes when combined with
`--from-findings`.

The system MUST append a `## Reconciliation` body note to BOTH concepts:
the holder's note MUST read
`Revises [<id>](/<id>.md) as of <date> (refinement; both remain current).`
referencing the other member, and the other member's note MUST read
`Revised by [<id>](/<id>.md) as of <date> (refinement; both remain current).`
referencing the holder. The system MUST append a `**Reconcile**` line to
`log.md` whose success text states that both concepts remain current, and
MUST commit with the message shape `openkos: reconcile <a> revises <b>`
(where `<a>` is the holder and `<b>` is the other member).

`revises` MUST NOT be part of the LLM-suggestable relation vocabulary: an
edge suggester MUST NEVER propose it. It is written only through this
explicit human `--revision` flag, mirroring the existing `supersedes` and
`reconciled_with` precedent.

#### Scenario: Revision writes a single outbound edge

- GIVEN concepts `alpha` and `beta` exist and carry no resolution edge
- WHEN the user runs `reconcile alpha beta --revision alpha` and confirms
- THEN `alpha` gains a single outbound `revises` edge targeting `beta`
- AND `beta` gains no outbound edge
- AND `alpha`'s `## Reconciliation` note reads
  `Revises [beta](/beta.md) as of <date> (refinement; both remain current).`
- AND `beta`'s `## Reconciliation` note reads
  `Revised by [alpha](/alpha.md) as of <date> (refinement; both remain current).`
- AND `log.md` gains a `**Reconcile**` line stating both remain current
- AND the commit message is `openkos: reconcile alpha revises beta`

#### Scenario: --revision id not in pair

- GIVEN concepts `alpha`, `beta`, `gamma` exist
- WHEN the user runs `reconcile alpha beta --revision gamma`
- THEN the command errors and exits non-zero
- AND no file is modified

#### Scenario: --revision combined with --winner refuses

- GIVEN concepts `alpha` and `beta` exist and are unreconciled
- WHEN the user runs `reconcile alpha beta --revision alpha --winner alpha`
- THEN the command errors and exits with code `1`
- AND no write is performed

#### Scenario: --revision combined with --from-findings refuses

- GIVEN a `--from-findings` walk invoked together with `--revision`
- WHEN the user runs `reconcile --from-findings --revision alpha`
- THEN the command errors and exits with code `1`
- AND no write is performed

#### Scenario: revises stays out of the suggestable vocabulary

- GIVEN the relation-type vocabulary constants (`REGISTRY` and
  `SUGGESTABLE_RELATION_TYPES`)
- WHEN they are inspected
- THEN neither contains `revises`, so no LLM edge suggester can ever
  propose it

### Requirement: At Most One Resolution Per Pair

At most one resolution — symmetric `reconciled_with`, directional
`supersedes`, or directional `revises` — MUST exist per pair at any time.
Given the pair's existing resolution state (`none`, `symmetric`,
`directional(<holder>)`, or `revision(<holder>)`) and the requested mode
(`symmetric`, `directional(<id>)` via `--winner <id>`, or `revision(<id>)`
via `--revision <id>`), the system MUST resolve to exactly one of three
outcomes: WRITE (existing is `none`), IDEMPOTENT NO-OP (the request exactly
repeats the existing resolution — same mode AND, for directional or
revision, the same holder), or REFUSE (any other combination — a different
mode, or the same mode with a different holder), per this table
(`revision(X)` means X revises its counterpart; `directional(X)` means X
supersedes it):

| Existing \ Requested | symmetric | directional(A) | revision(A) |
| --- | --- | --- | --- |
| none | write | write | write |
| symmetric | idempotent no-op | refuse | refuse |
| directional(A) | refuse | idempotent no-op | refuse |
| directional(B) | refuse | refuse | refuse |
| revision(A) | refuse | refuse | idempotent no-op |
| revision(B) | refuse | refuse | refuse |
| mixed | refuse | refuse | refuse |

Every REFUSE MUST exit non-zero (code `1`) with zero writes performed. The
refusal's error message MUST name the pair's existing resolution
(symmetric reconciliation; directional supersession naming its holder; or
revision naming its holder) so the user understands why the write was
refused.

The existing state is `mixed` when the pair carries more than one resolution
edge (`supersedes`, `revises` or `reconciled_with`, in either direction) and
those edges disagree, as can happen after a hand edit. A `mixed` pair MUST
refuse every request, exit `1` and perform zero writes, and the refusal MUST
name the state as conflicting resolutions. The system MUST NOT pick one edge
by precedence.

#### Scenario: Every refusal cell exits 1 with zero writes

- GIVEN a pair already reconciled symmetrically
- WHEN the user requests `--winner` for either member, or `--revision` for
  either member
- THEN each request errors, exits `1`, and writes nothing
- GIVEN a pair with an existing directional `supersedes` edge held by
  `alpha`
- WHEN the user requests symmetric reconciliation, requests `--revision`
  for either member, or requests `--winner beta` (the non-holder)
- THEN each request errors, exits `1`, and writes nothing
- GIVEN a pair with an existing `revises` edge held by `alpha`
- WHEN the user requests symmetric reconciliation, requests `--winner` for
  either member, or requests `--revision beta` (the non-holder)
- THEN each request errors, exits `1`, and writes nothing

#### Scenario: A hand-edited pair with conflicting resolutions refuses every request

- GIVEN a pair where `alpha` holds a `supersedes` edge to `beta` AND `beta`
  holds a `revises` edge to `alpha` (written by hand)
- WHEN the user requests symmetric reconciliation, `--winner` for either
  member, or `--revision` for either member
- THEN each request errors, exits `1`, writes nothing, and the error message
  names the pair's resolutions as conflicting

#### Scenario: Refusal names the existing resolution

- GIVEN a pair already reconciled with a directional `supersedes` edge
  held by `alpha`
- WHEN the user runs `reconcile alpha beta --revision alpha`
- THEN the command errors, exits `1`, and the error message names the
  existing directional resolution and its holder

## MODIFIED Requirements

### Requirement: Default Symmetric Reconciliation

By default (no `--winner` flag), the system MUST write a SYMMETRIC
`reconciled_with` typed edge on BOTH concepts, each referencing the other
as `target`. The system MUST append a `## Reconciliation` body note to
BOTH concepts referencing the counterpart, and MUST append a
`**Reconcile**` line to `log.md`.
(Previously: the note heading was written in this spec as
`# Reconciliation`; this corrects the spec to match the code, which writes
`## Reconciliation`.)

#### Scenario: Symmetric reconcile

- GIVEN concepts `alpha` and `beta` exist and are unreconciled
- WHEN the user runs `reconcile alpha beta` and confirms
- THEN `alpha` gains a `reconciled_with` edge targeting `beta` and a
  `## Reconciliation` note referencing `beta`
- AND `beta` gains a `reconciled_with` edge targeting `alpha` and a
  `## Reconciliation` note referencing `alpha`
- AND `log.md` gains a `**Reconcile**` line

### Requirement: Directional Reconciliation via --winner

When `--winner <id>` is passed, the system MUST write a DIRECTIONAL
`supersedes` edge from the winner to the loser (one outbound edge on the
winner only), and MUST add a `## Reconciliation` note on both concepts and
a `**Reconcile**` log line. `<id>` MUST equal exactly one of the two pair
members; any other value MUST error before any write occurs. The
`supersedes` edge MUST be documented as label-only: it enforces no
deprecation or lifecycle behavior.
(Previously: the note heading was written in this spec as
`# Reconciliation`; this corrects the spec to match the code, which writes
`## Reconciliation`.)

#### Scenario: Winner supersedes loser

- GIVEN concepts `alpha` and `beta` exist and are unreconciled
- WHEN the user runs `reconcile alpha beta --winner alpha` and confirms
- THEN `alpha` gains a single outbound `supersedes` edge targeting `beta`
- AND `beta` gains no outbound edge
- AND both concepts gain a `## Reconciliation` note; `log.md` gains a
  `**Reconcile**` line

#### Scenario: --winner id not in pair

- GIVEN concepts `alpha`, `beta`, `gamma` exist
- WHEN the user runs `reconcile alpha beta --winner gamma`
- THEN the command errors and exits non-zero
- AND no file is modified

### Requirement: Idempotent Re-run

Re-running `reconcile` on an already-reconciled pair (same shape:
symmetric, same `--winner`, or same `--revision`) MUST NOT duplicate the
edge (deduped on `(target, type)`) and MUST NOT re-append a
`## Reconciliation` note already citing the same counterpart. The system
MUST instead write a "no change" variant of the `**Reconcile**` log line.
(Previously: this requirement covered only the symmetric and `--winner`
shapes, and referenced the note heading as `# Reconciliation`; both are
corrected here — the invariant now also covers `--revision`, and the note
heading matches the code's `## Reconciliation`.)

#### Scenario: Re-run is a no-op write

- GIVEN `alpha` and `beta` were already reconciled symmetrically
- WHEN the user runs `reconcile alpha beta` again and confirms
- THEN no duplicate `reconciled_with` edge or `## Reconciliation` note is
  added to either concept
- AND `log.md` gains a `**Reconcile**: ...; no change.` line

#### Scenario: Revision re-run is a no-op write

- GIVEN `alpha` already revises `beta` (written via
  `reconcile alpha beta --revision alpha`)
- WHEN the user runs `reconcile alpha beta --revision alpha` again and
  confirms
- THEN no duplicate `revises` edge or `## Reconciliation` note is added to
  either concept
- AND `log.md` gains a `**Reconcile**: ...; no change.` line
