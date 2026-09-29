# Tasks: okf-v02-migration — adopt OKF v0.2 for concept frontmatter and migrate existing bundles

Refs #1064. Design: `design.md`. Proposal: `proposal.md`. ADR-0029
(`docs/adr/0029-adopt-okf-v02-frontmatter.md`) is already written on disk,
status `Proposed`, with its `docs/adr/README.md` index row already added
(both currently untracked/modified in the worktree) — it ships staged into
Phase 1's commit, the first code PR of the chain, per design.md "Decision
10". No task below edits either file; Phase 1 only stages and verifies them.

Strict TDD is ON, runner `uv run pytest`. Every behavioral task pairs a
`[TEST]` task — observed RED with the reason it is RED today — with the
`[IMPL]` task that turns it GREEN, in that order. `[IMPL]` tasks introduce
only what their paired `[TEST]` already pins. Revert every mutation with the
inverse edit (never `git checkout --`), and purge `__pycache__` before
trusting a verdict. Every fixture built from a real `merge`/`ingest` call
runs in `tmp_path`; no test reaches Ollama (LLM paths are untouched by this
change).

**Threat Matrix**: N/A, per design.md — this change adds no routing, shell,
subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. `repair` reuses the existing `_autocommit`
unchanged; the only difference is a longer bundle-relative path list built
from the bundle walk, passed as argv (no shell), exactly as every other verb
already does.

