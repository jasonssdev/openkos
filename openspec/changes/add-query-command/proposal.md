# Proposal: `add-query-command` — the `openkos query` CLI command (MVP-1 query chain #4)

## Intent

MVP-1's query chain is code-complete but headless: `fts-state` (#1), `llm-client`
(#2), and the `query-answer` library (#3) are archived, tested, and dormant — no
user can invoke them. `docs/cli.md:74-76` promises `openkos query "<question>"`
with cited answers, but the command does not exist. This change lands the thin
CLI wiring: a read-only `query` command that gates on the workspace, builds an
`OllamaClient` from config, calls `retrieval.answer()`, and renders the answer
plus citations as plain text — honoring AGENTS.md non-negotiables (local-first,
offline, cited answers). Plus two small `answer.py` follow-ups deferred from #3.

## Scope

### In Scope

- **New `@app.command() def query(question, --limit)`** in `cli/main.py`: reuse
  `config.require_workspace(root)` (refuse, exit 1, like `status`/`lint`); read
  `read_config(root).model` + `WorkspaceLayout(root).bundle_dir`; build
  `OllamaClient(model=...)`; call `answer(question, bundle_dir=..., llm=..., limit=n)`.
- **`--limit <n>` Option** (default 5) forwarded to `answer(..., limit=n)` — exposes
  an existing param at near-zero cost.
- **Plain-text render**: answer text, then a `Citations:` section listing each
  `→ <concept_id> (<title>)`. No-match (`citations == []`, `answer == NO_MATCH`):
  render the answer line only, **exit 0** (a valid "no match" answer, not an error).
- **Error mapping**: `except (FtsUnavailable, OllamaError) as exc:` around the
  `answer()` call → `openkos query: failed -- {exc}.` on stderr, exit 1. `OllamaError`
  base covers `OllamaUnavailable`/`OllamaModelNotFound`.
- **Two `answer.py` follow-ups** (+ `test_answer.py`): (a) docstring on `_SYSTEM_PROMPT`
  citing D5; (b) multi-survivor test asserting hit-rank citation ORDER and the
  two-block `\n\n`-joined user content.
- **`docs/cli.md:74-76`** stub fleshed out to real behavior (incl. `--limit`).
- **`tests/unit/cli/test_query.py`** (CliRunner, monkeypatch `answer`).

### Out of Scope (named non-goals)

- **`--no-color`/`NO_COLOR`/ANSI color** — documented convention exists in ZERO
  commands today; implementing it here is cross-cutting scope creep. Deferred to a
  future dedicated CLI-polish change.
- Streaming output; answer-refiling / two-output-rule automation; semantic/vector
  retrieval (lexical FTS5 only); any `answer()` signature change.
- **`pyproject.toml` dependency changes** — `typer` is already a runtime dep; the
  whole chain is stdlib/runtime. Confirmed no-op.

## Capabilities

### New Capabilities
- `query-command`: the `openkos query` CLI command — workspace gate, config-driven
  `OllamaClient`, `answer()` invocation, plain-text answer+citations rendering,
  no-match exit-0 path, and `(FtsUnavailable, OllamaError)` → exit-1 mapping.

### Modified Capabilities
- None. `query-answer`'s follow-ups (a docstring + a new test) change no requirement;
  `fts-state`/`llm-client`/`query-answer` are consumed read-only, `ingest`/`forget`/
  `status`/`lint` untouched.

## Approach

Mirror the existing read-only command shape (`status`/`lint`): `require_workspace`
refusal, no confirm gate, no `--auto`. Two phases — (A) workspace + config +
client construction, (B) a single `try/except (FtsUnavailable, OllamaError)` around
`answer()`, then render. Symbols follow the plain-glyph precedent (`+`/`~`/`-`), using
`→` as a plain bullet — no color to gate on. CLI tests monkeypatch the imported
`answer` symbol (no live Ollama/network); `test_answer.py` owns the FTS/prompt path.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | New `query` command + imports (`OllamaClient`/`OllamaError`, `answer`, `FtsUnavailable`) |
| `src/openkos/retrieval/answer.py` | Modified | `_SYSTEM_PROMPT` docstring (D5) only |
| `tests/unit/cli/test_query.py` | New | CliRunner coverage: render, no-match, `--limit`, error-map, refusal |
| `tests/unit/retrieval/test_answer.py` | Modified | Multi-survivor citation-order + two-block-join test |
| `docs/cli.md` | Modified | Flesh out `query` stub (incl. `--limit`, output shape, exit codes) |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Output layout/no-match exit undecided | Low | Resolved here: plain `→`-bulleted citations, no-match = exit 0; design finalizes exact strings |
| First CLI to catch network/`RuntimeError`-family exceptions | Low | Types already exported; explicit `(FtsUnavailable, OllamaError)` clause, design-noted not silent copy-paste |
| Scope creep via `--no-color`/color infra | Med | Named non-goal; explicitly deferred |
| Review size | Low | One command + two test files + docs — likely single PR; forecast at `sdd-tasks` |

## Rollback Plan

Trivial and additive: delete the `query` command function + its imports from
`cli/main.py`, revert the `_SYSTEM_PROMPT` docstring line and the multi-survivor
test, delete `tests/unit/cli/test_query.py`, and revert the `docs/cli.md` stub. No
persisted state, no migration, no config-schema change, no dependency change.

## Dependencies

- **Upstream (archived)**: `add-fts-state` (#1), `add-ollama-client` (#2),
  `add-query-answer` (#3). All confirmed with exact signatures in the exploration.
- **Unblocks**: completes MVP-1's query chain — `openkos query` becomes usable.

## Success Criteria

- [ ] `openkos query "<q>"` inside a workspace prints the answer + `→`-bulleted
      citations (concept_id + title) and exits 0.
- [ ] No-match renders the answer line only, no `Citations:` section, exits 0.
- [ ] Outside a workspace: refusal on stderr, exit 1 (shared `require_workspace`).
- [ ] `FtsUnavailable`/`OllamaError` family → `openkos query: failed -- ...`, exit 1.
- [ ] `--limit <n>` forwards to `answer(..., limit=n)`; default 5.
- [ ] `_SYSTEM_PROMPT` has a D5-citing docstring; multi-survivor test asserts
      citation order + two-block `\n\n` join.
- [ ] No `pyproject.toml` change; `uv run pytest` green ≥90% branch; ruff/mypy clean.
</content>
</invoke>
