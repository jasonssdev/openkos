# Apply progress: revises-relation

## Slice 1 (PR 1 → `main`): DONE

All tasks 1.1–1.20 in `tasks.md` are complete and checked off. Strict TDD
followed throughout: every `[TEST]` task was observed RED (for the reason
`tasks.md`/`design.md` predicted) before its paired `[IMPL]` task landed, and
every mutation `tasks.md`/`design.md` name was applied, observed to kill the
targeted test, and reverted by inverse edit (never `git checkout --`), with
`__pycache__` purged before each verdict.

### What changed

- `src/openkos/model/relations.py`: added `RESOLUTION_RELATION_TYPES`
  (`frozenset({"supersedes", "reconciled_with", "revises"})`), outside
  `REGISTRY`/`SEEDED_RELATION_TYPES`/`SUGGESTABLE_RELATION_TYPES`.
- `src/openkos/cli/main.py`:
  - `_ReconcileRole` extended with `"revises"`/`"revised"`; `_reconcile_sentence`
    gained both branches.
  - `_existing_reconciliation_state` rewritten as one table-driven pass
    (`_MODE_BY_RESOLUTION_TYPE`) collecting `(mode, holder)` pairs; returns
    `"mixed"` when a hand-edited pair carries disagreeing resolutions.
    `_reconciliation_state_description` gained `revision`/`mixed` wording.
  - New `_resolve_pair_member` helper, shared by `--winner` and the new
    `--revision <id>` option; new flag-conflict gate (`--winner` +
    `--revision` refuses, "mutually exclusive"); extended `--from-findings`
    combination gate to name `--revision`.
  - `_reconcile_pair` renamed `winner_canonical/loser_canonical` to
    `holder_canonical/target_canonical`, added keyword-only
    `edge_type: Literal["supersedes", "revises"] = "supersedes"`; new
    `_DIRECTED_ROLES` dict drives one directed edge/role branch instead of
    two hand-written `elif`s. Preview gained a direction line for both
    directed modes; log line, success echo, and commit message each gained a
    `revises` branch alongside the unchanged `supersedes`/symmetric ones.
    `_run_reconcile_from_findings`'s call site is byte-unchanged (positional
    `None, None`, default `edge_type`).
- `docs/cli.md`: `reconcile` section rewritten for three shapes, a
  `--revision` flag row, the extended idempotency/mixed-refusal sentence, and
  the `--from-findings` exclusion list.
- `docs/adr/0024-revises-relation-and-resolved-pairs.md` +
  `docs/adr/README.md` index row: staged as-is (written in the design phase,
  status `Proposed`), no edits.
- Tests: `tests/unit/model/test_relations.py` (+1 test function),
  `tests/unit/cli/test_reconcile.py` (37 → 74 collected tests; new test
  functions plus the 18-cell transition-matrix and 5-case mixed-state
  parametrizations), `tests/unit/test_lifecycle.py` (+1, non-RED pin),
  `tests/unit/bundle/test_listing.py` (+1, non-RED pin).
- `lifecycle.py` and `bundle/listing.py`: **not modified**, per design — both
  already special-case only the literal `"supersedes"` string, confirmed by
  running the new pin tests against the unmodified tree before any other
  change (both passed immediately, as design predicted).

### Mutations applied and reverted (strict TDD)

1. Removed the `len(found) == 1` / `"mixed"` branch in
   `_existing_reconciliation_state` → 5 `test_mixed_resolution_state_*`
   parametrizations failed on the message assertion (still exited 1, wrong
   stderr) — confirms the message assertion, not just the exit code, is
   load-bearing. Reverted; suite green.
2. Dropped the `existing_holder != holder_canonical` half of the refuse
   predicate (holder-blind comparison) →
   `test_reconciliation_transition_matrix[directional(B)-directional(A)]` and
   `[revision(B)-revision(A)]` failed (wrongly treated as idempotent).
   Reverted; suite green.
3. Collapsed `found.add((mode, None if mode == "symmetric" else
   canonical_x))` to always append `None` (revision/directional holder lost)
   → `test_winner_reconcile_idempotent_rerun`, two transition-matrix cells,
   and two mixed-state parametrizations failed. Reverted; suite green.
4. Swapped `_DIRECTED_ROLES["revises"]` to `("revised", "revises")` →
   `test_revision_writes_a_single_outbound_edge_and_notes` failed (anchor
   roles swapped). Reverted; suite green.

### Verification (foreground, unpiped)

- `uv run ruff check .` — **All checks passed!**
- `uv run ruff format --check .` — clean after `ruff format` was applied to
  the touched files (`src/openkos/cli/main.py`,
  `src/openkos/model/relations.py`, `tests/unit/cli/test_reconcile.py`,
  `tests/unit/model/test_relations.py`, `tests/unit/test_lifecycle.py`,
  `tests/unit/bundle/test_listing.py`).
- `uv run mypy .` — **Success: no issues found in 313 source files.**
- `uv run pytest` (full, unpiped) — **6470 passed, 2 skipped** (pre-existing
  platform-gated symlink/hardlink skips, unrelated to this change).

