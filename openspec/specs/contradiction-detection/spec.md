# Contradiction Detection Specification

## Purpose

`resolution/contradiction.py` is a read-only, config-free precision layer
over graph-typed edges: it judges each already-related concept pair via an
injected `LLMBackend` into a `CONTRADICTS` / `CONSISTENT` / `UNCERTAIN`
verdict with confidence, rationale, and cited conflicting claims, surfaced
through a read-only `contradictions` CLI verb. It never writes, merges, or
reconciles; verdicts are advisory, for human review only.

## Non-Goals

This spec does not define: embedding/near-neighbor or stamp-divergence
candidate signals; enhanced or contradiction-inferred staleness (covered by
S1/S2 mechanical/volatility staleness); any write path, auto-reconcile, or
config write (S4); a persisted OKF type for the judgment result; or a seeded
`contradicts` relation type (all typed edges are candidates).

## Requirements

### Requirement: Candidate Generation From Typed Graph Edges, Deduped

`find_contradictions(bundle_dir, *, llm)` MUST derive candidate pairs only
from typed graph edges (`relation_type is not None`) via `build_graph`.
Each unordered pair MUST be deduped by `frozenset({source_id, target_id})`
so symmetric, duplicate, and multi-edge pairs are judged exactly once.
Candidate generation MUST NOT surface edges whose `relation_type ==
"derived_from"`: a `derived_from` relationship is a derivation/provenance
link, never a contradiction candidate. This exclusion applies to EVERY
`derived_from` edge regardless of origin — both graph-projection-synthesized
provenance-mirror edges and any hand-authored `derived_from` entry in
`relations:` frontmatter — since candidate generation has no signal to
distinguish the two, and a derivation is never a contradiction candidate
either way.
(Previously: candidate generation admitted any edge with a non-`None`
`relation_type`, with no type-specific exclusion; this adds an explicit
`derived_from` exclusion so provenance-mirror edges — now typed
`derived_from` by graph projection instead of remaining `None` — do not
newly become contradiction candidates.)

#### Scenario: Symmetric and multi-edge pairs judged once

- GIVEN two concepts connected by both `A --relation1--> B` and
  `B --relation2--> A`
- WHEN `find_contradictions` runs
- THEN exactly one judgment is produced for the pair, not two

#### Scenario: Provenance-only bundle yields zero contradiction candidates

- GIVEN a bundle whose only typed edges are provenance-mirror edges typed
  `derived_from` by graph projection (concept-to-source links backed by
  `provenance:` frontmatter membership)
- WHEN `find_contradictions` runs
- THEN zero candidate pairs are generated, no concept-to-source pair is
  judged, and no LLM call is made — matching prior behavior when those rows
  were untyped and already excluded

#### Scenario: Genuine typed contradiction-eligible edge is still surfaced

- GIVEN a bundle with two event concepts connected by a `related_to`-typed
  edge (not `derived_from`)
- WHEN `find_contradictions` runs
- THEN that pair is included in the candidate set and judged, confirming
  the exclusion applies only to `derived_from`, not to all typed edges

### Requirement: Per-Pair Verdict Shape With Cited Claims

Each judgment MUST carry `verdict` (`CONTRADICTS`/`CONSISTENT`/`UNCERTAIN`),
`confidence: float`, `rationale: str`, and `conflicting_claims` cited from
the pair's content.

#### Scenario: CONTRADICTS with cited claims

- GIVEN a fake backend returning `CONTRADICTS`, confidence `0.9`, and
  non-empty `conflicting_claims`
- WHEN the pair is judged
- THEN the result carries that verdict, confidence, and cited claims

### Requirement: Citation-Gated Precision

A `CONTRADICTS` verdict WITHOUT non-empty `conflicting_claims` MUST degrade
to `UNCERTAIN`.

#### Scenario: Uncited CONTRADICTS degrades

- GIVEN a fake backend returning `CONTRADICTS` with empty `conflicting_claims`
- WHEN the pair is judged
- THEN the result is `UNCERTAIN`, not `CONTRADICTS`

### Requirement: Fail-Closed Reply Parsing And Confidence Coercion

