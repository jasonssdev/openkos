# Delta for Entity-Resolution Candidates

## MODIFIED Requirements

### Requirement: Strict Per-Type Blocking

`find_candidates` MUST only compare objects of the same declared OKF type
for the ACRONYM and LOW tiers, and MUST NOT produce an ACRONYM or LOW
candidate between objects of different types, regardless of title or
acronym similarity. The HIGH (exact normalized-key) tier is exempt from
this blocking — see Requirement: Cross-Type Exact-Title Bucketing (HIGH
Tier).
(Previously: this requirement applied to ALL tiers including HIGH, with no
exemption.)

#### Scenario: Cross-type similar-but-not-identical titles produce no candidate

- GIVEN a Concept and an Entity whose titles are similar but do not
  normalize identically (LOW-tier similarity only)
- WHEN `find_candidates` runs
- THEN no candidate is returned for that pair

#### Scenario: Cross-type acronym match produces no candidate

- GIVEN a Concept and an Entity that would match under the ACRONYM rule
- WHEN `find_candidates` runs
- THEN no candidate is returned for that pair

## ADDED Requirements

### Requirement: Cross-Type Exact-Title Bucketing (HIGH Tier)

`find_candidates`, `find_candidates_report`, and `find_exact_title_groups`
MUST bucket the HIGH (exact normalized-key) tier by normalized title ACROSS
all declared OKF types, not per-type: two objects of different types whose
titles normalize identically MUST form one HIGH-confidence candidate group.
This bucketing MUST be applied to both `find_candidates`/
`find_candidates_report` and `find_exact_title_groups` together, so their
existing equivalence contract (identical HIGH-slice output, same order)
continues to hold for cross-type groups. The bucketing MUST remain O(n) —
no pairwise comparison introduced — so `find_exact_title_groups` MUST
continue to invoke the near-match similarity function zero times.

#### Scenario: Cross-type exact-title pair forms one HIGH group in both entry points

- GIVEN a Concept and an Entity whose titles normalize identically
- WHEN `find_candidates(bundle_dir)` and `find_exact_title_groups(bundle_dir)`
  both run
- THEN both return a single HIGH-confidence group containing both
  concept_ids

#### Scenario: Cross-type HIGH groups stay equal between entry points

- GIVEN a bundle containing at least one cross-type exact-title group and
  one same-type exact-title group
- WHEN `find_exact_title_groups(bundle_dir)` and `find_candidates(bundle_dir)`
  both run
- THEN the first result equals the HIGH-confidence groups of the second as
  an ordered list, including the cross-type group

#### Scenario: find_exact_title_groups still makes zero near-match calls

- GIVEN a bundle whose only overlap is a cross-type exact-title pair
- WHEN `find_exact_title_groups(bundle_dir)` runs
- THEN the near-match similarity function is called zero times

#### Scenario: ACRONYM/LOW results are unaffected absent cross-type overlap

- GIVEN a bundle with ACRONYM and LOW candidate groups but no cross-type
  exact-title overlap
- WHEN `find_candidates(bundle_dir)` runs before and after this change
- THEN the ACRONYM and LOW groups returned are byte-identical

### Requirement: `CandidateGroup.member_types` Field

`CandidateGroup` MUST carry a `member_types: tuple[str, ...]` field,
index-aligned with `member_ids` (`member_types[i]` is the declared OKF type
of `member_ids[i]`). It MUST default, via `__post_init__`, to
`(okf_type,) * len(member_ids)` so every existing same-type construction
site remains valid without change and the field is never empty. `okf_type`
remains the display scalar: for a same-type group it is that shared type;
for a cross-type group it is the distinct member types, sorted and joined
with `+` (e.g. `"Concept+Entity"`) — an ephemeral display label only, never
a persisted OKF type, and never parsed back into individual types by any
consumer.

#### Scenario: Same-type group defaults member_types from okf_type

- GIVEN a HIGH, ACRONYM, or LOW group whose members share one OKF type
- WHEN the `CandidateGroup` is constructed with only `okf_type` given
- THEN `member_types` equals a tuple of that type repeated once per member

#### Scenario: Cross-type group carries each member's own type

- GIVEN a cross-type HIGH group with a Concept and an Entity member
- WHEN the `CandidateGroup` is constructed
- THEN `member_types` is index-aligned with `member_ids`, holding
  `"Concept"` at the Concept member's index and `"Entity"` at the Entity
  member's index

#### Scenario: Cross-type okf_type is a sorted, joined display label

- GIVEN a cross-type group spanning `Entity` and `Concept`
- WHEN its `okf_type` is read
- THEN it equals `"Concept+Entity"` (sorted, `+`-joined), regardless of
  which member type appears first in `member_ids`

#### Scenario: Cap-rank tie-break stays stable with a joined label

- GIVEN a bundle whose candidate-group count exceeds `_MAX_CANDIDATE_GROUPS`
  and includes at least one cross-type HIGH group with a joined `okf_type`
  label
- WHEN `find_candidates` ranks and truncates the full set
- THEN the joined label sorts deterministically as an opaque string in the
  `(okf_type, member_ids)` tie-break, and repeated calls over an unchanged
  bundle produce the identical retained set and order
