# Archive Report: add-lint-command

**Change**: add-lint-command | **Archived**: 2026-07-18 | **Status**: Complete | **Repository**: openkos (main 0a0c674)

This archive report closes the SDD cycle for the `add-lint-command` change. The feature implements the MVP-1 read-only bundle health check — `openkos lint` — a mechanical stale-stamp and orphan-page scanner with no mutations, no structured output, and no CI-gating. The implementation reuses `okf._iter_docs` read-only, introduces a standalone `LintReport`/`LintFinding` vocabulary, adds a `freshness_window` config field, and establishes the clock-injection precedent for future reader commands.

## Change Summary

**Purpose**: Ship the second **read** command (`lint`) promised in `docs/cli.md`, delivering both stale-stamp and orphan-page freshness checks as non-gating informational findings, following `status`'s precedent of Phase-A-only implementation.

**Scope**:
- New `src/openkos/lint.py` module with `LintReport`/`LintFinding`/`LintDoc` vocabulary (fully separate from `okf.BundleSurvey`/conformance)
- Stale-stamp scanner: regex-scan concept bodies for inline `(as of YYYY-MM-DD)` stamps, compare against `freshness_window` (default `7d`)
- Orphan-page scanner: flat markdown-link scan (no graph), exclude `log.md` from referenced-set per ADR Q2
- Clock injection: `today` computed once in `cli/main.py::lint()`, passed into checks
- Duration parser: support `Nd`/`Nw` (days/weeks) with graceful fallback to `7d` on unparseable/zero/negative
- Link normalization: canonical identity via POSIX bundle-relative path, uniform handling of `/`-rooted, plain-relative, `./`/`../`, extension-less forms
- Configuration: add `freshness_window: str` field to `Config`, passthrough to `lint.py` (no parsing in config)
- Read-only: zero mutations, zero `--json`, exit 0 on any successful run (even with findings)

**Key Architecture Decisions**:
- Q1: Canonical link identity = POSIX bundle-relative path minus `.md` (unifies all link forms, no false orphans on form drift)
- Q2: `index.md` IS a reference source (entries are real markdown links); `log.md` EXCLUDED from referenced-set (history is not reachability)
- Q3: Uniform Source treatment (no `type` exemption; safe because Q2 ensures ingest catalogs every Source)
- Q4: Duration grammar `Nd`/`Nw`, fallback to `7d` on invalid, lint never raises on bad config
- Q5: Only valid calendar dates in `(as of YYYY-MM-DD)` are stamps; malformed dates silently skipped, never crash

**Honest MVP-1 limitation**: `openkos ingest` produces only `freshness: snapshot` Sources with no `(as of ...)` body stamps by design. A bundle built purely from `ingest` therefore shows zero stale findings — this is correct, not broken. The check is generic (scans all non-reserved bodies) so it works forward-compatibly once MVP-2 emits stamped `pointer` concepts.

