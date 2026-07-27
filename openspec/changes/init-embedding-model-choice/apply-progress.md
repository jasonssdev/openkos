# Apply Progress: init-embedding-model-choice

## Scope covered so far

- PR1 (Phase 1: Foundation — `config.py` + template) — complete.
- PR2 (Phase 2: `llm/ollama.py` dimension error; Phase 3: `state/reindex.py`
  fatal handling) — complete.
- PR3 (Phase 4: `cli/main.py` wiring) — complete (THIS batch).
- Phase 5 (cross-PR verification/follow-up filing) — NOT started; the
  orchestrator handles this, per this run's instructions.

## Mode

Strict TDD (RED → GREEN → REFACTOR per task), all three batches.

## Completed Tasks — PR1 (Phase 1)

- [x] 1.1 RED: `tests/unit/test_config.py::test_default_embedding_model_in_allowlist`
- [x] 1.2 GREEN: `EMBEDDING_MODEL_ALLOWLIST: tuple[str, ...] = (DEFAULT_EMBEDDING_MODEL,)` in `config.py`
- [x] 1.3 RED: `validate_embedding_model` parity tests (trim/colon, unsafe values, YAML indicators, reserved words, off-allowlist acceptance)
- [x] 1.4 GREEN: extracted `_validate_model_token(tag, field)`; `validate_model`/`validate_embedding_model` both delegate to it
- [x] 1.5 RED: `write_config` dual-placeholder substitution + independent placeholder-count guard tests
- [x] 1.6 GREEN: `write_config(root, model=DEFAULT_MODEL, embedding_model=DEFAULT_EMBEDDING_MODEL)` — validates and substitutes both placeholders independently
- [x] 1.7 GREEN: added `embedding_model: __OPENKOS_EMBEDDING_MODEL__  # 1024-dim; changing it forces a full re-embed` under `model:` in `openkos.yaml.template`
- [x] 1.8 GREEN: corrected `Config`'s docstring; also corrected `DEFAULT_EMBEDDING_MODEL`'s own docstring, which made the same now-stale claim
- [x] 1.9 GREEN (collateral): updated `tests/unit/test_config.py`'s `_expected_config_bytes` helper for both placeholders

## Completed Tasks — PR2 (Phase 2 + 3)

- [x] 2.1 RED / 2.2 GREEN / 2.3 GREEN: `OllamaEmbeddingDimensionMismatch(OllamaError)` added to `llm/ollama.py`; `_validate_embedding_row`'s wrong-length branch raises it directly (D7)
- [x] 2.4 RED / 2.5 GREEN — safety-critical ordering: `embed()`'s retry loop `except (OllamaModelNotFound, OllamaEmbeddingDimensionMismatch): raise` placed BEFORE `except OllamaError:` (D8)
- [x] 2.6 RED / 2.7 RED: non-numeric-row and malformed/missing-key parity regression guards
- [x] 3.1 RED / 3.2 GREEN — safety-critical ordering: `state/reindex.py`'s fatal tuple extended to `(OllamaUnavailable, OllamaModelNotFound, OllamaEmbeddingDimensionMismatch)`, before the generic `except OllamaError:`
- [x] 3.3 RED / 3.4 GREEN: propagated message names it a permanent dimension mismatch, never "will retry next run"
- [x] 3.5 RED (regression): existing `OllamaUnavailable`/`OllamaModelNotFound` fatal-path tests unaffected
- [x] Added `test_reindex_dimension_mismatch_ordering_precedes_generic_ollama_error_catch` — dedicated ordering-proof test

## Completed Tasks — PR3 (Phase 4, THIS batch)

