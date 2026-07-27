# Tasks: Let `init` choose the embedding model, with an explicit re-embed warning

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~230 production + ~290 tests ≈ 520 total (range 450–650) |
| 400-line budget risk | High |
| 800-line budget risk | Medium |
| Chained PRs recommended | Yes |
| Suggested split | PR1 (config/template) → PR2 (llm/reindex fatal) → PR3 (cli wiring) |
| Delivery strategy | auto-forecast (treated as auto-chain) |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

Rationale: total estimate is under the session's raised 800-line budget but comfortably over the skill's 400-line default, and the change already has three clean, independently testable/revertible boundaries per the design's own rollback plan — splitting costs little and keeps each PR under ~60 min review.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `config.py` allowlist/validator/write_config + template placeholder + docstring fix | PR 1 | `uv run pytest tests/unit/test_config.py -k "embedding or allowlist or write_config"` | N/A — pure unit, no live Ollama needed | Revert PR1: new workspaces fall back to default-only; already-written explicit keys still parse (per design rollback §1) |
| 2 | `OllamaEmbeddingDimensionMismatch` + `state/reindex.py` fatal handling | PR 2 | `uv run pytest tests/unit/llm/test_ollama.py tests/unit/state/test_reindex.py -k "dimension or mismatch"` | N/A — fake `_urlopen`/embedder fixtures per existing test patterns | Revert PR2 independently: restores prior transient (buggy) classification; purely additive type removal (design rollback §2) |
| 3 | `cli/main.py` flag/resolver/picker/sticky-warning/reindex ladder branch | PR 3 | `uv run pytest tests/unit/cli/test_init.py tests/unit/cli/test_reindex_cmd.py` | Manual: `openkos init` on a TTY against a local Ollama with `bge-m3` installed, confirm picker + sticky warning | Depends on PR1+PR2 merged; revert removes flag/picker/warning only, no data impact |

## Phase 1: Foundation — `config.py` + template (PR1)

- [x] 1.1 RED: `tests/unit/test_config.py` — assert `DEFAULT_EMBEDDING_MODEL in EMBEDDING_MODEL_ALLOWLIST`
- [x] 1.2 GREEN: add `EMBEDDING_MODEL_ALLOWLIST: tuple[str, ...]` in `config.py`, next to `DEFAULT_EMBEDDING_MODEL`, default first
- [x] 1.3 RED: `tests/unit/test_config.py` — `validate_embedding_model` parity with `validate_model` (blank, reserved YAML word case-insensitive, bad chars, leading/trailing `:`/`-`), independent of allowlist membership
- [x] 1.4 GREEN: extract `_validate_model_token(tag, field)` from `validate_model`'s body in `config.py`; add `validate_embedding_model` reusing it with a field-specific message
- [x] 1.5 RED: `tests/unit/test_config.py` — `write_config` substitutes both placeholders; raises when either placeholder count ≠ 1
- [x] 1.6 GREEN: extend `write_config` in `config.py` with a second, independent placeholder param for `embedding_model`
- [x] 1.7 GREEN: add `embedding_model: __OPENKOS_EMBEDDING_MODEL__  # 1024-dim; changing it forces a full re-embed` under `model:` in `src/openkos/templates/openkos.yaml.template`
- [x] 1.8 GREEN: correct `Config`'s stale docstring at `config.py:344-347` — it currently claims `embedding_model` is "not part of `openkos.yaml.template`"; remove/update that claim
- [x] 1.9 GREEN (collateral): update the byte-identity helper/assertions to account for the new template line — this is required breakage per design, not new scope. NOTE: the actual byte-identity comparisons live in `tests/unit/test_config.py`'s `_expected_config_bytes` helper (`test_write_config_byte_identical`, `test_write_config_ignores_directory_name`, `test_write_config_custom_model`), not in `tests/unit/cli/test_init.py` as originally worded — `test_init.py` has zero full-file byte-comparison assertions (verified: it only substring-checks `model: <value>` in content, and its `test_preflight_outcome_never_changes_written_files` compares snapshots across outcomes of the SAME run, not against a static template), so it needed no changes and all 61 of its tests pass unmodified.

## Phase 2: `llm/ollama.py` dimension error (PR2)

- [x] 2.1 RED: `tests/unit/llm/test_ollama.py` — a 768-length row raises `OllamaEmbeddingDimensionMismatch`, not generic `OllamaError`
- [x] 2.2 GREEN: add `OllamaEmbeddingDimensionMismatch(OllamaError)` in `llm/ollama.py`, message states actual and expected (`EMBED_DIM`) length
- [x] 2.3 GREEN: raise it from `_validate_embedding_row`'s wrong-length branch (bypasses the `except (JSONDecodeError, KeyError, TypeError, ValueError)` rewrap in `_embed_once` since it is not a `ValueError`)
- [x] 2.4 RED: `tests/unit/llm/test_ollama.py` — dimension mismatch triggers zero `sleep` calls (not retried)
- [x] 2.5 GREEN — **safety-critical ordering**: in `embed()`'s retry loop (~lines 197-207), add `except OllamaEmbeddingDimensionMismatch: raise` alongside the existing `except OllamaModelNotFound: raise`, BOTH placed BEFORE `except OllamaError:` (retry-with-backoff)
- [x] 2.6 RED: `tests/unit/llm/test_ollama.py` — non-numeric row still raises generic `OllamaError` (D7 scope-discipline regression guard)
- [x] 2.7 RED: `tests/unit/llm/test_ollama.py` — malformed JSON / missing vector key / singular `embedding` key parity unaffected by the new branch

