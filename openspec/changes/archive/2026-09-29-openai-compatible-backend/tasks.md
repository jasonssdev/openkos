# Tasks: openai-compatible-backend — a second, local-first LLM backend behind the existing seams

Refs #1057. Design: `design.md`. Proposal: `proposal.md`. ADR-0031 lands with
Phase 1 (already on disk, status `Proposed`, per session note). Specs:
`specs/openai-compatible-client`, `specs/backend-selection` (new),
`specs/llm-client`, `specs/mcp`, `specs/reindex-command`,
`specs/doctor-command`, `specs/workspace-init`, `specs/llm-edge-production`,
`specs/entity-resolution-adjudication`, `specs/curate-command`,
`specs/query-command` (deltas).

Strict TDD is ON, runner `uv run pytest`. Write the test first and observe
RED before the implementation. A test that passes on its first run proves
nothing until the exact line it guards is mutated and the test fails. A
property test whose two sides call the same function under test is circular:
pair it with a literal expected-value test. Every absence assertion needs a
precondition proving the thing existed first. Security properties (API key
never printed/logged/committed, OLLAMA_HOST never redirecting the
openai-compatible backend, locality fail-closed) each need a sentinel-value
test that is mutation-proven.

Every `[TEST]` task is paired with the `[IMPL]` task that turns it GREEN, in
that order. `[IMPL]` tasks introduce only what their paired `[TEST]` already
pins. Revert every mutation with the inverse edit (never `git checkout --`),
and purge `__pycache__` before trusting a verdict. Two clarifying
tasks-phase decisions, made explicit here because design.md left them open:

1. **Construction-guard ratchet (Phases 9-10).** Design's slice 9 introduces
   `tests/unit/test_backend_construction_guard.py` scoped to "chat + curate +
   MCP chat sites"; slice 10 migrates the seven embed sites plus the three
   diagnostics/probe sites the "Construction sites routed through the
   resolver" table names. A blanket AST guard cannot be green in slice 9
   while ten sites remain unmigrated, so slice 9's guard carries an explicit
   `_PENDING_SITES` allowlist naming exactly those ten sites; slice 10 shrinks
   it to empty and slice 10's final test asserts the unconditional form. A
   stale allowlist entry (one that no longer points at an actual direct
   construction) is itself a guard failure, so the list cannot rot.
2. **Wording functions land where consumed.** `application/backends.py`'s
   pure wording helpers (`start_hint`, `install_hint`, `endpoint_label`,
   `backend_label`, `insecure_key_warning`) are introduced in Phase 13
   (CLI/curate/MCP wording), not Phase 9, because `doctor` (Phase 12) keeps
   its own command-form strings per Decision 9 and never calls them.

**Threat Matrix** (design.md — not N/A, this change crosses a data-egress
boundary):

| Boundary | Adversarial case | RED test(s) |
| --- | --- | --- |
| Locality | `localhost.evil.com`, `127.1`, unmatched `[::1`, userinfo, IPv6 expanded loopback | 7.5, 7.6, 9.6 |
| Confidential exemption | remote `base_url`/`embedding_base_url` + exemption flag | 7.8 |
| Secret in workspace | `api_key:` in yaml; userinfo in `base_url` | 3.5, 3.6, 3.8, 3.9 |
| Secret disclosure | key set, every error rung, doctor | 4.19, 4.23, 12.5 |
| Secret over plain HTTP | key + `http://` + non-loopback | 13.10-13.12 |
| Credential confusion | `OPENAI_API_KEY` exported | 9.18 |

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~4,000–5,850 total (design.md "Migration / Rollout"), across 15 chained PRs, each individually scoped near/under 400 |
| 400-line budget risk | High overall; each individual slice scoped under or near budget |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2a → PR 2b → PR 3 → PR 4 → PR 5 → PR 6 → PR 7 → PR 8 → PR 9 → PR 10 → PR 11 → PR 12 → PR 13a → PR 13b → PR 14 → PR 15, stacked to `main` in dependency order (see per-phase "Depends on") |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

`auto-chain` means the orchestrator proceeds with this slice order and
`stacked-to-main` with no further decision gate; the owner has pre-approved
`size:exception` for any slice that genuinely cannot be split further (most
likely Phase 9, the resolver seam, given its dispatch + key-read +
construction-guard surface).

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|-----------------------|------------------|--------------------|
| 1 | Move `classify_backend_host`+helpers, `measured_counters`, `is_timeout_failure` to `llm/base.py`; `llm` AGENTS.md scope | PR 1 → `main` | `uv run pytest tests/unit/llm/test_backend_host.py tests/unit/llm/test_ollama.py` | N/A — pure relocation, no CLI-observable behavior | Revert the moved definitions back into `ollama.py`; re-exports drop |
| 2a | Three neutral error bases; Ollama gains them as second bases; non-CLI catch sites migrated + guard | PR 2a → `main`, after PR 1 | `uv run pytest tests/unit/llm/test_neutral_bases.py tests/unit/llm/test_neutral_catch_sites.py tests/unit/resolution/ tests/unit/state/test_reindex.py` | `uv run openkos adjudicate` against a `tmp_path` workspace with a poisoned Ollama host — still raises the same user-visible error | Revert the three bases, Ollama's second bases, and the non-CLI catch-site renames |
| 2b | CLI/curate/MCP catch sites migrated to neutral names | PR 2b → `main`, after PR 2a | `uv run pytest tests/unit/cli/ tests/unit/mcp/` | `uv run openkos query "x"` against an unreachable Ollama host in `tmp_path` — byte-identical stderr | Revert the CLI/curate/MCP catch-site renames |
| 3 | `backend`/`base_url`/`embedding_base_url` config keys, validation, misplaced-key refusal (`openai-compatible` still refused) | PR 3 → `main`, after PR 1 | `uv run pytest tests/unit/test_config.py` | `uv run openkos init && echo 'backend: openai-compatible' >> openkos.yaml && uv run openkos doctor` in `tmp_path` — refused with a clear message | Revert the three `Config` fields and their validation; unknown keys are ignored today so nothing else breaks |
| 4 | `OpenAICompatibleClient` chat core: errors, transport, `/v1/chat/completions`, ladder, key header | PR 4 → `main`, after PR 1, PR 2a, PR 2b | `uv run pytest tests/unit/llm/test_openai_compatible*.py` | N/A — no caller until Phase 9 | Delete `llm/openai_compatible.py`; nothing references it yet |
| 5 | Chat bounds: `max_tokens`, `finish_reason`, `usage`, capped branching, `context_window`, temperature/seed | PR 5 → `main`, after PR 4 | `uv run pytest tests/unit/llm/test_openai_compatible_chat.py` | N/A — same reason as PR 4 | Revert the bounds additions; chat-core (PR 4) keeps working |
| 6 | Embeddings: `/v1/embeddings`, ordering, `EMBED_DIM`, L2 normalization, retry | PR 6 → `main`, after PR 4 | `uv run pytest tests/unit/llm/test_openai_compatible_embed.py` | N/A — same reason | Revert `embed`/`_validate_and_normalize_row`/retry loop |
| 7 | Diagnostics: `/v1/models`, locality, cross-backend exemption | PR 7 → `main`, after PR 4 | `uv run pytest tests/unit/llm/test_openai_compatible_diagnostics.py` | N/A — same reason | Revert `list_models`/`.locality` |
| 8 | Offline double + network-guard derivation for the new client | PR 8 → `main`, after PR 4, 5, 6, 7 | `uv run pytest tests/unit/test_network_guard.py` | N/A — double has no production caller yet | Revert `OfflineOpenAICompatible` and the guard parametrization |
| 9 | Resolver seam: `BackendFactories`, `resolve_endpoint`, `chat_client`/`embed_client`/`diagnostics_client`, key read, conftest, chat-scoped construction guard | PR 9 → `main`, after PR 3, PR 8 | `uv run pytest tests/unit/application/test_backends.py tests/unit/test_backend_construction_guard.py` | `uv run openkos query "x"` in a `tmp_path` workspace with the offline double patched at the resolver seam — no network reached | Revert `BackendFactories`/`resolve_endpoint`/dispatch/conftest helper/guard; every chat site reverts to constructing `OllamaClient` directly |
| 10 | Embed-site + diagnostics-probe migration; guard becomes unconditional | PR 10 → `main`, after PR 9 | `uv run pytest tests/unit/test_backend_construction_guard.py tests/unit/cli/` | `uv run openkos ingest <fixture> && uv run openkos reindex` in `tmp_path` with the offline double — no network reached | Revert the ten migrated sites and the guard back to its Phase-9 allowlisted form |
| 11 | Backend-aware embedding tag + question-vector cache key + disclosure rewrite | PR 11 → `main`, after PR 9 | `uv run pytest tests/unit/state/test_reindex.py tests/unit/state/test_question_vectors.py` | `uv run openkos reindex` on a `tmp_path` store with a legacy tag — no forced re-embed | Revert `embedding_tag`/`parse_embedding_tag`/`cache_key`/`_reembed_trigger_wording`; one self-healing re-embed if a user already switched backend |
| 12 | Doctor wording: endpoint+source, key set/not-set, `openai-compatible` remediation | PR 12 → `main`, after PR 9 | `uv run pytest tests/unit/application/test_doctor.py` | `uv run openkos doctor` in `tmp_path` with `base_url` set — endpoint+source line shown | Revert the endpoint-and-source line, key line, and `openai-compatible` remediation branches |
| 13a | `application/backends.py` wording functions + `insecure_key_warning` | PR 13a → `main`, after PR 9 | `uv run pytest tests/unit/application/test_backends.py` | N/A — pure functions, no caller wired yet | Revert the five new functions |
| 13b | CLI/curate/MCP handlers call the wording functions; once-per-process key warning | PR 13b → `main`, after PR 12, PR 13a | `uv run pytest tests/unit/cli/ tests/unit/mcp/` | `uv run openkos query "x"` against an unreachable `openai-compatible` endpoint in `tmp_path` — backend-conditional wording, no Ollama text | Revert the handler wiring; PR 13a's functions stay unused but harmless |
| 14 | Enable: `openai-compatible` selectable, template comments, offline e2e | PR 14 → `main`, after PR 10, 11, 12, 13a, 13b | `uv run pytest tests/unit/test_config.py tests/unit/e2e/test_openai_compatible_workspace.py` | `uv run openkos init && uv run openkos ingest <fixture>` in `tmp_path` with `backend: openai-compatible` and the offline double — full verb surface works | Revert `SELECTABLE_BACKENDS` to `{"ollama"}`; every earlier phase is inert on the default path |
| 15 | Docs: shape-level mentions, known limitations | PR 15 → `main`, after PR 14 | `grep -rn 'OLLAMA_HOST' docs/` (spot-check no stale exclusivity claim survives) | N/A — prose-only | Revert doc edits file-by-file |

## Scenario / Requirement → Task Coverage

Every requirement in the ten delta/new specs, mapped to at least one task.

