# Design: `improve-ollama-onboarding` — actionable Ollama errors in `query`

## Technical Approach

Split `query`'s single combined `except (FtsUnavailable, OllamaError)` block
(`cli/main.py:700-704`) into three ORDERED handlers: `OllamaUnavailable` first,
`OllamaModelNotFound` second, and the existing `(FtsUnavailable, OllamaError)`
tuple LAST as the fallback. Each still uses the established idiom
(`typer.echo(..., err=True)` + `raise typer.Exit(code=1) from exc`, no traceback).
openkos-authored remediation lives ONLY in `cli/main.py`; `llm/ollama.py` stays
untouched (structural messages only), preserving the leaf-module discipline the
`test_llm_modules_do_not_import_config` AST test enforces. No new client method,
no config `host` field, no exit-code change. Shape A only — no `doctor` command.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **D1** | Order handlers specific-before-general: `OllamaUnavailable` → `OllamaModelNotFound` → `(FtsUnavailable, OllamaError)`. | Keep the generic tuple first / re-order arbitrarily. | Both specific classes subclass `OllamaError` (`ollama.py:29,35`). Python matches `except` top-down; if the `OllamaError` tuple comes first it swallows both subclasses and silently reverts to today's generic message. Order is load-bearing. |
| **D2** | Unavailable message KEEPS `{exc}` (already carries the host) and APPENDS a remediation sentence naming `ollama serve`. | Reconstruct host via a new `cfg.host` field. | `OllamaUnavailable`'s message is `Ollama not reachable at {host}: {transport}` (`ollama.py:123`) — host is already visible. Appending avoids adding any config plumbing and keeps the existing test's `"Ollama not reachable"` + `startswith("openkos query: failed -- ")` assertions green. |
| **D3** | Model-not-found message AUTHORS clean text from `cfg.model` (in scope at `main.py:692/699`) and drops `{exc}`. | Interpolate `{exc}`. | `OllamaModelNotFound`'s message is `Model not found (404): {raw JSON}` — confusing and offers no isolated tag. `cfg.model` is the SAME tag passed to `OllamaClient(model=cfg.model)`, so the pull command is exact with zero plumbing. |
| **D4** | Generic `(FtsUnavailable, OllamaError)` fallback string unchanged. | Rewrite it. | Out of scope; FTS and non-typed Ollama errors keep today's honest wording. |

**ADR gate — verdict: NO ADR.** (1) New pattern/interface/tradeoff introducing a
technology/architecture choice? No — additive `except` branches reusing the
established echo/Exit idiom. (2) Hard-to-reverse? No — `git revert` restores the
single handler; no persisted state, schema, config, or dependency change. Both
conditions must hold; both fail. Matches the zero-ADR precedent of
`add-query-command` / `ingest-source-body`.

## Interfaces / Contracts

Exact new block replacing `main.py:700-704`:

```python
try:
    result = answer(question, bundle_dir=layout.bundle_dir, llm=llm, limit=limit)
except OllamaUnavailable as exc:
    typer.echo(
        f"openkos query: failed -- {exc}. Start it with `ollama serve`, "
        "then try again.",
        err=True,
    )
    raise typer.Exit(code=1) from exc
except OllamaModelNotFound as exc:
    typer.echo(
        f"openkos query: failed -- model '{cfg.model}' is not installed. "
        f"Pull it with `ollama pull {cfg.model}`, then try again.",
        err=True,
    )
    raise typer.Exit(code=1) from exc
except (FtsUnavailable, OllamaError) as exc:
    typer.echo(f"openkos query: failed -- {exc}.", err=True)
    raise typer.Exit(code=1) from exc
```

Import change at `main.py:15`: add the two subclasses —
`from openkos.llm.ollama import OllamaClient, OllamaError, OllamaModelNotFound, OllamaUnavailable`.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modify | Add 2 imports (L15); split L700-704 into 3 ordered handlers (D1-D4). |
| `src/openkos/llm/ollama.py` | — | UNCHANGED (leaf discipline; AST test stays green). |
| `docs/cli.md` | Modify | L84: clarify the query error prose. |
| `tests/unit/cli/test_query.py` | Modify | Add `OllamaModelNotFound, OllamaError` import (L17); extend/add branch tests. |

## Testing Strategy (strict TDD, ≥90 branch, no network)

| Layer | What | Approach |
|---|---|---|
| Unit | Unavailable branch | monkeypatch `openkos.cli.main.answer` → raise `OllamaUnavailable("Ollama not reachable at http://localhost:11434")`; assert stderr contains `ollama serve` AND `Ollama not reachable`, exit 1, no `Traceback`. (Extends existing L177 test.) |
| Unit | Model-not-found branch | raise `OllamaModelNotFound(...)`; assert stderr contains `` `ollama pull <configured-model>` `` with the ACTUAL configured tag AND `is not installed`, exit 1. |
| Unit | Configured-model in pull msg | assert the exact configured model NAME appears in the pull command (guards a wrong-model regression, e.g. hardcoding). |
| Unit | Generic `OllamaError` | raise a plain `OllamaError("boom")`; assert generic fallback unchanged (no `ollama serve`/`ollama pull` text), exit 1. |
| Unit | `FtsUnavailable` | existing L199 test unchanged — still hits the generic tuple. |

Seam: reuse `openkos.cli.main.answer` monkeypatch (CliRunner, zero network, zero
real client). Ordering is verified by asserting each subclass reaches ITS message,
not the generic one — this is the direct RED test for the D1 ordering risk.

## docs/cli.md plan

L84: refine the clause "a failure to reach Ollama … is caught and reported on
stderr (exit 1), never a raw traceback" to note that an unreachable Ollama and a
not-installed configured model now print ACTIONABLE guidance (`ollama serve` /
`ollama pull <model>`). One-line prose only; no command-list change.

## Threat Matrix

**N/A** — no routing, subprocess, shell execution, VCS/PR automation, or
executable-file classification. The remediation strings mention `ollama serve` /
`ollama pull` as INFORMATIONAL display text on stderr; openkos never executes them
and makes no new network call (still purely reactive to `answer()`).

## Migration / Rollout

No migration. Additive and local; `git revert` restores the single combined
handler. No persisted state, config schema, or dependency change.

## Open Questions

- [ ] None blocking. Ordering (D1), message shape (D2/D3), and leaf discipline resolved.
