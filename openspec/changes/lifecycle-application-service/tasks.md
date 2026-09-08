# Tasks: Lifecycle Application Service

Issue #918 (third and final bounded-context slice). Every commit uses `Refs
#918`, never `Closes`/`Fixes`/`Resolves` (the issue closes only at archive).
Commit type is `feat` where new public surface is introduced (S1) and
`refactor` elsewhere, matching the archived `ingest-application-service`
cycle's own commits. Commit scope is `lifecycle` on every commit in this
change — the `ingest` cycle used its domain name (`ingest`) as the scope for
all three of its PRs, including the ones that only touched `cli/main.py`, so
this change follows the same per-domain precedent rather than `cli`.

> **Line numbers in this file are pre-S1 and drift with every slice.** S1 removed
> 618 lines from `cli/main.py` at a position ahead of most later targets, so
> everything below the merge core shifted up by ~525 (`_execute_single_unmerge`:
> `10606` -> `10080`). Locate every target by CONTENT and re-derive its range
> against the working tree before editing. Do not trust a coordinate written
> here or in `design.md`.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,980–2,320 total (S1 350–400 / S2a 250–320 / S2b 350–400 / S3 300–400 / S4 350–400 / S5 380–400), per `design.md`'s Slice Plan table |
| 400-line budget risk | High — S1, S2b, S4, and S5 each sit within 20 lines of the 400-line cap; only S2a has real headroom |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (S1: `consent.py` + `lifecycle.py` seed) → PR 2 (S2a: `unmerge_core`) → PR 3 (S2b: `prepare_unmerge`) → PR 4 (S3: de-present `forget`) → PR 5 (S4: de-present `purge`) → PR 6 (S5: de-present `adjudicate --apply`/`--apply-same`) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| S1 | `application/consent.py` + seeded `application/lifecycle.py`: `ConfirmationRequest` union, relocated merge core, `snapshot_read` promotion, the 4 forced `test_adjudicate.py` repoints | PR 1 | `uv run pytest tests/unit/application tests/unit/cli/test_merge_core.py tests/unit/cli/test_merge.py tests/unit/cli/test_adjudicate.py -q` | `uv run openkos merge <a> <b>` against a seeded workspace | Revert the two new modules, `fsio.py`'s addition, and `main.py`'s adapter/import diff together; nothing downstream exists yet |
| S2a | `unmerge_core` — Phase B writes only, adapter keeps Phase A/preview/gate/guard | PR 2 | `uv run pytest tests/unit/application tests/unit/cli/test_unmerge.py tests/unit/cli/test_unmerge_surgical_catalog.py -q` | `uv run openkos unmerge <survivor>` against a merged pair in a seeded workspace | Revert `unmerge_core`/`UnmergeResult` and `main.py`'s call-out; `_execute_single_unmerge` still works standalone (S1 unaffected) |
| S2b | `prepare_unmerge` + `PreparedUnmerge` + `unwind_step_preview_lines`; adapter renders the preview from the dataclass; `_execute_single_unmerge` deleted | PR 3 | same as S2a | same as S2a, plus `--to` targeted unmerge | Revert `prepare_unmerge`/`PreparedUnmerge` and restore the inline preview in `main.py`; S2a's `unmerge_core` stays functional standalone |
| S3 | De-present `forget`: `ForgetPlan`, `ReferenceDisclosure` (#567 aggregation), Gate 1 stays adapter-side | PR 4 | `uv run pytest tests/unit/application tests/unit/cli/test_forget.py -q` | `uv run openkos forget <id>` against a seeded workspace with inbound references | Revert `ForgetPlan`/`prepare_forget`/`forget_core` and `main.py`'s adapter diff together; independent of S2 |
| S4 | De-present `purge`: `PurgePlan`, `PurgeDisclosure`, `purge_confirm_phrase`, the two string notices; all six rails stay adapter-side | PR 5 | `uv run pytest tests/unit/application tests/unit/cli/test_purge.py -q` | `uv run openkos purge <id> --confirm-phrase '<phrase>'` against a seeded workspace | Revert `PurgePlan`/`prepare_purge`/`purge_confirm_phrase` and `main.py`'s adapter diff together; independent of S2 and S3 |
| S5 | De-present `adjudicate --apply`/`--apply-same`: `prepare_one_merge`, `reconcile_planned`, `BatchApplyPreview`, `ordered_merge_pair`, the typed-count gate | PR 6 | `uv run pytest tests/unit/application tests/unit/cli/test_adjudicate.py -q` | `uv run openkos adjudicate --apply-same --confirm-count <n>` against a seeded workspace with SAME-verdict pairs | Revert `BatchApplyPreview`/`prepare_one_merge`/`preview_apply_same` and `main.py`'s adapter diff together; the 4 patch-target repoints stay from S1 |

### Dependency Graph

- **S1 → S2a → S2b** (strict): S2a's `unmerge_core` and S2b's `prepare_unmerge` both live in `application/lifecycle.py`, which S1 creates; S2b also deletes `_execute_single_unmerge`, which S2a still partially relies on.
- **S1 → S5** (strict): S5's `BatchApplyPreview.confirmation` reuses `TypedChallengeConfirmation` from S1's `consent.py`, and the 4 real `test_adjudicate.py` patch-target repoints (C3) land in S1, not S5.
- **S1 → S3** and **S1 → S4** (soft): both need `application/lifecycle.py` and `consent.py` to exist (`ForgetPlan.confirmation: BooleanConfirmation`, `PurgePlan.confirmation: TypedChallengeConfirmation`), but neither touches merge/unmerge/adjudicate code.
- **S3 and S4 are independent of each other and of S2a/S2b/S5** — any order after S1 is valid; any prefix of the full chain is a valid stopping point (every slice keeps all five verbs working end to end).

### Requirement Traceability

| Requirement (from `specs/lifecycle-application-service/spec.md`) | Primary tasks |
|---|---|
| Non-CLI Callable Lifecycle Composition | 2.4, 6.3, 8.3, 10.2, 12.2 |
| ConfirmationRequest Is A Tagged Union Of Boolean And Typed-Challenge Variants | 1.1, 1.3 (defined); 10.1, 12.1 (exercised) |
| Hard Refusal Gates Are Not Representable As Confirmations | 1.2 (D2 guard); 8.2 (forget's Gate 1) |
| Unmerge Has A Full Public Prepare/Core Pair Matching Merge | 4.1–4.2 (`unmerge_core`); 6.1, 6.3 (`prepare_unmerge`) |
| Purge's Disclosure Renders From Returned Templates Byte-For-Byte | 10.1–10.2; falsified at 11.2 |
| Shared Write Mechanics Stay Adapter-Side, Each With One Definition | 2.2–2.3 (`snapshot_read` + layering guard); reverified every slice gate |
| Adapter Owns Interaction, Presentation, And Exit Codes | 3.4, 5.2, 7.2, 9.2, 11.2, 13.2 |
| The Extraction Preserves Observable CLI Behavior | Every slice gate + falsification task (3.3–3.4, 5.1–5.2, 7.1–7.2, 9.1–9.2, 11.1–11.2, 13.1–13.2) |

## Slice S1 — PR 1: seed `consent.py` + `lifecycle.py` (~350–400 lines)

### Phase 1: The `ConfirmationRequest` union (RED first)

- [x] 1.1 RED: write `tests/unit/application/test_lifecycle.py` covering a granted `BooleanConfirmation`; `TypedChallengeConfirmation.matches()` under both `match_mode`s (`exact` for purge-style raw comparison, `strip-then-exact` for adjudicate-style) including a whitespace-only-differs response that must diverge between the two; and that `purge`'s request is the typed-challenge variant with `supplying_flag == "--confirm-phrase"` and no bypass field anywhere on it. Must fail: `openkos.application.consent` does not exist.
- [x] 1.2 RED: write `tests/unit/application/test_lifecycle_seams.py` asserting neither `BooleanConfirmation` nor `TypedChallengeConfirmation` declares a `granted`, `force`, or `override` field (D2). Must fail: module absent.
- [x] 1.3 GREEN: create `src/openkos/application/consent.py` with `BooleanConfirmation`, `TypedChallengeConfirmation` (frozen dataclasses, `Literal` `kind` tags) and the `ConfirmationRequest` union, per `design.md` D1. 1.1 and 1.2 pass.

### Phase 2: Relocate the merge core, id resolution, and `snapshot_read`

- [x] 2.1 RED: extend `tests/unit/application/test_lifecycle.py` with tests calling `application.lifecycle.prepare_merge`/`merge_core`/`canonicalize_concept_id`/`resolve_concept_path`/`merge_drift_targets` directly (no Typer runner), including a `../` path-escape and a symlink-outside-`bundle/` refusal for `resolve_concept_path`. Must fail: `openkos.application.lifecycle` does not exist.
- [x] 2.2 RED: extend `tests/unit/application/test_layering.py`'s shared-helper uniqueness check to include `snapshot_read`, and its offender-import list to include `openkos.vcs`. Must fail: `fsio.snapshot_read` does not exist yet.
- [x] 2.3 GREEN: promote `_snapshot_read` (`main.py:595`) to `fsio.snapshot_read`; `main._snapshot_read` becomes a one-line delegator (the `_slugify` precedent). 2.2 passes.
- [x] 2.4 GREEN: create `src/openkos/application/lifecycle.py`; relocate `StackedBodyReport`, `PreparedMerge`, `MergeResult`, `prepare_merge`, `merge_core`, `merge_drift_targets` (from `_merge_drift_targets`), `canonicalize_concept_id` (from `_canonicalize_concept_id`), `resolve_concept_path` (from `_resolve_concept_path`) verbatim, importing `fsio.snapshot_read`. `main.py`'s `merge` command calls the module (`application_lifecycle.prepare_merge(...)`), never an aliased name. 2.1 passes.
- [x] 2.5 GREEN: repoint `tests/unit/cli/test_merge_core.py`'s 53 `from openkos.cli.main import ...` references to one aliased `import openkos.application.lifecycle as application_lifecycle` line (the `test_query_save.py` precedent). Zero output-text assertions change.

### Phase 3: The adjudicate seam's 4 forced repoints, and the Slice 1 gate

- [x] 3.1 GREEN: repoint `tests/unit/cli/test_adjudicate.py`'s 4 `monkeypatch.setattr("openkos.cli.main.prepare_merge"/"merge_core", ...)` sites to `"openkos.application.lifecycle.prepare_merge"/"merge_core"`.
- [x] 3.2 GREEN: add `not hasattr(openkos.cli.main, "prepare_merge")` and `not hasattr(openkos.cli.main, "merge_core")` to `test_lifecycle_seams.py`; run `uv run ruff check .` to force out `main.py`'s now-dead imports (F401).
- [x] 3.3 Gate: `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy .`; `uv run pytest tests/unit/application tests/unit/cli/test_merge_core.py tests/unit/cli/test_merge.py tests/unit/cli/test_adjudicate.py -q`; full `uv run pytest --cov=src/openkos` ≥ 90%.
- [x] 3.4 Output-text-assertions-unmodified check: confirm zero output-text assertion changes in `test_merge.py`/`test_merge_core.py`/`test_adjudicate.py` (only patch-target and import-path literals differ). Falsify: mutate one character in a relocated preview line, purge `__pycache__`, confirm RED, revert with the inverse replace, confirm GREEN. Additionally delete one of the 4 repointed patch lines, confirm the spy-call assertion goes RED (dangerous-class no-op check), restore.
- [ ] 3.5 Commit: `feat(lifecycle): seed the application service with ConfirmationRequest and the merge core`.

## Slice S2a — PR 2: `unmerge_core`, Phase B writes only (~250–320 lines)

### Phase 4: Relocate the write-only core (RED first)

- [x] 4.1 RED: extend `tests/unit/application/test_lifecycle.py` with tests calling `application.lifecycle.unmerge_core(layout, prepared)` directly against a `PreparedUnmerge`-shaped fixture built from `_execute_single_unmerge`'s existing write inputs, asserting it returns `UnmergeResult` and performs no `typer`/`sys.stdin` access. Must fail: symbols absent.
- [x] 4.2 GREEN: add `UnmergeResult` and `unmerge_core(layout, prepared)` to `application/lifecycle.py`, relocating `_execute_single_unmerge`'s write-only body (`main.py:10437–10492`, re-derived against the current tree) verbatim. `main.py`'s `_execute_single_unmerge` calls `application_lifecycle.unmerge_core` for the write step; preview, gate, guard, and `_autocommit` stay inline for this slice.
- [x] 4.3 REFACTOR: confirm `rg -n '_execute_single_unmerge\(' tests/unit/cli/test_unmerge.py tests/unit/cli/test_unmerge_surgical_catalog.py` count is unchanged (0 direct call-sites in both files before and after — both drive it only through the CLI runner) — no call-site repoints in this slice (that is S2b).

### Phase 5: Slice 2a gate

- [x] 5.1 Gate: `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy .`; `uv run pytest tests/unit/application tests/unit/cli/test_unmerge.py tests/unit/cli/test_unmerge_surgical_catalog.py -q`; full `uv run pytest --cov=src/openkos` ≥ 90%. All green — see apply-progress memory for exact output (6033 passed, 3 skipped, 96.95% total coverage, `application/lifecycle.py` at 99%).
- [x] 5.2 Output-text-assertions-unmodified check: zero assertion changes in `test_unmerge.py`/`test_unmerge_surgical_catalog.py` (only the new S2a test block was added to `test_lifecycle.py`; no existing assertion text touched). Falsify: mutated the one-character absorbed-path literal `unmerge_core` builds (`f"{prepared.absorbed_canonical}.md"` -> `...x.md"`), purged `__pycache__`, confirmed `test_unmerge_restores_survivor_absorbed_index_log_and_reverses_links` went RED (exit code 1, autocommit-tracking assertion failure), reverted with the inverse replace, purged `__pycache__` again, confirmed GREEN.
- [ ] 5.3 Commit: `refactor(lifecycle): extract unmerge's write-only core into the application service`.

## Slice S2b — PR 3: `prepare_unmerge` (~350–400 lines)

### Phase 6: The missing Phase A (RED first)

- [ ] 6.1 RED: extend `tests/unit/application/test_lifecycle.py` with tests calling `application.lifecycle.prepare_unmerge(root, layout, survivor_path, survivor_canonical, absorbed_canonical, now=..., cfg=...)` directly, asserting it returns a `PreparedUnmerge` matching the preview `_execute_single_unmerge` builds today (reversed link/relation/provenance texts, `catalog_log_drifted`, `review`) and performs no write; and that `unwind_step_preview_lines(entry, survivor_canonical)` returns the same lines as `main.py`'s existing helper. Must fail: symbols absent.
- [ ] 6.2 RED: add `not hasattr(openkos.cli.main, "_execute_single_unmerge")` to `test_lifecycle_seams.py`. Must fail: the name still exists (S2a left a thin wrapper).
- [ ] 6.3 GREEN: add `PreparedUnmerge` and `prepare_unmerge(...)` to `application/lifecycle.py`, relocating the preview-computation body of `_execute_single_unmerge` (`main.py:10700–10891`) and `unwind_step_preview_lines` (`main.py:10558`). Rewrite `main.py`'s unmerge command to call `prepare_unmerge`, render the preview from `PreparedUnmerge`'s fields, run the existing gate/guard, then `unmerge_core`; delete `_execute_single_unmerge`. 6.1 and 6.2 pass.
- [ ] 6.4 GREEN: repoint `tests/unit/cli/test_unmerge.py` and `test_unmerge_surgical_catalog.py`'s patch targets to `application_lifecycle`; zero output-text assertion changes.

### Phase 7: Slice 2b gate

- [ ] 7.1 Gate: `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy .`; `uv run pytest tests/unit/application tests/unit/cli/test_unmerge.py tests/unit/cli/test_unmerge_surgical_catalog.py -q`; full `uv run pytest --cov=src/openkos` ≥ 90%.
- [ ] 7.2 Output-text-assertions-unmodified check: zero assertion changes across both unmerge test files, including the classic and `--to` forms. Falsify: mutate one preview line and, separately, the non-TTY refusal line; purge `__pycache__` each time; confirm RED then revert to GREEN.
- [ ] 7.3 Commit: `refactor(lifecycle): build prepare_unmerge and complete unmerge's phase A/B split`.

## Slice S3 — PR 4: de-present `forget` (~300–400 lines)

### Phase 8: `ForgetPlan` and `ReferenceDisclosure` (RED first)

- [x] 8.1 RED: write tests in `tests/unit/application/test_lifecycle.py` for `prepare_forget(root, layout, concept_id, scope=..., now=..., cfg=...)` returning a `ForgetPlan` whose `references: tuple[ReferenceDisclosure, ...]` preserves insertion order (#567 aggregation), whose `surviving_refs`/`unverifiable_refs` are Gate 1's inputs, and whose `confirmation` prompt text differs for `scope="self"` vs `scope="source"`; and for `forget_core(layout, plan)` performing the write. Must fail: symbols absent.
- [x] 8.2 RED: add an assertion (in `test_lifecycle_seams.py` or `test_lifecycle.py`) that `ForgetPlan.surviving_refs`/`unverifiable_refs` are never read by `ConfirmationRequest.matches()` — Gate 1 stays outside the union (D2/R3). Must fail: symbols absent.
- [x] 8.3 GREEN: add `ReferenceKind`, `ReferenceDisclosure`, `ForgetPlan`, `prepare_forget`, `forget_core` to `application/lifecycle.py`, de-presenting `forget`'s inline body (`main.py:5855–6431` on this branch's coordinates); every `typer.echo` moves verbatim to the adapter call site. `main.py`'s `forget` command keeps Gate 1's refusal inline before calling the service, renders `ForgetPlan`'s fields, drives the boolean gate from `plan.confirmation`, calls `forget_core`. 8.1 and 8.2 pass. (`_sweep_ledger_sidecars_for_ids`/`_sweep_decisions_for_ids`/`_sweep_findings_for_ids` stay adapter-side, unmoved — they are shared with `purge`'s own Phase B and `application/lifecycle.py` must stay siblings-only under ADR-0018; `forget_core` covers only the `index.md`/`log.md`/unlink write design explicitly calls out.)
- [x] 8.4 GREEN: repoint `tests/unit/cli/test_forget.py`'s one patch site (`main._snapshot_read` → `fsio.snapshot_read`, safe class); zero output-text assertion changes across all 89 tests.

### Phase 9: Slice 3 gate

- [x] 9.1 Gate: `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy .`; `uv run pytest tests/unit/application tests/unit/cli/test_forget.py -q`; full `uv run pytest --cov=src/openkos` ≥ 90%.
- [x] 9.2 Output-text-assertions-unmodified check: zero assertion changes across `test_forget.py`'s 89 tests. Falsify: mutate one character in a relocated reference-disclosure line (the #567 singular/plural wording), purge `__pycache__`, confirm RED, revert, confirm GREEN; separately mutate Gate 1's refusal line and confirm it still fires with no `ConfirmationRequest` able to satisfy it.
- [ ] 9.3 Commit: `refactor(lifecycle): de-present forget into the application service`.

## Slice S4 — PR 5: de-present `purge` (~350–400 lines)

### Phase 10: `PurgePlan` and `PurgeDisclosure` (RED first)

- [ ] 10.1 RED: write tests in `tests/unit/application/test_lifecycle.py` for `purge_confirm_phrase(canonical_id, purge_ids, scope)`; `prepare_purge(root, layout, concept_id, scope=..., now=...)` returning a `PurgePlan` with a `TypedChallengeConfirmation` (`supplying_flag == "--confirm-phrase"`, `match_mode == "exact"`, no bypass field); `PurgeDisclosure`'s `raw_absence` arm (`main.py:7202–7206`) and `cascade_total` (source scope only); and `dropped_store_notice`/`residual_store_notice` returning `str | None` unchanged (moved verbatim, D3's exception). Must fail: symbols absent.
- [ ] 10.2 GREEN: add `PurgeDisclosure`, `PurgePlan`, `prepare_purge`, `purge_confirm_phrase`, `dropped_store_notice`, `residual_store_notice` to `application/lifecycle.py`, de-presenting `purge`'s inline body (`main.py:6900–7543`) and its ~10 pre-existing helpers; all six rails (`main.py:7211–7314`) stay adapter-side, before the gate. `main.py`'s `purge` command renders `PurgePlan`'s disclosure templates byte-for-byte and drives the typed-phrase gate from `plan.confirmation`. 10.1 passes.
- [ ] 10.3 GREEN: repoint `tests/unit/cli/test_purge.py`'s 5 safe-class patch sites (`main._purge_confirm_phrase`, `main._snapshot_read` racing) to `application_lifecycle`; zero output-text assertion changes across all 76 tests.

