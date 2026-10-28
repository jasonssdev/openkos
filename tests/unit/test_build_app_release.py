"""`desktop/scripts/build-app.sh --release`: refuses before building anything (#1624).

A release build makes and signs the update archive (ADR-0079, ADR-0080), so it
needs the signing key and a version the updater can compare. Both are checked
before the first build step, which is what these tests run: the script exits
long before it would need macOS, Rust or pnpm.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_SCRIPT = Path(__file__).resolve().parents[2] / "desktop" / "scripts" / "build-app.sh"

_BASH = shutil.which("bash")
# Stand-ins: the script only tests that the variables are set.
_KEY_ONLY = {"TAURI_SIGNING_PRIVATE_KEY": "stand-in"}
_KEY_AND_PASSWORD = {**_KEY_ONLY, "TAURI_SIGNING_PRIVATE_KEY_PASSWORD": "stand-in"}

pytestmark = pytest.mark.skipif(_BASH is None, reason="needs bash")


def _run(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("TAURI_SIGNING")}
    assert _BASH is not None
    return subprocess.run(  # noqa: S603 -- our own script, fixed arguments
        [_BASH, str(_SCRIPT), *args],
        env={**base, **(env or {})},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_a_release_without_the_signing_key_is_refused() -> None:
    result = _run("--release", "0.7.0")
    assert result.returncode == 1
    assert "TAURI_SIGNING_PRIVATE_KEY" in result.stderr
    assert "==>" not in result.stdout


def test_a_release_without_the_key_password_is_refused() -> None:
    result = _run("--release", "0.7.0", env=_KEY_ONLY)
    assert result.returncode == 1
    assert "TAURI_SIGNING_PRIVATE_KEY_PASSWORD" in result.stderr
    assert "==>" not in result.stdout


@pytest.mark.parametrize("version", ["v0.7.0", "0.7", "latest", ""])
def test_a_release_version_that_is_not_semver_is_refused(version: str) -> None:
    result = _run("--release", version, env=_KEY_AND_PASSWORD)
    assert result.returncode == 1
    assert "version" in result.stderr
    assert "==>" not in result.stdout


def test_an_unknown_argument_is_refused() -> None:
    result = _run("--sign")
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_every_pull_request_makes_and_signs_the_archive_with_a_throwaway_key() -> None:
    # The release path is exercised on every pull request, never with the
    # project's key: CI makes a fresh one for the run, so the re-signed app's
    # archive is unpacked, verified and signed exactly as a release does.
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    job = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["desktop-bundle"]
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "cargo tauri signer generate --ci" in runs
    assert "desktop/scripts/build-app.sh --release" in runs
    assert "secrets." not in yaml.safe_dump(job)
    assert "environment" not in job
