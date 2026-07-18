# Verification Report: add-status-command

**Change**: add-status-command
**Branch**: feat/status-command
**Mode**: Strict TDD
**Verdict**: PASS WITH WARNINGS

## Gate — independently re-run (not trusted from apply-progress)

| Check | Result |
|-------|--------|
| `uv run pytest --cov -q` | **197 passed**, coverage **100.00%** line, **100.00%** branch (448/448 stmts, 110/110 branches; `fail_under=90` exceeded) |
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | 21 files already formatted |
| `uv run mypy .` (strict) | Success: no issues found in 21 source files |
| `uv build` | Successfully built `openkos-0.1.0.tar.gz` + `.whl` (re-verified, not just trusted) |

All numbers are **byte-identical** to what apply-progress (#903) reported. No discrepancy found.

## Task completeness (18/18)

All 18 tasks in `tasks.md` and Engram `sdd/add-status-command/tasks` (#902) are marked `[x]` and are backed by real code + tests, confirmed by direct source read (not just checkbox trust):

- Phase 1 (1.1/1.2): `_iter_docs`/`DocScan` extracted in `model/okf.py`; `check_conformance` rewritten to consume it.
- Phase 2 (2.1/2.2): `BundleSurvey`/`survey_bundle` added, consuming the same `_iter_docs` walk.
- Phase 3 (3.1/3.2): `config.require_workspace(root) -> str | None` added.
- Phase 4 (4.1/4.2): `bundle/log.py` `LogEntry`/`read_recent_entries` added.
- Phase 5 (5.1-5.5): `ingest` refactored onto `require_workspace`; `status` command added to `cli/main.py`.
- Phase 6 (6.1): `docs/cli.md` updated.
- Phase 7 (7.1-7.4): verification gate — confirmed above, independently.

## Spec conformance — 9 scenarios in `specs/status/spec.md`, all covered by a passing test

| # | Scenario | Covering test | Result |
|---|----------|---------------|--------|
| 1 | Run outside a workspace | `test_status_refuses_when_not_a_workspace` | exit 1, exact stderr message, no traceback — confirmed |
| 2 | Healthy bundle with sources | `test_status_healthy_bundle_full_render_has_three_sections` | `Sources: 1`, `Concepts: 0` — confirmed |
| 3 | Freshly initialized empty bundle | `test_status_fresh_bundle_empty_state` | `Sources: 0`, `Concepts: 0`, exit 0 — confirmed |
| 4 | Catalog drift — disk is the truth | `test_status_counts_reflect_disk_scan_not_index` | orphan file on disk counted, absent from `index.md` — confirmed |
| 5 | Healthy bundle shows recent activity | `test_status_healthy_bundle_full_render_has_three_sections` | Ingest entry rendered — confirmed |
| 6 | Empty log | `test_status_empty_log_body_shows_no_activity_recorded` | "No activity recorded yet." — confirmed |
| 7 | No conformance issues | `test_status_fresh_bundle_empty_state` | "Nothing needs attention." — confirmed |
| 8 | Conformance violation surfaced, non-fatal | `test_status_conformance_violation_is_surfaced_but_non_fatal` | finding listed, exit 0 — confirmed |
| 9 | No mutation on any run (incl. `--json` rejection) | `test_status_never_writes_to_the_workspace` + `test_status_rejects_json_flag` | full pre/post directory snapshot equality; `--json` exits non-zero via typer's own unknown-option handling — confirmed |

Note: the task brief referenced "12 spec scenarios" — the spec file as retrieved (Engram #899 and on-disk `specs/status/spec.md`, byte-identical) actually enumerates **9** scenarios across 5 requirements. All 9 are covered; no scenario is untested.

An additional test (`test_status_malformed_log_degrades_and_still_exits_zero`) covers the Q2/D5 lenient-degrade design decision, which is not a separate spec scenario but is directly implied by the "Recent Activity" requirement's robustness.

## Regression checks

- `check_conformance` §9 byte-identical to pre-refactor: `test_check_conformance_round_trip_regression` and `test_check_conformance_still_raises_oserror_after_refactor` in `tests/unit/model/test_okf.py` both pin exact violation strings/ordering and the `OSError`-raise contract; both pass under the current (post-refactor) code — confirmed via the independent full-suite run (197/197 green).
- `ingest`'s refusal message is byte-for-byte unchanged: `test_refuses_when_not_a_workspace_byte_identical_message` in `tests/unit/cli/test_ingest.py` asserts the exact stderr string; passes — confirmed.

## No-mutation confirmation

`status` never writes: `require_workspace`/`survey_bundle`/`read_recent_entries` are read-only by construction (no `fsio.write_*`/`Path.write_*` calls anywhere in the `status` command body), and `test_status_never_writes_to_the_workspace` snapshots the full directory tree (bytes of every file) before and after a `status` run against a bundle with a conformance finding, asserting equality — confirmed by source read and passing test.

## Design deviation — WARNING (not CRITICAL)

`design.md` §Q4/D4 and its "Output layout" diagram specify a trailing `(… and <N> more)` line "when entries exceed the limit." The apply did NOT implement this. Verified as an **acceptable, but real, gap**, not a false claim:

- `specs/status/spec.md`'s "Recent Activity from log.md" requirement and both its scenarios (Healthy bundle shows recent activity, Empty log) say nothing about a truncation indicator — no spec scenario depends on it.
- `tasks.md` Phase 5.3's RED test list (the authoritative task-level acceptance criteria) does not mention this line either.
- No test in `tests/unit/cli/test_status.py` or `tests/unit/bundle/test_log.py` asserts a "more" line.
- The `read_recent_entries(log_text, limit) -> list[LogEntry]` interface specified in design.md's own Interfaces section cannot support the feature without either an extra probe fetch (`limit + 1`) or changing the return shape to `(entries, has_more)` — i.e. design.md's Interfaces section and its Output-layout section are internally inconsistent, and the apply followed the literal interface contract.

Per the standard decision gate ("Design deviation exists → WARNING unless it breaks a spec"), this does not break any spec scenario, so it is **WARNING**, not CRITICAL. It is a legitimate follow-up candidate (e.g. for the future `lint`/`query` precedent) but does not block this change.

## Issues

**CRITICAL**: none.

**WARNING**:
1. Design deviation — `design.md` D4/Q4's trailing `(… and N more)` recent-activity line is not implemented. No spec scenario or task RED item requires it; recommend either updating `design.md` to mark it explicitly out-of-scope for this slice, or filing a follow-up task before it is assumed present by a future command.

**SUGGESTION**: none.

## Final Verdict

**PASS WITH WARNINGS** — all 18 tasks genuinely complete, all 9 spec scenarios covered by passing tests, full gate independently re-run and green (197 passed, 100.00% line+branch coverage, ruff clean, mypy clean, build succeeds), both regression contracts (`check_conformance` byte-identical, `ingest` refusal message byte-identical) hold, and `status` is confirmed read-only. One WARNING (design/spec gap on the recent-activity truncation line) does not block archive.
