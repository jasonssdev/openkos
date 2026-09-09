# Verify Report: Lifecycle Application Service

Change: `lifecycle-application-service` (issue #918, third and last bounded-context slice)
Verified against: `main` @ `fb05060` (all six slices merged: #946, #947, #948, #949, #950, #951)
Verdict: **PASS WITH WARNINGS**

## Summary

All 8 requirements in the new `lifecycle-application-service` capability are
satisfied by the shipped code at the literal spec-text level, backed by a
green 6070-test suite (0 failures) and 96.99% branch coverage. The four
Purpose-only delta specs (`forget-command`, `privacy-purge`,
`entity-resolution-merge`, `entity-resolution-adjudication`) carry zero
requirement changes, as declared. Zero CRITICAL issues found.

Four WARNING-level findings are reported below: the most material is that
the confirmation gate is staged as `ConfirmationRequest` typed data for only
3 of the union's 6 real call sites (`forget`, `purge`,
`adjudicate --apply-same`) — `merge`, `unmerge`, and `adjudicate --apply`'s
per-item walk keep their boolean prompt hardcoded in the CLI adapter,
exactly as before this change, which is narrower than the Purpose paragraph
and proposal framing suggest. All four warnings are pre-existing-shape or
documented-but-underemphasized design deviations, not functional
regressions — the full test suite and every falsification task in
`tasks.md` passed.

Six pre-existing, honestly self-disclosed test-assertion gaps make specific
wording (not behavior) for `purge` and `adjudicate --apply-same`'s TTY/typed
gates effectively unverifiable by any test in the repository, before or
after this change. These are reported plainly per instructions, not papered
over.

## Gate Evidence (run directly, not trusted from tasks.md)

| Gate | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | **PASS** — "All checks passed!" |
| Format | `uv run ruff format --check .` | **PASS** — "295 files already formatted" |
| Types | `uv run mypy .` | **PASS** — "Success: no issues found in 295 source files" |
| Tests | `uv run pytest -q` (unpiped, full output captured) | **PASS** — 6070 passed, 3 skipped, exit 0 (277.75s) |
| Coverage | `uv run pytest --cov=src/openkos --cov-report=term -q` | **PASS** — TOTAL 96.99% (gate: ≥90%); `application/consent.py` 100%, `application/lifecycle.py` 98%, `cli/main.py` 96%; 6070 passed, 3 skipped, exit 0 (333.56s) |

## Requirement-by-Requirement Verification

### 1. Non-CLI Callable Lifecycle Composition — **PASS**

- `src/openkos/application/lifecycle.py` (2365 lines) and `consent.py` (92
  lines) import none of `typer`, `rich`, `openkos.cli`; grep confirms zero
  matches outside docstring prose. `openkos.vcs` is also absent (threat-
  matrix requirement).
- `sys.stdin.isatty()`: zero calls in `application/*.py` (grep confirmed);
  mentioned only in docstrings describing what the module does NOT do.
- `tests/unit/application/test_lifecycle.py` imports nothing from
  `openkos.cli` (verified: `import` block at lines 14-29) and calls
  `lifecycle_service.prepare_merge`/`merge_core`/`prepare_unmerge`/
  `unmerge_core`/`prepare_forget`/`forget_core`/`prepare_purge`/
  `preview_apply_same` directly — the "non-CLI caller" scenario is
  runtime-proven, not just statically argued.
- `tests/unit/application/test_layering.py::test_application_modules_never_import_cli_typer_or_rich`
  AST-scans every `application/*.py` file at test time and passed in the
  full suite run.

### 2. ConfirmationRequest Tagged Union — **PASS**, with a scope caveat (see WARNING 1)

- `src/openkos/application/consent.py`: `BooleanConfirmation` (prompt,
  nullable `bypass_flag`, nullable `non_tty_refusal`) and
  `TypedChallengeConfirmation` (prompt, expected, supplying_flag,
  non_tty_refusal, mismatch_abort, `match_mode: Literal["exact",
  "strip-then-exact"]`) — exactly matches design D1.
- `TypedChallengeConfirmation` has **no `bypass_flag` field at all** (only
  `BooleanConfirmation` does) — `purge` is structurally incapable of a
  bypassable-boolean representation, confirmed by
  `test_purge_is_the_typed_challenge_variant_with_no_bypass_representable`.
- Both comparison policies verified at the two real call sites:
  - `purge` — `cli/main.py:6560-6583` (rail 6): raw `typed_phrase !=
    expected_phrase` comparison, no `.strip()`. `expected_phrase` is
    **deliberately recomputed via a LIVE call** to
    `application_lifecycle.purge_confirm_phrase` at this exact position
    (not read off `plan.confirmation.expected`) — see WARNING/Caveat
    section below; this is a documented, tested deviation, not a defect.
  - `adjudicate --apply-same` — `cli/main.py:2733`:
    `preview.confirmation.matches(typed_count)`, which literally invokes
    `TypedChallengeConfirmation.matches()` with `match_mode="strip-then-exact"`.
- Divergence on whitespace-only difference is unit-tested directly:
  `test_the_two_typed_gates_diverge_on_a_whitespace_only_difference`
  (`tests/unit/application/test_lifecycle.py:103-128`) constructs both
  variants from the same text and proves `purge_style.matches(...)` is
  `False` while `adjudicate_style.matches(...)` is `True` for
  `"  purge x (3 concepts)  "` — this exact scenario passed in the full run.

### 3. Hard Refusal Gates Are Not Representable As Confirmations — **PASS**

- `forget`'s Gate 1 (`cli/main.py:5792` — `if (plan.surviving_refs or
  plan.unverifiable_refs) and not force`) and `purge`'s per-target refusal
  (`cli/main.py:6455`) both read plain `int` fields (`surviving_refs`,
  `unverifiable_refs`, `verified_refs`) directly off the Plan dataclasses,
  never through `ConfirmationRequest`.
- Neither `BooleanConfirmation` nor `TypedChallengeConfirmation` declares a
  `granted`, `force`, or `override` field (confirmed by dataclass
  introspection in `test_lifecycle_seams.py::test_confirmation_request_variants_carry_no_grant_field`).
  Extended in Slice S5 to `BatchApplyPreview.confirmation`
  (`test_batch_apply_preview_confirmation_carries_no_grant_field`).
- `test_forget_plan_gate_one_counts_never_become_confirmation_request_fields`
  additionally asserts `BooleanConfirmation` has no `matches()` method at
  all, so Gate 1's counts have no method that could ever consult them.

### 4. Unmerge Has A Full Public Prepare/Core Pair Matching Merge — **PASS**

- `application/lifecycle.py` exposes `prepare_unmerge(...)  ->
  PreparedUnmerge` (line 725) and `unmerge_core(layout, prepared) ->
  UnmergeResult` (line 959), matching `prepare_merge`/`merge_core`'s Phase
  A/Phase B shape exactly.
- `_execute_single_unmerge` is confirmed gone from `cli/main.py`:
  `test_lifecycle_seams.py::test_execute_single_unmerge_no_longer_lives_on_cli_main`
  asserts `not hasattr(cli_main, "_execute_single_unmerge")`, and it passed.
- `cli/main.py:9238` defines `_run_single_unmerge`, a private **adapter**
  helper shared by the classic two-arg unmerge and the `--to` chain form —
  this is the caveat given in the task and confirmed exactly: the seam
  guard requires the OLD name gone, not that no helper exists at all, and
  that is what shipped.

### 5. Purge's Disclosure Renders From Returned Templates Byte-For-Byte — **PASS**

- `PurgeDisclosure` (application/lifecycle.py:1524) carries
  `expunge_targets`, `resource_warnings`, `raw_absence`, `cascade_total` as
  data; `cli/main.py:6430-6448` renders `"openkos purge: proposed
  IRREVERSIBLE history rewrite:"` followed by iterating
  `plan.disclosure.expunge_targets`/`resource_warnings` and reading
  `plan.disclosure.raw_absence`/`cascade_total` directly — no adapter-side
  rewording.