- [x] 4.1 RED / 4.2 GREEN: `--embedding-model` option added to `init`; flag wins outright, no picker shown even on TTY; other fields resolve to their defaults
- [x] 4.3 RED / 4.4 GREEN: `_resolve_embedding_model(flag, installed)` added, structurally mirroring `_resolve_model` — precedence flag > TTY picker > `DEFAULT_EMBEDDING_MODEL`
- [x] 4.5 RED / 4.6 GREEN: `_pick_embedding_model(installed)` added — candidates filtered on `EMBEDDING_MODEL_ALLOWLIST` ALONE (never `is_embedding_model(m) and allowlisted`); regression-guarded via `InstalledModel(tag="bge-m3", family=None)` still appearing as a candidate (D2)
- [x] 4.7 RED / 4.8 GREEN: candidate matching via `ollama.model_tag_matches` — a server-reported `bge-m3:latest` matches the allowlisted `bge-m3` entry, and the ALLOWLIST spelling (not the raw server tag) is listed and written (D3)
- [x] 4.9 RED / 4.10 GREEN: an off-allowlist `--embedding-model` value passes `validate_embedding_model` (YAML-safety only, D6), is written, and prints a non-fatal stderr warning naming it off-allowlist; exit code unaffected
- [x] 4.11 RED / 4.12 GREEN: unreachable Ollama and zero-allowlisted-candidate cases both fall back to `DEFAULT_EMBEDDING_MODEL` silently (no prompt at all, unlike the chat picker's typed-prompt fallback), exit 0; **reuses the chat picker's shared probe result — no second reachability request** (see Deviation 1 below)
- [x] 4.13 GREEN: resolved value passed into `config.write_config(root, model=..., embedding_model=...)`
- [x] 4.14 RED / 4.15 GREEN: sticky re-embed warning prints unconditionally on every successful `init` (TTY and non-TTY), worded about FUTURE cost only ("forces a full corpus re-embed the next time `openkos reindex` runs"), printed right after the existing post-success Ollama preflight block
- [x] 4.16 RED / 4.17 GREEN — safety-critical ordering: dedicated `except OllamaEmbeddingDimensionMismatch` branch added in `reindex`'s error ladder, immediately after the `OllamaModelNotFound` branch, BEFORE the broad `except (VecUnavailable, FtsUnavailable, OllamaError):` tuple; message names the remediation ("Restore the working 'embedding_model' value in openkos.yaml, then run `openkos reindex` again") and never says "will retry next run"

## Files Changed — PR3

| File | Action | What Was Done |
|------|--------|---------------|
| `src/openkos/cli/main.py` | Modified | Import `OllamaEmbeddingDimensionMismatch`; added `_probe_installed_models` (single shared reachability probe); `_resolve_model`/`_pick_chat_model` refactored to accept `installed: list[InstalledModel]` instead of probing internally; added `_resolve_embedding_model`/`_pick_embedding_model`; `init` gained `--embedding-model`, resolves both models from one shared Phase A probe, passes both into `write_config`, prints an off-allowlist warning and the unconditional sticky re-embed warning; `reindex`'s error ladder gained a dedicated `except OllamaEmbeddingDimensionMismatch` branch before the generic tuple |
| `tests/unit/cli/test_init.py` | Modified | 13 new tests (Slice C: embedding model wiring) covering flag precedence, allowlist-only picker filtering (D2 guard), `:latest` normalization (D3), off-allowlist warning, graceful degradation (unreachable / zero candidates), single-shared-probe proof, and the sticky warning (TTY + non-TTY). 2 pre-existing chat-picker tests updated (collateral, not new scope) — their assertions were scoped from "bge-m3 not in whole output" to "bge-m3 not in the chat-model section" specifically, since `bge-m3` now legitimately appears in the separate embedding-picker section when installed |
| `tests/unit/cli/test_reindex_cmd.py` | Modified | 1 new test: `test_reindex_dimension_mismatch_maps_to_exit_one_with_dedicated_message` — proves the dedicated branch's remediation text ("restore" + "openkos.yaml") is genuinely unreachable if the branch were reordered after the generic tuple (the generic branch never mentions those words) |

## TDD Cycle Evidence — PR3

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 4.1–4.4 | `tests/unit/cli/test_init.py` | Unit (CLI, via `CliRunner`) | ✅ 94/94 baseline (`test_init.py` + `test_reindex_cmd.py`) | ✅ `--embedding-model` unrecognized (exit 2) before the option existed | ✅ flag-precedence tests pass after `_resolve_embedding_model` + option added | ✅ 3 scenarios: flag-wins-on-TTY, flag-wins-over-picker-choice, non-TTY-default | ✅ extracted `_probe_installed_models` shared by both resolvers |
| 4.5–4.8 | `tests/unit/cli/test_init.py` | Unit | ✅ (same baseline) | ✅ `AttributeError`/`NameError` before `_pick_embedding_model` existed; D2/D3 assertions failed against old (nonexistent) picker | ✅ passed once `_pick_embedding_model` filters via `model_tag_matches` over `EMBEDDING_MODEL_ALLOWLIST` alone | ✅ `family=None` regression guard + `:latest` normalization as two distinct scenarios | ➖ None needed |
| 4.9–4.10 | `tests/unit/cli/test_init.py` | Unit | ✅ (same baseline) | ✅ failed (`--embedding-model` didn't exist, no warning path) | ✅ passed: value written, stderr warning present, exit 0 | ➖ Single scenario (off-allowlist warning is a binary branch) | ➖ None needed |
| 4.11–4.13 | `tests/unit/cli/test_init.py` | Unit | ✅ (same baseline) | ✅ `call_count == 1` failed pre-fix (2 separate probes: chat picker's own + a hypothetical embedding-own probe would have made it 3; measured 2 with the OLD/no-embedding-picker code since only chat-picker + preflight probed) | ✅ passed after hoisting the probe into `init` and threading it to both resolvers — `call_count == 2` (shared Phase A probe + pre-existing post-success preflight, unrelated feature) | ✅ unreachable-Ollama and zero-candidate scenarios both covered as distinct tests | ✅ `_pick_chat_model` refactored to accept `installed` param instead of probing internally |
| 4.14–4.15 | `tests/unit/cli/test_init.py` | Unit | ✅ (same baseline) | ✅ `'sticky' in result.stderr.lower()` failed (`''` — nothing printed) before the warning existed | ✅ passed once the unconditional `typer.echo` was added after the preflight block | ✅ TTY and non-TTY scenarios both covered as distinct tests | ➖ None needed |
| 4.16–4.17 | `tests/unit/cli/test_reindex_cmd.py` | Unit | ✅ 94/94 baseline | ✅ failed: `'restore' in result.stderr.lower()` was `False` — the generic tuple's message never mentions "restore"/"openkos.yaml" | ✅ passed once the dedicated branch was added BEFORE the generic tuple | ➖ Single scenario (ordering is binary; proven by exception-type routing, not by a second input variation) | ➖ None needed |

### Test Summary — PR3
- **Total tests written**: 14 (13 in `test_init.py`, 1 in `test_reindex_cmd.py`)
- **Pre-existing tests updated (collateral, not new scope)**: 2 in `test_init.py` (`test_picker_lists_chat_models_excludes_embedding`, `test_picker_zero_chat_models_falls_back_to_typed_prompt`) — both needed their `"bge-m3" not in ...` assertion re-scoped to the chat-model section specifically, since `bge-m3` legitimately now also appears in the new, separate embedding-picker section
- **Total tests passing**: 106/106 combined (`test_init.py` + `test_reindex_cmd.py`), up from 94 baseline
- **Layers used**: Unit (14), all via `CliRunner` (no live Ollama, no network)
- **Approval tests** (refactoring): None — `_pick_chat_model`'s signature change (probe hoisted out) was proven safe by re-running all 22 pre-existing chat-picker/preflight/TTY tests green unmodified (except the 2 collateral updates above, whose assertion SCOPE changed, not their intent)
- **Pure functions created**: 0 new pure functions; `_probe_installed_models` is a thin I/O wrapper, `_resolve_embedding_model`/`_pick_embedding_model` mirror the existing chat-model shape

## Work Unit Evidence — PR3

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/cli/test_init.py tests/unit/cli/test_reindex_cmd.py -q` → `106 passed in 4.02s` |
| Runtime harness command/scenario and exact result | N/A — all coverage via `typer.testing.CliRunner` against a faked `OllamaClient` (`_fake_ollama_client`/`_CountingFakeOllamaClient`), per the tasks.md forecast for Unit 3; no live Ollama needed for this batch. Manual TTY-against-real-Ollama verification (tasks.md's own suggested runtime harness) was NOT performed in this run — flagged as a risk below, deferred to Phase 5/verify |
| Rollback boundary | Revert `src/openkos/cli/main.py`, `tests/unit/cli/test_init.py`, `tests/unit/cli/test_reindex_cmd.py` only. Depends on PR1 (`config.py`/template) and PR2 (`OllamaEmbeddingDimensionMismatch`) already being merged; reverting PR3 alone removes the `--embedding-model` flag/picker/sticky-warning/reindex-ladder-branch only — no data impact, no other file touched |

## Local Gate (full repo) — after PR3

- `uv run pytest` → `2328 passed in 76.14s`
- `uv run pytest --cov` → `Required test coverage of 90.0% reached. Total coverage: 97.55%` (`2328 passed`)
- `uv run ruff check src tests` → `All checks passed!`
- `uv run ruff format --check src tests` → `140 files already formatted` (after auto-formatting `test_init.py` once)
- `uv run mypy` → `Success: no issues found in 140 source files`

## Deviations from Design — PR3

1. **Design D4 vs. the spec/tasks.md: shared probe, not `_pick_embedding_model`'s own probe.** `design.md`'s Decision D4 explicitly states `_pick_embedding_model` should run its OWN `list_models()` probe, structurally cloned from `_pick_chat_model`, and names "hoist one shared probe into `init`" as the REJECTED alternative. However, both `tasks.md` (4.11: "reuse the chat picker's existing probe call (no second reachability request)"; 4.12: "confirm `_pick_embedding_model` reuses the shared probe result") and the delta spec (`specs/workspace-init/spec.md`, "Graceful Degradation Of The Embedding Picker": "This probe MUST reuse the chat picker's existing probe call — it MUST NOT issue a second, separate reachability request") directly contradict D4's own rationale. I followed the spec and tasks.md (the concrete, reviewed acceptance criteria) over design.md's prose rationale: hoisted the probe into `_probe_installed_models()`, called once in `init` when needed, and threaded its result into both `_resolve_model`/`_pick_chat_model` (refactored to accept `installed: list[InstalledModel]` instead of probing internally) and `_resolve_embedding_model`/`_pick_embedding_model`. This is flagged here per the skill's instruction to note a design/spec conflict rather than silently deviate — the design document itself needs a correcting note if archived as-is; the implementation matches the spec, which is the acceptance-criteria source of truth.
2. **Off-allowlist flag warning wording is new (not in tasks.md verbatim), matching only the spec's requirement text.** Task 4.10 just says "print the off-allowlist warning" without wording; I wrote `"'{value}' is not on the vetted embedding-model allowlist; writing it anyway."` to satisfy the spec's "a non-fatal warning to stderr naming the value as off-allowlist" requirement.
3. **Probe-skip optimization not explicitly enumerated as its own task.** `init` only calls `_probe_installed_models()` when `sys.stdin.isatty() and (model is None or embedding_model is None)` — this avoids an unnecessary network call when both flags are given, or on non-TTY (matching prior chat-only behavior, which never probed unless the chat picker itself was reachable). This is implied by "no second reachability request" and by not regressing the prior model-flag-bypasses-picker behavior, but is worth flagging as an interpretation, not a literal task line item.

## Issues Found — PR3

None blocking. See Deviation 1 above (design/spec conflict on D4) — flagged for the archive phase to reconcile design.md's text with the shipped (spec-conforming) behavior.

## Remaining Tasks (out of scope for this run)

- [ ] Phase 5: Verification (5.1–5.3) — full-repo gate already run and green above (5.1); rollback-boundary confirmation (5.2) and follow-up filing (5.3) are the orchestrator's responsibility per this run's instructions

## Workload / PR Boundary

- Mode: stacked-to-main (chain strategy from tasks.md)
- PR1 (merged/prior batch): Unit 1 — `config.py`/template
- PR2 (merged/prior batch): Unit 2 — `OllamaEmbeddingDimensionMismatch` + `state/reindex.py` fatal handling
- PR3 (this batch): Unit 3 — `cli/main.py` wiring; branch `feat/init-embedding-model-picker`, cut from `main` at `427957e`, targets `main` per stacked-to-main
- Boundary: starts from `init` having no `--embedding-model` flag/picker/sticky-warning and `reindex`'s ladder having no dedicated dimension-mismatch branch; ends with all three wired, tested, and gated
- Estimated review budget impact: PR3 diff is `src/openkos/cli/main.py` (~180 new/changed lines) + 2 test files (~330 new/changed lines) ≈ ~510 lines — within the session's 800-line budget, over the skill's 400-line default (expected per tasks.md's own forecast; this is the last of three chained slices)

## Status

38/38 Phase 1–4 tasks complete (9 PR1 + 12 PR2 + 17 PR3). Ready for `sdd-verify` across the full change (PR1+PR2+PR3), and Phase 5 (5.2/5.3) for the orchestrator.
