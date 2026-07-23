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


class MultiCommitRepo(NamedTuple):
    """A multi-commit `openkos` workspace built for the Slice 2
    history-content-scrub COLLISION-SAFETY tests: an EARLIER commit writes
    the purge target's catalog bullet, a `forget`-style tombstone, a
    surviving sibling's catalog bullet, and a `log.md` line that mentions
    the purge target's id only in PROSE (never as its own link) -- then a
    LATER commit rewrites `index.md`/`log.md` again (adding an unrelated
    concept), so the residual to scrub lives in a NON-tip historical blob,
    not merely the current `HEAD`."""

    root: Path
    purge_id: str
    purge_title: str
    purge_body_rel_path: str
    sibling_id: str
    sibling_title: str
    sibling_body_rel_path: str
    prose_log_line: str
    tombstone_anchor: str
    anchor_survivor_id: str
    anchor_survivor_title: str
    anchor_survivor_bullet: str
    earlier_commit: str
    later_commit: str


@pytest.fixture
def tmp_git_repo_with_history_residual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> MultiCommitRepo:
    _git(["init"], cwd=tmp_path)
    monkeypatch.chdir(tmp_path)

    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0

    purge_id = "concepts/target"
    purge_title = "Target Concept"
    sibling_id = "concepts/sibling"
    sibling_title = "Sibling Concept"
    prose_log_line = f"Reviewed provenance touching {purge_id} during an audit."
    tombstone_anchor = purge_id
    anchor_survivor_id = "concepts/anchor-survivor"
    anchor_survivor_title = "Anchor Survivor"
    # A SURVIVING concept's own catalog bullet, whose FIRST link is its OWN
    # identity (never the purge target's), but whose free-text description
    # happens to contain a `(id: <purge-id>)`-shaped anchor -- proving the
    # `(id:)` anchor matcher must NOT be applied to `index.md` bullets (only
    # to `log.md` tombstones), or this SURVIVOR would be over-scrubbed.
    anchor_survivor_bullet = (
        f"* [{anchor_survivor_title}](/{anchor_survivor_id}.md) - "
        f"Mentions (id: {purge_id}) in its free-text description.\n"
    )

    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True, exist_ok=True)
    (bundle_dir / f"{purge_id}.md").write_text(
        "---\ntype: Concept\ntitle: Target Concept\n---\n\n"
        f"# {purge_title}\n\n"
        f"Body that legitimately mentions its own id {purge_id} and title "
        f'"{purge_title}" in its own text.\n',
        encoding="utf-8",
    )
    (bundle_dir / f"{sibling_id}.md").write_text(
        "---\ntype: Concept\ntitle: Sibling Concept\n---\n\n"
        f"# {sibling_title}\n\n"
        f'Sibling body that legitimately discusses {purge_id} ("{purge_title}") '
        "in its own analysis text -- must survive untouched, proving the "
        "scrub's filename gate never reaches bundle bodies.\n",
        encoding="utf-8",
    )

    (bundle_dir / f"{anchor_survivor_id}.md").write_text(
        "---\ntype: Concept\ntitle: Anchor Survivor\n---\n\n"
        f"# {anchor_survivor_title}\n\n"
        "A surviving concept whose catalog bullet's description text "
        "happens to contain an `(id: ...)`-shaped anchor.\n",
        encoding="utf-8",
    )

    index_path = bundle_dir / "index.md"
    index_path.write_text(
        index_path.read_text(encoding="utf-8") + "\n# Concepts\n\n"
        f"* [{purge_title}](/{purge_id}.md) - The purge target.\n"
        f"* [{sibling_title}](/{sibling_id}.md) - A surviving sibling.\n"
        f"{anchor_survivor_bullet}",
        encoding="utf-8",
    )

    log_path = bundle_dir / "log.md"
    log_path.write_text(
        log_path.read_text(encoding="utf-8").replace(
            "* **Initialization**",
            f"* **Forgot**: removed concept (id: {tombstone_anchor})\n"
            f"* {prose_log_line}\n"
            "* **Initialization**",
        ),
        encoding="utf-8",
    )

    _git(["add", "-A"], cwd=tmp_path)
    _git(
        ["commit", "-m", "Earlier: target+sibling+tombstone+prose mention"],
        cwd=tmp_path,
    )
    earlier_commit = vcs_git._run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path
    ).stdout.strip()

    later_id = "concepts/later"
    (bundle_dir / f"{later_id}.md").write_text(
        "---\ntype: Concept\ntitle: Later Concept\n---\n\n"
        "# Later Concept\n\nA later, unrelated concept.\n",
        encoding="utf-8",
    )
    index_path.write_text(
        index_path.read_text(encoding="utf-8")
        + "* [Later Concept](/concepts/later.md) - Added later.\n",
        encoding="utf-8",
    )
    log_path.write_text(
        log_path.read_text(encoding="utf-8").replace(
            "* **Initialization**",
            "* **Later entry**: added a follow-up concept.\n* **Initialization**",
        ),
        encoding="utf-8",
    )
    _git(["add", "-A"], cwd=tmp_path)
    _git(["commit", "-m", "Later: rewrite index.md/log.md again"], cwd=tmp_path)
    later_commit = vcs_git._run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path
    ).stdout.strip()

    return MultiCommitRepo(
        root=tmp_path,
        purge_id=purge_id,
        purge_title=purge_title,
        purge_body_rel_path=f"bundle/{purge_id}.md",
        sibling_id=sibling_id,
        sibling_title=sibling_title,
        sibling_body_rel_path=f"bundle/{sibling_id}.md",
        prose_log_line=prose_log_line,
        tombstone_anchor=tombstone_anchor,
        anchor_survivor_id=anchor_survivor_id,
        anchor_survivor_title=anchor_survivor_title,
        anchor_survivor_bullet=anchor_survivor_bullet,
        earlier_commit=earlier_commit,
        later_commit=later_commit,
    )


def historical_blob_shas(root: Path, rel_path: str) -> list[str]:
    """Every commit's blob SHA for `rel_path`, oldest-first, across ALL
    refs -- `None`-filtered for commits where the path did not exist yet.
    Used by the COLLISION-SAFETY tests to prove byte-exact identity (same
    blob hash) rather than merely textual equality."""
    commits = vcs_git._run(
        ["git", "rev-list", "--all", "--reverse"], cwd=root
    ).stdout.split()
    shas: list[str] = []
    for sha in commits:
        result = vcs_git._run(["git", "rev-parse", f"{sha}:{rel_path}"], cwd=root)
        if result.returncode == 0:
            shas.append(result.stdout.strip())
    return shas


def historical_blob_texts(root: Path, rel_path: str) -> list[str]:
    """Every commit's blob TEXT content for `rel_path`, oldest-first,
    across ALL refs. Used to assert absence/presence of specific text."""
    commits = vcs_git._run(
        ["git", "rev-list", "--all", "--reverse"], cwd=root
    ).stdout.split()
    texts: list[str] = []
    for sha in commits:
        result = vcs_git._run(["git", "show", f"{sha}:{rel_path}"], cwd=root)
        if result.returncode == 0:
            texts.append(result.stdout)
    return texts