**Slice/file placement note (tasks-phase decision, design.md "tasks phase
decides"):** design.md's own slice plan tentatively grouped
`src/openkos/templates/agents.md.template` and
`examples/good-life-demo/AGENTS.md` with the fixture-regeneration slice
(4a). This task list splits them out into the docs slice (Phase 8 / "4b")
instead, because both are hand-written PROSE describing the target field
shape — they need no `repair` run to write correctly, unlike the actual
`examples/good-life-demo/bundle/**` concept files, which genuinely must be
produced by running `repair` (Phase 7 / "4a"). This lets Phase 8 depend only
on Phase 3 (sources projection) and run in parallel with Phases 4-6, exactly
as the orchestrator's slice plan states ("4b parallel off 2b").

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,770-2,820 total (design.md "Migration / Rollout"), across 8 chained PRs |
| 400-line budget risk | High overall; each individual slice is scoped to land under or near 400 |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (Phase 1: readers+display+ADR) → PR 2 (Phase 2: writers generated+status) → PR 3 (Phase 3: writers sources) → PR 4 (Phase 4: migration function) → PR 5 (Phase 5: ledger migration) → PR 6 (Phase 6: repair verb) → PR 7 (Phase 7: fixture regeneration, after PR 6) → PR 8 (Phase 8: docs+template+renumbering, after PR 3, may run parallel with PRs 4-6) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

`auto-chain` means the orchestrator proceeds with this slice order and
`stacked-to-main` with no further decision gate; the owner has pre-approved
`size:exception` for any slice that genuinely cannot be split further (most
likely Phase 6, the `repair` verb, given its orchestration + safety-gate +
crash-order + round-trip test surface).

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|-----------------------|------------------|--------------------|
| 1 (Phase 1) | Dual reader (`generation_time`, `declares_deprecated`), `stable`/`deprecated` display, ADR-0029 staged | PR 1 → `main` | `uv run pytest tests/unit/model/test_okf_v02_readers.py tests/unit/test_lifecycle.py tests/unit/bundle/test_listing.py tests/unit/application/test_concept_read.py tests/unit/mcp/test_gate.py` | `uv run openkos status` and `uv run openkos list` against a scratch v0.1-shaped bundle in `tmp_path` — both must report `stable` | Revert the two accessor functions, the `lifecycle.py`/`listing.py`/`concept_read.py`/`mcp/gate.py` call-site changes, and their tests; no writer has changed yet, so no bundle byte changes to revert |
| 2 (Phase 2) | `Generated`, `engine_actor()`, `LEGACY_ACTOR`; builders + call sites emit `generated`/`status: stable`; `OKF_VERSION = "0.2"`; goldens | PR 2 → `main`, after PR 1 | `uv run pytest tests/unit/model/test_okf.py tests/unit/application/test_ingest.py tests/unit/application/test_query.py` | `uv run openkos init && uv run openkos ingest <fixture>` in `tmp_path` — the written Source carries `generated`/`status: stable`, no `timestamp` | Revert `Generated`/`engine_actor`/`LEGACY_ACTOR`, the builder signature changes, the three/four CLI call sites, `OKF_VERSION`, and the golden JSON; Phase 1 readers keep reading the reverted v0.1 output correctly |
| 3 (Phase 3) | `project_sources`/`refresh_sources`; builders + merge introduce `sources`; `apply_provenance_rewrites` maintains it; Source body drops `# Citations`; parity test; both AST guards | PR 3 → `main`, after PR 2 | `uv run pytest tests/unit/model/test_okf_sources_projection.py tests/unit/test_sources_key_guard.py` | `uv run openkos ingest <fixture> && uv run openkos check_conformance` (via existing conformance test) in `tmp_path` — `sources` present and matches `provenance` | Revert `project_sources`/`refresh_sources`, the builder/merge/provenance-rewrite call sites, and the two guard test files; Phase 2's `generated`/`status` output is untouched |
| 4 (Phase 4) | `migrate_document` pure function: generated/status/sources/citations rules, idempotency, builder-equivalence, commutation properties | PR 4 → `main`, after PR 3 | `uv run pytest tests/unit/model/test_okf_migrate_document.py` | N/A — a pure library with no caller until Phase 6 wires `repair`; nothing runs end-to-end yet | Revert `migrate_document`/`MigrationResult`/`MigrationChanges` and their tests; no other module imports it yet |
| 5 (Phase 5) | `migrate_sidecars_to_okf_v02`: snapshot/recursive/`index_before`/offset-shift ledger migration, Check B preserved | PR 5 → `main`, after PR 4 | `uv run pytest tests/unit/bundle/test_ledger_okf_migration.py` | N/A — a library with no caller until Phase 6 wires `repair`'s apply phase | Revert `migrate_sidecars_to_okf_v02` and its snapshot-index helper; no ledger sidecar has been rewritten by anything yet |
| 6 (Phase 6) | `application/repair.py` (`plan_repair`/`apply_repair`); CLI wiring, report, help, commit text; scoped Gate 2; idempotency, crash-order, and merge→repair→unmerge across all five ledger schemas | PR 6 → `main`, after PR 5 | `uv run pytest tests/unit/cli/test_repair.py` | `openkos repair` on a real v0.1 `tmp_path` workspace built by real `init`/`ingest`/`merge` calls — asserted single commit, idempotent second run | This is the first user-bundle-mutating slice; revert is `git revert` of the touched commit(s) plus reverting `application/repair.py`/CLI wiring; no bundle has actually been migrated in production yet at this point in the chain |
| 7 (Phase 7) | Regenerate `examples/good-life-demo/bundle/**` by running `repair` on a scratch copy of the frozen v0.1 fixture; keep the frozen copy as a test input; example passes `lint`/`status` clean | PR 7 → `main`, after PR 6 | `uv run pytest tests/unit/test_canonical_example.py` | `uv run openkos lint && uv run openkos status` inside `examples/good-life-demo` — both must exit 0 with no v0.1-shape findings | Revert `examples/good-life-demo/bundle/**` to its pre-PR-7 committed state via `git revert`; the frozen v0.1 fixture stays as evidence either way |
| 8 (Phase 8) | Docs, repository `AGENTS.md`, `templates/agents.md.template`, example `AGENTS.md`, ~37 OKF section citations in `src/`, numbered-citation drift fix, two living-spec archive-time notes | PR 8 → `main`, after PR 3 (parallel-eligible with PRs 4-6) | `uv run pytest tests/unit/test_canonical_example.py -k agents_md` plus `grep -rn 'OKF §9\|OKF §6\|OKF §7' docs/ src/ AGENTS.md` returning zero matches outside historical ADRs | N/A — prose-only change, no executable behavior; structural readback is the proportional check | Revert the docs/template/comment edits file-by-file; no code behavior depends on this slice |

## Scenario / Requirement → Task Coverage

Every requirement and scenario in the nine delta specs, mapped to at least
one task. No requirement is left uncovered.

| Spec | Requirement | Scenario(s) | Coverage |
|---|---|---|---|
| okf-format-migration | Generation-Time Resolution With Legacy `timestamp` Fallback | v0.2 resolves via `generated.at`; legacy resolves via `timestamp`; `generated.at` precedence | 1.1-1.4 |
| okf-format-migration | Legacy Lifecycle Values Are Not Deprecated | legacy `active` not deprecated; `stable` not deprecated; absent status not deprecated | 1.5-1.7 |
| okf-format-migration | Mixed-Bundle Read Parity | mixed bundle answers identically to its fully-migrated twin | 6.16 (repair e2e), 7.5 (fixture-level) |
| okf-format-migration | `repair` Migrates An OKF v0.1 Bundle To v0.2 In One Commit | v0.1 concept rewritten; `okf_version` flips same commit | 6.1-6.13 |
| okf-format-migration | `repair` OKF Migration Is Idempotent | re-run is a no-op | 6.14-6.15 |
| okf-format-migration | `repair` Preserves Hand-Authored `# Citations` Content | non-empty section survives + reported; bare empty heading removed | 4.11-4.14, 6.9 (report line) |
| okf-format-migration | `repair` OKF Migration Refuses Consistently With Existing Guards | workspace-locked refuses; nothing-to-migrate refuses cleanly | 6.3, 6.14 |
| okf-format-migration | Merge, Repair, And Unmerge Leave No V0.1-Shaped Document | restored document not v0.1-shaped after repair | 5.9-5.14 (round-trip across V1-V5) |
| ingestion | `sources` Is A Generated, One-Way Projection Of `provenance` | matches projection; no outside read | 3.1-3.6, 3.7-3.10 (guards) |
| ingestion | Ingest Raw Copy and Source Concept Generation (v0.2 fields) | verbatim embed, no trailing Citations, conformance clean | 2.9-2.10, 3.5-3.6 |
| ingestion | OKF §11 Conformance — Reserved File Structure (Rule 3) | rule 1-2 unperturbed | Pre-existing code path; regression-pinned by 7.4 (fixture conformance) — no behavior change, title renumber only, covered in 8.x |
| ingestion | index.md Frontmatter Conformance (§8 + §12 Root Exception) | root passes; non-root frontmatter is a violation | Pre-existing code path, unaffected by field-set changes; title/number renumber only — 8.x |
| ingestion | Reference Bundle Full §11 Conformance | reference bundle passes all three rules | 7.4 |
| ingestion | Source Event Date Frontmatter Key | omit when no evidence; never defaults to ingest time; derived objects never carry it | Pre-existing behavior, unaffected by this change except the contrast field renaming to `generated.at` — regression-pinned by 2.9 |
| entity-resolution-merge | Merge Metadata's Generation-Time Rule Reads `generated.at` With Legacy Fallback | v0.2 survivor + legacy absorbed; legacy survivor + v0.2 absorbed | 2.11-2.14 |
| entity-resolution-merge | The Stacked Form Keeps One Document Root | heading demoted; deeper sections verbatim incl. `# Citations`; heading-less unaffected; demoted stack still unmerges | Pre-existing behavior (#803), unaffected by field renaming — regression-pinned by 5.9-5.14's round-trip fixtures, which exercise stacked merges |
| entity-resolution-merge | Repair Verb Refuses On Any Sign Of Cross-Survivor Pollution Risk (scoped Gate 2) | clean ledger migrates; refuses on pollution risk; sidecar-only ≥2-entries proceeds to OKF migration | 6.4-6.8 |
| status-aware-retrieval | Effective Status Resolution | status alone; superseded regardless of own status; self-ref live / cycles fail-safe; `revises` deprecates neither end | 1.8-1.10 (dual-reader shape), pre-existing cycle/self-ref logic regression-pinned by 1.10's parametrized table |
| source-title-backfill | Exactly Two Byte-Level Edits Per Staged Source | only title+first line change incl. `generated`/`sources` preserved; unrewritable title skipped | 3.11-3.12 |
| query-answer | Revision History Block Labels Name The Relation, Edge Holder, And Event Date | depth-1 holder; depth-2 holder; refined-not-deprecated still current; refined-and-deprecated no longer current; unresolved unknown never ingest time; confidential source unknown; multiple dates range; successor label unaffected | 1.4 (helper reused), regression-pinned by existing `query-answer` test suite run in every slice's full-suite gate — no new behavior, only the `timestamp`→`generated.at` substitution source changes, covered by 1.1-1.4 |
| workspace-init | Bundle Index Shape | exact parsed frontmatter incl. `okf_version: "0.2"`, empty body | 2.15-2.16 |
| workspace-init | OKF Conformance | mechanical check passes vacuously; rule 3 holds by construction; unreadable file reported as I/O error not violation | 2.15-2.16 (version bump only; I/O-error branch pre-existing, unaffected) |
| next-action-pointer | No-Runnable-Action Output Never Claims Cleanliness | no tier fires on empty bundle; no tier fires despite commandless findings | Pre-existing behavior, unaffected — title-only renumber, covered by 8.x |
| status | Needs-Attention via §11 Conformance | no issues; violation surfaced non-fatal | Pre-existing behavior, unaffected — title-only renumber, covered by 8.x |
| status | Needs-Attention Surfaces Dangling References | dangling surfaced; purge-created dangling detected; no dangling no new entries | Pre-existing behavior, unaffected — title-only renumber, covered by 8.x |

---

## Phase 1 (PR 1 → `main`): Readers + display + ADR-0029

Design.md "Technical Approach" layer 1 and Decisions 6-7. Creates the two
accessor functions every later reader/writer depends on; changes no writer
and rewrites no bundle byte.

### `model/okf.py` — `generation_time` / `_parse_instant` (Decision 6)

- [x] **1.1** [TEST] `tests/unit/model/test_okf_v02_readers.py` (new file) —
  add `test_generation_time_resolves_generated_at_when_present`: a mapping
  `{"generated": {"at": "<T>"}}` (no `timestamp`) resolves to `<T>`. Covers
  okf-format-migration scenario "A v0.2 concept resolves via `generated.at`".
  **RED today**: `AttributeError` — `okf.generation_time` does not exist.
- [x] **1.2** [TEST] Same file — add
  `test_generation_time_falls_back_to_legacy_timestamp_when_generated_absent`:
  a mapping with only `timestamp: "<T>"` resolves to `<T>`; a mapping with
  neither key resolves to `None`. Covers scenario "A legacy concept resolves
  via `timestamp`". **RED today**: same `AttributeError`.
- [x] **1.3** [TEST] Same file — add
  `test_generation_time_generated_present_never_falls_back`: a mapping
  carrying BOTH `generated: {at: "<T1>"}` and `timestamp: "<T2>"` (`T1 !=
  T2`) resolves to `<T1>`; a mapping where `generated` is present but
  malformed (`generated` is not a mapping, or `at` is missing/unparseable)
  resolves to `None` and does NOT fall back to the sibling `timestamp`.
  Covers scenario "`generated.at` takes precedence when both are present".
  **RED today**: same `AttributeError`. Kills a fallback that fires whenever
  `generated.at` fails to parse instead of only when `generated` is absent.
- [x] **1.4** [TEST] Same file — add
  `test_parse_instant_accepts_datetime_str_and_rejects_the_rest`,
  parametrized: an unquoted YAML-resolved `datetime` object passes through
  unchanged; an ISO-8601 string parses via `fromisoformat`; a bare `date`
  object, a non-string/non-datetime, and an unparseable string all yield
  `None`. Covers the widening design.md Decision 6 calls out (today's
  `_parse_timestamp` rejects a `datetime`). **RED today**: `AttributeError`
  — `_parse_instant` does not exist (private, imported for this pin only).
  Kills a `datetime` value rejected identically to today's `_parse_timestamp`.
- [x] **1.5** [IMPL] `src/openkos/model/okf.py`: add `_parse_instant(value:
  object) -> datetime | None` (accepts `datetime` as-is; `str` via
  `datetime.fromisoformat`, else `None`; anything else `None`) and
  `generation_time(metadata: Mapping[str, object]) -> datetime | None` per
  design.md Decision 6's exact branching (`generated` key present ⇒
  authoritative, no fallback, even on parse failure; absent ⇒ read
  `timestamp` through `_parse_instant`). Makes 1.1-1.4 GREEN.

### `model/okf.py` — `declares_deprecated` (Decision 6)

- [x] **1.6** [TEST] Same file — add
  `test_declares_deprecated_status_value_table`, parametrized over
  `status`: `"deprecated"` -> `True`; `"active"`, `"stable"`, `"draft"`,
  absent key, and an arbitrary unknown string -> `False`. Covers
  okf-format-migration scenarios "A legacy `active` concept is not
  deprecated", "A `stable` concept is not deprecated", "An absent status is
  not deprecated". **RED today**: `AttributeError` —
  `okf.declares_deprecated` does not exist. Kills any value other than the
  exact literal `"deprecated"` marking a concept deprecated.
- [x] **1.7** [IMPL] Same module: add `declares_deprecated(metadata:
  Mapping[str, object]) -> bool` = `metadata.get("status") ==
  "deprecated"`. Makes 1.6 GREEN.

### `lifecycle.py` / `bundle/listing.py` read through the two accessors

- [x] **1.8** [TEST] `tests/unit/test_lifecycle.py` — extend the existing
  `deprecated_concept_ids` test(s) with a case: a concept with `status:
  active` (legacy) and no inbound `supersedes` edge is NOT in the returned
  set, alongside the existing `deprecated`/`supersedes`-cycle cases (regression
  pin for status-aware-retrieval's "self-reference stays live, but
  supersedes cycles fail safe" and "A revises edge deprecates neither end").
  **RED today**: passes already for the direct string comparison, but add an
  explicit assertion that `lifecycle.py` calls `okf.declares_deprecated`
  (via a `unittest.mock.patch` spy) so a future inline reimplementation of
  the comparison is caught — **RED today**: `AssertionError`, the spy is
  never called because `lifecycle.py` still compares `status` inline.
- [x] **1.9** [IMPL] `src/openkos/lifecycle.py`: replace the inline
  `metadata.get("status") == "deprecated"` comparison in
  `deprecated_concept_ids` with a call to `okf.declares_deprecated(metadata)`.
  Makes 1.8 GREEN; behavior is unchanged by construction (identical
  comparison, now centralized).
- [x] **1.10** [TEST] `tests/unit/bundle/test_listing.py` — add
  `test_bundle_object_status_reads_through_declares_deprecated`: same spy
  pattern as 1.8 against `bundle/listing.py`'s computed-status path.
  **RED today**: spy never called.
- [x] **1.11** [IMPL] `src/openkos/bundle/listing.py`: route the deprecated
  check through `okf.declares_deprecated`. Makes 1.10 GREEN.

### Display vocabulary `active` → `stable` (Decision 7 / owner decision B)

- [x] **1.12** [TEST] `tests/unit/bundle/test_listing.py` — add
  `test_bundle_object_status_reports_stable_not_active`: a live (non-
  deprecated) concept's `BundleObject.status` reads `"stable"`, never
  `"active"`. Covers status-aware-retrieval scenario "A revises edge
  deprecates neither end, in retrieval or in list STATUS" (the `stable`
  half). **RED today**: `AssertionError` — currently reports `"active"`.
- [x] **1.13** [IMPL] `src/openkos/bundle/listing.py`: change the computed
  live-status literal from `"active"` to `"stable"`. Makes 1.12 GREEN.
- [x] **1.14** [TEST] `tests/unit/application/test_concept_read.py` — add
  `test_concept_record_status_is_stable_not_active`, same assertion against
  `ConceptRecord.status`. **RED today**: `AssertionError`.
- [x] **1.15** [IMPL] `src/openkos/application/concept_read.py`: change
  `ConceptRecord.status`'s `Literal` type from `Literal["active",
  "deprecated"]` to `Literal["stable", "deprecated"]` and the computed
  literal from `"active"` to `"stable"`. Makes 1.14 GREEN.
- [x] **1.16** [TEST] `tests/unit/mcp/test_gate.py` — add
  `test_mcp_concept_payload_status_is_stable_not_active`: the MCP concept
  payload's `status` field reads `"stable"` for a live concept. **RED
  today**: `AssertionError`.
- [x] **1.17** [IMPL] `src/openkos/mcp/gate.py`: no logic change needed if
  `mcp/gate.py` already reads `ConceptRecord.status` verbatim (per design.md
  "Modified (conditional)"); confirm during implementation whether a literal
  string is duplicated locally and remove the duplication if so, sourcing
  the value from `ConceptRecord.status` exclusively. Makes 1.16 GREEN.
- [x] **1.18** [DOC] `CHANGELOG.md`: add an entry noting the computed
  `active` → `stable` display-vocabulary change in `list`'s STATUS column,
  `concept_read`, and the MCP concept payload, with a one-line rationale
  (frontmatter lifecycle vocabulary per OKF v0.2 §5.4) — user-visible for
  scripts/agents matching on the old value (design.md Decision 7 rationale).

### ADR-0029 staged, index row confirmed

- [x] **1.19** Confirm `docs/adr/0029-adopt-okf-v02-frontmatter.md` (already
  on disk, untracked) states status `Proposed`, records decisions 1-4 and a-e
  from `proposal.md`'s Decisions table, and the ledger-vs-migration choice
  from design.md Decision 1. No edit expected; if any decision drifted from
  the final proposal/design text during earlier phases, correct it now.
- [x] **1.20** Confirm `docs/adr/README.md` (already modified on disk) carries
  the ADR-0029 index row in the correct position. No edit expected.

### Phase 1 verification

- [x] **1.21** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **1.22** Run `uv run pytest tests/unit/model/test_okf_v02_readers.py
  tests/unit/test_lifecycle.py tests/unit/bundle/test_listing.py
  tests/unit/application/test_concept_read.py tests/unit/mcp/test_gate.py`
  focused, then `uv run pytest --cov` (unpiped) full suite — must be green,
  90% branch gate held.
- [x] **1.23** Run `uv run python evals/run_self_tests.py` — must be green
  (no eval harness touches status/generation-time reading paths, but the
  sweep must still pass with `OLLAMA_HOST` poisoned).
- [x] **1.24** Commit as one or more work-unit commits, scope `model` (e.g.
  `feat(model): add the OKF v0.2 dual-reader accessors and stable display
  vocabulary`), staging `docs/adr/0029-adopt-okf-v02-frontmatter.md` and
  `docs/adr/README.md` in the same commit set. Open PR 1 (Phase 1: readers +
  display + ADR) targeting `main`.

  Done as commit `6849b36` (`feat(model): add the OKF v0.2 dual-reader
  accessors and stable display vocabulary (#1064)`) on
  `feat/1064-okf-v02-p1-readers`, stacked on `f360390`, which already
  carries ADR-0029 and its `docs/adr/README.md` index row committed. PR
  opening is a user/repository-policy decision outside apply's scope; the
  branch is ready to open as PR 1 targeting `main`.

**Rollback boundary**: revert `generation_time`/`_parse_instant`/
`declares_deprecated`, the four call-site edits, the display-literal
changes, and the CHANGELOG entry. ADR-0029 may be reverted too or left in
place (it records a decision, not a behavior) — reverting it is optional and
does not affect any later slice, since no later slice's code depends on the
ADR file's presence.

---

## Phase 2 (PR 2 → `main`, after PR 1 merges): Writers — `generated` + `status: stable`

Design.md "Technical Approach" layer 2 (first half) and Decision 5. No
`sources` work yet — that is Phase 3.

### `model/okf.py` — `Generated`, `engine_actor`, `LEGACY_ACTOR`, `OKF_VERSION`

- [x] **2.1** [TEST] `tests/unit/model/test_okf.py` — add
  `test_engine_actor_reads_installed_distribution_version`: monkeypatches
  `importlib.metadata.version("openkos")` to return `"9.9.9"` and asserts
  `okf.engine_actor() == "openkos/9.9.9"`; a second case monkeypatches it to
  raise `importlib.metadata.PackageNotFoundError` and asserts
  `okf.engine_actor() == "openkos/0+unknown"` (mirroring `mcp/server.py`'s
  existing degrade pattern — grep it for the exact fallback string before
  writing this assertion). **RED today**: `AttributeError` —
  `okf.engine_actor` does not exist.
- [x] **2.2** [IMPL] `src/openkos/model/okf.py`: add `@dataclass(frozen=True)
  class Generated: by: str; at: str`, `LEGACY_ACTOR: Final =
  "openkos/legacy"`, and `engine_actor() -> str`, copying `mcp/server.py`'s
  existing version-lookup degrade pattern exactly (no new dependency).
  Makes 2.1 GREEN.
- [x] **2.3** [TEST] Same file — add
  `test_okf_version_is_0_2`: `okf.OKF_VERSION == "0.2"`. **RED today**:
  `AssertionError` — currently `"0.1"`.
- [x] **2.4** [IMPL] Same module: change `OKF_VERSION: Final = "0.1"` to
  `"0.2"`. **Do not** run the full suite yet — this single-line change is
  expected to turn many existing golden-byte assertions RED across the repo;
  those are fixed by 2.16 below. Makes 2.3 GREEN in isolation.

### `model/okf.py` — builders take `generated`, drop `timestamp`

- [x] **2.5** [TEST] `tests/unit/model/test_okf.py` — add
  `test_build_source_concept_emits_generated_and_stable_status`: calling
  `build_source_concept(..., generated=okf.Generated(by="openkos/test",
  at="2026-01-01T00:00:00Z"), ...)` (dropping the current `timestamp=`
  keyword) yields frontmatter with `generated: {by: "openkos/test", at:
  "2026-01-01T00:00:00Z"}`, `status: "stable"`, and no `timestamp` key.
  Covers ingestion's "Ingest Raw Copy and Source Concept Generation" v0.2
  field-set clause. **RED today**: `TypeError` — `build_source_concept` has
  no `generated` parameter and still requires `timestamp`.
- [x] **2.6** [TEST] Same file — add
  `test_build_source_concept_body_has_no_trailing_citations_heading`: the
  built Source's body, for both the verbatim-embed and undecodable-fallback
  cases, ends with exactly one trailing `\n` and no `# Citations` heading.
  Covers ingestion scenario "Successful ingest embeds verbatim text" (the
  no-Citations clause) and design.md's note that the new
  `build_source_concept` body ends `...{section.rstrip("\n")}\n`. **RED
  today**: `AssertionError` — the current builder appends the heading.
- [x] **2.7** [IMPL] `src/openkos/model/okf.py`: change
  `build_source_concept`'s signature to take `generated: Generated` instead
  of `timestamp: str`, write `generated`/`status: "stable"` into the
  frontmatter dict, and remove the empty `# Citations` heading append at the
  end of body construction (`...{section.rstrip("\n")}\n`). Makes 2.5-2.6
  GREEN.
- [x] **2.8** [TEST] Same file — add
  `test_build_concept_emits_generated_and_stable_status`: same shape as 2.5
  for `build_concept`. **RED today**: `TypeError`.
- [x] **2.9** [IMPL] Same module: change `build_concept`'s signature
  identically (`generated: Generated` replaces `timestamp: str`; writes
  `generated`/`status: "stable"`). Makes 2.8 GREEN.

### Call sites: `application/ingest.py`, `application/query.py`, `cli/main.py`

- [x] **2.10** [TEST] `tests/unit/application/test_ingest.py` — extend the
  existing successful-ingest assertions to check the written Source's
  frontmatter carries `generated: {by: <engine_actor()>, at: <the same
  instant previously passed as timestamp>}` and `status: "stable"`, with no
  `timestamp` key. **RED today**: `TypeError` at the `build_source_concept`
  call site (still passes `timestamp=`).
- [x] **2.11** [IMPL] `src/openkos/application/ingest.py`: replace every
  `timestamp=<now>` argument passed to `build_source_concept`/`build_concept`
  with `generated=okf.Generated(by=okf.engine_actor(), at=<now
  ISO-8601>)`. Makes 2.10 GREEN.
- [x] **2.12** [TEST] `tests/unit/application/test_query.py` — same pattern
  for the `query --save` (Insight) write path. **RED today**: `TypeError`.
- [x] **2.13** [IMPL] `src/openkos/application/query.py`: same replacement
  for the Insight builder call. Makes 2.12 GREEN.
- [x] **2.14** [IMPL] `src/openkos/cli/main.py`: update the
  `application_ingest`/`application_query` staging call sites design.md
  names (approx. lines 5302, 5421, 5516, 15081) that currently pass
  `timestamp=` down into `model/okf.py` builders, to pass `generated=`
  instead, matching 2.11/2.13's shape. No new test — covered by 2.10/2.12 at
  the application layer and by the full CLI suite in Phase 2 verification.

### `build_merged_document` generation rule + `active` → `stable`

- [x] **2.15** [TEST] `tests/unit/model/test_okf.py` — add
  `test_build_merged_document_generation_rule_table`, parametrized over the
  four cases design.md Decision 5 names: v0.2 survivor + legacy-timestamped
  absorbed (survivor's `generated` wins if more recent, else absorbed's is
  converted `{by: LEGACY_ACTOR, at: <absorbed timestamp>}` per whichever
  side is more recent through `generation_time`); legacy survivor + v0.2
  absorbed; both v0.2; both legacy. Assert the merged document never carries
  a bare `timestamp` key, and `freshness` still travels with the winner
  unchanged. Covers entity-resolution-merge scenarios "A v0.2 survivor
  merges with a legacy-timestamped absorbed object" and "A legacy-timestamped
  survivor absorbs a more recent v0.2 object". **RED today**:
  `_absorbed_is_more_recent` still reads raw `timestamp` and the merged
  document still carries a bare `timestamp` key. Kills the winner picked by
  raw-string comparison instead of `generation_time`, and kills a merged
  document that keeps `timestamp` instead of writing `generated`.
- [x] **2.16** [TEST] Same file — add
  `test_build_merged_document_status_active_to_stable`: a survivor or
  absorbed side carrying `status: active` yields a merged document with
  `status: "stable"`; a side carrying `status: draft` or `status: deprecated`
  is left untouched (survivor-wins default applies as today). **RED today**:
  no normalization exists yet.
- [x] **2.17** [IMPL] `src/openkos/model/okf.py`: replace
  `_absorbed_is_more_recent`'s two `_parse_timestamp` calls with
  `generation_time` calls over each side's full metadata mapping; add
  `generated`/`timestamp` to `build_merged_document`'s special-keys handling
  so the winner's generation is written as v0.2 form (verbatim `generated`
  mapping when the winner already has one, else `{by: LEGACY_ACTOR, at:
  <winner's timestamp value>}`), and the merged survivor never carries a
  bare `timestamp`; add the `active` → `stable` status normalization (exact
  match only; every other value including `draft`/`deprecated`/unknown
  strings passes through unchanged). Makes 2.15-2.16 GREEN.

