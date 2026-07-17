# Archive Report: add-init-command

**Change**: add-init-command  
**Date**: 2026-07-16  
**Status**: Complete  
**Branch**: feat/init-command (4-PR stacked chain to main, not yet pushed)  
**Mode**: Strict TDD  
**Review Gate**: review-init-final (result: allow)  

## Artifacts Retrieved

| Artifact | Observation | Content |
|----------|-------------|---------|
| Proposal | #787 | Scope, deferred items, rollback plan, resolved questions |
| Spec | #790 | 11 requirements, 19 scenarios |
| Design | #792 | D1-D7 (module map, two-phase architecture, template handling, no ADRs) |
| Tasks | #794 | 27/27 tasks complete (4 phases, 4 PRs) |
| Verify Report | #808 | 42 tests, 100% branch coverage, 0 CRITICAL, 2 WARNING (disclosed), ready |
| Follow-ups | #815 | 8 accepted findings, 4 named future changes, all non-blocking |

## Spec Merge Completed

**Source**: `openspec/changes/add-init-command/specs/workspace-init/spec.md`  
**Destination**: `openspec/specs/workspace-init/spec.md` (new capability)  
**Status**: ✅ Created (pure additive, no existing main spec to merge)

### Requirements Preserved

- 11 requirements intact (Workspace Creation, Bundle Index Shape, Bundle Log Shape, Generated Workspace Config, Static AGENTS.md Template, No Concept-Type Folders, Refusal Idempotency, Write Failure Handling, Adoption of Non-Workspace Directories, Default raw/ Permissions, OKF Conformance)
- 19 scenarios intact and all mapped to executable tests

### Scenario-to-Test Traceability

All 19 scenarios map to test coverage:
- Workspace Creation (2 scenarios): test_fresh_empty_directory, test_success_message_stdout
- Bundle Index Shape (1 scenario): test_create_writes_index_with_correct_frontmatter
- Bundle Log Shape (2 scenarios): test_create_writes_log_with_date, test_log_dated_section_uses_local_date_not_utc
- Generated Workspace Config (1 scenario): test_write_config_generates_name_from_directory
- Static AGENTS.md Template (1 scenario): test_agents_template_byte_identical
- No Concept-Type Folders (1 scenario): test_create_writes_exactly_index_and_log
- Refusal Idempotency (6 scenarios): test_refuses_existing_openkos_yaml, test_refuses_existing_agents_md, test_refuses_non_empty_raw_or_bundle (parametrized), test_refuses_raw_or_bundle_as_file, test_second_run_refuses, test_no_partial_output
- Write Failure Handling (1 scenario): test_write_failure_surfaces_clean_error
- Adoption of Non-Workspace Directories (1 scenario): test_adopt_non_workspace_directory
- Default raw/ Permissions (1 scenario): test_raw_default_permissions
- OKF Conformance (2 scenarios): test_fresh_bundle_is_conformant, test_rule_3_holds_by_construction

## What Shipped

### Output Files Created by `openkos init`
- `raw/` (empty directory, default permissions, no chmod)
- `bundle/index.md` (frontmatter `okf_version: "0.1"`, empty body)
- `bundle/log.md` (header, dated section with local date, initialization bullet)
- `openkos.yaml` (generated, not copied; name from cwd dir name; model default qwen3.5:9b)
- `AGENTS.md` (byte-identical static template copy)

### Code Packages Delivered
- `src/openkos/model/okf.py` — OKF format constants, frontmatter load/dump, §9 rules 1-2 check
- `src/openkos/bundle/index.py` — pure renderer for index.md
- `src/openkos/bundle/log.py` — pure renderer for log.md (takes date parameter)
- `src/openkos/bundle/bundle.py` — bundle root creation
- `src/openkos/config.py` — workspace layout, refusal conditions, config/AGENTS.md writers
- `src/openkos/cli/main.py` — Typer app, init command, pre-flight sequencing, exit codes
- `src/openkos/templates/` — package data (agents.md.template, openkos.yaml.template, no __init__.py)

### Console Entry Migration (One Commit, PR 1)
- `pyproject.toml:20` → `openkos = "openkos.cli.main:app"` (removed `openkos.main:main`)
- `src/openkos/__init__.py` → stub `main()` deleted (no alias to cli.main:app)
- `tests/unit/test_main.py` → greeting test deleted, entry-point test asserts `is openkos.cli.main.app`
- `.github/workflows/ci.yml:116` → smoke test changed to `uv run --isolated --no-project --with dist/openkos-*.whl openkos --help`

