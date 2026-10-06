# Proposal: curate-auto-merge — apply the measured structural Identity class without per-item consent, and offer "accept recommended"

Refs #1298. Governed by ADR-0034 and ADR-0044; records its decisions in a
new ADR-0049. The ship rules in `evals/auto_merge/PREREGISTRATION-1298.md`
("The class", "Ship rule (if PASS)", "Identity 'accept recommended'",
"Decisions") are binding. Measurement: PASS on both rules
(`evals/auto_merge/results/auto-merge-verdict-1298-20261005T181724Z-gemma4-26b-a4b.md`).

## Intent

Duplicates from pre-attach bundles, workspaces with `attach_at_ingest: false`,
and unreadable attach targets still reach curate's Identity stage as one
`[y/N]` prompt per group, and #702 forbids bulk consent there. ADR-0034 lets
exactly one class skip prior consent once it passes a pre-registered
measurement; ADR-0044 kept the structural base/`-N` class opt-in. That class
has now passed (R2 0 of 240 false auto-merges, S2 0 of 90, R4 0.97, L1
1.20x; `t*` = 0.90, `gemma4:26b-a4b`), and accept-recommended passed its own
bars (A2 0 of 480, A4 0.98).

This change ships what the measurement admits and nothing wider:

1. `openkos curate --auto-merge`: a per-run, off-by-default flag that applies
   in-class `same` merges at confidence `>= t*` without a prompt, mechanically,
   in one commit, with a disclosure naming every merge and its undo.
2. Identity "accept recommended": one answer that accepts the in-class groups
   the measured judge calls `same` (any confidence), shown with survivor,
   absorbed and undo, while every other group keeps its per-item prompt.

Success: a pre-attach bundle's measured-safe duplicates clear unattended
(`curate --auto --auto-merge` on a non-TTY), every merge is reversible with
`openkos unmerge <survivor>`, and nothing outside the class is ever merged
without a per-item answer.

## Scope

### In Scope

- **Class predicate in production.** Move `in_structural_class` from
  `evals/auto_merge/run_structural_class.py` to a new
  `src/openkos/application/auto_merge.py` (it needs `ATTACH_EXCLUDED_TYPES`,
  so it belongs in `application/`). The eval imports it; one copy only. Its
  self-test and mutation guards keep passing.
- **Measured constants as code constants** in the same module: model tag
  `gemma4:26b-a4b`, its full digest (read from the committed run files'
  harness stamp), `t*` = 0.90, the measured prompt/rubric identity, and the
  measured `context_window` 12288 / `max_generation_tokens` 8192.
- **Model digest in production.** Add `digest` to `InstalledModel`
  (`llm/base.py`) and populate it in `ollama.list_models`; a backend that
  reports no digest makes the class ineligible.
- **Run eligibility.** The class is ineligible for the run when the resolved
  adjudication model differs from the measured tag, the digest differs or is
  unknown, the prompt/rubric identity differs, or the workspace overrides
  `context_window` or `max_generation_tokens` away from the measured values.
  An ineligible run prints why; it is never a silent no-op. Interactive
  Identity then proceeds as today.
- **Per-group eligibility** (all must hold): in class; `cross_type_concern`
  is `None`; no confidential or LLM-blocked member; the stacked-body
  guardrail does not refuse the plan (added to this path; today it exists
  only in the batch path, `lifecycle.py:3372`); verdict `same` at
  confidence `>= t*` from a fresh judgment in this run; the survivor has not
  already been auto-merged in this run.
- **Fresh verdicts only.** In-class groups are re-judged in this run with the
  measured model; a cached (`adjudications` table) or queued verdict is never
  acted on. No schema migration.
- **Mechanical merges.** Survivor = base id (`ordered_merge_pair`); stacked
  body with `--no-reconcile` semantics, no LLM rewrite; reuse `prepare_one_merge`,
  `merge_core` and `reconcile_planned` unchanged.
- **Commit, log and disclosure.** All automatic merges of a run land in one
  git commit (ADR-0034 decision 4). Each merge keeps its own existing
  `log.md` bullet so `unmerge` still removes it by exact text. The run prints
  a disclosure block: one line per merge with survivor, absorbed, confidence
  and `openkos unmerge <survivor>`, beside the commit's sha and `git revert`
  line (Quiet Engine B4 matching rule kept), plus the
  `--discard-survivor-edits` caveat when a later curate stage in the same run
  edited a survivor.
