# Design: `add-query-command` — the `openkos query` CLI command (MVP-1 query chain #4)

## Technical Approach

One new `@app.command() def query` in `src/openkos/cli/main.py`, mirroring the
read-only `status`/`lint` shape exactly (no Phase B, no confirm gate, no
`--auto`). It gates on `config.require_workspace`, resolves `read_config(root).model`
+ `WorkspaceLayout(root).bundle_dir`, builds an `OllamaClient(model=...)`, calls
the archived `retrieval.answer.answer(...)`, and renders answer + citations as
plain text via `typer.echo`. Plus two `answer.py` follow-ups deferred from #3
(a `_SYSTEM_PROMPT` docstring, a multi-survivor test). Zero new dependencies
(`typer` is already a runtime dep; `pyproject.toml` unchanged).

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **D1** | **Read-only shape = `status`/`lint`**: bare `reason = require_workspace(root); if reason is not None: echo(refuse, err); Exit(1)` — NOT the try-wrapped `ingest`/`forget` form. | `ingest`'s try-wrapped gate. | `require_workspace` already catches `OSError` internally and returns the permission-denied reason string (proven by `test_status_refuses_cleanly...`); `status`/`lint` are the true read-only siblings. |
| **D2** | **Two error boundaries**: Phase A wraps `read_config` in `except (OSError, ValueError)` → `openkos query: failed while reading the workspace -- {exc}.` (lint parity); Phase B wraps `answer()` in `except (FtsUnavailable, OllamaError)` → `openkos query: failed -- {exc}.`. | Single guard around `answer()` only (explore sketch). | `lint` already guards its `read_config`; leaving a malformed `openkos.yaml` to raise a raw traceback violates the codebase's no-traceback discipline. `OllamaError` base catches `OllamaUnavailable`/`OllamaModelNotFound`. |
| **D3** | **Answer-first, banner-free output**; `Citations:` section only when non-empty. No-match renders the answer line alone, exit 0. | `status`-style `openkos query: workspace at {root}` banner. | The answer is the payload a user reads/pipes; a workspace banner would pollute it. No-match is a valid answer (D3 of #3), never an error. |
| **D4** | **Import the `answer` symbol** (`from openkos.retrieval.answer import answer`) so tests patch the `openkos.cli.main.answer` boundary. | Import the module (`answer_mod.answer`). | Boundary patch keeps CLI unit tests free of live Ollama/FTS; `test_answer.py` owns the real chain. |

**ADR gate — verdict: NO ADR.** (1) Decides a pattern/interface/tradeoff? Only a
minor plain-text render layout — not a technology/architecture choice. (2)
Hard-to-reverse? **No** — purely additive, dormant-until-invoked, `git revert`
deletes the command, no persisted state, no schema/migration. Both must hold; (2)
fails. Matches the zero-ADR precedent of `add-fts-state`/`add-ollama-client`/
`add-query-answer`.

## Command signature & control flow

```python
@app.command()
def query(
    question: str = typer.Argument(..., help="Natural-language question to answer from the bundle."),
    limit: int = typer.Option(5, "--limit", help="Max concepts to retrieve as context."),
) -> None:
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos query: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)
    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos query: failed while reading the workspace -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc
    llm = OllamaClient(model=cfg.model)
    try:
        result = answer(question, bundle_dir=layout.bundle_dir, llm=llm, limit=limit)
    except (FtsUnavailable, OllamaError) as exc:
        typer.echo(f"openkos query: failed -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(result.answer)
    if result.citations:
        typer.echo()
        typer.echo("Citations:")
        for citation in result.citations:
            typer.echo(f"  → {citation.concept_id} ({citation.title})")
```

New imports in `cli/main.py`: `from openkos.retrieval.answer import answer`,
`from openkos.llm.ollama import OllamaClient, OllamaError`,
`from openkos.state.fts import FtsUnavailable`.

**Literal output (match):** answer text, blank line, `Citations:`, then indented
`  → concepts/stoicism (Stoicism)` per citation (2-space indent + `→` bullet,
following the `+`/`~`/`-` glyph precedent of `ingest`/`forget`). **No-match:** the
single line `No matching concepts were found in the compiled bundle for this
question.`, no `Citations:` section, exit 0.

## Sequence

```
user ─ openkos query "<q>" [--limit n]
  │
  ├─ require_workspace(root) ─ not None ─→ echo "refusing to run -- {reason}." (stderr); Exit 1
  │        │ None
  ├─ read_config(root) ─ (OSError|ValueError) ─→ echo "failed while reading the workspace -- {exc}."; Exit 1
  │        │ cfg.model
  ├─ OllamaClient(model=cfg.model)                 # pure ctor, no I/O
  ├─ answer(q, bundle_dir=layout.bundle_dir, llm, limit)
  │        └─ (FtsUnavailable|OllamaError) ─→ echo "failed -- {exc}."; Exit 1
  │        │ AnswerResult(answer, citations)
  └─ echo answer; if citations: echo "Citations:" + "  → {id} ({title})" each; Exit 0
```

## `_SYSTEM_PROMPT` docstring follow-up

Add directly under the constant in `answer.py` (NO_MATCH docstring style):

```python
"""Stable system half of the 2-message prompt (D5): local-first grounding
rules (answer only from CONTEXT, cite by concept id, admit gaps honestly)
baked into system text; the `user` message carries the context blocks +
question."""
```

## Multi-survivor test follow-up

`tests/unit/retrieval/test_answer.py::test_multiple_surviving_hits_cite_in_rank_order_and_join_context`.
Two readable concepts (`concepts/stoicism` rank 1.0, `concepts/epictetus` rank
0.5); `_RecordingIndex` returns both in that order; monkeypatch `fts.build_index`;
`_FakeLLM`. Assert: `result.citations == [Citation("concepts/stoicism", "Stoicism"),
Citation("concepts/epictetus", "Epictetus")]` (exact ORDER); and the captured user
message (`llm.calls[0][1]["content"]`) contains the exact substring
`"[concept_id: concepts/stoicism — Stoicism]\n...\n\n[concept_id: concepts/epictetus — Epictetus]\n..."`
(both blocks, `\n\n`-joined, in rank order) — assert order via
`content.index(stoicism_block) < content.index(epictetus_block)`.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modify | New `query` command + 3 imports |
| `src/openkos/retrieval/answer.py` | Modify | `_SYSTEM_PROMPT` docstring (D5) only — no signature change |
| `tests/unit/cli/test_query.py` | New | CliRunner tests (see below) |
| `tests/unit/retrieval/test_answer.py` | Modify | Multi-survivor order + join test |
| `docs/cli.md` | Modify | Flesh out the `openkos query` stub (74-76) |

## Testing Strategy (strict TDD, ≥90 branch, no network)

`tests/unit/cli/test_query.py`: `CliRunner`, the shared `_init_workspace(tmp_path,
monkeypatch)` helper, and `monkeypatch.setattr("openkos.cli.main.answer", fake)`.
`OllamaClient()` is constructed but never calls the network (its `chat` is never
reached when `answer` is faked). Cases → spec scenarios:

| Test | Asserts |
|---|---|
| refuses outside workspace | exit 1, `openkos query: refusing to run -- ...` stderr, no Traceback |
| happy path with citations | fake → `AnswerResult("reply", [c1, c2])`; stdout has reply, `Citations:`, `  → {id} ({title})` per cite, exit 0 |
| no-match render | fake → `AnswerResult(NO_MATCH, [])`; stdout == NO_MATCH line, no `Citations:`, exit 0 |
| `--limit` forwarding | recorder fake captures kwargs; `--limit 3` → limit==3; omitted → 5 |
| `FtsUnavailable` mapped | fake raises `fts.FtsUnavailable`; exit 1, `failed -- ...`, no Traceback |
| `OllamaError` mapped | fake raises `OllamaUnavailable`; exit 1, `failed -- ...`, no Traceback |

## docs/cli.md plan (lines 74-76)

Expand the stub: read-only; workspace gate (refuse exit 1 outside); requires a
local Ollama serving the configured model; `--limit <n>` (default 5) flags table
like `ingest`'s; output shape (answer then `→`-bulleted `Citations:`); no-match
prints the answer line only and exits 0; `FtsUnavailable`/`OllamaError` → friendly
stderr, exit 1. Keep the "file a good answer back (two-output rule)" note.

## Threat Matrix

**N/A** — no shell, subprocess, routing, VCS/PR automation, or executable-file
classification. The question reaches SQLite already neutralized by `fts._quote_query`
(#1) and the LLM prompt via list assembly (never executed); the reply is echoed
verbatim, never run. The only new surface is an outbound HTTP call inside
`OllamaClient`, wrapped by the `OllamaError` family.

## Migration / Rollout

No migration. Additive; `git revert` removes the command + imports, the docstring,
the tests, and the docs edit. No config-schema or dependency change.

## Open Questions

- [ ] None blocking. D1–D4 resolved; ADR gate closed (none); output layout,
      no-match exit, and error boundaries all fixed above.
