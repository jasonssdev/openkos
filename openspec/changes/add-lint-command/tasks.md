# Tasks: `openkos lint` — freshness + orphan health check (read-only)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~485 (~170 src: `lint.py` ~120, `cli/main.py` ~45, `config.py` ~6; ~300 test; ~15 docs) |
| 400-line budget risk | High |
| Chained PRs recommended | No |
| Suggested split | Single PR (`size:exception`) — orchestrator-resolved; natural future seam if reopened: (1) config+parser+stale-scan+CLI skeleton, (2) `normalize_link`+`check_orphans`+orphan rendering |
| Delivery strategy | exception-ok |
| Chain strategy | size-exception |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `freshness_window` config field + `lint.py` (docs, parser, stale-scan, orphan-scan) + `lint` CLI command | PR 1 (`size:exception`) | `uv run pytest tests/unit/test_config.py tests/unit/test_lint.py tests/unit/cli/test_lint.py` | `uv run openkos lint` in a scratch workspace across empty / stale-stamp / orphan / log.md-only-linked / pure-ingest / bad-window / missing-workspace scenarios | `git revert` the PR; `lint.py`, the `lint` command, and `freshness_window` are additive-only; `read_config` collapses to its prior three fields |

## Phase 1: Config foundation

- [x] 1.1 RED — `tests/unit/test_config.py`: `freshness_window` present/absent/explicit-null in `openkos.yaml` → present value passthrough, absent/null → `"7d"` fallback via `is not None` check.
- [x] 1.2 GREEN — `config.py`: add `DEFAULT_FRESHNESS_WINDOW = "7d"`, `Config.freshness_window: str`, `read_config` fallback.

## Phase 2: `lint.py` vocabulary + doc collection

- [x] 2.1 RED — `tests/unit/test_lint.py`: `collect_docs` — `identity`/`rel_dir`/`body` computed per doc; reserved filenames and errored files (wrapping `okf._iter_docs`) skipped.
- [x] 2.2 GREEN — `lint.py`: frozen `LintDoc`/`LintFinding`/`LintReport` + `collect_docs(bundle_dir) -> list[LintDoc]`.

## Phase 3: Duration parser

- [x] 3.1 RED — `test_lint.py`: `parse_window` — `"7d"`→7 days, `"2w"`→14 days, surrounding whitespace tolerated; zero/negative/garbage → `ValueError`.
- [x] 3.2 GREEN — `lint.py`: `parse_window(raw) -> timedelta`.
- [x] 3.3 RED — `test_lint.py`: `resolve_window` — valid raw → `(window, None)`; unparseable/zero/negative → `(timedelta(7), notice)`.
- [x] 3.4 GREEN — `lint.py`: `resolve_window(raw)` wraps `parse_window`, falls back to `DEFAULT_FRESHNESS_WINDOW`.

## Phase 4: Stale-stamp scanner

- [x] 4.1 RED — `test_lint.py`: `check_stale_stamps` — beyond-window stamp flagged; within-window not flagged; exact-boundary not stale; malformed calendar date (e.g. `2026-13-45`) silently skipped; duplicate stamps dedupe to one finding per `(path, stamp-date)`; `today`/`window` injected, no `datetime.now()` call.
- [x] 4.2 RED — `test_lint.py`: pure-`ingest` bundle scenario — `freshness: snapshot` Source concepts with no `(as of ...)` stamp → zero stale-stamp findings.
- [x] 4.3 GREEN — `lint.py`: `STAMP_RE` + `check_stale_stamps(docs, *, today, window)`.

## Phase 5: Orphan-link normalization + scanner (highest correctness risk)

- [x] 5.1 RED — `test_lint.py`: `normalize_link` — `/`-rooted → `lstrip('/')`; plain-relative resolved against `source_rel_dir`; `./`/`../` resolution; extension-less form resolves to the same identity as its `.md` counterpart; `#fragment` and `` "title" `` stripped; `http`/`https`/`mailto` scheme → `None`; path escaping the bundle root → `None`.
- [x] 5.2 GREEN — `lint.py`: `normalize_link(target, source_rel_dir) -> str | None` via `PurePosixPath`.
- [x] 5.3 RED — `test_lint.py`: `check_orphans` — concept cataloged in `index.md` → not orphan; concept referenced only from another concept's body → not orphan; concept referenced ONLY via a `log.md` link → still flagged orphan (log.md excluded from the referenced-set — the invariant); wholly unreferenced concept → orphan; uncataloged Source → orphan (uniform, no `type` exemption); cataloged Source → not orphan.
- [x] 5.4 GREEN — `lint.py`: `check_orphans(docs, *, index_text)` — builds the referenced-set from `index.md` links plus every concept body, excluding `log.md`.

## Phase 6: CLI wiring

- [x] 6.1 RED — `tests/unit/cli/test_lint.py`: full render (exit 0, `Stale stamps:`/`Orphan pages:` sections); not-a-workspace → Exit 1 + shared `require_workspace` message under an `openkos lint:` prefix, no raw traceback; bad `freshness_window` → fallback notice line; empty-state wording (`No stale stamps.`/`No orphan pages.`); no `--json` flag accepted; no file under the workspace created/modified/deleted on any run.
- [x] 6.2 GREEN — `cli/main.py`: add `lint` command (Phase-A only) — `require_workspace` gate; `read_config(root).freshness_window`; `today = datetime.now(UTC).date()` computed once and injected; `resolve_window`, `collect_docs`, `check_stale_stamps`, `check_orphans`; render sections via `typer.echo` per the design's output layout.
- [x] 6.3 REFACTOR — `cli/main.py`: docstring for `lint` documenting the Phase-A-only flow, the single Exit-1 path, and the fallback-notice behavior.

