# Tasks: `openkos curate` — dependency-ordered decision session

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Slice 1 (PR 1) est. changed lines | ~455 src (`curate.py` new, `main.py` command + `_merge_drift_targets`, docs) + ~450-550 tests |
| Slice 2 (PR 2) est. changed lines | ~415 src (relate/set-volatility extraction, 3 stage impls, docs) + ~470 tests |
| Review budget (per this change) | 800 changed lines/slice (overrides skill default 400) |
| Slice 1 vs budget | ~900-1000, **exceeds 800** |
| Slice 2 vs budget | ~885, **exceeds 800** |
| Delivery strategy | single-pr (each slice ships as one non-chained PR) |
| Chain strategy | stacked-to-main — PR 1 merges to main first, PR 2 opens against post-merge main |

Both slices forecast above the 800-line budget. Per instruction, the D10 slice boundary is fixed and
NOT split further; this is flagged here for reviewer awareness instead.

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Stage framework + Preconditions + Identity, all 5 `_STAGES` declared | PR 1 | `uv run pytest tests/unit/cli/test_curate.py` | `openkos curate` against a seeded bundle with pending merges | `git revert` of `cli/curate.py` + the `main.py` command/`_merge_drift_targets` hunk; nothing else imports `curate.py` |
| 2 | relate/set-volatility core extraction + Structure/Metadata/Contradictions made live | PR 2 | `uv run pytest tests/unit/cli/test_curate.py tests/unit/cli/test_relate.py tests/unit/cli/test_set_volatility.py` | `openkos curate --auto` against a bundle with untyped edges, volatility gaps, contradictions | Revert independently; PR 1's framework and Identity behavior are untouched |

## Slice 1 (PR 1): Stage framework, Preconditions, Identity

- [x] 1.1 RED: test `Stage`/`StageProbe`/`StageOutcome` dataclasses exist with design's fields (`tests/unit/cli/test_curate.py`)
- [x] 1.2 GREEN: implement the three frozen dataclasses in `src/openkos/cli/curate.py`
- [x] 1.3 RED: test `cost_line(stage, probe)` == `"{n} {noun} -> {n} LLM call(s)"`
- [x] 1.4 GREEN: implement `cost_line`
- [x] 1.5 RED: parametrized test for `gate()` over the D3 table (TTY×`--auto`, TTY decline, non-TTY no `--auto`, non-TTY `--auto` writes=False, non-TTY `--auto` writes=True)
- [x] 1.6 GREEN: implement `gate(stage, probe, ctx)` (depends on 1.2, 1.4)
- [x] 1.7 RED: test stage order over an all-findings bundle visits Preconditions→Identity→Structure→Metadata→Contradictions
- [x] 1.8 RED: test a declined/unavailable/not-live stage does not abort later stages
- [x] 1.9 GREEN: implement `run_curate(ctx)` sequencer — no cached state between iterations (depends on 1.6)
- [x] 1.10 RED: test a `live=False` stage's `probe` is never called (`AssertionError` sentinel) and still appears in the summary
- [x] 1.11 GREEN: implement `live=False` short-circuit in `run_curate`
- [x] 1.12 RED: test `render_summary` always returns 5 entries, even with nothing eligible
- [x] 1.13 GREEN: implement `render_summary(outcomes)`
- [x] 1.14 RED: test lazy `OllamaClient` build (no client if every gate declined); `OllamaUnavailable`/`OllamaModelNotFound` sets a run-scoped flag short-circuiting later `needs_llm` stages with no second connection; generic `OllamaError` fails only that stage
- [x] 1.15 GREEN: implement D7 lazy client + exception handling in `run_curate`
- [x] 1.16 RED: test missing/empty `vectors.db` prints the starved-candidate-edges consequence + `openkos reindex` pointer, exits 0, no later stage runs
- [x] 1.17 GREEN: implement Preconditions `probe`/`run` reusing `_open_proximity_or_degrade` + `next_action._tier_missing_vector_index`; `halts_run=True`, `needs_llm=False`
- [x] 1.18 RED: test accepted Identity pair commits per-item via `_prepare_one_merge`/`_commit_one_merge`
- [x] 1.19 RED: test N>2 candidate group prints pairwise `openkos merge` commands via `_echo_n_gt2_skip`, performs no merge
- [x] 1.20 RED: TOCTOU test — target mutated mid-confirm exits 3 via drift guard, nothing written
- [x] 1.21 GREEN: extract `_merge_drift_targets(layout, prepared)` from `merge`'s inline mapping (`main.py:5399-5410`); call `_reject_drifted_targets(layout, _merge_drift_targets(...), "curate")` from both `merge` and Identity's run loop, after confirm before `_commit_one_merge`
- [x] 1.22 GREEN: implement Identity `probe` (`find_candidates`) and `run` (`adjudicate_candidates` + per-pair apply) in `cli/curate.py`; `writes=True`, `unattended_hint="adjudicate --apply-same --confirm-count <n>"` (depends on 1.18-1.21)
- [x] 1.23 Declare all five `_STAGES` entries in D1 order in `cli/curate.py`; Structure/Metadata/Contradictions get `live=False` placeholder `probe`/`run`
- [x] 1.24 RED: test `curate` command wiring — `--auto`/`--include-confidential`/`--include-deprecated` forwarded to `CurateContext`; exit codes 0/1/2/3
- [x] 1.25 GREEN: add thin Typer `curate` command to `src/openkos/cli/main.py` (workspace gate → `read_config` → `warn_if_walk_incomplete` once → `run_curate` → print summary)
- [x] 1.26 RED: test `NO_COLOR=1` + piped stdout produce no ANSI codes or prompts
- [x] 1.27 RED: test the end-of-run summary names all five stage outcomes
- [x] 1.28 REFACTOR: wire `observability` progress helpers + `stage_notice("curate", ...)`; confirm `warn_if_walk_incomplete` fires once/run
- [x] 1.29 Add `openkos curate` entry to `docs/cli.md` (flags, stage order, exit codes)
- [x] 1.30 Run `uv run pytest tests/unit/cli/test_curate.py tests/unit/cli/test_merge.py tests/unit/cli/test_adjudicate.py` — full green, unedited regressions pass

