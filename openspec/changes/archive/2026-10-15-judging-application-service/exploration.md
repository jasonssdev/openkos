## Exploration: Judging as an application service (MVP 6 stage)

All paths are under `src/openkos` unless they start with `tests/` or `docs/`. Exploration ran on main 9d13a2ee.

### Headline findings

1. **Three of four judging kinds already have application services.** `suggest_relations_service.py:562`, `suggest_volatility_service.py:211` and `contradictions_service.py:493` each provide an observer and a `max_calls` ceiling. `suggest_relations_service` also has a `CostQuote` (`:190`) and a `confirm_cost` hook (`:229`). Identity judging is the exception: there is no service. Its helpers live in `cli/main.py:13276-13591` and its orchestration is inline in `cli/curate.py:1601-1720`.
2. **Persisting a verdict alone does not unblock `pending_evaluation`.**
   - `_identity_actions` reads the verdict only from the queue row payload (`application/curate_decisions.py:1070-1103`).
   - curate's enqueue only inserts missing rows (`curate_queue.py:302-332`), so an open `adjudication: null` row is never upgraded.
   - `pq.upsert_proposal` replaces a row whose digest changed (`state/pending_queue.py:380-386`), but nothing calls it with a verdict for an existing row.
3. **The daemon undoes the verdict.**
   - `_identity_stage` (`cli/daemon.py:253-283`) calls `producers.enqueue_identity` without `adjudicated`. That argument defaults to `()` (`application/queue_producers.py:258`), so every pass proposes `adjudication: null` (`:300-306`).
   - The payload digest of that proposal differs from a judged row's, so `upsert_proposal` retires the judged row and inserts an unjudged one.
   - Evidence: this was verified by reading the code. No test pins it. `tests/unit/cli/test_daemon.py:980-1005` covers only judged-DIFFERENT, and `:1307-1372` covers only "the daemon never merges".
   - Fix: carry servable stored verdicts into `adjudicated`. The fix needs no model call.
4. **UNCERTAIN verdicts loop.** UNCERTAIN results and malformed-reply degrades are persisted (`cli/main.py:13498`), but `_identity_actions` still maps them to `pending_evaluation` (`curate_decisions.py:1096`). An app would offer to judge them again and pay again.
5. **The quote does not bind today.** After consent, `run` rebuilds the partition (`curate.py:1607-1608`, design D4 "no-memoization"), so it can spend more than the gate quoted. On a TTY the window is seconds; at app (human) timescales it is not.
6. **Cancellation stops only at stage boundaries.**
   - `StopToken` and `Deadline` (`application/runtime.py:44-134`) are checked only between runner stages (`application/runner.py:394-448`).
   - No judging leaf has a stop hook.
   - Results persist only after the whole batch returns, so a kill mid-run loses that batch's paid work.

### 1. Inventory

| Step | Probe / price | Model calls | Persists | Progress / partial / cancel | Home |
|---|---|---|---|---|---|
| **Identity** (`curate` stage, `adjudicate` verb) | `_identity_probe` (`curate.py:1083-1143`), which runs `find_candidates_report`, drops kept-distinct pairs, then calls `_partition_adjudication_serves` (`main.py:13306`), `_serve_identity_rows` (`curate.py:1050`) and `auto_merge.judging_partition` (`auto_merge.py:201`). Price = `len(to_judge)`; served = `len(served)` | `adjudicate_candidates` (`resolution/adjudication.py:493`): one chat call per group. An all-unreadable group costs 0 | `_persist_adjudications` (`main.py:13498`) writes the adjudications store, plus `resolve_judged_different` for DIFFERENT verdicts. Then `curate_queue.enqueue_identity` (missing-only) | `on_progress(i, total, result)` fires per completed group. A partial run returns `failure`/`failed_index`, and its prefix is persisted. No cancel | Leaf in `resolution/`; serve/persist/reassemble in `cli/main.py`; stage glue in `cli/curate.py`. **No service** |
| **Identity, `adjudicate` verb** | None: no cost gate (`main.py:12434`), and it enqueues nothing | Same leaf | Adjudications store only | `--fresh` bypasses serving | `cli/main.py:12105-12490` |
| **Identity, daemon** | None | None (`uses_model=False`, `daemon.py:436`) | `enqueue_identity` with `adjudication: null` (finding 3) | — | `daemon.py:253` |
| **Structure** (edge typing) | `_structure_probe` (`curate.py:1986-2061`): `candidate_edges`, `partition_edge_suggestion_serves`, `_serve_relation_rows` | `suggest_edge_types` (`edge_typing.py:828`): one call per edge | `persist_edge_suggestions`, then `enqueue_relations` | `on_progress`; a partial run returns `failure` | Service exists and the daemon uses it (`daemon.py:286-322`). curate inlines its own copy (`curate.py:2119-2182`) |
| **Metadata** (volatility) | `_metadata_probe` (`curate.py:2420-2465`): `unanswered_type_count` minus fresh queue rows | `suggest_volatility`: one call per type, with `max_calls`/`deferred` | `volatility_service.record_answers`, then `enqueue_volatility` | Same contract | Service exists (`:211-285`); the daemon uses it |
| **Contradictions** | `_contradictions_probe` (`curate.py:2677`) prices `plan.llm_calls` with **no** served discount | `find_contradictions` (`contradiction.py:1020`) | `persist_findings` (`curate.py:2759`) plus `enqueue_contradictions` | Same contract | Service exists and serves persisted findings (`contradictions_service.py:385-461`); curate does not use it. The two price differently; this is unpinned |
| **Revisions** | In-service `cost_gate` | `revisions.run_revisions` | `producers.enqueue_revisions` | — | Daemon-only; not a curate stage |

