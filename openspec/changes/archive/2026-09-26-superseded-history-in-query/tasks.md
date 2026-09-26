# Tasks: superseded-history-in-query — show how a retrieved answer came to be

Refs #1014 piece (b). Design: `design.md`. Proposal: `proposal.md`. Delta
specs: `specs/query-answer/spec.md`, `specs/status-aware-retrieval/spec.md`,
`specs/query-command/spec.md`. ADR-0026
(`docs/adr/0026-superseded-concepts-re-enter-answers-only-as-labelled-history.md`)
is already written, status `Proposed`, and its `docs/adr/README.md` index row
already exists in the worktree — both ship as-is inside slice 1's commit; no
task below writes them.

Strict TDD is ON, runner `uv run pytest`. Every behavioral task pairs a
`[TEST]` task, observed RED with the reason it is RED today, with the
`[IMPL]` task that turns it GREEN — in that order. A task marked `[IMPL] N/A`
means the behavior is expected to already be GREEN by construction from an
earlier task in the same slice; if it is RED instead, that is a design
violation to fix in that earlier task, not new production code to add here.
A task marked `[CHECK]` records a pre-refactor regression baseline, not a
RED/GREEN pair. Revert every mutation with the inverse edit (never `git
checkout --`), and purge `__pycache__` before trusting a verdict, per
design.md's Testing Strategy preamble and this project's "a test that passes
first try" and "mutation-pycache-invalidates-verdicts" practice.

**Delivery.** `delivery_strategy: auto-chain`, `chain_strategy:
stacked-to-main` (matching this project's practice on the sibling
`revises-relation` change). Four slices, each its own PR: PR 1 targets
`main`; PR 2a targets PR 1's branch; PR 2b targets PR 2a's branch; PR 3
targets PR 2b's branch. Once each PR merges in order, GitHub retargets the
next PR onto `main` automatically. Each slice is green on its own.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,380 (design.md "Migration / Rollout": 1: ~280, 2a: ~380, 2b: ~400, 3: ~320; delta specs excluded) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (event-date leaf + ADR) → PR 2a (history walk + nested budget, pure) → PR 2b (answer.py wiring) → PR 3 (config/CLI/save surface + docs) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

Per-slice estimate (design.md "Migration / Rollout"):

| Slice | Content | Authored lines (est.) |
| --- | --- | --- |
| 1 | `event_dates.py`, the `DateState` alias, `test_event_dates.py` (ADR-0026 already written; slice commits it) | ~280 (≈100 source, ≈150 tests, ≈30 alias and docs) |
| 2a | `retrieval/history.py`, `prompt_budget.nested_shares`, their tests. No caller yet | ~380 (≈170 source, ≈210 tests) |
| 2b | `answer.py`: `_guarded_read`, attaching, `_bound_with_history`, `Citation.history`, `history_truncated_titles`, the kwarg (no caller enables it), the retrieval layering test, answer tests | ~400 (≈150 source, ≈250 tests) |
| 3 | The config key and template, `run_query`, CLI markers and notice, `build_concept` `related_notes`, `stage_filed_answer`, `docs/cli.md` | ~320 |

Total ≈ 1,380, well over the ~400 single-PR budget. Each slice is useful and
green on its own: slice 1 ships a working, tested date resolver with no
caller; slice 2a ships a working, tested pure walk and budget split with no
caller; slice 2b makes `answer()` support `revision_history` end to end but
nothing yet sets it from configuration; slice 3 wires it up and surfaces it.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Bounded event-date resolver (`event_dates.py`), the `DateState` alias in `decision_revision.py`, ADR-0026 staged | PR 1 → `main` | `uv run pytest tests/unit/test_event_dates.py tests/unit/resolution/test_decision_revision.py` | `uv run python evals/run_self_tests.py` (confirms `evals/decision_revisions/`'s dated fixture and self-test still pass against the aliased `DateState`) — no CLI/command surface exists at this slice | Revert `src/openkos/event_dates.py`, the one-line alias in `resolution/decision_revision.py:58`, and `tests/unit/test_event_dates.py`; ADR-0026 and its README row stay (they were staged as-is, not written by this slice) |
| 2a | Pure bounded chain walk (`retrieval/history.py`) and the nested budget split (`prompt_budget.nested_shares`); no caller yet | PR 2a → PR 1's branch | `uv run pytest tests/unit/retrieval/test_history.py tests/unit/test_prompt_budget.py` | N/A — no caller wires `history.py`/`nested_shares` into any command yet; both are pure functions exercised only through fake readers and property tests | Revert `src/openkos/retrieval/history.py`, the `GroupShares`/`nested_shares` addition to `src/openkos/prompt_budget.py`, and their two test files; nothing outside this slice references either symbol yet |
| 3 (2b) | Wire `answer()`/`_assemble_context` to walk, attach, budget, cite, and disclose revision history behind a keyword-only `revision_history: bool = False` | PR 2b → PR 2a's branch | `uv run pytest tests/unit/retrieval/test_answer.py tests/unit/retrieval/test_layering.py` | N/A — no CLI/config path enables the kwarg until slice 3; exercised only through direct `answer(revision_history=...)` calls in tests | Revert the `answer.py` diff (the `_guarded_read` extraction stays functionally identical, so it can also be left in place — see 3.2) and `test_layering.py`; `answer()`'s default `revision_history=False` means no caller anywhere is affected by a full revert |
| 4 (3) | The `revision_history` config key and template, `run_query` threading, `[superseded]`/`[refined]` CLI markers and the truncation stderr notice, `build_concept`'s `related_notes`, `stage_filed_answer`'s history marking, `docs/cli.md` | PR 3 → PR 2b's branch | `uv run pytest tests/unit/test_config.py tests/unit/model/test_okf.py tests/unit/cli/test_query_save.py tests/unit/application/test_query.py` (adjust the last two paths to this repo's actual query-service/CLI test file names before running) | `uv run openkos init` a scratch workspace, add two concepts joined by `supersedes`/`revises`, set `revision_history: true` in `openkos.yaml`, run `uv run openkos query "<question>"` and confirm the `[superseded]`/`[refined]` marker and (when triggered) the truncation stderr notice, then `--save` and confirm the filed concept's `## Related` bullet | Revert `config.py`'s `DEFAULT_REVISION_HISTORY`/`Config.revision_history` (defaulted-last field, so this is a clean revert), the template block, `run_query`'s threading, `okf.build_concept`'s `related_notes` (defaults to `None`, byte-identical), `stage_filed_answer`'s mapping, the two CLI markers, the stderr notice, and `docs/cli.md`'s note; a stale `revision_history:` line left in a user's `openkos.yaml` is silently ignored by `read_config` |

## Scenario → Task Coverage

Every scenario in the three delta specs mapped to at least one task below.
"Unaffected" rows name existing scenarios this change must not break;
confirmed green by the slice's `pytest` gate, not by a dedicated new task.

