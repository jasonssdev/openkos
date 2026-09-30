"""Shared fixtures for the direct tests of the curation write services
(`merge_service`, `unmerge_service`, `reconcile_service`, issue #1168).

A service is called WITHOUT the CLI, so these helpers build a real workspace
once (through `init`, the one supported way to make one), then move the
process AWAY from it: every test proves the service works from an explicit
root alone.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.bundle import index as bundle_index
from openkos.cli.main import app


def make_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.chdir(root)
    assert CliRunner().invoke(app, ["init"]).exit_code == 0
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return root


def write_concept(
    root: Path, concept_id: str, *, title: str, body: str = "Body."
) -> Path:
    """A concept file plus its hand-authored `index.md` bullet."""
    path = root / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Concept\ntitle: {title}\n---\n\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )
    link_dir, slug = concept_id.rsplit("/", 1)
    index_path = root / "bundle" / "index.md"
    index_path.write_text(
        bundle_index.insert_index_entry(
            index_path.read_text(encoding="utf-8"),
            section="Concepts",
            link_dir=link_dir,
            title=title,
            slug=slug,
            description=f"{title}.",
        ),
        encoding="utf-8",
    )
    return path


def tree(root: Path) -> dict[str, bytes]:
    """Every workspace file's bytes, derived stores excluded."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".openkos" not in p.parts and ".git" not in p.parts
    }


class CommitRecorder:
    """An auto-commit port that records what it was asked to commit."""

    def __init__(self, sha: str | None = "abc1234") -> None:
        self.sha = sha
        self.calls: list[tuple[Path, list[str], str]] = []

    def __call__(self, root: Path, paths: Sequence[str], message: str) -> str | None:
        self.calls.append((root, list(paths), message))
        return self.sha