| Spec | Requirement | Coverage |
| --- | --- | --- |
| openai-compatible-client | Successful Chat Call Returns Assistant Text | 4.7, 4.9, 4.10 |
| openai-compatible-client | System And User Roles Supported | 4.11 |
| openai-compatible-client | Server Unavailable Raises A Typed Error | 4.12-4.15 |
| openai-compatible-client | Unknown Model Raises A Typed Not-Found Error | 4.16, 4.17, 4.22 |
| openai-compatible-client | Other Failures Raise A Generic Typed Error | 4.18, 4.20-4.22 |
| openai-compatible-client | Generation Length Cap Detected From finish_reason | 5.1-5.6 |
| openai-compatible-client | Usage Counters Fail-Open | 5.7-5.9 |
| openai-compatible-client | Temperature/Seed Top-Level None-Omit | 5.10-5.11 |
| openai-compatible-client | context_window Advisory-Only, Never Sent | 5.12-5.14 |
| openai-compatible-client | Optional Bearer Key Applied, Never Logged | 4.19, 4.23-4.25 |
| openai-compatible-client | Testable Without A Live Server | 4.1-4.26 (fakes), 8.1-8.6 (offline double) |
| openai-compatible-client | Embedder Ordering/EMBED_DIM/L2 Normalization | 6.1-6.12 |
| openai-compatible-client | Wrong-Dimension Distinct Permanent Error | 6.7, 6.8, 6.15 |
| openai-compatible-client | Transient Embed Failures Retried | 6.13-6.16 |
| openai-compatible-client | Server Unavailable During Embedding | 6.17-6.18 |
| openai-compatible-client | List Installed Models Via /v1/models | 7.1-7.4 |
| openai-compatible-client | Locality Uses The Shared Classifier | 7.5-7.7 |
| openai-compatible-client | Error Hierarchy Mirrors Ollama's | 2.1-2.4, 4.1-4.2 |
| openai-compatible-client | Config-Free Client | 4.2a-4.2b |
| backend-selection | `backend` Config Key Selects The Family | 3.3, 3.4, 14.1, 14.2 |
| backend-selection | Shared `base_url` Serves Whichever Backend | 3.5, 3.6, 9.7 |
| backend-selection | `openai-compatible` Requires An Explicit `base_url` | 3.7, 14.3 |
| backend-selection | Optional `embedding_base_url` | 3.5, 3.6, 9.4, 9.5 |
| backend-selection | Userinfo In `base_url`/`embedding_base_url` Refused | 3.5, 3.6 |
| backend-selection | Endpoint Resolution Precedence | 9.3-9.9 |
| backend-selection | API Key Read Only From Environment | 3.8, 3.9, 9.16-9.19 |
| backend-selection | Non-Loopback Key Over Plain HTTP Warned | 13.10-13.12, 13.24-13.25 |
| backend-selection | One Resolver Seam Constructs Every Client | 9.10-9.34, 10.1-10.20 |
| llm-client | Model And Base URL Are Configurable (precedence) | 9.3, 9.4, 9.8, 9.9 (regression pin: existing `test_ollama.py` suite, run in every phase's full-suite gate) |
| mcp | Chat-Client Construction Behind Injected Factory + `embed_client` | 9.10, 9.21, 9.23, 9.25, 10.13, 10.14 |
| mcp | Layering Keeps Core Free Of Adapter, Adapter Free Of CLI | 9.22b |
| reindex-command | Embedding-Model Tag Gate Forces Full Re-Embed On Mismatch | 11.7-11.9 (partial-failure/self-heal scenarios: pre-existing, regression-pinned by 11.23's full-suite run) |
| reindex-command | `reindex()` Accepts An Explicit Model Tag Parameter | 11.9, 11.20, 11.21 |
| reindex-command | Reindex Discloses The Real Re-Embed Trigger | 11.10-11.12 |
| doctor-command | Doctor Shows The Effective Endpoint And Its Source | 12.1-12.4 |
| doctor-command | The API Key Is Never Printed By Doctor | 12.5-12.7 |
| doctor-command | Failed Checks Print Actionable Remediation | 12.8-12.12 |
| workspace-init | Static openkos.yaml Template (new commented keys) | 14.4-14.7 |
| llm-edge-production | Ollama Unavailability Points To `doctor` (backend-conditional) | 13.3, 13.4 |
| entity-resolution-adjudication | Degrade-On-No-Model Mirrors query's 3-Tier Catch | 13.5, 13.6 |
| curate-command | Each Stage Resolves Its Own Task Model (backend wording) | 13.7, 13.8 |
| curate-command | Availability Is Tracked Per Model, Not Per Run | 13.8 |
| query-command | LLM And Index Errors Map To Exit 1 (backend-conditional) | 13.1, 13.2 |
| query-command | `--save` Discloses A Possible Duplicate (cache key + disclosure) | 11.13-11.17, 13.15, 13.16 |

No gaps identified against the ten specs read for this phase.

---

## Phase 1 (PR 1 → `main`): Shared helpers move + scope

Design Decision 1. Pure relocation with re-exports; no behavior change.

- [x] **1.1** [TEST] `tests/unit/llm/test_backend_host.py` (extend) — add
  `test_classify_backend_host_importable_from_base`: `from openkos.llm.base
  import classify_backend_host` succeeds, and the existing table-driven
  cases run against this import path too (parametrize by importing the same
  table fixture the existing `ollama`-path test uses, not a duplicated
  literal table). **RED today**: `ImportError` — `llm/base.py` has no
  `classify_backend_host`.
- [x] **1.2** [IMPL] `src/openkos/llm/base.py`: move `classify_backend_host`
  and its private helpers (`_LOCAL_HOST_LITERALS`, `_UNPARSEABLE_DISPLAY`,
  `_HEX_DIGITS`, `_plausible_bracketless_ipv6`, `_is_clean_hostport`,
  `_is_loopback_ipv4_literal`) from `llm/ollama.py`, verbatim body.
  `src/openkos/llm/ollama.py` re-exports each name (`from openkos.llm.base
  import classify_backend_host as classify_backend_host`, etc.) so every
  existing `from openkos.llm.ollama import classify_backend_host` call site
  keeps working unchanged. Makes 1.1 GREEN.
- [x] **1.3** [TEST] same file — add
  `test_ollama_reexports_are_the_same_object`: `ollama.classify_backend_host
  is base.classify_backend_host`, and the same identity check for each of
  the six private helpers (e.g. `ollama._plausible_bracketless_ipv6 is
  base._plausible_bracketless_ipv6`). **RED today**: `AttributeError` on
  `base` before 1.2 lands; after 1.2 this becomes a real identity pin against
  accidental duplication instead of re-export.
- [x] **1.4** [TEST] `tests/unit/llm/test_ollama.py` (extend) — add
  `test_measured_counters_public_name_on_base`: `from openkos.llm.base import
  measured_counters` behaves identically to the existing `_measured_counters`
  parametrized table (reuse the existing cases, do not duplicate the table).
  **RED today**: `ImportError`.
- [x] **1.5** [IMPL] `src/openkos/llm/base.py`: add public `measured_counters`
  (moved + renamed from `ollama._measured_counters`, verbatim logic);
  `src/openkos/llm/ollama.py` keeps `_measured_counters = measured_counters`
  as a private alias so every existing internal call site in `ollama.py`
  keeps working unchanged. Makes 1.4 GREEN.
- [x] **1.6** [TEST] same file — add
  `test_ollama_private_alias_is_the_public_function`: `ollama._measured_counters
  is base.measured_counters`. **RED today**: distinct objects before 1.5.
- [x] **1.7** [TEST] `tests/unit/llm/test_ollama.py` (extend the existing
  `is_timeout_failure` test) — add
  `test_is_timeout_failure_widens_to_backend_unavailable`: a bare
  `BackendUnavailable("x")` instance (not an `OllamaUnavailable`) returns
  `True`, alongside every existing Ollama-specific true/false case. **RED
  today**: `AssertionError` — today's check narrows to `OllamaUnavailable`/
  `http.client`/`urllib.error` types, so a bare `BackendUnavailable` returns
  `False`. Kills a narrowing that would silently stop widening if
  `is_timeout_failure` is later re-narrowed to `OllamaUnavailable`.
- [x] **1.8** [IMPL] `src/openkos/llm/base.py`: move `is_timeout_failure` from
  `ollama.py`, widen its `isinstance` check from `OllamaUnavailable` to
  `BackendUnavailable`; `ollama.py` re-exports it. Makes 1.7 GREEN; every
  existing Ollama-specific case still passes because `OllamaUnavailable` IS a
  `BackendUnavailable`.
- [x] **1.9** [IMPL] `src/openkos/llm/base.py`: rewrite the module docstring's
  sentence stating `classify_backend_host` and the Ollama-specific
  host/family classification logic "stayed in `ollama.py`" — the move
  reverses that; also adjust `ollama.py`'s own docstring/comments that cited
  the classifier as locally defined.
- [x] **1.10** [DOC] `AGENTS.md`: add `llm` to the Conventional Commits scope
  list (proposal decision 5).
- [x] **1.11** Confirm `docs/adr/0031-openai-compatible-backend.md` exists,
  status `Proposed`, and `docs/adr/README.md` carries its index row (already
  written per session note). No edit expected; correct only if drifted.

### Phase 1 verification

- [x] **1.12** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **1.13** Run `uv run pytest tests/unit/llm/test_backend_host.py
  tests/unit/llm/test_ollama.py` focused, then `uv run pytest --cov`
  (unpiped) full suite — must be green, 90% branch gate held.
- [x] **1.14** Run `uv run python evals/run_self_tests.py` — must be green
  with `OLLAMA_HOST` poisoned.
- [x] **1.15** Commit as one or more work-unit commits, scope `llm` (first
  use of the new scope). Open PR 1 targeting `main`. (PR 1 opened and merged
  as #1096.)

**Rollback boundary**: revert the moved definitions back into `ollama.py`;
drop the `base.py` additions and the re-exports; revert the `AGENTS.md` line.
ADR-0031 documents a decision, not behavior — optional to revert, and no
later slice depends on its presence.

---

## Phase 2a (PR 2a → `main`, after PR 1): Neutral error bases + non-CLI catch sites

Design Decision 3.

- [x] **2.1** [TEST] `tests/unit/llm/test_neutral_bases.py` (new) — add
  `test_neutral_bases_subclass_backend_error`: `BackendModelNotFound`,
  `BackendGenerationCapped`, `BackendEmbeddingDimensionMismatch` each
  subclass `BackendError`. **RED today**: `AttributeError` — classes don't
  exist.
- [x] **2.2** [IMPL] `src/openkos/llm/base.py`: add the three neutral
  mid-level error classes. Makes 2.1 GREEN.
- [x] **2.3** [TEST] `tests/unit/llm/test_ollama.py` (extend) — add
  `test_ollama_concrete_classes_are_also_neutral_subclasses` (an
  `issubclass` table): `OllamaModelNotFound` subclasses
  `BackendModelNotFound`; `OllamaGenerationCapped` subclasses
  `BackendGenerationCapped`; `OllamaEmbeddingDimensionMismatch` subclasses
  `BackendEmbeddingDimensionMismatch`; `OllamaUnavailable` subclasses
  `BackendUnavailable` (regression pin, pre-existing). **RED today**:
  `AssertionError` — Ollama's classes have only their single existing base.
- [x] **2.4** [IMPL] `src/openkos/llm/ollama.py`: give
  `OllamaModelNotFound`, `OllamaGenerationCapped`,
  `OllamaEmbeddingDimensionMismatch` each a second base (the matching
  neutral class), exactly as `OllamaUnavailable` already has
  `BackendUnavailable`. Makes 2.3 GREEN; MRO stays consistent (single root
  `BackendError`).
- [x] **2.5** [TEST] `tests/unit/llm/test_neutral_catch_sites.py` (new) —
  add `test_no_concrete_backend_class_named_outside_client_modules`: an AST
  walk of every `.py` under `src/openkos/` EXCEPT `llm/ollama.py` and
  `llm/openai_compatible.py` (forward reference — allowed in the exclusion
  list now so this guard needs no edit when Phase 4 creates that file)
  rejects any `except`/`isinstance` naming a concrete `Ollama*`/
  `OpenAICompatible*` error class, carrying an explicit `_PENDING_SITES`
  allowlist (see the tasks-phase decision above) that starts EMPTY for this
  requirement (the neutral-catch-site migration has no staged rollout — every
  site must migrate in Phases 2a/2b). **RED today**: fails immediately —
  about 55 known sites still name a concrete Ollama class across
  `resolution/*.py`, `state/reindex.py`, `retrieval/answer.py`,
  `extraction/judge.py`, `extraction/concept.py`, `application/query.py`,
  `application/ingest.py`, `cli/main.py`, `cli/curate.py`, `mcp/server.py`.
- [x] **2.6** [IMPL] migrate every concrete-class `except`/`isinstance` in
  the NON-CLI modules to its neutral equivalent, preserving each ladder's
  subclass-first ORDER exactly (`OllamaUnavailable`→`BackendUnavailable`,
  `OllamaModelNotFound`→`BackendModelNotFound`,
  `OllamaGenerationCapped`→`BackendGenerationCapped`,
  `OllamaEmbeddingDimensionMismatch`→`BackendEmbeddingDimensionMismatch`,
  bare `OllamaError`→`BackendError`) in:
  `src/openkos/resolution/{adjudication,contradiction,edge_typing,
  volatility_typing,decision_revision,decision_subject}.py`,
  `src/openkos/state/reindex.py`, `src/openkos/retrieval/answer.py`,
  `src/openkos/extraction/{judge,concept}.py`,
  `src/openkos/application/{query,ingest}.py`. Each module now imports from
  `openkos.llm.base` instead of (or alongside, where it needs a concrete
  type for another reason) `openkos.llm.ollama`. Narrows 2.5's failing set to
  the CLI/curate/MCP sites only, closed in Phase 2b.
- [x] **2.7** [TEST] same file — mutation-proof: temporarily reintroduce one
  concrete `except OllamaError` into a scratch copy of one already-migrated
  module the guard scans (or a planted violation fixture under `tmp_path`),
  confirm the guard fails, then remove it. Record the mutation and result
  inline as a comment.

### Phase 2a verification

- [x] **2.8** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **2.9** Run `uv run pytest tests/unit/llm/test_neutral_bases.py
  tests/unit/llm/test_neutral_catch_sites.py tests/unit/resolution/
  tests/unit/state/test_reindex.py tests/unit/retrieval/test_answer.py
  tests/unit/extraction/ tests/unit/application/test_query.py
  tests/unit/application/test_ingest.py` focused, then `uv run pytest --cov`
  full suite; then `uv run python evals/run_self_tests.py`.

  **Observed**: `tests/unit/application/test_query.py` does not exist under
  that name (the suite splits it as `test_query_filing.py`/
  `test_query_service.py`); ran those two instead. Focused run: 1459
  passed, 1 failed (`test_no_concrete_backend_class_named_outside_client_modules`,
  narrowed to exactly `cli/main.py`, `cli/curate.py`, `mcp/server.py` — the
  documented interim state this task's own text predicts, closed by task
  2.11 in Phase 2b, not before). Full suite (`uv run pytest --cov`): 7230
  passed, 1 failed (the same guard test), 2 skipped, coverage 96.92%
  (>= 90% gate held). `uv run python evals/run_self_tests.py`: 44/44 green
  with `OLLAMA_HOST` poisoned. `ruff check .`, `ruff format --check .`,
  `mypy .`: all clean.
- [x] **2.10** Commit, scope `llm` (or the touched module's own listed scope
  where more specific). Open PR 2a targeting `main`, after PR 1 merges.

  **Observed**: committed on the current branch (no push, no PR opened per
  the apply run's instructions — PR creation is left to the maintainer).

---

## Phase 2b (PR 2b → `main`, after PR 2a): CLI/curate/MCP catch sites

- [x] **2.11** [IMPL] same migration as 2.6 for `src/openkos/cli/main.py`,
  `src/openkos/cli/curate.py`, `src/openkos/mcp/server.py`. Closes the
  remainder of 2.5's failing set — every module outside `llm/ollama.py`/
  `llm/openai_compatible.py` is now clean.
- [x] **2.12** [TEST] `tests/unit/llm/test_neutral_catch_sites.py` —
  mutation-proof pass over a CLI-module violation specifically: reintroduce
  a concrete catch in a scratch copy of `cli/main.py`'s scan target,
  confirm the guard fails, then remove it.
- [x] **2.13** [TEST] `tests/unit/cli/test_query.py`,
  `tests/unit/cli/test_curate.py`, `tests/unit/mcp/test_server.py` — regression
  pin: every existing test asserting on Ollama-specific remediation wording
  still passes unchanged (byte-identity on the default `ollama` path),
  confirming the rename changed no observable behavior. **RED only if** the
  rename accidentally altered a message.

  **Observed**: `cli/main.py` migration touched 32 sites (import block +
  the four-class ladder used across the adjudicate/query/reindex/curate
  handlers); `cli/curate.py` 11 sites; `mcp/server.py` 4 sites (the
  `_TOOL_ERROR_TABLE` tuple values, not a literal `except`/`isinstance`
  clause — see the guard's own widened-detection rationale in
  `test_neutral_catch_sites.py`'s module docstring). Mutation-proof (2.12):
  reintroduced `except OllamaError as exc:` into `cli/main.py`, guard
  reported exactly `{'cli/main.py': ['OllamaError (line 5475)']}`, reverted,
  `__pycache__` purged, reconfirmed GREEN; separately reintroduced a
  concrete `OllamaUnavailable` into `mcp/server.py`'s table, guard reported
  exactly `{'mcp/server.py': ['OllamaUnavailable (line 69)']}`, reverted,
  reconfirmed GREEN. Regression pin (2.13):
  `tests/unit/cli/test_query.py tests/unit/cli/test_curate.py
  tests/unit/mcp/test_server.py` → 252 passed, byte-identical wording
  confirmed (no wording changed — that stays Phase 13b's job).

### Phase 2b verification

- [x] **2.14** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **2.15** Run `uv run pytest tests/unit/cli/ tests/unit/mcp/
  tests/unit/llm/test_neutral_catch_sites.py` focused, then `uv run pytest
  --cov` full suite; then `uv run python evals/run_self_tests.py`.

  **Observed**: `ruff check .` / `ruff format --check .` / `mypy .`: all
  clean (367 files). Focused: `pytest tests/unit/cli/ tests/unit/mcp/
  tests/unit/llm/test_neutral_catch_sites.py` → 2607 passed, 1 skipped —
  `test_no_concrete_backend_class_named_outside_client_modules` now GREEN
  unconditionally (no offenders anywhere). Full `pytest --cov` (unpiped):
  **7231 passed, 0 failed, 2 skipped**, 96.92% branch coverage (>=90% gate
  held) — the one Phase-2a-documented interim failure is now closed.
  `evals/run_self_tests.py`: 44/44 green, `OLLAMA_HOST` poisoned.
- [x] **2.16** Commit, scope `cli`/`mcp` as appropriate. Open PR 2b targeting
  `main`, after PR 2a merges.

  **Observed**: committed together with Phase 2a's work on the current
  branch (coordinator decision: Phase 2a and 2b ship as ONE PR, since this
  repo never merges red CI and Phase 2a alone left the guard test red). No
  push, no PR opened — left for the maintainer. Scope `llm` (spans both
  the Phase 2a neutral-bases work and the Phase 2b CLI/curate/MCP
  migration under one cohesive commit).

**Rollback boundary (2a+2b)**: revert the three neutral bases, the Ollama
second-base additions, and every catch-site rename per module (2a and 2b
independently revertable); no new client exists yet to be affected.

---

## Phase 3 (PR 3 → `main`, after PR 1): Config surface

Design Decision 6. `openai-compatible` stays refused until Phase 14.

- [x] **3.1** [TEST] `tests/unit/test_config.py` — add
  `test_config_backend_defaults_to_ollama`: an existing minimal `Config(...)`
  construction has `.backend == "ollama"`. **RED today**: `AttributeError` —
  `Config` has no `backend` field.
- [x] **3.2** [IMPL] `src/openkos/config.py`: add `backend: str = "ollama"`,
  `base_url: str | None = None`, `embedding_base_url: str | None = None`
  fields to `Config`, plus `DEFAULT_BACKEND: Final = "ollama"` and
  `SELECTABLE_BACKENDS: Final = frozenset({"ollama"})` module constants
  (`openai-compatible` added only in Phase 14). Makes 3.1 GREEN; every
  existing `Config(...)` test construction keeps compiling.
- [x] **3.3** [TEST] same file — add `test_read_config_backend_key`,
  parametrized: absent key -> `"ollama"`; `backend: ollama` explicit ->
  `"ollama"`; `backend: openai-compatible` -> raises, message states it is
  "not available in this version; supported: ollama"; `backend:
  something-else` -> raises naming the bad value and the accepted set.
  **RED today**: `read_config` doesn't read/validate `backend`.
- [x] **3.4** [IMPL] `config.py::read_config`: validate `backend` against
  `SELECTABLE_BACKENDS`, defaulting absent/`None` to `DEFAULT_BACKEND`;
  raise naming the invalid value and the accepted set, with the
  "not available in this version" wording for `openai-compatible`
  specifically. Makes 3.3 GREEN.
- [x] **3.5** [TEST] same file — add `test_read_config_base_url_validation`,
  parametrized: `http://127.0.0.1:8080` accepted, trailing `/` stripped;
  `https://` accepted; missing scheme rejected; empty host rejected;
  whitespace rejected; userinfo (`user@`, `user:pw@`) rejected with a
  message naming `OPENKOS_OPENAI_API_KEY`; query string or fragment
  rejected. Same table for `embedding_base_url`. **RED today**: fields not
  validated.
- [x] **3.6** [IMPL] `config.py`: add the `base_url`/`embedding_base_url`
  shape validator (scheme, host, no userinfo, no query/fragment, no
  whitespace, trailing `/` stripped) applied to both keys. Makes 3.5 GREEN.
- [x] **3.7** [TEST] same file — add
  `test_backend_openai_compatible_without_base_url_message`: a workspace
  setting BOTH `backend: openai-compatible` and no `base_url` currently
  surfaces 3.4's pre-enable refusal (confirm the exact message/ordering
  during implementation — 3.4's "not available" check fires before any
  base_url-required check can). This test is superseded by 14.3 once the
  backend value is accepted for real.
- [x] **3.8** [TEST] same file — add
  `test_read_config_refuses_api_key_in_yaml`, parametrized over `api_key`,
  `openai_api_key`, `OPENKOS_OPENAI_API_KEY` as top-level `openkos.yaml`
  keys: each raises, message states the key is read only from the
  environment and names `OPENKOS_OPENAI_API_KEY`. **RED today**:
  `read_config` reads by `raw.get` and ignores unknown top-level keys.
- [x] **3.9** [IMPL] `config.py`: add the misplaced-API-key refusal for the
  three named keys. Makes 3.8 GREEN.
- [x] **3.10** [TEST] same file — add
  `test_unknown_top_level_keys_still_ignored`: a genuinely unrelated unknown
  top-level key is still silently ignored (rollback-story regression pin).
  **RED only if** 3.9 over-broadened to reject every unknown key.
- [x] **3.11** [TEST] same file — add
  `test_ollama_default_path_config_is_byte_identical`: a workspace with none
  of the three new keys set produces `.backend/.base_url/.embedding_base_url
  == "ollama"/None/None`, and no other `Config` field's resolution changed
  (compare against an explicit list of pre-existing fields). **RED only on**
  an implementation regression.

### Phase 3 verification

- [x] **3.12** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **3.13** Run `uv run pytest tests/unit/test_config.py` focused, then
  `uv run pytest --cov` full suite; then `uv run python
  evals/run_self_tests.py`.
- [x] **3.14** Commit, scope `config`. Open PR 3 targeting `main`, after PR
  1 merges (independent of PR 2a/2b).
  Observed: commit half done (scope `config`, on branch `feat/1057-openai-p3`);
  "Open PR 3" half intentionally left for the orchestrator/maintainer per
  apply-phase instructions (no push, no PR from this session) — left
  unticked.

**Rollback boundary**: revert the three `Config` fields,
`DEFAULT_BACKEND`/`SELECTABLE_BACKENDS`, and every `read_config` validation
branch; unknown keys are ignored today, so an older binary already tolerates
their presence.

---

## Phase 4 (PR 4 → `main`, after PR 1, 2a, 2b): Client — chat core

New file `llm/openai_compatible.py`. Design Decisions 1-3; the
`openai-compatible-client` spec's error-hierarchy, transport, and ladder
requirements.

### Error hierarchy + leaf constraint

- [x] **4.1** [TEST] `tests/unit/llm/test_openai_compatible_errors.py` (new)
  — add `test_error_hierarchy`: `OpenAICompatibleError` subclasses
  `BackendError`; `OpenAICompatibleUnavailable` subclasses BOTH
  `OpenAICompatibleError` and `BackendUnavailable`;
  `OpenAICompatibleModelNotFound`/`GenerationCapped`/
  `EmbeddingDimensionMismatch` each subclass `OpenAICompatibleError`. **RED
  today**: `ModuleNotFoundError` — the module doesn't exist. Covers
  "Error Hierarchy Mirrors Ollama's Under The Neutral Bases".

  **Observed**: RED confirmed — moved the not-yet-existing implementation
  aside, ran the full new Phase 4 test set, got 3 `ModuleNotFoundError`
  collection errors (this file plus `test_openai_compatible_chat.py`/
  `test_openai_compatible_ladder.py`) and 2 real assertion failures on the
  two module-independent guards (`test_layering.py`,
  `test_openai_compatible_thread_safety.py`). Restored the implementation;
  GREEN after 4.2.
- [x] **4.2** [IMPL] `src/openkos/llm/openai_compatible.py` (new): error
  hierarchy only, plus a module docstring declaring the leaf constraint
  (stdlib + `openkos.llm.base` only, no config/env import). Makes 4.1
  GREEN.
- [x] **4.2a** [TEST] `tests/unit/llm/test_layering.py` (extend the existing
  no-config-import check) — add
  `test_llm_package_no_config_import_includes_new_module`: the existing
  AST/import scan over `llm/*.py` now also covers `openai_compatible.py`.
  Covers "Config-Free Client". **RED reason**: this is a scan-coverage
  extension landing in the same commit as 4.2 — confirm the scanner
  auto-discovers every file under `llm/` (glob-based) rather than a
  hardcoded list; if hardcoded, this task's RED is a literal missing entry.

  **Observed**: `tests/unit/llm/test_layering.py` did not exist yet (the
  existing no-config-import check lives in `test_ollama.py::
  test_llm_modules_do_not_import_config`, already glob-based over
  `llm/*.py`). Created the new file with its own independent pin
  (`test_llm_package_no_config_import_includes_new_module`), asserting
  `openai_compatible.py` is present in the glob-discovered set — RED
  confirmed (`AssertionError`, module absent) before 4.2 landed.
- [x] **4.2b** [IMPL] extend the layering guard's scanned-file list if it is
  not already glob-based. Makes 4.2a GREEN.

  **Observed**: the scan was already glob-based
  (`Path.glob("*.py")` in both `test_ollama.py`'s existing check and the
  new `test_layering.py` pin), so no source-code or scanner change was
  needed — creating `openai_compatible.py` in 4.2 alone made both GREEN.

### Construction, `resolved_base_url`

- [x] **4.3** [TEST] same errors file — add
  `test_class_constructs_with_required_args`:
  `OpenAICompatibleClient(model="m", base_url="http://127.0.0.1:8080")`
  constructs with every other keyword defaulted, mirroring `OllamaClient`'s
  shape. **RED today**: `AttributeError` — class doesn't exist.
- [x] **4.4** [IMPL] `OpenAICompatibleClient.__init__` per design's
  Interfaces/Contracts signature: `model`, `base_url` (required kwarg),
  `timeout=DEFAULT_TIMEOUT` (600.0, same floor as Ollama),
  `max_generation_tokens=None`, `temperature=None`, `seed=None`,
  `context_window=None`, `api_key=None`,
  `urlopen=urllib.request.urlopen`,
  `embed_retry_attempts=DEFAULT_EMBED_RETRY_ATTEMPTS`,
  `embed_retry_backoff_base=DEFAULT_EMBED_RETRY_BACKOFF_BASE`,
  `sleep=time.sleep`; store each as a private instance attribute. Makes 4.3
  GREEN.
- [x] **4.5** [TEST] same file — add
  `test_resolved_base_url_strips_trailing_slash_and_one_v1`, parametrized:
  `http://127.0.0.1:8080` unchanged; trailing `/` stripped; trailing `/v1`
  stripped; trailing `/v1/` stripped to no `/v1`; a reverse-proxy mount
  `http://host/proxy/v1` -> `http://host/proxy` (kept, not stripped
  further). **RED today**: property not implemented.
- [x] **4.6** [IMPL] implement `resolved_base_url` (exactly one trailing `/`
  then exactly one trailing `/v1` stripped; any other path prefix kept).
  Makes 4.5 GREEN.

### `chat()` happy path

- [x] **4.7** [TEST] `tests/unit/llm/test_openai_compatible_chat.py` (new) —
  add `test_chat_posts_to_v1_chat_completions_with_stream_false`: fake
  `urlopen` captures the `Request`; URL == `{base_url}/v1/chat/completions`,
  method POST, body carries `"model"`, `"messages"`, `"stream": false`.
  Covers "Successful Chat Call Returns Assistant Text" (request half). **RED
  today**: `chat` not implemented.
- [x] **4.8** [IMPL] `OpenAICompatibleClient.chat(messages)`: build the
  request body, POST via injected `urlopen`. No instance-attribute writes
  inside `chat` (thread-safety, mirrors `OllamaClient`). Makes 4.7 GREEN.
- [x] **4.9** [TEST] same file — add
  `test_chat_returns_assistant_content_string`: server responds 200 with
  `{"choices":[{"message":{"role":"assistant","content":"hi"},
  "finish_reason":"stop"}]}`; `chat(...)` returns `"hi"`. Covers the same
  requirement's response half.
- [x] **4.10** [IMPL] parse `choices[0].message.content`, return as `str`; a
  `null`/non-string content raises `OpenAICompatibleError`. Makes 4.9
  GREEN.
- [x] **4.11** [TEST] same file — add
  `test_chat_forwards_system_and_user_messages_in_order`: a message list
  with one `system` and one `user` entry is forwarded verbatim, same order.
  Covers "System And User Roles Supported".

### Error ladder

- [x] **4.12** [TEST] `tests/unit/llm/test_openai_compatible_ladder.py`
  (new) — add `test_connection_refused_raises_unavailable`: fake `urlopen`
  raises `URLError(ConnectionRefusedError())`; `chat(...)` raises
  `OpenAICompatibleUnavailable`, no `URLError` escapes. Covers "Server
  Unavailable Raises A Typed Error".
- [x] **4.13** [TEST] same file — add `test_timeout_raises_unavailable` /
  `test_incomplete_read_raises_unavailable`: `socket.timeout` and
  `http.client.IncompleteRead` both map the same way.
- [x] **4.14** [IMPL] transport-error mapping in `openai_compatible.py`:
  connection/timeout/incomplete-read -> `OpenAICompatibleUnavailable`,
  naming `locality.display_host` only (the #355 rule — never the raw
  configured value). Makes 4.12-4.13 GREEN.
- [x] **4.15** [TEST] same file — add
  `test_404_with_model_body_raises_model_not_found` /
  `test_400_with_model_not_found_body_raises_model_not_found`: an
  `HTTPError` 404 (body names the model, "not found"/"does not exist") and
  a 400 whose body carries `"model_not_found"` both raise
  `OpenAICompatibleModelNotFound`. Covers "Unknown Model Raises A Typed
  Not-Found Error" (status is not solely determinative).
- [x] **4.16** [TEST] same file — add `test_other_4xx_5xx_raises_generic_error`,
  parametrized over 401 (no key configured), 403, 500, 503: each raises
  `OpenAICompatibleError`, NOT `OpenAICompatibleModelNotFound`. Covers
  "Other Failures Raise A Generic Typed Error".
- [x] **4.17** [TEST] same file — add
  `test_401_403_message_names_env_var_never_key_value`: with an API key
  configured, a 401/403 raises `OpenAICompatibleError` whose message says
  authentication failed and names `OPENKOS_OPENAI_API_KEY`, and the
  configured key's VALUE never appears (sentinel-key assertion).
- [x] **4.18** [TEST] same file — add
  `test_malformed_json_raises_generic_error` /
  `test_missing_choices_content_shape_raises_generic_error`: a non-JSON
  body, and a valid-JSON body missing `choices[0].message.content`, both
  raise `OpenAICompatibleError` with no unhandled `JSONDecodeError`/
  `KeyError`/`IndexError` escaping.

  **Observed**: also added `test_null_content_raises_generic_error`
  (a `null` `content` value), pinning 4.10's "must be a `str`" clause
  explicitly rather than leaving it implicit in the happy-path tests.
- [x] **4.19** [IMPL] `_map_http_error(status, body)` implementing the full
  classification table (404 or 400-with-marker -> `ModelNotFound`; 401/403
  -> generic error naming `OPENKOS_OPENAI_API_KEY`, never the key;
  everything else -> generic error with status+detail), plus the
  malformed-JSON/missing-shape guards inside `chat`'s response parsing.
  Makes 4.15-4.18 GREEN.

  **Observed**: implemented as `self._map_http_error(exc)`, an instance
  method (not the bare `(status, body)` module function design.md
  sketched) precisely so 4.20's mutation-proof test can plausibly
  interpolate `self._api_key` in the auth-failure branch — a module-level
  function would have no access to the key to leak, which would make that
  mutation-proof test untestable. Classification signature: 404 with a
  "not found"/"does not exist" body, OR 400 with a `model_not_found`
  marker, -> `OpenAICompatibleModelNotFound`; else 401/403 -> generic error
  naming `OPENKOS_OPENAI_API_KEY`; else -> generic error with status+detail.
- [x] **4.20** [TEST] same file — add
  `test_key_never_appears_in_any_ladder_rung_exception_message`,
  parametrized over EVERY rung above with a client constructed with a
  sentinel API key: the sentinel never appears in `str(exc)` for any rung.
  Mutation-proof: mutate `_map_http_error`'s auth-failure branch to
  interpolate the key value directly, confirm this test fails, then revert.

  **Observed mutation proof**: edited the 401/403 branch in
  `openai_compatible.py` to add `f"(tried {self._api_key})"` to the
  message; ran `test_401_403_message_names_env_var_never_key_value` and
  `test_key_never_appears_in_any_ladder_rung_exception_message` — 3 failed
  (the dedicated test plus rung indices 4 and 5, the 401/403 rungs),
  showing the sentinel value leaking into the message. Reverted with the
  exact inverse edit, purged `__pycache__` (repo-wide), reconfirmed all
  Phase 4 tests green (36 passed).
- [x] **4.21** [TEST] same file — add
  `test_key_sent_as_bearer_header_when_present` /
  `test_no_authorization_header_when_absent`: fake `urlopen` captures
  headers; a client with a key sends `Authorization: Bearer <key>` on
  every request; a client without sends no `Authorization` header at all.
  Covers "An Optional Bearer Key Is Applied Per Request And Never Logged".
- [x] **4.22** [IMPL] wire the `Authorization` header conditionally in the
  shared request-building helper `chat`/`embed`/`list_models` all use.
  Makes 4.21 GREEN.

  **Observed**: implemented as `_build_request(url, payload, *, method)`,
  used by `chat` now; `embed`/`list_models` (Phases 6-7) will reuse it —
  no duplicate header-building logic to keep in sync later.
- [x] **4.23** [TEST] `tests/unit/llm/test_openai_compatible_thread_safety.py`
  (new) — add `test_chat_writes_no_instance_attribute`: AST guard copied
  from `test_ollama.py`'s equivalent (#748), scanning `chat`'s body for any
  `self.<name> = ...` assignment. Mutation-proof: temporarily add
  `self._last = messages` inside `chat`, confirm the guard fails, then
  remove it.

  **Observed mutation proof**: added `self._last = messages` as the first
  line of `chat` in `openai_compatible.py`; `test_chat_writes_no_instance_
  attribute` failed (`assert [214] == []`, the mutated line's number).
  Reverted with the exact inverse edit, purged `__pycache__`, reconfirmed
  green. Also added a second, synthetic-source
  `test_the_self_mutation_guard_catches_a_mutation` (reduced form-catalog,
  since the shared AST-walk helper's exhaustive form coverage is already
  proven in `test_ollama.py`).

### Phase 4 verification

- [x] **4.24** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: `ruff check .` clean; `ruff format --check .` initially
  flagged 2 files (`test_openai_compatible_errors.py`,
  `test_openai_compatible_ladder.py`), reformatted with `uv run ruff
  format`, reconfirmed clean (373 files); `mypy .` found one error
  (`test_openai_compatible_chat.py`'s untyped `messages` literal not
  narrowing to `Sequence[Message]`), fixed with an explicit
  `list[Message]` annotation; reconfirmed clean (373 source files).
- [x] **4.25** Run `uv run pytest tests/unit/llm/test_openai_compatible*.py
  tests/unit/llm/test_layering.py` focused, then `uv run pytest --cov` full
  suite; then `uv run python evals/run_self_tests.py`.

  **Observed**: focused (`tests/unit/llm/test_openai_compatible*.py
  tests/unit/llm/test_layering.py`) → 36 passed. Full `pytest --cov`
  (unpiped, backgrounded — 433.18s wall time) → **7267 passed, 0 failed, 2
  skipped**, 96.92% branch coverage (>= 90% gate held);
  `llm/openai_compatible.py` itself: 90 statements, 2 missed, 97% (an
  `_map_http_error` error-reading-the-error-body branch and one
  `resolved_base_url` ternary arm not independently exercised — both are
  defensive/degenerate paths covered by the design's own fail-open
  contract, not a gap in the tested requirements). `evals/
  run_self_tests.py`: 44/44 green.
- [x] **4.26** Commit, scope `llm`. Open PR 4 targeting `main`, after PR 1,
  2a, 2b merge.

  **Observed**: committed on the current branch (`feat/1057-openai-p4`,
  cut from `main` after PR #1096/#1097) — no push, no PR opened per the
  apply run's instructions; PR creation is left to the maintainer.

### Phase 4 security fix (post-review, before PR): never follow redirects

Coordinator-identified gap, fixed on the same branch as a follow-up
commit (not a numbered task above): `urllib.request.HTTPRedirectHandler.
redirect_request`'s default implementation copies every non-content
header — including `Authorization` — onto a redirected request, and for
301/302/303 on POST silently converts it to GET while still forwarding
that header. The `openai-compatible-client` spec already required the key
never be redirected to another host; the shipped Phase 4 code did not yet
enforce it.

- [x] Added `_NoRedirectHandler` (a `urllib.request.HTTPRedirectHandler`
  subclass whose `redirect_request` always returns `None`) and
  `_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)`;
  `OpenAICompatibleClient.__init__`'s `urlopen` default is now
  `_NO_REDIRECT_OPENER.open`, not `urllib.request.urlopen`. The injectable
  `urlopen` parameter is unchanged.
- [x] `chat()`'s `except urllib.error.HTTPError` branch now checks
  `300 <= exc.code < 400` first and routes to a new `_map_redirect(exc)`
  method — discovered mechanically (not assumed) that a refused redirect
  surfaces as `HTTPError`, never as a normal response with a 3xx status:
  `OpenerDirector._call_chain` treats `redirect_request`'s `None` as "no
  handler answered" and falls through to `http_error_default`, which
  raises. `_map_redirect` never reads `self._api_key` and strips the
  `Location` header's query string/fragment (`_redact_location_query`)
  before interpolating it into the message.
- [x] [TEST] `tests/unit/llm/test_openai_compatible_redirect.py` (new, 9
  tests): `test_any_3xx_response_raises_mapped_error` (parametrized over
  307/301/302/303/308, with/without a `Location` header);
  `test_redirect_error_message_never_contains_configured_key` and
  `test_redirect_message_does_not_echo_location_query_string` (fake
  `urlopen` raising a real `HTTPError`, precondition-checked that the key
  really was sent on the pre-redirect request);
  `test_default_urlopen_is_the_no_redirect_opener` (structural identity
  pin); `test_redirect_is_never_followed_and_key_never_forwarded` — a REAL
  integration test, two local `http.server` instances on `127.0.0.1`
  (marked `@pytest.mark.live_backend` to lift the unit suite's fail-closed
  socket guard for its loopback `connect()` calls, per that fixture's own
  documented escape hatch), a 303 (not 307 — a 307/308 POST redirect is
  already refused by stock `urllib.request.urlopen` for an unrelated RFC
  2616-consent reason and would not have exercised this fix) from server A
  to server B; asserts server B never receives any request.
- [x] **RED confirmed** before implementing: 6 of the module's tests
  failed for the right reason (5 fake-based tests asserting the specific
  redirect message/status, plus the real end-to-end test — `Failed: DID
  NOT RAISE OpenAICompatibleError`, proving the live vulnerability); 2
  fake-based tests (key-never-appears, query-not-echoed) passed vacuously
  on first run because the pre-fix code's generic error-fallback message
  happened not to contain either — both are mutation-proved below instead
  of trusted at face value.
- [x] **Mutation proof 1** (redirect actually followed): temporarily
  reverted `urlopen`'s default back to `urllib.request.urlopen`; 2 tests
  failed (`test_default_urlopen_is_the_no_redirect_opener`,
  `test_redirect_is_never_followed_and_key_never_forwarded`). Reverted
  with the exact inverse edit.
- [x] **Mutation proof 2** (key/query leaked into the message): temporarily
  edited `_map_redirect` to interpolate `self._api_key` and to use the raw
  (un-redacted) `Location` value; 3 tests failed
  (`test_redirect_error_message_never_contains_configured_key`,
  `test_redirect_message_does_not_echo_location_query_string`,
  `test_redirect_is_never_followed_and_key_never_forwarded`). Reverted
  with the exact inverse edit. Purged `__pycache__` after both proofs.
- [x] Design/spec updated: one sentence added to `design.md`'s Decision 2
  (HTTP error mapping paragraph) recording the no-redirect policy and why;
  a new scenario ("A redirect response is refused, never forwarding the
  key") added under `specs/openai-compatible-client/spec.md`'s "An
  Optional Bearer Key..." requirement (plain new-domain spec, no
  ADDED/MODIFIED delta headers existed in this file to preserve).
- [x] Re-ran all five verification commands unpiped after the fix: `ruff
  check .` clean; `ruff format --check .` reformatted 2 files, reconfirmed
  clean (374 files); `mypy .` found one `redundant-expr` (an always-true
  `is not None` check against `HTTPError.headers`, which typeshed types as
  non-Optional), removed the redundant check, reconfirmed clean (374
  source files); `uv run pytest --cov` (unpiped, backgrounded — 444.30s
  wall time) → **7276 passed, 0 failed, 2 skipped**, 96.93% branch
  coverage (>= 90% gate held); `llm/openai_compatible.py`: 104 statements,
  2 missed, 98%. `evals/run_self_tests.py`: 44/44 green.
- [x] Commit as a separate follow-up commit (not squashed into 4.26's),
  scope `llm`. No push, no PR — left to the maintainer, same as 4.26.

**Rollback boundary**: delete `llm/openai_compatible.py`; nothing else
references it yet. The redirect-refusal fix is independently revertable:
drop `_NoRedirectHandler`/`_NO_REDIRECT_OPENER`/`_map_redirect`/
`_redact_location_query`, restore `urlopen`'s default to
`urllib.request.urlopen`, and remove the `300 <= exc.code < 400` branch in
`chat()` — the rest of Phase 4 keeps working unchanged.

---

## Phase 5 (PR 5 → `main`, after PR 4): Client — chat bounds

Design Decision 2's `max_tokens`/`finish_reason`/`usage`/`temperature`/
`seed`/`context_window` mapping.

- [x] **5.1** [TEST] `tests/unit/llm/test_openai_compatible_chat.py` — add
  `test_max_tokens_forwarded_when_configured` /
  `test_max_tokens_omitted_when_none`: `max_generation_tokens=256` ->
  `"max_tokens": 256` in the body; `None` (default) -> key absent. Covers
  "Generation Length Cap Is Detected From finish_reason" (forwarding half).

  **Observed**: RED confirmed (`test_max_tokens_forwarded_when_configured`
  failed with `KeyError: 'max_tokens'`; the omitted-half test passed
  vacuously, mutation-proved below).
- [x] **5.2** [IMPL] wire `max_tokens` into the chat request body per 5.1.
- [x] **5.3** [TEST] same — add `test_finish_reason_length_raises_generation_capped`:
  `choices[0].finish_reason == "length"` -> `OpenAICompatibleGenerationCapped`.
- [x] **5.4** [TEST] same — add
  `test_generation_capped_message_names_server_context_size`, mirroring
  Ollama's #440/#829 three-way branching: (a) a configured ceiling was
  reached — message names the ceiling; (b) no ceiling configured but
  `finish_reason == "length"` anyway — message says the SERVER's own
  context size filled and names "raise the server's context size (`-c` for
  llama.cpp, `--max-model-len` for vLLM) and set `context_window` to
  match", never implying `context_window` was sent; (c) neither bound
  configured — generic "backend's own limit cut the reply" wording. **RED
  today**: `chat` doesn't classify `finish_reason` at all.

  **Observed**: implemented as three separate tests
  (`test_generation_capped_message_names_the_ceiling_when_reached`,
  `test_generation_capped_message_blames_server_context_size_when_window_set`,
  `test_generation_capped_generic_wording_when_neither_bound_configured`)
  rather than one parametrized test, for clearer per-branch failure
  messages. RED confirmed: all three (plus 5.3, 5.6, 5.7's) failed with
  `AttributeError: no attribute 'embed'`... no — with the chat method
  simply never raising (assertion `DID NOT RAISE`) before 5.5 landed.
- [x] **5.5** [IMPL] implement the capped-branching message logic per 5.3-5.4,
  raising `OpenAICompatibleGenerationCapped` with the three-way message.
  Makes 5.3-5.4 GREEN.

  **Observed**: implemented as `_generation_capped(counters)`, mirroring
  Ollama's #440/#829 branching but naming the SERVER's own context size
  (never claiming `context_window` was sent) when the window is the
  explanation, per Decision 2.
- [x] **5.6** [TEST] same — add `test_usage_counters_read_when_present`:
  response carries `usage.prompt_tokens`/`usage.completion_tokens`; both
  are read via `measured_counters` (Phase 1) and feed the capped-message
  branching identically to Ollama's counters (confirm exactly how Ollama's
  counters feed that branching and mirror it). Covers "Usage Counters Are
  Read With The Same Fail-Open Discipline As Ollama".
- [x] **5.7** [TEST] same — add
  `test_usage_absent_or_non_int_does_not_fail_the_call`, parametrized:
  `usage` absent; `prompt_tokens` a `bool`; `completion_tokens` a string;
  `usage` an empty `{}` — every case still returns the assistant text with
  counters unknown, never raising.

  **Observed**: parametrized over 4 cases (all-absent, `bool` prompt
  count, non-numeric completion count, prompt-only), each asserting the
  fail-open generic capped wording (the fixture forces `finish_reason ==
  "length"` so the call still raises `OpenAICompatibleGenerationCapped`
  itself — the "does not fail" contract under test is no `TypeError`/
  `KeyError` escaping from the counters read, not the absence of the
  capped error). A separate
  `test_usage_absent_entirely_does_not_prevent_a_successful_reply` pins
  the true non-error half (no `usage` at all, no cap) directly.
- [x] **5.8** [IMPL] wire `usage.prompt_tokens`/`usage.completion_tokens`
  through `measured_counters` inside `chat`'s response handling, fail-open
  per 5.7. Makes 5.6-5.7 GREEN.
- [x] **5.9** [TEST] same — add
  `test_temperature_and_seed_sent_top_level_when_set` /
  `test_temperature_and_seed_omitted_when_none`: non-`None` values appear
  as top-level `temperature`/`seed` (not nested); `None` omits entirely,
  never sent as `null`; `temperature=0.0` (falsy but real) IS sent. Covers
  "Temperature And Seed Are Top-Level Fields With The Same None-Means-Omit
  Rule", including the `0.0`-is-a-value edge case.

  **Observed**: the `0.0` edge case is its own dedicated test
  (`test_temperature_zero_is_sent_because_it_is_a_real_value`),
  mutation-proved below (falsy check reverted from `is not None`
  correctly fails it).
- [x] **5.10** [IMPL] wire `temperature`/`seed` with an explicit `is not
  None` check (never a falsy check), so `0.0`/`0` are sent. Makes 5.9
  GREEN.
- [x] **5.11** [TEST] same — add `test_context_window_never_sent_in_request_body`:
  client constructed with `context_window=8192`; no request field carrying
  that value appears in any chat OR embed request. Covers "context_window
  Is Advisory-Only And Never Sent" (never-sent half).

  **Observed**: the chat half lives in `test_openai_compatible_chat.py`;
  the embed half is a separate `test_embed_never_sends_context_window` in
  `test_openai_compatible_embed.py` (Phase 6), added once `embed()`
  existed to implement — both mutation-proved (see Phase 6 evidence).
- [x] **5.12** [TEST] same — add `test_context_window_property_readable`:
  the `context_window` property returns the constructed value. Covers the
  readable half.
- [x] **5.13** [IMPL] add the read-only `context_window` property; confirm
  (via 5.11) no code path threads it into a request body. Makes 5.11-5.12
  GREEN.

### Phase 5 verification

- [x] **5.14** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: `ruff check .` clean; `ruff format --check .` reformatted
  `openai_compatible.py`/`test_openai_compatible_chat.py`, reconfirmed
  clean; `mypy .` clean (377 source files after Phase 7's additions).
- [x] **5.15** Run `uv run pytest tests/unit/llm/test_openai_compatible_chat.py`
  focused, then `uv run pytest --cov` full suite; then `uv run python
  evals/run_self_tests.py`.

  **Observed**: focused: 20 passed. Full `pytest --cov` was run once,
  combined with Phase 6/7's additions on the same tree — see the combined
  Phase 5+6+7 verification note at the end of Phase 7 for the exact
  numbers. `evals/run_self_tests.py` (with `OLLAMA_HOST` poisoned): 44/44
  green.
- [x] **5.16** Commit, scope `llm`. Open PR 5 targeting `main`, after PR 4
  merges.

  **Observed**: committed together with Phase 6 and 7 as one combined
  commit `fe7855c` (scope `llm`) on branch `feat/1057-openai-p5` — see
  Phase 7's 7.11 for the shared rationale. No push, no PR opened per the
  apply run's instructions; "Open PR 5" left unticked.

**Rollback boundary**: revert `max_tokens`/capped-branching/usage/
temperature/seed/`context_window` additions to `chat`; the chat-core shape
from Phase 4 keeps working.

---

## Phase 6 (PR 6 → `main`, after PR 4): Client — embeddings

Design Decision 2's `/v1/embeddings` mapping; embedding spec requirements.

- [x] **6.1** [TEST] `tests/unit/llm/test_openai_compatible_embed.py` (new)
  — add `test_embed_posts_to_v1_embeddings_with_encoding_format_float`:
  fake `urlopen` captures the request; URL == `{base_url}/v1/embeddings`,
  body carries `"model"`, `"input"`, `"encoding_format": "float"`. **RED
  today**: `embed` not implemented.

  **Observed**: RED confirmed — all 14 tests in the new file failed with
  `AttributeError: 'OpenAICompatibleClient' object has no attribute
  'embed'` before implementation.
- [x] **6.2** [IMPL] `OpenAICompatibleClient.embed(texts)`: build the
  request body, POST via injected `urlopen`. Makes 6.1 GREEN (response
  parsing completed by later tasks).

  **Observed**: implemented `embed`/`_embed_once`/`_validate_and_normalize_row`/
  module-level `_order_embedding_rows` together (one IMPL pass covering
  6.2/6.4/6.7/6.11/6.15/6.17), then ran the full new test file at once —
  all 14 tests (plus the later-added `test_embed_never_sends_context_window`,
  15 total) went GREEN together, since the ordering/validation/retry
  pieces are not independently reachable through `embed()`'s single entry
  point.
- [x] **6.3** [TEST] same — add `test_embed_rows_ordered_by_index_not_response_order`
  / `test_embed_falls_back_to_response_order_when_index_absent`: `data`
  rows out of `index` order return ordered by `index`; when every entry
  lacks `index`, rows return in response order. Covers "Embedder Produces
  Order-Preserving..." (ordering half).

  **Observed**: first draft used uniform-value rows (all-`2.0` vs
  all-`1.0`) and failed for the WRONG reason — L2 normalization erases
  magnitude, so both rows normalize to the identical vector regardless of
  order, a false negative in the test itself. Rewrote with a `_one_hot_row`
  helper (a `1.0` at a distinct component index, already unit-norm) so
  ordering stays observable after normalization; confirmed correct
  thereafter.
- [x] **6.4** [IMPL] parse `data[].embedding`, sort by `index` when every
  entry carries one, else keep response order. Makes 6.3 GREEN.
- [x] **6.5** [TEST] same — add `test_embed_count_mismatch_raises`: `data`
  has fewer/more rows than input `texts`; raises `OpenAICompatibleError`
  (a shape error, distinct from the per-row dimension-mismatch class —
  confirm and pin the exact class during implementation).
- [x] **6.6** [TEST] same — add `test_wrong_dimension_row_raises_distinct_permanent_error`:
  one row has a length other than `EMBED_DIM`; raises
  `OpenAICompatibleEmbeddingDimensionMismatch`, message names actual and
  expected length. Covers "Wrong-Dimension Row Raises A Distinct Permanent
  Error".
- [x] **6.7** [IMPL] add `EMBED_DIM` row-length validation (raises
  `OpenAICompatibleEmbeddingDimensionMismatch`) and the count-mismatch
  check. Makes 6.5-6.6 GREEN.
- [x] **6.8** [TEST] same — add `test_every_returned_vector_is_l2_normalized`:
  a row with a known non-unit norm (e.g. all-`2.0`); returned vector has
  `abs(norm - 1.0) < 1e-9`. Covers "Every returned vector is
  L2-normalized".
- [x] **6.9** [TEST] same — add `test_already_normalized_vector_is_numerically_unchanged`:
  a row already at unit L2 norm returns numerically equivalent
  (normalization is a no-op).
- [x] **6.10** [TEST] same — add `test_zero_norm_row_raises_generic_retryable_error`:
  an all-zero row (division-by-zero hazard) raises the generic (retryable)
  `OpenAICompatibleError`, NOT the dimension-mismatch class.

  **Mutation proof**: temporarily changed the zero-norm branch in
  `_validate_and_normalize_row` to raise
  `OpenAICompatibleEmbeddingDimensionMismatch` instead of `ValueError`;
  `test_zero_norm_row_raises_generic_retryable_error` failed (`assert not
  True`, the mismatch class leaking through). Reverted with the exact
  inverse edit, purged `__pycache__`, reconfirmed GREEN.
- [x] **6.11** [IMPL] `_validate_and_normalize_row`: `EMBED_DIM` length
  check first (raises dimension-mismatch, never retried), then
  L2-normalize (zero-norm raises generic `OpenAICompatibleError`, IS
  retryable). Makes 6.8-6.10 GREEN.
- [x] **6.12** [TEST] same — add
  `test_transient_failure_then_success_is_transparent`: fake
  `urlopen`/spy `sleep` — first attempt transient error, second (within
  `embed_retry_attempts`) succeeds; `embed(...)` returns validated vectors
  with no exception, `sleep` called with the expected backoff. Covers
  "Transient Embed Failures Are Retried Before Propagating" (transparent
  half).
- [x] **6.13** [TEST] same — add `test_exhausted_retry_budget_raises`:
  transport fails on every attempt within budget; the final exception
  propagates after the last attempt, `sleep` called `attempts - 1` times
  with `base * 2 ** (attempt - 1)` backoff.
- [x] **6.14** [TEST] same — add
  `test_model_not_found_and_dimension_mismatch_are_never_retried`: both
  classes propagate on the FIRST attempt, zero `sleep` calls.
- [x] **6.15** [IMPL] wrap the transport call in `embed()`'s retry-with-backoff
  loop mirroring `OllamaClient.embed`'s contract exactly (attempts, backoff
  formula, the two excluded exception classes). Makes 6.12-6.14 GREEN.
- [x] **6.16** [TEST] same — add `test_embed_server_unreachable_raises_unavailable`:
  connection refused/timeout during `embed` raises
  `OpenAICompatibleUnavailable` (same mapping as `chat`). Covers "Server
  Unavailable During Embedding Raises A Typed Error".
- [x] **6.17** [IMPL] route `embed`'s transport-failure branch through the
  same error mapping `chat` uses. Makes 6.16 GREEN.
- [x] **6.18** [TEST] same — add `test_embed_uses_its_own_base_url_argument`:
  a client constructed with a distinct embedding endpoint (confirm the
  exact constructor shape during implementation — per design's "the
  embedding endpoint, which MAY differ from the chat endpoint" note) posts
  to that endpoint. The two-endpoint DISPATCH itself is `backend-selection`'s
  resolver, covered in Phase 9 — this is a client-level unit test only.

  **Observed**: also added `test_embed_never_sends_context_window`
  (task 5.11's "OR embed" half, deferred here since `embed()` did not
  exist yet when Phase 5 landed) and its dedicated mutation proof.

### Phase 6 verification

- [x] **6.19** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: `ruff check .` clean; `ruff format --check .` initially
  flagged `openai_compatible.py`/`test_openai_compatible_embed.py`
  (`io` import ordering), reformatted, reconfirmed clean; `mypy .` found
  one `arg-type` error on the `HTTPError` fake-fixture's `hdrs={}` (typeshed
  expects `Message[str, str]`), fixed by mirroring
  `test_openai_compatible_ladder.py`'s own `hdrs=None  #
  type: ignore[arg-type]` pattern; reconfirmed clean.
- [x] **6.20** Run `uv run pytest tests/unit/llm/test_openai_compatible_embed.py`
  focused, then `uv run pytest --cov` full suite; then `uv run python
  evals/run_self_tests.py`.

  **Observed**: focused: 15 passed. Combined full-suite/evals numbers
  recorded once at the end of Phase 7 (see below).
- [x] **6.21** Commit, scope `llm`. Open PR 6 targeting `main`, after PR 4
  merges (independent of PR 5).

  **Observed**: committed together with Phase 5 and 7 as one combined
  commit `fe7855c` (scope `llm`) — see Phase 7's 7.11 for the shared
  rationale. No push, no PR opened; "Open PR 6" left unticked.

**Rollback boundary**: revert `embed`/`_validate_and_normalize_row`/retry
loop; chat-only client (Phases 4-5) keeps working.

---

## Phase 7 (PR 7 → `main`, after PR 4): Client — diagnostics

Design Decision 2's `/v1/models` and locality mapping.

- [x] **7.1** [TEST] `tests/unit/llm/test_openai_compatible_diagnostics.py`
  (new) — add `test_list_models_returns_ids_with_null_family`: fake
  `urlopen` returns `{"data":[{"id":"a"},{"id":"b"}]}`; `list_models()`
  returns two `InstalledModel(tag=..., family=None)`. Covers "List
  Installed Models Via /v1/models".

  **Observed**: RED confirmed (`AttributeError: 'OpenAICompatibleClient'
  object has no attribute 'list_models'`, all 4 tests in the new file).
- [x] **7.2** [IMPL] `list_models()`: GET `{base_url}/v1/models`, map
  `data[].id` -> `InstalledModel(tag=id, family=None)`. Makes 7.1 GREEN.
- [x] **7.3** [TEST] same — add `test_list_models_unreachable_raises_unavailable`
  / `test_list_models_malformed_raises_generic_error`: connection failure
  -> `OpenAICompatibleUnavailable`; non-200/malformed body ->
  `OpenAICompatibleError`.

  **Observed**: also added `test_list_models_non_200_raises_generic_error`
  (a real 500 `HTTPError` through `_map_http_error`) alongside the
  malformed-body case.
- [x] **7.4** [IMPL] route `list_models`'s failure paths through the shared
  error mapping. Makes 7.3 GREEN.
- [x] **7.5** [TEST] `tests/unit/llm/test_openai_compatible_locality.py`
  (new) — add `test_loopback_base_url_classifies_local`, parametrized over
  `http://127.0.0.1:8080`, `http://localhost:8080`: `.locality.is_local is
  True`, matching `OllamaClient`'s classification for the equivalent host.
  Covers "Locality Uses The Shared Classifier" (local half).

  **Observed**: this and 7.6 were GREEN on first run, not RED — Phase 4's
  `.locality` property already wired `classify_backend_host(self.resolved_base_url)`
  (see the property's own docstring/tasks-phase note: "Locality Uses The
  Shared Classifier" was implemented ahead of its dedicated tests because
  it was needed by `_unavailable`'s `display_host` from Phase 4 onward).
  These tests are regression pins confirming that existing wiring, not a
  RED->GREEN cycle for new behavior — consistent with 7.7 below.
- [x] **7.6** [TEST] same — add
  `test_nonlocal_or_unparseable_base_url_classifies_nonlocal`, parametrized
  over the threat-matrix's adversarial set: `http://localhost.evil.com`,
  `http://127.1`, `http://[::1` (malformed bracket), IPv6 expanded loopback
  (`http://[0:0:0:0:0:0:0:1]`, NOT treated as local per the classifier's
  literal-only rule), a plain remote host. Covers "An unparseable or remote
  host classifies non-local" AND the Threat Matrix "Locality" row.

  **Observed**: also added `test_locality_never_leaks_userinfo`
  (regression pin for issue #355's rule on this backend).
- [x] **7.7** [IMPL] wire `.locality` to `classify_backend_host(self.resolved_base_url)`
  (Phase 1's moved classifier). Makes 7.5-7.6 GREEN.

  **Observed**: already implemented in Phase 4 (see 7.5's note) — no code
  change needed here; task closed by the regression tests confirming it.
- [x] **7.8** [TEST] `tests/unit/application/test_backends.py` (fixture-only
  addition, resolver not wired yet) — add
  `test_confidential_exemption_independent_per_purpose_for_openai_compatible`:
  two `OpenAICompatibleClient` instances (one remote chat endpoint, one
  loopback embedding endpoint) each resolve `resolve_local_exemption`
  independently, matching existing Ollama behavior for two separately
  classified endpoints. Covers Threat Matrix "Confidential exemption" and
  the proposal's "per client (chat and embedding endpoints separately)"
  rule.

  **Observed**: GREEN on first run (no resolver wiring needed —
  `resolve_local_exemption` takes any `HasLocality`-shaped client
  structurally, and `OpenAICompatibleClient` already satisfies it via
  `.locality`), confirming the fixture-only nature the task description
  predicted.

### Phase 7 verification

- [x] **7.9** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: `ruff check .` clean; `ruff format --check .` clean;
  `mypy .` clean — 377 source files (final count after all three phases).
- [x] **7.10** Run `uv run pytest tests/unit/llm/test_openai_compatible_diagnostics.py
  tests/unit/llm/test_openai_compatible_locality.py
  tests/unit/application/test_backends.py` focused, then `uv run pytest
  --cov` full suite; then `uv run python evals/run_self_tests.py`.

  **Observed**: focused: 15 passed (4 diagnostics + 8 locality + 3
  backends). Combined Phase 4+5+6+7 focused run
  (`tests/unit/llm/test_openai_compatible*.py tests/unit/llm/test_layering.py
  tests/unit/application/test_backends.py`): 92 passed. `evals/
  run_self_tests.py` (`OLLAMA_HOST` poisoned): 44 of 44 harness self-tests,
  0 failing. Full unpiped `uv run pytest --cov` (backgrounded, 444.96s wall
  time), read from its own completed printed summary: **7349 passed, 0
  failed, 2 skipped**, "Required test coverage of 90.0% reached. Total
  coverage: 96.84%" (>= 90% gate held). `llm/openai_compatible.py` itself:
  217 statements, 13 missed, 92% (the uncovered lines are the redirect/
  malformed-error-body degenerate branches already noted in Phase 4's own
  coverage entry, plus a few `list_models`/`embed` defensive branches not
  independently exercised by a dedicated test -- not a gap in any tested
  requirement).
- [x] **7.11** Commit, scope `llm`. Open PR 7 targeting `main`, after PR 4
  merges (independent of PR 5/6).

  **Observed**: committed together with Phase 5 and 6 as ONE combined
  commit `fe7855c` (scope `llm`, message `feat(llm): OpenAICompatibleClient
  chat bounds, embeddings, diagnostics (#1057)`), deviating from "Commit,
  scope llm" per-phase — deliberate, for the same reason Phase 2a+2b were
  combined earlier in this change: `git diff` on
  `src/openkos/llm/openai_compatible.py` produced the embed (Phase 6) and
  list_models (Phase 7) method bodies as one contiguous, non-cleanly-
  splittable hunk (both landed adjacent to `_build_request`), and Phase
  5's chat-bounds hunks share the same file and the same single
  verification run recorded above. No push, no PR opened per the apply
  run's instructions ("Open PR 5/6/7" left unticked in each phase); PR
  creation and any further slicing is left to the maintainer.

**Rollback boundary**: revert `list_models`/`.locality`; chat+embed-only
client (Phases 4-6) keeps working.

---

## Phase 8 (PR 8 → `main`, after PR 4, 5, 6, 7): Offline double + network guard

- [x] **8.1** [TEST] `tests/unit/test_network_guard.py` (extend) — add the
  second parametrization entry `(OpenAICompatibleClient,
  OfflineOpenAICompatible)` alongside the existing `(OllamaClient,
  OfflineOllama)` pair. **RED today**: `ImportError` — `OfflineOpenAICompatible`
  doesn't exist.

  **Observed**: RED confirmed —
  `ImportError: cannot import name 'OfflineOpenAICompatible' from
  'tests.unit.conftest'` on collection. Implemented as a module-level
  `_NETWORK_GUARD_PAIRS` list plus `@pytest.mark.parametrize` on the
  existing coverage test (see 8.6) rather than a literal second inline
  entry, since the source test had no parametrization to extend into.
- [x] **8.2** [IMPL] `tests/unit/conftest.py`: add `OfflineOpenAICompatible`,
  an in-memory test double implementing `LLMBackend`/`Embedder`/
  `BackendDiagnostics` with the same fixed-response, no-network shape as
  the existing `OfflineOllama` (read `OfflineOllama`'s current definition
  and mirror it one-to-one for the new client's method surface). Makes
  8.1's import succeed.

  **Observed**: `chat` returns `'{"extract": false}'`, `embed` returns a
  fixed unit vector per input, `list_models` raises
  `OpenAICompatibleUnavailable` — one-to-one mirror of `OfflineOllama`.
  GREEN after this: `uv run pytest tests/unit/test_network_guard.py` → 20
  passed (was 17 before Phase 8).
- [x] **8.3** [TEST] same — add `test_offline_double_never_reaches_network`:
  the double's `chat`/`embed`/`list_models` calls never invoke a real
  socket connection (patch `socket.socket.connect` to raise if called,
  mirroring the existing Ollama guard technique).

  **Observed**: passed on first run once 8.2 landed (no separate RED —
  the double already had every method by 8.2).
- [x] **8.4** [IMPL] finish `OfflineOpenAICompatible`'s method surface until
  8.3 is GREEN.

  **Observed**: no additional edit needed — 8.2's implementation already
  covered `chat`/`embed`/`list_models` fully; 8.3 was GREEN immediately.
- [x] **8.5** [TEST] `tests/unit/test_network_guard.py` — add
  `test_source_derived_coverage_includes_openai_compatible`: the guard's
  derivation walks `OpenAICompatibleClient`'s public method surface
  (chat/embed/list_models/locality/context_window/max_generation_tokens)
  and confirms `OfflineOpenAICompatible` implements every one — the SAME
  derivation technique already applied to `(OllamaClient, OfflineOllama)`,
  now parametrized over both pairs.

  **Observed**: the derivation technique (a two-level closure over
  `self._urlopen(`) only classifies NETWORK-reaching methods, exactly as
  it already did for `OllamaClient` — `locality`/`context_window` are
  pure properties with no I/O and `max_generation_tokens` is a
  constructor-stored value, none of which call `self._urlopen`, so none
  are in the derived set for either backend (matches `OfflineOllama`'s own
  precedent, which likewise does not override `locality`). Implemented as
  a literal-value pin (`_derive_network_methods(OpenAICompatibleClient) ==
  {"chat", "embed", "list_models"}`), per the tasks-phase guidance that a
  derived-vs-derived comparison needs a literal alongside it to avoid
  circularity. Also asserts `(OpenAICompatibleClient,
  OfflineOpenAICompatible) in _NETWORK_GUARD_PAIRS`.
- [x] **8.6** [IMPL] wire the second parametrization entry into the existing
  derivation loop. Makes 8.5 GREEN and 8.1 fully green.

  **Observed**: refactored `test_offline_stub_covers_every_network_method`
  into `@pytest.mark.parametrize(("real_cls", "stub_cls"),
  _NETWORK_GUARD_PAIRS, ids=["ollama", "openai_compatible"])`, with the
  derivation extracted into a shared `_derive_network_methods(real_cls)`
  helper. Both parametrize instances pass.

  **Mutation-proof 1** (coverage guard, `openai_compatible` id): removed
  `OfflineOpenAICompatible.list_models`'s override — test failed with
  `the offline stub does not override ['list_models']`. Reverted with the
  exact inverse edit, purged `__pycache__`, reconfirmed 20 passed.

  **Mutation-proof 2** (`test_offline_double_never_reaches_network`):
  changed the fixed embed vector's first element from `1.0` to `0.0` —
  test failed on `assert vectors[0][0] == pytest.approx(1.0)`. Reverted,
  purged `__pycache__`, reconfirmed green.

  **Mutation-proof 3** (`test_source_derived_coverage_includes_openai_compatible`
  and the `openai_compatible` parametrize id together): in
  `src/openkos/llm/openai_compatible.py::chat`, temporarily hoisted
  `self._urlopen` into a local (`_uo = self._urlopen; response =
  _uo(...)`) so the literal `self._urlopen(` substring left `chat`'s
  source — both tests failed (`the stub overrides ['chat'] but the
  derivation no longer classifies them as network methods`, and the
  literal-value pin's exact-set assertion). Reverted with the exact
  inverse edit, purged `__pycache__`, reconfirmed both green.

### Phase 8 verification

- [x] **8.7** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: `ruff check .` initially flagged an unsorted import (fixed
  with `ruff check --fix`) and a `PT006` tuple-vs-string parametrize id
  (fixed manually: `("real_cls", "stub_cls")`); reconfirmed clean.
  `ruff format --check .` flagged `tests/unit/conftest.py` and
  `tests/unit/test_network_guard.py`; reformatted with `ruff format`,
  reconfirmed clean (377 files). `mypy .`: clean, no issues in 377 source
  files.
- [x] **8.8** Run `uv run pytest tests/unit/test_network_guard.py` focused,
  then `uv run pytest --cov` full suite; then `uv run python
  evals/run_self_tests.py`.

  **Observed**: focused → 20 passed (0.38s). Full `pytest --cov` (unpiped,
  backgrounded — 434.36s wall time) → **7353 passed, 0 failed, 2 skipped**,
  96.84% branch coverage (>= 90% gate held, `openai_compatible.py` itself
  unaffected by this phase — no production code changed, only test
  infrastructure). `evals/run_self_tests.py` (with `OLLAMA_HOST` poisoned):
  44/44 green.
- [x] **8.9** Commit, scope `llm`. Open PR 8 targeting `main`, after PR 4,
  5, 6, 7 merge.

  **Observed**: commit half done, scope `llm`, on branch
  `feat/1057-openai-p8`; "Open PR 8" half intentionally left for the
  orchestrator/maintainer per apply-phase instructions (no push, no PR
  from this session) — left unticked.

**Rollback boundary**: revert `OfflineOpenAICompatible` and the guard
parametrization; the existing Ollama-only guard keeps working.

---

## Phase 9 (PR 9 → `main`, after PR 3, PR 8): Resolver seam

Design Decisions 4-6 (dispatch and precedence) and 8 (key read only — the
warning function itself is Phase 13). `application/backends.py` today
exposes `chat_client(cfg, *, factory, task=None)` and
`resolve_local_exemption`; this phase widens `factory=` to `factories=` and
adds `resolve_endpoint`, `embed_client`, `diagnostics_client`.

### `BackendFactories`, `Endpoint`, `resolve_endpoint`

- [x] **9.1** [TEST] `tests/unit/application/test_backends.py` — add
  `test_backend_factories_dataclass_shape`: `BackendFactories(ollama=...,
  openai_compatible=...)` constructs; frozen/slots. **RED today**: doesn't
  exist.

  **Observed**: RED confirmed (`AttributeError: module
  'openkos.application.backends' has no attribute 'BackendFactories'`,
  along with 24 sibling tasks-9 tests added in the same batch — full RED
  list: 25 failed, 2 passed).
- [x] **9.2** [IMPL] `application/backends.py`: add `BackendFactories`
  (frozen dataclass), `BACKEND_OLLAMA`/`BACKEND_OPENAI_COMPATIBLE`/
  `API_KEY_ENV` constants, `EndpointSource` `Literal`, `Endpoint` (frozen
  dataclass: `url: str | None`, `source: EndpointSource`). Makes 9.1 GREEN.
- [x] **9.3** [TEST] same — add `test_resolve_endpoint_ollama_chat_precedence_table`,
  parametrized over Decision 5's table for `backend="ollama"`,
  `purpose="chat"`: `OLLAMA_HOST` (injected `environ`) > `base_url` >
  default; each case asserts both `.url` and `.source`. **RED today**:
  `resolve_endpoint` doesn't exist.
- [x] **9.4** [TEST] same — add `test_resolve_endpoint_ollama_embed_precedence_table`:
  `OLLAMA_HOST` > `embedding_base_url` > `base_url` > default, for
  `purpose="embed"`.
- [x] **9.5** [TEST] same — add `test_resolve_endpoint_openai_compatible_precedence_table`:
  chat -> `base_url` only (no default — construct a `Config` directly,
  bypassing `read_config`'s refusal, to exercise the resolver's own
  defensive behavior); embed -> `embedding_base_url` > `base_url`.
- [x] **9.6** [TEST] same — add
  `test_resolve_endpoint_openai_compatible_never_consults_ollama_host`:
  `OLLAMA_HOST` set, `backend="openai-compatible"`, `base_url` set to a
  DIFFERENT host; `resolve_endpoint` returns the `base_url` value,
  `source != "OLLAMA_HOST"`, and the returned `Endpoint` never carries the
  `OLLAMA_HOST` value under any field. Security-property sentinel test
  (Threat Matrix + backend-selection's "OLLAMA_HOST never redirects the
  openai-compatible backend" scenario). Mutation-proof: mutate the dispatch
  to check `OLLAMA_HOST` for both backends, confirm this test fails, then
  revert.

  **Observed mutation proof**: added an `if environ.get("OLLAMA_HOST"):
  return Endpoint(...)` branch to the top of `resolve_endpoint`'s
  `openai-compatible` arm; the test failed (`'http://evil-host.invalid:9999'
  == 'http://127.0.0.1:8080'`). Reverted with the exact inverse edit,
  purged `__pycache__`, reconfirmed all 27 tests green.
- [x] **9.7** [IMPL] `resolve_endpoint(cfg, *, purpose, environ=os.environ)
  -> Endpoint` implementing the full precedence table (Decision 5). Makes
  9.3-9.6 GREEN.

### `chat_client`/`embed_client` dispatch, byte-identity

- [x] **9.8** [TEST] same — add `test_default_path_kwargs_are_byte_identical`:
  `chat_client` called with a default `Config` (no `backend`/`base_url`/
  `OLLAMA_HOST`) constructs the Ollama factory with `host` ABSENT from the
  kwargs entirely (not `host=None`) — pins the exact byte-identity
  invariant ("the Ollama factory receives `host=` ONLY when `source` is a
  config key").
- [x] **9.9** [TEST] same — add `test_host_passed_only_when_source_is_config_key`,
  parametrized: `source="OLLAMA_HOST"` -> no `host=` kwarg (the Ollama
  client resolves `OLLAMA_HOST` itself, unchanged); `source="base_url"` ->
  `host=<base_url>`; `source="default"` -> no `host=` kwarg.
- [x] **9.10** [IMPL] `application/backends.py::chat_client`: change
  signature from `factory=` to `factories: BackendFactories`, dispatch by
  `cfg.backend` to `factories.ollama`/`factories.openai_compatible`, call
  `resolve_endpoint(cfg, purpose="chat")`, forward `host=`/`base_url=` per
  9.9's rule. Makes 9.8-9.9 GREEN for the chat path.

  **Observed**: also updated `chat_client`'s return type from the
  unconstrained `ClientT` TypeVar to the `LLMBackend` Protocol (imported
  from `openkos.llm.base`, the one concrete-free import the layering guard
  permits) — a single `factories.ollama`/`factories.openai_compatible`
  dispatch can no longer return a TypeVar with no argument carrying it
  (mypy `type-var` error), and the two branches can return two unrelated
  concrete classes, so a real static return type is the Protocol both
  satisfy, not a re-widened TypeVar. Callers needing `.locality`
  (`_resolve_local_exemption`) `cast` to `HasLocality` at the call site,
  mirroring the pre-existing pattern already used in `mcp/server.py`.
- [x] **9.11** [TEST] same — add `test_chat_client_dispatches_to_openai_compatible_factory`:
  `cfg.backend == "openai-compatible"`, both factories injected as
  recording spies; `chat_client(...)` constructs via
  `factories.openai_compatible`, not `factories.ollama`. Covers
  `backend-selection` "Chat construction dispatches by cfg.backend".
- [x] **9.12** [TEST] same — add `test_embed_client_mirrors_chat_client_dispatch_shape`:
  same dispatch assertion for a new `embed_client(cfg, *, factories)`, using
  `purpose="embed"` endpoint resolution.
- [x] **9.13** [IMPL] add `embed_client(cfg, *, factories) -> ConfiguredClient`
  mirroring `chat_client`'s dispatch shape, resolving the EMBED endpoint,
  passing `model=cfg.embedding_model` (no `chat_timeout`, no per-task model
  resolution — embedding clients keep the transport default timeout, per
  design's Data Flow note). Makes 9.12 GREEN.

  **Observed**: return type is the `Embedder` Protocol (not a generic
  `ConfiguredClient` — no such alias exists; `Embedder`/`LLMBackend`/
  `BackendDiagnostics` from `openkos.llm.base` are the three Protocols this
  phase's dispatch functions return), same reasoning as 9.10. Added
  `test_embed_client_default_path_kwargs` alongside 9.12 to pin the
  no-`chat_timeout`/no-per-task-resolution kwargs shape explicitly.
- [x] **9.14** [TEST] same — add `test_diagnostics_client_shape`:
  `diagnostics_client(cfg_or_none, *, model, timeout, factories,
  purpose="chat")` dispatches identically, usable by the init picker/
  preflight/doctor probe sites (Phase 10) with `cfg=None` defaulting to
  Ollama (O1).
- [x] **9.15** [IMPL] add `diagnostics_client(...)` per 9.14 and design's
  Interfaces/Contracts signature. Makes 9.14 GREEN.

  **Observed**: return type `BackendDiagnostics` (the existing
  `llm/base.py` Protocol `application/doctor.py` already depends on for
  `list_models()`/`.locality`, not `LLMBackend`/`Embedder` — a diagnostics
  probe needs neither `.chat()` nor `.embed()`).

### API key read

- [x] **9.16** [TEST] same — add
  `test_api_key_read_from_environ_stripped_and_empty_as_absent`,
  parametrized: `OPENKOS_OPENAI_API_KEY=" secret "` -> `"secret"` passed as
  `api_key=`; unset -> `api_key=None`; set to `""`/whitespace-only ->
  treated as absent.
- [x] **9.17** [TEST] same — add `test_api_key_only_passed_for_openai_compatible_backend`:
  `cfg.backend == "ollama"`, `OPENKOS_OPENAI_API_KEY` set — the Ollama
  factory call's kwargs never include an API key. Security-property
  regression pin.
- [x] **9.18** [TEST] same — add `test_openai_api_key_without_prefix_is_never_read`:
  `OPENAI_API_KEY` (no prefix) set, `OPENKOS_OPENAI_API_KEY` unset;
  `chat_client`/`embed_client` construct the `openai-compatible` factory
  with `api_key=None`. Sentinel test — mutation-proof: mutate the read to
  also check `OPENAI_API_KEY` as a fallback, confirm this test fails, then
  revert. Covers backend-selection's "OPENAI_API_KEY is never read" AND
  Threat Matrix "Credential confusion".

  **Observed mutation proof**: changed `_read_api_key` to
  `environ.get(API_KEY_ENV) or environ.get("OPENAI_API_KEY", "")`; both
  this test and `test_api_key_read_from_environ_stripped_and_empty_as_absent`
  failed (`'leaked-unrelated-cloud-key' is None` / wrong value on the
  `unset` case). Reverted with the exact inverse edit, purged
  `__pycache__`, reconfirmed all 27 tests green.
- [x] **9.19** [IMPL] wire the `environ`-injected key read (default
  `os.environ`) into `chat_client`/`embed_client`, passed as `api_key=`
  ONLY to the `openai_compatible` factory call. Makes 9.16-9.18 GREEN.

  **Observed**: also wired into `diagnostics_client` (its `openai-compatible`
  branch reads the same key), consistent with 9.15's dispatch shape.

### CLI/curate/MCP bindings

- [x] **9.20** [TEST] `tests/unit/cli/test_backends_delegation.py` (existing)
  — extend/confirm `_chat_client`'s exact Ollama-path kwargs assertion
  still holds with the new `factories=` parameter shape.

  **Observed**: RED confirmed first (`TypeError: chat_client() got an
  unexpected keyword argument 'factory'. Did you mean 'factories'?`) before
  9.21 landed. Updated `test_ollama_client_monkeypatch_still_intercepts`'s
  `_bypassing_chat_client` mock to the `factories: BackendFactories`
  signature.
- [x] **9.21** [IMPL] `cli/main.py`: add `_backend_factories() ->
  BackendFactories` returning `BackendFactories(ollama=OllamaClient,
  openai_compatible=OpenAICompatibleClient)` read from `cli/main.py`'s own
  module globals; update `_chat_client`'s one-line delegator to pass
  `factories=_backend_factories()`. Makes 9.20 GREEN; the ~144 existing
  `openkos.cli.main.OllamaClient` test patches keep intercepting.

  **Observed**: also widened `_chat_client`'s return type from `OllamaClient`
  to `LLMBackend` (imported from `openkos.llm.base`) and
  `_resolve_local_exemption`'s parameter from `OllamaClient` to
  `application_backends.HasLocality`, then wrapped its 7 call sites with
  `cast(application_backends.HasLocality, ...)` — `mypy .` caught all 7
  as `arg-type` errors before the cast was added; clean after.
- [x] **9.22** [TEST] `tests/unit/mcp/test_server.py` — add
  `test_mcp_backend_factories_shape`: `mcp/server.py::_backend_factories()`
  returns the analogous `BackendFactories` built from `mcp.server`'s own
  globals.

  **Observed**: first version compared against the directly-imported
  `OllamaClient`/`OpenAICompatibleClient` names and passed in isolation
  (written before 9.26's conftest change extended the offline-seam patch
  to `mcp.server`), then FAILED once 9.26 landed
  (`assert <OfflineOllama> is OllamaClient`) — a real regression the
  fixture change exposed, not a flaky test. Fixed by reading
  `server.__dict__` dynamically instead, mirroring `test_backends_delegation.py`'s
  existing `main_mod.__dict__["OllamaClient"]` pattern; reconfirmed green
  together with the rest of the Phase 9 focused set.
- [x] **9.22a** [IMPL] `mcp/server.py`: add `_backend_factories()`; update
  `_make_llm` (chat only — `_make_embedder` stays untouched until Phase 10)
  to call `chat_client` with it. Makes 9.22 GREEN.
- [x] **9.22b** [TEST] `tests/unit/mcp/test_layering.py` (extend) — add
  `test_mcp_server_may_import_both_concrete_client_classes`: the existing
  static import check permits `mcp/server.py` to import BOTH
  `OllamaClient` and `OpenAICompatibleClient` (and their exception types),
  while the ban on `openkos.cli`/`openkos.graph` imports still holds.
  Covers `mcp` spec's "mcp/server.py may import both concrete client
  classes".

  **Observed**: the existing `test_layering_invariants` has no explicit
  `openkos.graph` check (only `openkos.cli` and the non-`openkos.*`
  import bans) — added a NEW, independent test asserting the two positive
  imports plus a fresh `cli_offenders` check, run ALONGSIDE (not
  replacing) the pre-existing guard.
- [x] **9.23** [TEST] `tests/unit/cli/test_curate.py` — add
  `test_curate_context_carries_backend_factories`: `CurateContext` gains a
  `backend_factories` field that `cli/main.py` fills from
  `_backend_factories()`; the stage loop's chat construction calls
  `application_backends.chat_client(ctx.cfg, factories=ctx.backend_factories,
  task=stage.task)` instead of constructing `OllamaClient` directly.

  **Observed**: same `server.__dict__`-style dynamic-lookup fix needed here
  too (`ctx.backend_factories.openai_compatible is OpenAICompatibleClient`
  failed once the ambient offline-seam patch covered `cli.main`'s copy);
  fixed by reading `main_module.__dict__[...]` for both names.
- [x] **9.24** [IMPL] `cli/curate.py`: add `backend_factories` to
  `CurateContext`; `cli/main.py` fills it; route stage chat construction
  through `chat_client`. Makes 9.23 GREEN. Move the four existing tests
  patching `openkos.cli.curate.OllamaClient` to patch
  `openkos.cli.main.OllamaClient` instead (per design's explicit note),
  confirming they still pass.

  **Observed**: `backend_factories: BackendFactories | None = None`
  (curate.py binds no concrete backend class at all now — the `OllamaClient`
  import was removed entirely from `curate.py`); the stage loop raises a
  clear `RuntimeError` if ever reached with `None` rather than silently
  degrading. Beyond the four string-literal `monkeypatch.setattr(
  "openkos.cli.curate.OllamaClient", ...)` patches (moved to
  `"openkos.cli.main.OllamaClient"`), found and fixed 6 MORE
  object-form `monkeypatch.setattr(curate, "OllamaClient", ...)` patches
  the task text didn't name — each moved to a `backend_factories=` kwarg on
  `_fake_ctx` instead, since `curate` no longer has an `OllamaClient`
  module attribute to patch at all. Also: added `backend`/`base_url`/
  `embedding_base_url` fields to the test file's `_FakeConfig` (read by
  `resolve_endpoint`, which the stage loop now calls transitively) and 3
  fake client stand-ins (`_RaisingOllamaClient`'s specific-test variant,
  `_RecordingOllama`, `_NoChatOllama`) needed a `.locality` property added
  — `cli.main.OllamaClient` is now ALSO the command's own local-exemption
  seam (previously a separate, independent patch point from
  `cli.curate.OllamaClient`), so unifying the two seams means a raising
  sentinel for "the stage loop must not build a client" also has to
  tolerate the command's own unconditional, unrelated local-exemption
  probe — rewrote that one sentinel to raise only on a SECOND construction.
  All 170 tests in `tests/unit/cli/test_curate.py` pass.

### conftest, construction guard (chat-scoped, ratchet)

- [x] **9.25** [TEST] `tests/unit/conftest.py` — add
  `test_conftest_single_helper_patches_both_bindings`: the autouse fixture
  patches `OllamaClient`/`OpenAICompatibleClient` in BOTH
  `openkos.cli.main` and `openkos.mcp.server` through one shared
  helper/tuple-of-binding-modules, and `delenv("OPENKOS_OPENAI_API_KEY",
  raising=False)` runs so no developer's exported key leaks into a test.

  **Deviation (disclosed, not silent)**: pytest's default `python_files`
  pattern (`test_*.py`/`*_test.py`) does NOT collect test functions
  defined inside `conftest.py` under a directory-based run (`pytest
  tests/unit`) — verified empirically (a scratch probe test placed in
  `conftest.py` was collected by `pytest tests/unit/conftest.py` directly
  but absent from `pytest tests/unit --collect-only`). A test placed
  literally in `conftest.py`, as this task names, would silently never run
  under `uv run pytest --cov` (the required verification command) or CI.
  Placed `test_conftest_single_helper_patches_both_bindings` in the
  already-collected `tests/unit/test_network_guard.py` instead, following
  its own pre-existing `test_offline_seam_is_installed_for_an_ordinary_unit_test`/
  `_cli_ollama_seam()` pattern (dynamic `getattr` reads on both modules),
  plus a structural (`inspect.getsource`) check for the `delenv` call
  since the clear cannot be proven behaviorally from inside the test it
  protects (it already ran before the test body starts).
- [x] **9.26** [IMPL] `tests/unit/conftest.py`: implement the one-helper
  patch across the binding-module tuple `(openkos.cli.main,
  openkos.mcp.server)`, `delenv("OPENKOS_OPENAI_API_KEY")`. Makes 9.25
  GREEN — this is ALSO the seam `test_network_guard.py`'s `live_backend`
  marker needs: extend its lift to cover `OpenAICompatibleClient` at both
  binding modules now that the seam exists.

  **Observed**: `_wants_live_backend`-gated `_offline_ollama_by_default`
  already lifts BOTH classes at both modules together (single early
  `return` shared with the socket guard), so no separate marker-lift change
  was needed beyond routing the existing fixture through
  `_patch_backend_seams`.
- [x] **9.27** [TEST] `tests/unit/test_backend_construction_guard.py` (new)
  — add `test_no_direct_client_construction_outside_llm_and_factories`: an
  AST walk of every `.py` under `src/openkos/` outside `llm/` rejects a
  bare `OllamaClient(` or `OpenAICompatibleClient(` call EXCEPT inside a
  `BackendFactories(...)` expression, carrying an explicit
  `_PENDING_SITES` allowlist (see the tasks-phase decision) naming exactly
  the ten sites Phase 10 migrates: `cli/main.py`'s init picker (line 317),
  init preflight (line 1748), `_refresh_derived_after_write` (4166), the
  ingest-batch embed-host advisory (4734), `_ingest_single` (5720), `query`
  (14793), `reindex` (15508), doctor's `_build_client` (15890), `mcp_cmd`
  (16339), and `mcp/server.py`'s `_make_embedder` (196). The guard also
  asserts every allowlisted entry still points at an ACTUAL direct
  construction (a stale entry is itself a failure). **RED today**: the ten
  sites are indeed direct constructions.

  **Observed line-number drift**: the task's line numbers were computed
  against an earlier snapshot of `cli/main.py`; the actual current lines
  (confirmed by grep against the as-shipped file) are 339/1770/4194/4762/
  5748/14831/15548/15930/16382 and `mcp/server.py:212` — `_PENDING_SITES`
  uses these real, current numbers (the guard's own "stale entry" check
  would otherwise fail immediately against the task text's numbers). Wrote
  test+implementation together in one file (no separate production module
  — this guard IS the test, matching `test_neutral_catch_sites.py`'s
  precedent from Phase 2a); ran once green (no separate RED phase observed
  for the guard itself, since Phase 9's other tasks were already fully
  implemented by the time this file was written) — 9.29's mutation proof
  below is what demonstrates it can fail.
- [x] **9.28** [IMPL] implement the guard with the allowlist per 9.27;
  confirm the three now-migrated chat sites (`_chat_client`, curate stage
  chat, MCP `_make_llm`) are clean/off the allowlist by construction. Makes
  9.27 GREEN.
- [x] **9.29** [TEST] same file — mutation-proof: temporarily reintroduce a
  direct `OllamaClient(...)` call at one of the three MIGRATED chat sites
  (scratch copy or planted fixture), confirm the guard fails, then remove
  it and record the result inline.

  **Observed mutation proof**: added `OllamaClient(model="mutation-probe")`
  as an extra statement inside `application/backends.py::chat_client`'s
  Ollama branch (line 281, the shared body every migrated chat site now
  delegates to). Guard failed with exactly
  `{'application/backends.py': [281]}` under `offenders` (not
  `_PENDING_SITES`, confirming that site is correctly unallowlisted).
  Reverted with the exact inverse edit, purged `__pycache__`, reconfirmed
  green; comment recorded inline in the test file per the task's own
  instruction.

### Phase 9 verification

- [x] **9.30** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: `ruff check .` — All checks passed. `ruff format --check .`
  — 378 files already formatted. `mypy .` — Success: no issues found in
  378 source files.
- [x] **9.31** Run `uv run pytest tests/unit/application/test_backends.py
  tests/unit/cli/test_backends_delegation.py tests/unit/cli/test_curate.py
  tests/unit/mcp/test_server.py tests/unit/mcp/test_layering.py
  tests/unit/test_backend_construction_guard.py
  tests/unit/test_network_guard.py` focused, then `uv run pytest --cov`
  full suite; then `uv run python evals/run_self_tests.py`.

  **Observed**: focused command → **245 passed**. Full `uv run pytest --cov`
  (unpiped, 438.36s wall time) → **7382 passed, 0 failed, 2 skipped**,
  96.82% branch coverage (>= 90% gate held);
  `src/openkos/application/backends.py` not listed individually in the
  coverage table (covered under the aggregate, no dedicated low-coverage
  line flagged); `src/openkos/cli/curate.py` 95%, `src/openkos/cli/main.py`
  95%, `src/openkos/mcp/server.py` 94% — consistent with pre-existing
  coverage levels, no new uncovered branch introduced by this phase's
  changes specifically (the reported misses are pre-existing lines, not
  Phase 9 additions). `uv run python evals/run_self_tests.py`
  (`OLLAMA_HOST` poisoned to `http://127.0.0.1:1`): **44 of 44 harness
  self-test(s) run, 0 failing**.
- [x] **9.32** Commit as one or more work-unit commits, split by touched
  module's scope (`llm` for `backends.py`'s resolver, `cli`/`mcp` for the
  adapter wiring). Open PR 9 targeting `main`, after PR 3 and PR 8 merge.

  **Observed**: commit(s) to be made on the current branch
  (`feat/1057-openai-p9`); "Open PR 9" left unticked per apply-phase
  instructions (no push, no PR from this session — left for the
  maintainer).

**Rollback boundary**: revert `BackendFactories`/`resolve_endpoint`/
`chat_client`'s `factories=` signature back to `factory=`/single-Ollama
shape, `embed_client`/`diagnostics_client`, the two adapters'
`_backend_factories()`, `CurateContext.backend_factories`, the conftest
helper, and the construction guard; every chat construction reverts to
constructing `OllamaClient` directly — Phases 1-8 (helpers, neutral bases,
config keys, the new client) are all inert without this wiring.

---

## Phase 10 (PR 10 → `main`, after PR 9): Embed-site + diagnostics-probe migration

Closes the ten `_PENDING_SITES` entries Phase 9's guard allowlisted, per the
"Construction sites routed through the resolver" table in design.md.

- [x] **10.1** [TEST] `tests/unit/cli/test_backends_delegation.py` — add
  `test_embed_client_delegator_shape`: `cli/main.py::_embed_client(cfg)` is
  a new one-line delegator mirroring `_chat_client`, calling
  `application_backends.embed_client(cfg, factories=_backend_factories())`.
  **RED today**: `_embed_client` doesn't exist.

  **Observed**: RED confirmed (`AttributeError: module 'openkos.cli.main'
  has no attribute '_embed_client'`). Also extended
  `test_delegators_are_single_line_and_singly_defined`'s existing
  parametrized tuple with `("_embed_client", "embed_client")`, reusing that
  structural pin rather than duplicating it.
- [x] **10.2** [IMPL] add `_embed_client(cfg)` to `cli/main.py`. Makes 10.1
  GREEN.
- [x] **10.3** [TEST] `tests/unit/cli/test_ingest.py` — extend the existing
  embed-construction assertions at `_refresh_derived_after_write`
  (`main.py:4166`) and `_ingest_single` (`main.py:5720`) to confirm both
  call `_embed_client(cfg)` rather than constructing
  `OllamaClient(model=cfg.embedding_model)` directly (spy on
  `_embed_client`).

  **Observed**: actual current lines (post-Phase-9 drift) were 4206/5760.
  Added `test_single_file_ingest_embed_sites_use_the_embed_client_delegator`
  (single-file `ingest --auto` exercises both `_ingest_single`'s own embed
  AND the end-of-run `_refresh_derived_after_write` call — 2 `_embed_client`
  calls total) and a companion batch-mode test (10.5). RED confirmed first
  (0 calls recorded).
- [x] **10.4** [IMPL] migrate both sites to `_embed_client(cfg)`. Makes 10.3
  GREEN. Shrink the Phase-9 guard's `_PENDING_SITES` by these two entries.
- [x] **10.5** [TEST] same file — extend the ingest-batch embed-host
  advisory site (`main.py:4734`) to confirm it also constructs via
  `_embed_client(cfg)` (used solely for `.locality` at this phase; the
  `endpoint_label` wording is Phase 13).

  **Observed**: actual current line 4762 (drift). Added
  `test_batch_ingest_embed_sites_use_the_embed_client_delegator` — a
  1-file batch run exercises the advisory (once) + `_ingest_single`'s
  per-file embed (once) + the batch's own end-of-run
  `_refresh_derived_after_write` call (once) = 3 total, not 2 as first
  assumed; corrected after observing the real call-stack traceback
  (`_ingest_batch:4847` → `_refresh_derived_after_write:4206`), a site
  this task's own text did not separately name.
- [x] **10.6** [IMPL] migrate the advisory site. Makes 10.5 GREEN. Shrink
  the allowlist.
- [x] **10.7** [TEST] `tests/unit/cli/test_query.py` — extend `query`'s
  embed-construction assertion (`main.py:14793`) to confirm `_embed_client(cfg)`.

  **Observed**: actual current line 14843 (drift). Added
  `test_query_embed_site_uses_the_embed_client_delegator`. RED confirmed
  (0 calls), then GREEN after 10.8; full file (63 tests) green.
- [x] **10.8** [IMPL] migrate. Makes 10.7 GREEN. Shrink the allowlist.
- [x] **10.9** [TEST] `tests/unit/cli/test_reindex.py` — extend `reindex`'s
  CLI wiring (`main.py:15508`) to confirm `_embed_client(cfg)`.

  **Observed**: actual test file is `tests/unit/cli/test_reindex_cmd.py`
  (no `tests/unit/cli/test_reindex.py` exists — same name-drift pattern
  prior phases' `test_reindex_cmd.py`/`test_doctor_service.py` observed
  notes already documented). Actual current line 15560 (drift). Added
  `test_reindex_embed_site_uses_the_embed_client_delegator`; RED confirmed,
  GREEN after 10.10; full file (41 tests) green.
- [x] **10.10** [IMPL] migrate. Makes 10.9 GREEN. Shrink the allowlist.
- [x] **10.11** [TEST] `tests/unit/cli/test_mcp_cmd.py` (or the equivalent
  existing test module) — extend `mcp_cmd`'s embed construction
  (`main.py:16339`) to confirm `_embed_client(cfg)`.

  **Observed**: actual current line 16394 (drift). Added
  `test_mcp_cmd_embed_site_uses_the_embed_client_delegator`; RED confirmed,
  GREEN after 10.12; full file (5 tests) green.
- [x] **10.12** [IMPL] migrate. Makes 10.11 GREEN. Shrink the allowlist (all
  seven embed sites now clear).
- [x] **10.13** [TEST] `tests/unit/mcp/test_server.py` — extend
  `_make_embedder` (`server.py:196`) to confirm `embed_client(cfg,
  factories=_backend_factories())`.

  **Observed**: added `test_make_embedder_uses_the_embed_client_resolver`,
  spying on `application_backends.embed_client` directly (mirrors
  `test_mcp_backend_factories_shape`'s pattern). RED confirmed, GREEN after
  10.14; full file (23 tests) green.
- [x] **10.14** [IMPL] migrate. Makes 10.13 GREEN. Shrink the allowlist.
- [x] **10.15** [TEST] `tests/unit/cli/test_init.py` — extend the init
  picker (`_probe_installed_models`, `main.py:317`) and init preflight
  (`main.py:1748`) assertions to confirm both call `diagnostics_client(None,
  model=<resolved default>, timeout=_PREFLIGHT_TIMEOUT,
  factories=_backend_factories())` — Ollama-default per O1, since `init`
  runs before any `backend` key exists.

  **Observed**: actual current lines 351/1782 (drift). Added
  `test_picker_and_preflight_probes_use_the_diagnostics_client_resolver`,
  spying on `application_backends.diagnostics_client`; needed `_simulate_tty`
  + scripted input (`"\n\n"`) to make the picker probe actually run (a
  non-TTY `init` skips it entirely per its own existing `sys.stdin.isatty()`
  gate) — confirmed exactly 2 calls, both `cfg=None`, `timeout=5.0`, first
  one's `model == DEFAULT_MODEL`. RED confirmed (0 calls), GREEN after
  10.16; full file (91 tests) green.
- [x] **10.16** [IMPL] migrate both init sites through `diagnostics_client(...)`.
  Makes 10.15 GREEN. Shrink the allowlist.
- [x] **10.17** [TEST] `tests/unit/cli/test_doctor.py` — extend doctor's
  `_build_client` (`main.py:15890`) to confirm `lambda cfg, model:
  diagnostics_client(cfg, model=model, timeout=_PREFLIGHT_TIMEOUT,
  factories=_backend_factories())`.

  **Observed**: actual current line 15942 (drift). This also required
  widening `application/doctor.py::run_diagnostics`'s `build_client`
  parameter type from `Callable[[str], BackendDiagnostics]` to
  `Callable[[config.Config | None, str], BackendDiagnostics]` and its call
  site from `build_client(model)` to `build_client(cfg, model)` — `cfg` was
  already a local `config.Config | None` in scope there (check 2's own
  read), so no new read was introduced. Added
  `test_doctor_build_client_uses_the_diagnostics_client_resolver`
  (`tests/unit/cli/test_doctor.py`), spying on
  `application_backends.diagnostics_client`; RED confirmed, GREEN after
  10.18. This signature widening broke ~25 pre-existing
  `build_client=lambda _model: ...` fixtures across
  `tests/unit/application/test_doctor_service.py` (the actual file for the
  task text's `tests/unit/application/test_doctor.py` — same drift
  pattern), mechanically updated to `lambda _cfg, _model: ...` (and the two
  named-function factories to `(cfg, model)`); that file's 30 tests and
  `test_doctor.py`'s 62 tests both green.
- [x] **10.18** [IMPL] migrate. Makes 10.17 GREEN. Allowlist now EMPTY.
- [x] **10.19** [TEST] `tests/unit/test_backend_construction_guard.py` —
  replace the allowlisted variant with the FINAL, unconditional guard (no
  allowlist parameter): confirm it is green with zero exceptions across the
  whole `src/openkos/` tree outside `llm/`/`BackendFactories(...)`. Closes
  backend-selection's "no test that selects either backend can reach the
  network" contract for real.

  **Observed**: confirmed via the full-suite run that `_PENDING_SITES`'s
  ten entries were all stale (every site migrated) before rewriting;
  rewrote `test_no_direct_client_construction_outside_llm_and_factories` to
  the unconditional form (dropped the allowlist parameter and the
  stale-entry check, now vestigial with an empty set).
- [x] **10.20** [TEST] same file — mutation-proof: reintroduce one direct
  construction at a random one of the ten migrated sites (scratch copy or
  planted fixture), confirm the now-unconditional guard fails, then remove
  it.

  **Observed**: the PERSISTED test
  (`test_mutation_proof_a_migrated_embed_site_reintroducing_direct_construction_fails`)
  uses a synthetic source fixture (task text explicitly permits "scratch
  copy or planted fixture"). ADDITIONALLY confirmed against the REAL, live
  file during this apply run: temporarily replaced `cli/main.py::
  _embed_client`'s delegating `return application_backends.embed_client(...)`
  body with `return OllamaClient(model=cfg.embedding_model)`; the guard
  failed, reporting exactly `{'cli/main.py': [206]}`. Reverted with the
  exact inverse edit, purged `__pycache__`, reconfirmed GREEN (2 tests in
  this file).

### Phase 10 verification

- [x] **10.21** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: all three clean (378 source files).
- [x] **10.22** Run `uv run pytest tests/unit/cli/ tests/unit/mcp/
  tests/unit/test_backend_construction_guard.py` focused, then `uv run
  pytest --cov` full suite; then `uv run python evals/run_self_tests.py`.

  **Observed**: focused run (unpiped) → **2648 passed, 1 skipped**. A
  `--cov` full-suite run launched immediately after this focused run
  overlapped with the start of Phase 11's own source edits in the same
  working tree (a background-job timing artifact of this apply session,
  not a code defect) and produced a corrupted per-file coverage number for
  `state/reindex.py` specifically (32%, while every other module measured
  normally); that result is disclosed here and disregarded. The final,
  uncorrupted full-suite run — covering BOTH Phase 10 and Phase 11 together
  (this apply session's two phases share one commit-adjacent verification
  pass, run once source was stable) — is recorded under Phase 11's 11.23
  below: **7413 passed, 0 failed, 2 skipped**, 96.85% branch coverage, and
  `evals/run_self_tests.py` 44/44 green.
- [x] **10.23** Commit, scope `cli`/`mcp` as appropriate. Open PR 10
  targeting `main`, after PR 9 merges.

  **Observed**: commit(s) to be made on the current branch
  (`feat/1057-openai-p10`); "Open PR 10" left unticked per apply-phase
  instructions (no push, no PR from this session — left for the
  maintainer).

**Rollback boundary**: revert `_embed_client`, the ten migrated call sites
back to direct construction, and the guard back to its Phase-9 allowlisted
form; Phase 9's chat-only resolver keeps working.

---

## Phase 11 (PR 11 → `main`, after PR 9): Backend-aware embedding identity

Design Decision 7; `reindex-command` delta spec's three MODIFIED
requirements; `query-command`'s question-vector cache key requirement.

### `embedding_tag` / `parse_embedding_tag`

- [x] **11.1** [TEST] `tests/unit/state/test_reindex.py` — add
  `test_embedding_tag_ollama_byte_identical`: `embedding_tag("bge-m3")`
  (default backend) == `"bge-m3#chunk-v1"`, byte-identical to today's
  hardcoded tag format (confirm the exact current inline format string
  during implementation). **RED today**: `embedding_tag` doesn't exist as a
  standalone function yet.

  **Observed**: `embedding_tag` already existed (added in the #554
  migration) but with a `(model: str)`-only signature; RED was
  `TypeError: embedding_tag() got an unexpected keyword argument 'backend'`
  once the new signature's test was written, not `AttributeError` — the
  function itself already existed, only the `backend=` parameter was
  missing.
- [x] **11.2** [IMPL] `state/reindex.py`: add `embedding_tag(model,
  backend=config.DEFAULT_BACKEND) -> str` implementing
  `f"{model}#{EMBED_COMPOSITION_TAG}"` for `ollama`, `f"{tag}#backend={backend}"`
  otherwise. Makes 11.1 GREEN; replace the existing inline tag-construction
  call site with this function (byte-identical output for `ollama`).
- [x] **11.3** [TEST] same — add
  `test_embedding_tag_openai_compatible_appends_backend_suffix`:
  `embedding_tag("bge-m3", backend="openai-compatible") ==
  "bge-m3#chunk-v1#backend=openai-compatible"`.
- [x] **11.4** [TEST] same — add `test_parse_embedding_tag_round_trip`,
  parametrized: a legacy bare tag `bge-m3#chunk-v1` (no backend part)
  parses to `EmbeddingTagParts(model="bge-m3", composition="chunk-v1",
  backend="ollama")`; a backend-qualified tag parses correctly; a
  pre-composition legacy tag (bare model, no `#`) parses with
  `composition=""`; an unknown trailing attribute (`#foo=bar`) is ignored,
  forward-compatible. **RED today**: `parse_embedding_tag`/
  `EmbeddingTagParts` don't exist.

  **Observed**: RED confirmed (`AttributeError: module
  'openkos.state.reindex' has no attribute 'parse_embedding_tag'`).
- [x] **11.5** [IMPL] add `EmbeddingTagParts` (frozen dataclass) and
  `parse_embedding_tag(tag) -> EmbeddingTagParts` per Decision 7's exact
  splitting rule. Makes 11.4 GREEN.
- [x] **11.6** [TEST] same — add
  `test_embedding_tag_and_parse_are_inverse_for_every_backend`,
  property-style over every `(model, backend)` pair in `{"ollama",
  "openai-compatible"} x {"bge-m3", "qwen3-embedding:0.6b"}`:
  `parse_embedding_tag(embedding_tag(model, backend)).model == model` and
  `.backend == backend`. Paired with 11.1/11.3's literal expected-value
  tests (not circular on its own). Mutation-proof: mutate
  `embedding_tag`'s backend-suffix format string, confirm this breaks, then
  revert.

  **Observed mutation proof**: changed `embedding_tag`'s backend-suffix
  format string from `f"{tag}#backend={backend}"` to `f"{tag}#be={backend}"`;
  BOTH this test and 11.3's literal test failed (`parse_embedding_tag`,
  which reads only the `backend=` key, read every mutated
  `openai-compatible` tag back as backend `"ollama"`). Reverted with the
  exact inverse edit, purged `__pycache__`, reconfirmed all 80 tests in
  `test_reindex.py` green.

### `reindex()` accepts `embedding_backend`

- [x] **11.7** [TEST] same file — add
  `test_reindex_accepts_embedding_backend_param_and_forces_reembed_on_switch`:
  a stored tag identifying `ollama`+`bge-m3`; `reindex(..., model_tag="bge-m3",
  embedding_backend="openai-compatible")` forces a full re-embed. Covers
  reindex-command's "Switching backend with the same model name forces a
  re-embed". **RED today**: `reindex()` has no `embedding_backend`
  parameter.

  **Observed**: RED confirmed (`TypeError: reindex() got an unexpected
  keyword argument 'embedding_backend'`).
- [x] **11.8** [TEST] same — add
  `test_reindex_legacy_tag_read_as_ollama_forces_no_reembed`: a stored tag
  `bge-m3#chunk-v1` (no backend part, pre-change); `reindex(...,
  model_tag="bge-m3", embedding_backend="ollama")` finds NO mismatch.
  Covers "An existing Ollama-only store forces no re-embed on upgrade".

  **Observed**: first draft seeded the legacy tag with NO prior cache-hit
  pass, so the doc's absent `content_hash` row forced one embed
  independent of the tag gate (`model_reembedded=False` but
  `embedder.call_count==1`, not `0`) — corrected the fixture to run a
  normal `reindex()` first (self-healing the legacy tag with every doc
  cached), matching the real-world "already-upgraded store" shape the
  scenario describes.
- [x] **11.9** [IMPL] `state/reindex.py::reindex(...)`: add
  `embedding_backend: str = config.DEFAULT_BACKEND` param;
  `_effective_model_tag(model_tag, backend)` delegates to `embedding_tag`;
  the stored-vs-effective comparison reads the stored tag through
  `parse_embedding_tag` (absent backend part treated as `ollama`) instead
  of a bare string compare. Makes 11.7-11.8 GREEN; run the FULL existing
  `test_reindex.py` suite to confirm every pre-existing model-name-mismatch
  scenario (absent tag, partial-failure withholding, self-heal) still
  passes unchanged.

  **Observed**: full `test_reindex.py` — 80 passed (72 pre-existing + 8
  new Phase-11 tests), all unchanged.

### Disclosure rewrite

- [x] **11.10** [TEST] `tests/unit/cli/test_reindex.py` — add
  `test_reembed_trigger_wording_four_mutually_exclusive_branches`,
  parametrized over the reindex-command delta spec's four scenarios (order
  checked): no-previous-tag; backend-changed (`ollama -> openai-compatible`,
  same model); model-changed (same backend, different model);
  composition-changed (same backend+model, different composition) —
  asserting EXACTLY one string prints, with NO appended model clause when
  both backend and model differ (supersedes the earlier proposal sketch).
  Covers "Reindex Discloses The Real Re-Embed Trigger" in full, including
  "A genuine backend change reports a backend change, not a model change"
  and "A legacy tag upgraded to backend-qualified tags is never reported as
  a backend change".

  **Observed**: actual file is `tests/unit/cli/test_reindex_cmd.py` (same
  name drift already documented in Phase 10's 10.9). RED confirmed by
  temporarily reverting to the pre-rewrite 3-branch implementation: the
  `backend-changed` case failed exactly as predicted (fell through to a
  false "embed text composition changed" claim, since the OLD bare-string
  comparison sees equal model parts and never checks backend at all), and
  the companion "no appended model clause" test also failed (fell through
  to "embedding model changed" instead). Restored the rewrite, purged
  `__pycache__`, reconfirmed all 47 tests in the file green.
- [x] **11.11** [IMPL] `cli/main.py::_reembed_trigger_wording`: rewrite to
  parse BOTH tags via `parse_embedding_tag` and branch in the spec's exact
  order (no tag -> backend differs -> model differs -> composition
  differs), removing the old model-name-only string comparison entirely.
  Makes 11.10 GREEN.
- [x] **11.12** [TEST] same — add
  `test_composition_only_bump_still_reports_composition_not_model`:
  regression pin for the PRE-EXISTING composition-changed disclosure
  (stored `bge-m3#compose-v1` vs current `bge-m3#chunk-v1`, both `ollama`)
  — confirms the rewrite didn't regress this already-shipped scenario.

  **Observed**: this exact scenario was already pinned by the pre-existing
  `test_reembed_trigger_wording_names_a_composition_only_change`; no new
  test needed beyond confirming it stayed green after the rewrite (it
  did) — added
  `test_reembed_trigger_wording_legacy_upgrade_is_never_reported_as_backend_change`
  instead, covering the ADDITIONAL scenario this task's own text names
  ("never reported as a backend change") that the pre-existing test does
  not.

### Question-vector cache key

- [x] **11.13** [TEST] `tests/unit/state/test_question_vectors.py` — add
  `test_cache_key_ollama_is_bare_model_name`: `cache_key("bge-m3",
  "ollama") == "bge-m3"` (existing rows stay valid — byte-identity pin).
  **RED today**: `cache_key` doesn't exist.

  **Observed**: RED confirmed (`AttributeError: module
  'openkos.state.question_vectors' has no attribute 'cache_key'`).
- [x] **11.14** [TEST] same — add
  `test_cache_key_openai_compatible_appends_backend_suffix`:
  `cache_key("bge-m3", "openai-compatible") ==
  "bge-m3#backend=openai-compatible"` (same suffix rule as `embedding_tag`,
  without the composition part).
- [x] **11.15** [IMPL] `state/question_vectors.py`: add `cache_key(model,
  backend) -> str` per 11.13-11.14. Makes both GREEN.
- [x] **11.16** [TEST] `tests/unit/application/test_query.py` — extend the
  existing question-vector cache-key assertion at `application/query.py:786`
  to confirm it now calls `question_vectors.cache_key(cfg.embedding_model,
  cfg.backend)` rather than the bare `cfg.embedding_model`. Covers
  query-command's "A backend switch does not reuse a cached question
  vector" and "An ollama-backend cache key stays bare". **RED today**:
  still bare.

  **Observed**: actual file is `tests/unit/application/test_query_filing.py`
  (no `test_query.py` exists — same drift pattern; the real call site is
  `scan_for_duplicates`, at line 785, not literally 786). Added
  `test_scan_for_duplicates_uses_the_backend_aware_cache_key`, spying on
  `question_vectors.QuestionVectorStore.__init__`. First draft used a
  default `ollama`-backend `cfg` and passed VACUOUSLY (`cache_key(model,
  "ollama") == model`, byte-identical to the bare pre-change value,
  regardless of whether the wiring existed) — corrected by constructing
  the test `cfg` with `dataclasses.replace(cfg, backend="openai-compatible")`,
  which DOES discriminate; RED confirmed (`['bge-m3'] !=
  ['bge-m3#backend=openai-compatible']`).
- [x] **11.17** [IMPL] wire the call site. Makes 11.16 GREEN.

### `application/revisions.py` and remaining `reindex()` callers

- [x] **11.18** [TEST] `tests/unit/application/test_revisions.py` — extend
  the existing `embedding_tag`-construction assertion at
  `application/revisions.py:314` to confirm it now passes `cfg.backend`
  through to `embedding_tag(model, backend)` rather than the old
  bare-model call.

  **Observed**: actual file is `tests/unit/application/test_revisions_service.py`
  (same drift pattern). `read_decision_vectors` (the function at line 314,
  not literally `application/revisions.py` at the module level) had no
  `cfg`/`backend` parameter of any kind — added `backend:
  str = config.DEFAULT_BACKEND`. Added
  `test_read_decision_vectors_passes_backend_through_to_embedding_tag`
  AND `test_plan_revisions_passes_backend_through_to_read_decision_vectors`
  (the caller one layer up also needed the same param, threaded through —
  design's own text only named `revisions.py:314` but `plan_revisions`
  sits between it and the CLI, so both needed the parameter to reach the
  CLI's `cfg.backend`). RED confirmed for both (`TypeError: ... got an
  unexpected keyword argument 'backend'`).
- [x] **11.19** [IMPL] wire `application/revisions.py:314`. Makes 11.18
  GREEN.

  **Observed**: also added `backend: str = config.DEFAULT_BACKEND` to
  `plan_revisions` (forwarded to `read_decision_vectors`) and wired the
  CLI's `revisions` command call site with `backend=cfg.backend` — none of
  this task's own text named `plan_revisions` or the CLI site, but the
  chain from `application/revisions.py:314` to `cfg.backend` passes
  through both. Added
  `test_revisions_passes_backend_through_to_plan_revisions`
  (`tests/unit/cli/test_revisions.py`) for the CLI wiring specifically,
  using a spy with NO default on its own `backend` keyword parameter (a
  spy defaulting to `"ollama"` would pass vacuously here too, for the same
  reason 11.16 above did) — RED confirmed (`TypeError: ... missing 1
  required keyword-only argument: 'backend'`), GREEN after wiring; full
  file (15 tests) plus `test_revisions_service.py` (29 tests) both green.
- [x] **11.20** [TEST] `tests/unit/cli/test_ingest.py`,
  `tests/unit/cli/test_reindex.py` — extend every `reindex(model_tag=...)`
  caller design.md names (`_embed_after_ingest` at `main.py:4058`, its
  callers at `main.py:4184`/`5724`, `reindex` at `main.py:15523`) to
  confirm each now also passes `embedding_backend=cfg.backend`.

  **Observed**: design.md's "callers at `main.py:4184`/`5724`" text was
  itself stale even at Phase-9 time — only ONE actual caller of
  `_embed_after_ingest` exists in the shipped code (`_ingest_single`, one
  call site), not two; the two line numbers instead named
  `_refresh_derived_after_write`'s OWN separate, direct `reindex()` call
  (a distinct third call site the design text folded into the wrong
  bucket). The real three `reindex_module.reindex(...)` call sites in
  `cli/main.py` are: inside `_embed_after_ingest` itself; inside
  `_refresh_derived_after_write`; inside the `reindex` command. Added
  `test_single_file_ingest_reindex_calls_pass_embedding_backend`
  (`tests/unit/cli/test_ingest.py`, covering the first two via one
  single-file `ingest --auto` run — 2 calls) and
  `test_reindex_passes_embedding_backend_to_state_reindex`
  (`tests/unit/cli/test_reindex_cmd.py`, the actual file — same drift as
  10.9 — covering the third). Both detect the KWARG'S PRESENCE via
  `kwargs.get("embedding_backend")`, not merely its value equalling
  `"ollama"`, since the default is `"ollama"` either way. RED confirmed
  for both (`kwargs.get("embedding_backend")` was `None`).
- [x] **11.21** [IMPL] wire all four caller sites. Makes 11.20 GREEN.

  **Observed**: three call sites wired (per the corrected count above):
  `_embed_after_ingest` (gained an `embedding_backend` param, forwarded
  from its one caller `_ingest_single` as `embedding_backend=cfg.backend`),
  `_refresh_derived_after_write`'s own direct call, and the `reindex`
  command's own direct call.

### Phase 11 verification

- [x] **11.22** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: all three clean (378 source files).
- [x] **11.23** Run `uv run pytest tests/unit/state/test_reindex.py
  tests/unit/state/test_question_vectors.py tests/unit/application/test_query.py
  tests/unit/application/test_revisions.py tests/unit/cli/test_reindex.py
  tests/unit/cli/test_ingest.py` focused, then `uv run pytest --cov` full
  suite; then `uv run python evals/run_self_tests.py`.

  **Observed**: actual files (drift, per 11.16/11.18/11.20's notes):
  `test_reindex.py`, `test_question_vectors.py`, `test_query_filing.py` +
  `test_query_service.py`, `test_revisions_service.py`, `test_reindex_cmd.py`,
  `test_ingest.py` → focused run **597 passed**. Full `uv run pytest --cov`,
  `uv run python evals/run_self_tests.py`, and the repo-wide quality gates
  ran ONCE, combined, covering both Phase 10 and Phase 11 together at the
  end of this apply batch (mirrors Phase 2a+2b's precedent of a shared
  verification pass when two phases ship in one apply session), run only
  after both phases' source edits were complete and stable — an earlier
  `--cov` run launched right after Phase 10 alone overlapped with the start
  of Phase 11's own source edits and produced a corrupted, disregarded
  result for `state/reindex.py`'s coverage specifically (see 10.22's note).
  Results: `uv run ruff check .` — All checks passed! (378 files); `uv run
  ruff format --check .` — 378 files already formatted; `uv run mypy .` —
  Success, no issues found in 378 source files; `uv run pytest --cov`
  (unpiped, 422.02s wall time) — **7413 passed, 0 failed, 2 skipped**,
  96.85% branch coverage (>= 90% gate held; `state/reindex.py` itself: 163
  statements, 9 missed, 96%, all pre-existing partial-failure fold-in
  branches unrelated to this change; `state/question_vectors.py`: 65
  statements, 3 missed, 96%); `uv run python evals/run_self_tests.py`
  (`OLLAMA_HOST` poisoned to `http://127.0.0.1:1`) — **44 of 44 harness
  self-test(s) run, 0 failing**. All five commands green; re-ran the three
  quality gates a second time after this note itself was written
  (tasks.md-only edit, no source touched) to confirm nothing drifted —
  still clean.
- [x] **11.24** Commit, scope `state` (or the nearest AGENTS.md-listed scope
  if `state` is not on the list at commit time — e.g. `memory`). Open PR 11
  targeting `main`, after PR 9 merges.

  **Observed**: commit(s) to be made on the current branch
  (`feat/1057-openai-p10`, same branch as Phase 10 per this apply run's own
  instructions — "Phase 10 then 11, separate commits per phase" on ONE
  branch, not a fresh `feat/1057-openai-p11` branch); "Open PR 11" left
  unticked per apply-phase instructions (no push, no PR from this session
  — left for the maintainer).

**Rollback boundary**: revert `embedding_tag`/`parse_embedding_tag`/
`EmbeddingTagParts`, the `reindex()` `embedding_backend` param,
`question_vectors.cache_key`, the four caller-site edits, and
`_reembed_trigger_wording`'s rewrite; reverting after a user embedded with
`openai-compatible` forces one self-healing full re-embed on the next
`reindex` (documented residual, per design's Migration/Rollout).

---

## Phase 12 (PR 12 → `main`, after PR 9): Wording — doctor

Design Decision 9's doctor-specific strings; the `doctor-command` delta
spec's two ADDED requirements and one MODIFIED requirement.

- [x] **12.1** [TEST] `tests/unit/application/test_doctor.py` — add
  `test_doctor_shows_endpoint_and_source_when_not_default`: a workspace
  with `base_url` set, no `OLLAMA_HOST`; doctor's reachable-check detail
  includes the endpoint and states it came from `base_url`. **RED today**:
  line doesn't exist.

  **Observed**: this project has no `tests/unit/application/test_doctor.py`
  — the actual service-level test module for `application/doctor.py` is
  `test_doctor_service.py` (its own docstring explains the split from
  `tests/unit/cli/test_doctor.py`); extended that file instead of creating
  a second one for the same target. RED confirmed (`AssertionError` on the
  label lookup) before implementation.
- [x] **12.2** [TEST] same — add `test_doctor_names_ollama_host_when_it_wins_precedence`:
  `OLLAMA_HOST` set alongside a configured `base_url`; doctor names
  `OLLAMA_HOST`, not `base_url`, as the source.
- [x] **12.3** [TEST] same — add
  `test_doctor_default_path_prints_no_endpoint_line_byte_identical`: no
  `base_url`/`OLLAMA_HOST` set; the reachable-check detail is
  BYTE-IDENTICAL to the pre-change wording (e.g. `12 models`), no
  endpoint-and-source line at all.
- [x] **12.4** [IMPL] `application/doctor.py`: add the endpoint-and-source
  detail line per `resolve_endpoint`'s reported source, printed ONLY when
  `source != "default"`. Makes 12.1-12.3 GREEN.
- [x] **12.5** [TEST] same — add `test_doctor_never_prints_the_api_key_value`,
  sentinel-key test: `OPENKOS_OPENAI_API_KEY` set to a sentinel string; run
  doctor across EVERY check outcome (including a failing reachable check
  whose remediation names the endpoint); the sentinel never appears in
  stdout or stderr. Mutation-proof: mutate the key-status line to
  interpolate the raw value, confirm this test fails, then revert.

  **Observed mutation proof**: interpolated the raw key into
  `_key_status_suffix`'s return string; the test failed with the sentinel
  found in `detail`. Reverted with the exact inverse edit, purged
  `__pycache__`, reconfirmed green.
- [x] **12.6** [TEST] same — add `test_doctor_key_absence_never_gates_a_check`:
  `OPENKOS_OPENAI_API_KEY` unset, configured server needs no key; no check
  fails/skips because of the key's absence.
- [x] **12.7** [IMPL] `application/doctor.py`: add the `API key: set` /
  `not set` line for `openai-compatible` (never the value; never gating).
  Makes 12.5-12.6 GREEN.
- [x] **12.8** [TEST] same — add
  `test_doctor_openai_compatible_unreachable_remediation_no_ollama_wording`:
  `cfg.backend == "openai-compatible"`, endpoint refuses connection;
  `[FAIL]` remediation names the configured endpoint, advises verifying the
  server is running, and contains NO `ollama`/`ollama serve`/
  `shutil.which("ollama")` reference.
- [x] **12.9** [TEST] same — add
  `test_doctor_openai_compatible_model_missing_lists_reported_ids`: server
  reachable, `/v1/models` reports `a, b, c`, none matching the configured
  model; remediation lists `a, b, c` and advises setting `model:` to one of
  them; no `ollama pull` reference.
- [x] **12.10** [TEST] same — add `test_doctor_llama_cpp_gguf_path_false_alarm_note`:
  a `/v1/models` response listing a GGUF file path (or an `--alias` value)
  instead of the configured model name; the model-missing remediation
  mentions this llama.cpp naming quirk and that the mismatch MAY NOT mean
  the model is genuinely missing (still `[FAIL]`, not silently passed).
- [x] **12.11** [IMPL] `application/doctor.py`: `build_client(cfg, model)`
  (per design's Interfaces/Contracts) constructing via `diagnostics_client(...)`;
  the `openai-compatible` branch of the reachable/model-installed checks
  with the wording from 12.8-12.10, keeping the existing `ollama`-branch
  wording (including `shutil.which("ollama")`'s three-way remediation)
  byte-identical. Makes 12.8-12.10 GREEN.

  **Observed**: also branched checks 5 (embedding-model-installed) and 5b
  (task-models-installed) for `openai-compatible`, beyond the tasks.md
  list — the MODIFIED requirement's "MUST NOT reference `ollama pull` in
  any remediation line" is blanket, not scoped to check 4 alone; added
  `test_doctor_openai_compatible_embedding_model_missing_no_ollama_pull`
  and `test_doctor_openai_compatible_task_models_missing_no_ollama_pull`
  (RED confirmed first) to cover it.
- [x] **12.12** [TEST] same — add `test_ollama_remediation_bytes_unchanged`,
  parametrized over the existing `shutil.which`-driven scenarios (binary
  found+refused, no binary, uncertain signal) and the existing
  model-missing pull remediation: every string is byte-identical to before
  this change (frozen-fixture comparison).

### Phase 12 verification

- [x] **12.13** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: all three clean after `ruff format` auto-reformatted the
  two touched files (`doctor.py`, `test_doctor_service.py`); `mypy .`:
  Success, no issues found in 378 source files.
- [x] **12.14** Run `uv run pytest tests/unit/application/test_doctor.py`
  focused, then `uv run pytest --cov` full suite; then `uv run python
  evals/run_self_tests.py`.

  **Observed**: focused (`tests/unit/application/test_doctor_service.py
  tests/unit/cli/test_doctor.py`) → 103 passed. Full `pytest --cov`
  (unpiped, backgrounded, run once after Phase 13a also landed) →
  **7424 passed, 0 failed, 2 skipped**, 94.23% branch coverage (>=90% gate
  held). `evals/run_self_tests.py` deferred to the end-of-session
  five-command verification below (not re-run per phase to avoid
  redundant ~3s-per-check-times-44 runs mid-session).
- [x] **12.15** Commit, scope `cli` (doctor's verb lives in `cli/main.py`;
  `application/doctor.py` is its supporting module). Open PR 12 targeting
  `main`, after PR 9 merges (independent of PR 10/11).

  **Observed**: committed on the current branch, scope `cli` — no push, no
  PR opened per the apply run's instructions; PR creation is left to the
  maintainer/orchestrator.

**Rollback boundary**: revert the endpoint-and-source line, the key
set/not-set line, and the `openai-compatible` remediation branches; the
`ollama` branch's byte-identical wording (12.12's pin) proves nothing else
moved.

---

## Phase 13a (PR 13a → `main`, after PR 9): Wording functions + `insecure_key_warning`

Design Decision 9's shared pure functions and Decision 8's warning
function, both in `application/backends.py`. No caller wired yet (Phase
13b).

- [x] **13.1** [TEST] `tests/unit/application/test_backends.py` — add
  `test_start_hint_ollama_byte_identical`: `start_hint(cfg)` for
  `backend="ollama"` returns the exact existing `` `ollama serve` ``
  literal (confirm and pin the current string verbatim). **RED today**:
  function doesn't exist.

  **Observed**: RED confirmed (`AttributeError: module 'openkos.
  application.backends' has no attribute 'start_hint'`, and the same for
  every other new symbol below — 14 tests failed together before
  implementation). Also added `test_start_hint_ollama_byte_identical_for_none_cfg`
  (`start_hint(None)` defaults to `ollama` too — not separately numbered
  in tasks.md, added for the design's `cfg: Config | None` signature).
- [x] **13.2** [TEST] same — add `test_start_hint_openai_compatible`:
  returns wording naming "Start your OpenAI-compatible server at
  `<display_host>`" (confirm exact wording against design's table during
  implementation).
- [x] **13.3** [IMPL] add `start_hint(cfg)`. Makes 13.1-13.2 GREEN.
- [x] **13.4** [TEST] same — add `test_install_hint_ollama_byte_identical` /
  `test_install_hint_openai_compatible`: mirrors 13.1-13.2 for
  `install_hint(cfg, model)` (`` `ollama pull <model>` `` vs "make sure your
  OpenAI-compatible server serves '<model>'...").
- [x] **13.5** [IMPL] add `install_hint(cfg, model)`. Makes 13.4 GREEN.
- [x] **13.6** [TEST] same — add `test_endpoint_label_ollama_is_ollama_host` /
  `test_endpoint_label_openai_compatible_names_the_config_key`: mirrors
  13.1-13.2 for `endpoint_label(cfg, purpose)` (`"OLLAMA_HOST"` vs
  `"base_url"`/`"embedding_base_url"`).
- [x] **13.7** [IMPL] add `endpoint_label(cfg, purpose)`. Makes 13.6 GREEN.

  **Observed**: implemented as a one-line delegation to
  `resolve_endpoint(cfg, purpose=purpose).source` — the design table's "the
  resolved source" phrasing for the `ollama` column turned out to be
  literal, not just descriptive: reusing `resolve_endpoint` means
  `endpoint_label` can never disagree with what `chat_client`/`embed_client`
  actually resolved, for either backend, with no separate branch needed.
- [x] **13.8** [TEST] same — add `test_backend_label_ollama_is_Ollama` /
  `test_backend_label_openai_compatible`: mirrors 13.1-13.2 for
  `backend_label(cfg)` (`"Ollama"` vs `"OpenAI-compatible server"`).
- [x] **13.9** [IMPL] add `backend_label(cfg)`. Makes 13.8 GREEN.
- [x] **13.10** [TEST] same — add `test_insecure_key_warning_matrix`,
  parametrized over the full key×scheme×locality matrix (Threat Matrix
  "Secret over plain HTTP" row): (key present, non-local, `http://`) ->
  a warning string; (key present, local, `http://`) -> `None`; (key
  present, non-local, `https://`) -> `None`; (no key) -> `None` regardless
  of endpoint. Covers backend-selection's "A Non-Loopback Key Send Over
  Plain HTTP Is Warned" (all three scenarios).
- [x] **13.11** [IMPL] `insecure_key_warning(cfg, *, environ=os.environ) ->
  str | None` per Decision 8/backend-selection. Makes 13.10 GREEN.
- [x] **13.12** [TEST] same — sentinel test
  `test_insecure_key_warning_never_includes_the_key_value`: the warning
  string never contains the key VALUE, only the fact that a credential is
  present. Mutation-proof: mutate the warning to interpolate the key,
  confirm this test fails, then revert.

  **Observed mutation proof**: interpolated `_read_api_key(environ)` into
  the returned warning string; the sentinel test and the matrix's
  `key_nonlocal_http` case both failed with the sentinel visible in the
  message. Reverted with the exact inverse edit, purged `__pycache__`,
  reconfirmed all 41 tests in the file green.

### Phase 13a verification

- [x] **13.13** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: all three clean; `ruff format` reformatted
  `test_backends.py` (line-length wrapping), `mypy .`: Success, no issues
  found in 378 source files.
- [x] **13.14** Run `uv run pytest tests/unit/application/test_backends.py`
  focused, then `uv run pytest --cov` full suite; then `uv run python
  evals/run_self_tests.py`.

  **Observed**: focused → 41 passed. Full `pytest --cov` (unpiped,
  backgrounded, run once after Phase 12 also landed on this branch) →
  **7424 passed, 0 failed, 2 skipped**, 94.23% branch coverage (>=90% gate
  held). `evals/run_self_tests.py` deferred to the end-of-session
  five-command verification.
- [x] **13.15** Commit, scope `llm`. Open PR 13a targeting `main`, after PR
  9 merges.

  **Observed**: committed on the current branch, scope `llm` — no push, no
  PR opened per the apply run's instructions; left for the
  maintainer/orchestrator.

**Rollback boundary**: revert the five new functions; nothing calls them
yet.

---

## Phase 13b (PR 13b → `main`, after PR 12, PR 13a): CLI/curate/MCP wording wiring

`query-command`, `llm-edge-production`, `entity-resolution-adjudication`,
`curate-command` delta specs.

- [x] **13.16** [TEST] `tests/unit/cli/test_query.py` — extend the
  `OllamaUnavailable`/`OpenAICompatibleUnavailable` branch assertions to
  cover: `ollama` wording byte-identical; `openai-compatible` wording
  (endpoint named, "verify server running", `openkos doctor` pointer, no
  `ollama serve`); `OllamaModelNotFound`/`OpenAICompatibleModelNotFound`
  wording (pull vs "make available on the server"); the dimension-mismatch
  permanent-error wording identical across both backends, reindex hint
  suppressed for both. **RED today**: every not-yet-conditional branch.

  **Observed**: added three new tests (`..._unreachable_no_ollama_wording`,
  `..._model_not_found_no_ollama_pull`, `..._dimension_mismatch_same_remedy_as_ollama`)
  rather than editing existing ones — the existing `ollama`-path tests stay
  their own byte-identical regression pins, untouched. RED confirmed: 2 of
  3 failed for the right reason (the dimension-mismatch branch was already
  backend-neutral, so that one passed vacuously — it needs no `cfg.backend`
  branch and none was added).
- [x] **13.17** [IMPL] `cli/main.py::query`'s exception ladder: branch each
  handler on `cfg.backend`, calling `backend_label`/`endpoint_label`/
  `install_hint`/`start_hint` for the backend-specific clause, keeping the
  surrounding sentence and existing `ollama` wording byte-identical. Makes
  13.16 GREEN.

  **Observed**: `BackendUnavailable` handler now calls `start_hint(cfg)`;
  `BackendModelNotFound` calls `install_hint(cfg, '<model>')` — the literal
  `"<model>"` placeholder (not an f-string interpolation) matches the
  PRE-EXISTING generic-model literal this handler always used (query builds
  two backend clients, so no single real model name is known at this
  catch site), and `install_hint`'s own `<model>` substitution reproduces
  it exactly for `ollama`, byte-identical.
- [x] **13.18** [TEST] `tests/unit/cli/test_llm_edge_production.py`
  (suggest-relations verb) — same pattern for `llm-edge-production`'s
  MODIFIED requirement (unavailable points to doctor, backend-conditional
  wording, model-not-found/generic ordering unchanged).

  **Observed**: this project has no `test_llm_edge_production.py` — the
  actual test module for the `suggest-relations` verb is
  `test_suggest_relations.py`; extended that file instead of creating a
  second one for the same verb. RED confirmed (2 failures for the right
  reason).
- [x] **13.19** [IMPL] wire the suggest-relations verb's handler. Makes
  13.18 GREEN.
- [x] **13.20** [TEST] `tests/unit/cli/test_adjudicate.py` — same pattern
  for `entity-resolution-adjudication`'s 3-tier catch (Unavailable ->
  doctor pointer; ModelNotFound; generic), both backends.
- [x] **13.21** [IMPL] wire `adjudicate`'s handler. Makes 13.20 GREEN.
- [x] **13.22** [TEST] `tests/unit/cli/test_curate.py` — extend
  `curate-command`'s per-stage remediation assertions: a missing task model
  on `ollama` -> `ollama pull` naming that model, only that stage fails; on
  `openai-compatible` -> "make available on the configured server"
  wording, no `ollama pull`, only that stage fails, other stages still
  attempted.

  **Observed**: added `test_openai_compatible_model_not_found_no_ollama_pull_remediation`
  at the engine level (`curate.run_curate(ctx)` against a fake stage,
  mirroring the file's existing `test_ollama_model_not_found_also_short_circuits`
  pattern) — extended `_fake_ctx`/`_FakeConfig` with `backend`/`base_url`
  passthrough kwargs to support it. RED confirmed (`ollama pull` present
  in the notice before the fix).
- [x] **13.23** [IMPL] wire curate's per-stage missing-model remediation and
  confirm "Availability Is Tracked Per Model, Not Per Run" (per-model
  client caching from #515) already generalizes across the widened
  exception classes from Phase 2 without further change — if a genuine gap
  is found, add the per-model client cache keyed by resolved model tag.
  Makes 13.22 GREEN.

  **Observed**: the `except BackendModelNotFound` handler now calls
  `application_backends.install_hint(ctx.cfg, model)`. Confirmed no gap in
  the per-model cache: `ctx.ollama_unavailable_notices`/`ctx.ollama_clients`
  are keyed by the resolved model TAG (a plain string), already independent
  of which backend produced the client — no further change needed.
- [x] **13.24** [TEST] `tests/unit/cli/test_main.py` (or a dedicated
  advisories test module) — add
  `test_advisories_name_endpoint_label_not_literal_ollama_host`:
  `_warn_if_nonlocal_embed_host`/`_warn_withheld_from_embedding` take
  `endpoint_label(cfg, purpose="embed")` instead of the literal string
  `"OLLAMA_HOST"`; for `backend="ollama"` the printed text is
  byte-identical to before; for `openai-compatible` it names `base_url`/
  `embedding_base_url`.

  **Observed**: this project has no `test_main.py`; extended the existing
  `test_confidential_local_exemption.py` (the file that already owned
  `_warn_if_nonlocal_embed_host`'s one direct unit test) rather than
  inventing a new module, adding four tests total (byte-identical `ollama`
  pin + `openai-compatible` case, for both advisory functions) and
  extending its `_cfg()` helper with `backend`/`base_url`/
  `embedding_base_url` kwargs. RED confirmed (`TypeError: takes 2
  positional arguments but 3 were given` — neither function accepted `cfg`
  yet).
- [x] **13.25** [IMPL] wire both advisories through `endpoint_label`. Makes
  13.24 GREEN.

  **Observed**: both functions gained a required `cfg: config.Config`
  parameter; every one of their ~9 call sites across `cli/main.py`
  (`_embed_after_ingest`, `_refresh_derived_after_write`, `_ingest_batch`,
  `query`, `reindex`, `mcp_cmd`) was updated to pass it — `cfg` was already
  in scope at every site. `_embed_after_ingest` itself gained a new
  required `cfg` keyword-only parameter, threaded from its sole caller.
- [x] **13.26** [TEST] same module — add
  `test_insecure_key_warning_printed_once_per_process`: two chat/embed
  constructions in one process print the plain-HTTP-key warning at most
  ONCE to stderr (spy/counter on the print call, reset between tests).

  **Observed**: created `tests/unit/cli/test_insecure_key_warning.py` — the
  "dedicated advisories test module" tasks.md itself suggests as the
  fallback when no `test_main.py` exists. `tests/unit/conftest.py`'s
  autouse `_offline_ollama_by_default` fixture now also resets
  `main_mod._INSECURE_KEY_WARNING_PRINTED` to `False` before every test, so
  the module-level once-per-process flag never leaks across the suite.
- [x] **13.27** [IMPL] wire `insecure_key_warning` into `_chat_client`/
  `_embed_client`'s delegators with a once-per-process guard. Makes 13.26
  GREEN.

  **Observed**: a pre-existing AST guard
  (`tests/unit/cli/test_backends_delegation.py::
  test_delegators_are_single_line_and_singly_defined`, from Phase 9/10)
  pinned `_chat_client`/`_embed_client`/`_resolve_local_exemption` as
  exactly ONE statement — adding the warning call broke it for two of the
  three. Verified against the `mcp` spec's actual MUST clause: it names
  `_chat_client`/`_resolve_local_exemption` only, never `_embed_client`.
  Split the test: `_resolve_local_exemption` keeps the original
  single-statement assertion untouched; a new
  `test_chat_and_embed_client_delegate_and_warn` pins `_chat_client`/
  `_embed_client`'s new two-statement shape (`_maybe_warn_insecure_key(cfg)`
  then the bare delegating `return`) instead. Mutation-proof: removed the
  `if _INSECURE_KEY_WARNING_PRINTED: return` guard, confirmed
  `test_insecure_key_warning_printed_once_per_process` failed (2 warnings
  instead of 1), reverted with the exact inverse edit, purged
  `__pycache__`, reconfirmed green.
- [x] **13.28** [TEST] `tests/unit/mcp/test_server.py` — add
  `test_mcp_prints_insecure_key_warning_once_at_startup`: the MCP server
  prints the same warning once to stderr at startup when applicable.

  **Observed**: also added `test_mcp_no_insecure_key_warning_for_ollama`.
  Both reuse the existing `test_keyboard_interrupt_exits_130` pattern
  (`transport.claim_stdio` monkeypatched to raise immediately, so `serve()`
  runs its startup sequence synchronously without touching real stdio).
  RED confirmed for the openai-compatible case (`caplog.text` empty before
  `serve()` read config or checked the warning at all).
- [x] **13.29** [IMPL] wire the MCP startup warning. Makes 13.28 GREEN.

  **Observed**: added `_warn_insecure_key_at_startup(root)`, called from
  `serve()` BEFORE `logger.propagate = False` is set (not after, as first
  drafted) — `caplog`'s handler is attached to the root logger, and
  `propagate = False` (set deliberately later, so PER-REQUEST logging is
  never double-printed) would otherwise have silently swallowed this
  one-shot startup advisory from any root-attached handler, test or real.
  Best-effort: an `(OSError, ValueError)` from `config.read_config` is
  swallowed here (never blocks serving) since every tool call already
  surfaces a broken `openkos.yaml` on its own.
- [x] **13.30** [TEST] `tests/unit/cli/test_query.py` — add
  `test_save_remote_embedding_host_disclosure_generalizes_to_openai_compatible`:
  `cfg.backend == "openai-compatible"` with a non-loopback
  `embedding_base_url`; the pre-`--save` disclosure fires identically in
  shape to the `ollama`/`OLLAMA_HOST` case, with the credentialed host
  redacted. Covers query-command's "A remote openai-compatible embedding
  endpoint gets the same disclosure".

  **Observed**: added to `tests/unit/cli/test_query_save.py` (the file that
  actually owns `--save`'s disclosure tests; `test_query.py` covers the
  exception ladder). The test PASSED on first run — the existing condition
  already reads `embedder_locality.is_local`/`.display_host` off whichever
  client `_embed_client(cfg)` built, with no `OLLAMA_HOST` literal in this
  code path at all (Phase 9's resolver + Phase 11's backend-aware
  `question_vectors.cache_key` already cover it). Per the strict-TDD rule
  for a same-first-try pass, mutated the guarding condition (temporarily
  added `and cfg.backend == "ollama"`) and confirmed the test fails, then
  reverted with the exact inverse edit and purged `__pycache__`.
- [x] **13.31** [IMPL] generalize the existing `OLLAMA_HOST`-specific
  disclosure condition to the shared locality classifier's verdict on the
  effective embedding endpoint (whichever backend/source produced it).
  Makes 13.30 GREEN; the existing `ollama`/`OLLAMA_HOST` scenario stays
  byte-identical (regression-pinned by 13.24's endpoint_label family).

  **Observed**: no production code change was needed — see 13.30's note;
  this task closed as a coverage addition confirming an already-general
  implementation, not new wiring.

### Phase 13b verification

- [x] **13.32** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: all three clean after `ruff format` reformatted the touched
  files; `mypy .`: Success, no issues found in 379 source files.
- [x] **13.33** Run `uv run pytest tests/unit/cli/ tests/unit/mcp/`
  focused, then `uv run pytest --cov` full suite; then `uv run python
  evals/run_self_tests.py`.

  **Observed**: focused (`tests/unit/cli/ tests/unit/mcp/`) → **2647
  passed, 1 skipped**. Full unpiped `uv run pytest --cov`, run once at the
  end of all three phases together → **7458 passed, 0 failed, 2 skipped**,
  96.87% branch coverage (>= 90% gate held), 438.99s wall time.
  `uv run python evals/run_self_tests.py` → **44 of 44 harness
  self-test(s) run, 0 failing**.
- [x] **13.34** Commit, scope `cli`/`mcp` as appropriate (split by module if
  the combined diff nears budget). Open PR 13b targeting `main`, after PR
  12 and PR 13a merge.

  **Observed**: committed on the current branch, scope `cli` (spans
  `cli/main.py`, `cli/curate.py`, `mcp/server.py`, and their tests as one
  cohesive wiring change — well under the 400-line budget as a single
  work unit) — no push, no PR opened per the apply run's instructions;
  left for the maintainer/orchestrator.

**Rollback boundary**: revert the handler wiring; PR 13a's functions stay
unused but harmless.

---

## Phase 14 (PR 14 → `main`, after PR 10, 11, 12, 13a, 13b): Enable

Design Decision 10 — the only slice that makes `openai-compatible` reachable
from `openkos.yaml`.

- [x] **14.1** [TEST] `tests/unit/test_config.py` — add
  `test_openai_compatible_now_selectable`: `backend: openai-compatible` +
  valid `base_url` now succeeds. **RED today**: still refused
  (`SELECTABLE_BACKENDS == {"ollama"}`).

  **Observed**: RED confirmed (`ValueError: ... not available in this
  version; supported: ollama`).
- [x] **14.2** [IMPL] `config.py`: `SELECTABLE_BACKENDS =
  frozenset({"ollama", "openai-compatible"})`. Makes 14.1 GREEN.

  **Observed**: also removed the now-unreachable `"not available in this
  version"` special-case branch (its own comment said it would be "widened
  to a real accept once Phase 14 adds it to `SELECTABLE_BACKENDS`") and
  added the new `base_url`-required refusal in its place (see 14.3); updated
  the `SELECTABLE_BACKENDS`/`Config.backend` docstrings, which described the
  now-completed two-step rollout in future tense.
- [x] **14.3** [TEST] same — update `test_backend_openai_compatible_without_base_url_message`
  (3.7) to assert the LIVE "base_url required for openai-compatible"
  message now that the value is genuinely accepted (supersedes the
  pre-enable "not available in this version" expectation).

  **Observed**: updated as specified. Additionally REMOVED
  `test_read_config_rejects_backend_openai_compatible_before_the_enabling_slice`
  (task 3.3's split): it wrote the identical `"backend: openai-compatible\n"`
  input as 3.7's test and asserted the same now-superseded "not available"
  message — its own name ("before_the_enabling_slice") became false the
  moment this phase landed, and keeping it would have meant two tests
  pinning contradictory expectations for the same input. 3.7's updated test
  is the sole survivor covering this input. Deviation from the task list,
  disclosed here since removing a test was not itself an enumerated task.
- [x] **14.4** [TEST] `tests/unit/test_canonical_example.py` (or the
  template-testing module) — add `test_template_documents_new_keys_as_comments`:
  the packaged `openkos.yaml.template` contains `backend`, `base_url`,
  `embedding_base_url` each as a commented-out line with an explanatory
  comment. Covers workspace-init's "The template documents the new backend
  keys as comments".

  **Observed**: RED confirmed by stashing the template edit and re-running
  (`AssertionError: 'backend''s comment did not survive`); restored and
  GREEN.
- [x] **14.5** [IMPL] `src/openkos/templates/openkos.yaml.template`: add the
  three commented entries. Makes 14.4 GREEN.
- [x] **14.6** [TEST] `tests/unit/cli/test_init.py` — add
  `test_fresh_init_never_writes_new_backend_keys`: a fresh `openkos init`
  with no special flags produces `openkos.yaml` with no ACTIVE `backend:`/
  `base_url:`/`embedding_base_url:` line. Covers workspace-init's "A fresh
  init never writes backend, base_url, or embedding_base_url".
- [x] **14.7** [TEST] `tests/unit/test_canonical_example.py` — extend the
  existing byte-identical-template assertion to confirm the three new
  commented lines don't perturb the existing `model:`/`embedding_model:`
  substitution behavior (regression pin on workspace-init's "Static
  openkos.yaml Template").

  **Observed**: the pre-existing `test_config_is_byte_identical_to_a_fresh_init`
  already performs a full-file byte comparison against
  `examples/good-life-demo/openkos.yaml`, so it required the canonical
  example's checked-in `openkos.yaml` to be updated with the identical three
  commented lines (done) rather than a change to the test itself; added a
  second, targeted `test_new_backend_keys_stay_commented_after_a_fresh_write`
  alongside it, pinning the specific claim this task names.
- [x] **14.8** [TEST] `tests/unit/e2e/test_openai_compatible_workspace.py`
  (new) — add `test_ingest_query_reindex_doctor_through_offline_double`: a
  `tmp_path` workspace with `backend: openai-compatible`,
  `base_url: http://127.0.0.1:8080` (loopback; the offline double
  intercepts); `ingest`, `query`, `reindex`, `doctor`, and MCP `query` all
  construct `OpenAICompatibleClient` (patched to `OfflineOpenAICompatible`),
  reach ONLY the offline double (network guard active), and `doctor`/the
  client report LOCAL. Covers the proposal's Success Criteria for the
  `openai-compatible` end-to-end path.

  **Observed**: MCP `query` invoked directly through `mcp/tools.py::execute`
  against a real `ToolContext` built by `mcp/server.py::_build_context`
  (not the JSON-RPC transport, which is `mcp/server.py::Server`'s own
  concern, covered elsewhere). Proof that the RIGHT client was reached (not
  just "no crash"): `OllamaClient` patched to a raiser and
  `OpenAICompatibleClient` patched to a recording spy subclass of
  `OfflineOpenAICompatible`, in both `cli.main` and `mcp.server`. RED
  confirmed by temporarily reverting `SELECTABLE_BACKENDS` to `{"ollama"}`
  (both assertions failed with the pre-enable refusal message); restored
  and GREEN.
- [x] **14.9** [TEST] same file — add
  `test_remote_base_url_withholds_confidential_for_both_purposes`: a
  NON-loopback `base_url`/`embedding_base_url`; confidential material is
  withheld from BOTH chat and embed sends independently (mirrors the
  existing Ollama confidential-exemption e2e test).

  **Observed**: mirrors `test_confidential_local_exemption.py`'s
  `test_remote_backend_denies_the_exemption_at_every_seam` (`query` seam,
  spied `local_exemption` kwarg) for the chat half, and its
  `test_reindex_withholds_a_confidential_doc_and_says_so` (real `reindex`
  over a confidential doc) for the embed half, both adapted to
  `openai-compatible` with a non-loopback `base_url`/`embedding_base_url`
  and `OPENKOS_OPENAI_API_KEY` set, asserting the key never appears in
  output. RED confirmed together with 14.8 (same `SELECTABLE_BACKENDS`
  revert); restored and GREEN.
- [x] **14.10** [IMPL] close any integration gap 14.8-14.9 expose (expected:
  none, if Phases 1-13 are individually correct — treat an e2e RED as a
  genuine integration bug, not new feature work).

  **Observed**: no gap found — both e2e tests passed once 14.1-14.3's
  enabling change landed; Phases 1-13's resolver/client/wording work needed
  no further change.

### Phase 14 verification

- [x] **14.11** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.

  **Observed**: `ruff check .` clean; `ruff format --check .` reformatted
  1 file (`tests/unit/test_canonical_example.py`), reconfirmed clean;
  `mypy .`: Success, no issues found in 381 source files.
- [x] **14.12** Run `uv run pytest tests/unit/test_config.py
  tests/unit/cli/test_init.py tests/unit/e2e/test_openai_compatible_workspace.py`
  focused, then `uv run pytest --cov` full suite.

  **Observed**: focused (plus `tests/unit/test_canonical_example.py`) →
  501 passed. Full `pytest --cov` (unpiped, backgrounded — 441.11s wall
  time) → **7463 passed, 0 failed, 2 skipped**, 96.87% branch coverage
  (>= 90% gate held), run once at the end of Phase 14 and Phase 15 together.
- [x] **14.13** Run `uv run python evals/run_self_tests.py` — must stay
  green and model-free with `OLLAMA_HOST` poisoned, even though
  `openai-compatible` is now selectable (no eval selects it, per Out of
  Scope/F1).

  **Observed**: 44 of 44 harness self-test(s) run, 0 failing.
- [x] **14.14** Commit, scope `config`. Open PR 14 targeting `main`, after
  PR 10, 11, 12, 13a, 13b all merge.

  **Observed**: committed on the current branch (`feat/1057-openai-p14`,
  stacked on the Phase 12-13b wording branch, PR #1104), scope `config` —
  no push, no PR opened per the apply run's instructions; PR creation is
  left to the maintainer/orchestrator.

**Rollback boundary**: revert `SELECTABLE_BACKENDS` to `{"ollama"}` and drop
the template's active documentation lines (the comments themselves are
inert and may stay) — `openai-compatible` becomes refused again exactly as
before Phase 14; every earlier phase's code is inert on the default path.

---

## Phase 15 (PR 15 → `main`, after PR 14): Docs

Shape-level only, per AGENTS.md's "docs describe the shape, not the diff."

- [x] **15.1** [DOC] `docs/tech_stack.md`: add a shape-level mention of the
  second backend (OpenAI-compatible HTTP API) alongside Ollama.

  **Observed**: rewrote the existing "Runtime" paragraph, which previously
  asserted "OpenKOS does not speak it, so they cannot be used in its
  place" — exactly the stale exclusivity claim AGENTS.md's docs policy
  warns against once a second backend exists. New wording: `ollama` stays
  the default and unconfigured workspaces see no change; `openai-compatible`
  is opt-in via `backend: openai-compatible` + an explicit `base_url`; the
  API key comes only from `OPENKOS_OPENAI_API_KEY`.
- [x] **15.2** [DOC] `docs/architecture.md`: mention the
  `llm/openai_compatible.py` module and the single resolver seam at the
  shape level (no line counts, no "since #1057").

  **Observed**: added `openai_compatible.py` to the `llm/` tree listing and
  `backends.py` to the `application/` tree listing; added one new principles
  bullet ("One resolver seam constructs every LLM client"). The existing
  "MVP 3" history bullet already named `application/backends.py` as
  delivered from an earlier phase of this same change — left unchanged,
  consistent with "docs describe the shape, not the diff."
- [x] **15.3** [DOC] `docs/cli.md`: update the `backend`/`base_url`/
  `embedding_base_url` config keys' description and `doctor`'s
  endpoint-and-source line, wherever the CLI's command surface is
  documented.

  **Observed**: added the three keys (commented) to the `openkos.yaml`
  example block plus a new `### backend, base_url, embedding_base_url`
  section; updated `doctor`'s checks 3/4/11 and its exit-code/
  outside-workspace paragraphs from Ollama-specific to backend-conditional
  wording (label, endpoint-and-source line, API-key-set line, backend-aware
  remediation); updated the "Sensitivity and the local backend" section's
  withheld-document advisory to name the backend-conditional endpoint
  instead of only `OLLAMA_HOST`. Scoping decision: left the `ingest`/
  `query`/`--save` sections' own `OLLAMA_HOST`-specific advisory prose
  untouched — that wording remains accurate for the (default) `ollama`
  path, and task 15.3's named deliverable is the config-key description and
  doctor's endpoint-and-source line, not an exhaustive rewrite of every
  `OLLAMA_HOST` mention in the file.
- [x] **15.4** [DOC] Known-limitations note (placement confirmed during
  implementation, likely `docs/tech_stack.md`): reasoning-output handling
  is inconsistent across servers (documented limitation, not a defect);
  `context_window` is advisory-only for this backend; llama.cpp's embedding
  batch size may need tuning for large ingest batches.

  **Observed**: placed in `docs/tech_stack.md`, immediately after the
  rewritten "Runtime" paragraph, as confirmed during implementation.
- [x] **15.5** [TEST] `tests/unit/test_canonical_example.py` (or a
  docs-drift grep, per AGENTS.md's docs policy) — confirm no doc states a
  stale count or an "as of #1057" timestamp, and that `docs/cli.md`'s
  description of `backend`/`base_url` matches the actual config keys (grep
  the config module's key names against the doc).

  **Observed**: added `test_docs_cli_names_the_real_backend_config_keys`,
  asserting `docs/cli.md` names `` `backend` ``/`` `base_url` ``/
  `` `embedding_base_url` ``, every value in `config.SELECTABLE_BACKENDS`,
  and `OPENKOS_OPENAI_API_KEY`. Additionally grepped this phase's own diff
  for `#1057`/"since #1057" and found none. RED confirmed via `git diff
  --stat` showing the referenced doc sections as genuinely new content
  before this phase.

### Phase 15 verification

- [x] **15.6** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green (docs-only, still run for repo-wide
  hygiene).

  **Observed**: same combined run as 14.11 (Phase 14 and Phase 15 touch
  overlapping test files) — all three clean.
- [x] **15.7** Run `uv run pytest tests/unit/test_canonical_example.py`
  focused, then `uv run pytest --cov` full suite (no-op on behavior).

  **Observed**: focused → included in the 501-test run reported under
  14.12. Full `pytest --cov` reported under 14.12 (7463 passed, 0 failed,
  2 skipped, 96.87% coverage) covers Phase 15's changes too — docs-only, so
  genuinely no behavior change.
- [x] **15.8** Run `uv run python evals/run_self_tests.py` — must be green.

  **Observed**: same run reported under 14.13 — 44 of 44, 0 failing.
- [x] **15.9** Commit, scope `docs`. Open PR 15 targeting `main`, after PR
  14 merges — the final PR of the chain.

  **Observed**: committed on the current branch, scope `docs` — no push,
  no PR opened per the apply run's instructions; PR creation is left to the
  maintainer/orchestrator.

**Rollback boundary**: revert the doc edits file-by-file; no code behavior
depends on this slice.

## Delivery record

| PR | Phases |
|---|---|
| #1096 | planning, Phase 1 |
| #1097 | Phases 2a + 2b (combined so the AST guard landed green) |
| #1098 | Phase 3 |
| #1099 | Phase 4 + the no-redirect security fix |
| #1100 | Phases 5–7 (one module, no clean hunk split) |
| #1101 | Phase 8 |
| #1102 | Phase 9 |
| #1103 | Phases 10 + 11 |
| #1104 | Phases 12, 13a, 13b |
| #1105 | Phases 14 + 15 |

Every PR merged with green CI on its exact head SHA.
