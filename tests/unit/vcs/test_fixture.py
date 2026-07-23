"""RED-first test for the `tmp_git_repo` fixture itself (tasks 1.2/1.3):
proves the fixture produces a clean repository with exactly one commit
containing the ingested Source, before any adapter test relies on it."""

from openkos.vcs.git import _run
from tests.unit.vcs.conftest import TmpGitRepo


def test_tmp_git_repo_fixture_produces_clean_single_commit_repo(
    tmp_git_repo: TmpGitRepo,
) -> None:
    status = _run(["git", "status", "--porcelain"], cwd=tmp_git_repo.root)
    assert status.returncode == 0
    assert status.stdout == ""

    log = _run(["git", "log", "--oneline"], cwd=tmp_git_repo.root)
    assert log.returncode == 0
    assert len(log.stdout.strip().splitlines()) == 1

    concept_path = tmp_git_repo.root / "bundle" / f"{tmp_git_repo.source_id}.md"
    assert concept_path.exists()