### Commits (feature branch `feat/1014-revision-detection`)

1. `ec18308` — `feat(model): add RESOLUTION_RELATION_TYPES constant`
   (`src/openkos/model/relations.py`, `tests/unit/model/test_relations.py`)
2. `c524451` — `feat(cli): add reconcile --revision for refinement resolutions`
   (`src/openkos/cli/main.py`, `tests/unit/cli/test_reconcile.py`,
   `tests/unit/test_lifecycle.py`, `tests/unit/bundle/test_listing.py`)
3. `eb95a5f` — `docs(sdd): document reconcile --revision and accept ADR-0024`
   (`docs/cli.md`, `docs/adr/0024-revises-relation-and-resolved-pairs.md`,
   `docs/adr/README.md`)

No `openspec/` paths were staged or committed (`tasks.md`'s checkboxes are a
working-tree-only update, per the orchestrator's store instructions).

### Deviations from tasks.md's literal wording

- **1.8** (`test_revision_flag_refuses_when_id_is_not_in_the_pair`): the task
  text implied all three parametrized bad values (non-member id,
  `sources/nonexistent`, `../../evil`) share the stderr substrings
  `"--revision"` and `"must resolve to one of the pair"`. Design Decision 5
  step 3 says a nonexistent id or traversal path refuses earlier, through
  `resolve_concept_path`'s own path-safety/existence gate, "exactly as
  `--winner` does" — and `--winner`'s own equivalent tests assert no message
  content for those cases. The implemented test asserts both substrings only
  for the genuine non-member case, and only exit-code + zero-writes for the
  other two, matching actual (and `--winner`-consistent) behavior. Task 1.8
  itself flagged this as unconfirmed ("confirm the observed behavior before
  1.11 lands").
- **1.9**: added a small additional test
  (`test_from_findings_combination_gate_names_every_excluded_flag`) pinning
  the exact extended `--from-findings` message text, since the pre-existing
  test only checked the `"--from-findings"` substring.
- Commit message shapes (`openkos: reconcile <a> revises <b>`) are not
  unit-tested directly: `tests/unit/cli/test_reconcile.py`'s
  `_init_workspace` does not isolate git identity (unlike
  `test_set_volatility.py`'s `isolate_git_identity`), so autocommit may be
  silently skipped in a runner lacking a global git identity, and no existing
  test in this file asserts commit messages for that reason. The commit
  message code path is implemented per design and exercised functionally
  (`_autocommit` receives the right message when git identity is present),
  just not independently pinned by a new test — consistent with this file's
  existing convention for `--winner`'s commit message.

## Slice 2 (PR 2 → PR 1's branch): DONE

All tasks 2.1–2.11 in `tasks.md` are complete and checked off. Strict TDD
followed throughout: every `[TEST]` task (2.1–2.5, 2.7) was observed RED
before the paired `[IMPL]` task (2.6) landed, and every mutation `tasks.md`
names was applied, observed to kill exactly the predicted tests, and
reverted by inverse edit (never `git checkout --`), with `__pycache__`
purged before each verdict.

### What changed

- `src/openkos/resolution/contradiction.py`: `_candidate_pairs` now excludes,
  before `total_count`/the cap, any pair joined in either direction by an
  edge whose `relation_type` is in `RESOLUTION_RELATION_TYPES`
  (`supersedes`, `reconciled_with`, `revises` — from
  `openkos.model.relations`, landed in slice 1). Module docstring,
  `_candidate_pairs`'s own docstring, and `CandidatePlan.edge_total`'s
  docstring updated to describe the new exclusion, in the same style as the
  existing `derived_from` exclusion they already documented.
  `plan_candidates` and `find_contradictions` both read `_candidate_pairs`
  (via `_pairs_and_types`), so the `curate` cost gate (`plan.edge_total`,
  `plan.llm_calls`) and the actual judged spend fall by the same excluded
  count with no change to `curate.py`.
- `tests/unit/resolution/test_contradiction.py`: five new tests at the
  `_candidate_pairs`/`plan_candidates` level
  (`test_resolved_pairs_excluded_from_candidates` — parametrized over the
  three types × both directions; `test_resolved_pair_excluded_even_with_another_typed_edge_present`;
  `test_resolution_exclusion_applied_before_cap` — `cap=1` and `cap=None`;
  `test_unrelated_live_pair_still_judged_alongside_a_resolved_pair`;
  `test_plan_candidates_cost_gate_excludes_resolved_pairs`), plus the
  pre-existing `test_find_contradictions_genuine_typed_edge_still_surfaced`
  converted to a 4-way parametrize (`derived_from` + the three resolution
  types) run with `include_deprecated=True` to keep the check from being
  confounded by `supersedes`'s own unrelated deprecation effect.
- `tests/unit/cli/test_contradictions.py`: one pre-existing test,
  `test_contradictions_include_deprecated_restores_the_superseded_pair`,
  updated — see Deviations below.
- `docs/cli.md`: one sentence in the `contradictions` section stating a
  resolved pair is never a candidate, including under
  `--include-deprecated`, and that removing the relation restores it.

### Mutations applied and reverted (strict TDD)

1. Removed the `- resolved` subtraction from `pair_keys` (filter removed) →
   all 14 exclusion-dependent tests failed (the resolved pairs were no
   longer excluded). Reverted; suite green.
2. Moved the resolution filter to AFTER the cap slice, so `total_count` no
   longer excluded resolved pairs and a `cap=1` call could let a resolved
   pair consume the one slot → both `test_resolution_exclusion_applied_before_cap`
   cases (`cap=1`, `cap=None`) and `test_plan_candidates_cost_gate_excludes_resolved_pairs`
   failed (wrong `total`/`edge_total`), plus every test asserting the exact
   `pairs`/`total` shape. Reverted; suite green.
3. Narrowed the constant used in the filter to `RESOLUTION_RELATION_TYPES -
   {"revises"}` (one type dropped from the set) → exactly the `revises`-only
   parametrizations of `test_resolved_pairs_excluded_from_candidates` and
   `test_find_contradictions_genuine_typed_edge_still_surfaced`, plus
   `test_unrelated_live_pair_still_judged_alongside_a_resolved_pair` (which
   includes a `revises` edge among its resolved pairs), failed. Reverted;
   suite green.
4. Restricted the exclusion to forward-direction edges only
   (`edge.source_id < edge.target_id`) — a direction-sensitivity mutation →
   exactly the three `b-a`-holder parametrizations of
   `test_resolved_pairs_excluded_from_candidates` failed (the `a-b` cases
   and every other test stayed green, since none of them exercise the
   reverse direction alone). Reverted; suite green.

### Verification (foreground, unpiped)

- `uv run ruff check .` — **All checks passed!**
- `uv run ruff format --check .` — **313 files already formatted** (no
  reformatting needed).
- `uv run mypy .` — **Success: no issues found in 313 source files.**
- `uv run pytest` (full, unpiped) — **6484 passed, 2 skipped** (same
  pre-existing platform-gated skips as slice 1).

### Commits (branch `feat/1014-revision-detection-2`, stacked on slice 1's
`feat/1014-revision-detection`)

1. `c0c14f9` — `fix(resolution): exclude resolved pairs from contradiction
   candidates before the cap` (`src/openkos/resolution/contradiction.py`,
   `tests/unit/resolution/test_contradiction.py`,
   `tests/unit/cli/test_contradictions.py`)
2. `50d800f` — `docs(cli): note that a resolved pair never re-enters
   contradiction candidacy` (`docs/cli.md`)

`git diff --stat feat/1014-revision-detection...HEAD`: 4 files changed, 202
insertions(+), 23 deletions(-) — within the ~90-120 estimate's spirit once
the necessary pre-existing-test fix and full docstring updates are counted;
well under the 400-line budget as its own slice.

No `openspec/` paths were staged or committed (working-tree-only, per the
orchestrator's store instructions, matching slice 1).

### Deviations from tasks.md's literal wording

- **Scope for the resolution/contradiction.py commit**: task 2.11 flagged
  this as unconfirmed ("no dedicated `resolution` scope is listed yet") and
  suggested checking the originating change
  (`openspec/changes/archive/2026-07-22-freshness-contradiction-detection/`)
  before finalizing. That change's own squashed commit used scope
  `retrieval` (`feat(retrieval): add read-only LLM contradiction detection
  over typed graph edges`), but every dedicated later change touching only
  `resolution/contradiction.py` (`#449` "reserve a floor", `#470` "return
  partial contradiction...", `#879` "tone is not a property") used scope
  `resolution` — none of them used `retrieval`, `graph`, or `contradictions`
  for a resolution-only change. `resolution`/`contradictions` are not on
  AGENTS.md's documented scope list, but that list documents itself as
  growing ("the list grows as code lands"); I judged the consistent,
  repeated recent precedent for this exact file-touch pattern (pure
  `resolution/contradiction.py` logic, no `cli.py`/`curate.py` change) a
  stronger signal than the one original bundled commit, and used `fix(resolution):`.
- **Pre-existing test updates beyond tasks.md's named scope**: tasks.md
  scoped this slice to `resolution/contradiction.py` and
  `tests/unit/resolution/test_contradiction.py`; it did not name
  `tests/unit/cli/test_contradictions.py`. The full unpiped `uv run pytest`
  run (task 2.10) surfaced one additional pre-existing failure there —
  `test_contradictions_include_deprecated_restores_the_superseded_pair` —
  for the identical reason as
  `test_include_deprecated_true_restores_a_pair_touching_a_superseded_concept`
  in `test_contradiction.py`: both used a `supersedes` edge to exercise
  `--include-deprecated`'s restore path, and design Decision 7 makes that
  exclusion unconditional (it applies "under `--include-deprecated` too").
  Fixed identically: `related_to` edge + `status: deprecated` on the
  counterpart, preserving the test's original intent (confirm the flag
  restores a deprecated-by-status pair) without the new confound. This is a
  necessary, design-predicted fallout of the confirmed shipped-behavior
  change, not a deviation from the design itself.
