# Delta for Entity-Resolution Candidates

## ADDED Requirements

### Requirement: Bounded Candidate-Group Output Per Call

`find_candidates` MUST bound its returned `CandidateGroup` list to a fixed
ceiling, expressed as a private module-level `Final[int]` constant
(`_MAX_CANDIDATE_GROUPS`, value `50`, matching `sqlite_graph._MAX_CANDIDATE_EDGES`
at `graph/sqlite_graph.py:241`), applied to the FULL cross-type group set
BEFORE `find_candidates` returns and, transitively, BEFORE `curate`'s
Identity stage or standalone `adjudicate`/`duplicates` issues a single
adjudication call. Today `find_candidates` (`resolution/candidates.py:220-287`)
returns every group an unbounded pairwise pass produces (module docstring,
`candidates.py:307-309`: "an O(n^2) cost in concepts-per-type"), and an
exhaustive grep for `_MAX`/`limit` across `resolution/candidates.py` and
`resolution/adjudication.py` returns zero hits (exploration.md) — this
requirement closes that gap by extending the house cap idiom
(`_MAX_CANDIDATE_EDGES`, `graph/sqlite_graph.py:241`; `_MAX_PAIRS`,
`resolution/contradiction.py:71`) to the one stage that never received it.

This ceiling is a SAFETY RAIL against pathological corpora, not a
per-session curation budget. It MUST be sized so that it rarely binds on a
representative corpus and MUST NOT be interpreted or retuned as an
iterative-curation mechanism: `curate`'s Identity stage MUST remain a
single pass over `find_candidates`' output in normal operation, with no
retry loop, resumable cursor, or multi-invocation contract implied by this
cap. When the cap does bind, the excess groups are simply absent from this
call's result; a later invocation over an unchanged bundle reproduces the
identical truncation (see Deterministic Ranking below), and only bundle
mutation (e.g. an accepted merge shrinking the pool) changes what a
subsequent call sees.

#### Scenario: Adjudication call count never exceeds the cap

- GIVEN a bundle whose type partitions would otherwise produce 200
  `CandidateGroup`s from an uncapped pairwise pass
- WHEN `find_candidates` runs and its result is handed to
  `adjudicate_candidates`
- THEN `find_candidates` returns at most 50 groups, and the number of
  adjudication calls issued is at most 50, regardless of corpus size

#### Scenario: Below-cap corpus is unaffected

- GIVEN a bundle whose type partitions produce 12 `CandidateGroup`s in
  total, below the cap
- WHEN `find_candidates` runs
- THEN all 12 groups are returned and none are discarded

### Requirement: Deterministic Ranking For Truncation

WHEN the full cross-type candidate-group set exceeds `_MAX_CANDIDATE_GROUPS`,
`find_candidates` MUST rank the full set before truncating, using this
total order: tier priority first — HIGH before ACRONYM before LOW, matching
the existing `_TIER_ORDER` table (`candidates.py:42-64`) — then, within the
LOW tier only, by `near_match_score` descending (closest match first; the
score is recoverable from `CandidateGroup.trigger`, formatted per
`candidates.py:282`). Because HIGH and ACRONYM groups carry no score, ties
within either of those two tiers, and any tie within LOW at equal score,
MUST be broken by the SAME `(okf_type, member_ids)` ascending total order
`find_candidates` already establishes as its final sort key
(`candidates.py:286`, documented at `candidates.py:295-306`). Tier priority
in this ranking is GLOBAL across the whole cross-type set — a HIGH-tier
group in an alphabetically later `okf_type` MUST outrank a LOW-tier group
in an alphabetically earlier `okf_type` when both compete for the same
capacity. Once the retained subset is selected, `find_candidates` MUST
still return it in the module's existing canonical output order
(`okf_type` ascending, then tier, then `member_ids` — `candidates.py:286`):
the ranking above governs ONLY which groups survive the cap, never the
order of the returned list. This ranking MUST be stable: given an
unchanged bundle, repeated calls MUST produce the identical retained set
in the identical order (extends the existing determinism guarantee,
`candidates.py:227-230`).

#### Scenario: HIGH groups fill the cap before any LOW group is considered

- GIVEN a bundle producing 45 HIGH-tier groups and 30 LOW-tier groups
  (75 total, exceeding the 50 cap)
