# Design: LLM Edge Production (MVP-2 Slice 2b)

> Corrected re-run (v2). Two human architectural corrections applied: (1) the LLM
> module moves OUT of `graph/` into the derived `resolution/` package so the
> "No CLI Surface" invariant and its AST test stay GREEN and UNMODIFIED; (2) the
> typed-pair annotation is DROPPED to keep design and spec identical (untyped-only,
> no re-surfacing). Everything else from v1 stands.

## Technical Approach

Mirror `resolution/adjudication.py` exactly, one layer over. A new derived-layer LLM
module `resolution/edge_typing.py` OWNS the graph read internally: it calls `build_graph`,
filters to UNTYPED body-link edges (`relation_type is None`), prompts an injected
`LLMBackend` for a relation type + rationale per edge, and returns one `EdgeSuggestion`
per untyped edge (same order, fail-closed). A new read-only CLI verb `suggest-relations`
imports ONLY `openkos.resolution.edge_typing` — NEVER `openkos.graph` — mirroring
`adjudicate`'s workspace gate / config guard / 3-tier Ollama handler, then tells the human
to write via existing `relate`. Zero writes, zero schema change, slice-2a untouched
(no writes ⇒ no ledger surface). See proposal + spec (`llm-edge-production`).

## Architecture Decisions

### D1 — Verb name: `suggest-relations` (unchanged from v1)
**Choice**: `suggest-relations` (advisory, present-tense, like `adjudicate`).
**Rejected**: `type-links` (ambiguous "type", leaks "links" internal); `propose-relations`
(collides with SDD "proposal").
**Rationale**: `adjudicate` PRINTS a verdict and never writes; this PRINTS a suggestion and
never writes. "relations" binds it to `relate` / `relations:` frontmatter.

### D2 — Module home: `src/openkos/resolution/edge_typing.py` (NOT `graph/`) — CORRECTED
**Choice**: new leaf in the derived `resolution/` package.
**Rejected**: `graph/edge_typing.py` (v1's choice) — the CLI cannot import `openkos.graph`
(No CLI Surface invariant), so any verb wired to a `graph/` module would force a spec/test
relaxation. `resolution/edges.py` naming — kept `edge_typing.py` for intent clarity.
**Rationale**: The architectural boundary WINS over domain-semantics locality. Edge typing
DOES conceptually relate to the graph — but that relationship is expressed by the module's
name, docstring, and internal `build_graph` read, NOT by physically nesting under `graph/`,
which is the one package the CLI is forbidden to touch. `cli/main.py:33-34` already imports
`openkos.resolution` / `openkos.resolution.adjudication` and never imports graph — we mirror
that precedent exactly. `resolution/__init__.py` already anticipates this: it notes the
package "does not import `openkos.graph` **this slice**" — slice 2b is that slice.

### D3 — Candidate sourcing: filter `edges()` internally, no Protocol change (unchanged)
**Choice**: `untyped_edges(store) = [e for e in store.edges() if e.relation_type is None]`,
encapsulated INSIDE `edge_typing.py`. No new `GraphStore` method.
**Rationale**: `build_graph` already dedupes the untyped pass on `(source, target)`; `edges()`
returns deterministic `ORDER BY source, target, relation_type` (NULL first). Because the
candidate set is filtered to `relation_type is None`, already-typed edges are inherently
excluded and never re-surfaced — this directly satisfies the spec's "Already-typed edges are
excluded" scenario with no extra logic. Batching/bounding deferred (read-only).

### D4 — Prompt + fail-closed output contract (mirrors `adjudication.py`) (unchanged)
Per edge, guarded read-only re-read of source AND target docs (skip on `OSError`/parse fail).
2-message prompt: system names the OPEN vocabulary + demands JSON-only; user carries
`[source_id — title] body` + `[target_id — title] body`. Model emits
`{"suggested_type", "rationale"}`. Parse reuses the 3-step `_extract_json_object`
(raw → fenced → first `{...}`). Validate `suggested_type` through
`model.relations.validate_relation_type` (the ONE gate). Empty/whitespace/non-string or
unparseable → degrade to `EdgeSuggestion(suggested_type=None, rationale=_MALFORMED_...)`,
NEVER crash, NEVER dropped — one result per input edge in order. `OllamaError`-family
propagates unswallowed to the verb's 3-tier handler. Unknown-but-nonempty accepted (open
vocab) with a stderr advisory. No Pydantic, no retry.

### D5 — Display contract (typed-pair annotation DROPPED) — CORRECTED
Per suggestion: `[type] source -> target` then `  rationale: …`; degraded items render
`[?] source -> target` + `  note: no valid type suggested`. The verb lists each untyped edge
as `(source, suggested_type, target, rationale)` and NOTHING ELSE — v1's
`note: pair already typed as '<t>'` annotation is REMOVED to keep design and spec identical
(spec is untyped-only, no re-surfacing of typed pairs). That annotation is recorded as a
deferred future nicety, out of scope here. Closing line: `openkos relate <source> <type> <target>`.

