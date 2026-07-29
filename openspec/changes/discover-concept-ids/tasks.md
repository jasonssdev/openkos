# Tasks: Add a `list` verb so concept ids are discoverable from the CLI

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~860 total (PR1 ~370, PR2 ~490) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 (`bundle/listing.py` + its tests) → PR2 (CLI verb + tests + docs + spec) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Single-pass enumerator + vocabulary resolver, fully unit-tested, nothing imports it yet | PR 1 | `uv run pytest tests/unit/bundle/test_listing.py` | N/A — pure canonical-layer module, no CLI/process surface to exercise until PR2 wires it | Revert PR1 branch; no other module imports `listing.py` |
| 2 | `openkos list` CLI verb wired to the enumerator, docs, spec | PR 2 | `uv run pytest tests/unit/cli/test_list.py` | `uv run openkos list` against a scratch workspace (manual smoke) | Revert PR2 commit(s); `cli/main.py` change is additive (`@app.command`), PR1 stays intact |

Branch plan (stacked-to-main): PR1 branch `feat/list-enumerator` → `main`. PR2 branch `feat/list-cli-verb`, based on PR1's HEAD, targets `feat/list-enumerator` until PR1 merges, then retargets to `main`.

## PR1 — `src/openkos/bundle/listing.py` + `tests/unit/bundle/test_listing.py` (branch `feat/list-enumerator` → `main`)

### Phase 1: Enumerator Foundation (RED)

- [x] 1.1 Create `tests/unit/bundle/test_listing.py`; write failing tests for `BundleObject` field derivation on a `tmp_path` bundle fixture (mirroring `tests/unit/test_lifecycle.py`): `concept_id` from path, `link_dir` structural (including root-level doc → `link_dir == ""`), title whitespace/newline collapse, `""` title on absent title.
- [x] 1.2 Add failing tests for `sensitivity` derivation: valid `SENSITIVITY_ORDER` member passthrough, and `"unknown"` for absent/blank/garbage/non-string values (spec: sensitivity governs LLM-send gating only, never display — Requirement "Confidential Titles Are Printed in Full").
- [x] 1.3 Add failing tests for `readable=False` rows: inject a `DocScan` with `read_error`/`parse_error` set (pattern from `tests/unit/test_sensitivity.py:103-114`); assert row still has `concept_id`/`link_dir` from path, `title=""`, `sensitivity="unknown"`.

### Phase 2: Enumerator Implementation (GREEN)

- [x] 2.1 Create `src/openkos/bundle/listing.py` with `BundleObject` frozen dataclass (`concept_id`, `link_dir`, `title`, `sensitivity`, `status`, `readable`) per design D2; implement `list_objects(bundle_dir) -> list[BundleObject]` with exactly one `for scan in okf._iter_docs(bundle_dir):` loop.
- [x] 2.2 Implement id/link_dir derivation structurally from path (never from frontmatter `type`); add the inline comment naming the two other spellings (`lifecycle.py:70`, `sensitivity.py:116`) and the extraction trigger, per D2.
- [x] 2.3 Implement title collapse (`" ".join(str(raw).split())`) and sensitivity derivation (valid `SENSITIVITY_ORDER` member else `"unknown"`); run Phase 1 tests to GREEN.

### Phase 3: Single-Walk Enforcement (RED then GREEN)

- [x] 3.1 Add failing test: non-generator counting wrapper (`_counting_iter_docs`, plain function, NOT `yield from` — records the call at call time) monkeypatched onto `okf._iter_docs`; call `list_objects(bundle_dir)`; assert `len(calls) == 1`.
- [x] 3.2 Confirm `list_objects` already satisfies 3.1 (no production change expected — this test locks the structural constraint from D3); if it fails, fix `list_objects` to remove any second walk.

### Phase 4: Status Derivation and Drift Guard (RED then GREEN)

