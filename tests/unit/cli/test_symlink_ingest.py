"""`ingest` refuses to write through a symlinked destination directory (#1126).

`fsio.write_exclusive` opens with mode `x`, which follows a symlinked PARENT,
so `bundle/sources -> /outside` carried the source text out of the workspace.
The refusal is the same D1-shaped one `require_workspace` returns, and it lands
before anything is written (no partial writes).
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from tests.unit.cli.test_ingest import (
    _entity_reply,
    _init_workspace,
    _patch_llm,
)

runner = CliRunner()

pytestmark = pytest.mark.cross_platform_smoke


@pytest.fixture(autouse=True)
def _skip_if_symlinks_unsupported(tmp_path: Path) -> None:
    probe = tmp_path / ".probe"
    target = tmp_path / ".probe-target"
    target.write_text("x", encoding="utf-8")
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink privilege unavailable: {exc}")
    probe.unlink()


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git/" not in p.as_posix()
    }


def test_ingest_refuses_a_symlinked_sources_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "bundle" / "sources").symlink_to(outside, target_is_directory=True)
    (tmp_path / "note.txt").write_text("Some raw notes.", encoding="utf-8")
    before = _tree(tmp_path / "bundle") | {
        f"raw/{k}": v for k, v in _tree(tmp_path / "raw").items()
    }

    result = runner.invoke(app, ["ingest", "note.txt", "--auto"])

    assert result.exit_code == 1
    assert "sources" in result.output
    assert "is a symlink" in result.output
    assert list(outside.iterdir()) == [], "ingest wrote outside the workspace"
    assert not (tmp_path / "raw" / "note.txt").exists(), "partial write into raw/"
    after = _tree(tmp_path / "bundle") | {
        f"raw/{k}": v for k, v in _tree(tmp_path / "raw").items()
    }
    assert after == before


def test_ingest_refuses_a_symlinked_derived_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _entity_reply())
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "bundle" / "entities").symlink_to(outside, target_is_directory=True)
    (tmp_path / "notes.txt").write_text("A field manual.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 1
    assert "entities" in result.output
    assert "is a symlink" in result.output
    assert list(outside.iterdir()) == [], "ingest wrote outside the workspace"
    assert not (tmp_path / "raw" / "notes.txt").exists(), "partial write into raw/"
    assert not (tmp_path / "bundle" / "sources" / "notes.md").exists()


def test_ingest_still_writes_in_a_clean_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No-regression: the guard must not refuse an ordinary ingest."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _entity_reply())
    (tmp_path / "notes.txt").write_text("A field manual.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "bundle" / "entities" / "enchiridion.md").is_file()
