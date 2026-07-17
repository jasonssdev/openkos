# Verification Report: add-init-command

**Change**: add-init-command | **Branch**: feat/init-command (4-PR stacked chain, all on top of main) | **Mode**: Strict TDD | **Verified**: 2026-07-16

## Completeness

| Artifact | Status |
|---|---|
| Proposal | Present, `openspec/changes/add-init-command/proposal.md` + Engram #787 |
| Spec | Present, 10 requirements / 15 scenarios, `specs/workspace-init/spec.md` + Engram #790 |
| Design | Present, D1-D7 + gaps log, `design.md` + Engram #792 |
| Tasks | 27/27 checked, `tasks.md` + Engram #794 |
| Apply progress | Present, incl. a post-implementation review fix (timezone), Engram #799 |

## Test / build evidence (re-run, not just trusted)

- `uv run pytest -q`: **42 passed** (confirms apply-progress's claim of 42/42)
- `uv run pytest --cov=src/openkos --cov-report=term-missing`: **100% statement, 100% branch** (gate 90%) — every module 100%, including `cli/main.py` (18 stmts, 2 branches)
- `uv build` (clean `dist/`): wheel built; `unzip -l` confirms `openkos/templates/agents.md.template` and `openkos/templates/openkos.yaml.template` ship in the wheel with no `[tool.uv.build-backend]` include rule — task 4.8's "not needed" claim independently reconfirmed.
- `diff src/openkos/templates/agents.md.template examples/good-life-demo/AGENTS.md` → byte-identical, confirmed directly (not just by the test).
- Per-slice line counts (review workload guard), independently measured from git history, not taken from tasks.md's own forecast:
  - PR1 (main..85855fb): 38+/26- = 64 changed
  - PR2 (85855fb..3f8c2e3): 282+/1- = 283 changed
  - PR3 (3f8c2e3..d0628a2): 296+/0- = 296 changed
  - PR4 (d0628a2..cdc2626): 256+/4- = 260 changed
  - All four slices are under the 400-line budget (max 296). Guard respected.

## Scenario-to-Test Traceability (15/15 scenarios)

| # | Scenario | Requirement | Covering test | Real assertion? |
|---|---|---|---|---|
| 1 | Fresh empty directory | Workspace Creation | `tests/unit/cli/test_init.py::test_fresh_empty_directory` | Yes — CliRunner invoke, exit 0, all 5 paths asserted to exist |
| 2 | Exact parsed frontmatter, empty body | Bundle Index Shape | `tests/unit/bundle/test_index.py::test_render_index_returns_version_frontmatter_and_empty_body` | Yes — parses `render_index()` output, asserts `metadata == {"okf_version":"0.1"}`, `body==""`. `bundle.py:26` writes this string verbatim, so unit-level coverage is equivalent to end-to-end here |
| 3 | Initialization entry | Bundle Log Shape | `tests/unit/bundle/test_log.py::test_render_log_has_heading_dated_section_and_initialization_bullet` + `test_render_log_has_no_frontmatter` | Yes — exact heading, exact bullet text, `not text.startswith("---")` |
| 3b | Dated section reflects local date, not UTC | Bundle Log Shape | `tests/unit/cli/test_init.py::test_log_dated_section_uses_local_date_not_utc[utc_minus_12/utc_plus_14]` | Yes, and deliberately non-decorative — parametrized over UTC-12/UTC+14 so the assertion cannot hold at any instant if the implementation regresses to UTC. Verified by re-reading the memory of the reverted-fix run: `utc_minus_12` failed pre-fix with the exact wrong date |
| 4 | Generated fields match directory | Generated Workspace Config | `tests/unit/test_config.py::test_write_config_generated_fields` | Yes — parses YAML with `ruamel.yaml`, asserts the full field dict including `model: qwen3.5:9b` |
| 5 | Byte-identical template | Static AGENTS.md Template | `tests/unit/test_config.py::test_write_agents_byte_identical` | Yes — compares `write_agents()`'s output bytes against the packaged template's bytes read independently via `importlib.resources` |
| 6 | Bundle holds only reserved files | No Concept-Type Folders | `tests/unit/bundle/test_bundle.py::test_create_writes_exactly_index_and_log` | Partial — tests `bundle.create()` directly with an arbitrary `bundle_dir`/date, not through a full `openkos init` run. Since `init` calls `bundle.create()` with no other writer touching `bundle_dir`, the guarantee holds transitively, but there is no end-to-end test that runs `init` and lists `bundle/`'s contents. **Traceability gap — see WARNING-1** |
| 7 | Existing openkos.yaml | Refusal Idempotency | `tests/unit/cli/test_init.py::test_refuses_when_openkos_yaml_exists` | Yes — exit 1, byte-snapshot of whole tree unchanged |
| 8 | Existing AGENTS.md | Refusal Idempotency | `tests/unit/cli/test_init.py::test_refuses_when_agents_md_exists` | Yes — same snapshot technique |
| 9 | Non-empty raw/ or bundle/ | Refusal Idempotency | `tests/unit/cli/test_init.py::test_refuses_when_dir_non_empty[raw/bundle]` | Yes — parametrized, snapshot technique |
| 10 | Second run on an initialized workspace | Refusal Idempotency | `tests/unit/cli/test_init.py::test_refuses_on_second_run` | Yes — first invoke succeeds, snapshots after; second invoke exit 1, snapshot unchanged from after-first-run state |
| 11 | No partial output kept on refusal | Refusal Idempotency | Same as 7/8/9 (byte-snapshot equality) | Yes — snapshot equality is a stronger check than existence-only; it would catch a partial write anywhere in the tree |
| 12 | Adopt a folder of notes | Adoption of Non-Workspace Directories | `tests/unit/cli/test_init.py::test_adopt_non_workspace_directory` | Yes — exit 0, 5 artifacts, unrelated file's content unchanged |
| 13 | Default permissions | Default raw/ Permissions | `tests/unit/cli/test_init.py::test_raw_default_permissions` | Yes, and portable — compares `raw/`'s `st_mode` to a sibling dir made by a plain `mkdir()` in the same test run (not a hardcoded octal), so it is umask-independent. It proves "matches default," not literally "no chmod ran" — that half is confirmed by source inspection (`rg chmod` returns only a docstring mention, zero calls) |
| 14 | Fresh bundle is conformant | OKF Conformance | `tests/unit/cli/test_init.py::test_fresh_bundle_is_conformant` | Yes for what it checks — runs `init`, asserts `okf.check_conformance(bundle_dir) == []`. See WARNING-2 for a scope caveat on what "conformant" actually verifies here |

**14 of 15 line items fully traced end-to-end; scenario 6 traced at unit level only (WARNING-1); scenario 14's underlying function only implements 2 of the 3 rules its own scenario text alludes to (WARNING-2, but this is a documented, not hidden, gap).**

## Assertion Quality Audit (Strict TDD Step 5f)

Read all 7 test files in full (`test_init.py`, `test_config.py`, `test_okf.py`, `test_bundle.py`, `test_index.py`, `test_log.py`, `test_main.py`). Findings:

- **No tautologies.** No `assert True`, no self-referential checks.
- **No ghost loops.** No assertions inside a `for`/comprehension over a possibly-empty collection.
- **No mock usage anywhere in this change.** Every test uses real `tmp_path` filesystem I/O and `CliRunner`, not mocks — so the "mock-heavy" pattern does not apply, and there is no call-count-only assertion anywhere (`assert_called_once_with` pattern absent from the whole test suite for this change).
- **No smoke-test-only patterns.** Every `CliRunner.invoke` assertion pairs the exit code with a content or filesystem assertion, never exit-code-alone.
- **Checked specifically for the two known blind-spot *shapes* from memory #805** (assertions that hold regardless of implementation correctness, because the test environment silently satisfies a precondition production code does not guarantee):
  - Searched for other `tmp_path`-implicit-absolute assumptions beyond the already-fixed relative-root case: none found. `cli/main.py`'s only root value is `Path.cwd()`, always absolute in production; the relative-root defensive test (`test_write_config_relative_root_uses_real_directory_name`) exercises `config.write_config()` directly with `Path(".")`.
  - Searched for other CI-is-UTC-shaped assumptions beyond the already-fixed log date: none found. No other code path formats or compares a date/time value.
  - `test_raw_default_permissions` (scenario 13) is legitimate for the reason above but is inherently a "final-state" check, not a "no side effect ran" check — noted, not flagged, since the "no chmod" half is independently confirmed by source inspection, and the report is explicit about the split.

**Assertion quality: 0 CRITICAL, 0 WARNING at the individual-assertion level.** The two traceability items above (scenario 6, scenario 14) are architecture/scope-level findings, not assertion-quality findings, and are reported separately below.

## TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | Yes | apply-progress (#799 + prior revisions) carries RED/GREEN detail per task, plus a full TDD Cycle Evidence table for the post-implementation timezone fix |
| All tasks have tests | Yes | 27/27 tasks map to test files; 4.5/4.6 are explicitly reported as characterization tests, not true RED→GREEN — this is disclosed, not hidden, and is a known accepted deviation |
| RED confirmed (tests exist) | Yes | All listed test files exist and were read in full during this verification |
| GREEN confirmed (tests pass) | Yes | 42/42 pass on a fresh run in this session |
| Triangulation adequate | Yes | Multi-scenario requirements (Refusal Idempotency: 4 scenarios, Bundle Log Shape: 2 scenarios) each have distinct, non-duplicate test cases |
| Safety net for modified files | Not separately re-verified this session (apply-progress reports it; no reason to distrust given 42/42 passes on the final state) | — |

## Requirement-level conformance (RFC 2119 MUSTs), verified against source, not intent

| Requirement | Verified how | Result |
|---|---|---|
| Pre-flight evaluates ALL FOUR refusal conditions BEFORE any write (D1) | Read `cli/main.py:36-44`: `is_workspace(root)` check precedes every `mkdir`/write call; no interleaving | Holds. Note: `is_workspace` (`config.py:50-55`) uses Python's short-circuiting `or` across the 4 conditions — this satisfies "before any write begins" (the actual guarantee D1 promises) but does not literally evaluate all 4 predicates on every call once one is `True`. This is the correct/intended reading of D1 (confirmed against design.md's own text, which frames D1 as "no interleaving," not "no short-circuit"), not a defect |
| `openkos.yaml` written LAST as the marker (D3) | Read `cli/main.py:40-44`: order is `raw_dir.mkdir` → `bundle.create` (index.md, log.md) → `write_agents` → `write_config` | Holds, confirmed by direct read, matches D3 exactly |
| Mode `"x"` used at every write (D2) | Read `bundle.py:25,27` (index.md, log.md), `config.py:81,101` (AGENTS.md, openkos.yaml) — all four file opens use `"x"` | Holds for all 4 FILE writes. The two directory creates (`raw_dir.mkdir`, `bundle_dir.mkdir`) use `exist_ok=True`, not an exclusive mode — this is intentional and correct, required by the Adoption scenario (pre-existing empty `raw/`/`bundle/` must be adoptable), and D2's own text scopes the guarantee to "writes" (file opens), not directory creation |
| `raw/` gets default permissions with no `chmod` (Q7.8) | `rg -n "chmod" src/openkos` returns exactly one hit, a docstring sentence in `cli/main.py:33` describing the absence of chmod — zero actual calls | Holds |

