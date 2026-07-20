# Proposal: `proactive-ollama-onboarding` — surface missing Ollama BEFORE the first ingest

## Intent

Today Ollama guidance is REACTIVE: a user learns Ollama is missing only when a
verb fails, and the "unreachable" remedy can't tell "not installed" from
"installed but off". Make onboarding PROACTIVE — a user who runs `install` then
`init` already knows Ollama is missing and which command to run, without reading
the README or failing an ingest first. Small diff, high first-run UX impact.

## Scope

### In Scope (three change sites, all in `src/openkos/cli/main.py`)
- **init post-success preflight** (new code after `main.py:141`): ONE
  short-timeout, NON-FATAL Ollama probe (reachable + model) reusing config-free
  `llm/ollama.py` primitives (`list_models()`/`model_tag_matches()`). On any gap,
  print ONE UNIFIED WARNING deferring to `doctor` (spirit: "Workspace ready.
  Ollama isn't available yet; run `openkos doctor` before your first ingest.").
  init stays exit 0, never pulls a model, never starts the server.
- **doctor pointer on exactly THREE verbs** — append "…or run `openkos doctor`
  to diagnose the environment." to the existing `OllamaUnavailable` branch of
  `query` (2202-2208), `adjudicate` (1965-1971), `suggest-relations` (2072-2078).
  Ordered 3-tier logic and exit codes untouched.
- **doctor not-installed-vs-off** — the "Ollama reachable" `[FAIL]` remediation
  (`main.py:2363-2372`) uses `shutil.which("ollama")`: `None` → "no `ollama`
  binary found on PATH — install from https://ollama.com" (NEVER "not
  installed"); found+refused → `ollama serve`; uncertain → cover BOTH.
- **Named `_PREFLIGHT_TIMEOUT = 5.0`** replacing the bare literal at `main.py:2351`,
  reused by init's preflight and doctor's probe.

### Out of Scope (non-goals)
- No Ollama server lifecycle management (no spawn/kill of `ollama serve`).
- No auto-pull of models without explicit consent.
- No change to init's pure-file-writer guarantee — preflight is a POST-success
  warning, never a precondition.
- `ingest` untouched (generic `except OllamaError`, Source-only degrade, exit 0).
- No change to the ordered `OllamaUnavailable→OllamaModelNotFound→OllamaError`
  logic or any exit code.
- No extraction/refactor of doctor's inlined checks into a shared helper.
- `llm/` stays config-free; all remediation text stays in `cli/` (D1).
- `shutil.which` lives in `doctor` ONLY.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `workspace-init`: add a non-fatal post-success Ollama preflight warning; exit-0
  contract preserved.
- `doctor-command`: reachable-check remediation distinguishes not-installed vs off
  via `shutil.which`, without over-claiming.
- `query-command`: `OllamaUnavailable` message appends the `doctor` pointer.
- `entity-resolution-adjudication`: `adjudicate`'s `OllamaUnavailable` message
  appends the `doctor` pointer.
- `llm-edge-production`: `suggest-relations`' `OllamaUnavailable` message appends
  the `doctor` pointer.

## Approach

Connect / observe / warn — never manage. init's preflight builds
`OllamaClient(model=resolved_model, timeout=_PREFLIGHT_TIMEOUT)` and calls the
already-config-free `list_models()`/`model_tag_matches()` primitives directly (no
helper extraction), wrapped so ANY failure is caught broadly and treated as
strictly non-fatal. The three verbs get a one-clause string append on their
existing `OllamaUnavailable` handler only — `OllamaModelNotFound`'s precise
`ollama pull <model>` remedy stays untouched. doctor gains one `shutil.which`
branch selecting narrower-but-honest wording. Layering held: remediation stays in
`cli/`, `llm/` stays config-free.

## ADR Assessment

Per project ADR policy (significant/hard-to-reverse only): NONE warranted. This
reuses established patterns (3-tier remediation, config-free llm primitives, D1
layering), adds zero schema and zero interface changes, and is trivially
reversible. Fails the "hard-to-reverse" gate.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | init preflight; 3 verb `OllamaUnavailable` appends; doctor `shutil.which` branch; `_PREFLIGHT_TIMEOUT` constant |
| `src/openkos/llm/ollama.py` | Read-only | Reuses `list_models()`/`model_tag_matches()`; no change |
| `tests/unit/cli/` | New/Modified | Branch coverage for preflight, 3 verbs, doctor which()-gated wording (90% gate) |
| `docs/cli.md` | Modified | Note the preflight and not-installed-vs-off wording |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| init's new network probe becomes fatal / changes exit code | Med | Catch broadly; strictly non-fatal; assert exit 0 on every preflight outcome |
| Verb except-ordering regression | Med | Only append to the existing `OllamaUnavailable` string; keep specific-before-general order; test each branch |
| `shutil.which` false-negative (GUI `Ollama.app` without CLI on PATH) | Med | Never say "not installed"; phrase "no binary on PATH"; cover both remedies when uncertain |
| Remediation text drifts into `llm/` | Low | Non-goal explicit; existing AST no-config-import test enforces it |
| New branches breach 400-line PR budget | Low | Single file, additive; flag branch-count to sdd-tasks |

## Rollback Plan

Local and additive: revert the init preflight block, the three string appends,
the doctor `shutil.which` branch, and the `_PREFLIGHT_TIMEOUT` rename. No persisted
state, no schema, no migration, no `llm/ollama.py` change.

## Dependencies

Consumes archived `workspace-init`, `doctor-command`, `query-command`,
`entity-resolution-adjudication`, `llm-edge-production` read-only. `list_models()`/
`model_tag_matches()` already present. `shutil` is stdlib. No new runtime deps.

## Success Criteria

- [ ] A user with no Ollama running does `install` → `init` and, at init's finish,
      already sees ONE non-fatal warning naming `openkos doctor`; init exits 0.
- [ ] `query`/`adjudicate`/`suggest-relations` `OllamaUnavailable` messages end
      with the `doctor` pointer; ModelNotFound and exit codes unchanged.
- [ ] `doctor` distinguishes not-installed (no binary on PATH → ollama.com) from
      off (`ollama serve`), covering both when uncertain, never over-claiming.
- [ ] `_PREFLIGHT_TIMEOUT` shared by init preflight and doctor; no bare `5.0`.
- [ ] `ingest`, exit codes, and 3-tier ordering byte-unchanged; `llm/` stays
      config-free (no-config-import test green).
- [ ] `uv run pytest` green; 90% branch coverage held; ruff/mypy clean.
