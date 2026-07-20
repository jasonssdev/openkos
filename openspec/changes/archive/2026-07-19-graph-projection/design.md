# Design: Graph Projection (MVP-2 slice 1)

## Technical Approach

Adopt proposal Approach #3. Add a derived-layer `src/openkos/graph/` package that
projects the bundle into an **in-memory SQLite node-edge store**, mirroring
`state/fts.py`'s rebuild-per-run, context-managed lifecycle. Nodes = OKF concept
ids; edges = bundle-relative markdown links extracted from doc bodies by a scoped
regex (consistent with `okf.py`'s regex conventions). `relation_type` is a
present-but-NULL reserved column. A thin `analysis.py` converts the store to an
`nx.DiGraph`. No CLI verb, no persistence, no writes to bundle bytes.

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Store | in-memory SQLite `:memory:`, rebuild-per-run | file DB / `state/db.py` | Mirrors `fts.py` precedent; plain `CREATE TABLE` is byte-identical for `:memory:`→file, so the later flip needs **zero schema migration** |
| Edge parse | scoped regex over `/…​.md` links | `markdown-it-py` | Slice only needs `[t](/p.md)` links; avoids pulling an unused dep; matches `okf.py`'s `re` approach |
| Graph lib | NetworkX (BSD, tech-stack default) | hand-rolled | Thin, deterministic conversion; used only in `analysis.py` |
| `relation_type` | nullable column, always NULL | commit vocabulary now | Reserves the attach path (`knowledge-object-model.md:211-222`) without deciding frontmatter-vs-prose typing — later slice |
| `base.py` scope | `nodes()`/`edges()`/`neighbors()` only | path queries in Protocol | YAGNI; path finding is nx's job via `analysis.py`, keeping `base.py` a stdlib-only leaf (no nx import) |

**ADR gate — does NOT fire.** Per `openspec/config.yaml` `rules.design`, an ADR
needs BOTH a tech/pattern/interface decision AND hard-to-reverse. Every decision
here is cheaply reversible: the in-memory mirror copies an established precedent
(`fts.py`); NetworkX reverts in one `pyproject.toml` line (rollback plan);
`GraphStore` is a small structural Protocol reshapeable while this slice is its
only consumer; `relation_type` is deliberately reserved with no vocabulary commit.
"When in doubt, do not create one." No ADR stub authored.

## Data Flow

    bundle/ ──okf._iter_docs──> DocScan (read-only)
        │                           │
        │           re-read body (load_frontmatter, TOCTOU-guarded)
        │                           │
        │                   _LINK_RE scan ──> edge targets
        ▼                           ▼
    nodes(concept_id)         edges(source,target,NULL)
        └──────── SQLite :memory: ────────┘
                        │
              analysis.to_digraph ──> nx.DiGraph

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/graph/__init__.py` | Create | Package marker |
| `src/openkos/graph/base.py` | Create | `GraphStore` Protocol + `Edge` dataclass; stdlib-only leaf (llm/base.py style) |
| `src/openkos/graph/sqlite_graph.py` | Create | `SqliteGraphStore` + `build_graph(bundle_dir)` factory + `_LINK_RE`; imports `okf` read-only |
| `src/openkos/graph/analysis.py` | Create | `to_digraph(store) -> nx.DiGraph`; imports networkx + `graph.base` only |
| `pyproject.toml` | Modify | Add `networkx>=3.4` to `[project].dependencies`; add `types-networkx` to dev group (mypy strict) |

## Interfaces / Contracts

```python
# graph/base.py — leaf, stdlib typing only
@dataclass(frozen=True)
class Edge:
    source_id: str
    target_id: str
    relation_type: str | None  # RESERVED — always None this slice

class GraphStore(Protocol):
    def nodes(self) -> list[str]: ...          # concept ids, sorted
    def edges(self) -> list[Edge]: ...         # sorted, deterministic
    def neighbors(self, concept_id: str) -> list[str]: ...  # out-edge targets
```

**SQLite DDL** (module constants, `fts.py` style):

```sql
CREATE TABLE nodes (concept_id TEXT PRIMARY KEY);
CREATE TABLE edges (
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    relation_type TEXT               -- reserved, NULL this slice
);
CREATE INDEX edges_source ON edges (source_id);
CREATE INDEX edges_target ON edges (target_id);
```

`_LINK_RE = re.compile(r"\[[^\]]*\]\(/([^)\s#]+\.md)(?:#[^)]*)?\)")` — bundle-relative
`/…​.md` links only (external/relative/non-`.md` ignored; anchors stripped). Target
concept id = captured path minus `.md`. Edges deduped per `(source,target)` before
insert, then filtered to targets that resolve to a known node id in the same
projection **before** insert; a target with no matching node produces **no
edge** (dropped silently — building does not raise). `build_graph` mirrors `build_index`: `:memory:` connect,
one `_iter_docs` pass, TOCTOU-guarded body re-read, `skipped` notes, connection
closed on any build exception; `SqliteGraphStore` is a context manager owning the
conn. `to_digraph` adds all nodes first (isolated nodes survive), then edges with
a `relation_type` attribute.

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | regex capture/reject, anchor strip, dedup | fixtures of body strings |
| Unit | `build_graph` nodes/edges, `relation_type` NULL, provenance backlinks become edges | tmp bundle |
| Unit | store `nodes/edges/neighbors` ordering + ctx-manager close | in-memory store |
| Unit | TOCTOU skip (doc vanishes between passes) | mirror `fts` test |
| Unit | no-write invariant: bundle bytes hash unchanged | before/after hash |
| Unit | `to_digraph` counts, isolated nodes, determinism | assert on nx graph |

Layering enforced manually this slice: each graph module carries a docstring
boundary note; `model`/`bundle`/`state` MUST NOT import `graph`. import-linter is
unwired (`docs/architecture.md:112`) — reviewer verifies.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. SQLite `:memory:` + regex over
local files, read-only.

## Migration / Rollout

No migration. Rollback = delete `src/openkos/graph/` + revert the `networkx` line;
no canonical files, bundle bytes, or persisted derived state touched.

## Open Questions

- [x] Relation-typing populate path (frontmatter key vs. prose) — RESERVED, later slice (deferred to MVP-2 non-goals; not resolved by this change).
- [x] Dangling/orphan-link detection — RESERVED, out of scope this slice. Links
  that resolve to no known node produce no edge here; surfacing them is a
  separate lint concern for a later slice, not a driver of edge insertion.
