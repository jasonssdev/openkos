"""Shared fixtures for `tests/unit/vcs/**`: a REAL `git` repository, used by
`openkos.vcs.git` adapter tests instead of mocking `subprocess` -- the
adapter's only job is to wrap real `git`/`git-filter-repo` correctly, so its
tests must exercise real `git` behavior end to end.

Git commands issued by fixtures in this module go through the adapter's own
`openkos.vcs.git._run()` (with a pinned author/committer `env`) rather than
calling `subprocess` a second time, so `_run` stays the ONE subprocess call
site in the whole test+production tree.
"""

import os
from pathlib import Path
from typing import NamedTuple

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.vcs import git as vcs_git

runner = CliRunner()

# Pinned author/committer identity AND dates so fixture commits never depend
# on the host's global git config (CI has none configured) NOR on wall-clock
# time -- both are needed for reproducible commit SHAs across runs/machines.
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "OpenKOS Test",
    "GIT_AUTHOR_EMAIL": "test@openkos.invalid",
    "GIT_AUTHOR_DATE": "2024-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "OpenKOS Test",
    "GIT_COMMITTER_EMAIL": "test@openkos.invalid",
    "GIT_COMMITTER_DATE": "2024-01-01T00:00:00+00:00",
}


def _git(args: list[str], cwd: Path) -> None:
    result = vcs_git._run(["git", *args], cwd=cwd, env=_GIT_ENV)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


class TmpGitRepo(NamedTuple):
    """An initialized `openkos` workspace, at a git repository root, with one
    commit containing an ingested Source concept."""

    root: Path
    source_id: str


@pytest.fixture
def tmp_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TmpGitRepo:
    """`git init` a `tmp_path` workspace, `openkos init` it, ingest one
    Source, and commit everything in a single clean commit."""
    _git(["init"], cwd=tmp_path)
    monkeypatch.chdir(tmp_path)

    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0

    source_name = "notes.txt"
    (tmp_path / source_name).write_text("content", encoding="utf-8")
    ingest_result = runner.invoke(app, ["ingest", source_name, "--auto"])
    assert ingest_result.exit_code == 0
    source_id = "sources/notes"

    _git(["add", "-A"], cwd=tmp_path)
    _git(["commit", "-m", "Initial workspace + one Source"], cwd=tmp_path)

    return TmpGitRepo(root=tmp_path, source_id=source_id)
