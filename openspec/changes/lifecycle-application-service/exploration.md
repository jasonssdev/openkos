# Exploration: lifecycle-application-service

Issue [#918](https://github.com/jasonssdev/openkos/issues/918), lifecycle slice — the third and final part, following the archived `query-application-service` and `ingest-application-service` changes. Verified against `src/openkos/cli/main.py` as it stands (read-only exploration; no edits).

## Premise check

#918 phrases the remaining scope as "forget/purge/merge flows, including their confirmation contracts expressed as data." That phrasing is **not a spec** — checked against the code, and the finding differs sharply by command:

- **`merge` already has the exact shape ADR-0018 asks for**, just not relocated. `prepare_merge` (`main.py:9390`) and `merge_core` (`main.py:9574`) are pure, presentation-free Phase A / Phase B functions — their own docstrings say "Non-interactive... Writes nothing to disk" and "Performs NO VCS side effect." `tests/unit/cli/test_merge_core.py` (19 tests) already calls them white-box, bypassing the CLI runner entirely. This is architecturally identical to `retrieval/answer.py::answer()` for query and `extraction/concept.py::extract_concept` for ingest: **the goal is already done for merge's core**, just sitting in `cli/main.py` instead of `application/`.
- **`unmerge` is NOT already clean** — see Correction C2 below. `_execute_single_unmerge` runs `main.py:10606–11056` (439 lines) and is command-shaped: 15 `typer.echo` calls, a `typer.confirm` gate at `10922`, `sys.stdin.isatty()` at `10921`, and `_autocommit` at `11031`. Only the sub-range `10606–10887` is echo-free.
- **`forget` and `purge` are NOT split.** Both commands' Phase A (build preview) / confirm / Phase B (write) sequence is entirely inline inside the command body, interleaved with ~15–20 (`forget`) and ~25+ (`purge`) `typer.echo` calls for preview lines, refusal messages, and (for `purge`) an "IRREVERSIBLE history rewrite" disclosure. This is the same shape ingest's `_stage_derived_objects` was in before its own de-presentation slice — genuinely entangled, not already-delegated.
- **`adjudicate --apply`/`--apply-same` is the hardest unit**, comparable to nothing already shipped. Its core helpers (`_run_adjudicate_apply` at `main.py:2643`, `_run_adjudicate_apply_same` at `main.py:2845`, plus `_prepare_one_merge`, `_reconcile_planned`, `_apply_reconciliation`, `_commit_one_merge`) are ~1,000 lines, heavily interleaved with `typer.echo`, and drive `prepare_merge`/`merge_core` internally for the batch case. `tests/unit/cli/test_adjudicate.py` (154 tests) contains 154 `"openkos.cli.main.X"` patch literals, but only **4** target names this change relocates — see Correction C3 below.

**Conclusion for the design phase:** this is not one uniform "extract the lifecycle verbs" job. It is three different jobs of different sizes: (1) relocate already-clean pure functions (`merge`), (2) de-presentation work comparable to ingest's Slice 2 (`forget`, `purge`, `unmerge`'s preview half), and (3) a harder, `answer()`-scale injection-seam repoint plus de-presentation (`adjudicate --apply-same`).

## 1. Inventory of the lifecycle surface

All line numbers are from `src/openkos/cli/main.py` at exploration time (18,275 lines).