Two kinds behave differently from identity:
- Only identity has a "judge an existing row" semantic. For the other kinds, judging produces rows.
- Contradiction and revision rows have no app actions (ADR-0053 §1), so judging them makes nothing decidable in the app.

### 2. The cost gate

**What a probe reports today.**
- Each probe is LLM-free and returns `StageProbe(items, llm_calls, served, notice)` (`curate.py:126-152`).
- `llm_calls` counts only what the stores cannot answer:
  - #867 identity: the adjudications store plus fresh open queue rows;
  - #799 relations: the edge-suggestions store;
  - #1332 volatility: the prompt-digest cache.
- `cost_line` (`curate.py:410-434`) prints `"{queued} {noun}(s), {served} served -> {n} LLM call(s)"`.
- `gate()` (`:834-879`) adds three notices — model (`:504`), optional-upgrade (`:533`) and the `--accept` hint (`:448`) — then calls `curate_decisions.spend_consent`.

**What a data-level quote → consent → run protocol needs.**
- A kind-tagged quote carrying:
  - `total`, `served`, `to_judge` and `calls` (`CostQuote` at `suggest_relations_service.py:190` is the existing shape);
  - the resolved per-task model tag (`config.resolve_task_model`), the backend and its locality;
  - the effective confidential bit (`include_confidential` plus `local_exemption`);
  - for identity, the rubric digest (`adjudication.rubric_digest()`);
  - the work keys with their per-member digests (the same pin as `judged_digests`);
  - the truncation/quarantine notices.
- Consent as a reference to the quote token.
- A run that consumes the quote and never exceeds it.