### Phase 11: Slice 4 gate

- [ ] 11.1 Gate: `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy .`; `uv run pytest tests/unit/application tests/unit/cli/test_purge.py -q`; full `uv run pytest --cov=src/openkos` ≥ 90%.
- [ ] 11.2 Output-text-assertions-unmodified check: zero assertion changes across `test_purge.py`'s 76 tests. Falsify (R5's direct proof): mutate one character in the IRREVERSIBLE-history-rewrite disclosure, purge `__pycache__`, confirm RED, revert, confirm GREEN; repeat for rail-4's dirty-tree refusal (incl. the 10-path cap and overflow clause) and rail-5's push-state refusal.
- [ ] 11.3 Commit: `refactor(lifecycle): de-present purge into the application service`.

## Slice S5 — PR 6: de-present `adjudicate --apply`/`--apply-same` (~380–400 lines)

### Phase 12: `BatchApplyPreview` and the typed-count gate (RED first)

- [ ] 12.1 RED: write tests in `tests/unit/application/test_lifecycle.py` for `prepare_one_merge(root, layout, index_path, log_path, group, ordered_pair=None)`, `reconcile_planned(prepared, no_reconcile=..., reconcile=...)`, `ordered_merge_pair(bundle_dir, member_ids)`, and `preview_apply_same(root, layout, index_path, log_path, results, include_cross_source=..., include_cross_type=...)` returning `BatchApplyPreview` whose `confirmation.expected == str(len(previewed))` and `match_mode == "strip-then-exact"` (`main.py:3057`'s policy); assert the zero-eligible short-circuit is decided before the gate is ever constructed. Must fail: symbols absent.
- [ ] 12.2 GREEN: add `PreviewedPair`, `BatchApplyPreview`, `prepare_one_merge`, `reconcile_planned`, `ordered_merge_pair`, `preview_apply_same` to `application/lifecycle.py`, de-presenting `_run_adjudicate_apply`/`_run_adjudicate_apply_same`'s helper chain (`main.py:2364–3200`). `adjudicate_candidates`, `find_candidates_report`, `OllamaClient`, `_reconcile_merged_survivor` stay untouched (C3) — no new `test_adjudicate.py` patch-target repoints beyond S1's 4. 12.1 passes.
- [ ] 12.3 GREEN: rewrite `main.py`'s `adjudicate --apply`/`--apply-same` as adapters: Pass 1 calls `preview_apply_same`, renders the preview and typed-count gate from `BatchApplyPreview`; Pass 2 re-resolves via `prepare_one_merge(ordered_pair=...)` per pair, never reusing Pass 1's result (preserving #776's pinned-direction behavior); `_apply_reconciliation`, `_commit_one_merge`, `_reject_drifted_targets` stay adapter-side.
- [ ] 12.4 GREEN: extend `test_lifecycle_seams.py`'s D2 guard to cover `BatchApplyPreview.confirmation` (no `granted`/`force`/`override` field); confirm `rg -c '"openkos\.cli\.main\.[A-Za-z_]+"' tests/unit/cli/test_adjudicate.py` still reduces by exactly the 4 sites already repointed in S1.

### Phase 13: Slice 5 gate

- [ ] 13.1 Gate: `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy .`; `uv run pytest tests/unit/application tests/unit/cli/test_adjudicate.py -q`; full `uv run pytest --cov=src/openkos` ≥ 90%.
- [ ] 13.2 Output-text-assertions-unmodified check: zero assertion changes across `test_adjudicate.py`'s 154 tests beyond the 4 patch-target literals already changed in S1. Falsify: mutate one character in a batch preview line and, separately, in the typed-count mismatch-abort wording; purge `__pycache__` each time; confirm RED then GREEN after reverting. Delete one of the 4 repointed patch lines from S1, confirm the spy-call assertion goes RED, restore.
- [ ] 13.3 Commit: `refactor(lifecycle): de-present adjudicate --apply and --apply-same into the application service`.

## Notes

Threat Matrix coverage (per `design.md`, only 4 rows applicable):

| Row | Task |
|---|---|
| Git repository selection | 2.2 (layering guard: `openkos.vcs` offender) |
| Commit state | 11.2 (purge rail-4 falsification) |
| Push state | 11.2 (purge rail-5 falsification) |
| Path-traversal deletion | 2.1 (relocated `resolve_concept_path`/`canonicalize_concept_id` refusal tests) |

`relate`, `set_volatility_cmd`, `reconcile`, and `curate` are out of scope
(D5/non-goals); no task in this file touches them. #918 stays open until
archive per the `ingest-application-service` precedent's own closing
convention.
