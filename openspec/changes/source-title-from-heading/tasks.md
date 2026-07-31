# Tasks: Derive a Source's title from its content

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~500-620 (helper 120, unit tests 220-280, call site ~12, integration tests 150-200, fixture edits 0 planned / up to 60+ contingent) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (helper + unit tests, self-contained) → PR 2 (call-site wiring + integration tests + fixture-churn resolution + final gate) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending — orchestrator must ask user: stacked-to-main / feature-branch-chain / size-exception |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Pure `source_title.py` module + full branch-covering unit suite, no wiring into `ingest` yet | PR 1 | `uv run pytest tests/unit/test_source_title.py` | N/A — pure function, no CLI/filesystem harness needed | Delete `source_title.py` and `test_source_title.py`; zero blast radius, nothing imports it yet |
| 2 | Wire `derive_source_title` into `ingest`, integration tests, fixture-churn resolution, final gate | PR 2 | `uv run pytest tests/unit/cli/test_ingest.py` | `uv run openkos ingest examples/good-life-demo/raw/*.txt` (manual smoke, real title observed in `index.md`/`log.md`) | Revert `main.py` diff at `:1684-1695`; no backfill exists so no other file changes |

## Phase 1: Pure Helper — RED/GREEN per predicate branch (`tests/unit/test_source_title.py`, `src/openkos/source_title.py`)

- [x] 1.1 RED: write parametrized failing tests for `_frontmatter_end` — no leading `---`; leading `---` with later closing `---` (skip); leading `---` with no closing `---` anywhere (treated as content).
- [x] 1.2 GREEN: implement `_frontmatter_end(lines)` bounded prefix probe in `src/openkos/source_title.py`.
- [x] 1.3 RED: tests for fence tracking — H1 outside any fence (accepted); H1 inside a fence (ignored, real H1 after it wins); `~~~` fence; unclosed fence swallows rest of document; fence closed only by its own 3-char marker (mismatched marker does not close it).
- [x] 1.4 GREEN: implement the single walk's fence state (local `_FENCE_MARKERS: Final = ("\`\`\`", "~~~")`) inside `derive_source_title`, per design's "copy, do not import" decision citing `bundle/links.py:50-74` and `graph/sqlite_graph.py` in the module docstring.
- [x] 1.5 RED: tests for ATX H1 detection and normalization — plain H1; H1 with trailing `#` sequence stripped (`_ATX_CLOSING_RE = re.compile(r" #+$")`); `Grade A#` and `C# vs F#` preserved untouched; whitespace-only heading rejected as empty after collapse.
- [x] 1.6 GREEN: implement `_ATX_H1_RE`, `_ATX_CLOSING_RE`, and the heading-only normalization branch.
- [x] 1.7 RED: tests for rule (b) title-plausible predicate, one test per clause: next line blank; next line EOF; text ending in `.`/`,`/`;`/`:` rejected; text starting with `-`/`*`/`>`/`#`/`|`/`` ``` ``/`~~~` rejected; wrapped-prose first line (no trailing blank) rejected.
- [x] 1.8 GREEN: implement rule (b) evaluation using `first_body_index` and `lines[i+1]` lookahead-by-index (no second pass).
- [x] 1.9 RED: tests for `_FORBIDDEN_IN_TITLE`, one case per forbidden member (`\x00-\x1f`, `\x7f`, `[`, `]`, `(`, `)`, backtick, `*`, `_`, `<`, `>`, `|`) plus explicit non-rejection cases for `#`, `&`, `"`, `'`, `:` mid-string, `-`, and non-ASCII (em dash).
- [x] 1.10 GREEN: implement `_FORBIDDEN_IN_TITLE = re.compile(...)` following `lint.py:603` `_UNSPELLABLE_IN_SPAN` shape — one compiled class, docstring justifying every member individually and naming what is deliberately excluded.
- [x] 1.11 RED: tests for length — exactly 120 chars post-normalization accepted; 121 rejected; length measured after normalization (a heading padded with spaces is not rejected for pre-collapse length).
- [x] 1.12 GREEN: implement `_TITLE_MAX_CHARS = 120` check in `_normalize_and_validate`, applied in the fixed order: collapse/strip → heading-only closer strip → empty check → length check → forbidden-char check.
- [x] 1.13 RED: test that a CRLF source (`\r\n` line endings) is accepted — whitespace collapse must destroy the `\r` before the control-character class runs.
- [x] 1.14 RED: test that a rejected H1 returns `None` directly and does NOT cascade into rule (b) evaluation (use an H1 that fails validation, followed by a title-plausible plain line — assert `None`, not the plain line).
- [x] 1.15 RED: edge cases — no lines at all (empty string); whitespace-only document; no H1 and no plausible line anywhere (`None`).
- [x] 1.16 GREEN: finish `derive_source_title(raw_content: str) -> str | None` wiring all of the above into the single linear pass; add module docstring covering purity/idempotence, `None`-is-not-an-error, no-cascade, fence duplication citation, "not a general sanitiser", frontmatter fence-blindness, and issue #248 naming caveat.
- [x] 1.17 Run `uv run pytest tests/unit/test_source_title.py --cov=src/openkos/source_title --cov-branch` and confirm every branch is covered (no CLI/`tmp_path`/`runner` fixtures needed, per design).

