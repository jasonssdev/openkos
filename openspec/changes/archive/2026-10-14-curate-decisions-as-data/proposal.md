# Proposal: Curate Decisions As Data

MVP 6 stage "Curate decisions as data" ([roadmap](../../../docs/roadmap.md), MVP 6;
[ADR-0039](../../../docs/adr/0039-the-stable-python-api-ships-with-a-desktop-app-as-its-first-client.md)).
The full analysis is in `exploration.md` in this folder; this proposal does not
repeat it.

## Intent

The pending queue already holds every open curation decision as durable data, but
the only way to *act* on one is the interactive `curate` walk in
`src/openkos/cli/curate.py`. The per-item write sequencing (member-exists, drift
re-check, catalog recompose, read-drift, the write core, the queue resolution) is
inlined in three walks and interleaved with `typer` prompts, `typer.echo` and
`typer.Exit`. A desktop app, the first client of the stable Python API that MVP 6
owes, cannot list what is waiting for the human or apply one chosen answer to one
item without importing `openkos.cli`.

This change gives curation decisions an `application/` surface: **list the open
decisions, and apply one chosen action to one item**. It returns plain data and
never prompts, prints, reads a TTY or raises `typer.Exit`. The CLI `curate` walk
becomes its first client, with byte-identical output and unchanged consent.

Why now: it is the first MVP 6 stage, and ADR-0039 asks for the API to be shaped by a
real client rather than designed in a vacuum. The CLI is that client today, and the
desktop review screen is the next one.

## Binding scope decisions

These were resolved by the user on 2026-10-14 and recorded in `exploration.md`.
Spec and design must not reopen them.

1. **Only judged-SAME identity items are mergeable**, as curate does today.
   Identity rows with `adjudication: null` are listed as *pending evaluation* and
   carry no merge action.
2. **The list returns all six queue kinds**, each with its own action set.
   `identity`, `relation_type` and `volatility` carry actions in this change.
   `contradiction`, `revision` and `watch_refusal` are listed with an empty action
   set and the place where each one resolves.
3. **Judging stays out of this surface.** It lists and applies; it never calls a
   model to compute a decision. The judge orchestration and its cost gate stay in
   `cli/curate.py`.

## Scope

### In scope

- **S0 — characterization goldens for `curate`**, recorded on the current tree
  before anything moves, following `golden_support.Goldens`. Scenarios: identity
  y/s/d; accept-recommended; `--auto-merge`; structure y/n/s/a/r; metadata y/n/a;
  member vanished; drift exit 3; write failure exit 1; partial availability; non-TTY
  refusal. Plus a consent-matrix test: stage × TTY × `--auto` × `--accept` ×
  `--auto-merge` → outcome. This may be two PRs.
- **S1a — surface types, keep-distinct and skip.** A new
  `src/openkos/application/curate_decisions.py` with the decision item, the apply
  outcome, typed refusals (on `write_gate`), the ports and the observer. The identity
  walk's keep-distinct (`d`) and skip write through it, and
  `_record_identity_decline_from_walk` is replaced.
- **S1b — identity merge, two-phase.** A preview, then a commit against an expected
  version token. The token is the row's `payload_digest` plus its input digests,
  re-validated inside the commit section. The commit wraps the `_identity_write_one`
  sequence: reconcile port, commit section, member-exists, drift, catalog,
  read-drift, `commit_merge`, and disclosure data. The preview exposes
  `reconcile_planned`. A stale item returns a typed stale result, never a silent
  write. Accept-recommended and `--auto-merge` keep their own consent logic and
  write through the new apply.
- **S2 — structure.** Relate, accept-remaining, and reversed direction for
  asymmetric types. This slice deduplicates the sequencing against
  `apply_relation_suggestions` only if that is behavior-safe.
- **S3 — metadata.** Set-tier through `set_volatility_core` and the config-edit
  commit.
