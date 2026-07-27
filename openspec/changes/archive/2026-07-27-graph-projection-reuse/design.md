# Design: Graph Projection Reuse Within One CLI Invocation

Issues: #197 (PR1), #196 + #195 (PR2).

## 1. Verified locations (current tree, re-checked at design time)

Every reference below was re-read from the working tree. `sdd-apply` MUST re-verify
before editing; anchors are given as symbol names, not only line numbers.

| Symbol | File | Line (verified) |
|---|---|---|
| `graph_edge_summary(bundle_dir)` def | `src/openkos/graph/summary.py` | 46 |
| — its `with build_graph(bundle_dir) as store:` | same | 55 |
| `GraphStore` Protocol / `Edge` | `src/openkos/graph/base.py` | 39 / 21 |
| `build_graph(bundle_dir, *, candidates=None)` | `src/openkos/graph/sqlite_graph.py` | 416 |
| `SqliteGraphStore.__enter__/__exit__/close` | same | 272 / 276 / 268 |
| `candidate_edges(...)` def | `src/openkos/resolution/edge_typing.py` | 282 |
| — its `with build_graph(..., candidates=candidates) as store:` | same | 319 |
| — stale layering docstring (module) | same | 25-28 |
| — stale "so `cli/main.py` never imports `openkos.graph`" | same | 298-299 |
| `find_contradictions(...)` def | `src/openkos/resolution/contradiction.py` | 381 |
| — its `with build_graph(..., candidates=candidates) as store:` | same | 443-445 |
| — stale layering docstring (module) | same | 25-28 |
| `status()` def / docstring "scans … ONCE" | `src/openkos/cli/main.py` | 4304 / 4313-4314 |
| — `survey_bundle` walk | same | 4337 |
| — `lint_check.collect_docs` walk (3rd walk) | same | 4369 |
| — `graph_edge_summary(layout.bundle_dir)` | same | 4396 |
| `_zero_edge_state_message(...)` def | same | 4840 |
| — its `graph_edge_summary(layout.bundle_dir)` | same | 4881 |
| `suggest_relations_cmd` def | same | 4894 |
| — stale "never `openkos.graph` directly" docstring | same | 4912-4915 |
| — `_open_proximity_or_degrade` / `candidate_edges` / `finally: source.close()` | same | 4977 / 4980-4984 / 4985-4987 |
| — zero branch `_zero_edge_state_message` | same | 4993-5005 |
| `contradictions` def | same | 5191 |
| — stale "never `openkos.graph` directly" docstring | same | 5217-5219 |
| — `find_contradictions` call / `finally: source.close()` | same | 5281-5287 / 5311-5315 |
| — zero branch `_zero_edge_state_message` | same | 5320-5329 |
| `from openkos.graph.summary import graph_edge_summary` | same | 35 |
| `nullcontext` already imported | same | 10 |
| `_touch_vectors_db` (identical twins) | `tests/unit/cli/test_suggest_relations.py` / `test_contradictions.py` | 74 / 73 |
| `_write_nonempty_vectors_db` | `tests/unit/cli/test_status.py` | 40 |
| `conftest.py` (`_offline_ollama_by_default`) | `tests/unit/cli/conftest.py` | 61-65 |

Call sites of the three helpers (14 total): `test_suggest_relations.py` 184, 222, 260,
554, 764, 824; `test_contradictions.py` 486, 514, 777, 880; `test_status.py` 391, 411,
427, 493.

Correction to explore.md: `contradictions`' zero-branch is at 5320-5329 and its
`find_contradictions` at 5281-5287 — both still accurate. No other drift found.

## 2. The keyword's exact contract

**Name: `store`.** Chosen over `graph_store` because (a) every existing
`with build_graph(...) as store:` site and every internal consumer
(`_candidate_edges(store)`, `_candidate_pairs(store, excluded)`,
`_pair_relation_types(store)`) already calls it `store`; (b) the annotation
`GraphStore` already carries the "graph" noun, so `graph_store: GraphStore` is
redundant; (c) each module has exactly one store concept, so there is nothing to
disambiguate from.

