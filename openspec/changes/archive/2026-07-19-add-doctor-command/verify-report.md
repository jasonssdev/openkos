# Verification Report: `add-doctor-command`

**Change**: `add-doctor-command` — `openkos doctor` health check + `OllamaClient.list_models()`/`model_tag_matches()`
**Mode**: full artifacts (proposal, specs, design, tasks all present)
**Verdict**: **PASS WITH WARNINGS**

## Completeness

| Dimension | Status |
|---|---|
| Tasks | 30/30 checked (`[x]`), no unchecked items |
| Specs | 2 capability domains present, read in full |
| Design | present, all D1-D7 decisions matched in code |
| Bounded correction | applied and verified (3/3 items) |

## Test / Build / Coverage Evidence

| Command | Exit code | Result |
|---|---|---|
| `uv run pytest --cov=openkos --cov-report=term-missing` | 0 | 433 passed, 98.87% total coverage (threshold 90%); `ollama.py` 100%, `cli/main.py` 99% (2 pre-existing unrelated misses: line 379, 486->488, both outside `doctor`/`list_models`) |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 45 files already formatted |
| `uv run mypy .` | 0 | Success, no issues found in 45 source files |

## Bounded Correction — Verified

| Item | Severity fixed | Verified |
|---|---|---|
| (a) `list_models()` maps non-list/null `models` body to `OllamaError` family, not a bare `TypeError` | CRITICAL | Confirmed: `ollama.py` lines 149-159 wrap `json.loads(body)["models"]` AND the `for entry in entries` loop in one `try/except (json.JSONDecodeError, KeyError, TypeError, ValueError)`. Test: `test_list_models_non_list_models_value_raises_ollama_error` (parametrized `"null"`, `"42"`), `tests/unit/llm/test_ollama.py:556-567` — passes. |
| (b) doctor's reachability `OllamaClient` uses `timeout=5.0`, not the 120s default | WARNING | Confirmed: `cli/main.py:842` `client = OllamaClient(model=model, timeout=5.0)` with an explanatory comment about fast interactive-diagnostic preflight. Not asserted by a dedicated unit test (the fake client accepts and ignores `**kwargs`), but the literal source line is unambiguous and directly inspectable — see Suggestion S1 below. |
| (c) `CheckResult.status` is `typing.Literal["pass", "fail", "skip"]` | SUGGESTION | Confirmed: `cli/main.py:8` imports `Literal`, `cli/main.py:750` `status: Literal["pass", "fail", "skip"]`. `mypy --strict` clean. |

## Spec Compliance Matrix — `doctor-command` (6 requirements / 12 scenarios, all covered)

| Requirement | Scenario | Test | Status |
|---|---|---|---|
| Doctor Runs And Prints All Applicable Checks | Healthy workspace prints all applicable checks | `test_doctor_all_healthy_exits_zero` | PASS |
| | A failing check does not stop later checks from running | `test_doctor_later_check_still_prints_after_earlier_critical_failure` | PASS |
| Failed Checks Print Actionable Remediation | Ollama down shows a start-server remediation | `test_doctor_ollama_down_shows_start_server_remediation` | PASS |
| | Missing model shows a pull remediation naming the tag | `test_doctor_missing_model_shows_pull_remediation_with_exact_tag` | PASS |
| | Outside a workspace shows an init remediation | `test_doctor_outside_workspace_unhealthy_ollama_exits_one` | PASS |
| Exit Code Reflects Critical Failures Only | Informational-only failure still exits zero | `test_doctor_outside_workspace_healthy_exits_zero`, `test_doctor_bundle_findings_fail_but_stay_informational` | PASS |
| | Any critical failure causes exit one | `test_doctor_ollama_down_shows_start_server_remediation`, `test_doctor_missing_model_shows_pull_remediation_with_exact_tag` | PASS |
| Doctor Works Outside An Initialized Workspace | Unhealthy pre-init environment exits one | `test_doctor_outside_workspace_unhealthy_ollama_exits_one` | PASS |
| | Healthy pre-init environment exits zero | `test_doctor_outside_workspace_healthy_exits_zero` | PASS |
| Model-Installed Check Uses Tag-Normalized Matching | Bare configured tag matches a :latest installed entry | `test_model_tag_matches_bare_configured_matches_latest_installed` (llm-client unit level; doctor calls `model_tag_matches` directly, no extra logic) | PASS (see S2) |
| | Non-matching tag fails with pull remediation | `test_doctor_missing_model_shows_pull_remediation_with_exact_tag` + `test_model_tag_matches_no_match_returns_false` | PASS |
| Doctor Is Read-Only | Doctor run leaves the workspace unchanged | `test_doctor_run_leaves_workspace_unchanged` | PASS |

## Spec Compliance Matrix — `llm-client` (2 requirements / 7 scenarios, all covered)

