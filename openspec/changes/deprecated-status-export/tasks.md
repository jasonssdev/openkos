# Tasks: deprecated-status-export — write `status: deprecated` on a superseded concept for OKF v0.2 consumers

Refs #1075. Design: `design.md`. Proposal: `proposal.md`. ADR-0032 is
written, status `Proposed`, with its `docs/adr/README.md` row, in the
planning commit; no task below edits it except archive's status flip.

Strict TDD is ON, runner `uv run pytest`. Every behavioral task pairs a
`[TEST]` (observed RED, with the reason it is RED today) with the `[IMPL]`
that turns it GREEN, in that order. A `[TEST]` that is GREEN on its first
run proves nothing until a `[MUT]` task breaks the exact line it guards and
observes it go RED; revert every mutation with the inverse edit (never
`git checkout --`), and purge `__pycache__` before trusting any verdict.
`[PRE]` tasks assert an absence the phase relies on BEFORE writing code.
All fixtures run in `tmp_path` from real verb calls; no test reaches an LLM.

Gate per phase: `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy .`, `uv run pytest --cov` (run directly, unpiped).

Each phase is one PR-sized slice (≤ ~400 authored changed lines, advisory),
stacked in order; the chain strategy is the orchestrator's call.

## Review Workload Forecast

| Phase | Slice | Est. lines | Risk |
|---|---|---|---|
| 1 | Projection, marker, read-back rule, superseded set | ~300 | high (touches the retrieval predicate) |
| 2 | `lint` drift scan + `repair` fixer | ~380 | medium |
| 3 | `reconcile --winner` + `relate supersedes` writers | ~300 | medium |
| 4 | `forget` + `purge` withdrawals | ~350 | high (purge is irreversible) |
| 5 | `merge` + `unmerge` projection | ~350 | high (round-trip parity) |
| 6 | Docs + shipped example | ~60 | passive |

## Phase 1 — Projection and the never-read-back rule (`okf`, no writer yet)

- [x] 1.1 [PRE] Assert no reader of raw `status` exists outside
  `model/okf.py`: `grep -rn '"status"' src/openkos` shows only
  `okf.py` hits and `judge_status`/`extraction_status`-family names. Record
  the output in the PR. Also assert `status_derived_from` appears nowhere
  in `src/` or `examples/` today.
- [x] 1.2 [TEST] `tests/unit/test_okf_status_export.py`: one test per row of
  the spec table (`project_deprecation_export`) — EXPORT from absent,
  `stable`, `active`; UNCHANGED for superseded `deprecated` with and
  without marker; BLOCKED for `draft` and for an unknown value (asserting
  `blocked_value`); WITHDRAW; UNCHANGED for unmarked live `deprecated`;
  DROP-MARKER for invalid marker beside `draft` and for marker value
  `manual`. RED: module attribute does not exist.
- [x] 1.3 [IMPL] Add `STATUS_DERIVED_FROM_KEY`, `has_valid_export_marker`,
  `ExportOutcome`, `ExportDecision`, `project_deprecation_export` to
  `model/okf.py`. GREEN 1.2.
- [x] 1.4 [TEST] Idempotence sweep: for every row × both `superseded`
  values, re-projecting the result yields UNCHANGED or BLOCKED; every
  non-status key and the body survive byte-for-byte through
  `apply_deprecation_export`; UNCHANGED/BLOCKED return the SAME input text
  object (no re-serialization). RED: `apply_deprecation_export` missing.
- [x] 1.5 [IMPL] `apply_deprecation_export`. GREEN 1.4.
- [x] 1.6 [TEST] `declares_deprecated` returns `False` for
  `status: deprecated` + valid marker, `True` for unmarked `deprecated` and
  for `deprecated` + marker value `manual`. RED on the first case.
- [x] 1.7 [IMPL] Narrow `declares_deprecated`. GREEN 1.6.
- [x] 1.8 [TEST] `lifecycle.superseded_from_metadata` / `superseded_concept_ids`:
  non-self targets only; cycles fail safe (2- and 3-cycles all members);
  an unreadable doc (`None`) and a malformed `relations:` each make
  `complete=False` and are named in `unreadable`. And
  `deprecated_concept_ids` over a bundle with a marked-but-unsuperseded
  concept excludes it (spec: "A stale export does not hide a concept"),
  while `list` reports it `stable`. RED: functions missing; predicate still
  counts the marked value until 1.7 lands (order the run to show it).
- [x] 1.9 [IMPL] Add `SupersededSet` + both functions; refactor
  `deprecated_concept_ids` to reuse the edge rule. GREEN 1.8.
