# Proposal: okf-v02-migration — adopt OKF v0.2 for concept frontmatter and migrate existing bundles

## Intent

Refs #1064. Exploration: Engram `sdd/okf-v02-migration/explore`.

OKF v0.2 supersedes v0.1 with two deliberate breaking changes (v0.2 §13.1):
`timestamp` is superseded by `generated: { by, at }` (§5.2), and the body
`# Citations` list is superseded by the frontmatter `sources` list (§5.1). It
also standardizes lifecycle as `status: draft | stable | deprecated` (§5.4).
OpenKOS still writes v0.1 shapes: `timestamp`, `status: active`, a bare empty
`# Citations` heading on every Source, and `okf_version: "0.1"` in the
bundle-root `index.md`. AGENTS.md makes adopting OKF non-negotiable and
forbids deciding again what OKF has decided, so an engine that emits a
retired field set is out of line with its own first principle. Every bundle
written from now on in v0.1 shape is also one more bundle to migrate.

Why now: #1062 ("preserve incoming frontmatter") lifts `author` /
`last_modified` into the v0.2 `sources[]` shape, so this change MUST land
before #1062.

Success: a fresh `init` + `ingest` produces a v0.2-shaped bundle
(`okf_version: "0.2"`, `generated`, `sources`, `status: stable`, no empty
`# Citations`); `openkos repair` migrates an existing v0.1 bundle in place as
one reviewable, idempotent, git-revertible commit; every reader keeps working
on v0.1, v0.2 and mixed bundles; the conformance fixture, template, docs and
living specs describe the v0.2 shape.

## Scope

### In Scope

1. **Reader compatibility.** One helper in `model/okf.py` resolves a
   concept's generation time from `generated.at`, falling back to legacy
   `timestamp` (v0.2 §13.1 permits the fallback). Status readers treat legacy
   `active`, `stable`, `draft` and an absent `status` as not deprecated; only
   `deprecated` (and the existing `supersedes`-edge rule) deprecates.
   `merge_metadata`'s most-recent rule reads through the helper.
2. **Writers emit v0.2.** `build_source_concept` and `build_concept` write
   `status: stable` and `generated: { by: openkos/<version>, at: <ISO-8601 Z> }`
   instead of `timestamp`/`status: active`; `build_source_concept` no longer
   appends the empty `# Citations` heading; `merge_metadata` carries
   `generated` forward instead of `timestamp`; `OKF_VERSION` becomes `"0.2"`,
   so `init` writes `okf_version: "0.2"`.
3. **`sources[]` as a generated projection.** At the single frontmatter write
   point in `model/okf.py`, every derived concept's `sources` list is
   generated from its `provenance` (entry `resource` = the bundle-relative
   Source path, plus `id` and `title` where known; exact entry shape is a
   design decision). `provenance` remains the internal source of truth. No
   engine code reads `sources` back as a trust, sensitivity, merge or
   provenance input. A parity test asserts `sources` equals the projection of
   `provenance` on every written concept, and a guard test asserts no module
   outside the projection reads the `sources` key.
4. **`openkos repair` migrates v0.1 bundles in place.** Extends the existing
   verb (today it migrates merge ledgers, ADR-0013) with an OKF migration:
   `timestamp` → `generated: { by: openkos/legacy, at: <old timestamp,
   unchanged> }`; `status: active` → `status: stable`; `sources` generated
   from `provenance`; the bare empty `# Citations` heading removed; bundle-root
   `okf_version` flipped to `"0.2"` in the **same** commit as the concept
   rewrites. Reuses repair's workspace lock, torn/pending-write refusal,
   "nothing to migrate" refusal, git reset-point note, and single
   `openkos: repair (...)` commit. Idempotent: a second run reports nothing
   to migrate and writes nothing.
5. **Fixture, template, docs, specs.** Regenerate
   `examples/good-life-demo/` in v0.2 shape; update
   `src/openkos/templates/agents.md.template`, the example's `AGENTS.md`,
   `docs/okf-alignment.md`, `docs/knowledge-object-model.md` and the other
   docs that restate the field set; fix the pre-existing drift where docs
   claim the engine emits numbered `[N]` citations under `# Citations` (no
   code path does); renumber OKF section references that moved between v0.1
   and v0.2 (conformance §9 → §11, versioning §11 → §12, reserved `index.md`
   and `log.md` sections renumbered) in docs, code comments, user-visible
   messages and the repository `AGENTS.md`.