| Requirement | Scenario | Test | Status |
|---|---|---|---|
| List Installed Models | Reachable server returns installed tags | `test_list_models_returns_installed_tags_from_model_field` | PASS |
| | Unreachable server raises OllamaUnavailable | `test_list_models_unreachable_raises_ollama_unavailable`, `test_list_models_body_read_failure_raises_ollama_unavailable` | PASS |
| | Non-200 or malformed response raises OllamaError | `test_list_models_non_200_raises_ollama_error`, `test_list_models_malformed_json_raises_ollama_error`, `test_list_models_non_list_models_value_raises_ollama_error` (bounded-correction test) | PASS |
| Model Tag Matching Tolerates Bare And Latest-Qualified Tags | Bare configured tag matches a :latest installed entry | `test_model_tag_matches_bare_configured_matches_latest_installed` | PASS |
| | Exact tag match | `test_model_tag_matches_exact_match` | PASS |
| | Installed entry exposes its tag only under name | `test_list_models_falls_back_to_name_field` (see W1) | PASS |
| | No matching entry returns False | `test_model_tag_matches_no_match_returns_false` | PASS |

**Requirements**: 8/8. **Scenarios**: 19/19, all covered by a passing runtime test.

## Design Coherence — D1-D7

All seven decisions (D1 `list_models()` as an `OllamaClient` method; D2 defensive `model`/`name` field read; D3 `model_tag_matches` module-level pure function; D4 case-sensitive bare-to-`:latest` normalization; D5 `CheckResult`/`_render_check` accumulate-then-exit; D6 SKIP-blocked model check when unreachable; D7 criticality split) are matched verbatim in `src/openkos/llm/ollama.py` and `src/openkos/cli/main.py`. The `timeout=5.0` line in `doctor()` deviates from the design's literal code skeleton (which shows `OllamaClient(model=model)` with no timeout override) — this is the accepted bounded correction (b), not an unreviewed drift.

## Other Checks

- `test_llm_modules_do_not_import_config` passes — `llm/ollama.py` leaf discipline confirmed (no `openkos.config` import; only `openkos.llm.base`, stdlib `http.client`/`json`/`os`/`urllib`).
- `docs/cli.md` — `### openkos doctor` section present (lines 121-137): 5 checks, `[PASS]`/`[FAIL]`/`[SKIP]` format, remediation lines, criticality split, outside-workspace behavior, exit-code contract.
- `docs/roadmap.md` line 46 — MVP-1 command list includes `doctor` (6→7 commands).
- Accumulate-then-exit-once flow structurally confirmed: `doctor()` appends every `CheckResult` to a list, renders the full list unconditionally (lines 910-913), and performs a single `typer.Exit(code=1)` check only after the render loop (lines 915-916) — no early `raise`/`return` inside any check branch.
- Exit-code/criticality contract confirmed: `if any(r.status == "fail" and r.critical for r in results)` — informational checks (`workspace-initialized`, `bundle-readable`) are constructed with `critical=False` and never gate the exit code (proven by `test_doctor_outside_workspace_healthy_exits_zero` and `test_doctor_bundle_findings_fail_but_stay_informational`).

## Issues

### CRITICAL
None.

### WARNING
- **W1 — spec wording misattributes field-defensive reading.** The `llm-client` spec's "Model Tag Matching Tolerates Bare And Latest-Qualified Tags" requirement states `model_tag_matches` "MUST read each installed entry defensively, preferring a `model` field and falling back to a `name` field" — but the actual `model_tag_matches(configured: str, installed: list[str])` signature (both spec's own snippet reference and the implementation) takes already-extracted string tags, not dict entries. The `model`/`name` defensive read is performed by `list_models()`, not `model_tag_matches()` (confirmed in `design.md`'s own interface snippets, which correctly separate the two). Code and design are internally consistent and correctly tested (`test_list_models_falls_back_to_name_field`); only the spec prose in `specs/llm-client/spec.md` misattributes the behavior to the wrong function. Recommend a documentation-only spec correction before or during archive; does not block archive.

### SUGGESTION
- **S1** — No test asserts the `timeout=5.0` kwarg is actually forwarded to `OllamaClient(...)` inside `doctor()` (the fake `_FakeOllamaClient.__init__` accepts and discards `**kwargs`). The fix is verified by direct source inspection only. A lightweight test capturing the constructor kwargs would close this gap for regression safety, mirroring `test_custom_timeout_is_forwarded_to_urlopen` in `test_ollama.py`.
- **S2** — The "Bare configured tag matches a :latest installed entry" scenario under the `doctor-command` domain is proven only via the `llm-client`-level unit test of the underlying pure function, not via a dedicated CLI-level `doctor` scenario. Functionally sound (doctor's call site is a straightforward one-line delegation, covered structurally by the 99%-branch `cli/main.py` coverage), but a CLI-level fixture exercising a bare configured tag against a `:latest`-suffixed installed entry would make the doctor-command scenario self-contained.

## Final Verdict

**PASS WITH WARNINGS** — full test suite green (433/433), static analysis clean (ruff, mypy strict), all 30 tasks complete, all 19 spec scenarios across both capability domains covered by passing runtime tests, and all three bounded-correction items (CRITICAL, WARNING, SUGGESTION) confirmed present and correct. One documentation-only spec-wording issue (W1) and two test-coverage-hardening suggestions (S1, S2) remain — none block archive.

**Next recommended**: `sdd-archive` (after merge).
