# Archive Report: set-sensitivity-verb

**Date Archived**: 2026-07-28
**Status**: ARCHIVED
**GitHub Issue**: #185 (P1, problem 1 only)

| PR | Merged as | Scope |
|---|---|---|
| #220 | `f982609` | `okf.sensitivity_direction` public helper + unit tests |
| #221 | `cac50cf` | `set_sensitivity_cmd` CLI verb + CLI/autocommit tests (611 lines, size:exception) |
| #222 | `1b9d340` | The `prompt_will_run` / `isatty` gate fix + 2 tests (review-driven) |
| #223 | `1a44582` | `docs/cli.md` section, ADR-0008, comment correction (review-driven) |
| #224 | `16d22b0` | The unstripped-idempotence boundary test (review-driven) |

## Summary

Issue #185, problem 1 only: `openkos set-sensitivity <concept-id> <level>` sets that one concept's `sensitivity` field directly, with validation, preview, standard confirm gate, log entry, and auto-commit. **The upstream premise was false.** Issue #185 claimed sensitivity is inherited by derived objects via high-water-mark and that manual edits leave them stale. At `cli/main.py:1660` and `:1674`, ingest stamps each derived object independently with `cfg.default_sensitivity` — siblings, not parent and child. There is no propagation edge. The real gap is #219 (source-to-derived on merge, out of scope here).

This change ships only the write verb, making no propagation claim.

## Implementation Scope

Five PRs merged to `main`:
- **#220** (`f982609`): `okf.sensitivity_direction(current, target) -> Literal["raise", "same", "lower"]` public helper in `model/okf.py`, private `_rank` untouched. Classifies direction for fail-closed handling of missing/blank/malformed current values.
- **#221** (`cac50cf`): `set_sensitivity_cmd` CLI verb in `src/openkos/cli/main.py` (placed between `relate` and `set_volatility_cmd`), mirroring `relate`'s Phase A/B shape. Implements exact-equality idempotence, downgrade gate (at Phase A step 7, before any preview), level validation, concept resolution, log entry, and auto-commit. 611 production lines; `size:exception` accepted via native review.
- **#222** (`1b9d340`): Added `isatty()` check to the confirm-gate precedence, fixing a leakage of piped downgrades through the Phase A downgrade gate. Found by four-lens review of #221. Adds 2 tests to `test_set_sensitivity.py`.
- **#223** (`1a44582`): Added `### openkos set-sensitivity <id> <level>` section to `docs/cli.md` with honesty statement ("no sibling or derived object touched"), ADR-0008 (`docs/adr/0008-human-sensitivity-override.md`) created with status `Proposed`, index row added to `docs/adr/README.md`. Fixed one comment error in PR #221's verb docstring.
- **#224** (`16d22b0`): Added `test_padded_current_equal_to_target_is_not_a_no_op` to `test_set_sensitivity.py`, pinning the exact-equality idempotence boundary (e.g., `"public "` with trailing whitespace is NOT a no-op). Found by four-lens review of #223.

**Review findings**: Five native review lineages, all reaching `approved`:
- `review-c65c0275297f07ef` (PR1, medium, one lens): no findings
- `review-fd2740fffb62e133` (PR2, high, four lenses): four WARNING-class findings — all addressed as load-bearing test coverage
- `review-4f5109b24978a6a7` (PR2 fix, high, four lenses): no findings  
- `review-b53d8ec234f0c9f1` (PR3, high, four lenses): one CRITICAL (conflicting documentation), corrected under bounded correction transaction and validated
- `review-ec4fdde692985f00` (PR4, medium, one lens): no findings

**Verification**: PASS, 0 blockers, 10/10 requirements, 22/22 scenarios (20 fully direct, 2 structural/indirect), 2383 tests passing, 97.52% branch coverage, ruff and mypy clean.

## The Story This Cycle Actually Has

### Scope Narrowing: #185 Premise Was False

