# Proposal: Lifecycle Application Service

Issue [#918](https://github.com/jasonssdev/openkos/issues/918), lifecycle slice —
the third and final part, after the archived `query-application-service` and
`ingest-application-service`. Every commit uses `Refs #918`; this is the last
slice, so #918 closes at archive.

## Intent

Give the concept-lifecycle bounded context a home outside the CLI, so the MVP 3
`api` and `mcp` adapters can merge, unmerge, forget, purge and batch-apply
adjudications without importing `openkos.cli`. This applies ADR-0018 to the third
and last of its contexts, and it is where the **headless-consent debt deferred at
the query boundary lands**: the confirmation gate becomes typed data a non-TTY
adapter can answer, instead of a `typer.confirm` call the service performs.

**Correcting the premise, as both predecessors had to.** #918 frames the work as
"forget/purge/merge flows, including their confirmation contracts expressed as
data." Checked against `cli/main.py`, that is three different jobs, not one:

| Verb | Actual state | Job |
| --- | --- | --- |
| `merge` | `prepare_merge` (9390) / `merge_core` (9574) are already pure and echo-free, already white-box tested | Relocate |
| `unmerge` | `_execute_single_unmerge` (10606, zero `typer.echo`) is a clean private Phase B; the preview half is still inline | Relocate + build the missing Phase A |
| `forget`, `purge` | Phase A / confirm / Phase B fully inline, interleaved with ~20 and ~25+ `typer.echo` calls | De-present, like ingest's `_stage_derived_objects` |
| `adjudicate --apply/--apply-same` | ~1,000 helper lines, echo-threaded, plus a `monkeypatch.setattr("openkos.cli.main.…")` injection seam in `test_adjudicate.py` | De-present, then repoint the seam |

## The five decisions

**D1 — one tagged `ConfirmationRequest` family, not one untyped shape.**
`adjudicate --apply-same` is not a boolean gate: it is a typed-count
challenge-response (`main.py:3045`) that exists to defeat rubber-stamping a batch
of N pairs. `BooleanConfirmation` and `TypedCountConfirmation` are two variants of
one union. A single untyped `{granted: bool}` would let a caller send a truthy
answer to a count gate and silently defeat it.

**D2 — hard refusal gates are NOT part of the consent shape.** `forget`'s Gate 1
(surviving references, bypassed only by `--force`) and `purge`'s per-target
refusals are safety refusals independent of any human answer. Folding them into
the confirmation data would let a headless adapter *grant* a refusal. They stay
outside the union, by construction.

**D3 — `unmerge` gets a full public `prepare_unmerge`/`unmerge_core` pair**,
matching `merge`'s shape exactly. Phase B is already done; asymmetry between a
command and its own inverse is an adapter-parity cost with no benefit.

**D4 — `purge`'s "IRREVERSIBLE history rewrite" disclosure is rendered from the
same string templates the service returns, byte-for-byte.** No rephrasing, no new
goldens. The 76 `test_purge.py` CLI-runner tests are the verification.

**D5 — `relate` and `set_volatility` do not ride along**, even though their pure
pairs already exist and the marginal cost is near zero. Named as a non-goal below.

## Scope

### In scope

- New `src/openkos/application/lifecycle.py`, holding the `ConfirmationRequest`
  family plus the pure pairs for all five verbs.
- Promote `_snapshot_read` (`main.py:595`, 63 references) to `fsio`, leaving a
  delegator behind. **Not scoped in the original draft, but forced**:
  `prepare_merge` calls it six times and the application layer may not import
  `openkos.cli`. Mirrors the `_slugify` -> `source_titles.slugify` promotion the
  ingest cycle already made for the same reason (`design.md` D5).
- Relocate `prepare_merge`/`merge_core`; split and relocate
  `_execute_single_unmerge`; build `prepare_unmerge`.
- De-present `forget`, `purge`, and the `adjudicate --apply/--apply-same` helper
  chain into typed plan/disclosure data; adapters render identically.
- Repoint the `monkeypatch.setattr` sites in `test_adjudicate.py` that actually
  target relocated names — **4**, not 96; see `design.md` C3.
- Delta specs. `tests/unit/application/test_layering.py` already generalizes over
  `application/*` and needs no change.

### Out of scope

- `relate`, `set_volatility_cmd`, `set_sensitivity_cmd`, `backfill_sensitivity_cmd`,
  `normalize_names_cmd`, `backfill_source_titles_cmd` — a fast follow-on (D5).
- `reconcile` / `contradictions` — a different bounded context.
- `curate` — a *caller* of the shared write helpers, not a relocation target.
- `ledger_migrate`/`repair` — a maintenance verb with no lifecycle shape.
- The headless-consent protocol's **wire/transport** shape. This change defines
  the typed contract, not how a non-TTY caller supplies a pre-recorded answer.
- The `api`/`mcp` adapters; any change to on-disk format, ledger semantics, or
  observable CLI wording.

### Size — stated up front

This is **materially larger than either predecessor**: five commands instead of
one, six slices, **~1,850–2,350 changed lines**. The reviewer should not discover
that mid-chain.

| Slice | Content | Est. lines |
| --- | --- | --- |
| 1 | Seed `lifecycle.py`: relocate the merge core; define the `ConfirmationRequest` family | 250–350 |
| 2 | `unmerge` — promote `unmerge_core`, build `prepare_unmerge` | 300–400 |
| 3 | De-present `forget` (89 CLI tests, no monkeypatch-by-name risk) | 300–400 |
| 4 | De-present `purge` (76 CLI tests; D4 wording) | 350–400 |
| 5 | `adjudicate` part 1 — de-presentation, incl. the typed-count shape | 350–400 |
| 6 | `adjudicate` part 2 — injection-seam repoint (mostly test churn) | 300–400 |

Each slice fits the 400-line budget and keeps every verb working end to end.
1→2 and 5→6 are strict dependency pairs; 3 and 4 are independent. Commit scope is
`lifecycle`, per-domain like the `ingest` cycle's own `(ingest)`. It is not in
`config.yaml`'s declared list, but that list does not match practice: the history
already carries `(evals)` 26x, `(extraction)` 25x, `(query)` 11x, `(purge)` 6x
and `(adjudicate)` 6x, none of them declared. The real convention is the domain
of the change.

## Capabilities

### New

- `lifecycle-application-service`: what the lifecycle service composes, the
  confirmation-request data contract, and what stays with the adapter.

### Modified — Purpose paragraphs only

- `forget-command`, `privacy-purge`, `entity-resolution-merge`,
  `entity-resolution-adjudication`: each describes its Typer command as the thing
  that previews, confirms and writes. That composition moves behind the service.
  Mirror the `query-command` / `ingestion` precedent — **zero requirement
  changes**. Expect thin deltas; the 443-test suite is the real contract.

## ADR

**No new ADR.** ADR-0018 already decides the layer, its granularity, the import
invariant, "services never render" and "services stage, adapters write". This
change *applies* those decisions. The one arguably new element — the typed
confirmation contract — is the literal restatement of ADR-0018's D4 rule, with a
shipped precedent in `QueryOutcome`'s typed degrade flags. Record it in
`design.md`. There is no `engine.py`, and none is proposed.

## Non-negotiables preserved

Behavior-preserving by construction: no change to local-first operation, raw/
immutability, reconstructibility from canonical files, provenance/freshness,
sensitivity levels, or the human-curates/engine-maintains split. `purge`'s
history-rewrite semantics are moved, never redefined.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| A repointed `adjudicate` monkeypatch goes a silent no-op and real merges run (ADR-0018's 124-site precedent) | High | Only 4 sites relocate (design C3); import the module, never alias the name, so a stale target raises `AttributeError` |
| Wording drift in `purge`'s IRREVERSIBLE disclosure | Medium | D4 — templates returned by the service, rendered byte-for-byte |
| A hard refusal leaks into the consent shape | Medium | D2 — refusals are structurally outside the union; pinned by a success criterion |
| Six slices exceed the aggregate budget | High | Stated above; `delivery_strategy` is `auto-chain`, `stacked-to-main` |
| Shared write helpers fork | Medium | Called through, never copied; `test_shared_write_helpers_are_never_forked` enforces it |

## Rollback

Additive-then-subtractive; touches no data. Reverting the merge commit restores
`cli/main.py` in full — no migration, no on-disk state, no derived store affected.
Every slice keeps all five verbs working end to end, so any prefix of the chain is
a valid stopping point; a partially landed chain leaves `lifecycle.py` holding the
relocated verbs and `cli/main.py` holding the rest, both functional.

## Success criteria

- [x] `src/openkos/application/lifecycle.py` references zero of `typer`, `rich`,
      `openkos.cli`, and never calls `sys.stdin.isatty()`; guarded by
      `tests/unit/application/test_layering.py`.
- [x] `ConfirmationRequest` is a tagged union with both a boolean and a
      typed-count variant; no hard refusal gate is representable as a confirmation.
- [x] All ~443 existing CLI/unit tests across the five verbs pass; output-text
      assertions are unmodified (slice 6 changes patch targets only).
- [x] `merge`, `unmerge`, `forget`, `purge`, `adjudicate --apply/--apply-same`
      produce byte-identical stdout, stderr and exit codes for equivalent inputs,
      including the non-TTY refusal path.
- [x] A caller outside `openkos.cli` can run each of the five flows without
      importing anything from `openkos.cli`.
- [x] `_reject_drifted_targets`, `_autocommit`, `_refresh_derived_after_write`,
      `_echo_commit_disclosure` each retain exactly one definition, all adapter-side.
- [x] `relate`, `set_volatility_cmd` and `reconcile` bodies are unchanged.
- [x] `uv run pytest`, `ruff check .`, `ruff format --check .`, `mypy .` green;
      branch coverage stays above the 90% gate.
- [x] Every commit uses `Refs #918`.