| Spec | Scenario | Task(s) |
|---|---|---|
| query-answer | A supersedes predecessor is attached as a history block | 3.3, 3.4 |
| query-answer | A 4-long chain stops at depth 3 and a cycle terminates | 2.3 (depth, walk-level), 2.5 (cycle, walk-level), 3.25 (depth cause at `AnswerResult` level) |
| query-answer | More than 3 predecessors truncates deterministically | 2.1 (order), 2.4 (cap, walk-level), 3.25 (cap cause at `AnswerResult` level) |
| query-answer | Disabled by default makes zero extra reads | 3.28, 3.30 |
| query-answer | A revises predecessor already a hit is not repeated | 3.12, 3.13 |
| query-answer | A predecessor shared by two successors is attached once | 3.12, 3.13 |
| query-answer | A confidential predecessor is excluded like a hit | 3.7, 3.8 |
| query-answer | An unreadable predecessor is skipped without raising | 2.7 (walk-level), 3.7, 3.8 |
| query-answer | A refused predecessor is a dead end, never traversed | 2.7 (walk-level), 3.9 (integration) |
| query-answer | A depth-1 superseded predecessor's label names the retrieved successor as holder | 2.2 (walk-level `holder_id`), 3.3, 3.4 |
| query-answer | A depth-2 predecessor's label names its immediate holder, not the top-level successor | 2.2, 3.3, 3.4 |
| query-answer | A refined predecessor not deprecated elsewhere states it is still current | 3.3, 3.4 |
| query-answer | A refined predecessor that is also deprecated elsewhere states it is no longer current | 3.3, 3.4 |
| query-answer | An unresolved date renders as unknown, never ingest time | 1.1, 1.4 (resolver), 2.9, 2.10 (`date_phrase`), 3.5 |
| query-answer | A confidential Source's date resolves as unknown, without excluding the predecessor's own block | 1.1, 1.4, 3.10, 3.11 |
| query-answer | Multiple distinct dates render as an earliest-to-latest range | 1.1, 1.4, 2.9, 2.10, 3.5 |
| query-answer | The successor's own label is unaffected | 3.3, 3.4 |
| query-answer | Adding history to one successor leaves other hits' bodies unchanged | 3.14, 3.21 |
| query-answer | Unspent budget lets a small history block fit without excerpting | 3.16, 3.21 |
| query-answer | A fully-spent window still splits within the successor's own share | 3.17, 3.21 |
| query-answer | An oversized history block is excerpted, not dropped | 3.18, 3.21 |
| query-answer | A zero-share history block within a kept group is dropped and disclosed as omitted | 3.19, 3.21 |
| query-answer | A history group that would cost its successor its whole body is dropped entirely instead | 3.20, 3.21 |
| query-answer | A reported attribution naming a history block sets history | 3.22, 3.23 |
| query-answer | An ordinary hit citation always carries history=None | 3.22, 3.23 |
| query-answer | Absent attribution keeps every included block, hit or history | 3.22, 3.23 |
| query-answer | Successful answer sets success metadata | unaffected — confirmed green by 3.36 |
| query-answer | AnswerResult reports no graph metadata | unaffected — confirmed green by 3.36 |
| query-answer | context_block_count includes attached history blocks | 3.24, 3.27 |
| query-answer | The truncation signal names the capped successor's title | 3.25, 3.27 |
| query-answer | A chain cut by the depth bound also sets the truncation signal | 3.25, 3.27 |
| query-answer | Disabled by default reports no truncation | 3.26, 3.27 |
| query-answer | Module has no config dependency | unaffected — confirmed green by 3.33/3.36 |
| query-answer | revision_history is caller-supplied, not config-read | 3.33 |
| status-aware-retrieval | Deprecated concept absent from a matching query | unaffected — confirmed green by 3.36 |
| status-aware-retrieval | Superseded concept absent from contradiction candidates | unaffected — confirmed green by 3.36 (pre-existing, unrelated to `lifecycle.py`) |
| status-aware-retrieval | Only match is deprecated yields the standard no-match result | unaffected — confirmed green by 3.36 |
| status-aware-retrieval | A superseded concept reaches the prompt only as an attached history block | 3.3, 3.4 (fused-count/hit-count exclusion asserted alongside attach) |
| status-aware-retrieval | Flag restores a deprecated concept | unaffected — confirmed green by 3.36 |
| status-aware-retrieval | Flag is opt-in, not the default | unaffected — confirmed green by 3.36 |
| status-aware-retrieval | History is suppressed under --include-deprecated | 3.31, 3.32 |
| query-command | A superseded history citation renders its marker | 4.10, 4.11 |
| query-command | A refined history citation renders its marker | 4.10, 4.11 |
| query-command | An ordinary citation carries neither marker | 4.10, 4.11 |
| query-command | A truncated successor's title triggers the stderr notice | 4.12, 4.13 |
| query-command | Multiple truncated successors are named together | 4.12, 4.13 |
| query-command | No truncation prints no notice | 4.12, 4.13 |
| query-command | Key absent defaults to off | 4.1, 4.2 |
| query-command | Key present and true is threaded to answer() | 4.4, 4.5 |
| query-command | A non-bool value is rejected | 4.1, 4.2 |
| query-command | The template documents the key as unmeasured and off by default | 4.14, 4.15 |
| query-command | A leftover key from a reverted feature is silently ignored | 4.16, 4.17 |
| query-command | Default filing is a declaratively-titled Insight | unaffected — confirmed green by 4.20 |
| query-command | A definitional question titles the filing by its subject | unaffected — confirmed green by 4.20 |
| query-command | An over-long first sentence titles the filing by its clause | unaffected — confirmed green by 4.20 |
| query-command | An unusable first sentence falls back to the question title | unaffected — confirmed green by 4.20 |
| query-command | --title, --description, --type override defaults | unaffected — confirmed green by 4.20 |
| query-command | A superseded history citation is filed with its mark | 4.8, 4.9 |
| query-command | A refined history citation is filed with its mark | 4.8, 4.9 |
| query-command | An ordinary cited concept's Related bullet is unaffected | 4.8, 4.9 |
| query-command | build_concept without related_notes stays byte-identical | 4.6, 4.7 |

---

## Slice 1 (PR 1 → `main`): the bounded event-date resolver

### `event_dates.py` — the resolver (design Decision 1)

- [x] **1.1** [TEST] `tests/unit/test_event_dates.py` (new) —
  `test_resolve_event_date_all_states`, parametrized over: `dated` (a single
  admitted Source with one event date); `missing` (an admitted Source that is
  absent, malformed, unreadable, or refused by `admit`); `multiple` (more
  than one distinct date across reached Sources, asserting `earliest`/
  `latest`); `none-reached` (empty `provenance:`, or `provenance:` naming
  only non-Source intermediates whose own `provenance:` is empty). Covers
  query-answer's date-resolution requirement across all four `DateState`
  values. **RED today**: `ModuleNotFoundError` — `event_dates.py` does not
  exist. Kills swapping the `missing`/`multiple` check order, and dropping
  the `admit` parameter (a value that should be refused would then count).
- [x] **1.2** [TEST] Same file — `test_one_hop_resolution_bound`: an
  intermediate's own `sources/` entries count toward the resolution; a Source
  reachable only through a second intermediate (two hops from the member)
  does not. **RED today**: same reason as 1.1. Kills recursing past hop 2.
- [x] **1.3** [TEST] Same file — `test_refused_intermediate_is_not_traversed`:
  an intermediate entry that `admit` refuses contributes nothing to the
  aggregation, and its own `provenance:` is never read (assert via a reader
  call-count/spy). **RED today**: same reason as 1.1. Kills traversing into a
  refused intermediate's `sources/` entries.
- [x] **1.4** [IMPL] `src/openkos/event_dates.py` (new): `DateState =
  Literal["dated", "missing", "multiple", "none-reached"]`;
  `ResolvedEventDate` (frozen dataclass: `state`, `earliest: date | None`,
  `latest: date | None`); `resolve_event_date(bundle_dir, metadata, *,
  admit=None) -> ResolvedEventDate`, implementing design Decision 1's rule
  exactly: read `provenance:` (non-list → empty; non-string entries skipped;
  de-duplicated); a `sources/`-prefixed entry (`TYPE_TO_LINK_DIR["Source"]`)
  is resolved directly via a guarded read + `okf.read_event_date`; any other
  entry gets one further hop into its own `provenance:`'s `sources/` entries
  only; a guarded-read failure (`OSError`, `UnicodeDecodeError`, parse
  failure, or `admit(id, meta) is False`) makes a Source `missing` and an
  intermediate contribute nothing without being traversed; aggregate in the
  order none-reached → any-missing → multiple → dated. Imports only stdlib,
  `openkos.model.okf`, `openkos.model.types`. Never raises. Makes 1.1–1.3
  GREEN.
