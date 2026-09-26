# Tasks: revises-relation — record a refinement that hides nothing

Refs #1014 piece (a), sub-change 1 of 3. Design: `design.md`. Proposal:
`proposal.md`. ADR-0024 (`docs/adr/0024-revises-relation-and-resolved-pairs.md`)
is already written, status `Proposed`, and its `docs/adr/README.md` index row
already exists — both ship as-is inside slice 1's commit; no task below writes
them.

Strict TDD is ON, runner `uv run pytest`. Every behavioral task pairs a
`[TEST]` task, observed RED with the reason it is RED today (or explicitly
noted as a non-RED regression pin when the design says the touched file is
NOT modified), with the `[IMPL]` task that turns it GREEN — in that order.
`[IMPL]` tasks introduce only what their paired `[TEST]` already pins. Revert
every mutation with the inverse edit (never `git checkout --`), and purge
`__pycache__` before trusting a verdict, per design.md's Testing Strategy
preamble.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~420-500 (design.md "Migration / Rollout": slice 1 ~330-380, slice 2 ~90-120; delta specs excluded) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (constant + reconcile plumbing + ADR/docs) → PR 2 (contradiction candidate exclusion) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

Per-slice estimate (design.md "Migration / Rollout"):

| Slice | Content | Authored lines (est.) |
| --- | --- | --- |
| 1 | `RESOLUTION_RELATION_TYPES` constant + reconcile plumbing (`--revision`, `_resolve_pair_member`, table-driven classifier with `mixed`, notes, preview/log/commit) + ADR-0024 + `docs/cli.md` reconcile section | ~330-380 |
| 2 | `_candidate_pairs` resolved-pair exclusion + `plan_candidates` cost-gate pin + `docs/cli.md` contradictions sentence | ~90-120 |

Total ≈ 420-500, over the ~400 single-PR budget. Slice 1 is useful on its own
(a human can record a refinement by hand). Slice 2 depends only on the
`RESOLUTION_RELATION_TYPES` constant landed in slice 1. If slice 1 comes in
well under its estimate and the running total lands at or below ~400, a
single PR is acceptable — but the default plan below is two stacked PRs.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `RESOLUTION_RELATION_TYPES` constant, `--revision`/`_resolve_pair_member`, table-driven `_existing_reconciliation_state` with `mixed`, roles/sentences/log/echo/commit/preview, `lifecycle`/`listing` pins, `docs/cli.md` reconcile section, ADR-0024 staged | PR 1 → `main` | `uv run pytest tests/unit/model/test_relations.py tests/unit/cli/test_reconcile.py tests/unit/test_lifecycle.py tests/unit/bundle/test_listing.py` | `uv run openkos reconcile <a> <b> --revision <a> --auto` against a scratch `openkos init` workspace with two unreconciled concepts; confirm the edge, both `## Reconciliation` notes, the `log.md` line, and `openkos list` shows both `active` | Revert the `model/relations.py` diff, the `cli/main.py` reconcile-block diff, `docs/cli.md`'s reconcile section, and the four test-file diffs; `_reconcile_pair`'s default `edge_type="supersedes"` keeps `--winner` and `--from-findings` byte-unchanged, so reverting drops only `--revision` |
| 2 | `_candidate_pairs` resolved-pair subtraction before count/cap, `plan_candidates` cost-gate pin, `docs/cli.md` contradictions sentence | PR 2 → PR 1's branch | `uv run pytest tests/unit/resolution/test_contradiction.py` | `uv run openkos contradictions <bundle>` against a scratch workspace holding one `reconcile`d pair and one unrelated typed-edge pair; confirm the resolved pair never appears as a candidate and the unrelated pair is still judged | Revert the `resolution/contradiction.py` diff, `docs/cli.md`'s contradictions sentence, and the `test_contradiction.py` diff; `_candidate_pairs` falls back to its pre-change `derived_from`-only exclusion, and every resolved pair becomes a candidate again exactly as it is pre-change |

## Scenario → Task Coverage

Every scenario in the three delta specs mapped to at least one task below.

