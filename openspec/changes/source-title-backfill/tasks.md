# Tasks: Backfill content-derived Source titles

**Change**: `source-title-backfill` · **Issue**: [#298](https://github.com/jasonssdev/openkos/issues/298) · **Baseline**: `main` @ `61b50ce`

## Review Workload Forecast
Estimated changed lines: 915-1200 total (Slice 1: `bundle/source_titles.py` + tests ~380-490; Slice 2: `relabel_index_entry` + tests ~155-220; Slice 3: CLI verb + tests ~380-490, depends on 1 and 2)
Decision needed before apply: Yes
Chained PRs recommended: Yes
400-line budget risk: High
800-line budget risk: Medium

---

## Slicing rationale

The design's three-slice split partitions cleanly against the spec:

- **Slice 1** owns the pure classification/derivation core (`bundle/source_titles.py`): the bundle-wide sweep argument shape, the three-bucket classification order, the mechanical-vs-curated test (including the `01-Introduction.md` counterexample), the `None`/identical-derivation no-op rules, and `retitle_document`'s body-first-line safety property and two-edit invariant. These map to spec Requirements "Bundle-Wide Sweep With No Concept Argument", "Three-Bucket Classification In Fixed Evaluation Order", "Body First-Line Safety Property", and "Exactly Two Byte-Level Edits Per Staged Source" — none of these requires `index.md` or the CLI shell, so the slice is independently testable over in-memory snapshots only.
- **Slice 2** owns `relabel_index_entry` in isolation (spec Requirement "`index.md` Bullet Label Update") — independent of Slice 1 because it operates on `index.md` text and a `(concept_id, new_title)` pair, not on the source-titles core.
- **Slice 3** owns the CLI verb, which is the only place the remaining requirements ("Invariants Preserved Across Every Run", "Empty-Result Short Circuit", "Three-Bucket Preview Then Confirm Gate", "Idempotence", "Atomicity And Partial-Failure Reporting", "One Log Entry And One Autocommit For The Whole Sweep") can be exercised end-to-end, because they depend on Phase A/B orchestration, `write_atomic`, `_autocommit`, and `insert_log_entry` wiring together both prior slices.

This confirms the design's decomposition; no adjustment needed.

---

## Slice 1 — `bundle/source_titles.py` (pure core) + promoted `titleize`

### Phase 1.0 — Preparatory correction (do this first, costs nothing against budget)

- [ ] 1.0 Fix the wrong docstring at `src/openkos/source_title.py:162` (spec: enables correct future documentation of Requirement "Three-Bucket Classification In Fixed Evaluation Order," scenario "The `01-Introduction.md` counterexample classifies as mechanical, not curated"). It currently reads "`None` here means the caller falls back to `_titleize(slug)`, the pre-#248 behavior" — factually wrong; the real fallback is `_titleize(src.stem)` (`main.py:1743`, pre-promotion) / `titleize(stem)` (post-promotion, once task 1.4 lands). Update the docstring to name `titleize(stem)` and cross-reference `bundle/source_titles.py` as the identifier's new home, since this correction lands in the same slice that performs the promotion.

### Phase 1.1 — `titleize` promotion (single call-site refactor)

- [ ] 1.1 Write the failing test for `titleize` in the new `tests/unit/bundle/test_source_titles.py`: import `openkos.bundle.source_titles.titleize`, assert it is byte-for-byte equivalent to the current `_titleize` behavior at `src/openkos/cli/main.py:1083` (same regex-driven word-splitting/capitalization rules, including the `01-Introduction` -> `01 Introduction` case). File: `tests/unit/bundle/test_source_titles.py`.
- [ ] 1.2 Create `src/openkos/bundle/source_titles.py` with the public `titleize(stem: str) -> str` function, moved verbatim from `cli/main.py:1083`'s `_titleize` body. No `openkos` imports beyond `openkos.model.okf` and `openkos.source_title`; no `pathlib.Path`; no I/O (design D1). File: `src/openkos/bundle/source_titles.py`.
- [ ] 1.3 Write the failing regression test proving the CLI call site still works after delegation: extend `tests/unit/cli/test_ingest.py` (or the closest existing ingest title-fallback test) to assert `openkos ingest` on a file whose derived title is `None` still falls back to the same title it produced before this change. File: `tests/unit/cli/test_ingest.py`.
- [ ] 1.4 Update `src/openkos/cli/main.py:1083` so `_titleize` becomes a one-line delegation to `source_titles.titleize`, and update the one production call site at `main.py:1743` to use the delegation (no behavior change). File: `src/openkos/cli/main.py`.

### Phase 1.2 — Three-bucket classification: `scan_source_titles`

- [ ] 1.5 Write failing parametrized tests for `scan_source_titles(files)` in `tests/unit/bundle/test_source_titles.py` covering: only `type: source` concepts are considered and returned as candidates (spec: "Only type-source concepts are considered"); malformed `resource` shapes land in `warned` with reason `resource-missing` / `resource-malformed` — absent, non-`str`, missing `raw/` prefix, `..` segment, nested path, leading `/`, backslash (spec: "Malformed resource is warned and never staged"); a curated title (`title != titleize(Path(resource).stem)`) lands in `skipped` with reason `curated` and is never staged regardless of raw content (spec: "A curated title is skipped, not staged"); the `01-Introduction.md` counterexample classifies as a candidate, not curated, because the test compares against `titleize(Path(resource).stem)` and not `titleize(slug)` (spec: "The `01-Introduction.md` counterexample classifies as mechanical, not curated"); deterministic `concept_id`-sorted output ordering across all three buckets. File: `tests/unit/bundle/test_source_titles.py`.
- [ ] 1.6 Implement `scan_source_titles(files: Mapping[str, str]) -> ScanResult` (or equivalent named tuple/dataclass carrying `warned`, `skipped`, `candidates`) in `src/openkos/bundle/source_titles.py`, per design D2/D3: pure function over an in-memory snapshot, no filesystem, resource-containment check before any path is built, closed reason vocabulary (`resource-missing`, `resource-malformed`, `curated`). File: `src/openkos/bundle/source_titles.py`.

### Phase 1.3 — Re-derivation: `resolve_source_title_backfill`

- [ ] 1.7 Write failing parametrized tests for `resolve_source_title_backfill(scan, raw_texts)` in `tests/unit/bundle/test_source_titles.py` covering: a candidate whose `raw/<name>` bytes yield a non-`None` title different from current is staged with the newly derived title (spec: "A mechanical title with a differing derivation is staged"); a `None` re-derivation stages nothing, reason `no-derivable-title` (spec: "A `None` re-derivation stages nothing"); an identical re-derivation stages nothing, reason `already-current` (spec: "An identical re-derivation stages nothing"); `derive_source_title` is NOT called when raw text is `None` or blank/whitespace-only, reason `empty-raw-source` (design D2); a missing key in `raw_texts` (file absent/unreadable) lands in `warned` with reason `raw-unreadable`, distinct from an explicit `None` value (undecodable) which lands in `warned` with reason `raw-undecodable` (design D2); deterministic `concept_id`-sorted output across `staged`/`skipped`/`warned`. File: `tests/unit/bundle/test_source_titles.py`.
- [ ] 1.8 Implement `resolve_source_title_backfill(scan: ScanResult, raw_texts: Mapping[str, str | None]) -> SourceTitleBackfill` in `src/openkos/bundle/source_titles.py`: the four frozen dataclasses (`SourceRetitle`, `SkippedSource`, `WarnedSource`, `SourceTitleBackfill`), calling `derive_source_title` and `retitle_document` (task 1.10) per candidate, per design D3. File: `src/openkos/bundle/source_titles.py`.

### Phase 1.4 — `retitle_document` and the two-edit / safety-property core

- [ ] 1.9 Write failing golden-string tests for `retitle_document(text, *, current_title, new_title)` in `tests/unit/bundle/test_source_titles.py` covering: byte-identical output apart from the frontmatter `title:` value and the first body line (spec: "Only title and first line change" — assert `description`, `## Source content`, `# Citations`, and every other frontmatter key are unchanged, using the verified byte-identical `load_frontmatter`/`dump_frontmatter` round trip as the golden baseline); a first body line that does NOT read exactly `# {current_title}` raises `ValueError` naming the concept and the line found, and is never written (spec: "A hand-edited first line is refused, not overwritten"); a first body line that matches exactly is rewritten to `# {new_title}` (spec: "A matching first line is overwritten normally"); a CRLF first line has its `\r` stripped for comparison and re-attached verbatim on write, with no other byte moved (design D4). File: `tests/unit/bundle/test_source_titles.py`.
- [ ] 1.10 Implement `retitle_document(text: str, *, current_title: str, new_title: str) -> str` in `src/openkos/bundle/source_titles.py`: `load_frontmatter` -> assert `metadata["title"] == current_title` -> `metadata["title"] = new_title` -> replace only `body.split("\n")[0]` (CRLF-aware) -> `dump_frontmatter`; raise `ValueError` on any first-line mismatch, caught by `resolve_source_title_backfill` and filed under `warned` / `heading-mismatch`. File: `src/openkos/bundle/source_titles.py`.

### Phase 1.5 — Slice 1 quality gate

- [ ] 1.11 Run `uv run pytest --cov` and confirm branch coverage >= 90% for the new module; fix any gaps.
- [ ] 1.12 Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy .`; fix any findings.
- [ ] 1.13 Confirm `main` stays green and independently mergeable at this point: `bundle/source_titles.py` has no CLI wiring yet, so no user-facing command or behavior changes.

---

## Slice 2 — `relabel_index_entry` in `bundle/index.py`

Independent of Slice 1; may be developed and reviewed in parallel with it.

- [ ] 2.1 Write failing tests for `relabel_index_entry(index_text, concept_id, new_title)` in `tests/unit/bundle/test_index.py`, modeled on `remove_index_entry`'s existing test suite, covering: the bullet's label is rewritten to `new_title` while slug, link target, and `description` text remain unchanged (spec: "The index bullet label reflects the new title"); every other bullet in `index.md` is byte-identical (spec: "Unstaged Sources' index bullets are untouched"); identity is matched via `_link_identity(target) == concept_id` on the bullet's first markdown link, never by label text; zero matches returns `(index_text, 0)` unchanged, not an error; multiple matches relabels all of them and reports the total; indentation, bullet marker (`* `/`- `), the ` - ` separator, and line ending round-trip verbatim; the frontmatter block (split via `_split_frontmatter_verbatim`) is never re-dumped and stays byte-identical; `_reject_newline("title", new_title)` rejects a `new_title` containing a newline; malformed frontmatter raises `ValueError` (matching `remove_index_entry`'s existing contract). File: `tests/unit/bundle/test_index.py`.
- [ ] 2.2 Implement `relabel_index_entry(index_text: str, concept_id: str, new_title: str) -> tuple[str, int]` in `src/openkos/bundle/index.py`, plus the new module-level `_LABELLED_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")` used only by this function. Do NOT add a capture group to the existing `_LINK_RE` at `src/openkos/bundle/index.py:145` — `remove_index_entry` at `index.py:222` reads `group(1)` as the link target and depends on `_LINK_RE` staying exactly as it is; rewrite only the label span between `[` and `]` of the first link on a candidate bullet line. File: `src/openkos/bundle/index.py`.

### Slice 2 quality gate

- [ ] 2.3 Run `uv run pytest --cov` and confirm branch coverage >= 90% for the new/changed code in `index.py`; fix any gaps.
- [ ] 2.4 Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy .`; fix any findings.
- [ ] 2.5 Confirm `main` stays green and independently mergeable: `relabel_index_entry` is unused dead code from any CLI's perspective until Slice 3 wires it in, and every existing `index.py` caller (`insert_index_entry`, `remove_index_entry`) is untouched.

---

## Slice 3 — `backfill-source-titles` CLI verb

Depends on Slice 1 (`bundle/source_titles.py`) and Slice 2 (`relabel_index_entry`) both landing first.

### Phase 3.1 — Empty-result short circuit

- [ ] 3.1 Write the failing CLI test for the empty-result short circuit in `tests/unit/cli/test_backfill_source_titles.py` (new file, modeled on `tests/unit/cli/test_backfill_sensitivity.py`): a bundle where every Source is curated or warned reports nothing staged, writes no file, creates no commit, and exits 0 (spec: "A fully curated or warned bundle is a no-op"); a bundle with zero `type: source` concepts behaves identically (spec: "A bundle with no Sources is a no-op"). Use `snapshot_with_mtime` from `tests/unit/cli/conftest.py` to prove nothing was written at the filesystem level. File: `tests/unit/cli/test_backfill_source_titles.py`.
- [ ] 3.2 Implement the `backfill-source-titles` Typer command skeleton in `src/openkos/cli/main.py`: no positional concept-id argument (spec: "The command accepts no concept-id argument"), `require_workspace`/`read_config`, `rglob(bundle/*.md)` snapshot, call `scan_source_titles`, read `raw/<name>` for each candidate into `raw_texts` (catching `UnicodeDecodeError` before the outer `except (OSError, ValueError)`, per design D2's ordering note), call `resolve_source_title_backfill`, and short-circuit with exit 0 / "nothing was staged" when `staged` is empty. File: `src/openkos/cli/main.py`.

### Phase 3.2 — Three-bucket preview and confirm gate

- [ ] 3.3 Write the failing CLI tests for the preview and confirm-gate precedence in `tests/unit/cli/test_backfill_source_titles.py`: the preview lists staged, curated, and warned buckets before any confirm prompt (spec: "Preview shows all three buckets before any prompt"); `--auto` skips the prompt and writes (spec: "`--auto` skips the prompt only"); `review: false` in workspace config skips the prompt and writes (spec: "`review: false` skips the prompt like `--auto`"); a non-TTY session without `--auto` refuses with non-zero exit and no write (spec: "Non-TTY without `--auto` refuses to write"); declining an interactive TTY prompt performs no write to any concept file, `index.md`, or `log.md`, and creates no commit (spec: "Declining the prompt performs no write"). Use `_simulate_tty` and `snapshot_with_mtime`, mirroring `test_backfill_sensitivity.py`'s helpers. File: `tests/unit/cli/test_backfill_source_titles.py`.
- [ ] 3.4 Implement the three-bucket preview render and the confirm-gate precedence (`--auto` / `cfg.review == False` / TTY `typer.confirm` / non-TTY refuse) in `src/openkos/cli/main.py`, reusing `backfill-sensitivity`'s precedence verbatim. File: `src/openkos/cli/main.py`.

### Phase 3.3 — Write ordering, `relabel_index_entry` wiring, one log entry, one commit

- [ ] 3.5 Write the failing CLI tests in `tests/unit/cli/test_backfill_source_titles.py` for: the index bullet label update on confirm (spec: "The index bullet label reflects the new title", "Unstaged Sources' index bullets are untouched"); exactly one new `log.md` entry and exactly one commit covering every changed Source document, `index.md`, and the log entry, for a multi-Source run (spec: "A multi-Source run produces one log entry and one commit"); write order is `index.md` -> each staged Source (sorted) -> `log.md`, matching design D6 (assert via commit file list or intermediate-state inspection, not by re-deriving the ordering rationale). File: `tests/unit/cli/test_backfill_source_titles.py`.
- [ ] 3.6 Implement Phase B write orchestration in `src/openkos/cli/main.py`: compute the new `index.md` text (via `relabel_index_entry`, one call per staged Source) and the new `log.md` text in the pre-preview `try` block (so a malformed `index.md` refuses before any write, per design D6); on confirm, `write_atomic(index.md)` first, then each staged Source document (sorted by `concept_id`), then `log.md`, appending each path to a `landed` accumulator only after its `write_atomic` returns; finish with one `_autocommit(landed, "openkos: backfill-source-titles")`. File: `src/openkos/cli/main.py`.

### Phase 3.4 — Atomicity and partial-failure reporting

- [ ] 3.7 Write the failing CLI test for a mid-sweep write failure in `tests/unit/cli/test_backfill_source_titles.py`: a sweep staging changes across multiple Sources where the write fails after the first two Sources land but before the third exits non-zero, leaves the first two Sources updated on disk (no rollback), and names both of their paths explicitly in the failure message (spec: "A mid-sweep write failure names the paths that already landed"). File: `tests/unit/cli/test_backfill_source_titles.py`.
- [ ] 3.8 Implement the failure-reporting path in `src/openkos/cli/main.py`: on a write failure partway through Phase B, do not roll back any already-landed Source, and raise/report `"... failed while writing the backfill -- {exc}. Already written (left partially retitled, not rolled back): {paths}."` naming every path in the `landed` accumulator, matching the `set-sensitivity`/`relate`/`merge`/`backfill-sensitivity` precedent (design D6). File: `src/openkos/cli/main.py`.

### Phase 3.5 — Idempotence and cross-run invariants

- [ ] 3.9 Write the failing CLI test for idempotence in `tests/unit/cli/test_backfill_source_titles.py`: re-running `backfill-source-titles` immediately after a successful sweep stages nothing, writes nothing, creates no commit, and exits 0 (spec: "Immediate re-run after a successful sweep is a no-op"). File: `tests/unit/cli/test_backfill_source_titles.py`.
- [ ] 3.10 Write the failing CLI tests for the cross-run invariants in `tests/unit/cli/test_backfill_source_titles.py`, using `snapshot_bytes`/`snapshot_with_mtime` diffs restricted to expected paths: `raw/` bytes are untouched across a confirmed run (spec: "`raw/` bytes are untouched"); a pre-existing `log.md` entry keeps the old title unchanged, with only a new entry appended (spec: "Historical `log.md` entries keep the old title"); filename, slug, and Concept ID are identical before/after (spec: "Slug, filename, and Concept ID never change"); `.openkos/{fts,vectors,graph}.db` are byte-identical before/after when present (spec: "The derived-index databases are untouched"). File: `tests/unit/cli/test_backfill_source_titles.py`.
- [ ] 3.11 Implement/adjust `src/openkos/cli/main.py` as needed so all invariant tests from 3.10 pass without touching any of `raw/`, historical `log.md` entries, slugs, filenames, Concept IDs, or `.openkos/*.db` (expected to require no additional production code beyond 3.2/3.6, since the pure core and write path already exclude these paths by construction — this task exists to close any gap the tests in 3.9/3.10 surface). File: `src/openkos/cli/main.py`.

### Phase 3.6 — Malformed-resource warned path (CLI-level integration)

- [ ] 3.12 Write the failing CLI test in `tests/unit/cli/test_backfill_source_titles.py`: a Source with a malformed/absent `resource` field appears in the warned bucket, is never staged, and its frontmatter and body are byte-identical after the run (spec: "Malformed resource is warned and never staged"). File: `tests/unit/cli/test_backfill_source_titles.py`.
- [ ] 3.13 Confirm no additional production code is needed beyond 3.2 (the pure core from Slice 1 already classifies this); if the test in 3.12 reveals a gap in how the CLI surfaces the warned bucket, fix it in `src/openkos/cli/main.py`. File: `src/openkos/cli/main.py`.

### Phase 3.7 — Hand-edited first-line refusal (CLI-level integration)

- [ ] 3.14 Write the failing CLI test in `tests/unit/cli/test_backfill_source_titles.py`: a Source staged for a title change whose document's first body line was hand-edited away from `# {current_title}` is refused, reported as refused in the run's output, and its frontmatter and body remain byte-identical after the run — even though it passed mechanical classification (spec: "A hand-edited first line is refused, not overwritten"). File: `tests/unit/cli/test_backfill_source_titles.py`.
- [ ] 3.15 Confirm no additional production code is needed beyond 1.10 (`retitle_document`'s `ValueError`) and 3.6 (the CLI already catches and files it under `warned`/`heading-mismatch` via `resolve_source_title_backfill`); if the test in 3.14 reveals a gap in how the refusal reaches the CLI's reported output, fix it in `src/openkos/cli/main.py`. File: `src/openkos/cli/main.py`.

### Phase 3.8 — Slice 3 quality gate

- [ ] 3.16 Run `uv run pytest --cov` and confirm branch coverage >= 90% for `cli/main.py`'s new command and the whole change set; fix any gaps.
- [ ] 3.17 Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy .`; fix any findings.
- [ ] 3.18 Full end-to-end confirmation: `uv run pytest` (whole suite, not just the new tests) is green, proving Slice 3's wiring did not regress `backfill-sensitivity`, `ingest`, or any other command that shares `_autocommit`, `write_atomic`, or `insert_log_entry`.

---

## Traceability: spec requirement -> tasks

| Spec Requirement | Tasks |
|---|---|
| Bundle-Wide Sweep With No Concept Argument | 1.5, 1.6, 3.2 |
| Three-Bucket Classification In Fixed Evaluation Order | 1.0, 1.5, 1.6, 1.7, 1.8 |
| Body First-Line Safety Property | 1.9, 1.10, 3.14, 3.15 |
| Exactly Two Byte-Level Edits Per Staged Source | 1.9, 1.10 |
| `index.md` Bullet Label Update | 2.1, 2.2, 3.5, 3.6 |
| Invariants Preserved Across Every Run | 3.10, 3.11 |
| Empty-Result Short Circuit | 3.1, 3.2 |
| Three-Bucket Preview Then Confirm Gate | 3.3, 3.4, 3.12, 3.13 |
| Idempotence | 3.9 |
| Atomicity And Partial-Failure Reporting | 3.7, 3.8 |
| One Log Entry And One Autocommit For The Whole Sweep | 3.5, 3.6 |

## Notes

- `chain_strategy` (stacked PRs vs. feature-branch chain) is not yet collected; this document describes slice boundaries and dependency order only, not branch names or PR topology.
- Design write-ordering decision (`index.md` written FIRST, design D6) is not reopened here; task 3.6 implements it as specified.
- No task in this document touches `src/` or `tests/` directly — this is the checklist only; `sdd-apply` executes it.