- [x] 1.10 [MUT] For each first-try-green test in 1.4/1.8: flip the
  `relation.target != cid` self-loop guard, drop the invalid-marker discard,
  and make WITHDRAW write `deprecated`; each mutation must turn a named
  test RED. Revert by inverse edit; purge `__pycache__`.
- [x] 1.11 [TEST] Seam guard: a test greps `src/openkos` for
  `status_derived_from` outside `model/okf.py` and for `.get("status")`
  outside `model/okf.py`, failing on any hit. [MUT] add a stray read in
  `lint.py`, observe RED, revert.
- [x] 1.12 Regression: `deprecated_concept_ids` equality over
  `examples/good-life-demo/bundle` and the v0.1 fixture before/after this
  phase (no exports exist yet, so it must be identical).

## Phase 2 — `lint` reports, `repair` fixes (the safety net before any writer)

- [x] 2.1 [PRE] Confirm `lint` has no `--fix` and no write path
  (`openspec/specs/lint/spec.md:242`) and that `plan_repair` composes
  per-document rewrites (`application/repair.py:83-152`); record both.
- [x] 2.2 [TEST] `check_status_export`: drift for missing export, stale
  export, invalid marker; `status-export-blocked` naming `draft`; nothing
  on a consistent bundle; detail text names `openkos repair` for drift and
  "hidden from retrieval regardless" for blocked; exit `0`. RED: check
  missing.
- [x] 2.3 [IMPL] `lint.check_status_export` + wiring into the report and
  rendering. GREEN 2.2.
- [x] 2.4 [TEST] Incomplete walk: one unparsable doc + a stale export →
  no drift finding for it, and a `not-run` for the stale half naming the
  unreadable doc; EXPORT findings still reported. RED.
- [x] 2.5 [IMPL] Not-run degradation per the lint spec's structured
  not-run contract. GREEN 2.4. Deviation from the literal table read:
  `not_run` fires only when an ACTUAL withdrawal candidate is skipped, not
  merely because SOME document in the bundle is unreadable/unparseable --
  an unrelated unreadable file with zero exports anywhere must not gate an
  otherwise-clean `lint` run (regression:
  `test_lint_surfaces_a_skipped_unparseable_file_as_a_notice`, which
  expects exit 0). This still satisfies every literal scenario in the lint
  delta spec, which is always stated in terms of an existing export
  candidate.
- [x] 2.6 [TEST] `repair` over: a hand-written `supersedes` edge (exports),
  a hand-deleted edge (withdraws), a `draft` (blocked, reported, bytes
  unchanged), an invalid marker (dropped); summary counts each separately;
  one commit. RED: repair reports nothing to migrate.
- [x] 2.7 [IMPL] Export pass in `plan_repair` composed after
  `migrate_document` (ONE rewrite per doc), `DocumentRewrite.changes.export`,
  `has_work`, CLI summary lines, "nothing to repair" wording. GREEN 2.6.
  Implementation note: `DocumentRewrite.changes` is now
  `application.repair.RepairDocumentChanges` (widens `okf.MigrationChanges`
  with `export`), not `okf.MigrationChanges` itself, so `migrate_document`'s
  general contract stays untouched. `RepairPlan` gained
  `blocked_export_ids`/`skipped_withdrawal_ids`; the CLI reports both
  BEFORE the `has_work` early return, since a BLOCKED/skipped concept alone
  never counts as work but still MUST be reported.
- [x] 2.8 [TEST] A v0.1 doc with `status: active` that is superseded is
  migrated AND exported in one rewrite (`status: deprecated` + marker, and
  `generated`/`sources` migrated). Second `repair` writes nothing and
  creates no commit. Incomplete walk: withdrawal skipped and reported.
  RED/GREEN against 2.7 (split an IMPL task if RED reveals a gap).
- [x] 2.9 [MUT] Remove the complete-walk check in `repair`; 2.8's skip test
  must go RED. Swap migrate/project order (fed the export pass PRE-migration
  metadata instead of post-migration); the v0.1 test must go RED. Revert,
  purge `__pycache__`.
- [x] 2.10 Regression: `deprecated_concept_ids` identical before and after
  `repair` on every fixture with a `supersedes` edge (the export must not
  change effective status). No shipped fixture carries a `supersedes` edge
  yet, so this is a dedicated tmp_path bundle rather than
  `examples/good-life-demo`/the v0.1 fixture (task 6.2 plants one in the
  shipped example).

## Phase 3 — Additive writers: `reconcile --winner`, `relate supersedes`

- [x] 3.1 [PRE] Confirm `_reconcile_pair` is the only reconcile write path
  (`cli/main.py:10146`, `:10628`, `:10746` all call it) and already writes
  both pair files under one drift guard (`:10377-10398`).