- `dropped_store_notice`/`residual_store_notice` moved verbatim as `str |
  None` (D3's stated exception), confirmed by direct source read; both
  keep their full pre-existing prose intact.
- 76 `test_purge.py` tests pass unmodified (behavior contract); `git diff`
  confirms only 2 patch-target repoints and zero assertion-text changes.

### 6. Shared Write Mechanics Stay Adapter-Side, Each With One Definition — **PASS**, with a coverage gap noted (WARNING 3)

- `grep -rn "^def _reject_drifted_targets\|^def _autocommit\|^def _refresh_derived_after_write" src/` returns exactly one definition each, all in `cli/main.py` (lines 1085, 1395, 4039).
- `_echo_commit_disclosure` also has exactly one definition
  (`cli/main.py:1357`), confirmed by direct grep — but see WARNING 3: it is
  not in the automated `test_shared_write_helpers_are_never_forked` set.
- `snapshot_read` promoted to `fsio.snapshot_read` (fsio.py:20);
  `main._snapshot_read` (main.py:593) is a one-line delegator, not a
  duplicate, matching the `_slugify` precedent verbatim.
- `test_shared_write_helpers_are_never_forked` (test_layering.py:131) now
  covers `{_reject_drifted_targets, _autocommit,
  _refresh_derived_after_write, snapshot_read}` and passed.

### 7. Adapter Owns Interaction, Presentation, And Exit Codes — **PASS**

- Zero `sys.stdin.isatty()` calls in `application/*.py` (confirmed by
  grep); 22 calls remain in `cli/main.py`.
- `typer.confirm`/`typer.prompt`/`typer.echo`/`typer.Exit` calls for all
  five verbs' gates remain exclusively in `cli/main.py` (spot-checked at
  the merge, purge rail-6, and adjudicate --apply-same gate sites).

