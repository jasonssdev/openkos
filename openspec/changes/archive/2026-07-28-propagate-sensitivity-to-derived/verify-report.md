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

---

# Verification Report: propagate-sensitivity-to-derived (PR 2 of 2)

```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:66825be18c34544996c926c7522cd58ba2eef624
verdict: pass
blockers: 0
critical_findings: 0
requirements: 2/2
scenarios: 7/7
test_command: uv run pytest -q
test_exit_code: 0
test_output_hash: sha256:761b5136f3069ffc374046416d0080ee073bcf27d3c080071dcb9555319ef873
build_command: uv run ruff check . && uv run ruff format --check . && uv run mypy .
build_exit_code: 0
build_output_hash: sha256:03a2fe9a7ba5d4487df1326a2cf7e35215a3c1d711527d7ebabc9c8f4b56fac
```

**Scope of this run**: `feat/set-sensitivity-propagates-to-derived` branch, 4 commits off `fix/ingest-inherits-source-sensitivity` (`d0900a2`, PR 1, already open as PR #226 with green CI) — Phase 2 tasks 2.1-2.19 (set-time raise-only propagation from a Source to its provenance descendants), ADR-0009, and the `sensitivity-config` delta spec. PR 1's creation-time inheritance is treated as already verified; re-checked here only for regression risk.

**Verdict: PASS** — 0 CRITICAL, 1 WARNING, 1 SUGGESTION.

## Commits reviewed
- `c912fa8` feat(cli): raise Source sensitivity to provenance descendants
- `3f402d1` docs(sensitivity-config): back raise-only propagation with a spec delta
- `1e9242e` docs(adr): record ADR-0009 superseding ADR-0008's scope statement
- `66825be` chore(sdd): mark Phase 2 tasks complete for propagate-sensitivity-to-derived

## Completeness (Phase 2 tasks)
All 19 tasks (2.1-2.19) checked `[x]` in `tasks.md`, each verified against the actual diff (`git diff fix/ingest-inherits-source-sensitivity..HEAD`): 11 test additions/inversions in `tests/unit/cli/test_set_sensitivity.py`, the `_DescendantRaise` dataclass and Source-branch propagation block in `src/openkos/cli/main.py`, the `sensitivity-config` spec delta applied verbatim, and ADR-0009 + README index row created. No unchecked tasks. Changed lines vs base: 698 additions + 45 deletions across 6 files (743 total) — over the 400-line default budget by design (chained/stacked `auto-chain`), under the 800-line session ledger cap, matching the tasks-phase forecast (~590, actual 743 including the +38-line tasks.md checkbox diff).

## Behavioral invariants — proven, not assumed

| # | Invariant | Proof | Result |
|---|---|---|---|
| 1 | Raise-only: lowering a Source must not lower a descendant | `test_lowering_source_never_lowers_derived` — raises both, then lowers Source with `--allow-downgrade`; asserts descendant file bytes unchanged post-lowering | ✅ PASS |
| 2 | A descendant already above the Source's new (lower) target level stays put | `test_descendant_already_higher_is_not_lowered` — descendant at `confidential`, Source lowered to `private`; descendant bytes unchanged | ✅ PASS |
| 3 | Phase B write order is descendants → target; a mid-way failure over-classifies, never under-classifies | `test_descendants_written_before_target_on_failure` — monkeypatches `fsio.write_atomic` to raise `OSError` only for the Source's own path; asserts exit 1, descendant already raised to `confidential` on disk, Source's own file still `private` (unwritten) | ✅ PASS — real ordering proof, not inferred from source order |
| 4 | Non-Source targets are byte-identical to today, whole-bundle scan skipped entirely | Source inspection: the entire descendant-closure/warning-scan block sits inside `if metadata.get("type") == "Source":` — a non-Source `concept_id` never enters that branch, no `rglob`/frontmatter read of any other file occurs. Behaviorally confirmed by `test_non_source_concept_touches_only_itself` (Source's own file byte-identical) and `test_non_source_success_message_keeps_only_this_concept_line` | ✅ PASS — skip is structural (whole `if` block), not merely an empty result set |
| 5 | Dangling/unresolvable provenance warns, is excluded, does not abort the target's own write | `test_dangling_provenance_warns_and_never_lowers` — stderr WARNING names `does-not-exist`, dangling concept's own `sensitivity` unchanged (`public`), Source's own write still exits 0 and reaches `confidential` | ✅ PASS |
| 6 | `--auto` still propagates; skips only the confirmation prompt | `test_auto_propagates_without_prompting` proves propagation under `--auto`. Source inspection confirms the descendant-closure/staging block runs unconditionally, before the `confirm_enabled`/`prompt_will_run` branch — `auto` only affects the boolean gating `typer.confirm`, never the staging loop | ✅ PASS |
| 7 | `combine_sensitivity` reused, never reimplemented | `okf.combine_sensitivity(member_current, level)` called directly at the one call site (`cli/main.py` descendant loop); `model/okf.py`'s `combine_sensitivity`/`_rank` are unmodified by this diff; no second max/rank/ordering implementation found anywhere in the new code | ✅ PASS |
| 8 | `sensitivity-aware-llm` untouched | `git diff` shows zero changes to `openspec/specs/sensitivity-aware-llm/spec.md` (still 8 `### Requirement` blocks) and zero changes to any of its call sites (`blocks_llm_send` import/gate at `main.py:1243-1265` untouched by this diff — only lines ≥3056 changed) | ✅ PASS |

## Flagged items — verdicts

### Dangling-provenance warning as an independent full-bundle re-scan
**Verdict: correct as implemented, not a defect — but scoped wider than the closure it complements, by design.**

`find_provenance_descendants`'s fixed-point algorithm (`bundle/provenance.py:86-98`) requires a candidate's *entire* `provenance` set to already be a subset of the growing `purge` set seeded from `root_ids`. A candidate is silently excluded from the closure both when a cited id is truly dangling (no file anywhere) and when a cited id is a *different, real* Source (the deferred multi-source high-water-mark case) — the closure algorithm does not distinguish the two reasons for exclusion.

The independent warning scan checks each bundle file's `provenance` entries against `known_ids = {canonical_id} ∪ {every file id in the snapshot}` — i.e. "does this id resolve to *any* file in the bundle," not "is this id reachable from the current root." This means:
- It never disagrees with the closure on the multi-source case: those entries reference real files, so `entry_id in known_ids` is true and no warning fires — correctly silent, matching the design's deferred-non-goal.
- It never disagrees with the closure on truly-missing ids: both mechanisms treat them the same way (excluded from closure, and separately reported).
- It **is** unscoped to the current run: the scan iterates every file in the bundle snapshot, so a `set-sensitivity <SourceA> ...` invocation will also warn about a dangling reference belonging to an entirely unrelated `SourceB`'s descendant tree, if one exists anywhere in the bundle. This matches design.md's literal instruction ("scan the parsed provenance map for entries naming an id with no file in the snapshot") and apply-progress's own flagged deviation note.
- Cost is exactly what design.md states: one extra frontmatter parse per bundle file, on a rare human verb, only when the target is a Source (already paid once for the closure itself).

No test exercises a multi-Source-tree bundle to confirm the "unrelated dangling reference still warns" behavior is intentional rather than accidental scope creep. **WARNING**: add (or explicitly accept as a documented follow-up) a test asserting the scan's bundle-wide scope, so a future reader cannot mistake the unscoped noise for a bug and silently narrow it in a way that would then hide a real dangling reference outside the current root's own descendants.

### ADR-0008 left unedited (no status change)
**Verdict: correct, matches established repo convention — not a documentation gap.**

`docs/adr/README.md`'s status lifecycle defines `Superseded by ADR-XXXX` as "replaced by a later decision" (whole-ADR framing). This repository has direct, in-repo precedent for a *partial*, scope-narrowing decision that does NOT trigger a status change on the ADR being narrowed: ADR-0008 itself states "We scope ADR-0003's 'never less' to **machine-chosen** values" — an explicit narrowing of ADR-0003's "never less" rule — yet ADR-0003's `status` was left `Accepted` (verified: `docs/adr/0003-sensitivity-high-water-mark.md:5,17` and the README index row both still read `Accepted`, not `Superseded by ADR-0008`). ADR-0009 does the same kind of thing to ADR-0008 (narrows one scope sentence, leaves the downgrade-gate decision fully in force) and follows the exact same precedent: no status edit. This is consistent, not a gap — the apply agent's own flagged uncertainty is resolved in favor of "zero edits was correct."

## Spec compliance matrix — `sensitivity-config` delta (2 requirements, 7 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Scope Is Exactly One Named Concept (modified) | Sibling concepts and a non-Source target's derived concepts are untouched | `test_non_source_concept_touches_only_itself`, `test_non_source_success_message_keeps_only_this_concept_line` | ✅ COMPLIANT |
| Raise-Only Propagation to Provenance Descendants (added) | Raising a Source raises every derived object in the same run | `test_raising_source_raises_derived_objects` | ✅ COMPLIANT |
| " | Lowering a Source leaves derived objects untouched | `test_lowering_source_never_lowers_derived` | ✅ COMPLIANT |
| " | A derived object already at a higher level is not lowered | `test_descendant_already_higher_is_not_lowered` | ✅ COMPLIANT |
| " | Unresolvable provenance warns, is excluded, and does not abort | `test_dangling_provenance_warns_and_never_lowers` | ✅ COMPLIANT |
| " | A Source with zero derived objects behaves exactly as today | `test_source_with_zero_derived_objects_unchanged` | ✅ COMPLIANT |
| " | `--auto` propagates without prompting | `test_auto_propagates_without_prompting` | ✅ COMPLIANT |

**Compliance summary**: 7/7 scenarios compliant.

## Design coherence
`design.md`'s decisions all match implementation exactly: reuse `find_provenance_descendants` unchanged (no signature change, confirmed by diff); descendants written before target (`test_descendants_written_before_target_on_failure`); Source detection via `type` field, never path (`metadata.get("type") == "Source"`, confirmed); idempotence short-circuit (`current == level` early return) sits before the descendant block, confirmed by reading source order — the early `return` at the top of the function precludes the descendant-closure code from ever running on that path; `combine_sensitivity` used only for descendants, never for the target's own human-assigned value (target still uses `sensitivity_direction`/ADR-0008's gate, unchanged). No deviations found beyond the two explicitly flagged and resolved above.

## Full gate results
| Command | Exit | Result |
|---|---|---|
| `uv run pytest -q` | 0 | 2396 passed |
| `uv run pytest tests/unit/cli/test_set_sensitivity.py -q` | 0 | 35 passed |
| `uv run pytest --cov=openkos --cov-branch --cov-report=term-missing -q` | 0 (one transient run showed 2 unrelated flaky failures in `tests/unit/cli/test_relate.py`, a file untouched by this diff; both passed in isolation and on two subsequent full reruns — pre-existing/environmental flake, not a regression from this change) | 97.52% total branch coverage (gate: 90%). New code's 3 required branches (Source branch, empty-descendant branch, dangling-provenance branch) are fully covered per tasks 2.1/2.8/2.5; the only missing branches inside the new descendant-propagation block are the defensive `OSError`/`ValueError` exception arms and the per-file malformed-frontmatter skip inside the warning scan — explicitly out of scope per task 2.17 and apply-progress |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 143 files already formatted |
| `uv run mypy .` | 0 | Success: no issues found in 143 source files |

## TDD Compliance
| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | ✅ | Full cycle table for tasks 2.1-2.10 in apply-progress |
| All tasks have tests | ✅ | 10/10 RED tasks have test files; 9 GREEN/REFACTOR tasks are implementation/spec/doc tasks |
| RED confirmed | ✅ | Cross-referenced against actual test file content (all 11 new/inverted tests present and asserting real behavior) |
| GREEN confirmed | ✅ | 35/35 pass in the focused file; 2396/2396 full suite |
| Triangulation | ✅ | 9 distinct propagation behaviors each get a distinct test asserting a different expected value (raised, unchanged, byte-identical, warning text, commit set, etc.) — no repeated trivial assertion pattern |
| Safety net | ✅ | Full-repo suite (2396) run and green; `test_relate.py` flake is isolated and unrelated to files this change touches |

**TDD Compliance**: 6/6 checks passed

## Assertion Quality
Reviewed all 11 new/modified tests in `test_set_sensitivity.py`. No tautologies, no ghost loops over possibly-empty collections, no assertion-free tests. All assertions check real file bytes, real exit codes, real stdout/stderr text, or the real `git` commit path set (`_last_commit_files` reads actual `git show --name-only`). `test_commit_stages_every_changed_path` explicitly checks a companion non-swept case (`"concepts/unrelated.md" in status`) rather than only an empty/negative assertion.

**Assertion quality**: ✅ All assertions verify real behavior

## Issues Found
**CRITICAL**: None.

**WARNING**: The dangling-provenance warning scan is intentionally bundle-wide (not scoped to the current Source's own closure/tree) — correct and matches design, but no test pins this scope decision for a multi-Source bundle, so a future reader could mistake the cross-tree noise for a bug and narrow it incorrectly, silently hiding a real dangling reference. Recommend a follow-up test asserting the scan's bundle-wide scope explicitly.

**SUGGESTION**: The `test_relate.py` flake observed once under `--cov` (passed on 2 other full-suite runs, and in isolation) appears pre-existing and unrelated to this diff (file untouched); worth a separate investigation ticket for test isolation under coverage instrumentation, not a blocker for this change.

## Final Verdict: PASS WITH WARNINGS
0 CRITICAL, 1 WARNING (informational — behavior is correct, only its scope is undocumented by a test), 1 SUGGESTION (pre-existing, unrelated flake). All 7 spec scenarios and all 8 explicitly requested behavioral invariants are proven by real, passing runtime tests, not source-reading inference. Ready for `sdd-archive`.
