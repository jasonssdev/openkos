# Tasks: `ingest-source-body` — embed raw source content into the Source body (MVP-1 value fix) + lint snapshot-skip

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~275 (`okf.py` ~15; `cli/main.py` ~25; `lint.py` ~12; `docs/cli.md` ~15; `test_okf.py` ~45 new; `test_ingest.py` ~80; `test_answer.py` ~50 new; `test_lint.py` ~35) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR — one cohesive value fix across `okf`/`ingest`/`lint`, all additive |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `okf.build_source_concept` raw_content param + `ingest` decode-guard/description + `lint` snapshot-skip + tests + docs | PR 1 (single PR, under budget) | `uv run pytest tests/unit/model/test_okf.py tests/unit/cli/test_ingest.py tests/unit/test_lint.py tests/unit/retrieval/test_answer.py` | `CliRunner ingest --auto` a distinctive-fact source, `state.fts.build_index`, `answer()` with fake `LLMBackend` seam (`tests/unit/retrieval/test_answer.py`) — no live Ollama; manual smoke against real Ollama deferred to user acceptance | `git revert`; additive param + guard + lint field + tests + docs, no schema/config/state change, `raw/` untouched |

## Phase 1: RED — `build_source_concept` body renderings
- [x] 1.1 `tests/unit/model/test_okf.py`: `test_build_source_concept_embeds_text_content` — `raw_content="hello"` → body has `## Source content` + `hello` verbatim, before `# Citations`.
- [x] 1.2 same file: `test_build_source_concept_binary_fallback_note` — `raw_content=None` → body has the honest "could not be embedded as text" note, no `## Source content`.
- [x] 1.3 same file: `test_build_source_concept_empty_source_note` — `raw_content=""`/whitespace → body has a distinct "source is empty" note, distinguishable from both other cases.

## Phase 2: GREEN — implement `raw_content` param (D1/D3)
- [x] 2.1 `src/openkos/model/okf.py`: add `raw_content: str | None = None` to `build_source_concept`; implement the 3-way body section (text / `None` / empty-whitespace) per design's `Interfaces` snippet; update the docstring's honesty note.

## Phase 3: RED — `ingest` decode guard + description branching (D2)
- [x] 3.1 `tests/unit/cli/test_ingest.py`: update `test_successful_ingest_of_valid_path` — assert body contains the source text verbatim under `## Source content`.
- [x] 3.2 same file: update `test_description_is_honest_no_extraction_claim` — new wording states content was "embedded", not extracted/compiled.
- [x] 3.3 same file: `test_undecodable_source_degrades_without_crashing` — write non-UTF-8 bytes as source, `ingest --auto` exits 0, raw copy byte-identical, concept body has the binary-fallback note, description states binary/non-text could not be embedded (Scenario: Undecodable source falls back).
- [x] 3.4 same file: `test_empty_source_renders_distinct_body` — zero-length source, `ingest --auto` exits 0, body has the distinct empty note (Scenario: Empty source renders a distinct body).
- [x] 3.5 same file: `test_decode_guard_precedes_generic_value_error` — monkeypatch `Path.read_text` to raise a plain `ValueError` (NOT `UnicodeDecodeError`) for the source path; assert `ingest` still fails (exit 1, stderr from the outer `except (OSError, ValueError)`), proving the specific guard does not swallow an unrelated `ValueError` (D2 ordering).

## Phase 4: GREEN — implement `ingest` changes
- [x] 4.1 `src/openkos/cli/main.py`: move `description` construction into the existing try block; add `try: raw_content = src.read_text("utf-8") except UnicodeDecodeError: raw_content = None` BEFORE the existing `except (OSError, ValueError)`; branch `description` text-vs-binary (single-line, honest); pass `raw_content=raw_content` to `build_source_concept`.

## Phase 5: RED — lint snapshot-skip (D4)
- [x] 5.1 `tests/unit/test_lint.py`: add `freshness: str = "current"` param to the `_doc` helper, pass through to `LintDoc(...)`.
- [x] 5.2 same file: `test_check_stale_stamps_skips_snapshot_docs_with_stamp_shaped_text` — `freshness="snapshot"` doc containing `(as of 2000-01-01)` → zero findings (Scenario: Snapshot concept with an embedded stamp-shaped string is not flagged).
- [x] 5.3 same file: `test_check_stale_stamps_still_flags_non_snapshot_docs` — same stale stamp with default (non-snapshot) `freshness` → still flagged (Scenario: Stale stamp is flagged, pinned against the new skip).

## Phase 6: GREEN — implement lint changes
- [x] 6.1 `src/openkos/lint.py`: add `freshness: str` field to `LintDoc`; in `collect_docs`, change `_, body = okf.load_frontmatter(text)` to `metadata, body = ...` and pass `freshness=str(metadata.get("freshness", ""))`; in `check_stale_stamps`, skip any doc with `freshness == "snapshot"`. `check_orphans` untouched.

## Phase 7: End-to-end confirmation (zero-change per design)
- [x] 7.1 `tests/unit/retrieval/test_answer.py`: add integration test — `CliRunner ingest --auto` a source with a distinctive phrase into `tmp_path`, `state.fts.build_index`, then `answer(q, bundle_dir, llm=_FakeLLM())`: assert citations reference the Source (not `NO_MATCH`) AND the fake LLM's received context contains the real text (Scenario: Query retrieves and cites ingested content). Confirms design's zero-change claim for `fts.py`/`answer.py`.

## Phase 8: Docs
- [x] 8.1 `docs/cli.md`: reword the `openkos ingest` section (lines ~56-72) — body now embeds verbatim source text under `## Source content`, queryable via `openkos query`; binary/non-text sources get an honest fallback note; empty files get a distinct note; still no LLM extraction/concept-splitting (MVP-2).

## Phase 9: Verification Gate
- [x] 9.1 `uv run pytest --cov` — full suite green; ≥90% branch on changed lines.
- [x] 9.2 `uv run ruff check .` && `uv run ruff format --check .` — clean.
- [x] 9.3 `uv run mypy .` — clean (strict).
