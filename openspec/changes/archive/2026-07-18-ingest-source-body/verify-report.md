# Verification Report: `ingest-source-body`

**Verdict**: PASS
**Mode**: Full artifacts (proposal/specs/design/tasks/apply-progress) — Strict TDD active

## Completeness

| Check | Result |
|---|---|
| Tasks complete | 19/19 (`[x]`) — no unchecked tasks |
| Apply-progress present | Yes (`sdd/ingest-source-body/apply-progress`, 19/19 tasks, ready for verify) |
| Deviations from design | None reported, none found on inspection |

## Build / Test / Coverage Evidence (re-run independently)

| Command | Exit | Result |
|---|---|---|
| `uv run pytest --cov=openkos --cov-report=term-missing` | 0 | 407 passed, 98.75% total coverage; `okf.py`/`lint.py`/`retrieval/answer.py`/`state/fts.py` all 100%; `cli/main.py` 99% (2 pre-existing branch misses at unrelated `forget`/`query` lines) |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 44 files already formatted |
| `uv run mypy .` | 0 | Success, no issues in 44 source files (strict) |

All four gates match the apply-progress report exactly — independently reproduced, not trusted blindly.

## Spec Compliance Matrix

### Capability: ingestion

| Requirement | Scenario | Test | Status |
|---|---|---|---|
| Ingest Raw Copy and Source Concept Generation | Successful ingest embeds verbatim text | `test_successful_ingest_of_valid_path` | PASS |
| | Path does not exist | `test_path_does_not_exist` | PASS |
| | Already-ingested source is refused, not overwritten | `test_collision_refuses_in_phase_a` | PASS |
| | Undecodable source falls back without crashing | `test_undecodable_source_degrades_without_crashing` | PASS |
| | Empty source renders a distinct body | `test_empty_source_renders_distinct_body` | PASS |
| Embedded Content Is Queryable End-to-End | Query retrieves and cites ingested content | `test_query_retrieves_and_cites_ingested_content` | PASS |

**Ingestion: 2/2 requirements, 6/6 scenarios — all covered by passing tests.**

### Capability: lint

| Requirement | Scenario | Test | Status |
|---|---|---|---|
| Stale-Stamp Scan | Stale stamp is flagged | `test_check_stale_stamps_flags_a_stamp_beyond_the_window` + `test_check_stale_stamps_still_flags_non_snapshot_docs` (pins skip to exactly `freshness == "snapshot"`) | PASS |
| | Fresh stamp is not flagged | `test_check_stale_stamps_does_not_flag_a_stamp_within_the_window` | PASS |
| | Pure-ingest bundle produces zero stale findings | `test_check_stale_stamps_pure_ingest_bundle_has_zero_findings` | PASS |
| | Snapshot concept with an embedded stamp-shaped string is not flagged | `test_check_stale_stamps_skips_snapshot_docs_with_stamp_shaped_text` | PASS |

**Lint: 1/1 requirement, 4/4 scenarios — all covered by passing tests.**

**Grand total: 3/3 requirements, 10/10 scenarios.**

## D2 Decode-Guard Ordering (CRITICAL check per orchestrator request)

Verified in `src/openkos/cli/main.py` (~line 260-276):

```python
try:
    raw_content: str | None = src.read_text(encoding="utf-8")
except UnicodeDecodeError:
    raw_content = None
...
except (OSError, ValueError) as exc:   # outer, unchanged
    ...
```

