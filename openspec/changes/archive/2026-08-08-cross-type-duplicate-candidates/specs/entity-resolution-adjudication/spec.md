# Delta for Entity-Resolution Adjudication

## ADDED Requirements

### Requirement: Cross-Type Prompt Honesty

`_build_messages` MUST render a single-type `CandidateGroup`'s prompt with
today's exact bytes (unchanged: `"OKF TYPE: {okf_type}"` plus each member's
untagged header). For a cross-type group (`member_types` holding more than
one distinct value), the prompt MUST instead name every distinct type
present — sourced from `member_types`, not the joined `okf_type` display
label — and MUST tag each member's own header with that member's own type,
so the LLM is never told a false single-type fact for a cross-type group.
The `Verdict` schema (`verdict`/`confidence`/`rationale`) and the
`adjudicate --json` payload's field set (`member_ids`, `okf_type`, `tier`,
`verdict`, `rationale`) MUST remain unchanged: no `member_types` field is
added to the `--json` payload or to `AdjudicatedCandidate`.

#### Scenario: Single-type group keeps today's exact prompt bytes

- GIVEN a same-type `CandidateGroup`
- WHEN `_build_messages` renders its prompt
- THEN the prompt bytes are identical to before this change

#### Scenario: Cross-type group names both types and tags each member

- GIVEN a cross-type `CandidateGroup` with a Concept member and an Entity
  member
- WHEN `_build_messages` renders its prompt
- THEN the prompt names both `Concept` and `Entity`, and each member's
  header is tagged with that member's own type

#### Scenario: Verdict schema is unchanged for a cross-type group

- GIVEN a cross-type group is adjudicated by a fake backend returning a
  valid reply
- WHEN `adjudicate_candidates` runs
- THEN the returned `AdjudicatedCandidate` exposes only `candidate`,
  `verdict`, `confidence`, and `rationale` — no new field

#### Scenario: `--json` payload keys are unchanged for a cross-type group

- GIVEN a cross-type group's adjudication result
- WHEN `adjudicate --json` runs
- THEN each parsed JSON object has exactly the existing keys `member_ids`,
  `okf_type`, `tier`, `verdict`, `rationale`, and no `member_types` key
