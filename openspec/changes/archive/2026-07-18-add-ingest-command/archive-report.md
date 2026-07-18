# Archive Report: add-ingest-command

**Change**: add-ingest-command | **Archived**: 2026-07-18 | **Status**: Complete | **Repository**: openkos (main de5eae3)

This archive report closes the SDD cycle for the `add-ingest-command` change. The feature implements the MVP 1 "null compiler" — a real `openkos ingest <path>` command that copies raw source, generates one conformant OKF Source concept (with honest "not yet extracted" description), records provenance OKF-natively, and updates the bundle catalog (`index.md`) and log (`log.md`), with no LLM extraction. The implementation includes config reading, bundle append primitives, atomic write capability, and a Phase A/B confirm-based workflow matching the shape of `init`.

## Change Summary

**Purpose**: Ship the first useful vertical slice of the `openkos ingest <path>` command promised in `docs/cli.md`, proving the end-to-end ingest write path and review/confirm UX so later slices add only extraction logic, not new plumbing.

**Scope**:
- Config reader (`config.read_config`) returning `model`, `review`, `default_sensitivity` from `openkos.yaml`
- Bundle append primitives for `index.md` (catalog entry) and `log.md` (dated line)
- Non-exclusive atomic write primitive (`fsio.write_atomic`) separate from `write_exclusive` (stays create-only)
- `openkos ingest <path>` command with Phase A (compute in memory) / Phase B (create-only + atomic writes in order)
- Review/confirm flow with `--auto` flag and config-driven `review` setting
- Source concept generation (plain dict, no pydantic) with honest null-compiler description + provenance + `# Citations` body
- Single-concept-per-source model (MVP 1 per knowledge-object-model.md, multi-concept deferred to MVP 2)

**Key Decisions**:
- D1: `fsio.write_atomic` — temp-file + `os.replace` in same dir, atomic rename, `write_exclusive` stays create-only as a separate primitive
- D2: Append = parse-then-render with frontmatter bytes verbatim — no YAML AST round-trip, clean body-only edits
- D3: `read_config` via PyYAML `safe_load`, frozen `Config` dataclass, packaged defaults, wrapped `YAMLError` as `ValueError` (matches CLI exception contract)
- D4: Plain dict + `dump_frontmatter` + `check_conformance`, NO pydantic (no untrusted JSON to validate in this slice)
- D5 (RETREATED, final): Phase A/B mirrors `init`; Phase B is **create-only/atomic per-file, non-transactional, git recovery** (see design.md "Known limit" section). Content written before catalog so catalog never references missing files; a failure partway through leaves detectable orphans recoverable via `git status`/`git checkout`