6. **ADR-0029** (written in design): adopt OKF v0.2 frontmatter; `provenance`
   internal and `sources` a one-way projection; explicit migration via
   `repair`; `openkos/legacy` as the backfill actor; per-claim `[^id]`
   footnotes deferred indefinitely, with `## Related` as the attribution
   surface.

### Out of Scope

- `verified`, trust tiers, `stale_after`, `usage_count`/`usage_window`, and
  Attested Computation (owner decision 4).
- Per-claim `[^id]` footnotes in bodies; `## Related` stays unchanged.
- Lazy (on-read or on-next-write) migration of existing bundles.
- Emitting `status: draft` from any writer.
- Lifting incoming-frontmatter `author` / `last_modified` into `sources[]`
  (that is #1062, which follows this change).
- Rewriting historical ADRs (0002, 0004, 0023 and others) that cite v0.1
  sections; ADRs are append-only.
- A `doctor`/`status` advisory that points unmigrated bundles at `repair`
  (candidate follow-up; reader compatibility makes an unmigrated bundle safe).
- Full OKF import/export (MVP 3 roadmap item).

## Decisions

| # | Decision | Chosen | Rejected | Why |
| --- | --- | --- | --- | --- |
| 1 | Lifecycle vocabulary (owner) | Write `stable`; `deprecated` keeps its meaning; readers accept legacy `active` as `stable` | Keep `active` as an OpenKOS extension value | v0.2 §5.4 fixes the vocabulary; a private synonym re-decides what OKF decided. The only frontmatter `status` reader (`lifecycle.py`) keys on `deprecated` alone, so the rename is read-safe by construction. |
| 2 | Provenance model (owner) | `provenance` stays the source of truth; `sources[]` generated from it at the single write point in `model/okf.py`, with a parity test; `sources` never read back | Make `sources[]` canonical and retire `provenance`; keep both independently writable | Sensitivity high-water-mark, merge, lint, graph and MCP disclosure all fail closed on `provenance` today. Moving that trust input to a field any external OKF tool may rewrite would widen the fail-closed surface. Two writable copies drift. A one-way projection gives external consumers v0.2 provenance without changing any trust path. |
| 3 | Existing bundles (owner) | Explicit in-place migration by `openkos repair`, one reviewable commit, idempotent | Lazy migration on next write; a new `migrate` verb | Human curates, engine maintains: a format rewrite of every concept is consequential and must be reviewable. Lazy migration produces long-lived mixed bundles and hides rewrites inside unrelated commits. `repair` already owns explicit, git-revertible bundle migrations. |
| 4 | Scope (owner) | `# Citations` → `sources`, `timestamp` → `generated: { by, at }`, `status`; `generated.by = openkos/<version>`; `verified`, `stale_after`, Attested Computation deferred | Adopt all of v0.2 at once | The two breaking renames plus lifecycle are the minimum for honest v0.2 conformance; the rest is additive and has no consumer yet. |
| a | `generated` for migrated concepts | `by: openkos/legacy`, `at` = the old `timestamp` unchanged | `by: openkos/<current version>` with `at` = migration time; `process:migrated` | v0.1 recorded no producer, so no real actor exists. Claiming the current version, or a fresh time, would assert a generation event that never happened; `at` records the content's last change, and relabelling is not a content change. `openkos/legacy` follows the §7 `<producer>/<version>` convention and stays distinguishable. |
| b | When `okf_version` flips | Same `repair` commit as the concept rewrites | A separate later step | A bundle must never claim v0.2 while its concepts are v0.1, and repair's design is one whole-bundle commit. |
| c | Numbered-citation doc/code drift | Fix inside this change (fixture/docs slice) | File separately | The heading being described is retired here; leaving prose that documents it as generator output would misdescribe the new shape. |
| d | Per-claim footnotes | Deferred indefinitely, stated in ADR-0029; `## Related` unchanged | Schedule footnotes as next work | No product need exists yet; recording the deferral prevents it being reopened as an oversight. |
| e | ADR | ADR-0029, written in design | No ADR | A format-version adoption and a source-of-truth direction are hard to reverse and project-wide. |

## Capabilities

### New Capabilities

- `okf-format-migration`: the `repair` OKF v0.1 → v0.2 migration (field
  rewrites, `openkos/legacy` backfill, empty-`# Citations` removal,
  `okf_version` flip in the same commit, idempotency, refusals, single
  commit), and the reader-compatibility guarantees (legacy `timestamp`
  fallback, legacy `active` read as `stable`, mixed bundles readable). Spec
  phase MAY instead place the migration under `entity-resolution-merge`,
  which owns the repair verb today; it decides and states why.

### Modified Capabilities

Verified by grep against `openspec/specs/` for this proposal:

- `ingestion`: the Source/derived frontmatter field set (`timestamp` →
  `generated`, `status` value, `sources`), the Source body no longer ending
  in `# Citations`, the conformance requirement's "OKF v0.1 schema" / §9
  wording (now §11), the root-index `okf_version: "0.1"` scenario, and the
  `event_date` requirement that contrasts itself with `timestamp`.
- `workspace-init`: `okf_version` parsed value `"0.1"` → `"0.2"`.
- `entity-resolution-merge`: `merge_metadata`'s most-recent rule over
  `generated.at` (legacy `timestamp` fallback); the `# Citations` "OKF §8
  reserved heading" wording in body reconciliation (§8 is now `index.md`;
  the heading is legacy but still preserved when hand-authored).