- [x] **1.5** [TEST] Same file — `test_leaf_imports_are_bounded`: an AST scan
  of `src/openkos/event_dates.py`'s import statements asserts every imported
  module is stdlib, `openkos.model.okf`, or `openkos.model.types`. This test
  can only meaningfully run once 1.4 exists; write it alongside 1.4 and
  confirm it is GREEN immediately, then treat it as the standing regression
  guard against a future disallowed import (e.g. `sensitivity`,
  `resolution`).

### `resolution/decision_revision.py` — the `DateState` alias (design Decision 1)

- [x] **1.6** [TEST] `tests/unit/resolution/test_decision_revision.py` — add
  `test_date_state_is_the_event_dates_alias`: `decision_revision.DateState is
  event_dates.DateState` (identity, not just structural equality); the
  existing import `from openkos.resolution.decision_revision import
  DateState` (`test_decision_revision.py:26`) still resolves. **RED today**:
  `decision_revision.py:58` defines its own local `Literal`, so the identity
  comparison is `False`. Kills a future re-introduction of a second, separate
  `Literal` definition instead of the alias.
- [x] **1.7** [IMPL] `src/openkos/resolution/decision_revision.py:58` —
  replace the local `Literal["dated", "missing", "multiple",
  "none-reached"]` with `from openkos import event_dates` at the top of the
  module and `DateState = event_dates.DateState` at line 58. `DecisionDate`,
  `DirectionReason`, and `pair_direction` stay untouched. Makes 1.6 GREEN.
- [x] **1.8** [CHECK] Run the full existing
  `tests/unit/resolution/test_decision_revision.py` suite — confirm it is
  unaffected beyond 1.6 (a regression pin, not a new RED/GREEN pair): the
  harness imports only `DecisionDate`
  (`evals/decision_revisions/revision_fixtures.py:36`,
  `run_decision_revisions_eval.py:94-98`), so it is unaffected by the alias,
  and `revision_fixtures.py:116`'s docstring mention of
  `decision_revision.DateState` stays true through the alias.

### Slice 1 staged artifacts

- [x] **1.9** Confirm
  `docs/adr/0026-superseded-concepts-re-enter-answers-only-as-labelled-history.md`
  (status `Proposed` in both frontmatter and body) and its
  `docs/adr/README.md` index row are present and unchanged from the design
  phase — both already exist in the worktree. No edit is made here; they are
  staged into slice 1's commit as-is.

### Slice 1 verification

- [x] **1.10** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **1.11** Run `uv run pytest` (unpiped) — must be green, including
  `tests/unit/test_event_dates.py` and
  `tests/unit/resolution/test_decision_revision.py`.
- [x] **1.12** Run `uv run python evals/run_self_tests.py` — must be green.
  This is the must-have proof that the `DateState` alias keeps
  `resolution/decision_revision.py` and `evals/decision_revisions/` working
  end to end, not just import-compatible.
- [x] **1.13** Commit as one or more work-unit commits — confirm the closest
  scope from AGENTS.md's list for the new package-root leaf `event_dates.py`
  before finalizing the message (it is not `okf`, `model`, `retrieval`, or
  `graph` by AGENTS.md's letter; `lifecycle.py`, its closest structural
  precedent, has shipped under multiple prior scopes — check its own git
  history for the nearest analog, or use `retrieval` since `retrieval/` is
  its first consumer-to-be), e.g. `feat(retrieval): add bounded event-date
  resolver`; a separate commit scope `resolution` for the `DateState` alias
  (matching this branch's own recent commits, e.g. `feat(resolution): alias
  DateState to the shared event-date leaf`); tests alongside their behavior;
  the ADR and its README row are staged as-is in either commit. Open PR 1
  targeting `main`.

---

## Slice 2a (PR 2a → PR 1's branch): the pure chain walk and nested budget

This slice has no caller. It depends only on slice 1's `event_dates.py` for
nothing (the walk itself never resolves dates — that is slice 2b's job) and
can proceed independently once PR 1 merges.

### `retrieval/history.py` — the walk (design Decision 2)

- [x] **2.1** [TEST] `tests/unit/retrieval/test_history.py` (new) —
  `test_walk_order_mixed_edges_and_siblings`: a fake dict-backed reader with
  a successor holding both `supersedes` and `revises` edges to siblings;
  assert the returned `predecessors` order is `supersedes` before `revises`,
  then ascending concept id, and BFS level by level (a depth-2 sibling group
  is fully ordered by its own parents' order before depth-3 starts).
  **RED today**: `ModuleNotFoundError` — `history.py` does not exist. Kills
  the sort key (e.g. `revises` before `supersedes`, or reversed id).
- [x] **2.2** [TEST] Same file — `test_holder_id_names_immediate_edge_owner`:
  a chain successor→P (depth 1)→Q (depth 2); assert `Predecessor(P).holder_id
  == successor_id` and `Predecessor(Q).holder_id == P`'s id — the immediate
  edge owner, never the top-level successor for a depth-2+ node. **RED
  today**: same reason as 2.1. Kills tracking a single global holder instead
  of per-edge holder.
- [x] **2.3** [TEST] Same file — `test_depth_bound`: a 4-long chain of
  `supersedes` edges attaches exactly 3 predecessors (through depth 3) with
  `truncated=True`; a 3-long chain attaches all 3 with `truncated=False`.
  **RED today**: same reason as 2.1. Kills `depth == MAX_DEPTH` becoming
  `depth > MAX_DEPTH`.
- [x] **2.4** [TEST] Same file — `test_block_cap`: a successor with 5 direct
  predecessors attaches exactly 3 (in order) and sets `truncated=True`; a
  successor with exactly 3 direct predecessors attaches all 3 and leaves
  `truncated=False`. **RED today**: same reason as 2.1. Kills the cap-check
  placement and a vacuous truncation flag (one that is always `True` or
  always `False`).
- [x] **2.5** [TEST] Same file — `test_cycle_guard`: a mutual `revises`
  cycle (A revises B, B revises A) terminates without looping, attaching
  both once each; a self-edge (A revises A) is ignored and contributes no
  block. **RED today**: same reason as 2.1. Kills removing the `visited.add`
  call. This is the must-have cycle-guard test.
- [x] **2.6** [TEST] Same file — `test_skip_set`: a member of the passed
  `skip` set is neither attached as a predecessor nor expanded — its own
  outbound edges are never enqueued (assert via the fake reader's call log).
  **RED today**: same reason as 2.1. Kills expanding a skipped node's edges.
- [x] **2.7** [TEST] Same file — `test_dead_end_on_refused_read`: the fake
  reader returns `None` for one node (simulating a gate refusal or an
  unreadable file); that node is not attached, and its own outbound edges are
  never read or enqueued (assert via the fake reader's call log that a
  target reachable only through the refused node is never requested). Covers
  the walk-level half of "A refused predecessor is a dead end, never
  traversed" and "An unreadable predecessor is skipped without raising".
  **RED today**: same reason as 2.1. Kills enqueuing a refused node's edges.
- [x] **2.8** [IMPL] `src/openkos/retrieval/history.py` (new):
  `HistoryRole = Literal["superseded", "refined"]`; `MAX_DEPTH: Final = 3`;
  `MAX_BLOCKS: Final = 3`; `Predecessor` (frozen dataclass: `concept_id`,
  `role`, `holder_id`, `metadata`, `body`); `HistoryWalk` (frozen dataclass:
  `predecessors: tuple[Predecessor, ...]`, `truncated: bool`);
  `walk_history(successor_id, successor_metadata, *, read, skip) ->
  HistoryWalk`, implementing design Decision 2's exact BFS algorithm: `edges
  = okf.decode_relations(meta)` filtered to `supersedes`/`revises`, sorted by
  `(0 if supersedes else 1, target)` (a `ValueError` from malformed
  `relations:` yields `[]`, matching `lifecycle.py:73-76`'s fail-safe); a
  `deque` seeded with the successor's edges at depth 1, `holder =
  successor_id`; per popped `(target, role, depth, holder)`: skip if already
  `visited` (add it either way — the cycle guard); skip without expanding if
  in `skip`; if `len(attached) == MAX_BLOCKS`, set `truncated` and stop, but
  only when some queued/current target is not `visited`/`skip`; call
  `read(target)`, treating `None` as a dead end (never enqueue its edges);
  append the node; if `depth == MAX_DEPTH`, set `truncated` only when the
  node has an unvisited/unskipped outbound edge, then do not enqueue further;
  otherwise enqueue its edges at `depth + 1` with `holder = target`. Makes
  2.1–2.7 GREEN.

### `retrieval/history.py` — label and date-phrase strings (design Decision 5)

- [x] **2.9** [TEST] Same file — `test_date_phrase_and_label_note_strings`:
  `date_phrase` for `dated` → `"event date <D>"`, for `multiple` → `"event
  dates <earliest> to <latest>"` (ISO, earliest-to-latest), for `missing` and
  `none-reached` → `"event date unknown"`; `label_note` for the three
  (role, currency) rows from design Decision 5's table (`superseded` →
  `no longer current`; `refined` + `current=True` → `still current`;
  `refined` + `current=False` → `no longer current`), asserting the exact
  wording `" (earlier version, superseded|refined by concept_id: <H>; <date
  phrase>; no longer current|still current)"`. **RED today**:
  `AttributeError` — neither function exists. Kills any wording drift in
  either function.
