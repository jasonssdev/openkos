# Tasks: Durable pending work — the contradictions vertical

## Slicing rationale (D6 compliance, read before the task list)

The design body's slice plan (`design.md` Review Workload Forecast, A → B → C)
put the `purge`/`forget` decision-subtree sweep in slice **C**, after slice
**B** already lands the `--decline`/`--reopen` verbs that first write a file
under `bundle/.state/decisions/`. The maintainer decision **D6**
(`design.md`, "Maintainer decisions — 2026-08-12") rejects that ordering:
*"The sweep MUST land in the SAME slice that first writes a decision file...
No version of the tracker branch, and no version of `main`, may exist in
which a decision file is written but `purge` does not reach it."*

This tasks file satisfies D6 by construction, using an ordering the design
body did not consider rather than one oversized PR:

- **Slice A** creates `bundle/decisions.py` (`write_decisions`/
  `read_decisions`/`iter_decisions`/`decision_key_for`) but wires it to
  **no CLI verb**. No operator action can produce a `bundle/.state/
  decisions/**` file yet — the module exists, but nothing outside a unit
  test calls its writer. D6 does not apply to this slice: there is no
  decision-writing capability on any branch yet.
- **Slice B** is the first slice that makes writing a decision file reachable
  from the CLI (`contradictions --decline/--reopen`). Per D6 it MUST NOT land
  without the sweep already present on the same branch. Slice B is
  therefore split into two chained PRs that land in this fixed order:
  - **B1 — sweep infrastructure first.** `purge`/`forget` decision-subtree
    sweep, `_purge_rebuild_indexes`'s `findings.db` deletion, and the
    `vcs/git.py` path-validation RED test. Its own tests build fixture
    decision files directly via `bundle.decisions.write_decisions` (already
    shipped by Slice A) — no CLI writer needed to exercise the sweep.
  - **B2 — the writer, landing on top of B1.** `contradictions
    --decline/--reopen/--declined`, the D3 declined-listing view, and the
    `_autocommit` scoped-staging wiring. By the time B2's branch exists, B1
    is already an ancestor commit, so **every commit that can write a
    decision file already has the sweep that reaches it** — there is no
    tracker-branch or `main` state, and no intermediate branch state either,
    in which a decision write outruns the sweep.

  B1 and B2 together are one design slice (D6's "SAME slice" requirement,
  read as one deliverable work unit) split into two review-sized PRs purely
  for the 400-line budget — never split by the property D6 protects.
- **Slice C** (the `next` tier + honesty-guard tests) has no privacy
  implication and stays last, unchanged from the design body.

This makes the forecast larger than the design body's ~450/~350/~400 split,
exactly as D6's rationale accepts: *"the first slice is larger than the
design forecast. Accepted."*

## Review Workload Forecast

| Slice | Contents | Est. changed lines (add+del, incl. tests) | 400-line budget risk | Chained PR |
|---|---|---|---|---|
| A | `state/findings.py`, `bundle/decisions.py` (unwired), `decision_key_for`, per-input staleness, curate stage persist, ADR-0014, curate-command spec delta (D1, D5) | ~450 | High | PR #1 (base: tracker) |
| B1 | `purge`/`forget` decision-subtree sweep, `_purge_rebuild_indexes` `findings.db` deletion, `vcs/git.py` path-validation RED test, privacy-purge + forget-command spec deltas | ~380 | High | PR #2 (base: PR #1) |
| B2 | `contradictions --decline/--reopen/--declined`, D3 declined-listing view, `_autocommit` scoped-staging wiring, workspace-autocommit spec delta | ~360 | High | PR #3 (base: PR #2) |
| C | `next_action.py` `_tier_open_contradictions`, honesty-guard tests, pending-work spec's rankability requirement | ~180 | Low | PR #4 (base: PR #3) |

Total forecast: ~1,370 changed lines against a 2,000-line session
`review_budget_lines`.

`400-line budget risk: High` (A, B1, B2 each individually near/over 400)
`Chained PRs recommended: Yes`
`Chain strategy: feature-branch-chain` (D7) — PR #1 targets
`tracker/durable-pending-work`; PR #2 targets PR #1's branch; PR #3 targets
PR #2's branch; PR #4 targets PR #3's branch. Only the tracker merges to
`main`.
`Decision needed before apply: No` (delivery strategy is `auto-chain`)

