# Tasks: Typed Relationships (Slice 1)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~530 total (PR1 ~180, PR2 ~150, PR3 ~90, PR4 ~110) |
| 400-line budget risk | Low (per-PR; each PR well under 400) |
| Chained PRs recommended | Yes |
| Suggested split | PR1 → PR2 → PR3 → PR4 (stacked on tracker) |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Low

Tracker branch `feat/typed-relationships`; only the tracker merges to `main` (human checkpoint). PR1 base = tracker; PR2 base = PR1 branch; PR3 base = PR2 branch; PR4 base = PR3 branch.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `relations:` vocabulary + encode/decode + §9 rule | PR1 | `uv run pytest tests/unit/model/` | N/A — pure codec/parsing, no I/O side effects to exercise beyond unit tests | Revert `relations.py`, `okf.py` relations hunks; no callers yet |
| 2 | `openkos relate` CLI verb | PR2 | `uv run pytest tests/unit/cli/test_relate.py` | `openkos relate <src> references <tgt> --auto` against a scratch workspace | Revert `relate` command + log line; PR1 codec untouched |
| 3 | Graph projection typed edges | PR3 | `uv run pytest tests/unit/graph/` | `openkos query`/graph rebuild against a fixture bundle with `relations:` | Revert `sqlite_graph.py` typed-edge pass; untyped `_LINK_RE` path unaffected |
| 4 | Merge guard refusal | PR4 | `uv run pytest tests/unit/bundle/test_merge.py tests/unit/cli/test_merge.py` | `openkos merge <survivor> <absorbed>` against a fixture with typed relations | Revert `find_relation_conflicts` + Phase A hook; merge behaves as before this slice |

## Phase 1 (PR1 → tracker): Relation Vocabulary + Codec + §9 Rule

Spec: `relations:` Frontmatter Field Shape; OKF §9 Conformance — `relations:` Field Shape.

- [x] 1.1 RED: `tests/unit/model/test_relations.py` — `SEEDED_RELATION_TYPES` contains the 8 KOM defaults, module has zero `openkos` imports.
- [x] 1.2 GREEN: create `src/openkos/model/relations.py` mirroring `types.py::REGISTRY` shape with `SEEDED_RELATION_TYPES`.
- [x] 1.3 RED: `tests/unit/model/test_okf.py` — `encode_relations`/`decode_relations` round-trip a well-formed `relations: [{target, type}]` list (Scenario: well-formed entry parses).
- [x] 1.4 RED: `decode_relations` raises `ValueError` on newline in `target`/`type` (Scenario: newline rejected).
- [x] 1.5 RED: absent `relations:` key decodes to empty list, no error (Scenario: absent key is valid).
- [x] 1.6 RED: `encode_relations` sorts entries by `(target, type)` and strips `.md`/canonicalizes target deterministically.
- [x] 1.7 GREEN: implement `encode_relations`/`decode_relations` in `src/openkos/model/okf.py`, mirroring `encode_merged_from`/`decode_merged_from`; insert `relations` key after `provenance`, before `merged_from`.
- [x] 1.8 RED: `check_conformance` reports a violation for malformed `relations:` shape in the existing `f"{path}: {message}"` form (Scenario: malformed relations entry reported).
- [x] 1.9 RED: regression — `check_conformance` output for documents without `relations:` is byte-identical before/after this rule (Scenario: byte-identical output when relations absent).
- [x] 1.10 RED: well-formed `relations:` passes conformance with no violation.
- [x] 1.11 GREEN: wire the additive §9 rule into `check_conformance` in `okf.py`, gated on `scan.metadata` containing `relations`.
- [x] 1.12 DOC: author `docs/adr/0004-typed-relationships-frontmatter.md` recording frontmatter storage over roadmap alt, seeded-open vocabulary (WARN not reject), and merge-REFUSE-in-slice-1 with rewiring deferred to slice 2.
- [x] 1.13 VERIFY: `uv run pytest tests/unit/model/` green; `ruff check` and `mypy --strict` clean on `relations.py` and `okf.py`.

## Phase 2 (PR2 → PR1): `relate` CLI Verb

Spec: `relate` CLI Verb Writes A Typed Relation; Seeded-But-Extensible Relation Vocabulary; Target Containment Consistent With Existing Verbs.

