# Tasks: source-provenance-shape — stop writing `provenance` on a Source concept

Refs #1076. Proposal: `proposal.md`. Delta: `specs/ingestion/spec.md`.

Strict TDD is ON, runner `uv run pytest`. Every behavioral task's test was
observed RED against the pre-change code before the paired implementation
made it GREEN; a test that would have passed against the unchanged code was
mutation-proved on its exact line (inverse edit, `__pycache__` purged)
before being trusted.

## Phase 1: Evidence and decision

- [x] 1.1 Read every consumer of a Source's own `provenance` field
      (CodeGraph + `grep -rn "\.provenance\b" src/openkos`): `model/okf.py`
      (`project_sources`, never projects a `raw/`-prefixed entry),
      `bundle/provenance.py` (`find_provenance_descendants`/
      `provenance_closure`, `resolve_source_raises`/
      `resolve_backfill_raises`, `find_unresolvable_provenance`),
      `lint.py` (`check_dangling_provenance`, `check_unbacked_provenance`),
      `application/ingest.py` (`family_owns_source`), `event_dates.py`
      (`resolve_event_date`), `graph/sqlite_graph.py`. See `proposal.md`'s
      "Evidence" section for the file:line findings.
- [x] 1.2 Confirm the canonical example
      (`examples/good-life-demo/bundle/sources/*.md`) already carries no
      `provenance` key on either Source, and that
      `docs/knowledge-object-model.md` already documents "a Source carries
      no `provenance`" as the intended shape -- both already match the
      target state; the decision is to fix `ingest`, not the example or the
      docs.
- [x] 1.3 Decide: `ingest` stops writing `provenance` on a Source;
      `okf.build_source_concept`'s `provenance` parameter becomes optional
      (`None` default, emitted only when non-empty) rather than removed
      outright, so a fixture modeling a pre-existing, legacy-shaped Source
      can still round-trip one. No consumer depends on a Source's own
      `provenance`; `lint`/`find_unresolvable_provenance`'s existing
      raw-resource exclusions are kept, now serving only pre-existing
      bundles, with no migration of already-ingested Sources.

## Phase 2: openspec change

- [x] 2.1 Create `openspec/changes/source-provenance-shape/` with
      `proposal.md`, `specs/ingestion/spec.md` (MODIFIED: "Ingest Raw Copy
      and Source Concept Generation", "OKF-Native Provenance"), and this
      `tasks.md`.
- [x] 2.2 Verify the delta composes cleanly: `gentle-ai sdd-archive-compose
      --canonical openspec/specs/ingestion/spec.md --delta
      openspec/changes/source-provenance-shape/specs/ingestion/spec.md
      --output -` exits 0, and the composed output differs from the
      canonical spec ONLY in the two modified requirements (diffed by hand
      against `openspec/specs/ingestion/spec.md`).
- [x] 2.3 Checked every other domain's spec for a requirement mentioning a
      Source's own `provenance`: none does (`sensitivity-backfill/spec.md`'s
      Non-Goals section notes "every Source cites its raw `resource`" as
      design rationale for NOT calling `find_unresolvable_provenance`, but
      that rationale is unchanged -- pre-existing Sources still do, and the
      sweep still must not warn about them -- and it sits in Non-Goals, not
      a `### Requirement:` heading, so no delta is needed there). No other
      domain delta required.

## Phase 3: Implementation (TDD)

- [x] 3.1 [TEST] `tests/unit/model/test_okf.py`: drop the `_build_call_source`
      helper's hardcoded `"provenance": [...]` default; change
      `test_build_source_concept_emits_required_frontmatter_fields` to
      assert `"provenance" not in metadata`; add
      `test_build_source_concept_omits_provenance_by_default`,
      `test_build_source_concept_emits_provenance_when_explicitly_given`,
      `test_build_source_concept_omits_provenance_when_given_an_empty_list`.
      Observed RED: `TypeError: build_source_concept() missing 1 required
      keyword-only argument: 'provenance'` (the helper no longer passes it)
      and an explicit-empty-list assertion failure.
- [x] 3.2 [IMPL] `src/openkos/model/okf.py::build_source_concept`:
      `provenance: list[str] | None = None`; emit `metadata["provenance"]`
      only `if provenance:` (truthy -- `None` and `[]` both omit it).
      Updated the docstring (the "FOUR values" paragraph, and a new
      dedicated `provenance` parameter paragraph). GREEN: `uv run pytest
      tests/unit/model/test_okf.py -k source_concept` (28 passed) and the
      full file (275 passed).