Signature added to all three functions, **keyword-only** (each already has a `*`
separator, so this is forced, and it keeps the positional contract frozen):

```python
def graph_edge_summary(
    bundle_dir: Path, *, store: GraphStore | None = None
) -> tuple[int, int]: ...

def candidate_edges(
    bundle_dir: Path,
    *,
    include_confidential: bool = False,
    candidates: CandidateSource | None = None,
    store: GraphStore | None = None,
) -> list[Edge]: ...

def find_contradictions(
    bundle_dir: Path,
    *,
    llm: LLMBackend,
    include_deprecated: bool = False,
    include_confidential: bool = False,
    candidates: CandidateSource | None = None,
    store: GraphStore | None = None,
) -> tuple[list[ContradictionVerdict], int]: ...
```

`store` goes **last** in each parameter list so existing keyword ordering in
diffs stays stable.

**Decision — three explicit branches, NOT a `nullcontext` helper.** `nullcontext`
is a live idiom in `cli/main.py` (`_open_graph_or_degrade`, line 5402ff), but the
readers are the wrong place for it:

| Option | Tradeoff | Decision |
|---|---|---|
| `with build_graph(...) if store is None else nullcontext(store) as s:` | Requires an `AbstractContextManager[GraphStore]` annotation and relies on `SqliteGraphStore` structurally satisfying a covariant `AbstractContextManager` Protocol — a real `mypy --strict` variance risk. Adds a `contextlib` import to three modules that have none. | Rejected |
| Shared helper (e.g. `graph._reuse_or_build`) | Creates a new cross-module utility for a 3-line branch; `graph/summary.py` would then be imported by `resolution/`, a new edge in the layer graph. | Rejected |
| Two-branch early return, repeated per function | 3 extra lines per function, zero new imports, zero variance question, and it makes the ownership rule structurally visible at each site. | **Chosen** |

**When `candidates` and `store` are both supplied**, `candidates` is silently
unused (the caller already consumed it building the store). Document this in the
docstring; do not raise. Rationale: raising would add an error path with no
caller, and the CLI always passes the same `candidates` it built the store with.

## 3. Store lifetime and ownership — designed out, not warned about

**Rule: the `with` statement may only ever wrap a store the function itself
constructed.** Enforced structurally by making the supplied-store branch return
before any `with` is reached:

```python
def graph_edge_summary(bundle_dir, *, store=None):
    if store is not None:
        return _summarize(store)          # caller owns it; no with, no close
    with build_graph(bundle_dir) as owned:  # only ever the local build
        return _summarize(owned)
```

