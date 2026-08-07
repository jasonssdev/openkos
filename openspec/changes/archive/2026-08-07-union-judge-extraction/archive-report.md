# Archive Report — `union-judge-extraction`

**Archived**: 2026-08-07. **Delivered**: PR #458, squash commit `04e05e7` on
`main`. **Closes**: #456. **Follow-up**: #457 (open, design/P3 — adjudicate
AMI subject-level ground truth before tuning judge selectivity; carries the
title-only reply-protocol disambiguation deferral).

## What shipped

Union-of-2-runs + selector-judge extraction replaces the blind positional
cap: two same-prompt `_extract_once` runs below the chunk threshold, per-run
twin-drop, richer-body union merge, a 24-candidate pre-judge ceiling
(reported via `pre_judge_dropped` and a dedicated stderr notice), the
`extraction/judge.py` selector leaf over the closed candidate list
(normalized-title admission per design D4, deterministic `Procedure`
re-admission, empty-admission floor `judge_status="empty"`, judge skip on an
empty union), and a backstop cap of 12 applied exactly once, last. Chunked
sources keep single fan-out with judge-only selection. Default ON behind the
`union_judge` config flag; `extract_concept` unchanged as the single-run
primitive.

## Final state at close

- Suite 3843 passed; `ruff check`, `ruff format --check`, `mypy .` all clean.
- Measurement gate PASS (maintainer-closed): prose post-cap recall
  0.84 / 0.97 / 0.80 vs baselines 0.71 / 0.88 / 0.73 with cap_cost 0.00 on
  all three adjudicated fixtures; AMI type coverage held, with
  `TS3005a.transcript` reaching `Concept`/`Procedure`/`Project` for the
  first time. Recorded in `evals/extraction_cap/report.md` and
  `evals/decision_extraction/report.md`.
- Two post-verify work units are part of the merged commit and are NOT in
  `verify-report.md`'s snapshot: (1) the Gentle AI 4R review's bounded
  correction (164 of 180 declared lines; normalized judge matching, empty-
  union skip, `_pre_judge_ceiling_notice`, pinned same-title/different-type
  bound; 4 new tests), and (2) a ruff-format normalization of 8 files.
- Review receipts: `review-72ffb6302c4e94b3` (4R + bounded correction,
  approved) and `review-53a99e83b2d47c80` (format-only candidate, approved);
  pre-commit/pre-push/pre-pr gates all `allow`.

## Spec merges performed at archive

- New capability spec: `openspec/specs/extraction-union-judge/spec.md`.
- `openspec/specs/ingestion/spec.md`: staging requirement reconciled to the
  post-judge backstop of 12 (the pre-existing drift — spec said 5, code
  enforced 6 — is recorded in its "Previously" note), chunking acknowledged,
  judge-failure stderr notice added, eval regression gate added. Two stale
  "hard cap of 5" prose occurrences (overview and requirement preamble)
  were reconciled by the orchestrator after the archive executor's merge
  left them behind.

## The defects the gates caught (why this cycle's shape earned its cost)

1. The phase-5 measurement gate caught the empty-admission defect (valid
   judge selection admitting zero → `[]` with `judge_status="ok"`) that
   3837 green unit tests missed; fixed in-change with a deterministic floor.
2. The 4R review caught raw-equality judge matching (design D4 violation)
   and the type-blind same-title admission — also invisible to the suite.
