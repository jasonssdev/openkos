# Exploration: query-application-service

Scope: issue #918, restricted to the **query** bounded context. Ingestion and
lifecycle services are explicitly out of scope and become their own changes.

## Current state — the query surface

`src/openkos/cli/main.py` is 18,847 lines across 18 `@app.command` verbs.

The `query` command's decorator starts at line 16742 and `def query(...)` at
16749; the body ends at 17574, where the next command (`reindex`) begins.
Command span: 833 lines, of which the docstring is roughly 100 (16808–16907)
and the body roughly 667 (16908–17574).

Query-exclusive private helpers sit directly above the command, each verified
as single-call-site:

| Helper | Lines |
| --- | --- |
| `_no_match_message` | 16218–16259 |
| `_open_vector_store_or_degrade` | 16260–16283 |
| `_open_fts_or_degrade` | 16284–16354 |
| `_declarative_answer_title` | 16355–16397 |
| `_question_subject` | 16424–16482 |
| `_clause_answer_title` | 16515–16583 |
| `_stage_filed_answer` | 16584–16740 |

Plus the `_FiledAnswerPlan` dataclass and the
`_SYNTHESIS_SHARE_WARN_THRESHOLD = 0.5` constant.

Total query-exclusive span: lines 16218–17574, roughly 1,357 lines.

### Call tree

```
query()
  -> config.require_workspace / config.read_config
  -> _chat_client(cfg)                          [shared]
  -> OllamaClient(model=cfg.embedding_model)     (embedder)
  -> _warn_if_nonlocal_embed_host                [shared]
  -> _resolve_local_exemption                    [shared]
  -> observability.warn_if_walk_incomplete       [shared]
  -> _open_vector_store_or_degrade / _open_fts_or_degrade   [query-only]
  -> _stale_index_names                          [shared, also used by status]
  -> observability.stage_notice
  -> answer()                                    [retrieval/answer.py:921]
  -> rendering (typer.echo)
  -> if --save:
       _stage_filed_answer                       [query-only]
       _snapshot_read / bundle_index.insert_index_entry / bundle_log.insert_log_entry
       confirm gates
       _reject_drifted_targets                   [shared]
       fsio.write_exclusive / write_atomic
       _autocommit                               [shared]
       insight_identity.near_duplicate_insights  [existing domain seam]
       _refresh_derived_after_write              [shared]
```

## Classification

| Category | Examples | Verdict |
| --- | --- | --- |
| Argument parsing | `typer.Option` / `Argument` definitions (16749–16807) | Stays in CLI |
| Presentation | every `typer.echo`: retrieval summary, citation formatting (`[confidential]` / `[partial]` / `[synthesis]`), skip notices, `--save` preview | Stays in CLI — renders fields already computed by `AnswerResult` / `Citation` / plan |
| Exit-code policy | the four ordered handlers for `OllamaUnavailable`, `OllamaModelNotFound`, `OllamaEmbeddingDimensionMismatch`, generic (17000–17048) | Adapter concern, but the *ordering* is domain knowledge worth preserving as a service-level exception taxonomy the CLI maps one-to-one |
| Coordination | building llm/embedder, opening and degrading stores, calling `answer()`, sequencing stage → confirm → write → autocommit → refresh | **Moves to the service** |
| Application rules | sensitivity high-water-mark in `_stage_filed_answer`, the title-derivation cascade, the synthesis-share threshold, the unattributed-citation gate, duplicate-scan invocation | **Moves to the service** |
| Persistence | `fsio.write_exclusive` / `write_atomic`, `_autocommit`, `_reject_drifted_targets`, `_refresh_derived_after_write`, store opening | **Shared infrastructure** — must not be duplicated into a query-only service |

## What is already delegated

Most of what the issue calls "goal 1 — retrieval, generation, citation
assembly" **already exists** outside the CLI, in `retrieval/answer.py`.
`answer()` (line 921) is a 231-line function with a documented contract that
owns FTS term extraction (`_fts_query_terms`), FTS search, dense search with a
fatal/transient error split, status and sensitivity filtering
(`lifecycle.filter_hits`, `sensitivity.sensitive_concept_ids`), RRF fusion,
context assembly and citation building (`_assemble_context`), the sufficiency
gate (`_context_holds_the_answer`), and attribution parsing
(`_split_attribution`). It already has external callers beyond the CLI and is
independently unit-testable. `Citation` and `AnswerResult` (answer.py:329,
:362) are the existing typed contract.