- **Mid-run failure.** Commit the merges that already landed, stop the
  auto-merge pass, and report what was applied and what was not.
- **Flag semantics.**
  - `--auto-merge` does not imply `--auto`. Unattended use is
    `--auto --auto-merge`. With `--auto-merge` alone the normal cost gate
    still prompts on a TTY and refuses on a non-TTY, as today.
  - On a non-TTY, `--auto-merge` gets a scoped exemption from the Identity
    non-TTY refusal (`curate.py:2610-2632`) for the automatic pass only; the
    interactive remainder never reaches `typer.prompt` on a pipe.
  - `--reconcile` together with `--auto-merge` is refused.
  - No configuration key enables it; `review: false` and `--accept identity`
    keep their current meaning.
- **Accept recommended** in curate's Identity step only: offered only for
  in-class groups judged `same` (any confidence) by the measured model, under
  the same run eligibility; the list shows survivor, absorbed and undo per
  item; merges keep per-merge commits (#800). Not added to
  `adjudicate --apply` or `--apply-same`.
- **Daemon pin.** The daemon never auto-merges (already structural,
  `cli/daemon.py:253-283`, ADR-0037); add a test that pins it.
- **ADR-0049** recording the admitted class, its constants, the ineligibility
  rules and the log shape (per-merge bullets plus one run commit and a
  disclosure), as the follow-on to ADR-0034's "Outcome of the first
  measurement".
- `docs/cli.md` entry for the flag and the new Identity answer; CHANGELOG.

### Out of Scope

- Default-on auto-merge or any standing configuration key (needs an ADR
  superseding ADR-0034, backed by evidence from use; ADR-0044).
- Event and Person families, families of three or more, `-a`/`-b` and
  `-1`/`-2` shapes, and cross-type pairs (out of class).
- `qwen3:8b` or any other model (decision 4 of the pre-registration).
- Auto-merge in the daemon or the pending-work queue.
- Persisting model tag/digest on cached verdicts (a schema migration).
- Extracting an `identity_service` from `_identity_run` (worth doing; a
  separate refactor).
- Accept-recommended in `adjudicate`.
- The Quiet Engine G1' amendment (a later measurement's pre-registration).

## Capabilities

### New Capabilities

- `identity-auto-merge`: the structural class predicate, the measured
  constants, run and per-group eligibility, ineligibility reporting, fresh
  verdict provenance, mechanical merge semantics, one-merge-per-survivor,
  one-commit-per-run, the disclosure block and mid-run failure semantics.

### Modified Capabilities

- `curate-command`: the `--auto-merge` flag and its relation to `--auto`,
  the cost gate and `--reconcile`; the scoped non-TTY exemption; the
  automatic pass inside "Identity Stage Reuses Merge Cores"; the
  accept-recommended answer; exceptions noted in "Per-Stage Accept-All Is
  Opt-In And Never Covers Identity" and "`review: false` Accepts Only The
  Non-Destructive Stages" (both stay true for every non-class group).
- `entity-resolution-adjudication`: "Persisted Verdicts Are Served Before
  Re-Judging" gains the exception that the auto-merge pass re-judges
  in-class groups fresh.
- `entity-resolution-merge`: the stacked-body guardrail refusal reaches the
  automatic path; per-merge log bullets keep `unmerge` exact-text reversal
  when merges share one commit.
- `workspace-autocommit`: one commit for all automatic merges of a run, as an
  exception to curate Identity's per-merge commits (#800), with commit
  disclosure.
- `llm-client`: "List Installed Models" carries the model digest.
- `job-runtime`: pin that the maintenance pass never auto-merges.

## Approach

Approach 1 from the exploration: an automatic pass inside `_identity_run`
(`cli/curate.py`), run before the per-item walk, with the decision logic in
the new `application/auto_merge.py` so the class, constants and eligibility
are testable without the CLI. The pass filters candidate groups by the class
predicate, checks run eligibility once, re-judges the in-class groups fresh,
applies the per-group gates, executes eligible merges through the existing
merge cores with no reconcile, commits once, and prints the disclosure. Groups
it does not merge fall through to the normal per-item walk (or, on a non-TTY,
are left as today). Accept-recommended is a pre-pass in the same walk over the
remaining in-class `same` groups. The daemon path is untouched.

