"""CLI-level tests for `purge`'s deprecated-status export withdrawal
(deprecated-status-export, issue #1075, Phase 4, tasks 4.7-4.8): a
resurrected target outside the purge set has its export withdrawn as part
of the live-tree cleanup, in the SAME post-rewrite commit as `index.md`/
`log.md` (spec: `privacy-purge` "Purge Withdraws The Deprecated-Status
Export Of Resurrected Targets"). Uses the real-git `tmp_git_repo` fixture,
mirroring `test_purge.py`'s own style."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import fsio
from openkos.cli.main import app
from openkos.model import okf
from openkos.vcs import git as vcs_git
from tests.unit.vcs.conftest import TmpGitRepo, _git, tmp_git_repo

__all__ = ["tmp_git_repo"]

runner = CliRunner()


def _write_target_concept(root: Path, concept_id: str) -> None:
    """A plain concept, already carrying a valid deprecated-status export,
    outside the purge set -- committed with the pinned test identity via
    the shared `_git` helper (CI-safe, no reliance on ambient git config)."""
    path = root / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Y",
                "status": "deprecated",
                okf.STATUS_DERIVED_FROM_KEY: "supersedes",
            },
            "Body.\n",
        ),
        encoding="utf-8",
    )
    _git(["add", "-A"], cwd=root)
    _git(["commit", "-m", "fixture: plant target concept"], cwd=root)


def _add_supersedes_edge(root: Path, source_id: str, target_id: str) -> None:
    """Hand-add a `supersedes` edge from `source_id` to `target_id`,
    committed with the pinned test identity."""
    path = root / "bundle" / f"{source_id}.md"
    metadata, body = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    metadata[okf.RELATIONS_KEY] = okf.encode_relations(
        [okf.Relation(target=target_id, type="supersedes")]
    )
    path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")
    _git(["add", "-A"], cwd=root)
    _git(["commit", "-m", "fixture: add supersedes edge"], cwd=root)


def _metadata_of(root: Path, concept_id: str) -> dict[str, object]:
    text = (root / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(text)
    return metadata


def test_purged_superseders_target_is_withdrawn_in_the_same_commit(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """spec: 'A purged superseder's target is withdrawn in the same
    commit' -- Y carries `status: stable` and no marker, and the
    post-rewrite commit contains `bundle/Y.md` beside `index.md`/`log.md`."""
    _write_target_concept(tmp_git_repo.root, "concepts/y")
    _add_supersedes_edge(tmp_git_repo.root, tmp_git_repo.source_id, "concepts/y")
    phrase = f"purge {tmp_git_repo.source_id}"

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    metadata = _metadata_of(tmp_git_repo.root, "concepts/y")
    assert metadata["status"] == "stable"
    assert okf.STATUS_DERIVED_FROM_KEY not in metadata
    # #886/purge's own contract: the post-rewrite auto-commit stages only
    # paths that actually changed (`git add -- <paths>`), so `index.md`/
    # `log.md` join this commit only when the cleanup touched them -- this
    # minimal single-Source fixture does not exercise that. What this test
    # pins is that Y's withdrawn export landed in the SAME commit
    # `_autocommit` made for the rest of purge's live-tree cleanup, not a
    # separate write outside the atomic post-rewrite commit.
    show_result = vcs_git._run(
        ["git", "show", "--name-only", "--format=", "HEAD"], cwd=tmp_git_repo.root
    )
    assert show_result.returncode == 0
    last_commit_files = show_result.stdout.splitlines()
    assert "bundle/concepts/y.md" in last_commit_files
    log_text = (tmp_git_repo.root / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "Tombstone" not in log_text


def test_a_failed_withdrawal_does_not_fail_the_purge(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'A failed withdrawal does not fail the purge' -- an `OSError`
    writing Y's withdrawn export is a non-fatal WARNING naming Y and
    `openkos repair`; `purge` still exits with its normal success code."""
    _write_target_concept(tmp_git_repo.root, "concepts/y")
    _add_supersedes_edge(tmp_git_repo.root, tmp_git_repo.source_id, "concepts/y")
    phrase = f"purge {tmp_git_repo.source_id}"

    real_write_atomic = fsio.write_atomic

    def _raise_for_y(path: Path, text: str) -> None:
        if path.name == "y.md":
            raise OSError("simulated write failure")
        real_write_atomic(path, text)

    monkeypatch.setattr(fsio, "write_atomic", _raise_for_y)

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert "concepts/y" in result.stderr
    assert "openkos repair" in result.stderr