- [x] **2.10** [IMPL] `src/openkos/retrieval/history.py` — add
  `date_phrase(resolved: ResolvedEventDate) -> str` and `label_note(role:
  HistoryRole, holder_id: str, resolved: ResolvedEventDate, current: bool) ->
  str` with the exact strings from design Decision 5. Makes 2.9 GREEN.

### `prompt_budget.py` — the nested split (design Decision 4)

- [x] **2.11** [TEST] `tests/unit/test_prompt_budget.py` — add
  `test_nested_shares_no_history_equals_fair_shares`: a property test over
  random `head_sizes`/`budget` combinations where every `inner_sizes[i]` is
  `[]` — assert `nested_shares(...)` returns, for every group, `head ==
  fair_shares(head_sizes, budget=budget)[i]` and `inner == ()`. **RED
  today**: `AttributeError` — `GroupShares`/`nested_shares` do not exist.
  Kills computing the outer split as `budget - Σoverhead` instead of
  reusing today's `fair_shares` unchanged.
- [x] **2.12** [TEST] Same file — `test_nested_shares_total_fits_budget`:
  for groups with non-empty `inner_sizes`, the sum of every group's `head +
  sum(inner)` never exceeds `budget`, across a property sweep of sizes and
  overheads. **RED today**: same reason as 2.11. Kills skipping the slack
  stage (step 2) and double-spending unspent budget across groups.
- [x] **2.13** [TEST] Same file —
  `test_nested_shares_dropped_group_keeps_outer_share`: a group whose nested
  pool cannot give the head a non-zero share under the `split[0] == 0` rule
  (design Decision 4 step 4) is marked `dropped=True`, its `head` equals its
  own `outer[i]` from step 1, and every entry in `inner` is `0`. **RED
  today**: same reason as 2.11. Kills the dropped-group fallback (using
  anything other than the unchanged outer share).
- [x] **2.14** [IMPL] `src/openkos/prompt_budget.py` — add `GroupShares`
  (frozen dataclass: `head: int`, `inner: tuple[int, ...]`, `dropped: bool`)
  and `nested_shares(head_sizes, inner_sizes, inner_overheads, *, budget) ->
  list[GroupShares]`, implementing design Decision 4's exact arithmetic:
  `outer = fair_shares(head_sizes, budget=budget)`; `slack = max(budget, 0) -
  sum(outer)`; per group with history, `need_i = (head_sizes[i] - outer[i]) +
  inner_overheads[i] + sum(inner_sizes[i])`, `extra = fair_shares([need_i,
  ...], budget=slack)`; `pool_i = outer[i] + extra_i - inner_overheads[i]`;
  if `pool_i <= 0`, drop; else `split = fair_shares([head_sizes[i],
  *inner_sizes[i]], budget=pool_i)`, and drop if `split[0] == 0` while
  `head_sizes[i] > 0` and `outer[i] > 0`; a dropped group gets `head =
  outer[i]`, every inner `0`; a group with no history gets `head = outer[i]`,
  `inner = ()`. Makes 2.11–2.13 GREEN.

### Slice 2a verification

- [x] **2.15** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **2.16** Run `uv run pytest` (unpiped) — must be green, including
  `tests/unit/retrieval/test_history.py` and `tests/unit/test_prompt_budget.py`.
  No other suite is affected — `history.py` and `nested_shares` have no
  caller yet.
- [x] **2.17** Commit as one or more work-unit commits — scope `retrieval`
  for `history.py` (e.g. `feat(retrieval): add bounded revision-history
  walk`), scope `retrieval` for `prompt_budget.nested_shares` (e.g.
  `feat(retrieval): add nested budget split for revision history`); tests
  alongside their behavior. Open PR 2a targeting PR 1's branch.

---

## Slice 2b (PR 2b → PR 2a's branch): wiring `answer()` to walk, attach, budget, cite, disclose

This slice depends on slice 1's `event_dates.py` and slice 2a's `history.py`
and `prompt_budget.nested_shares`. It has no config caller yet —
`revision_history` stays exercised only through direct `answer(...)` calls in
tests.

### `_guarded_read` extraction (design Decision 2, "The guarded-read refactor")

- [x] **3.1** [CHECK] Run `uv run pytest tests/unit/retrieval/test_answer.py`
  before any edit in this slice — record the full existing suite as green.
  This is the regression baseline for the extraction below; no production or
  test edit happens in this task.
- [x] **3.2** [IMPL] `src/openkos/retrieval/answer.py` — extract the
  existing hit-loop's `blocked` check, `concept_path_for` read under
  `(OSError, UnicodeDecodeError)`, `load_frontmatter` under the existing
  broad `except` (keeping `# noqa: S112`), and `sensitivity.should_block`
  check (today's hit-loop body) verbatim into `_guarded_read(bundle_dir,
  concept_id, blocked, *, include_confidential, local_exemption) ->
  tuple[dict[str, object], str] | None`. Update the hit loop to call it.
  This is a pure refactor with no behavior change — run `uv run pytest
  tests/unit/retrieval/test_answer.py` (unpiped) and confirm the exact same
  pass/fail set as 3.1's baseline.

### Attach, label, and currency (design Decision 3, 5)

- [x] **3.3** [TEST] `tests/unit/retrieval/test_answer.py` — add
  `test_history_block_attached_with_label_and_citation`, covering in one
  parametrized/multi-assertion test: (a) successor S `supersedes` P → P is
  re-read and appears as its own separately-numbered block placed after S's,
  labelled `(earlier version, superseded by concept_id: S; event date <D>;
  no longer current)`, `Citation.history == "superseded"` for P, and P is
  absent from `fused_count` and every hit-count field; (b) successor S
  `revises` P, P not itself in the bundle's deprecated set → P's label reads
  `refined by concept_id: S; ...; still current`; (c) same edge, but P is
  separately superseded elsewhere (in the deprecated set) → P's label reads
  `...no longer current`, even though the traversing edge was `revises`; (d)
  S `supersedes` P, P separately `revises` Q → Q's label names P as holder
  (`refined by concept_id: P`), not S; (e) S's own context-block label is
  byte-identical to its non-history label. Covers query-answer's "A
  supersedes predecessor is attached as a history block",
  "A depth-1 superseded predecessor's label names the retrieved successor as
  holder", "A depth-2 predecessor's label names its immediate holder, not
  the top-level successor", "A refined predecessor not deprecated elsewhere
  states it is still current", "A refined predecessor that is also
  deprecated elsewhere states it is no longer current", "The successor's own
  label is unaffected", and status-aware-retrieval's "A superseded concept
  reaches the prompt only as an attached history block". **RED today**:
  `_assemble_context`/`answer()` accept no `revision_history` keyword —
  `TypeError`. Kills attaching before the successor's own block; naming the
  top-level successor as holder instead of the immediate edge owner; and
  deciding currency from the edge's role alone instead of the predecessor's
  own deprecated-set membership.
- [x] **3.4** [IMPL] `src/openkos/retrieval/answer.py` — add
  `revision_history: bool = False`, `deprecated: frozenset[str] =
  frozenset()`, `history_truncated_out: list[str] | None = None`
  keyword-only parameters to `_assemble_context` (every existing positional
  parameter and keyword stays unchanged, per design Decision 3 — the eval
  harness call sites
  `evals/query_sufficiency/run_query_sufficiency_probe.py:297` and
  `evals/query_entailment/run_query_entailment_probe.py:348,871` keep
  today's behavior). Inside the fused-id loop, after building each hit's
  label/body/citation as today: when `revision_history`, call
  `history.walk_history(concept_id, metadata, read=..., skip=...)` (the
  `read=`/`skip=` wiring lands in 3.8/3.13); for each returned predecessor,
  resolve its date, build its label via `history.label_note(role, holder_id,
  resolved, current=pred.concept_id not in deprecated)` and
  `history.date_phrase(resolved)`, append its block/citation with
  `history=pred.role`. Makes 3.3 GREEN.

### Date phrase end to end (design Decision 5)

- [x] **3.5** [TEST] Same file — `test_date_phrase_rendering_end_to_end`: a
  predecessor with one resolved event date renders `event date <D>`; one
  with multiple distinct dates renders `event dates <D1> to <D2>`; one whose
  resolution is `missing`/`none-reached` renders `event date unknown` and
  never the concept's ingest timestamp. Covers "An unresolved date renders
  as unknown, never ingest time" and "Multiple distinct dates render as an
  earliest-to-latest range". **RED today**: same reason as 3.3. Kills
  substituting the ingest timestamp as a fallback for an unresolved date.
- [x] **3.6** [IMPL] N/A — expected GREEN by construction from 3.4's use of
  `event_dates.resolve_event_date`/`history.date_phrase`. If RED, fix the
  wiring in 3.4.

### Predecessor send-time guards (design Decision 2, 3)

- [x] **3.7** [TEST] Same file — `test_predecessor_send_time_guards`,
  parametrized over: a predecessor whose freshly re-read frontmatter marks
  it confidential (`include_confidential` off) is absent from history blocks
  and citations, and present when `include_confidential`/`local_exemption`
  is on; a predecessor whose id is in the `blocked` set is silently omitted;
  an unreadable or unparsable predecessor is silently omitted, with no
  exception propagating. Covers "A confidential predecessor is excluded like
  a hit" and "An unreadable predecessor is skipped without raising". **RED
  today**: same reason as 3.3. Kills calling a raw read (e.g.
  `Path.read_text`) instead of `_guarded_read` inside the walk's reader.
- [x] **3.8** [IMPL] `src/openkos/retrieval/answer.py` — pass
  `read=functools.partial(_guarded_read, bundle_dir, blocked=blocked,
  include_confidential=include_confidential, local_exemption=
  local_exemption)` into every `walk_history` call, and record each
  `_guarded_read` failure's concept id into `rejected` (see 3.13) so it is
  never re-read. Makes 3.7 GREEN.

### Refused predecessor is a dead end, integration-level (design Decision 2)

- [x] **3.9** [TEST] Same file — `test_refused_predecessor_is_a_dead_end`: a
  predecessor P fails a send-time guard (blocked, confidential, or
  unreadable), and P itself holds an outbound `supersedes` edge to another
  predecessor Q; assert Q is never read (via a spy on `_guarded_read`'s call
  arguments) and never attached. Covers "A refused predecessor is a dead
  end, never traversed" at the integration level (2.7 already proves this
  for the pure walk with a fake reader; this re-confirms it with the real
  `_guarded_read`). **RED today**: same reason as 3.3. Kills a regression
  where the real integration enqueues a refused node's edges even though
  `walk_history` itself guards against it.

### Confidential Source's date (design Decision 1's `admit` gate)

- [x] **3.10** [TEST] Same file — `test_confidential_source_date_shown_as_unknown`:
  a predecessor P whose `provenance:` names only a Source that the
  sensitivity gate refuses (confidential, `include_confidential` off), while
  P's own frontmatter and body pass their own guards; assert P's label
  reads `event date unknown`, and P's own history block IS still attached
  (the Source's refusal affects only the date lookup). Covers "A confidential
  Source's date resolves as unknown, without excluding the predecessor's own
  block". **RED today**: same reason as 3.3. Kills dropping the `admit`
  closure when calling `resolve_event_date`.
- [x] **3.11** [IMPL] `src/openkos/retrieval/answer.py` — build the `admit`
  closure over `blocked` and `sensitivity.should_block` (matching the
  send-time gate exactly) and pass it as
  `event_dates.resolve_event_date(bundle_dir, pred.metadata, admit=admit)`.
  Makes 3.10 GREEN.

### Deduplication (design Decision 3, "Dedupe against fused_set")

- [x] **3.12** [TEST] Same file — `test_dedup_rules`, covering: (a)
  predecessor P is both an ordinary fused hit and reachable from successor S
  via a `revises` edge — P appears exactly once, as its ordinary hit block,
  never also as history; (b) predecessor P is reachable from both successor
  S1 (ranked before S2 in fused order) and successor S2 — P is attached
  exactly once, under S1. Covers "A revises predecessor already a hit is not
  repeated" and "A predecessor shared by two successors is attached once".
  **RED today**: same reason as 3.3. Kills passing `skip=` without the fused
  set, or without tracking `attached` across loop iterations.
- [x] **3.13** [IMPL] `src/openkos/retrieval/answer.py` — compute
  `fused_set = frozenset(concept_ids)` once; maintain `attached: set[str]`
  and `rejected: set[str]` across the loop; pass `skip=fused_set | attached |
  rejected` to each `walk_history` call; add each attached predecessor's id
  to `attached` immediately after appending its block; add each
  `_guarded_read` failure's id to `rejected` (from 3.8). Makes 3.12 GREEN.

### Budget isolation and nested split (design Decision 4)

- [x] **3.14** [TEST] Same file — `test_budget_isolation_unrelated_hits_unchanged`:
  four hits under a small window, one of which is a successor with attached
  history; assert the other three hits' bounded bodies are byte-identical to
  a history-free twin run with the same hits and window. Covers "Adding
  history to one successor leaves other hits' bodies unchanged". **RED
  today**: `_bound_with_history` doesn't exist; `_assemble_context` never
  calls `nested_shares`. Kills measuring `overhead_hits` over all labels
  (the history-inclusive "flat pool") instead of only the hit labels.
- [x] **3.15** [TEST] Same file — `test_fit_within_todays_bound`: across a
  sweep of window sizes with mixed history/no-history hits, `len(user_content)
  + max(system prompt lengths)` never exceeds the bound today's plan already
  allows (computed over the same hit labels and budget). Kills the marginal-
  overhead computation (using the final label set's total overhead instead
  of the telescoping per-group marginal sum).
- [x] **3.16** [TEST] Same file —
  `test_unspent_budget_lets_small_history_fit_without_excerpting`: a window
  where every hit's outer share already comfortably fits its body, leaving
  unspent budget; one successor carries one small history block; assert it
  is sent in full, unexcerpted, funded from the unspent budget rather than
  carved from the successor's own outer share. Kills funding history only
  from the successor's own outer share with no slack stage.
- [x] **3.17** [TEST] Same file —
  `test_fully_spent_window_splits_within_own_share`: a window where every
  hit's outer share is fully spent (no unspent budget); a successor carries
  history; assert its nested pool equals exactly its own outer share minus
  the history frame overhead, split across `[successor, *history]`.
- [x] **3.18** [TEST] Same file — `test_oversized_history_block_excerpted_not_dropped`:
  a history block whose body exceeds its computed sub-share, within a group
  that is not dropped as a whole; assert it receives an even-coverage
  excerpt of its sub-share, its citation is marked `excerpted`, and its
  title carries the ` (earlier version)` suffix in `excerpted_titles`.
- [x] **3.19** [TEST] Same file — `test_zero_share_history_block_dropped_and_disclosed`:
  a history block whose sub-share of the successor's nested split is zero,
  while the successor itself keeps a non-zero share; assert it is dropped
  from the prompt and citations, and its ` (earlier version)`-suffixed title
  is reported in `omitted_titles`.
- [x] **3.20** [TEST] Same file — `test_history_group_dropped_entirely_when_it_would_zero_the_successor`:
  a successor whose nested split, if applied, would leave the successor's
  own share at zero despite a non-zero outer share; assert NONE of that
  successor's history blocks are sent, the successor's share reverts to its
  unchanged outer share, and every dropped history title is disclosed in
  `omitted_titles`, suffixed ` (earlier version)`.
- [x] **3.21** [IMPL] `src/openkos/retrieval/answer.py` — implement
  `_bound_with_history(labels, bodies, holders, *, llm, question) ->
  tuple[list[str], list[bool], list[bool]]` per design Decision 4:
  `overhead_hits` computed exactly as today, over hit labels only;
  `inner_overheads[i]` computed as the marginal frame growth via the
  telescoping `len(_user_content(L_i, q)) - len(_user_content(L_{i-1}, q))`
  sequence, where `L_i` inserts groups `≤ i`'s history labels in final
  order; call `prompt_budget.nested_shares`; apply the drop rule from design
  Decision 4 step 4 (`split[0] == 0` while `head_sizes[i] > 0` and
  `outer[i] > 0` ⇒ drop, `head` reverts to `outer[i]`, every inner share
  `0`); bound each kept block with `bounded_text`, reusing #882's
  excerpt/omission marking; append every disclosed history title, suffixed
  ` (earlier version)`, into `excerpted_titles`/`omitted_titles`.
  `_assemble_context` calls `_bound_with_history` only when at least one
  history block exists; otherwise it calls the unchanged `_bound_bodies`.
  Makes 3.14–3.20 GREEN.

### `Citation.history` and attribution (design Decision 6)

- [x] **3.22** [TEST] Same file — `test_used_naming_and_citation_history_field`,
  covering: (a) the model's `USED:` attribution line names a history block's
  number — `citations` includes a `Citation` for that predecessor with
  `history` set to its role; (b) an ordinary hit citation always carries
  `history=None`; (c) an absent attribution line keeps every context-included
  block, hit and history, with hits at `history=None` and history blocks at
  their relation, and `attribution == "absent"`. Covers "A reported
  attribution naming a history block sets history", "An ordinary hit
  citation always carries history=None", "Absent attribution keeps every
  included block, hit or history". **RED today**: `Citation` has no
  `history` field — `AttributeError`/`TypeError`. Kills omitting `history=`
  when constructing a history-block citation, or defaulting an ordinary
  citation to anything other than `None`.
- [x] **3.23** [IMPL] `src/openkos/retrieval/answer.py` — add `history:
  Literal["superseded", "refined"] | None = None` to `Citation`, after
  `confidential`; construct every history-block citation with
  `history=pred.role`; every ordinary hit citation keeps the implicit
  `None` default. Makes 3.22 GREEN.

### `AnswerResult.context_block_count` and `history_truncated_titles` (design Decision 6)

- [x] **3.24** [TEST] Same file — `test_context_block_count_includes_history`:
  `revision_history=True`, a successor with 2 attached history blocks among
  otherwise ordinary hits; assert `context_block_count` equals the hit-block
  count plus 2, while `fused_count`, `fts_hit_count`, and `dense_hit_count`
  are unaffected. Covers "context_block_count includes attached history
  blocks". **RED today**: same reason as 3.3. Kills counting history blocks
  in `fused_count`/`fts_hit_count`/`dense_hit_count`.
- [x] **3.25** [TEST] Same file — `test_truncation_signal_cap_and_depth_causes`,
  covering both truncation causes: (a) a successor S titled "T" whose
  reachable predecessors within depth 3 exceed 3 — `history_truncated_titles`
  contains "T"; (b) a successor S titled "T" whose chain continues past
  depth 3 with fewer than 3 blocks attached — `history_truncated_titles`
  STILL contains "T". Covers "The truncation signal names the capped
  successor's title" and "A chain cut by the depth bound also sets the
  truncation signal". **RED today**: `AnswerResult` has no
  `history_truncated_titles` field. Kills reporting truncation for only one
  of the two causes.
- [x] **3.26** [TEST] Same file — `test_disabled_reports_no_truncation`:
  `revision_history=False` (the default); `history_truncated_titles == []`;
  `context_block_count` unaffected by this requirement. **RED today**: same
  reason as 3.25.
- [x] **3.27** [IMPL] `src/openkos/retrieval/answer.py` — add
  `history_truncated_titles: list[str] = field(default_factory=list)` to
  `AnswerResult`; thread it onto every return that follows assembly; in
  `_assemble_context`, when `walk.truncated` and `history_truncated_out is
  not None`, append the successor's title. `context_block_count` (already
  `len(context_blocks)`) naturally includes history blocks once they are
  appended to the same `labels`/`bodies`/`citations` lists as hits — confirm
  this holds rather than re-deriving the count. Makes 3.24–3.26 GREEN.

### Off path: zero extra reads and byte-identical prompt (must-have tests)

- [x] **3.28** [TEST] Same file — `test_off_path_zero_extra_reads`: install a
  `Path.read_text` spy counting bundle reads; call `answer()` with
  `revision_history` omitted (its default) and, separately, with
  `revision_history=False` explicitly, against a bundle containing
  `supersedes`/`revises` chains; assert exactly one read per fused hit — no
  extra reads for any chain member. Then call `answer(...,
  revision_history=True)` on the SAME bundle and assert the read count is
  strictly higher, proving the spy is not vacuous. Covers "Disabled by
  default makes zero extra reads". **RED today**: `revision_history` is not
  a recognized keyword — `TypeError` on the `True` call, which the test
  needs to establish the contrast. Kills calling the walk unconditionally
  regardless of the flag.
- [x] **3.29** [TEST] Same file — `test_off_path_messages_byte_identical`:
  with `revision_history=False` and with the parameter omitted, the captured
  LLM `messages` argument on a bundle with `supersedes`/`revises` chains is
  byte-for-byte equal to the captured `messages` against a chain-free twin
  bundle carrying the same hits. Covers the requirement's "byte-identical to
  the pre-history-feature behavior" clause and "revision_history is
  caller-supplied, not config-read". **RED today**: same reason as 3.28.
  Kills any code path that runs even a trivial no-op (e.g. always allocating
  `attached`/`rejected` and folding them into label construction) when the
  flag is off.
- [x] **3.30** [IMPL] `src/openkos/retrieval/answer.py` — confirm (adjusting
  if needed) that `revision_history=False` takes the exact pre-feature code
  path: no `walk_history` call, no `_bound_with_history` call (routes to the
  unchanged `_bound_bodies`), and every history-only field stays at its
  empty default. Makes 3.28–3.29 GREEN.

### `--include-deprecated` suppresses the walk (design Decision 8)

- [x] **3.31** [TEST] Same file — `test_include_deprecated_suppresses_history_walk`:
  `include_deprecated=True` with `revision_history=True` produces no history
  blocks at all, and the captured `messages` equal those of the same call
  with `revision_history=False`. Covers status-aware-retrieval's "History is
  suppressed under --include-deprecated". **RED today**: `answer()` doesn't
  yet compute `revision_history and not include_deprecated` — `TypeError`
  until the kwarg exists, then a behavioral failure once it does. Kills
  dropping the `not include_deprecated` term.
- [x] **3.32** [IMPL] `src/openkos/retrieval/answer.py` — in `answer()`,
  compute `walk = revision_history and not include_deprecated` and pass
  `revision_history=walk` into `_assemble_context`, alongside the
  already-computed `deprecated` frozenset (today's deprecated-set
  computation, unchanged). Makes 3.31 GREEN.

### Layering and config-free guards (design's stated invariants)

- [x] **3.33** [TEST] `tests/unit/retrieval/test_layering.py` (new) —
  `test_retrieval_does_not_import_resolution`: an AST scan of every module
  under `src/openkos/retrieval/` asserts none imports `openkos.resolution`
  or any of its submodules; a companion positive assertion that
  `retrieval/history.py` imports `openkos.event_dates`; extend or confirm
  the existing "`answer.py` does not import `config`" guard still passes
  here (move it into this file if it currently lives elsewhere, or leave it
  in place and just assert it stays green); a static check that `answer()`'s
  `revision_history` parameter has no internal read of `openkos.config`
  anywhere in `answer.py`. Covers "retrieval/ imports nothing from
  resolution/, and answer.py does not import config" and "revision_history
  is caller-supplied, not config-read". This is a structural regression
  guard, expected GREEN by construction from 2.8/3.2/3.4/3.32 — if it is
  RED, that is a design violation in an earlier task, not new production
  code to write here.
- [x] **3.34** [IMPL] N/A — regression guard only.

### Slice 2b verification

- [x] **3.35** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **3.36** Run `uv run pytest` (unpiped) — must be green, including
  every test added in this slice (3.3–3.33) and the full pre-existing
  `tests/unit/retrieval/test_answer.py` suite (3.1's baseline), unchanged
  beyond this slice's additions.
- [x] **3.37** Run `uv run python evals/run_self_tests.py` — must be green,
  confirming `evals/query_sufficiency/run_query_sufficiency_probe.py:297`,
  `evals/query_entailment/run_query_entailment_probe.py:348,871`, and
  `evals/query_attribution/run_query_attribution_probe.py:806,839` (the
  `_assemble_context`/`_bound_bodies` harness call sites) are unaffected.
- [x] **3.38** Commit as one or more work-unit commits — scope `retrieval`
  for the `_guarded_read` extraction (e.g. `refactor(retrieval): extract
  _guarded_read from the hit loop`), scope `retrieval` for the attach/
  budget/citation/truncation wiring (e.g. `feat(retrieval): attach revision
  history to answer() context`); tests alongside their behavior. Open PR 2b
  targeting PR 2a's branch.

---

## Slice 3 (PR 3 → PR 2b's branch): the config, CLI, and `--save` surface

### `config.py` — the `revision_history` key (design Decision 9)

- [x] **4.1** [TEST] `tests/unit/test_config.py` — add
  `test_revision_history_key_validation`: an absent key or an explicit
  `null` gives `cfg.revision_history is False`; `revision_history: true`
  gives `True`; `revision_history: 1`, `revision_history: "yes"`, and a list
  value are all refused with the same bool-only refusal message shape used
  by another existing bool key. Covers "Key absent defaults to off" and
  "A non-bool value is rejected". **RED today**: `Config` has no
  `revision_history` field and `read_config` never reads the key —
  `AttributeError`, or a truthy value is silently accepted instead of
  refused. Kills truthy coercion (accepting `1`/`"yes"` instead of
  refusing).
- [x] **4.2** [IMPL] `src/openkos/config.py` — add `DEFAULT_REVISION_HISTORY:
  Final = False` with a docstring stating the behavior is unmeasured and off
  until a harness measures it; add `revision_history: bool =
  DEFAULT_REVISION_HISTORY` as the LAST field on `Config` (defaulted, so the
  existing hand-built `config.Config(**fields)` test helpers, e.g.
  `tests/unit/cli/test_query_save.py:105-133`, stay valid without edits); in
  `read_config`, read `raw.get("revision_history")` and validate with the
  existing narrow `isinstance(x, bool)` guard and message shape, falling
  back to the default on an explicit `null`. Makes 4.1 GREEN.
- [x] **4.3** [CHECK] Run the full existing `tests/unit/test_config.py` and
  every hand-built `config.Config(**fields)` test helper (grep for
  `config.Config(` across `tests/`) — confirm none required an edit, per
  design's "added last and defaulted" guarantee.

### `application/query.py` — `run_query` threading (design's Approach)

- [x] **4.4** [TEST] `tests/unit/application/test_query.py` (adjust path to
  this repo's actual query-service test file if it differs) — add
  `test_run_query_threads_revision_history`: a spy on `answer()` records the
  `revision_history` kwarg it receives; `cfg.revision_history=True` results
  in `answer(..., revision_history=True)`; `cfg.revision_history=False` (or
  absent) results in `revision_history=False`. Covers "Key present and true
  is threaded to answer()". **RED today**: `run_query` never reads or passes
  `cfg.revision_history` — the spy never receives that kwarg, or receives a
  hard-coded value. Kills a hard-coded `False` regardless of
  `cfg.revision_history`.
- [x] **4.5** [IMPL] `src/openkos/application/query.py` — in `run_query`,
  read `cfg.revision_history` and pass `revision_history=cfg.revision_history`
  to `answer(...)`, alongside `sufficiency_check`. Makes 4.4 GREEN.

### `model/okf.py` — `build_concept`'s `related_notes` (design Decision 7)

- [x] **4.6** [TEST] `tests/unit/model/test_okf.py` — add
  `test_build_concept_related_notes`, covering: (a) an existing call site
  that does not pass `related_notes` produces byte-identical output to its
  pre-change golden/baseline (a regression pin — this half already passes
  once 4.7 lands with a `None` default); (b) a `related_notes={ref: note}`
  mapping renders that reference's `## Related` bullet with the mapped note
  instead of the default `related_note` text; (c) a `related_notes` key not
  present in `provenance` raises `ValueError`. Covers "build_concept without
  related_notes stays byte-identical". **RED today**: `build_concept` has
  no `related_notes` parameter — `TypeError` on (b)/(c). Kills the default
  path changing bytes (e.g. defaulting to `{}` instead of `None`, or
  iterating `related_notes` unconditionally even when unset).
- [x] **4.7** [IMPL] `src/openkos/model/okf.py` — add `related_notes:
  Mapping[str, str] | None = None` to `build_concept`; each `## Related`
  bullet reads `related_notes.get(ref, related_note)` when `related_notes`
  is not `None`, else the unchanged default `related_note` text; a
  `related_notes` key absent from `provenance` raises `ValueError`. Makes
  4.6 GREEN.

### `application/query.py` — `stage_filed_answer`'s history marking (design Decision 7)

- [x] **4.8** [TEST] `tests/unit/application/test_query.py` (same file as
  4.4) — add `test_stage_filed_answer_marks_history_citations`, covering:
  (a) a filed answer with one `history="superseded"` citation and one
  ordinary citation — `provenance:` lists both concept ids flatly, unchanged
  in shape; the filed concept's `## Related` section carries `— earlier
  version (superseded) cited as history for this answer` for the history
  citation and the unaffected default note for the ordinary one; (b) the
  same with a `history="refined"` citation, rendering `(refined)`; (c) with
  no history citations at all, the filed concept's provenance and
  `## Related` section are byte-identical to today's output. Covers "A
  superseded history citation is filed with its mark", "A refined history
  citation is filed with its mark", "An ordinary cited concept's Related
  bullet is unaffected". **RED today**: `stage_filed_answer` never builds a
  `related_notes` mapping — the marked bullet text never appears. Kills
  always constructing a non-empty `related_notes` mapping even when there
  are zero history citations (which `build_concept` must treat identically
  to `None`/no mapping).
- [x] **4.9** [IMPL] `src/openkos/application/query.py` — in
  `stage_filed_answer`, build `related_notes={c.concept_id: f"earlier
  version ({c.history}) cited as history for this answer" for c in citations
  if c.history}`, passing it (or `None` when empty) to the ingest builder's
  `build_concept` call. Makes 4.8 GREEN.

### `cli/main.py` — citation markers (design Decision 6)

- [x] **4.10** [TEST] `tests/unit/cli/test_query_save.py` (or this repo's
  actual query-rendering CLI test file — confirm the exact file before
  editing) — add `test_citation_history_markers`, covering: a citation with
  `history="superseded"` renders its line ending `[superseded]`;
  `history="refined"` renders `[refined]`; `history=None` renders neither;
  when a citation is also `confidential`/`excerpted`, the marker order is
  `history` first, then `synthesis`, then `partial`, then the existing
  confidential marker. Covers "A superseded history citation renders its
  marker", "A refined history citation renders its marker", "An ordinary
  citation carries neither marker". **RED today**: the citation-rendering
  code has no `history` lookup — the marker never appears. Kills the marker
  mapping (swapping which string maps to which role, or omitting one).
- [x] **4.11** [IMPL] `src/openkos/cli/main.py` — add `history =
  {"superseded": " [superseded]", "refined": " [refined]"}.get(
  citation.history, "")`, rendered first in the marker sequence:
  `f"{history}{synthesis}{partial}{marker}"`. Makes 4.10 GREEN.

### `cli/main.py` — truncation stderr notice (design Decision 6)

- [x] **4.12** [TEST] Same CLI test file — add
  `test_history_truncation_stderr_notice`, covering: `result.
  history_truncated_titles` with one title prints the exact stderr sentence
  `openkos query: the revision history of 1 document(s) (T1) goes back
  further than the earlier versions shown; the answer did not see the
  rest.`, printed after the existing omitted-context notice; with two
  titles, both are named and the count reads `2`; with
  `history_truncated_titles == []` (including every `revision_history=False`
  run), no notice is printed. Covers "A truncated successor's title triggers
  the stderr notice", "Multiple truncated successors are named together",
  "No truncation prints no notice". **RED today**: `cli/main.py` never
  reads `result.history_truncated_titles`, so no notice is ever printed.
  Kills printing the notice unconditionally, or misordering it before the
  omitted-context notice.
- [x] **4.13** [IMPL] `src/openkos/cli/main.py` — after the existing
  omitted-context notice, when `result.history_truncated_titles` is
  non-empty, print the exact sentence from design Decision 6 to stderr,
  interpolating the count and the comma-joined titles. Makes 4.12 GREEN.

### Config template (design Decision 9)

- [x] **4.14** [TEST] `tests/unit/test_config.py` (or the existing template
  test, if the project already has one) — add
  `test_template_documents_revision_history`: parse
  `templates/openkos.yaml.template` and assert a commented
  `# revision_history: false` block is present after `sufficiency_check`,
  and its comment text states the key is opt-in, unmeasured, and off by
  default, without recommending enabling it. Covers "The template documents
  the key as unmeasured and off by default". **RED today**: the template
  has no `revision_history` block.
- [x] **4.15** [IMPL] `src/openkos/templates/openkos.yaml.template` — add
  the commented `# revision_history: false` block after `sufficiency_check`
  with the required wording. Makes 4.14 GREEN.

### Leftover-key tolerance (design Decision 9, regression pin)

- [x] **4.16** [TEST] `tests/unit/test_config.py` — add (or extend an
  existing) `test_unknown_top_level_key_is_ignored` asserting that
  `read_config` never rejects an arbitrary unrecognized top-level key —
  the standing proof that a leftover `revision_history: true` line, left
  behind after a hypothetical revert of this feature, would still load
  without error. Covers "A leftover key from a reverted feature is silently
  ignored". This is a regression pin on `read_config`'s existing unknown-key
  tolerance, not new production behavior.
- [x] **4.17** [IMPL] N/A — `read_config`'s existing unknown-key tolerance
  already satisfies 4.16 by construction; no production change.

### Docs

- [x] **4.18** [IMPL] `docs/cli.md` — add a `query` note documenting the
  `revision_history` config key, the `[superseded]`/`[refined]` citation
  markers, the truncation stderr notice, and `--save`'s `## Related` marking;
  state plainly that the key is unmeasured and not recommended for enabling
  yet.

### Slice 3 verification

- [x] **4.19** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **4.20** Run `uv run pytest` (unpiped) — must be green, including
  4.1, 4.4, 4.6, 4.8, 4.10, 4.12, 4.14, and 4.16, plus the full existing
  `config`, `application/query`, `model/okf`, and CLI `query` suites,
  unaffected beyond this slice's additions.
- [x] **4.21** Run `uv run python evals/run_self_tests.py` — must be green,
  confirming no eval harness regresses from the `Config`/`build_concept`
  signature changes.
- [x] **4.22** Commit as one or more work-unit commits — scope `config` for
  the key and template (e.g. `feat(config): add revision_history key`),
  scope `okf` for `build_concept`'s `related_notes` (e.g. `feat(okf): add
  optional related_notes to build_concept`), scope `retrieval` (or the
  closest existing scope covering `application/query.py`) for `run_query`/
  `stage_filed_answer` threading, scope `cli` for the markers and notice
  (e.g. `feat(cli): render revision-history citation markers and truncation
  notice`), scope `docs` for `docs/cli.md`; tests alongside their behavior.
  Open PR 3 targeting PR 2b's branch.

---

## Post-merge (archive phase, not a task here)

Per `openspec/config.yaml`'s `rules.archive`, the archive phase — not this
task list — merges the three delta specs
(`openspec/changes/superseded-history-in-query/specs/{query-answer,
status-aware-retrieval,query-command}/`) into their living
`openspec/specs/{domain}/spec.md` files, and flips ADR-0026's status from
`Proposed` to `Accepted` in both the frontmatter and the body `**Status:**`
line, plus its `docs/adr/README.md` index row.

Unlike the sibling `revises-relation` change, no delta here carries a
hand-apply-only "Non-Goals Correction" section — the design's "Open
Questions / spec alignment" items (the nested-budget wording, the label's
"edge holder vs. successor" wording, the `revises` currency exception, the
truncation-by-depth signal, and the dedupe scope) are already reconciled
into the three delta specs read for this task list; no further hand-editing
is required at archive beyond the ordinary delta merge and the ADR flip.

No task above performs any archive-phase action; it is explicitly out of
scope for `sdd-apply`.