### Documentation Corrections
- `AGENTS.md:64` — openkos init added ahead of ingest in MVP 1 vertical-slice description (was: ingest first)
- `docs/cli.md:27` — ollama pull qwen3 → ollama pull qwen3.5:9b (stale tag in prerequisites)
- `docs/cli.md:48` — honest-gap rewrite: removed false promises ("Helps you pick a local model", "concept folders"). Added note naming add-model-selection deferral.
- `docs/cli.md:99` — yaml block: model: qwen3:8b → model: qwen3.5:9b

### Delivery Metrics
- **4 PRs stacked to main** (~130 + ~283 + ~296 + ~260 = ~969 changed lines, broken into slices comfortably under 400-line budget)
- **27/27 implementation tasks complete** (4 phases, 4 commits, all RED-GREEN-REFACTOR cycles closed)
- **42/42 tests pass** (4 refusal test functions, 4 characterization tests, 1 smoke test, 1 CI step, 100% branch coverage, gate 90%)
- **ruff check/format, mypy, uv lock --locked** all green
- **Not yet pushed** — PRs staged on feat/init-command branch, pending push authorization

## Deferred (Named, NOT Delivered)

### User-Facing Gaps Documented
1. **`docs/cli.md:48` still promises interactive model picker** (`add-model-selection` — pulls in llm/ module; CI has no Ollama for testing; explicitly deferred by proposal Q9/Q10)
2. **`docs/cli.md:48` still promises concept folders created at init** (deliberate Q7.3 refusal; ingest creates them on first write)
3. **`git init` not run** (`add-workspace-git` — explicit scope boundary; deferred change)
4. **Repo-wide model-tag refresh** (`refresh-model-guidance` — six docs still reference `qwen3:8b`, different project scope, updated stale tags only where init output touches the user-created files)

### Mechanical Checks Deferred
- **OKF §9 rule 3 mechanical verification** — init's output satisfies rule 3 by construction (log/index shape), but the MECHANICAL CHECK that validates rule 3 across all markdown in a bundle is deferred to the `lint` command (out of scope for this slice)

## Follow-ups Carried Forward (Observation #815)

Eight accepted, non-blocking findings from three 4R bounded review passes:

| # | Finding | Risk | Owner |
|---|---------|------|-------|
| 1 | Directory name starting with `#` yields `name: #foo` → YAML parses as comment → `name: None`. Narrow (first char only; `mis#notas` is fine). No live consumer yet. | pre-existing, data | lint / add-workspace-git |
| 2 | Refusal test snapshot helper blind to directory creation; `raw/` creation before pre-flight would pass undetected. | pre-existing, coverage | add-init-hardening |
| 3 | `bundle_dir.mkdir(exist_ok=True)` follows symlink `bundle`, writing outside workspace root. Leaf files protected by mode `"x"`, so impact is misplaced non-secret content, not overwrite. | pre-existing, edge case | add-init-hardening |
| 4 | `check_conformance` broad `except Exception` conflates I/O failure (PermissionError) with conformance violation. Not called in production init path, only in tests against always-readable fresh bundle. | pre-existing, deferred | lint |
| 5 | Exclusive-create `open(..., "x")` pattern duplicated at four sites with near-duplicate docstrings; fifth call site could silently drop `"x"`. | pre-existing, maintainability | add-init-hardening |
| 6 | `_refusal_conditions` yields bare `(bool, str)`; meaning lives only in docstring, not at yield sites. | pre-existing, clarity | add-init-hardening |
| 7 | Docstring claims `Path.mkdir` "would raise uncaught FileExistsError" without 5th condition. **FALSE**: cli/main.py already catches OSError (FileExistsError subclass). Docstring rotted after same-session correction. | pre-existing, documentation | add-init-hardening |
| 8 | Phase-B failure leaves `bundle/` non-empty; retry refuses with generic message (no hint it is stray from crashed init). Correctly classified pre-existing; manual recovery weighed and documented. | pre-existing, UX | documentation / add-init-hardening |

**Also open (not review findings)**:
- `refresh-model-guidance` (six docs with `qwen3:8b`, separate project scope)
- `add-model-selection` (Ollama pick/pull flow, waiting for hardware/testing infrastructure)
- `add-workspace-git` (`git init` integration)