- [x] 2.1 RED: `tests/unit/cli/test_relate.py` — successful `relate a references b` (confirmed/`--auto`) appends `{target: b, type: references}` to `a`'s `relations:`, updates log.md (Scenario: successful relate writes into source frontmatter).
- [x] 2.2 RED: missing target fails closed — no write, non-zero exit, clear error (Scenario: missing target fails closed).
- [x] 2.3 RED: missing source fails closed — no write, non-zero exit, clear error (Scenario: missing source fails closed).
- [x] 2.4 RED: non-TTY without `--auto` refuses, nothing written (Scenario: non-TTY without --auto refuses).
- [x] 2.5 RED: known relation type accepted silently; unknown type accepted with WARN to stderr; empty/whitespace type rejected with no write (Scenarios: known/unknown/empty type).
- [x] 2.6 RED: traversal-shaped id (`../../evil`) for source or target is refused, no write (Threat Matrix: path traversal).
- [x] 2.7 RED: `source == target` is rejected before any write.
- [x] 2.8 RED: duplicate `(target, rel)` relate call is idempotent (no duplicate entry).
- [x] 2.9 GREEN: implement `relate` command in `src/openkos/cli/main.py`, mirroring the `forget` verb scaffold: Phase A `require_workspace` + `_resolve_concept_path` on both source/target, preview/confirm/`--auto`/`review:` gate; Phase B writes source concept file + `**Relate**` log.md line only (no index.md).
- [x] 2.10 VERIFY: `uv run pytest tests/unit/cli/test_relate.py` green; `ruff check` and `mypy --strict` clean on `main.py` relate hunk.

## Phase 3 (PR3 → PR2): Graph Projection Typed Edges

Spec: Edge `relation_type` Populated From Frontmatter `relations:`.

- [x] 3.1 RED: `tests/unit/graph/test_sqlite_graph.py` — typed relation edge carries its `relation_type` (`relations: [{target: concepts/x, type: depends_on}]` → edge `relation_type == "depends_on"`) (Scenario: typed relation edge carries its relation_type).
- [x] 3.2 RED: untyped-link edge (no `relations:` key, bundle-relative link in body) keeps `relation_type` `NULL`, unchanged (Scenario: untyped-link edge remains NULL relation_type).
- [x] 3.3 RED: regression — existing untyped `_LINK_RE` edge extraction output is byte-identical for objects without `relations:` (before/after this change).
- [x] 3.4 RED: `relations:` entry whose target is unknown/unresolvable is dropped (consistent with existing untyped-link drop-if-unknown behavior); dedup key is `(source_id, target_id, relation_type)` so typed and untyped edges between the same pair coexist as two rows.
- [x] 3.5 GREEN: extend `build_graph` in `src/openkos/graph/sqlite_graph.py` with a second pass populating `Edge.relation_type` from decoded `relations:`, keyed on `(source, target, relation_type)`; leave the existing untyped `_LINK_RE` pass unchanged.
- [x] 3.6 GREEN: extend `_SELECT_EDGES_SQL` ORDER BY to include `relation_type` (NULLs first).
- [x] 3.7 VERIFY: `uv run pytest tests/unit/graph/` green; `ruff check` and `mypy --strict` clean on `sqlite_graph.py`.

## Phase 4 (PR4 → PR3): Merge Guard — Fail-Closed Refusal — COMPLETE

Spec: Non-Silent Guard For Edge-Bearing Merge.

- [x] 4.1 RED: `tests/unit/bundle/test_merge.py` — `find_relation_conflicts` detects the absorbed object's own outbound `relations:` entries as non-empty (Scenario: merge of an object with outbound relations surfaces a guard).
- [x] 4.2 RED: `find_relation_conflicts` detects an inbound typed relation from another bundle file targeting the absorbed object (Scenario: merge of an inbound relation target surfaces a guard).
- [x] 4.3 RED: merge of an object with no typed relations (inbound or outbound) proceeds unaffected, per existing merge requirements (Scenario: merge of an object with no typed relations proceeds unaffected).
- [x] 4.4 GREEN: implement `find_relation_conflicts(absorbed_id, files, absorbed_text)` in `src/openkos/bundle/merge.py`, mirroring `links.find_inbound_link_rewrites` for the outbound/inbound scan.
- [x] 4.5 RED: `tests/unit/cli/test_merge.py` — `merge` Phase A refuses (fail-closed) with a clear error and non-zero exit before any write when `find_relation_conflicts` reports a hit; no rewiring attempted.
- [x] 4.6 GREEN: wire the refusal hook into `cli/main.py::merge` Phase A, after resolving survivor/absorbed and the existing `other_files` bundle scan.
- [x] 4.7 VERIFY: `uv run pytest tests/unit/bundle/test_merge.py tests/unit/cli/test_merge.py tests/unit/cli/test_merge_roundtrip.py tests/unit/cli/test_unmerge.py` green (61 passed); `ruff check` and `mypy --strict` clean on `merge.py` and `main.py` merge hunk.

