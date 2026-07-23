# Tasks: Two-Output Rule — `query --save`

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~115 authored non-test + ~200 test LOC (~315 total) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-chain |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Ship `query --save` end-to-end (okf.py + main.py + spec correction) | PR 1 | `uv run pytest tests/ -k "build_concept or stage_filed_answer or query_save or query_purity"` | `openkos query "<question>" --save --auto` against a scratch workspace | Revert `build_concept` kwarg, `_FiledAnswerPlan`/`_stage_filed_answer`, and the query save block — flag is additive, no migration |

## Phase 1: Spec Correction (blocks nothing, but must land with the behavior it describes)

- [ ] 1.1 Fix `openspec/changes/two-output-rule/specs/query-command/spec.md`: in "Sensitivity Is The High-Water-Mark" requirement, change the zero-citations behavior from "fall back to default, filing still proceeds" to "REFUSE, exit non-zero, no write" — matches design decision (build_concept requires non-empty provenance).
- [ ] 1.2 Rewrite the "Zero citations fall back to the configured default" scenario as "Zero citations refuse to file": GIVEN zero readable citations, WHEN `--save` is passed, THEN `query` refuses, exits non-zero, bundle unchanged.

## Phase 2: `build_concept` — byte-identical parameterization (model/okf.py)

- [ ] 2.1 RED: add golden test asserting `build_concept(...)` output (no `related_note` passed) is byte-identical to the current ingest golden fixture.
- [ ] 2.2 RED: add test asserting a custom `related_note="concept cited to produce this answer"` renders in the `## Related` section instead of the default phrase.
- [ ] 2.3 GREEN: add `related_note: str = "source this was extracted from"` trailing kwarg to `build_concept` (okf.py:140); update the `## Related` render (okf.py:197-199) to use `related_note` in place of the hardcoded phrase.
- [ ] 2.4 GREEN: run full ingest test suite to confirm no regression (existing ingest calls never pass `related_note`).

## Phase 3: `_stage_filed_answer` helper (cli/main.py)

- [ ] 3.1 RED: unit test — provenance equals `[c.concept_id for c in citations]`.
- [ ] 3.2 RED: unit test — title/description default to the question; `--title`/`--description` overrides apply.
- [ ] 3.3 RED: unit test — `--type` override validated against `CLASSIFIABLE_TYPES`; invalid type raises `ValueError`.
- [ ] 3.4 RED: unit test — zero citations raises `ValueError` ("nothing to file -- the answer cited no concepts...").
- [ ] 3.5 RED: unit test — empty slug (after `_slugify(title)`) raises `ValueError`.
- [ ] 3.6 RED: unit test — `path.exists()` collision raises `ValueError` ("a concept already exists at bundle/<link_dir>/<slug>.md...").
- [ ] 3.7 RED: unit test — sensitivity folds via `okf.combine_sensitivity` seeded at `cfg.default_sensitivity`; unreadable cited concept is skipped (floor holds).
- [ ] 3.8 RED: unit test — high-water-mark: one confidential cited concept (surfaced under `--include-confidential`) yields plan sensitivity=confidential.
- [ ] 3.9 GREEN: implement `_FiledAnswerPlan` frozen dataclass (`link_dir, section, slug, title, description, path, content, sensitivity`) and `_stage_filed_answer(...)` in cli/main.py, mirroring `_stage_derived_objects`; re-reads cited frontmatter, folds sensitivity, calls `build_concept` with `related_note="concept cited to produce this answer"`.
- [ ] 3.10 GREEN: run Phase 3 unit tests to confirm all pass.

## Phase 4: Wire `--save` into `query` (cli/main.py:3915-4106)

- [ ] 4.1 RED: integration test — purity: `query` WITHOUT `--save` produces byte-identical stdout+stderr vs a captured baseline (matched-answer case).
- [ ] 4.2 RED: integration test — `--save` on a matched answer with citations writes `bundle/<link_dir>/<slug>.md` (body=answer, title=question, type=Concept, provenance=cited ids), adds the `index.md` bullet, and appends the "Filed answer" log line.
- [ ] 4.3 RED: integration test — `--title`/`--description`/`--type` overrides propagate to the written concept; invalid `--type` exits non-zero with no write.
- [ ] 4.4 RED: integration test — zero-citation matched answer + `--save` refuses, exits non-zero, no write (per corrected spec).
- [ ] 4.5 RED: integration test — preview is shown; TTY without `--auto` requires confirmation before write; `--auto` (or `cfg.review: false`) bypasses the prompt.
- [ ] 4.6 RED: integration test — non-TTY without `--auto` refuses to write, exits non-zero, bundle unchanged.
- [ ] 4.7 RED: integration test — slug collision (pre-existing file at target path) refuses, exits non-zero, no write.
- [ ] 4.8 RED: integration test — successful save prints a reindex hint (`openkos reindex`).
- [ ] 4.9 GREEN: add `--save` (default off), `--title`, `--type`, `--description` options to `query`; reuse the existing `--auto` option.
- [ ] 4.10 GREEN: place the entire save block AFTER the existing answer/citations print AND after the `no_match_cause` early return, gated by `if save:`; on `citations` empty raise/catch the `_stage_filed_answer` `ValueError` and exit 1 with the message.
- [ ] 4.11 GREEN: reuse ingest's confirm gate (mirror main.py:741-750: `--auto` → `cfg.review` false → TTY confirm → non-TTY refuse exit 1) and Phase-B write (`write_exclusive` for the concept file, then `insert_index_entry`, then `insert_log_entry` with the distinct "Filed answer" log line), catalog writes last.
- [ ] 4.12 GREEN: print the reindex hint on successful write.
- [ ] 4.13 GREEN: run all Phase 4 integration tests to confirm pass.

## Phase 5: Full-Suite Verification

- [ ] 5.1 Run `uv run pytest` (full suite) — confirm existing ingest and query tests remain green, no regressions from the `build_concept` signature change or the query save block.
- [ ] 5.2 Manual smoke: `openkos query "<question>" --save --auto` in a scratch workspace bundle, confirm file/index/log written and reindex hint printed.