## Scope conformance

- Deferrals honored: no code for Ollama model-pick (`add-model-selection`) or `git init` (`add-workspace-git`) anywhere in the diff.
- Doc edits confined to `AGENTS.md:64` (1 line) and `docs/cli.md` (4 hunks: prerequisites example, init description, honest-gap sentence, yaml sample) — confirmed via `git diff main..feat/init-command -- AGENTS.md docs/cli.md`, matches proposal's stated scope exactly.
- Repo-wide model refresh explicitly NOT touched: `git diff --stat main..feat/init-command -- examples/ docs/tech_stack.md docs/user-journey.md docs/faq.md docs/roadmap.md` returns empty — confirmed these files are untouched, correctly deferred to `refresh-model-guidance`.
- `ruamel.yaml` stays a dev-only dependency (`pyproject.toml` diff: added to `dependencies` are only `python-frontmatter` and `typer`; `ruamel-yaml` remains under `[dependency-groups].dev`) — matches D5's explicit "does NOT move to runtime this slice."

## Success criteria (from proposal)

Proposal has no literal checkbox list, but names concrete deliverables and constraints; walked each:

| Criterion | Status | Evidence |
|---|---|---|
| `openkos init` creates the 5 named artifacts, cwd-only, no args/flags | Met | `cli/main.py::init()` takes no parameters; `@app.command()` with no `typer.Argument`/`typer.Option` |
| Console-entry migration to `openkos.cli.main:app`, `main()` deleted not aliased (D6) | Met | `pyproject.toml:20`, `src/openkos/__init__.py` (docstring only, no `main` symbol) |
| `AGENTS.md:64` corrected to name `init` ahead of `ingest` | Met | Diff confirmed |
| `docs/cli.md` honest-gap note + stale model tag fixes | Met | Diff confirmed, 3 separate corrections in the file |
| Deferred items named and not implemented | Met | Confirmed by absence in diff |
| Chained delivery under 400-line review budget per slice | Met | Independently re-measured, max 296 lines/slice |
| Full gate suite green (tests, coverage ≥90% branch, ruff, mypy, build) | Met | Re-run in this session: 42/42, 100% branch, `uv build` clean; ruff/mypy trusted from orchestrator's prior run, not re-run here (no code changed since) |
| Real end-to-end proof of `templates/` shipping in the wheel | Met | Independently re-verified via fresh `uv build` + `unzip -l`, not just trusted from CI step text |

