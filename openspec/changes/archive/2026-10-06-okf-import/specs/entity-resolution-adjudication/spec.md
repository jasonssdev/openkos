# Delta for Entity Resolution Adjudication

## MODIFIED Requirements

### Requirement: Survivor/Absorbed Preview And Prompt

For each eligible group whose two members are a base/`-N` family (the same
directory, one id being the other plus `-` and decimal digits -- the
ingest-time collision suffix), the survivor MUST be the un-suffixed id
whatever the bodies weigh, and the preview MUST state
`survivor: <id> (canonical id (base of a -N family))`: the Concept ID is
the entity's identity under OKF, so a disambiguator must not become
permanent. The absorbed body is not lost -- the merge stacks and
reconciles both. For every group that is not such a family and in which
exactly one member is an imported concept (a Concept ID under `imports/`,
adopted by `openkos import`) and the other is local, the survivor MUST be the
local member whatever the bodies weigh, and the preview MUST state
`survivor: <id> (local over imported)`: the merged concept then lives at a
local Concept ID, and an imported Concept ID must not become the permanent
identity of a locally held entity. For every other group, the survivor MUST be the member
with the RICHER BODY (longer stripped body text), falling back to `member_ids[0]`
(ascending id) only on an exact tie (string order alone made a
bilingual pleonasm the permanent Concept ID purely because `f` sorts
before `o`; the richer-body rule mirrors the extraction union's own
twin-drop precedent). A group whose two members are both imported, or both
local, is ordered by the same rules as before. An unreadable member measures below every readable
one and never survives on the richer-body rule. The criterion that DECIDED MUST be
stated in the preview (`survivor: <id> (richer body)` or `survivor: <id>
(id order -- equal body length)`) -- an arbitrary-looking choice with no
stated criterion is the defect, not the determinism. Before prompting, a
preview of what `prepare_merge` would fuse (survivor, absorbed, rewrites,
removed) MUST be printed. The prompt text MUST be exactly
`Merge <absorbed> into <survivor>? [y]es / [s]kip / [d]istinct (d records a
permanent keep-distinct ruling)` -- the same prompt `curate`'s Identity stage
asks. The prompt MUST say what `d` records, because `d` is the only answer
that persists anything.

The same ordering rule and criterion disclosure apply to EVERY walk that
drives `_prepare_one_merge`: `--apply`, `--apply-same`, and `curate`'s
Identity stage. Each walk MUST compute the ordering ONCE, display it, and
PIN that exact `(survivor, absorbed)` pair through the prepare-and-write
that follows -- `--apply-same`'s Pass 2 in particular MUST apply the
direction Pass 1 previewed and the typed count consented to, never a live
recomputation: an earlier merge in the same batch can enrich a shared
member enough to flip a recomputed ordering, and the operator would then
get a direction they never saw. A structural consequence, deliberate: with the smaller
body always absorbed into the larger, a 2-member batch merge cannot
reach the 80% stacked-share domination guardrail -- the hazard
that guardrail refuses is prevented by construction (the guardrail
itself remains as defense in depth).
(Previously: no rule distinguished imported from local members; the
local-over-imported rule sits after the base/`-N` rule and before the
richer-body rule.)

#### Scenario: Preview precedes the exact prompt text

- GIVEN an eligible SAME 2-member group
- WHEN `adjudicate --apply` runs
- THEN a `prepare_merge` preview is printed before the prompt
- AND the prompt line is exactly `Merge <absorbed> into <survivor>? [y]es /
  [s]kip / [d]istinct (d records a permanent keep-distinct ruling)`
  with `<survivor>` = the richer-body member and `<absorbed>` the other

#### Scenario: The richer body survives regardless of id order

- GIVEN a SAME 2-member group whose alphabetically-later member carries
  the longer body
- WHEN any apply walk previews it
- THEN that member is the survivor and the preview states `richer body`

#### Scenario: A base/-N family keeps the canonical id
- GIVEN a SAME 2-member group `people/ana` and `people/ana-2`, where
  `people/ana-2` has the longer body
- WHEN any apply walk previews it
- THEN `people/ana` is the survivor and the preview states the
  canonical-id criterion

#### Scenario: A tie keeps ascending-id order and says so

- GIVEN a SAME 2-member group whose members' stripped bodies have equal
  length
- WHEN any apply walk previews it
- THEN `member_ids[0]` is the survivor and the preview states the id-order
  tiebreak

#### Scenario: A local member survives an imported one whatever the bodies weigh

- GIVEN a SAME 2-member group of `concepts/stoicism` (local, short body) and
  `imports/acme/concepts/stoicism` (imported, longer body)
- WHEN any apply walk previews it, and again with the ids' sort order
  reversed
- THEN `concepts/stoicism` is the survivor in both cases and the preview
  states `local over imported`

#### Scenario: The surviving local concept is no longer imported

- GIVEN the previous group is merged by `curate` Identity
- WHEN the merge completes
- THEN the surviving concept has a local Concept ID, the absorbed imported
  document is gone from `imports/acme/`, and the survivor's content,
  provenance and label follow the existing merge rules

#### Scenario: Two imported members use the existing rules

- GIVEN a SAME 2-member group where both members are imported
- WHEN any apply walk previews it
- THEN the survivor is chosen by the base/`-N` rule or the richer-body rule
  exactly as for two local members, and the preview does not state `local
  over imported`

#### Scenario: An imported base/-N family keeps the canonical-id rule

- GIVEN a group `imports/acme/concepts/python` and
  `imports/acme/concepts/python-3`, both imported
- WHEN any apply walk previews it
- THEN `imports/acme/concepts/python` is the survivor by the canonical-id
  rule, as before this change
