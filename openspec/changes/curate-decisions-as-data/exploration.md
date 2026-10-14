## Exploration: curate decisions as data

MVP 6 stage "Curate decisions as data": the pending queue's decisions become an `application/` surface (list the open decisions, apply one chosen action to one item), the CLI `curate` walk becomes a client of it, CLI behavior and output stay byte-identical, and the consent rules stay exactly as they are (spend is consented in bulk; identity merges never are).

### Current State

**1. Stages and decision kinds.** `_STAGES` in `src/openkos/cli/curate.py` has five stages; only three are decision walks.

| Stage | Queue kind | Per-item actions today | What an action writes | Consent |
|---|---|---|---|---|
| Preconditions | — | none (halts) | nothing | — |
| Identity (`_identity_run`) | `identity` | `y` merge · `s`/`n`/Enter skip · `d` keep-distinct (`_confirm_identity`) | Merge: `merge_service.commit_merge` → `lifecycle.merge_core` + per-pair `_autocommit`; `merge_core` resolves the row `resolve_merged` and retires every open row naming the absorbed id. Keep-distinct: `cli_main._record_identity_decline_from_walk` → `duplicates_service.apply_identity_decision` (writes a `bundle/.state` sidecar, resolves the row `declined`; the caller commits). Skip: nothing, the row stays `pending` | Spend: stage `gate()`. Write: always per item on a TTY; `auto_acceptable=False`, so `--accept identity` / `review: false` never reach it |
| Structure (`_structure_run`) | `relation_type` | `y` / `n` / `s` / `a` (accept the rest) / `r` reverse for asymmetric types (`_confirm_item`) | `prepare_relate` → `relate_core` → `_autocommit`; `resolve_related` (`as_proposed`, or `modified` if reversed). `n` and `s` write nothing (`n` is only listed under `declined:`) | Spend: `gate()`. Write: per item, or bulk via `--accept structure` / `a`, except asymmetric types are never bulk (#624). The stage is opt-in via `--structure` (#1268) |
| Metadata (`_metadata_run`) | `volatility` | `y` / `n` / `s` / `a` | `prepare_set_volatility` → `set_volatility_core` (writes `openkos.yaml`) → `_autocommit_config_edit`; `resolve_volatility_set` | As Structure; fully bulk-acceptable |
| Contradictions (`_contradictions_run`) | `contradiction` | none; report-only | Enqueues rows and persists findings. Decisions happen in other verbs: `contradictions --decline` (`apply_contradiction_decision`), `reconcile`, `unmerge` | Spend: `gate()` only |

Other queue kinds are not walked by curate: `revision` (no resolve core; retired by its producer) and `watch_refusal` (resolved through `ingest`).

Consent paths:

- `gate()`: `--auto` accepts the spend; on a TTY it asks `typer.confirm`; on a non-TTY without `--auto` it declines.
- Write-walk refusal (`run_curate`): a write stage on a non-TTY is declined unless `_accepts` holds or `_unattended_identity` applies.
- `--auto-merge` (ADR-0049): the one unattended identity path. Its constants live in `application/auto_merge.py`, it needs `--auto` on a non-TTY, and it plans then applies over fresh judgments only, mechanically (no reconcile), with one commit per run.
- Accept-recommended (`_offer_accept_recommended`): on a TTY, one y/N covers all fresh in-class SAME groups, while each merge still writes through `_identity_write_one` with its own commit.
- "Judged different" is not a human action. A persisted DIFFERENT verdict resolves the row `declined` inside `cli.main._persist_adjudications`.

**2. What lives where.**

Already in `application/`:

- Writes: `merge_service.commit_merge`; `lifecycle.{prepare_one_merge, merge_core, prepare_relate, relate_core, prepare_set_volatility, set_volatility_core, merge_drift_targets, recompose_*, merge_walk_confirmation}`; `duplicates_service.apply_identity_decision`; `contradictions_service.apply_contradiction_decision`.
- Queue: `queue_resolution.resolve_*`; `curate_queue.{read_open_rows, identity_verdicts, relation_suggestions, volatility_suggestions, enqueue_*}`; `pending_queue_report.read_report`.
- Gates and policy: `drift.describe_drift`, `commit_phase.describe_read_drift`, `write_gate` (`Refused`, `DriftDetected`, …), `consent.py`, `auto_merge.*`.
- Walk helpers: `suggest_relations_service.{apply_relation_suggestions, partition_edge_suggestion_serves, persist_edge_suggestions}`, `suggest_volatility_service.{cached_answers, record_answers}`.

Only in `cli/`:

- The per-item write sequencing is inlined in the three walks: `_identity_write_one`, the relate block in `_structure_run` (which duplicates the sequencing in `apply_relation_suggestions`), and the tier block in `_metadata_run`.
- The prompts and the bulk-answer state: `_confirm_item`, `_ask`, `_confirm_identity`, `_confirm_choice`, `_confirm_direction`, `_confirm_recommended`, and the `ctx.accepted_stages` mutation. `cli/main.py` also imports `_confirm_identity` and `_confirm_choice` for `adjudicate` and `suggest-relations --apply`.
- The `cli/main.py` private helpers curate uses: `_autocommit`, `_autocommit_config_edit`, `_plan_config_commit`, `_reject_drifted_targets` and `_reject_read_drift` (both `typer.Exit(3)`), `_recomposed_catalog`, `_apply_reconciliation`, `_record_identity_decline_from_walk`, `_partition_adjudication_serves`, `_persist_adjudications`, `_commit_section_for` (a CLI ContextVar), and `_echo_commit_disclosure` / `_format_merge_preview_line` / `_identity_walk_prompt`.
- The stage engine: `CurateContext`, `StageProbe`, `StageOutcome`, `gate`, `run_curate`, `render_summary`.
- The daemon imports `curate_module.persist_findings` and `finding_input_digests` from `cli/curate.py`.

**3. Item identity and staleness.**

- The handle:
  - `pq.PendingItem.id` is an autoincrement in `findings.db`, a derived, rebuildable cache, so it is not a stable handle.
  - The stable handle is `decision_key = "<kind>:<body>"` (`pq.identity_key`, `relation_type_key`, `volatility_key`, `contradiction_key`). A partial unique index allows one open row per key.
  - `payload_digest` (the payload plus the input digests) is a ready version token.
- What the read path carries:
  - `curate_queue.read_open_rows(layout, kind)` yields `OpenRow(item, payload, inputs)` with `item.id` and `item.decision_key`.
  - A relation row carries the effective direction and type.
  - A volatility row carries no targets or digests; it is fresh while `current_default == effective_tier`.
  - An identity row may carry `adjudication: null`: the daemon's identity stage runs `uses_model=False`, so the rows it makes are unjudged.
- Freshness:
  - `read_open_rows` does not filter for freshness; the typed readers apply `_fresh` digest checks.
  - `pending` lists stale-but-open rows until a producer pass retires them.
- The guards at apply time: member-exists, `_reject_drifted_targets` against the bytes `prepare_*` read, `_recomposed_catalog`, and `_reject_read_drift`.
  - All of them run inside `_commit_section_for(root)()`, the workspace lock (ADR-0036), which is held only for the commit phase, never across a prompt.
  - Drift exits 3 and aborts the whole curate run; a write failure exits 1.
- The consequence for a stateless caller:
  - The CLI holds its `prepared` baselines across the prompt in-process; a stateless API caller cannot.
  - The surface therefore needs a two-phase shape: preview, then commit against an expected version.
  - Re-preparing only at apply time would silently drop the preview-to-write drift window.

**4. LLM involvement.**

- List time needs no model and costs nothing.
- Compute time costs one model call per unit, priced by the stage probes (LLM-free) and consented once by `gate()`: per unserved group (identity), per untyped edge (structure), per type (metadata), per pair (contradictions). `served` (adjudication store plus open rows, #799/#867/#1141) lowers the price.
- Apply time: an identity merge may make one reconciliation call (`_apply_reconciliation`, #645).
  - It is disclosed in the preview line but not counted in the cost gate, and it runs after consent and before the drift re-check.
  - The surface must expose a `reconcile_planned` field in the preview and take the reconciliation call as a port.
  - Auto-merge is mechanical and never reconciles.

**5. Test coverage.**

- No curate characterization golden exists.
  - Other verbs have them: contradictions, duplicates, ingest, merge/reconcile, reindex, revisions, suggest-relations and suggest-volatility (`tests/unit/cli/fixtures/*_characterization_goldens.json`, `golden_support.py`).
  - The only byte-identical guard today is the stdout/stderr assertions in `test_curate.py`.
- Curate-related tests: `test_curate.py`, `test_curate_auto_merge.py`, `test_curate_accept_recommended.py`, `test_curate_queue.py`, `test_curate_lock.py`, `test_curate_structure_opt_in.py`, `test_walk_output_convention.py`, `test_confidential_local_exemption.py`.
- Monkeypatch coupling: about 117 `monkeypatch.setattr("openkos.cli.curate.<name>")` sites across 12 files.
  - They patch `find_candidates_report`, `adjudicate_candidates`, `candidate_edges`, `suggest_edge_types`, `suggest_volatility`, `build_graph`, `persist_findings`, `_concept_type_names`, `_contradiction_plan`, and `application_backends.chat_client`.
  - Moving compute code out of `cli/curate.py` breaks them, or leaves them silently patching names nobody reads.
- Layering guards (`tests/unit/application/test_layering.py`):
  - `application/` may not import `openkos.cli`, `typer`, `rich` or `openkos.vcs`.
  - `_autocommit`, `_reject_drifted_targets` and `_echo_commit_disclosure` must each have one definition, outside `application/`.
  - No private `application` name may be consumed from outside it.
- Registry guard: a test enumerates every `*_core`, and `WRITE_CORE_RESOLUTIONS` in `queue_resolution.py` lists `openkos.cli.main._persist_adjudications`, a cli path.
- AST guards that read `cli/curate.py`: `test_chat_timeout_wiring.py`, `test_backend_construction_guard.py`, `test_neutral_catch_sites.py`.

**6. Constraints.**

- AGENTS.md and ADR-0018: narrow synchronous services under `application/`, no `engine.py`.
- ADR-0021: the core is synchronous.
- ADR-0036: the lock covers commit phases only, never human time.
- ADR-0037: unattended work computes and enqueues; only a human-facing path applies.
- ADR-0039: the stable Python API ships in MVP 6. This surface stays internal and is not declared public in this change.
- ADR-0034 and ADR-0049: ADR-0034's own first measurement failed. The structural `base`/`-N` class was admitted by ADR-0049, whose constants live in `application/auto_merge.py`.
- `openspec/specs/curate-command/spec.md` is the contract. Requirements touched:
  - "Curate Reads And Resolves The Pending Queue": a presented but unwritten row returns to `pending`.
  - "Per-Stage Accept-All Is Opt-In And Never Covers Identity".
  - "`--auto-merge` …".
  - "Per-Stage Cost Gate".
- Sensitivity:
  - Row payloads (`rationale`, `conflicting_claims`) derive from concept text and may include confidential content when the producer ran under `local_exemption`.
  - `pending_queue_report` never renders payloads.
  - The MCP `pending` tool projects only kind, targets and resolving command, and withholds rows with a non-disclosable target.
  - A surface that returns payloads is owner-local only: `mcp/` keeps its reduced projection and must not import it unguarded.
- Calibration: the adjudicator reports 0.95 on right and wrong verdicts alike (ADR-0034 outcome), so the surface must not present confidence as a trust signal.

**Corrections to the prior mapping.**

- Contradictions is report-only in curate.
- Structure is opt-in.
- "Judged different" is a model verdict, not a per-item action.
- `main.py` imports curate's prompt helpers.
- The daemon imports `persist_findings` and `finding_input_digests` from `cli/curate.py`.
- The registry test points at a cli function.
- Structure and Metadata declines record nothing; only Identity `d` persists a ruling. A declined relation or tier is therefore re-offered next run, in the CLI and in any future desktop client. This is out of scope here but flagged.

### Affected Areas

- `src/openkos/application/curate_decisions.py` (new): items, preview/commit, apply per kind, typed refusals, ports, observer.
- `src/openkos/cli/curate.py`: the walks become clients. Probes, `gate`, judging and prompts stay.
- `src/openkos/cli/main.py`: supplies the ports (`_autocommit`, `_commit_section_for`, reconciliation); `_record_identity_decline_from_walk` shrinks.
- `src/openkos/application/{curate_queue,queue_resolution,pending_queue_report}.py`: read and registry reuse.
- `src/openkos/application/auto_merge.py` and `src/openkos/cli/daemon.py`: touched only if the write path or the shared `persist_findings` helpers are rerouted.
- Tests:
  - `tests/unit/cli/test_curate*.py`;
  - `tests/unit/application/test_layering.py`;
  - the queue-resolution registry test;
  - new goldens and tests.
- `openspec/specs/curate-command/spec.md` (deltas); a new ADR; `docs/architecture.md` only if the shape changes.

### Approaches

1. **Generic list/apply protocol first, then migrate kinds.**
   - Pros: one coherent contract, and the list is immediately useful to a desktop client.
   - Cons: the abstraction is designed before the hard case is handled, which is the "designed in a vacuum" risk ADR-0039 warns about.
   - Effort: medium, with high rework risk.
2. **Vertical slice per kind, Identity first, generic list last.**
   - Characterization goldens come first. The Identity slice sets the protocol: two-phase preview/commit, a version token, typed refusals and ports. Structure and Metadata then follow it.
   - The generic list and consent-as-data come last, derived from three real kinds.
   - Pros: it handles the destructive, most-guarded kind first; the CLI becomes a client incrementally; and the monkeypatch targets survive because only the apply half moves.
   - Cons: the list surface arrives late.
   - Effort: medium-high, with the smallest per-PR risk.
3. **Horizontal by layer.**
   - All apply cores move first, then all rewiring.
   - Cons: a big-bang rewire of three walks at once, and the protocol is not exercised until the end.
   - Effort: high.

### Recommendation

Approach 2. The sizes below are rough authored changed lines, including tests and docs. About 400 per PR is an advisory heuristic only; tests and docs are never cut to fit it.

- **S0 — characterization goldens for curate.** Tests only, about 450–600 lines, possibly two PRs. They follow `golden_support.Goldens` and are recorded on the current tree before anything moves.
  - Scenarios: identity y/s/d; accept-recommended; auto-merge; structure y/n/s/a/r; metadata y/n/a; member vanished; drift exit 3; write failure exit 1; partial availability; non-TTY refusal.
  - Plus a consent-matrix test: stage × TTY × `--auto` × `--accept` × `--auto-merge` → outcome.
- **S1a — the shared surface types plus keep-distinct and skip (about 350).**
  - Adds `DecisionItem`, `ApplyOutcome`, typed refusals on `write_gate`, and the ports and observer.
  - The CLI walk is rewired and `_record_identity_decline_from_walk` is replaced.
- **S1b — Identity merge (about 500).**
  - A two-phase preview / `commit_merge_decision` wraps the `_identity_write_one` sequence: reconcile port, commit section, member-exists, drift, catalog, read-drift, `commit_merge`, disclosure data.
  - The CLI renders from the returned data.
  - Accept-recommended and auto-merge keep their consent logic and write through the new apply.
- **S2 — Structure relate, accept and reversed (about 300).** It also deduplicates against `apply_relation_suggestions` if that is safe.
- **S3 — Metadata set-tier (about 250).**
- **S4 — generic list and consent policy as data (about 450).**
  - `list_open_decisions(layout)` returns plain data, with stale rows marked rather than hidden.
  - `apply_decision` dispatches by `decision_key` plus the version token.
  - Bulk eligibility is a table like `Stage.auto_acceptable`, so identity merges can never be bulk-applied.
  - `gate()` and the write policy call a pure consent function.
- **S5 — spec, ADR, docs (about 200).** The ADR and the delta spec are drafted at design, before S1; the docs land at archive.

Why this order:

- The byte-identical risk lives in the per-item write sequencing.
- The surface can raise typed refusals and take ports exactly like `merge_service`.
- The adapter keeps rendering, exit-code mapping and the lock ContextVar.

### Risks

- **Byte-identical output without goldens.** S0 is mandatory before anything moves.
- **Ordering of stderr/stdout and notices.** These must fire at the same points, and the observer events must mirror them:
  - the merge-skip stderr line;
  - `committed as <sha>` after the lock exits;
  - the reconciliation notices;
  - `section_break`.
- **Exit-code mapping.**
  - Drift exits 3 and ends the run; a write failure exits 1; `ctx.partial_progress` stays in the stage engine.
  - The surface raises typed errors, and the adapter maps them to exit codes and formats them.
  - The `merge_service` precedent embeds `openkos <verb>:` text in `Refused`; a UI-neutral surface should prefer structured fields.
- **Test coupling.** About 117 monkeypatch sites and the AST guards on `cli/curate.py` mean compute code stays in place.
- **Layering.**
  - `_autocommit` lives on the adapter side and prints, so a future API needs its own non-printing commit adapter. That adapter is not in this change.
  - Moving `_persist_adjudications` would require updating the registry path.
- **Stateless callers versus ADR-0036.** No lock is held across human time, so the version token (`payload_digest` plus input digests) is re-validated in the commit phase. A stale item yields a typed `Stale` result, never a silent write.
- **Consent drift.**
  - Bulk eligibility stays data, with a pinned test that Identity merges are never bulk-applied.
  - Accept-recommended and auto-merge keep their consent outside the generic bulk path.
- **Unjudged identity rows (product decision).** The queue can hold rows with `adjudication: null`, but curate only offers SAME. For byte-identity, curate keeps that filter.
- **Sensitivity.** The surface returns payload text, so it is owner-local only. MCP must not wire it without its own gate.
- **Stability.** This is the first piece of the stable API, but ADR-0039 puts the freeze in MVP 6, so the surface is marked internal.
- **Process.** Specs can overstate the code, and a passing verify is not a met Purpose. At verify and archive, check the requirement text against shipped behavior.

### Scope decisions (resolved by the user, 2026-10-14)

1. **Only judged-SAME identity items are mergeable**, as curate does today. Unjudged identity rows (`adjudication: null`) are listed as pending evaluation, with no merge action. Merging an unjudged item on a human decision alone may be added later as an additive action.
2. **The list returns all six queue kinds**, each with its own action set:
   - identity, relation_type and volatility carry actions in this change;
   - contradiction, revision and watch_refusal list with an empty action set and say where they resolve.
3. **Judging stays out of this surface.** The surface lists and applies; it never calls a model to compute a decision, so the judge orchestration and its cost gate stay in `cli/curate.py` for this change.
   - **Required follow-up (do not lose):** "Judging as an application service" is its own change, recorded as an MVP 6 stage in `docs/roadmap.md`, after this change and before the app's review screen.
   - Without it, the desktop app cannot start the judge, and unjudged identity items never become decidable.
   - The proposal must list it under out-of-scope with this pointer.

### Ready for Proposal

Yes. S0 (goldens) is the first deliverable; the proposal also needs a delta spec and an ADR before code.
