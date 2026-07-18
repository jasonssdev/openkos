# Tasks: `openkos ingest <path>` — null compiler

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~650-800 (6 source modules + manifest + 6 test files + docs) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Pure primitives + config reader + manifest: `config.read_config`/`Config`, `fsio.write_atomic`/`copy_exclusive`, `bundle/index.py::insert_source_entry`, `bundle/log.py::insert_log_entry`, `model/okf.py::build_source_concept`, `pyproject.toml` pyyaml dep | PR 1 | `uv run pytest tests/unit/test_config.py tests/unit/test_fsio.py tests/unit/bundle/test_index.py tests/unit/bundle/test_log.py tests/unit/model/test_okf.py` | N/A — pure/fs primitives only, exercised via `tmp_path`; no CLI entrypoint wired yet | `git revert` PR1 range; purely additive functions, nothing imports them yet |
| 2 | `ingest` CLI wiring (Phase A/B, confirm gate, `--auto`) + `docs/cli.md` | PR 2 | `uv run pytest tests/unit/cli/test_ingest.py` | `uv run openkos ingest <path>` (TTY confirm) and `uv run openkos ingest <path> --auto` in a scratch workspace | `git revert` PR2 range; adds a new command only, `init` untouched |

## Phase 1: Manifest (PR 1)

- [x] 1.1 `pyproject.toml` — declare `pyyaml` as an explicit direct runtime dependency (no new install; already transitive via `python-frontmatter`).

## Phase 2: Config reader (PR 1)

- [x] 2.1 RED — `tests/unit/test_config.py`: `read_config` returns `model`/`review`/`default_sensitivity`; missing keys fall back to packaged defaults; `yaml.YAMLError` → `ValueError`; non-mapping root → `ValueError`.
- [x] 2.2 GREEN — `config.py`: frozen `Config` dataclass + `read_config(root) -> Config` (PyYAML `safe_load`, defaults, wrap `YAMLError`).

## Phase 3: fsio primitives (PR 1)

- [x] 3.1 RED — `tests/unit/test_fsio.py`: `write_atomic` overwrites an existing file; interrupted write (monkeypatch `os.replace` to raise) leaves the original file byte-identical; `copy_exclusive` copies binary content; `copy_exclusive` refuses an existing destination; regression — `write_exclusive` still refuses an existing file ("x" create-only, unchanged).
- [x] 3.2 GREEN — `fsio.py`: `write_atomic(path, content)` (temp file in `path.parent`, `flush`+`fsync`, `os.replace`, unlink temp on failure); `copy_exclusive(src, dst)` ("xb" open, binary copy).

## Phase 4: Bundle index append (PR 1)

- [x] 4.1 RED — `tests/unit/bundle/test_index.py`: `insert_source_entry` creates `# Sources` on a fresh empty-body index; canonical section order `[Concepts, Decisions, People, Sources]`; existing sections/entries round-trip byte-for-byte; frontmatter block preserved verbatim.
- [x] 4.2 GREEN — `bundle/index.py`: `insert_source_entry(index_text, *, title, slug, description) -> str` (split frontmatter verbatim, parse `# `-headed body sections, locate/create `# Sources`, append bullet, re-render).

## Phase 5: Bundle log append (PR 1)

- [x] 5.1 RED — `tests/unit/bundle/test_log.py`: `insert_log_entry` prepends to today's `## YYYY-MM-DD` section; creates today's section at the top when absent; prior dated entries unchanged.
- [x] 5.2 GREEN — `bundle/log.py`: `insert_log_entry(log_text, today, entry) -> str` (parse `## ` sections, prepend/create today's section).

## Phase 6: OKF Source concept builder (PR 1)

- [x] 6.1 RED — `tests/unit/model/test_okf.py`: `build_source_concept` emits frontmatter with `type`/`title`/`description`/`resource`/`tags`/`timestamp`/`status`/`version`/`freshness`/`sensitivity`/`provenance` plus a `# Citations` body; result passes `check_conformance`; `description` states the source was imported and not yet compiled/extracted (no extraction claim); `sensitivity` equals the passed value.
- [x] 6.2 GREEN — `model/okf.py`: `build_source_concept(...) -> str` (plain dict → `dump_frontmatter`, static `# Citations` body).

## Phase 7: CLI `ingest` command (PR 2)

- [ ] 7.1 RED — `tests/unit/cli/test_ingest.py`: Phase A preview shown, nothing written, when TTY and no `--auto`; confirm → Phase B all-or-nothing (raw copy + concept + `index.md` + `log.md` all present, catalog written last); `--auto` skips the prompt; config `review: false` skips the prompt like `--auto`; non-TTY + `review: true` + no `--auto` refuses (exit 1, "re-run with `--auto`", nothing written); missing/unreadable `<path>` refuses, nothing written; collision (`raw/<name>` or `bundle/sources/<slug>.md` exists) refuses in Phase A, nothing overwritten; traversal path `../../evil.txt` lands as `raw/evil.txt` only, nothing written outside `raw/`/`bundle/sources/`; missing workspace (`bundle/index.md`/`log.md` absent) refuses; generated concept `sensitivity` equals config `default_sensitivity`.
- [ ] 7.2 GREEN — `cli/main.py`: add `ingest` command. Phase A: validate `<path>` is a readable file; workspace check; `read_config`; derive slug/dest from `Path(src).name` only (never raw path segments); collision refusal; `build_source_concept`; `insert_source_entry`/`insert_log_entry` against current on-disk bytes; preview. Confirm gate: `--auto` → no prompt; else `review=false` → no prompt; else TTY → `typer.confirm`; else refuse (exit 1). Phase B: `mkdir bundle/sources`; `copy_exclusive` raw; `write_exclusive` concept; `write_atomic` `index.md`; `write_atomic` `log.md` (catalog last). Reuse `init`'s `except (OSError, ValueError)` → `echo(err=True)` + `Exit(1)`, prefix `openkos ingest:`.
- [ ] 7.3 REFACTOR — `cli/main.py`: document `ingest`'s Phase A/B flow, confirm-gate resolution order, and refusal cases in its docstring.

## Phase 8: Documentation (PR 2)

- [ ] 8.1 `docs/cli.md` — record `ingest`'s null-compiler behavior (raw copy + one Source concept, no extraction); mark `--sensitivity`/`--batch` explicitly as "not in this slice".

## Phase 9: Verification Gate

- [ ] 9.1 `uv run pytest --cov` — full suite green, branch coverage ≥90%.
- [ ] 9.2 `uv run ruff check .` and `uv run ruff format --check .` — clean.
- [ ] 9.3 `uv run mypy .` — clean (strict mode).
- [ ] 9.4 `uv build` + wheel smoke test.
