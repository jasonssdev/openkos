# Exploration: backfill-sensitivity (GitHub #231)

## Current State

**`combine_sensitivity`** — `src/openkos/model/okf.py:313-323`. Pure, stdlib-only:
`SENSITIVITY_ORDER[max(_rank(a), _rank(b))]` — high-water-mark, fail-closed on dirty
input (ADR-0003).

**`sensitivity_direction`** — `okf.py:282-310`. Classifies `current -> target` as
raise/same/lower; dirty/missing `current` floors at `confidential` (fail-closed, ADR-0008).

**Descendant/provenance closure walk** — TWO layers:

- `src/openkos/bundle/provenance.py:75-139` `find_provenance_descendants(files, *, root_ids)`
  — pure, canonical-layer fixpoint closure over `provenance:` frontmatter. Conservative
  "non-empty subset of purge" rule: a derived object citing MULTIPLE sources is only pulled
  in if **every** cited id is already in the root/purge set. Confirmed by ADR-0009 itself:
  "Multi-source high-water-mark combination... stays deferred per the ingestion spec's
  existing MVP-2/3 non-goal; `find_provenance_descendants`'s conservative rule already
  excludes that case rather than combining it incorrectly." This is THE key edge case for
  backfill (see Risks).
- `src/openkos/cli/main.py:3339-3411` — the inline descendant-scan block inside
  `set_sensitivity_cmd` (not extracted into a helper): builds a whole-bundle snapshot
  (`layout.bundle_dir.rglob("*.md")`, skipping `okf.RESERVED_FILENAMES` and the target
  itself), calls `find_provenance_descendants(bundle_snapshot, root_ids={canonical_id})`,
  then for each descendant computes `okf.combine_sensitivity(member_current, level)`,
  staging a write only on strict raise. Also does the dangling-provenance stderr-warning
  scan (bundle-wide, not scoped to the invoked Source's own closure — this is issue #232's
  exact complaint, explicitly flagged as out-of-scope overlap in both #230's
  `explore.md:134-136` and `archive-report.md:114`).

**`set_sensitivity_cmd`** — `main.py:3182-3450+`. Full docstring at 3205-3271 is the spec of
record. Gating: propagation only runs when `metadata.get("type") == "Source"` AND
`direction == "raise"` (both required — a downgrade never cascades even though
`combine_sensitivity` could compute a raise for some individual descendant below the new
lower level). Phase B write order: every staged descendant raise, then the target concept,
then `log.md`, then ONE `_autocommit` covering every changed path (fail-closed on partial
failure — no cross-file rollback, matches `relate`/`merge`).

**`--auto` threading** — uniform pattern across every write verb (`set_sensitivity_cmd`,
`reconcile` at `main.py:4683-4687`, `relate`, `merge`, `forget`): `--auto` skips the confirm
prompt only; `cfg.review` (config `review: false`) also skips it; a TTY prompts via
`typer.confirm` and aborts on decline; non-TTY without `--auto` refuses to write.
`--allow-downgrade` is the separate, ADR-0008-specific flag gating a lowering assignment
specifically on paths where the prompt won't run.

**`status`/`lint` finding structure** — `src/openkos/lint.py`. `LintDoc` (frozen dataclass,
`lint.py:25-80`) carries `path/identity/rel_dir/body/freshness/type/volatility/relations/
extraction_status/resource` — NOTE: no `sensitivity` or `provenance` field yet.
`LintFinding` (`kind/path/detail`, flat, no severity tiers). `check_unextracted`
(`lint.py:544-581`) is the closest precedent for a new "sensitivity-below-Source" detection
finding: pure function taking only `docs: list[LintDoc]` (deliberately no `bundle_dir`
param — "structural no-fifth-walk guard"), returns `list[LintFinding]`. Both `lint`
(`main.py:5300-5405`) and `status` (`main.py:5030-5159`) call `lint_check.collect_docs` ONCE
and reuse the SAME `docs` list for `unextracted` (and `dangling`) — no second walk.
`status`'s "Needs attention:" section (`main.py:5101-5144`) folds `survey.findings` +
`dangling` + `unextracted` + duplicate-group + vector-index lines together.

**`reconcile`** verb exists (`main.py:4666-4754+`) but is an UNRELATED domain: human-judged
contradiction resolution between exactly two named concepts (symmetric `reconciled_with` or
directional `supersedes` via `--winner`), no LLM, no sensitivity involvement. Not a natural
home for a bundle-wide sensitivity sweep.

## Prior-context verification

1. **#230 (rewrite-provenance-on-merge) is merged in `main`** (confirmed: `fb968d7`/`3f26c98`
   in recent commits) and its own archive-report explicitly says: "Issue #231 backfill (which
   walks provenance descendants) is now unblocked by this change." This confirms the
   sequencing rationale. #230 retargets third-party `provenance:` entries to the survivor on
   merge/unmerge — meaning provenance chains no longer dangle after a merge, which is exactly
   what a bundle-wide backfill needs to resolve correctly. Before #230, a backfill's
   descendant closures would have silently missed anything whose Source got merged away.
