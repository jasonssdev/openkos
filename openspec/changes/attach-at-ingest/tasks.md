# Tasks: attach-at-ingest (#1268 deliverable 1)

Strict TDD: every task starts with a failing test and ends green. Each task
is one work unit (one Conventional Commit, scope `ingest`, `okf`,
`config`, `cli` or `docs` as noted). Runner: `uv run pytest`.

## Phase 1: Shared building blocks

- [x] 1.1 Pin merge output first. Add byte-exact golden tests over
      `okf.build_merged_document` (list union order, high-water
      sensitivity, freshness winner, legacy `timestamp`, `sources`
      re-projection, `type_alternative`/`event_date` exclusion); then factor
      the field-union core into one private function both builders call.
      Goldens stay green unchanged. (`okf`)
- [x] 1.2 `okf.build_attached_document`: `version` read as int (absent or
      non-int is 1) plus 1; `provenance` union and `sources`
      re-projection; `tags` union; `combine_sensitivity` never lowers;
      newer-side `freshness`/`generated`; identity fields untouched;
      `## Update from <title> (sources/<slug>)` section above `## Related`
      with headings demoted via the existing helper; contained-body skip;
      one `## Related` bullet; no `merged_from`; result passes
      `okf.check_conformance`. (`okf`)
- [x] 1.3 Promote the eligibility walk in `resolution/candidates.py`
      (`_iter_eligible` / `_eligible_keyed_docs`) to one public
      `keyed_documents`, and promote the base/`-N` identity rule
      (`lifecycle._is_suffix_family` / `ordered_merge_pair`) to a public
      `canonical_family_member`. Equivalence tests: `find_candidates` and
      `find_exact_title_groups` outputs unchanged on the existing fixtures.
      (`model` / `lifecycle`)

## Phase 2: Staging

- [x] 2.1 Config key `attach_at_ingest` (bool, default per Open Question 1)
      in `config.read_config`, the workspace template comment, and its
      requirement in the ingestion spec delta so
      `tests/unit/test_config_keys_specified.py` passes; non-boolean refused
      like the other boolean keys. (`config`)
- [x] 2.2 `AttachIndex` and the attach decision in
      `stage_derived_objects`: exclusion constant `ATTACH_EXCLUDED_TYPES =
      {"Event", "Person"}`; different type does not match; deprecated is not
      a target and falls back to `-N`; family attaches to the canonical
      member; a same-source match anywhere in the matches is the
      create-only no-op; `None` lookup reproduces today's plans exactly.
      `DerivedPlan` gains the attach fields; a new `StagingDrop` kind.
      (`ingest`)
- [x] 2.3 `compose_catalog_update`: no second `index.md` bullet for an attach
      plan; one `**Attach**` log entry; `**Disambiguation**` entry unchanged
      for the candidates attach does not take. (`ingest`)

## Phase 3: Service, adapter, watch

- [x] 3.1 `ingest_service._prepare`/`_write`: build the lookup from the
      drift-guard snapshot reads; attach targets in `guarded_targets` and not
      `created_targets`; Phase B writes them with `write_atomic`; commit
      paths and message (`+N concepts, ~M revised`, byte-identical when
      M is 0); `IngestOutcome.attached_count`. Tests: drift refusal on a
      changed target writes nothing; interrupted-run adoption (#1136) stays
      idempotent; a hand-edited target is refused by the guard. (`ingest`)
- [x] 3.2 CLI presentation of attaches (stderr drop line; stdout summary
      names id and new `version`; batch summary counts). No logic in
      `cli/main.py`. (`cli`)
- [x] 3.3 Watch end-to-end under `tests/unit/e2e/`: import, edit, import;
      assert no `-N` copy, `version: 2`, both Sources in provenance, the
      supersession row still proposed and nothing deprecated, and a
      re-saved version converges with no model call. A second case: the
      same shape for a `Person` still forks. (`ingest`)

## Phase 4: Proof and docs

- [ ] 4.1 Mutation checks on the exact lines, restored by inverse edit and
      `__pycache__` purged: the `Event`/`Person` exclusion, the
      same-source guard, `version + 1`, the guarded-target registration,
      the deprecated filter, the type equality in the key. Each mutation
      must turn a named test red.
- [ ] 4.2 `docs/cli.md` ingest section (what attach does, the key, the
      disclosure, the undo); `docs/architecture.md` only if a layer changes
      (it should not). `docs/adr/NNNN-...md` with status Proposed and its
      row in `docs/adr/README.md` (next free number at apply time; see
      design.md "ADR gate"). (`docs`)
- [ ] 4.3 Gates, run directly: `uv run ruff check .`, `uv run ruff format
      --check .`, `uv run mypy .`, `uv run pytest --cov`,
      `uv run python evals/run_self_tests.py`,
      `uv run pytest -q tests/unit/test_adr_index.py`. PR carries
      `Refs #1268` and `Refs #1259` on separate lines.