| Command | Decorator / body | Size | Already-extracted pure logic | Presentation entanglement |
| --- | --- | --- | --- | --- |
| `forget` | 5910 / 5918–6499 | ~590 | `_canonicalize_concept_id` (5832), `_resolve_concept_path` (5861) — small pure helpers | High — preview, refusal, confirm all inline in the command |
| `purge` | 6878 / 6900–7543 | ~665 body, plus ~380 lines of pre-command pure/near-pure helpers (`_purge_confirm_phrase` 6500, `_purge_clean_live_index` 6513, `_purge_clean_live_log` 6554, `_purge_rebuilt_store_paths` 6584, `_purge_dropped_stores` 6594, `_purge_store_is_gone` 6638, `_purge_sweep_store_sidecars` 6657, `_purge_dropped_store_notice` 6679, `_purge_residual_store_notice` 6753, `_purge_rebuild_indexes` 6787) | ~1050 total | The 10 helpers above are mostly pure computation/predicates | High — "IRREVERSIBLE history rewrite" preview, per-target refusals, and derived-store rebuild reporting are inline |
| `merge` | 9915 / 9923–10276 | ~360 | **`prepare_merge` (9390–9573, 184 lines) and `merge_core` (9574–9673, 100 lines) already pure, already independently tested** | Low — command body is mostly rendering `PreparedMerge` fields plus the confirm gate |
| `unmerge` | — / 10287–11056 | ~770 | `_execute_single_unmerge` (10606–**11056**, 439 lines) is command-shaped, NOT a clean Phase B — Correction C2 | Preview, gate, drift guard and `_autocommit` all inline; no public pair yet |
| `adjudicate` (incl. `--apply`/`--apply-same`) | — / 12825–13267 (command), plus `_run_adjudicate_apply` (2643–2828), `_run_adjudicate_apply_same` (2845–3200), `_prepare_one_merge` (2364), `_reconcile_planned` (2428), `_apply_reconciliation` (2495), `_commit_one_merge` (2608) | ~440 command + ~1000 helper lines | Calls `prepare_merge`/`merge_core` internally for each pair, but its own orchestration/preview/typed-count-confirm layer is unextracted | High — `typer.echo` and `typer.prompt` are threaded throughout the batch loop |

Not in this table but touching the same shared infrastructure: `relate` (7544) and `set_volatility_cmd` (8989) **also already have** `prepare_relate`/`relate_core` (9733/9804) and `prepare_set_volatility`/`set_volatility_core` (9833/9855) pure pairs — same shape as merge, same "already done, needs relocation" verdict. Neither is named in #918's "forget/purge/merge" phrasing; see Not In Scope.

`reconcile` (11187) and `_run_reconcile_from_findings` (11668) sit adjacent but operate on a different bounded context (contradiction findings, not concept identity) — see Not In Scope.

## 2. The headless-consent protocol

The archived query exploration states, verbatim:

> "The headless-consent protocol itself is out of scope here; it binds when the lifecycle service lands. The two gates above are recorded as the query-path surfaces that will need it."

...naming, in its table: **"Unattributed-citation confirm gate" (17454–17468)** and **"Ordinary review-gated confirm" (17469–17478)**, both marked "Needs the future headless-consent protocol." This change is where that debt lands.

### Confirmation gates inventoried

`config.DEFAULT_REVIEW = True` (`config.py:92`) backs `cfg.review`, read once at config-load time (`config.py:1489`). Every mutating verb gates its prompt the same way — verified at `forget`'s gate (`main.py:6344–6356`):

```python
if not auto and cfg.review:
    if sys.stdin.isatty():
        typer.confirm(f"Delete {len(purge_ids)} concepts?", abort=True)
    else:
        typer.echo("openkos forget: refusing to write without confirmation -- "
                   "stdin is not a TTY; re-run with --auto.", err=True)
        raise typer.Exit(code=1)
```

~~`purge` (6344/6351),~~ **`purge` does NOT — see Correction C1.** `merge` (10183–10184), `unmerge` (10921–10922), `relate` (7687–7688) and the other boolean write verbs repeat this identical three-way shape: `--auto` skips the prompt outright; `cfg.review == False` skips it the same way; TTY prompts and aborts on decline; **non-TTY with neither flag refuses outright (exit 1)** — exactly what `tests/unit/cli/test_ingest.py::test_non_tty_review_true_no_auto_refuses` already pins for ingest. It is uniform across every *boolean-gated* lifecycle verb, so the same non-TTY-refusal contract is what a non-TTY adapter must be able to answer *before* the refusal fires, not work around after.

`adjudicate --apply-same` is the **odd one out**: it does not use a boolean confirm. It uses a **typed-count challenge-response** (`main.py:3045–3063`):

```python
if confirm_count is not None:
    typed_count = confirm_count
elif sys.stdin.isatty():
    typed_count = typer.prompt(f"Type the eligible count ({total}) to proceed")
else:
    typer.echo("... refusing to apply -- stdin is not a TTY; re-run with --confirm-count.", err=True)
    raise typer.Exit(code=1)
if typed_count.strip() != str(total):
    typer.echo("... aborted -- confirmation count did not match exactly; nothing was written.", err=True)
    raise typer.Exit(code=1)
```

