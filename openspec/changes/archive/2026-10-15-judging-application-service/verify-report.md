# Verify report: judging-application-service (main 4ffbf10f)

Test run (unpiped): `uv run pytest tests/unit/application/test_judging.py tests/unit/application/test_judging_equivalence.py tests/unit/application/test_identity_adjudication.py tests/unit/cli/test_daemon.py -q` -> `328 passed in 21.89s`.

Verdict: 0 CRITICAL, 3 WARNING (spec overstatements, amend at archive), 1 goal-level WARNING (queue rows), several SUGGESTIONs.

## judging-application-service
All paths src/openkos/application/judging.py (J) unless noted; tests tests/unit/application/test_judging.py (T).
| Requirement | Verdict | Evidence |
|---|---|---|
| Plain data, no interaction | PASS | no typer/rich/cli/vcs imports; no print/isatty (grep clean); test_layering.py:133 (AST isatty, judging.py); T:288, 1529, 2588, 3531 capsys; persist failure = JudgingOutcome.unpersisted_cause (J:1009) |
| Quote LLM-free, kind-tagged | PARTIAL | quote_judging J:836; _CallRecorder J:336; T:287 unreachable backend; T:396, 416 zero-call. Gaps: `served` excludes UNCERTAIN (extra `uncertain` field, J:248-262); no `quarantined` notice for identity (only relation_type). See W1, W2 |
| Token binds context, opaque, no TTL | PARTIAL | QuoteBinding/QuoteToken J:113, 186; T:234 no time field, T:681 not stale by age. Token is a transparent frozen dataclass, not opaque. See W3 |
| Judged-uncertain excluded by default | PASS | J:513-558 partition, J:866-880; T:465, 480, 492, 500 |
| Notices as data | PASS (identity + relation + volatility unattended) | J:383 _unattended_notice, read-only open (T:576 no file created); T:587-648 |
| Consent is a token reference | PASS | run_judging J:1118 returns RunRefused("no_quote_token"); T:827 |
| Run never exceeds quote | PASS | _run_identity J:1179 intersection, not_in_quote, settled; _CeilingClient J:1042 refuses before the call; T:916-1062, 2028-2082, 2976-3025 |
| QuoteStale | PASS | changed_fields J:146; run_judging J:1118; T:858 parametrized (model, backend, locality, confidential, rubric), T:886 work-only not stale |
| Persist per chunk | PASS | _persist_chunk J:1087; identity_adjudication.persist_adjudications (:340-420) digest pin, DIFFERENT -> resolve_judged_different, unreadable member skipped; T:1064, 1177, 1204 |
| Cancellation at chunk boundary | PASS | stop checked per chunk J:1213; T:1096, 1113, 1126; latency stated in module docstring J:1-60 |
| Progress via observer | PASS | _ObserverGuard J:1061; T:1220, 1253, 1280 |
| Busy retry then unpersisted | PASS (constants UNMEASURED, see below) | J:982-983, 1087; T:1304, 1330, 1361 |
| Typed outcome | PARTIAL | JudgingOutcome J:1009; T:1396, 1423, 1437. `not_in_quote` is a tuple of keys not a count; `dropped` added. See W2/amendments |
| No lock across model call | PASS | T:1468, 1495, 1361 |
| App may judge while daemon runs | PARTIAL | no lease taken; T:1514 (active engine does not block), test_judging_equivalence.py:250 (run + daemon pass leave one open row). "Spend does not count against daemon budget" holds by construction (no shared counter), untested. Concurrent double-persist scenario covered only at identity, one group |
| Ends at persisted verdicts | PASS | no merge/auto_merge/write-core import in J; T:1177 never merges |
| relation_type / volatility same protocol | PARTIAL | quote+run+stale+stop+persist+typed outcome all present (J:704-835, 1423-1764; T:1667-3530). BUT neither kind writes or amends any queue row (see G1) |
| Existing commands keep output | PASS | S0a goldens (apply-progress), test_daemon.py/curate goldens green in this run; not re-run in full here |
| Owner-local | PASS | test_curate_decisions_boundary.py `_TARGET` includes judging + identity_adjudication; mcp/api scan |

## curate-decisions delta
| Requirement | Verdict | Evidence |
|---|---|---|
| Write-through to open row (SAME/UNCERTAIN, no create, no reopen, pinned) | PASS | curate_queue.set_identity_verdicts curate_queue.py:370-421 (amend-only via pq.amend_open_proposal); tests/unit/state/test_pending_queue.py:608-670; test_curate_queue.py:213+ |
| MODIFIED mergeable / judged_uncertain | PASS | tests/unit/application/test_curate_decisions.py:2861-2875 |
| MODIFIED computes no judgment | PASS | no model call in curate_decisions |

## pending-work delta
| Requirement | Verdict | Evidence |
|---|---|---|
| Identity producer carries servable verdict | PASS | identity_adjudication.servable_stored_verdicts :256-292 (include_confidential=None, read-only, no write); daemon.py:285-289; test_daemon.py:1415 (carried), 1437 (old rubric -> amended to null, not retired), 1452 (edited -> retire), 1467 (either confidential bit), store untouched |
| Write-through not a new/stale proposal | PASS | test_pending_queue.py:762 (kind_stats unchanged), 774-823 amend_same_inputs; test_daemon.py second-pass test moves only last_seen with kind_stats equal |

