# Delta for Curate Command

## MODIFIED Requirements

### Requirement: Identity Stage Reuses Merge Cores

Identity MUST call `find_candidates` then `adjudicate_candidates`, then
apply each accepted pair via `_prepare_one_merge`/`_commit_one_merge`,
auto-committing per merge. N>2 groups MUST NOT be auto-merged; `curate`
MUST print the exact pairwise `openkos merge` commands per group.
Because `find_candidates` now bounds and ranks its output before any
adjudication call (entity-resolution delta: Bounded Candidate-Group
Output Per Call), the number of `CandidateGroup`s Identity's probe
(`_identity_probe`, `cli/curate.py:271-282`) queues, and therefore the
number of adjudication calls `_identity_run` issues, MUST never exceed
`_MAX_CANDIDATE_GROUPS` regardless of corpus size — the SAME sequencer
that already gates Identity's cost line and consent flow (curate-command:
Per-Stage Cost Gate) is unchanged; only the upstream group count it reads
from `probe.llm_calls` is now bounded.

#### Scenario: Accepted pair is committed per-item

- GIVEN one accepted duplicate pair
- WHEN Identity applies it
- THEN `_prepare_one_merge`/`_commit_one_merge` run and the bundle
  auto-commits before the next item

#### Scenario: N>2 group prints pairwise commands, never auto-merges

- GIVEN a candidate group of 3
- WHEN Identity reaches it
- THEN it prints the exact pairwise `openkos merge` commands and performs
  no merge

#### Scenario: Identity's adjudication call count stays capped on a large corpus

- GIVEN a bundle whose Identity queue would otherwise total 150
  `CandidateGroup`s (an uncapped `find_candidates` result)
- WHEN `curate` runs the Identity stage with `--auto`
- THEN the printed cost line's call count and the number of adjudication
  calls actually issued both stay at or below `_MAX_CANDIDATE_GROUPS`

## ADDED Requirements

### Requirement: Identity Cost Line Discloses Truncation

`_identity_probe` MUST expose the SAME `produced`/`retained` truncation
signal `find_candidates` now makes observable (entity-resolution delta:
Truncation Is Never Silent), through `StageProbe.notice` — the same
channel `_structure_probe` already uses for the Structure stage's
candidate-edge cap (`cli/curate.py:417-431`). WHEN Identity's candidate-
group set is truncated (`produced > retained`), the printed notice MUST
disclose both counts, in a shape consistent with the existing
`"{retained} of {produced} ... shown (cap reached)"` pattern
(`resolution/edge_typing.py:589`) substituting the group noun for the
edge noun used by Structure. WHEN Identity's candidate-group set is NOT
truncated (`produced == retained`), NO truncation notice MUST be printed,
matching Structure's existing no-truncation behavior. The exact notice
wording is confirmed at design time; only this disclose-iff-truncated
contract, and the `{retained} of {produced}` count pair within it, are
required here.

#### Scenario: Cap reached — Identity's notice discloses both counts

- GIVEN a bundle whose Identity candidate-group set is truncated from 80
  produced to 50 retained
- WHEN `curate` runs the Identity stage
- THEN a notice is printed disclosing both the produced count (80) and
  the retained count (50), in the "N of M ... shown (cap reached)" shape

#### Scenario: Cap not reached — no truncation notice

- GIVEN a bundle whose Identity candidate-group set has 12 produced and
  12 retained groups (below the cap)
- WHEN `curate` runs the Identity stage
- THEN no truncation notice is printed for Identity

### Requirement: Below-Cap Cost-Line Output Is Byte-Identical To Pre-Change Behavior

For any bundle whose Identity `CandidateGroup` count does not exceed
`_MAX_CANDIDATE_GROUPS`, EVERY existing pinned literal in
`tests/unit/cli/test_curate.py` that asserts Identity's `cost_line`
output (the `"{n} candidate group(s) -> {n} LLM call(s)"` shape produced
by `cost_line`, `cli/curate.py:188-204`, from `probe.llm_calls`) MUST
remain unchanged: this change MUST NOT alter the cost-line wording,
MUST NOT alter `probe.llm_calls`'s value for a below-cap corpus, and
MUST NOT introduce a truncation notice for a below-cap corpus. Only a
bundle whose candidate-group count exceeds the cap is a test-visible
contract change (a new notice line, and `probe.llm_calls` bounded rather
than equal to the uncapped group count).

#### Scenario: Below-cap Identity cost line is unchanged

- GIVEN a bundle producing 6 candidate groups, below the cap, exactly as
  in the pre-change pinned test fixtures
- WHEN `curate` reaches the Identity stage's cost gate
- THEN the printed cost line reads `"6 candidate group(s) -> 6 LLM
  call(s)"`, byte-identical to its pre-change wording, and no truncation
  notice is printed

#### Scenario: Above-cap Identity cost line reflects the bounded count

- GIVEN a bundle producing 80 candidate groups, exceeding the cap
- WHEN `curate` reaches the Identity stage's cost gate
- THEN the printed cost line's call count is the capped `retained` value
  (50), not the uncapped `produced` value (80), and a truncation notice
  naming both counts is also printed
