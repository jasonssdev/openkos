# Design: Ingest Progress Feedback (spinner + per-type tally)

## Technical Approach

Two additive, stderr-safe UX signals in `src/openkos/cli/main.py`, no new spec
capability: (1) a `rich` spinner on **stderr** wrapping only the blocking
`extract_concept` call (`main.py:493-501`); (2) a reusable
`_format_type_tally(counts: dict[str, int]) -> str` helper (near `_plural`,
`main.py:348-351`) rendering one strictly-additive stdout line after the summary
(`main.py:925`). Verified against source: `rich==15.0.0` locked transitive of
`typer==0.27.0` (`uv.lock:710`), no `pyproject.toml` change. Strict TDD: every
requirement below has a named RED test.

## Architecture Decisions

### Decision: Canonical ordering source
**Choice**: Sort tally types by key-index of the already-imported ordered dict
`_TYPE_TO_SECTION` (`main.py:42`, `TYPE_TO_SECTION` from `model/types.py:63-66`).
**Alternatives**: order by `CLASSIFIABLE_TYPES` — **rejected, it is a
`frozenset` (types.py:51) and is UNORDERED**; import `REGISTRY` directly — adds a
new import for no gain. **Rationale**: `_TYPE_TO_SECTION` is an insertion-ordered
dict whose key order == `REGISTRY` classifiable order (Concept, Entity, Place,
Event, Procedure, Decision, Project, Person, Organization), already imported, and
mirrors the existing canonical-order precedent `_bundle_content_lines`
(`main.py:3319`). Ordering key: `list(_TYPE_TO_SECTION).index(t)`.

### Decision: `_format_type_tally` contract
**Choice**: `def _format_type_tally(counts: dict[str, int]) -> str`. Returns
`extracted {N} objects — {c} {Type}, ...` (em dash `—`, comma-joined, canonical
order); `N` = `sum(counts.values())`; "object" pluralized `f"object{_plural(N)}"`
(confirmed `_plural(n:int)->str` returns `""`/`"s"`). Empty/zero-total dict
returns `""`. **Caller guards** by only calling when `derived_plans` is non-empty;
helper still returns `""` defensively. **Rationale**: guard + defensive empty
keeps the helper reusable (#133 could feed a `dict[str,int]` later) without
coupling to ingest.

### Decision: Spinner integration
**Choice**: Inside `_stage_derived_objects`, construct `Console(stderr=True)`
**per-call**, wrap the call in `with console.status("openkos ingest: extracting
concepts…"):` placed **inside** the existing `try`, so `__exit__` clears the
spinner before the `except OllamaError` handler runs — cleared on both success
and error. `rich.console.Console.status` exists in 15.0.0. **Alternatives**:
module-level Console — **rejected**, binds the wrong stream under `CliRunner`
stream-swapping; `rich.status.Status(...)` directly — equivalent, Console.status
is the idiomatic wrapper. **Rationale**: stderr never pollutes the stdout the
suite asserts on; rich auto-detects a non-TTY output stream and emits nothing
(no control chars) when stderr is a pipe.

## Data Flow

    extract_concept (blocking ~20s)
      └─ wrapped by Console(stderr=True).status(...)  → spinner on TTY, silent on pipe
    derived_plans[_DerivedPlan.doc_type] ──Counter──▶ dict[str,int]
      └─ _format_type_tally(counts) ──▶ typer.echo(line)  (stdout, after :925)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` | Modify | Add `from rich.console import Console` + `from collections import Counter`; spinner wrap at `493-501`; `_format_type_tally` near `348-351`; tally echo after `925` |
| `tests/unit/cli/test_ingest.py` | Modify | Tally (zero/one/mixed), spinner-invoked, non-TTY silence, clears-on-error |

## Interfaces / Contracts

```python
def _format_type_tally(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    if total == 0:
        return ""
    order = {t: i for i, t in enumerate(_TYPE_TO_SECTION)}
    parts = ", ".join(
        f"{counts[t]} {t}" for t in sorted(counts, key=lambda t: order[t])
    )
    return f"extracted {total} object{_plural(total)} — {parts}"
```

Caller (after `main.py:925`, before `_autocommit` at `927`):

```python
if derived_plans:
    counts = Counter(p.doc_type for p in derived_plans)
    typer.echo(_format_type_tally(counts))
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `_format_type_tally` empty/one/mixed + ordering | Call directly: `{}`→`""`; `{"Entity":1,"Concept":2}`→`"extracted 3 objects — 2 Concept, 1 Entity"` (canonical) |
| CLI | Tally line on stdout | `_patch_llm(_concept_reply)`/`_entity_reply`, `--auto`; assert substring in `result.stdout` |
| CLI | Non-TTY spinner silence | default `CliRunner` (pipe); assert no `\x1b[` / spinner glyphs in `result.stdout`/`result.stderr`; exit 0 |
| CLI | Spinner invoked + cleared on error | Spy `monkeypatch.setattr("openkos.cli.main.Console", ...)`; assert `.status()` entered and `__exit__` ran; drive error via `_patch_llm(raises=OllamaUnavailable(...))` |

**Stream separation confirmed**: existing tests read `result.stderr` and
`result.stdout` independently (`test_ingest.py:374,425,551,592`), so this
`CliRunner` separates streams (Click ≥8.2 / `mix_stderr=False`). No mixing.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. Spinner is pure stderr
rendering; tally is pure string formatting. `_autocommit` unchanged.

## Migration / Rollout

No migration. Additive, revert-the-commit rollback.

## Open Questions

- [ ] **Spinner PRESENCE on TTY** is not assertable via captured output: rich
  checks the OUTPUT stream's `is_terminal`, and `_simulate_tty` only patches
  `sys.stdin`. Test presence via the `main.Console` spy seam (table above), not
  by emulating a TTY pipe. Flags the proposal's "spinner shows on TTY" criterion
  as behaviorally verified through the invocation seam, not raw glyph capture.
- [x] Resolved: `OllamaUnavailable(OllamaError)` (`ollama.py:37`) — `_FakeLLM`
  raising it in `chat()` propagates through `extract_concept` to the
  `except OllamaError` handler (`main.py:495`). No open risk.
