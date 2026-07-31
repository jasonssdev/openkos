# Verification Report: source-title-from-heading

**Change**: source-title-from-heading
**Mode**: Post-delivery verification of merged `main` (branch `main` @ `e0abe7f`)
**Delivered across**: PR #292 (`9d84eb1`), #293 (`7f29cdd`), #294 (`f6e62ae`), #295 (`e0abe7f`)
**TDD mode**: Strict TDD active, `uv run pytest`

## Command Evidence (verbatim)

```
$ uv run pytest -q
2831 passed in 93.14s (0:01:33)

$ uv run pytest tests/unit/test_source_title.py --cov=openkos.source_title --cov-branch -q
Name                          Stmts   Miss Branch BrPart  Cover   Missing
-------------------------------------------------------------------------
src/openkos/source_title.py      70      0     36      0   100%
-------------------------------------------------------------------------
TOTAL                            70      0     36      0   100%
Required test coverage of 90.0% reached. Total coverage: 100.00%
82 passed in 0.20s

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
156 files already formatted

$ uv run mypy .
Success: no issues found in 156 source files

$ git log --oneline -6
e0abe7f fix(ingest): reject the two invisible ranges the title guard still missed (#295)
f6e62ae fix(ingest): close the three findings the #248 review left open (#294)
7f29cdd feat(ingest): derive a Source's title from its content (#248) (#293)
9d84eb1 feat(ingest): add a pure title-derivation helper for Source concepts (#248) (#292)
afe92bf test(lint): prove the CR arm of the unspellable-resource contract (#291)
c1a1350 fix(model): stop calling a Source's filename a trusted input (#285) (#290)
```

## Completeness — Tasks

All 7 phases in `tasks.md` are checked. Every checked box was verified against code, not taken on faith:

| Phase | Tasks | Verified against code |
|---|---|---|
| 1 | 1.1–1.17 | `src/openkos/source_title.py` implements the single-walk design; `tests/unit/test_source_title.py` (82 tests) covers every branch named in the tasks (frontmatter probe, fence tracking incl. `~~~` and mismatched-marker non-closure, ATX H1 + closing-`#` strip, rule-(b) predicate per clause, forbidden-char class per member, length boundary at 120/121, CRLF, no-cascade, edge cases). 100% branch coverage confirmed by direct run. |
| 2 | 2.1–2.3 | `main.py:1741-1743` deletes the unconditional slug assignment and inserts derivation strictly between the UTF-8 decode and `_build_source_document`'s first call (`:1797`). `source_title` imported at `main.py:21`. `slug` (computed earlier, consumed before the decode) is untouched. |
| 3 | 3.1 | `test_stage_derived_objects_receives_the_final_derived_title` (`test_ingest.py:4107`) asserts on the captured fake-LLM prompt content, not just frontmatter. |
| 4 | 4.1–4.12 | Twelve integration tests (`test_ingest.py:3845-4064`) map one-to-one to spec scenarios; all assert real observable behavior (frontmatter, body H1, index/log labels, or explicit non-invocation via monkeypatch spy). |
| 5 | 5.1–5.5 | Confirmed zero fixture edits were required (verified by full-suite run, not just grep, per apply-progress record); boundary comment present near the fixture-churn analysis. |
| 6 | 6.1–6.5 | Full suite green, coverage ≥ 90% (actual 100%), ruff/ruff-format/mypy clean, no ADR created — all reproduced above. |
| 7 | 7.1–7.5 | `TestFrontmatterViaPublicApi` (public-API frontmatter tests), fence-blindness pin, `#`-prefix-without-space test, dead `_FENCE_MARKERS` splat removed from `_BLOCK_SYNTAX_PREFIXES` (confirmed absent in current source), spec rule (2) reworded to "only the first non-blank line is considered" (confirmed in `specs/ingestion/spec.md:41-43`) with a pinning test. All five confirmed present in code/spec, not just checked in the task list. |

No unchecked tasks. No task claims completion the code contradicts.

## Spec Compliance Matrix

Every Given/When/Then scenario in `specs/ingestion/spec.md` mapped to a passing test:

| Scenario | Test | Status |
|---|---|---|
| Successful ingest embeds verbatim text | pre-existing suite (unaffected) | PASS |
| Path does not exist | pre-existing suite | PASS |
| Already-ingested source is refused | pre-existing suite | PASS |
| Successful extraction yields a Concept/Entity/Multiple | pre-existing suite | PASS |
| Undecodable source falls back without crashing | `test_undecodable_source_degrades_without_crashing` | PASS |
| Empty source renders a distinct body | `test_empty_source_renders_distinct_body` | PASS |
| First ATX H1 becomes the title | `test_first_atx_h1_becomes_the_title` | PASS |
| An H1 inside a fenced code block is ignored | `test_h1_inside_a_fenced_block_is_ignored_later_real_h1_wins` | PASS |
| No H1, a title-plausible first line is used | `test_no_h1_title_plausible_first_line_is_used` | PASS |
| Wrapped prose first line is not title-plausible | `test_wrapped_prose_first_line_falls_back_to_slug_title` | PASS |
| A candidate carrying a forbidden character falls back | `test_forbidden_character_candidate_falls_back_to_slug_title` | PASS |
| A candidate over 120 characters falls back | `test_candidate_over_120_chars_falls_back_to_slug_title_no_truncation` | PASS |
| A well-formed leading frontmatter block is skipped | `test_well_formed_frontmatter_block_is_skipped` (integration) + `TestFrontmatterViaPublicApi` (unit) | PASS |
| An unclosed leading `---` is treated as content | `test_unclosed_leading_dashes_are_treated_as_content_falls_back_to_slug` (integration) + unit twin | PASS |
| A binary source uses the slug title | `test_binary_source_never_calls_derivation_keeps_slug_title` — asserts non-invocation via monkeypatch spy, not just the resulting title | PASS |
| An empty source uses the slug title | `test_blank_source_never_calls_derivation_keeps_slug_title` — same non-invocation assertion, parametrized over empty/whitespace-only; explicitly documented as replacing a prior vacuous test | PASS |
| Byte-identical re-ingest yields a byte-identical Source (Idempotent Title Derivation) | `test_reingest_of_identical_bytes_writes_a_byte_identical_source_document` — uses `_FixedClock`, asserts only `timestamp` lines differ | PASS |

