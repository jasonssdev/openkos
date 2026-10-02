# Delta for Forget Command

## ADDED Requirements

### Requirement: Retiring A Superseded Source Detaches Its Historical References

WHEN the purge set contains a Source that another Source supersedes (the
target of a `supersedes` relation authored by a Source outside the purge
set), `openkos forget` MUST treat exactly two kinds of inbound reference as
HISTORICAL rather than blocking, in Phase A, for both scopes:

1. the superseding Source's `supersedes` relation whose target is a
   purge-set Source; and
2. in a concept outside the purge set, the references the engine itself
   generated for a purge-set Source: its entry in `provenance` and in the
   projected `sources`, and its `## Related` bullet in the generated shape
   (`- [sources/<slug>](/sources/<slug>.md) — <phrase>`).

Historical references MUST be previewed as edits (`~ bundle/<id>.md`, naming
the removal), MUST be covered by the same count confirmation, and MUST be
removed in Phase B through the same drift-guarded write path as the
resurrection status withdrawals, before any deletion. Removing the last
relation MUST omit the `relations:` key. A rewritten concept MUST keep its
remaining `provenance` entries, MUST keep `sensitivity` unchanged (never
lowered), and MUST NOT have its `version` changed. A concept whose provenance
would become empty MUST NOT be rewritten (it is already in the purge set by
Provenance Descendant Resolution).

Every other external reference MUST still block exactly as "Refuse Forget
When Inbound References Exist, Unless `--force`" requires: a hand-written
link, a `## Related` bullet in any other shape, another relation type, a
`supersedes` relation from a non-Source concept, and an unverifiable
referrer. A forget whose purge set contains no superseded Source MUST be
unchanged.

#### Scenario: Retiring the old version needs no --force and leaves no dangling reference

- GIVEN `sources/v2` has `supersedes -> sources/v1`, `sources/v1` has a
  sole-source concept C1 and a shared concept C2 with `provenance:
  [sources/v1, sources/v2]`
- WHEN `openkos forget sources/v1 --scope source` is confirmed
- THEN `sources/v1` and C1 are deleted, `sources/v2` no longer has the
  `supersedes` relation, C2 remains with `provenance: [sources/v2]` and no
  `sources/v1` bullet in `## Related`, and `openkos lint` reports no
  dangling reference

#### Scenario: The edits are previewed and counted

- GIVEN the same bundle
- WHEN the Phase A preview is printed
- THEN it lists `sources/v2` and C2 as `~` edits beside the deletions, and
  the confirmation count is unchanged by them

#### Scenario: A hand-written link still blocks

- GIVEN the same bundle, plus a concept H whose body links
  `/sources/v1.md` by hand
- WHEN `openkos forget sources/v1 --scope source` runs without `--force`
- THEN it refuses in Phase A, exits non-zero, and writes nothing

#### Scenario: A supersedes relation from a non-Source still blocks

- GIVEN `sources/v1` is the target of a `supersedes` relation authored by a
  concept that is not a Source
- WHEN `openkos forget sources/v1 --scope source` runs without `--force`
- THEN it refuses and writes nothing

#### Scenario: A concept whose only Source is retired is deleted, not edited

- GIVEN C1 has `provenance: [sources/v1]` only
- WHEN the retire path runs
- THEN C1 is in the purge set and is deleted, with no detach edit planned

#### Scenario: A detached concept keeps its sensitivity and version

- GIVEN C2 has `sensitivity: confidential` and `version: 3`
- WHEN `sources/v1` is retired
- THEN C2 still has `sensitivity: confidential` and `version: 3`

#### Scenario: A changed target refuses the whole forget

- GIVEN a historical edit target that changes on disk after Phase A
- WHEN Phase B's drift guard runs
- THEN the forget refuses and writes nothing

#### Scenario: A forget with no superseded Source is unchanged

- GIVEN a purge set containing no Source that another Source supersedes
- WHEN `openkos forget` runs
- THEN no reference is classified historical and every refusal rule is as
  before

## MODIFIED Requirements

### Requirement: Refuse Forget When Inbound References Exist, Unless `--force`

`openkos forget` MUST refuse to proceed (Phase A refusal, exits non-zero,
writes nothing) when one or more EXTERNAL inbound references or
unverifiable referrers were detected, UNLESS `--force` is passed. A referrer
whose id is itself a member of the purge set MUST NOT count toward this
refusal (set-difference): an intra-set backlink (e.g. a cascade child's
`## Related` link back to its Source) is expected and MUST NOT block. A
reference that "Retiring A Superseded Source Detaches Its Historical
References" classifies as historical MUST NOT count toward this refusal
either. When `--force` is passed and external references exist, the forget
proceeds; those references are left dangling.

#### Scenario: Intra-set backlink does not block
- GIVEN a cascade child renders a `## Related` backlink to its Source, both
  in the purge set
- WHEN `openkos forget <source-id> --scope source` runs
- THEN this backlink is excluded from the refusal count and does not block

#### Scenario: External inbound reference still refuses by default
- GIVEN a concept outside the purge set holds a reference to a purge-set
  member
- WHEN `openkos forget <source-id> --scope source` runs without `--force`
- THEN it refuses in Phase A, exits non-zero, and writes nothing

#### Scenario: External unverifiable referrer still refuses by default
- GIVEN an unverifiable external referrer mentioning a purge-set member's id
- WHEN `openkos forget <source-id> --scope source` runs without `--force`
- THEN it refuses in Phase A, exits non-zero, and writes nothing

#### Scenario: `--force` overrides an external refusal
- GIVEN an external inbound reference to a purge-set member was detected
- WHEN `openkos forget <source-id> --scope source --force` runs (subject to
  the confirm gate)
- THEN the cascade proceeds and the external reference is left dangling

#### Scenario: A historical supersession reference does not block
- GIVEN the only external references to a purge-set Source are the
  superseding Source's `supersedes` relation and a surviving concept's
  generated provenance entry and `## Related` bullet
- WHEN `openkos forget <source-id> --scope source` runs without `--force`
- THEN it does not refuse
