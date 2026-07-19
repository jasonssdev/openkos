# Tasks: LLM Concept/Entity Extraction (add-llm-concept-extraction)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~750-950 (additions+deletions) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 extraction/concept.py -> PR2 okf.build_concept -> PR3 bundle/index.py -> PR4 main.py wiring+docs |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main (PR1-3 additive/unused-until-wired, non-breaking standalone; PR4 wires them) |

Decision needed before apply: Yes (resolved -- stacked-to-main)
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | extraction/concept.py: prompt, fail-closed parse, validation | PR 1 | `uv run pytest tests/unit/extraction/` | N/A (unwired leaf, fake-LLM only) | delete `extraction/` package |
| 2 | okf.build_concept validated builder | PR 2 | `uv run pytest tests/unit/model/test_okf.py` | N/A (unused until wired) | revert `build_concept` addition |
| 3 | bundle/index.py generalized section-aware inserter | PR 3 | `uv run pytest tests/unit/bundle/test_index.py` | N/A (thin wrapper keeps callers byte-identical) | revert `insert_index_entry`, restore original `insert_source_entry` |
| 4 | main.py ingest() wiring, preview, Phase B write, docs | PR 4 | `uv run pytest tests/unit/cli/test_ingest.py` | manual: `openkos init && openkos ingest sample.txt` against local Ollama, inspect `bundle/concepts\|entities/` -- ALSO exercised via a real `CliRunner` invocation against an unreachable local OllamaClient (no mocking), confirming Source-only degrade, exit 0 | revert extraction call + second write; Source-only ingest restored |

## Phase 1: Foundation — WU1 (COMPLETE)

- [x] 1.1 Create `src/openkos/extraction/__init__.py` (empty leaf pkg, mirrors `retrieval/__init__.py`)
- [x] 1.2 Create `tests/unit/extraction/__init__.py`
- [x] 1.3 Add import-graph guard test: `extraction/` and `llm/` import no `openkos.config`

## Phase 2: extraction/concept.py — WU1 (COMPLETE)

- [x] 2.1 [RED] `tests/unit/extraction/test_concept.py` with fake `LLMBackend` (copy `_FakeLLM`, test_answer.py:41-50): well-formed Concept; well-formed Entity; fenced ```json; JSON-in-prose; `extract:false` -> `None`
- [x] 2.2 [RED] fail-closed scenarios: malformed JSON -> `None`; type outside `{Concept,Entity}` -> `None`; empty/missing title -> `None`; blank body falls back to description; `chat` raising `OllamaError` propagates unswallowed
- [x] 2.3 [GREEN] Implement `ExtractionResult`, `_SYSTEM_PROMPT` (closed vocab, 3-test heuristic, prefer-Concept-over-Entity, JSON-only), `extract_concept(source_text, *, source_title, llm)` — 2-msg chat, 3-step parse (raw/fenced/regex), validation
- [x] 2.4 [REFACTOR] `uv run pytest tests/unit/extraction/` green; tidy prompt/parse helpers

## Phase 3: okf.build_concept — WU2 (COMPLETE)

- [x] 3.1 [RED] `tests/unit/model/test_okf.py`: conformant frontmatter (type/title/description/tags/timestamp/status/version/freshness/sensitivity/provenance=[f"sources/{slug}"]), body w/ `## Related` backlink, sensitivity inherited verbatim
- [x] 3.2 [RED] `ValueError` on type outside `{Concept,Entity}`; `ValueError` on blank title/description
- [x] 3.3 [GREEN] Implement `okf.build_concept(*, type, title, description, body, provenance, sensitivity, timestamp)` parallel to `build_source_concept`, with validation gate
- [x] 3.4 [REFACTOR] `uv run pytest tests/unit/model/test_okf.py` green; confirm `check_conformance` regression unaffected

## Phase 4: bundle/index.py generalized inserter — WU3 (COMPLETE)

- [x] 4.1-4.5 -- see `sdd/add-llm-concept-extraction/apply-progress` for full detail. `insert_index_entry(index_text, *, section, link_dir, title, slug, description)` generalized; `insert_source_entry` is a thin wrapper; `_CANONICAL_SECTION_ORDER = ("Concepts", "Entities", "Decisions", "People", "Sources")`. `uv run pytest tests/unit/bundle/test_index.py` -> 43/43 green.

## Phase 5: cli/main.py ingest() integration — WU4 (COMPLETE)