Also already delegated: `config.type_birth_sensitivity` (sensitivity floor),
`insight_identity.near_duplicate_insights` (duplicate scan),
`bundle_index.insert_index_entry` / `bundle_log.insert_log_entry` (catalog
mutation), `okf.build_concept` / `okf.combine_sensitivity`.

**The genuine gap** is therefore narrower than the issue implies: the
orchestration *around* `answer()` (config, store and embedder setup, degrade
handling, staleness checks) and the entire `--save` staging and filing flow,
which has no non-CLI home at all.

## The `--save` boundary

`--save` follows the same shape as every mutating verb in this codebase:
stage (phase A) → confirm gate → write (phase B) → autocommit → refresh. The
three helpers involved are called from ingest, forget, relate, set-sensitivity,
normalize-names, backfill-source-titles, merge, unmerge, reconcile, adjudicate,
suggest-relations and curate — 17, 24 and 17 call sites respectively for
`_reject_drifted_targets`, `_autocommit` and `_refresh_derived_after_write`.
They are cross-cutting write infrastructure, not query-specific.

The **filing domain logic**, however (`_stage_filed_answer`,
`_declarative_answer_title`, `_question_subject`, `_clause_answer_title`,
`_FiledAnswerPlan`), operates exclusively on `AnswerResult` / `Citation` and
has no meaning without a query having just run: title derivation reads the
answer text, and the duplicate scan compares the question against other filed
questions. That is intrinsically the query context recording what a query
produced — not a lifecycle concern like forget, purge or merge, which act on
pre-existing concepts.

**Evidence-based conclusion (for the design phase to settle):** `--save`
splits. Staging and domain logic go to the query service; write mechanics stay
shared infrastructure the service calls through; the confirm/TTY gate stays in
the CLI adapter.

## Consent and disclosure surfaces in the query path

| Surface | Location | Classification |
| --- | --- | --- |
| Nonlocal embed-host advisory before `--save` | 16926–16945 | Application rule (gated on save + locality) plus presentation |
| `_warn_if_nonlocal_embed_host` | 16925 | Presentation |
| `warn_if_walk_incomplete` | 16949–16953 | Presentation |
| Stale-index warning | 16971–16977 | Presentation |
| Sufficiency-degraded notice | 17123–17134 | Presentation |
| Attribution absent / unparsed notices | 17144–17158 | Presentation |
| Skip notices | 17159–17167 | Presentation |
| No-match message | 17169–17171 | Presentation |
| Citation markers | 17179–17196 | Presentation |
| "citations drew on none of N concepts" | 17197–17215 | Presentation |
| Synthesis-share warning (>= 0.5) | 17217–17248 | **Application rule** — threshold and comparison are policy, a pure function of `result.citations` |
| Confidential-citation sharing NOTICE | 17258–17264 | Presentation (borderline) |
| `--save` plan preview | 17315–17374 | Presentation of a domain object |
| Duplicate-scan disclosure | 17427–17443 | Presentation |
| Unattributed-citation confirm gate | 17454–17468 | **Needs the future headless-consent protocol** |
| Ordinary review-gated confirm | 17469–17478 | **Needs the future headless-consent protocol** |

The headless-consent protocol itself is out of scope here; it binds when the
lifecycle service lands. The two gates above are recorded as the query-path
surfaces that will need it.

## Test topology

`tests/unit/cli/test_query.py` holds 58 tests, `tests/unit/cli/test_query_save.py`
holds 103 — 161 total, all driving the Typer CLI end to end through
`CliRunner.invoke`. By construction they are the no-behavior-change guard, and
they assert only on `exit_code`, `stdout` and `stderr`.

**The trap.** Test doubles are injected by patching private module attributes:
`monkeypatch.setattr("openkos.cli.main.answer", ...)` (123 occurrences
repo-wide), `"openkos.cli.main.OllamaClient"` (118), and
`"openkos.cli.main.stale_derived_stores"`. This works because `main.py` imports
those names at module level and calls them unqualified, so attribute lookup
resolves through the CLI module at call time.