2. **#219** shipped both propagation points
   (`openspec/changes/archive/2026-07-28-propagate-sensitivity-to-derived/`), and its "Known
   Follow-Ups" item 6 states verbatim: "**Bulk backfill of existing bundles: deliberately out
   of scope by user decision.**... Tracked as expected behavior; bulk backfill is deferred."
   Item 4 in the same list: "The descendant-scan block is inline in `set_sensitivity_cmd`...
   The descendant walk could be extracted into a helper for readability, deferred to a
   separate refactor change" — this is #235.
3. **#187** (`extraction_status` + lint `unextracted` finding) and **#184** (`list` verb) are
   real precedents for how a read-only detection finding is surfaced.
4. **#232** (dangling-provenance warning's bundle-wide scan) — confirmed as a real,
   acknowledged, explicitly out-of-scope overlap in both #230's `explore.md:134-136` and
   `archive-report.md:114`. Independent of #231: #232 is about `set_sensitivity_cmd`'s
   WARNING-emission scope, not about the write path this issue adds.
5. **#235** (extract descendant scan out of `set_sensitivity_cmd`) — flagged as a known
   follow-up in #219's archive-report item 4. No separate design/proposal exists yet, so its
   precise API surface is unconfirmed.
6. **#233 / #234** — both are `set_sensitivity_cmd` defects recorded during #219's native
   review, and both sit inside the exact code slice 1 would refactor. #233: the Phase B
   write-failure message names none of the paths that already landed, and the multi-descendant
   partial state is untested. #234: the descendant-resolution failure and the log/frontmatter
   rendering failure emit a byte-identical "failed while preparing" message. Both are P3 and
   are natural companions to the #235 extraction rather than to the backfill write path.

## Is #235's refactor a real prerequisite?

**Not a hard technical blocker, but a strong architectural prerequisite for a clean
implementation.**

- The new backfill verb needs the SAME three things `set_sensitivity_cmd`'s inline block
  already does: build a bundle snapshot, resolve `find_provenance_descendants` per Source,
  compute `combine_sensitivity` per descendant, stage only strict raises.
- Without extraction, backfill would duplicate ~70 lines of `main.py:3339-3411` verbatim,
  which directly conflicts with this codebase's own established "no-fifth-walk"/no-duplicate
  scan convention (explicitly named in `check_unextracted`'s and `status`'s docstrings,
  `main.py:5044-5045,5110-5112`).