- [x] 5.1 [RED->GREEN] `tests/unit/cli/test_ingest.py`: fake-LLM Concept reply -> Source + `bundle/concepts/<slug>.md` both written, provenance references Source, `check_conformance` clean; Entity reply -> `bundle/entities/<slug>.md`
- [x] 5.2 [RED->GREEN] malformed JSON / invalid type / empty title -> Source-only, stderr degrade note, exit 0; `chat` raising `OllamaError` -> Source-only, stderr "concept extraction skipped -- {exc}", exit 0; additionally covered two edge cases beyond the original task list: extracted-title-slugifies-empty (fail-closed slug guard) and `okf.build_concept` raising `ValueError` on an embedded-newline title that slipped past `extract_concept`'s own validation -- both degrade to Source-only, never crash
- [x] 5.3 [RED->GREEN] `--auto` runs extraction, skips prompt, writes both; interactive preview lists both proposed objects before confirm, declining aborts with no files written
- [x] 5.4 [RED->GREEN] re-ingest with existing (possibly hand-edited) derived file -> byte-unchanged; `sensitivity=confidential` source -> derived object inherits it; `index.md` lists Source under `# Sources` and derived object under `# Concepts`/`# Entities`, `log.md` records both writes; additionally covered: a re-ingest of an identical Source that newly succeeds at extraction (LLM declined the first time) still stages and writes a fresh derived object under the `regenerate` preview banner
- [x] 5.5 [GREEN] Wired `extraction.extract_concept` into Phase A after `okf.build_source_concept` via new `_stage_derived_object` helper; derives slug from the extracted title using the SAME `_slugify` helper Source slugs use (not a second scheme); builds `bundle/concepts|entities/<slug>.md` path; calls `okf.build_concept`; extends the SAME index/log diff via `bundle_index.insert_index_entry(section="Concepts"|"Entities", link_dir="concepts"|"entities", ...)`; skips the derived write entirely (returns `None`, no catalog/log entry) if the target path already exists (create-only, idempotent)
- [x] 5.6 [GREEN] `OllamaError` caught locally inside `_stage_derived_object` (never inside `extraction/concept.py`, which still lets it propagate per design); emits the two distinguishing degrade notes verbatim per design wording (`no concept extracted from this source; keeping the Source only.` vs `concept extraction skipped -- {exc}; keeping the Source only.`); exit 0 preserved in every degrade path, verified both by the fake-LLM test suite AND a manual `CliRunner` invocation against a real unreachable `OllamaClient`
- [x] 5.7 [GREEN] Extended preview (both `regenerate` and fresh branches) and Phase B with the second create-only write (`fsio.write_exclusive`, directory `mkdir(parents=True, exist_ok=True)` on demand) landing BEFORE `index.md`/`log.md` (content-before-catalog invariant preserved); catalog-last unchanged; success message extended to mention the derived path when one was written
- [x] 5.8 [REFACTOR] `uv run pytest tests/unit/cli/test_ingest.py` green (42/42: 27 pre-existing untouched + 15 new extraction scenarios); existing Source-only ingest tests unaffected (zero assertions weakened, zero test bodies of pre-existing tests modified beyond the shared autouse LLM fixture); `ruff format`/`ruff check`/`mypy` clean

## Phase 6: Verification + Docs — WU4 (COMPLETE)

- [x] 6.1 Run `uv run pytest` (full suite) — 513 passed, zero failures/regressions; `--cov=openkos` 98.89% total (gate 90%), `cli/main.py` at 99% (the 4 remaining uncovered lines are pre-existing gaps in `_resolve_concept_path`/`doctor`, untouched by this slice, confirmed via `git diff` showing zero changes to those functions)
- [x] 6.2 Updated `docs/cli.md` ingest section: new "Extraction: one derived Concept or Entity, or a graceful degrade" subsection covering the `{Concept, Entity}` vocab, all three degrade categories (no text / declined-or-invalid / LLM unavailable) with their distinguishing stderr wording, create-only idempotency, `--auto` semantics (extraction still runs, only the prompt is skipped), and updated the "Not in this slice" list to the post-slice non-goals (multiple derived objects, other 9 OKF types, entity resolution, relationship graph)
- [x] 6.3 Updated `docs/user-journey.md`: the core-loop "In MVP 1: compile" line (~34), the Step 2 Compile section (~80) reframed from "null compiler" to "embeds verbatim + attempts one extraction step", the Step 3 preview example annotated with the extraction-success preview line, the Step 4 Commit paragraph, the Step 5 query citation-chain paragraph (previously claimed "no separate extracted topic page yet" -- now inaccurate, corrected), and the "Editing by hand" hand-edit-protection paragraph — all kept under the "In MVP 1: / Later MVPs:" honesty convention, no overselling (still at most ONE derived object, no multi-concept, no relationship graph, no drafted summary)
- [x] 6.4 Confirmed import-graph guard (1.3) still green as part of the full-suite run (6.1)

## Status

25/25 tasks complete (Phase 1: 3/3, Phase 2: 4/4, Phase 3: 4/4, Phase 4: 5/5, Phase 5: 8/8, Phase 6: 4/4). ALL WORK UNITS (WU1-WU4) COMPLETE. Ready for sdd-verify.