- **S4 — the generic list and consent policy as data.** `list_open_decisions` covers
  all six kinds and marks stale rows rather than hiding them. `apply_decision`
  dispatches by `decision_key` plus the version token. Bulk eligibility becomes a
  table, so identity merges can never be bulk-applied. `gate()` and the write-walk
  refusal call a pure consent function.
- **S5 — spec, ADR and docs.** The ADR and the delta specs are drafted at design,
  before S1. The docs land at archive, and only where the general shape changes.

### Out of scope

- **Judging as an application service.** This is the **required follow-up change**,
  recorded as its own MVP 6 stage in `docs/roadmap.md`. It comes after this change
  and before the app's review screen. Without it the desktop app cannot start the
  judge, and unjudged identity items never become decidable. Note: the roadmap
  entry lands via PR #1367, so the copy of `docs/roadmap.md` in this worktree may
  not show it yet.
- Merging an unjudged identity item on a human decision alone. That would be a
  later, additive action.
- Recording Structure and Metadata declines. Today `n` writes nothing, so a declined
  relation or tier is re-offered next run, in the CLI and in any future client. This
  is flagged, not fixed.
- Actions for `contradiction`, `revision` and `watch_refusal`. They keep resolving
  through `contradictions --decline`, `reconcile`, `unmerge`, their producers and
  `ingest`.
- Declaring the surface public or stable. ADR-0039 puts the freeze in MVP 6's API
  stage, so this surface stays internal.
- A non-printing commit adapter for a future API caller. `_autocommit` stays on the
  adapter side, and the surface takes the commit as a port.
- Any `api/` or `mcp/` wiring. The surface returns payload text that may derive from
  confidential concepts, so it is owner-local only. `mcp/` keeps its reduced
  `pending` projection and must not import this surface without its own disclosure
  gate.
- Moving compute code (probes, `gate`, judge orchestration, prompts) out of
  `cli/curate.py`. About 117 monkeypatch sites and three AST guards read it there.
- Any change to CLI wording, exit codes, stream ordering, on-disk format, queue
  schema or consent rules.

## Capabilities

### New Capabilities

- `curate-decisions`: the application surface for curation decisions. It covers the
  decision item and its per-kind action set (including pending-evaluation identity
  items and action-less kinds), the two-phase preview/commit protocol with a version
  token and a typed stale result, the typed refusals, the ports (commit section,
  commit, reconciliation) and the observer, consent and bulk eligibility as data,
  and the guarantee that the surface never prompts, prints, reads a TTY or raises
  `typer.Exit`.

### Modified Capabilities

- `curate-command`: a delta spec is required. The walk becomes a client of
  `curate-decisions`, so requirements that describe the walk as the composer of the
  write move behind the service with unchanged observable behavior. These are at
  least "Identity Stage Reuses Merge Cores", "Structure Stage Writes Through The
  Relate Core", "Metadata Stage Writes Tiers, Reports Sensitivity" and "Curate Reads
  And Resolves The Pending Queue". The consent requirements ("Per-Stage Cost Gate",
  "Per-Stage Accept-All Is Opt-In And Never Covers Identity", "Bulk Acceptance
  Excludes Asymmetric Relation Types", "`review: false` Accepts Only The
  Non-Destructive Stages" and "`--auto-merge` …") keep their behavior verbatim; the
  delta records only that the policy is evaluated from data. The spec phase must
  name-match every touched heading exactly.
- `pending-work`: expected unchanged. The spec phase confirms that the list reads the
  queue without changing its contract.

## ADR

**A new ADR is required: ADR-0053**, the next number after ADR-0052 in
`docs/adr/README.md`. Design writes it. The decision it records:

> Curation decisions are an `application/` surface that lists open decisions across
> all queue kinds and applies one action to one item through a two-phase
> preview/commit protocol. The protocol is keyed on `decision_key` and validated
> against a version token inside the commit section, because no lock is held
> across human time (ADR-0036). Consent and bulk eligibility are data, identity
> merges are structurally never bulk-eligible, and the surface never computes a
> judgment.