## Phase 2: Call-Site Wiring (`src/openkos/cli/main.py`)

- [x] 2.1 Delete `title = _titleize(src.stem)` at `main.py:1684`.
- [x] 2.2 Insert derivation immediately after the UTF-8 decode block ends at `main.py:1689` (`raw_content: str | None = src.read_text(encoding="utf-8")` / `except UnicodeDecodeError: raw_content = None`) and strictly before `_build_source_document`'s first call at `main.py:1753`: `derived = None if raw_content is None else source_title.derive_source_title(raw_content); title = derived if derived is not None else _titleize(src.stem)`. **Correction to design.md**: the decode is at `:1689`, not `:1695` — insert after `:1689`, not `:1695`.
- [x] 2.3 Import `source_title` module at the top of `main.py`; confirm `slug` (computed at `:1646`, consumed by `:1651` and `:1654-1674`) is untouched and still runs before the decode.

## Phase 3: LLM Prompt Consumer Verification

- [x] 3.1 RED/verify: write or extend an integration test in `tests/unit/cli/test_ingest.py` asserting `_stage_derived_objects(source_title=title)` at `main.py:1764` — and therefore `extraction/concept.py:189`'s `SOURCE TITLE:` prompt line — receives the FINAL derived title (H1/plausible-line value), not the slug, when a candidate is accepted. Use the fake LLM backend already in the test harness and assert on the captured prompt argument, not just the frontmatter.

## Phase 4: Integration Tests — End-to-End Scenarios (`tests/unit/cli/test_ingest.py`)

- [x] 4.1 RED: `# Introduction to Stoicism` as first non-fenced line -> frontmatter `title`, body `# {title}`, `index.md` bullet, and `log.md` link label all read `Introduction to Stoicism`.
- [x] 4.2 RED: an H1 inside a fenced code block is ignored; a later real `# Chapter One` outside any fence wins.
- [x] 4.3 RED: no H1, a title-plausible first line (`Call with Maria Salazar — 2026-07-14` followed by a blank line) becomes the title.
- [x] 4.4 RED: wrapped prose first line (no blank line after it) is NOT title-plausible; title falls back to `_titleize(src.stem)`.
- [x] 4.5 RED: a candidate carrying a forbidden character (`[`, `]`, backtick) after normalization falls back to the slug.
- [x] 4.6 RED: a candidate over 120 chars post-normalization falls back to the slug, with no truncation.
- [x] 4.7 RED: a well-formed leading `---`...`---` frontmatter block is skipped; its own `title:` key (if present) is not read; a later `# Chapter One` becomes the title.
- [x] 4.8 RED: an unclosed leading `---` (no later closing `---`) is treated as ordinary content, fails the title-plausible predicate (starts with block syntax), falls back to the slug.
- [x] 4.9 RED: a binary (`UnicodeDecodeError`) source never calls `derive_source_title` and keeps the slug title.
- [x] 4.10 RED: an empty or whitespace-only source never calls `derive_source_title` and keeps the slug title.
- [x] 4.11 RED: `test_reingest_of_identical_bytes_writes_a_byte_identical_source_document` — reuse the `_FixedClock` monkeypatch pattern at `test_ingest.py:2385-2390` (freeze `timestamp`); assert byte-identical Source document across two ingests of the same raw bytes, following the precedent shape of `test_reingest_with_equal_values_writes_byte_identical_output` (`:2401`).
- [x] 4.12 GREEN: run 4.1-4.11 against the Phase 2 wiring; fix any integration-level defect (the unit suite in Phase 1 should already cover the underlying branch logic).

