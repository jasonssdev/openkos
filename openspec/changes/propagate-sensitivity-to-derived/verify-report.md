# Verification Report: propagate-sensitivity-to-derived (PR 1 of 2)

**Scope of this run**: `fix/ingest-inherits-source-sensitivity` branch only (2 commits off `main`, not pushed) — Phase 1 tasks 1.1-1.8 (real creation-time sensitivity inheritance at ingest). Phase 2 (set-time propagation, ADR-0009, `sensitivity-config` spec delta, tasks 2.1-2.19) is explicitly OUT OF SCOPE for this verification and is NOT reported as a gap.

**Verdict**: **PASS**

## Commits reviewed
- `7021530` fix(ingest): derive stamp from the Source's own resolved sensitivity
- `8a38995` docs(ingestion): back the inheritance requirement with a real read

## Completeness (Phase 1 tasks)
| Task | Status | Evidence |
|---|---|---|
| 1.1 RED | ✅ | `test_derived_object_inherits_source_document_value_not_config` written, proven to fail on `main` |
| 1.2 RED/pin | ✅ | `test_extract_gate_still_reads_workspace_floor` written, proven to be a real regression pin (fails if gate collapsed) |
| 1.3 Retitle baseline | ✅ | `test_derived_object_inherits_source_sensitivity` docstring updated, notes it alone doesn't prove inheritance |
| 1.4 GREEN param split | ✅ | `_stage_derived_objects` split into `workspace_floor`/`stamp_sensitivity`, docstring updated |
| 1.5 GREEN call site | ✅ | `main.py:1683-1690` reads back `source_metadata["sensitivity"]` via `okf.load_frontmatter` |
| 1.6 REFACTOR | ✅ | Single call site confirmed (`main.py:1685`), no stale `sensitivity=` kwarg; ruff/mypy clean |
| 1.7 Spec delta applied | ✅ | `openspec/specs/ingestion/spec.md` restated requirement + 2 scenarios, matches delta file exactly |
| 1.8 Coverage | ✅ | Changed lines (1178-1400, 1653-1695) fully branch-covered; confirmed independently |

All 8 Phase 1 tasks checked `[x]` in `tasks.md` and verified to match code state. No unchecked tasks in scope.

## Independent proof — RED test genuinely distinguishing
Built a disposable git worktree at `main` (pre-fix `src/openkos/cli/main.py`), copied the branch's updated `tests/unit/cli/test_ingest.py` into it, and ran the 3 relevant tests:

```
uv run pytest tests/unit/cli/test_ingest.py -q -k "test_derived_object_inherits_source_document_value_not_config or test_extract_gate_still_reads_workspace_floor or test_derived_object_inherits_source_sensitivity"
→ 1 failed, 2 passed, 102 deselected
FAILED test_derived_object_inherits_source_document_value_not_config
  AssertionError: assert 'public' == 'confidential'
```

Confirms: on unmodified `main`, the derived object is stamped with `cfg.default_sensitivity` (`public`) instead of the forged Source's own value (`confidential`) — a genuine RED, not a coincidental pass. The same 3 tests all pass on the fix branch (`3 passed`).

## Independent proof — Requirement 4 pin is a real regression pin, not a fake RED
Mutated the fix branch's own gate check in place (`blocks_llm_send(workspace_floor)` → `blocks_llm_send(stamp_sensitivity)`, i.e. simulated collapsing the two parameters back together) and re-ran `test_extract_gate_still_reads_workspace_floor`:

```
FAILED test_extract_gate_still_reads_workspace_floor
  assert 'keeping the Source only' in "openkos ingest: embeddings not updated..."
```

The test fails as expected when the parameter split is collapsed — proving it is a legitimate regression pin for `sensitivity-aware-llm` Requirement 4, not a test that would pass regardless of implementation. The apply agent's framing (it was already green pre-fix "by design," as a refactor-preservation/approval test) is legitimate: it is not asserting NEW behavior, it is asserting UNCHANGED behavior across a refactor, and the mutation test above proves it is load-bearing.

Restored the file via `git checkout` immediately after the mutation test; `git status` confirmed clean.