**Documented non-blocking follow-up** (in design and tasks): `lint` does not yet surface `okf._walk_errors` (directory-walk errors) the way `status` does; MVP-2 or a future enhancement may extend this.

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-18-add-lint-command/proposal.md` | Moved from change folder; includes 5 open design questions (all resolved in design.md) |
| Specification | `archive/2026-07-18-add-lint-command/specs/lint/spec.md` | Promoted to main spec tree at `openspec/specs/lint/spec.md` + moved to archive |
| Design | `archive/2026-07-18-add-lint-command/design.md` | Moved from change folder; documents Q1-Q5 decisions, link normalization rationale, testing strategy |
| Tasks | `archive/2026-07-18-add-lint-command/tasks.md` | 23/23 checked; all 8 phases complete |
| Verification Report | `archive/2026-07-18-add-lint-command/verify-report.md` | PASS (no CRITICAL, no WARNING, no SUGGESTION issues; 11/11 spec scenarios passing; 5/5 design decisions verified) |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **NEW** | `lint` | Created new capability spec at `openspec/specs/lint/spec.md` |
| Requirements at archive time | 5 | Workspace Presence Check (1 scenario), Stale-Stamp Scan (3 scenarios), Orphan-Page Scan (3 scenarios), Non-Gating Exit Contract (2 scenarios), Read-Only and Human-Readable Only (1 scenario) |
| Total scenarios at archive time | 11 | Full coverage of workspace gating, stale-stamp and orphan detection, empty states, non-zero handling, no mutations, no structured output |
| Source | Delta spec from change folder | `/openspec/changes/add-lint-command/specs/lint/spec.md` promoted to `/openspec/specs/lint/spec.md` |
| Merge mode | NEW capability | The `lint` capability did not exist before; this change establishes it. |
| Divergence note | Archived historical copy | The archived delta copy at `openspec/changes/archive/2026-07-18-add-lint-command/specs/lint/spec.md` is left unchanged as the historical record; the canonical `openspec/specs/lint/spec.md` is the source of truth for this capability going forward. |

## Verification Status

**Final Verdict**: PASS (0 CRITICAL blocking, 0 WARNING blocking, 0 SUGGESTION blocking)

**Evidence Summary**:
- All 23/23 tasks checked and verified complete via independent source inspection
- Spec compliance: 11/11 scenarios covered by passing tests (test_lint_refuses_when_not_a_workspace, test_check_stale_stamps_flags_a_stamp_beyond_the_window, test_check_stale_stamps_does_not_flag_a_stamp_within_the_window, test_check_stale_stamps_exact_boundary_is_not_stale, test_check_stale_stamps_pure_ingest_bundle_has_zero_findings, test_check_orphans_wholly_unreferenced_concept_is_orphan, test_check_orphans_cataloged_concept_is_not_orphan, test_check_orphans_referenced_only_from_another_concepts_body_is_not_orphan, test_lint_fresh_bundle_empty_state, test_lint_flags_a_stale_stamp, test_lint_flags_an_orphan_page, plus exit-0 and no-mutation assertions)
- Design decision verification: Q1 (link normalization tests), Q2 (log.md exclusion structurally proven + test), Q3 (uniform Source treatment tests), Q4 (duration parser + fallback tests), Q5 (malformed-date handling tests)
- Test execution (final, independent verify run): 266 passed, 0 failed, 0 skipped
- Coverage: 100% line + 100% branch (gate ≥90%)
- Quality gates:
  - `uv run ruff check .` pass (exit 0)
  - `uv run ruff format --check .` pass (29 files already formatted)
  - `uv run mypy .` pass (strict mode, 29 source files, no issues)
  - `uv build` succeeded, wheel smoke test (fresh `openkos init` workspace → `openkos lint` exit 0 + clean empty state; `examples/good-life-demo/` → `openkos lint` exit 0 + zero findings) passed
- Byte-unchanged: `git diff --stat -- src/openkos/model/okf.py` → empty (okf.py untouched)
- Clock injection: `rg "datetime.now()" src/openkos/lint.py` → no match (lint.py never touches the clock; today injected from cli/main.py)

**Deviation: 5 test `__init__.py` files added**
- Reason: mypy "Duplicate module named test_lint" collision between `tests/unit/test_lint.py` and `tests/unit/cli/test_lint.py` (both appeared as the same module to mypy's default resolution)
- Fix: added empty `__init__.py` to `tests/`, `tests/unit/`, `tests/unit/bundle/`, `tests/unit/cli/`, `tests/unit/model/` to make them proper packages
- Verified: all 5 files are 0 bytes (no logic); pytest collection unchanged (266 tests); pyproject.toml unchanged (no config changes)
- Verdict: acceptable, benign, complete fix (no directory left without `__init__.py`)

## Delivery History

This change was delivered as a single PR after orchestrator approval of `size:exception`:
- **PR #18** (merged to main, 2026-07-18, after review approval and correction rounds): Complete lint implementation — config field + duration parser + link normalization + stale-stamp scanner + orphan-page scanner + CLI wiring + docs + 23 tasks + 266 tests + 100% coverage. Underwent bounded review process: first lineage `review-28c03b532d2cc2ff` ESCALATED (correction budget 201 exceeded 200-line limit); a fresh lineage `review-2ab97da2af81d9c5` on the corrected tree was APPROVED after one further correction (an inferential CRITICAL — incomplete TOCTOU guard around `okf.load_frontmatter` — which a refuter CORROBORATED and was then fixed); final gate 278 tests passed (full suite), 100% line+branch coverage, ruff+mypy clean.

**Repository State**: main @ 0a0c674 (commit: "docs(sdd): archive add-lint-command" — this archive commit itself)

**Issue**: #17 closed (feature request: "add `openkos lint`")

## Review Gate & Closure

**Delivery review history**:
- PR #18 (complete implementation): bounded 4R review started (`review/start(target)`), first lineage `review-28c03b532d2cc2ff` ESCALATED (correction needed, 201 > 200 budget); fresh lineage `review-2ab97da2af81d9c5` on corrected tree APPROVED (terminal ALLOW receipt) after one final correction (inferential CRITICAL about TOCTOU guard fixed via refuter corroboration)

**Current status**:
- PR #18 merged to main
- Issue #17 closed
- All 266 tests passing, 100% line+branch coverage
- No blockers remain; all critical findings closed
- No escalations or follow-ups beyond the documented lint non-goal (okf._walk_errors surfacing, left for MVP-2)

**Review receipt**: lineage `review-2ab97da2af81d9c5`, terminal state ALLOW, reflects corrected candidate tree at 0a0c674

## Implementation Details

**Modules added/modified**:
- `src/openkos/lint.py`: `LintDoc`/`LintFinding`/`LintReport` (frozen dataclasses), `collect_docs(bundle_dir)`, `normalize_link(target, source_rel_dir)`, `parse_window(raw)`/`resolve_window(raw)`, `check_stale_stamps(docs, *, today, window)`, `check_orphans(docs, *, index_text)`
- `src/openkos/config.py`: `DEFAULT_FRESHNESS_WINDOW = "7d"`, `Config.freshness_window: str`, `read_config` fallback via `is not None`
- `src/openkos/cli/main.py`: `lint` command (Phase-A only: workspace gate, clock injection, config read, window resolve, doc collect, stale/orphan checks, sectioned typer.echo render, exit 0)
- `src/openkos/model/okf.py`: untouched (byte-unchanged confirmed via git diff)
- `tests/unit/test_lint.py`: 52 pure tests (collect_docs, parse_window, resolve_window, normalize_link, check_stale_stamps, check_orphans)
- `tests/unit/cli/test_lint.py`: 9 CLI integration tests (full render, workspace gating, fallback notice, empty states, no-mutation check)
- `tests/unit/test_config.py`: extended with 3 tests for `freshness_window` field
- `docs/cli.md`: documented `lint` behavior (read-only, two non-gating checks, `freshness_window` config)
- `tests/` directory tree: 5 new `__init__.py` files (mypy disambiguation fix)

**Link normalization algorithm**:
- Input: markdown link `target` and source doc's `source_rel_dir` (bundle-relative directory)
- Process: drop `#fragment` and ` "title"` suffix; return `None` for `scheme:` URLs (http/https/mailto); resolve `/`-rooted to `lstrip('/')`, relative/`./`/`../` against source dir via `PurePosixPath`, skip external (escape-bundle) paths, strip trailing `.md`
- Output: canonical POSIX bundle-relative path (e.g., `concepts/x` from `/concepts/x.md`, `concepts/x.md`, `./x.md`, `x`)