An unparseable, non-object, or invalid reply MUST degrade that pair to
`UNCERTAIN` without raising; the run MUST continue for remaining pairs. An
unrecognized verdict string MUST map to `UNCERTAIN`. Confidence MUST be
clamped to `[0.0, 1.0]`; `NaN`, `Inf`, or boolean values MUST coerce to
`0.0`.

#### Scenario: Malformed reply degrades one pair only

- GIVEN one pair's backend reply is non-JSON and another's is valid
- WHEN `find_contradictions` runs
- THEN only the malformed pair degrades to `UNCERTAIN`; the valid pair's
  result is unaffected and neither raises

### Requirement: Pair Cap With Explicit Truncation Notice

Candidate pairs MUST be capped at a fixed maximum. When the cap truncates
the candidate set, the report MUST state this explicitly — never silently.

#### Scenario: Cap truncation is reported

- GIVEN a graph whose deduped pair count exceeds the cap
- WHEN `contradictions` runs
- THEN only the capped subset is judged and the report states truncation
  occurred

### Requirement: Read-Only `contradictions` CLI Verb, High-Confidence Default

The CLI MUST expose a `contradictions` verb gating on `require_workspace`,
building `OllamaClient` and injecting it into `find_contradictions`,
performing zero bundle writes. Its only persistence is the findings store
under `.openkos/` (#653) — the same "persisting a finding is not a bundle
write" carve-out `curate`'s Contradictions stage holds. By default it MUST
display only `CONTRADICTS` verdicts above the confidence threshold;
`CONSISTENT` and `UNCERTAIN` MUST be hidden.

#### Scenario: Default view hides CONSISTENT/UNCERTAIN, zero writes

- GIVEN a bundle whose pairs judge to a mix of verdicts
- WHEN `contradictions` runs
- THEN only high-confidence `CONTRADICTS` verdicts print, no bundle file is
  created or modified

### Requirement: Persisted Findings Are Served Before Re-Judging

A default `contradictions` run MUST serve a candidate pair from the
persisted findings store instead of re-judging it iff the pair's LATEST
persisted finding's input digests exactly match the digests computed from
the pair's current bytes by the same function that recorded them.
`consistent` findings serve identically — they are what proves a pair
needs no re-judging. Any other candidate — no persisted row, digest
drift, an unreadable input, or an unrecognized stored verdict — MUST be
re-judged, and every freshly judged verdict MUST be persisted through the
same write path `curate` uses, cited claims included, so a served
`CONTRADICTS` renders indistinguishably from a fresh one. The run MUST
report how many candidates were served versus judged. A `--fresh` flag
MUST bypass serving and re-judge every candidate.

#### Scenario: Digest-fresh finding serves without a model call

- GIVEN a candidate pair whose persisted finding's digests match its
  current bytes
- WHEN `contradictions` runs without `--fresh`
- THEN the stored verdict prints (claims included) and `llm.chat` is never
  called for that pair

#### Scenario: Stale pair re-judges and re-persists

- GIVEN a persisted finding whose stored digest no longer matches the
  pair's current bytes
- WHEN `contradictions` runs
- THEN that pair is judged with one model call and the fresh verdict is
  persisted, so the next default run serves it

#### Scenario: `--fresh` re-judges everything

- GIVEN digest-fresh persisted findings for every candidate
- WHEN `contradictions --fresh` runs
- THEN every candidate is judged with a model call

### Requirement: `--all` Reveals Every Verdict

The `contradictions` verb MAY accept `--all` to display every verdict
regardless of type or confidence. This flag MUST NOT change which pairs
are judged: it is a display-only filter over the served-plus-judged
verdict list.

#### Scenario: `--all` shows CONSISTENT and UNCERTAIN too

- GIVEN the same mixed-verdict bundle
- WHEN `contradictions --all` runs
- THEN `CONSISTENT` and `UNCERTAIN` verdicts also print

### Requirement: Degrade-On-No-Model Mirrors `adjudicate`'s 3-Tier Catch

The verb MUST report each of `OllamaUnavailable`, `OllamaModelNotFound`,
and generic `OllamaError` (checked in that order) with an actionable
message, write nothing, and exit non-zero — mirroring `adjudicate`'s
degrade contract. Since #441, a mid-loop failure from `llm.chat` reaches
the verb as `ContradictionBatch.failure` (with the completed verdicts
preserved and reported first), not as a raise; the 3-tier catch around the
call itself remains only for a failure raised outside the guarded chat
seam.

#### Scenario: Each tier degrades cleanly with zero writes

- GIVEN `find_contradictions` returns a batch whose `failure` is one of the
  three `OllamaError` tiers (or, for a failure outside the guarded chat
  seam, raises one)
- WHEN `contradictions` runs
- THEN the completed verdicts (if any) report first, the matching message
  prints, no bundle write occurs, and the process exits non-zero

### Requirement: Empty Graph Yields Clear Message, No Crash

WHEN `contradictions` finds zero candidate pairs, it MUST distinguish
three mutually exclusive states rather than a single generic message: (1)
the graph has no typed edges at all — nothing to work with yet; (2) typed
edges exist but none survive candidate-pair generation (e.g. all excluded
as `derived_from`) — candidates existed but none matched; (3) candidates
are not computable yet because embeddings are missing (`vectors.db` absent
or empty), which additionally starves any embedding-sourced candidate
edges. State 3 MUST use a message distinguishable from state 1. Every
state MUST exit `0`, never crash.
(Previously: any zero-candidate-pairs outcome produced the same single "no
candidate pairs" message regardless of cause.)

#### Scenario: No typed edges at all

- GIVEN a bundle whose graph has no typed edges and `vectors.db` is
  present and populated
- WHEN `contradictions` runs
- THEN it prints a message stating the graph has no typed edges yet,
  distinct from the other two states, and exits `0`

#### Scenario: Typed edges exist but none survive candidate-pair generation

- GIVEN a bundle whose graph has typed edges, all excluded from candidate
  generation (e.g. all `derived_from`)
- WHEN `contradictions` runs
- THEN it prints a message stating no candidate pairs remain after
  filtering, distinct from the empty-graph message, and exits `0`

#### Scenario: Missing embeddings reports not-computable-yet

- GIVEN a bundle whose `vectors.db` is absent or empty
- WHEN `contradictions` runs
- THEN it prints a message stating candidates are not computable yet due
  to missing embeddings, distinct from both other messages, and exits `0`

### Requirement: Deterministic Candidate Pair Ordering

Given a fixed bundle, the candidate pair set and its order MUST be
deterministic (sorted by pair key).

#### Scenario: Repeated runs yield the same pair order

- GIVEN the same bundle and the same fake backend replies
- WHEN `find_contradictions` runs twice
- THEN both runs produce candidate pairs in the same order with equal
  results

### Requirement: Merged-Body Candidate Source Relocates Without Changing Verdict Semantics

`_merged_body_candidates` MUST read merge-ledger entries from each
survivor's `bundle/.state/ledger/` sidecar rather than from the survivor's
own `merged_from` frontmatter. This is a source relocation only: the set
of merged-body candidate pairs generated, the per-entry
`sensitivity.merged_content_blocked` gating applied to each (once per
entry, never once per survivor — see `sensitivity-aware-llm`), and the
resulting `CONTRADICTS`/`CONSISTENT`/`UNCERTAIN` verdicts MUST be
unaffected by the relocation for any fixed bundle state.

#### Scenario: Same bundle state yields the same candidates and verdicts before and after relocation

- GIVEN a bundle whose merge ledger entries are read from
  `bundle/.state/ledger/` instead of survivor frontmatter, but whose
  content is otherwise identical to a pre-relocation bundle
- WHEN `find_contradictions` runs
- THEN it produces the same merged-body candidate pairs, in the same
  order, judged to the same verdicts as it would have against the
  pre-relocation frontmatter-embedded ledger

#### Scenario: A sensitivity-blocked entry stays blocked after relocation

- GIVEN a ledger sidecar entry that `merged_content_blocked` would exclude
  under the per-entry gate
- WHEN `_merged_body_candidates` reads it from `bundle/.state/ledger/`
- THEN it is still excluded from the judged candidate set, identically to
  the pre-relocation behavior
