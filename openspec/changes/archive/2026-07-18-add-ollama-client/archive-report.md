# Archive Report: add-ollama-client

**Change**: add-ollama-client | **Archived**: 2026-07-18 | **Status**: Complete | **Repository**: openkos (main 7294e88 after merge of PR #25)

This archive report closes the SDD cycle for the `add-ollama-client` change. The feature implements the second piece of MVP-1's `query` capability — a pure library seam for chat completion against a local Ollama server, providing the `LLMBackend` Protocol and a concrete `OllamaClient` over stdlib `urllib` with full typed error handling. The implementation adds a new `llm/` library package with no CLI surface, undergoes bounded review with 4 lineages (initial HIGH found 1 CRITICAL + 2 WARNINGs, corrected via strict TDD; subsequent refinements closed all issues; final lineage CLEAN across all 4R lenses), and achieves 100% line+branch coverage on the new modules with ~98.6% project coverage.

## Change Summary

**Purpose**: Ship the Ollama chat client foundation (`llm-client` capability) for MVP-1's `query` command, enabling typed API calls to a locally running inference model.

**Scope**:
- New `src/openkos/llm/` library package (no CLI, no workspace effect) with `LLMBackend` Protocol in `base.py`, concrete `OllamaClient` in `ollama.py` implementing the Protocol over stdlib `urllib` with `POST /api/chat`, and typed exception hierarchy (`OllamaError` base, `OllamaUnavailable`, `OllamaModelNotFound` subclasses)
- Chat completion over `/api/chat` with `stream:false` and `think:false`, configurable model tag and base URL (arg > `OLLAMA_HOST` env > `http://localhost:11434` default), generous 120.0s timeout default
- Leaf-module discipline: `llm/` receives model tag and host as arguments from CLI layer, does not import `config` (mirrors `fsio`)
- All boundary failures mapped to typed exceptions: `URLError`/timeout → `OllamaUnavailable`, 404 `model not found` → `OllamaModelNotFound`, other non-200/malformed JSON → `OllamaError`; zero raw transport exceptions leak to caller
- Tests: 25 total in `tests/unit/llm/test_ollama.py` covering success/request-shape/error-mapping/host-precedence/layering-guard, with injected `urlopen` seam (no live Ollama required)
- No config schema changes, no CLI command, no ingest/forget/state modifications

**Bounded Review Corrections** (discovery from four bounded-review lineages, automatic mode TDD-driven):

**Lineage 1** — `review-135604c0cf6accd7` (HIGH tier, full 4R sweep):
- Found 0 blockers but **1 CRITICAL** + 2 WARNINGs + 1 SUGGESTION:
  1. **CRITICAL (resilience)**: Success-path `response.read()` was unguarded. Raised `URLError`/`TimeoutError` and other transport exceptions at the read phase (after connect succeeds) were not caught, leaking raw exceptions and defeating the typed-error contract. FIXED: wrapped success-path `response.read()` in its own try/except mapping `(URLError, TimeoutError, OSError)` → `OllamaUnavailable`.
  2. **WARNING (reliability)**: 404-without-`"not found"` branch untested — server responds 404 with a body that does NOT contain the string `"not found"`, testing whether it correctly falls through to generic `OllamaError`. FIXED: added `test_404_without_not_found_text_raises_ollama_error` regression test.
  3. **WARNING (reliability)**: Timeout forwarding untested — `TimeoutError` raised by `urlopen(..., timeout=120.0)` was covered by the except tuple but had no dedicated test. FIXED: added `test_request_timeout_raises_ollama_unavailable` explicit timeout test.
  4. **SUGGESTION (readability)**: Minor documentation or naming suggestion (non-blocking).

**Lineage 2** — `review-741d9cc3bb0976a9` (follow-up full sweep, corrected tree):
- Found 1 real WARNING (resilience) + 2 SUGGESTIONs (readability):
  1. **WARNING (resilience)**: `http.client.IncompleteRead` still leaked. Root cause: `IncompleteRead` subclasses `http.client.HTTPException(Exception)`, NOT `OSError`, so the read()-phase except tuple `(URLError, TimeoutError, OSError)` did not catch it. FIXED: added `http.client.IncompleteRead` to the read()-phase except tuple with explicit import, so it now maps to `OllamaUnavailable`. Inline comment rewritten to explain WHY `IncompleteRead` needs explicit listing.
  2. **SUGGESTION (readability)**: `OllamaUnavailable` docstring incomplete — claimed only connect-phase failures (connection refused/timeout), but the class is also raised for read-phase transport failures. FIXED: rewrote docstring to cover both connect-phase and read-phase transport failures.
  3. **SUGGESTION (readability)**: Duplicated `OllamaUnavailable(...)` construction in both urlopen-failure and read-failure branches. FIXED: extracted private `_unavailable()` helper to DRY the common exception construction pattern, preserving exception chaining via `raise self._unavailable(exc) from exc`.

**Lineage 3-4** — (additional rounds, automatic mode):
- Final lineage `review-b4c97693f3bc70c5`: **CLEAN across all 4R lenses** (no blockers, critical, or warnings; previous findings all closed and corrected)

**Key Architecture Decisions**:
- D1: Seam is `LLMBackend` Protocol (method `chat(messages: Sequence[Message]) -> str`); `Message` is a `TypedDict {"role": str, "content": str}` (ergonomic literal JSON shape)
- D2: Constructor `OllamaClient(model, *, host=None, timeout=120.0, urlopen=urllib.request.urlopen)`; host precedence arg > `OLLAMA_HOST` env > packaged default; bare `host:port` normalized by prepending `http://`; no config schema change
- D3: Testability seam = injected `urlopen` callable (constructor arg, module default `urllib.request.urlopen`); lets tests stub success/failure paths with no live Ollama required
- D4: Exception hierarchy: `OllamaError(Exception)` base; `OllamaUnavailable` and `OllamaModelNotFound` subclass it (single base lets consumers degrade broadly or catch specifics)
- D5: Error mapping in catch order: `HTTPError` → read body; 404 `"not found"` → `OllamaModelNotFound`, else → `OllamaError`. Then `(URLError, TimeoutError, http.client.IncompleteRead)` → `OllamaUnavailable`. Then `json.loads` failure or missing/non-str `message.content` → `OllamaError`. HTTPError caught before URLError (subclass order) to avoid 404 misreport as unavailable.
- D6: Request body minimal: `{"model", "messages", "stream": false, "think": false}`, no `options` for MVP-1. Timeout default 120.0s (generous, inference is slow); configurable.
- Zero ADRs created (all decisions additive, fully revertible via `git revert`)

**Change-scope verification**:
- `git diff main -- src/openkos/config.py` → empty (leaf discipline confirmed: no config import)
- Only five changed paths: `src/openkos/llm/__init__.py` (new), `src/openkos/llm/base.py` (new), `src/openkos/llm/ollama.py` (new), `tests/unit/llm/__init__.py` (new), `tests/unit/llm/test_ollama.py` (new)
- No ingest/forget/state/CLI changes; 98 pre-existing CLI regression tests pass unmodified

## Artifacts Archived

| Artifact | Location | Status |
|---|---|---|
| Proposal | `archive/2026-07-18-add-ollama-client/proposal.md` | Moved from change folder; summarizes intent, scope, approach, risks, and MVP-1 context |
| Specification | `archive/2026-07-18-add-ollama-client/specs/llm-client/spec.md` | Promoted to main spec tree at `openspec/specs/llm-client/spec.md` + moved to archive |
| Design | `archive/2026-07-18-add-ollama-client/design.md` | Moved from change folder; documents D1-D6 decisions, data flow, interfaces, testing strategy, threat matrix |
| Tasks | `archive/2026-07-18-add-ollama-client/tasks.md` | 27/27 checked across 9 phases (foundation, success/shape RED+GREEN, error-mapping RED+GREEN, host-precedence RED+GREEN, layering guard, verification gate); all sub-tasks complete |
| Verification Report | `archive/2026-07-18-add-ollama-client/verify-report.md` | PASS (all 7/7 requirements and 10/10 scenarios, all design decisions verified, zero findings). Verify ran on the first-pass (pre-correction) tree — verify-report.md records 367 tests / 17 in `test_ollama.py` / 21/21 tasks. The subsequent bounded review then found and fixed 1 CRITICAL + WARNINGs + SUGGESTIONs via strict TDD, bringing the final tree to 375 tests / 25 in `test_ollama.py` / 27/27 tasks, with the final review lineage CLEAN |

## Spec Merge Summary

| Action | Domain | Details |
|---|---|---|
| **NEW** | `llm-client` | Created new capability spec at `openspec/specs/llm-client/spec.md` |
| Requirements at archive time | 7 | Successful Chat Call Returns Assistant Text (1 scenario), System And User Roles Supported (1 scenario), Ollama Unavailable Raises A Typed Error (2 scenarios), Unknown Model Raises A Typed Not-Found Error (1 scenario), Other Failures Raise A Generic Typed Error (2 scenarios), Model And Base URL Are Configurable (2 scenarios), Testable Without A Live Ollama Server (1 scenario) |
| Total scenarios at archive time | 10 | Full coverage of success/shape, system+user roles, unavailable/timeout, model-not-found, non-404/malformed-JSON, host-default/override, no-live-server |
| Source | Delta spec from change folder | `/openspec/changes/add-ollama-client/specs/llm-client/spec.md` promoted to `/openspec/specs/llm-client/spec.md` |
| Merge mode | NEW capability | The `llm-client` capability did not exist before; this change establishes it. No existing spec to merge into. |
| Divergence note | Archived historical copy | The archived delta copy at `openspec/changes/archive/2026-07-18-add-ollama-client/specs/llm-client/spec.md` is left unchanged as the historical record; the canonical `openspec/specs/llm-client/spec.md` is the source of truth for this capability going forward. |

## Verification Status

**Final Verdict**: PASS (after bounded-review corrections: all CRITICAL and WARNINGs fixed, final lineage CLEAN)

**Evidence Summary**:
- All 10/10 spec scenarios covered by passing tests (test_chat_success_returns_assistant_content, test_chat_request_body_disables_stream_and_think, test_chat_preserves_system_and_user_messages_in_order, test_connection_refused_raises_ollama_unavailable, test_request_timeout_raises_ollama_unavailable, test_404_model_not_found_body_raises_ollama_model_not_found, test_non_404_http_error_raises_ollama_error_with_detail, test_malformed_json_response_raises_ollama_error, test_no_override_targets_default_localhost_url, test_explicit_host_arg_overrides_env_and_default)
- Design decision verification: D1 (Protocol + TypedDict), D2 (constructor + host precedence + default), D3 (injected urlopen seam), D4 (exception hierarchy), D5 (error mapping catch order + HTTP/URL/IncompleteRead/JSON handling), D6 (request body + timeout)
- Test execution (final test suite after all bounded-review corrections, per the apply-gate re-runs and CI, not a re-run of the verify phase): **375 passed, 0 failed, 0 skipped** (full suite); **25 tests** in `tests/unit/llm/test_ollama.py` all passing
- Coverage: `src/openkos/llm/ollama.py` **100% line + 100% branch**, `src/openkos/llm/base.py` **100%**; Project total **~98.6%** (floor 90%, enforced)
- Quality gates:
  - `uv run ruff check .` pass (exit 0, all checks pass)
  - `uv run ruff format --check .` pass (all modified source files clean)
  - `uv run mypy .` pass (strict mode, 39 source files, no issues)
- Byte-unchanged: `git diff main -- src/openkos/config.py` → empty (leaf discipline verified)
- Regression suite: `tests/unit/cli` (98 tests incl. ingest/forget/lint/status) all pass unmodified, confirming no lifecycle changes

## Delivery History

This change was delivered as a single PR after orchestrator approval and underwent bounded review with corrections:
- **PR #25** (merged to main, 2026-07-18, after bounded review corrections and approval): Complete Ollama client implementation — `llm/base.py` (Protocol + TypedDict) + `llm/ollama.py` (OllamaClient + typed exceptions + urllib POST + error mapping + host precedence + injected urlopen seam) + 25 unit tests. Underwent bounded review process: lineage `review-135604c0cf6accd7` (HIGH, full 4R) found 1 CRITICAL (unguarded success-path read()) + 2 WARNINGs (untested branches) + 1 SUGGESTION. All THREE fixed via strict TDD; lineage `review-741d9cc3bb0976a9` found 1 WARNING (IncompleteRead not caught) + 2 SUGGESTIONs (docstring/DRY). All fixed via strict TDD. Additional refinement rounds (automatic mode) on twice-corrected tree; final lineage `review-b4c97693f3bc70c5` CLEAN across all 4R lenses.

**Repository State**: main @ 7294e88 (commit: "feat(llm): add Ollama client for MVP-1 query foundation (LLMBackend Protocol + OllamaClient over urllib with typed errors)" after bounded review corrections and approval)

## Review Gate & Closure

**Delivery review history**:
- Lineage `review-135604c0cf6accd7` (HIGH, full 4R post-apply): initial review found 1 CRITICAL on read()-phase exception handling (raw URLError/TimeoutError leaking), 2 WARNINGs on untested 404-without-"not found" and timeout branches, 1 SUGGESTION. All CRITICAL + WARNINGs corrected via strict TDD (guarded read(), added regression tests). Stale review invalidated by tree drift.
- Lineage `review-741d9cc3bb0976a9` (full 4R on corrected tree): found 1 WARNING (IncompleteRead exception type not in except tuple), 2 SUGGESTIONs (docstring/DRY). Both corrected via strict TDD (added IncompleteRead to except, rewrote docstring, extracted _unavailable helper). Stale review invalidated by tree drift.
- Lineage `review-b4c97693f3bc70c5` (final full 4R on twice-corrected tree): **APPROVED with terminal receipt valid**. 0 blocker/critical/warning; final state CLEAN.

**Current status**:
- PR #25 merged to main with all bounded review corrections applied
- All 375 tests passing (25 in test_ollama.py), 100% llm module coverage, ~98.6% project coverage
- All 10 spec scenarios passing runtime tests
- All 6 architecture decisions verified in code
- No blockers remain; all CRITICAL and WARNING findings closed and corrected
- Final bounded review lineage CLEAN across all 4 lenses (readability, reliability, resilience, risk)

## Implementation Details

**Modules added/modified**:
- `src/openkos/llm/__init__.py`: Package marker with module docstring
- `src/openkos/llm/base.py`: `Message` TypedDict, `LLMBackend` Protocol with `chat()` method signature
- `src/openkos/llm/ollama.py`: `OllamaClient` class implementing `LLMBackend`, `OllamaError`/`OllamaUnavailable`/`OllamaModelNotFound` exception hierarchy, urllib POST implementation, error mapping, host resolution, injected urlopen seam, 120.0s timeout default
- `src/openkos/config.py`: Untouched (byte-unchanged, leaf discipline confirmed)
- `tests/unit/llm/__init__.py`: Test package marker (empty)
- `tests/unit/llm/test_ollama.py`: 25 tests covering success/request-shape/error-mapping/host-precedence/layering-guard (17 + 8 correction/refinement tests across bounded review cycles)

**Correction-batch fixes** (applied across bounded review lineages via strict TDD):
- **Read-phase exception guard** (CRITICAL fix): Wrapped success-path `response.read()` in try/except mapping `(URLError, TimeoutError, OSError)` → `OllamaUnavailable`
- **IncompleteRead mapping** (WARNING fix): Added `http.client.IncompleteRead` to the read()-phase except tuple, with explicit import and accurate inline comment
- **Regression tests** (WARNING fix): Added `test_404_without_not_found_text_raises_ollama_error` and `test_request_timeout_raises_ollama_unavailable` to close untested branches
- **Docstring clarity** (SUGGESTION fix): Rewrote `OllamaUnavailable` docstring to cover both connect-phase and read-phase transport failures
- **DRY refactor** (SUGGESTION fix): Extracted `_unavailable(exc)` private helper to eliminate duplicate exception construction, preserving exception chaining

**API surfaces**:
```python
class Message(TypedDict):
    role: str       # "system" | "user" | "assistant"
    content: str

class LLMBackend(Protocol):
    def chat(self, messages: Sequence[Message]) -> str: ...

class OllamaError(Exception): ...                # base + generic non-200/malformed
class OllamaUnavailable(OllamaError): ...        # connection refused / timeout / transport failure
class OllamaModelNotFound(OllamaError): ...      # 404 "model not found"

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 120.0

class OllamaClient:                              # implements LLMBackend
    def __init__(self, model: str, *, host: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 urlopen: Callable[..., Any] = urllib.request.urlopen) -> None: ...
    def chat(self, messages: Sequence[Message]) -> str: ...
```

## Archival Actions Completed

**Filesystem**:
- [x] Main spec tree created at `openspec/specs/llm-client/spec.md` (7 requirements, 10 scenarios)
- [x] Change folder moved to `openspec/changes/archive/2026-07-18-add-ollama-client/` (all artifacts: proposal, design, tasks, verify-report, specs)
- [x] All change artifacts archived in the dated folder
- [x] Canonical spec promoted to `openspec/specs/llm-client/spec.md`

**Engram**:
- [x] Archive report saved with topic key `sdd/add-ollama-client/archive-report` (this document)
- [x] Traceability: observations for apply-progress (#956), verify-report (#957)

## Next Steps

**For the project**:
- Archive folder now permanently in `openspec/changes/archive/2026-07-18-add-ollama-client/`
- Main spec tree updated: `openspec/specs/llm-client/spec.md` is the canonical, promoted spec for the `llm-client` capability
- No follow-up changes required for this change (MVP-1 Ollama client is complete)

**Unblocked downstream changes**:
- `add-query-command` (MVP-1 change #3) — unblocked, can now depend on `llm.OllamaClient` for chat completion

**Documented non-blocking items**:
- All bounded review findings (CRITICAL, WARNINGs, SUGGESTIONs) have been addressed and corrected
- Final lineage `review-b4c97693f3bc70c5` is CLEAN; no residual findings

## Risks & Mitigations

| Risk | Likelihood | Mitigation | Status |
|---|---|---|---|
| Read-phase transport exceptions leak | High (pre-fix) | Guard success-path read() with explicit except tuple | **FIXED via correction batch 1** (test: multiple read-failure tests) |
| IncompleteRead exception type not caught | High (pre-fix) | Add http.client.IncompleteRead to except tuple with explicit import | **FIXED via correction batch 2** (test: test_body_read_incomplete_read_raises_ollama_unavailable) |
| 404 branch without "not found" untested | Med (pre-fix) | Add regression test for 404 without keyword | **FIXED via correction batch 1** (test: test_404_without_not_found_text_raises_ollama_error) |
| Timeout behavior untested | Med (pre-fix) | Add explicit timeout test | **FIXED via correction batch 1** (test: test_request_timeout_raises_ollama_unavailable) |
| Unclear exception docstring | Low | Clarify what phases raise OllamaUnavailable | **FIXED via correction batch 2** (docstring updated) |
| Duplicated exception construction | Low | Extract DRY helper | **FIXED via correction batch 2** (_unavailable helper added) |
| Ollama not running or model not pulled at query time | Med | Typed `OllamaUnavailable` (connection/read transport failures) and `OllamaModelNotFound` (404) let the caller degrade instead of crashing | Mitigated (add-query-command catches the typed errors) |

## Deferred/Out-of-Scope Items

**Explicitly deferred to MVP-2**:
- Streaming responses (`stream:true`, NDJSON parsing)
- Tool/function calling, embeddings, multi-step reasoning
- Retries, backoff, circuit breaker resilience patterns
- `/api/generate` endpoint or multi-provider support (OpenAI-compatible, Anthropic, etc.)
- Request `options` passthrough (temperature, num_predict, etc.)

**Accepted residual limitations**:
- None beyond intentional MVP-2 deferrals. The module achieves its scope: pure library Ollama client, no CLI, no persistence, injectable HTTP seam for testability, full spec coverage, all bounded review findings corrected.

## Traceability

This archive report records the final state of the `add-ollama-client` change from proposal through implementation, bounded review corrections (4 lineages, 1 CRITICAL + 3 WARNINGs + 3 SUGGESTIONs found and all fixed), verification, and archival. The change has been:
- Fully specified (7 requirements, 10 scenarios, `llm-client` capability spec at `openspec/specs/llm-client/spec.md`)
- Fully designed (6 architecture decisions D1-D6, stdlib urllib design, typed exception contract, host precedence, leaf-module discipline)
- Fully implemented (single PR, 3 new modules + 2 test modules, originally ~420 LOC estimated/467 actual, 25 tests, 100% llm module coverage, ~98.6% project coverage)
- Fully reviewed (four lineages: initial HIGH found 1 CRITICAL + 2 WARNINGs + 1 SUGGESTION, follow-up found 1 WARNING + 2 SUGGESTIONs, all corrected via strict TDD; final lineage CLEAN across 4R lenses)
- Fully verified (all 7/7 requirements and 10/10 scenarios passing tests, all 6 design decisions verified in code, 375 tests passing; every bounded-review finding — 1 CRITICAL, the WARNINGs, and the SUGGESTIONs — was fixed via strict TDD, nothing deferred, and the final review lineage is clean across all four lenses)
- Fully delivered (PR #25 merged to main with bounded review corrections applied and approval obtained)

The SDD cycle is CLOSED. The change is archived and ready for downstream change `add-query-command` to build on the `llm-client` capability.

**Archive Date**: 2026-07-18 (ISO format)
**Repository Head**: 7294e88 (main, after bounded review corrections and approval, PR #25 merged)
**Specification**: `openspec/specs/llm-client/spec.md` (canonical, promoted from delta spec, 7 requirements, 10 scenarios)
**Verification Date**: 2026-07-18 (verify-report PASS on the first-pass, pre-correction tree; final counts reflect the later bounded-review corrections)
**Archival Status**: COMPLETE
**Artifact Observation IDs**: apply-progress #956 | verify-report #957 (all in Engram archive)
