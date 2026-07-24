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
and MUST NOT produce a candidate between objects of different types,
regardless of title similarity.

#### Scenario: Cross-type similar titles produce no candidate

- GIVEN a Concept and an Entity whose titles normalize identically
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