If the extraction moves the `answer(...)` call site into a service module that
does its own import and calls within its own namespace, those patches become
silent no-ops: the attribute is set and nobody reads it, and the real networked
`answer()` runs instead. In CI this fails loudly rather than silently, but it
can trip most of the 161 tests at once.

This is an injection *seam* built on a private attribute, not a test asserting
on a private symbol's output. The design phase must choose between:

- **(a)** the service accepts `answer` / `OllamaClient` / `stale_derived_stores`
  as injected callables that the CLI still owns and passes in by name,
  preserving `openkos.cli.main.X` patchability; or
- **(b)** migrating roughly 161 tests to patch the service module instead — a
  mechanical but substantial migration that is itself a meaningful slice.

No test was found coupling to private CLI return values or internal state
beyond this seam.

## Sizing

Removing roughly 1,000 lines from `main.py`, adding 1,100–1,300 to the service
module, leaving a 150–300 line CLI shim, plus 400–900 lines of test changes,
gives a rough total of **2,500–3,000 changed lines** — above the 2,000-line
review budget for this change. Proposed slices:

1. **Read path.** Extract setup, the `answer()` call and all rendering except
   `--save`, with dependency injection preserving patchability. Proves the
   pattern on the smallest working surface.
2. **`--save` filing.** Extract the staging and filing domain logic, leaving
   the confirm gate and the shared write helpers in the CLI.
3. **Optional.** If the design migrates patch targets to the service module,
   that migration is its own slice given the 161-test blast radius.

## Constraints and traps

- **Layering.** The canonical layer (`model`, `bundle`, `state`) never depends
  on the derived layer (`retrieval`, `graph`, `memory`). A service composing
  both is fine — services sit above both — but placing it *inside* `retrieval/`
  would introduce canonical-writing imports (`bundle_index`, `bundle_log`,
  `fsio`) into a package documented as derived and rebuildable.
- **Synchronous core.** The service must stay synchronous; the future async
  edge calls into it through a thread pool. No `async def`.
- **`engine.py` does not exist on disk** despite `docs/architecture.md`
  describing it as the thin orchestrator. There is currently no named layer for
  application logic that is neither CLI nor a domain package. This is a genuine
  architectural gap for the design phase, and plausibly an ADR.
- **No empty scaffolding.** `AGENTS.md` and `openspec/config.yaml` forbid
  creating packages before their code arrives, so no `ingest` or `lifecycle`
  stubs land in this change.
- **Shared helpers must not fork.** `_stale_index_names`,
  `_reject_drifted_targets`, `_autocommit` and `_refresh_derived_after_write`
  are used across 10–20 commands. Either they stay CLI-local and are injected,
  or they get their own relocation — which is outside a query-only change.
- **Layering is unenforced.** `docs/architecture.md` states it is a followed
  convention with no automated guard, so nothing in CI will catch a violation.
- **Commit scope** is `cli` per `AGENTS.md`. A new top-level package may warrant
  a new scope entry.

## Placement options

Existing precedent: `cli/curate.py` and `cli/next_action.py` already extract
non-trivial engine logic into siblings inside the `cli/` package, with
`main.py` keeping only the thin Typer command.

- **Option A — `cli/query_service.py`.** Follows the existing precedent exactly;
  zero new packages, low risk, familiar shape. But it is by its own precedent's
  stated intent a CLI-layer module, so MCP and API adapters would import from
  `openkos.cli.*` — backwards for "cli, api and mcp are thin adapters over one
  engine", and arguably reproduces the very problem #918 exists to fix, one file
  lower.
- **Option B — `application/query.py`.** Matches the issue's own vocabulary,
  gives adapters a home with zero CLI coupling, and extends naturally to ingest
  and lifecycle later. Introduces a new top-level package, which is
  architecturally significant and hard to reverse — a strong ADR candidate.
- **Option C — inside `retrieval/`.** Co-locates with `answer()`, no new
  package, but makes a derived-layer package write canonical state. The clearest
  layering violation of the three.

Not settled here. This is the design phase's decision.

## Risks

1. The injection-seam trap is the highest-probability source of a large,
   confusing test-failure wave if the DI strategy is not decided before
   implementation starts.
2. Sizing exceeds the review budget; the chaining decision belongs in the task
   breakdown, not mid-implementation.
3. No package exists for application services. Either an ADR resolves it, or the
   change explicitly accepts the CLI-adjacent precedent despite its conflict
   with the issue's motivation.