- [x] 3.2 [TEST] `reconcile a b --winner a --auto`: `b` carries the export,
  same commit as the edge; preview line names `status → deprecated`;
  a `draft` loser keeps `draft` and the preview says so; symmetric and
  `--revision` write no status; an idempotent re-run (edge present, export
  missing) writes no status; a declined gate leaves `b`'s bytes unchanged.
  RED on the first.
- [x] 3.3 [IMPL] In `_reconcile_pair`, when `edge_type == "supersedes"`
  and the edge was added, pass the target text through
  `apply_deprecation_export(superseded=True)`; preview/echo wording.
  GREEN 3.2.
- [x] 3.4 [TEST] `--from-findings` and the revision-findings walk
  (`reconcile` REVERSES verdict → `supersedes`) export the loser too.
  Expected first-try GREEN (shared path) → 3.5.
- [x] 3.5 [MUT] Guard 3.4: make 3.3's branch key on `edge_added_a` only;
  the `holder == b` walk case must go RED. Revert, purge.
- [x] 3.6 [TEST] `relate a supersedes b --auto` exports `b` in the same
  commit; `relate a references b` leaves `b` untouched; idempotent relate
  writes no status; drift guard refuses when `b` changed after the preview.
  RED.
- [x] 3.7 [IMPL] `prepare_relate` projects the target when the added type
  is `supersedes` and ids differ; `relate_core` writes it; baselines and
  commit paths include it; preview line. GREEN 3.6.
- [x] 3.8 After this phase, `lint` on a bundle built only by `reconcile`
  and `relate` reports zero `status-export-drift`.

## Phase 4 — Subtractive writers: `forget`, `purge`

- [x] 4.1 [PRE] Confirm `prepare_forget` already holds metadata for every
  non-purged document (`other_files`, `application/lifecycle.py:1290-1306`)
  so no second walk is needed; confirm `purge` reuses forget Phase A.
  Deviation from a literal reading: `prepare_purge` does NOT literally call
  `prepare_forget` -- it is its OWN independent Phase A that re-derives the
  identical `purge_ids`/`other_files`/`member_metadata`/reference-detection
  computation (this predates this change). "Reuses forget's Phase A" holds
  at the ALGORITHM level (same purge-set resolution, same edge rule), which
  is what this task's export-withdrawal computation was duplicated to
  match -- not as a literal shared function call.
- [x] 4.2 [TEST] `forget M` where M solely supersedes Y (exported): Y
  withdrawn, preview names it; Y also superseded by surviving N: Y
  unchanged; Y with unmarked `deprecated`: unchanged; incomplete walk: Y
  kept and the skip reported. RED.
- [x] 4.3 [IMPL] `ForgetPlan.status_rewrites` from
  `superseded_from_metadata(post view)`; preview lines; drift baselines.
  GREEN 4.2. Implementation note: named `ForgetPlan.status_withdrawals`
  (a `tuple[StatusWithdrawal, ...]`, each carrying `target`/`outcome`/
  `new_text`) rather than a bare `dict[str, str]`, so the preview can also
  report `WITHDRAW` vs `DROP_MARKER` per target and `purge` (task 4.7) can
  reuse the exact same shape.
- [x] 4.4 [TEST] Write ordering: fault-inject `fsio.remove_file` to fail on
  the first delete; assert index/log written, Y already withdrawn, M still
  present (catalog → exports → deletes). RED until 4.5.
- [x] 4.5 [IMPL] `forget_core` writes status rewrites after the catalog,
  before deletes, sorted. GREEN 4.4.
- [x] 4.6 [MUT] Move the export writes after the deletes; 4.4 must go RED.
  Revert, purge.
- [x] 4.7 [TEST] `purge M` (git fixture): Y withdrawn and `bundle/Y.md` in
  the post-rewrite commit beside index/log; an `OSError` writing Y gives a
  non-fatal WARNING naming Y and `openkos repair`, exit code unchanged.
  RED.
- [x] 4.8 [IMPL] Purge live-tree cleanup writes the rewrites and extends
  `commit_paths_rel` (`cli/main.py:6961-7043`). GREEN 4.7.

## Phase 5 — Rewiring writers: `merge`, `unmerge`