## Proposal Purpose / goal check
G1 (WARNING, goal-level). The Purpose says "A run ends at persisted verdicts and at queue rows that carry them, so the item becomes decidable through curate-decisions." Shipped reality:
- identity: write-through is amend-only. A group with no open row stays invisible to curate-decisions until a daemon producer pass (cli/daemon.py `_identity_stage`) runs. With `unattended` off, a desktop app can quote, consent and judge, and persist verdicts, but nothing becomes listable/mergeable; no application-layer producer exists that the app can call (producers are driven only by cli/daemon.py and curate).
- relation_type and volatility: the run persists to the edge-suggestion and volatility stores only. No queue row is created or amended (grep: no queue symbol in judging.py or the two services; no queue assertion in T for these kinds). They become decidable only after a daemon/curate producer pass.
So: quote and consent and judge of all three kinds work without a terminal (PASS), but "decidable without a terminal" holds only where a row already exists or a producer pass runs. Either amend the Purpose, or file a follow-up for an app-callable producer.

## Spec overstatements and exact amendments (apply at archive)
W1. Quote requirement, `served`: "`served` (items already answered by a persisted, still-servable verdict)". Code: `served` counts only decided (SAME/DIFFERENT) verdicts; servable UNCERTAIN is counted in a separate `uncertain` field (J:248-262, J:866-880). Amend to: "`served` (items already answered by a persisted, still-servable SAME or DIFFERENT verdict), `uncertain` (items holding a servable UNCERTAIN verdict, in `to_judge` only when opted in), so that `total == served + uncertain + fresh`."
W2. Quote requirement, notices: "a quarantine notice when members were quarantined". Identity never emits one (apply-progress S2b). Amend to: "a quarantine notice for relation_type when edges were quarantined (identity has no quarantine)". Also relation `truncated`/`quarantined` carry only rendered `text`, no count (QuoteNotice.text); state "a truncation notice (count for identity, rendered text for relation_type)".
W3. Token requirement: "The token MUST be opaque to the caller." Code exposes `QuoteToken` as a frozen dataclass with public fields (J:186); only `digest()` is a transport form. Amend to: "The token MUST be an immutable value the caller hands back unchanged; its fields are read-only data and a client MUST NOT construct or alter one."
Also (minor wording): Typed outcome: "`not_in_quote` ... with its count" -> "`not_in_quote` (the work keys, whose length is the count)"; add `dropped` and `observer_failed`.

## Recorded deviations: does the spec need amending?
1. Chunk sizes 4 and 1 (not 8 and 4): No spec change. The spec states latency formula only ("in-flight chunk's calls times chat timeout"), which holds. Update design/ADR-0059 only if they quote 8/4 (ADR line 62 says "set by measurement": fine). Suggested ADR note: none required.
2. `include_uncertain` ignored for relation_type/volatility: YES amend. In "Judged-Uncertain Items Are Excluded..." add: "The opt-in applies to identity only. For relation_type and volatility a quote MUST ignore it and bind `include_uncertain=false`, because those kinds have no UNCERTAIN verdict." (J:866 docstring, apply-progress).
3. `JudgingOutcome` gained `dropped`: YES amend "The Run Outcome Is Typed": "counts `judged`, `persisted`, `dropped`, `deferred`, `not_in_quote`, `unpersisted` and `settled_since_quote`, with `judged == persisted + dropped + unpersisted`; `dropped` counts verdicts withheld on purpose (member edited, raised, forgotten, unreadable, or a degraded reply)."
4. Volatility drops answers about concepts edited/forgotten mid-call: Covered by the spec's persist requirement ("raised or forgotten ... MUST be dropped") but that sentence sits under identity-style persistence. Amend "Relation Type And Volatility Use The Same Protocol": add "A relation_type or volatility answer whose sampled concepts or endpoints changed during the call MUST be dropped, not stored (reported as `dropped`)." Code: _VolatilityChunk J:1531, _classify_chunk J:1392; T:2314, 3240, 3259.
5. PERSIST_ATTEMPTS/backoff UNMEASURED: Spec says only "bounded number of times"; no value stated, so no overstatement. Do NOT add numbers to the spec. Keep the UNMEASURED label in J:982 docstring; follow-up F1.
6. Quote counts zero-call groups via a recording stand-in: No spec change (spec states the behavior, not the mechanism). tasks.md Conflict 1 already resolved in the spec's favor. Optional ADR/design note.
7. J13 observer carries counts when a backend error is raised: Already worded in the spec ("counts ... reach the caller through the progress observer's per-chunk events"). Code matches (guard.chunk_persisted before `raise failure`, J:1262-1270). One nuance to add: "`chunk_persisted` reports the cumulative persisted count and fires only for a chunk that produced results." Also state: when a persist fails in the same chunk as an availability error, the outcome is `unpersisted` (returned) and the error is NOT raised (J:1255-1260), which contradicts "BackendUnavailable MUST propagate". Amend: "...MUST propagate to the caller as raised once the completed chunks are persisted; if that chunk's own persist fails, the typed `unpersisted` outcome is returned with `failure` set instead."

## Non-blocking follow-ups
F1. Measure the real contenders (daemon commit section with git autocommit, ingest) before fixing PERSIST_BACKOFF_SECONDS; the app adapter also needs to pick `wait_seconds` for its commit section.
F2. G1: add an app-callable identity/relation/volatility producer (or let the run enqueue missing rows) so a judged item becomes listable without the daemon. Needs a spec decision because the identity write-through is "MUST NOT create a row".
F3. Test gap: no test that app spend is excluded from the daemon budget, and the concurrent-persist scenario is covered for one group only (equivalence test :250).
F4. Spec "calls ... shipped `max_calls` semantics MUST be kept" for relation/volatility: the surface exposes `chunk_size` and the token ceiling, not `max_calls`; acceptable, but say so in the spec.
F5. Proposal text (not merged) still says the daemon matches confidential inclusion; spec correctly says it does not. No action beyond archiving the spec text.