**Stale-stamp detection**:
- Regex pattern `(as of YYYY-MM-DD)` matched via `STAMP_RE`
- Date validation via `date(y, m, d)` in try/except (malformed silently skipped)
- Stale iff `today - stamp_date > window`
- One finding per unique `(path, stamp-date)` pair (deduplicates multi-stamp concepts)

**Orphan detection**:
- Referenced-set built from markdown links in `index.md` + all concept bodies
- `log.md` explicitly excluded (see ADR Q2: history is not reachability)
- A concept is orphan iff its identity is absent from the referenced-set
- Uniform treatment: no `type: Source` exemption (Q3: safe because Q2 ensures ingest catalogs every Source in index.md)

**Non-transactional guarantee**: `lint` performs zero writes and zero execution; it is purely a read/scan with exit 0 on success, exit 1 only on workspace-absent or index.md-unreadable (via `require_workspace` gate).

## Archival Actions Completed

**Filesystem**:
- [x] Main spec tree created at `openspec/specs/lint/spec.md` (5 requirements, 11 scenarios)
- [x] Change folder moved to `openspec/changes/archive/2026-07-18-add-lint-command/` (all artifacts: proposal, design, tasks, verify-report, specs)
- [x] All change artifacts archived in the dated folder
- [x] Original `openspec/changes/add-lint-command/` removed (verified via post-archive glob check)