### D6 — Layering: BOTH guards stay green and UNMODIFIED — CORRECTED
- Canonical (`model`/`bundle`/`state`) imports NO graph — guarded by
  `test_base.py::test_canonical_layer_does_not_import_graph` (green, untouched; the new module
  is derived, not canonical).
- `test_analysis.py::test_cli_main_never_imports_graph_and_registers_no_graph_command`
  (docstring: "spec: No CLI Surface") stays GREEN and **UNMODIFIED**. The CLI imports ONLY
  `openkos.resolution.edge_typing`; the `build_graph` read is encapsulated inside that derived
  module. derived→derived (`resolution → graph`) is allowed; ONLY canonical→graph and cli→graph
  are forbidden. v1's "mandated test edit" / "relaxed guard" risk is **VOID and RETRACTED** —
  there is NO MODIFIED capability, NO spec change, NO guard relaxation.

### D7 — No ADR (confirmed)
Read-only, additive, zero schema/interface change, reuses adjudicate/relate + hand-rolled-LLM.
Fails the ADR gate — confirmed, no ADR required.

## Data Flow

    suggest-relations (verb) ──▶ resolution.edge_typing.suggest_relations(bundle_dir, llm)
         (imports resolution ONLY,          │ build_graph(bundle_dir)  [encapsulated]
          NEVER openkos.graph)              │ untyped_edges = relation_type is None
                                            │ per edge: load src+tgt docs, prompt llm
                                            ▼
    display (verb) ◀── [EdgeSuggestion(edge, type|None, rationale)] ──┘
         │ print "[type] s -> t / rationale"; then "run: openkos relate …"
         ▼  (human) ──▶ existing `relate` write path (unchanged)

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/resolution/edge_typing.py` | Create | `EdgeSuggestion`, `untyped_edges`, LLM leaf `suggest_edge_types`, orchestrator `suggest_relations` (owns internal `build_graph` read), `_load_doc`, `_build_messages`, reused `_extract_json_object`/`_parse_reply` |
| `src/openkos/cli/main.py` | Modify | New `@app.command()` `suggest_relations`; import ONLY `from openkos.resolution.edge_typing import suggest_relations, EdgeSuggestion` — NEVER `openkos.graph` |
| `src/openkos/resolution/__init__.py` | Modify (minor) | Update package docstring's "does not import `openkos.graph` this slice" note; edge_typing now reads graph (derived→derived, still no canonical import) |
| `tests/unit/resolution/test_edge_typing.py` | Create | untyped filter, prompt shape, fail-closed parse, per-item degrade, one-per-edge order |
| `tests/unit/cli/test_suggest_relations.py` | Create | verb wiring, gates, 3-tier Ollama, display (no typed-pair note), zero-writes |
| `tests/unit/graph/test_analysis.py` | UNCHANGED | No-CLI-Surface AST guard stays green and unmodified — explicitly NOT edited |

## Interfaces / Contracts

```python
@dataclass(frozen=True)
class EdgeSuggestion:
    edge: Edge                     # untyped source Edge (relation_type is None)
    suggested_type: str | None     # None ⇒ fail-closed degrade
    rationale: str                 # never blank on degrade paths

def untyped_edges(store: GraphStore) -> list[Edge]: ...
def suggest_edge_types(edges: list[Edge], *, bundle_dir: Path, llm: LLMBackend) -> list[EdgeSuggestion]: ...
def suggest_relations(bundle_dir: Path, *, llm: LLMBackend) -> list[EdgeSuggestion]:
    ...  # opens build_graph, filters untyped, delegates to suggest_edge_types
```

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `untyped_edges` filters NULL only; `suggest_edge_types` one-per-edge/order; malformed/empty/non-string degrade; unknown accepted | RED-first, fake `LLMBackend` |
| Unit | prompt = 2 messages, JSON-only system, src+tgt bodies in user turn | direct assertion |
| Integration | verb: gates, 3-tier Ollama degrade, display incl. `[?]` (NO typed-pair note), ZERO writes | Typer `CliRunner`, temp workspace |
| Guard | canonical never imports graph (unchanged); cli-main never imports graph + no `graph` command (UNCHANGED, green) | existing AST scans — no edits |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. Fail-closed cases (malformed reply degrade, unreadable doc skip,
Ollama unavailable) are retained as RED test requirements.

## Migration / Rollout

No migration. Additive, read-only. Rollback = revert the PR; removal leaves slice-1/2a
byte-identical. Well under the 400-line budget — single PR, no chaining.

## Open Questions

- None blocking. No test edits or spec changes are required by this design.
