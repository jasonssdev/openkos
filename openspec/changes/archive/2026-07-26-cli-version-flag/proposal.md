# Proposal: CLI `--version` Flag (Closes #181)

## Intent

`openkos` cannot report its own build. Version lives only in `pyproject.toml:3`;
nothing in `src/` reads it. Bug reports, CI wheel smoke tests, and `doctor`
output therefore carry no build identity, and `docs/testing.md:169` fakes it with
`openkos --help | head -3`. Success: any user or script can print the installed
version in one read-only command that works without a workspace.

## Scope

### In Scope

- Eager `--version` on the existing bare callback (`src/openkos/cli/main.py:106-108`),
  `is_eager=True`, `expose_value=False`, exit 0 before any subcommand runs.
- **Output format**: exactly `openkos {version}`, one line, nothing else. No
  Python version, no install path — minimal and greppable.
- Shared `_resolve_version()` helper wrapping `importlib.metadata.version("openkos")`.
- **Fallback**: on `PackageNotFoundError`, print `openkos unknown`.
- **`doctor` integration (IN scope)**: leading banner `typer.echo` at
  `main.py:6176`, before check rendering — NOT a `CheckResult` at all, so
  `doctor`'s ten checks keep their numbering and exit-code semantics. (Review
  found `docs/cli.md:325-337` documented only nine of them; that pre-existing
  drift is corrected here.)
- **CI (IN scope)**: extend `.github/workflows/ci.yml:117` to also run
  `openkos --version` against the isolated wheel — proof it reads real packaged
  metadata, not a constant.
- Docs: `docs/cli.md` global-options entry; `docs/testing.md` §0.4 build-ID step
  switched to `--version` AND removal of the stale #181 callout at `:172-175`;
  `CHANGELOG.md` `[Unreleased]`. `README.md` only if it already shows a verify step.

### Out of Scope (Non-Goals)

- **Stale dist-info detection.** The real failure mode is a bumped
  `pyproject.toml` without `uv sync`, printing an old-but-plausible number. This
  change neither detects nor reconciles it; it is documented as a known caveat only.
- `--version` for subcommands, `--json`/machine formats, `openkos version`
  subcommand (rejected: must work standalone), `openkos.__version__` constant
  (rejected: second source of truth), version pinned into any artifact.
- No ADR: reversible, single-file, no architectural commitment.

## Capabilities

### New Capabilities
- `cli-version`: standalone `--version` output contract, exit code, fallback,
  workspace independence.

### Modified Capabilities
- `doctor-command`: adds a non-check version banner line preceding the
  `[PASS]/[FAIL]/[SKIP]` block; check count, order, and exit codes unchanged.

## Approach

Typer's own `--help` mechanism, reused. One helper, two call sites
(`callback`, `doctor`). Read-only, no network, no workspace resolution — the
callback never calls `require_workspace`.

**Fallback rationale**: `unknown` cannot be mistaken for a release; `0.0.0-dev`
parses as valid semver and would be triaged as a real pre-release build.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py:106-108` | Modified | Eager `--version` option |
| `src/openkos/cli/main.py:6176` | Modified | Version banner in `doctor` |
| `tests/unit/test_main.py` | Modified | Happy path, fallback, standalone exit 0 |
| `tests/unit/cli/test_doctor.py` | Modified | Banner assertion |
| `.github/workflows/ci.yml:117` | Modified | Wheel smoke test |
| `docs/cli.md`, `docs/testing.md`, `CHANGELOG.md` | Modified | Docs |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Stale dist-info prints wrong version | Med | Documented caveat; explicit non-goal |
| Fallback branch hard to cover under 90% branch gate | Med | Monkeypatch `metadata.version` to raise |
| Doctor banner breaks output-shape assertions | Low | Banner precedes checks; assert prefix only |

## Rollback Plan

Single-commit revert. The flag is additive: no existing command signature,
exit code, or output line changes. Docs revert with the same commit.

## Dependencies

None. `importlib.metadata` is stdlib; no new runtime dependency.

## Success Criteria

- [x] `openkos --version` prints `openkos <semver>` and exits 0 outside a workspace.
- [x] Same command succeeds against the isolated built wheel in CI.
- [x] `openkos doctor` shows the version banner; still ten checks, same exit codes.
- [x] `PackageNotFoundError` yields `openkos unknown`, covered by a test.
- [x] No "no `--version` yet" claim remains in `docs/`: the `docs/testing.md`
      known-issues row is gone and `docs/roadmap.md:89` now reports #181 as
      closed. Both still link #181, but as shipped history, not as a gap.
