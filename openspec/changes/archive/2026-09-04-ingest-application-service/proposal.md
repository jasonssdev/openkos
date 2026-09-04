# Proposal: Ingest Application Service

Issue [#918](https://github.com/jasonssdev/openkos/issues/918), ingest slice.
Every commit uses `Refs #918`; the issue also covers lifecycle and stays open.

## Intent

Give the ingest bounded context a home outside the CLI, so the MVP 3 `api` and
`mcp` adapters can ingest a source without importing `openkos.cli`. This applies
ADR-0018 to the second of its three contexts, following the shipped
`application/query.py`.

**Correcting the premise, as the query slice had to.** #918 frames the goal as
extracting the extraction pipeline. `extraction/concept.py` is already pure and
CLI-free — verified: zero `typer`, `cli` or `config` imports. It is ingest's
exact analogue to `retrieval/answer.py::answer()`, the part that is already
done. The real gap is the **orchestration wrapper directly around it**, and
unlike query's, that wrapper is itself presentation-heavy.

## The three decisions

### D1 — `_stage_derived_objects` is de-presented in THIS change, and it goes first

`_stage_derived_objects` (548 lines) carries 24 `typer.echo` calls, a
`rich.Console(stderr=True).status(...)` spinner, and
`observability.phase_callback(...)`. Extracting only the outer 305-line plan
block would place an `application/` service on top of a callee that renders —
an ADR-0018 breach by construction, and unfixable later without `application`
importing `cli`. So it is in scope, and it is slice 1.

The de-presentation is mechanical, not a rewrite, because detection is already
separated from rendering: every notice is computed by a pure
`_<name>_notice(outcome.report)` helper returning `str | None`.

| Concern | Disposition |
| --- | --- |
| The 15 `_*_notice(report)` helpers and their echo order | Stay in `cli/main.py`, moved verbatim to the caller |
| `ExtractionReport` | Returned by the service, so the adapter can run those helpers |
| Per-candidate drops (empty slug, in-batch collision, exists, build failure) | Returned as an ordered tuple of typed drop records; adapter renders |
| Degrade returns (`no-extractable-text`, `blocked-by-sensitivity`, `failed`) | Returned as reason + the caught `OllamaError`; adapter renders, including the #746 concurrency advisory |
| `Console(...).status` spinner and `phase_callback` | Adapter builds both; the service takes an `on_progress` callable it forwards to the extractor |
| `_chat_client(cfg, task="extraction")` (L6313) | Construction moves up to the adapter; the service already takes `llm: LLMBackend` |

Move and de-present in the **same** slice. Doing them separately touches the
~40 white-box `main._stage_derived_objects(...)` call sites twice — more changed
lines and two reviews for one outcome.

### D2 — the adapter performs every `_snapshot_read`; the service receives text

The bytes feed `guarded_targets`, the drift-guard baseline — write
infrastructure ADR-0018 D3 keeps adapter-side. The text feeds business logic.
One read, adapter-side, bytes retained for the guard, decoded text passed in as
a parameter.

Rationale: the guard's whole point is that the baseline is the exact bytes the
later write validates against. A service that re-read would open a TOCTOU window
in which the adapter guards bytes nobody used. It also keeps `_snapshot_read` at
one definition and leaves the service a pure `text -> new text` function,
unit-testable without a filesystem — ADR-0018's stated benefit.

Rejected: service reads and returns bytes for the guard. That makes the service
own an input that exists only to serve the adapter's write machinery, inverting
D3.

### D3 — slice boundary: the single-file path only

`_ingest_batch` calls `_ingest_single` wholesale, once per matched file, reading
only the typed `_SingleIngestOutcome` — verified. It already treats it as an
opaque unit and needs no rework. Confirmed in scope: `_stage_derived_objects`
plus the plan-composition core of `_ingest_single`. Everything else in
`_ingest_single`'s 683-line shell (workspace gating, raw copy, writes,
autocommit, derived refresh, the confirm prompt) stays adapter-side per D3/D4.

## Scope

### In scope

- New `src/openkos/application/ingest.py`.
- De-present and relocate `_stage_derived_objects`.
- Extract the plan-composition core of `_ingest_single`.
- Delta specs; extend `tests/unit/application/test_layering.py` to guard the new
  module.

