# Proposal: `improve-ollama-onboarding` — actionable Ollama errors on first run

## Intent

Now that `query` (MVP-1 chain) works, the #1 first-run friction is that a user
whose Ollama isn't running, or whose configured model isn't pulled, only learns
at `query` time via a non-actionable technical error, e.g.
`openkos query: failed -- Ollama not reachable at http://localhost:11434: <urlopen error [Errno 61] Connection refused>`.
It states the fact but never tells the user HOW to fix it. This change makes
`query`'s Ollama failures ACTIONABLE, in-surface, by branching the single combined
`except (FtsUnavailable, OllamaError)` block into per-cause handlers that append
openkos-authored remediation. Small diff, high UX impact: it makes MVP-1 genuinely
testable by users who haven't finished setting up Ollama.

## Scope

### In Scope
- **`src/openkos/cli/main.py` (`query`, ~L699-704)** — split the combined
  `except` into ORDERED handlers (specific before general, since both subclass
  `OllamaError`):
  - `except OllamaUnavailable` — keep the host context AND tell the user to start
    Ollama (spirit: "Ollama isn't responding at `<host>`. Start it with
    `ollama serve`, then try again.").
  - `except OllamaModelNotFound` — tell the user to pull the configured model
    (spirit: "Model `<cfg.model>` isn't installed. Pull it with
    `ollama pull <cfg.model>`, then try again."). `cfg.model` is already resolved.
  - `except (FtsUnavailable, OllamaError)` — existing generic fallback, unchanged.
  All still `err=True` + `raise typer.Exit(code=1)`, no traceback. Literal strings
  finalized in design.
- **`docs/cli.md`** — one-line clarification of `query`'s actionable error behavior.

### Out of Scope (non-goals)
- NO new `openkos doctor` command (Shape B) — a new command outside the documented
  MVP-1 six (`init`/`ingest`/`query`/`lint`/`status`/`forget`), with its own
  exit-code/scope/`/api/tags` design forks. Deferred to a separate future change;
  recommended as follow-up.
- NO proactive preflight; NO changes to `llm/ollama.py` structure or its exception
  messages; NO new `OllamaClient` methods (e.g. `list_models()`); NO config `host` field.
- NO change to exit codes (still 1); NO change to the no-match or success paths.
- Remediation text MUST live in `cli/main.py` — NOT `llm/ollama.py`, which stays a
  clean library leaf (a test AST-asserts `llm/` never imports `openkos.config`).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `query-command`: the "LLM And Index Errors Map To Exit 1" requirement is refined
  so `OllamaUnavailable` and `OllamaModelNotFound` produce distinct, actionable
  remediation messages; generic `OllamaError`/`FtsUnavailable` fallback unchanged.

## Approach

Reactive, in-surface (exploration-verified). `answer()` raises the typed exceptions;
`query` catches them in type-specific order. `cfg.model` is already resolved before
`OllamaClient` construction, so the model-pull message references it with zero extra
plumbing; host stays visible from the existing `OllamaUnavailable` message. The
leaf-module discipline is preserved: `llm/ollama.py` keeps emitting structural facts
only (host, code, detail); all CLI-specific remediation vocabulary lives in
`cli/main.py`.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | `query` except-block split into ordered per-cause handlers with remediation |
| `docs/cli.md` | Modified | One-line clarification of `query` actionable error behavior |
| `tests/unit/cli/test_query.py` | Modified | New assertions for the two remediation messages (reuses `answer`-monkeypatch seam) |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Except-clause ordering wrong (subclasses caught by base first) | Med | Specific `OllamaUnavailable`/`OllamaModelNotFound` handlers MUST precede generic catch; test each branch's message |
| Remediation text drifts into `llm/ollama.py` | Low | Non-goal is explicit; existing AST test enforces `llm/` no-config-import discipline |
| Wording implies openkos manages Ollama | Low | Honest phrasing: instruct the user's own `ollama serve`/`ollama pull`, no automation claim |

## Rollback Plan

Local and additive: revert the `query` except-block split back to the single
`except (FtsUnavailable, OllamaError)` clause, and revert the `docs/cli.md` line and
test additions. No persisted state, no migration, no config-schema change, no
dependency change, no touch to `llm/ollama.py`.

## Dependencies

- Consumes archived `query-command`/`query-answer`/`llm-client` read-only (no
  signature changes).

## Success Criteria

- [ ] With Ollama not running, `query` prints an actionable message naming the host
      and `ollama serve`, exit 1, no traceback.
- [ ] With the configured model not pulled, `query` prints an actionable message
      naming `cfg.model` and `ollama pull <cfg.model>`, exit 1, no traceback.
- [ ] Other `OllamaError`/`FtsUnavailable` cases keep the existing generic message.
- [ ] No change to `llm/ollama.py`; the `llm/` no-config-import test stays green.
- [ ] `docs/cli.md` describes the actionable `query` error behavior.
- [ ] `uv run pytest` green; ruff/mypy clean.

## AGENTS.md Non-Negotiables

Honored: local-first & private (no new network calls, still local Ollama); offline
(reactive-only, no new preflight round-trips); honest errors (messages state the real
cause and the user's own fix, no automation or cloud claim). Leaf-module discipline
respected: remediation stays in `cli/main.py`, `llm/` remains config-free.
