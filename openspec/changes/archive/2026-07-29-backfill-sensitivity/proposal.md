# Proposal: Backfill sensitivity onto existing provenance descendants (#231)

## Intent

Since #219, raising a Source's `sensitivity` propagates to its provenance descendants — but
only from that moment forward. Every bundle built before #219, and every descendant created
by a path that predates it, is still sitting below its Source's level with no way to close the
gap except re-running `set-sensitivity` by hand, Source by Source, guessing which ones matter.
#219 deferred bulk backfill explicitly ("deliberately out of scope by user decision"). #230
(merged) retargets `provenance:` on merge, so descendant closures now resolve correctly across
merged Sources — which is what unblocked this.

Two user-visible gaps today:

1. **No signal.** `lint` and `status` never say a derived object is under-classified relative
   to its Source. The bundle looks clean while it is not.
2. **No remedy.** There is no one-shot, previewable, raise-only sweep to fix it.

MVP 1 scope. Raise-only, fail-closed, over-classify-never-under-classify — the invariant #219
established (ADR-0003, ADR-0009).

## Scope

### In Scope

- Extract the inline descendant scan (`main.py:3339-3411`) into one shared, per-Source helper
  in `bundle/provenance.py` — **closes #235**.
- Fix the Phase B partial-write failure message so it names the paths that already landed —
  **closes #233**.
- Read-only detection: a `lint`/`status` finding reporting (a) descendants below their Source
  and (b) multi-source descendants the backfill cannot cover.
- A dedicated `backfill-sensitivity` verb: one bundle-wide preview, one confirmation, one
  `log.md` entry, one `_autocommit`.
- ADR-0012 recording the per-Source sweep and its coverage limit.

### Out of Scope (non-goals)

- **#232** — the dangling-provenance warning's bundle-wide scope. Confirmed independent
  overlap in #230's `explore.md:134-136`; touches warning emission, not this write path.
- **#234** — the ambiguous "failed while preparing" message. Deferred: it sits on a surface
  backfill does not share (backfill authors its own preparation messages), and slice 1 makes
  it *cheaper* to fix later by moving descendant resolution behind a helper boundary. P3.
- **Multi-source high-water-mark combination.** Deferred to MVP-2/3 by ADR-0009; see below.
- Any downgrade path. No `--allow-downgrade` equivalent — the verb is raise-only by
  construction, so ADR-0008's gate does not apply.
- Re-triggering extraction or touching `extraction_status`. Backfill is purely additive to
  `sensitivity`.

## Fixed decisions (user-confirmed, not reopened)

| Decision | Contract |
|---|---|
| Multi-source descendants | **Skip and report.** Sweep per Source (`root_ids={one_source_id}`), identical to `set_sensitivity_cmd` today; `lint`/`status` MUST report uncovered multi-source objects explicitly. |
| Shape | Dedicated verb. Not a `set-sensitivity` flag, not folded into `reconcile`. |
| Confirmation | One bundle-wide preview + one confirm. Ladder: `--auto` > `cfg.review: false` > TTY `typer.confirm` > refuse on non-TTY without `--auto`. |
| Reporting | One `log.md` entry and one `_autocommit` for the whole sweep. |

**Why skip-and-report.** `find_provenance_descendants` (`provenance.py:75-139`) pulls a
multi-source derived object into a closure only when *every* cited id is already in the root
set, so a per-Source sweep never reaches it. Combining across Sources would pull ADR-0009's
deferred MVP-2/3 semantics forward. Skipping silently would leave objects under-protected with
no signal. Reporting is the compensating control: the tool never lies by silence.

## Scope decisions with evidence