- [x] 4.1 Add failing tests for in-pass status: build bundle fixtures with own-deprecated, superseded, self-superseding (dropped), and cyclic-supersession objects; assert `status` per D4's rule (own `status == "deprecated"` OR id in `superseded`).
- [x] 4.2 Add failing test for malformed `relations:` frontmatter: assert `okf.decode_relations` `ValueError` is caught, yields no edges, and does not crash the walk.
- [x] 4.3 Add the drift-guard test: `{r.concept_id for r in rows if r.status == "deprecated"} == lifecycle.deprecated_concept_ids(bundle_dir) & {r.concept_id for r in rows}` (intersected per D4's rationale — `lifecycle` may name ids with no on-disk file).
- [x] 4.4 Implement `supersedes` collection (`okf.decode_relations(meta)` in `try/except ValueError`, dropping self-edges) and post-loop `superseded` set computation inside `list_objects`; run Phase 4 tests to GREEN.

### Phase 5: Vocabulary Resolver (RED then GREEN)

- [x] 5.1 Add failing parametrized tests for `resolve_link_dir(raw)`: all 10 canonical `link_dir` values, all 10 `REGISTRY.name` values (including `Source` — the case `TYPE_TO_LINK_DIR` would silently break), `""`, wrong case (`People`, `person`), and an unknown value → `None`.
- [x] 5.2 Implement `resolve_link_dir` and its backing maps built from `REGISTRY` directly (`_LINK_DIRS`, `_NAME_TO_LINK_DIR`) — explicitly NOT from `types.TYPE_TO_LINK_DIR` (design D7 gotcha: that map is `llm_classifiable`-only and omits `Source`); run Phase 5 tests to GREEN.

### Phase 6: Remaining Coverage (RED then GREEN)

- [x] 6.1 Add failing test: empty bundle → `list_objects` returns `[]`.
- [x] 6.2 Add failing test: alphabetical order of returned rows (id-sorted, matching `_iter_docs`'s own order).
- [x] 6.3 Run full `test_listing.py` suite GREEN; run `uv run pytest --cov` and confirm `listing.py` branch coverage ≥ 90%; add any missing-branch tests named by the coverage report.

### Phase 7: PR1 Cleanup

- [x] 7.1 Run `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .` on `listing.py`; fix findings.
- [x] 7.2 Open PR1 on branch `feat/list-enumerator` targeting `main`; verify diff is `bundle/listing.py` + `test_listing.py` only.

## PR2 — CLI verb + docs + spec (branch `feat/list-cli-verb`, based on PR1 HEAD, targets `feat/list-enumerator` until PR1 merges)

### Phase 8: Argument-Refusal Ladder (RED)

- [x] 8.1 Create `tests/unit/cli/test_list.py` using `CliRunner` on `tests/unit/cli/test_duplicates.py`'s fixture shape. Write failing test: `openkos list bogus-type` outside a workspace (no `require_workspace` call reached) exits non-zero, error names the unrecognized type, error enumerates only canonical `link_dir` names, no raw traceback — spec Scenario "Bad argument outside a workspace reports the argument".
- [x] 8.2 Add failing test: `openkos list --limit 0` (and separately `--limit -1`) exits non-zero, prints a clear error, prints no rows — before any workspace/disk access.
- [x] 8.3 Add failing test: `openkos list` outside a workspace with valid arguments exits non-zero via `require_workspace`, clear error, no raw traceback — spec Scenario "Run outside a workspace" (must come AFTER 8.1/8.2 pass, confirming refusal ordering).

### Phase 9: CLI Command Skeleton (GREEN)

- [x] 9.1 In `src/openkos/cli/main.py`, add `@app.command("list")` on `def list_objects_cmd(...)` (not named `list` — shadows the builtin, per `set-sensitivity`/`set-volatility` precedent) with optional positional `TYPE`, `--limit` (default 50), `--all` flags.
- [x] 9.2 Implement the refusal ladder: `resolve_link_dir(TYPE)` first (unknown → stderr refusal, exit 1, no disk access), then `--limit` validation (`<= 0` and not `--all` → stderr refusal, exit 1), then `config.require_workspace(cwd)` (failure → stderr refusal, exit 1) — mirroring `set-volatility` (`cli/main.py:3545-3563`); run Phase 8 tests to GREEN.

### Phase 10: Single-Walk and Lifecycle-Isolation Guards (RED then GREEN)

- [x] 10.1 Add failing test: counting wrapper (same non-generator pattern as PR1) monkeypatched onto `okf._iter_docs`; `CliRunner` invokes `list`; assert `len(calls) == 1` — spec Requirement "Exactly One Bundle Walk", Scenario "Single walk regardless of filter" (`list people --limit 5`).
- [x] 10.2 Add failing test: `monkeypatch.setattr(lifecycle, "deprecated_concept_ids", _fail)` where `_fail` raises `AssertionError`; command still exits 0 — names the proposal's explicit prohibition with a legible failure message (design D3).
- [x] 10.3 Wire `list_objects_cmd` to call `listing.list_objects(layout.bundle_dir)` exactly once and nothing else that touches disk; run Phase 10 tests to GREEN.

### Phase 11: Filtering, Limiting, Formatting (RED then GREEN)

- [x] 11.1 Add failing tests: filter by canonical `link_dir` (`list people`), filter by `REGISTRY.name` alias (`list Person`) produces identical rows, filter with zero matches.
- [x] 11.2 Add failing tests: default limit 50 with truncation footer reporting shown/total (bundle with 412 matches); `--limit N` truncates with footer; `--all` prints every row with no footer.
- [x] 11.3 Add failing test: column layout — exactly `ID`, `SENSITIVITY`, `STATUS`, `TITLE` in order, `ljust`-aligned per design D6, widths computed over shown rows only.
- [x] 11.4 Implement in-memory filter (by resolved `link_dir`) → slice to limit/`--all` → width computation → `typer.echo` rows → truncation footer, per design Data Flow; run Phase 11 tests to GREEN.

### Phase 12: Mandatory Spec Scenarios (RED then GREEN)

- [x] 12.1 Add failing test: confidential title printed in full — bundle with `sensitivity: confidential` concept titled "Jane's Medical History"; assert the row prints the complete unredacted title, byte-identical in shape to a public object's row (spec Requirement "Confidential Titles Are Printed in Full").
- [x] 12.2 Add failing test: deprecated object shown by default with `STATUS = deprecated`, no flag required.
- [x] 12.3 Add failing test: empty bundle prints a friendly "no objects" message and exits 0.
- [x] 12.4 Add failing test: bundle with one well-formed object and one document with unparseable frontmatter — both rows printed, broken document shows `(unreadable)` title marker, command exits 0, no raw traceback.
- [x] 12.5 Add failing test: `(untitled)` marker for a readable document with no title (distinct from `(unreadable)`).
- [x] 12.6 Implement/confirm the `(untitled)`/`(unreadable)` rendering branch and empty-state early return in `list_objects_cmd`; run Phase 12 tests to GREEN.

### Phase 13: Full Suite and Coverage

- [x] 13.1 Run `uv run pytest tests/unit/cli/test_list.py` full suite GREEN.
- [x] 13.2 Run `uv run pytest --cov` and confirm branch coverage ≥ 90% on `cli/main.py`'s new command and `listing.py`; add tests for any uncovered branch named by the report (expected risk areas: the `--limit` negative-vs-zero branches, `--all` vs default-limit branch, alias-vs-canonical resolution branch, readable-vs-unreadable render branch).
- [x] 13.3 Run `uv run ruff check . && uv run ruff format --check .` and `uv run mypy .`; fix findings.

### Phase 14: Docs and Cleanup

- [x] 14.1 Update `docs/cli.md`: add `### openkos list` section beside `### openkos status`, documenting `TYPE`, `--limit`, `--all`, column layout, and the deprecated/confidential visibility rules.
- [x] 14.2 Confirm `openspec/changes/discover-concept-ids/specs/list-command/spec.md` reflects the final argument-refusal-before-workspace ladder (already current per orchestrator amendment — no edit expected, verify only).
- [x] 14.3 Open PR2 on branch `feat/list-cli-verb` targeting `feat/list-enumerator`; retarget to `main` once PR1 merges; verify diff is `cli/main.py`, `test_list.py`, `docs/cli.md`, spec only.
