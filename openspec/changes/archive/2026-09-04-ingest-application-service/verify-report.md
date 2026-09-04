```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:8e92ada223d8f0007b508457cdd4b1cb40c73b910345374c5bae1b822b6467b4
verdict: pass
blockers: 0
critical_findings: 0
requirements: 7/7
scenarios: 9/9
test_command: uv run pytest --cov=src/openkos -q
test_exit_code: 0
test_output_hash: sha256:c32148cbfea3d9c336c5e59f4994f9ec3a32d6418d707b097b5399ba5439cc86
build_command: uv run ruff check . && uv run ruff format --check . && uv run mypy .
build_exit_code: 0
build_output_hash: sha256:64c3b8b0166d7ba81ce809037b4d9adb8f3eb5a710ae3a3336fec673ebbca092
```

## Verification Report

**Change**: ingest-application-service
**Version**: N/A (openspec change, issue #918)
**Mode**: Strict TDD

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 44 (Slice 1: 8, Slice 2: 18, Slice 3: 18) |
| Tasks complete | 44 |
| Tasks incomplete | 0 |

All three slices merged to `main`: PR #940 (`7ec516b`), PR #941 (`e5a7682`), PR #943 (`8dcefbe`). Verified on branch `chore/918-ingest-service-verify-archive`, off `main` at `8dcefbe`.

### Build & Tests Execution

**Build**: ✅ Passed
```text
$ uv run ruff check .
All checks passed!
$ uv run ruff format --check .
291 files already formatted
$ uv run mypy .
Success: no issues found in 291 source files
```

**Tests**: ✅ 6020 passed / ❌ 0 failed / ⚠️ 1 skipped
```text
$ uv run pytest --cov=src/openkos -q
TOTAL   12611  350  4016  157  97%
Required test coverage of 90.0% reached. Total coverage: 96.93%
6020 passed, 1 skipped, 25 warnings in 332.70s (0:05:32)
```

**Focused suites, run independently:**
- `uv run pytest tests/unit/application/test_layering.py -v` — 4/4 passed.
- `uv run pytest --cov=src/openkos/application tests/unit/application -q` — 79/79 passed; `application/ingest.py` 96% branch coverage (10 statements, 2 partial branches uncovered — the two provably-unreachable branches, see Correctness table below); `application/query.py` 98%. Gate requires 90%, reached.
- `uv run pytest tests/unit/cli/test_ingest.py -q` — 322/322 passed.
- `uv run pytest tests/unit/cli/test_ingest_characterization.py -v` (default git-config environment) — 10/10 passed.
- `env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null uv run pytest tests/unit/cli/test_ingest_characterization.py -v` — 10/10 passed. Byte-identical result in both environments; this is exactly the asymmetry that reddened all three Python versions in CI on Slice 2's first attempt (per apply-progress), and it does not reproduce here.

**Coverage**: 96.93% total / threshold 90.0% → ✅ Above. `application/ingest.py` alone: 96% (239 stmts, 10 missed; 58 branches, 2 partial). Uncovered lines: 130, 137, 157-160 (macOS-only NFD-normalization defensive branches in `collision_family`/`family_owns_source`, pre-existing and orthogonal to this change), 621-625 (`converged_reingest`'s `except ValueError:` — see #942 below), 673-674 (`_read_source_title`'s `except Exception:` — provably unreachable, see Correctness table).

### Spec Compliance Matrix

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Non-CLI Callable Ingest Composition | A non-CLI caller stages derived objects | `tests/unit/application/test_ingest.py` (whole module imports nothing from `openkos.cli`, calls `ingest_service.stage_derived_objects`/`compose_source_document`/`compose_catalog_update`/`converged_reingest` directly) + `test_layering.py::test_application_modules_never_import_cli_typer_or_rich` | ✅ COMPLIANT |
| Non-CLI Callable Ingest Composition | No concrete backend is bound inside the service | `test_layering.py::test_application_modules_bind_no_concrete_llm_backend` (AST-scans `application/*.py`; only `openkos.llm.base` permitted) | ✅ COMPLIANT |
| Extraction Disclosure Data Is Returned, Not Rendered | The service module renders nothing | `test_layering.py::test_application_modules_never_import_cli_typer_or_rich` (AST-scans for `openkos.cli*`/`typer`/`rich` imports) + `test_stage_derived_objects_renders_nothing` (capsys empty) + manual grep confirming zero `typer.`/`rich`/`Console`/`observability` calls in `application/ingest.py` (matches are docstring/comment prose only) | ✅ COMPLIANT |
| Extraction Disclosure Data Is Returned, Not Rendered | A degrade condition is returned as typed data | `test_stage_derived_objects_returns_no_extractable_text_reason`, `..._returns_blocked_by_sensitivity_reason`, `..._returns_no_concepts_found_reason` | ✅ COMPLIANT |
| Progress Reporting Is Injected, Never Owned | The adapter supplies progress reporting | `stage_derived_objects`'s `on_progress: ProgressHook \| None = None` parameter forwarded unchanged to `extract_concept`/`extract_concept_union` (`application/ingest.py:362-368`); no `Console`/`observability` import anywhere in the module (grep-confirmed); exercised end-to-end by `tests/unit/cli/test_ingest.py`'s 4 `monkeypatch.setattr(main, "Console", ...)` sites, which still pass unmodified | ✅ COMPLIANT |
| Decoded Text Arrives As A Parameter | The service reads no files | `application/ingest.py` has no `open(`/`Path.read_text`/`_snapshot_read` call; `stage_derived_objects` and `compose_source_document` both take `raw_content`/`concept_text` as `str \| None` parameters (grep-confirmed, plus module-wide `test_application_modules_never_import_cli_typer_or_rich` catches nothing filesystem-specific but the signatures are the direct evidence) | ✅ COMPLIANT |
| The #773 Convergence Short-Circuit Is A Typed Outcome | Convergence returns a typed outcome, not a raw return | `test_converged_reingest_returns_carried_notices_on_convergence`, `..._returns_empty_notices_on_a_clean_convergence`; adapter mapping proven byte-identical by `test_converged_reingest_matches_pre_move_golden` (characterization, both git-config environments) | ✅ COMPLIANT |
| Shared Write Mechanics And Client Construction Stay Adapter-Side | Committing a plan uses the existing shared helpers | `test_layering.py::test_shared_write_helpers_are_never_forked` (AST-scans all of `src/` for `_reject_drifted_targets`/`_autocommit`/`_refresh_derived_after_write`; each must have exactly one definition, repo-wide) | ✅ COMPLIANT |
| The Extraction Preserves Observable CLI Behavior | A previously-passing CLI scenario is unchanged | `tests/unit/cli/test_ingest.py` full suite (322/322 passed, unmodified output-text assertions) + 10/10 characterization goldens byte-identical in both environments | ✅ COMPLIANT |

**Compliance summary**: 9/9 scenarios compliant, 7/7 requirements satisfied.

### Correctness (Static Evidence)

| Requirement | Status | Notes |
|------------|--------|-------|
| `application/ingest.py` imports no `cli` | ✅ Verified | `test_layering.py` AST scan, passing; manual `grep` of the module's actual (non-docstring) code confirms zero `typer.`/`rich.`/`Console`/`observability` calls |
| `openkos.llm.*` imports exactly `["openkos.llm.base"]` | ✅ Verified | Single `from openkos.llm.base import LLMBackend` at line 52; `test_application_modules_bind_no_concrete_llm_backend` passes |
| `main.py` no longer exposes `extract_concept`/`extract_concept_union` | ✅ Verified | `grep` of `main.py`'s production code shows zero calls (only docstring prose); `test_main_no_longer_exposes_the_extractor_names` asserts `not hasattr(main, "extract_concept"/"extract_concept_union")` |
| Two branches provably unreachable, correctly uncovered | ✅ Confirmed | `converged_reingest`'s `except ValueError:` (malformed YAML raises `yaml.YAMLError`, not a `ValueError` subclass — this IS issue #942, confirmed filed/open, pre-existing on `main` before this change, deliberately out of scope) and `_read_source_title`'s `except Exception:` (unreachable because `_read_source_sensitivity` always runs first on identical text and raises first) |

### Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| Service returns typed disclosure data; adapter owns every word | ✅ Yes | `StagedDerivedObjects`/`StagingDrop` carry all 24 former `typer.echo` payloads; `main.py`'s `_render_staged_derived_objects`/`_render_staging_drop` render them |
| Backend exception propagates; adapter catches `OllamaError` | ✅ Yes | No `except OllamaError` (or any exception around `llm.chat`) inside `application/ingest.py`; `main.py` retains the verbatim `except OllamaError` block including the #746 advisory |
| Refusals raise; adapter's existing handler unchanged | ✅ Yes | `_read_source_sensitivity`/`_read_source_title` raise `ValueError`; caught by `main.py`'s unchanged `except (OSError, ValueError)` region |
| `on_progress` is an injected `ProgressHook`; spinner stays in CLI | ✅ Yes | No `Console`/spinner construction in `application/ingest.py`; `on_progress` forwarded unchanged at lines 362-368 |
| `converged_reingest` replaces the #773 mid-region `return` | ✅ Yes | Implemented per design's exact signature; adapter maps non-`None` to the same exit path, byte-identical per golden |
| Layering guard generalized, not copied | ✅ Yes | `test_layering.py` iterates `application/*.py` rather than a hardcoded module constant |
| S1 splits off with zero call-site repoints | ✅ Yes (per apply-progress, not independently re-verified) | Task 0.1/1.5 confirm zero call-site changes in Slice 1 |
| `SourceDocumentPlan` carries `raw_content`/`origin_key` beyond the design's literal signature | ⚠️ Documented deviation | Design's `compose_catalog_update` signature omits these; the shipped code needs them to rebuild the Source document on conditional re-render. Flagged honestly in apply-progress as necessary, not a design violation in substance — confirmed by reading the actual `compose_catalog_update` implementation, which does depend on `source.raw_content`/`source.origin_key` |
| Malformed-prior-frontmatter refusal wording changed (names `source_display_path` not the concept `Path`) | ⚠️ Documented deviation | `application/ingest.py` never holds a `Path` to the concept file (D2); no existing test pinned the old wording, so nothing broke. Not part of the 9 checkable spec scenarios |

### Issues Found

**CRITICAL**: None

**WARNING**: None. The two documented "Coherence" deviations above (`SourceDocumentPlan`'s extra fields, and the refusal-message wording change) are implementation necessities under D2's constraint, not test-breaking or spec-violating — no spec requirement or scenario references either the exact `SourceDocumentPlan` field list or the exact refusal wording, so these are non-blocking design notes rather than compliance gaps.

**SUGGESTION**:
- Issue #942 (`converged_reingest`'s `except ValueError:` cannot catch the `yaml.YAMLError` it documents) is confirmed OPEN on GitHub, confirmed pre-existing on `main` before this change (per its own body, verified against `git show main:src/openkos/cli/main.py` history), and confirmed correctly out of scope for this change's "no behavior change" contract. No action needed here; tracked separately.
- The `ingestion` delta spec (`openspec/changes/ingest-application-service/specs/ingestion/spec.md`) correctly carries zero `## Requirements` entries — confirmed by design ("this is a composition refactor... no delta is written") and by direct read of the file, which contains only a Purpose-paragraph replacement deferred to archive-time merge. This is by design, not an omission.

### Verdict

**PASS**

All 7 requirements and 9 scenarios are compliant with passing runtime tests; the full repository gate (6020 tests, ruff, mypy) is green; 10/10 characterization goldens are byte-identical to the pre-move tree in both git-config environments; all 44 tasks are complete and match the shipped code. Ready to archive.