| Question | Decision | Evidence |
|---|---|---|
| Fold in **#235**? | **Yes — slice 1.** This change closes #235. | Backfill and the detection finding need the identical walk. Without extraction, backfill duplicates ~70 lines of `main.py:3339-3411`, violating the repo's own "no-fifth-walk"/no-duplicate-scan convention (`main.py:5044-5045, 5110-5112`). #219's archive-report item 4 already names it. Pure refactor; the 29 tests in `tests/unit/cli/test_set_sensitivity.py` are the regression guard. |
| Fold in **#233**? | **Yes — slice 1, as a separate commit after the byte-identical refactor.** | Under a fail-closed, no-rollback contract the operator MUST know what landed. `set-sensitivity` fails over 1-2 files; backfill fails over N descendants across M Sources. A message naming none of them is not a nicety there — it is the difference between a recoverable and an unrecoverable partial state. |
| Fold in **#234**? | **No.** | Message-disambiguation defect confined to `set_sensitivity_cmd`'s preparation phase. Backfill does not inherit it. Folding it in would add message assertions to slice 1's byte-identical regression set for no gain. P3. |
| Verb name | **`backfill-sensitivity`.** | Matches `set-sensitivity`'s `verb-noun` form, used precisely because the verb alone is meaningless — `backfill` alone is equally ambiguous (backfill embeddings? extraction?). Single-word verbs in the vocabulary (`merge`, `forget`, `reindex`, `purge`) all have unambiguous domains. Reads as `set-sensitivity`'s sibling in `--help`. Rejected: `propagate` (could mean edges), `sweep` (no domain), `reconcile-sensitivity` (collides with `reconcile`'s human-judged contradiction domain). |
| New ADR? | **Yes — ADR-0012**, next free number per `docs/adr/README.md` (0011 is the last). Title: *Sensitivity backfill sweeps per Source; multi-source closures are reported, not combined.* | Precedent: ADR-0010 was added for re-ingest's raise-only resolution, itself an application of ADR-0003/0009. The durable decision here is new: an existing-bundle gap is closed by an explicit operator-run sweep rather than an automatic migration, and a deliberate coverage limit is compensated by a detection signal instead of silently accepted. |

## Capabilities

### New Capabilities

- `sensitivity-backfill`: the `backfill-sensitivity` verb — bundle-wide per-Source sweep,
  preview/confirm ladder, raise-only staging, single log entry and autocommit, idempotency,
  partial-failure contract.

### Modified Capabilities

- `lint`: new below-Source sensitivity scan, with a second category for uncovered
  multi-source descendants (shape follows `Requirement: Unextracted-Source Scan`).
- `status`: "Needs attention" surfaces the same two categories (follows
  `Requirement: Needs-Attention Surfaces Unextracted Sources`).
- `sensitivity-config`: `Requirement: Raise-Only Propagation to Provenance Descendants`
  gains a partial-write-failure clause requiring the failure message to name every path that
  already landed (#233).

## Approach

One shared per-Source helper, three consumers, three PRs.

`resolve_source_raises(bundle_snapshot, source_id, level) -> list[DescendantRaise]` lands in
`bundle/provenance.py`, honouring that module's canonical-layer, no-`openkos.graph`-import
contract. `set_sensitivity_cmd` calls it (behaviour unchanged), the lint/status finding calls
it read-only (compute would-be raises, never write), and the backfill verb calls it once per
Source and unions the staged raises.

Backfill Phase B mirrors `set_sensitivity_cmd`: every staged descendant raise, then `log.md`,
then one `_autocommit` over every changed path. No Source's own frontmatter is ever written —
backfill only raises descendants.

## Delivery: three chained PRs (budget 800 lines)

| Slice | Scope | Est. lines | Depends on |
|---|---|---|---|
| 1. Extract helper (#235) + failure message (#233) | Move the scan into `bundle/provenance.py`, re-wire `set_sensitivity_cmd` with characterization tests proving byte-identical behaviour; then a separate RED-first commit changing the partial-failure message to name landed paths | ~130-200 | — |
| 2. Detection finding | `LintDoc` gains sensitivity/provenance awareness; pure `check_below_source_sensitivity`-shaped function returning `LintFinding`s in two categories; wire into `lint` + `status` reusing the single `collect_docs` walk | ~150-250 | 1 |
| 3. `backfill-sensitivity` verb | Typer command, preview/confirm/`--auto`, Phase B write + log + autocommit, docstring, ADR-0012, tests | ~250-400 | 1 |

Total ~530-850. Fits only as three chained PRs (matches `delivery_strategy: auto-chain`), each
at or near the 400-line per-PR default. Slices 2 and 3 both depend on 1 but not on each other.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/bundle/provenance.py` | Modified | New shared per-Source raise-resolution helper |
| `src/openkos/cli/main.py` | Modified | `set_sensitivity_cmd` re-wired; new verb; `lint`/`status` wiring |
| `src/openkos/lint.py` | Modified | `LintDoc` field(s) + new pure detection function |
| `docs/adr/0012-*.md` | New | Per-Source sweep decision and coverage limit |
| `openspec/specs/{lint,status,sensitivity-config}/spec.md` | Modified | Delta specs |
| `tests/unit/{cli,model}/` | Modified/New | Characterization + new behaviour tests |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Slice 1's refactor silently changes behaviour | Med | Characterization tests land RED **before** the move; the 29 existing `test_set_sensitivity.py` tests must stay green byte-for-byte |
| Multi-source objects stay under-protected | High (accepted) | Explicit, tested lint/status category; ADR-0012 records the limit; MVP-2/3 owns the fix |
| Pre-#230 merges left dangling `provenance:` | Low | Excluded from closures (fail-safe) but under-protected; covered by the same detection finding |
| Backfill partial write leaves a mixed bundle | Med | Fail-closed, no rollback, over-classify-never-under-classify (#219's invariant); #233's fix makes the landed set explicit |
| Slice 3 exceeds the 400-line PR budget | Med | ADR-0012 can split into its own trivial docs PR if slice 3 runs long |
| Re-run produces a spurious empty commit | Low | Strict-raise-only staging (`main.py:3393-3394`); pinned by an idempotency test |

## Rollback Plan

Per-slice, since each is an independent PR. Slice 1: revert the extraction commit —
`set_sensitivity_cmd` returns to its inline block, no data touched. Slice 2: revert; the
findings disappear, nothing was ever written. Slice 3: revert the verb; already-backfilled
bundles keep their raised values (raise-only, so reverting the code never under-classifies
anything), and each sweep is one `_autocommit` that can be reverted in Git individually.

## Dependencies

- #230 merged in `main` (`fb968d7`, `3f26c98`) — confirmed. Its archive-report states #231 is
  now unblocked.
- No new runtime dependencies. `find_provenance_descendants` and `combine_sensitivity` are
  pure and stdlib-only.

## Success Criteria

- [x] `backfill-sensitivity` raises every descendant sitting below its Source, in one sweep,
      one preview, one confirmation, one log entry, one commit.
- [x] No descendant is ever lowered by any path in this change.
- [x] A second run immediately after a successful one stages zero writes and creates no commit.
- [x] `lint` and `status` both report descendants below their Source **and** multi-source
      descendants the sweep does not cover, as distinct categories.
- [x] `tests/unit/cli/test_set_sensitivity.py` (29 tests) stays green through slice 1.
- [x] On partial write failure the message names every path that already landed (#233).
- [x] `--auto` skips only the prompt; non-TTY without `--auto` refuses to write.
- [x] ADR-0012 accepted and indexed in `docs/adr/README.md`.
- [x] Issues #231, #235, #233 closed; #232 and #234 remain open and untouched.
