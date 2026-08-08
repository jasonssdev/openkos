# Tasks: cross-type-duplicate-candidates (issue #437)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~430 (candidates.py ~115, adjudication.py ~35, okf.py ~6, tests ~275) |
| 400-line budget risk | Low — session review budget is 2000; 430 is well under it |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Cross-type HIGH bucketing + adjudication honesty + survivor-type pin | PR 1 | `uv run pytest tests/unit/resolution/test_candidates.py tests/unit/resolution/test_adjudication.py tests/unit/model/test_okf.py` | `uv run pytest` full suite (ephemeral candidates, no bundle/state migration) | `git revert` restores per-type blocking; already-applied merges stay valid |

## PR — one slice (targets: main)

Satisfies: `specs/entity-resolution/spec.md` MODIFIED "Strict Per-Type
Blocking" and ADDED "Cross-Type Exact-Title Bucketing (HIGH Tier)" /
"`CandidateGroup.member_types` Field"; `specs/entity-resolution-adjudication/
spec.md` ADDED "Cross-Type Prompt Honesty"; `specs/entity-resolution-merge/
spec.md` MODIFIED "Frontmatter-Conflict Resolution".

### Phase 1 — RED (candidates.py)

- [x] 1.1 `test_candidates.py:167` — replace with its inverse: cross-type
  HIGH group, `member_types == ("Concept","Entity")`, `okf_type ==
  "Concept+Entity"`.
- [x] 1.2 New: cross-type exact-title pair forms one HIGH group in BOTH
  `find_candidates` and `find_exact_title_groups` (same test, paired
  assertion).
- [x] 1.3 Extend `:636`/`:660` equivalence fixtures with a cross-type pair;
  update literal `member_ids` lists.
- [x] 1.4 Keep `:1075` prefix test; assert unchanged literal ordering. Keep
  `:688` `near_match_score` spy verbatim; re-verify zero calls on a
  cross-type-only fixture.
- [x] 1.5 New (narrows `:179`'s Strict Per-Type Blocking coverage): explicit
  "Cross-type acronym match produces no candidate" test, separate from the
  existing `:179` same-titles-own-pairs test.
- [x] 1.6 New: ACRONYM/LOW results byte-identical on a fixture with no
  cross-type exact-title overlap.
- [x] 1.7 New: `member_types` length invariant raises `ValueError`; default
  derivation from `okf_type` for a same-type group; index-aligned per-member
  types for a cross-type group.
- [x] 1.8 New: `_type_label` determinism — `("Entity","Concept")` →
  `"Concept+Entity"` regardless of input order.
- [x] 1.9 New: cap-rank tie-break stays stable with a joined `okf_type`
  label — a bundle exceeding `_MAX_CANDIDATE_GROUPS` with a cross-type HIGH
  group returns an identical retained set/order across repeated calls.

### Phase 2 — GREEN (candidates.py)

- [x] 2.1 Split into `_eligible_keyed_docs` (I/O) + pure
  `_keyed_docs_by_type`; add `_high_candidate_groups` bucketing the flat
  list by normalized key.
- [x] 2.2 Wire `find_candidates`/`find_candidates_report` AND
  `find_exact_title_groups` to `_eligible_keyed_docs` → `_high_candidate_groups`
  in the SAME commit — pair structurally so a partial implementation cannot
  land.
- [x] 2.3 Add `member_types` to `CandidateGroup` with `__post_init__` default
  derivation (`object.__setattr__`) and `ValueError` length guard; add
  module-level `_type_label()`.
- [x] 2.4 Dense docstrings on the new/changed functions and `CandidateGroup`
  documenting the flat/partition split and the ephemeral display-label
  contract.
- [x] 2.5 Run Phase 1 suite green.

### Phase 3 — RED (adjudication.py)

- [x] 3.1 `test_adjudication.py`: single-type group prompt bytes unchanged
  (regression pin).
- [x] 3.2 New: cross-type prompt names both types and tags each member
  header — sourced from `member_types_by_id` keyed by concept_id (NOT
  `candidate.okf_type`), because `_build_messages` receives a filtered
  `members` subset and positional alignment would mislabel.
- [x] 3.3 New: blocked-member prompt still tags correctly under the
  concept_id-keyed lookup.
- [x] 3.4 New: verdict schema unchanged for a cross-type group —
  `AdjudicatedCandidate` exposes only `candidate`/`verdict`/`confidence`/
  `rationale`.
- [x] 3.5 New: `--json` payload keys unchanged for a cross-type group —
  exactly `member_ids`/`okf_type`/`tier`/`verdict`/`rationale`, no
  `member_types` key.

### Phase 4 — GREEN (adjudication.py)

- [x] 4.1 `_build_messages` gains `member_types_by_id: Mapping[str, str] |
  None`, built by the caller keyed by concept_id; single-type path stays
  byte-identical; cross-type path adds the joined `OKF TYPE` header, a
  `(OKF type: {t})` suffix per member, and one NOTE turn.
- [x] 4.2 Run Phase 3 suite green.

### Phase 5 — Merge pin (okf.py)

- [x] 5.1 `test_okf.py`: survivor's `type` wins on a cross-type merge;
  absorbed `type` is discarded and never surfaced as a conflict.
- [x] 5.2 `build_merged_document` docstring names `type` explicitly under
  the existing scalar rule (`okf.py:995-997,1045-1047`) — no behavior
  change.
- [x] 5.3 Run Phase 5 test green.

### Phase 6 — Quality gates

- [x] 6.1 `uv run pytest` — full suite green, coverage ≥ 90.
- [x] 6.2 `uv run ruff check . && uv run ruff format --check .`
- [x] 6.3 `uv run mypy .` (whole repo, not just `src/` — CI lints
  everything).
