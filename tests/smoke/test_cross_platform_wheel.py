"""Cross-platform smoke tests (#929): the REDUCED subset CI runs on
`macos-latest` and `windows-latest`, where the full gate never runs (see
the `test` job's comment in `.github/workflows/ci.yml`). This module
covers the fourth of the issue's four pieces -- `init`, a tiny `ingest`,
and `doctor` end to end -- against the PACKAGED wheel; the filesystem-
sensitive slice (path normalization, symlink/permission refusals, SQLite
WAL) lives as marked tests inside `tests/unit/**`, next to the code they
already exercise, rather than duplicated here.

Two prior incidents paid for this job: platform-gated tests that first ran
on Linux cost two fix cycles on PR #492, and HFS+/SMB normalization
differences produced real graph-edge bugs (#430).

Every test here carries `cross_platform_smoke` (registered in
`pyproject.toml`) and runs against the WHEEL `uv build` produces, installed
NON-editably into the same environment pytest runs in (CI: `uv pip install
--reinstall-package openkos dist/openkos-*.whl`, verified to round-trip
cleanly against `uv sync --locked` during development of this change) --
not the editable dev checkout. This mirrors the Linux `build` job's own
"prove the packaged artifact, not just the checkout" concern (its wheel
smoke test and its templates/ packaging-proof init smoke test), extended
to macOS/Windows filesystem semantics.

Deliberately NOT under `tests/unit/`: that tree's `_no_network_by_default`
autouse guard (`tests/unit/conftest.py`, #217) intercepts every socket
connection attempt, including a loopback one -- but the whole point of
`test_doctor_reports_ollama_unreachable_without_crashing` and the ingest
test below is a REAL OS-level connection refusal, deterministic because
nothing listens on the poisoned port on any of the three CI platforms, the
same poisoning `evals/run_self_tests.py`'s `UNREACHABLE_OLLAMA` already
relies on for its own model-free self-tests. Opting individual tests out
with `@pytest.mark.live_backend` was rejected: that marker's own
registration reserves it for a test that reaches the REAL Ollama backend
and gates itself on a reachability probe -- the opposite of what these
tests want, which is a guaranteed-absent one.
"""

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

import openkos
from openkos.cli.main import app

pytestmark = pytest.mark.cross_platform_smoke

runner = CliRunner()

# Port 1 is privileged and unbound on every platform CI runs on, so a
# connection attempt is refused immediately rather than hanging until a
# transport deadline -- the same address and the same reasoning
# `evals/run_self_tests.py`'s `UNREACHABLE_OLLAMA` uses.
_UNREACHABLE_OLLAMA = "http://127.0.0.1:1"

# Which install this run is meant to be exercising. Every CI job that
# collects this module MUST declare it: "1" for the packaged wheel, "0" for
# the editable checkout. See `test_the_openkos_under_test_is_the_declared_
# install` below for why an undeclared CI run is a hard failure rather than
# a skip.
_EXPECT_WHEEL_ENV = "OPENKOS_SMOKE_EXPECT_WHEEL"