- `status-aware-retrieval`: scenario wording where a concept's own status is
  `"active"`; `stable`/`draft`/legacy `active` are all non-deprecating.
- `source-title-backfill`: preservation clauses that name `# Citations` and
  "all other frontmatter keys" (must preserve `generated`/`sources`, and not
  expect a `# Citations` section).
- `query-answer`: the history-label requirement that names "the concept's
  ingest timestamp" (now `generated.at`, with legacy fallback).
- `query-command`: **no v0.1-shape wording found**; its `Citations:` block
  is the answer footer, a different feature. Listed by the exploration;
  spec phase confirms and writes no delta unless decision B below changes
  the rendered status vocabulary.

Conditional on the open decision B below: `list` STATUS column and MCP
concept payload specs (spec phase locates their owning capabilities).

## Approach

- **One seam.** All format knowledge stays in `model/okf.py` (AGENTS.md):
  the generation-time helper, the status normalization, the
  `provenance` → `sources` projection, and the v0.1 → v0.2 document
  rewrite used by `repair` all live there. `repair` orchestration moves to or
  stays thin over an `application/` service (ADR-0018); the CLI keeps
  parsing, presentation and exit codes.
- **Reader-first ordering.** Readers accept both shapes before any writer
  changes, so every intermediate state of the chain (and every mixed bundle
  a user holds) is readable.
- **Projection, not a second truth.** `sources` is recomputed at every write
  from `provenance`; nothing reads it back. Merge unions `provenance`, then
  the write point regenerates `sources`.
- **Migration is a pure function per document** (bytes in, bytes out, or
  "already v0.2"), tested exhaustively, then applied by `repair` under its
  existing guards. A non-empty, hand-authored `# Citations` section is never
  silently dropped (see open decision C).
- Core stays synchronous; no new dependencies; `raw/` untouched.

### Slice plan (auto-chain, stacked to main, dependency order)

Forecasts are the exploration's estimates scaled by this repo's observed
~1.95x forecast-to-actual ratio (authored lines, additions + deletions,
delta specs excluded).

| # | Slice | Contents | Explore estimate | Scaled forecast |
| --- | --- | --- | --- | --- |
| 1 | Reader compatibility | generation-time helper with `timestamp` fallback, status normalization, `merge_metadata` reads via helper, tests on v0.1 / v0.2 / mixed inputs; no writer change | 100-150 | ~195-290 |
| 2a | Writers: lifecycle + generated | `status: stable`, `generated: { by, at }`, `merge_metadata` carries `generated`, `OKF_VERSION = "0.2"`, golden and unit test updates | 100-150 | ~195-290 |
| 2b | Writers: sources projection | `sources` from `provenance` at the write point, drop empty `# Citations`, parity test, no-read-back guard test | 100-150 | ~195-290 |
| 3 | `repair` migration | per-document migration function, `repair` extension, same-commit `okf_version` flip, idempotency and refusal tests, ledger interaction test | 150-250 | ~290-490 |
| 4a | Fixture + template | regenerate `examples/good-life-demo/`, `agents.md.template`, example `AGENTS.md`, fixture-pinned tests | 75-150 | ~145-290 |
| 4b | Docs + section renumbering | `docs/okf-alignment.md`, `docs/knowledge-object-model.md`, other docs, repository `AGENTS.md`, code comments and messages citing moved sections, numbered-citation drift fix | 75-150 | ~145-290 |

