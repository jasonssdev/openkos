# Design: curate-auto-merge

Refs #1298. Governed by ADR-0034 and ADR-0044; records its decisions in
ADR-0049 (written by slice 1, content in D12 below). Inputs: `proposal.md`
(its "Decisions" are resolved and not reopened here), `exploration.md`,
`evals/auto_merge/PREREGISTRATION-1298.md` (binding ship rules), the verdict
file `evals/auto_merge/results/auto-merge-verdict-1298-20261005T181724Z-gemma4-26b-a4b.md`,
ADR-0034, ADR-0036, ADR-0037, ADR-0047, and the code at `48b08d0f`.

## Technical approach

```
openkos curate [--auto] --auto-merge                                    (cli/main.py::curate)
   |  --reconcile together with --auto-merge  -> usage error, exit 2 (before any workspace read)
   v
run_curate (cli/curate.py)
   Identity probe: llm_calls from auto_merge.judging_partition(...)   <- same function the run uses
   gate(): --auto still the only spend consent (TTY prompts, non-TTY refuses without --auto)
   non-TTY write refusal (curate.py:2610): Identity exempt iff ctx.auto_merge
   v
_identity_run (cli/curate.py)                                        compute phase, NO lock
   1. partition served/fresh (unchanged: _partition_adjudication_serves + _serve_identity_rows)
   2. eligibility = auto_merge.run_eligibility(cfg, list_models)     static checks, then one /api/tags read
        ineligible -> stderr reason; TTY: today's flow; non-TTY: declined, nothing judged
   3. auto_merge.judging_partition(...): in-class groups forced fresh (never served)
   4. adjudicate_candidates(to_judge) -> persist (write-back) -> reassemble -> enqueue (unchanged)
   5. auto_merge.plan_auto_merges(results, fresh_keys, blocked, ...)  per-group gates, pure
   6. auto_merge.apply_auto_merges(...)                               ONE commit phase, lock held:
        per planned merge, in order: judged-digest check -> prepare_one_merge (re-plan
        against the bundle the previous merge left) -> guardrail -> snapshot -> merge_core
        then ONE autocommit over the union of paths
   7. disclosure block (stdout): one line per merge + its unmerge, then the commit line
   8. non-TTY: stop here (remainder left, never prompted)
      TTY: accept-recommended pre-pass (slice 4) -> today's per-item walk
   v
cli/main.py::curate end of run: summary, derived refresh, survivor-edit caveat,
exit 1 iff the auto pass failed mid-write
```

The decision logic (class, constants, eligibility, gates, the multi-merge
commit phase) lives in a new `src/openkos/application/auto_merge.py`, so it
is testable without the CLI and has no `typer`. `cli/curate.py` keeps
rendering, TTY detection and the prompt. No `engine.py`, no async, no new
dependency, no schema or file-format change.

## Decisions

### D1. `application/auto_merge.py` is the one seam

**Choice.** A new synchronous module under `application/` (ADR-0018). It
holds the class predicate, the measured constants, run eligibility, the
judging partition, the per-group planner, the multi-merge commit phase, the
accept-recommended selector, and the survivor-edit check. It imports only
`config`, `sensitivity`, `llm.base`, `resolution.candidates`,
`resolution.normalize`, `resolution.adjudication` (`rubric_digest`,
`Verdict`, `AdjudicatedCandidate`), `application.ingest`
(`ATTACH_EXCLUDED_TYPES`, `application/ingest.py:121`) and
`application.lifecycle`. It never imports `openkos.cli`, a concrete
`openkos.llm.*` client, or `yaml` (the application layering guard in
`tests/unit/application/test_layering.py` keeps holding). The VCS and lock
side effects come in as injected callables (`autocommit`, `commit_section`),
the same shape `merge_service.commit_merge` already takes
(`application/merge_service.py:188-214`).