**Engram**:
- [x] Archive report saved with topic key `sdd/add-lint-command/archive-report`
- [x] Traceability: verified observations for proposal, spec, design, tasks, verify-report

## Next Steps

**For the project**:
- Archive folder now permanently in `openspec/changes/archive/2026-07-18-add-lint-command/`
- Main spec tree updated: `openspec/specs/lint/spec.md` is the canonical, promoted spec for the `lint` capability
- No follow-up changes required for this change (MVP-1 read-only health check is complete)

**Documented non-blocking follow-ups** (intentionally deferred, not blockers):
- `lint` does not yet surface `okf._walk_errors` (directory-walk errors); MVP-2 or future enhancement may extend this to parallel `status`'s error reporting

## Risks & Mitigations

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| Link-form drift causes false orphans | Low | ADR Q1 unifies all link forms into one canonical identity; test coverage (5 normalize_link tests + orphan scenarios) | Verified |
| Stale detection becomes too noisy (MVP-1 ingest produces zero stale findings) | Low | Documented plainly in proposal + design; matches honest MVP-1 limitation; test confirms pure-ingest → zero stale | Verified |
| Scope creep into conformance or CI-gating | Low | Non-goals explicit in spec; separate vocabulary; flat warning-level only | Verified |
| `freshness_window` config unparseable causes silent failure | Low | Graceful fallback to 7d + notice printed; lint never crashes on bad config (Q4) | Verified |
| Malformed `(as of ...)` date causes crash | Low | Regex shape-match then `date(y,m,d)` in try/except; malformed silently skipped (Q5) | Verified |
| Missing workspace identity recovery on lint errors | Low | Workspace-absent → `require_workspace` gate (same as ingest/status); unreadable index.md → OSError caught, exit 1 with error message; no silent failures | Verified |

## Deferred/Out-of-Scope Items

**Explicitly deferred to later changes**:
- CI-gating or non-zero exit on findings (findings remain informational, exit 0 on successful run)
- Error vs. warning tiers (flat warning-level in MVP-1)
- `--json` or structured output (human-readable text only)
- Volatility classification via the `freshness` field (lint never reads it; MVP-2 concern)
- Conformance checking blurred with lint checks (separate vocabularies, per OKF §9)
- Surfacing `okf._walk_errors` (directory-walk errors); MVP-2 enhancement

**Accepted residual limitations**:
- `lint` scans only inline `(as of ...)` body stamps, never the `freshness` field (forward-compatible with MVP-2 when stamped `pointer` concepts are emitted)
- No automatic fix or remediation; findings are informational only (same as `status`)

## Traceability

This archive report records the final state of the `add-lint-command` change from proposal through implementation, verification, and archival. The change has been:
- Fully specified (5 requirements, 11 scenarios, `lint` capability spec at `openspec/specs/lint/spec.md`)
- Fully designed (5 architecture decisions Q1-Q5, link normalization + stale/orphan checks + clock injection + zero ADRs)
- Fully implemented (single PR, 1 new module + 2 modified modules + 65+ tests + docs, 266 tests, 100% line+branch coverage)
- Fully verified (all 23 tasks verified complete, all 11 spec scenarios passing tests, all 5 design decisions verified in code, no CRITICAL/WARNING/SUGGESTION issues)
- Fully delivered (PR #18 merged to main, issue #17 closed, review approval obtained via lineage review-2ab97da2af81d9c5)

The SDD cycle is CLOSED. The change is archived and ready for the next change.

**Archive Date**: 2026-07-18 (ISO format)
**Repository Head**: 0a0c674 (main)
**Specification**: `openspec/specs/lint/spec.md` (canonical, promoted from delta spec, 5 requirements, 11 scenarios)
**Verification Date**: 2026-07-18 (verify-report pass)
**Archival Status**: COMPLETE
**Artifact Observation IDs**: verify-report #916 (Engram); proposal, spec, design, tasks, verify-report in archive folder
