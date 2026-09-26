# Delta for Status-Aware Retrieval

## MODIFIED Requirements

### Requirement: Deprecated Concepts Excluded By Default

By default, retrieval and candidate-generation paths MUST NOT return, rank,
or surface any concept whose effective status is deprecated. This applies
uniformly to FTS hits, vector hits, graph/PPR hits, the fused list feeding
`answer`, and candidate pairs loaded for adjudication and contradiction
detection.

A deprecated (superseded) concept's ONLY path back into an answer's prompt
is as a history block attached to the hit that supersedes or revises it,
when `revision_history` is enabled (see `query-answer`'s "Revision History
Is Attached To A Retrieved Concept When Requested"). It MUST NEVER be
counted in `fused_count`, `fts_hit_count`, `dense_hit_count`, or any other
hit-count metadata, and it MUST NEVER be rendered or attributed as a
current answer — its history block is always labelled as an earlier,
non-current version.
(Previously: this requirement did not address revision history; a
superseded concept had no retrieval-adjacent path back into the prompt at
all.)

#### Scenario: Deprecated concept absent from a matching query
- GIVEN a deprecated concept whose content matches a question lexically and
  semantically
- WHEN `query`/`answer` runs without `--include-deprecated`
- THEN it is absent from FTS hits, vector hits, graph hits, the fused list,
  and citations

#### Scenario: Superseded concept absent from contradiction candidates
- GIVEN a superseded concept connected to another by a typed graph edge
- WHEN contradiction-detection candidate generation runs
- THEN no candidate pair includes the superseded concept

#### Scenario: Only match is deprecated yields the standard no-match result
- GIVEN the only concept matching a question anywhere (lexically,
  semantically, or via graph proximity) is deprecated
- WHEN `query`/`answer` runs without `--include-deprecated`
- THEN the result is the standard no-match outcome, not an error — this is
  documented, expected behavior

#### Scenario: A superseded concept reaches the prompt only as an attached history block

- GIVEN a superseded concept P, superseded by a retrieved hit S, and
  `revision_history` is enabled
- WHEN `query`/`answer` runs without `--include-deprecated`
- THEN P is absent from FTS hits, vector hits, the fused list, and every
  hit-count field, and P appears in the prompt or in citations, if at all,
  only as a history block attached to S — never as an ordinary retrieval
  hit, and never labelled as current

### Requirement: `--include-deprecated` Escape Flag

Retrieval-facing commands MUST offer an opt-in `--include-deprecated` flag
that restores deprecated and superseded concepts to full participation in
results, identical to a live concept.

WHEN `--include-deprecated` is passed, the revision-history walk (see
`query-answer`'s "Revision History Is Attached To A Retrieved Concept When
Requested") MUST NOT run at all: no history block is attached to any
successor. Under the flag, a formerly deprecated predecessor is admitted as
an ordinary hit, and attaching it again as a history block would carry a
non-current label that contradicts its restored hit status.
(Previously: this requirement did not address revision history's
interaction with the flag.)

#### Scenario: Flag restores a deprecated concept
- GIVEN the only-deprecated-match scenario above
- WHEN `query --include-deprecated` runs
- THEN the concept appears in hits, the fused list, and citations

#### Scenario: Flag is opt-in, not the default
- GIVEN a mixed bundle of live and deprecated concepts
- WHEN `query` runs without any flag
- THEN deprecated concepts are excluded

#### Scenario: History is suppressed under --include-deprecated

- GIVEN `revision_history` is enabled and `--include-deprecated` is passed
- WHEN `query`/`answer` runs
- THEN the revision-history walk does not run at all: no history block is
  attached to any successor, and every predecessor that is now an ordinary
  hit under the flag carries no history label