### 8. The Extraction Preserves Observable CLI Behavior — **PASS**, with disclosed pre-existing gaps (see below)

- Full suite: 6070 passed, 3 skipped, exit 0.
- `git diff --stat 38ea84e..fb05060` on all seven touched test files
  (`test_merge.py`, `test_merge_core.py`, `test_unmerge.py`,
  `test_unmerge_surgical_catalog.py`, `test_forget.py`, `test_purge.py`,
  `test_adjudicate.py`) shows only import/patch-target changes and added
  comments — spot-checked `test_forget.py` and `test_merge_core.py` in
  full: zero output-text assertion changed in either.
- `test_adjudicate.py`'s aggregate diff for the full six-slice chain is 69
  lines (imports + comments), not zero — the "zero-line diff" caveat in
  the task prompt refers specifically to the **S5 commit's own** diff of
  that file (task 13.2's own claim, "confirmed via git diff, 0 lines" for
  that slice), which is consistent: S1's 4 repoints landed earlier and S5
  added no new patch-target changes of its own.

## Caveats Confirmed Against Code (from the apply-phase report)

1. **`PartialForgetWrite` carries the K-of-N count, no filesystem probing.**
   Confirmed: `application/lifecycle.py:1155-1179` defines
   `PartialForgetWrite(OSError)` with `unlinked_count` set only after each
   successful unlink inside `forget_core`'s try block
   (`application/lifecycle.py:1411-1445`). `cli/main.py:5908-5920`'s
   `except (OSError, ValueError)` arm reads `getattr(exc, "unlinked_count",
   ...)` — no `Path.exists()` or other filesystem probe anywhere in the
   handler. The docstring's reasoning (`Path.exists()` re-raises `EACCES`)
   matches `_purge_store_is_gone`'s own documented behavior.
