# Tasks: `status` surfaces pending duplicate groups

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~15-20 production, ~140-160 test (new file section: `_write_doc` helper ~10 lines + 6 tests ~20-25 lines each) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

The proposal's ~15/~120 estimate is close but the test side is a touch higher:
6 tests (T1-T6) at ~20-25 lines each (fixture setup + invoke + asserts) plus a
copied `_write_doc` helper (~10 lines) lands near 150-170 test lines. Total
stays well under 400 either way; no chaining needed.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Add the fourth `needs_attention` source (find_candidates + Tier.HIGH filter + line) with full RED-then-GREEN test coverage, docstring update, and spec delta | PR 1 | `uv run pytest tests/unit/cli/test_status.py -k duplicate` | `uv run openkos status` against a scratch workspace with two identically-titled concept docs | Revert the `main.py:4564-4574` block, the docstring diff, and the new `test_status.py` block — no other file touched |

## Phase 1: Spec confirmation

- [x] 1.1 Confirm `openspec/changes/status-surfaces-pending-duplicates/specs/status/spec.md` (already drafted) matches the four scenarios: no groups, exact-title surfaced, near-match-only stays clear, deprecated-only excluded. No edits expected; flag drift if found. — Confirmed, no drift.

## Phase 2: RED — failing tests first (`tests/unit/cli/test_status.py`)

- [x] 2.1 Copy `_write_doc(path, *, doc_type="Concept", title="Stub")` from `tests/unit/cli/test_duplicates.py:66` into `tests/unit/cli/test_status.py` (new needs-attention wiring block, after line 279, following the existing `_write_doc`-free precedent — per-module duplication).
- [x] 2.2 T1 — `test_status_surfaces_exact_title_duplicate_group`: two `Concept` docs titled `Stoicism` / `STOICISM`; assert the new line is present, contains `1 candidate group`, names `openkos duplicates`, `Nothing needs attention.` is absent, exit code 0. RED: `find_candidates` is not yet called.
- [x] 2.3 T2 — `test_status_duplicate_line_has_no_tier_labels`: same fixture as T1; assert `HIGH`, `LOW`, `exact`, `near` are all absent from stdout (scope the assertion to the duplicate line if another status string could collide).
- [x] 2.4 T3 — `test_status_near_match_only_duplicates_still_all_clear` (own numbered task, never folded into T4): `Stoicism` / `Stoic Philosophy` (near-match, `Tier.LOW`) **plus the `seed_vectors_db` fixture** (`tests/unit/cli/conftest.py:71`) applied to `tmp_path` before invoking `status`. Assert the duplicate line is absent, `Nothing needs attention.` is present, exit code 0. This is the sole pin on the HIGH-only decision and the sole cover for the tier filter's false branch — do not merge it into any other test.
- [x] 2.5 T4 — `test_status_no_duplicate_groups_no_new_entry`: fresh bundle, no near/exact titles, **plus `seed_vectors_db`**. Assert the duplicate line is absent, `Nothing needs attention.` is present, exit code 0. Covers the `if exact_title_groups:` false branch.
- [x] 2.6 T5 — `test_status_deprecated_only_duplicate_group_excluded`: `Stoicism` + `STOICISM` where both docs carry `status: deprecated` (mirror `test_duplicates.py:408` `test_duplicates_default_excludes_a_deprecated_group_member`), **plus `seed_vectors_db`**. Assert the duplicate line is absent, exit code 0.
- [x] 2.7 T6 — `test_status_duplicate_line_plural_wording`: two distinct exact-title groups (four docs total, two title pairs). Assert `2 candidate groups` (plural) appears.
- [x] 2.8 Run `uv run pytest tests/unit/cli/test_status.py -k duplicate` and confirm all six new tests FAIL (RED) before touching `main.py`. — T1/T2/T6 failed for the right reason (no duplicate-groups line rendered yet). T3/T4/T5 assert the pre-existing negative state (no line, all-clear) and passed trivially before the change exists — an inherent property of regression-guard tests on a not-yet-built feature, not a fixture-trap false pass; they remained green after GREEN too, confirming the false-arm coverage they exist to pin.

## Phase 3: GREEN — production change (`src/openkos/cli/main.py`)

- [x] 3.1 Insert the fourth `needs_attention` source at `main.py:4564-4569` (before the `vectors_missing` assignment), following D1-D4 from design.md exactly:
  ```python
  # #186: pending duplicate groups are ACTIONABLE -- name `duplicates` as
  # the next step. Exact-title matches only (Tier.HIGH); near-match (LOW)
  # is a deliberate high-recall review queue, not an alert (similarity.py).
  exact_title_groups = sum(
      1 for group in find_candidates(layout.bundle_dir) if group.tier is Tier.HIGH
  )
  if exact_title_groups:
      needs_attention.append(
          f"{exact_title_groups} candidate group{_plural(exact_title_groups)} with "
          "identical titles — run `openkos duplicates` to review."
      )
  ```
  Run unconditionally, above the `vectors_missing` check. Do not touch `_format_group_tally`, `find_candidates`, or `resolution/`.
- [x] 3.2 Update the `status` docstring at `main.py:4498-4507`: "THREE independent `bundle/**/*.md` walks" → "FOUR", adding the `resolution.find_candidates` walk (unconditional, stdlib `difflib`-only, never gated on `vectors_missing`; evaluates `lifecycle.deprecated_concept_ids` under the default `include_deprecated=False`). Keep the "#195 consolidation is out of scope" sentence and the "`status` calls `build_graph` exactly once" guarantee verbatim.
- [x] 3.3 Run `uv run pytest tests/unit/cli/test_status.py -k duplicate` and confirm all six tests now PASS (GREEN). — 6 passed.

## Phase 4: Full verification and cleanup

- [x] 4.1 Run the full suite: `uv run pytest` — confirm no regressions in `test_status.py` (conformance, dangling-reference, vectors.db ordering) or `test_duplicates.py`. — 2339 passed (baseline 2333 + 6 new).
- [x] 4.2 Run `uv run pytest --cov` and confirm branch coverage stays ≥90%; verify both arms of `if exact_title_groups:` (T1/T4) and both arms of the `Tier.HIGH` generator filter (T1 true; T3 false, T5 exclusion-before-pairing) are exercised, plus both `_plural()` outcomes (T6 plural, T1 singular). — 97.61% total branch coverage; gate (90%) passed.
- [x] 4.3 Run `ruff check`, `ruff format --check`, and `mypy --strict` on `src/openkos/cli/main.py` and `tests/unit/cli/test_status.py`; fix any findings. — All clean, no findings.
- [x] 4.4 Manually smoke-test: `uv run openkos init` in a scratch dir, add two identically-titled concept docs, run `uv run openkos status` and confirm the line renders as designed; run again on a bundle with only a near-match pair and confirm `Nothing needs attention.` still prints. — Confirmed both: `1 candidate group with identical titles — run \`openkos duplicates\` to review.` rendered for the exact-title pair; `Nothing needs attention.` rendered for the near-match-only pair (with vectors.db seeded).

## Delivery note

Commit the code change (`main.py` + `test_status.py`, Phase 2-4) as one
`fix(cli): ...` work unit. Commit the SDD planning artifacts
(`explore.md`, `proposal.md`, `design.md`, `tasks.md`, `specs/status/spec.md`)
separately as `chore(sdd): ...` — never mixed into the code commit. This keeps
planning prose out of the code review's changed-line budget (lesson from the
prior cycle, where ~1070 lines of planning prose pushed a ~300-line code
change into a high review tier).
