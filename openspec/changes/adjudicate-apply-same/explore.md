# Exploration: `adjudicate --apply-same` (issue #137, part 3 — guarded batch)

Final slice of issue #137. Parts already shipped and archived: `adjudicate --json`
(Slice 2a, #161), merge-core-extraction (Slice 2b-i, #163), `adjudicate --apply`
interactive per-pair walk (Slice 2b-ii, #165). This slice adds the guarded batch
`--apply-same`, previously deferred pending #138 (verdict quality).

## Post-#147 adjudication signals (the key question for the batch guardrail)

`#138` was resolved by change **#147** (commit 6d06d2c). **#147's fix is
prompt-only.**

- `src/openkos/resolution/adjudication.py` — `AdjudicatedCandidate` is UNCHANGED:
  still `candidate`, `verdict`, `confidence: float`, `rationale` (lines 82-96). No
  new part-whole / relationship-shape field, no calibration. `_coerce_confidence`
  (172-186) is still a raw clamp of the LLM-reported value.
- `#147` changed only `_SYSTEM_PROMPT` (46-63): the LLM is now instructed to treat
  PART/COMPONENT/ASPECT/SUBTYPE/INSTANCE relationships as DIFFERENT and to prefer
  DIFFERENT/UNCERTAIN over SAME when unsure. Pinned as system-message TEXT
  assertions in `tests/unit/resolution/test_adjudication.py:426-444`, not a field.
- `src/openkos/cli/main.py:4179-4181` / `4314-4317` still say, present tense, that
  the local model returns a flat, uncalibrated confidence "kept for future
  thresholding."

**Consequence:** the prior exploration's claim (Engram 1859) HOLDS. The only
machine-checkable guardrail for a batch remains `verdict == Verdict.SAME` plus
2-member group size. #147 improved verdict *quality* behaviorally but added no new
programmatic exclusion criterion. A `--min-confidence` guardrail would give false
safety today.

## Affected areas

- `src/openkos/cli/main.py:516-631` (`_run_adjudicate_apply`) — the shipped
  per-pair loop body: skip logic, `_resolve_concept_path`, `prepare_merge` /
  `merge_core` / `_autocommit`, ledger commits. Reuse verbatim.
- `src/openkos/cli/main.py:4130-4321` (`adjudicate` command) — flag wiring +
  mutual-exclusion pattern to extend for `--apply-same`.
- `src/openkos/cli/main.py:2658-2811` (`PreparedMerge`, `MergeResult`,
  `prepare_merge`, `merge_core`) — reused verbatim, no changes.
- `src/openkos/cli/main.py:3086` (`unmerge`) — LIFO-per-survivor; batch undo is N
  sequential calls, no batch-level undo command exists.
- `tests/unit/cli/test_adjudicate.py:1007-1600` — ~30 existing `--apply` tests to
  mirror (monkeypatch `adjudicate_candidates`, fake SAME verdicts, snapshot/ledger
  assertions).

## Approach

Add `_run_adjudicate_apply_same` reusing `prepare_merge`/`merge_core`/`_autocommit`
verbatim, restructured as: build + print a full aggregate preview of every eligible
SAME 2-member pair → one explicit batch confirmation (not a bare `[y/N]`, per the
issue's "must not blindly merge") → sequential execute with the same
mid-run-failure-stops-but-keeps-prior-commits semantics as `--apply`. N>2 HIGH
groups skipped identically to `--apply`. `--apply-same` mutually exclusive with
`--apply` and `--json`.

## Open guardrail decisions for the maintainer

1. Confidence still cannot exclude anything — a `--min-confidence` flag would be a
   NEW decision, not something #147 unlocked, and would provide false safety.
2. Confirmation shape (typed count / "APPLY" vs bare y/N) — no existing
   aggregate-preview UX to copy; needs explicit sign-off.
3. Batch-size cap — none exists; whether to add one is open.
4. Batch-scale reversibility — real, but N sequential LIFO `unmerge` calls, no batch
   undo command. Document, don't silently assume.
5. N>2 handling and flag mutual-exclusion error codes — likely mirror `--apply`,
   confirm explicitly.

## Sizing

~350-550 changed lines total (production ~150-200, tests ~200-350). Fits one PR
under the 800-line budget.

## Ready for proposal

Yes. The one genuine maintainer decision is the batch confirmation shape (#2); the
rest have safe defaults mirroring the shipped `--apply`.