**Constraint restated so it survives resequencing:** PR #2 (B1) MUST merge
into its base branch before PR #3 (B2) is opened for review — B2's diff is
only safe to review once B1's sweep is an ancestor commit. If B1 and B2 are
ever folded or reordered during review, re-validate the D6 property (a
decision-writing commit is never reachable without the sweep already
present) before requesting re-review, per the pre-pr trap recorded from
`#550`'s delivery (revalidate after every fold).

## Current suite size and expected growth

Current: 4287 passing, 1 skipped.

| After PR | Expected total passing (approx.) |
|---|---|
| PR #1 (A) | ~4287 + 18-22 new = ~4307 |
| PR #2 (B1) | + 10-14 new = ~4319 |
| PR #3 (B2) | + 14-18 new = ~4335 |
| PR #4 (C) | + 6-8 new = ~4342 |

Exact counts depend on parametrization; state the actual new total in each
PR body against the actual `pytest` run, unpiped.

## Per-slice quality-gate checklist (every PR)

- [ ] `uv run pytest <focused path> -v` — unpiped, not through `tail`
- [ ] `uv run pytest` (full suite) — unpiped, confirms no regression elsewhere
- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run mypy .` (whole repo, not `src/` only)
- [ ] For every RED-test task below: confirm the test fails before the GREEN
      commit, then purge `__pycache__` before the GREEN re-run (a same-byte-
      length revert can otherwise execute stale bytecode and report a false
      pass)

---

## PR #1 — Slice A: findings + decisions primitives, curate persistence (base: tracker branch)

### Phase 1: `.openkos/findings.db`

- [x] A1.1 RED: `tests/unit/state/test_findings.py` — `record_findings`
      writes a row; `open_findings` reads back verdict, confidence,
      rationale, and per-input digest rows, using
      `state.derived.open_derived_connection` (`state/derived.py:137`).
- [x] A1.2 GREEN: create `src/openkos/state/findings.py` — schema, `record_findings`,
      `open_findings`. Does not participate in `derived.MANIFEST_HASH_KEY`
      gating (design Decision 1: staleness is per-row, not whole-store).
- [x] A1.3 RED: staleness unit test — mutate one of two input digests, assert
      only that finding's row is marked stale, the other is not.
- [x] A1.4 GREEN: per-input staleness evaluation in `findings.py`, using
      `state.vectorstore.content_hash` (`state/vectorstore.py:226`) over raw
      bytes, per Decision 2's input table (typed-edge: both concept files'
      bytes + `relation_type` label string; merged-body: `survivor_before`
      and `absorbed_snapshot` strings).

### Phase 2: `decision_key` and `bundle/decisions.py` (unwired — see slicing rationale)

- [x] A2.1 RED: `tests/unit/bundle/test_decisions.py` — `decision_key_for`
      produces a stable 32-hex-char key (mirrors `_ORIGIN_KEY_HEX_CHARS`,
      `model/okf.py:147`) from `("contradiction/v1", pair_ids[0],
      pair_ids[1], merged_absorbed_id or "")`; a different `merged_absorbed_id`
      with identical `pair_ids` produces a different key (Decision 3).
- [x] A2.2 GREEN: `decision_key_for` in `src/openkos/bundle/decisions.py`.
- [x] A2.3 RED: path-mapping test mirroring `concept_path_for`'s own suite
      (NFC/NFD on a byte-exact filesystem) for
      `bundle/.state/decisions/<pair_ids[0]>.decisions.okf`.
- [x] A2.4 GREEN: `decisions_root`, `decisions_path_for` (using
      `okf.concept_path_for(concept_id, decisions_root, suffix=".decisions.okf")`,
      `model/okf.py:1296`), `read_decisions`, `write_decisions` (via
      `okf.dump_frontmatter({...}, body="")`, ADR-0002 invariant 3),
      `iter_decisions`. Leaf module — MUST NOT import `openkos.graph`
      (AGENTS.md:41). **Not called from any CLI verb in this PR.**
- [x] A2.5 RED: layering test — `bundle/decisions.py` imports nothing from
      `openkos.graph` (mirrors `tests/unit/resolution/test_layering.py`'s
      shape for the resolution layer).

### Phase 3: Curate stage persistence

- [x] A3.1 RED: `tests/unit/cli/test_curate.py` (or equivalent) — the
      Contradictions stage (`cli/curate.py:1272-1323`) calls
      `findings.record_findings` once per verdict in `batch.results`, after
      the existing echo loop, before returning `StageOutcome`.
- [x] A3.2 GREEN: wire the persist call into
      `cli/curate.py`'s Contradictions stage handler.
- [x] A3.3 RED: byte-compare test — `bundle/` is byte-identical before and
      after a Contradictions stage run that persists findings (curate-command
      spec: "Contradictions Stage Is Report-Only And Last").
- [x] A3.4 RED: `curate` resumability test — two consecutive `curate`
      invocations re-derive the Contradictions candidate queue from current
      bundle state regardless of whether a finding for a pair is already
      persisted (curate-command spec: "Resumability By Construction" delta).

### Phase 4: Documentation and spec deltas

- [x] A4.1 Create `docs/adr/0014-durable-pending-work-stores.md` from
      `docs/adr/template.md`, status `Proposed`, citing ADR-0013; states (a)
      decisions extend ADR-0013 by exactly one kind, (b) findings live in
      `.openkos/` under `vectors.db`'s delete-without-rebuild posture, (c) the
      read-time `decision_key` join is why no two-phase write is needed here
      (Decision 7).
- [x] A4.2 Add ADR-0014's row to `docs/adr/README.md` index.
- [x] A4.3 Land `openspec/specs/curate-command/spec.md` deltas exactly as
      drafted in `specs/curate-command/spec.md` of this change (D1, D5) —
      apply per this project's OpenSpec archive convention.

### PR #1 quality gates

- [x] Focused: `uv run pytest tests/unit/state/test_findings.py tests/unit/bundle/test_decisions.py tests/unit/cli/test_curate.py -v`
      — 144 passed
- [x] Full suite, ruff check, ruff format --check, mypy . — all green
      (4314 passed, 1 skipped; baseline was 4287 passed, 1 skipped — +27 new
      tests, no regressions)
- [x] Rollback: `git revert` PR #1; `.openkos/findings.db` and
      `bundle/decisions.py` are both new, unreferenced by any other shipped
      code path, so revert loses nothing durable (findings are recomputable;
      no decision was ever written)

---

## PR #2 — Slice B1: privacy sweep lands before the writer (base: PR #1 branch)

### Phase 1: `vcs/git.py` path validation (threat matrix row)

- [x] B1.1 RED: a decision path containing `==>` is rejected by
      `_validate_rel_paths` (`vcs/git.py:515-551`) before it reaches
      `expunge_targets` — construct a concept id containing `==>`, assert the
      purge preparation step refuses rather than silently mis-parsing the
      rename directive `_validate_rel_paths`'s own docstring warns about
      (`vcs/git.py:535-544`).
- [x] B1.2 GREEN: the decision-path construction in `expunge_targets` (see
      B2 below) always routes through the existing validated `literal:`
      path builder — no new validation logic needed if the existing call
      site already validates every appended member; confirm and, if not,
      add the guard before appending. (Confirmed insufficient on its own —
      `vcs_git.expunge_paths`'s own validation runs too late, past the
      point of no return, to refuse cleanly. Added an explicit
      `vcs_git._validate_rel_paths(expunge_targets)` call in Phase A,
      inside the existing `except (OSError, ValueError)` clause, so a
      malformed decision path refuses with a clean CLI message rather than
      an uncaught traceback mid-rewrite.)

### Phase 2: `purge` sweep — the D6 hazard

- [x] B1.3 RED:
      `tests/unit/cli/test_purge.py::test_purging_a_concept_removes_its_decision_from_history`
      — construct a `bundle/.state/decisions/<id>.decisions.okf` file via
      `bundle.decisions.write_decisions` (Slice A, no CLI writer needed),
      referencing `pair_ids = (purged_id, other_id)`; run `purge` on
      `purged_id`; assert via `git rev-list --objects --all` + `git
      cat-file` that no historical blob of the decision path contains
      `purged_id`, and that the rewrite ran in the SAME `git filter-repo`
      pass as the concept's own file expunge (privacy-purge spec, Scenario
      1). This test MUST fail before B1.4/B1.5 exist. (Confirmed RED by
      temporarily stashing the `cli/main.py` implementation and
      re-running; also added a second RED/GREEN test for the FOREIGN-file
      case — a decision file owned by a live concept whose record names
      the purge target — since B1.4's scope is broader than the ledger
      sidecar's own-file-only precedent.)
- [x] B1.4 GREEN: extend `expunge_targets` (`cli/main.py:4905-4945`) — for
      each purge-set member, append its OWN
      `bundle/.state/decisions/<member>.decisions.okf` path (when
      `pair_ids[0] == member`) AND every OTHER live decisions file whose
      `pair_ids` contains `member`, mirroring the existing ledger-sidecar
      append at `cli/main.py:4940-4945`. No second `git filter-repo`
      invocation. (New helper `_decisions_history_targets`.)
- [x] B1.5 GREEN: extend `_sweep_ledger_sidecars_for_ids`'s pattern
      (`cli/main.py:602-654`) with a decisions-subtree counterpart — a
      purge-set member's OWN decisions file is deleted outright; any OTHER
      live decisions file whose `pair_ids` names the member has that record
      dropped/rewritten (or the file removed if no records remain). Reuses
      `bundle.decisions.iter_decisions` (Slice A) as the INCLUDE-walk
      primitive. (New shared primitive `_sweep_decisions_for_ids`, used by
      both `purge` and `forget`.)
- [x] B1.6 RED:
      `tests/unit/cli/test_purge.py::test_purging_an_unrelated_concept_leaves_decision_untouched`
      — a decision file referencing a concept outside the purge set stays
      byte-identical in every historical commit (privacy-purge spec,
      Scenario 2).
- [x] B1.7 RED/GREEN: wire B1.5's sweep result's touched paths into the
      post-rewrite auto-commit's `commit_paths_rel`, mirroring the ledger
      sidecar precedent.

### Phase 3: `forget` sweep

- [x] B1.8 RED:
      `tests/unit/cli/test_forget.py::test_forgetting_a_concept_removes_its_live_decision_entry`
      — `openkos forget <id>` Phase B removes the live decision entry
      referencing `<id>` from every `bundle/.state/decisions/**` file, live
      tree only, no history rewrite (forget-command spec, Scenario 1).
      (Plus a second test for the FOREIGN-file case, mirroring B1.3's own
      split.)
- [x] B1.9 GREEN: wire B1.5's shared decisions-sweep primitive into
      `forget`'s Phase B write and `_autocommit` path list, same pattern as
      the ledger sidecar sweep already wired there.
- [x] B1.10 RED:
      `tests/unit/cli/test_forget.py::test_forgetting_a_concept_leaves_unrelated_decision_entry`
      — a decision entry for an unrelated concept is left unchanged
      (forget-command spec, Scenario 2).

### Phase 4: `_purge_rebuild_indexes` — findings.db

- [x] B1.11 RED: `_purge_rebuild_indexes` (`cli/main.py:4658-4684`) test —
      after `purge`, `.openkos/findings.db` no longer exists on disk and is
      NOT rebuilt in-line (mirrors `vectors.db`'s posture, design Decision
      1's rebuild-posture table).
- [x] B1.12 GREEN: add `layout.findings_db_path` (or equivalent) to the
      delete tuple at `cli/main.py:4672-4676`; do not add it to the
      fts/graph rebuild calls below that tuple.

### PR #2 quality gates

- [x] Focused: `uv run pytest tests/unit/cli/test_purge.py tests/unit/cli/test_forget.py -v`
      — 127 passed
- [x] Full suite, ruff check, ruff format --check, mypy . — all green
      (4322 passed, 1 skipped; baseline was 4314 passed, 1 skipped — +8 new
      tests, no regressions; coverage 97.00% against a 90% branch gate)
- [x] Rollback: `git revert` PR #2; sweep code becomes dead again (no writer
      exists until PR #3), so revert re-opens no privacy gap that did not
      already exist before this PR (still no decision-writing verb on any
      branch)

---

## PR #3 — Slice B2: the writer, landing on top of the sweep (base: PR #2 branch)

### Phase 1: `contradictions --decline`

- [ ] B2.1 RED:
      `tests/unit/cli/test_contradictions.py::test_decline_writes_a_decision_and_hides_the_finding`
      — `openkos contradictions --decline <pair-identity>` writes a
      `bundle/.state/decisions/**` record with `state: declined`, and a
      subsequent `contradictions` run (or `--declined` view, Phase 3) no
      longer shows it in ordinary output (pending-work spec: "Declining Is A
      Non-Interactive Verb...").
- [ ] B2.2 GREEN: `contradictions` gains `--decline`, addressing a finding
      by its `decision_key`-derived identity (sorted `pair_ids` +
      `merged_absorbed_id`), short-circuiting before the graph build and LLM
      client (design File changes table), calling
      `bundle.decisions.write_decisions`.
- [ ] B2.3 RED: `--decline` with no matching findings row still succeeds
      (Decision 7 corollary — decline never reads the findings store as a
      precondition; the row may have been purged).
- [ ] B2.4 RED: a typed-edge and a merged-body candidate sharing the same
      `pair_ids` stay distinct — declining one does not affect the other
      (pending-work spec, Scenario "stay distinct").

### Phase 2: `contradictions --reopen`

- [ ] B2.5 RED:
      `tests/unit/cli/test_contradictions.py::test_reopen_reinstates_a_declined_finding`
      — explicit `--reopen <identity>` flips a declined decision back to
      open; ranking eligibility is restored (pending-work spec: "Re-Opening
      A Declined Finding Requires Explicit Operator Action").
- [ ] B2.6 GREEN: `--reopen` handler, same short-circuit shape as
      `--decline`.
- [ ] B2.7 RED: content-change-does-not-reopen test — a declined finding
      whose concept is edited is marked stale on recompute (Slice A's
      staleness), NOT reopened, and stays hidden (pending-work spec,
      Scenario "Content change does not silently reopen a decline").

### Phase 3: `contradictions --declined` listing view (D3)

- [ ] B2.8 RED:
      `tests/unit/cli/test_contradictions.py::test_declined_view_lists_declined_findings`
      — `openkos contradictions --declined` shows a declined finding,
      identified and marked declined; ordinary `contradictions`/`status`/
      `next` output does not show it (pending-work spec: "Declined Findings
      Are Hidden By Default...").
- [ ] B2.9 GREEN: `--declined` flag/view.
- [ ] B2.10 RED: stale-but-declined-or-open findings remain visible as
      `stale` in the declined-listing view or `status`, never silently
      omitted (pending-work spec, Scenario "A stale finding remains visible
      as stale").

### Phase 4: `_autocommit` scoped-staging hazard

- [ ] B2.11 RED:
      `tests/unit/cli/test_contradictions.py::test_decline_stages_only_the_decision_path`
      — run `--decline`, assert the `bundle/.state/decisions/**` path is in
      `_autocommit`'s committed set (`cli/main.py:929`, `git add -- <paths>`,
      never `-A`); mutate the decline command to drop that path from the
      caller's list passed to `_autocommit` and confirm the test goes red;
      revert the mutation and purge `__pycache__` before re-confirming green
      (workspace-autocommit spec: "Scoped Staging Only" delta).
- [ ] B2.12 GREEN: decline/reopen handlers return their written decision
      path; the command passes it into `_autocommit`'s `paths` argument,
      mirroring `MergeResult.ledger_sidecar_path`'s existing pattern
      (`cli/main.py:6934-6948`).
- [ ] B2.13 RED: unrelated pre-existing dirty file in the workspace is left
      untouched by a `--decline` run's `_autocommit` call (workspace-
      autocommit spec, Scenario "Unrelated dirty file is left untouched").

### PR #3 quality gates

- [ ] Focused: `uv run pytest tests/unit/cli/test_contradictions.py -v`
- [ ] Full suite, ruff check, ruff format --check, mypy . — all green
- [ ] Rollback: `git revert` PR #3; the sweep from PR #2 remains an ancestor
      commit on the branch, so no decision-writing capability survives the
      revert either (the CLI verb that wrote it is gone)
- [ ] Before requesting review: re-validate the pre-pr gate against B1+B2
      combined if any commit was folded across the two, per the `#550`
      revalidate-after-every-fold trap

---

## PR #4 — Slice C: `next` tier and the honesty guard (base: PR #3 branch)

### Phase 1: New tier

- [ ] C1.1 RED:
      `tests/unit/cli/test_next_action.py::test_open_contradiction_is_ranked`
      — one open, non-stale, non-declined persisted finding, no higher tier
      fires, `next` returns that finding as its action (pending-work spec,
      Scenario "An open contradiction is ranked").
- [ ] C1.2 GREEN: `_BundleSignals.open_contradictions` + new
      `_tier_open_contradictions`, appended LAST in `_TIERS`
      (`next_action.py:599-607`), recommending `openkos contradictions`
      (`cli/main.py:10196`).

### Phase 2: Honesty guard regression

- [ ] C2.1 RED:
      `tests/unit/cli/test_next_action.py::test_stale_or_declined_only_yields_none_action`
      — a bundle whose only findings are stale or declined yields
      `action is None`; the rendered output still carries `_STATUS_POINTER`
      (`next_action.py:71-75`) and does not assert the bundle is clean
      (pending-work spec, Scenario "An unranked finding does not become a
      false all-clear"; design Decision 6).
- [ ] C2.2 GREEN: confirm `_tier_open_contradictions`'s guard clause (open ∧
      not stale ∧ not declined) already satisfies C2.1 without touching
      `next_action.py:616-621`'s `None`-action contract or `_NO_ACTION_LINE`
      (`:77-80`).
- [ ] C2.3 RED then GREEN: mutation-test the tier's guard — temporarily
      widen it to fire on a stale finding, confirm C2.1 goes red, revert,
      purge `__pycache__`, confirm green again.

### PR #4 quality gates

- [ ] Focused: `uv run pytest tests/unit/cli/test_next_action.py -v`
- [ ] Full suite, ruff check, ruff format --check, mypy . — all green
- [ ] Rollback: `git revert` PR #4; `next` loses the new tier only, all
      other tiers and the honesty guard are untouched

---

## Requirement traceability

| Spec requirement | Tasks |
|---|---|
| pending-work: Contradiction Findings Are Persisted With Provenance | A1.1-A1.4, A3.1-A3.2 |
| pending-work: Persisted Findings Are Rankable, Honesty Guard Preserved | C1.1-C2.3 |
| pending-work: Declining Is A Non-Interactive Verb | A2.1-A2.4 (identity), B2.1-B2.4 |
| pending-work: Declined Findings Hidden By Default, Explicit Listing View | B2.8-B2.10 |
| pending-work: Re-Opening Requires Explicit Operator Action | B2.5-B2.7 |
| pending-work: A Finding Is Invalidated Honestly When Its Inputs Change | A1.3-A1.4, B2.7, C2.10 |
| curate-command: Contradictions Stage Is Report-Only And Last (delta) | A3.1-A3.3 |
| curate-command: Resumability By Construction (delta) | A3.4 |
| workspace-autocommit: Scoped Staging Only (delta) | B2.11-B2.13 |
| privacy-purge: Whole-History Expunge Covers The Pending-Work Decision Subtree | B1.1-B1.7 |
| forget-command: Forget Sweeps Live Decision Entries | B1.8-B1.10 |

## Parallelization

Sequential only: A → B1 → B2 → C is a strict dependency chain (D7 chain
strategy; D6 forces B1 before B2). Within a single PR, phases are also
sequential (each depends on the prior phase's GREEN state), except:

- A1 (findings.db) and A2 (decisions.py primitives) touch disjoint modules
  and MAY be worked in parallel within PR #1, provided A3 (curate wiring)
  waits for both.
- B1.1-B1.2 (git.py validation) and B1.3-B1.7 (purge sweep) MAY be worked in
  parallel within PR #2, since B1.4 only needs the validation contract to
  already exist, not a specific commit order.
