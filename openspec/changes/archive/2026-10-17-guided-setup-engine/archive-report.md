# Archive Report: guided-setup-engine

**Archived**: 2026-10-17 (S4, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-17-guided-setup-engine/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1486, closed by this slice. Follow-up decision: #1487 (how the app creates the first workspace), left open.

## Final State At Close

Three slices before this one merged to `main`, each one squash-merged PR.

| Slice | PR | Squash commit | Authored lines (additions + deletions; generated `api_surface.json`, `openapi.json`, `schema.d.ts` excluded) |
|-------|----|---------------|------------------------------------------------------------------------------------------------------------------|
| S1 plan | #1488 | `9e3eae61` | about 700 (docs) |
| S2 readiness: `OllamaClient.version`, `setup_readiness`, `GET /v1/setup/readiness`, `setupReadiness()` | #1495 | `1074703a` | about 890, 546 of them tests |
| S3 pull: `OllamaClient.pull`, `pull_model`, the pull registry and routes, three client methods | #1502 | `eaa60050` | about 1,740, 1,063 of them tests |
| S4 spec merge, CHANGELOG, roadmap status, archive | this PR | | docs only |

- **Shipped surface** (all additive, `API_VERSION` unchanged at 1): `setup_readiness`, `SetupReadiness`, `RuntimeReadiness`, `ModelReadiness`, `MemoryReadiness`, `pull_model`, `PullObserver`, `PullOutcome`, `PullPhase`, `PullProgress`, `PullRefused`. Over HTTP: `GET /v1/setup/readiness`, `POST /v1/setup/pulls` (202 and `Location`), `GET /v1/setup/pulls/{pull_id}`, `POST /v1/setup/pulls/{pull_id}/stop`, and the rows `pull_active` (409, retryable), `model_not_configured` and `pull_not_supported` (422). Client: `setupReadiness`, `startModelPull`, `modelPull`, `stopModelPull` and the three codes.
- **Engine support**: `application/setup.py` (new); `doctor.configured_task_models`, which check 5b now calls; `llm/base.py` `ModelRuntime` and `PullEvent`; `OllamaClient.version` and `OllamaClient.pull`; `localapi/_pulls.py` (new).
- **ADRs**: none created (design D11). ADR-0057 stays Proposed until the setup screen ships.
- **Not changed, on purpose**: `desktop/ui/` (owned by another change); the CLI and `docs/cli.md`; no workspace creation through the API (#1487).

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| stable-python-api | Updated | 3 ADDED requirements appended: Setup Readiness Is A Model-Free Read; A Configured Model Is Pulled With Progress And A Cooperative Stop; Setup Extends v1 Additively. The Purpose's v1 list names guided setup and no longer excludes it. |
| llm-client | Updated | 2 ADDED appended: Ollama Reports Its Version; Ollama Pulls A Model As A Progress Stream. The Purpose names the two endpoints and the Non-Goals no longer exclude the pull's stream. |
| local-http-api | Updated | The 2 MODIFIED requirements (The Operations Are Served Under `/v1`, One Per Python API Operation; Outcomes And Errors Map To Status Codes With A Uniform Body) were re-applied as additions to the current living text, not replaced: other changes edited the same requirements after the delta was written, so a wholesale replacement would have dropped their rows. Added: the four setup rows and the readiness sentence and scenario; the three pull error rows and the unconfigured-model scenario. 1 ADDED appended: A Model Pull Runs As A Single Pollable Job. The Purpose names the pull registry; the "No operation beyond the API" scenario no longer lists setup. |
| desktop-client | Updated | 1 ADDED appended: The Client Exposes Setup Readiness And The Model Pull. |

The deltas' "(Previously: ...)" notes were not carried into the living specs.

### Heading Name-Match

After the merge every `### Requirement:` heading in the four living specs is unique, and every `#### Scenario:` heading is unique within its requirement (checked by script). Each ADDED heading appears exactly once; no REMOVED or RENAMED headings.

## Requirement-vs-Code Check

| Requirement | Result |
|-------------|--------|
| Setup Readiness Is A Model-Free Read | Met. Version probe only on `ollama` with a `ModelRuntime`; the listing only after the probe answers (or always on `openai-compatible`); unreachable is a value; `installed` is `None` when the list could not be read; `ready` needs the runtime and every required model; memory from `sysconf`, `None` where unknown; the API maps `SetupConfigUnreadable` to `WorkspaceUnreadable`. |
| A Configured Model Is Pulled | Met. Exact-tag check before any client is built; `backend_cannot_pull` for `openai-compatible` and for a client without the capability; `already_installed` with no pull; stop checked before the stream and after every line; per-layer max; phases normalised. |
| Setup Extends v1 Additively | Met. The classifier printed only `additive` lines on S2 and S3. |
| Ollama Reports Its Version / Pulls A Model | Met. Redacted host in `OllamaUnavailable`; error lines and HTTP errors that say the model does not exist are `OllamaModelNotFound`; the reply is closed on every exit. |
| Operations, Outcomes (modified) | Met. Both route tables and the published schema list the four routes; the TS `ERROR_CODES` parity test holds. |
| A Model Pull Runs As A Single Pollable Job | Met. Admission at the first stop check; refusals and pre-admission exceptions answer the start; 16 finished kept; shutdown in the app lifespan. |
| The Client Exposes Setup | Met. Four methods, the three codes, a typed `pull_active` refusal test. |

## Decisions Taken During Implementation

1. **The readiness double needed `pull`.** Once `ModelRuntime` gained `pull` in S3, a runtime without it is no longer a `ModelRuntime` and readiness reports no version for it; the readiness tests' doubles gained the method.
2. **The pull's status key is named, not spelled inline**, because the OKF seam guard confines `.get("status")` to `model/okf.py`; this is a wire field of Ollama's, not frontmatter.
3. **The raw-host guard's endpoint vocabulary gained `/api/version` and `/api/pull`**, and the offline Ollama test stub overrides both new network methods.

## Verification Record

- Strict TDD in both code slices: RED observed first (import errors on `ModelRuntime`, `setup`, `PullEvent`, `_pulls`; failing route and client tests), then green.
- Mutations on the exact guarded lines, each restored from a byte copy with `__pycache__` purged, every one failing a test: S2 4 (the `required` filter, the chat-model dedup, the reachability branch, the floor comparison), S3 4 (the not-configured refusal, the client's stop check, the per-layer max, the pre-stream stop).
- Local full suite `OPENKOS_REQUIRE_API_BASE_REF=1 uv run pytest --cov`: S2 96.78% and S3 96.80%; each run surfaced repository guard failures (the offline stub, the raw-host vocabulary, the status-key seam), which were fixed and re-run on their own before pushing, and CI then ran the whole suite; `ruff check`, `ruff format --check`, `mypy .` clean; `evals/run_self_tests.py` 58 of 58; Node `typecheck`, vitest, `check:schema` on both slices. CI green on every merged slice.
- **Not verified**: a pull against a real Ollama (no multi-GB download was run; every transport is faked), Ollama's real HTTP status for an unknown model on the installed version, an absent-Ollama run, and `sysconf` on Windows (it returns `None` there by design).

## Follow-ups

- #1487: how the app creates a first-time user's workspace (recommended default: the shell runs the engine's `init`).
- Readiness probes the chat endpoint only, as `doctor` does; a workspace whose embedding model is served from a different endpoint is reported against the chat host.
