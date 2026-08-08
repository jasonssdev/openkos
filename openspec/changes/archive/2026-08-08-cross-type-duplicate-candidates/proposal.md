# Proposal: Exact-title duplicates are visible across OKF types

**Issue**: [#437](https://github.com/jasonssdev/openkos/issues/437) (split from
closed #427; former dependency #379 closed). **Baseline**: `main` @ `bfe9f3d`.
**Mode**: hybrid (OpenSpec + Engram).

## Intent

A duplicate whose two sources were classified into different OKF types is
invisible to curation. `_keyed_docs_by_type` (candidates.py:208-246) partitions
by `type` *before* any pairing runs, so a `Concept` and an `Entity` with the
same normalized title are never compared — pinned by
`test_cross_type_identical_normalized_title_produces_no_candidate`
(test_candidates.py:167). Classification is a per-source judgment call; one
close call should not permanently hide a real duplicate.

## Decision

**Cross-type matching applies to the HIGH (exact-title) tier only.** A single
bucket keyed by normalized title across all types stays O(n) with no pairwise
work — the same shape `find_exact_title_groups` already contracts. ACRONYM and
LOW keep per-type partitioning: their cross-type cost would go from O(n²)
per-type to O(N²) whole-corpus and is unmeasured against #379's 309-call bound.

## Scope

### In scope

1. HIGH-tier bucketing keyed by normalized title across types, in
   `find_candidates`/`find_candidates_report` **and** `find_exact_title_groups`
   together (equivalence tests at test_candidates.py:636, :660, :1075).
2. `CandidateGroup.member_types: tuple[str, ...]`, index-aligned with
   `member_ids`; defaults to `(okf_type,) * len(member_ids)` via `__post_init__`
   so the 76 existing construction sites stay valid and the field is never
   empty. `okf_type` stays the display scalar: the shared type, or for a
   cross-type group the distinct types sorted and joined (`"Concept+Entity"`) —
   `_cap_rank_key`'s tie-break needs only a stable string.
3. Adjudication prompt honesty: single-type groups keep today's exact prompt
   bytes; a cross-type group names both types and tags each member header with
   its own type. The verdict schema (`verdict`/`confidence`/`rationale`) and the
   `--json` payload keys are unchanged.
4. Survivor's `type` wins on a cross-type merge — today's implicit
   `build_merged_document` scalar behavior (okf.py:1018,1045), made explicit in
   spec, docstring, and a pinning test.
5. Replace test_candidates.py:167 with its inverse.

### Out of scope

- Cross-type ACRONYM/LOW (needs a fresh cost measurement first).
- Embedding-proximity tier (#437 excludes it).
- Adjudicator-returned type — defensible fast-follow, not this slice.
- Reading `type_alternative`: covers a narrow subset and would be the first
  break of the documented "nothing reads `type_alternative`" invariant
  (docs/knowledge-object-model.md:210).
- Any cross-source synthesis step. A cross-type group merges only through an
  LLM `SAME` verdict and an ordinary 2-member merge (rejected in #427).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `entity-resolution`: "Strict Per-Type Blocking" narrows to ACRONYM/LOW;
  exact-title groups may span types and carry `member_types`. Cap, ranking, and
  `_MAX_CANDIDATE_GROUPS` unchanged.
- `entity-resolution-adjudication`: prompt represents a cross-type group's
  types honestly; verdict and JSON contracts unchanged.
- `entity-resolution-merge`: scalar `type` explicitly resolves to the
  survivor's on a cross-type merge.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `resolution/candidates.py` | Modified | HIGH bucketing, `member_types`, docstrings |
| `resolution/adjudication.py` | Modified | `_build_messages` type rendering |
| `model/okf.py` | Modified | Document survivor-type resolution |
| `tests/unit/resolution/` | Modified | Replace :167; extend equivalence tests |

`cli/next_action.py`, `cli/curate.py`, `resolution/__init__.py` need zero
changes (verified: no `.okf_type` read).

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| One entry point changed without the other | Med | Equivalence tests fail loudly; task order pairs them |
| `member_types` drifts from `okf_type` | Med | `__post_init__` derives it; invariant test |
| Cross-type groups crowd real duplicates out of the 50-group cap | Low | Same `groups` list before `_cap_rank_key`; truncation notice already surfaces it |
| Joined `okf_type` label misread as a real OKF type | Low | Ephemeral display only, never persisted; spec + docstring say so |

## Rollback Plan

`git revert` of the PR. Nothing is written to `bundle/` or `state/` —
candidates are ephemeral — so a revert only restores per-type blocking. Merges
already applied stay valid: the survivor-type rule is today's behavior made
explicit, not new behavior.

## Success Criteria

- [ ] A same-title `Concept`/`Entity` pair is returned as one HIGH group by
      both `find_candidates` and `find_exact_title_groups`.
- [ ] `find_exact_title_groups` still makes zero `near_match_score` calls and
      still equals the HIGH slice in order.
- [ ] ACRONYM/LOW results are byte-identical to pre-change on a fixture with no
      cross-type exact-title overlap.
- [ ] A cross-type prompt names both types; the verdict schema is unchanged.
- [ ] Merging a cross-type SAME pair keeps the survivor's `type`, under test.
- [ ] `uv run pytest`, `ruff`, `mypy .` green; coverage ≥ 90.
