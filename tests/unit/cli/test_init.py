"""Unit tests for the `init` CLI command: pre-flight refusal and workspace creation.

Pre-flight (D1 Phase A) is a pure read: all four refusal conditions are
checked before any write happens, so a refusal leaves the directory exactly
as it was found (D1, D2 belt-and-suspenders with `config.is_workspace`).
"""

from pathlib import Path

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
    assert _snapshot(tmp_path) == before


def test_refuses_on_second_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second `init` on an already-initialized workspace refuses and changes nothing (scenarios 10-11)."""
    monkeypatch.chdir(tmp_path)
    first = runner.invoke(app, ["init"])
    assert first.exit_code == 0
    before = _snapshot(tmp_path)

    second = runner.invoke(app, ["init"])

    assert second.exit_code == 1
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
    """A freshly initialized bundle passes the OKF §9 conformance check (scenario 14)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert okf.check_conformance(tmp_path / "bundle") == []