| Spec | Scenario | Task(s) |
|---|---|---|
| reconcile-command | Revision writes a single outbound edge | 1.10, 1.12, 1.13 |
| reconcile-command | --revision id not in pair | 1.8, 1.11 |
| reconcile-command | --revision combined with --winner refuses | 1.9, 1.11 |
| reconcile-command | --revision combined with --from-findings refuses | 1.9, 1.11 |
| reconcile-command | revises stays out of the suggestable vocabulary | 1.1, 1.2 |
| reconcile-command | Every refusal cell exits 1 with zero writes | 1.4, 1.7 |
| reconcile-command | A hand-edited pair with conflicting resolutions refuses every request | 1.5, 1.7 |
| reconcile-command | Refusal names the existing resolution | 1.4, 1.7 |
| reconcile-command | Symmetric reconcile (heading correction pin) | 1.6 (one-sided regression guard), existing suite unedited |
| reconcile-command | Winner supersedes loser | 1.4 (transition matrix `directional(A)` cells), unaffected by 1.7/1.11/1.12 |
| reconcile-command | --winner id not in pair | pre-existing test, unaffected — confirmed green by 1.19 |
| reconcile-command | Re-run is a no-op write | 1.4 (transition matrix `symmetric`→`symmetric` cell) |
| reconcile-command | Revision re-run is a no-op write | 1.4 (transition matrix `revision(A)`→`revision(A)` cell) |
| status-aware-retrieval | status field alone marks deprecated | pre-existing test, unaffected — confirmed green by 1.19 |
| status-aware-retrieval | superseded concept is deprecated regardless of its own status | pre-existing test, unaffected — confirmed green by 1.19 |
| status-aware-retrieval | self-reference stays live, but supersedes cycles fail safe to deprecated | pre-existing test, unaffected — confirmed green by 1.19 |
| status-aware-retrieval | A revises edge deprecates neither end, in retrieval or in list STATUS | 1.14, 1.15 |
| contradiction-detection | Symmetric and multi-edge pairs judged once | pre-existing test, unaffected — confirmed green by 2.10 |
| contradiction-detection | Provenance-only bundle yields zero contradiction candidates | pre-existing test, unaffected — confirmed green by 2.10 |
| contradiction-detection | Genuine typed contradiction-eligible edge is still surfaced | 2.5 |
| contradiction-detection | A resolved pair is excluded from candidates, before the count and the cap | 2.1, 2.3, 2.6 |
| contradiction-detection | Resolution exclusion applies regardless of which member holds the edge | 2.1, 2.6 |
| contradiction-detection | An unrelated live pair is still judged | 2.4, 2.6 |
| contradiction-detection | The exclusion keeps the cost gate exact | 2.3, 2.7, 2.6 |

---

## Slice 1 (PR 1 → `main`): the `revises` relation and reconcile plumbing

### `model/relations.py` — shared resolution constant (Decision 1)

- [x] **1.1** [TEST] `tests/unit/model/test_relations.py` — add
  `test_resolution_relation_types_membership`: `RESOLUTION_RELATION_TYPES ==
  frozenset({"supersedes", "reconciled_with", "revises"})`; disjoint from
  `SEEDED_RELATION_TYPES`; disjoint from `SUGGESTABLE_RELATION_TYPES`;
  `"revises"` is not the name of any entry in `REGISTRY`. Covers
  reconcile-command scenario "revises stays out of the suggestable
  vocabulary". **RED today**: `AttributeError` — `RESOLUTION_RELATION_TYPES`
  does not exist. Kills seeding `revises` into `REGISTRY`.
- [x] **1.2** [IMPL] `src/openkos/model/relations.py`: add
  `RESOLUTION_RELATION_TYPES: frozenset[str] = frozenset({"supersedes",
  "reconciled_with", "revises"})` after `ASYMMETRIC_RELATION_TYPES`, with a
  docstring explaining why it is outside `REGISTRY`,
  `SEEDED_RELATION_TYPES`, and `SUGGESTABLE_RELATION_TYPES` (design.md
  Decision 1). Makes 1.1 GREEN.

### `cli/main.py` — table-driven classifier with `mixed` (Decisions 2, 3)