Total ~1,170-1,950 authored lines. The exploration's slice 2 and slice 4 are
split here because their scaled forecasts exceeded the 400-line budget;
slice 3 may still exceed it at the high end, in which case tasks splits the
migration function from the `repair` wiring. Delta specs land with the
slice whose behavior they describe (tasks phase decides). ADR-0029 lands in
slice 1 or with the design commit.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/openkos/model/okf.py` | Modified | `OKF_VERSION`, writers, `merge_metadata`, generation-time helper, status normalization, `sources` projection, migration function, section-reference comments |
| `src/openkos/lifecycle.py`, `src/openkos/bundle/listing.py`, `src/openkos/application/concept_read.py` | Modified | status reads accept v0.2 vocabulary; display vocabulary per decision B |
| `src/openkos/mcp/gate.py` | Modified (conditional) | concept `status` payload value, per decision B |
| `src/openkos/bundle/index.py` | Unchanged code | writes `okf_version` from `OKF_VERSION` |
| `src/openkos/cli/main.py` (`repair`), `src/openkos/application/` | Modified | OKF migration in `repair`, thin CLI |
| `src/openkos/templates/agents.md.template` | Modified | v0.2 field set, no `# Citations` convention |
| `examples/good-life-demo/` | Modified | v0.2-shaped fixture |
| `docs/*.md`, `AGENTS.md` | Modified | v0.2 shape, section renumbering, drift fix |
| `docs/adr/0029-*.md`, `docs/adr/README.md` | New/Modified | ADR, status Proposed |
| `openspec/specs/*` (via deltas) | Modified | the capabilities listed above |
| `tests/unit/...` | New/Modified | compat, writers, parity, guard, migration, goldens |

## Principles Impact

- **Adopt OKF:** the point of the change; OpenKOS extension keys
  (`provenance`, `freshness`, `sensitivity`, `version`, `relations`) stay
  legal under v0.2 as unknown frontmatter keys.
- **Reconstructible:** derived stores rebuild from the migrated files;
  `sources` is itself derived from `provenance`.
- **Provenance and sensitivity:** fail-closed machinery keeps reading
  `provenance` only; the guard test enforces it.
