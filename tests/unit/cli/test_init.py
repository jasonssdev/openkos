"""Unit tests for the `init` CLI command: pre-flight refusal and workspace creation.

Pre-flight (D1 Phase A) is a pure read: `config.refusal_reason`'s five
conditions are checked before any write happens, so a refusal leaves the
directory exactly as it was found.
"""

import os
import stat
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


def _snapshot(root: Path) -> dict[Path, bytes]:
    """Capture every file's exact bytes under `root`, keyed by relative path."""
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_refuses_when_openkos_yaml_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing `openkos.yaml` refuses with exit 1 and zero writes (scenario 7)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "openkos.yaml").write_text("name: x\n", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos.yaml" in result.stderr
    assert _snapshot(tmp_path) == before


def test_refuses_when_agents_md_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing `AGENTS.md` refuses with exit 1 and zero writes (scenario 8)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# manual\n", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "AGENTS.md" in result.stderr
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("dirname", ["raw", "bundle"])
def test_refuses_when_dir_non_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirname: str
) -> None:
    """A non-empty `raw/` or `bundle/` refuses with exit 1 and zero writes (scenario 9)."""
    monkeypatch.chdir(tmp_path)
    target_dir = tmp_path / dirname
    target_dir.mkdir()
    (target_dir / "existing.txt").write_text("original", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert dirname in result.stderr
    assert "not empty" in result.stderr
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("dirname", ["raw", "bundle"])
def test_refuses_when_dir_is_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirname: str
) -> None:
    """A `raw` or `bundle` that exists as a plain file refuses cleanly (requirement 3).

    `_non_empty_dir` (and `is_workspace`) call `Path.is_dir()`, which is
    `False` for a plain file -- so without this fifth pre-flight condition,
    a lone file named `raw` would slip past refusal into Phase B, where
    `Path.mkdir` raises an uncaught `FileExistsError`.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / dirname).write_text("not a directory", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert dirname in result.stderr
    assert "not a directory" in result.stderr
    assert _snapshot(tmp_path) == before


def test_refuses_on_second_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second `init` on an already-initialized workspace refuses and changes nothing (scenarios 10-11)."""
    monkeypatch.chdir(tmp_path)
    first = runner.invoke(app, ["init"])
    assert first.exit_code == 0
    before = _snapshot(tmp_path)

    second = runner.invoke(app, ["init"])

    assert second.exit_code == 1
    assert isinstance(second.exception, SystemExit)
    assert "openkos.yaml" in second.stderr
    assert _snapshot(tmp_path) == before


def test_fresh_empty_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh empty directory gets all five artifacts and exits 0 (scenario 1)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "bundle" / "index.md").is_file()
    assert (tmp_path / "bundle" / "log.md").is_file()
    assert (tmp_path / "openkos.yaml").is_file()
    assert (tmp_path / "AGENTS.md").is_file()
    assert result.stdout.strip() != ""
    assert "openkos.yaml" in result.stdout


def test_raw_default_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`raw/` keeps the filesystem's default directory mode; no `chmod` runs (scenario 13).

    Compared against a sibling directory created directly by `mkdir`, rather
    than a hardcoded mode, since the default mode depends on the running
    system's umask.
    """
    monkeypatch.chdir(tmp_path)
    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    expected_mode = reference_dir.stat().st_mode

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / "raw").stat().st_mode == expected_mode


def test_adopt_non_workspace_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-empty directory with none of the four markers is adoptable (scenario 12)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("scratch", encoding="utf-8")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "bundle" / "index.md").is_file()
    assert (tmp_path / "bundle" / "log.md").is_file()
    assert (tmp_path / "openkos.yaml").is_file()
    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "scratch"


def test_fresh_bundle_is_conformant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§9 rules 1-2 hold vacuously on a fresh bundle (scenario 14).

    `index.md` and `log.md` are both reserved, so `check_conformance` has no
    non-reserved `.md` file to check and returns `[]` regardless of what
    `init` wrote -- this cannot fail today. Its real value is as a
    regression guard: it starts failing the moment `init` ever writes a
    non-reserved `.md` that violates rules 1-2. Rule 3 (reserved-file
    structure) is deferred to `lint` and not checked here; it holds by
    construction via the index/log shapes, genuinely enforced by
    `tests/unit/bundle/test_index.py` and `test_log.py`.
    """
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert okf.check_conformance(tmp_path / "bundle") == []


@pytest.mark.parametrize(
    "tz_name",
    ["Etc/GMT+12", "Pacific/Kiritimati"],
    ids=["utc_minus_12", "utc_plus_14"],
)
def test_log_dated_section_uses_local_date_not_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tz_name: str
) -> None:
    """`log.md`'s dated section reflects the machine's local calendar date, not UTC's.

    `ubuntu-latest` (CI) runs in UTC, where local == UTC -- a single fixed
    timezone would pass in CI even if the implementation used UTC, the same
    blind spot that hid the `Path(".")` bug (tmp_path is always absolute).
    `Etc/GMT+12` (UTC-12) and `Pacific/Kiritimati` (UTC+14) are the two most
    extreme real UTC offsets; together their "local date differs from UTC
    date" windows cover all 24 hours of a UTC day, so at least one of the two
    parametrized cases always disagrees with a UTC-based date at any instant
    this test runs -- neither can silently pass alongside a UTC bug.

    `time.tzset()` re-reads the `TZ` environment variable into the C
    library's timezone state, which is what `datetime.now().astimezone()`
    (no-arg) consults. The environment is restored and re-synced explicitly
    in a `finally` block, rather than left to `monkeypatch`'s teardown alone,
    because `monkeypatch` reverts the `TZ` env var but does not itself call
    `tzset()` afterwards -- without the explicit re-sync here, the process's
    C library timezone state would leak into later tests even though `TZ`
    itself was reverted.
    """
    monkeypatch.chdir(tmp_path)
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = tz_name
    time.tzset()
    try:
        expected_local_date = datetime.now(ZoneInfo(tz_name)).date()

        result = runner.invoke(app, ["init"])
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()

    assert result.exit_code == 0
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert f"## {expected_local_date.isoformat()}" in log_text


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission-based write failures require a POSIX non-root user",
)
def test_write_failure_surfaces_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Phase-B write failure exits non-zero with a clear message, no traceback (requirement 4).

    Pre-flight (Phase A) only reads, so it passes on an empty, writable
    directory. Stripping write permission from `tmp_path` itself, after
    pre-flight would pass but before Phase B runs, forces the very first
    write (`raw/`'s `mkdir`) to raise `PermissionError` -- a write failure
    that a `chmod`-based collision on `openkos.yaml` cannot reach, since
    pre-flight would refuse before any write is attempted. Root is exempted
    (`geteuid() == 0`) because root bypasses POSIX permission bits
    entirely, which would make this test silently pass without exercising
    the failure path -- an untestable claim on that platform, not a false
    one.
    """
    monkeypatch.chdir(tmp_path)
    original_mode = stat.S_IMODE(tmp_path.stat().st_mode)
    tmp_path.chmod(0o500)
    try:
        result = runner.invoke(app, ["init"])
    finally:
        tmp_path.chmod(original_mode)

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "openkos init" in result.stderr
    assert "failed" in result.stderr