Issue #185 claimed sensitivity is inherited via high-water-mark and that manual edits leave derived concepts stale. **That is false.** At `cli/main.py:1660` and `:1674`, ingest stamps Source and derived objects each with `cfg.default_sensitivity` independently — siblings fed one constant, not parent and child. There is no edge to re-propagate. Only the verb ships; the real gap (source-to-derived propagation) is #219.

### Five PRs, Not Three

The 3-PR forecast (okf helper, CLI verb, specs/docs/ADR) became 5 because two of the high-tier reviews found real bugs and design drift:

- **PR #222** (four-lens review of #221): The Phase A downgrade gate omitted the `isatty()` check, so a script using `openkos set-sensitivity <id> public --auto | tee log` (piped) would leak past the unattended downgrade gate and fail at the confirm-gate refusal, printing the preview first. The refusal message already said "confirm prompt is disabled (--auto, or config review: false)" — but the gate should refuse *before* any preview in unattended mode. Fixed, test added.

- **PR #224** (four-lens review of #223): The idempotence short-circuit (`current == level`) uses exact equality. A current value with trailing whitespace (`"public "`) is not exactly equal to `"public"` and should not short-circuit — it should rank fail-closed and potentially require `--allow-downgrade`. Test added to pin the boundary.

### A Correction Was Scope-Refused, Correctly

Review of PR #223 (`docs/cli.md` section, ADR-0008, etc.) raised CRITICAL: The new `### openkos set-sensitivity` section claimed "A confirmed write is atomic" but the verb performs two independent writes (concept file, then log) plus a separate commit — atomicity is NOT guaranteed if the verb crashes between writes. The code comment from PR #221 already contradicted this. The bounded correction tool could not touch the test file (outside genesis scope of that candidate) to add a test. Result: The doc was corrected inline, the test was added as PR #224 separately. Record this as the contract working, not as a failure.

### Review Tier and File