- Doing #235 first means one canonical
  `resolve_source_raises(bundle_snapshot, source_id, level) -> list[DescendantRaise]`-shaped
  helper (likely landing in `bundle/provenance.py` alongside `find_provenance_descendants`,
  matching that module's canonical-layer, no-`openkos.graph`-import contract) that
  `set_sensitivity_cmd`, the new backfill verb, AND a detection-only `lint`/`status` finding
  can all call.
- Recommendation: sequence #235 immediately before (or as the first task slice of) #231's
  implementation. It is small, mechanical (pure refactor, no behavior change —
  `tests/unit/cli/test_set_sensitivity.py`'s 29 tests are the regression guard). It MUST be
  done before or within that slice, not skipped.

## The Four Open Questions

**1. Shape — dedicated verb vs. flag vs. `reconcile`.**
Recommendation: **dedicated verb**, NOT a flag on `set-sensitivity` and NOT folded into
`reconcile`.

- Not a `set-sensitivity` flag: that verb's contract is "exactly one named concept, plus its
  own descendants" (docstring `main.py:3205-3213`); a bundle-wide sweep with no target
  argument is a different shape entirely and would strain the existing 29-test contract.
- Not `reconcile`: confirmed above as an unrelated domain.
- A dedicated verb matches this codebase's pattern of one verb per operation shape.

**2. Confirmation UX — single bundle-wide preview + one confirmation.**
Recommendation: **yes**. Same precedence ladder as `set_sensitivity_cmd`/`reconcile`:
`--auto` > `cfg.review: false` > TTY `typer.confirm` > refuse on non-TTY without `--auto`.
No `--allow-downgrade`-equivalent is needed since this verb is raise-only by construction,
so ADR-0008's downgrade gate does not apply.

**3. Reporting/commit content.**
Mirror `set_sensitivity_cmd` exactly — preview lists every staged
`(concept_id, current -> new_level)` raise across ALL sources scanned, success message
repeats the same list, ONE `log.md` entry (not one per Source) summarizing the sweep, ONE
`_autocommit` covering every changed path. Phase B write order: descendants first
(fail-closed, over-classified on partial failure, never under-classified — the invariant
#219 established), then `log.md`, then the single autocommit. No Source's own frontmatter
needs writing — backfill only ever raises descendants.

**4. Detection-first as a separate slice.**
Recommendation: **yes, split.** Detection reuses the SAME extracted helper in read-only mode
(compute would-be raises, never write), architecturally identical in shape to
`check_unextracted`: a pure function returning `LintFinding`s, wired into both `lint` and
`status`'s "Needs attention" section exactly like #187's precedent.

## Slicing and Line Estimate

| Slice | Scope | Est. changed lines | Notes |
|---|---|---|---|
| 1. Extract descendant-scan helper (#235) | Move `main.py:3339-3411`-shaped logic into `bundle/provenance.py` (or a new small module), re-wire `set_sensitivity_cmd`, zero behavior change | ~80-150 | Pure refactor; regression-proven by the existing 29 `test_set_sensitivity.py` tests |
| 2. Detection-only finding | New sensitivity/provenance-aware `LintDoc` field(s), new `check_below_source_sensitivity`-shaped pure function in `lint.py`, wire into `lint` + `status`, new tests | ~150-250 | Ships independently; no write path; lowest risk |
| 3. Backfill write verb | New Typer command, preview/confirm/`--auto`, Phase B write+log+autocommit, docstring, tests | ~250-400 | Depends on slice 1; largest slice |

Total ~480-800 lines across 3 slices — fits the 800-line session budget only if chained as 3
PRs (matches `delivery_strategy: auto-chain`), each at or near the 400-line default per-PR
budget. Recommend the same stacked-to-main chaining #219 used.

## Risks and Edge Cases

- **Multi-source provenance closures are conservatively EXCLUDED today.**
  `find_provenance_descendants`'s non-empty-subset rule means a derived object citing 2+
  Sources is only pulled into a closure if ALL cited ids are already in `root_ids`. If
  backfill iterates Sources ONE AT A TIME (mirroring `set_sensitivity_cmd`'s per-invocation
  `root_ids={single_source}`), a multi-source object is silently skipped for EVERY
  single-Source root. **Design decision needed**: does backfill scan with
  `root_ids = {every Source id in the bundle}` in one pass (correctly reaches multi-source
  objects, but changes semantics vs. `set_sensitivity_cmd`'s single-source call) or per-Source
  sequentially (matches existing precedent exactly, but never touches multi-source objects,
  silently under-protecting them — arguably the worse failure mode for a "close the gap"
  issue)? ADR-0009 explicitly defers multi-source high-water-mark combination to MVP-2/3 —
  this must be surfaced as an explicit scope question, not silently resolved.
- **Merged/absorbed ids.** #230 already retargets `provenance:` on merge, so this is largely
  resolved for post-#230 merges; a merge performed BEFORE #230 shipped could still have
  dangling `provenance:`. Those objects are excluded from any closure (fail-safe) but
  silently under-protected. Worth a lint/status detection line.
- **`extraction_status` skipped Sources.** A Source with `extraction_status: failed` (or
  `blocked-by-sensitivity`/`no-concepts-found`) still has its own `sensitivity` field and can
  still be a valid closure root — extraction status is orthogonal. No special-casing needed,
  but worth one pinning test.
- **Confidential-gate interactions.** Raising a derived object's sensitivity via backfill
  never re-triggers extraction and never touches `extraction_status` — purely additive to
  `sensitivity`. Call this out in the design so a reviewer does not assume otherwise.
- **Idempotency.** Re-running backfill after a successful run must be a clean no-op,
  mirroring `set_sensitivity_cmd`'s strict-raise-only staging (`main.py:3393-3394`). Pin with
  an explicit test: second run stages zero writes and produces no spurious empty commit.
- **Partial write failure.** Follow the SAME fail-closed, no-rollback,
  over-classify-never-under-classify contract #219 established. #233 (the failure message
  naming no landed paths) is a good opportunity to fix in THIS change rather than deferring
  again, since backfill's failure surface is larger.

## Test Surface

Existing:

- `tests/unit/cli/test_set_sensitivity.py` (29 tests) — regression guard for slice 1; must
  stay green with byte-identical behavior.
- `tests/unit/model/test_okf_sensitivity.py` (4 tests) — covers
  `combine_sensitivity`/`sensitivity_direction` directly.
- `tests/unit/cli/test_merge.py`, `tests/unit/model/test_okf.py` — reference for how the pure
  closure function is already tested.

New tests under strict TDD (RED first for each):

- Slice 1: characterization tests proving the extracted helper produces byte-identical
  behavior (same staged raises, same warnings) BEFORE the refactor lands.
- Slice 2: `test_check_below_source_sensitivity_flags_descendant_below_source`,
  `..._multi_source_excluded`, `..._clean_bundle_reports_nothing`, plus `lint`/`status`
  wiring tests.
- Slice 3: `test_backfill_raises_every_descendant_below_its_source`,
  `test_backfill_never_lowers`, `test_backfill_is_idempotent_second_run_is_noop`,
  `test_backfill_auto_skips_prompt_only`,
  `test_backfill_multi_source_descendant_{included|excluded}_per_scope_decision`,
  `test_backfill_partial_write_failure_leaves_bundle_over_classified`,
  `test_backfill_reports_every_staged_raise_in_preview_and_success_message`.

## Recommendation

Proceed to proposal with three sequenced slices: (1) extract the descendant-scan helper
(#235) as a pure refactor with characterization tests, (2) a read-only `lint`/`status`
detection finding reusing that helper, (3) the dedicated backfill write verb with a single
bundle-wide preview/confirm/`--auto`. Surface the multi-source-closure scope question
explicitly in the proposal rather than resolving it silently. Change name
`backfill-sensitivity` is confirmed as accurate.