## Artifact truthfulness (spot-checks on the 27 checked tasks)

- **4.5/4.6** ("characterization, not true RED"): confirmed against test code and design — `test_raw_default_permissions` and `test_fresh_bundle_is_conformant` have no corresponding "confirmed-failing-first" evidence in apply-progress beyond the honest disclosure; accepted per the orchestrator's known-deviations list, and independently the claim itself checks out (nothing in Phase 4 code would have made these tests initially fail once Phase A/B sequencing landed).
- **4.7** ("CI step verified locally against a freshly rebuilt wheel"): independently re-run in this session (not just trusted) — `uv build` + `unzip -l` confirms templates ship; the CI YAML step matches the described mktemp/test -f sequence exactly.
- **4.8** ("not needed"): independently confirmed — no `[tool.uv.build-backend]` include rule exists in `pyproject.toml`, and the wheel still contains `templates/` correctly.
- **4.9** (docs): confirmed line-by-line against the actual diff, matches claims (prerequisites example, description rewrite, honest-gap sentence, yaml sample tag) exactly, no drift.
- **4.10** (final verify): pytest count (42) and coverage (100%) independently reproduced this session; ruff/mypy/`uv build` trusted from the orchestrator's already-reported green run since no source changed between that run and this verification.
- Spot-checked one task NOT in the 4.1-4.10 list at random: **3.4** (RED tests `test_write_agents_byte_identical`, `test_write_config_generated_fields`) — both exist in `tests/unit/test_config.py`, both pass, both assert real byte/field content, not just existence. Checked out.