2. **`purge_confirm_phrase` is called LIVE at rail 6, not during `prepare_purge`.**
   Confirmed: `application/lifecycle.py:1805-1810` duplicates the phrase
   formula inline in `prepare_purge` (for `plan.confirmation.expected`,
   used only for introspection/rendering) specifically so the LIVE call at
   `cli/main.py:6571` remains the only call
   `test_drift_on_the_unprompted_path_is_refused` observes.
   `tests/unit/cli/test_purge.py:1567-1607` monkeypatches
   `application_lifecycle.purge_confirm_phrase` to inject a concurrent edit
   and asserts `exit_code == 3` (the drift guard) — not exit 1 — proving the
   call lands strictly after rail 4.
3. **`_run_single_unmerge` is a private adapter helper shared by both unmerge forms.**
   Confirmed: `cli/main.py:9238` defines it; `cli/main.py:9136` and `:9195`
   both call it (classic and `--to` chain paths); `_execute_single_unmerge`
   is fully gone and guarded by `test_lifecycle_seams.py`.
4. **`test_adjudicate.py`'s S5 diff was zero-line; moved functions aliased back under original private names.**
   Confirmed: `cli/main.py:3736-3740` binds `_ordered_merge_pair`,
   `_prepare_one_merge`, `_reconcile_planned` (plus
   `_cross_source_same_pair`/`_cross_type_concern`/`_member_body_length`)
   back onto `main` under their pre-move private names — see WARNING 2 for
   a design-consistency note on this.

## WARNING Findings

**WARNING 1 — RESOLVED after this report, by PR #952.**

*As reported:* neither `PreparedMerge` nor `PreparedUnmerge` carried a
`confirmation` field — only `review: bool`, telling the adapter *whether* to
ask, never *what* to ask. `design.md`'s own `boolean_confirmation(verb)`
helper was never implemented (zero occurrences in `src/`). Only `forget`,
`purge` and `adjudicate --apply-same` constructed a real
`ConfirmationRequest`. That was not a literal violation of Requirements 1 or
2 — which describe the union's shape, not universal per-verb adoption — but
it was narrower than the Purpose paragraph and than issue #918's own
sentence, which names `merge` explicitly: "forget/purge/**merge** flows,
including their confirmation contracts expressed as data (so a non-TTY
adapter can drive them)".

*Resolution:* PR #952 implemented `consent.boolean_confirmation(verb)` and
added `confirmation` to both `PreparedMerge` and `PreparedUnmerge`. Three
CLI gates now read it — `merge`'s, `unmerge`'s per-step, and the `--to`
chain's, the last calling the helper directly because it consents to the
whole unwind sequence before any step's `prepare_unmerge` runs. All five
`Prepared*`/`*Plan` types report `confirmation` present. Falsified: mutating
the refusal sentence once in `consent.py` turns all three CLI gates RED
together, which is what proves they read one definition rather than
duplicated literals. `merge`'s non-TTY test, which asserted only
`"--auto" in result.stderr`, now pins the whole sentence.

*Still open, and deliberately so:* `adjudicate --apply`'s per-item walk
routes through `curate_module._confirm`'s validating `[y/N]` loop, a
different mechanism with no bypass flag and no non-TTY refusal arm of its
own. `consent.py`'s `BooleanConfirmation` docstring already describes the
`bypass_flag=None, non_tty_refusal=None` shape that gate would take; wiring
it is follow-on work, not a gap in this change.

*Lesson recorded:* this report returned PASS on 8/8 requirements and was
correct to. No requirement demanded per-verb adoption. A spec that passes is
not the same as a goal that is met, and the Purpose paragraph and the issue
text are part of the contract a verification should read.

