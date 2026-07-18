```yaml
schema: gentle-ai.verify-result/v1
verdict: pass
blockers: 0
critical_findings: 0
requirements: 7/7
scenarios: 10/10
test_command: uv run pytest --cov=openkos --cov-report=term-missing
test_exit_code: 0
test_output_hash: sha256:f8817893799f78de0fb8411f82962310e76ae729ed6673db558ac0e30220ac3f
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:8b09188cd4334b7a36b9f6dc1f7ef942a9f88d6aa8cc3cdbb7ae7841468e6997
```

# Verify Report: add-ollama-client

## Change
`add-ollama-client` (change #2 of MVP-1 `query` capability chain) — pure library seam for chat completion against a local Ollama server: `LLMBackend` Protocol + concrete `OllamaClient` over stdlib `urllib`. No CLI surface, dormant until `add-query-command` consumes it.

## Mode
Strict TDD. Full artifact set present: proposal, spec, design, tasks, apply-progress. All 21/21 tasks (9 phases) marked `[x]` in `tasks.md` — confirmed via direct read, zero unchecked boxes.

## Independent Gate Re-run (exact numbers, this session, branch `feat/add-ollama-client`)

| Command | Result |
|---|---|
| `uv run pytest --cov=openkos --cov-report=term-missing` | **367 passed**, 0 failed. `src/openkos/llm/base.py`: 9 stmts / 0 branch, **100%**. `src/openkos/llm/ollama.py`: 48 stmts / 6 branch, **100%, 0 missing**. Project TOTAL: 859 stmts, 224 branches, **98.61% coverage** ("Required test coverage of 90.0% reached. Total coverage: 98.61%") — above the enforced 90% floor. |
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | 39 files already formatted |
| `uv run mypy .` | Success: no issues found in 39 source files |

All numbers independently reproduced this session (not trusted from apply-progress) and match the apply-progress artifact exactly.

## Task Completeness
21/21 tasks across 9 phases checked `[x]`: foundation (1.1-1.3), success/request-shape RED+GREEN (2.x, 3.x), error-mapping RED+GREEN (4.x, 5.x), host-precedence RED+GREEN (6.x, 7.x), layering guard (8.x), verification gate (9.x). Spot-verified against real code, not just checkbox state — every task maps to passing tests in `tests/unit/llm/test_ollama.py` (17 tests, independently counted via `rg -c '^def test_'`, matches apply-progress claim exactly).

## Spec Conformance Matrix (7 requirements / 10 scenarios, 10/10 covered)

| Requirement | Scenario | Covering test(s) | Result |
|---|---|---|---|
| Successful Chat Call Returns Assistant Text | Chat call returns clean assistant text | `test_chat_success_returns_assistant_content` + `test_chat_request_body_disables_stream_and_think` | COMPLIANT |
| System And User Roles Supported | System and user messages both forwarded | `test_chat_preserves_system_and_user_messages_in_order` | COMPLIANT |
| Ollama Unavailable Raises A Typed Error | Server not running raises OllamaUnavailable | `test_connection_refused_raises_ollama_unavailable` | COMPLIANT |
| Ollama Unavailable Raises A Typed Error | Request timeout raises OllamaUnavailable | `test_request_timeout_raises_ollama_unavailable` | COMPLIANT |
| Unknown Model Raises A Typed Not-Found Error | Not-pulled model raises OllamaModelNotFound | `test_404_model_not_found_body_raises_ollama_model_not_found` | COMPLIANT |
| Other Failures Raise A Generic Typed Error | Non-404 server error raises OllamaError | `test_non_404_http_error_raises_ollama_error_with_detail` | COMPLIANT |
| Other Failures Raise A Generic Typed Error | Malformed JSON response raises OllamaError | `test_malformed_json_response_raises_ollama_error` + `test_missing_message_content_raises_ollama_error` | COMPLIANT |
| Model And Base URL Are Configurable | Default base URL used when no override given | `test_no_override_targets_default_localhost_url` | COMPLIANT |
| Model And Base URL Are Configurable | Base URL override is honored | `test_explicit_host_arg_overrides_env_and_default` | COMPLIANT |
| Testable Without A Live Ollama Server | Full behavior covered with the HTTP layer mocked | All 17 tests inject a fake `urlopen`; confirmed via `rg` scan of the test file for `socket.`/`requests.`/direct `urllib.request.urlopen(` calls — zero matches | COMPLIANT |

Extra coverage beyond the literal 10 scenarios (bonus triangulation, not a gap): `test_chat_request_targets_host_api_chat_with_model_and_messages`, `test_non_string_message_content_raises_ollama_error`, `test_ollama_host_env_overrides_default_when_no_arg_given`, `test_bare_host_port_is_normalized_with_http_scheme`, `test_message_typed_dict_holds_role_and_content`, `test_llm_modules_do_not_import_config` (17 tests total, all pass, all exercise real production code paths).

## Locked-Decision Conformance (D1-D6)

| Decision | Verification |
|---|---|
| D1 — `LLMBackend` Protocol + `Message` TypedDict | `base.py`: `class Message(TypedDict): role: str; content: str`; `class LLMBackend(Protocol): def chat(self, messages: Sequence[Message]) -> str: ...` — confirmed by source read |
| D2 — `OllamaClient(model, *, host=None, timeout=120.0, urlopen=urllib.request.urlopen)`, host precedence arg > `OLLAMA_HOST` env > default, bare `host:port` normalized | Constructor signature matches exactly; `_normalize_host` prepends `http://` when no scheme present; `resolved_host = host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST` — precedence order correct; 4 host tests pass (default, explicit-arg-over-env, env-over-default, bare-port-normalize) |
| D3 — injected `urlopen` seam, no live server | Every one of 17 tests passes a fake `urlopen`; independently confirmed via grep — zero `socket.connect`/`requests.`/un-injected `urllib.request.urlopen(` calls anywhere in the test file |
| D4 — exception hierarchy, `OllamaError` base; `OllamaUnavailable`/`OllamaModelNotFound` subclass it | Confirmed by source read: both subclass `OllamaError(Exception)`; `OllamaError` also raised directly for the generic/malformed cases |
| D5 — error mapping in catch order: `HTTPError` (404-"not found"→`OllamaModelNotFound`, else→`OllamaError`) caught **before** `(URLError, TimeoutError)`→`OllamaUnavailable`; JSON/missing-content→`OllamaError` | `chat()` source: `except urllib.error.HTTPError` precedes `except (urllib.error.URLError, TimeoutError)` — correct, since `HTTPError` subclasses `URLError` and must be caught first or every 404 would misreport as `OllamaUnavailable`. `_map_http_error` implements the 404/"not found" branch correctly. 6 error-mapping tests pass covering every branch |
| D6 — request body `{"model","messages","stream":false,"think":false}`, no `options`, `DEFAULT_TIMEOUT=120.0` | Confirmed by source read; `test_chat_request_body_disables_stream_and_think` asserts both flags in the sent JSON body |

All 6 design decisions hold in code, confirmed by source inspection plus passing runtime tests. Zero ADRs, as design specifies (additive, revertible).

## Non-Goals + Layering Respected
- **No CLI added**: `git status --short` shows exactly 3 untracked top-level paths (`openspec/changes/add-ollama-client/`, `src/openkos/llm/`, `tests/unit/llm/`) — no `cli/` files touched.
- **`config.py` untouched**: `git diff main -- src/openkos/config.py` → **empty**.
- **`ingest`/`forget`/`state` untouched**: `git diff main --stat -- src/openkos/cli src/openkos/state src/openkos/bundle` → **empty** (no output).
- **No streaming/tools/embeddings/retries/`/api/generate`/other-provider**: confirmed by source read of `ollama.py` — single `chat()` method, `stream:false` hard-coded, no `options` passthrough, no retry loop.
- **`llm` does not import `config`**: `test_llm_modules_do_not_import_config` (AST-based scan, not string grep) passes; independently confirmed via `rg -n "openkos\.config|from openkos import config" src/openkos/llm/*.py` → no matches.
- **Change scope**: `git diff main --stat` on tracked files → empty (zero tracked-file modifications); the only changes are 5 new untracked files totaling 467 lines (`llm/__init__.py` 9, `llm/base.py` 26, `llm/ollama.py` 109, `tests/unit/llm/__init__.py` 0, `test_ollama.py` 323) — matches apply-progress's reported ~467 lines and the tasks.md forecast's `size:exception` acceptance (forecast ~420, actual 467, both under the 400-line budget only via the accepted exception).

## Regression Check
Full suite: **367 passed**, 0 failed, 0 errors, 0 skipped. No pre-existing test broke — all prior 350 tests (cli/ingest/forget/lint/status/config/fsio/lint/main/bundle/state) still pass alongside the 17 new `llm/` tests.

## TDD Compliance
| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | Yes | Full RED/GREEN/Triangulate table found in apply-progress for all 9 task phases |
| All tasks have tests | Yes | 9/9 phase groups map to test files/cases in `test_ollama.py` |
| RED confirmed (tests exist) | Yes | `tests/unit/llm/test_ollama.py` exists, 17 tests collected (independently counted) |
| GREEN confirmed (tests pass) | Yes | 367/367 pass on independent re-run, including all 17 `test_ollama.py` cases |
| Triangulation adequate | Yes | Multiple cases per behavior (4 success/shape cases, 6 error-mapping cases, 4 host-precedence cases); single-case items (`Message` TypedDict, layering guard) are structurally single-scenario, honestly noted as such |
| Safety Net for modified files | N/A (new) | Every file under `llm/`/`tests/unit/llm/` is newly created — no pre-existing file was modified |

**TDD Compliance**: 6/6 checks passed

**Disclosed process deviation (non-blocking)**: apply-progress honestly documents that host-precedence logic (task 7.1) was implemented one phase earlier than task ordering suggests — task 2.4's URL-target test already required host resolution, so by the time tasks 6-7's dedicated RED tests were written the precedence logic was already correct. Those tests lock/triangulate existing-correct behavior rather than driving new code through a strict RED-first cycle. This is a process ordering note, not a spec or correctness gap — task 8.2 in `tasks.md` explicitly anticipates the analogous layering-guard case ("confirm guard passes with no production change"), so that one is expected behavior, not a deviation. Classified as SUGGESTION, not WARNING: fully disclosed, does not affect scenario compliance, and both areas remain triangulated and covered by passing tests.

## Assertion Quality Audit
Scanned all 17 tests in `test_ollama.py`. No tautologies, no ghost loops over possibly-empty collections, no assertions skipping production code calls, no smoke-test-only patterns (every test asserts a specific returned value, raised exception type, or captured request-body field). Mock/assertion ratio is reasonable throughout (1 fake `urlopen` per test, 1-4 value assertions each). `test_message_typed_dict_holds_role_and_content`'s single-case triangulation is correctly self-noted in a comment as intentional (purely structural `TypedDict`, one possible shape).

**Assertion quality**: All assertions verify real behavior. 0 CRITICAL, 0 WARNING.

## Issues
**CRITICAL**: None
**WARNING**: None
**SUGGESTION**: Host-precedence tests (Phase 6/7) and the layering guard (Phase 8) were written after the code they verify was already correct, rather than driving it through a strict RED-first cycle — honestly disclosed in apply-progress, does not affect spec/scenario compliance or coverage.

## Final Verdict: PASS

Requirements: 7/7 covered. Scenarios: 10/10 covered by passing runtime tests. Blockers: 0. Critical findings: 0.
