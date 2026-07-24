# Archive Report: adjudicate-json (#137, Slice 2a)

**Change**: adjudicate-json
**Issue**: #137 (Slice 2a of multi-slice arc; #137 remains OPEN for future slices)
**PR**: #161 (merged to main)
**Verification**: PASS (0 CRITICAL, 0 WARNING, 1 non-blocking SUGGESTION)
**Date**: 2026-07-24

## What Shipped

**Capability Modified**: `entity-resolution-adjudication`
- Added machine-readable `--json` output mode to the `adjudicate` CLI verb
- Added pure `_adjudication_payload` builder (testable, reusable helper)
- Locked schema: `member_ids`, `okf_type`, `tier`, `verdict`, `rationale` (confidence intentionally omitted per #138)
- First `--json` convention in the codebase; establishes precedent for future commands

**Changes**:
- `src/openkos/cli/main.py`: added `import json` (stdlib), `--json` flag via Typer, `_adjudication_payload` helper, short-circuit branch, docstring update (~51 lines net)
- `tests/unit/cli/test_adjudicate.py`: 9 new unit/integration tests covering payload shape, enum rendering, composability, empty state, error paths, byte-parity (~311 lines added, 0 deleted — proof of non-regression)

## Scope

### In Scope
- Additive `--json` flag; emits valid JSON to stdout when set
- Suppresses all human output when flag is active
- Filters via `--same-only` inside the JSON array
- Empty state emits `[]` (valid JSON) instead of prose

### Out of Scope (Future Slices)
- Interactive apply (`--apply`, per-pair confirm) — Slice 2b
- Guarded/unattended batch apply (`--apply-same`) — deferred pending #138
- Any destructive action; no changes to `adjudicate_candidates`, `merge`, ledger

**Issue #137 Status**: OPEN — Slice 2a complete; interactive and batch slices remain for future planning

## Locked Conventions & Product Decisions

1. **Confidence Exclusion** — Consistent with #138. The local model returns uncalibrated flat values. Exposing confidence as JSON would revive misleading precision for machine consumers. Kept in the dataclass for future thresholding, not exposed in output.

2. **Schema — One Object Per Group, Exact Fields**:
   ```json
   [
     {
       "member_ids": ["concept-a", "concept-b"],
       "okf_type": "person",
       "tier": "HIGH",
       "verdict": "SAME",
       "rationale": "Same individual; identical canonical name and role."
     }
   ]
   ```
   - No `survivor`/`absorbed` field (no heuristic exists; consumer decides)
   - `tier` and `verdict` rendered as UPPERCASE strings (`group.tier.name`, `result.verdict.value.upper()`)
   - `member_ids` rendered as a sorted list (tuple → list conversion)

3. **`--same-only` Composability** — `--json` emits the FULL array by default; consumers filter on `verdict`. If `--same-only` is also passed, filters emitted array to SAME-only (single filter predicate in `_adjudication_payload`).

4. **Empty State** — Emit `[]` (valid empty array), never "No candidates found." or "No SAME-verdict candidates to display."

5. **Formatting** — Pretty-printed with `indent=2`; deterministic results order; identical input yields byte-identical stdout.

6. **Error Paths** — Ollama-unavailable / model-not-found / generic handlers unchanged — stderr + exit 1, never on the JSON stdout.

## Verification Summary

**Suite**: 2002 tests passed (full project test run; includes all pre-existing tests)
**Code Quality**: All passing
- `ruff check .` — All checks passed
- `ruff format --check .` — 132 files already formatted
- `mypy .` — No issues found in 132 source files

**Spec Conformance**: 7/7 major requirements verified:
- ✅ Exact field set (member_ids, okf_type, tier, verdict, rationale)
- ✅ Confidence omitted (not in any JSON object)
- ✅ Tier rendered as UPPERCASE via `.name` (not `.value`; critical trap avoided)
- ✅ Verdict rendered as UPPERCASE via `.value.upper()`
- ✅ `--json` suppresses all human output (tally, legend, detail, Next hint)
- ✅ `--json --same-only` filters array to SAME verdicts only
- ✅ Empty state emits `[]` (not prose)
- ✅ Error paths write to stderr, exit 1, stdout has no JSON
- ✅ Non-`--json` output byte-identical to pre-change behavior (0 deletions in test file; 36 pre-existing tests all passing)

**TDD Compliance**: Strict TDD followed throughout
- All 15 task sub-tasks marked complete and verified against real test execution
- RED → GREEN cycle confirmed for all 7 task groups
- No tautologies or ghost assertions found
- Determinism proven by source inspection (single `indent=2` literal, no nondeterministic collection usage)

**Non-Blocking Suggestion**:
- Add an explicit test asserting multi-line/indented JSON stdout shape (e.g., counting `\n` or checking output span) and/or a literal "invoke twice, assert byte-identical stdout" test, to close the narrow gap between the "Deterministic, Pretty-Printed JSON" scenarios and directly-observable test evidence. (Currently satisfied by source inspection of the single `indent=2` call site plus the order-preservation test.)
  - Risk if not addressed: Low (precedent is already set and locked; future developers will reference this implementation)
  - Recommendation: Strengthen future `--json` convention tests with explicit indent/determinism cases as a precedent-strengthening effort

## Target Spec Updated

**Main Spec**: `openspec/specs/entity-resolution-adjudication/spec.md`
- Added 7 new requirements (Machine-Readable `--json` Output Mode, `--json` Fully Suppresses Human Output, `--same-only` Composes With `--json`, Empty State Emits Valid Empty Array Under `--json`, Deterministic Pretty-Printed JSON, Error Paths Unaffected By `--json`, Non-JSON Output Stays Byte-Identical)
- All pre-existing requirements preserved (from #139 and earlier work)
- Consolidated, no duplicates

## Artifacts Reviewed

- **proposal.md**: Scope clearly bounded; dependencies documented (none); rollback plan simple (single revert commit)
- **design.md**: Architecture decisions locked (payload builder pattern, branch placement, enum rendering); all attribute paths source-verified
- **tasks.md**: 7 task groups, 15 sub-tasks, all marked complete ([x]); TDD evidence provided; review workload forecast confirmed (Low risk, <800 lines; single PR)
- **verify-report.md**: Full verification run (2002 tests, ruff/format/mypy clean); spec conformance matrix complete; TDD compliance verified; 0 CRITICAL, 0 WARNING, 1 SUGGESTION

## Archive Status

**Status**: PASS — ready for closure
**Change Folder Destination**: (Orchestrator will move to `openspec/changes/archive/2026-07-24-adjudicate-json/`)

**Closure Notes**:
- Feature is non-destructive; existing `adjudicate` behavior unchanged without `--json` flag
- Establishes first `--json` convention in codebase; future commands should reference this pattern
- Confidence field exclusion decision (#138) is locked and should guide all similar output-mode decisions
- Interactive and batch slices (#137 future work) will build on this schema and pattern

---

**Archived By**: SDD Archive Phase
**Artifact Store Mode**: HYBRID (filesystem + Engram)
**Observation IDs**: See Engram topic `sdd/adjudicate-json/archive-report`