`_summarize` is a new module-private pure function (`GraphStore -> tuple[int,int]`)
holding today's two lines from `summary.py:56-58`. The same shape applies to
`candidate_edges` (extract `_edges_from(store) -> list[Edge]`) and
`find_contradictions` (extract
`_pairs_and_types(store, excluded) -> tuple[list[Pair], int, dict[Pair, str]]`,
holding today's `contradiction.py:444-445`).

Because `store` is never bound by a `with`/`try-finally` inside the callee, there
is no code path in which a callee can close a caller's store. Reviewers can verify
this by grepping: `store.close()` MUST NOT appear in `summary.py`,
`edge_typing.py`, or `contradiction.py`.

**The proposal's Medium "reader called after the CLI `with` closes" risk is
eliminated** by requiring every reader call AND the zero-branch
`_zero_edge_state_message(...)` call to sit lexically inside the single
`with build_graph(...) as store:` block (see §4). The `return` in each zero branch
exits the `with` normally. `raise typer.Exit(...)` inside the block also closes the
store via `__exit__`. There is no path that escapes the block holding `store`.

## 4. CLI call-site restructuring

### The `candidates` trap, resolved

`suggest_relations_cmd` and `contradictions` build with `candidates=source`;
`status` builds with no candidates. **A store built with different `candidates`
is not interchangeable.** Resolution: each command owns exactly one build,
parameterized identically to what its own reader would have built internally.
There is no cross-command sharing (one invocation = one command).

- `suggest_relations_cmd` → `build_graph(layout.bundle_dir, candidates=source)`
  (matches `candidate_edges`' internal build at `edge_typing.py:319`).
- `contradictions` → `build_graph(layout.bundle_dir, candidates=source)`
  (matches `contradiction.py:443`).
- `status` → `build_graph(layout.bundle_dir)`, no candidates (matches
  `summary.py:55`, and `status` never opens a proximity source today).

### ⚠ The one genuine behavior delta this creates

Today `_zero_edge_state_message` calls `graph_edge_summary(bundle_dir)` — a
**candidates-free** build. After this change it receives the
**candidates-seeded** store. Proximity-derived rows are extra *untyped*
concept-to-concept edges, so `total` (and therefore `untyped = total - typed`)
can increase.

Blast radius, precisely: `typed` is unaffected (proximity rows are always
`relation_type=None`), so `contradictions` (`use_typed_count=True`) is
byte-identical. Only `suggest-relations`' `all_excluded` message
(`"{count} relation(s) exist; {untyped} untyped, …"`) can print different
numbers, and only when ALL of: zero candidates survived, `vectors.db` is
non-empty, and at least one proximity row exists but was dropped by the
confidentiality filter.

**Decision: accept the delta.** The `all_excluded` branch exists to explain "the
graph has untyped rows but none survived *the caller's* filtering" — that claim
is only coherent when the summary is computed over the same projection the
filtering ran on. Today's candidates-free summary is a latent inconsistency; this
change fixes it. Rejected alternative: keep a second candidates-free
`build_graph` just for the message — that reintroduces the exact double build
#196 exists to remove.

Requires a dedicated RED test pinning the new number and an explicit note on
issue #196 when closing. Does **not** require a spec amendment: no
`candidate-edge-seeding` scenario pins these counts.

### Shape — `suggest_relations_cmd` (replaces 4977-5006)

Close the proximity source as early as possible: `build_graph` consumes
`candidates` eagerly inside `_populate_graph_tables` (verified
`sqlite_graph.py:402-411`, called at 433), so the source is dead the instant
`build_graph` returns. This is strictly better than today's lifetime.

```python
source = _open_proximity_or_degrade(layout.vectors_db_path)
embeddings_missing = source is None
try:
    graph = build_graph(layout.bundle_dir, candidates=source)
finally:
    if source is not None:
        source.close()

with graph as store:
    edges = candidate_edges(
        layout.bundle_dir,
        include_confidential=include_confidential,
        store=store,
    )
    typer.echo(f"openkos suggest-relations: workspace at {root}")
    typer.echo()
    total = len(edges)
    if total == 0:
        typer.echo(
            _zero_edge_state_message(
                layout,
                store=store,
                use_typed_count=False,
                embeddings_missing=embeddings_missing,
                none_survived="...",       # unchanged
                all_excluded="...",        # unchanged
            )
        )
        return
```

Everything from `if not auto:` onward moves **outside** the `with` block (the
store is not needed once `edges` is materialized), keeping the LLM run and its
minutes-long progress loop out of the store's lifetime.

### Shape — `contradictions` (replaces 5278-5330)

Same source-then-build prologue. The `try/except OllamaUnavailable /
OllamaModelNotFound / OllamaError` ladder (5288-5310) stays exactly as-is,
nested **inside** `with graph as store:`; its `finally: source.close()` clause is
deleted (the source is already closed by the prologue). `find_contradictions`
gains `store=store`; `candidates=source` is dropped from that call.

Unlike `suggest-relations`, the LLM loop runs *inside* `find_contradictions`, so
the store stays open across it. Accepted: it is an in-memory SQLite connection
holding no file lock, and when `verdicts` is empty (the only branch needing the
store afterwards) zero `llm.chat` calls were made. Splitting the block would
require two builds — rejected.

The zero branch calls `_zero_edge_state_message(layout, store=store, …)` inside
the `with`, then `return`.

### Shape — `status` (replaces 4395-4400)

```python
if not vectors_missing:
    with build_graph(layout.bundle_dir) as store:
        total, typed = graph_edge_summary(layout.bundle_dir, store=store)
    ...unchanged echo branches...
```

`status` gains no behavior change and, honestly, no measurable speedup (§195
caveat in the proposal). Docstring correction at 4312-4314: replace
`"sequences three reads … scans bundle/**/*.md ONCE"` with wording that names
the three independent walks:

> On a workspace, sequences three reads and renders their result as plain text
> via `typer.echo`. Note that these reads perform THREE independent
> `bundle/**/*.md` walks, not one: `okf.survey_bundle` (source/concept counts and
> §9 findings), `lint_check.collect_docs` (dangling-reference findings, #141),
> and — only when `vectors.db` is non-empty — `build_graph`'s walk behind the
> informational edge-count line. Consolidating them is deliberately out of scope
> (issue #195); what IS guaranteed is that `status` calls `build_graph` exactly
> once. `survey_bundle`'s counts always reflect its disk scan, never `index.md`
> alone, so catalog drift after an interrupted `ingest` is still visible.

`cli/main.py` gains one import: `from openkos.graph.sqlite_graph import
build_graph` — grouped with the existing `graph.summary` import at line 35.
Legal per the live constraint (`test_analysis.py::test_cli_main_registers_no_graph_command`
comment); `reindex` (2249) and `query` (5458) already import from
`graph.sqlite_graph`.

## 5. `_zero_edge_state_message` signature

```python
def _zero_edge_state_message(
    layout: config.WorkspaceLayout,
    *,
    store: GraphStore,          # REQUIRED, not Optional
    use_typed_count: bool,
    none_survived: str,
    embeddings_missing: bool,
    all_excluded: str | None = None,
) -> str:
```

Line 4881 becomes `total, typed = graph_edge_summary(layout.bundle_dir, store=store)`.

`store` is **required**, not `GraphStore | None = None`. Rationale: this is a
module-private helper with exactly two call sites, both of which now have a store
in scope. An optional parameter would leave a live fallback path that silently
rebuilds — i.e. the #196 regression would be one forgotten keyword away and would
never fail a test. Requiring it makes the regression a `TypeError` at import-time
test collection. `layout` is retained (unused by the graph read now, but still
required as the state-3 early return's context and to keep the call sites'
readability). Note `layout.bundle_dir` is still passed to `graph_edge_summary`
even though it is unused on the supplied-store path — that keeps the reader's
signature honest and its `store=None` default reachable.

`cli/main.py` gains `from openkos.graph.base import GraphStore` for the
annotation.

## 6. Typing

- `GraphStore` is a `@runtime_checkable` `typing.Protocol`
  (`graph/base.py:38-39`) with three zero-arg-ish methods. Using it as a
  parameter annotation is ordinary structural typing; `mypy --strict` accepts it
  with no `Any` leakage and no `disallow_any_explicit` conflict.
- `SqliteGraphStore` satisfies it structurally (already asserted elsewhere in the
  tree), so `build_graph(...)`'s return binds to `store: GraphStore` without a
  cast.
- **No new import-layer problem.** `resolution/edge_typing.py:36` already has
  `from openkos.graph.base import Edge, GraphStore`, and
  `resolution/contradiction.py:58` already has
  `from openkos.graph.base import GraphStore`. **Zero import changes needed in
  `resolution/`.** `graph/summary.py:15` already imports from `graph.base`; it
  adds `GraphStore` to that line. The only genuinely new import is
  `cli/main.py`'s `build_graph` + `GraphStore`.
- `warn_unreachable = true` and `redundant-expr` are enabled: the
  `if store is not None: return …` branch is reachable under both defaults, so
  neither fires.
- No `# type: ignore` is expected anywhere in this change. If one appears,
  the design is being violated.

## 7. #197 — shared `vectors.db` fixture

**Decision: a pytest *factory* fixture in `tests/unit/cli/conftest.py`, not a
plain helper module.**

The three current helpers are called as `helper(tmp_path)`. A plain
`@pytest.fixture def seeded_vectors_db(tmp_path)` would invert that to a
zero-arg dependency and force every call site to be deleted rather than kept —
and would fire for tests that only *sometimes* want it. A factory fixture keeps
the call line byte-identical:

```python
# tests/unit/cli/conftest.py
@pytest.fixture
def seed_vectors_db() -> Callable[[Path], None]:
    """Return a callable that writes a `.openkos/vectors.db` holding one
    `vector_meta` row, so the bundle counts as embeddings PRESENT.

    Issue #183's state 3 keys on "absent OR empty", not merely absent -- a
    zero-byte file must NOT read as present. `init` never creates this file, so
    embeddings-missing is the default for a bare `_init_workspace` call unless a
    test opts out via this fixture.

    A factory rather than a plain fixture: seeding is opt-in per test (most CLI
    tests want the embeddings-missing default), and the callable form keeps the
    existing `seed_vectors_db(tmp_path)` call shape from the three module-level
    helpers it replaces (#197).
    """
    def _seed(workspace_root: Path) -> None:
        openkos_dir = workspace_root / ".openkos"
        openkos_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(openkos_dir / "vectors.db"))
        try:
            conn.execute(
                "CREATE TABLE vector_meta (concept_id TEXT PRIMARY KEY, "
                "content_hash TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO vector_meta (concept_id, content_hash) "
                "VALUES ('stub', 'hash')"
            )
            conn.commit()
        finally:
            conn.close()
    return _seed
```

Adds `import sqlite3`, `from pathlib import Path`,
`from collections.abc import Callable` to `conftest.py` (it already imports
`Sequence` from `collections.abc`). Note the body also gains a `try/finally`
around the connection — all three originals leak the connection on a mid-body
raise.

**Call-site shape at all three modules** — the call line is unchanged apart from
the name; only the test signature gains a parameter:

```python
def test_status_state1_no_edges_reports_distinct_message_from_state3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
```

Applied at all 14 sites; the three helper defs
(`test_suggest_relations.py:74`, `test_contradictions.py:73`,
`test_status.py:40`) are deleted, along with any `import sqlite3` that becomes
unused (verify per-module — `test_status.py` imports it at line 11 and may use it
elsewhere; Ruff F401 will catch it either way).

## 8. Test strategy under Strict TDD

The crux is asserting "exactly one `build_graph` per invocation" **without** a
brittle mock-counting exercise. Three complementary layers:

### 8a. Behavior-first RED tests (resolution + graph layers) — write these first

Pure contract tests, no counting:

| Test | Assertion |
|---|---|
| `test_graph_edge_summary_with_supplied_store_matches_own_build` | `graph_edge_summary(bundle, store=s) == graph_edge_summary(bundle)` for a store built from the same bundle |
| `test_graph_edge_summary_does_not_close_supplied_store` | after the call, `store.edges()` still works (a closed `sqlite3` conn raises `ProgrammingError`) — **this is the ownership test, and it needs no mock at all** |
| same pair for `candidate_edges` and `find_contradictions` | identical shape; `find_contradictions` uses the existing fake `LLMBackend` |
| `test_candidate_edges_ignores_bundle_walk_when_store_supplied` | supply a store built from bundle A while passing bundle B's path; the returned edges come from A |

The "does not close" test is the strongest guard in the suite: it fails loudly
and unambiguously if a callee ever adds a `with` around the supplied store, and
it is immune to refactoring.

### 8b. The one-build regression tests (CLI layer)

Counting is unavoidable here — "how many times was the projection built" is
literally the requirement. Make it robust by **counting through a real
pass-through wrapper, not a `MagicMock`**, patched at the single seam
`openkos.cli.main.build_graph`:

```python
def test_suggest_relations_builds_the_graph_once_on_the_zero_path(
    tmp_path, monkeypatch, seed_vectors_db
) -> None:
    """#196: the zero-candidate path must not rebuild the projection for its
    "nothing to suggest" message."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    calls: list[Path] = []
    real = sqlite_graph.build_graph

    def _counting_build_graph(bundle_dir: Path, **kwargs: object) -> object:
        calls.append(bundle_dir)
        return real(bundle_dir, **kwargs)   # real store, real behavior

    monkeypatch.setattr("openkos.cli.main.build_graph", _counting_build_graph)
    monkeypatch.setattr(
        "openkos.graph.summary.build_graph", _counting_build_graph
    )
    monkeypatch.setattr(
        "openkos.resolution.edge_typing.build_graph", _counting_build_graph
    )

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "No concept relationships in the graph yet." in result.stdout
```

Why this is not brittle mock-counting:

1. **The wrapper delegates to the real `build_graph`**, so the command's actual
   output is still asserted in the same test. If the plumbing is wrong, the
   output assertion fails too — the count is a *second* signal, never the only
   one.
2. **All three module-level seams are patched**, so the test cannot be satisfied
   by moving a build from one module to another. It measures the invocation-wide
   total, which is exactly the requirement.
3. **It asserts a number the spec cares about (1), not a call-argument shape.**
   Adding an unrelated keyword to `build_graph` later does not break it
   (`**kwargs` pass-through).
4. `calls` records `bundle_dir`, so the assertion message names *which* build
   fired on failure.

Four such tests: `suggest-relations` zero path (#196), `suggest-relations`
non-zero path (guards against the split-block refactor regressing), 
`contradictions` zero path (#196), `status` (#195). Each also asserts the
existing output line, so none is a pure counting test.

### 8c. The behavior-delta test (§4)

`test_suggest_relations_all_excluded_message_counts_proximity_rows` — a bundle
with a confidential concept reachable only via a proximity row, seeded
`vectors.db`, asserting the `all_excluded` wording with the new `untyped` count.
This is the RED test that documents the accepted delta; it must be written
deliberately, not discovered as a failure.

### 8d. Coverage

The new `if store is not None: return …` branches are branch-covered by 8a
(supplied) + every existing call site (default `None`). No `# pragma: no cover`
should be needed. Gate: 90% branch, `uv run pytest --cov`.

## 9. Stale docstring corrections — exact wording

There are **four** stale sites, not two (explore.md found two; two more are in
`cli/main.py` and become flatly false the moment `main.py` imports `build_graph`).

**(a) `resolution/edge_typing.py:25-28`** — replace:

> Layering: this module is DERIVED, not canonical -- it MAY import
> `openkos.graph` (derived -> derived, allowed). The live, tested constraint is
> narrower than an earlier version of this docstring claimed: the canonical
> layer (`openkos.model`, `openkos.bundle`, `openkos.state`) MUST NOT import
> `openkos.graph` (`tests/unit/graph/test_base.py::test_canonical_layer_does_not_import_graph`),
> and `graph/` MUST NOT register a CLI verb
> (`tests/unit/graph/test_analysis.py::test_cli_main_registers_no_graph_command`).
> `cli/main.py` importing `openkos.graph` is NOT a violation and is established
> practice (`query`, `reindex`, and — since graph-projection-reuse — the shared
> per-invocation `build_graph` this module's optional `store` parameter accepts).

**(b) `resolution/edge_typing.py:298-299`** — replace
`"Encapsulates the `openkos.graph` read so `cli/main.py` never imports `openkos.graph` directly (design D2/D6)."`
with:

> Owns the `openkos.graph` read logic (the `_candidate_edges` narrowing and the
> confidentiality filter live here, not in any caller). Lifecycle ownership is
> optional: pass an already-open `store` and this function reuses it without
> closing it, letting one CLI invocation build the projection once
> (graph-projection-reuse); omit it and the function opens and closes its own
> `build_graph`, byte-identically to before.

**(c) `resolution/contradiction.py:25-28`** — same replacement as (a), with
`find_contradictions` substituted for `candidate_edges` in the final clause.

**(d) `cli/main.py:4912-4915` and `cli/main.py:5217-5219`** — both claim
`"this module imports ONLY from openkos.resolution.…, never openkos.graph directly"`.
Replace with:

> …which owns the candidate-narrowing logic. This command builds the graph
> projection ONCE per invocation via `graph.sqlite_graph.build_graph` and threads
> the open store into every reader it calls, including the zero-result
> `_zero_edge_state_message` path that used to trigger a second full build
> (#196). Holding an open `openkos.graph` store here is established practice
> (`query`, `reindex`); the live layering rule forbids only canonical-layer
> imports of `openkos.graph` and a `graph` CLI verb.

## 10. Slice boundaries and line estimates

| Slice | Scope | Files | Est. changed lines (add+del) |
|---|---|---|---|
| **PR1 — #197** | Factory fixture in `tests/unit/cli/conftest.py`; delete 3 helpers; update 14 call sites + their signatures | `tests/unit/cli/conftest.py` (+~40), `test_suggest_relations.py` (~±35), `test_contradictions.py` (~±25), `test_status.py` (~±30) | **~130** |
| **PR2 — #196 + #195** | `store` keyword ×3 + helper extractions; `_zero_edge_state_message` signature; 3 CLI call-site restructures; 4 docstring corrections; new RED tests | `graph/summary.py` (+~18), `resolution/edge_typing.py` (+~25), `resolution/contradiction.py` (+~28), `cli/main.py` (~±90), 3 CLI test modules (+~110), `tests/unit/resolution/*` + `tests/unit/graph/test_summary.py` (+~70) | **~340** |

Both slices are under the 400-line review budget. PR2 targets PR1's branch
(Feature Branch Chain). `Decision needed before apply: No`.
`Chained PRs recommended: Yes`. `400-line budget risk: Medium` — PR2 at ~340 has
little headroom; if the RED-test suite grows past estimate, split the docstring
corrections (§9) into a trivial PR3.

## Architecture decisions (summary)

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Keyword name | `store` | `graph_store` | Matches every existing local/parameter name; type already says "graph" |
| Keyword kind | keyword-only, default `None`, last | positional | All three functions already have `*`; keeps ~30+ call sites untouched |
| Reuse mechanism | two-branch early return | `nullcontext`, shared helper | No `AbstractContextManager` variance risk under `--strict`; no new imports in `resolution/`; ownership visible at each site |
| Ownership guarantee | supplied store never touched by a `with` | `try/finally` with an `owned` flag | A flag is a runtime invariant; the early return is a structural one |
| `_zero_edge_state_message.store` | required | `GraphStore \| None = None` | An optional store leaves a silent rebuild fallback one forgotten keyword away |
| Zero-branch summary source | the candidates-seeded store | a second candidates-free build | The second build IS #196; the delta is narrow and arguably a bug fix (§4) |
| #197 fixture form | factory fixture returning `Callable[[Path], None]` | plain autouse fixture; shared helper module | Preserves the `f(tmp_path)` call shape; keeps seeding opt-in |
| One-build assertion | pass-through counting wrapper on all 3 seams + output assertion | `MagicMock` call counting | Real store, real output, invocation-wide total; not shape-coupled |

**ADR: not warranted.** Per the project convention (`docs/adr/NNNN-*.md`,
"significant / hard-to-reverse decisions ONLY"), this change is additive,
default-preserving, has no external consumers of the new keyword, and reverts
cleanly per the proposal's rollback plan. No `docs/adr/` file, no
`docs/adr/README.md` entry.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. All three commands remain
read-only over an in-memory SQLite projection; no new disk, network, or process
surface. The `derived-index-cache` on-disk `graph.db` is explicitly untouched
(`open_graph_store_readonly` remains used only by `query` at `cli/main.py:5458`)
— `sdd-verify` MUST confirm this.

## Migration / Rollout

No migration. No feature flag. No data, schema, or on-disk artifact touched.

## Open Questions

- [ ] §4's accepted behavior delta in `suggest-relations`' `all_excluded`
      message: confirm with the maintainer before PR2 merges, and note it on
      issue #196 at close. Design default: accept, with a dedicated RED test.
- [ ] #195 closes with plumbing + docstring only, no measurable speedup
      (already stated in the proposal). Confirm the issue is closed with that
      comment rather than re-scoped.
