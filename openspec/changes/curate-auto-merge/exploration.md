# Exploration: curate-auto-merge (#1298)

Refs #1298. The pre-registered measurement passed (#1303); verdict in
`evals/auto_merge/results/auto-merge-verdict-1298-20261005T181724Z-gemma4-26b-a4b.md`.
The ship rules in `evals/auto_merge/PREREGISTRATION-1298.md` are binding.

## Summary

`--auto-merge` and Identity "accept recommended" fit as a pass inside curate's
Identity stage, reusing the existing merge cores unchanged. Three things must
be closed before design: the log shape, where the verdict's model identity
comes from, and confidential members. Estimated about 1,900 authored lines in
4 slices.

## Where it plugs in

- Identity lives in the CLI layer, with no application service: `_identity_run`
  at `src/openkos/cli/curate.py:1057-1426`. It judges at 1161, walks the groups
  at 1198-1386 and commits at 1369.
- The non-TTY refusal at `curate.py:2610-2632` always declines Identity on a
  pipe. `--auto-merge` needs a scoped exemption, and the interactive remainder
  must never reach `typer.prompt` on a pipe.
- Merge reuse:
  - `ordered_merge_pair` (`lifecycle.py:2903`) and `prepare_one_merge`
    (`lifecycle.py:3005`).
  - `merge_core` (`lifecycle.py:626`) has no VCS side effect.
  - `reconcile_planned` (`lifecycle.py:3057`) produces the stacked body, with
    no LLM, under `no_reconcile`.
  - `ordered_merge_pair` uses `is_suffix_family` directly, not
    `canonical_family_member` as the pre-registration says. The behaviour is
    equivalent.
- The class predicate has no production twin. It exists only at
  `evals/auto_merge/run_structural_class.py:85-96`. Proposed seam: a new
  `src/openkos/application/auto_merge.py`, which must sit in `application/`
  because it needs `ATTACH_EXCLUDED_TYPES` (`application/ingest.py:121`). The
  eval would import it rather than keep a second copy.
- The daemon already cannot auto-merge: its identity stage only enqueues
  (`cli/daemon.py:253-283`, ADR-0037). That needs a pin test, not code.

## Gaps

- **Served verdicts carry no model.** The `adjudications` table
  (`state/adjudications.py:45-55`) and the queue payloads
  (`queue_producers.py:300-306`) store verdict, confidence and rationale only.
  `_serve_identity_rows` (`curate.py:972`) mixes cached and fresh verdicts, so
  a cached verdict from another model could be acted on.
- **The model digest is not in production.**
  - `InstalledModel` carries only `tag` and `family` (`llm/base.py:91-100`),
    and `ollama.list_models` drops the digest (`llm/ollama.py:641-690`).
  - Measured: digest `001e5daf…3ca8c`, prompt hash `aaed5c3e06569c83` (which
    covers `_SYSTEM_PROMPT` only), `context_window` 12288 and
    `max_generation_tokens` 8192.
  - The production `rubric_digest()` also covers the withdrawal markers, and
    the eval did not record it.
- **One commit per run is a new shape.** Identity commits per pair today
  (#800). `application/digest.py` is used only by the daemon and renders one
  line per commit, so it cannot list N merges.
- **Per-merge log bullets versus "one run entry".** Unmerge removes its own
  bullet by exact text (`bundle/merge.py:273-288`).
- **The stacked-body guardrail refusal** exists only in the batch path
  (`lifecycle.py:3372`), so auto-merge must add it.

## Approaches

1. **An auto-merge pass inside `_identity_run`, before the per-item walk.**
   Recommended. It reuses every core. Medium-high effort.
2. **Extract an `identity_service` first.** A large refactor of destructive
   code ahead of the feature. Better as a separate follow-up.
3. **A new `AutoMerge` stage.** Rejected: it breaks the stage-order invariant
   and adjudicates twice.

## Size and split (estimate)

1. The class predicate moves to `application/auto_merge.py`, the eval
   re-points to it, and `InstalledModel.digest` is added (about 440 lines).
2. The `--auto-merge` pass: the non-TTY exemption, one commit, the disclosure
   block and the log entry (about 800 lines; can split in two).
3. The accept-recommended pre-pass (about 340 lines).
4. Specs, `docs/cli.md`, CHANGELOG and the ADR (about 350 lines).

## ADRs and specs

- ADR-0034 governs. ADR-0044, ADR-0036/37/38 and ADR-0047 are satisfied as
  they stand.
- ADR-0034's "Outcome of the first measurement" says `--auto-merge` is not
  built. Accepted ADRs are append-only, so a new ADR is needed to record the
  admitted class, its constants and the log shape.
- Spec deltas:
  - `curate-command`: Identity Reuses Merge Cores, Accept-All (lines
    251-268), `review: false` (lines 341-354), and the cost gate.
  - `entity-resolution-merge`.
  - `entity-resolution-adjudication`.
  - `workspace-autocommit`.
  - Pin-only changes to `job-runtime` and `pending-work`.

## Open questions

1. Log shape: per-merge bullets plus a run disclosure, or one run bullet?
2. Verdict provenance: re-judge in-class groups fresh, or persist model tag and
   digest (a schema migration)?
3. Does `--auto-merge` imply `--auto` (spend consent)?
4. May a group with a confidential or LLM-blocked member auto-merge?
5. Mid-run failure: commit what landed and stop, or leave the tree dirty?
6. Does a workspace overriding `context_window` or `max_generation_tokens`
   make the class ineligible?
7. `--reconcile` together with `--auto-merge`: refuse, or apply it only to the
   interactive remainder?
8. Accept-recommended scope: curate only, or also `adjudicate --apply`?
9. Accept-recommended commits: per merge (#800) or batched?
10. Undo line: name the `--discard-survivor-edits` caveat in the disclosure?
11. A new ADR, or an amendment to ADR-0034?

## Risks

- `merge_core` is not transactional across merges, so a mid-run failure leaves
  uncommitted writes.
- A union-paths commit can sweep in a concurrent writer's `index.md` and
  `log.md` edits.
- The candidate scan is capped at 50 groups, and an in-class pair can sit
  inside a group of 3 or more, which is out of class.
- The eligibility conjunction has many guards that look redundant; each needs
  a test that kills its mutation.