This passes the design gate. It decides an interface and its stale-handling
trade-off, and that interface is the first piece of the stable API, which makes it
hard to reverse. It applies ADR-0018 (narrow synchronous services, no `engine.py`),
ADR-0021, ADR-0036, ADR-0037, ADR-0039 and ADR-0049, and supersedes none of them.

## Approach

This follows approach 2 from the exploration: **vertical slices per kind, identity
first, the generic list last.**

1. The goldens come first, because the byte-identical risk lives in the per-item
   write sequencing, and no curate golden exists today.
2. Identity sets the protocol. It is the destructive, most-guarded kind, so the
   two-phase shape, version token, typed refusals and ports are proven against the
   hard case. They follow the `merge_service` precedent of typed refusals plus
   injected ports. Unlike that precedent, refusals carry structured fields rather
   than embedded `openkos <verb>:` text.
3. Structure and metadata then reuse the protocol.
4. The generic list and consent-as-data are derived from three real kinds, not
   designed ahead of them.

Only the apply half moves. Probes, `gate()`, judging, prompts and the stage engine
stay in `cli/curate.py`. The adapter keeps rendering, exit-code mapping (drift → 3
and run end; write failure → 1), `ctx.partial_progress` and the lock ContextVar.
Observer events fire at the same points the current notices do: the merge-skip
stderr line, `committed as <sha>` after the lock exits, the reconciliation notices,
and `section_break`.

**Delivery:** auto-chain. Estimated at about 2,500–2,750 authored changed lines
across 7–8 PRs. The ~400-line-per-PR figure is an advisory heuristic only; tests and
docs are never cut to fit it. **Decision needed at the tasks phase:** the chain
strategy (`stacked-to-main` or `feature-branch-chain`) has not been chosen.