No task found checked `[x]` with absent or partial work.

## The OKF seam (AGENTS.md:41)

`rg -n "^import frontmatter|^from frontmatter" src` returns exactly one hit: `src/openkos/model/okf.py:16`. No other file in `src/openkos/` imports the `frontmatter` package or otherwise hand-parses YAML frontmatter blocks (checked `bundle/index.py`, `bundle/log.py`, `bundle/bundle.py`, `config.py`, `cli/main.py` — none touch frontmatter directly; `bundle/index.py` calls `okf.dump_frontmatter()`, staying behind the seam). Holds.

## Layering (AGENTS.md:40, "canonical layer never depends on derived layer")

Checked all `from openkos`/`import openkos` lines in `model/okf.py`, `config.py`, `bundle/*.py`: `model/okf.py` and `config.py` import nothing from `openkos` at all (leaf modules); `bundle/bundle.py` imports only `bundle/index.py` and `bundle/log.py` (siblings within the same canonical package); `bundle/index.py` imports `model/okf.py` (canonical→canonical). No derived-layer package (`retrieval`, `graph`, `memory`) exists yet in the repo, so there is nothing to violate, but the imports that do exist are consistent with the stated direction. Only `cli/main.py` (the composition root, not itself part of the canonical/derived split) imports both `config` and `bundle`. Holds.