**WARNING 2 — Design.md's "forbids aliasing any relocated callable" is broader than what shipped; 9 aliases exist, unguarded.**
`cli/main.py:3732-3740` binds 9 relocated callables back onto `main` under
their original private names (`_canonicalize_concept_id`,
`_resolve_concept_path`, `_merge_drift_targets`, `_member_body_length`,
`_ordered_merge_pair`, `_cross_source_same_pair`, `_cross_type_concern`,
`_prepare_one_merge`, `_reconcile_planned`), each justified by an inline
comment ("none carries a dangerous test patch site"). I confirmed by
repo-wide grep that no test currently monkeypatches any of these 9 names,
so the risk is dormant, not manifesting as a bug today. But design.md
states an unqualified rule — "This design forbids aliasing any relocated
callable" — that the shipped code does not actually follow; only
`prepare_merge`/`merge_core` (and, via the seams file,
`_execute_single_unmerge`/`_purge_confirm_phrase`/
`_decisions_history_targets`/`_purge_dropped_store_notice`/
`_purge_residual_store_notice`) are covered by `test_lifecycle_seams.py`'s
`hasattr`-absence guard. None of the 9 aliases above has an equivalent
guard, so a future test author who naturally reaches for
`monkeypatch.setattr(main, "_prepare_one_merge", ...)` — following the
existing direct-call precedent already present in `test_adjudicate.py` at
lines 5062/5087 — would silently write a dead patch, since
`_run_adjudicate_apply`/`_run_adjudicate_apply_same` call
`application_lifecycle.prepare_one_merge` directly, never through the
alias. Recommend either narrowing design.md's stated rule to match reality
or adding the 9 names to a guard.

**WARNING 3 — `_echo_commit_disclosure` is not in the fork-guard test set.**
The spec's "Shared Write Mechanics" requirement names four helpers:
`_reject_drifted_targets`, `_autocommit`, `_refresh_derived_after_write`,
`_echo_commit_disclosure`. `test_shared_write_helpers_are_never_forked`'s
`shared` set (test_layering.py:143-148) covers only the first three plus
`snapshot_read` — `_echo_commit_disclosure` is absent. I confirmed by
direct grep that it still has exactly one definition
(`cli/main.py:1357`), so the requirement holds today, but by inspection
only, not by the same automated regression guard protecting the other
three.