- The specific `except UnicodeDecodeError` guard sits INSIDE the outer `try`, wraps only `src.read_text`, and precedes the outer `except (OSError, ValueError)`. `UnicodeDecodeError` is a `ValueError` subclass, so the specific catch shadows the generic one for this exact call.
- Degrade path proven: `test_undecodable_source_degrades_without_crashing` writes non-UTF-8 bytes, runs `ingest --auto`, and asserts exit 0, byte-identical raw copy, and the honest binary-fallback body/description — i.e. the specific guard converts the decode failure into `raw_content=None` rather than failing the whole ingest.
- Ordering-sensitivity proven: `test_decode_guard_precedes_generic_value_error` monkeypatches `Path.read_text` to raise a **plain** `ValueError` (not `UnicodeDecodeError`) for the source path, and asserts `ingest` still exits 1 via the outer handler (stderr contains "failed", filesystem snapshot unchanged). This is the genuine non-decode-error case and it correctly still fails ingest — the specific guard does not swallow unrelated `ValueError`s.
- If the specific `except UnicodeDecodeError` guard were removed (D2's rejected alternative — "rely on the generic handler"), `test_undecodable_source_degrades_without_crashing` would fail: the `UnicodeDecodeError` would propagate to the outer `except (OSError, ValueError)`, `ingest` would exit 1 instead of 0, and none of that test's degrade-path assertions (exit 0, fallback body) would hold. The two tests together are ordering-sensitive proof, not just coverage.

**D2 verdict: confirmed correct and test-protected.**

## Zero-Change Confirmation (`state/fts.py` / `retrieval/answer.py`)

```
$ git diff -- src/openkos/state/fts.py src/openkos/retrieval/answer.py
(empty output, exit 0)
```

Confirmed byte-for-byte: neither file appears in `git status --short` for this change, and a direct `git diff` against both paths is empty. `test_query_retrieves_and_cites_ingested_content` (the Phase 7 integration test) exercises the full `ingest --auto` → `fts.build_index` → `answer()` loop and passed on first run per apply-progress — consistent with the design's zero-change claim being real rather than assumed.

## Lint Snapshot-Skip (D4) Confirmation

- `LintDoc.freshness: str` added; `collect_docs` now captures `metadata.get("freshness", "")` (previously discarded via `_, body = ...`).
- `check_stale_stamps` skips any doc with `freshness == "snapshot"` — verified in source (`src/openkos/lint.py` line 184-185).
- Snapshot doc with `(as of 2000-01-01)`-shaped text → zero findings: `test_check_stale_stamps_skips_snapshot_docs_with_stamp_shaped_text` (PASS).
- Same stamp shape, non-snapshot (`freshness="current"`) doc → still flagged: `test_check_stale_stamps_still_flags_non_snapshot_docs` (PASS) — this is the pinning test that proves the skip is scoped to exactly `freshness == "snapshot"`, not a broader/looser match.
- `check_orphans` confirmed untouched: `git diff -- src/openkos/lint.py` shows changes ONLY in the `LintDoc` dataclass, `collect_docs`, and `check_stale_stamps` docstring/skip line — zero lines changed in `normalize_link` or `check_orphans`.

## Ingest→Index→Answer Loop Depth Check

`test_query_retrieves_and_cites_ingested_content` (`tests/unit/retrieval/test_answer.py:364`) is not shallow:

- Runs a REAL `CliRunner ingest --auto` (not a stub) against a `tmp_path` workspace with a distinctive phrase (`"the flurbnorxal protocol requires triple validation"`).
- Calls the real `answer()` seam with only the `LLMBackend` faked (`_FakeLLM`) — FTS indexing, retrieval, and context assembly are all real production code paths.
- Asserts THREE independent things, not just "no crash": (1) `result.answer != NO_MATCH`, (2) the Source concept's `concept_id` is present among `result.citations`, (3) the fake LLM's captured `user_content` actually contains the distinctive phrase verbatim — i.e. the embedded content genuinely reached the LLM context, not just the FTS index.
- This proves retrievability AND citation AND context-feeding in one real round trip — a shallow test would stop at exit-code or citation-count only.

## Assertion Quality Audit

Reviewed all new/modified test bodies (`test_okf.py` +3, `test_ingest.py` +3 new/2 modified, `test_lint.py` +2 new, `test_answer.py` +1 new). No tautologies, no assertion-free tests, no ghost loops over possibly-empty collections, no smoke-test-only patterns. Every assertion checks either specific string content, exact ordering (`body.index(...) < body.index(...)`), exit codes, byte-identical file content, or absence of forbidden substrings (`"## Source content" not in body"`, etc.) — all exercise real production code paths.

**Assertion quality**: All assertions verify real behavior. 0 CRITICAL, 0 WARNING.

## Accepted Non-Blocking Follow-Ups (noted, not re-flagged as blockers)

Per orchestrator instruction, these two items from lineage review-0b49093a0c31876e are confirmed still present but are accepted follow-ups, not verification blockers:

1. No inline comment on the decode-guard ordering directly at the `except UnicodeDecodeError` site in `cli/main.py` (the docstring explains it; the D2 rationale lives in `design.md`).
2. `test_undecodable_source_degrades_without_crashing` does not itself assert the "not yet extracted" honesty clause on the binary branch (it asserts "binary"/"non-text" + "could not be embedded" in `description`; the "not yet extracted" wording is shared code with the text branch and is separately covered by `test_description_is_honest_no_extraction_claim` on the text path only).

Neither breaks a spec scenario or fails a test; both remain open, non-blocking documentation/coverage-completeness suggestions.

## Issues

**CRITICAL**: None.
**WARNING**: None.
**SUGGESTION**: None beyond the two explicitly-accepted follow-ups above (already tracked, not new).

## Final Verdict: PASS

All 19 tasks complete and match code state. 3/3 spec requirements, 10/10 scenarios covered by passing tests across both `ingestion` and `lint` capabilities. Full gate green (pytest/ruff check/ruff format/mypy, all exit 0). D2 decode-guard ordering independently confirmed correct and test-protected in both directions. Zero changes to `state/fts.py`/`retrieval/answer.py` confirmed via empty `git diff`. Lint snapshot-skip correctly scoped and `check_orphans` confirmed untouched. End-to-end ingest→index→answer loop test is genuinely deep, not shallow.

**Next recommended**: `sdd-archive` (after merge).