## `source_metadata["sensitivity"]` dict-subscript risk assessment
Investigated whether `okf.build_source_concept` can render frontmatter without a `sensitivity` key, making the direct `source_metadata["sensitivity"]` subscript at `main.py:1684` capable of raising `KeyError` from user input.

Finding: **not reachable**. `build_source_concept` (`src/openkos/model/okf.py:114-126`) unconditionally sets `metadata["sensitivity"] = sensitivity` regardless of the input value (including blank/whitespace strings) — there is no code path that omits the key. Verified empirically:

```python
okf.build_source_concept(..., sensitivity="", ...)  →  load_frontmatter(...) → {'sensitivity': '', ...}
```

The key survives a blank value through the YAML round-trip.

Additionally, the call site builds `concept_content` and reads it back in the same code block (`main.py:1667` then `:1683`), so the dict subscript operates on content this same function just constructed with a known `sensitivity` key — there is no scenario where a different/foreign document reaches this subscript. **No CRITICAL or WARNING here**; this is a SUGGESTION-level note only: `cfg.default_sensitivity` itself has no type/value validation in `config.py` (pre-existing, not introduced by this change), so a config author could set `default_sensitivity: 0` (YAML int) and it would flow through as a non-`str` value — the new `str(source_metadata["sensitivity"])` cast actually makes this slightly more defensive than before, not less.

## Ingestion spec delta correctness
`openspec/specs/ingestion/spec.md`'s "Derived Object Provenance and Sensitivity Inheritance" requirement now states inheritance MUST read the Source's own resolved value, not `cfg.default_sensitivity` — this matches the code exactly. Its two scenarios map 1:1 to the two relevant tests:
- "Provenance and sensitivity inherited from the Source's own value" → `test_derived_object_inherits_source_sensitivity` (baseline, same-value case)
- "Inheritance tracks the Source's resolved value, not the config default" → `test_derived_object_inherits_source_document_value_not_config` (distinguishing case)

Both scenarios are backed by passing runtime tests, not just static text.

## Design coherence
`design.md`'s "Read the Source document back, and split the two `sensitivity` roles" decision (workspace_floor / stamp_sensitivity, read-back location, docstring rationale) matches the implementation exactly. No deviations found.

## Full gate results
| Command | Exit | Result |
|---|---|---|
| `uv run pytest -q` | 0 | 2385 passed |
| `uv run pytest tests/unit/cli/test_ingest.py -q` | 0 | 105 passed |
| `uv run pytest --cov=openkos --cov-branch --cov-report=term-missing -q` | 0 | 97.52% total branch coverage (gate: 90%); changed line ranges (1178-1400, 1653-1695 in `main.py`) have zero entries in the missing-branches list |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 143 files already formatted |
| `uv run mypy .` | 0 | Success: no issues found in 143 source files |

## TDD Compliance
| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | ✅ | Found in apply-progress, cycle table present |
| All tasks have tests | ✅ | 2/2 code tasks (1.1, 1.2) have test files; remainder are docstring/refactor/spec/coverage tasks |
| RED confirmed | ✅ | Independently reproduced on a `main`-based worktree (see above) |
| GREEN confirmed | ✅ | 105/105 pass on branch; 2385/2385 full suite |
| Triangulation | ➖ | Single scenario per task, matches spec's 1-scenario-per-task shape for this slice |
| Safety net | ✅ | 103 pre-existing tests in file passed before modification (per apply-progress) |

**TDD Compliance**: 6/6 checks passed

## Assertion Quality
No tautologies, ghost loops, or assertion-free tests found in the 3 reviewed tests (1 new distinguishing RED, 1 new regression pin, 1 modified baseline). All assert real file/frontmatter content, real exit codes, or real absence of an LLM call (`fake.calls == []`) — not implementation-detail coupling.

**Assertion quality**: ✅ All assertions verify real behavior

## Issues
**CRITICAL**: None.
**WARNING**: None.
**SUGGESTION**: `cfg.default_sensitivity` has no format/type validation in `config.py` (pre-existing — not introduced by this change); the new `str(...)` cast at the read-back site is incidentally more defensive than the status quo, not a regression.

## Final Verdict: PASS
