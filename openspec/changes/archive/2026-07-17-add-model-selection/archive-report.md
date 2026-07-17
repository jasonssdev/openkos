# Archive Report: add-model-selection

**Change**: add-model-selection | **Archived**: 2026-07-17 | **Status**: Complete | **Repository**: openkos (main f87a76d)

This archive report closes the SDD cycle for the `add-model-selection` change. The feature resolves a deferred promise in `docs/cli.md:50`, implementing user-selectable local model choice at `openkos init` time while preserving the `qwen3:8b` default and byte-identical template guarantee.

## Change Summary

**Purpose**: Allow users to select a local LLM model tag when initializing an OpenKOS workspace, with safe validation and silent defaults for non-interactive environments.

**Scope**: 
- Adds `--model <TAG>` flag to `openkos init`
- TTY-only interactive prompt (typer.prompt) when flag absent and stdin is a TTY
- Silent default `qwen3:8b` for non-TTY environments
- Validation logic: reject empty/blank/whitespace/quotes/`#`/newline; allow colons for Ollama name:tag format
- Template placeholder substitution (plain-text token replacement, never YAML dumper)
- New 8 scenarios in workspace-init spec

**Key Decisions**:
- D1: Placeholder token + plain-text substitution — Template line 1 → `model: __OPENKOS_MODEL__`, write_config asserts exactly one occurrence then substitutes. Rejected ruamel dumper (D5 class, fold/whitespace-collapse); rejected f-string rebuild (loses verbatim template copy). Default qwen3:8b output is byte-identical by construction.
- D2: Resolution in CLI, validation + default in config — `config.DEFAULT_MODEL="qwen3:8b"`, `config.validate_model(tag)->str` (pure trim+reject), `write_config(root, model: str = DEFAULT_MODEL)`. `cli/main.py::_resolve_model(flag: str|None)`: flag→validate; else sys.stdin.isatty()→typer.prompt()→validate; else default. Single chokepoint, keeps LLM-tag logic out of src/openkos/model/ package (naming trap avoided).
- D3: Validation predicate — After strip(), reject empty, any whitespace, `"`, `'`, `#`, newline. COLON IS ALLOWED. Rejects all whitespace makes only dangerous colon form (`: `) unreachable, so name:tag + default qwen3:8b stay valid; `#` rejected because `model: #x` parses to null in YAML.
- D0 (No ADR): `--model` flag + TTY prompt is a public CLI interface (gate condition 1 met) but additive and reversible (condition 2 likely unmet): git revert removes it, no persisted state, no consumer reads model: back. Mirror precedent add-init-command (0 ADRs). Both openspec/config.yaml:22-27 conditions must hold; design assesses but recorded as NO ADR.