Implementation notes: the guard also covers the SURVIVOR's own outbound relations targeting the absorbed object (not just third-party files), reusing the existing `other_files` bundle-wide read plus `survivor_text` already in memory — no second bundle scan. A conflict raises `ValueError` inside the same try block that already wraps `plan_merge`, reusing the existing "openkos merge: failed while preparing the merge -- {exc}." fail-closed error path (exit 1, no write).

## Cross-PR Regression Guard

- [x] 5.1 After each PR: `uv run pytest` (full suite) green; branch/line coverage stays at or above the existing ~90% branch bar. (PR1: 896 passed; PR2: 911 passed; PR2 correction batch: 916 passed, full suite green; PR3: 921 passed, full suite green; PR3 correction batch: 922 passed, full suite green; PR4: 928 passed, full suite green; PR4 correction batch: 931 passed, full suite green)
- [x] 5.2 After each PR: `build_concept`/LLM ingest output remains byte-identical (no `relations:` emitted by `build_concept`) — assert via existing ingest golden/regression tests. (PR1: `test_build_concept_output_byte_identical_regression` added and passing; PR2: unaffected — `relate` never touches `build_concept`, regression test still passing; PR3: unaffected — `build_graph` change is graph-projection-only, ingest golden test still passing; PR3 correction batch: unaffected — only the skip-note observability of the malformed-relations path changed; PR4: unaffected — merge guard is detection-only, `build_concept`/`build_graph` untouched, ingest golden test still passing; PR4 correction batch: unaffected — exception handling and codec normalization only, `build_concept` untouched, ingest golden test still passing)

## PR4 Correction Batch (post-review, pre-commit)

Bounded correction applied against `feat/tr-04-merge-guard` in response to a dual reliability + risk review of PR4. TDD (RED confirmed before each fix). Not new feature work; tree left uncommitted for orchestrator review.

- [x] E1 RED/GREEN (CRITICAL, reliability): `find_relation_conflicts`'s INBOUND scan called `okf.load_frontmatter`/`okf.decode_relations` on every OTHER bundle file with no exception handling — a malformed-YAML unrelated file (`yaml.YAMLError`, not a `ValueError`) escaped the CLI's `except (OSError, ValueError)` fail-closed handler and crashed `merge` with a raw traceback instead of completing. Wrapped the per-file parse in a broad `except Exception` (mirroring `lint.py::collect_docs`'s identical guard around the same call, same "a concurrent/hand edit can corrupt frontmatter mid-scan" rationale) and skip that file (contributes no conflict) — restructured as an `if relations is None: continue` after the `try/except` (not `except: continue` directly) to satisfy ruff `S112`. RED: `tests/unit/cli/test_merge.py::test_merge_succeeds_despite_unrelated_file_with_malformed_frontmatter` — pre-fix `assert 1 == 0` with the raw `ScannerError` escaping as `result.exception` (not a clean `SystemExit`), confirmed before the fix.
- [x] E2 RED/GREEN (WARNING, risk — also benefits the PR3 graph match): the inbound comparison `relation.target == absorbed_id` was raw string equality, so a hand-authored non-canonical target (leading `/`, e.g. `/concepts/absorbed`, mirroring the `[text](/id.md)` link style) missed the guard, orphaning that edge; the same non-canonical form also fails PR3's `relation.target in node_ids` graph match. Root-caused in the codec: extended `okf._validate_relation_target` with a new `_normalize_relation_path` helper (`PurePosixPath`-based) that strips a leading `/` and collapses redundant separators/`.` segments before the existing `.md`-suffix strip — shared by `encode_relation`/`decode_relation`, so both the merge guard and the graph benefit with zero code change in either caller. `..` traversal is deliberately left unrejected (simply won't match any real target-id), per instruction — no new path-security layer added this batch. RED (codec): `tests/unit/model/test_okf.py::test_decode_relation_normalizes_leading_slash_target` — pre-fix `target: '/concepts/x' != 'concepts/x'`. RED (integration): `tests/unit/bundle/test_merge.py::test_find_relation_conflicts_detects_non_canonical_leading_slash_target` — pre-fix `assert 0 == 1` (guard missed it). Both confirmed before the fix; all existing PR1 codec tests still pass unchanged (none pinned the old non-normalized form).
- [x] E3 VERIFY: `uv run pytest` → 931 passed (928 + 3 new: 1 CLI, 1 codec unit, 1 bundle integration); `uv run ruff check .` → All checks passed; `uv run ruff format tests/unit/model/test_relations.py` applied to clear the pre-existing PR1 format drift the verify phase flagged (cosmetic only); `uv run ruff format --check .` → 83 files already formatted; `uv run mypy` → Success, no issues found in 82 source files.

