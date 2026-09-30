# Tasks: source-tag-sync — add a Source's tags to its existing derived concepts

Refs #1093. Proposal: `proposal.md`. Design: `design.md` (Decisions 1-9).
Specs: `specs/tag-sync/spec.md`, `specs/ingestion/spec.md`. ADR-0033
(Proposed, committed with this plan).

Strict TDD is ON, runner `uv run pytest`. Every behavioral `[TEST]` task
records an observed RED with the reason it is RED today; its paired
`[IMPL]` introduces only what that test pins. A `[TEST]` that passes on its
first run is not trusted until its `[MUT]` task has mutated the exact line
it guards, watched it go RED, and reverted with the inverse edit (never
`git checkout --`), purging `__pycache__` before re-running.

Every absence assertion (byte-unchanged file, no commit, no advisory, no tag
value in `log.md`) carries a precondition in the same test proving the
thing existed or could have appeared: the file existed with known bytes, a
commit count was read first, the advisory does print under the sibling
fixture, the tag value is present in the Source.

No test reaches Ollama; every CLI fixture is a git-initialized `tmp_path`
workspace. Gate before each PR: `uv run ruff check .`, `uv run ruff format
--check .`, `uv run mypy .`, `uv run pytest --cov`, `uv run python
evals/run_self_tests.py`.

## Review Workload Forecast

| Phase | PR | Est. authored lines | Scope |
|---|---|---|---|
| 1 | PR 1 → `main` | ~300 | `okf` dataclasses + pure resolver |
| 2 | PR 2 → `main` | ~350 | application `prepare`/`core` |
| 3 | PR 3 → `main` | ~400 | CLI verb + `docs/cli.md` |
| 4 | PR 4 → `main` | ~120 | ingest advisory |

Each phase depends only on the one before it, except Phase 4, which needs
only the verb name and may land after Phase 3 or together with it.

## Scenario → Task Coverage

| Spec requirement | Tasks |
|---|---|
| Target Selection | 2.5-2.8, 3.1-3.4 |
| Write Set Is The Closure, Minus Sources | 1.3-1.6 |
| Union Only, Never Remove | 1.7-1.11, 3.9 |
| Malformed Tags Value | 1.12-1.14 |
| Below The Source's Sensitivity | 1.15-1.18 |
| --all Fold | 2.9-2.11 |
| Preview, Confirm Gate, --auto | 3.5-3.8 |
| Drift Guard, Write Order, Log, Commit | 2.12-2.15, 3.10-3.14 |
| Ingestion: Source-Only Rewrite advisory | 4.1-4.5 |

## Phase 1 (PR 1): Pure resolver

### Precondition

- [x] **1.1** [CHECK] `grep -rn "sync_tags\|sync-tags\|TagAddition\|resolve_source_tag_additions" src tests`
      returns nothing, recorded in the PR description, so every later test
      is known to be RED for the right reason.

### `model/okf.py` — dataclasses

- [x] **1.2** [IMPL] Add frozen `TagAddition(concept_id, added, content)`
      and `TagSkip(concept_id, reason)` beside `DescendantRaise`, Path-free.
      Pure data, exercised through 1.3+; no standalone test.

### `bundle/provenance.py` — closure (Decision 1)

- [x] **1.3** [TEST] `tests/unit/bundle/test_provenance_tag_additions.py`:
      `test_single_source_descendant_is_staged` — Source `sources/notes`
      (`tags: [alpha]`), `concepts/a` citing only it, untagged → one
      `TagAddition("concepts/a", ("alpha",), …)`. RED: function missing.
- [x] **1.4** [TEST] `test_transitive_descendant_is_staged` (a → b chain)
      and `test_multi_source_concept_is_not_a_candidate` (precondition: the
      same concept IS staged when its provenance is only `sources/a`).
- [x] **1.5** [TEST] `test_source_typed_member_is_never_written` — a
      `type: Source` concept inside the closure is absent from both result
      lists; precondition: an otherwise identical `type: Concept` member is
      staged.
- [x] **1.6** [IMPL] `resolve_source_tag_additions`: walk
      `find_provenance_descendants(files, root_ids={source_id})`, drop the
      root and Source-typed members, sort by id. GREEN 1.3-1.5.

### Union rule (Decision 2, ADR-0033)

- [x] **1.7** [TEST] `test_union_appends_after_existing` — existing
      `[gamma, alpha]`, Source `[alpha, beta]` → content's `tags` is
      exactly `[gamma, alpha, beta]` (parsed back, literal list) and
      `added == ("beta",)`.
- [x] **1.8** [TEST] `test_hand_added_tag_survives` and
      `test_tag_removed_from_source_is_kept` (literal expected lists).
- [x] **1.9** [TEST] `test_complete_member_is_not_staged` — precondition:
      the member is a closure member (asserted via
      `find_provenance_descendants`), then both result lists empty.
- [x] **1.10** [TEST] `test_only_tags_changes` — body, `sensitivity`,
      `provenance`, `title` re-parse equal to the original values.