### Frozen v0.1 fixture + golden regeneration

- [x] **2.18** [IMPL] Move today's v0.1 builder goldens into
  `tests/unit/model/fixtures/okf_v01_documents.json` (new file): a
  representative set of `build_source_concept`/`build_concept` outputs
  produced with the OLD `timestamp=`/`status: active` signature, frozen as
  literal strings — these become Phase 4's migration-input fixtures and
  Phase 2's "what a pre-this-change engine wrote" regression baseline. No
  paired TEST — this is fixture data, verified indirectly by every test that
  loads it in Phase 4.
- [x] **2.19** [IMPL] Regenerate `tests/unit/model/fixtures/
  okf_framing_goldens.json` with the new v0.2 builder signatures and a fixed
  `Generated(by="openkos/test", at="2026-01-01T00:00:00Z")` (or the file's
  existing convention for a pinned actor) so every golden test using it is
  deterministic across machines.
- [x] **2.20** [IMPL] Update every test file design.md's "Goldens and
  fixtures" section names (about 80 occurrences across 12 test files) that
  pin `status: active`/`timestamp:` byte sequences, to the new
  `generated`/`status: stable` shape — grep `timestamp:` and `status:
  active` across `tests/unit/` and update each occurrence that exercises a
  Phase 2 code path. Occurrences belonging to Phase 3's `# Citations`
  removal or Phase 6/7's migration output are left for those phases.

### Phase 2 verification

- [x] **2.21** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **2.22** Run `uv run pytest tests/unit/model/test_okf.py
  tests/unit/application/test_ingest.py tests/unit/application/test_query.py`
  focused, then `uv run pytest --cov` (unpiped) full suite — must be green,
  90% branch gate held. Full-stream CLI goldens that assert on `timestamp`/
  `status: active` bytes are expected to need the 2.20 update; do not skip
  or xfail any — fix the golden.
- [x] **2.23** Run `uv run python evals/run_self_tests.py` — must be green.
- [x] **2.24** Commit as one or more work-unit commits, scope `model` (e.g.
  `feat(model): emit generated/status: stable from every writer`). Open PR 2
  (Phase 2: writers — generated + status) targeting `main`, branched from
  `main` after PR 1 merges.

**Rollback boundary**: revert `Generated`/`engine_actor`/`LEGACY_ACTOR`,
`OKF_VERSION`, the builder signature changes, the four call sites, the
`build_merged_document` generation rule, and the golden/fixture files.
Phase 1's readers keep working against the reverted v0.1 output; no
`sources` key exists yet to revert.