**The D5 Retreat**: An earlier bounded review attempted multi-step rollback (undo flags, reverse-order unlink/restore). A fresh bounded review found two CRITICALs proving that rollback cannot be made truly atomic across independent filesystem writes — a failure *during rollback itself* has no further fallback. The maintainer retreated to D5's originally-ratified position: **no in-process rollback, content-before-catalog ordering, git recovery**. This matches `init`'s own D3 ("no cleanup path") and is the final, shipped behavior across all 166 tests + 100% coverage gate.

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-18-add-ingest-command/proposal.md` | Moved from change folder; includes deferred items + risks |
| Specification | `archive/2026-07-18-add-ingest-command/specs/ingestion/spec.md` | Promoted to main spec tree at `openspec/specs/ingestion/spec.md` + moved to archive |
| Design | `archive/2026-07-18-add-ingest-command/design.md` | Moved from change folder; includes D5 retreat rationale in "Known limit" section |
| Tasks | `archive/2026-07-18-add-ingest-command/tasks.md` | 19/19 checked; all phases complete |
| Apply Progress | `archive/2026-07-18-add-ingest-command/apply-progress.md` | Detailed 2-PR chronological batch log (primitives PR + CLI PR) + review corrections + retreat + verify closure |
| Verification Report | `archive/2026-07-18-add-ingest-command/verify-report.md` | PASS (critical issue closed: missing test for "No workspace config" scenario now covered; stale doc drift fixed) |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **NEW** | `ingestion` | Created new capability spec at `openspec/specs/ingestion/spec.md` |
| Requirements at archive time | 9 | Config Reader (2 scenarios), Bundle Catalog Append (1 scenario), Bundle Log Append (1 scenario), Non-Exclusive Atomic Write (2 scenarios), Ingest Raw Copy and Source Concept Generation (3 scenarios), Path Containment (1 scenario), OKF-Native Provenance (1 scenario), Review/Confirm Flow (6 scenarios), Default Sensitivity from Config (1 scenario) |
| Total scenarios at archive time | 18 | Corrected count (previously miscounted as 19 with Review/Confirm Flow listed as 7 scenarios; the requirement had 6). Full coverage of config reading, bundle append, atomic write, path containment, source concept generation, Phase A/B flow, confirm-gate ordering, TTY/non-TTY handling, and `--auto` behavior |
| **Post-archive completion** | +4 scenarios | The archive review found 3 shipped, tested behaviors from PR #12's review-correction rounds were never propagated to the spec. Added to the canonical `openspec/specs/ingestion/spec.md` (no new requirement headings — scenarios added under existing requirements): (1) `write_exclusive` cleanup-on-write-failure scenario under **Non-Exclusive Atomic Write** (`fsio.py`, `tests/unit/test_fsio.py::test_write_exclusive_unlinks_partial_file_on_write_failure`); (2) empty-slug refusal scenario under **Path Containment** (`cli/main.py`, `tests/unit/cli/test_ingest.py::test_empty_slug_after_sanitization_refuses`); (3) two newline-rejection scenarios, one each under **Bundle Catalog Append** and **Bundle Log Append** (`bundle/index.py` + `bundle/log.py` `_reject_newline`, `tests/unit/bundle/test_index.py` + `tests/unit/bundle/test_log.py`) |
| Requirements — final (canonical spec.md) | 9 | Unchanged — no new requirement headings added, only scenarios |
| Total scenarios — final (canonical spec.md) | 22 | 18 (archive-time, corrected) + 4 (post-archive completion) |
| Source | Delta spec from change folder | `/openspec/changes/add-ingest-command/specs/ingestion/spec.md` promoted to `/openspec/specs/ingestion/spec.md` |
| Merge mode | NEW capability | The `ingestion` capability did not exist before; this slice establishes it. |
| Divergence note | Archived historical copy | The archived delta copy at `openspec/changes/archive/2026-07-18-add-ingest-command/specs/ingestion/spec.md` is left unchanged as the historical record of the delta as written (18 scenarios); only the canonical `openspec/specs/ingestion/spec.md` was completed to 22 scenarios to match shipped+tested behavior. |

## Verification Status

**Final Verdict**: PASS (0 CRITICAL blocking, 0 WARNING blocking)

**Evidence Summary**:
- All 19/19 tasks checked and verified complete via independent source inspection
- Spec compliance: 18/18 scenarios covered by passing tests at archive time (corrected count; final gate after verify-closure batch closed the missing "No workspace config" test)
- Test execution (final, post-retreat, post-verify-closure): 166 passed, 0 failed, 0 skipped
- Coverage: 100% line + 100% branch (gate ≥90%)
- Quality gates:
  - `uv run ruff check .` pass (exit 0)
  - `uv run ruff format --check .` pass (20 files already formatted)
  - `uv run mypy .` pass (strict mode, 20 source files, no issues)
  - `uv build` succeeded, wheel smoke test (installed to scratch venv, `openkos init` + `openkos ingest --auto` end-to-end) passed

**Closed Issues**:
1. CRITICAL from verify-report: Missing test for Config Reader "No workspace config" scenario — FIXED in the verify-closure batch by adding two tests (`test_read_config_raises_clear_error_when_config_missing` in test_config.py + `test_missing_config_refuses_via_ingest` in test_ingest.py)
2. WARNING from verify-report: Stale "all-or-nothing" language in proposal.md + tasks.md after D5 retreat — FIXED by rewording 4 spots in proposal.md + 1 spot in tasks.md with "superseded — see design D5" reconciliation notes

**Non-blocking tracked follow-ups** (intentionally deferred, not blockers):
1. Decompose the `ingest` command body (currently ~150 lines inline in `cli/main.py`) — identified as refactoring opportunity, left for a future enhancement PR
2. Deduplicate `_reject_newline` between `bundle/index.py` and `bundle/log.py` — utility could be moved to a shared module, left for future refactoring
3. `bundle.create` docstring mentions "write_exclusive" behavior; description slightly under-documents the newer `write_atomic` capability alongside it — minor stale phrasing, left for later review
4. PR #12 review suggestion (from okf-literals review): document OKF literal semantics and resource-path conventions — left for a documentation follow-up PR

## Delivery History

This change was delivered as two stacked PRs:
- **PR #11** (merged to main, 2026-07-17, after review approval): Pure primitives + config reader + manifest (tasks phases 1-6) — `config.read_config`, `fsio.write_atomic`/`copy_exclusive`, `bundle/index.py::insert_source_entry`, `bundle/log.py::insert_log_entry`, `model/okf.py::build_source_concept`, `pyproject.toml` pyyaml dep. Underwent hardening corrections (present-but-null fallback, unique temp names, cleanup on partial write) + review approval.
- **PR #12** (merged to main, 2026-07-17, after review approval): `ingest` CLI command + docs (tasks phases 7-9) — added RISK-1/RISK-2 newline-rejection fixes to primitives; `cli/main.py` ingest Phase A/B with confirm gate; `docs/cli.md` updated; full verification gate (166 tests, 100% coverage). Underwent review corrections (all-or-nothing rollback attempted, then retreated per D5), escalation follow-ups (dead-code cleanup, workspace-identity recovery), the major D5 retreat (removing rollback, restoring non-transactional design), and verify-closure (missing test + stale doc drift).

**Repository State**: main @ de5eae3 (commit: "docs(sdd): archive add-ingest-command" — this archive commit itself)

## Review Gate & Closure

**Delivery chain history**:
- PR #11 (primitives): bounded 4R review approved (`review/start(target)` → ALLOW receipt)
- PR #12 (CLI): bounded 4R review approved, then review corrections applied, escalation follow-ups applied, D5 retreat applied, verify-closure applied; re-validated with fresh evidence; final receipt ALLOW

**Current status**:
- Both PRs merged to main
- Issue #10 closed
- All 166 tests passing, 100% coverage
- No blockers remain; all critical and warning findings closed
- Non-blocking suggestions tracked for follow-up PRs

## Implementation Details

**Modules added/modified**:
- `config.py`: `read_config(root) -> Config` (frozen dataclass with model, review, default_sensitivity)
- `fsio.py`: `write_atomic(path, content)` (temp+replace, separate from create-only `write_exclusive`), `copy_exclusive(src, dst)` (binary "xb", create-only)
- `bundle/index.py`: `insert_source_entry(...)` (parse-then-render, frontmatter verbatim, `# Sources` section append)
- `bundle/log.py`: `insert_log_entry(...)` (dated section parse-then-render, prepend within today's section)
- `model/okf.py`: `build_source_concept(...)` (plain dict → dump_frontmatter, no pydantic, honest description + provenance + `# Citations`)
- `cli/main.py`: `ingest` command (Phase A compute, Phase B write, confirm gate: `--auto` > `review:false` > TTY > non-TTY-refuse)
- `pyproject.toml`: declared `pyyaml>=6.0.3` as direct runtime dep (was already transitive)

**Path containment**: destinations derive from `Path(src).name` (strips directory components) + sanitized slug (no `/`), never raw user path segments. `../../evil.txt` lands as `raw/evil.txt`, never outside `raw/` or `bundle/sources/`.

**Non-transactional guarantee**: Phase B writes content before catalog (`mkdir bundle/sources` → `copy_exclusive` raw → `write_exclusive` concept → `write_atomic` index → `write_atomic` log). Each individual write is create-only or atomic, so no file is left half-written. A failure partway leaves a detectable orphan, visible via `git status`, recoverable via `git checkout`/`git clean`.

## Archival Actions Completed

**Filesystem**:
- [x] Main spec tree created at `openspec/specs/ingestion/spec.md`
- [x] New `ingestion` capability spec with 9 requirements, 18 scenarios at archive time (corrected count; later completed to 22 scenarios in the canonical spec — see "Post-archive completion" in Spec Merge Summary)
- [x] Change folder moved to `openspec/changes/archive/2026-07-18-add-ingest-command/`
- [x] All change artifacts archived (proposal, design, tasks, verify-report, apply-progress, specs)

**Engram**:
- [x] Archive report saved with topic key `sdd/add-ingest-command/archive-report`
- [x] All artifact observation IDs recorded for traceability (proposal #873, spec #875, design #876, tasks #878, verify-report #890)

## Next Steps

**For the project**:
- Archive folder now permanently in `openspec/changes/archive/2026-07-18-add-ingest-command/`
- Main spec tree updated: `openspec/specs/ingestion/spec.md` is the canonical, promoted spec for the `ingestion` capability
- No follow-up changes required for this slice (MVP 1 null-compiler is complete; extraction brain, `--sensitivity`/`--batch` flags, multi-concept reconciliation deferred to MVP 2)

**For tracked follow-ups** (optional, non-blocking, no issue/PR assigned yet):
1. Decompose `ingest` CLI body (currently ~150 lines inline)
2. Deduplicate `_reject_newline` utility
3. Refine bundle.create docstring to fully describe write_atomic alongside write_exclusive
4. Add OKF literals documentation (follow-up to PR #12's okf-literals review comment)

## Risks & Mitigations

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| Phase B partial-result confusion | Low | Documented in design D5 "Known limit", spec scenarios, and docs/cli.md; `git status` is the detection/recovery mechanism | Verified |
| `write_exclusive`/`write_atomic` atomicity weakened | Low | Separate primitives, distinct contracts, full test coverage including interrupted-write scenarios | Verified |
| Path containment bypass (traversal injection) | Low | Destinations derived from `Path(src).name` + slug only, not raw argument; RED test + static inspection confirm | Verified |
| Config reading fails silently | Low | `FileNotFoundError` caught by existing `except (OSError, ValueError)` in ingest Phase A; now covered by dedicated test | Verified |
| Provenance/resource field mismatch | Low | Both use workspace-relative `raw/<name>` path; consistent, stable, matches good-life-demo examples | Verified |
| YAML quote-style drift on append | Low | Frontmatter preserved verbatim (split byte-for-byte, body-only parse-render), no round-trip serialization | Verified |

## Deferred/Out-of-Scope Items

**Explicitly deferred to later changes**:
- LLM backend implementation (Ollama httpx, `LLMBackend` Protocol)
- Single-concept extraction + schema validation + bounded retry
- `--sensitivity` and `--batch` flags (syntax ready, feature flag deferred)
- Model-quality evaluation and evals harness
- Multi-concept reconciliation (sensitivity high-water-mark, cross-concept links)
- `openkos lint` command (orphan-page detection, freshness checks)
- Dedup `_reject_newline` utility into a shared module
- Decompose `ingest` CLI body

**Accepted residual limitations**:
- Phase B is non-transactional: a failure partway through Phase B leaves a detectable orphan (e.g. concept written but index/log not yet updated). Recovery is via git, not automatic rollback.
- No `lint` command exists yet to detect uncatalogued concepts; detectability relies on `git status` today. (Planned lint capability referenced in docs/cli.md but not implemented in this slice.)

## Traceability

This archive report records the final state of the `add-ingest-command` change from proposal through implementation, verification, and archival. The change has been:
- Fully specified (9 requirements, 18 scenarios at archive time — corrected count; canonical spec later completed to 22 scenarios, see "Post-archive completion" in Spec Merge Summary — `ingestion` capability spec)
- Fully designed (5 architecture decisions D1-D5, D5 ratified as non-transactional/git-recovery after retreat from attempted rollback)
- Fully implemented (two stacked PRs, 6 source modules + tests + docs + manifest, 166 tests, 100% coverage)
- Fully verified (all 19 tasks verified complete, all spec scenarios passing tests, all critical/warning findings closed, 100% line/branch coverage, ruff/mypy/build all green)
- Fully delivered (PRs #11 and #12 merged to main, issue #10 closed, review approvals obtained)

The SDD cycle is CLOSED. The change is archived and ready for the next change.

**Archive Date**: 2026-07-18 (ISO format)
**Repository Head**: de5eae3 (main)
**Specification**: `openspec/specs/ingestion/spec.md` (canonical, promoted from delta spec)
**Verification Date**: 2026-07-17 (verify-closure pass)
**Archival Status**: COMPLETE
**Artifact Observation IDs**: proposal #873, spec #875, design #876, tasks #878, verify-report #890