Candidates touching `src/openkos/cli/main.py` classified `high` on a `process_boundary / shell_process` signal from unrelated git subprocesses elsewhere in that file. The test-only candidate (PR #224) classified `medium` with one lens — no such signal. Correct behavior per native tier discovery.

### Stale Artifacts in Cycle

- **`design.md`** Interfaces table: Literal error-ladder strings do not match shipped code. PR #222 expanded the downgrade-refusal message to mention "non-interactive stdin" beyond the design.md pinning. No spec scenario enforces exact literal strings, but design.md drifted.
- **`tasks.md`**: Claims ADR-0008 "already shipped" when it only existed on an unmerged design branch until PR #223. Per AGENTS.md convention, ADR status is flipped to Accepted at archive time, not before.

Both are recorded as known drift, not silently corrected.

## ADR-0008 Status Flip at Archive

ADR-0008 (`docs/adr/0008-human-sensitivity-override.md`) was created in design phase with status `Proposed`. Per AGENTS.md's append-only convention, status flip to `Accepted` happens only at archive time. **This archive executor has flipped both frontmatter `status:` and the body line to `Accepted`, and updated the index row in `docs/adr/README.md`.**

The ADR scopes ADR-0003's "never less" to machine-chosen combine operations and fail-closed dirty ranking. Human explicit assignment (`openkos set-sensitivity <id> <level>`) may lower when prompted interactively; unattended downgrades (under `--auto` or config `review: false`) require `--allow-downgrade`. The flag is load-bearing and documented in the spec.

## Specs Merged

Two delta specs applied at archive:

### 1. New: `sensitivity-config/spec.md` → `openspec/specs/sensitivity-config/spec.md`

The write layer for one concept's `sensitivity`, modeled on `volatility-config` (the read/write pair). Ten requirements, 17 scenarios, all 10/10 requirements and 22/22 scenarios verified PASS.

### 2. Delta: `workspace-autocommit/spec.md`

Applied to existing `openspec/specs/workspace-autocommit/spec.md`:
- Enumeration: Added `set-volatility` (pre-existing ship, was absent) and `set-sensitivity` (new)
- Paths clause: Rewrote universal "plus `bundle/index.md` and `bundle/log.md`" to "verb's own Phase-B-written paths, including `index.md`/`log.md` where the verb writes them" — required because `set-sensitivity` commits log only (no index), and `set-volatility` commits `openkos.yaml` only (no log or index)
- New scenarios: Added `set-volatility` and `set-sensitivity` scenarios detailing exact commit structure
- Delta accuracy verified against shipped commit behavior: `set-sensitivity` commits `[bundle/{id}.md, bundle/log.md]` only; `set-volatility`'s pre-existing `openkos.yaml` commit is unchanged

**Bounded edits only**: enumeration, paths clause, message table, two new scenarios. No wider spec reconciliation.

## Deliverables

Archived to `openspec/changes/archive/2026-07-28-set-sensitivity-verb/`:

- `proposal.md` — Intent, scope narrowing, approach, decision rationale, risks, rollback plan, success criteria
- `explore.md` — Exploration notes and recommendations
- `design.md` — Architecture decisions (direction helper as policy export, exact-equality idempotence, flag name, preview words, gate placement), data flow, interfaces, testing strategy, threat matrix
- `tasks.md` — Phase 1–3 tasks (23 implementation, 4 Phase 3 spec/doc); all marked complete
- `verify-report.md` — Verification findings after 5 PRs merged; PASS verdict; TDD compliance; 0 CRITICAL, 2 WARNING, 6 SUGGESTION (pre-recorded)
- `archive-report.md` — this report
- `specs/sensitivity-config/spec.md` — new capability spec (now merged into `openspec/specs/sensitivity-config/spec.md`)
- `specs/workspace-autocommit/spec.md` — delta spec (now merged into `openspec/specs/workspace-autocommit/spec.md`)

## Scope Boundaries (Confirmed)

Out of scope, not implemented:

- Source-to-derived propagation (#219)
- Any changes to `sensitivity.py`, `sensitivity-aware-llm/spec.md`, or LLM gates
- Bulk / glob / recursive application, `--dry-run`, read-only `get-sensitivity`
- Per-object floor from `cfg.default_sensitivity`
- Generic set-frontmatter-field primitive (two call sites, different shapes; premature)
- Wider reconciliation of `workspace-autocommit/spec.md` beyond four bounded edits

All confirmed via diff and review evidence.

## Open Follow-Ups (Not This Change's Scope)

Issues #216, #217, #218 (pre-existing), #219 (source-to-derived propagation). From this cycle's reviews (not new bugs, documented pre-known gaps):

1. Preview direction wording ("raising") has no dedicated string assertion.
2. The exact `log.md` entry text format has no dedicated assertion.
3. "Raising is never gated" has no dedicated test.
4. Phase-B else branch (refusal for raise/normalization on non-interactive stdin) untested for `set-sensitivity` — pre-existing pattern shared with `relate`.
5. Byte-Preserving and Scope scenarios use indirect/structural evidence rather than dedicated fixture with sibling/derived concepts present.

None block archive.

## Verification Summary

**Verdict**: PASS
- Requirements: 10/10 compliant
- Scenarios: 22/22 compliant (20 direct, 2 structural/indirect)
- Tests: 2383 passed, 0 failed
- Coverage: 97.52% (gate 90%)
- Build: ruff/mypy clean
- Blockers: 0 CRITICAL
- Warnings: 2 non-blocking (design.md literal strings stale, 1-test count forecast mismatch)
- Suggestions: 6 pre-acknowledged open follow-ups

The core security decision (downgrade gating wherever confirm prompt doesn't run, fail-closed ranking of dirty values) is proven end-to-end. No propagation shipped. Honesty statements on all three surfaces. Ready to close.

## SDD Cycle Complete

The change has been fully planned, designed, implemented across five PRs (code + three spec/doc slices + two review-driven corrections), verified, and archived. All Phase 1–2 implementation tasks complete; Phase 3 spec/doc/ADR artifacts complete and merged. ADR-0008 status flipped to Accepted at archive time per convention. Ready for the next change.