## Phase 5: Fixture-Churn Verification (own commit, own line-count entry)

- [x] 5.1 Run the full `tests/unit/cli/test_ingest.py` suite (`uv run pytest tests/unit/cli/test_ingest.py -v`) BEFORE assuming any fixture is safe. Do not rely on the grep finding "no assertions match `Imported [` / `](/sources/`" as a substitute for running the suite — that grep is not proof.
- [x] 5.2 Identify precisely which of the ~42 content-titled fixtures (34 writing `"content"`, 3 writing `"body"`, plus `"original"`, `"original concept"`, `"malicious"`, 2 `"new content"`) and which of the 46 slug-titled fixtures (`"Some raw notes about self-control."`, `.`-terminated) actually change observed test outcomes, versus which pass unchanged because no assertion reads the derived label.
- [x] 5.3 Check `tests/unit/vcs/conftest.py:104` (bare-string fixture) against the same predicate; confirm whether it is title-plausible and whether any VCS test asserts on the title it produces.
- [x] 5.4 IF fixtures require edits to keep assertions correct: make those edits in a SEPARATE commit from the Phase 1/2 implementation commits, and report their line count as its own line in the review forecast — never fold them silently into the implementation diff.
- [x] 5.5 Add a short code comment at the fixture-churn boundary (e.g. near the first `.`-terminated fixture adjacent to unterminated ones in `test_ingest.py`) explaining why one fixture keeps its slug title and its neighbor does not — the split is a direct consequence of the title-plausible predicate's terminal-punctuation clause, not an inconsistency.

## Phase 6: Final Gate

- [x] 6.1 `uv run pytest` — full suite green, including `--cov` branch coverage ≥ 90 for `src/openkos/source_title.py`.
- [x] 6.2 `uv run ruff check .` clean.
- [x] 6.3 `uv run ruff format --check .` clean.
- [x] 6.4 `uv run mypy .` clean (strict mode; `derive_source_title` fully typed, no `Any`).
- [x] 6.5 Confirm no ADR was created (design's ADR gate evaluated both conditions; condition 2 — hard-to-reverse — is not met per the proposal's single-revert rollback plan).

## Phase 7: Review Findings (lineage `review-7ba24d13d2b911db`, addressed in PR 2)

- [x] 7.1 Add public-API tests (via `derive_source_title`, not the private `_frontmatter_end` helper alone) for the well-formed frontmatter block (skipped) and the unclosed `---` (treated as content) scenarios.
- [x] 7.2 Pin the frontmatter probe's fence-blindness with a test exercising a `---` inside an early fenced block that closes the frontmatter scan early.
- [x] 7.3 Add coverage for the `#` member of `_BLOCK_SYNTAX_PREFIXES`: a line starting with `#` that is NOT a matched ATX H1 (`##Subheading`, no space) is rejected by the plain-line rule.
- [x] 7.4 Remove the dead `_FENCE_MARKERS` splat from `_BLOCK_SYNTAX_PREFIXES` (unreachable: the walk `continue`s on any fence-marker line before `first_body_index` can be assigned); update the docstring to state the unreachability instead of implying a live rejection.
- [x] 7.5 Tighten the delta spec's rule (2) wording from "the first title-plausible line" to state plainly that only the first non-blank line is considered (no scanning ahead); add a test pinning that a later-qualifying line is never used when the first line fails.