---

## Phase 3 (PR 3 → `main`, after PR 2 merges): Writers — `sources` projection

Design.md "Technical Approach" layer 2 (second half) and Decisions 3-4.

### `model/okf.py` — `project_sources` (Decision 3)

- [x] **3.1** [TEST] `tests/unit/model/test_okf_sources_projection.py` (new
  file) — add `test_project_sources_normalizes_concept_id_entries`,
  parametrized over a `provenance` entry shape: `sources/foo` (bare),
  `/sources/foo` (leading slash), `sources/foo.md` (trailing `.md`),
  `/sources/foo.md` (both) — every variant projects to `{"id":
  "sources/foo", "resource": "/sources/foo.md"}`. Covers ingestion scenario
  "`sources` matches the projection of `provenance`" (normalization half).
  **RED today**: `AttributeError` — `project_sources` does not exist.
- [x] **3.2** [TEST] Same file — add
  `test_project_sources_skips_raw_entries_and_dedupes_and_orders`: a
  `provenance` list mixing a `raw/<name>` workspace path (skipped, not
  projected) with two Concept-ID entries, one repeated (first occurrence
  wins, order preserved) — the projected list has exactly the two distinct
  Concept-ID entries in first-occurrence `provenance` order. **RED today**:
  same `AttributeError`.