## ADR Analysis

**Zero ADRs created. Deliberate.** Evaluated against openspec/config.yaml:21-27 gate (must decide a technology/pattern/interface/trade-off AND be hard-to-reverse):

1. **Console entry point migration to `openkos.cli.main:app`** — Fails hard-to-reverse gate: v0.1.0, no published wheel, no release, no CI publish step. Reversal = one commit revert across three files (exact rollback plan). Not a permanent decision, conditional on future publication. Also: decision already documented (AGENTS.md:34, docs/architecture.md:106) — design executed existing precedent, not made a new one.

2. **Removing model selection from init's scope** — Fails gate: this is a deferral (add-model-selection named), not a trade-off. Nothing to reverse; ingest adds the feature when ready. Also: decision already documented (docs/tech_stack.md:108) — ship a working default (verified qwen3.5:9b exists on Ollama registry) until the spike runs.

3. **Omit concept-type folder scaffolding** — Fails gate: behavior contract belongs in the spec (workspace-init spec, line Requirement: No Concept-Type Folders), not in an architectural record. Implementation follows documented decision (Q7.3).

4. **qwen3.5:9b model default** — Evaluated and rejected: one-line reversible by design (edit one value in openkos.yaml after init). Chose a working tag (verified against Ollama registry 2026-07-16) over trying to future-proof; the model spike will benchmark and reset. Not a technology lock-in.

5. **Templates as package data (files() + importlib.resources)** — Evaluated and rejected: reversible (move files elsewhere, update loader). Pattern commonplace in Python packaging.

**Conclusion**: The design executed two previously-documented decisions (console entry point, model default) and one scoped behavior contract (no scaffolding). No novel decision met both ADR criteria. openspec/config.yaml is explicit: "When in doubt, do not create one." If a decision genuinely went unrecorded, report rather than back-fill an ADR whose forces are no longer fresh. None identified.

## Quality Gates

### Verification Summary (from #808)
- **Tests**: 42/42 passed, 100% branch coverage (gate: 90%)
- **Review**: 3 full 4R passes, receipt `review-init-final` = allow
- **Blockers**: 0 CRITICAL issues
- **Warnings**: 2 (disclosed, non-blocking — scenario traceability seam, deferred-rule wording)
- **Suggestions**: 1 (cosmetic comment recommendation)

### Traceability (SDD artifact observation IDs)
- Proposal: #787 (resolved 9 questions, 3-way split rejected, 4-way approved)
- Spec: #790 (11 req, 19 scenarios, all mapped to tests)
- Design: #792 (D1-D7, zero ADRs justified, console entry + template approach + two-phase init)
- Tasks: #794 (27/27 complete, 4 phases, apply-progress #799)
- Verify: #808 (0 CRITICAL, 2 WARNING, ready)
- Follow-ups: #815 (8 findings, 4 named future changes)

## Archival Summary

✅ **Delta spec merged to main specs**: `openspec/specs/workspace-init/spec.md` created (11 req, 19 scenarios intact)  
✅ **All 27 tasks complete**: No stale checkboxes, apply-progress #799 confirms implementation end-to-end  
✅ **No CRITICAL issues in verify-report**: 2 disclosed WARNINGs (pre-existing, deferred)  
✅ **Review gate satisfied**: review-init-final receipt allows archive  
✅ **Artifacts immutable**: source code, examples, raw/ untouched; archives will persist via git mv  

## Next Recommended Changes

Based on follow-up backlog (#815) and deferred scope:

1. **add-init-hardening** (0-sprint spike) — Batch findings 2, 3, 5, 6, 7, 8 into one hardening change after this archive lands. Improves error messages, refactors helpers, fixes rotted docstring.

2. **refresh-model-guidance** (cross-cutting docs) — Update six docs (tech_stack, user-journey, faq, roadmap, examples/) from `qwen3:8b` to `qwen3.5:9b` once all CLI commands that write configs land.

3. **add-model-selection** (MVP 1, blocked on Ollama availability in CI) — Interactive model picker using hardware probe and Ollama list/pull.

4. **add-workspace-git** (MVP 1 vertical slice) — `git init` in workspace root.

5. **lint** (deferred, separate team) — Implement OKF §9 rule-3 mechanical check and integrate into pre-commit.

---

**Archive Date**: 2026-07-16  
**Archived By**: sdd-archive  
**Change Status**: Complete, awaiting push authorization to main
