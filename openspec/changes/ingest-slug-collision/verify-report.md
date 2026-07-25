# Verify Report: ingest-slug-collision (#131)

**Change**: ingest-slug-collision
**Mode**: Full artifacts (proposal, delta spec, design, tasks, apply-progress) — Strict TDD active
**Verdict**: PASS

## Scope Check

`git diff main --stat` shows exactly the expected three files modified:
`src/openkos/cli/main.py`, `tests/unit/cli/test_ingest.py`,
`tests/unit/cli/test_duplicates.py` (395 insertions / 8 deletions total),
plus the untracked `openspec/changes/ingest-slug-collision/` SDD artifact
directory. `git diff main --stat -- openspec/specs/` is empty — the main
spec tree is confirmed untouched, matching the deferred task 4.4 rationale
(main-spec merge is `sdd-archive`'s job).

## Task Completeness

17/18 tasks checked `[x]` on disk in `tasks.md`, matching the apply-progress
report. Task 4.4 (main-spec sync) is explicitly unchecked with an inline
deferral rationale — this is a legitimate scope boundary, not a gap: it is
`sdd-archive`'s responsibility per the orchestrator's process rule, and the
delta spec file is already present and complete at
`openspec/changes/ingest-slug-collision/specs/ingestion/spec.md`.

## Requirements / Scenario Compliance Matrix

Delta spec: 4 requirements (2 MODIFIED, 2 ADDED), 13 scenarios total.

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Bounded, Deduplicated Derived-Object Staging | More than cap of 5 is bounded | `test_batch_of_five_all_staged_no_second_cap_in_main` (pre-existing, unmodified) | PASS |
| " | Two objects in one reply collide on slug | `test_in_batch_slug_collision_keeps_first_drops_second` (pre-existing) | PASS |
| " | Same-source slug collision is create-only no-op | `test_idempotent_reingest_leaves_existing_derived_object_untouched` (pre-existing) + covered by new family-scan path | PASS |
| " | First foreign-source collision writes `<slug>` | `test_noncolliding_candidate_written_without_suffix` | PASS |
| " | Second, different-source, same-title writes `<slug>-2` | `test_foreign_collision_writes_slug_2` | PASS |
| " | Third, different-source, same-title writes `<slug>-3` | `test_third_foreign_source_writes_slug_3` | PASS |
| Idempotent Re-Ingest Reconciles Derived Objects Per Slug | Re-ingest leaves existing derived object untouched | `test_idempotent_reingest_leaves_existing_derived_object_untouched` (+5 type variants, pre-existing) | PASS |
| " | Re-ingest inserts slug-missing object, skips existing | `test_reingest_reconciles_per_slug_skips_existing_inserts_new` (pre-existing) | PASS |
| " | Re-ingesting first source spawns no new file | `test_reingest_owner_of_base_slug_is_noop` | PASS |
| " | **CRITICAL**: re-ingesting owner of `<slug>-2` does not spawn `-3` | `test_reingest_owner_of_slug_2_does_not_spawn_slug_3` | PASS |
| " | Byte-identical raw re-ingest short-circuits (D2) | `test_byte_identical_reingest_short_circuit_still_holds` | PASS |
| Durable Disambiguation Audit Log | Disambiguating ingest recorded and surfaced | `test_disambiguation_writes_audit_log_entry` + `test_status_surfaces_disambiguation_entry` | PASS |
| Disambiguated Concepts Remain Resolvable | Disambiguated pair forms a candidate group | `test_disambiguated_pair_forms_candidate_group` (`test_duplicates.py`) | PASS |

Supporting/guard tests also verified:
- `test_family_regex_excludes_base_word_slug` — false-positive guard for `^{base}(-\d+)?$` vs `<base>-word` — PASS
- `test_family_scan_skips_malformed_frontmatter_member` — graceful degrade, no crash — PASS

Requirements: 4/4. Scenarios: 13/13 (all covering tests passed at runtime; no scenario relies on static inspection alone).

## Idempotency Predicate — Source Inspection

Read `_collision_family`, `_family_owns_source`, `_first_free_disambiguated_slug`
directly in `src/openkos/cli/main.py`:

- `_collision_family`: regex `^{re.escape(base_slug)}(?:-(\d+))?$` matched
  against `path.stem`, sorted ascending by `N` (bare slug = 0). Confirmed
  NOT a glob — excludes `<base>-word.md` per test.
- `_family_owns_source`: scans every family member's `provenance` for
  `sources/<source_slug>`; catches `OSError`/`UnicodeDecodeError` and a
  broad `except Exception` (documented `# noqa: S112`) per member so one
  malformed file never aborts the scan.
- `_first_free_disambiguated_slug`: `taken = {family stems} | reserved`;
  linear scan from `n=2` while `f"{base}-{n}"` in `taken`. `family` is
  always finite (bounded by files actually on disk) and `reserved` is
  bounded by extractions in one batch (already capped at 5) — the loop is
  guaranteed to terminate in at most `len(family) + len(reserved) + 1`
  iterations, no infinite-loop risk.
- Slug reservation into `seen_slugs` happens only AFTER `build_concept`
  validation succeeds (line ~1140), confirming the spec requirement "a
  dropped or redirected candidate never reserves a slug for a later one."
- Re-ingest termination: because `_family_owns_source` scans the WHOLE
  family (not just the base slug), a source that previously won `<slug>-2`
  is recognized there on re-ingest and short-circuits to no-op — this is
  exactly what `test_reingest_owner_of_slug_2_does_not_spawn_slug_3`
  proves at runtime, not just by design inspection.

No infinite loop, no re-spawn on re-ingest — predicate confirmed correct
both by source inspection and by the passing critical test.

## Resolution Code Unchanged

`git diff main --stat` includes no entries for `src/openkos/resolution/candidates.py`,
`src/openkos/resolution/adjudication.py`, or `src/openkos/bundle/merge.py`.
Confirmed byte-unchanged. `test_disambiguated_pair_forms_candidate_group`
passes against the new `<slug>`/`<slug>-2` fixture shape with zero changes
to `find_candidates`/`adjudicate`/`merge`.

## Quality Gate (independently re-run, not trusted from apply-progress)

| Command | Exit Code | Result |
|---|---|---|
| `uv run pytest` | 0 | 2141 passed in 109.21s |
| `uv run ruff check .` | 0 | All checks passed! |
| `uv run ruff format --check .` | 0 | 134 files already formatted |
| `uv run mypy .` | 0 | Success: no issues found in 134 source files |

Targeted re-runs (verbose) of all 12 new tests plus adjacent pre-existing
ingest/reingest tests: 26/26 passed in `test_ingest.py` subset,
1/1 passed in `test_duplicates.py` subset.

`test_output_hash` (sha256 of full `uv run pytest` stdout+stderr):
`f1e3c1431b7a918159da9267cd5068af58aa7adde1eb5f0b622bf6b682757961`

## Findings

**CRITICAL**: None.

**WARNING**: None.

**SUGGESTION**: None.

## Verdict

**PASS.** All 4 delta-spec requirements and 13 scenarios have passing
covering tests independently re-run in this session. Quality gate is clean
(pytest, ruff, mypy all exit 0). Scope is correctly bounded to the three
expected files; `openspec/specs/` is untouched as required, with the sync
correctly deferred to `sdd-archive` (task 4.4). Resolution code
(`find_candidates`/`adjudicate`/`merge`) confirmed unchanged. The critical
idempotency predicate (re-ingest of `<slug>-2` owner does not spawn `-3`)
is proven correct both by source inspection (termination guarantee) and by
a passing runtime test.

Ready for `sdd-archive`.

---

```yaml
schema: gentle-ai.verify-result/v1
verdict: PASS
blockers: []
critical_findings: []
warning_findings: []
suggestion_findings: []
requirements: "4/4"
scenarios: "13/13"
test_command: "uv run pytest"
test_exit_code: 0
test_output_hash: "sha256:f1e3c1431b7a918159da9267cd5068af58aa7adde1eb5f0b622bf6b682757961"
lint_command: "uv run ruff check . && uv run ruff format --check ."
lint_exit_code: 0
typecheck_command: "uv run mypy ."
typecheck_exit_code: 0
```