All 19 delta-spec scenarios have a covering test that asserts the scenario's actual outcome, not merely that the command exits 0. No scenario is UNTESTED or vacuously tested.

## Design Coherence

- Module placement: `src/openkos/source_title.py`, top-level, zero `openkos` imports (stdlib `re` only) — matches design's chosen option.
- Public signature: `def derive_source_title(raw_content: str) -> str | None` — exact match.
- Single-pass walk: confirmed — one `for` loop over `lines`, `first_body_index` remembered by index, `lines[i+1]` read after the loop, no second pass.
- Fence masking: copied (not imported) as a 3-line inline state machine with local `_FENCE_MARKERS`; module docstring cites `bundle/links.py:50-74` and `graph/sqlite_graph.py` as sibling copies, per design.
- Call-site placement: derivation sits strictly between the UTF-8 decode (`raw_content` assignment) and `_build_source_document`'s first call, exactly as the data-flow diagram specifies. The design's own text had a stale line-number reference (`:1695` vs actual `:1689`), which the apply phase caught and corrected — recorded as a deviation, not a defect.
- `okf.build_source_concept`: confirmed unchanged — no validation added, docstring stance intact.

Two justified extensions beyond the original design (introduced to close review findings, not silent scope creep):
1. A blank/whitespace-only-content guard at the call site (`main.py:1738-1743`) so `derive_source_title` is never invoked on blank input, closing a previously-vacuous test.
2. `_read_source_title` + a retitle clause in the re-ingest preview (`main.py:1169-1197`, `1925-1931`), addressing a review finding that re-ingest silently overwrote a pre-existing Source's title with no mention in the preview. Explicitly does not make title sticky — re-ingest still recomputes from content every run.

Both are traceable to the review-findings record in the apply-progress artifact and to merged PR history (#294, #295); neither contradicts a spec requirement.

## Non-Goals — Confirmed Held

- **No backfill**: confirmed no backfill code exists; `openkos list` still shows slug titles for every pre-existing Source. Recorded explicitly in `proposal.md` ("Out of Scope") and `design.md` ("Migration/Rollout" — "No migration. No backfill"), not merely implied.
- **No lint check for "title equals slug"**: confirmed absent from `src/openkos/lint.py`. Recorded as a stated non-goal in `proposal.md`.
- **No escaping added at render sites**: `index.py`/`log.py` still only reject `\n`/`\r` by raising; no new escaping introduced.
- **Source's own YAML `title:` not read**: confirmed — `_frontmatter_end` only locates the frontmatter block to skip it; nothing reads its `title:` key. Test `test_well_formed_frontmatter_block_is_skipped` explicitly proves the frontmatter's own `title:` is ignored.
- **No setext heading support**: confirmed — grep finds no `setext` reference in source or tests; only ATX (`# `) H1s are matched.
- **Variation Selectors Supplement (`U+E0100`-`U+E01EF`)**: NOT rejected by `_FORBIDDEN_IN_TITLE` — confirmed by reading the regex. This is a genuine, deliberate gap. It IS recorded: PR #295's merged description states verbatim, "The review noted the Variation Selectors Supplement (`U+E0100`–`U+E01EF`) as a sibling vector reaching the same sink. It marked this pre-existing, not introduced here, so it is left for a separate decision rather than folded in silently." This record lives in GitHub PR history (`gh pr view 295`), not in any spec/design/proposal file in the repo — a future reader who does not check PR history will not find it. **GitHub issue #296 was filed for it**, with a verified reproduction against `e0abe7f`.

## RESOLUTION — Gap Addressed

The verify WARNING about the Variation Selectors Supplement being recorded only in PR history is now RESOLVED via the filing of GitHub issue #296. The decision and its rationale are now discoverable from issue #296 in the repo's own issue tracker, independent of GitHub PR retention.

## Verdict

**PASS WITH WARNINGS RESOLVED.**

Full test suite (2831 tests) passes. `source_title.py` has 100% branch coverage. Ruff, ruff format, and mypy strict are all clean. Every Given/When/Then scenario in the delta spec has a corresponding test that asserts the scenario's actual outcome. Every task in `tasks.md` (Phases 1-7) is genuinely complete, verified against code, not just against the checkbox. The design's core decisions (module placement, signature, single-pass walk, copy-not-import fence masking, call-site placement) all shipped as specified, with two justified, review-driven extensions beyond the original design that do not contradict any spec requirement. All stated non-goals hold. The prior documentation-durability gap for the Variation Selectors Supplement gap has been resolved by filing GitHub issue #296.