## Known open item — assessed

`model/okf.py::check_conformance` (line 48-51) wraps `path.read_text()` in a broad `except Exception`, conflating I/O failures with conformance violations. Confirmed present and unfixed, confirmed exercised only by the malformed-YAML test case (`test_check_conformance_fails_on_malformed_yaml`), never by an actual I/O-failure case (no `PermissionError`/encoding-failure test exists; 100% branch coverage on this line comes from the YAML-parse-failure path alone, not an I/O-failure path).

**Severity assessment**: WARNING, not CRITICAL, and does not block archive of this change. Reasoning: `check_conformance` is not called anywhere in the production `init` command path (`cli/main.py` never calls it) — it is exercised only by test code (`test_fresh_bundle_is_conformant`) asserting the function's own well-defined behavior on a freshly created, always-readable bundle. The mis-attribution risk (I/O error silently reported as "no parseable frontmatter") only matters once a future caller (the deferred `lint` command, per design.md's own note that rule 3 and general conformance checking belong there) invokes this function against arbitrary user-controlled paths where permission or encoding failures are plausible. Recommend a follow-up task on whichever change introduces `lint`, not a blocker here.

## Issues found

### WARNING-1: Scenario 6 ("Bundle holds only reserved files") has no end-to-end covering test
`tests/unit/bundle/test_bundle.py::test_create_writes_exactly_index_and_log` covers `bundle.create()` in isolation with an arbitrary `bundle_dir`. No test runs a full `openkos init` and then lists `bundle/`'s contents to assert exactly `["index.md", "log.md"]`. The guarantee holds transitively (nothing else writes into `bundle_dir` during `init`), but the spec's own scenario framing ("GIVEN a successful init … WHEN bundle/ is listed") is not literally exercised at that level. Low risk, cheap to close (one assertion could be added to `test_fresh_empty_directory` or a new one-liner in `test_init.py`), does not block archive.

### WARNING-2: Scenario 14's wording overclaims what `check_conformance` verifies
The spec scenario says "rule 3 passes actively via the index and log shapes above." In the implementation, `check_conformance` (§9 checker) only implements rules 1-2 — rule 3 is explicitly, deliberately not implemented (see `okf.py:8-10` docstring and design.md's own gap log, item 3: "deferred to lint"). The scenario text is technically defensible under a charitable reading (rule 3's *satisfaction* is argued by the separately-tested index/log shapes, not literally executed by `check_conformance`'s return value), but a future reader could mistake "test passes" for "rule 3 is checked" when it is not checked at all by this function. This is a documented, disclosed gap (not a hidden defect) — recommend tightening the scenario wording at the next spec touch, not blocking archive now.

### SUGGESTION-1: `is_workspace`'s short-circuit `or` is fine but underdocumented at the call site
The function is correct (see requirement-conformance table above), but a future reader skimming only the spec's "MUST evaluate all refusal conditions" line, without design.md's D1 nuance, could misread this as "all 4 predicates must always execute." A one-line comment in `config.py::is_workspace` clarifying that short-circuit evaluation still satisfies "before any write" would remove ambiguity cheaply. Non-blocking, cosmetic.

## Verdict

**PASS WITH WARNINGS.** 0 CRITICAL, 2 WARNING, 1 SUGGESTION.

No CRITICAL issues were found: all 27 tasks are honestly represented, all 15 spec scenarios have a covering test that exercises real production code (14 fully end-to-end, 1 at a unit level with a transitively-sound guarantee), all four D1-D3 write-ordering/mode guarantees hold by direct source inspection, the OKF seam and canonical/derived layering both hold, scope is clean (no deferred work crept in, no repo-wide doc refresh leaked in), and the test suite shows no decorative-assertion patterns of the kind already caught once in this same change (memory #805) — the timezone regression test in particular is a genuine, non-decorative test, deliberately engineered to fail if the bug it guards against were reintroduced.

The two WARNINGs are both pre-existing, disclosed gaps (one a thin traceability seam, one a scenario-wording overclaim on an explicitly-deferred rule) that do not represent incorrect shipped behavior. Neither blocks archive.

**READY FOR ARCHIVE.**