### Out of scope

- `extraction/concept.py` — already pure. Do not touch.
- `_ingest_batch`, `_expand_batch_sources`, `_estimate_batch_calls`, the batch
  cost gate, the `ingest` Typer command body.
- `application/lifecycle.py` — no stub package (`AGENTS.md`).
- The headless-consent protocol (deferred to the lifecycle change).
- The `api`/`mcp` adapters; any change to the CLI surface, on-disk format,
  knowledge model, or extraction behaviour.

## Capabilities

### New

- `ingest-application-service`: what the ingest service composes, what it
  returns, and what stays with the adapter.

### Modified

- `ingestion`: **Purpose paragraph only.** It describes `openkos ingest` as the
  thing that attempts extraction and stages derived objects; that composition
  moves behind the service. Mirror the `query-command` precedent — zero
  requirement changes. Expect a thin delta; the suite is the real contract.

## ADR

**No new ADR.** Gate applied honestly: ADR-0018 already decides the layer, its
granularity, the import invariant, "services never render" and "services stage,
adapters write". This change *applies* those decisions. The one arguably new
element — returning typed disclosure data for the adapter to render — is the
literal restatement of an ADR-0018 rule and already has a shipped precedent in
`QueryOutcome`'s `vector_store_unavailable` / `fts_unavailable` flags. Record it
in `design.md`, not in a new ADR. When in doubt, do not create one.

## Delivery shape

Estimated **1,800–2,600 changed lines** total against a 2,000-line budget, so
chaining is expected. Test churn dominates: ~40 white-box call sites and the
monkeypatch-by-name sites for `main.extract_concept` /
`main.extract_concept_union`.

| Slice | Content | Estimate |
| --- | --- | --- |
| 1 | De-present + relocate `_stage_derived_objects`; notice/drop/degrade return contract; `on_progress` injection; repoint white-box sites | 1,100–1,600 |
| 2 | Plan-composition core of `_ingest_single`; `_chat_client` call site moves up; D2 text-passing | 700–1,000 |

Each slice keeps `openkos ingest` working end to end. Slice 1 alone is a valid
stopping point.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Wording drift breaks ~290 output-text assertions | High | Echo blocks move verbatim, with their comments; no reflow |
| Monkeypatch-by-name goes a silent no-op and the real extractor runs (ADR-0018's 124-site precedent) | High | Inventory both string-literal AND attribute-object forms before slice 1; the attribute form is grep-invisible |
| The 305-line block's #773 convergence short-circuit `return`s from inside the region | Medium | It becomes a typed outcome the adapter maps to a `return`; pin it in design |
| Aggregate exceeds 2,000 lines | High | Chained slices above; `delivery_strategy` is `auto-chain` |
| Shared write helpers fork | Medium | Called through, never copied; pinned by a success criterion |

## Rollback

Additive-then-subtractive; touches no data. Reverting the merge commit restores
`cli/main.py` in full — no migration, no on-disk state, no derived store
affected. If slice 1 lands and slice 2 does not, ingest remains fully
functional.

## Success criteria

- [ ] `src/openkos/application/ingest.py` references zero of `typer`, `rich`,
      `openkos.cli`, guarded by `tests/unit/application/test_layering.py`.
- [ ] All 307 `tests/unit/cli/test_ingest.py` tests pass; the ~290 output-text
      assertions are unmodified.
- [ ] `openkos ingest <file>` and `openkos ingest --batch` produce
      byte-identical stdout, stderr and exit codes for equivalent inputs.
- [ ] A caller outside `openkos.cli` can ingest a source without importing
      anything from `openkos.cli`.
- [ ] `_snapshot_read`, `guarded_targets`, `_reject_drifted_targets`,
      `_autocommit`, `_refresh_derived_after_write` each retain exactly one
      definition, all adapter-side.
- [ ] `_ingest_batch`'s body is unchanged.
- [ ] No `application/lifecycle.py` is created.
- [ ] `uv run pytest`, `ruff check .`, `ruff format --check .`, `mypy .` green;
      branch coverage stays above the 90% gate.
- [ ] Every commit uses `Refs #918`; #918 is still OPEN at archive.
