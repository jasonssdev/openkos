# Tasks: CLI `--version` Flag (Closes #181)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~140 (src ~32, tests ~75, docs ~30, ci ~3) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | single-pr-default |
| Chain strategy | N/A (no chaining) |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: N/A (no chaining)
Size exception required: No (~140 lines is well inside the 800-line budget)
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `--version` flag + doctor banner + docs/CI/CHANGELOG | PR 1 | `uv run pytest tests/unit/test_main.py tests/unit/cli/test_doctor.py -k version` | `uv build && uv run --isolated --no-project --with dist/openkos-*.whl openkos --version` | Single commit revert; purely additive, no migration |

## Phase 1: Tests — RED (write failing, confirm they fail for the right reason)

- [x] 1.1 `tests/unit/test_main.py`: `test_version_flag_prints_version_and_exits_zero` — regex `^openkos \d+\.\d+\.\d+`, exit 0 (Req: Version Flag Prints Exact Output And Exits Zero)
- [x] 1.2 `tests/unit/test_main.py`: `test_version_flag_matches_installed_distribution_metadata` — compare against `metadata.version("openkos")` (Req: Version Read From Installed Distribution Metadata)
- [x] 1.3 `tests/unit/test_main.py`: `test_version_flag_works_outside_workspace` — `monkeypatch.chdir(tmp_path)`, no workspace resolution (Req: Eager Evaluation Short-Circuits)
- [x] 1.4 `tests/unit/test_main.py`: subcommand-combined case — `--version doctor` prints only version, subcommand never runs (Req: Eager Evaluation — combined invocation scenario)
- [x] 1.5 `tests/unit/test_main.py`: `test_version_flag_prints_exactly_one_line` — single stdout line, nothing on stderr (Req: Version Flag Prints Exact Output)
- [x] 1.6 `tests/unit/test_main.py`: `test_version_flag_falls_back_when_distribution_missing` — monkeypatch module-local `_pkg_version` (NOT stdlib origin) to raise `PackageNotFoundError`; assert `openkos unknown`, exit 0; plus a direct `_version_line()` assertion under the same patch (Req: Unknown Version Fallback — the hard, otherwise-unreachable branch required by the 90% BRANCH gate)
- [x] 1.7 `tests/unit/test_main.py`: no-side-effects check — no file written, no network/Ollama call during `--version` (Req: Version Resolution Is Read-Only)
- [x] 1.8 `tests/unit/test_main.py`: `openkos --help` output includes `--version` (Req: Version Flag Is Discoverable In Help Text)
- [x] 1.9 `tests/unit/cli/test_doctor.py`: `test_doctor_prints_version_banner_first` — first stdout line starts with `openkos `, check-line count and exit code unchanged (Req: Doctor Prints A Leading Version Banner — both scenarios)
- [x] 1.10 `tests/unit/cli/test_doctor.py`: regression check that malformed `model: yes` still yields `[FAIL]` + remediation, no traceback, other checks still run, and no check-line shape change beyond the new banner (Req: Doctor Never Raises On A Malformed Model Config — confirm pre-existing `try/except` still covers all 4 scenarios; add a test only if not already present)
- [x] 1.11 Run full suite, confirm 1.1–1.10 fail for the expected reason (missing `--version` option / missing banner), not an import error

## Phase 2: Source — GREEN

- [x] 2.1 `src/openkos/cli/main.py`: add `from importlib.metadata import PackageNotFoundError, version as _pkg_version` in the existing stdlib import block
- [x] 2.2 `src/openkos/cli/main.py`: define `_version_line() -> str` and `_version_callback(value: bool) -> None` strictly BETWEEN `_LOCK_CONTENTION_MSG` (line ~100-103) and `@app.callback()` (line ~106) — mandatory placement, `_version_callback` is a parameter default evaluated at definition time; defining it below `callback()` raises `NameError` at import
- [x] 2.3 `src/openkos/cli/main.py`: add `version: bool = typer.Option(False, "--version", help=..., is_eager=True, callback=_version_callback)` param to `callback()`; no `-V` short flag, no `expose_value`
- [x] 2.4 `src/openkos/cli/main.py`: insert `typer.echo(_version_line())` immediately before the existing `openkos doctor: checking environment at {root}` line (~6176), before the check loop
- [x] 2.5 Confirm all Phase 1 tests now pass (GREEN)

## Phase 3: CI

- [x] 3.1 `.github/workflows/ci.yml:117`: extend the isolated wheel-smoke step to also run `uv run --isolated --no-project --with dist/openkos-*.whl openkos --version`

## Phase 4: Documentation

- [x] 4.1 `docs/cli.md`: add `### Global options` at the end of `## Conventions` (before line ~26) — `--version`, exact output, exit 0, workspace independence, staleness caveat
- [x] 4.2 `docs/cli.md`: add one sentence near line ~323 noting the doctor version banner. The banner adds no check; the check list is renumbered only because review found it documented nine of `doctor`'s ten checks (`Workspace vector index present` was missing) — corrected here along with the matching table in `docs/testing.md`
- [x] 4.3 `docs/testing.md:169`: replace `openkos --help | head -3` with `openkos --version`
- [x] 4.4 `docs/testing.md:172-175`: delete the obsolete "no `--version` yet" callout referencing #181

## Phase 5: Changelog

- [x] 5.1 `CHANGELOG.md`: add `### Added` entry under `[Unreleased]` (~line 15) citing #181; commit scope `cli` per Conventional Commits

## Phase 6: Verification

- [x] 6.1 `uv run pytest` — full suite green, 90% branch coverage gate met
- [x] 6.2 `uv run ruff check . && uv run ruff format --check .`
- [x] 6.3 `uv run mypy .`
- [x] 6.4 `uv build && uv run --isolated --no-project --with dist/openkos-*.whl openkos --version` — local wheel smoke check