- [x] 5.1 [PRE] Pin current behavior: write a test showing that today an
  absorbed `status` fills a survivor gap through the generic scalar rule
  (`model/okf.py:2415-2425`), and whether `unmerge` refuses when the
  survivor changed after the merge (design Open Question). Record both
  results before changing code. Findings: (1) confirmed -- an absorbed
  human-authored `status` still fills a gapped survivor today, and this
  rule is UNCHANGED by this phase for a non-exported value; (2)
  resolved -- `unmerge` does NOT refuse: `prepare_unmerge`'s
  `survivor_bytes` baseline is captured fresh at its OWN Phase A (whatever
  is on disk right now), never compared against the merge-time state, so a
  hand-edit landing between merge and unmerge is silently discarded by the
  ledger's verbatim restore. Both pinned by
  `tests/unit/cli/test_merge_status_export.py`.
- [x] 5.2 [TEST] `build_merged_document`: absorbed marker never crosses;
  absorbed marked `deprecated` never fills a survivor gap; an absorbed
  human `draft` still fills it (unchanged rule). RED on the first two.
- [x] 5.3 [IMPL] `_SPECIAL_KEYS` + marked-status skip. GREEN 5.2.
- [x] 5.4 [TEST] `merge` end to end: third party supersedes absorbed →
  survivor exported, preview names it; absorbed supersedes survivor
  (self-loop dropped) with survivor exported → survivor withdrawn. RED.
- [x] 5.5 [IMPL] `prepare_merge` projects the survivor over the post-merge
  view. GREEN 5.4. Implementation note: the projected text is folded
  directly into `plan.merged_survivor` (a `dataclasses.replace` on the
  frozen `MergePlan`, not a separate `PreparedMerge` field) -- this keeps
  the ledger's `survivor_sha256` binding, the eventual disk write, and
  `_reconcile_merged_survivor`'s body-only rebuild (which re-extracts
  metadata from that exact text) consistent for free. `status_outcome` is
  the only new `PreparedMerge` field, carried solely for the preview.
- [x] 5.6 [TEST] Parity: on an export-consistent fixture (third party
  supersedes absorbed, absorbed exported) `merge` then `unmerge` leaves
  every file byte-identical. Expected first-try GREEN → 5.8.
- [x] 5.7 [TEST] Drift carve-out: pre-merge hand-written edge with an
  unexported absorbed → after `unmerge`, absorbed is exported, preview
  names it, all other bytes match snapshots. RED.
- [x] 5.8 [IMPL] `prepare_unmerge` projects restored survivor/absorbed
  over the post-unmerge view; `unmerge_core` writes the projected texts.
  GREEN 5.7 and keep 5.6 GREEN. Implementation note: `prepare_unmerge`
  holds no in-memory whole-bundle snapshot of its own (unlike `merge`/
  `forget`/`purge`, it only ever reads the files it must reverse), so this
  is a genuinely NEW `okf._iter_docs` walk, overridden at exactly the
  three places Phase B is about to change (restored survivor, restored
  absorbed, reversed relation-retargeted third parties). Same
  fold-into-`plan` pattern as 5.5 (`restored_survivor`/`restored_absorbed`
  mutated via `dataclasses.replace`); two new preview-only fields
  (`survivor_status_outcome`/`absorbed_status_outcome`).
- [x] 5.9 [MUT] Guard 5.6: make the unmerge projection always EXPORT; 5.6
  must go RED. Make it project the survivor only; 5.7 must go RED. Revert,
  purge.
- [x] 5.10 `unmerge --to` chain over two merges with exports: each step
  consistent; `lint` reports no drift afterwards.

## Phase 6 — Docs and the shipped example

- [x] 6.1 `docs/knowledge-object-model.md` Lifecycle (line ~377): one
  sentence — a superseded concept's frontmatter carries `status:
  deprecated` with `status_derived_from: supersedes`, derived from the edge
  and never read back. `docs/cli.md` reconcile (`--winner`, ~405/416) and
  `relate`: the loser's frontmatter now says `deprecated`; `repair` fixes
  drift. No counts, no "since #".
- [x] 6.2 Check `examples/good-life-demo/bundle` for `supersedes` edges; if
  any, run `openkos repair` there and commit the result; run `openkos lint`
  and `openkos status` on it and a fresh `init` (zero export findings).
  Finding: no `supersedes` edge exists anywhere in the shipped example, so
  no `repair`/commit was needed. Verified on a temp copy of the example
  and on a fresh `init`: `lint` reports "No deprecated-status export
  findings" (14/14 checks completed) and `status` runs clean on both.
- [ ] 6.3 Archive notes (for the archive phase, not apply): the Non-Goals
  prose of `okf-format-migration` ("the engine starting to write `status:
  deprecated`") and `status-aware-retrieval` ("any change to how
  `status`/`supersedes` are written") becomes false once merged; edit both
  paragraphs at archive, since a delta cannot carry them. Flip ADR-0032 to
  Accepted in all three places (front matter, Status line, README row).