- [x] **3.3** [TEST] Same file — add
  `test_project_sources_none_cases`, parametrized: `provenance` absent; a
  non-list value; a list containing only non-string/empty entries; an empty
  list; a list with only `raw/` entries — every case yields `project_sources(...)
  is None`. Covers the "whole projection is `None`" rule and the Source-
  document case (a Source's only provenance is its raw original). **RED
  today**: same `AttributeError`. Kills a `None` case that instead returns
  `[]`, which would make the builder insert an empty `sources: []` key
  rather than omitting it.
- [x] **3.4** [TEST] Same file — add
  `test_project_sources_key_order_is_id_then_resource`: the returned dict's
  `list(entry.keys()) == ["id", "resource"]` for every entry (insertion
  order, per §5.1's own example). **RED today**: same `AttributeError`.
- [x] **3.5** [IMPL] `src/openkos/model/okf.py`: add `SOURCES_KEY: Final =
  "sources"` and `project_sources(provenance: object) -> list[dict[str,
  str]] | None` implementing design.md Decision 3's table exactly (skip
  `raw/`-prefixed entries; normalize a Concept-ID entry by stripping one
  leading `/` and one trailing `.md`; `id`-then-`resource` key order; first-
  occurrence-wins dedup; `None` when nothing survives). Makes 3.1-3.4 GREEN.

### `model/okf.py` — `refresh_sources` (Decision 3-4)

- [x] **3.6** [TEST] Same file — add `test_refresh_sources_maintenance_rules`,
  parametrized: `sources` present + projection non-`None` -> replaced in
  place at its existing key position; `sources` present + projection
  `None` -> key removed; `sources` absent -> metadata returned unchanged
  (no key inserted). **RED today**: `AttributeError` — `refresh_sources`
  does not exist.
- [x] **3.7** [IMPL] Same module: add `refresh_sources(metadata: dict[str,
  object]) -> dict[str, object]` (returns a copy) per 3.6's table. Makes 3.6
  GREEN.

### Builders + merge introduce `sources`; provenance retarget maintains it

- [x] **3.8** [TEST] `tests/unit/model/test_okf.py` — add
  `test_build_concept_sources_matches_provenance_projection`: for a
  `build_concept` call with a Concept-ID `provenance` list, the returned
  document's `sources` equals `project_sources(provenance)`; key placement
  is immediately after `provenance`. Covers ingestion's parity scenario at
  the single-document level. **RED today**: `AssertionError` — no `sources`
  key written.
- [x] **3.9** [TEST] Same file — add
  `test_build_source_concept_never_writes_sources`: a `build_source_concept`
  call (whose only provenance is its `raw/` original) never writes a
  `sources` key. **RED today**: passes vacuously today (no `sources` logic
  exists at all) — assert explicitly that the key is absent even after 3.10
  wires the projection call, so this stays a real regression pin once 3.10
  lands; write it as `assert "sources" not in metadata` before 3.10 to
  confirm it is genuinely exercised.
- [x] **3.10** [IMPL] `src/openkos/model/okf.py`: call `project_sources`
  inside `build_concept` (introduces `sources` when non-`None`, inserted
  immediately after `provenance`) and inside `build_source_concept`
  (always `None` for the OpenKOS shape today, so no key is ever written —
  no call needed, but add a one-line comment citing design.md Decision 3's
  "a Source document therefore gets no `sources`" rule so a future reader
  does not add one accidentally). Makes 3.8-3.9 GREEN.
- [x] **3.11** [TEST] Same file — add
  `test_build_merged_document_sources_matches_unioned_provenance`: the
  merged document's `sources` equals `project_sources` of the UNIONED
  `provenance` list (both sides combined per the existing merge rule).
  **RED today**: `AssertionError`.
- [x] **3.12** [IMPL] Same module: call `project_sources` inside
  `build_merged_document` over the already-unioned `provenance` value,
  introducing/replacing `sources` on the merged survivor. Makes 3.11 GREEN.
- [x] **3.13** [TEST] `tests/unit/bundle/test_provenance.py` — add
  `test_apply_provenance_rewrites_refreshes_sources`: a document with an
  existing `sources` key, after a provenance retarget rewrite, has `sources`
  updated via `refresh_sources` to match the new `provenance`; a document
  with NO `sources` key is left with none after the same rewrite (v0.1
  document pre-`repair` stays byte-identical, per design.md Decision 4's
  rationale). **RED today**: `AssertionError` — `apply_provenance_rewrites`
  does not touch `sources` today.
- [x] **3.14** [IMPL] `src/openkos/bundle/provenance.py`: call
  `okf.refresh_sources(metadata)` inside `apply_provenance_rewrites` after
  the `provenance` list is rewritten. Makes 3.13 GREEN.

### Source body: no trailing `# Citations`

- [x] **3.15** [TEST] `tests/unit/model/test_okf.py` — extend/confirm the
  test from 2.6 (already covers the no-Citations body shape for both
  verbatim and undecodable cases) additionally checks the empty-source case
  from ingestion's "Empty source renders a distinct body" scenario also
  ends with no `# Citations` heading. **RED today**: covered by 2.7's
  removal already if 2.6/2.7 landed correctly in Phase 2 — this task is a
  regression confirmation, not new behavior; if RED, it means Phase 2 missed
  the empty-source body path — fix there, not here.

### Parity test (bundle-wide, across ingest→merge)

- [x] **3.16** [TEST] `tests/unit/model/test_okf_sources_projection.py` —
  add `assert_sources_parity(bundle_dir)` helper (design.md's Testing
  Strategy table) plus `test_sources_parity_after_ingest_and_merge`: run a
  real `ingest` then a real `merge` in `tmp_path` via the application layer
  (no CLI subprocess needed), then assert `assert_sources_parity` holds for
  every document that carries a `sources` key. **RED today**: `sources`
  key does not exist yet at the start of this task's TEST/IMPL pairing
  sequence — if 3.5-3.14 already landed in this same PR before this task
  runs, this is a genuine end-to-end regression pin; write it RED first by
  asserting before those IMPL tasks, or — since Strict TDD requires an
  observed RED — implement this task's harness immediately after 3.5 lands
  (before 3.10/3.12/3.14), observe it RED (`sources` key absent everywhere,
  so parity vacuously fails the "at least one document has sources"
  precondition), then GREEN once 3.10/3.12/3.14 land. **MUTATION**: change
  `project_sources`'s Concept-ID normalization to drop the trailing `.md`
  incorrectly (or swap `id`/`resource`) and confirm this test fails.

### AST guard 1 — no read of `sources` outside the projection

- [x] **3.17** [TEST] `tests/unit/test_sources_key_guard.py` (new file) —
  add `test_no_module_outside_projection_reads_sources_key`: an AST walk of
  every `.py` file under `src/openkos/` that rejects any `ast.Subscript`
  load, `.get("sources")`/`.get(okf.SOURCES_KEY)` call, or `"sources" in
  <mapping>` / `okf.SOURCES_KEY in <mapping>` membership test, EXCEPT inside
  `project_sources`, `refresh_sources`, and `migrate_document` in
  `model/okf.py` (the last is a forward reference to Phase 4 — allow it now
  so this guard does not need editing again in Phase 4). Covers ingestion
  scenario "No module outside the projection reads `sources` back". **RED
  today**: `ModuleNotFoundError`/`AttributeError` — the test file and its
  scanner do not exist.
- [x] **3.18** [IMPL] `src/openkos/model/okf.py` + guard test: implement the
  AST scanner inside the test file itself (test-only code, no production
  change beyond what 3.5/3.7 already added). Makes 3.17 GREEN.
- [x] **3.19** [TEST, mutation-proof] In `test_sources_key_guard.py`,
  temporarily add a forbidden read (e.g. `_ = metadata.get("sources")`
  inside an unrelated function in a scratch module the test scans, or
  monkeypatch the scan target list to include a deliberately-planted
  violation fixture file under the test's own `tmp_path`) and confirm
  3.17's scanner reports it as a failure; then remove the planted violation.
  Record the mutation and result inline as a comment in the test file per
  project practice (mutate the line it should catch before trusting it).

### AST guard 2 — only builders + retarget store `provenance`

- [x] **3.20** [TEST] Same file — add
  `test_provenance_key_writers_are_pinned_to_builders_and_retarget`: an AST
  walk pinning the exact set of functions across `src/openkos/` that assign
  into a `provenance`/`okf.PROVENANCE_KEY`-equivalent dict key (or the
  literal string `"provenance"`) to exactly `{build_concept,
  build_source_concept, build_merged_document, migrate_document,
  apply_provenance_rewrites}` (confirm the exact constant/literal
  `model/okf.py` uses for the `provenance` key name during implementation
  and pin that name, not a guessed one). **RED today**:
  `ModuleNotFoundError`/`AttributeError`.
- [x] **3.21** [IMPL] Implement the second scanner in the same test file.
  Makes 3.20 GREEN.
- [x] **3.22** [TEST, mutation-proof] Same file — remove the
  `okf.refresh_sources` call added in 3.14 from a scratch copy of
  `apply_provenance_rewrites` used only inside this mutation test (or
  monkeypatch a stand-in), and confirm 3.13's parity assertion (or 3.16's
  bundle-wide parity) fails without it; restore the call. Record the
  mutation and result inline as a comment.

### Phase 3 verification

- [x] **3.23** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **3.24** Run `uv run pytest tests/unit/model/test_okf_sources_projection.py
  tests/unit/test_sources_key_guard.py tests/unit/bundle/test_provenance.py
  tests/unit/model/test_okf.py` focused, then `uv run pytest --cov`
  (unpiped) full suite — must be green, 90% branch gate held.
- [x] **3.25** Run `uv run python evals/run_self_tests.py` — must be green.
- [ ] **3.26** Commit as one or more work-unit commits, scope `model` (e.g.
  `feat(model): project sources from provenance at every write point`).
  Open PR 3 (Phase 3: writers — sources) targeting `main`, branched from
  `main` after PR 2 merges.

**Rollback boundary**: revert `project_sources`/`refresh_sources`, the
builder/merge/provenance-retarget call sites, `SOURCES_KEY`, and both guard
test files. Phase 2's `generated`/`status: stable` output is untouched;
Phase 1's readers keep working.

---

## Phase 4 (PR 4 → `main`, after PR 3 merges): The migration function, `migrate_document`

Design.md "Technical Approach" layer 3 (library half) and Decision 8. A pure
library with no production caller until Phase 6.

### `migrate_document` — R1 (`generated`) and unquoted-timestamp preservation

- [x] **4.1** [TEST] `tests/unit/model/test_okf_migrate_document.py` (new
  file) — add `test_migrate_document_r1_generated_from_scalar_timestamp`: a
  frontmatter block with `timestamp: '2026-07-14T09:00:00Z'` (quoted
  scalar) and no `generated` key migrates to
  `generated: {by: openkos/legacy, at: '2026-07-14T09:00:00Z'}` with
  `timestamp` removed. Covers okf-format-migration scenario "A v0.1 concept
  is rewritten to v0.2 shape" (the `generated` half). **RED today**:
  `ModuleNotFoundError` — the module does not exist.
- [x] **4.2** [TEST] Same file — add
  `test_migrate_document_r1_preserves_unquoted_timestamp_source_text`: a
  frontmatter block with a BARE, UNQUOTED `timestamp: 2026-07-14T09:00:00Z`
  (which PyYAML resolves to a `datetime` on load) migrates so that
  `generated.at`'s SOURCE TEXT is the exact original unquoted string
  `2026-07-14T09:00:00Z` — obtained via `yaml.compose` over the verbatim
  frontmatter block, never via `datetime.isoformat()` (which would rewrite
  `Z` as `+00:00`). Covers design.md Decision 5's "Unchanged means the
  string value is identical; YAML quoting may differ" rule — this is the
  hard part design.md flags explicitly. **RED today**: same
  `ModuleNotFoundError`; once 4.3 exists without this specific handling,
  **RED reason after 4.3 lands naively**: `AssertionError` — a naive
  `str(parsed_datetime)` would emit `2026-07-14 09:00:00+00:00`, not the
  original `Z`-suffixed text.
- [x] **4.3** [TEST] Same file — add
  `test_migrate_document_r1_noop_and_refusal_cases`, parametrized: `generated`
  present (with or without a leftover `timestamp`, which stays as an unknown
  key) -> no `generated`-related rewrite; neither key present -> no rewrite;
  `timestamp` present as a YAML mapping or list (not a scalar) -> `Refused
  ("timestamp is not a scalar")`. **RED today**: same `ModuleNotFoundError`.
- [x] **4.4** [IMPL] `src/openkos/model/okf.py`: add `MigrationChanges`
  (frozen dataclass: `generated: bool, status: bool, sources: bool,
  citations_removed: bool, legacy_citations: bool`), `Unchanged` (frozen
  dataclass: `legacy_citations: bool`), `Migrated` (frozen dataclass: `text:
  str, changes: MigrationChanges`), `Refused` (frozen dataclass: `reason:
  str`), `MigrationResult = Unchanged | Migrated | Refused`, and
  `migrate_document(text: str) -> MigrationResult` implementing ONLY rule R1
  so far: use `split_frontmatter_verbatim` to get the frontmatter block,
  `yaml.compose` it to locate the `timestamp` scalar node's source text
  without resolving it to a `datetime`, apply R1's three branches from
  design.md Decision 8's table, and re-serialize via `dump_frontmatter` only
  when R1 fires. Makes 4.1-4.3 GREEN.

### `migrate_document` — R2 (`status`), R3 (`sources`)

- [x] **4.5** [TEST] Same file — add `test_migrate_document_r2_status_active_to_stable`:
  `status: active` -> `status: stable` in place; every other value
  (`stable`, `draft`, `deprecated`, an unknown string, absent) is left
  untouched. **RED today**: `AssertionError` — R2 not implemented.
- [x] **4.6** [TEST] Same file — add
  `test_migrate_document_r3_sources_set_or_unchanged`: a document whose
  `project_sources(provenance)` differs from its current `sources` (or
  whose `sources` is absent but the projection is non-`None`) gets `sources`
  set via the refresh/insert rule; a document whose `sources` already
  matches the projection is left byte-unchanged by R3 (though R1/R2/R4 may
  still fire independently). **RED today**: `AssertionError` — R3 not
  implemented.
- [x] **4.7** [IMPL] Same module: add R2 (exact-match `active` -> `stable`
  rewrite) and R3 (call `project_sources`/insertion-or-replacement per
  Decision 3's key-placement rule; no-op when already equal) to
  `migrate_document`. Makes 4.5-4.6 GREEN.

### `migrate_document` — R4 (`# Citations`) and the legacy-citations report

- [x] **4.8** [TEST] Same file — add
  `test_migrate_document_r4_removes_bare_trailing_citations_heading`: a
  `type: Source` document whose body ends with a bare `# Citations` heading
  (only whitespace after it) has that heading and the blank line before it
  removed, body ending in exactly one `\n`. Covers okf-format-migration
  scenario "A bare empty Citations heading is removed". **RED today**:
  `AssertionError` — R4 not implemented.
- [x] **4.9** [TEST] Same file — add
  `test_migrate_document_reports_legacy_citations_without_converting`: a
  document whose `# Citations` section carries non-empty hand-authored
  content anywhere in the body — including a NON-trailing bare heading (per
  the "report" row of design.md Decision 8's table) — is left with that
  content byte-unchanged, and the result's `legacy_citations` flag
  (`Migrated.changes.legacy_citations` or `Unchanged.legacy_citations`) is
  `True`. Covers okf-format-migration scenario "A non-empty Citations
  section survives migration". **RED today**: `AssertionError`.
- [x] **4.10** [IMPL] Same module: add R4 (type == `"Source"` AND body ends
  in a bare trailing `# Citations` heading -> remove it) and the
  `legacy_citations` detection (any non-empty `# Citations` section, or a
  bare one that is not trailing) to `migrate_document`, threading the flag
  through both `Unchanged` and `Migrated`. Makes 4.8-4.9 GREEN.
- [x] **4.11** [TEST] Same file — add
  `test_migrate_document_unparseable_or_missing_frontmatter_refuses`: text
  with no frontmatter block, or a frontmatter block whose YAML fails to
  parse, yields `Refused("unparseable frontmatter")`. **RED today**:
  `AssertionError`.
- [x] **4.12** [IMPL] Same module: wire the unparseable-frontmatter refusal
  as `migrate_document`'s first check, before any rule. Makes 4.11 GREEN.

### Properties: idempotency, unchanged-returns-identical-bytes, builder-equivalence, commutation

- [x] **4.13** [TEST] Same file — add
  `test_migrate_document_unchanged_returns_input_bytes_untouched`: when no
  rule fires, `Unchanged`'s implicit text (the caller's own input, since
  `Unchanged` carries no `text` field per the Interfaces/Contracts table —
  confirm during implementation whether the caller is expected to keep its
  own `text` reference on `Unchanged`, and assert exactly that contract) —
  a hand-formatted, already-v0.2 document is never cosmetically
  re-serialized; detection is exactly "would migration change the bytes".
  **RED today**: `AssertionError` if any rule's no-op branch accidentally
  re-serializes via `dump_frontmatter`.
- [x] **4.14** [TEST] Same file — add `test_migrate_document_is_idempotent`,
  property-style over every entry of
  `tests/unit/model/fixtures/okf_v01_documents.json` (2.18): for each fixture
  text `x`, migrating twice equals migrating once — `migrate(migrate(x)) ==
  migrate(x)` compared on the resulting text (or both `Unchanged` on the
  second pass). Covers okf-format-migration's idempotency requirement at the
  per-document level (the whole-`repair`-run idempotency is Phase 6). **RED
  today**: `AssertionError` if any rule is not stably idempotent (e.g. a
  timestamp re-quoted differently on a second pass).
- [x] **4.15** [TEST] Same file — add
  `test_migrate_document_equivalent_to_new_builders`, looping over every
  fixture in `okf_v01_documents.json` alongside the exact builder arguments
  that produced it (fixture format from 2.18 must carry both): asserts
  `migrate_document(old_builder_output) == new_builder_output(same args,
  generated=Generated(by="openkos/legacy", at=<the old fixture's
  timestamp>))` for every fixture. **RED today**: `AssertionError` on any
  fixture where migration and the new builder diverge (e.g. key order,
  quoting style).
- [x] **4.16** [TEST] Same file — add
  `test_migrate_document_commutes_with_provenance_and_relation_rewrites`:
  for a representative v0.1 document `s`, `migrate_document(apply_provenance_
  rewrites_text(s)) == apply_provenance_rewrites_text(migrate_document(s))`,
  and the same for `apply_relation_rewrites`/`apply_link_rewrites` — build
  each rewrite via the real bundle functions over `tmp_path` fixtures, not
  hand-constructed text. Covers design.md's commutation property, load-
  bearing for Phase 5's link-offset math. **RED today**: `AssertionError` if
  key placement/ordering between migration and a rewrite pass disagree.
- [x] **4.17** [IMPL] Fix any divergence 4.13-4.16 expose in
  `migrate_document`, `project_sources`, or `dump_frontmatter`'s key-
  ordering behavior until all four properties hold. No new production
  surface — this task closes gaps the property tests found.

### Phase 4 verification

- [x] **4.18** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [x] **4.19** Run `uv run pytest tests/unit/model/test_okf_migrate_document.py`
  focused, then `uv run pytest --cov` (unpiped) full suite — must be green,
  90% branch gate held.
- [x] **4.20** Run `uv run python evals/run_self_tests.py` — must be green.
- [x] **4.21** Commit as one or more work-unit commits, scope `model` (e.g.
  `feat(model): add the pure OKF v0.1 to v0.2 document migration function`).
  Open PR 4 (Phase 4: migration function) targeting `main`, branched from
  `main` after PR 3 merges.

  Done as commit `0edb102` (`feat(model): add the pure OKF v0.1 to v0.2
  document migration function (#1064)`) on
  `feat/1064-okf-v02-p3a-migrate-document`, stacked on `634b3f6` (Phase
  3/PR 3, not yet merged to `main`). PR opening is a user/repository-policy
  decision outside apply's scope; the branch is ready to open as PR 4
  targeting `main`.

**Rollback boundary**: revert `migrate_document`/`MigrationResult`/
`MigrationChanges`/`Unchanged`/`Migrated`/`Refused` and the new test file.
Nothing calls this function yet, so no bundle-facing behavior changes.

---

## Phase 5 (PR 5 → `main`, after PR 4 merges): Ledger migration, `migrate_sidecars_to_okf_v02`

Design.md Decision 1 (the hardest part of this change). A library with no
caller until Phase 6 wires `repair`'s apply phase.

### Snapshot migration — whole-document fields

- [ ] **5.1** [TEST] `tests/unit/bundle/test_ledger_okf_migration.py` (new
  file) — add `test_migrate_sidecars_migrates_every_whole_document_snapshot`:
  build a real V3-or-later sidecar (via a real `merge` call in `tmp_path`,
  so `relation_rewrites`/`provenance_rewrites` are populated) whose
  `absorbed_snapshot`, `survivor_before`, each `relation_rewrites[].snapshot`,
  and each `provenance_rewrites[].snapshot` are v0.1-shaped text; run
  `migrate_sidecars_to_okf_v02`; assert every one of those four field
  categories is now `migrate_document`'s output for its original bytes.
  **RED today**: `AttributeError` — `migrate_sidecars_to_okf_v02` does not
  exist.
- [ ] **5.2** [TEST] Same file — add
  `test_migrate_sidecars_recurses_into_embedded_merged_from_snapshots`: a
  `survivor_before` snapshot that itself embeds a pre-relocation
  `merged_from` list (a nested nested-prefix case, per ADR-0002/#758) has
  ITS embedded entries' own snapshot fields migrated too, recursively, by
  the same function — so `scan_nesting_violations`'s (Check B) nested-prefix
  equality still holds after migration (equal inputs map to equal outputs).
  **RED today**: same `AttributeError`.
- [ ] **5.3** [IMPL] `src/openkos/bundle/ledger.py`: add
  `migrate_sidecars_to_okf_v02(bundle_dir: Path, *, current_texts:
  Mapping[str, str]) -> list[tuple[Path, str, list[okf.MergeLedgerEntry]]]`
  covering: iterate every sidecar via the existing scan/read path, call
  `okf.migrate_document` on `absorbed_snapshot`/`survivor_before`/each
  `relation_rewrites[].snapshot`/each `provenance_rewrites[].snapshot`,
  recursing into any embedded `merged_from` inside a `survivor_before`
  (decode it, migrate its own snapshot fields the same way, re-encode),
  return only the `(path, survivor_id, entries)` triples whose bytes
  actually changed. Makes 5.1-5.2 GREEN.

### `index_before` flip (V1-V4 only)

- [ ] **5.4** [TEST] Same file — add
  `test_migrate_sidecars_flips_index_before_okf_version_v1_through_v4`,
  parametrized over `MERGE_LEDGER_SCHEMA_V1`..`V4` fixtures (built by real
  merges under monkeypatched schema constants if needed, or hand-constructed
  `MergeLedgerEntry`s matching each schema's exact required-field shape from
  `okf.py`'s docstrings): each entry's `index_before` (a whole-`index.md`
  snapshot) has its `okf_version` value flipped to `"0.2"` with its body
  otherwise untouched (via `split_frontmatter_verbatim`, never a full
  re-dump, matching design.md's "its body is untouched" rule); `log_before`
  is unchanged. A V5 entry (no `index_before`/`log_before` fields at all —
  see `MERGE_LEDGER_SCHEMA_V5`'s replacement mechanism) is skipped by this
  rule entirely (nothing to flip). **RED today**: `AssertionError`/
  `AttributeError`.
- [ ] **5.5** [IMPL] `src/openkos/bundle/ledger.py`: extend
  `migrate_sidecars_to_okf_v02` to flip each V1-V4 entry's `index_before`
  `okf_version` frontmatter value via `split_frontmatter_verbatim` +
  `dump_frontmatter` (or a targeted regex/YAML-node rewrite that preserves
  quoting, matching how `repair`'s own bundle-root flip will work in Phase
  6) when the snapshot's declared `okf_version` differs from `"0.2"`. Makes
  5.4 GREEN.
- [ ] **5.6** [TEST] Same file — add
  `test_migrate_sidecars_check_b_still_passes_after_migration`: after
  migrating a fixture with an embedded nested `merged_from` history,
  `bundle/ledger.scan_nesting_violations` reports the same (zero, in a clean
  fixture) result on the migrated sidecar as it did before migration —
  equal inputs map to equal outputs, so nesting equality is preserved.
  **RED today**: fails if 5.3's recursion is not byte-exact on both sides
  of the comparison.

### Link-offset shift

- [ ] **5.7** [TEST] Same file — add
  `test_migrate_sidecars_shifts_link_rewrite_offsets_from_current_text`: a
  sidecar whose file `f` has no LATER ledger snapshot recorded (so its
  frontmatter "right after" the merge is `f`'s CURRENT text) — each
  `link_rewrites[].offset` for `f` is shifted by exactly `body_start(migrate_
  document(current_text_of_f)) - body_start(current_text_of_f)`, computed on
  the ACTUAL texts (never assumed from YAML length). An offset below the OLD
  body start is left unshifted. **RED today**: `AttributeError`/
  `AssertionError`.
- [ ] **5.8** [TEST] Same file — add
  `test_migrate_sidecars_shifts_link_rewrite_offsets_from_a_later_snapshot`:
  a sidecar whose file `f` DOES have a later ledger entry (any sidecar) with
  `merged_at > this entry's merged_at` recording a snapshot of `f`
  (`survivor_before`, `absorbed_snapshot`, a `relation_rewrites` or
  `provenance_rewrites` entry) — the shift uses THAT earliest later
  snapshot's frontmatter, not the current text, built from an index of
  snapshots constructed once across all sidecars from the pre-migration
  ledger state. **RED today**: same.
- [ ] **5.9** [IMPL] `src/openkos/bundle/ledger.py`: add the cross-sidecar
  snapshot-index builder (one pass over every sidecar's pre-migration state,
  keyed by file path, tracking the earliest snapshot with `merged_at >
  entry.merged_at` for each `link_rewrites` target file) and the offset-
  shift application: `body_start` measured on real text via
  `split_frontmatter_verbatim`, shift = new body_start − old body_start,
  applied only to offsets at or above the old body_start. Makes 5.7-5.8
  GREEN.

### V1-V5 round-trip commutation: `unmerge(repair(merge(B)))[d] == repair(B)[d]`

This is the hard-part invariant design.md states explicitly. It exercises
Phase 6's `repair`/`apply_repair` too, so these tasks are written now but
their final GREEN state depends on Phase 6 landing; keep them RED (skipped
with a tracked reason, never silently passed) until Phase 6's PR, then
confirm GREEN as part of Phase 6 verification (6.16 references back to this
task).

- [ ] **5.10** [TEST] `tests/unit/bundle/test_ledger_okf_migration.py` — add
  `test_merge_repair_unmerge_round_trip_v1_schema`: using real `init` +
  `ingest` (x2) + `merge` calls in `tmp_path` with the ledger schema
  constant monkeypatched to force a V1 write (or a fixture pre-built to V1
  shape if monkeypatching the schema constant is impractical — decide
  during implementation and document the choice inline), then Phase 6's
  `apply_repair`, then `unmerge` — for every concept document `d` the merge
  touched, `unmerge(repair(merge(B)))[d] == repair(B)[d]` (build `repair(B)`
  as a separate reference bundle from the same pre-merge state, migrated
  directly with no merge in between). **RED today**: `ModuleNotFoundError`
  — `application/repair.py` (Phase 6) does not exist yet; mark this test
  `pytest.mark.skip(reason="okf-v02-migration Phase 6 not yet landed")`
  in Phase 5's PR and remove the skip mark in Phase 6.
- [ ] **5.11** [TEST] Same file — add
  `test_merge_repair_unmerge_round_trip_v2_schema`, same shape, forcing a V2
  ledger entry (`relation_rewrites` populated, `provenance_rewrites` empty).
  Same skip-until-Phase-6 treatment.
- [ ] **5.12** [TEST] Same file — add
  `test_merge_repair_unmerge_round_trip_v3_schema`, forcing V3
  (`provenance_rewrites` populated too). Same skip-until-Phase-6 treatment.
- [ ] **5.13** [TEST] Same file — add
  `test_merge_repair_unmerge_round_trip_v4_schema`, forcing V4
  (`carried_content_ids` populated via a second merge onto the same
  survivor). Same skip-until-Phase-6 treatment.
- [ ] **5.14** [TEST] Same file — add
  `test_merge_repair_unmerge_round_trip_v5_schema`, using an UNFORCED (i.e.
  current-default) real merge, which per `MERGE_LEDGER_SCHEMA_V5` writes
  `index_restores` instead of `index_before`/`log_before` — assert the same
  invariant AND that no v0.1-shaped document remains anywhere in the bundle
  after `unmerge` (covers okf-format-migration's "Merge, Repair, And Unmerge
  Leave No V0.1-Shaped Document" requirement directly, its own scenario).
  Same skip-until-Phase-6 treatment.

### Phase 5 verification

- [ ] **5.15** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **5.16** Run `uv run pytest tests/unit/bundle/test_ledger_okf_migration.py`
  focused (5.10-5.14 expected `SKIPPED`, not failed, with the recorded
  reason), then `uv run pytest --cov` (unpiped) full suite — must be green,
  90% branch gate held.
- [ ] **5.17** Run `uv run python evals/run_self_tests.py` — must be green.
- [ ] **5.18** Commit as one or more work-unit commits, scope `bundle` (e.g.
  `feat(bundle): migrate merge-ledger sidecars to OKF v0.2 snapshots`). Open
  PR 5 (Phase 5: ledger migration) targeting `main`, branched from `main`
  after PR 4 merges.

**Rollback boundary**: revert `migrate_sidecars_to_okf_v02` and the snapshot-
index helper. No sidecar has been rewritten in production; `unmerge` still
restores unmodified V1-V5 snapshots exactly as before this PR.

---

## Phase 6 (PR 6 → `main`, after PR 5 merges): The `repair` verb — OKF migration

Design.md Decision 9-10 and the entity-resolution-merge delta's scoped Gate
2. The only slice that rewrites a user's bundle.

### Scoped Gate 2 (entity-resolution-merge delta)

- [ ] **6.1** [TEST] `tests/unit/cli/test_repair.py` — add
  `test_repair_gate2_not_evaluated_when_nothing_to_extract`: a bundle with
  no pre-relocation, frontmatter-embedded `merged_from` history to extract
  (`scan_unmigrated` returns `[]`), but a survivor whose ALREADY-RELOCATED
  sidecar carries 2+ merge-ledger entries — `repair` does NOT refuse on Gate
  2 and the OKF migration proceeds. Covers entity-resolution-merge scenario
  "A sidecar-only bundle with 2 or more entries proceeds to OKF migration".
  **RED today**: `AssertionError` — Gate 2 is evaluated unconditionally
  today.
- [ ] **6.2** [TEST] Same file — add
  `test_repair_gate2_still_refuses_whole_run_when_extraction_has_pollution_risk`:
  a bundle WITH pre-relocation ledgers to extract AND a survivor (migrated
  or not) carrying 2+ entries — `repair` refuses the WHOLE run (OKF
  migration included), exits non-zero, writes nothing, with the existing
  message and no `--force`/override. Covers scenario "Repair verb refuses
  the whole run when extraction meets the pollution-risk gate". **RED
  today**: should already pass (existing behavior) — write it as an
  explicit regression pin before touching the gate's evaluation condition in
  6.3, so a later change to the condition is caught if it silently narrows
  the refusal.
- [ ] **6.3** [IMPL] `src/openkos/cli/main.py` (or wherever `repair`'s Gate
  2 check currently lives — confirm exact location during implementation):
  wrap the `bundle_ledger.bundle_wide_max_entries(...) >= 2` check with `if
  bundle_ledger.scan_unmigrated(bundle_dir):` so it is evaluated only when
  there is pre-relocation extraction to do. Makes 6.1 GREEN, keeps 6.2
  GREEN.

### `application/repair.py` — plan phase

- [ ] **6.4** [TEST] `tests/unit/application/test_repair.py` (new file,
  mirroring `application/repair.py`'s pure-function boundary — CLI-level
  behavior stays in `tests/unit/cli/test_repair.py`) — add
  `test_plan_repair_gate1_pending_marker_refuses_unconditionally`: a
  `.pending` marker anywhere refuses the whole plan (unchanged text),
  regardless of OKF or ledger state. **RED today**:
  `ModuleNotFoundError` — `application/repair.py` does not exist.
- [ ] **6.5** [TEST] Same file — add
  `test_plan_repair_okf_scan_refuses_whole_run_on_any_refused_document`: one
  document that `migrate_document` refuses (e.g. a non-scalar `timestamp`)
  makes `plan_repair` return a `RepairRefusal` naming that concept's id and
  the refusal reason, for the WHOLE bundle — no partial plan, no override.
  **RED today**: same `ModuleNotFoundError`.
- [ ] **6.6** [TEST] Same file — add
  `test_plan_repair_detects_bundle_version_flip_needed`: a bundle whose
  `index.md` declares `okf_version` other than `"0.2"` (including a bundle
  with NO `index.md` at all, per OKF §11's tolerance) is correctly reflected
  in the plan's flip-needed flag; a bundle with no `index.md` plans no flip.
  **RED today**: same.
- [ ] **6.7** [TEST] Same file — add
  `test_plan_repair_nothing_to_migrate_when_bundle_is_fully_v2`: a bundle
  where every document is already v0.2-shaped, no ledger extraction needed,
  and `okf_version` already `"0.2"` yields the "nothing to migrate" refusal-
  shaped result (not a hard error — exit 0), writing nothing. Covers
  okf-format-migration scenario "A bundle with no v0.1-shaped concept
  refuses with nothing to migrate". **RED today**: same.
- [ ] **6.8** [IMPL] `src/openkos/application/repair.py` (new file): add
  `RepairPlan` (frozen dataclass: extraction list, document rewrites,
  sidecar rewrites, index flip flag, drift baselines, report counts),
  `RepairRefusal` (frozen dataclass: `message: str`), and `plan_repair
  (bundle_dir: Path) -> RepairPlan | RepairRefusal` implementing design.md
  Decision 9's five-step plan phase exactly: Gate 1 (pending markers, reused
  unchanged) → ledger extraction scan + scoped Gate 2 (6.3's condition,
  reused) → OKF scan via `migrate_document` over every non-reserved `.md`
  through `fsio.snapshot_read` (bytes kept as drift baselines), any
  `Refused` aborting the whole plan → per-sidecar dry-run through
  `migrate_sidecars_to_okf_v02` (Phase 5) → bundle-version check → "nothing
  to migrate" detection. Makes 6.4-6.7 GREEN.

### `application/repair.py` — apply phase, write ordering

- [ ] **6.9** [TEST] `tests/unit/cli/test_repair.py` — add
  `test_repair_reports_migration_counts_and_legacy_citations`: the printed
  report lines match design.md's exact shape (one line per applicable
  category: ledger, documents with per-field counts, sidecars,
  `okf_version` flip, and the `legacy_citations`-preserved line naming each
  concept id) against a fixture bundle exercising every category at once.
  **RED today**: `AttributeError`/`AssertionError` — `apply_repair` does not
  exist.
- [ ] **6.10** [TEST] Same file — add
  `test_repair_writes_in_order_sidecars_then_documents_then_index_last`: a
  monkeypatched write-order spy (patching `fsio.write_atomic` to append to
  an ordered list) over a fixture bundle with pending sidecar and document
  rewrites, plus an `okf_version` flip, asserts the observed write order is
  exactly: ledger extraction writes (if any) → sidecar OKF migrations →
  concept documents → `index.md` flip LAST. **RED today**: same.
- [ ] **6.11** [TEST] Same file — add
  `test_repair_crash_before_index_flip_leaves_okf_version_0_1_and_reruns_clean`:
  inject a failure (monkeypatch to raise) immediately before the
  `index.md` write step, confirm the bundle is left with `okf_version:
  "0.1"` over PARTIALLY migrated documents (never a bundle claiming v0.2
  with v0.1 content — assert this invariant directly), then re-run `repair`
  for real and confirm it completes cleanly (idempotent per-artifact
  detection picks up exactly the unfinished work). Covers design.md's torn-
  write safety guarantee. **RED today**: same.
- [ ] **6.12** [IMPL] Same module: add `RepairOutcome` and `apply_repair
  (root: Path, plan: RepairPlan) -> RepairOutcome` implementing design.md
  Decision 9's eight-step apply phase: reset-point note → `_reject_drifted_
  targets` against the plan's baselines → ledger extraction writes (existing
  behavior, unchanged) → sidecar OKF migrations (`bundle_ledger.
  rewrite_entries_at`, one `fsio.write_atomic` per sidecar, from Phase 5's
  output) → concept documents (`fsio.write_atomic` each) → `index.md`
  `okf_version` flip LAST (frontmatter re-rendered via
  `split_frontmatter_verbatim`, body kept verbatim) → one `_autocommit` over
  every touched path → `_refresh_derived_after_write(layout, cfg,
  verb="repair")`. Makes 6.9-6.11 GREEN.

### `openkos repair` CLI wiring, commit message, `unmerge` hint

- [ ] **6.13** [TEST] `tests/unit/cli/test_repair.py` — add
  `test_repair_commit_message_and_exactly_one_commit`: after a mixed
  extraction+OKF-migration run, exactly ONE commit exists whose message
  matches `openkos: repair (<parts>)` joining the applicable clause(s) with
  `; ` per design.md's exact wording, and whose touched-file list equals the
  plan's full touched set (sidecars + documents + `index.md`). **RED
  today**: `AttributeError` — `repair` is not yet wired to
  `application/repair.py`.
- [ ] **6.14** [TEST] Same file — add
  `test_repair_second_run_reports_nothing_to_migrate_and_writes_nothing`:
  after a successful migration, a second `repair` invocation prints the
  "nothing to migrate" line (extended to also cover OKF content, not only
  merge ledgers), exits 0, creates no commit, and the bundle's file mtimes/
  git status show zero changes. Covers okf-format-migration scenario
  "Re-running repair after a successful migration is a no-op". **RED
  today**: same.
- [ ] **6.15** [TEST] Same file — add
  `test_unmerge_refusal_names_repair_on_an_unrepaired_bundle`: `unmerge`'s
  existing drift refusal, when the bundle's `index.md` declares an
  `okf_version` other than `okf.OKF_VERSION`, appends the exact sentence
  "this bundle predates OKF 0.2; run `openkos repair` first". **RED today**:
  `AssertionError` — the hint does not exist yet.
- [ ] **6.16** [IMPL] `src/openkos/cli/main.py`: rewrite the `repair`
  command to call `plan_repair`/`apply_repair`, keeping only CLI parsing,
  printing, and exit codes; update `help=`/docstring text to describe both
  migrations (merge-ledger relocation and OKF v0.1→v0.2); append the
  `okf_version`-mismatch hint to `unmerge`'s existing drift-refusal message.
  Makes 6.13-6.15 GREEN. **Also**: remove the `pytest.mark.skip` from
  Phase 5's 5.10-5.14 round-trip tests now that `application/repair.py`
  exists, and confirm all five (V1-V5) pass GREEN as part of this task —
  this is the explicit closure of the merge → `repair` → `unmerge`
  commutation invariant design.md states as the hardest guarantee of the
  whole change.

### Phase 6 verification

- [ ] **6.17** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **6.18** Run `uv run pytest tests/unit/application/test_repair.py
  tests/unit/cli/test_repair.py tests/unit/bundle/test_ledger_okf_migration.py`
  focused (confirm zero `SKIPPED` remain in the round-trip suite), then `uv
  run pytest --cov` (unpiped) full suite — must be green, 90% branch gate
  held.
- [ ] **6.19** Run `uv run python evals/run_self_tests.py` — must be green.
- [ ] **6.20** Commit as one or more work-unit commits, scope `cli` (e.g.
  `feat(cli): migrate an OKF v0.1 bundle to v0.2 via repair`). If the slice
  exceeds ~400 authored lines even after the plan/apply split, invoke the
  owner's pre-approved `size:exception` for this PR rather than fragmenting
  the round-trip test suite across two PRs (design.md: "slice 3 may still
  exceed it at the high end, in which case tasks splits the migration
  function from the `repair` wiring" — that split is what Phases 4/5/6
  already do; a further split inside Phase 6 risks separating
  `apply_repair` from the CLI wiring that exercises it end-to-end, which is
  the more valuable coupling to keep). Open PR 6 (Phase 6: `repair` verb)
  targeting `main`, branched from `main` after PR 5 merges.

**Rollback boundary**: this is the first slice that mutates a real user
bundle. Code rollback is `git revert` of `application/repair.py` and the
`cli/main.py` wiring; any bundle a user already migrated with this code
stays migrated (per-bundle rollback is `git revert` of that bundle's own
`openkos: repair (...)` commit, independent of this code revert).

---

## Phase 7 (PR 7 → `main`, after PR 6 merges): Fixture regeneration, `examples/good-life-demo/`

Design.md "Goldens and fixtures" (fixture half) and Decision 9's product-
dogfooding requirement.

- [ ] **7.1** [IMPL] Create a frozen copy of the CURRENT (v0.1-shaped)
  `examples/good-life-demo/` fixture as a test input — e.g.
  `tests/unit/fixtures/good_life_demo_v01/` or an equivalent location
  consistent with `tests/unit/test_canonical_example.py`'s existing fixture
  conventions (confirm the exact convention during implementation) — so the
  example stays pinned to `repair(v0.1 fixture)` and is reproducible from a
  known input.
- [ ] **7.2** [IMPL] Regenerate `examples/good-life-demo/bundle/**` by
  running `openkos repair` on a SCRATCH copy of the frozen v0.1 fixture
  (7.1) in a throwaway directory, then copying the migrated result over the
  live `examples/good-life-demo/bundle/` tree and committing it — the
  product migrating its own canonical example, per design.md. Confirm
  `concepts/stoicism.md` keeps its hand-written, non-empty `# Citations`
  list untouched (the pinned case for owner decision C), and that the
  migration report's `legacy_citations` line names it.
- [ ] **7.3** [TEST] `tests/unit/test_canonical_example.py` — extend the
  existing byte-identity/conformance assertions with
  `test_good_life_demo_bundle_equals_repair_of_frozen_v01_fixture`:
  running `repair` fresh on a scratch copy of the frozen v0.1 fixture (7.1)
  produces output byte-identical to the committed `examples/good-life-demo/
  bundle/**`. **RED today**: `AssertionError`/fixture missing — this test
  is written before 7.2's regeneration lands, observed RED against the
  still-v0.1 committed fixture, then GREEN once 7.2 lands.
- [ ] **7.4** [TEST] Same file — extend the existing conformance test
  (design.md's "Reference Bundle Full §11 Conformance" requirement,
  already spec-covered by the `ingestion` delta) to additionally assert
  `okf_version: "0.2"` on the root `index.md` and that no concept document
  in the fixture carries a bare `timestamp` or `status: active` key. Covers
  the success-criterion "`examples/good-life-demo/` is v0.2-shaped and
  passes the product's own lint and `status`". **RED today**: `AssertionError`
  against the pre-7.2 fixture.
- [ ] **7.5** [TEST] Same file — add
  `test_good_life_demo_passes_lint_and_status_clean`: run `openkos lint`
  and `openkos status` against `examples/good-life-demo` (as a workspace
  root, matching the existing test's harness) and assert both exit 0 with
  no v0.1-shape-related finding. **RED today**: depends on 7.2's
  regeneration; before it, either fails or is meaningless against v0.1
  content — write it now, observe RED/misleading-pass explicitly noted,
  confirm real GREEN only after 7.2.
- [ ] **7.6** [IMPL] If 7.3-7.5 surface any drift between `repair`'s output
  and the committed fixture (for example a non-deterministic actor string,
  since `repair`'s `generated.by` is always `openkos/legacy` and thus
  deterministic — confirm no other source of nondeterminism exists, such as
  dict/list ordering), fix it in `application/repair.py` or the fixture
  commit, not by loosening the test.

### Phase 7 verification

- [ ] **7.7** Run `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy .` — must be green.
- [ ] **7.8** Run `uv run pytest tests/unit/test_canonical_example.py`
  focused, then `uv run pytest --cov` (unpiped) full suite — must be green,
  90% branch gate held.
- [ ] **7.9** Run `uv run python evals/run_self_tests.py` — must be green.
- [ ] **7.10** Commit as one or more work-unit commits, scope `docs` (the
  fixture is documentation/example content, matching the project's existing
  scope convention for `examples/**` changes — confirm against a prior
  commit touching `examples/good-life-demo/` before finalizing the scope).
  Open PR 7 (Phase 7: fixture regeneration) targeting `main`, branched from
  `main` after PR 6 merges.

**Rollback boundary**: `git revert` of the fixture-regeneration commit
restores the v0.1-shaped `examples/good-life-demo/bundle/**`; the frozen
v0.1 test fixture (7.1) is independently useful evidence either way and may
be kept regardless.

---

## Phase 8 (PR 8 → `main`, after PR 3 merges, parallel-eligible with Phases 4-6): Docs, template, section renumbering

Design.md "Goldens and fixtures" (template half, per this task list's
placement note above) and proposal.md scope item 5. No behavioral test —
this slice is prose. Structural readback (grep-driven verification) is the
proportional check, per the sdd-phase-common Native Checking Contract for a
passive documentation-only edit.

### `src/openkos/templates/agents.md.template`

- [ ] **8.1** [IMPL] Update `src/openkos/templates/agents.md.template` to
  describe the v0.2 field set (`generated`, `status: stable|draft|deprecated`,
  `sources`) instead of `timestamp`/`status: active`, and remove any
  `# Citations` convention it documents. Cross-check against
  `build_source_concept`/`build_concept`'s actual v0.2 output (Phase 2/3)
  so the template never claims a shape the engine does not produce.
- [ ] **8.2** [TEST] `tests/unit/test_canonical_example.py` (or wherever the
  existing "`AGENTS.md` stays byte-identical to a fresh `init`" test lives —
  confirm exact location) — confirm this EXISTING test still passes after
  8.1 (a fresh `init`'s rendered `AGENTS.md` must still equal the template
  render). This is a regression confirmation, not new behavior: if RED, 8.1
  introduced a template/renderer mismatch — fix the template, not the test.

### `examples/good-life-demo/AGENTS.md`

- [ ] **8.3** [IMPL] Update `examples/good-life-demo/AGENTS.md` to match
  8.1's regenerated template output exactly (the existing byte-identity test
  from 8.2 covers a FRESH `init`'s `AGENTS.md`; this file is the example's
  own committed copy and must match by the same rule the example's test
  suite already enforces — confirm via `test_canonical_example.py`'s
  existing assertions, extending them if no such check exists yet for this
  specific file).

### Repository `AGENTS.md`, `docs/*.md`, code comments and messages

- [ ] **8.4** [IMPL] Update the repository root `AGENTS.md` (the file these
  very sdd-tasks instructions operate under): both `§9`-referencing
  sentences ("§9 conformance hold *by construction*" and "OKF is a v0.1
  draft whose §11 permits breaking major bumps") — renumber §9→§11
  (conformance) and correct "v0.1" to "v0.2", with versioning now living at
  §12 rather than §11 (confirm both renumbered references land on the
  correct new section per OKF v0.2's table of contents before writing).
- [ ] **8.5** [IMPL] Update `docs/okf-alignment.md`: renumber every OKF
  section reference per the v0.1→v0.2 table (§9→§11 conformance, §11→§12
  versioning, §6→§8 index files, §7→§9 log files — confirm the complete
  mapping against OKF v0.2's spec before editing), AND fix the pre-existing
  drift where the doc claims the engine emits numbered `[N]` citations under
  a `# Citations` heading (proposal.md decision c: no code path does this;
  describe the actual mechanism — frontmatter `sources`/`provenance`, `##
  Related` for attribution — instead).
- [ ] **8.6** [IMPL] Update `docs/knowledge-object-model.md`: the data-model
  description of a concept's frontmatter fields — `timestamp` → `generated:
  {by, at}`, `status: active` → `status: stable` (and the full `draft |
  stable | deprecated` vocabulary), `sources` as a generated projection of
  `provenance`, no `# Citations` body convention.
- [ ] **8.7** [IMPL] Update `docs/architecture.md`, `docs/cli.md`,
  `docs/glossary.md`, `docs/tech_stack.md`, `docs/roadmap.md`,
  `docs/ideas.md`: grep each for `timestamp`, `status: active`, `# Citations`,
  `§9`, `§6`, `§7`, `§11` (versioning sense) and update every hit found to
  the v0.2 shape/section numbers; leave any hit that is describing PAST
  behavior in a clearly historical/dated context unchanged (docs describe
  the shape, not the diff — no "since #1064" markers per AGENTS.md's own
  rule).
- [ ] **8.8** [IMPL] Grep `src/` for the remaining OKF section citations in
  code comments and user-visible CLI/error messages (design.md estimates
  ~37 total across `docs/`+`src/`+`AGENTS.md`; Phase 1's `ingestion`/
  `workspace-init`/`status`/`next-action-pointer` delta specs already cover
  the SPEC-level renumbering — this task covers the remaining SOURCE-level
  comments/messages the deltas don't reach): `grep -rn '§9\|§6\b\|§7\b'
  src/openkos/` and update each hit to its v0.2 number.
- [ ] **8.9** [IMPL] Living-spec passages a delta cannot reach — edit these
  TWO at archive time, in the archive commit, not in this PR, and record
  that decision here so it is not lost: the `## OKF §9 Conformance Rules
  1-3` heading in `openspec/specs/ingestion/spec.md`, and the `## Non-Goals`
  §9 mention in `openspec/specs/lint/spec.md`. No task in this PR touches
  either file — `sdd-archive` does, when it merges this change's deltas
  into the main specs.
- [ ] **8.10** [TEST] Structural readback: run `grep -rln '§9\|OKF v0\.1'
  docs/ src/openkos/ AGENTS.md` and confirm zero matches remain outside
  historical ADRs (`docs/adr/000*`, `docs/adr/002*` and others that
  deliberately cite v0.1 sections and are append-only, per AGENTS.md's ADR
  policy) and outside the two living-spec passages named in 8.9 (which are
  explicitly deferred to archive time). Any unexpected remaining match is a
  missed renumbering — fix it before this task is considered done.

### Phase 8 verification

- [ ] **8.11** Run `uv run ruff check . && uv run ruff format --check .` —
  must be green (docs-only changes should not affect `mypy`, but run it
  too for safety: `uv run mypy .`).
- [ ] **8.12** Run `uv run pytest --cov` (unpiped) full suite — must be
  green; this slice should touch zero test-covered production code paths
  other than the template-render regression check in 8.2, so no coverage
  regression is expected.
- [ ] **8.13** Run `uv run python evals/run_self_tests.py` — must be green.
- [ ] **8.14** Commit as one or more work-unit commits, scope `docs` (e.g.
  `docs: adopt OKF v0.2 field set and section numbering across docs, the
  template, and code comments`). Open PR 8 (Phase 8: docs + template +
  renumbering) targeting `main`, branched from `main` after PR 3 merges
  (rebase onto `main` again if PRs 4-6 merged first and touched any
  overlapping line — unlikely, since this slice is prose-only).

**Rollback boundary**: revert each doc/template/comment file independently;
no executable behavior depends on this slice, so a partial revert (e.g. of
just `docs/okf-alignment.md`) is always safe.

---

## Success-Criteria Cross-Check

Every bullet in `proposal.md`'s "Success Criteria" section maps to a
verification task above:

| Success criterion | Verified by |
|---|---|
| Fresh `init`+`ingest` yields v0.2 shape, `check_conformance` clean | 2.10, 3.8, Phase 2/3 verification |
| Every reader returns identical results on v0.1/v0.2/mixed bundles | 1.8-1.17 (dual-reader), 6.16 (e2e via round-trip), 7.5 |
| `repair` produces exactly one commit, second run no-op, `generated.at`/`by` exact | 6.13, 6.14, 6.9 |
| merge → repair → unmerge leaves no v0.1-shaped document | 5.10-5.14 closed by 6.16 |
| Parity test and `sources` guard exist and fail under mutation | 3.16, 3.19, 3.22 |
| `examples/good-life-demo/` v0.2-shaped, lint/status clean, no numbered-citation-drift claim | 7.3-7.5, 8.5 |
| `ruff`/`mypy`/`pytest --cov`/eval self-tests green | every phase's verification block |
| ADR-0029 exists, status `Proposed`, indexed | 1.19-1.20 |

## Key Learnings

1. Design.md's slice plan tentatively grouped the template and example
   `AGENTS.md` with fixture regeneration (4a), but both are prose that needs
   no `repair` run, so this task list splits them into the docs slice (8)
   instead, matching the orchestrator's explicit file list and letting that
   slice run in parallel with Phases 4-6.
2. The merge→repair→unmerge round-trip invariant genuinely spans two
   slices: its five schema-version tests are written in Phase 5 (ledger
   migration) but must stay `pytest.mark.skip`d until Phase 6 lands
   `application/repair.py`, then get unskipped and closed there.
3. The unquoted-timestamp preservation rule (Decision 5) requires
   `yaml.compose` over the verbatim frontmatter block rather than
   `datetime.isoformat()`, because PyYAML round-trips an unquoted `Z`-suffixed
   scalar as `+00:00` through a parsed `datetime`.
4. Two living-spec passages (`ingestion` and `lint` spec headings citing
   §9) cannot be reached by any delta in this change and are explicitly
   deferred to the `sdd-archive` commit, recorded as task 8.9 rather than
   silently dropped.
5. Design.md's Decision 2 (scoped Gate 2) already has its spec delta
   written under `entity-resolution-merge`, so Phase 6's task list treats it
   as spec-covered rather than an open decision needing a tasks-phase
   judgment call.
