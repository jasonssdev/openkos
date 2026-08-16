# Apply Progress: Always Identify Named People (#712)

**Scope of this batch**: Phase 1 (Slice 1 — Volume Eval) ONLY, tasks 1.1–1.6,
plus the read-only investigation for task 0.1. Phases 2–5 NOT started —
Phase 0.2's gate blocks slice 2 until this report is evaluated, and that
evaluation is the orchestrator's job, not this batch's.

Branch: `feat/npc-slice1-volume-eval` (off `tracker/named-person-capture`).
Commit: `e7e46d8` — `feat(evals): measure named-person volume vs subject
recall (#712 slice 1)`.

## Mode

**Strict TDD.** Test runner: `uv run pytest`.

## TDD Cycle Evidence

| Task | RED | GREEN | REFACTOR |
|---|---|---|---|
| 1.1 `--self-test` skeleton | `test_run.py` written first; `uv run pytest evals/named_person_volume -k self_test` failed with `ModuleNotFoundError: No module named 'run'` (no `run.py` existed) | `run.py` implemented; same command passed | ruff/mypy clean pass, no further changes needed |
| 1.2 fixtures + adjudication | Covered by the same self-test RED/GREEN cycle (fixture-loading assertions in `_self_test`) | `es-bare`/`ami-ts3005a` fixtures + `adjudication.json` in place | `adjudication.json` re-authored after the real sweep with actual candidate text, not left as a placeholder |
| 1.3 metrics A-D + monkeypatch | Same self-test cycle: initial self-test run FAILED on `run_recall` expectation (`SELF-TEST FAILED: the scripted Decision title must satisfy the es-bare ventana/contexto expected-subject keywords`) — a genuine RED caught by the test, not by inspection | Fixed the assertion to match `run_recall`'s real semantics (0.5, not 1.0, since only 1 of 2 expected keyword-tuples was satisfied); GREEN | — |
| 1.6 mutation-confirm | N/A (this task IS the RED/GREEN mutation cycle) | Mutated `evaluate_reject_rule`'s exact target line (`<` → `<=`); `__pycache__` purged before; self-test FAILED (`SELF-TEST FAILED: a clean win on all four axes must ACCEPT (got ('subject recall dropped on es-bare: 1.00 < baseline 1.00',))`) — mutation caught | Reverted, purged `__pycache__` again, self-test GREEN |

## Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest evals/named_person_volume -k self_test` → `1 passed` |
| Runtime harness command/scenario and exact result | `uv run python -u evals/named_person_volume/run.py --runs 3` (real `qwen3:8b`, 12 runs attempted, 10 completed, 2 `OllamaGenerationCapped` errors on `ami-ts3005a`/treatment) → stored `results/named-person-volume-20260815T023755Z-qwen3-8b.jsonl`; `--rescore` re-derives the same VERDICT: REJECT with no model call |
| Rollback boundary | `rm -rf evals/named_person_volume/` — self-contained new directory, no production file touched, no shared file edited |

## Repo-wide checks (before commit)

- `uv run ruff check .` → All checks passed
- `uv run ruff format --check .` → 209 files already formatted
- `uv run mypy .` → Success: no issues found in 209 source files
- `uv run pytest evals/named_person_volume -k self_test` → 1 passed

## Completed Tasks

- [x] 0.1 Enumerate every reader of `ExtractionReport.produced`/`.retained`/`.discarded_titles` (read-only, no code changed). Full list recorded in `evals/named_person_volume/report.md` under "Task 0.1", reproduced below for slice 3:
  - **Genuine `ExtractionReport` readers (production)**: `src/openkos/cli/main.py` — `_judge_failure_notice` (~line 2909, reads `.retained`); `_extraction_cap_notice` (~lines 3087-3095, reads `.produced`/`.retained`/`.discarded_titles`).
  - **Eval/script readers (non-production, affected by D3's narrowing)**: `evals/extraction_collapse/run_collapse_probe.py`; `evals/extraction_cap/run_cap_eval.py`; `evals/decision_extraction/scripts/run_type_coverage.py`; `evals/participant_anchor/run_participant_anchor_probe.py` (design D3's own named consequence-2 site, `RunRecord.schema` marker planned there); `evals/named_person_volume/run.py` (this eval — its own `RunRecord` also stores `produced`/`retained`; slice 3 should decide whether this already-published report needs a retroactive schema stamp or is read as `schema: 1` by convention since it predates the marker).
  - **NOT readers of `ExtractionReport`** (same field names, different classes — checked and excluded): `src/openkos/cli/curate.py` / `src/openkos/resolution/candidates.py` (`find_candidates_report`'s own fields); `src/openkos/resolution/edge_typing.py` / `src/openkos/cli/main.py:11915` (pass-3 edge-typing's own fields); `evals/model_spike/run_spike.py`, `evals/model_spike/run_title_ab.py` (a local harness field unrelated to `ExtractionReport`).
- [x] 1.1 `--self-test` harness skeleton (no model), `uv run pytest`-visible.
- [x] 1.2 `es-bare` + AMI `TS3005a` fixtures; hand-authored `adjudication.json`.
- [x] 1.3 Metrics A-D + `p_max`/backstop derivation; `_TreatmentPatch` monkeypatch on `concept._PARTICIPANT_CAPTURE_SYSTEM_PROMPT`, production untouched.
- [x] 1.4 Real qwen3:8b sweep, stored JSONL.
- [x] 1.5 `report.md` with derived backstop and REJECT verdict.
- [x] 1.6 Mutation-confirmed scoring function.

## Files Changed