- [x] **1.3** [TEST] `tests/unit/cli/test_reconcile.py` — add
  `test_mode_and_role_tables_cover_every_resolution_type`:
  `set(main._MODE_BY_RESOLUTION_TYPE) == RESOLUTION_RELATION_TYPES` and
  `set(main._DIRECTED_ROLES) == RESOLUTION_RELATION_TYPES -
  {"reconciled_with"}`. **RED today**: `AttributeError` — neither dict
  exists yet. Kills a fourth resolution type added to one table but not the
  other.
- [x] **1.4** [TEST] Same file — add
  `test_reconciliation_transition_matrix`, one parametrized test over all 18
  cells of the transition table (6 existing states × 3 requests: `none`,
  `symmetric`, `directional(A)`, `directional(B)`, `revision(A)`,
  `revision(B)`, requested `symmetric`/`directional(A)`/`revision(A)`).
  Set up each existing state via real `reconcile` runs. Assert: write → the
  edge is present; idempotent no-op → concept bytes unchanged and
  `"already reconciled; no change."` is in `log.md`; refuse → exit `1`,
  `"already reconciled"` is in stderr, and a `_snapshot` taken before the
  run is unchanged after it. Covers reconcile-command scenarios "Every
  refusal cell exits 1 with zero writes", "Refusal names the existing
  resolution", "Re-run is a no-op write", and "Revision re-run is a no-op
  write". **RED today**: the `directional`/`symmetric` cells already pass
  against today's code (write these as regression pins); every cell
  involving `revision(A)`/`revision(B)` as either the existing or the
  requested state fails, because `--revision` is not yet a recognized
  option — confirm the actual Typer/Click usage-error shape before 1.11-1.12
  land. Kills a holder-blind comparison and a `revision` holder returned as
  `None`.