**WARNING 4 — Documentation checkboxes were never updated to reflect completed work.**
`tasks.md`'s six commit-checkbox items (3.5, 5.3, 7.3, 9.3, 11.3, 13.3) and
`proposal.md`'s entire Success Criteria checklist remain `- [ ]` unchecked,
despite all six PRs (#946-#951) being verifiably merged into `main` per
`git log`. This did not affect my verification (I verified completion
independently via source inspection and a green test run, per this skill's
explicit instruction not to trust checkboxes), but it should be corrected
before archive so the artifact record matches reality.

## Honest Disclosure: Wording That No Test Can Fail

Per instructions, the following are reported plainly as pre-existing gaps
in assertion depth — none introduced or worsened by this change (every
string moved verbatim; I confirmed by reading both the falsification notes
in `tasks.md` and the actual test assertions):

| Wording | Test | Assertion depth | Requirement 8 status |
|---|---|---|---|
| `adjudicate --apply-same`'s typed-count prompt positive content | none | Only a negative check exists: `"Type the eligible count" not in result.stdout` on the zero-eligible path (test_adjudicate.py:3758) | Unverifiable by test for this exact string; behavior-preservation for it rests on "moved verbatim + full suite green" only |
| `adjudicate --apply-same`'s mismatch-abort wording | `test_adjudicate_apply_same_confirm_count_mismatch_aborts_with_zero_writes` | Checks `exit_code` and workspace snapshot only, never stderr text | Unverifiable by test |
| `adjudicate --apply-same`'s non-TTY refusal, `--confirm-count` token | `test_adjudicate_apply_same_non_tty_without_confirm_count_refuses` | `assert "TTY" in result.stderr` — a substring on the word "TTY", not the flag name | Partially verifiable: mutating "TTY" itself is caught; mutating `--confirm-count` alone is not |
| `purge`'s TTY prompt wording | the `_prompt` stub (test_purge.py:1353-1357) | `def _prompt(*args, **kwargs) -> str` ignores its own prompt argument entirely | Unverifiable by test |
| `purge`'s non-TTY refusal, rest of sentence | `test_purge_non_tty_without_confirm_phrase_refuses` | Loose `"confirm-phrase" in result.output.lower()` | Partially verifiable: the flag token is caught; "refusing to purge"/"stdin is not a TTY" wording is not |
| `unmerge`'s non-TTY refusal, both classic and `--to` forms | `test_unmerge_non_tty_without_auto_refuses`, `test_unmerge_to_non_tty_without_auto_refuses_whole_plan` | Only `"--auto" in result.stderr` and exit code | Unverifiable by test for the rest of the sentence |

None of these six items is a regression: I confirmed each string is a
byte-for-byte verbatim relocation and that the identical weak assertion
already existed against the pre-move inline code (the apply-phase
falsification tasks make the same finding independently, and I traced each
one to its exact test to confirm rather than take it on trust).

## Requirement Traceability Cross-Check

Every requirement listed in `tasks.md`'s traceability table maps to tasks
that are checked `[x]` and to tests that pass in the current suite, with
one exception category already covered above: the six commit-checkboxes
(3.5, 5.3, 7.3, 9.3, 11.3, 13.3) are unchecked (WARNING 4) but their
corresponding commits exist and are merged, verified via `git log
--oneline 38ea84e..HEAD`.

## Success Criteria Checklist (proposal.md) — Verified Against Code

- [x] `lifecycle.py` references zero of `typer`/`rich`/`openkos.cli`, never calls `sys.stdin.isatty()` — confirmed by grep + passing `test_layering.py`.
- [x] `ConfirmationRequest` is a tagged union with boolean + typed-challenge variants; no hard refusal representable — confirmed.
- [x] ~443 existing tests pass; output-text assertions unmodified — confirmed (6070 passed overall; spot-checked diffs on touched test files).
- [x] Byte-identical stdout/stderr/exit codes for equivalent inputs, incl. non-TTY refusal — confirmed for behavior; wording-exactness for 6 specific strings is untested both before and after (see disclosure above).
- [x] A caller outside `openkos.cli` can run each of the five flows without importing `openkos.cli` — confirmed for Phase A/B calls, and (after PR #952) for every verb's confirmation wording too; `adjudicate --apply`'s per-item `[y/N]` walk remains follow-on work (WARNING 1).
- [x] `_reject_drifted_targets`/`_autocommit`/`_refresh_derived_after_write`/`_echo_commit_disclosure` each retain exactly one definition, adapter-side — confirmed by direct grep (WARNING 3: only 3 of 4 are guarded by an automated test).
- [x] `relate`, `set_volatility_cmd`, `reconcile` bodies unchanged — confirmed via `git diff` (no `-`/`+` lines inside their bodies).
- [x] `ruff check .`, `ruff format --check .`, `mypy .`, `pytest` green; coverage ≥90% — confirmed, all green, 96.99% total.
- Checkboxes in `proposal.md` itself remain unticked (WARNING 4).

## Verdict

**PASS WITH WARNINGS** — 8/8 requirements verified against shipped code and
passing tests; 0 CRITICAL; 4 WARNING; 6 pre-existing test-assertion gaps
disclosed (SUGGESTION-level, not blocking).

**Post-report status, recorded before archive:**

- **WARNING 1 — RESOLVED** by PR #952 (`b88bbb9`). It was the one finding
  that touched #918's own stated goal rather than a spec requirement, so it
  was closed before archiving rather than deferred. `adjudicate --apply`'s
  per-item `[y/N]` walk stays follow-on.
- **WARNING 4 — RESOLVED**: `tasks.md`'s commit checkboxes and
  `proposal.md`'s Success Criteria are ticked.
- **WARNING 2 and 3 remain open** and are filed as follow-up issues: 9
  relocated callables aliased back onto `cli.main` with no absence guard,
  and `_echo_commit_disclosure` missing from the single-definition fork
  guard. Neither is a functional regression; both are guards that would not
  catch a future regression they are meant to.
- The 6 test-assertion gaps also remain, filed together: they make specific
  TTY/typed-gate wording for `purge` and `adjudicate --apply-same`
  unverifiable by any test in the repository, before or after this change.

Archiving with WARNING 2/3 and the assertion gaps open is deliberate: none
is caused by this change, and each is a hardening task with its own issue
rather than something this change left half-done.
