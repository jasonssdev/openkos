# Proposal: Sensitivity Fail-Closed Filter (Gap #8 · S3)

## Intent

The `sensitivity` frontmatter field is inert: written by `okf.build_source_concept`
(L124) and `build_concept` (L194) with the workspace floor `"private"`
(config.py:34), but its only reader today is merge's high-water-mark recompute
(ADR-0003). No verb or retrieval path gates on it — exactly parallel to S1's
inert `status`. S3 is the HARD GATE of the Gap #8 arc: a fail-closed filter that
prevents `sensitivity: confidential` concepts from ever reaching `llm.chat`
across all **6** call sites (adjudicate, contradictions, suggest-relations,
suggest-volatility, query, extract). All `llm.chat` is local Ollama today, so
this is preventive enforcement that MUST land before any cloud-backend or export
slice.

## Scope

### In Scope (S3 only)
- Shared fail-closed predicate `sensitive_concept_ids(bundle_dir, *, threshold)`
  (one `okf._iter_docs` walk, per-doc fail-safe), built on the `okf._rank`
  primitive (okf.py:207-228).
- Wire the filter at all 6 llm.chat sites; reuse `lifecycle.filter_hits` at the
  4 S1-pattern seams (query, contradictions, adjudicate, suggest-relations).
- Thread sensitivity into `lint.LintDoc` (dropped today, L113-123) for
  suggest-volatility; gate `extract` on `cfg.default_sensitivity`; defense-in-depth
  post-filter in `_assemble_context`.
- `--include-confidential` escape mirroring S1's `--include-deprecated`.
- Hygiene fold-in (separate slice S3c): #1606 dedupe 4 cloned JSON object-parsers
  → `openkos/llm/parsing.py`; #1592 add `TypeError` to config.py `read_config`
  except mapping (L373-376).

### Out of Scope / Non-Goals
- New config key — `max_send_sensitivity` rejected; threshold is confidential-only.
- Per-source `ingest --sensitivity` input (its own future slice).
- Redaction — we **exclude**, not redact (mirror S1 lifecycle pattern).
- S4 export exclusion.

### Locked Decisions (user sign-off, do not relitigate)
- **Threshold = CONFIDENTIAL-ONLY.** Block only `sensitivity: confidential` plus
  fail-closed fallbacks (missing / malformed / unreadable / unknown-type →
  confidential → blocked). `private` + `public` are sent. Rationale: default floor
  is `private`, so any `>= private` gate blocks essentially every doc.
- **Extract seam = gate on `cfg.default_sensitivity`.** extract runs on raw source
  pre-bundle (extraction/concept.py:282) with no per-doc sensitivity; if the
  workspace floor is confidential, extract skips `llm.chat` (fail-closed).

## Capabilities

### New Capabilities
- `sensitivity-aware-llm`: the fail-closed invariant governing which concepts may
  reach `llm.chat` — the `sensitive_concept_ids` predicate, confidential-only
  threshold with fail-closed fallbacks, `--include-confidential` escape, extract
  pre-bundle gate, and `_assemble_context` defense-in-depth.

### Modified Capabilities
- None at the spec-requirement level. The 6 verbs inherit the new cross-cutting
  invariant without changing their own contracts (mirrors S1, which left verb
  specs untouched). #1606/#1592 are implementation-level (refactor + bugfix),
  not spec deltas.

**New-vs-modified justification**: like S1's `status-aware-retrieval`, S3 is a
single cross-cutting lifecycle invariant spanning multiple verbs. Documenting it
once as one new capability is cleaner than fragmenting identical REQUIREMENTS
across 6 existing verb specs, and keeps a future `sensitivity-aware-export` (S4)
cleanly separable.

## Approach

Clone the S1 `lifecycle.py` shape: a shared fail-closed predicate resolves each
concept's sensitivity value via `okf._rank` and returns the confidential (+
fallback) id set. The 4 S1-pattern seams already hold sensitivity-bearing
metadata and reuse `filter_hits`. The 2 divergent seams need small plumbing —
add a sensitivity field to `LintDoc` for volatility, gate extract on the
workspace floor. `_assemble_context` adds a redundant post-filter for
defense-in-depth. `--include-confidential` computes the predicate but passes
through, at zero cost when absent.

## Slicing (feature-branch-chain, 800-line budget)
- **S3a spine**: `sensitive_concept_ids` predicate + wire the 4 S1-pattern seams
  (query / contradictions / adjudicate / suggest-relations) + `--include-confidential`.
- **S3b seams**: suggest-volatility (thread sensitivity into `LintDoc`), extract
  (gate on `cfg.default_sensitivity`), `_assemble_context` defense-in-depth.
- **S3c hygiene**: #1606 dedupe → `openkos/llm/parsing.py`; #1592 config
  `TypeError` → `ValueError`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `bundle/lifecycle.py` (or new leaf) | New | `sensitive_concept_ids` predicate; reuse `filter_hits` |
| `retrieval/answer.py` | Modified | query filter + `_assemble_context` defense-in-depth |
| `resolution/{adjudication,contradiction,edge_typing}.py` | Modified | filter candidate ids/edges/members |
| `resolution/volatility_typing.py`, `lint.py` | Modified | `LintDoc` sensitivity field + volatility gate |
| `extraction/concept.py` | Modified | gate `extract` on `cfg.default_sensitivity` |
| `openkos/llm/parsing.py` | New | shared JSON parsers (#1606) |
| `config.py` | Modified | `read_config` `TypeError` mapping (#1592) |

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Over-block breaks the pipeline | Low | Confidential-only threshold; fail-closed only on genuinely unreadable/unknown |
| A missed llm.chat site leaks confidential | Med | Spine wires 4 uniformly; S3b covers the 2 outliers; `_assemble_context` defense-in-depth |
| Divergent seams drift from shared predicate | Med | Single `_rank` primitive; extract reuses `_rank` on the floor |
| extract fully skipped when floor=confidential | Low | Intended fail-closed; documented; user can lower the floor |
| Empty query results when only confidential matches | Low | `--include-confidential` escape |

## Rollback Plan

Additive read-side filter. Removing it (or defaulting the predicate to an empty
set) restores today's sensitivity-blind sends with no data migration —
`sensitivity` stays written as before.

## Dependencies

- S1 `lifecycle.filter_hits` (shipped) — reused, not modified.
- `okf._rank` sensitivity primitive (shipped) — consumed.

## Success Criteria

- [ ] A `sensitivity: confidential` concept is never sent to `llm.chat` at any of the 6 sites.
- [ ] Missing / malformed / unreadable / unknown-type sensitivity → treated confidential → blocked (fail-closed).
- [ ] `private` + `public` concepts are sent unchanged.
- [ ] `extract` skips `llm.chat` when `cfg.default_sensitivity` is confidential.
- [ ] `--include-confidential` bypasses the filter for query/verbs.
- [ ] #1592: a YAML mapping with an unhashable complex key raises `ValueError`, not uncaught `TypeError`.

## Arc Note

S3 of MVP-3 gap #8, the retrieval/lifecycle arc (S1 status-aware retrieval → S2
reference-aware forget → **S3 sensitivity fail-closed** → S4 export exclusion).
S3 is the **HARD GATE**: it MUST land before any cloud-backend or export slice
(incl. S4). No cloud backend or export verb exists in code today, so this is
preventive enforcement.
