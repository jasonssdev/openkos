# Design: Query Application Service

## Technical Approach

Introduce `src/openkos/application/` as the layer for bounded-context services,
and land `application/query.py` as its first and only member. The service owns
the query context's **composition and application rules**; `cli/main.py`'s
`query` command becomes a thin adapter that builds collaborators, renders, and
gates.

The service stays **synchronous** (`AGENTS.md`: the core is synchronous; async
only at the MVP 3 edge, which calls in through a thread pool). No `async def`.

## Architecture Decisions

### D1 — The test-injection seam: inject collaborators, migrate `answer`

The three patched names are **not symmetric**, so a single strategy for all
three is wrong. Measured against the working tree:

| Patched name | Sites | In the query suite | Elsewhere | Can it move? |
| --- | --- | --- | --- | --- |
| `openkos.cli.main.OllamaClient` | 118 | 5 | 113 (doctor 48, init 28, ingest 10, contradictions 10, reindex 3, adjudicate 3, candidate-edges 3, conftest 2, +6 singles) | **No** |
| `openkos.cli.main.stale_derived_stores` | 1 | 1 | 0, but it is read by `_stale_index_names` (`main.py:4139`), which `status` and `next` also call | **No** |
| `openkos.cli.main.answer` | 123 | 116 | 7 (`test_write_time_refresh.py` 4, `test_embed_host_advisory.py` 2, `test_adjudicate.py` 1) | **Yes** — one call site (`main.py:16984`) |

**Choice: hybrid.**

- **`llm: LLMBackend` and `embedder: Embedder` are constructor parameters of the
  service call**, built by the CLI exactly as today (`_chat_client(cfg)`,
  `OllamaClient(model=cfg.embedding_model)`). This is *not* a test-shaped API:
  `retrieval.answer.answer()` already takes both by parameter, and `AGENTS.md`
  requires extension interfaces be `typing.Protocol` (`LLMBackend`). Building
  `OllamaClient` inside the service would bind an application service to a
  concrete backend. Preserving 118 patch sites is a consequence, not the reason.