**Alternatives.** Extracting an `identity_service` from `_identity_run`
first (rejected in the proposal: a large refactor of destructive code ahead
of the feature; worth a separate change). Putting the logic in
`cli/curate.py` (rejected: untestable without the CLI and it would grow a
second copy of class logic beside the eval's). A separate `AutoMerge` stage
(rejected: breaks the stage-order invariant and adjudicates twice).

### D2. The class predicate moves; the eval imports it

`in_structural_class(group: CandidateGroup) -> bool` moves verbatim from
`evals/auto_merge/run_structural_class.py:85-96` into `auto_merge.py`.
`run_structural_class.py` replaces its definition with
`from openkos.application.auto_merge import in_structural_class` (the name
stays in the eval's namespace, so `exposure()` at line 131 and
`_self_test_predicate()` at lines 676-712 call the production copy
unchanged, and `evals/run_self_tests.py` keeps discovering it). The 13
self-test cases are behavioural, so the move needs no self-test edit. The
same cases, plus one boundary per clause, are ported to
`tests/unit/application/test_auto_merge.py` so pytest (and its coverage
gate) kills a mutation of each clause, not only the eval self-test.

### D3. Measured constants and where each comes from

All are `typing.Final` module constants in `auto_merge.py`, never settings
(ADR-0034 decision 3). Each cites its evidence in its docstring.

| constant | value | source |
|---|---|---|
| `MEASURED_MODEL` | `"gemma4:26b-a4b"` | run files' `"model"` and `stamp.model.name` |
| `MEASURED_MODEL_DIGEST` | `"001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c"` | `stamp.model.digest` in both `runs-structural-calibration-20261005T164453Z-gemma4-26b-a4b.json` and `runs-structural-confirmation-20261005T170308Z-gemma4-26b-a4b.json` (line 21 of each) |
| `T_STAR` | `0.90` | verdict file line 12 (`t*: 0.9000`), from the frozen #1054 rule |
| `MEASURED_AT_COMMIT` | `"63b5f551541f7e0e98c939331898f747e23f38da"` | `stamp.harness.commit`, `dirty: false`, both arms |
| `MEASURED_PROMPT_SHA256_16` | `"aaed5c3e06569c83"` | `stamp.prompts[adjudication/system].sha256_16`, both arms |
| `MEASURED_RUBRIC_DIGEST` | `"sha256:<64 hex>"`, computed in slice 1 (see D4) | `rubric_digest()` evaluated on the tree at `63b5f551` |
| `MEASURED_CONTEXT_WINDOW` | `12288` | run files' `settings.context_window` |
| `MEASURED_MAX_GENERATION_TOKENS` | `8192` | run files' `settings.max_generation_tokens` |

A unit test (`test_constants_match_the_committed_stamp`) loads the two
gemma4 structural run files and asserts the model, digest, commit, prompt
hash and settings constants equal the stamp, so a constant can never drift
from its evidence. It reads committed JSON only; no model.

### D4. Rubric identity: production checks the full `rubric_digest()`

**The gap.** The harness stamp hashed only `_SYSTEM_PROMPT`
(`aaed5c3e06569c83`), while `rubric_digest()`
(`resolution/adjudication.py:344-372`) also covers `_OCCURRENCE_MARKERS`,
`_NEGATION_CUES`, `_SENTENCE_SPLIT` and `OCCURRENCE_WITHDRAWN_NOTE`.
ADR-0034 says a change to the withdrawal markers invalidates the
measurement, and the measured verdicts went through
`withdraw_self_refuting_same` (`adjudication.py:610`), so the measured rubric
is the whole rubric at `63b5f551`, not the prompt alone.

**Choice.** Eligibility compares `rubric_digest()` at runtime with
`MEASURED_RUBRIC_DIGEST`. That constant is computed once, in slice 1, by
evaluating `rubric_digest()` on `git show 63b5f551:src/openkos/resolution/adjudication.py`
(in a scratch copy, never committed), after checking that the same tree's
`prompt_hash(_SYSTEM_PROMPT)` is `aaed5c3e06569c83` (which ties the
computed digest to the stamped prompt). Two pinned tests follow: the constant
equals `rubric_digest()` at HEAD (so a rubric edit fails CI loudly and the
editor must decide, exactly as `tests/unit/test_prompt_byte_identity.py:26`
already forces for the prompt), and the measured prompt hash constant equals
`prompt_hash(_SYSTEM_PROMPT)`.

**HEAD versus the measured commit.** Not fully verified in this phase,
because the phase had no shell to run `git show` or compute a sha256. What
is established: the system prompt at HEAD still hashes to the measured
`aaed5c3e06569c83`. Two tests pin that value at HEAD
(`tests/unit/llm/test_prompts.py:19`, `tests/unit/test_prompt_byte_identity.py:26`).
The only commits on `main` after the measurement are `b5715d65` (okf export)
and `48b08d0f` (docs), and neither archive mentions the adjudication module.
Unverified: the four withdrawal-rule components. **Slice 1's first task is
that verification. If `rubric_digest()` at HEAD differs from the value at
`63b5f551`, the class is ineligible from day one: apply stops and reports it
as a blocker.** It does not update the constant, because that would ship a
rubric that was never measured.

**Alternatives.** Checking only the 16-hex prompt hash, as the stamp did
(rejected: a marker edit would leave the class "eligible" on an unmeasured
rubric). Re-running the harness to stamp the full digest (not needed when
the tree at the measured commit is in git history and the digest is
computed from data, never from function source).

### D5. The model digest reaches production through `InstalledModel`

- `llm/base.py:91-100`: `InstalledModel` gains `digest: str | None = None`
  as its last field. The default keeps every existing
  `InstalledModel(tag=..., family=...)` construction valid: 44 callers,
  doctor and tests included.
- `llm/ollama.py:641-690`: `list_models` reads `entry.get("digest")` and
  keeps it only when it is a non-empty `str` (otherwise `None`), inside the
  existing parse `try`. The value is stored as Ollama lists it (64 hex, no
  `sha256:` prefix), which is the shape the harness stamped through
  `evals/harness_stamp.py:150-173`.
- `llm/openai_compatible.py`: no change. It already constructs
  `InstalledModel` without a digest, so `digest` is `None`. A test pins it.
- **Read at runtime** by `auto_merge.model_digest_ineligibility(list_models, tag)`.
  It takes a `Callable[[], list[InstalledModel]]`, finds the entry whose
  `tag` equals the resolved tag exactly, and returns a reason string or
  `None`. Curate supplies the callable from
  `application_backends.diagnostics_client(cfg, model=tag, timeout=…, factories=ctx.backend_factories).list_models`
  (`application/backends.py:340-378`). That is one `GET /api/tags`, no model
  call, and it uses the same endpoint resolution as the chat client.
- **What makes the class ineligible**, each with its own reason:
  - a `BackendError` raised by the listing ("could not list installed models");
  - the tag is not listed;
  - the backend lists the tag with no digest (the `openai-compatible`
    backend always, and an Ollama entry without one);
  - a digest that differs from the measured one. Both are shown as their
    first 12 hex characters.

### D6. Run eligibility: static checks first, the digest last

`run_eligibility(cfg, list_models) -> RunEligibility(reasons: tuple[str, ...])`.
It is eligible if and only if `reasons` is empty. It collects every failing
static reason (no I/O) before the digest:

1. `config.resolve_task_model(cfg, "adjudication")` equals `MEASURED_MODEL`.
   This is the resolver `chat_client` uses (`backends.py:293`), so a
   `models: {adjudication: null}` opt-out lands on `cfg.model` and fails here.
2. `cfg.context_window == MEASURED_CONTEXT_WINDOW`.
3. `cfg.max_generation_tokens == MEASURED_MAX_GENERATION_TOKENS`.

   For checks 2 and 3, `chat_client` forwards these values verbatim, so the
   config value is the value sent. An explicit key equal to the measured
   value is eligible; `None` or any other value is not.
4. `rubric_digest() == MEASURED_RUBRIC_DIGEST`.

Only when 1-4 pass does it call `list_models` (D5), which avoids a network
read for a run that is already ineligible. `static_ineligibility(cfg)` is
exposed separately, because the probe needs it and the probe does no I/O
(D8).

An ineligible run prints one stderr line, never a silent no-op:
`openkos curate: Identity: --auto-merge is not available this run -- <reason>[; <reason>...]; every group keeps its per-item prompt.`

### D7. The pass, in order

**Per-group gates.** `plan_auto_merges(results, *, fresh_keys, blocked, cross_type_concern, ordered_pair) -> AutoMergePlan(planned, skipped)`
is pure. Its inputs are injected facts, so each gate is unit-testable on
hand-built `AdjudicatedCandidate`s. A group is considered only when
`in_structural_class(group)`; a non-class group is not "skipped", it simply
never enters the pass. Each in-class group then has to pass these gates in
order, and the first failing gate becomes its `AutoMergeSkip.reason`:

| # | gate | skip reason |
|---|---|---|
| 1 | its group key is in `fresh_keys` (judged in this run's batch, never served from `adjudications` or the queue) | `no fresh verdict this run` |
| 2 | `verdict is Verdict.SAME` | (not listed; a non-SAME group is simply not merged) |
| 3 | `confidence >= T_STAR` | `confidence 0.85 below 0.90` |
| 4 | no member in `blocked` (D7a) | `a member is confidential or cannot be sent to the model` |
| 5 | `cross_type_concern((survivor, absorbed)) is None` (`lifecycle.py:2976`) | the concern text |
| 6 | survivor not already planned this run | `survivor already merged this run` |

Gates 7-9 run in the commit phase below.

`ordered_pair` is `lifecycle.ordered_merge_pair` (`lifecycle.py:2903`). For
an in-class group it always returns the base id as survivor, under the
suffix-family criterion: the class predicate requires `is_suffix_family`,
and `ordered_merge_pair` checks that first. No new survivor rule.

Gate 6 cannot fire through `find_candidates` today, because HIGH groups have
disjoint member sets (`resolution/candidates.py:619-621`). It stays because
ADR-0034 makes it a rule rather than a coincidence of the candidate
generator, and its test uses hand-built overlapping groups (see Testing).

**D7a. The confidential and LLM-blocked exclusion.**
`blocked = sensitivity.sensitive_concept_ids(bundle_dir)` is called with
**both escape hatches off**, deliberately ignoring the run's
`include_confidential` and `local_exemption`. With both off, it returns
every id whose sensitivity ranks confidential plus every unreadable,
unparseable, absent or blank one (`sensitivity.py:255-296`, fail-closed
rank at `sensitivity.py:185-197`). That is exactly "confidential or
LLM-blocked" under the strictest reading, computed in one walk. It is
exposed as `strict_blocked_members(bundle_dir)`, so the auto pass and the
accept-recommended selector use one predicate.

**Commit phase.** `apply_auto_merges(root, layout, planned, *, commit_section, autocommit, judged_digests, digest_of) -> AutoMergeOutcome`
enters `with commit_section():` **once** for the whole run. Inside it,
each planned merge goes through these steps in order:

7. **Judged-content check.** Every member's current
   `digest_of(member_id)` must equal `judged_digests[member_id]`. These are
   the #1137 pins `_identity_run` already takes before judging
   (`curate.py:1155-1160`). The verdict covers the bytes that were judged,
   so a member edited since then fails with the reason
   `changed since it was judged`.
8. **Re-plan against the current bundle.** The step calls
   `prepare_one_merge(root, layout, index_path, log_path, group, ordered_pair=(survivor, absorbed))`
   (`lifecycle.py:3005`) under the lock. `None` (a member no longer
   resolves) is a skip.
9. **Stacked-body guardrail.** A plan that crosses it is a skip. The check
   is a new one-line predicate,
   `lifecycle.stacked_body_refused(prepared) -> bool`, extracted from
   `lifecycle.py:3372-3375`. `preview_apply_same` calls the same predicate,
   so there is one guardrail predicate, not two.
10. **Snapshot.** The pre-merge bytes of every path the merge will write or
    unlink: the write targets `merge_drift_targets(layout, prepared, include_catalog=True)`
    returns, plus the ledger sidecar's current bytes or its absence.
11. **Write.** `merge_core(bundle_dir, index_path, log_path, prepared)`
    (`lifecycle.py:626`), with no `_apply_reconciliation`. That is
    `--no-reconcile` semantics, so the stacked body is what lands and
    `unmerge` restores byte parity. Each merge writes its own `log.md`
    bullet and its own ledger entry, exactly as today.
12. **Record.** `AutoMergeRecord(survivor, absorbed, confidence, survivor_after_sha256)`.
    The sha256 is `bundle_ledger.survivor_sha256` of the survivor as
    written, the value `unmerge`'s #1110 check compares against
    (`lifecycle.py:1045-1061`).

A skip inside the commit phase leaves the group to the per-item walk on a
TTY, or unmerged on a non-TTY. Then, still under the lock, comes the one
commit:
`autocommit(root, union_paths, message)`. `union_paths` is the ordered,
de-duplicated union of each merge's `merge_commit_paths(prepared, result)`
(`merge_service.py:167-178`). The message is
`openkos: auto-merge <N> pair(s) in the measured identity class`, followed
by a blank line and one `merge <absorbed> into <survivor>` line per merge.
With zero applied merges, nothing is committed.

**Why one lock across the run (the concurrent-writer question).** If each
merge took its own commit phase, with the run commit at the end, a
concurrent `openkos` verb could take the lock between two of them. Its
pathspec-scoped autocommit (`vcs/git.py:650-669`) would then stage
`bundle/log.md` and `bundle/index.md` while they still held this run's
uncommitted bullets, sweeping our merges into its commit. Holding the lock
from the first re-validation to the autocommit excludes every `openkos`
writer for the whole burst. A human editor is not excluded by any lock, and
that risk is unchanged from today's per-merge commits, which also commit the
whole `index.md`/`log.md`.

**Why re-plan under the lock.** ADR-0036 Decision One puts planning in
compute. Here every merge in a run changes the bundle the next one reads.
`prepare_merge`'s read dependencies are the whole bundle
(`lifecycle.py:573-576`), so a plan computed before the first merge
read-drifts by construction on the second. The re-plan is model-free and
prompt-free, which are the two things ADR-0036 keeps out of the lock (its
line 82). It is bounded by the 50-group candidate cap
(`_MAX_CANDIDATE_GROUPS`), and in practice by the class's low frequency. The
compute phase still does the expensive and fallible work: the judging and
the gates. ADR-0049 records this as a scoped refinement (D12).

**Mid-run failure (decision 5).** Suppose `merge_core` raises
`OSError`/`ValueError` on merge *k*. The pass first restores merge *k*'s
snapshot (step 10): it rewrites each path's prior bytes, recreates the
absorbed file and restores the ledger sidecar, so the working tree is
exactly what merges 1..k-1 left. It then commits merges 1..k-1 as above and
stops; no later planned merge is attempted. `AutoMergeOutcome.failure`
names *k*'s pair and the error. If the restore itself fails, the pass does
**not** commit, because committing a half-merge is worse than a dirty tree:
it reports every path left modified and the merges that landed uncommitted.
`_identity_run` then returns a `failed` `StageOutcome` and skips the walk.
Later stages run, because stage failures are stage-scoped. `curate` exits 1
at the end, which keeps the documented "1 on a failed mid-walk write"
contract (`cli/main.py:15248-15252`) while the summary and the derived
refresh still run for what landed. A `WorkspaceBusyError` on entering the
section propagates before anything is written, as today (exit 3).

**Partial batch (#441).** When `adjudicate_candidates` stops mid-batch, the
pass still runs over the completed fresh verdicts (the merges need no
model). After that, `_identity_run`'s existing failure split re-raises
`BackendUnavailable`/`BackendModelNotFound` or returns `failed`, exactly as
for the walk (`curate.py:1388-1417`).

### D8. Fresh verdicts only, one partition function for the probe and the run

`judging_partition(groups, served_by_key, to_judge, *, auto_merge, interactive, statically_eligible) -> (served_by_key, to_judge)`:

| case | result |
|---|---|
| `auto_merge` false, or `statically_eligible` false | unchanged (today's serve) |
| eligible, `interactive` | every in-class group is removed from `served_by_key` and appended to `to_judge` (served from `adjudications` or from an open queue row alike) |
| eligible, not `interactive` | `to_judge` = every in-class group, served or not; non-class groups are neither judged nor walked, as before this change, when a non-TTY Identity spent nothing |

`_identity_probe` (`curate.py:1005-1054`) calls it with
`statically_eligible=static_ineligibility(cfg) == ()`, so `llm_calls`
prices what the run will pay. The run calls it with the full eligibility,
digest included. If the digest fails after a statically eligible probe, the
run falls back to today's partition and pays less than the cost line said.
That is the safe direction: the line never understates the spend. `fresh_keys`
is the set of group keys in `batch.results`.

**Write-back: yes.** Fresh in-class verdicts are persisted through the
existing `cli_main._persist_adjudications(...)` with the #1137
`judged_digests` pins (`curate.py:1173-1179`), like every fresh Identity
judgment. They replace any older row for that group key; slice 2 confirms
that the store's write replaces the row rather than adding one beside it.
The reason: the fresh verdict is the newest judgment under the current
rubric, and leaving an older row in place would make a later prompt or
`adjudicate` display a verdict this run has already superseded. Nothing the
auto pass does ever reads the cache back (gate 1), so writing it is
display-only, never authority. No schema change; model provenance on cached
rows stays out of scope.

### D9. CLI wiring

- **Flag.** `curate --auto-merge` (`cli/main.py:15133-15190`), default
  `False`, threaded as `CurateContext.auto_merge: bool = False`
  (`curate.py:242-313`), which fails closed when a context is built without
  it. Its help text names the class, the per-run opt-in, the one commit and
  the undo. No configuration key; `--accept identity` stays refused;
  `review: false` is unchanged.
- **`--auto` is still the only spend consent.** `gate()` is untouched. With
  `--auto-merge` alone, a TTY gets the normal cost prompt and a non-TTY
  without `--auto` declines with no spend, exactly as today.
- **Non-TTY exemption.** At `curate.py:2610`, the condition becomes
  `stage.writes and not sys.stdin.isatty() and not _accepts(ctx, stage.name) and not _unattended_identity(ctx, stage)`,
  where `_unattended_identity` is `stage.name == "Identity" and ctx.auto_merge`.
  `accepted_stages` is not touched (Identity still structurally cannot
  enter it). Inside `_identity_run`, `interactive = sys.stdin.isatty()` is
  read once, and on a non-TTY:
  - an ineligible run returns `declined`, with the reason and the existing
    unattended hint, and nothing is judged;
  - an eligible run judges in-class groups only, runs the pass, prints the
    disclosure and returns. Its notice reads
    `applied <n> automatically; <m> in-class group(s) left for review -- run \`openkos curate\` on a terminal.`
  The accept-recommended pre-pass and the per-item walk are never entered,
  so `typer.prompt` is unreachable on a pipe. A test makes `typer.prompt`
  raise to prove it.
- **`--reconcile` with `--auto-merge`** is refused up front, next to the
  #803 refusal (`cli/main.py:15253-15258`), before the workspace gate:
  `openkos curate: --auto-merge merges mechanically and cannot be combined with --reconcile.`
  It exits 2 (usage error, the same code as the `--reconcile`/`--no-reconcile`
  pair). The message is a module constant beside `_RECONCILE_CONFLICT_MESSAGE`.
  `--no-reconcile --auto-merge` is allowed: it is a no-op for the auto pass
  and applies to the interactive remainder as today.
- **Disclosure block** (stdout, printed right after the pass, before the
  walk). The header is
  `openkos curate: Identity: merged <N> pair(s) automatically (measured class, gemma4:26b-a4b, confidence >= 0.90):`.
  Then, per merge:
  `  <absorbed> -> <survivor> (confidence 0.97) -- undo: openkos unmerge <survivor>`.
  Then `cli_main._echo_commit_disclosure(sha, prefix="  ")`, the shared
  #800/#1221 sentence, printed only when `autocommit` returned a sha (the
  #817 rule). Each merge line carries both concept ids and an undo command,
  so the Quiet Engine's B4 rule ("sha or every touched document on a line
  with an undo command") matches it. In-class SAME groups that were not
  merged get one stderr line each:
  `openkos curate: Identity: not merged automatically: <absorbed> -> <survivor> -- <reason>.`
- **Survivor-edit caveat.** `ctx.auto_merged` (`field(default_factory=list, init=False)`)
  keeps the records. After `run_curate` returns, `cli/main.py::curate` calls
  `auto_merge.survivors_edited_since(layout, ctx.auto_merged)`. It compares
  each survivor's current `survivor_sha256` with the recorded one, and for
  each survivor a later stage of the same run (Structure, Metadata) edited,
  it prints:
  `  note: <survivor> changed after its automatic merge; \`openkos unmerge <survivor>\` will refuse unless run with --discard-survivor-edits, which discards that later edit.`
- **Exit codes.** 0 normal; 1 workspace or config failure, or an auto-pass
  write failure (D7); 2 usage, including `--reconcile --auto-merge`; 3
  drift in the interactive walk (unchanged) or a busy lock. The auto pass
  never exits 3 on a changed member: it skips the group (gate 7), the
  conservative direction.
- **Daemon pin.** No code. The daemon's identity stage only enqueues
  (`cli/daemon.py:253-283`, ADR-0037). Two tests:
  - a behavioural test runs the maintenance identity stage on an in-class
    fixture with a stub judge that answers `same` at 1.0, and asserts no
    file is deleted, no merge commit is made and only identity rows are
    enqueued;
  - a structural test asserts that `cli/daemon.py` and
    `application/runner.py` do not import `openkos.application.auto_merge`.

### D10. Accept-recommended pre-pass (slice 4)

- **Where.** In `_identity_run`, on a TTY only, after the auto pass (when
  there was one) and before the per-item walk.
- **Which groups it offers.**
  `auto_merge.recommended(results, *, fresh_keys, blocked, cross_type_concern, ordered_pair, excluded_survivors)`
  applies the D7 gates with gate 3 removed ("any confidence"): in class;
  a fresh verdict from an eligible run; `SAME`; no strict-blocked member; no
  cross-type concern; one per survivor (excluding survivors the auto pass
  merged). The stacked-body guardrail is also applied when each item is
  prepared, because a one-answer acceptance is bulk consent like
  `--apply-same`, which refuses guardrail-crossing plans for the same
  reason (`lifecycle.py:3372`).
- **When the run counts as eligible.** If `--auto-merge` already computed
  eligibility, that result is reused. Otherwise it is computed after
  judging, and only when at least one fresh in-class `SAME` verdict exists,
  which avoids the `/api/tags` read on runs that have nothing to recommend.
- **What is shown.** One list. Each item shows absorbed, survivor,
  confidence and `undo: openkos unmerge <survivor>`, followed by
  `Accept all <n> recommended merge(s)? [y/N]`. `y` accepts every listed
  item. Anything else (N or Enter) sends them all to the per-item walk,
  where each gets today's `[y/N/s/d]`-style prompt.
- **How each accepted item is written.** Through the walk's existing
  post-consent write block (`curate.py:1315-1386`: reconciliation per the
  context flags, the commit-phase re-validation, `commit_merge`, and the
  per-item `_echo_commit_disclosure`). That keeps **per-merge commits**
  (#800). The block is extracted into one `_identity_write_one(ctx, prepared) -> bool`
  helper that the walk and the pre-pass both call, so the two paths cannot
  drift. A drift refusal keeps today's exit 3.
- **Confidential groups (the open conservative reading).** A group with any
  strict-blocked member is excluded from the recommended list and keeps its
  per-item prompt. This is implemented as written and is
  **owner-revisable**: if the owner later admits such groups to a
  human-confirmed list, the change is one argument (`blocked=frozenset()`
  for the selector) plus its tests.
- **What it reads.** Without `--auto-merge`, served in-class verdicts are
  not fresh, so they are not recommended; this change adds no default
  spend (see Open questions). Not added to `adjudicate --apply` or
  `--apply-same`.

### D11. `log.md` and commit shape

There is one git commit per run and one disclosure block per run. Each merge
keeps its own `log.md` bullet, because `unmerge` removes its merge's bullet
by exact text (`bundle/merge.py:273-288`). A single run bullet would break
that reversal, or would need a new unmerge path for "one bullet, many
merges". `application/digest.py` is not used: it renders one line per
commit and cannot name each merge's undo. That refinement of ADR-0034
decision 4 is stated in ADR-0049.

### D12. ADR-0049 content (written by slice 1, status Proposed)

Title: *ADR-0049: The structural base/-N Identity class merges without
prior consent under measured constants, one commit per run.* Follows
ADR-0034's "Outcome of the first measurement", which stays as written
(append-only). Its index row in `docs/adr/README.md` lands in the same
slice, because ADR status is checked in more than one place.

1. **The admitted class.** HIGH tier, exactly two members, one OKF type
   outside `ATTACH_EXCLUDED_TYPES`, a base/`-N` suffix family. Per group,
   it also requires:
   - no `cross_type_concern`;
   - no confidential or LLM-blocked member, judged with both escape hatches
     off;
   - no stacked-body guardrail refusal;
   - a fresh `same` verdict at confidence `>= 0.90`;
   - one merge per survivor per run.

   The survivor is the base id.
2. **The constants**, the D3 table verbatim: model `gemma4:26b-a4b`, its full
   digest, `t*` 0.90, the prompt hash, the full rubric digest at
   `63b5f551`, `context_window` 12288, `max_generation_tokens` 8192, and the
   verdict file. Changing any of them requires re-running the
   pre-registered harness.
3. **Ineligibility.** A different resolved adjudication model; a digest
   that is unknown, unlisted or different; a different rubric digest; a
   `context_window` or `max_generation_tokens` other than the measured
   value. An ineligible run says why, and the per-item flow stands.
4. **Fresh verdicts only.** A cached or queued verdict is never acted on,
   because neither records the model that produced it.
5. **The log-shape refinement of ADR-0034 decision 4.**
   - One git commit per run, and one disclosure block that names every
     merge with its `openkos unmerge <survivor>` plus the commit's
     `git revert` line.
   - Per-merge `log.md` bullets instead of one run entry, so `unmerge`'s
     exact-text reversal keeps working for each merge on its own.
   - ADR-0034's "one `log.md` run entry" is read as "one run's merges are
     recorded together and reversible one by one"; this ADR makes that
     reading explicit rather than silently departing from the letter.
6. **The commit phase holds the lock across the whole run, and model-free
   re-planning is allowed inside it.** This refines ADR-0036 Decision One.
   It applies only because separate commit phases would let a concurrent
   verb's autocommit sweep uncommitted merge writes into its own commit.
   No model call or prompt is ever made under the lock.
7. **Accept-recommended is admitted on its own bars** (A0-A4 PASS): in
   class, measured model, any confidence, human-confirmed in one answer,
   with per-merge commits (#800). Groups with confidential or LLM-blocked
   members are excluded, pending the owner's call.
8. **Never unattended.** It is never available in the daemon or the
   pending-work queue (ADR-0037). The per-run flag on a human-invoked
   `curate` is the human-facing act. The flag is off by default and has no
   standing key (ADR-0034 decision 2, ADR-0044).

The ADR also records these alternatives, all rejected: one run bullet in
`log.md`; per-merge commits for the auto pass; separate per-merge commit
phases; acting on cached verdicts; checking the prompt hash only.

### D13. Considered and rejected (design-level)

- Prepare every merge in compute and only re-validate under the lock:
  every merge after the first read-drifts by construction (D7).
- Per-merge commit phases with a final run commit: the concurrent-writer
  sweep described in D7.
- Exiting 1 immediately on a mid-pass failure, as the walk does: that skips
  the derived refresh for merges that were committed. The pass returns
  `failed` and the exit becomes 1 at the end instead.
- Excluding cross-source pairs, as `--apply-same` does by default
  (`lifecycle.py:3324`): the measured population includes them (the
  `key-cross-source-dup` positives, and cross-source homonym negatives), so
  excluding them would ship a narrower class than the one measured, with no
  safety evidence behind the narrowing.

## Data flow

```
probe:  find_candidates_report -> kept-distinct filter -> serve partition
        -> judging_partition(static eligibility) -> llm_calls  (cost line)

run:    serve partition --> run_eligibility --+-- ineligible: reason; TTY today's flow / non-TTY declined
                                              |
                                              +-- eligible: judging_partition (in-class fresh)
                                                     |
               adjudicate_candidates (no lock) <-----+
                     |
               persist (write-back) / reassemble / enqueue
                     |
               plan_auto_merges (pure gates 1-6)
                     |
       +-------------v--------------------------------------+
       | commit_section (lock held once)                    |
       |   per merge: judged digests -> prepare_one_merge   |
       |              -> guardrail -> snapshot -> merge_core|
       |   autocommit(union paths, one message)             |
       +-------------+--------------------------------------+
                     |
               disclosure (stdout) / skips (stderr)
                     |
        non-TTY: return      TTY: recommended pre-pass -> per-item walk (per-merge commits)
                     |
cli/main.py: summary -> derived refresh -> survivor-edit caveat -> exit 1 iff auto pass failed
```

## File changes

| File | Action | Slice | Description |
|---|---|---|---|
| `src/openkos/application/auto_merge.py` | Create | 1, 2, 4 | predicate, constants, eligibility (1); judging partition, planner, commit phase, survivor-edit check (2); recommended selector (4) |
| `evals/auto_merge/run_structural_class.py` | Modify | 1 | imports `in_structural_class` instead of defining it |
| `src/openkos/llm/base.py` | Modify | 1 | `InstalledModel.digest: str \| None = None` |
| `src/openkos/llm/ollama.py` | Modify | 1 | `list_models` parses `digest` |
| `docs/adr/0049-structural-identity-class-merges-under-measured-constants.md`, `docs/adr/README.md` | Create / Modify | 1 | ADR-0049 (Proposed) and its index row |
| `src/openkos/application/lifecycle.py` | Modify | 2 | extract `stacked_body_refused(prepared)`; `preview_apply_same` calls it |
| `src/openkos/cli/curate.py` | Modify | 3, 4 | context fields, probe partition, non-TTY exemption, pass wiring, disclosure (3); `_identity_write_one` extraction and pre-pass (4) |
| `src/openkos/cli/main.py` | Modify | 3 | `--auto-merge`, `--reconcile` refusal, caveat, end-of-run exit 1 |
| `docs/cli.md`, `CHANGELOG.md` | Modify | 5 | flag and the new Identity answer |
| `tests/unit/application/test_auto_merge.py` | Create | 1, 2, 4 | predicate, constants-vs-stamp, rubric pin, eligibility guards, gates, commit phase |
| `tests/unit/llm/test_ollama.py`, `tests/unit/llm/test_openai_compatible*.py` | Modify | 1 | digest parsed / `None` |
| `tests/unit/cli/test_curate_auto_merge.py` | Create | 3 | e2e and CLI contracts |
| `tests/unit/cli/test_daemon.py` | Modify | 3 | daemon pin |
| `tests/unit/cli/test_curate_accept_recommended.py` | Create | 4 | pre-pass |

`llm/openai_compatible.py` does not change: its `InstalledModel` construction
already omits the digest.

## Interfaces / contracts

```python
# src/openkos/application/auto_merge.py (signatures; bodies per D2-D10)
MEASURED_MODEL: Final = "gemma4:26b-a4b"
MEASURED_MODEL_DIGEST: Final = "001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c"
T_STAR: Final = 0.90
MEASURED_AT_COMMIT: Final = "63b5f551541f7e0e98c939331898f747e23f38da"
MEASURED_PROMPT_SHA256_16: Final = "aaed5c3e06569c83"
MEASURED_RUBRIC_DIGEST: Final = "sha256:..."  # computed in slice 1 from 63b5f551 (D4)
MEASURED_CONTEXT_WINDOW: Final = 12288
MEASURED_MAX_GENERATION_TOKENS: Final = 8192

def in_structural_class(group: CandidateGroup) -> bool: ...
def static_ineligibility(cfg: config.Config) -> tuple[str, ...]: ...
def model_digest_ineligibility(
    list_models: Callable[[], list[InstalledModel]], tag: str
) -> str | None: ...

@dataclass(frozen=True)
class RunEligibility:
    reasons: tuple[str, ...]
    @property
    def eligible(self) -> bool: ...

def run_eligibility(
    cfg: config.Config, list_models: Callable[[], list[InstalledModel]]
) -> RunEligibility: ...
def judging_partition(
    groups: Sequence[CandidateGroup],
    served_by_key: Mapping[str, AdjudicatedCandidate],
    to_judge: Sequence[CandidateGroup],
    *, auto_merge: bool, interactive: bool, statically_eligible: bool,
) -> tuple[dict[str, AdjudicatedCandidate], list[CandidateGroup]]: ...
def strict_blocked_members(bundle_dir: Path) -> frozenset[str]: ...

@dataclass(frozen=True)
class PlannedAutoMerge:
    result: AdjudicatedCandidate
    survivor: str
    absorbed: str

@dataclass(frozen=True)
class AutoMergeSkip:
    member_ids: tuple[str, ...]
    reason: str

@dataclass(frozen=True)
class AutoMergePlan:
    planned: tuple[PlannedAutoMerge, ...]
    skipped: tuple[AutoMergeSkip, ...]

def plan_auto_merges(
    results: Sequence[AdjudicatedCandidate], *,
    fresh_keys: frozenset[str], blocked: frozenset[str],
    cross_type_concern: Callable[[tuple[str, str]], str | None],
    ordered_pair: Callable[[tuple[str, ...]], tuple[str, str, str]],
) -> AutoMergePlan: ...

@dataclass(frozen=True)
class AutoMergeRecord:
    survivor: str
    absorbed: str
    confidence: float
    survivor_after_sha256: str

@dataclass(frozen=True)
class AutoMergeFailure:
    survivor: str
    absorbed: str
    error: str
    restored: bool
    unrestored_paths: tuple[str, ...]

@dataclass(frozen=True)
class AutoMergeOutcome:
    applied: tuple[AutoMergeRecord, ...]
    skipped: tuple[AutoMergeSkip, ...]   # gates 7-9
    sha: str | None
    failure: AutoMergeFailure | None

def apply_auto_merges(
    root: Path, layout: config.WorkspaceLayout, plan: AutoMergePlan, *,
    commit_section: Callable[[], AbstractContextManager[object]],
    autocommit: Callable[[Path, Sequence[str], str], str | None],
    judged_digests: Mapping[str, str | None],
    digest_of: Callable[[str], str | None],
) -> AutoMergeOutcome: ...
def survivors_edited_since(
    layout: config.WorkspaceLayout, records: Sequence[AutoMergeRecord]
) -> tuple[str, ...]: ...
def recommended(...) -> AutoMergePlan: ...  # slice 4, D10

# src/openkos/application/lifecycle.py
def stacked_body_refused(prepared: PreparedMerge) -> bool: ...

# src/openkos/llm/base.py
@dataclass(frozen=True, slots=True)
class InstalledModel:
    tag: str
    family: str | None
    digest: str | None = None
```

The exact types of `judged_digests`/`digest_of` follow
`application_pending.current_finding_digest`'s return type. Slice 2 matches
them to the real signature.

## Testing strategy

TDD is strict (project setting). The runner is `uv run pytest`, and the
gates are `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy .`, `uv run pytest --cov` (90% branch) and
`uv run python evals/run_self_tests.py`. Each production line is preceded
by an observed RED. Every guard test is written so that deleting or
inverting its guard alone turns it red (repo practice; a passing-first-try
test is mutated before it is trusted).

| Layer | What | Approach |
|---|---|---|
| Unit (slice 1) | predicate: each clause excludes on its own (LOW, ACRONYM, 3 members, Event, Person, cross-type, `-a/-b`, `-1/-2`, `-2a`, other directory) and both orders admit | hand-built `CandidateGroup`s, ported from the eval cases |
| Unit (slice 1) | constants equal the committed stamp; rubric constant equals `rubric_digest()`; prompt constant equals `prompt_hash(_SYSTEM_PROMPT)` | read the two gemma4 run files |
| Unit (slice 1) | run eligibility: one test per reason (model tag, `context_window`, `max_generation_tokens`, rubric, listing `BackendError`, tag unlisted, digest `None`, digest different), each from an all-valid baseline with one deviation; the all-valid baseline is eligible; static failure means `list_models` is never called | fake `Config` and a `list_models` spy |
| Unit (slice 1) | `list_models` parses the digest; missing or non-string digest gives `None`; openai-compatible gives `None` | existing urlopen stubs |
| Unit (slice 2) | gates 1-6, each the only failing fact: served verdict SAME at 1.0 not merged (gate 1); boundary `0.90` merges and `0.8999` does not (kills `>=` to `>`); a blocked member; a cross-type concern; two hand-built groups sharing a survivor (second skipped); a non-class group never appears in `skipped` | pure `plan_auto_merges` |
| Unit (slice 2) | gates 7-9 under the lock: a member edited after judging is skipped; an unresolvable member is skipped; a guardrail-crossing plan is skipped | tmp bundle, real `prepare_one_merge` |
| Unit (slice 2) | the commit phase is entered exactly once; one `autocommit` call whose paths are the de-duplicated union and whose message lists every merge; zero merges mean no call; the body is the stacked body (no reconciliation call) | spies on `commit_section`/`autocommit`; a chat stub that raises if called |
| Unit (slice 2) | mid-run failure: `merge_core` raises on merge 2 after a partial write, the tree equals the post-merge-1 state, the commit holds merge 1 only, `failure` names pair 2; a failing restore means no commit and the unrestored paths are listed | monkeypatch at the exact call target (repo memory: an unread monkeypatch fails silently, so assert the patch was hit) |
| Unit (slice 2) | `unmerge <survivor>` after an auto-merge restores byte parity and removes only that merge's `log.md` bullet, for each of two merges that share one commit | real `unmerge` core |
| CLI/e2e (slice 3) | non-TTY `curate --auto --auto-merge` on an in-class fixture (stub judge: `same` at 0.95 for the true pair, 0.85 for one, `different` for a homonym; plus a confidential pair and a non-class pair): exactly one new commit holding the eligible merge; disclosure lines carry ids and `openkos unmerge`; the others are unmerged; `typer.prompt` never called | `CliRunner`, real git repo with a pinned environment (`GIT_CONFIG_COUNT` identity, per repo memory) |
| CLI (slice 3) | `--auto-merge` alone on a non-TTY: declined, zero chat calls; `--reconcile --auto-merge`: exit 2, no workspace read; ineligible model on a non-TTY: declined with the reason, zero chat calls; TTY ineligible: reason line, then today's walk; the probe's `llm_calls` equals the stub's chat-call count for every case | `CliRunner`, chat-call counter |
| CLI (slice 3) | survivor-edit caveat printed when Metadata edits an auto-merged survivor in the same run, and absent otherwise; exit 1 after an injected pass failure, with the summary still printed | stage stub |
| CLI (slice 3) | daemon pin (behavioural and import guard) | D9 |
| CLI (slice 4) | recommended list shows only fresh in-class SAME groups (any confidence), excludes blocked, cross-type and guardrail-crossing groups; `y` commits each merge separately (N commits); `N` sends them to per-item prompts; never offered on a non-TTY; walk and pre-pass share `_identity_write_one` | `CliRunner` with scripted input |
| Eval | `run_structural_class.py --self-test` green against the moved predicate | `evals/run_self_tests.py` |

**TDD order per slice.**
- Slice 1: rubric verification (blocker gate); then predicate tests (RED by
  import error), then the move; then constants-vs-stamp; then the
  eligibility guards one by one; then the digest parse.
- Slice 2: gates 1-6; then the `stacked_body_refused` extraction (the
  existing `preview_apply_same` tests stay green); then the commit phase;
  then failure and restore; then unmerge parity.
- Slice 3: the `--reconcile` refusal; then the non-TTY no-prompt test; then
  the e2e; then the cost parity; then the caveat and the exit code; then the
  daemon pin.
- Slice 4: the selector, then the extraction (existing walk tests green),
  then the pre-pass.

## Threat matrix

The change alters VCS automation: one autocommit now holds several merges.

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A: no file is classified or executed; merges write only `.md` concept files, catalog files and the ledger sidecar, as today | none | none |
| Git repository selection | N/A: reuses `cli_main._autocommit` with the workspace root as cwd, unchanged; no new `git -C` or path composition | none | none |
| Commit state | Applicable | `autocommit` stages and commits only the explicit union pathspec (`vcs/git.py:650-669`, `git add -- <paths>` then `git commit -- <known>`). The deleted absorbed path is in the pathspec, as in `merge_commit_paths`; duplicates are removed; zero merges mean no commit call; the lock spans the writes and the commit | (a) a file the user staged before the run is not in the auto-merge commit and stays staged; (b) the absorbed files' deletions are in the commit; (c) duplicate paths (`index.md`, `log.md`, a document two merges rewrote) appear once; (d) zero eligible merges create no commit; (e) a degraded autocommit (no repo or no identity) prints the merge lines without a commit line |
| Push state | N/A: nothing pushes | none | none |
| PR commands | N/A: no PR automation | none | none |

## Migration / rollout

No migration. `InstalledModel.digest` is in-memory only; no schema,
file-format or configuration change. The feature is opt-in per run.
Reverting any slice's PR restores the previous behaviour. If the model,
digest or rubric moves later, the class becomes ineligible automatically and
says so until the harness is re-run.

### Slice plan (auto-chain, about 400 authored lines each)

| # | Slice | Est. | Owns | Done when |
|---|---|---|---|---|
| 1 | Seam: rubric verification, predicate move, constants, run eligibility, `InstalledModel.digest`, ADR-0049 and its index row | ~430 | `application/auto_merge.py` (new), `evals/auto_merge/run_structural_class.py`, `llm/base.py`, `llm/ollama.py`, `docs/adr/0049-*.md`, `docs/adr/README.md`, `tests/unit/application/test_auto_merge.py`, `tests/unit/llm/test_ollama.py`, `tests/unit/llm/test_openai_compatible*.py` | eval self-test green on the moved predicate; every eligibility guard killed; constants tied to the stamp; or **stopped at the blocker** if the rubric differs |
| 2 | Pass core: judging partition, planner gates 1-6, guardrail extraction, one-lock commit phase with gates 7-9, restore-on-failure, survivor-edit check | ~420 | `application/auto_merge.py`, `application/lifecycle.py`, `tests/unit/application/test_auto_merge.py` | every gate killed; one commit per run proven; unmerge parity per merge |
| 3 | CLI wiring: flag, `--reconcile` refusal, probe parity, non-TTY exemption, pass call, disclosure, caveat, exit code, daemon pin | ~400 | `cli/curate.py`, `cli/main.py`, `tests/unit/cli/test_curate_auto_merge.py`, `tests/unit/cli/test_daemon.py` | the non-TTY e2e merges the eligible pair in one commit, prints its undo, never prompts |
| 4 | Accept-recommended: selector, `_identity_write_one` extraction, pre-pass | ~340 | `application/auto_merge.py` (selector only), `cli/curate.py`, `tests/unit/cli/test_curate_accept_recommended.py` | offered only for fresh in-class SAME groups; per-merge commits |
| 5 | Docs: `docs/cli.md` entry, CHANGELOG | ~120 | `docs/cli.md`, `CHANGELOG.md` | docs match `--help` |

This adjusts the proposal in two ways:
- The ADR index row moves from slice 5 to slice 1, beside the ADR file
  (ADR status is checked in more than one place).
- Slice 2 owns the `lifecycle.py` guardrail extraction.

Slices 1 and 2 both touch `application/auto_merge.py` and its test file, and
slices 3 and 4 both touch `cli/curate.py`. Under auto-chain those are
sequential and never parallel.

## Open questions

- [ ] **Blocker check (slice 1):** does `rubric_digest()` at HEAD equal its
      value at `63b5f551`? Not verifiable in this phase without a shell. The
      prompt half is pinned equal at HEAD; the withdrawal-rule half is
      unverified. If it differs, the class is ineligible from day one and
      apply stops (D4).
- [ ] **Sampling pins (owner):** the measurement ran with no pinned
      `temperature` or `seed`. The proposal's ineligibility list names only
      `context_window` and `max_generation_tokens`, so a workspace that pins
      `temperature`/`seed` stays eligible as designed. Should either make
      the class ineligible? Recommendation: yes for `temperature` (it
      changes the verdict distribution the threshold was fitted on). It is
      one more static guard and one test.
- [ ] **Accept-recommended and confidential members (owner-revisable):**
      implemented as the conservative reading (excluded from the list,
      per-item prompt kept), D10.
- [ ] **Accept-recommended coverage (owner):** without `--auto-merge`, only
      in-class verdicts judged fresh in this run are recommended, so served
      verdicts are not, and default spend is unchanged. The alternative
      re-judges in-class groups on every eligible interactive run, so the
      list is always available, at a few extra model calls shown in the
      cost line.
- [ ] **Mid-pass failure scope (orchestrator to confirm):** D7 lets later
      stages run after an auto-pass write failure and exits 1 at the end.
      The stricter alternative stops the whole run immediately, as the walk
      does, at the cost of skipping the derived refresh for committed merges.
