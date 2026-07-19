# Proposal: `add-doctor-command` — `openkos doctor` environment health check

## Intent

With `query` working and its Ollama errors now actionable, the last onboarding gap
is that a user cannot PROACTIVELY check their setup. `openkos doctor` (a NEW 7th CLI
command) runs a fixed set of health checks and tells the user exactly what to fix —
usable even before `init`, as a pure "is my Ollama ready" preflight.

## Scope

### In Scope
- **New `@app.command() def doctor()` in `src/openkos/cli/main.py`** — runs 5 checks,
  prints EVERY applicable one as `[PASS]`/`[FAIL]` (plus an indented `  -> <fix cmd>`
  remediation line under each `[FAIL]`), never short-circuits, then exits.
  1. **workspace-initialized** (informational) — `config.require_workspace`; on fail:
     `-> openkos init`.
  2. **openkos.yaml valid** (CRITICAL when applicable) — `config.read_config` wrapped in
     `except (OSError, ValueError)`.
  3. **Ollama reachable** (CRITICAL, always applicable) — via `list_models()`; on fail:
     `-> ollama serve`.
  4. **model installed** (CRITICAL when Ollama reachable) — match configured tag
     (`cfg.model`, or `config.DEFAULT_MODEL` `qwen3:8b` outside a workspace) against
     `list_models()`; on fail: `-> ollama pull <model>`.
  5. **bundle readable/sane** (informational) — reuse `okf.survey_bundle` as `status` does.
- **Exit code**: run + print all applicable checks first, then `raise typer.Exit(code=1)`
  ONCE if any CRITICAL check failed, else exit 0.
- **Outside a workspace**: checks 2 and 5 are N/A/skipped; 3 and 4 (against `DEFAULT_MODEL`)
  still run; check 1 reports informational "not initialized". A healthy reachable Ollama
  serving `qwen3:8b` → exit 0.
- **New library capability** in `src/openkos/llm/ollama.py`: `OllamaClient.list_models()
  -> list[str]` (GET `{host}/api/tags`, reuse existing `urlopen` + `OllamaError` mapping,
  stay config-free) and pure `model_tag_matches(configured, installed) -> bool` (normalize
  bare tag to `<name>:latest`; read entry as `entry.get("model") or entry.get("name")`).
- **Docs**: add `doctor` to `docs/cli.md` and `docs/roadmap.md` (MVP-1 surface grows 6 → 7).

### Out of Scope (non-goals)
- NO color/ANSI/`NO_COLOR` infra (none exists; plain text `[PASS]`/`[FAIL]`).
- NO auto-fixing — doctor only diagnoses + advises, never runs `ollama pull`/`serve` itself.
- NO network beyond the single `/api/tags` reachability call.
- NO new config fields; NO changes to any other command.
- Remediation TEXT lives in `cli/main.py`; only the mechanical `list_models`/`model_tag_matches`
  live in `llm/` (leaf-discipline: `test_llm_modules_do_not_import_config` must stay green).

## Capabilities

### New Capabilities
- `doctor-command`: the `openkos doctor` command — 5-check health scan, criticality split,
  run-all-then-exit-once contract, and outside-workspace preflight behavior.

### Modified Capabilities
- `llm-client`: add `OllamaClient.list_models()` (GET `/api/tags`, same error vocabulary as
  `chat()`) and the pure `model_tag_matches()` helper — config-free, leaf-safe.

## Approach

`doctor` diverges from every existing command's "raise on first failure" idiom: each check
returns a small `(passed, critical, detail)` result; the render loop prints unconditionally
and accumulates a `critical_failed` flag, exiting once at the end. Detection is layered
(`require_workspace` → `read_config`/survey), mirroring `query`. `list_models()` clones
`chat()`'s urlopen + `_map_http_error`/`_unavailable` plumbing; model-missing is N/A when
Ollama is unreachable (no double-reporting one root cause).

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | New `doctor` command with all remediation text |
| `src/openkos/llm/ollama.py` | Modified | New `list_models()` method + `model_tag_matches()` helper |
| `tests/unit/cli/test_doctor.py` | New | CLI scenario coverage (down/missing/malformed/no-ws/healthy) |
| `tests/unit/llm/test_ollama.py` | Modified | `list_models()` + `model_tag_matches()` unit tests |
| `docs/cli.md`, `docs/roadmap.md` | Modified | Add `doctor` (6 → 7 commands) |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `/api/tags` field variance (`name` vs `model`) across Ollama versions (#9985) | Med | Read `entry.get("model") or entry.get("name")`, skip entries with neither |
| Tag matching too strict/loose (bare tag vs `:latest`) | Med | Pure `model_tag_matches` normalizes both sides to `<name>:latest`; unit-tested |
| A failing check short-circuits later checks | Med | New run-all-accumulate control flow; test proves no early `typer.Exit` mid-scan |
| Remediation text leaks into `llm/` | Low | Non-goal explicit; AST leaf-discipline test enforces no config import |

## Rollback Plan

Additive and local: remove the `doctor` command from `cli/main.py`, remove
`OllamaClient.list_models()` + `model_tag_matches()` from `llm/ollama.py`, remove the new
tests, and revert the `docs/cli.md`/`docs/roadmap.md` entries. No persisted state, no
migration, no config-schema change, no dependency change, no touch to other commands.

## Dependencies

- Consumes `config.require_workspace`/`read_config`/`DEFAULT_MODEL`, `okf.survey_bundle`,
  and the `OllamaClient`/`OllamaError` family read-only (no signature changes to callers).

## Success Criteria

- [ ] `openkos doctor` prints all 5 applicable checks as `[PASS]`/`[FAIL]` with `-> <fix>` lines.
- [ ] Any CRITICAL fail (config-valid/Ollama-reachable/model-installed) → exit 1; else exit 0.
- [ ] Outside a workspace with reachable Ollama serving `qwen3:8b` → exit 0.
- [ ] No check short-circuits a later check from running/printing.
- [ ] `list_models()` tolerates `name`/`model` field variance; `model_tag_matches` handles bare tags.
- [ ] `llm/` no-config-import test stays green; `docs/cli.md` + `docs/roadmap.md` list `doctor`.
- [ ] `uv run pytest` green; ruff/mypy clean.

## AGENTS.md Non-Negotiables

Honored: local-first & private (single local `/api/tags` call, no cloud); offline-tolerant
(unreachable Ollama is a clean `[FAIL]` + remediation, never a crash); honest (states real
cause + the user's OWN fix command, never claims to manage Ollama). Leaf-module discipline
respected: remediation vocabulary stays in `cli/main.py`; `llm/` keeps only the mechanical
`list_models`/`model_tag_matches` and remains config-free.