Rejected: extracting an Identity service first (large refactor of destructive
code ahead of the feature), and a separate `AutoMerge` stage (breaks the stage
order invariant and adjudicates twice).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/application/auto_merge.py` | New | class predicate, measured constants, eligibility, pass execution |
| `evals/auto_merge/run_structural_class.py` | Modified | imports the predicate instead of defining it |
| `src/openkos/llm/base.py`, `src/openkos/llm/ollama.py` | Modified | `InstalledModel.digest`, parsed from `/api/tags` |
| `src/openkos/llm/openai_compatible.py` | Modified (likely) | constructs `InstalledModel` with no digest |
| `src/openkos/cli/curate.py` | Modified | flag, non-TTY exemption, `--reconcile` refusal, automatic pass, disclosure, accept-recommended |
| `src/openkos/application/lifecycle.py` | Modified (small) | stacked-body guardrail reachable from the automatic path |
| `tests/unit/` | New/Modified | predicate mutation guards, eligibility guards, pass e2e, daemon pin |
| `docs/adr/0049-*.md`, `docs/adr/README.md` | New/Modified | ADR-0049 and index row |
| `docs/cli.md`, `CHANGELOG.md` | Modified | flag and new answer |

## Principles Impact

- **Human curates, engine maintains**: consent moves from before to after
  only for the measured class, opt-in per run; announced, committed, logged
  and reversible (ADR-0034).
- **Local-first & private**: no confidential or LLM-blocked member is ever
  auto-merged; the measured model runs locally.
- **Reconstructible / immutable raw**: unchanged; merges write canonical
  files through existing cores; `raw/` is untouched.
- **Provenance**: merge ledger and provenance rewiring are reused unchanged.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| A wrong automatic merge sits in the bundle until undone | Low (0 of 240 measured) | per-merge undo line; one merge per survivor per run; mechanical body so `unmerge` restores byte parity |
| The production rubric identity at HEAD differs from the measured one (the eval recorded `_SYSTEM_PROMPT` hash only, not the withdrawal markers in `rubric_digest()`) | Med | design pins the identity from measured commit `63b5f551`; if HEAD differs the class is ineligible and says so, rather than shipping an unmeasured rubric |
| `merge_core` is not transactional across merges; a failure leaves uncommitted writes | Med | commit what landed, stop, report (decision 5) |
| One union-paths commit sweeps a concurrent writer's `index.md`/`log.md` edits | Low | short commit phase under the workspace lock, as other curate commits |
| An in-class pair inside a 3+ group, or beyond the 50-group candidate cap, is never seen | Med | a 3+ group is out of class by design and keeps its prompt; a group beyond the cap is reached by a later run, and the existing cap disclosure stays |
| Many eligibility guards look redundant and a mutation survives | Med | one test per guard that kills its mutation (repo practice) |
| ADR-0034 decision 4 says "one `log.md` run entry"; per-merge bullets depart from its letter | Med | ADR-0049 states the refinement and its reason (`unmerge` exact-text reversal) explicitly |
| A one-answer accept-recommended reads as bulk consent | Low | offered only in class and measured model; list shows each item with undo; A0–A4 PASS |

## Rollback Plan

Each slice is a separate PR and reverts cleanly; the feature is opt-in per
run, so no workspace changes behavior until someone passes `--auto-merge` or
picks the new answer. No schema or file-format change is introduced
(`InstalledModel.digest` is in-memory). Merges already applied by a user are
reversed with `openkos unmerge <survivor>` (named in the disclosure) or
`git revert <sha>` for the whole run. If the measured model, digest or rubric
changes later, the class becomes ineligible automatically until the harness
is re-run.

## Dependencies

- The committed run files' harness stamp for the full model digest and
  prompt identity (`evals/auto_merge/results/runs-structural-*-gemma4-26b-a4b.json`).
- Shipped cores: `ordered_merge_pair`, `prepare_one_merge`, `merge_core`,
  `reconcile_planned`, `is_suffix_family`, `adjudicate_candidates`, the
  stacked-body guardrail, `unmerge`.

## Slice Plan (auto-chain, about 400 authored lines each)

| # | Slice | Est. lines | Done when |
|---|---|---|---|
| 1 | Seam: `application/auto_merge.py` with the predicate and measured constants; eval imports it; `InstalledModel.digest`; ADR-0049 (Proposed) | ~440 | eval self-test green against the moved predicate; digest parsed and tested |
| 2 | Automatic pass core: run and per-group eligibility with ineligibility reasons, fresh re-judge, guardrail, one-per-survivor, mechanical merges, one commit, failure semantics (application-level, no CLI) | ~400 | each guard has a mutation-killing test; one commit per run proven |
| 3 | CLI wiring: `--auto-merge`, `--auto` separation and cost gate, non-TTY exemption, `--reconcile` refusal, disclosure block, daemon pin test | ~400 | non-TTY `curate --auto --auto-merge` e2e merges an in-class fixture and prints the undo lines |
| 4 | Accept-recommended pre-pass in Identity | ~340 | offered only for in-class measured-model `same` groups; per-merge commits kept |
| 5 | `docs/cli.md`, CHANGELOG, ADR index row | ~150 | docs match `--help` |

Spec deltas live in this change folder and merge at archive.

## Success Criteria

- [ ] `curate --auto --auto-merge` on a non-TTY merges every eligible
      in-class pair of a fixture in one commit, prints one disclosure line
      per merge with `openkos unmerge <survivor>`, and leaves every other
      Identity group unmerged.
- [ ] `openkos unmerge <survivor>` on each auto-merged pair restores byte
      parity and removes that merge's own `log.md` bullet.
- [ ] A run with a different adjudication model, digest, rubric identity or
      overridden `context_window`/`max_generation_tokens` merges nothing and
      prints the ineligibility reason.
- [ ] Groups with a confidential or LLM-blocked member, a cross-type concern,
      a guardrail refusal, confidence below 0.90, or a second merge onto the
      same survivor are never auto-merged.
- [ ] `--auto-merge` without `--auto` still hits the cost gate;
      `--reconcile --auto-merge` is refused.
- [ ] The daemon pin test proves the maintenance pass never merges.
- [ ] The eval's `--self-test` passes against the production predicate.
- [ ] Ruff, format check, MyPy, `pytest --cov` (90% branch) and eval
      self-tests are green.

## Decisions (resolved; recorded here, not reopened)

- **Owner, 2026-10-05:** `--auto-merge` does not imply `--auto`; spend consent
  stays separate. `--auto-merge` alone keeps the normal cost gate (prompt on a
  TTY, refusal on a non-TTY).
- **Orchestrator defaults (no owner objection):**
  1. Never auto-merge a group with any confidential or LLM-blocked member.
  2. Re-judge in-class groups fresh with the measured model; never act on a
     cached or queued verdict; no schema migration.
  3. Model tag, digest, `t*` = 0.90 and prompt/rubric identity are code
     constants; differing model, digest, or `context_window`/
     `max_generation_tokens` overrides make the class ineligible, with a
     stated reason.
  4. `--reconcile` with `--auto-merge` is refused.
  5. Mid-run failure: commit what landed, stop, report.
  6. Per-merge `log.md` bullets; a run disclosure block with undo lines and
     the `--discard-survivor-edits` caveat; one git commit per run.
  7. At most one automatic merge per survivor per run; survivor is the base
     id; stacked body, no LLM rewrite; stacked-body guardrail applies.
  8. The predicate moves to `application/auto_merge.py`; the eval imports it.
  9. The daemon never auto-merges; pinned by a test.
  10. Accept-recommended in curate Identity only, in-class and measured model
      only, per-merge commits (#800).
  11. New ADR-0049 records the class, constants and log shape.

## Open Questions (for the orchestrator)

1. **Accept-recommended and confidential members.** Decision 1 excludes
   confidential or LLM-blocked members from *automatic* merges. Should the
   same exclusion apply to the accept-recommended list (which is
   human-confirmed), or may such a group appear there when it has a measured
   verdict? Conservative reading pending an answer: exclude it from the
   recommended set and keep its per-item prompt.

Design note (technical, left to `sdd-design`): whether the fresh in-class
verdicts are written back to the `adjudications` cache like other Identity
judgments. It changes what later prompts and `adjudicate` display, never what
auto-merge acts on.