## PR3 Correction Batch (post-review, pre-commit)

Bounded correction applied against `feat/tr-03-graph-typed-edges` in response to a review of PR3 (WARNING + SUGGESTION on the malformed-`relations:` degrade path). TDD (RED confirmed before the fix). Not new feature work.

- [x] D1 RED/GREEN (WARNING + SUGGESTION): the `except ValueError: continue` branch for malformed `relations:` frontmatter was untested AND logged nothing, unlike every other skip path in `build_graph` (which append `_skip_note(concept_id, reason=...)` to `skipped`). Added `test_malformed_relations_contributes_no_typed_edges_and_is_noted_in_skipped` (RED: `store.skipped` assertion failed — `AssertionError: assert [] == ['concepts/stoicism.md: skipped (malformed relations)']`, while the node/zero-typed-edges assertions already passed pre-fix). Fixed by appending `skipped.append(_skip_note(source_id, reason="malformed relations"))` before `continue` in `src/openkos/graph/sqlite_graph.py:267`, mirroring the format/style of the other 4 skip paths in the same function.
- [x] D2 VERIFY: `uv run pytest` → 922 passed (921 + 1 new); `uv run ruff check .` → All checks passed; `uv run mypy` → Success: no issues found in 82 source files.

## PR2 Correction Batch (post-review, pre-commit)

Bounded correction applied against `feat/tr-02-relate-verb` in response to a reliability review of PR2. TDD (RED confirmed before each fix). Not new feature work.

- [x] C1 RED/GREEN (CRITICAL): fixed encode/decode `.md`-suffix asymmetry — `decode_relation` did not strip a trailing `.md` from `target` while `encode_relation` did, so a hand-edited `.md`-suffixed stored target was not recognized by `relate`'s idempotency dedup and could produce a literal duplicate entry on disk. Factored `_validate_relation_target` (shared, DRY) in `src/openkos/model/okf.py`, called from both `encode_relation` and `decode_relation`. RED: `tests/unit/model/test_okf.py::test_decode_relation_strips_md_suffix_from_target`, `::test_decode_relation_rejects_target_empty_after_md_strip`; `tests/unit/cli/test_relate.py::test_duplicate_relate_recognizes_hand_edited_md_suffixed_target` (all 3 failed pre-fix, confirmed).
- [x] C2 RED/GREEN (WARNING): fixed misleading `relate` preview on a no-op repeat — preview always printed `+{target: ..., type: ...}` even when `already_present`. Branched the preview in `src/openkos/cli/main.py` on `already_present` (unchanged/already-present line, count stays `N -> N`, no `+`). RED: `tests/unit/cli/test_relate.py::test_repeated_relate_preview_shows_no_change_not_addition` (failed pre-fix, confirmed).
- [x] C3 (coverage gap, low severity): added `tests/unit/cli/test_relate.py::test_relate_review_false_skips_the_prompt_like_auto`, mirroring `forget`'s equivalent test — passed immediately (gate code is byte-identical to `forget`'s), coverage-only as flagged.
- [x] C4 VERIFY: `uv run pytest` → 916 passed (911 + 5 new: 2 in `test_okf.py`, 3 in `test_relate.py`); `uv run ruff check .` → All checks passed; `uv run mypy` → Success, no issues in 82 source files; `uv run ruff format` applied to `test_relate.py` for style consistency (no logic change, tests stayed green).
