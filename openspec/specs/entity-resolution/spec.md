# Entity-Resolution Candidates Specification

## Purpose

`resolution/` is a new derived-layer package: a read-only, whole-bundle pass
that surfaces CANDIDATE pairs/groups of same-type objects that MIGHT be the
same real-world entity, so fragmentation (e.g. "Stoicism" vs "Stoic
Philosophy") becomes visible for human review. It never decides, merges, or
writes; candidates are ephemeral (returned dataclasses plus a rendered
report), never a persisted OKF type or `bundle/` state file.

## Non-Goals

This spec does not define: LLM adjudication of candidates (slice 2);
destructive `merge`/`resolve`, merge records, tombstones, sensitivity
recompute, or un-merge (slice 3); embedding/vector-based candidate
generation; any mutation of bundle bytes; changes to `ingest`'s
single-source contract; or stable/content-based concept ids.

## Requirements

### Requirement: Whole-Bundle Candidate Generation

`resolution.find_candidates(bundle_dir)` MUST scan every non-reserved
concept document in the bundle (mirroring the existing `_iter_docs` walk)
and return candidate groups. Each candidate MUST reference the involved
concept_ids, their shared OKF type, a confidence tier, and the normalized
key or similarity value that triggered the match.

#### Scenario: Candidates reference concept_ids, type, and match reason

- GIVEN a bundle with two same-type documents whose titles are near-duplicates
- WHEN `find_candidates(bundle_dir)` runs
- THEN the result includes a candidate with both concept_ids, their shared
  type, a confidence tier, and the triggering key/similarity value

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

### Requirement: Exact Normalized-Key Match (HIGH Confidence)

Two same-type objects whose titles normalize to an identical key MUST form
a HIGH-confidence candidate. Normalization MUST case-fold, collapse
internal whitespace and strip surrounding whitespace, strip punctuation,
and remove diacritics via Unicode normalization, before comparison.

#### Scenario: Differently formatted identical titles form a HIGH candidate

- GIVEN two same-type objects titled e.g. "Café Society" and
  "cafe   society"
- WHEN `find_candidates` runs
- THEN a HIGH-confidence candidate is returned for both concept_ids,
  carrying the shared normalized key

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

### Requirement: Near-Match Tier (LOW Confidence)

`find_candidates` MUST apply a single fixed, documented similarity
threshold, computed via a deterministic stdlib-only algorithm (e.g.
`difflib` ratio or token overlap; no third-party dependency, no LLM) over
normalized titles. Same-type titles at or above the threshold, but not
normalized-identical, MUST form a LOW-confidence candidate; titles below
the threshold MUST NOT form a candidate on this basis.

#### Scenario: Highly similar non-identical titles form a LOW candidate

- GIVEN two same-type objects with clearly similar but non-identical
  normalized titles (e.g. "Stoicism" vs "Stoic Philosophy")
- WHEN `find_candidates` runs
- THEN a LOW-confidence candidate is returned, carrying the similarity value

#### Scenario: Dissimilar titles form no candidate

- GIVEN two same-type objects with clearly dissimilar normalized titles
- WHEN `find_candidates` runs
- THEN no candidate is returned for that pair

### Requirement: Exact-Title-Only Entry Point

`resolution.find_exact_title_groups(bundle_dir)` MUST return exactly the
HIGH-confidence (exact normalized-key) candidate groups that
`find_candidates` returns for the same bundle and the same
`include_deprecated` value, in the same order, and MUST do so without
running the near-match tier: the similarity computation MUST NOT be invoked
at all. Every other candidate-building contract in this spec applies to it
unchanged — strict per-type blocking, deterministic read-only building, no
self-pairing, unordered pairs once, trivial bundles, and degrade-not-crash
on unreadable or malformed documents. It MUST be a distinct public function
rather than a tier-filter parameter on `find_candidates`, so that no caller
can silently select the quadratic cost class, and `find_candidates` MUST
stay unchanged for the callers that render or adjudicate both tiers. What
this entry point removes is the pairwise near-match work only; it MUST NOT
be specified or documented as reducing the number of bundle walks.

#### Scenario: Exact-title-only result equals the full pass's HIGH groups

- GIVEN a bundle containing at least two types, more than one exact-title
  group within a single type, a near-match-only pair, and a deprecated
  concept
- WHEN `find_exact_title_groups(bundle_dir)` and `find_candidates(bundle_dir)`
  both run
- THEN the first result equals the HIGH-confidence groups of the second as an
  ordered list, for both the default `include_deprecated=False` and
  `include_deprecated=True`

#### Scenario: Near-match computation is never invoked

- GIVEN a bundle whose documents include at least one near-match-only pair
- WHEN `find_exact_title_groups(bundle_dir)` runs
- THEN the near-match similarity function is called zero times, while
  `find_candidates` over the same bundle calls it a non-zero number of times

#### Scenario: Malformed document is skipped by the exact-title-only pass

- GIVEN a bundle with one malformed/unreadable document and two other
  same-type documents whose titles normalize identically
- WHEN `find_exact_title_groups(bundle_dir)` runs
- THEN it does not raise, the malformed document is excluded, and the
  exact-title group among the valid documents is still returned

### Requirement: Deterministic, Read-Only Candidate Building

Building candidates MUST NOT modify any bundle file's bytes or mtime and
MUST create no persisted state. Given an unchanged bundle, running
`find_candidates` twice MUST yield the same candidate set in the same
stable order.

#### Scenario: Building candidates writes nothing

- GIVEN any bundle
- WHEN `find_candidates` runs
- THEN every file under the bundle is unchanged (bytes and mtime), and no
  new file or directory is created

#### Scenario: Repeated runs are deterministic

- GIVEN a bundle unchanged between two calls
- WHEN `find_candidates` runs twice
- THEN both calls return the same candidate set in the same order

### Requirement: No Self-Pairing; Unordered Pairs Once; Trivial Bundles

An object MUST NOT be reported as a candidate against itself. Each
unordered pair of matching objects MUST appear at most once (never
duplicated as both A-B and B-A). A bundle with zero or one document of a
given type MUST yield no candidates for that type and MUST NOT raise.

#### Scenario: Matching pair appears exactly once

- GIVEN two same-type objects that match on either tier
- WHEN `find_candidates` runs
- THEN exactly one candidate entry represents that pair

#### Scenario: Empty or single-object bundle yields no candidates

- GIVEN a bundle with zero or one concept document
- WHEN `find_candidates` runs
- THEN it returns no candidates and does not raise

### Requirement: Degrade, Not Crash, On Unreadable Or Malformed Documents

`find_candidates` MUST mirror the bundle scan's existing skip-and-continue
contract for unreadable or malformed documents: such a document MUST be
skipped from candidate consideration without raising, and MUST NOT prevent
candidates among the remaining valid documents.

#### Scenario: Malformed document is skipped, others still compared

- GIVEN a bundle with one malformed/unreadable document and two other
  same-type documents that would otherwise match
- WHEN `find_candidates` runs
- THEN it does not raise, the malformed document is excluded, and the
  matching pair among the valid documents is still returned

### Requirement: Read-Only CLI Candidate Report Verb

The CLI MUST expose a read-only reporting verb — named distinctly from the
reserved `resolve`/`merge` verbs and shaped like `lint`/`status` — that
renders `find_candidates`' output as a human-readable report to stdout,
performs zero writes, requires no confirmation gate, and exits 0 whether or
not any candidates are found.

#### Scenario: Report renders candidate groups with zero writes

- GIVEN a bundle containing at least one candidate pair
- WHEN the report verb runs
- THEN candidate groups are printed to stdout, the command exits 0, and no
  bundle file is created or modified

#### Scenario: No candidates still exits 0

- GIVEN a bundle with no matching candidates
- WHEN the report verb runs
- THEN it prints a clear "no candidates" report, exits 0, and writes
  nothing

### Requirement: Leading Candidate-Group Tally Line

When `openkos duplicates` finds one or more candidate groups, the report
output MUST include a leading summary line `N candidate group(s) (X exact, Y near)`
as the first line of the report body (following the workspace-banner header and blank line),
where `N` is the total group count, `X` is the count of HIGH-tier groups, `Y` is the count of
LOW-tier groups, and `group(s)` MUST pluralize correctly for `N`. This line
is additive only; existing per-group detail lines are unchanged.

#### Scenario: Single group

- GIVEN a bundle with exactly one HIGH-tier candidate group
- WHEN `duplicates` runs
- THEN the report body begins with `1 candidate group (1 exact, 0 near)`

#### Scenario: Multiple mixed exact/near groups

- GIVEN a bundle with two HIGH-tier and three LOW-tier candidate groups
- WHEN `duplicates` runs
- THEN the report body begins with `5 candidate groups (2 exact, 3 near)`

#### Scenario: All-exact groups

- GIVEN a bundle with three HIGH-tier candidate groups and no LOW-tier groups
- WHEN `duplicates` runs
- THEN the report body begins with `3 candidate groups (3 exact, 0 near)`

#### Scenario: All-near groups

- GIVEN a bundle with two LOW-tier candidate groups and no HIGH-tier groups
- WHEN `duplicates` runs
- THEN the report body begins with `2 candidate groups (0 exact, 2 near)`

### Requirement: One-Time Trigger-Column Legend Line

When at least one candidate group is printed, `duplicates` MUST print
exactly one legend line explaining the `[tier] type -- trigger` columns
(trigger = normalized key for HIGH, similarity ratio for LOW), placed after
the tally and BEFORE the group loop. The legend MUST NOT repeat per group.

#### Scenario: Legend appears once regardless of group count

- GIVEN a bundle with four candidate groups
- WHEN `duplicates` runs
- THEN the legend line appears exactly once in stdout, before the first
  group's detail lines

### Requirement: Trailing Next-Action Hint

When at least one candidate group is printed, the LAST line of `duplicates`
stdout MUST be `Next: openkos merge <survivor> <absorbed>`.

#### Scenario: Hint is the final line

- GIVEN a bundle with at least one candidate group
- WHEN `duplicates` runs
- THEN the last stdout line is `Next: openkos merge <survivor> <absorbed>`

### Requirement: Empty State Stays Single-Line

WHEN `duplicates` finds zero candidate groups, stdout MUST contain ONLY the
existing `"No candidates found."` line — no tally, no legend, and no
`Next:` hint.

#### Scenario: Zero groups print only the existing message

- GIVEN a bundle with no candidate groups
- WHEN `duplicates` runs
- THEN stdout is exactly `"No candidates found."` with no additional lines

### Requirement: Reusable Group-Tally Formatting Helper

The system MUST provide a pure formatting helper, sibling to
`_format_type_tally`, that renders the tally requirement's line from the
per-tier counts (HIGH/exact and LOW/near), reusing `_plural`. Given all-zero
counts it MUST return `""`. Its argument shape is an implementation detail;
only the returned string and the empty-on-zero contract are observable. This
helper MUST NOT be `_format_type_tally` itself (that helper stays
extraction-specific).

#### Scenario: Zero counts yield empty string

- GIVEN the helper is called with all-zero tier counts
- WHEN it runs
- THEN it returns `""`

#### Scenario: Populated counts yield the tally line

- GIVEN the helper is called with counts for HIGH and LOW tiers
- WHEN it runs
- THEN it returns the `N candidate group(s) (X exact, Y near)` line matching
  those counts

### Requirement: Existing Detail Lines Stay Byte-Identical

All per-group detail lines emitted by `duplicates` before this change MUST
remain byte-identical after adding the tally, legend, and hint lines.

#### Scenario: Pre-existing substring assertions still pass

- GIVEN any pre-existing CliRunner test asserting a per-group detail
  substring on `duplicates` output
- WHEN `duplicates` runs after this change
- THEN that substring is still present, unchanged

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
