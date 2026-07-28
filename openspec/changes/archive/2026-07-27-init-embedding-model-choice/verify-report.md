> **SUPERSEDED AT ARCHIVE TIME (2026-07-27)**: this report's `verdict: fail` /
> `blockers: 1` / `critical_findings: 1` reflect the state after PR1+PR2+PR3 only.
> PR4 ([#208](https://github.com/jasonssdev/openkos/pull/208), `7d44b2e`) closed the
> single CRITICAL finding below (the untested numbered-selection picker scenario) and
> the associated WARNING (uncovered reprompt/exhaustion branches) before this change
> was archived. See `archive-report.md` for the final, current state. This report is
> preserved unmodified as the historical verification record for PR1–PR3.

```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:cd25668224dfda8217c2527300c4d9ec5d7e4c30ba5e2608c1b470592b5784b3
verdict: fail
blockers: 1
critical_findings: 1
requirements: 10/10
scenarios: 36/37
test_command: uv run pytest -q
test_exit_code: 0
test_output_hash: sha256:0c8588f4210584441b7637dd4ae006b3b71b2c05239baa4ee43398dfcae4a333
build_command: uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
build_exit_code: 0
build_output_hash: sha256:0786becf372dfbeae4b578b0e8b49c5b78751f65157f726ce582676f2bb7bc1e
```

## Verification Report

**Change**: init-embedding-model-choice
**Version**: PR1 `444e513` + PR2 `427957e` + PR3 `584e3e1`, all merged to `main`, closing GitHub issue #189
**Mode**: Strict TDD

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total (Phase 1-4, this change's scope) | 38 |
| Tasks complete | 38 |
| Tasks incomplete | 0 |
| Phase 5 (5.1-5.3) | Orchestrator's responsibility, not counted here; 5.1's gate is independently reproduced below |

### Build & Tests Execution

**Build**: PASSED
```text
$ uv run ruff check src tests
All checks passed!
$ uv run ruff format --check src tests
140 files already formatted
$ uv run mypy
Success: no issues found in 140 source files
```

**Tests**: PASSED — 2330 passed / 0 failed / 0 skipped
```text
$ uv run pytest -q
2330 passed in 82.22s (0:01:22)
```
Matches the expected count from the session preflight exactly (2330). No discrepancy to report.

**Coverage**: 97.55% / threshold 90% → Above
```text
$ uv run pytest --cov
Required test coverage of 90.0% reached. Total coverage: 97.55%
2330 passed
```
`src/openkos/llm/ollama.py`: 100% (168/168 lines, 36/36 branches) — the D7/D8 dimension-mismatch code is fully line- and branch-covered.
`src/openkos/state/reindex.py`: 94% (81/87 lines; misses are pre-existing `model_reembedded` corner lines 240-245/254-256, unrelated to this change).
`src/openkos/cli/main.py`: 96% (1927/2014 lines) — see Coverage Honesty finding below for the one line range (365-372) inside this change's new code that is uncovered.
`src/openkos/config.py`: 96% — misses are pre-existing lines (534-601 area), not this change's new allowlist/validator code.

### Spec Compliance Matrix

**Domain: workspace-init** (`specs/workspace-init/spec.md`)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Static openkos.yaml Template (MODIFIED) | Byte-identical, default path | `test_config.py::test_write_config_byte_identical` + `test_init.py` non-TTY default tests | ✅ COMPLIANT |
| Static openkos.yaml Template | Flag override selects the model | `test_init.py::test_tty_prompt_custom_value` family / `--model` flag tests | ✅ COMPLIANT |
| Static openkos.yaml Template | Embedding flag override selects the embedding model | `test_init.py::test_embedding_model_flag_overrides_picker_and_writes_value` | ✅ COMPLIANT |
| Static openkos.yaml Template | TTY, picker preconditions hold, accept default | pre-existing chat-picker default tests (unmodified requirement, not new to this change) | ✅ COMPLIANT |
| Static openkos.yaml Template | TTY, picker preconditions hold, custom selection | `test_init.py::test_picker_numeric_choice_selects_and_persists` (chat) | ✅ COMPLIANT |
| Static openkos.yaml Template | Non-TTY, no flag, silent default | `test_init.py::test_embedding_model_non_tty_no_flag_resolves_to_default` | ✅ COMPLIANT |
| Static openkos.yaml Template | Blank input is rejected | `test_config.py::test_write_config_rejects_invalid_embedding_model` + `test_write_config_rejects_invalid_model` | ✅ COMPLIANT |
| Static openkos.yaml Template | Unsafe token is rejected | `test_config.py::test_validate_embedding_model_rejects_unsafe_values` | ✅ COMPLIANT |
| Static openkos.yaml Template | Reserved YAML boolean/null word rejected | `test_config.py::test_validate_embedding_model_rejects_yaml_reserved_words` | ✅ COMPLIANT |
| Vetted 1024-Dim Embedding Model Allowlist (ADDED) | Allowlist includes the packaged default | `test_config.py::test_default_embedding_model_in_allowlist` | ✅ COMPLIANT |
| Vetted 1024-Dim Embedding Model Allowlist | Allowlist gates only the picker, not the flag or manual edit | `test_config.py::test_validate_embedding_model_accepts_off_allowlist_value` + `test_init.py::test_embedding_model_flag_off_allowlist_writes_with_warning` | ✅ COMPLIANT |
| Interactive Embedding Model Picker (ADDED) | Picker lists installed allowlisted models, default marked | `test_init.py::test_embedding_picker_lists_allowlisted_candidate_with_default_marked` | ✅ COMPLIANT |
| Interactive Embedding Model Picker | Selecting a number picks that embedding model | **none** | ❌ UNTESTED (see finding below) |
| Interactive Embedding Model Picker | Empty input picks the default | `test_init.py::test_embedding_picker_lists_allowlisted_candidate_with_default_marked` (input `"\n\n"`) | ✅ COMPLIANT |
| Interactive Embedding Model Picker | Selection is persisted to openkos.yaml | same test, asserts `openkos.yaml` content | ✅ COMPLIANT |
| Graceful Degradation Of The Embedding Picker (ADDED) | Unreachable Ollama falls back, workspace created | `test_init.py::test_embedding_picker_unreachable_ollama_falls_back_to_default_silently` | ✅ COMPLIANT |
| Graceful Degradation Of The Embedding Picker | Zero allowlisted models installed falls back | `test_init.py::test_embedding_picker_zero_allowlisted_candidates_falls_back_silently` | ✅ COMPLIANT |
| Off-Allowlist Embedding Model Flag Is Warned, Not Blocked (ADDED) | Off-allowlist flag value is written with a warning | `test_init.py::test_embedding_model_flag_off_allowlist_writes_with_warning` | ✅ COMPLIANT |
| Sticky Re-Embed Warning On Every Successful Init (ADDED) | Warning prints on every successful init, TTY or not | `test_init.py::test_sticky_reembed_warning_prints_on_every_successful_init_tty` + `..._non_tty` | ✅ COMPLIANT |
| Sticky Re-Embed Warning On Every Successful Init | Warning worded about future cost on a fresh workspace | `test_init.py::test_sticky_reembed_warning_does_not_blame_another_workspace` + the TTY/non-TTY wording tests | ✅ COMPLIANT |

**Domain: llm-client** (`specs/llm-client/spec.md`)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| OllamaClient Embeds Text Via /api/embed (MODIFIED) | Successful embed call returns validated vectors | pre-existing embed tests, unaffected | ✅ COMPLIANT |
| OllamaClient Embeds Text Via /api/embed | Singular embedding key is accepted | `test_ollama.py::test_embed_singular_key_wrong_dimension_also_raises_dimension_mismatch` + pre-existing singular-key test | ✅ COMPLIANT |
| OllamaClient Embeds Text Via /api/embed | Malformed or non-numeric row raises generic OllamaError | `test_ollama.py::test_embed_row_non_numeric_values_raises_ollama_error`, `test_embed_malformed_json_raises_ollama_error` | ✅ COMPLIANT |
| OllamaClient Embeds Text Via /api/embed | Wrong-dimension row raises the distinct permanent error | `test_ollama.py::test_embed_row_wrong_length_raises_dimension_mismatch_not_generic_error` | ✅ COMPLIANT |
| Dimension Mismatch Is A Distinct Permanent Error (ADDED) | Dimension mismatch is never retried | `test_ollama.py::test_embed_dimension_mismatch_never_retried` | ✅ COMPLIANT |
| Dimension Mismatch Is A Distinct Permanent Error | Message names actual and expected dimension | `test_ollama.py::test_embed_dimension_mismatch_message_names_actual_and_expected_length` | ✅ COMPLIANT |
| Dimension Mismatch Is A Distinct Permanent Error | Subclass relationship preserves existing broad catches | `test_ollama.py::test_embed_dimension_mismatch_is_a_ollama_error_subclass` | ✅ COMPLIANT |

**Domain: reindex-command** (`specs/reindex-command/spec.md`)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Per-Doc Embed Failure Is Isolated, Not Fatal (MODIFIED) | One poison doc survives as partial-progress run | pre-existing `test_reindex.py` embed_failed test, unaffected | ✅ COMPLIANT |
| Per-Doc Embed Failure Is Isolated, Not Fatal | Survivors committed and immediately queryable | pre-existing integration coverage, unaffected | ✅ COMPLIANT |
| Per-Doc Embed Failure Is Isolated, Not Fatal | Every doc transiently fails leaves empty pass, not a crash | pre-existing `test_reindex.py` test, unaffected | ✅ COMPLIANT |
| Per-Doc Embed Failure Is Isolated, Not Fatal | Unreachable Ollama mid-loop is fatal | pre-existing `OllamaUnavailable` fatal-path test (regression-checked, task 3.5) | ✅ COMPLIANT |
| Per-Doc Embed Failure Is Isolated, Not Fatal | Missing embedding model mid-loop is fatal | pre-existing `OllamaModelNotFound` fatal-path test (regression-checked, task 3.5) | ✅ COMPLIANT |
| Per-Doc Embed Failure Is Isolated, Not Fatal | Dimension mismatch mid-loop is fatal, not a per-doc skip | `test_reindex.py::test_reindex_dimension_mismatch_mid_loop_propagates_and_stays_unrecorded` + `test_reindex_dimension_mismatch_ordering_precedes_generic_ollama_error_catch` | ✅ COMPLIANT |
| Reindex Surfaces An Actionable Re-Run Notice (MODIFIED) | Embed-failure skip prints the actionable re-run notice | pre-existing test, unaffected | ✅ COMPLIANT |
| Reindex Surfaces An Actionable Re-Run Notice | Ordinary unreadable-file skip does not print the notice | pre-existing test, unaffected | ✅ COMPLIANT |
| Reindex Surfaces An Actionable Re-Run Notice | Model-switch partial failure prints the same notice | pre-existing test, unaffected | ✅ COMPLIANT |
| Reindex Surfaces An Actionable Re-Run Notice | Dimension mismatch never reaches the transient re-run notice | `test_reindex.py::test_reindex_dimension_mismatch_message_is_permanent_never_will_retry` + `test_reindex_cmd.py::test_reindex_dimension_mismatch_maps_to_exit_one_with_dedicated_message` | ✅ COMPLIANT |

**Compliance summary**: 36/37 scenarios compliant (97.3%). One scenario is genuinely UNTESTED — see Issues below.

### Correctness (Static Evidence) — Named Traps

| Check | Status | Notes |
|---|---|---|
| Trap (a): `_pick_embedding_model` filters on allowlist ALONE, never `is_embedding_model(m) and allowlisted` | ✅ Confirmed | `cli/main.py:339-343`: `candidates = [allowed for allowed in config.EMBEDDING_MODEL_ALLOWLIST if model_tag_matches(allowed, installed_tags)]` — no `is_embedding_model` call. `InstalledModel(tag="bge-m3", family=None)` regression-guard test present and passing. |
| Trap (b): written value is the allowlist spelling, never `bge-m3:latest` | ✅ Confirmed | `_canonical_allowlist_spelling` (flag path) and `_pick_embedding_model`'s `candidates` list (picker path) both use `config.EMBEDDING_MODEL_ALLOWLIST` entries, never raw `installed_tags`. `test_embedding_picker_server_latest_tag_normalizes_to_allowlist_spelling` proves `bge-m3:latest` never lands in `openkos.yaml`. |
| Safety-critical ordering #1: `llm/ollama.py` `embed()` retry loop | ✅ Confirmed, reversal-sensitive test | `except (OllamaModelNotFound, OllamaEmbeddingDimensionMismatch): raise` precedes `except OllamaError:` (lines 216-218). `test_embed_dimension_mismatch_never_retried` would fail on reversal: a reversed order would retry, call `sleep`, and exhaust the fake `urlopen` sequence, raising a different exception than the `pytest.raises(OllamaEmbeddingDimensionMismatch)` expects. |
| Safety-critical ordering #2: `state/reindex.py` per-doc loop | ✅ Confirmed, reversal-sensitive test | `except (OllamaUnavailable, OllamaModelNotFound, OllamaEmbeddingDimensionMismatch): raise` precedes `except OllamaError:` (lines 267-286). `test_reindex_dimension_mismatch_ordering_precedes_generic_ollama_error_catch` asserts `embedder.call_count == 2` (loop stopped after doc 2, "zz.md" never reached) — a reversed order would let the loop continue to doc 3 and this assertion would fail. |
| Safety-critical ordering #3: `cli/main.py` `reindex` ladder | ✅ Confirmed, reversal-sensitive test | `except OllamaEmbeddingDimensionMismatch` (line 6236) is placed after `OllamaModelNotFound` and BEFORE `except (VecUnavailable, FtsUnavailable, OllamaError):`. `test_reindex_dimension_mismatch_maps_to_exit_one_with_dedicated_message` asserts `"restore"` + `"openkos.yaml"` in stderr — the generic branch's message never contains those words, so reversal would fail this assertion for real, not by coincidence. |
| Known contradiction: `design.md` D4 vs. shipped code | ✅ Confirmed as described, needs archive-time correction | `design.md` D4 (lines 22-26) states `_pick_embedding_model` should run its own `list_models()` probe and names a shared probe as the REJECTED alternative. Shipped code instead uses one `_probe_installed_models()` hoisted into `init` (line 152), matching `tasks.md` 4.11/4.12 and the `workspace-init` spec's "Graceful Degradation Of The Embedding Picker" requirement verbatim ("MUST reuse the chat picker's existing probe call — it MUST NOT issue a second, separate reachability request"). The code correctly follows the SPEC over the stale design prose. `apply-progress.md` already documents this deviation explicitly and flags it for archive-time correction of `design.md`. |
| `_pick_chat_model` signature change / issue #188 embedding-exclusion fix | ✅ Confirmed intact | `_pick_chat_model(installed: list[InstalledModel])` now receives `installed` instead of probing (line 202); its filter is unchanged: `if not is_embedding_model(m) and _is_selectable_model_tag(m.tag)` (line 227). Regression-checked by 22 pre-existing chat-picker tests, all passing unmodified except 2 collateral re-scoped assertions (documented in apply-progress.md). |

### Coherence (Design)

| Decision | Followed? | Notes |
|---|---|---|
| D1 allowlist location in config.py | ✅ Yes | `EMBEDDING_MODEL_ALLOWLIST` in `config.py`, next to `DEFAULT_EMBEDDING_MODEL`. |
| D2 allowlist-only picker filter | ✅ Yes | Confirmed above. |
| D3 allowlist-spelling normalization | ✅ Yes | Confirmed above. |
| D4 own probe per picker | ❌ No — deliberate, spec-driven deviation | See "Known contradiction" row above; this is the one design decision the shipped code does not follow, and it does not follow it correctly (spec and tasks.md both override it). |
| D5 shared `_validate_model_token` helper | ✅ Yes | `_validate_model_token(tag, field)` extracted and reused by both validators. |
| D6 off-allowlist flag warned, not blocked | ✅ Yes | Confirmed by `test_embedding_model_flag_off_allowlist_writes_with_warning`. |
| D7 dedicated exception raised directly from `_validate_embedding_row` | ✅ Yes | Bypasses `_embed_once`'s `ValueError` rewrap as designed. |
| D8 immediate-raise in `embed()`'s retry loop | ✅ Yes | Confirmed above. |

### Issues Found

**CRITICAL**:
1. **Spec scenario "Selecting a number picks that embedding model" (workspace-init, Interactive Embedding Model Picker) has no passing covering test — UNTESTED per the report-format.md compliance definitions.** `EMBEDDING_MODEL_ALLOWLIST` currently contains exactly one entry (`(DEFAULT_EMBEDDING_MODEL,)`), so no test — and no reachable production input today — can ever present the embedding picker with a second candidate to select by number. Line 364 of `cli/main.py` (`return config.validate_embedding_model(candidates[int(choice) - 1])`) shows as "covered" in the coverage report only because the default index (`1`) exercises the same line; it has never been proven to select a *non-default* index. Classified CRITICAL per the skill's decision gate ("Spec scenario has no passing covering test -> CRITICAL UNTESTED"), not because the code is defective: the logic is structurally identical to `_pick_chat_model`'s numbered-selection loop, which IS proven with two real candidates (`test_picker_numeric_choice_selects_and_persists`), and the scenario is currently unreachable dead code given the one-entry allowlist. This is why the verdict below is `fail` at the schema level while carrying zero code defects. Recommend: either (a) add a test that monkeypatches `EMBEDDING_MODEL_ALLOWLIST` to two entries and selects index 2, closing the gap now, or (b) explicitly accept the debt and require that test the moment the allowlist gains a second real entry — do not let the allowlist grow past one member without it. This is the sole reason `blockers: 1` / `critical_findings: 1` in the envelope above; every other check in this report is clean.

**WARNING**:
1. **The same picker's invalid-input reprompt and bounded-fallback branches are uncovered.** Coverage shows `cli/main.py:365-372` as missed — the "isn't a valid choice, reprompt" `typer.echo` and the post-`_MAX_PICKER_ATTEMPTS` fallback to `DEFAULT_EMBEDDING_MODEL`. The equivalent chat-picker branches (`test_picker_invalid_selection_reprompts_then_succeeds`, `test_picker_exhausted_invalid_selections_falls_back_to_default`) ARE tested; no embedding-picker analog exists. Not required by any explicit delta-spec scenario (the delta spec's "Interactive Embedding Model Picker" requirement does not enumerate an invalid-input scenario), so this is a test-completeness gap rather than a spec violation — bundling it with CRITICAL 1's fix (a two-entry monkeypatched allowlist) would close both at once.
2. **`design.md` D4 is stale and will mislead a future reader until corrected.** Confirmed real per the task brief: D4 explicitly rejects the shared-probe approach the shipped code uses, and the shipped code is correct (it follows the spec and tasks.md, both of which explicitly forbid a second reachability request). `apply-progress.md` already names this for archive-time correction; flagging again here so the verify record independently confirms it rather than taking the apply agent's word for it.

**SUGGESTION**:
1. `tests/unit/llm/test_ollama.py::test_embed_row_wrong_dimension_raises_ollama_error` (line 829) still asserts only `pytest.raises(OllamaError)` for a wrong-length row. Since `OllamaEmbeddingDimensionMismatch` subclasses `OllamaError`, this still passes but no longer proves what its docstring claims ("raises `OllamaError`" — technically true but no longer the most precise available assertion). The newer `test_embed_row_wrong_length_raises_dimension_mismatch_not_generic_error` (line 1147) already proves the specific subclass; consider retiring or retitling the older test at archive time to avoid the docstring appearing to contradict the more precise sibling test.
2. `config.py`'s `_PLACEHOLDER_RE` (built from a tuple of two module constants) and `write_config`'s `substitutions` dict (built from the same two constants) are two separate hand-maintained structures. Confirmed real: both currently reference the same `_MODEL_PLACEHOLDER`/`_EMBEDDING_MODEL_PLACEHOLDER` constants, so today they cannot drift — but a future third placeholder added to the regex tuple and forgotten in the `substitutions` dict would raise `KeyError` (loud, safe), while one added to `substitutions` but forgotten in the regex tuple would leave a literal `__OPENKOS_*__` token unsubstituted with no exception (silent, unsafe). Non-blocking readability finding from PR1, correctly scoped as informational.
3. `retrieval/answer.py`'s `_vector_hits` (line 312: `except (VecUnavailable, sqlite3.Error, OllamaError):`) still catches `OllamaEmbeddingDimensionMismatch` generically and degrades `query` to FTS-only silently. Confirmed real and correctly named as an out-of-scope follow-up in both `design.md`'s audit table and `tasks.md` 5.3. As of this verification, no GitHub issue exists yet for it (checked via `gh issue list`) — still open for the orchestrator to file per task 5.3.
4. No manual TTY run against a real Ollama server was performed for PR3, per `apply-progress.md`'s own honest disclosure; all picker coverage is `CliRunner` plus a faked `OllamaClient`. Confirmed accurate — acceptable given the change's low-risk, additive nature and the extensive faked-client coverage, but worth a manual smoke test before wide announcement of the feature.

### Review Workload Guard

Per-PR diffs (not the cumulative 1343-line total across all three merges): PR1 = 422 lines, PR2 = 297 lines, PR3 = 626 lines (`src`+`tests`, `git diff --shortstat`). All three individually fit under the session's 800-line budget; PR3 exceeds the skill's 400-line default as tasks.md's own forecast predicted, and the chained/stacked-to-main delivery strategy was followed as planned. No violation.

### Strict TDD Compliance (from apply-progress.md's PR3 "TDD Cycle Evidence" table, cross-checked against the codebase)

| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | ✅ | Found in `apply-progress.md`, PR3 section, full RED/GREEN/TRIANGULATE/SAFETY NET table for tasks 4.1-4.17 |
| All tasks have tests | ✅ | 5 task groups (4.1-4.4, 4.5-4.8, 4.9-4.10, 4.11-4.13, 4.14-4.15, 4.16-4.17), each with a named test file |
| RED confirmed (tests exist) | ✅ | `tests/unit/cli/test_init.py`, `tests/unit/cli/test_reindex_cmd.py` both exist and contain the referenced tests |
| GREEN confirmed (tests pass) | ✅ | All 2330 tests pass on this run's fresh execution, including every test named in the evidence table |
| Triangulation adequate | ✅ / ➖ | Most rows show 2-3 distinct scenarios; two rows ("off-allowlist warning", "reindex ladder ordering") are honestly marked single-scenario because the underlying behavior is a binary branch — consistent with actual test count |
| Safety Net for modified files | ✅ | 94/94 (later 106/106) baseline reported and reproduced |

**TDD Compliance**: 6/6 checks passed

### Assertion Quality
No tautologies, no assertion-free tests, no ghost loops found across `test_init.py`'s 14 new tests, `test_reindex_cmd.py`'s 1 new test, or the PR2 dimension-mismatch tests in `test_ollama.py`/`test_reindex.py` reviewed above. All new assertions call production code and assert distinguishing values (specific exception types, specific stderr substrings, specific written YAML content) rather than type-only or smoke-test-only checks. The one SUGGESTION-level assertion-precision note (test_embed_row_wrong_dimension_raises_ollama_error) is not a trivial/tautological assertion — it is a legitimate but now-superseded assertion.

**Assertion quality**: 0 CRITICAL, 0 WARNING (beyond the SUGGESTION already listed above)

### Verdict
**FAIL (schema-level, evidence-completeness) — zero functional defects found in shipped code**

`gentle-ai sdd-verify-validate` correctly refused a `pass` verdict against `scenarios: 36/37` (it rejects any "passing verdict contradicts failing or incomplete evidence"), so the authoritative envelope above reads `verdict: fail`, `blockers: 1`, `critical_findings: 1`. That single blocker is CRITICAL 1 above: one delta-spec scenario for the embedding picker's numbered-selection path has no covering test, because the shipped `EMBEDDING_MODEL_ALLOWLIST` has only one entry and the scenario's own precondition (two allowlisted candidates) cannot be reached with real data yet.

Everything else in this change is clean: all 38 in-scope tasks are complete and verified against the shipped code, not merely checked off. The full local gate reproduces exactly what apply-progress.md and the task brief predicted: 2330 passed, 97.55% coverage against a 90% gate, ruff/mypy clean — no discrepancy anywhere. Both named design traps (D2 allowlist-only filtering, D3 allowlist-spelling normalization) hold exactly as specified. All three safety-critical exception orderings (`llm/ollama.py` embed() retry loop, `state/reindex.py` per-doc loop, `cli/main.py` reindex ladder) are confirmed correct with genuinely reversal-sensitive tests, not type-match coincidences. The `_pick_chat_model`/#188 embedding-exclusion fix survived the signature change intact. The design.md D4 contradiction is real, the shipped code correctly follows the spec over the stale design prose, and it is already flagged (independently, by both the apply agent and this verify pass) for an archive-time correcting note.

Given the change is already merged to `main` and closes issue #189, the honest path is a named follow-up, not a revert: add (or explicitly defer, tracked) a test that monkeypatches `EMBEDDING_MODEL_ALLOWLIST` to two entries before the allowlist ever grows a second real member, and correct `design.md`'s D4 at archive time. Recommend archive proceeds with those two items carried forward as explicit follow-ups rather than silently dropped.