**Contradiction Surfaced & Fixed**: Proposal text listed "colon" among rejected chars, but default `qwen3:8b` and all Ollama `name:tag` tags contain colons — contradiction identified in design phase. Resolution: reject whitespace/quote/# (neutralizes only hazardous colon form `: `) and ALLOW colon. Delta spec and all tests reflect this predicate.

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-17-add-model-selection/proposal.md` | Moved from change folder |
| Specification | `archive/2026-07-17-add-model-selection/specs/workspace-init/spec.md` | Merged to main spec tree at `openspec/specs/workspace-init/spec.md` |
| Design | `archive/2026-07-17-add-model-selection/design.md` | Moved from change folder |
| Tasks | `archive/2026-07-17-add-model-selection/tasks.md` | 14/14 checked; all phases complete |
| Verification Report | `archive/2026-07-17-add-model-selection/verify-report.md` | PASS (0 CRITICAL, 0 WARNING, 2 SUGGESTION non-blocking) |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **MODIFIED** | `workspace-init` | "Static openkos.yaml Template" requirement updated at `openspec/specs/workspace-init/spec.md` |
| Original scenarios | 2 (byte-identical, no-directory-derived) | Preserved and refined |
| New scenarios added | 8 | Flag override, TTY prompt (default + custom), non-TTY silent default, flag-wins-over-TTY, blank rejection, unsafe-token rejection (whitespace/quote/#/newline), colon-containing-tag acceptance |
| Total scenarios | 10 | Full coverage of model selection, validation, and template substitution logic |
| Source | Delta spec from change folder | `/openspec/changes/add-model-selection/specs/workspace-init/spec.md` merged into `/openspec/specs/workspace-init/spec.md` |
| Merge mode | MODIFIED requirement + scenarios | No other requirements touched; all 10 other requirements in workspace-init spec remain unchanged |

## Verification Status

**Final Verdict**: PASS (0 CRITICAL, 0 WARNING)

**Evidence Summary**:
- All 14/14 tasks checked (1 validation primitive, 2 template placeholder + write_config, 3 CLI flag + TTY resolution, 1 documentation, 5 verification gates, 2 refactor/docstring updates)
- Spec compliance: 10/10 scenarios of "Static openkos.yaml Template" requirement verified with passing covering tests
- Test coverage (final, post-review-correction): 101 passed / 0 failed / 0 skipped (`uv run pytest --cov`, 100.00% branch coverage across 165 stmts / 38 branches; gate: 90%). The pre-review verify stage had 89 tests; the bounded 4R correction added 12 (trailing-colon/leading-indicator rejection, end-to-end colon write, Phase B ValueError handling).
- Build: `uv build` succeeds; wheel smoke tests pass
- Quality gates: `ruff check` pass (exit 0), `ruff format --check` pass (18 files already formatted), `mypy` pass (strict mode, 18 source files, no issues)
- Code verification (independent source inspection):
  - Template has exactly one `__OPENKOS_MODEL__` placeholder with trailing spaces/comment preserved
  - `config.py` contains DEFAULT_MODEL, validate_model, write_config with plain str.replace (no YAML dumper)
  - `cli/main.py` contains _resolve_model with flag > TTY prompt > default precedence, import sys, no ruamel usage
  - init() wires --model option, catches ValueError to existing refusal path before any write
  - Colon allowed, whitespace/quote/#/newline rejected (validated at config.py:32-41)
  - Nothing added under src/openkos/model/ (OKF package untouched); model/__init__.py and okf.py pre-existing
  - D5 regression guard: write_config never reads root.name, uses plain str.replace, write_exclusive preserves byte-identity of default
- Model tag consistency: qwen3:8b default unified across template, docs, examples

**Issues**:
- 0 CRITICAL (blocks archive): None
- 0 WARNING (would be test-strength gaps): None
- 2 SUGGESTION (non-blocking, informational):
  1. No single dedicated CLI test does `--model mistral:7b` end-to-end in one assertion; colon-verbatim scenario proven via composed evidence (unit-level validate_model + default-path write_config) instead of one direct e2e test
  2. `build_output_hash` recorded as N/A — `dist/` removed post-build-check per repo convention, no output file captured for hashing; exit code 0 and success message captured directly

## Delivery History

This change was delivered as a single PR:
- **PR #9** (merged into main, 2026-07-17): Complete `--model` end-to-end (template placeholder, validation, write_config, CLI resolution, docs) — ~260-340 changed lines total, under 400-line budget (Low risk)

**Repository State**: main @ f87a76d (commit: "feat(init): add --model flag for user-selectable local LLM")

## Review Gate & Closure

**Bounded 4R Review**: 
- Lineage: review-37c6e172983a8e9a
- Receipt: ALLOW
- Approved PRs: #9 (merged main, 2026-07-17)

All review findings cleared; no blockers remain. Change is fully implemented, verified, and approved.

## Deferred/Out-of-Scope Items

**Explicitly deferred to later changes**:
- Model consumer implementation (other commands reading model: from openkos.yaml)
- Live `ollama list` integration (validation against available local models)
- Ollama `ollama pull` automation (if model not found locally)
- Curated allowlist of known-good models (only validation is syntactic, not semantic)

**Accepted residual limitations**:
- `model` naming collision: The variable `model` in `cli/main.py` and `config.py` is local CLI/config state, separate from the `openkos.model` package (OKF format layer). No import collision because model: remains a config field, never imported as a module. Naming trap avoided by design decision D2.

## Risks & Mitigations

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| YAML-corruption regression (D5 class) | Low | Constrained single-token plain-text replacement; validate_model RED tests; double-spaced-dir regression unchanged | Verified |
| Prompt breaks CI | Mitigated | TTY guard (sys.stdin.isatty()); CliRunner stdin is non-TTY by default; existing test unchanged | Verified |
| Model tag syntax issues | Mitigated | Allow colon (name:tag support), reject whitespace/quote/#/newline (only dangerous form `: ` unreachable) | Verified |
| Template placeholder invariant breaks | Low | Assert template.count("__OPENKOS_MODEL__") == 1 at write time; caught by test_write_config_raises_on_corrupt_template | Verified |
| Default byte-identity lost | Mitigated | Template line unchanged except placeholder; default qwen3:8b renders byte-identical to original by construction | Verified |

## Archival Actions Completed

**Filesystem**:
- [x] Main spec tree updated at `openspec/specs/workspace-init/spec.md`
- [x] "Static openkos.yaml Template" requirement merged with 8 new scenarios (10 total)
- [x] All other workspace-init requirements preserved unchanged
- [x] Change folder prepared for move to `openspec/changes/archive/2026-07-17-add-model-selection/`
- [x] All change artifacts (proposal, design, tasks, verify-report, specs) ready for archive

**Engram**:
- [x] Archive report saved with topic key `sdd/add-model-selection/archive-report`
- [x] All artifact observation IDs recorded for traceability (proposal #851, spec #852, design #853, tasks #855, verify-report #860)

## Next Steps

**For the project**:
- Archive folder move to `openspec/changes/archive/2026-07-17-add-model-selection/` (orchestrator will execute via git mv)
- Final archive commit message: "docs(sdd): archive add-model-selection"
- No follow-up changes required for this slice; model consumer, ollama integration, and allowlist are explicitly deferred

**For model consumer work**:
- When ready, implement reading model: from openkos.yaml in engine/inference layer
- Deferred model validation (semantic allowlist check against local ollama list)
- Deferred model pull automation

## Traceability

This archive report records the final state of the `add-model-selection` change from proposal through implementation, verification, and archival. The change has been:
- Fully specified (1 modified requirement, 8 added scenarios, 10 total scenarios in workspace-init)
- Fully designed (3 architecture decisions, contradiction surfaced and fixed, no ADRs needed)
- Fully implemented (template placeholder, validation predicate, CLI resolution, 2 source files + template line + 2 test files + docs)
- Fully verified (14/14 tasks complete, 101/101 tests pass after the review correction, 100% coverage, all quality gates green; sdd-verify was clean, and the bounded 4R review found + fixed one CRITICAL — a trailing-colon YAML break — before approval)
- Fully delivered (main f87a76d, PR #9 merged, 4R review approved)

The SDD cycle is CLOSED. The change is archived and ready for the next change.

**Archive Date**: 2026-07-17 (ISO format)  
**Repository Head**: f87a76d (main)  
**Verification Date**: 2026-07-17  
**Review Lineage**: review-37c6e172983a8e9a (ALLOW)  
**Archival Status**: COMPLETE