## Phase 7: Documentation

- [x] 7.1 `docs/cli.md` — record `lint`'s read-only stale-stamp + orphan-page behavior and non-goals (no `--json`, no CI-gating, flat warning-level findings).

## Phase 8: Verification Gate

- [x] 8.1 `uv run pytest --cov` — full suite green, 100% line and branch coverage.
- [x] 8.2 `uv run ruff check .` and `uv run ruff format --check .` — clean.
- [x] 8.3 `uv run mypy .` — clean (strict mode).
- [x] 8.4 `uv build` + wheel smoke test.

## Apply status: 23/23 tasks complete. Ready for verify.

### Final gate numbers
- `uv run pytest --cov -q`: 266 passed, 100.00% total coverage (line + branch); `src/openkos/lint.py` 107 stmts/40 branches, 100%; `src/openkos/cli/main.py` 183 stmts/42 branches, 100%; `src/openkos/config.py` 116 stmts/28 branches, 100%.
- `uv run ruff check .`: All checks passed!
- `uv run ruff format --check .`: 29 files already formatted.
- `uv run mypy .`: Success: no issues found in 29 source files.
- `uv build`: `dist/openkos-0.1.0.tar.gz` + `dist/openkos-0.1.0-py3-none-any.whl` built successfully; wheel smoke-tested in a fresh Python 3.13 venv (`openkos init` → `openkos lint` → `openkos status`, exit 0 on a fresh workspace) and against `examples/good-life-demo/` (exit 0, zero findings — matches design's verified fixture).
- `git diff --stat -- src/openkos/model/okf.py`: empty — okf.py is byte-unchanged.

### Deviation from design (documented, not silent)
`tests/unit/test_lint.py` (pure `lint.py` tests) and `tests/unit/cli/test_lint.py` (CLI tests) share the basename `test_lint.py` in different directories. pytest (`--import-mode=importlib`) handles this without issue, but `uv run mypy .` failed with "Duplicate module named test_lint" because no `tests/**` directory had an `__init__.py`, so mypy's default file-based module-name resolution collapsed both files to the same top-level module name. Fixed via mypy's own suggested resolution (b): added empty `__init__.py` to `tests/`, `tests/unit/`, `tests/unit/bundle/`, `tests/unit/cli/`, `tests/unit/model/` — turning them into proper packages so `tests.unit.test_lint` and `tests.unit.cli.test_lint` resolve as distinct modules. Verified this does not change pytest collection or count (266 tests, same as before the fix). No `pyproject.toml` change was needed or made.

### TDD Cycle Evidence

| Task | RED | GREEN | REFACTOR |
|---|---|---|---|
| 1.1/1.2 `freshness_window` config field | 4 new/extended tests failed (`AttributeError: no attribute 'freshness_window'`) | `config.py`: `DEFAULT_FRESHNESS_WINDOW`, `Config.freshness_window`, `read_config` `is not None` fallback — all pass | n/a (minimal) |
| 2.1/2.2 `collect_docs` + vocabulary | `ImportError: cannot import name 'lint'` (module did not exist) | `lint.py`: `LintDoc`/`LintFinding`/`LintReport` + `collect_docs` wrapping `okf._iter_docs` — 5 tests pass | n/a |
| 3.1-3.4 `parse_window`/`resolve_window` | 21 new tests failed (`AttributeError`) | `parse_window`/`resolve_window` implemented — 26 tests pass | n/a |
| 4.1-4.3 `check_stale_stamps` | 6 new tests failed (`AttributeError`) | `STAMP_RE` + `check_stale_stamps` implemented — 32 tests pass | n/a |
| 5.1/5.2 `normalize_link` | 12 new tests failed (`AttributeError`) | `normalize_link` implemented via `PurePosixPath` — 44 tests pass | n/a |
| 5.3/5.4 `check_orphans` | 6 new tests failed (`AttributeError`) | `check_orphans` implemented — 50 tests pass; 2 extra tests added post-GREEN to close a branch-coverage gap (external-link-normalizes-to-`None` in both `index_text` and doc-body loops) — 52 tests, 100% branch coverage on `lint.py` | n/a |
| 6.1-6.3 CLI wiring | 8 new tests failed (exit code 2, unknown command `lint`) | `cli/main.py::lint` command implemented (workspace gate, injected clock, `resolve_window`/`collect_docs`/`check_stale_stamps`/`check_orphans`, sectioned `typer.echo` render) — 9 tests pass | Docstring added documenting Phase-A-only flow, the two exit-1 paths (absent workspace; unreadable `index.md`), and fallback-notice behavior |
| 7.1 Docs | n/a (docs, not code) | `docs/cli.md` `openkos lint` section rewritten to match final read-only/no-confirmation/two-exit-path behavior; corrected an outdated forward-reference to "a future `lint` command" under `status`'s "Not in this slice" | n/a |

### Work Unit Evidence (single work unit, `size:exception`)

| Evidence | Value |
|---|---|
| Focused test command and exact result | `uv run pytest tests/unit/test_config.py tests/unit/test_lint.py tests/unit/cli/test_lint.py -q` → 87 passed |
| Runtime harness command/scenario and exact result | `openkos lint` run from the built wheel in a fresh `openkos init` workspace (empty-state, exit 0) and against `examples/good-life-demo/` (exit 0, zero findings, matches design's verified fixture) |
| Rollback boundary | `git revert` the PR; `lint.py`, the `lint` command, `freshness_window`, and the four test `__init__.py` files are additive-only; `read_config` collapses to its prior three fields |