| File | Action | What Was Done |
|---|---|---|
| `evals/named_person_volume/run.py` | Created | Harness: fixtures, `_TreatmentPatch` monkeypatch, metrics A-D, `p_max`/backstop derivation, `evaluate_reject_rule`, `--self-test`, `--rescore`, `main` |
| `evals/named_person_volume/test_run.py` | Created | `uv run pytest`-visible wrapper calling `run._self_test()` |
| `evals/named_person_volume/adjudication.json` | Created | Hand-written `fixture::type::title -> "has-role"` labels, from real candidate description/body text after the sweep |
| `evals/named_person_volume/README.md` | Created | Harness documentation (arms, fixtures, metrics, REJECT rule) |
| `evals/named_person_volume/report.md` | Created | REJECT verdict, per-combination metrics, capacity number, task 0.1 reader enumeration |
| `evals/named_person_volume/results/named-person-volume-20260815T023755Z-qwen3-8b.jsonl` | Created | 12 stored runs (2 fixtures × 2 arms × 3 runs, 10 ok / 2 error) |
| `openspec/changes/named-person-capture/tasks.md` | Modified | Marked 0.1, 1.1–1.6 `[x]` |

## Result Summary (REJECT-rule verdict)

**VERDICT: REJECT.** Two of the four D2 conditions fire independently:

1. Subject recall drop: **did not fire** — 0.00 on both arms on both
   fixtures (baseline already at the floor; see report.md's "Subject
   recall is zero everywhere" finding — a real, pre-existing,
   orthogonal defect worth its own investigation, not a #712 regression).
2. **Run latency >= 1.5x baseline: FIRED.** Treatment 104.7s vs baseline
   54.6s (1.92x, threshold 82.0s) — driven partly by two of three
   `ami-ts3005a`/treatment runs failing outright at the shipped
   8192-token generation ceiling.
3. **Merely-named person count did not increase: FIRED.** Treatment 0 <=
   baseline 0, after hand adjudication — every retained `Person` on both
   fixtures in both arms already states a role or meeting action.
4. Fabrication: did not fire — every proposed name on the name-bearing
   fixture (`es-bare`) is a literal source name.

Per design D2: rejection ships nothing prompt-level. The D2 rewrite
(`_TREATMENT_CAPTURE_SYSTEM_PROMPT`) stays in the harness as a
reproducible monkeypatch.

`_PARTICIPANT_BACKSTOP = max(8, ceil(1.5 * p_max)) = 8` (`p_max = 3`,
derived, not chosen — the floor binds, not the multiplier).

## Deviations from Design

- **Design's exact wording split rules 2/3 by fixture vs global**:
  `evaluate_reject_rule` implements condition 1 (subject recall)
  per-fixture ("on either fixture," as design states explicitly) but
  conditions 2 (latency) and 3 (merely-named count) as GLOBAL aggregates
  across both fixtures pooled — the design's wording for those two does
  not say "per fixture," unlike condition 1's explicit qualifier. Reported
  here as an interpretation choice, not a silent deviation.
- **`ObjectRecord` gained `description`/`body` fields not explicit in
  design's D1 table.** The first sweep attempt stored only `type`/`title`
  and made metric B (merely-named count) impossible to hand-adjudicate
  honestly, since the shipped anchor-gate precedent
  (`evals/participant_anchor.CandidateRecord`) keeps description/body for
  exactly this reason. Caught before trusting the first sweep's numbers;
  the sweep was re-run after the fix (old, incomplete results file
  deleted, since it predated any report referencing it).
- **Metric B's candidate key is title-only (`fixture::type::title`), matching
  `evals/participant_anchor`'s own precedent**, but this sweep exposed a
  real limitation of that scheme: the SAME title can carry a role-stated
  description in most runs and a bare "meeting participant" description in
  one run (`es-bare` treatment run 3). The scheme cannot express a
  per-run split under one label; this is documented in `adjudication.json`
  and `report.md` rather than silently resolved either way.

## Issues Found

None beyond what is documented above and in `report.md`'s "Subject recall
is zero everywhere" section (a pre-existing pipeline finding, out of this
slice's scope to fix).

## Remaining Tasks

- [ ] 0.2 (Gate) — orchestrator's job, not this batch's: evaluate whether
  this report's REJECT verdict blocks or reshapes slice 2's plan.
- [ ] Phase 2 (Slice 2) — NOT started, gated by 0.2.
- [ ] Phase 3 (Slice 3) — NOT started, gated by 0.1's enumeration (now
  available above) and by slice 2.
- [ ] Phase 4 (Slice 4) — NOT started.
- [ ] Phase 5 (Verification) — NOT started.

## Workload / PR Boundary

- Mode: chained PR slice (`feature-branch-chain`, PR1 → tracker)
- Current work unit: Unit 1 — "Volume eval, capacity number"
- Boundary: starts from an empty `evals/named_person_volume/` and ends
  with a committed, self-contained eval harness + real measurement +
  report; no other directory touched.
- Estimated review budget impact: **forecast (~450-550 authored lines)
  undershot** — actual diff is ~1221 authored lines (excluding the
  generated JSONL, which the workload guard excludes from authored risk
  count) plus 12 lines of stored JSONL. `run.py` alone is 1059 lines,
  comparable in scale to `evals/participant_anchor/run_participant_anchor_probe.py`
  (859 lines) plus this file's more extensive `--self-test` (proving
  4 REJECT conditions independently, the treatment monkeypatch
  install/restore, the renamed-seam guard, and backstop derivation) and a
  4-metric scoring layer `participant_anchor` does not need. Flagging for
  the orchestrator: this PR is standalone, eval-only, touches no shared
  file, and is directly comparable in size/shape to an already-merged
  precedent (`participant_anchor`'s own PR) — worth confirming whether
  `size:exception` should be recorded explicitly given the forecast
  undershoot, even though the delivery strategy is already `auto-chain`.
