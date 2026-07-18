# Tasks: `openkos status` — read-only bundle overview

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~400 (~140 src: `okf.py` ~50, `cli/main.py` ~55, `log.py` ~25, `config.py` ~10; ~250 test; ~15 docs) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR (`size:exception`) — regression tests (Phase 1) land as the first commit inside the same PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | size-exception |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `require_workspace` extraction + `_iter_docs`/`survey_bundle` (okf) + `read_recent_entries` (log) + `status` CLI command + `ingest` refactor | PR 1 (`size:exception`) | `uv run pytest tests/unit/model/test_okf.py tests/unit/test_config.py tests/unit/bundle/test_log.py tests/unit/cli/test_status.py tests/unit/cli/test_ingest.py` | `uv run openkos status` in a scratch workspace across empty / healthy / catalog-drift / malformed-log / missing-workspace scenarios | `git revert` PR range; `status`, `survey_bundle`, `read_recent_entries` are additive/removable; `ingest` reverts to its pre-extraction inline workspace check |

## Phase 1: Regression safety net (highest risk — before refactor)

- [x] 1.1 RED — `tests/unit/model/test_okf.py`: characterization tests pinning `check_conformance`'s current output on real fixtures (clean bundle, missing-`type` file, unparseable frontmatter, unreadable file → raised `OSError`); must pass unmodified against HEAD.
- [x] 1.2 GREEN/REFACTOR — `model/okf.py`: add frozen `DocScan(path, metadata, read_error, parse_error)` + `_iter_docs(bundle_dir)` generator (sorted `rglob("*.md")`, skip `RESERVED_FILENAMES`, catch `(OSError, UnicodeDecodeError)` as `read_error`, parse failures as `parse_error`); rewrite `check_conformance` to consume it, re-raising `read_error` and emitting the same rule-1/rule-2 strings. Confirm Phase 1.1 tests stay byte-identical.

## Phase 2: Bundle survey (okf.py)

- [x] 2.1 RED — `tests/unit/model/test_okf.py`: `survey_bundle` — `type=="Source"` → `sources`; other non-empty `type` → `concepts`; missing/empty `type`, unparseable frontmatter, unreadable file → finding, not counted; fresh empty bundle → `BundleSurvey(0, 0, [])`.
- [x] 2.2 GREEN — `model/okf.py`: add frozen `BundleSurvey(sources, concepts, findings)` + `survey_bundle(bundle_dir) -> BundleSurvey`, consuming `_iter_docs` (D2/D3).

## Phase 3: Shared workspace gate (config.py)

- [x] 3.1 RED — `tests/unit/test_config.py`: `require_workspace` returns `None` when both `bundle/index.md` and `bundle/log.md` are files; returns the exact reason string `"no OpenKOS workspace found in this directory (run 'openkos init' first)"` when either is missing.
- [x] 3.2 GREEN — `config.py`: add `require_workspace(root: Path) -> str | None` (D1).

## Phase 4: Recent-activity reader (log.py)

- [x] 4.1 RED — `tests/unit/bundle/test_log.py`: `read_recent_entries` — newest-first flattening across `## YYYY-MM-DD` sections, no sort; stops at `limit`; multi-bullet same-day order preserved; malformed section chunk → `ValueError`; empty log body → `[]`.
- [x] 4.2 GREEN — `bundle/log.py`: add frozen `LogEntry(date, text)` + `read_recent_entries(log_text, limit) -> list[LogEntry]` (D4).

## Phase 5: CLI wiring

- [x] 5.1 RED — `tests/unit/cli/test_ingest.py`: regression — `ingest`'s missing-workspace refusal message stays byte-identical after switching to `require_workspace`.
- [x] 5.2 GREEN — `cli/main.py`: refactor `ingest` to call `config.require_workspace(root)` in place of its inline `index.md`/`log.md` check.
- [x] 5.3 RED — `tests/unit/cli/test_status.py`: full render in a healthy workspace (exit 0, three sections); not-a-workspace → Exit 1 + shared `require_workspace` message under an `openkos status:` prefix, no raw traceback; malformed/unreadable `log.md` → degrade notice, exit 0; fresh-bundle empty-state wording (`Sources: 0`, `Concepts: 0`, "No activity recorded yet.", "Nothing needs attention."); counts reflect disk scan, not `index.md` (catalog-drift scenario); a conformance violation is listed under "Needs attention" and exit stays 0; no `--json` flag accepted; no file under the workspace is created/modified/deleted on any run.
- [x] 5.4 GREEN — `cli/main.py`: add `status` command (Phase-A only) — `require_workspace` gate (Exit 1 on reason); `survey_bundle(bundle_dir)` for counts + findings; read `log.md` text, `except (OSError, ValueError)` around `read_recent_entries(text, RECENT_ACTIVITY_LIMIT=5)` → degrade notice; render the three sections via `typer.echo` per the design's output layout (Q4); exit 0 on every success path.
- [x] 5.5 REFACTOR — `cli/main.py`: docstring for `status` documenting the Phase-A-only flow, the single Exit-1 path, and the recent-activity degrade behavior.

## Phase 6: Documentation

- [x] 6.1 `docs/cli.md` — record `status`'s read-only, three-section behavior and its non-goals (no `--json`, no lint checks, non-zero exit only on a missing/unreadable workspace).

## Phase 7: Verification Gate

- [x] 7.1 `uv run pytest --cov` — full suite green, 100% line and branch coverage.
- [x] 7.2 `uv run ruff check .` and `uv run ruff format --check .` — clean.
- [x] 7.3 `uv run mypy .` — clean (strict mode).
- [x] 7.4 `uv build` + wheel smoke test.
