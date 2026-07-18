# Tasks: `openkos forget <concept-id>` — mirror-image delete of `ingest`

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~550-800 (3 source modules + 3 test files + docs; this codebase's docstring-per-function convention inflates line count even for a conceptually small slice — verified against `ingest`'s comparable actual 650-800) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 |
| Delivery strategy | ask-on-risk (resolved: single-pr with size:exception ACCEPTED by maintainer) |
| Chain strategy | N/A — single PR |

Decision needed before apply: Yes — RESOLVED: maintainer accepted `size:exception`, single PR, consistent with ingest/status/lint precedent.
Chained PRs recommended: Yes (not applied — single PR chosen instead)
Chain strategy: N/A (single PR)
400-line budget risk: High (actual diff ~779 changed lines, within forecast, exception accepted)

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Pure primitives: `fsio.remove_file`, `bundle/index.py::remove_index_entry`/`_link_identity` | PR 1 | `uv run pytest tests/unit/test_fsio.py tests/unit/bundle/test_index.py` | N/A — pure/fs primitives exercised via `tmp_path`; no CLI entrypoint wired yet | `git revert` PR1 range; purely additive functions, nothing imports them yet |
| 2 | `forget` CLI wiring (Phase A/B, confirm gate, `--auto`, `_resolve_concept_path`) + `docs/cli.md` | PR 2 | `uv run pytest tests/unit/cli/test_forget.py` | `uv run openkos forget <concept-id>` (TTY confirm) and `uv run openkos forget <concept-id> --auto` in a scratch workspace | `git revert` PR2 range; adds a new command only, `ingest`/`status`/`lint` untouched |

Delivered as ONE PR (size:exception) instead of the suggested split, per maintainer decision.

## Phase 1: `fsio.remove_file` (PR 1)

- [x] 1.1 RED — `tests/unit/test_fsio.py`: `remove_file` deletes an existing file; `remove_file` on a missing path raises `FileNotFoundError` (default `missing_ok=False`, no silent no-op).
- [x] 1.2 GREEN — `fsio.py`: `remove_file(path: Path) -> None` (`path.unlink()`); docstring documents symmetry with `copy_exclusive`/`write_exclusive`/`write_atomic`.

## Phase 2: Bundle index removal primitive (PR 1)

- [x] 2.1 RED — `tests/unit/bundle/test_index.py`: `remove_index_entry` drops the matching bullet from each of the four sections (Sources, Concepts, People, Decisions); link-form variants match — leading-slash (`/sources/x.md`), no leading slash (`sources/x.md`), no extension (`sources/x`), trailing `#fragment`/quoted title stripped; 0-match returns `(index_text, 0)` unchanged (no unrelated bullet dropped); 1-match drops exactly that line; >1-match drops ALL matches and reports the total count; frontmatter preserved byte-for-byte; malformed frontmatter raises `ValueError` (reuses `_split_frontmatter_verbatim`).
- [x] 2.2 GREEN — `bundle/index.py`: add `remove_index_entry(index_text, concept_id) -> tuple[str, int]` and private `_link_identity(target) -> str | None` + bullet/link regexes; split frontmatter verbatim, walk body lines only, drop each bullet whose first markdown link's normalized identity equals `concept_id`, rejoin preserving every other byte verbatim. NO import from `lint`.

## Phase 3: CLI `forget` command (PR 2)

- [x] 3.1 RED — `tests/unit/cli/test_forget.py` (mirror `test_ingest.py`'s `_snapshot` pattern): traversal concept-id `../../evil` refuses (exit 1, nothing written/deleted); absolute concept-id `/etc/passwd` refuses (exit 1, nothing written/deleted); reserved basename `index` refuses (exit 1, nothing written/deleted); nonexistent concept-id refuses with clear error (exit 1, nothing written); missing workspace refuses (exit 1, no traceback); successful forget of a Sources-section entry removes the index bullet + appends a `**Forget**` `log.md` line (no tombstone marker) + deletes the file; successful forget of a hand-authored People/Concepts bullet across link forms (relative, leading-slash, with/without `.md`); `--auto` skips the prompt and Phase B proceeds; non-TTY without `--auto` refuses to write (exit 1, nothing deleted/modified); config `review: false` skips the prompt like `--auto`; Phase B ordering — `index.md`/`log.md` updated BEFORE the file is deleted (monkeypatch `fsio.remove_file` to raise; assert catalog already updated, concept file still present); malformed `index.md` refuses (exit 1, nothing written).
- [x] 3.2 GREEN — `cli/main.py`: add `_resolve_concept_path(bundle_dir, concept_id) -> Path` (reject absolute, any `..` segment, reserved basename via `okf.RESERVED_FILENAMES`, raise `ValueError`; then refuse exit 1 if the resolved file is missing) and `forget` command: Phase A validates + builds `remove_index_entry`/`insert_log_entry` in memory, prints preview; confirm gate identical precedence to `ingest` (`--auto` > `cfg.review=false` > TTY `typer.confirm` > non-TTY refuse); Phase B writes `index.md` then `log.md` via `write_atomic`, then `fsio.remove_file(concept_path)` LAST; reuse `except (OSError, ValueError)` convention, prefix `openkos forget:`. DEVIATION: log line uses `concept_id` as the link title/text (`**Forget**: Removed [<concept_id>](/<concept_id>.md).`) instead of a human "Title" — no reliable human title source exists generically across all 4 sections without an extra frontmatter read, so the concept_id itself names the removed concept unambiguously.
- [x] 3.3 REFACTOR — `cli/main.py`: document `forget`'s Phase A/B flow, confirm-gate precedence, and refusal cases in its docstring (mirror `ingest`'s docstring style).

## Phase 4: Documentation (PR 2)

- [x] 4.1 `docs/cli.md` — document `forget`: generic index removal across all sections, catalog-before-file deletion ordering, and the known limitation that dangling INBOUND links from other concepts are not rewritten (deferred to MVP-2). (Did NOT touch the pre-existing "operational state"/SQLite phrasing — that inaccuracy fix is an explicit separate follow-up per proposal non-goals.)

## Phase 5: Verification Gate

- [x] 5.1 `uv run pytest --cov` — full suite green, branch coverage ≥90%. (Plain `uv run pytest` run: 316 passed, 0 failed. `--cov` run during sdd-verify: 316 tests pass, branch coverage verified. See verify-report for details.)
- [x] 5.2 `uv run ruff check .` and `uv run ruff format --check .` — clean. (Formatted via `ruff format .` on 2 test files after bounded review fixes; ruff check and format both pass in final state.)
- [x] 5.3 `uv run mypy .` — clean (strict mode). (Completed and verified in verify-report.)
- [x] 5.4 `uv build` + wheel smoke test. (Completed and verified in verify-report.)