- [x] **1.11** [IMPL] Compute `okf.union_tags(existing, source_tags)`,
      stage only when it adds, render via `okf.dump_frontmatter`. GREEN
      1.7-1.10. [MUT] for any first-try-green test among them: replace
      `union_tags(existing, source_tags)` with `list(source_tags)` → 1.8
      must go RED; revert.

### Malformed tags (Decision 3)

- [x] **1.12** [TEST] `test_malformed_tags_are_skipped`, parametrized over
      a mapping, a bare string, a number, `[alpha, 3]` → one
      `TagSkip(…, "malformed-tags")`, no addition.
- [x] **1.13** [TEST] `test_absent_or_null_tags_gain_source_tags` → added
      `("alpha",)`, content `tags == ["alpha"]`.
- [x] **1.14** [IMPL] Writable iff absent/`None` or a list of `str`.
      GREEN 1.12-1.13. [MUT] if 1.12's bare-string case passed first try:
      route strings through `okf.normalize_tags` → it must go RED; revert.

### Sensitivity floor (Decision 4, ADR-0033)

- [x] **1.15** [TEST] `test_below_source_sensitivity_is_skipped` —
      Source level `confidential`, member `private` → `TagSkip(…,
      "below-source-sensitivity")`; precondition: the same member at
      `confidential` is staged.
- [x] **1.16** [TEST] `test_missing_member_sensitivity_under_confidential_is_skipped`
      and `test_unrecognized_member_sensitivity_is_not_below` (ranks
      fail-closed at `confidential`, so it is staged).
- [x] **1.17** [IMPL] Skip when `okf.sensitivity_direction(member_raw,
      source_level) == "raise"`. GREEN 1.15-1.16.
- [x] **1.18** [MUT] Flip the comparison to `== "lower"` → 1.15 must go
      RED; revert, purge `__pycache__`, re-run green.

### Phase 1 close

- [x] **1.19** Run the full gate. Commit `feat(bundle): resolve Source tag
      additions over the provenance closure (#1093)`.

## Phase 2 (PR 2): Application service

- [x] **2.1** [CHECK] Phase 1 merged; `grep -n "prepare_sync_tags"
      src/openkos/application/lifecycle.py` returns nothing.
- [x] **2.2** [IMPL] `PreparedTagSync` dataclass (design Interfaces).
- [x] **2.3** [TEST] `tests/unit/application/test_lifecycle.py`:
      `test_prepare_sync_tags_single_source` — `tmp_path` workspace, one
      Source + one descendant → one addition, `roots == ("sources/notes",)`,
      `confirmation` equals `boolean_confirmation("sync-tags")`.
- [x] **2.4** [IMPL] `prepare_sync_tags` single-Source path: resolve via
      `resolve_concept_path`, snapshot every non-reserved `.md` with
      `fsio.snapshot_read`, read `normalize_tags(source.tags)` and
      `combine_sensitivity(source.sensitivity, "public")`, call the
      resolver. GREEN 2.3.
- [x] **2.5** [TEST] `test_prepare_sync_tags_refuses_non_source` →
      `ValueError` naming the id and "Source".
- [x] **2.6** [TEST] `test_prepare_sync_tags_refuses_unsafe_id`
      (parametrized: absolute, `..`, reserved basename, missing).
- [x] **2.7** [IMPL] `type == "Source"` check. GREEN 2.5 (2.6 is expected
      green on first run through `resolve_concept_path`).
- [x] **2.8** [MUT] Remove the `type` check → 2.5 RED; revert. For 2.6,
      bypass `resolve_concept_path` with a plain join → at least the `..`
      case RED; revert.

### `--all` fold (Decision 8)

- [x] **2.9** [TEST] `test_prepare_sync_tags_all_folds_every_source` — two
      Sources, one descendant each → two additions, roots sorted.
- [x] **2.10** [TEST] `test_all_stages_each_file_once` — a descendant in
      two closures (Source B citing only Source A is the fixture that makes
      this reachable; Source-typed B itself is not written) gains the union
      in root order and appears once.
- [x] **2.11** [IMPL] Enumerate Sources by `type`, sort, accumulate per
      member, render once. GREEN 2.9-2.10.

### Log text and baselines (Decisions 5, 6)

- [x] **2.12** [TEST] `test_log_entry_carries_no_tag_value` — Source
      `tags: [secret-project]`; precondition: `"secret-project"` is in the
      Source text; then `"secret-project" not in prepared.new_log_text` and
      the entry reads `**Sync-tags**: Added tags from
      [sources/notes](/sources/notes.md) to 1 concept(s).`
- [x] **2.13** [TEST] `test_baselines_include_roots_and_log` — keys are
      staged files + contributing Sources + `log.md`.
- [x] **2.14** [IMPL] Log rendering via `bundle_log.insert_log_entry`,
      baselines dict. GREEN 2.12-2.13.
- [x] **2.15** [TEST+IMPL] `sync_tags_core` writes additions in id order
      then `log.md`; `test_sync_tags_core_names_landed_on_failure`
      (monkeypatch `fsio.write_atomic` to fail on the second call, assert
      the monkeypatch was actually invoked, then the error's `landed` list
      is exactly the first path). RED first, then implement.
