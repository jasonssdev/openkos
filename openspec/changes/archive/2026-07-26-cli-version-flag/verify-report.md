```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:5b6ca343bdb294a467f76c192d51d955c75deca4a6cd9580b840dd937374f405
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 8/8
scenarios: 13/13
test_command: uv run pytest
test_exit_code: 0
test_output_hash: sha256:c15677b7c1870b5ae09b5ed5b6e91936bca81fd6fdd30b165522c6f72b254f82
build_command: uv run ruff check . && uv run ruff format --check . && uv run mypy .
build_exit_code: 0
build_output_hash: sha256:c028c2b916869a306e6c5e3b9656d0fae094dd2afb3689f8fe8158bd1200ba92
```

<!--
The numerators were briefly 7/8 and 12/13: the doctor-command scenario
"Other applicable checks still run despite the malformed model" names four
checks, and its (pre-existing, #128-era) regression test asserted only three.
Rather than archive with a knowingly partial scenario -- the requirement it
belongs to is MODIFIED by this change's delta spec -- the gap was closed: the
test now stubs `probe_vec_loadable` and asserts the fourth check. Both
numerators are therefore honestly complete.

verdict is pass_with_warnings, not pass, because the body still records one
WARNING (no `apply-progress` artifact). The envelope is the routing input, so
a bare `pass` would strip that signal from any consumer that reads only this
block. That warning is a process-recording gap, not a code defect.
-->

# Verification Report: cli-version-flag

**Change**: cli-version-flag (closes #181, PR #182, merged to `main` as `ab914a9`)
**Mode**: hybrid (OpenSpec files + Engram), full artifact set (proposal, specs x2, design, tasks) — no `apply-progress` artifact exists.
**Strict TDD**: active, `strict-tdd-verify.md` loaded.

## Completeness

All 26 tasks in `tasks.md` are checked `[x]` across Phases 1-6 (11 + 5 + 1 + 4 + 1 + 4). Independent inspection confirms every task was genuinely delivered:

- Phase 1 (RED tests, 1.1-1.11): all 8 `--version` tests exist in `tests/unit/test_main.py:53-175`; the doctor banner test exists at `tests/unit/cli/test_doctor.py:844-868`; the malformed-model regression test (`test_doctor_non_str_model_fails_and_exits_one_without_traceback`, `test_doctor.py:179-219`) pre-exists and covers the MODIFIED requirement's scenarios. It was
extended during verification to stub `probe_vec_loadable` and assert `[PASS] Vector extension
loadable`, closing a pre-existing gap that had left one scenario partially pinned.
- Phase 2 (GREEN, 2.1-2.5): `src/openkos/cli/main.py:13-14` (alias import), `:108-127` (`_version_line`/`_version_callback`), `:137-143` (`--version` option), `:6222` (banner call). All Phase-1 tests pass.
- Phase 3 (CI, 3.1): `.github/workflows/ci.yml:117-141` extends the wheel-smoke step; the shipped version goes beyond the design's plan (see Design Conformance, deviation D-CI below).
- Phase 4 (docs, 4.1-4.4): `docs/cli.md:26-28` (Global options section), `docs/cli.md:325-342` (ten-check enumeration, corrected), `docs/testing.md:169` (`--version` replaces `--help | head -3`), `docs/testing.md:172-173` (old #181 callout replaced).
- Phase 5 (changelog, 5.1): `CHANGELOG.md:15-29`, `[Unreleased]` gains both an `### Added` entry and a `### Fixed` entry for the doc-drift correction, citing #181.
- Phase 6 (verification, 6.1-6.4): all four commands independently re-run below with matching results.

No unchecked tasks. No CRITICAL from the completeness dimension.

## Build / Test / Coverage Evidence (independently re-run, not taken on faith)

| Command | Result |
|---|---|
| `uv run pytest -q` | `2166 passed in 111.03s` |
| `uv run pytest tests/unit/test_main.py tests/unit/cli/test_doctor.py -q` | `45 passed in 4.93s` |
| `uv run pytest -q --cov=src/openkos --cov-branch --cov-report=term-missing` | `TOTAL 97%` branch; `src/openkos/cli/main.py` 96% (1918 stmts / 87 missing, 614 branches / 23 missing). None of the missing line/branch ranges fall inside the new code (`:108-127`, `:6222`); the uncovered set is entirely pre-existing and unrelated to this change. |
| `uv run mypy .` | `Success: no issues found in 134 source files` |
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `134 files already formatted` |
| `uv build && uv run --isolated --no-project --with dist/openkos-0.2.0-py3-none-any.whl openkos --version` | `openkos 0.2.0` |
| `... openkos --help \| grep -i version` | `--version  Show the installed openkos version and exit.` |
| CI checks on merge commit `ab914a9` (`gh api .../check-runs`) | `test (3.12/3.13/3.14)`, `quality (ruff, mypy)`, `build (uv build + wheel smoke test)` — all `success` |

The environment coverage gotcha (`--cov=openkos` reports 0.00% under this venv's flat editable install; must use `--cov=src/openkos`) reproduced as described and is confirmed to be an environment quirk, not a change defect.

## Spec Compliance Matrix — `specs/cli-version/spec.md`

| Requirement | Scenario | Test(s) | Status |
|---|---|---|---|
| Version Flag Prints Exact Output And Exits Zero | Bare version flag | `test_version_flag_prints_version_and_exits_zero` (`test_main.py:53`) + `test_version_flag_matches_installed_distribution_metadata` (`:63`, exact-string match) + `test_version_flag_prints_exactly_one_line` (`:97`, single stdout line, empty stderr) | PASS — 3 tests jointly pin "exactly" |
| Version Read From Installed Distribution Metadata | Version matches installed dist-info | `test_version_flag_matches_installed_distribution_metadata` (`:63-69`) | PASS |
| Eager Evaluation Short-Circuits | Works outside any workspace | `test_version_flag_works_outside_workspace` (`:72-83`, `monkeypatch.chdir(tmp_path)`) | PASS |
| Eager Evaluation Short-Circuits | Short-circuits a combined subcommand invocation | `test_version_flag_short_circuits_combined_subcommand_invocation` (`:86-94`, asserts `"checking environment at"` absent from stdout) | PASS |
| Unknown Version Fallback On Missing Package Metadata | Package metadata unavailable | `test_version_flag_falls_back_when_distribution_missing` (`:107-124`) — monkeypatches the module-local `_pkg_version` alias (not the stdlib origin), asserts exit 0, exact `openkos unknown`, plus a direct `_version_line()` unit assertion | PASS — this is the hard branch required by the 90% branch gate; independently confirmed covered in the coverage run |
| Version Resolution Is Read-Only | No side effects from version resolution | `test_version_flag_has_no_side_effects` (`:127-144`) — asserts `OllamaClient` construction raises if attempted, and a `tmp_path` filesystem snapshot is unchanged before/after | PASS (proxy for "no network call": `OllamaClient` is the only network-capable dependency the CLI touches on this path; reasonable and sufficient) |
| Version Flag Is Discoverable In Help Text | Help text lists the flag | `test_version_flag_is_discoverable_in_help_text` (`:147-175`) — ANSI-stripped, `COLUMNS=80` pinned; asserts BOTH the `--version` entry and its description string on the same rendered line | PASS |

6/6 requirements, 7/7 scenarios pinned by a passing test. No CRITICAL.
(The table lists 7 rows for 6 requirements because `Eager Evaluation
Short-Circuits Before Any Subcommand Or Workspace Resolution` has two
scenarios and therefore two rows.)

## Spec Compliance Matrix — `specs/doctor-command/spec.md`

| Requirement | Scenario | Test(s) | Status |
|---|---|---|---|
| Doctor Prints A Leading Version Banner (ADDED) | Banner precedes all check lines | `test_doctor_prints_version_banner_first` (`test_doctor.py:844-868`) — `lines[0]` regex `^openkos \d+\.\d+\.\d+`, `lines[1]` equals the existing header | PASS |
| Doctor Prints A Leading Version Banner (ADDED) | Check count and exit code are unaffected by the banner | Same test — `result.exit_code == 0`, `stdout.count("[PASS]") == 10` | PASS |
| Doctor Never Raises On A Malformed Model Config (MODIFIED) | Non-str model value fails cleanly instead of crashing | `test_doctor_non_str_model_fails_and_exits_one_without_traceback` (`:179-219`) — `isinstance(result.exception, SystemExit)`, `"Traceback" not in result.stdout` | PASS |
| Doctor Never Raises On A Malformed Model Config (MODIFIED) | Malformed model reports FAIL with actionable remediation | Same test — `"[FAIL] Config valid"`, `"  -> fix openkos.yaml"` | PASS |
| Doctor Never Raises On A Malformed Model Config (MODIFIED) | Other applicable checks still run despite the malformed model | Same test — asserts `[PASS]` for all four checks the scenario names (Ollama reachable, Embedding model installed, Bundle readable, Vector extension loadable), plus Model installed, which the scenario does not name. The Vector-extension assertion was added during verification with `probe_vec_loadable` stubbed, so it is not environment-dependent | PASS |
| Doctor Never Raises On A Malformed Model Config (MODIFIED) | Check-line shape is unchanged; only the leading banner is new | Covered jointly by `test_doctor_non_str_model_fails_and_exits_one_without_traceback` (shape) and `test_doctor_prints_version_banner_first` (banner-is-the-only-new-line) | PASS |

6/6 scenarios fully pinned. No CRITICAL. One scenario was partially pinned when verification began (a #128-era regression test omitted the vector-extension assertion); it was closed rather than waived, since the requirement it belongs to is MODIFIED by this change's delta spec.

## Design Conformance

| Decision | Shipped code | Match |
|---|---|---|
| D1 — `_version_line()` returns the full line, single shared helper | `main.py:108-119`, consumed at `:126` (callback) and `:6222` (doctor) | Match |
| D2 — module-local alias import `_pkg_version`, not `metadata.version` | `main.py:13-14`; monkeypatch target is `openkos.cli.main._pkg_version` in tests | Match |
| D3 — `--version` only, no `-V` | `grep '"-[a-zA-Z]"' src/openkos/cli/main.py` → 0 matches (re-verified) | Match |
| D4 — doctor banner is the bare line, first, no 10th `CheckResult` | `main.py:6219-6222`, outside `results`, comment explicit about it | Match |
| D5 — `is_eager=True` alone, no `expose_value` | `main.py:137-143`; `grep expose_value` → 0 matches | Match |
| Placement — new symbols strictly between `_LOCK_CONTENTION_MSG` (`:102-105`) and `@app.callback()` (`:130`) | `_version_line`/`_version_callback` at `:108-127`, directly between them | Match |
| No new module, no new dependency | Confirmed — one file touched in `src/`, `importlib.metadata` is stdlib | Match |
| No workspace resolution on the read path | `callback()` body never calls `require_workspace`; `--version` test asserts it works from `tmp_path` outside any workspace | Match |
| No second version constant (`__version__`) in `src/` | `grep -rn __version__ src/` → 0 matches | Match |

**One deviation (NOTE — not a WARNING and not a spec break; deliberately excluded from the WARNING count below):** design.md's CI section (lines 196-206) specified extending the wheel-smoke step to just *also run* `openkos --version` with no assertion beyond exit status. The shipped `.github/workflows/ci.yml:117-141` goes further (the comparison and its failure
path are at `:137-141`): it resolves the wheel glob to exactly one file (guards against a stale second wheel), and compares the printed version string against the version embedded in the built wheel's own filename — not just checking exit 0. This is a stricter, better test than the design specified (the PR body explains the exit-status-only version couldn't fail on a metadata regression, since the `openkos unknown` fallback also exits 0). Confirmed correct and matches CI's actual passing run. This is an improvement discovered during review, not a scope or spec violation — no doctor/`--version` output-contract requirement is affected.

## Task Amendments vs Shipped State

- **Task 4.2** (amended during review to add the doc-drift correction): shipped `docs/cli.md:325-342` lists all ten checks including `Workspace vector index present` (previously missing per the proposal's own claim); `docs/testing.md:207-218` table also lists all ten. Confirmed both match code (`doctor` emits exactly ten `CheckResult`s, verified via `test_doctor_all_healthy_exits_zero`, `stdout.count("[PASS]") == 10`).
- **Proposal success criteria** (all `[x]`): all 5 independently reproduced — `--version` prints and exits 0 outside a workspace (confirmed), works against the isolated wheel in CI (confirmed, CI green), `doctor` still shows ten checks with the banner (confirmed), `PackageNotFoundError` fallback covered by a test (confirmed, `test_version_flag_falls_back_when_distribution_missing`), and `docs/testing.md`'s known-issues callout is gone with `docs/roadmap.md:89` reporting #181 as closed (confirmed at `docs/roadmap.md:89` and `docs/testing.md:668`).

## Anything Claimed But Not Delivered / Delivered But Undocumented

None found. The one extra behavior beyond the design (the wheel-filename comparison in CI) is documented in the PR body and is a strictly stronger test than what was designed, not an undocumented surprise.

## TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | ⚠️ Not available | No `apply-progress` artifact exists for this change (apply ran in a prior session that did not write one) — cannot cross-check a RED/GREEN cycle table against actual execution history per the strict-TDD protocol. |
| All tasks have tests | ✅ | `tasks.md` Phase 1 lists 8 `--version` tests + 2 doctor tests, all present in the codebase (see Completeness section). |
| RED confirmed (tests exist) | ✅ | All 10 test functions verified present and passing on this run. |
| GREEN confirmed (tests pass) | ✅ | `45 passed` on the focused run, `2166 passed` on the full suite. |
| Triangulation adequate | ✅ | The `Version Flag Prints Exact Output` requirement alone is triangulated by 3 distinct tests asserting different aspects (regex match, exact string, single-line/no-stderr); the fallback branch gets both a CLI-level and a direct-unit-level assertion. |
| Safety Net for modified files | N/A | Cannot be verified without `apply-progress`; `test_doctor.py`'s other 33 pre-existing tests all still pass (re-run above), which is consistent with — but not proof of — a safety-net run before modification. |

**TDD Compliance**: 4/6 checks directly confirmed; 1 unavailable (no apply-progress artifact) and 1 not independently provable without it. This is a **process-recording gap, not a code-quality defect** — actual runtime evidence (tests exist, pass, and are behaviorally meaningful — see Assertion Quality below) substitutes for the missing table. Flagged as **WARNING**, not CRITICAL, given the strength of the independently-reproduced runtime evidence; the orchestrator/user should ensure `apply-progress` is written in future sessions so this gap doesn't recur.

## Assertion Quality

Reviewed all 10 test functions added/relied upon by this change (`test_main.py:53-175`, `test_doctor.py:844-868`, plus `test_doctor.py:179-219` regression coverage). No tautologies, no orphan empty-collection checks, no ghost loops, no assertion-without-production-call, no smoke-test-only patterns. Each test exercises the real `CliRunner().invoke(app, ...)` path and asserts specific, non-trivial output (exact strings, regex-anchored version format, single-line counts, exit codes, filesystem snapshots). Mock-to-assertion ratio is low for the `--version` tests (0-1 `monkeypatch.setattr` calls
against 1-3 assertions). `test_doctor_prints_version_banner_first` is the exception at 4
`monkeypatch.setattr` calls against 4 assertions -- unavoidable, since `doctor` probes Ollama,
the vector extension, and both git binaries, all of which must be stubbed to reach a
deterministic all-PASS state.

**Assertion quality**: ✅ All assertions verify real behavior.

**Accepted tradeoff (recorded, not a defect).** `test_version_flag_is_discoverable_in_help_text`
asserts the verbatim help sentence on the same rendered line as `--version`, and uses a
single-element unpacking to pin that exactly one such line exists. This couples the test to
Rich's cell wrapping at the pinned `COLUMNS=80` and to the production help copy, so rewording
that copy or adding a longer top-level option name will fail it. That is deliberate: the spec
clause is about the entry *describing* the flag, and a silent copy change that stopped
describing it is exactly what this assertion exists to catch. The unpacking surfaces a
rendering change as `ValueError` rather than `AssertionError`; the signal is equivalent.

## Test Layer Distribution

| Layer | Tests | Files | Tools |
|---|---|---|---|
| Unit/CLI-integration (Typer `CliRunner`, in-process) | 10 | 2 (`test_main.py`, `test_doctor.py`) | `typer.testing.CliRunner` |
| E2E | 1 (informal) | CI workflow step | wheel-smoke test against a real built artifact, run in CI only |

## Issues

**CRITICAL**: None.

**WARNING**:
1. No `apply-progress` artifact was persisted for this change, so the strict-TDD RED/GREEN cadence cannot be cross-verified against a recorded evidence table (only against reproduced runtime results). Does not block archive given the strength of independently-reproduced test/coverage/CI evidence, but should not recur.

**RESOLVED DURING VERIFICATION**: `test_doctor_non_str_model_fails_and_exits_one_without_traceback` did not assert a `[PASS] Vector extension loadable` line and did not stub `probe_vec_loadable`, leaving one of the 4 checks named in the doctor-command spec's "Other applicable checks still run" scenario only implicitly covered. Because that requirement is MODIFIED by this change's delta spec, the gap was closed rather than carried into the archive.

**SUGGESTION**: None.

## Final Verdict

**PASS WITH WARNINGS**

All spec requirements and scenarios are pinned by passing tests. Design decisions (D1-D5, placement, no new module/dependency, no `-V`, no workspace resolution) all match the shipped code exactly. All 26 tasks are genuinely delivered, including both mid-review amendments (task 4.2's doc-drift correction, and all 5 proposal success criteria). Full suite (2166 tests), mypy strict, ruff, and the CI wheel smoke test all reproduce green independently. The only real gap is process-recording (missing `apply-progress`), not a code defect, and does not block archive.
