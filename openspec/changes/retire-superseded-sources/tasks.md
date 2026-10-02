# Tasks: retire-superseded-sources (#1268 deliverable 2)

Strict TDD: every task starts with a failing test and ends green. Each task
is one work unit (one Conventional Commit; scopes `lint`/`model`/`cli` as
the code touched, `docs` for docs). Runner: `uv run pytest`. Apply
`attach-at-ingest` first; tasks 1.x and 2.x do not depend on it, 3.x are
written against its shape.

## Phase 1: Deprecation follows provenance

- [x] 1.1 Pure `provenance_orphans(provenance_by_id, superseded_ids)` in
      `lifecycle.py`, reusing `bundle.provenance.provenance_closure` and
      rooted only at superseded ids under `sources/`; table-driven tests:
      sole-source, shared with a live Source, empty provenance, transitive
      Insight, cycle, non-Source superseded id, roots excluded from the
      result. Settle the import direction (import the closure vs move it to
      a leaf) and pin it with an AST guard that `lifecycle` imports nothing
      from `state`, `retrieval` or `graph`. (`lint`)
- [x] 1.2 `deprecated_concept_ids` collects normalized provenance in its
      existing walk and folds the orphans in; tests over a real bundle
      including `unrelate` restoring liveness with no file written, and an
      unreadable document failing safe (no hide, no crash). (`lint`)
- [x] 1.3 `bundle/listing.list_objects` calls the same pure function over the
      data it already holds (still one walk); parity test asserting `list`
      `deprecated` rows equal `deprecated_concept_ids` on one shared
      fixture. (`cli`)
- [x] 1.4 Retrieval proof: a retrieval/`answer` test where a sole-source
      concept of a superseded Source is absent from FTS, vector and fused
      results and from adjudication/contradiction candidates, and present
      with `--include-deprecated`. (`retrieval`)
- [x] 1.5 Export stays edge-only: tests that `relate`, `unrelate`, `forget`,
      `merge`, `repair` and the `lint` drift scan write and report nothing
      for a provenance orphan; `superseded_from_metadata` unchanged.
      (`lint`)

## Phase 2: Vocabulary

- [ ] 2.1 `relation_type_note` returns `None` for
      `RESOLUTION_RELATION_TYPES`; tests: no note for `supersedes`,
      `revises`, `reconciled_with`; note kept for an unknown type;
      `SUGGESTABLE_RELATION_TYPES` and the seeded set unchanged and
      excluding the three. (`model`)

## Phase 3: The retire path

- [ ] 3.1 Pure helpers beside the provenance rewrite helpers in
      `bundle/provenance.py`: remove one `provenance` entry (and the
      projected `sources` entry) and remove the generated `## Related`
      bullet for a given Source; match only the builder's exact bullet
      shape; leave other text byte-identical; refuse to produce an empty
      `provenance`. Tests include a hand-edited bullet that must not match.
      (`bundle`)
- [ ] 3.2 `prepare_forget` classification: build the historical set only when
      the purge set holds a Source superseded by a Source outside it;
      historical rewrites planned like the resurrection withdrawals;
      blocking references computed as before from the remainder; `purge`'s
      own reference computation untouched. Tests: the E2E shape (v1/v2,
      sole-source concept, shared concept), hand-written link blocks,
      non-Source `supersedes` blocks, sensitivity and `version` untouched,
      no-superseded-Source forget unchanged. (`lint`)
- [ ] 3.3 `forget_core` Phase B writes the rewrites through the existing
      drift-guarded path before deletions; catalog and tombstones unchanged;
      removing the last relation omits `relations:`; a changed target
      refuses everything. (`lint`)
- [ ] 3.4 CLI preview lines for the `~` edits (presentation only); end-to-end
      test: edit watched file, watch imports, `relate`, `forget` without
      `--force`, then `lint` is clean and `answer` cites only the live
      Source. (`cli`)
- [ ] 3.5 `purge` regression tests: a `supersedes` relation to a purge-set
      Source still refuses, and no out-of-set file is rewritten. (`cli`)

## Phase 4: Proof and docs

- [ ] 4.1 Mutation checks on the exact lines, restored by inverse edit and
      `__pycache__` purged: the non-empty-provenance guard, the `sources/`
      root filter, the live-Source subset test, the generated-bullet
      matcher, the Source-authored requirement on the `supersedes` referrer,
      and the purge-unchanged guard. Each must turn a named test red.
- [ ] 4.2 `docs/cli.md` (`forget` retire behavior, `relate` no longer warns,
      `list` status); `docs/knowledge-object-model.md` only if the lifecycle
      paragraph's shape changes. ADR with status Proposed and its row in
      `docs/adr/README.md` (next free number at apply time; see design.md
      "ADR gate"). (`docs`)
- [ ] 4.3 Gates, run directly: `uv run ruff check .`, `uv run ruff format
      --check .`, `uv run mypy .`, `uv run pytest --cov`,
      `uv run python evals/run_self_tests.py`,
      `uv run pytest -q tests/unit/test_adr_index.py`. PR carries
      `Refs #1268`, `Refs #1259` and `Refs #1263` on separate lines.