- WHEN `find_candidates` runs
- THEN all 45 HIGH-tier groups are retained and exactly 5 LOW-tier groups
  are retained, chosen by highest `near_match_score`

#### Scenario: HIGH-tier ranking outranks a LOW-tier group in an earlier type

- GIVEN a bundle where a LOW-tier group belongs to an `okf_type` that
  sorts alphabetically before the `okf_type` of a HIGH-tier group, and
  together with other groups they compete for the last cap slot
- WHEN `find_candidates` runs
- THEN the HIGH-tier group is retained and the LOW-tier group is
  truncated, even though its `okf_type` would sort first in the
  module's existing per-type output order

#### Scenario: HIGH-only excess is tie-broken by (okf_type, member_ids)

- GIVEN a bundle producing 60 HIGH-tier groups and zero ACRONYM/LOW
  groups, exceeding the 50 cap
- WHEN `find_candidates` runs
- THEN the 50 retained groups are exactly the first 50 in `(okf_type,
  member_ids)` ascending order among the 60 HIGH-tier groups

#### Scenario: LOW-tier ties are broken deterministically

- GIVEN a bundle where two or more LOW-tier groups share the identical
  `near_match_score` and, together with higher-scored groups, compete for
  the last cap slot
- WHEN `find_candidates` runs
- THEN the group retained among the tied candidates is the one that
  sorts first by `(okf_type, member_ids)` ascending

#### Scenario: Retained groups keep the module's existing output order

- GIVEN a bundle whose candidate-group count exceeds the cap
- WHEN `find_candidates` runs
- THEN the returned (truncated) list is ordered `okf_type` ascending,
  then tier, then `member_ids` ascending — the same order
  `find_candidates` already guarantees for an uncapped result — and NOT
  in the ranking order used to select the retained subset

#### Scenario: Repeated calls over an unchanged bundle truncate identically

- GIVEN an unchanged bundle whose candidate-group count exceeds the cap
- WHEN `find_candidates` runs twice
- THEN both calls return the identical retained group set in the
  identical order

### Requirement: Truncation Is Never Silent

`find_candidates` MUST make BOTH the pre-cap count (`produced`) and the
post-cap count (`retained`) observable to its callers, mirroring the
`CandidateReport(produced, retained)` shape `graph/sqlite_graph.py:251-273`
already establishes for `_MAX_CANDIDATE_EDGES`. WHEN `produced > retained`
(the cap bound), that fact MUST be disclosed to a caller rendering a
report or cost line — never silently dropped. WHEN `produced == retained`
(the cap did not bind), no truncation MUST be disclosed. The exact
mechanism by which `produced`/`retained` reach a caller (an additional
return value, a module-level report object, or an equivalent shape) is
unspecified here and left to design; only this observable contract is
required.

#### Scenario: Cap binds — produced and retained diverge and are observable

- GIVEN a bundle producing 80 candidate groups, exceeding the 50 cap
- WHEN `find_candidates` runs
- THEN a caller can observe `produced == 80` and `retained == 50`

#### Scenario: Cap does not bind — produced equals retained

- GIVEN a bundle producing 12 candidate groups, below the cap
- WHEN `find_candidates` runs
- THEN a caller observes `produced == retained == 12`

### Requirement: ACRONYM Once-Under-The-Stronger-Tier Behavior Is Preserved

The existing rule that a pair matching both the ACRONYM and LOW criteria
is emitted exactly once, under the ACRONYM tier (`candidates.py:258-273`,
"evaluated BEFORE the near-match rule so a pair qualifying under both is
emitted once, under the stronger of the two"), MUST be unaffected by
ranking or truncation: the ranking and cap in this delta operate strictly
AFTER group construction and MUST NOT cause a pair to be reconsidered
under, or double-counted against, a different tier.

#### Scenario: ACRONYM/LOW dedup holds when the cap is engaged

- GIVEN a bundle containing a pair that matches both the ACRONYM rule and
  the near-match (LOW) rule, together with enough other groups that the
  total candidate-group count exceeds the cap
- WHEN `find_candidates` runs
- THEN that pair appears in the result at most once, under `Tier.ACRONYM`,
  exactly as it would with the cap absent