def test_the_openkos_under_test_is_the_declared_install() -> None:
    """Everything else in this module imports `openkos` by module name and
    asserts on filesystem artifacts, so NOTHING it executes distinguishes
    the packaged wheel from the editable checkout. Without this test the
    whole "against the wheel" claim rests on step ordering inside the CI
    job -- and that job's own comment records that the failure mode is
    SILENT: dropping `--no-sync` reverts to the editable install before
    pytest even starts collecting, and every assertion here would still
    pass while proving nothing about packaging (#929 review, CRITICAL
    `R3-wheel-provenance-unasserted`).

    The declaration is an environment variable rather than an inference
    because both answers are legitimate: the Linux `test` job runs this
    module against the editable checkout as part of the full suite, and
    the cross-platform smoke job runs it against the wheel. Asserting one
    of them unconditionally would just move the lie.

    An UNDECLARED run under CI fails rather than skips. A skip would
    reintroduce exactly the hole this test closes -- a job that stopped
    installing the wheel, or stopped declaring what it installed, would go
    green having proved nothing. `GITHUB_ACTIONS` is set by the runner
    itself, not by this repository's workflow, so it cannot be dropped by
    the same edit that drops the declaration.
    """
    declared = os.environ.get(_EXPECT_WHEEL_ENV)
    if declared is None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(
                f"{_EXPECT_WHEEL_ENV} is unset in CI. Every job that collects "
                "tests/smoke/ must declare which install it is exercising: "
                '"1" for the packaged wheel, "0" for the editable checkout. '
                "Failing rather than skipping is deliberate -- see this "
                "test's docstring."
            )
        pytest.skip(
            f"{_EXPECT_WHEEL_ENV} is unset: a local run exercises the editable "
            f"checkout by design. Set {_EXPECT_WHEEL_ENV}=0 to assert that, or "
            "=1 after installing the built wheel over it."
        )

    package_root = Path(openkos.__file__).resolve().parent
    source_tree = (Path(__file__).resolve().parents[2] / "src" / "openkos").resolve()
    is_checkout = package_root == source_tree

    if declared == "1":
        assert not is_checkout, (
            "this job declares it is testing the PACKAGED WHEEL, but "
            f"`openkos` resolves to the checkout at {package_root}. The wheel "
            "install did not stick -- check that `uv run` still carries "
            "`--no-sync`, and that the build and `uv pip install "
            "--reinstall-package` steps ran."
        )
    elif declared == "0":
        assert is_checkout, (
            "this job declares it is testing the EDITABLE CHECKOUT, but "
            f"`openkos` resolves to {package_root}, not {source_tree}."
        )
    else:
        pytest.fail(
            f'{_EXPECT_WHEEL_ENV} must be "1" (packaged wheel) or "0" '
            f"(editable checkout); got {declared!r}."
        )


def test_init_creates_a_workspace_against_the_installed_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`openkos init` against the packaged wheel creates the five
    artifacts `init`'s own docstring promises -- `raw/`, `bundle/index.md`,
    `bundle/log.md`, `openkos.yaml`, `AGENTS.md` -- mirroring the Linux
    `build` job's own "Init smoke test (packaging proof for templates/)"
    step, exercised here against macOS/Windows path and directory-creation
    semantics instead of Linux's."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", _UNREACHABLE_OLLAMA)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "bundle" / "index.md").is_file()
    assert (tmp_path / "bundle" / "log.md").is_file()
    assert (tmp_path / "openkos.yaml").is_file()
    assert (tmp_path / "AGENTS.md").is_file()


def test_ingest_of_a_tiny_fixture_degrades_gracefully_without_ollama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real, end-to-end `ingest` of a tiny fixture with no Ollama
    reachable: the raw copy, the Source-only concept, and the catalog
    updates are genuine filesystem writes on THIS platform; only concept
    extraction degrades (design D6), which is the one outcome that is both
    deterministic and network-free everywhere CI runs. Confirmed locally
    (see this change's verification report) that this is exactly the
    behavior a real `--isolated --no-project` wheel install produces on
    macOS -- CI is what proves it on Windows for the first time."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", _UNREACHABLE_OLLAMA)
    assert runner.invoke(app, ["init"]).exit_code == 0
    source = tmp_path / "notes.txt"
    source.write_text(
        "Tiny fixture.\n\nAlice and Bob discussed the roadmap.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "raw" / "notes.txt").is_file()
    assert (tmp_path / "bundle" / "sources" / "notes.md").is_file()
    assert "extraction skipped" in result.output


def test_doctor_reports_ollama_unreachable_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`openkos doctor` is usable even before `init` (its own docstring's
    guarantee); run here with NO workspace at all, against a real (refused)
    socket. The critical Ollama-reachable check fails, the run exits 1, and
    every OTHER check still renders (accumulate-then-exit-once, design D5)
    -- this is the one command in the subset that never touches the
    filesystem beyond reading it, so it is the cheapest possible proof that
    the packaged wheel's error-handling ladder (`OllamaUnavailable` ->
    clean `[FAIL]` line, never a raw traceback) holds on this platform."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", _UNREACHABLE_OLLAMA)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1, result.output
    assert "[FAIL] Ollama reachable" in result.output
    assert "[PASS] git available" in result.output
    assert "Traceback" not in result.output