- **Human curates:** migration is explicit, one commit, reviewable, revertible.
- **Immutable `raw/` / local-first:** untouched.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Merge ledger byte-parity (ADR-0002, ADR-0013): ledger snapshots of absorbed documents are v0.1 bytes, so `unmerge` after migration restores v0.1 shape into a v0.2 bundle | High | Design decides between migrating ledger snapshots inside the same `repair` commit or normalizing restored documents through the migration function; slice 3 carries an explicit merge → repair → unmerge test either way. |
| A future reader trusts `sources` over `provenance` (sensitivity, merge, lint, graph, MCP disclosure) | Med | One-way projection, parity test, and a guard test that fails if any module outside the projection reads the `sources` key. |
| MCP read surface exposes status/time vocabulary to external agents | Med | `mcp/gate.py` echoes a derived `status` (`active`/`deprecated`) and no `timestamp`; its vocabulary follows decision B, with a disclosure test. |
| Lint regressions (lint reads `provenance`, flags dangling links) | Low | Lint does not parse `# Citations` or `timestamp`; run lint and `status` on the migrated fixture in tests. |
| Mixed bundles (partial migration, hand-edited files, #1062 imports) | Med | Reader compatibility ships first; `repair` is idempotent and whole-bundle; `okf_version` flips only in the same commit. |
| `repair` destroys hand-authored `# Citations` content | Med | Only the engine's bare empty heading is removed automatically; non-empty sections follow decision C. |
| Goldens and full-stream snapshots pinned to v0.1 bytes | High | Updated in the writer slices; goldens pin their environment (existing practice). |
| `generated.at` semantics differ from `timestamp` ("last meaningful change" vs ingest time) on re-ingest and merge | Low | Design states the rule per write path; migration never changes the value. |
| Section renumbering misses a user-visible message | Low | Grep-driven checklist in slice 4b over `src/`, `docs/`, templates and `AGENTS.md`. |
| Sequencing with #1062 | Med | This change merges first; #1062 is rebased onto the v0.2 `sources[]` shape. |

## Rollback Plan

Revert the slices in reverse order; each is independently green. Before slice
3 merges, no user bundle is rewritten: reverting slices 2a/2b returns writers
to v0.1 while slice 1's readers still accept v0.2 documents already written.
After a user runs the migration, the rollback for that bundle is
`git revert` of the single `openkos: repair (...)` commit (repair prints the
reset point). Code rollback past slice 1 is unsafe once v0.2 bundles exist in
the wild, because pre-slice-1 readers ignore `generated`; therefore slice 1 is
reverted last, and only if no v0.2 bundle has been written.

## Dependencies

- None blocking. `openkos repair` (ADR-0013) and the merge ledger
  (ADR-0002) are prerequisites already shipped.
- #1062 depends on this change and must follow it.

## Success Criteria

- [ ] `init` + `ingest` on a fresh workspace yields `okf_version: "0.2"`,
      `generated: { by: openkos/<version>, at }`, `status: stable`,
      `sources` matching `provenance`, and no empty `# Citations` heading;
      `check_conformance` reports no violations.
- [ ] Every reader returns the same results on a v0.1 bundle, its migrated
      v0.2 twin, and a mixed bundle (retrieval, `list`, lint, graph, MCP).
- [ ] `openkos repair` on a v0.1 bundle produces exactly one commit that
      rewrites every concept and flips `okf_version`; a second run reports
      nothing to migrate and writes nothing; `generated.at` equals the old
      `timestamp` byte-for-byte and `generated.by` is `openkos/legacy`.
- [ ] merge → repair → unmerge leaves a bundle with no v0.1-shaped document.
- [ ] The parity test and the `sources` no-read-back guard test exist and
      fail under mutation.
- [ ] `examples/good-life-demo/` is v0.2-shaped and passes the product's own
      lint and `status`; no doc claims numbered `[N]` citations are generator
      output.
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
      `uv run pytest --cov` (90% branch gate) and the eval self-test sweep
      are green.
- [ ] ADR-0029 exists, status `Proposed`, indexed.

## Open decisions for the orchestrator

These are not settled by the owner or orchestrator decisions above. Work
that does not depend on them (slices 1, 2a core, 3 core) can proceed;
the named dependent parts should wait.

- **A. Does the engine start writing `status: deprecated` when a concept is
  superseded?** Today nothing writes `deprecated`; deprecation is derived
  from `supersedes` edges at read time. The owner decision text reads
  "`deprecated` for superseded", while the exploration records it as
  "`deprecated` unchanged". Recommended: no new write (keep deprecation
  edge-derived; `deprecated` is written only by a human), because
  materializing it makes frontmatter able to disagree with the edges and
  adds a write that `unreconcile`/`unmerge` must undo. Stake: with the
  recommendation, an external v0.2 tool reading only frontmatter sees a
  superseded concept as `stable`. Depends: slice 2a scope and the
  `status-aware-retrieval` delta.
- **B. Does the derived display vocabulary change from `active` to
  `stable`?** `list`'s STATUS column, `concept_read.ConceptRecord.status`
  and the MCP concept payload report a computed `active | deprecated`.
  Recommended: switch to `stable` for consistency with the frontmatter the
  user now sees. Stake: `list` output and the MCP payload change a value
  scripts or agents may match on. Depends: slice 1 display code, MCP and
  `list` spec deltas.
- **C. What does `repair` do with a non-empty, hand-authored `# Citations`
  section?** Recommended: leave it in place and report it (v0.2 §13.1 lets
  consumers still parse a legacy list), converting nothing, because
  resolving free-text entries into `sources` would guess. Stake: such
  concepts keep a legacy body list after migration. Depends: slice 3's
  body rewrite and the fixture's `stoicism.md`, which carries one today.

Note: this proposal relied on the exploration's record of the owner decision
in #1064; the issue comment itself was not re-read in this phase.