**TDD:** strict mode is active for apply, with runner `uv run pytest`. S0 is
characterization (it records current behavior, so it is green by design). Every
later slice needs an observed RED before it is implemented.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/application/curate_decisions.py` | New | Items, list, preview/commit, apply per kind, typed refusals, ports, observer, consent policy |
| `src/openkos/application/write_gate.py` | Modified | Typed refusals and the stale result |
| `src/openkos/application/{curate_queue,queue_resolution,pending_queue_report}.py` | Modified (read reuse) | List reads; `WRITE_CORE_RESOLUTIONS` registry kept consistent |
| `src/openkos/cli/curate.py` | Modified | Walks become clients; probes, gate, judging and prompts stay |
| `src/openkos/cli/main.py` | Modified | Supplies the ports; `_record_identity_decline_from_walk` replaced |
| `src/openkos/application/auto_merge.py`, `src/openkos/cli/daemon.py` | Possibly modified | Only if the write path or shared `persist_findings` helpers are rerouted |
| `tests/unit/cli/fixtures/curate_characterization_goldens.json` and the curate tests | New / Modified | S0 goldens, consent matrix, per-slice RED tests |
| `tests/unit/application/test_layering.py`, the queue-resolution registry test | Modified if needed | Must stay green, and must not be weakened |
| `openspec/specs/curate-command/spec.md`, new `openspec/specs/curate-decisions/spec.md` | Delta / New | At archive |
| `docs/adr/0053-*.md`, `docs/adr/README.md` | New / Modified | Written at design |
| `docs/architecture.md`, `docs/cli.md` | Possibly modified | Only if the general shape changes |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| CLI output drifts with no golden to catch it | High without S0 | S0 lands first; every later slice runs it unchanged |
| Stream ordering of notices shifts (skip line, `committed as`, reconcile notices, `section_break`) | Medium | Observer events mirror the existing emission points; goldens capture full streams with a pinned environment |
| Exit-code semantics change (drift ends the run with 3; write failure exits 1) | Medium | The surface raises typed refusals, the adapter alone maps them to exits; golden scenarios for both |
| Consent drift: an identity merge becomes bulk-applicable | Low | Bulk eligibility is a table with a pinned test; accept-recommended and auto-merge stay outside the generic bulk path |
| Preview-to-write drift silently dropped for a stateless caller | Medium | A version token is re-validated in the commit section; the stale result is typed and tested |
| A relocated monkeypatch target goes silently inert | Medium | Compute code does not move; patch targets are checked per slice by mutating the patched line |
| Confidential payload text crosses a boundary | Low | The surface is internal and owner-local; there is no `mcp/` or `api/` wiring; `mcp/` keeps its reduced projection |
| Calibrated-looking confidence is treated as trust | Low | The surface does not present adjudicator confidence as a trust signal (ADR-0034 outcome) |
| The spec overstates the shipped code | Medium | At verify and archive, check requirement text against shipped behavior and this Intent, not against ticked tasks |
| The aggregate size exceeds the per-PR budget | High | Auto-chain slices with clear boundaries; the chain strategy is chosen at tasks |

## Rollback Plan

The change is behavior-preserving and touches no data, queue schema or on-disk format.
Each slice is a self-contained PR that leaves `curate` working end to end, so any
prefix of the chain is a valid stopping point. Reverting a slice's merge commit
restores the previous walk code: S0 is tests only; S1a–S3 each revert one kind's
wiring; S4 reverts the list and the consent table. No migration, derived store or
workspace state needs repair. The S0 goldens stay in place after a revert as the
guard for a retry.

## Dependencies

- PR #1367, which records the "Judging as an application service" stage in
  `docs/roadmap.md`. This change does not block on it. It is cited for the
  follow-up pointer.
- Existing write cores and gates in `application/`: `merge_service`, `lifecycle`,
  `duplicates_service`, `queue_resolution`, `drift`, `commit_phase` and
  `write_gate`.

## Non-negotiables preserved

- Local-first: the list is LLM-free and costs nothing.
- `raw/` is untouched, and `bundle/` stays pure OKF.
- The engine stays reconstructible. `findings.db` remains a derived cache, so its
  autoincrement id is never used as the handle; `decision_key` is.
- Provenance and freshness are written by the same cores as before.
- Sensitivity stays fail-closed, and the surface is owner-local.
- Human curates, engine maintains: there are no new automatic writes, and no
  identity merge is ever consented in bulk.
- The core stays synchronous, and there is no `engine.py`.

## Success Criteria

- [ ] `curate` characterization goldens exist, were recorded before any move, and
      pass unchanged at the end of every slice. Stdout, stderr and exit codes are
      byte-identical for every scenario.
- [ ] The consent-matrix test passes unchanged across all slices. Spend is consented
      in bulk; identity merges are never consented in bulk.
- [ ] `curate_decisions.py` imports none of `typer`, `rich`, `openkos.cli` or
      `openkos.vcs`. It never calls `isatty`, never prints, never prompts and never
      raises `typer.Exit`. This is guarded by `test_layering.py`.
- [ ] `list_open_decisions` returns all six kinds, needs no model, and marks stale
      rows. Unjudged identity items are pending evaluation with no merge action.
      Contradiction, revision and watch_refusal have empty action sets and name
      where they resolve.
- [ ] A commit against a superseded version token returns the typed stale result
      and writes nothing.
- [ ] No code path in the surface calls a model to compute a decision.
- [ ] `_autocommit`, `_reject_drifted_targets` and `_echo_commit_disclosure` each
      keep exactly one definition, on the adapter side.
- [ ] `curate-command` deltas and the new `curate-decisions` spec are merged at
      archive, with every heading name-matched. ADR-0053 is written and indexed.
- [ ] "Judging as an application service" is named as the follow-up in the archive
      report.
- [ ] `uv run pytest --cov` passes with branch coverage at or above 90%, along with
      `ruff check .`, `ruff format --check .`, `mypy .` and the eval self-tests.