**The quote can go stale.** Between quote and run:
- ingest or the daemon can add groups;
- edits can invalidate served rows;
- a build upgrade can change the rubric (#838);
- `models:` or the backend can change;
- another surface can pay for some of the items.

**Detection.**
- Bind the token over (kind, model, backend, flags, rubric, the sorted (work key, member digests) set).
- On run, re-partition, judge only the intersection of the quoted set and the still-needed set, and report `not_in_quote`.
- Return a typed `QuoteStale` only on a change of model, locality, rubric or the confidential flag. Those are the cases where consent was given to a named model (ADR-0047 §3).

### 3. Long-running behavior

- **Slowness.**
  - The judge is `gemma4:26b-a4b`; the other roles use `qwen3:8b`.
  - On the 32 GB floor only one chat model is resident at a time (ADR-0047 §6). Curate's stage order swaps models: judge → qwen → qwen → judge.
  - Measured per edge: about 1.3 s for `qwen3:8b`, and about 6.5–11 s for a 27B model (`curate.py:516-523`).
  - The judge's ADR-0047 median is 84 s per run. No per-call figure is recorded, so an ETA cannot be sourced.
  - `chat_timeout` defaults to 600 s.
- **Progress.**
  - Every leaf takes `on_progress(index, total, result)`, 1-based, called after each item.
  - The services wrap it in observers. The CLI adapter (`observability.progress_callback`) is TTY-gated.
  - An exception raised inside the callback discards the batch.
- **Cancellation.**
  - `StopToken` is latching and lock-free. `check_halt` runs only between runner stages.
  - Nothing preempts a unit (ADR-0021), so cancel latency is at least one chat call.
  - The `max_calls` + `deferred` pattern already implements stop-early-and-resume.
- **Partial progress.**
  - A `BackendError` returns `failure`/`failed_index`; the prefix is persisted and counts are reported.
  - `BackendUnavailable` and `BackendModelNotFound` re-raise, and the partial count is stashed in `ctx.partial_progress`.
- **What a desktop caller needs.**
  - Progress events with `total`.
  - Cancellation through `StopToken`.
  - Incremental persistence by chunking the to-judge list in the orchestrator (no leaf change), so a re-quote shows `served > 0`.
  - A typed outcome: `judged`, `persisted`, `deferred`, `failure`, `unpersisted`.
  - A bounded retry when the workspace is busy. Today a busy workspace costs the spend (`main.py:13585-13591`).

### 4. Locking and concurrency

- **The lock is held only for persistence.** Judging holds no workspace lock: `curate` and `adjudicate` are `commit_phase=True` verbs, and ADR-0036 keeps model calls out of the lock. The lock is taken only for persist/enqueue, through `commit_section`. `lock_wait.locked_commit_section` is available to an app adapter.
- **A busy workspace fails open today.** The run prints an advisory and the next run re-judges. For an app that is double spend.
- **Daemon versus app.**
  - There is no mutual exclusion. The lock has no holder identity, and `jobs.db` has no running marker.
  - Both sides could judge the same items. Persisting is idempotent: the adjudications store appends and serves the latest row, and there is one open queue row per key. The harm is wasted spend, not corruption.
  - The daemon's budget (`application/budget.py`) applies to the runner only, so app spend does not count against `max_calls_per_day`.
  - The model-swap thrash of ADR-0047 §6 is the real cost. The one unavoidable interaction is finding 3.

### 5. Test coupling

**How patching is exposed.**
- There are 206 mentions of nine judging targets across 8 test files, 165 of them in `test_curate.py`: `find_candidates_report`, `adjudicate_candidates`, `candidate_edges`, `suggest_edge_types`, `suggest_volatility`, `build_graph`, `persist_findings`, `_contradiction_plan` and `find_contradictions`.
- There are 279 `setattr(curate…)` calls in total, across 14 files.
- A patch of `openkos.cli.curate.X` works only while curate code still looks `X` up in its own module.

**How to avoid neutering those tests.**
- Move no patched name. Put the new orchestration beside curate and leave the stage bodies untouched.
- Share helpers, and keep delegators in `cli/main.py` (the #1168 precedent at `main.py:13420-13491`).
- Give the ports call-time lookup (the daemon's `lambda *a, **k: …` pattern).
- For each slice, mutate one patched line and confirm that a test reddens.
- Update these guards rather than working around them:
  - `test_chat_timeout_wiring.py`: add an `_EXPECTED_SERVICE_TASKS` entry with the literal `chat_client(cfg, "adjudication")` shape.
  - The queue-resolution registry key `"openkos.cli.main._persist_adjudications"`.
  - `test_layering.py` (it scans new modules automatically).
  - `test_curate_decisions_boundary.py`, which must cover the new owner-local module.
  - The budget AST guards.

**What is already pinned.**
- **S0a/S0b goldens** pin full streams with the judge stubbed:
  - identity walks;
  - accept-recommended and auto-merge;
  - a vanished member, drift, and write failures;
  - keep-distinct fail-open;
  - the non-TTY refusal;
  - a backend going down mid-batch;
  - `adjudicate --apply d`;
  - the Structure and Metadata walks;
  - the cost line at 0 served.
- **The consent matrix** stubs both probe and run, so it pins the gate, refusal and prompt counts only.
- **Piecewise unit tests** cover `cost_line` and the gate, the model and upgrade notices, identity pricing and truncation, auto-merge pricing, the served split on a partial failure, and queue serving.

**Unpinned:**
- the Contradictions stage as a full stream;
- the cost line with served > 0 as a full stream;
- the model-disclosure lines in a stream;
- declining at the gate with real probes;
- partial-batch failure for Structure, Metadata and Contradictions as streams;
- persist fail-open and busy advisories for adjudications and edge suggestions;
- a judged-SAME queue row surviving a daemon pass. This one fails today.

### Approaches

| Approach | Pros | Cons | Effort |
|---|---|---|---|
| **1. Identity-first vertical, goldens first, protocol emerges.** Slices: S0 goldens → S1 move helpers → S2 identity quote/run → S3 relation + volatility → S4 spec/ADR/archive | Hardest case first (queue write-through, daemon clobber, UNCERTAIN). Matches ADR-0053's "protocol set by identity, then generalized". Mostly additive | Two identity orchestration paths, which can drift | Medium |
| **2. Generic `quote()`/`run()` protocol first** | Clean shape across four kinds | ADR-0053 rejected this: it is designed in a vacuum, before the identity edge cases are known (ADR-0039) | Medium-high |
| **3. Lift curate's Stage engine into `application/`** | One implementation | Moves the code the 206 patches resolve, so silent neutering is likely. Touches the byte-identical CLI | High |

Rough sizes for approach 1. About 400 authored lines per PR is advisory only.

| Slice | Content | Size |
|---|---|---|
| S0 | Characterization goldens for the unpinned spend paths, plus a RED test for the daemon clobber | ~400 (JSON excluded) |
| S1 | Move identity serve/persist/reassemble into `application/`, with `cli/main.py` delegators, the registry key and the AST task map | ~250 net |
| S2a | Identity quote and queue write-through (`curate_queue.set_identity_verdicts` via `upsert_proposal`); the daemon carries stored servable verdicts; a `judged_uncertain` evaluation (spec change) | ~350 |
| S2b | Identity run: progress observer, `StopToken` cancellation through chunking, incremental persistence, bounded persist retry, `QuoteStale` | ~400 |
| S3 | `relation_type` and `volatility` on the same protocol, over the existing services | ~350 |
| S4 | Delta specs, an ADR (0059 or later; 0055–0058 are reserved for the desktop ADRs), archive | ~150 |

### Recommendation

Approach 1, under these constraints:
- Goldens come first, including a RED test for the daemon clobber.
- The new service ends at persisted verdicts. It never calls the auto-merge pass or any write core (ADR-0037, ADR-0049).
- It does not rewire curate's judging section. Some duplicated sequencing is accepted.
- One equivalence test runs both paths on the same fixture and compares the persisted store and the queue rows.

### Scope decisions (resolved by the user, 2026-10-14; binding)

1. **Kinds.** Identity first, then relation_type and volatility.
   - Contradictions and revisions are deferred: they carry no app actions and require the judge swap.
   - The quote carries `kind`, so adding them later is additive.
2. **The quote is a binding ceiling.**
   - A run judges only quoted items that are still needed, never more, and reports new work as `not_in_quote`.
   - A change of model, locality, rubric or the confidential flag returns a typed `QuoteStale` and requires new consent.
   - There is no TTL; staleness is decided by digests.
3. **The app may judge while the daemon runs.**
   - There is no lease lock (that would contradict ADR-0036). Safety comes from idempotent persists and digest pins.
   - When `unattended` is enabled in config, an informational notice says the automatic engine is also active.
   - The daemon clobber (finding 3) is fixed in this change regardless.

Carried into the proposal (not blockers):
- The name for an UNCERTAIN verdict: `judged_uncertain`, not re-offered by default.
- Whether an ETA belongs in the quote. Not now; no per-call figure exists.

### Risks

- **Daemon clobber:** the daemon rewrites judged identity rows as unjudged (finding 3). This must be fixed here, or the feature does not work.
- **UNCERTAIN loop:** the re-judge-and-repay loop (finding 4) needs an additive `evaluation` value.
- **Lost spend:** a busy workspace loses the persist and with it the spend. A kill mid-batch loses all of that batch's paid work.
- **Path drift:** the two identity orchestration paths can drift. Shared helpers and the equivalence test mitigate this.
- **Model swap:** 26B ↔ 8B thrash when the daemon and the app alternate models (ADR-0047 §6).
- **Cancel latency:** at least one chat call, up to the 600 s `chat_timeout`.
- **Rubric staleness:** queue-row verdicts carry no rubric digest, so `curate_queue.identity_verdicts` can serve a verdict from before a rubric change. Read in the code, not tested; the proposal should check it.
- **Privacy:** the new module is owner-local and must be covered by the boundary guard (ADR-0053 §6).

### Ready for Proposal

Yes, after the three product decisions are answered one at a time.