- [x] 3.3 [TEST] `tests/unit/application/test_ingest.py`: add
      `test_compose_source_document_never_writes_provenance` and
      `test_compose_catalog_update_rerender_never_writes_provenance`; update
      `test_compose_source_document_frontmatter_free_is_byte_identical`'s
      reference `okf.build_source_concept(...)` call to drop
      `provenance=["raw/notes.txt"]` (mirroring the production call it
      pins) and assert `"provenance" not in` the loaded metadata. Observed
      RED: both new tests failed (`provenance` present in the composed
      metadata).
- [x] 3.4 [IMPL] `src/openkos/application/ingest.py`: drop
      `provenance=[resource],` from both `build_source_concept(...)` call
      sites (`compose_source_document`, `compose_catalog_update`'s
      conditional re-render). GREEN: full `tests/unit/application/
      test_ingest.py` (80 passed).
- [x] 3.5 [TEST] `tests/unit/cli/test_ingest.py::test_successful_ingest_of_valid_path`:
      updated to assert `metadata["resource"] == "raw/notes.txt"` and
      `"provenance" not in metadata` instead of `metadata["provenance"] ==
      ["raw/notes.txt"]`. Observed RED (`KeyError: 'provenance'`) against
      the already-changed production code, confirming the test exercises
      the real write path; GREEN after the assertion update.
- [x] 3.6 Audited every other `provenance`-related test in
      `tests/unit/cli/test_ingest.py`, `tests/unit/cli/
      test_backfill_source_titles.py`, `tests/unit/bundle/
      test_provenance_source_raises.py`, `tests/unit/bundle/
      test_cited_high_water_raises.py`, and `tests/unit/model/
      test_okf_sources_projection.py`: each hand-builds a Source fixture
      with a `raw/`-prefixed `provenance` entry to test a DIFFERENT
      concern (collision-family staging, `backfill-source-titles`'
      byte-swap guard, `find_unresolvable_provenance`'s general algorithm,
      `project_sources`'s own exclusion) against a document that MODELS a
      pre-existing/legacy/hand-authored shape -- none asserts what `ingest`
      itself produces, so none needed a change.
- [x] 3.7 [TEST] `tests/unit/test_lint_dangling_provenance.py`: added
      `test_a_freshly_ingested_source_with_no_provenance_is_never_flagged`
      (a Source with `provenance=()`, asserting zero findings via the loop
      never running, independent of the `doc.resource == entry`
      exclusion). This test passed on the first run (the check's logic is
      unchanged), so it was mutation-proved: temporarily changed
      `for target in doc.provenance:` to `for target in doc.provenance or
      ("__mutation_sentinel__",):`, confirmed the test failed with a
      spurious `dangling-provenance` finding, then reverted the exact line
      and purged `__pycache__` before re-confirming GREEN.
- [x] 3.8 [DOCS] Updated `check_dangling_provenance`'s docstring
      (`src/openkos/lint.py`) and `tests/unit/test_lint_dangling_provenance.py`'s
      module/test docstrings to say the `doc.resource == entry` exclusion
      is now legacy-only -- kept for Sources ingested before this change,
      never produced by current `ingest`. No behavior change; the exclusion
      itself is unchanged.
- [x] 3.9 [DOCS] `src/openkos/model/okf.py::build_source_concept`: docstring
      updated (see 3.2) to describe the new optional `provenance` parameter
      and its rationale, cross-referencing "OKF-Native Provenance".

## Phase 4: Verification

- [x] 4.1 `uv run ruff check .` -- All checks passed.
- [x] 4.2 `uv run ruff format --check .` -- 381 files already formatted.
- [x] 4.3 `uv run mypy .` -- Success: no issues found in 381 source files.
- [x] 4.4 `uv run pytest --cov` -- full suite green, branch coverage ≥90%
      (see commit/PR for the exact numbers).
- [x] 4.5 `uv run python evals/run_self_tests.py` -- 44 of 44 harness
      self-tests run, 0 failing.
- [x] 4.6 `openkos lint` and `openkos status` against a fresh copy of
      `examples/good-life-demo` in a temp dir (never modifying the
      checked-in example): both exit 0, `lint` reports "No dangling
      provenance findings" (13/13 checks completed, 0 did not run).

## Out of scope (confirmed, not implemented)

- No change to `examples/good-life-demo` (already matched the target shape
  before this change).
- No migration/`repair` step for existing `provenance: [raw/<file>]`
  Source entries.
- No change to a derived concept's own `provenance` (`build_concept`'s
  parameter stays required and non-empty).
- No change to `find_unresolvable_provenance`/`resolve_backfill_raises`'s
  existing raw-resource workaround (still needed for pre-existing bundles).
