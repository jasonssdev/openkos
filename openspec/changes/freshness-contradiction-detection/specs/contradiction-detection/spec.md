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

#### Scenario: Symmetric and multi-edge pairs judged once

- GIVEN two concepts connected by both `A --relation1--> B` and
  `B --relation2--> A`
- WHEN `find_contradictions` runs
- THEN exactly one judgment is produced for the pair, not two

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

The CLI MUST expose a read-only `contradictions` verb gating on
`require_workspace`, building `OllamaClient` and injecting it into
`find_contradictions`, performing zero bundle writes. By default it MUST
display only `CONTRADICTS` verdicts above the confidence threshold;
`CONSISTENT` and `UNCERTAIN` MUST be hidden.

#### Scenario: Default view hides CONSISTENT/UNCERTAIN, zero writes

- GIVEN a bundle whose pairs judge to a mix of verdicts
- WHEN `contradictions` runs
- THEN only high-confidence `CONTRADICTS` verdicts print, no bundle file is
  created or modified

### Requirement: `--all` Reveals Every Verdict

The `contradictions` verb MAY accept `--all` to display every verdict
regardless of type or confidence. This flag MUST NOT affect
`find_contradictions`, which always judges every pair.

#### Scenario: `--all` shows CONSISTENT and UNCERTAIN too

- GIVEN the same mixed-verdict bundle
- WHEN `contradictions --all` runs
- THEN `CONSISTENT` and `UNCERTAIN` verdicts also print

### Requirement: Degrade-On-No-Model Mirrors `adjudicate`'s 3-Tier Catch

The verb MUST catch `OllamaUnavailable`, then `OllamaModelNotFound`, then
generic `OllamaError` (in that order), report an actionable message, write
nothing, and exit non-zero — mirroring `adjudicate`'s degrade contract.

#### Scenario: Each tier degrades cleanly with zero writes

- GIVEN `find_contradictions` raises one of the three `OllamaError` tiers
- WHEN `contradictions` runs
- THEN the matching message prints, no bundle write occurs, and the process
  exits non-zero

### Requirement: Empty Graph Yields Clear Message, No Crash

A graph with no typed edges MUST produce a clear "no candidate pairs"
message and exit `0`, never a crash.

#### Scenario: No typed edges

- GIVEN a bundle whose graph has no typed edges
- WHEN `contradictions` runs
- THEN it prints a clear no-candidates message and exits `0`

### Requirement: Deterministic Candidate Pair Ordering

Given a fixed bundle, the candidate pair set and its order MUST be
deterministic (sorted by pair key).

#### Scenario: Repeated runs yield the same pair order

- GIVEN the same bundle and the same fake backend replies
- WHEN `find_contradictions` runs twice
- THEN both runs produce candidate pairs in the same order with equal
  results
