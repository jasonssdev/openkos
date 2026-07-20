# Archive Report: entity-resolution-adjudication

**Change**: entity-resolution-adjudication (MVP-2 slice 2 of a 2-3 change mini-chain) | **Archived**: 2026-07-20 | **Status**: Complete | **Repository**: openkos (main 83df7b3, #80) | **Mode**: hybrid

This archive report closes the SDD cycle for the `entity-resolution-adjudication` change: a read-only, config-free precision layer over slice 1's `find_candidates` output. It prompts an injected `LLMBackend` to adjudicate each `CandidateGroup` (title + full body per member) into a `SAME`/`DIFFERENT`/`UNCERTAIN` verdict with confidence and rationale, surfaced through a read-only `adjudicate` CLI verb. It never merges, writes, or decides — verdicts are ephemeral, for human review only.

## Change Summary

**Purpose**: Slice 1 shipped high-recall, deterministic candidate generation that intentionally over-produces LOW-tier false positives (e.g. "cats vs carts"). This slice adds the precision layer that answers that documented gap: real content signal (not just string similarity) adjudicates each candidate group, proposing rather than deciding — the engine produces a review queue, never an auto-merge.

**Scope shipped**:
- New `src/openkos/resolution/adjudication.py`: `Verdict` enum (`SAME`/`DIFFERENT`/`UNCERTAIN`), frozen ephemeral `AdjudicatedCandidate(candidate, verdict, confidence: float, rationale: str)`, and the config-free leaf `adjudicate_candidates(candidates, *, bundle_dir, llm)`.
- Per-group LLM adjudication (Approach A: one `chat()` call per group with readable content), mirroring `extraction/concept.py`'s 2-message prompt / fail-closed JSON parse / propagate-`OllamaError` pattern.
- Read-only member loading (title + full body) via `okf.load_frontmatter`, mirroring `retrieval/answer.py:_assemble_context`'s guarded re-read; a group with zero readable members short-circuits to `UNCERTAIN`/`confidence=0.0`/`"no readable member content"` without any `llm.chat` call.
- Fail-closed reply parsing: JSON `{verdict, confidence, rationale}`; verdict case-insensitive with unknown → `UNCERTAIN`; confidence clamped `[0.0, 1.0]`; malformed/unparseable reply degrades to `UNCERTAIN`/`0.0` with a rationale noting the failure — the group is never dropped.
- New read-only CLI verb `adjudicate` in `src/openkos/cli/main.py`, wired exactly like `query` (`require_workspace` → `read_config` → `OllamaClient(model=cfg.model)` → `find_candidates` → `adjudicate_candidates`); grouped verdict/confidence/rationale render; display-only `--same-only` filter (library always receives every group); 3-tier `OllamaError` degrade catch (`OllamaUnavailable` → `OllamaModelNotFound` → generic), exit 1, zero writes; no `--auto`; not named `resolve`/`merge`.
- `tests/unit/resolution/test_layering.py` extended with a positive, non-vacuous assertion that `resolution` MAY import `openkos.llm` (still forbids `bundle`/`state`/`graph`).
- Integration proof: `adjudicate` run read-only over `examples/good-life-demo/bundle` with a monkeypatched fake `OllamaClient`, asserting byte+mtime-identical before/after and exit 0.

**Review-caught bug fixed before verify**: the initial `confidence` clamp used naive `max(0.0, min(1.0, value))`, which fails open on non-finite input — `float("nan")` and `float("inf")` both pass through `min`/`max` comparisons unpredictably and can resolve to `1.0` rather than the safe `0.0` degrade. Fixed by gating the clamp on `math.isfinite(value)` first; non-finite confidence now fails closed to `0.0`, matching the same fail-closed contract as an unparseable reply. Locked by `test_nan_confidence_fails_closed_to_zero` and `test_infinity_confidence_fails_closed_to_zero`, both asserting `confidence == 0.0` (not the naive-clamp bug's `1.0`). This is now Requirement 4 (Fail-Closed Reply Parsing And Validation) in the living spec, including the non-finite case.

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-20-entity-resolution-adjudication/proposal.md` | Moved from change folder; Engram `sdd/entity-resolution-adjudication/proposal` (id 1171) |
| Specification (delta) | `archive/2026-07-20-entity-resolution-adjudication/specs/entity-resolution-adjudication/spec.md` | Copied verbatim to main spec tree at `openspec/specs/entity-resolution-adjudication/spec.md` (new domain); Engram `sdd/entity-resolution-adjudication/spec` (id 1172) |
| Design | `archive/2026-07-20-entity-resolution-adjudication/design.md` | Moved from change folder; Engram `sdd/entity-resolution-adjudication/design` (id 1173) |
| Tasks | `archive/2026-07-20-entity-resolution-adjudication/tasks.md` | 22/22 checkboxes `[x]` across 6 phases (confirmed by direct filesystem read at archive time); Engram `sdd/entity-resolution-adjudication/tasks` (id 1174) |
| Apply progress | (recorded below) | Engram `sdd/entity-resolution-adjudication/apply-progress` (id 1175) |
| Verification Report | (recorded below) | Engram `sdd/entity-resolution-adjudication/verify-report` (id 1176) |

No task-completion gate issue: both the Engram `tasks` observation (id 1174) and the filesystem `openspec/changes/entity-resolution-adjudication/tasks.md` agree — all 22 boxes across Phases 1-6 are `[x]`, confirmed by direct file read at archive time, not merely trusted from the Engram preview.

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **CREATED** | `entity-resolution-adjudication` | New spec domain created at `openspec/specs/entity-resolution-adjudication/spec.md` (no prior main spec existed for this capability) |
| Requirement count | 10 requirements + 13 scenarios | Full specification of the read-only LLM adjudication contract: per-group order-preserving adjudication, `Verdict`/`AdjudicatedCandidate` shape, read-only full-body member loading with per-member/all-unreadable degrade, fail-closed reply parsing (including non-finite confidence), keep-all-verdicts, `OllamaError` propagation from the leaf, the read-only `adjudicate` CLI verb, `--same-only` display-only filter, 3-tier degrade-on-no-model, and determinism |
| Source | Delta spec from change folder | `openspec/changes/entity-resolution-adjudication/specs/entity-resolution-adjudication/spec.md` → `openspec/specs/entity-resolution-adjudication/spec.md` |
| Merge mode | Direct copy (no pre-existing spec; delta already authored in living-spec shape — no ADDED/MODIFIED delta headers to convert) | First spec for this domain; delta becomes canonical main spec verbatim |

No requirements or scenarios were invented, dropped, or altered during merge — the delta spec (Engram id 1172) was already structured as a standalone capability spec (Purpose / Non-Goals / Requirements+Scenarios) and is preserved faithfully in `openspec/specs/entity-resolution-adjudication/spec.md`, matching the structure of `openspec/specs/entity-resolution/spec.md` and `openspec/specs/graph-projection/spec.md`.

## Verification Status

**Final Verdict** (Engram `sdd/entity-resolution-adjudication/verify-report`, id 1176): **PASS WITH WARNINGS** — 0 CRITICAL, 1 WARNING (non-blocking, documentation-accuracy only), 0 SUGGESTION.

**Evidence Summary** (independently reproduced at verify time, on branch `feat/era-02-cli-verb`, not merely copied from apply-progress):
- `uv run pytest` → 760 passed, exit 0
- `uv run ruff check .` → "All checks passed!", exit 0
- `uv run ruff format --check .` → 73 files already formatted, exit 0
- `uv run mypy .` → "Success: no issues found in 73 source files" (project `pyproject.toml` sets `strict = true`), exit 0
- `uv run pytest --cov=openkos.resolution --cov-branch --cov-report=term-missing tests/unit/resolution/` → 89 passed; **100.00% branch coverage** on `openkos.resolution` (218 stmts / 58 branches, 0 missed; `adjudication.py` itself 108 stmts / 24 branches, 0 missed — gate ≥ 90%, comfortably exceeded)
- `uv run pytest tests/unit/cli/test_adjudicate.py -v` → 15 passed, exit 0
- `uv run pytest tests/unit/resolution/test_layering.py -v` → 6 passed, exit 0

**All 10 requirements verified PASS** against `openspec/specs/entity-resolution-adjudication/spec.md`:
1. Per-Group LLM Adjudication Preserving Order — PASS.
2. `Verdict`/`AdjudicatedCandidate` shape — PASS.
3. Read-only full-body member loading, degrade per member, all-unreadable short-circuit without an `llm.chat` call — PASS.
4. Fail-closed reply parsing and validation, including the review-caught non-finite-confidence fix (`math.isfinite` gate) — PASS.
5. All three verdicts preserved, never auto-dropped — PASS.
6. `OllamaError`-family propagates unswallowed from the leaf — PASS.
7. Read-only `adjudicate` CLI verb (zero writes, no confirm gate, not named `resolve`/`merge`) — PASS.
8. `--same-only` is a display-only filter (library always receives every group) — PASS.
9. Degrade-on-no-model mirrors `query`'s 3-tier catch order — PASS.
10. Deterministic given a fixed backend — PASS.
11. Layering: `resolution` may import `openkos.llm` (positive, non-vacuous assertion), still forbids `bundle`/`state`/`graph` — PASS.

**Issues at final close**:
- 0 CRITICAL.
- 1 WARNING (non-blocking): the apply-progress artifact self-reported "21 tests" for `tests/unit/cli/test_adjudicate.py`; independent inspection at verify time found 15 actual test functions. This is a reporting-accuracy discrepancy in the apply phase's self-report only — every spec scenario for the CLI verb (workspace gate, config gate, no-candidates, read-only, render, `--same-only` ×2, model wiring, 3-tier degrade ×4, no-auto, command-name, demo integration) is independently confirmed covered by the 15 tests that do exist. Does not block archive.
- 0 SUGGESTION.

## Delivery History

Delivered as a **2-unit Feature Branch Chain** (tasks forecast, Engram id 1174: ~650-750 estimated changed lines against the 400-line review budget — `ask-on-risk` fired, High risk, chained PRs recommended, resolved to `feature-branch-chain`).

1. **Sub-PR #78** — Unit 1 (Phases 1-4): `resolution` library core — `adjudication.py` (`Verdict`, `AdjudicatedCandidate`, `adjudicate_candidates`, prompt/parse/member-load helpers) and its full RED-first unit test suite (member loading, prompt construction, fail-closed parse, order-preserving orchestration, `OllamaError` propagation, determinism). Included a post-review fix: non-finite (`NaN`/`Infinity`) confidence values were failing open under a naive clamp; gated on `math.isfinite` to fail closed to `0.0`, with parity tests added.
2. **Sub-PR #79** — Unit 2 (Phases 5-6): `adjudicate` CLI verb, the layering-guard positive-assertion extension, and the read-only integration proof over the good-life-demo bundle.

Both sub-PRs landed to `main` via **PR #80** (the tracker/integration PR), merged as commit **83df7b3**.

**Repository State**: `main` @ `83df7b3`.

## Deferred / Non-Goals (explicit, unchanged from proposal through archive)

The following remain explicitly out of scope for `entity-resolution-adjudication` and are deferred to the next slice of the mini-chain or beyond, per the proposal's Out of Scope section and the spec's Non-Goals:

- Destructive `merge`/`resolve` verb, merge record, tombstone, sensitivity recompute, un-merge — **slice 3 of this mini-chain**. Confirmed untouched by diff-stat inspection at verify time (`git diff main..feat/era-01-library` and `git diff feat/era-01-library..feat/era-02-cli-verb` show only additive `adjudication.py`/`main.py`/test files touched; no `resolve`/`merge` command exists).
- Embedding/vector-based candidate generation — unchanged non-goal, no embeddings import anywhere in `resolution/`.
- Any change to slice-1 `find_candidates`/thresholds — `resolution/candidates.py` untouched by either unit.
- Any bundle/state write or persisted OKF type for the adjudication result — no bundle/state write path found in either unit; `AdjudicatedCandidate` lives only in `resolution/adjudication.py`, ephemeral.
- Batching of multiple groups into one LLM call — `adjudicate_candidates` issues exactly one `llm.chat` call per group with readable content, confirmed by source inspection.
- Content truncation/summarization of member bodies — full body used with no truncation logic in `_build_messages`/`_load_members`.

## Risks & Limitations Recorded

| Risk | Likelihood | Status |
|---|---|---|
| Prompt reliability on small local models | Med | Mitigated by closed-vocabulary rubric + fail-closed validation, mirroring `concept.py`'s pattern; real-Ollama behavior remains a follow-up validation item, not blocking (unit tests fully cover the deterministic fake-backend contract) |
| Latency: N per-group calls on large LOW-tier lists | Med | Accepted; per-group degrade limits blast radius of one slow/failing call; batching remains a documented, deferred follow-up |
| Full-body context-window pressure | Low | `DEFAULT_TIMEOUT=120s` absorbs slow calls; truncation deferred as a tightening, not required for this slice |
| Non-finite confidence fail-open bug (NaN/Infinity bypassing naive clamp) | Was Med, now Resolved | Fixed pre-verify via `math.isfinite` gate; locked by two dedicated regression tests (`test_nan_confidence_fails_closed_to_zero`, `test_infinity_confidence_fails_closed_to_zero`); now a named spec requirement so it cannot silently regress |
| Apply-progress self-reported test count (21) did not match actual test count (15) in `test_adjudicate.py` | Low | Non-blocking WARNING; independent spec-scenario coverage confirmed complete regardless of the count discrepancy; recommend correcting future apply-progress self-reports to count from the actual test file rather than an estimate |

## Archival Actions Completed

**Filesystem**:
- [x] Living spec created at `openspec/specs/entity-resolution-adjudication/spec.md` (new capability domain; direct copy from the delta spec, format matched against `openspec/specs/entity-resolution/spec.md` and `openspec/specs/graph-projection/spec.md`)
- [x] Change artifacts (proposal, design, tasks, specs) written to `openspec/changes/archive/2026-07-20-entity-resolution-adjudication/`, byte-identical to the pre-archive change-folder originals
- [x] This archive report written to `openspec/changes/archive/2026-07-20-entity-resolution-adjudication/archive-report.md`

**Engram**:
- [x] Archive report saved with topic key `sdd/entity-resolution-adjudication/archive-report`
- [x] All artifact observation IDs recorded above for traceability (proposal 1171, spec 1172, design 1173, tasks 1174, apply-progress 1175, verify-report 1176)

## Known Limitation of This Archival Pass

The executor performing this archive had access only to `Read`/`Write`/`Edit`/`Glob` and Engram tools — no shell/`git` tool was available in this session (same constraint as the preceding `entity-resolution-candidates` and `graph-projection` archives). Every artifact was therefore **written as a byte-identical copy** to the archive and living-spec locations rather than moved with `git mv`, and the original `openspec/changes/entity-resolution-adjudication/` directory could **not** be deleted by this executor. The orchestrator is expected to run the equivalent of `git rm -r openspec/changes/entity-resolution-adjudication/` after confirming the archived copies are byte-identical to the originals, so the rename is preserved as a move in history and the active changes directory no longer lists this change.

## Next Steps

**For the project**:
- `entity-resolution-adjudication` (slice 2) unblocks slice 3 of the mini-chain: destructive, confirm-gated, reversible merge (tombstone, un-merge, sensitivity recompute) over the review queue this slice surfaces. Slice 3 will get its own explicit human checkpoint before it is built, per the original proposal's mini-chain commitment.
- The deferred non-goals above (batching, content truncation) remain open follow-up candidates for future tightening, separate from the mini-chain's core scope.

**For archive verification**:
- No CRITICAL issues remain; the single non-blocking WARNING (apply-progress test-count self-report mismatch) is a documentation-accuracy note only and does not affect spec compliance.
- The only outstanding action is the mechanical `git rm`/deletion of the original change folder noted above, owned by the orchestrator (no shell/git tool available to this executor).

## Traceability

This archive report records the final state of the `entity-resolution-adjudication` change from proposal through verification and archival. The change has been:
- Fully specified (10 requirements, 13 scenarios)
- Fully designed (6 architecture decisions, ADR gate correctly did not fire — all decisions cheaply reversible)
- Fully implemented (2-unit feature-branch-chain, sub-PRs #78-#79, landed via #80), including a review-caught non-finite-confidence fail-open bug found and fixed pre-verify
- Fully verified (100% branch coverage on `openkos.resolution`, mypy strict clean, 760/760 tests passing, 0 CRITICAL / 1 non-blocking WARNING / 0 SUGGESTION)
- Fully delivered (main @ 83df7b3)

The SDD cycle is CLOSED for slice 2. The change is archived and ready for slice 3 (confirm-gated, reversible destructive merge) to begin, which will get its own explicit human checkpoint before it is built, pending the mechanical folder-removal follow-up noted above.

**Archive Date**: 2026-07-20 (ISO format)
**Repository Head**: 83df7b3 (main)
**Archival Status**: COMPLETE (content), PENDING (original-folder removal — requires shell/git access not available to this executor)
