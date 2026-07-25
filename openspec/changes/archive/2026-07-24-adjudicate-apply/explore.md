# Exploration: adjudicate-apply (#137, Slice 2b-ii)

Interactive apply: `adjudicate --apply` walks SAME verdicts and, per eligible
group, prompts `Merge <absorbed> into <survivor>? [y/N/skip]`, executing accepted
merges via the extracted core. Final slice of #137's "path to merge".

Full 2b exploration in Engram `sdd/adjudicate-apply/explore`. Prerequisite 2b-i
(merge-core-extraction) shipped in #163: `prepare_merge`/`merge_core` in
`src/openkos/cli/main.py` are callable and non-interactive.

## Maintainer product decisions (LOCKED — Engram decision #1877)
1. **Survivor/absorbed**: ALPHABETICAL-FIRST survives. `member_ids` is sorted
   ascending; first = survivor, the rest = absorbed. Zero I/O, deterministic. The
   prompt shows `Merge <absorbed> into <survivor>?`; to reverse direction the user
   skips and merges manually.
2. **N>2 groups**: SKIP in v1 with a message (`skipped (N>2, merge manually)`).
   `--apply` handles only exactly-2-member groups. Avoids the unmerge LIFO risk.
3. **Prompt `y/N/skip`**: empty input = N (don't merge, continue); `skip` = same
   as N for now (don't merge, continue). Only `y` merges.

## Building blocks (from 2b-i, on main)
- `prepare_merge(...) -> PreparedMerge` (Phase A: reads + plan + preview data; no
  writes; raises OSError/ValueError).
- `merge_core(prepared) -> MergeResult` (Phase B: writes + ledger; no VCS side
  effect; raises).
- The `merge` command's own flow (`gate → preview → confirm → prepare/core →
  autocommit`) is the reference; `--apply` reuses prepare_merge/merge_core.
- `adjudicate` already computes `results` (AdjudicatedCandidate list) and has the
  `--json`/`--same-only` flags (Slices 2a/1). `--apply` is another mode.

## Flow for `adjudicate --apply`
1. Run the normal adjudication (find_candidates → adjudicate_candidates → results),
   reusing the existing Ollama error handling.
2. Filter to eligible: `verdict == SAME` AND exactly 2 members.
   - Non-SAME verdicts: never applied.
   - SAME groups with >2 members: print `skipped (N>2, merge manually)`, do not prompt.
3. For each eligible group, in `results` order: survivor = member_ids[0], absorbed
   = member_ids[1]. Call `prepare_merge` to compute/preview what will fuse, print a
   concise preview, then `typer.confirm`/prompt `Merge <absorbed> into <survivor>?
   [y/N/skip]`. On `y`: `merge_core(prepared)` then commit (via `_autocommit`, once
   per applied merge or batched — design decides). On N/skip/empty: continue.
4. End with a summary tally: `applied X, skipped Y (Z groups N>2)`.

## Guardrails / safety
- Only SAME + 2-member groups are ever merged. Destructive but reversible via
  `unmerge` (same `merged_from` ledger).
- `--apply` must surface exactly what it will fuse BEFORE doing it (per issue #137).
- #138 (uncalibrated confidence) is mitigated by the interactive human-in-the-loop.
- Mutual exclusivity: `--apply` with `--json` should be rejected (or `--json` wins
  as non-interactive) — design to decide; `--apply` is interactive, `--json` is
  machine output, so combining them is contradictory → error out.

## Open design questions (for design phase)
- Commit granularity: `_autocommit` per applied merge vs one commit at the end.
  Per-merge keeps each reversible independently and matches single-`merge` UX;
  design to lock (per-merge recommended for unmerge granularity).
- Interaction with `--same-only`: `--apply` implies acting on SAME only; `--apply`
  + `--same-only` is redundant but harmless (design decides — likely just ignore
  or treat as no-op since apply is inherently SAME-only).
- Prompt mechanism: `typer.confirm` is y/N only; `skip` as a third option needs a
  custom prompt (`typer.prompt` with parsing) — design/tasks to specify.
- Re-adjudication safety: after a merge, subsequent groups' member ids might now
  reference an absorbed id. In v1 (2-member groups, results computed once up
  front) assess whether an earlier merge can invalidate a later group's ids; if
  so, skip-with-message when a member no longer exists.

## Risks
- Destructive command — the strongest guardrails and clearest preview matter most.
- Stale ids across sequential merges within one `--apply` run (see above).
- Prompt parsing (y/N/skip + empty) needs its own tests (no existing per-item
  prompt precedent; `suggest-relations` does one aggregate confirm).
