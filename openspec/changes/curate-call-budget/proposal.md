# Proposal: Bound the Identity Stage's Call Budget

**Issue**: #382. **Unblocks**: #379 (P0) criterion 3 — "bounded cost: LLM calls grow
sub-quadratically as the corpus doubles".

## Intent

`resolution/candidates.py::find_candidates` is the one uncapped, genuinely
quadratic stage in `curate`: every `CandidateGroup` costs one adjudication call.
A 15-source run produced ~150 sequential calls in 17m19s, filled Ollama's prompt
cache (8166/8192 MiB) and left the machine swapping. #378 capped only Structure;
the quadratic stage is untouched, so #382 is partially open, not superseded.

## Scope

### In Scope

- Module-level `_MAX_CANDIDATE_GROUPS: Final[int]` in `resolution/candidates.py`,
  applied to a **ranked** group set (HIGH before LOW, LOW by `near_match_score`
  descending) **before any LLM call**.
- `produced` / `retained` reporting mirroring `CandidateReport`; the Identity
  cost line renders "N of M shown (cap reached)". Truncation is never silent.
- Test updates for the pinned cost-line literals.

### Out of Scope (named follow-ups)

- **Unified per-stage budget surface** (`--limit` flag + `config.Config` knob) —
  follow-up issue. Every existing cap (`_MAX_CANDIDATE_EDGES`, `_MAX_PAIRS`,
  `_MAX_OBJECTS_PER_SOURCE`) is a hardcoded `Final[int]` with no knob; adding one
  knob for one stage would invent an inconsistent surface.
- **Wall-clock estimate ("74 calls ≈ four minutes")** — follow-up issue. No timing
  instrumentation exists anywhere in `src/`; a constant would be fabricated.
- **Persisted checkpoint / resume.** Accepted items shrink the pool next run, so
  ranked truncation already yields incremental resumption for 3 of 4 stages.

Deferring is safe: the cap alone makes call count O(1)-bounded, which is exactly
what #379 criterion 3 asserts. The deferred items improve UX, not boundedness.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `entity-resolution`: `find_candidates` MUST rank and cap returned groups, and
  MUST report pre-cap vs. post-cap counts.
- `curate-command`: the Identity cost line MUST disclose truncation.

## Approach

Extend the house cap idiom rather than invent a second one. Rank, cap, report —
same shape as `sqlite_graph._MAX_CANDIDATE_EDGES`. Read-only determinism of
`find_candidates` is preserved; no layer boundary is crossed; `engine.py` untouched.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/resolution/candidates.py` | Modified | Cap + ranking + report |
| `src/openkos/cli/curate.py` | Modified | Identity cost line |
| `tests/unit/resolution/test_candidates.py` | Modified | Cap behavior |
| `tests/unit/cli/test_curate.py` | Modified | Pinned literals |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Cap hides real merges | Med | Ranked, never silent; re-runs surface the remainder |
| Cost-line literal churn | High | Called out as a test-visible contract change |
| Cap value guessed wrong | Med | Single `Final[int]`, trivially retunable |

## Rollback Plan

Revert the commit; the cap is one constant and one slice. No migration, no
persisted state, no schema change.

## Dependencies

None. #378's cap patterns are already merged.

## Success Criteria

- [ ] Identity call count is bounded by a constant regardless of corpus size.
- [ ] Doubling the corpus does not double Identity calls once the cap binds.
- [ ] Truncation is disclosed in the cost line; `produced == retained` when unbound.
- [ ] `uv run pytest` green; ruff and mypy strict clean.
