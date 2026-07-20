---
type: Decision
title: "ADR-0003: Sensitivity high-water-mark ordering and fail-closed combine"
description: How a derived object's sensitivity is recomputed when sources combine.
status: Proposed
date: 2026-07-20
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-20T00:00:00Z
sensitivity: public
---

# ADR-0003: Sensitivity high-water-mark ordering and fail-closed combine

- **Status:** Proposed
- **Date:** 2026-07-20

## Context

KOM (`docs/knowledge-object-model.md:255-272`) states a derived object is "at
least as sensitive as the most sensitive source" — a synthesis of a
confidential and a public source is confidential. A repo-wide scan finds
**zero implementation**: sensitivity today is only inherited verbatim from
config `default_sensitivity` or a Source (`cli/main.py:318,542`). The merge
slice is the first operation that combines two objects' sensitivity, so it
must be the first real implementation. Sensitivity is a security-relevant
field; values read from frontmatter may be missing or malformed (hand-edited,
LLM-derived, wrong type).

## Decision

We define `SENSITIVITY_ORDER = ("public", "private", "confidential")` and a
pure `combine_sensitivity(a, b) -> str` in `model/okf.py` (alongside
`build_concept`). It returns `SENSITIVITY_ORDER[max(rank(a), rank(b))]` —
always the more restrictive side. Ranking **fails closed**: a missing or
blank value ranks as `private` (the config default floor); a present but
unrecognized or non-string value ranks as `confidential` (the most
restrictive level). The result is always a canonical member of the ordering.
Merge invokes it at build time so the survivor's sensitivity is **recomputed,
never copied** from either input, and records `sensitivity_before` in the
merge ledger (ADR-0002) so the lossy recompute stays reversible.

## Consequences

Easier: a single, tested source of truth for combining sensitivity that other
future derivations (synthesis, compilation) can reuse; safe-by-default
handling of dirty frontmatter. Harder: the ordering and fail-closed rules
become a load-bearing policy other tooling will rely on; because the recompute
is lossy, any reversal must depend on the ledger snapshot, not re-derivation.
Requires dedicated tests for every pair plus the missing/malformed/non-string
edges.

## Alternatives considered

- **Survivor-wins sensitivity** (like other scalar conflicts): rejected — it
  can silently downgrade a confidential absorbed object into a public
  survivor, violating the KOM high-water-mark and the sensitivity principle.
- **Fail-open on unknown values** (treat as public/private-min): rejected —
  a security field must fail toward more restrictive, never less.
- **No ordering constant, ad-hoc comparison at the call site**: rejected —
  scatters a security rule and blocks reuse by future derivations.