- [x] **1.5** [TEST] Same file — add a `_set_relations(tmp_path, cid, [...])`
  helper (via `load_frontmatter`/`encode_relations`/`dump_frontmatter`, per
  design.md's Testing Strategy row "CLI (mixed)") and
  `test_mixed_resolution_state_refuses_every_request`, parametrized over the
  five hand-set combinations: `revises` both ways; `supersedes` both ways;
  `revises` + `supersedes`; `revises` + `reconciled_with`; `supersedes` +
  `reconciled_with`. For each, assert every one of the three requests
  (symmetric, `--winner` either member, `--revision` either member) exits
  `1`, that `"conflicting resolutions"` is in stderr, and that concept bytes
  are unchanged. Covers reconcile-command scenario "A hand-edited pair with
  conflicting resolutions refuses every request". **RED today**:
  `AttributeError`/`ImportError` — `_MODE_BY_RESOLUTION_TYPE` does not exist
  and `_existing_reconciliation_state` has no `mixed` branch. **Kills
  removal of the `len(found) > 1` branch specifically via the message
  assertion**: without that branch, tuple-unpacking two-or-more results
  raises a bare `ValueError`, which still exits `1` — only asserting
  `"conflicting resolutions"` in stderr (not just the exit code) kills that
  mutation.
- [x] **1.6** [TEST] Same file — add
  `test_one_sided_reconciled_with_stays_symmetric`: only `alpha` holds a
  `reconciled_with → beta` edge; a plain symmetric `reconcile alpha beta`
  request proceeds and adds `beta`'s edge (not a refuse); a follow-up
  `--revision alpha` on the same pair refuses. Pins design.md Decision 2's
  shipped-behavior note that a one-sided `reconciled_with` still classifies
  as `symmetric`. **Partially RED today**: the symmetric-proceeds half
  already passes on unmodified code (write it as a regression guard before
  1.7 lands); the `--revision` refusal half is RED because `--revision`
  does not exist yet — confirm the observed failure mode before 1.11 lands.
- [x] **1.7** [IMPL] `src/openkos/cli/main.py`: replace the body of
  `_existing_reconciliation_state` (today's two hand-written `any(...)`
  blocks) with design.md Decision 2's table-driven pass: add the module
  dict `_MODE_BY_RESOLUTION_TYPE: dict[str, _RequestedMode]` (`{"
  reconciled_with": "symmetric", "supersedes": "directional", "revises":
  "revision"}`), collect the `(mode, holder)` set across both sides'
  relations, and return `("none", None)` / the single element / `("mixed",
  None)` accordingly. Extend `_ResolutionMode` to
  `Literal["none", "symmetric", "directional", "revision", "mixed"]` and add
  `_RequestedMode = Literal["symmetric", "directional", "revision"]`. Extend
  `_reconciliation_state_description` (Decision 3) with the exact `revision`
  and `mixed` wording from design.md. Keep the refuse predicate at
  `main.py:9969-9971` exactly as-is, with the renamed `holder_canonical`.
  Makes 1.3, 1.5, 1.6 GREEN, and keeps 1.4's non-`revision` cells GREEN.

### `cli/main.py` — `_resolve_pair_member`, `--revision`, flag gates (Decisions 4, 5)

- [x] **1.8** [TEST] Same file — add
  `test_revision_flag_refuses_when_id_is_not_in_the_pair`, parametrized over
  a non-member id, `sources/nonexistent`, and a traversal path (`../x`).
  Assert exit `1`, a `_snapshot` taken before the run is unchanged after it,
  and stderr contains `"--revision"` and
  `"must resolve to one of the pair"`. Covers reconcile-command scenario
  "--revision id not in pair". **RED today**: `--revision` is not a
  recognized Typer option, so the actual failure is a Click/Typer
  unrecognized-option usage error with a different exit/stderr shape —
  confirm the observed behavior before 1.11 lands.
- [x] **1.9** [TEST] Same file — add
  `test_revision_flag_conflicts_with_winner_and_from_findings`:
  `reconcile alpha beta --winner alpha --revision alpha` exits `1` with
  `"mutually exclusive"` in stderr and zero writes;
  `reconcile --from-findings --revision alpha` exits `1` with
  `"--from-findings"` in stderr and zero writes. Also extend the existing
  `--from-findings` combination test to match the new message text
  (`"--from-findings takes no concept ids, no --winner, no --revision, and
  no --auto; use the two-id form for a directional, revision, or unattended
  reconciliation"`). Covers reconcile-command scenarios "--revision combined
  with --winner refuses" and "--revision combined with --from-findings
  refuses". **RED today**: `--revision` is unrecognized, so both new
  assertions fail on the wrong exit/stderr shape, and the existing
  `--from-findings` test still matches only the pre-change substring.
- [x] **1.10** [TEST] Same file — add
  `test_revision_writes_a_single_outbound_edge_and_notes` and
  `test_revision_holder_may_be_either_pair_member` (the second passing
  `--revision beta`, covering the `else` branch of `_DIRECTED_ROLES`/
  `_resolve_pair_member`). Assert, for `reconcile alpha beta --revision
  alpha --auto`: `relations(alpha) == [Relation(beta, "revises")]`,
  `relations(beta) == []`; the `## Reconciliation` anchor on `alpha` reads
  `role=revises` and on `beta` reads `role=revised`; the exact sentences
  `"Revises [beta](/beta.md) as of <date> (refinement; both remain
  current)."` and `"Revised by [alpha](/alpha.md) as of <date> (refinement;
  both remain current)."`; `log.md` gains the `**Reconcile**` line for a
  revision; stdout contains `"as revising"` and `"both remain current"`;
  the autocommit message is `"openkos: reconcile alpha revises beta"`.
  Covers reconcile-command scenario "Revision writes a single outbound
  edge". **RED today**: `--revision` is unrecognized — none of these code
  paths exist.
- [x] **1.11** [IMPL] `src/openkos/cli/main.py`: extract the `--winner`
  pair-membership block (`main.py:9853-9865`) into
  `_resolve_pair_member(layout, flag, value, canonical_a, canonical_b) ->
  tuple[str, str]`, returning `(holder, counterpart)` and raising the exact
  message from design.md Decision 5 step 3 on a non-member value. Call it
  once for `--winner` (`edge_type="supersedes"`, byte-identical message to
  today's) and once for `--revision` (`edge_type="revises"`). Add the
  `--revision <id>` Typer option beside `--winner` (`main.py:9660-9667`)
  with the exact help text from design.md Decision 5. Add the flag-conflict
  gate (Decision 5 step 2: `if winner is not None and revision is not None:
  raise ValueError(...)`, exact message) in the first `try`, after the
  `--from-findings` branch and before the two-ids check. Extend the
  `--from-findings` gate condition (`or revision is not None`) and its
  message (`main.py:9800-9806`, exact new text from Decision 5 step 1).
  Makes 1.8 and 1.9 GREEN.
- [x] **1.12** [IMPL] `src/openkos/cli/main.py`: rename `_reconcile_pair`'s
  positional parameters `winner_canonical, loser_canonical` to
  `holder_canonical, target_canonical`, and add a keyword-only
  `edge_type: Literal["supersedes", "revises"] = "supersedes"` (Decision 4).
  Add the module dict `_DIRECTED_ROLES: dict[str, tuple[_ReconcileRole,
  _ReconcileRole]] = {"supersedes": ("supersedes", "superseded"), "revises":
  ("revises", "revised")}`. Replace the edge branch (`main.py:9986-10003`)
  with the unchanged symmetric branch plus one directed branch driven by
  `_DIRECTED_ROLES`: the holder gets `okf.Relation(target=counterpart,
  type=edge_type)` and the holder role; the counterpart gets no edge and
  the target role. Confirm `_run_reconcile_from_findings`'s call
  (`main.py:10238-10251`, passing `None, None`) stays byte-unchanged, since
  `edge_type` defaults to `"supersedes"` and is ignored when
  `holder_canonical is None`. Makes 1.4's `revision` cells and 1.10 GREEN.
- [x] **1.13** [IMPL] `src/openkos/cli/main.py`: extend `_ReconcileRole` to
  `Literal["reconciled", "supersedes", "superseded", "revises", "revised"]`;
  add the `revises`/`revised` branches to `_reconcile_sentence`
  (`main.py:9535`), ahead of the defensive `raise`, with the exact sentences
  from design.md Decision 6. Add the new-write `**Reconcile**` log-line
  shape for a revision, the success echo (`"...as revising '<t>'; both
  remain current..."`), and the commit message shape (`"openkos: reconcile
  <h> revises <t>"`). Add the preview direction line after `"proposed
  changes:"` for both directed modes (`"  = '<h>' supersedes '<t>'"` /
  `"  = '<h>' revises '<t>'"`) — this line is additive and has no dedicated
  pinning test per design.md ("no test pins reconcile preview bytes"); its
  presence is exercised incidentally by 1.10's stdout assertions. Makes the
  remaining assertions in 1.10 GREEN.

### `lifecycle.py` / `bundle/listing.py` — hides-nothing pins (no code change)

- [x] **1.14** [TEST] `tests/unit/test_lifecycle.py` — add
  `test_revises_edge_does_not_deprecate_either_end`: two concepts, `a` with
  an outbound `revises → b` edge; assert `deprecated_concept_ids(bundle) ==
  frozenset()`. Covers status-aware-retrieval scenario "A revises edge
  deprecates neither end...". **Not RED-then-GREEN**: per design.md,
  `lifecycle.py:78` only special-cases the string `"supersedes"` today and
  is NOT modified by this change — this assertion already holds on the
  unmodified tree. Confirm it passes as written, then treat it as the
  permanent regression guard that kills a future mutation adding
  `"revises"` to that deprecation set.
- [x] **1.15** [TEST] `tests/unit/bundle/test_listing.py` — add
  `test_revises_edge_leaves_both_rows_active`: same two-concept setup as
  1.14; assert both rows' `status == "active"` in `list`'s STATUS column.
  Same non-RED framing as 1.14, guarding `listing.py:143`.

### Slice 1 docs and staged artifacts

- [x] **1.16** [IMPL] `docs/cli.md` (reconcile section, ~line 371): add a
  bullet for the third shape, **`--revision <id>`** (a directional
  refinement: one `revises` edge on the refining concept, both remain
  current, nothing is hidden); add a flag row; rewrite the idempotency
  sentence to cover three modes and state that a pair carrying disagreeing
  resolutions refuses every request; extend the Batch-mode refusals to name
  `--revision`; add one sentence that `--winner` and `--revision` refuse
  together.
- [x] **1.17** Confirm `docs/adr/0024-revises-relation-and-resolved-pairs.md`
  (status `Proposed` in both frontmatter and body) and its
  `docs/adr/README.md` index row are present and unchanged from the design
  phase — both already exist in the worktree. No edit is made here; they are
  staged into slice 1's commit as-is.

### Slice 1 verification

- [x] **1.18** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **1.19** Run `uv run pytest` (unpiped) — must be green, including
  every pre-existing scenario named in the Scenario → Task Coverage table
  above as "unaffected", plus `tests/unit/model/test_relations.py`,
  `tests/unit/cli/test_reconcile.py`, `tests/unit/test_lifecycle.py`, and
  `tests/unit/bundle/test_listing.py`.
- [x] **1.20** Commit as one or more work-unit commits — scope `model` for
  the `relations.py` constant (e.g. `feat(model): add
  RESOLUTION_RELATION_TYPES constant`), scope `cli` for the reconcile
  plumbing (e.g. `feat(cli): add reconcile --revision for refinement
  resolutions`), scope `docs` for `docs/cli.md` and the staged ADR/index
  row, tests alongside their behavior. Open PR 1 targeting `main`.

---

## Slice 2 (PR 2 → PR 1's branch): resolved pairs leave contradiction candidacy

This slice depends only on `RESOLUTION_RELATION_TYPES` (task 1.2).

### `resolution/contradiction.py` — resolved-pair exclusion (Decision 7)

- [x] **2.1** [TEST] `tests/unit/resolution/test_contradiction.py` — add
  `test_resolved_pairs_excluded_from_candidates`, parametrized over the
  three resolution types (`supersedes`, `reconciled_with`, `revises`) ×
  both edge directions: `_FakeGraphStore([Edge(a, b, type), Edge(c, d,
  "related_to")])` returns `pairs == [("c", "d")]` and `total == 1`. Covers
  contradiction-detection scenarios "A resolved pair is excluded from
  candidates, before the count and the cap" and "Resolution exclusion
  applies regardless of which member holds the edge". **RED today**:
  `_candidate_pairs` only excludes `derived_from`, so the `(a, b)` pair is
  still returned — `AssertionError`.
- [x] **2.2** [TEST] Same file — add
  `test_resolved_pair_excluded_even_with_another_typed_edge_present`: a pair
  carrying BOTH `related_to` and a resolution edge is still excluded (the
  resolution edge alone is sufficient). **RED today**: same gap as 2.1.
- [x] **2.3** [TEST] Same file — add
  `test_resolution_exclusion_applied_before_cap`: a resolved pair plus one
  unrelated live pair, with `cap=1` — the live pair is still returned and
  `total == 1` (the resolved pair never consumes the cap slot); repeat for
  `cap=None`. Covers "The exclusion keeps the cost gate exact" and "before
  the total candidate count and the cap are computed". **RED today**:
  without the exclusion, the resolved pair could consume the cap slot and
  starve the live pair — `AssertionError`.
- [x] **2.4** [TEST] Same file — add
  `test_unrelated_live_pair_still_judged_alongside_a_resolved_pair`: one
  resolved pair plus one pair connected only by an ordinary typed edge —
  the unrelated pair is still generated as a candidate. Covers "An unrelated
  live pair is still judged". **RED today**: fails only if the exclusion
  incorrectly narrows to `derived_from`-only removal that also drops the
  live pair by accident; confirm this is a genuine regression check against
  2.1-2.3's fix, not a trivially-passing case.
- [x] **2.5** [TEST] Same file — extend the existing "genuine typed
  contradiction-eligible edge is still surfaced" test's parametrize list so
  its excluded-type set explicitly names `supersedes`, `reconciled_with`,
  and `revises` alongside `derived_from`, pinning that the exclusion is
  scoped to exactly those four relation types and nothing broader. **RED
  today**: `supersedes`/`reconciled_with`/`revises` are not yet excluded, so
  this extended parametrize would currently show them as included, not
  excluded — confirm the observed (wrong) result before 2.6 lands.
- [x] **2.6** [IMPL] `src/openkos/resolution/contradiction.py`: in
  `_candidate_pairs`, insert between `typed_edges` (`:344-350`) and
  `ordered` (`:352`):
  ```python
  resolved = {
      _pair_key(edge.source_id, edge.target_id)
      for edge in typed_edges
      if edge.relation_type in RESOLUTION_RELATION_TYPES
  }
  pair_keys = {_pair_key(e.source_id, e.target_id) for e in typed_edges} - resolved
  ```
  Import `RESOLUTION_RELATION_TYPES` from `openkos.model.relations`. This
  lands before the deprecation filter, `total_count`, and the cap slice, so
  the "filter before cap" rule holds on both `return`s. Update the
  docstrings of `_candidate_pairs`, `CandidatePlan.edge_total` ("…
  resolution-filtered…"), and the module header in the same style. Makes
  2.1-2.5 GREEN.
- [x] **2.7** [TEST] Same file — add
  `test_plan_candidates_cost_gate_excludes_resolved_pairs`:
  `plan_candidates(tmp_path / "bundle", store=_FakeGraphStore([<a reconciled
  edge a-b>, <a related_to edge c-d>]))` gives `edge_total == 1` and
  `llm_calls == 1` — the exact number `curate._contradictions_probe` states
  (`curate.py:1664-1667`). Covers "The exclusion keeps the cost gate exact"
  from the `curate` cost-gate side, pinning that `plan_candidates` (not only
  `find_contradictions`) carries the exclusion, since both read the same
  `_candidate_pairs`/`_pairs_and_types` path. **RED today**: `edge_total`
  would be `2` without the exclusion — `AssertionError`. Kills an exclusion
  applied only inside `find_contradictions` and not in `plan_candidates`.

### Slice 2 docs

- [x] **2.8** [IMPL] `docs/cli.md` (`contradictions` section): add one
  sentence stating that a pair a human already resolved with `reconcile`
  (joined by `supersedes`, `reconciled_with`, or `revises`, in either
  direction) is never a candidate, including under `--include-deprecated`,
  and that removing the relation makes it a candidate again.

### Slice 2 verification

- [x] **2.9** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **2.10** Run `uv run pytest` (unpiped) — must be green, including the
  pre-existing `tests/unit/resolution/test_contradiction.py` scenarios named
  as "unaffected" in the Scenario → Task Coverage table above, unedited
  beyond 2.1-2.5, 2.7.
- [x] **2.11** Commit as one or more work-unit commits — pick the closest
  existing scope from AGENTS.md's list for `resolution/contradiction.py`
  (no dedicated `resolution` scope is listed yet; confirm the scope used by
  the module's originating change, `openspec/changes/archive/
  2026-07-22-freshness-contradiction-detection/`, before finalizing the
  message — e.g. `feat(graph): exclude resolved pairs from contradiction
  candidates before the cap`), scope `docs` for the `docs/cli.md` sentence,
  tests alongside their behavior. Open PR 2 targeting PR 1's branch.

---

## Post-merge (archive phase, not a task here)

Per `openspec/config.yaml`'s `rules.archive`, the archive phase — not this
task list — merges the three delta specs
(`openspec/changes/revises-relation/specs/{reconcile-command,
status-aware-retrieval,contradiction-detection}/`) into their living
`openspec/specs/{domain}/spec.md` files, and flips ADR-0024's status from
`Proposed` to `Accepted` in both the frontmatter and the body `**Status:**`
line, plus its `docs/adr/README.md` index row.

One item has **no ordinary delta mechanism** and must be hand-applied at
archive: the `contradiction-detection` delta's "Non-Goals Correction (for
archive)" section. It instructs narrowing the main spec's Non-Goals
parenthetical from "...or a seeded `contradicts` relation type (all typed
edges are candidates)." to something like "...(excluding `derived_from`
edges and any pair joined by a resolution edge — `supersedes`,
`reconciled_with`, or `revises`)." This is prose guidance for the archive
phase to apply by hand, not an ADDED/MODIFIED/REMOVED/RENAMED requirement
delta, so it will not be picked up by an automated spec merge — the archive
phase must read that section directly and edit the main spec's Non-Goals
paragraph accordingly.

No task above performs any of this; it is explicitly out of scope for
`sdd-apply`.