- [x] **2.16** Full gate. Commit `feat(cli): stage Source tag sync in the
      lifecycle service (#1093)`.

## Phase 3 (PR 3): CLI verb + docs

- [ ] **3.1** [TEST] `tests/unit/cli/test_sync_tags.py`:
      `test_both_forms_refuse` and `test_neither_form_refuses` — exit 1,
      stderr names both forms; precondition: bundle bytes hashed before,
      unchanged after.
- [ ] **3.2** [TEST] `test_non_source_refuses` — exit 1, "takes a Source".
- [ ] **3.3** [IMPL] `@app.command("sync-tags", rich_help_panel="Maintain")`,
      `@_guard_workspace_lock("sync-tags")`, `sync_tags_cmd(source_id:
      str | None, all_: bool = --all, auto: bool = --auto)`; argument
      validation before any read; `prepare_sync_tags`, catching
      `OSError`/`ValueError` → exit 1. GREEN 3.1-3.2.
- [ ] **3.4** [TEST] `test_help_lists_sync_tags` in the `Maintain` panel.
- [ ] **3.5** [TEST] `test_preview_lines` — `~ bundle/concepts/a.md (tags
      added: alpha, beta)` then `~ log.md (new dated entry)`.
- [ ] **3.6** [TEST] `test_decline_writes_nothing` (TTY forced, input
      `n`); `test_non_tty_without_auto_refuses` (exit 1, names `--auto`);
      `test_review_false_skips_prompt`.
- [ ] **3.7** [IMPL] Preview + shared gate driven by
      `prepared.confirmation`. GREEN 3.5-3.6.
- [ ] **3.8** [TEST] `test_skip_lines_on_stderr` — malformed WARNING and
      below-sensitivity note naming `openkos set-sensitivity`, once each.
      [IMPL] render `prepared.skips`.
- [ ] **3.9** [TEST] `test_nothing_to_sync_exits_zero` — precondition: git
      commit count read before; after: same count, message printed.
- [ ] **3.10** [TEST] `test_descendant_drift_exits_3` and
      `test_source_drift_exits_3` — edit the file inside a monkeypatched
      `typer.confirm` (assert it was called), then exit 3 and every staged
      file byte-unchanged.
- [ ] **3.11** [IMPL] `_reject_drifted_targets(layout,
      prepared.baselines, "sync-tags")`. GREEN 3.10.
- [ ] **3.12** [MUT] Drop the Source paths from the baselines → 3.10's
      Source case RED; revert.
- [ ] **3.13** [TEST] `test_one_commit_message_and_refresh` — commit count
      +1, message `openkos: sync-tags sources/notes`, no tag value in it;
      `_refresh_derived_after_write` spy called exactly once with
      `verb="sync-tags"`; `--all` variant message `openkos: sync-tags --all`.
- [ ] **3.14** [IMPL] `sync_tags_core`, `_autocommit`, refresh; mid-write
      failure message names landed paths. GREEN 3.13.
- [ ] **3.15** [DOC] `docs/cli.md`: add `### openkos sync-tags (<source-id>
      | --all)` after `backfill-sensitivity`, stated timelessly: what it
      adds, that it never removes (link ADR-0033), the closure and its
      multi-Source exclusion, both skip rules, gate/drift/commit shape, and
      that tag values stay out of `log.md` and the commit. No issue
      numbers as "since", no verb counts. Verify every flag named exists in
      `openkos sync-tags --help`.
- [ ] **3.16** Full gate. Commit `feat(cli): add sync-tags to add a
      Source's tags to its derived concepts (#1093)` and `docs(cli): …`.

## Phase 4 (PR 4): Ingest advisory

- [ ] **4.1** [TEST] `tests/unit/cli/test_ingest.py`:
      `test_tag_delta_rewrite_advises_sync_tags` — Source-only rewrite
      fixture (stored `[alpha]`, incoming `[beta]`): stderr contains
      exactly one line naming `openkos sync-tags sources/<slug>`; no
      `set-sensitivity` advisory. RED: no such line.
- [ ] **4.2** [TEST] `test_no_tag_delta_no_sync_tags_advisory` —
      event-date-only and frontmatter-only rewrites; precondition: the
      4.1 fixture does print it (shared helper asserts presence first).
- [ ] **4.3** [TEST] `test_tag_and_sensitivity_delta_print_both`.
- [ ] **4.4** [IMPL] One `typer.echo(..., err=True)` keyed on
      `converged is not None and source_plan.tags_added`, next to the
      `set-sensitivity` advisory; wording must not assert that derived
      objects exist. GREEN 4.1-4.3.
- [ ] **4.5** [MUT] Key the advisory on `source_plan.lift_changed`
      instead → 4.2's frontmatter-only case RED; revert.
- [ ] **4.6** Full gate. Commit `feat(cli): advise sync-tags when a
      Source-only rewrite adds tags (#1093)`.

## Archive

- [ ] **5.1** Merge `specs/tag-sync/spec.md` as the new canonical domain and
      compose `specs/ingestion/spec.md`; name-match every requirement
      heading. Flip ADR-0033 to Accepted in frontmatter, body, and README
      (renumber first if #1075 did not take 0032).