## Phase 3: `state/reindex.py` fatal handling (PR2)

- [x] 3.1 RED: `tests/unit/state/test_reindex.py` — `OllamaEmbeddingDimensionMismatch` propagates out of `reindex`; `embed_failed` stays `0`; no `upsert_many`/`commit`/`write_model_tag` called
- [x] 3.2 GREEN — **safety-critical ordering**: at `state/reindex.py:272`, add `OllamaEmbeddingDimensionMismatch` to the fatal tuple `(OllamaUnavailable, OllamaModelNotFound)` — this clause MUST precede the broad `except OllamaError:` also at line 272
- [x] 3.3 RED: `tests/unit/state/test_reindex.py` — stderr message on this path names it a permanent dimension mismatch, never "will retry next run"
- [x] 3.4 GREEN: build the dedicated stderr message text for the dimension-mismatch fatal path
- [x] 3.5 RED: `tests/unit/state/test_reindex.py` — existing `OllamaUnavailable`/`OllamaModelNotFound` fatal-path scenarios remain unaffected (regression)

## Phase 4: `cli/main.py` wiring (PR3)

- [ ] 4.1 RED: `tests/unit/cli/test_init.py` — `--embedding-model` overrides picker, writes value, all other fields template-identical
- [ ] 4.2 GREEN: add `--embedding-model` option to the `init` command in `cli/main.py`
- [ ] 4.3 RED: `tests/unit/cli/test_init.py` — `_resolve_embedding_model` precedence: flag > picker > `DEFAULT_EMBEDDING_MODEL`
- [ ] 4.4 GREEN: add `_resolve_embedding_model` in `cli/main.py`, structurally mirroring `_resolve_model`
- [ ] 4.5 RED: `tests/unit/cli/test_init.py` — picker candidates are allowlist-only (NOT `is_embedding_model(m) and allowlisted`); `bge-m3` marked recommended; `InstalledModel(tag="bge-m3", family=None)` still appears (D2 regression guard — no `details.family` must not drop the default)
- [ ] 4.6 GREEN: add `_pick_embedding_model` in `cli/main.py` — own `list_models()` probe, same broad `except Exception`, same `_MAX_PICKER_ATTEMPTS`, same non-TTY silence as `_pick_chat_model`; filter candidates on allowlist alone
- [ ] 4.7 RED: `tests/unit/cli/test_init.py` — server tag `bge-m3:latest` normalizes via `ollama.model_tag_matches` and writes the allowlist spelling `bge-m3`, not the raw server tag (D3)
- [ ] 4.8 GREEN: wire that normalization into `_pick_embedding_model`'s selection/write path
- [ ] 4.9 RED: `tests/unit/cli/test_init.py` — off-allowlist `--embedding-model` value validates, writes, warns on stderr, exit code unaffected
- [ ] 4.10 GREEN: call `validate_embedding_model` on the flag path and print the off-allowlist warning
- [ ] 4.11 RED: `tests/unit/cli/test_init.py` — unreachable Ollama and zero allowlisted-candidate cases both fall back to default, exit 0, and reuse the chat picker's existing probe call (no second reachability request)
- [ ] 4.12 GREEN: confirm `_pick_embedding_model` reuses the shared probe result, `except Exception -> [] -> default`
- [ ] 4.13 GREEN: pass the resolved value into `write_config(embedding_model=...)` in `init`
- [ ] 4.14 RED: `tests/unit/cli/test_init.py` — sticky re-embed warning prints on every successful `init` (TTY and non-TTY), worded about future cost only, never present cost
- [ ] 4.15 GREEN: print the sticky warning unconditionally in `init`, next to the existing post-success Ollama preflight warning, after Phase B completes
- [ ] 4.16 RED: `tests/unit/cli/test_reindex_cmd.py` — the `reindex` CLI error ladder prints a dedicated message for dimension mismatch (names the fix: restore `embedding_model` in `openkos.yaml`, re-run) and exits 1, never "will retry next run"
- [ ] 4.17 GREEN — **safety-critical ordering**: in `cli/main.py`'s `reindex` ladder (~line 6066), add a dedicated `except OllamaEmbeddingDimensionMismatch` branch immediately after the `OllamaModelNotFound` branch, BEFORE the broad `except (VecUnavailable, FtsUnavailable, OllamaError):` tuple

## Phase 5: Verification (spans PR1–PR3, gate at each)

- [ ] 5.1 Run full local gate per PR: `uv run pytest`, `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy`
- [ ] 5.2 Confirm each rollback boundary independently (design rollback §1 config/template/cli, §2 llm/reindex)
- [ ] 5.3 File a named follow-up (issue, not code): `retrieval/answer.py:311` `_vector_hits` still degrades silently to FTS-only on a permanent dimension mismatch — deliberately out of scope here

## Follow-ups (not work in this change)

- `retrieval/answer.py:311` dimension-mismatch handling — named deferral, see 5.3.
- Consider filing a small linked sub-issue under #189 for the `reindex` dimension-mismatch fatal reclassification (PR2/Phase 2–3) for traceability, since it has no issue of its own; not required to land the change — #189 alone can track it given it's bounded and reviewed alongside the picker.