- **Staleness is not injected at all.** The CLI keeps
  `_stale_index_names(layout, reads=("fts",))` and its warning where they are
  (`main.py:16971–16977`), *before* the service call. `reads=("fts",)` is an
  adapter-declared fact (#436), the warning is presentation, and keeping it in
  place makes stderr ordering byte-identical by construction.
- **`answer` is imported and called by the service**, and its 123 patch targets
  migrate to `openkos.application.query.answer`. It is the only one of the three
  that can move, and it *must*: a service whose central operation is a
  caller-supplied callable has no contract — every MVP 3 adapter would have to
  import `answer` from `retrieval` and pass it, reproducing the coupling this
  change exists to remove.

**Rejected — inject `answer` too.** Zero test churn, but it hollows out the
service contract as above. The tempting variant (`answer_fn: AnswerFn = answer`)
is worse than either option: a default argument binds at `def` time, so patching
the module attribute afterwards silently does nothing. The seam would *look*
preserved and would not be.

**Rejected — migrate everything.** Not available. `OllamaClient` and
`stale_derived_stores` stay in `main.py` on their own merits, so a full migration
would produce a split convention: some doubles patched at the CLI, some at the
service.

Migration is a pure target rename — the service imports `answer` at module level
and calls it unqualified, the same mechanism `main.py` uses today, so every test
keeps its structure. **Slice consequence: the migration is NOT its own slice.**
It cannot precede the move (the target module does not exist yet) and cannot
follow it (the tests would fail in between), so all 123 edits ride in Slice 1.

### D2 — Exceptions propagate; ordering stays in the adapter

The taxonomy already exists outside `cli/`: `OllamaUnavailable`,
`OllamaModelNotFound` and `OllamaEmbeddingDimensionMismatch` all subclass
`OllamaError` (`llm/ollama.py`), plus `FtsUnavailable` (`state/fts.py`). A new
service-level hierarchy would re-wrap it and erase the subclass relationship the
generic fourth arm depends on. Exit codes are also not a service concern — an
HTTP or MCP adapter maps to its own error shape.

So the service **propagates** and documents the constraint as a contract:
its `Raises:` block states that the three specific types MUST be handled before
the `(FtsUnavailable, OllamaError)` catch-all, because reordering silently
swallows them. `stage_filed_answer` keeps raising `ValueError` (caught at
`main.py:17282`).

### D3 — `--save` splits on the existing Phase A / Phase B line

The service **does not write**, so it never needs the shared write helpers —
no injection, no import from `openkos.cli`, no duplication. `_stage_filed_answer`
is already documented as pure and in-memory (`main.py:16597–16601`); the domain /
infrastructure boundary already falls exactly on the phase line.

| Symbol | Home |
| --- | --- |
| `_stage_filed_answer` → `stage_filed_answer` | Service (public) |
| `_FiledAnswerPlan` → `FiledAnswerPlan` | Service (public — the CLI renders its fields) |
| `_declarative_answer_title`, `_question_subject`, `_clause_answer_title`, `_DECLARATIVE_TITLE_MAX_CHARS`, `_DECLARATIVE_TITLE_MIN_CHARS` | Service (private) |
| `_SYNTHESIS_SHARE_WARN_THRESHOLD` + its comparison | Service, as `synthesis_share_warrants_warning(citations) -> bool`; the sentence stays in the CLI |
| `_open_vector_store_or_degrade`, `_open_fts_or_degrade` | Service (private — query-only coordination) |
| duplicate-scan orchestration (`main.py:17407–17426`) | Service, as `scan_for_duplicates(...)`; the CLI renders candidates and the `unavailable` notice |
| `_no_match_message` | **CLI** — pure presentation |
| `_snapshot_read`, `insert_index_entry`, `insert_log_entry`, `_reject_drifted_targets`, `fsio.write_*`, `_autocommit`, `_refresh_derived_after_write`, `_stale_index_names`, `_chat_client`, `_resolve_local_exemption`, `_warn_if_nonlocal_embed_host`, `observability.*` | **CLI** — shared write/catalog infrastructure and presentation (17 / 24 / 17 call sites for the three write helpers) |

### D4 — Consent gates stay in the adapter; the service supplies facts only

Both gates (`main.py:17454–17468` unattributed, `17469–17478` ordinary review)
stay in `cli/main.py`, verbatim. The seam's **shape**, and nothing more: the
service exposes `grounding_unverified(result) -> bool` (today's
`result.llm_invoked and result.attribution != "reported"`, `main.py:17351`) as
application policy, and **never** calls `typer.confirm`, `sys.stdin.isatty()`,
or `input()`. Consent-relevant disclosures leave the service as data. A future
headless-consent protocol lands as a port the adapter supplies. The protocol
itself is out of scope.

### D5 — `application/__init__.py` exports nothing

A docstring only, matching `retrieval/__init__.py`. Re-exporting would create a
second import path for every symbol and make `openkos.application` grow a
surface it does not own. Callers import `openkos.application.query`.

## Interfaces / Contracts

```python
# src/openkos/application/query.py  -- synchronous, no Typer, no openkos.cli import

@dataclass(frozen=True)
class QueryOutcome:
    result: AnswerResult              # answer, citations, fts_hit_count, llm_invoked,
                                      # no_match_cause, attribution, sufficiency_degraded,
                                      # excerpted_titles, omitted_titles, dense_degraded
    vector_store_unavailable: bool    # renders main.py:17076
    fts_unavailable: bool             # renders main.py:17076

def run_query(
    question: str, *, layout: config.WorkspaceLayout, cfg: config.Config,
    llm: LLMBackend, embedder: Embedder,
    limit: int, include_deprecated: bool, include_confidential: bool,
    local_exemption: bool,
) -> QueryOutcome: ...

def grounding_unverified(result: AnswerResult) -> bool: ...
def synthesis_share_warrants_warning(citations: Sequence[Citation]) -> bool: ...
def stage_filed_answer(...) -> FiledAnswerPlan: ...       # raises ValueError
def scan_for_duplicates(question, *, layout, cfg, embedder) -> DuplicateScan: ...
```

`QueryOutcome` carries every field the CLI reads, so all `typer.echo` stays in
the adapter.

## Data Flow

    CLI adapter                         application/query.py        domain packages
    ───────────────────────────────────────────────────────────────────────────────
    require_workspace / read_config
    _chat_client, OllamaClient ──llm, embedder──┐
    advisories, _stale_index_names              │
    stage_notice                                ▼
                                  run_query ──→ open stores (degrade)
                                            ──→ retrieval.answer.answer()
    render(QueryOutcome) ←──────────────────────┘
    [--save]              ──→ stage_filed_answer ──→ okf / config / bundle
    render plan preview   ←── FiledAnswerPlan
                          ──→ scan_for_duplicates ──→ resolution.insight_identity
    confirm gates  (adapter only)
    _snapshot_read / insert_*_entry / _reject_drifted_targets
    fsio.write_* → _autocommit → _refresh_derived_after_write

## Layering

**Invariant:** `openkos.application` may import `model`, `bundle`, `state`,
`retrieval`, `graph`, `resolution`, `llm`, `config`, `fsio`. Nothing in those
packages may import `openkos.application`, and `openkos.application` must never
import `openkos.cli`. Services sit *above* both the canonical and derived layers,
so composing them is legal; placing this code inside `retrieval/` would not be,
because it would put canonical writes into a documented rebuildable package.

`docs/architecture.md:112` states layering is a followed convention with **no
automated guard** — nothing in CI catches a violation. This design avoids the
violation by construction (`application/` cannot be imported by a lower layer
without an obvious cycle) rather than by relying on a check that does not exist.

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `src/openkos/application/__init__.py` | Create | Docstring only; no re-exports |
| `src/openkos/application/query.py` | Create | Query composition + application rules |
| `src/openkos/cli/main.py` | Modify | `query` becomes a thin adapter; ~700 lines removed |
| `tests/unit/application/test_query_service.py` | Create | Direct service unit tests |
| `tests/unit/cli/test_query.py`, `test_query_save.py`, `test_write_time_refresh.py`, `test_embed_host_advisory.py`, `test_adjudicate.py` | Modify | `answer` patch-target rename |
| `docs/adr/0018-application-layer-for-bounded-context-services.md` | Create | ADR, status Proposed |
| `docs/adr/README.md` | Modify | Index row |

## Testing Strategy

| Layer | What to test | Approach |
| --- | --- | --- |
| Unit (service) | store degrade paths, exception propagation, staging refusals, title cascade, synthesis-share and grounding predicates, duplicate-scan degrade | Direct calls, patching `openkos.application.query.answer`; no Typer. This is how the 90% branch gate is met cheaply on the degrade branches |
| Unit (adapter) | exit codes, handler ordering, stdout/stderr text, both confirm gates | Existing 161 `CliRunner` tests, unchanged except the patch-target rename |
| Regression | byte-identical stdout/stderr/exit codes | The existing suite is the contract; Strict TDD applies to every new service symbol |

**Correction to the exploration.** `test_query_save.py` is *not* purely
black-box: it imports `_stage_filed_answer` directly (line 20) and calls it at
23 sites. Slice 2 keeps this to **one changed line** with a test-local alias —
`from openkos.application.query import stage_filed_answer as _stage_filed_answer`
— rather than 23 call-site edits. Three files mention the moved helpers in prose
only (`tests/unit/state/test_derived.py:404`,
`tests/unit/bundle/test_cited_high_water_raises.py:7`,
`tests/unit/cli/test_slugify.py:203`); those references go stale and should be
repointed.

## Slice Boundaries

Budget: **2000 changed lines** (`additions + deletions`) per slice. Chained.

| # | Scope | main.py | service | CLI shim | tests | Total |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Read path: `application/` package, store degrade, `answer()` call, `QueryOutcome`, all non-`--save` rendering stays in CLI. **Carries all 123 `answer` patch-target edits (~246 changed lines).** | −280 | +330 | +60 | +496 | **~1,170** |
| 2 | `--save` filing: staging, title cascade, synthesis-share and grounding predicates, duplicate scan. Confirm gates, catalog inserts, drift check and writes stay in the CLI. | −415 | +430 | +50 | +301 | **~1,195** |

Both fit. Each slice leaves `openkos query` working end to end, so a landed
slice with no successor is safe. No third slice: the patch migration is absorbed
by Slice 1 (see D1).

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. `_autocommit` invokes git, but
it is unchanged by this design and stays in the adapter.

## Migration / Rollout

No data migration. Pure composition refactor: no change to the knowledge model,
the on-disk format, or the CLI surface. Reverting the merge restores `main.py`.

## Open Questions

- [ ] `AGENTS.md`'s Conventional Commit scope list has no entry for a top-level
      application layer; `cli` remains accurate for these slices, and adding a
      scope is a separate documentation change.
- [ ] Automated layering enforcement (import-linter) stays unwired, as
      `docs/architecture.md` already records. Out of scope here.