This gate consents to a **batch of N pairs sight-unseen beyond the preview**, not a single yes/no — the typed count is a deliberate anti-rubber-stamp mechanism (issues #137/#191 per the surrounding comments). A headless-consent data contract that only carries `{granted: bool}` cannot represent it; it needs at minimum `{expected_count: int, response: int | None}`.

### What "the confirmation contract expressed as data" must carry

- **Boolean gates** (`forget`, `purge`, `merge`, `unmerge`, `relate`, and siblings): a prompt string, the refusal/decline wording, and a `granted: bool` slot. Close to representable as a `ConfirmationRequest(prompt, on_decline_message)` / `ConfirmationResponse(granted)` pair.
- **Typed-count gates** (`adjudicate --apply-same`): the same, plus `expected_count: int` and a response that must exactly match it, not merely be truthy.
- **Orthogonal refusal gates** that are NOT the confirm gate at all — `forget`'s Gate 1 (surviving references, bypassed only by `--force`, never by consent) and `purge`'s per-target refusals — MUST NOT be folded into the consent data shape. They are hard refusals independent of any human answer; conflating them with "confirmation" would let a headless adapter treat a real safety refusal as a yes/no it could grant.

### Memory-flagged facts checked

- **Issue #800** does **not** describe gating `_autocommit` to only destructive verbs — `_autocommit` (`main.py:1475`) is called unconditionally by every mutating verb (17–24 call sites). What #800 actually shipped is `_echo_commit_disclosure` (`main.py:1437`), a shared one-line "committed as `<sha>` -- undo with `git revert <sha>`" renderer, called only by `forget`, `merge`, and `curate` — "the verbs whose writes a user most often wants back" (docstring, verbatim). This is presentation infrastructure, adapter-side by the same ADR-0018 rule as `_autocommit`'s family; it must be called through, never forked.
- **"curate has THREE commit points, not one"** is confirmed structurally: `cli/curate.py` calls `cli_main._autocommit` at two of its own call sites (1337, 1546) plus a per-accepted-item commit path noted in `_echo_commit_disclosure`'s docstring. `curate` itself is out of scope but is a caller of the same shared infrastructure this change must not fork.

## 3. The seam shape to mirror

`application/query.py` and `application/ingest.py` establish one consistent pattern, enforced by `tests/unit/application/test_layering.py`:

- **Typed outcome dataclasses**, not `typer.echo`: `QueryOutcome`, `FiledAnswerPlan` in query; `StagedDerivedObjects`/`StagingDrop`/`ConvergedReingest` in ingest. A service composes and decides; it returns data. The adapter renders it with the *exact same wording, in the exact same order* as before (per each module's spec requirement, "The Extraction Preserves Observable CLI Behavior").
- **No concrete backend bound inside the service** — `LLMBackend`/`Embedder` arrive as parameters (D1); `application/*.py` may import `openkos.llm.base` only, never `openkos.llm.ollama`.
- **Zero imports of `openkos.cli`, `typer`, or `rich`** — enforced by AST scan in `test_application_modules_never_import_cli_typer_or_rich`, which already generalizes over every module under `application/`, so `application/lifecycle.py` is covered automatically the moment the file exists.
- **Shared write mechanics are called through, never forked** — `_reject_drifted_targets`, `_autocommit`, `_refresh_derived_after_write` keep exactly one definition each, checked by `test_shared_write_helpers_are_never_forked` (an AST scan over all of `src/`).
- **Services stage; adapters write, confirm, and exit.** Interactive confirmation, TTY detection, exit-code selection, and rendering stay adapter-side (D4/ADR-0018). `application/lifecycle.py` should return the confirmation *request* as data, never call `typer.confirm`/`typer.prompt`/`sys.stdin.isatty()` itself.
- **`tests/unit/application/test_layering.py`** is a 127-line AST-based guard with three checks plus the shared-write-helper uniqueness check. It needs **zero new test-file changes** to cover `lifecycle.py` — its docstring explicitly names `application/lifecycle.py` as the anticipated "future third context."

`prepare_merge`/`merge_core` already conform byte-for-byte (verified: zero `typer.` references in `main.py:9390–9673`). Moving them is closer to ingest Slice 1 (mechanical relocation) than to ingest Slice 2 (de-presentation).

## 4. Size and slicing

400-line review budget, `auto-chain` / `stacked-to-main` delivery. A flat "one slice per verb" split undersizes the easy parts and oversizes the hard one.

| Slice | Content | Est. changed lines | Rationale |
| --- | --- | --- | --- |
| **1 — Seed `application/lifecycle.py` with the merge core** | Relocate `MergeResult`, `PreparedMerge`, `prepare_merge`, `merge_core`, `_merge_drift_targets` verbatim; repoint `merge()`'s and `test_merge_core.py`'s imports; define the shared confirmation-request dataclass family here | 250–350 | Mirrors ingest Slice 1 — already-pure code, mechanical move, proves the module and the consent-data shape at once |
| **2 — `unmerge`'s write core** | Promote `_execute_single_unmerge` to a public, relocated `unmerge_core`; build the missing `PreparedUnmerge`/`prepare_unmerge` pair for its still-inline preview half; repoint `test_unmerge.py`/`test_unmerge_surgical_catalog.py` | 300–400 | Same shape as Slice 1 plus the Phase-A extraction `merge` didn't need |
| **3 — De-present `forget`** | Convert `forget`'s inline preview/refusal echoes into a typed plan + disclosure data, mirroring `_stage_derived_objects`'s de-presentation; adapter renders identically | 300–400 | 89 CLI tests, zero monkeypatch-by-name risk — clean black-box safety net |
| **4 — De-present `purge`** | Same shape as Slice 3, larger surface and the IRREVERSIBLE-history-rewrite disclosure | 350–400 | 76 CLI tests, zero monkeypatch-by-name risk; the 10 existing helpers reduce net new surface |
| **5 — `adjudicate --apply`/`--apply-same`, part 1: de-presentation** | Convert the ~1,000-line helper chain's echoes into typed data, including the typed-count challenge-response shape | 350–400 | Largest single command; splitting de-presentation from the repoint keeps each PR reviewable |
| ~~**6 — `adjudicate`, part 2: injection-seam repoint**~~ | **Dissolved by Correction C3.** Only 4 sites relocate; `design.md` folds them into Slice 1. | — | — |

Total: **~1,850–2,350 changed lines across 6 slices** — larger than either predecessor, which tracks with covering five commands instead of one. All six fit under the 400-line budget individually. Slices 1→2 and 5→6 are strict dependency pairs; Slices 3 and 4 are independent of each other and of 1/2.

## Corrections (recorded 2026-09-08, after `sdd-design` re-measured against the code)

Three claims above were wrong. They are struck through in place rather than
deleted, because this artifact is archived as the change's evidence base and a
silently rewritten record teaches nothing.

**C1 — `purge`'s gate is a typed *phrase*, not a boolean, and `--auto` cannot
reach it.** Section 2 lists `purge` among the verbs sharing the boolean
`--auto`/TTY/refuse shape and cites `main.py:6344/6351` for it. Those lines are
inside `forget`'s body (`5918–6499`). `purge`'s real gate is rail 6 at
`main.py:7316`, whose own comment reads `EXACT match only -- no --auto bypass
(irreversible)`; the flag is `--confirm-phrase` and the comparison is raw
(`7331`), unlike `adjudicate --apply-same`'s `.strip()` (`3057`). **Building a
boolean confirmation for `purge` would have invented an `--auto` bypass on the
one irreversible verb.** The design's `TypedChallengeConfirmation` covers both
typed gates via a `match_mode` field.

**C2 — `_execute_single_unmerge` is not a presentation-free Phase B.** It runs
`10606–11056` (439 lines), not `10606–10887`, and holds 15 `typer.echo` calls,
the confirm gate (`10920–10929`) and `_autocommit` (`11031`). The "zero
`typer.echo`" measurement was true only of the sub-range this document itself
asserted — verifying a range-scoped claim by grepping that same range is
circular. Slice 2 therefore splits in two in `design.md`.

**C3 — the adjudicate seam is 4 relocating sites, not 96.** Measured breakdown of
the 154 `"openkos.cli.main.X"` literals in `test_adjudicate.py`:
`adjudicate_candidates` 81, `find_candidates_report` 65, `prepare_merge` 2,
`merge_core` 2, `_reconcile_merged_survivor` 2, `OllamaClient` 2. The first two
are the discovery half's collaborators; both `_run_adjudicate_apply` and
`_run_adjudicate_apply_same` receive `results: Sequence[AdjudicatedCandidate]`
by parameter, so those names are unreachable from any relocating code. The
original "96" counted `monkeypatch.setattr` call sites without asking *which
target each one names* — the count was right and the question was wrong.

## 5. Risks and open questions (each with a recommendation)

1. ~~**Risk — the 96-site injection-seam repoint is the single largest failure surface.**~~ **Superseded by Correction C3:** 4 sites, not 96. The residual risk is a *silent* no-op, addressed in `design.md` by importing the module rather than aliasing the name, so a stale target raises `AttributeError`.
2. **Open question — does the typed-count challenge-response gate belong in the same `ConfirmationRequest` family as the boolean gates, or as a separate type?** **Recommendation:** one tagged dataclass family (`BooleanConfirmation` and `TypedCountConfirmation` as two variants of one `ConfirmationRequest` union), decided in design — folding them into one untyped shape would let a caller send a truthy response to a count gate, silently bypassing its anti-rubber-stamp purpose.
3. **Open question — does `unmerge` get a full `prepare_unmerge`/`unmerge_core` public pair, or does `_execute_single_unmerge` move as-is with a thinner Phase-A wrapper?** **Recommendation:** match `merge`'s public shape exactly — asymmetry between a command and its own inverse is a readability and adapter-parity cost with no offsetting benefit, and Phase B is already done.
4. **Open question — do `relate`/`set_volatility` ride along since the marginal cost is near zero?** **Recommendation:** wait. #918 names "forget/purge/merge flows" specifically; creep into two more verbs inflates the review surface of an already six-slice change. Flag them as a fast, low-risk follow-on once `lifecycle.py` exists.
5. **Risk — `purge`'s "IRREVERSIBLE history rewrite" disclosure is safety-critical wording.** De-presenting it risks silent wording drift. **Recommendation:** the adapter must render from the exact same string templates the service returns, byte-for-byte, verified by the existing 76 `test_purge.py` CLI-runner tests — no new golden strings, no rephrasing.
6. **Open question — commit scope for a new module under `application/`.** **Recommendation:** reuse whatever scope the `ingest-application-service` cycle settled on; if none was recorded, use `ingest`/`cli`-style per-domain scoping consistent with that precedent rather than inventing one.
7. **Risk — `docs/architecture.md`'s canonical/derived layering convention remains unenforced** beyond `test_layering.py`. **Recommendation:** accepted residual risk, matching both prior slices' disposition; no new guard in scope.

## 6. What is NOT in scope

- **`relate`, `set_sensitivity_cmd`, `backfill_sensitivity_cmd`, `normalize_names_cmd`, `backfill_source_titles_cmd`, `set_volatility_cmd`** — not named by #918; a clean follow-on, not this change.
- **`reconcile` / `_run_reconcile_from_findings` / `contradictions`** — a different bounded context (contradiction-finding resolution, not concept-identity lifecycle), arguably its own future `application/contradictions.py`.
- **`curate`** — already a `cli/`-package composition with its own three commit points; a **caller** of the shared write helpers, not a relocation target.
- **The `api`/`mcp` adapters themselves** — same non-goal as both predecessors; this change only makes the callable surface exist.
- **The headless-consent protocol's wire/transport shape** — this change defines the *typed data contract*, not how a non-TTY caller supplies a pre-recorded answer, matching the "out of scope here" language accepted at the query-slice boundary.
- **Any change to on-disk formats, ledger semantics, or observable CLI wording** for these five verbs — behavior preservation is the point, guarded by the existing 443 CLI/unit tests across them.
- **`ledger_migrate`/`repair`** (`main.py:17319`) — touches the same ledger sidecar machinery but is a maintenance verb with no confirm gate and no lifecycle-verb shape.

## Ready for Proposal

Yes. Decisions 2 and 3 in section 5 affect `application/lifecycle.py`'s public surface from Slice 1 onward and must be resolved in the design phase rather than discovered mid-implementation. Both carry recommendations above.