## Slice 2 (PR 2): relate/set-volatility extraction, Structure, Metadata, Contradictions

- [x] 2.1 RED: test `prepare_relate` returns `PreparedRelate` with snapshot drift baseline, Phase A writes nothing
- [x] 2.2 GREEN: extract `prepare_relate` from `main.py:3715-3753` into `src/openkos/cli/main.py`
- [x] 2.3 RED: test `relate_core(prepared)` performs only `write_atomic` x2 (`main.py:3800-3801`), no commit, raises `OSError`/`ValueError`
- [x] 2.4 GREEN: implement `relate_core`
- [x] 2.5 Refactor `relate` command onto `prepare_relate`/`relate_core`, keeping gate/preview/confirm/drift-guard/echo (3686-3711, 3760-3775, 3777-3786, 3790-3797, 3808-3818) unchanged
- [x] 2.6 GATE: run `uv run pytest tests/unit/cli/test_relate.py` UNEDITED — must pass (D5/spec preservation proof)
- [x] 2.7 RED: test `prepare_set_volatility` returns `PreparedSetVolatility` with snapshot baseline, Phase A writes nothing
- [x] 2.8 GREEN: extract `prepare_set_volatility` from `main.py:4769-4776`
- [x] 2.9 RED: test `set_volatility_core(prepared)` performs only `write_atomic` (`main.py:4805`), no commit
- [x] 2.10 GREEN: implement `set_volatility_core`
- [x] 2.11 Refactor `set-volatility` command onto `prepare_set_volatility`/`set_volatility_core`, keeping vocab/gate/preview/confirm/guard/echo (4726-4767, 4781-4782, 4784-4793, 4800-4802, 4810-4819) unchanged
- [x] 2.12 GATE: run `uv run pytest tests/unit/cli/test_set_volatility.py` UNEDITED — must pass
- [x] 2.13 RED: test Structure's accepted edge-type suggestion writes via extracted `relate` core matching standalone output; declined suggestion writes nothing
- [x] 2.14 GREEN: implement Structure `probe` (`build_graph(..., candidates=source)` + `candidate_edges`) and `run` (`suggest_edge_types` with `on_progress`, accepted → `prepare_relate`/`relate_core`); noun `"untyped edge"` (depends on 2.4)
- [x] 2.15 RED: post-merge freshness test — seed all five finding kinds, merge in Identity, assert Structure's queue references the survivor
- [x] 2.16 RED: test Metadata's accepted tier writes via extracted `set-volatility` core; sensitivity gap reported only, naming `openkos set-sensitivity`, no write
- [x] 2.17 GREEN: implement Metadata `probe` (`lint.collect_docs` + `cfg.type_tiers`) and `run` (`suggest_volatility` with `on_progress`, one `llm.chat` call per concept TYPE, accepted → `prepare_set_volatility`/`set_volatility_core`); noun `"concept type"` (depends on 2.10)
- [x] 2.18 RED: test Contradictions runs last, calls `find_contradictions` with `on_progress`, never proposes/performs a write
- [x] 2.19 GREEN: implement Contradictions `probe`/`run` (`build_graph` + `find_contradictions`); `writes=False`, noun `"pair"`
- [x] 2.20 Flip `live=True` on Structure/Metadata/Contradictions descriptors in `_STAGES` without touching `Stage`/`gate`/`run_curate`/`render_summary`
- [x] 2.21 RED: test the full five-stage summary with no "not yet available" label remaining
- [x] 2.22 Update `docs/cli.md` `curate` entry — remove "not yet available" note
- [x] 2.23 Run `uv run pytest tests/unit/cli/test_curate.py tests/unit/cli/test_relate.py tests/unit/cli/test_set_volatility.py tests/unit/cli/test_merge.py tests/unit/cli/test_adjudicate.py tests/unit/cli/test_next.py tests/unit/cli/test_status.py` — full regression, unedited suites pass
</content>
