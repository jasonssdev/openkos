"""Unit tests for the `purge` CLI command: the irreversible, true-erasure
counterpart to `forget` (MVP-2 right-to-be-forgotten, Slice 1). Reuses
`forget`'s Phase A (path safety, purge-set resolution, reference-aware
detection) unchanged, then runs six fail-closed safety rails, ALL before any
write, before invoking `vcs.git.expunge_paths` -- the point of no return.

Most tests shell out to a REAL git repository via the `tmp_git_repo` fixture
(`tests/unit/vcs/conftest.py`) -- `purge`'s whole job is to safely drive real
`git`/`git-filter-repo`, so its tests must prove that against the real
binary, not a mock."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.vcs import git as vcs_git
from tests.unit.vcs.conftest import TmpGitRepo, _git, tmp_git_repo

__all__ = ["tmp_git_repo"]

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _write_child_concept(
    tmp_path: Path,
    concept_id: str,
    *,
    provenance: list[str],
    title: str = "Child",
) -> None:
    """Write a hand-crafted concept file with an explicit `provenance:`
    frontmatter list, plus a matching `index.md` bullet -- used to build
    `--scope source` cascade fixtures, mirroring `test_forget.py`'s own
    helper."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_lines = [
        "type: Concept",
        f"title: {title}",
        f"provenance: [{', '.join(provenance)}]",
    ]
    concept_path.write_text(
        "---\n" + "\n".join(metadata_lines) + f"\n---\n\n# {title}\n\nBody.\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "bundle" / "index.md"
    index_text = index_path.read_text(encoding="utf-8")
    index_path.write_text(
        index_text + f"\n# Concepts\n\n* [{title}](/{concept_id}.md) - A child.\n",
        encoding="utf-8",
    )


def _committed_snapshot(root: Path) -> set[Path]:
    """Every path git currently tracks/has ever tracked, via a fast
    `git ls-tree` over every commit reachable from `--all`, used only to
    sanity-check absence, not as the primary erasure assertion below."""
    return {path.relative_to(root) for path in root.rglob("*.md")}


def _blob_history_contains(root: Path, rel_path: str) -> bool:
    """`True` iff `rel_path` appears anywhere in `git rev-list --objects
    --all`'s output -- the authoritative "is this truly gone from history"
    check (spec req 3 scenarios)."""
    result = vcs_git._run(["git", "rev-list", "--objects", "--all"], cwd=root)
    assert result.returncode == 0
    return any(line.endswith(rel_path) for line in result.stdout.splitlines())


def _reflog_is_empty(root: Path) -> bool:
    result = vcs_git._run(["git", "reflog"], cwd=root)
    assert result.returncode == 0
    return result.stdout.strip() == ""


def _tree_contains_path(root: Path, rel_path: str) -> bool:
    """`True` iff `rel_path` is a live path in `HEAD`'s tree. Unlike
    `_blob_history_contains` (which lists each BLOB OBJECT once, by
    whichever path `git rev-list --objects` happens to visit it through
    first), this is the right tool to prove a specific path SURVIVES when
    its content happens to be byte-identical to another retained path
    (e.g. a raw copy sharing its source file's blob) -- `git ls-tree`
    enumerates every tree entry, not deduplicated by blob id."""
    result = vcs_git._run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=root)
    assert result.returncode == 0
    return rel_path in result.stdout.splitlines()


# --- Phase A reuse (spec req 1) ---------------------------------------------


def test_purge_self_scope_resolves_single_concept(tmp_git_repo: TmpGitRepo) -> None:
    """Default `--scope self` targets exactly the one concept-id -- proven
    via the printed preview, before any rail runs (no `--confirm-phrase`
    given, non-TTY, so it refuses at rail 6, but the preview is already
    printed by then)."""
    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert f"bundle/{tmp_git_repo.source_id}.md" in result.output
    assert "raw/notes.txt" in result.output
    assert "Total:" not in result.output


def test_purge_source_scope_cascades_descendants(tmp_git_repo: TmpGitRepo) -> None:
    """`--scope source` expands the purge set via
    `find_provenance_descendants`, identical to `forget --scope source`."""
    _write_child_concept(
        tmp_git_repo.root,
        "concepts/child-a",
        provenance=[tmp_git_repo.source_id],
        title="Child A",
    )

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id, "--scope", "source"])

    assert f"bundle/{tmp_git_repo.source_id}.md" in result.output
    assert "bundle/concepts/child-a.md" in result.output
    assert "Total: 2 concept(s) to purge." in result.output


# --- Rail 1: reference-aware refusal ----------------------------------------


def test_purge_reference_aware_refuses_without_force(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """A surviving external inbound link refuses at rail 1, before any
    other rail (in particular, before the git-root/clean-tree/remote rails
    even run) -- no write, no rewrite."""
    referrer_path = tmp_git_repo.root / "bundle" / "concepts" / "referrer.md"
    referrer_path.parent.mkdir(parents=True, exist_ok=True)
    referrer_path.write_text(
        "---\ntype: Concept\ntitle: Referrer\n---\n\n"
        f"# Referrer\n\nSee [source](/{tmp_git_repo.source_id}.md).\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code == 1
    assert "inbound reference" in result.output.lower()
    assert _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )


# --- Rail 2: tool availability ----------------------------------------------


def test_purge_tool_missing_refuses(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git-filter-repo` unavailable (monkeypatched) refuses at rail 2 with
    an install remediation, before the git-root/clean-tree/remote rails."""
    monkeypatch.setattr(vcs_git, "filter_repo_available", lambda: False)

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", "irrelevant"]
    )

    assert result.exit_code == 1
    assert "git-filter-repo" in result.output
    assert "install" in result.output.lower()
    assert _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )


def test_purge_git_itself_missing_refuses(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git` itself unavailable (monkeypatched `git_available`) refuses at
    rail 2, same as the `git-filter-repo`-missing sub-case above -- must
    not fall through to a later rail or attempt any write."""
    monkeypatch.setattr(vcs_git, "git_available", lambda: False)

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", "irrelevant"]
    )

    assert result.exit_code == 1
    assert "git is not available" in result.output.lower()
    assert _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )
    assert (tmp_git_repo.root / "bundle" / f"{tmp_git_repo.source_id}.md").is_file()


# --- Rail 3: workspace root == git repo root --------------------------------


def test_purge_non_git_root_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workspace is nested inside a git repo whose root is an ANCESTOR
    directory, not the workspace root itself -- refuses at rail 3."""
    _git(["init"], cwd=tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0

    source_name = "notes.txt"
    (workspace / source_name).write_text("content", encoding="utf-8")
    ingest_result = runner.invoke(app, ["ingest", source_name, "--auto"])
    assert ingest_result.exit_code == 0

    concept_path = workspace / "bundle" / "sources" / "notes.md"
    raw_path = workspace / "raw" / source_name
    assert concept_path.is_file()
    assert raw_path.is_file()

    result = runner.invoke(app, ["purge", "sources/notes"])

    assert result.exit_code == 1
    assert "git repository root" in result.output.lower()
    # No-mutation: refusal at rail 3 must leave every file untouched, and
    # must never attempt to delete/rebuild the derived indexes.
    assert concept_path.is_file()
    assert raw_path.is_file()
    assert not (workspace / ".openkos" / "fts.db").exists()


# --- Rail 4: dirty working tree ----------------------------------------------


def test_purge_dirty_tree_refuses(tmp_git_repo: TmpGitRepo) -> None:
    (tmp_git_repo.root / "bundle" / "index.md").write_text(
        (tmp_git_repo.root / "bundle" / "index.md").read_text(encoding="utf-8")
        + "\nstray edit\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code == 1
    assert "uncommitted changes" in result.output.lower()
    assert _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )


# --- Rail 5: commits published on a remote ----------------------------------


def test_purge_remote_present_refuses(tmp_git_repo: TmpGitRepo) -> None:
    bare = tmp_git_repo.root.parent / "bare.git"
    _git(["init", "--bare", str(bare)], cwd=tmp_git_repo.root)
    _git(["remote", "add", "origin", str(bare)], cwd=tmp_git_repo.root)
    _git(["push", "origin", "HEAD:refs/heads/main"], cwd=tmp_git_repo.root)

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code == 1
    assert "remote" in result.output.lower()
    assert "published" in result.output.lower()
    assert _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )


# --- Rail 6: typed confirmation phrase --------------------------------------


def test_purge_confirmation_mismatch_no_write(tmp_git_repo: TmpGitRepo) -> None:
    """Wrong `--confirm-phrase` aborts at rail 6, after every other rail
    passed -- proving zero writes/rewrite occurred at the very last gate."""
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", "wrong phrase"]
    )

    assert result.exit_code == 1
    assert "did not match" in result.output.lower()
    assert _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )
    assert (tmp_git_repo.root / "bundle" / f"{tmp_git_repo.source_id}.md").is_file()
    assert not (tmp_git_repo.root / ".openkos" / "fts.db").exists()


def test_purge_non_tty_without_confirm_phrase_refuses(
    tmp_git_repo: TmpGitRepo,
) -> None:
    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code == 1
    assert "confirm-phrase" in result.output.lower()
    # No-mutation: refusal at the final rail must leave every file and blob
    # untouched, and never delete/rebuild the derived indexes.
    assert _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )
    assert (tmp_git_repo.root / "bundle" / f"{tmp_git_repo.source_id}.md").is_file()
    assert (tmp_git_repo.root / "raw" / "notes.txt").is_file()
    assert not (tmp_git_repo.root / ".openkos" / "fts.db").exists()


def test_purge_bare_yes_does_not_satisfy_confirmation(
    tmp_git_repo: TmpGitRepo,
) -> None:
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", "yes"]
    )

    assert result.exit_code == 1
    assert _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )


# --- All rails pass: preview precondition + Phase B -------------------------


def test_purge_all_rails_pass_rewrite_proceeds(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precondition check, before the heavier blob-history assertions below:
    once every rail passes, `vcs_git.expunge_paths` is actually invoked."""
    called: dict[str, object] = {}
    real_expunge = vcs_git.expunge_paths

    def _spy(root: Path, rel_paths: list[str]) -> None:
        called["rel_paths"] = list(rel_paths)
        real_expunge(root, rel_paths)

    monkeypatch.setattr(vcs_git, "expunge_paths", _spy)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert called["rel_paths"] is not None


def test_purge_prints_point_of_no_return_message_before_rewrite(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """Between the confirmation match and the (potentially long, silent,
    buffered) irreversible `expunge_paths` call, `purge` must print a clear
    "beginning the irreversible rewrite -- do not interrupt" message --
    otherwise an operator who Ctrl-C's believing the process hung lands in
    the catastrophic mid-rewrite state."""
    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert "do not interrupt" in result.output.lower()


def test_purge_self_scope_removes_blobs_from_history(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """Full self-scope purge: raw + concept files are gone from ALL git
    history (rev-list/reflog/cat-file), indexes rebuilt, residual warning
    printed, no tombstone written (spec req 3 scenario 1, req 4, req 5)."""
    phrase = f"purge {tmp_git_repo.source_id}"

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert not _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )
    assert not _blob_history_contains(tmp_git_repo.root, "raw/notes.txt")
    assert _reflog_is_empty(tmp_git_repo.root)
    assert not (tmp_git_repo.root / "bundle" / f"{tmp_git_repo.source_id}.md").exists()
    assert not (tmp_git_repo.root / "raw" / "notes.txt").exists()

    assert "NOT complete right-to-be-forgotten" in result.output
    log_text = (tmp_git_repo.root / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "Tombstone" not in log_text

    assert (tmp_git_repo.root / ".openkos" / "fts.db").exists()
    assert (tmp_git_repo.root / ".openkos" / "graph.db").exists()
    assert not (tmp_git_repo.root / ".openkos" / "vectors.db").exists()


def test_purge_sibling_survives_no_over_delete(tmp_git_repo: TmpGitRepo) -> None:
    """`purge` must not over-delete: an unrelated sibling Source (with its
    OWN raw file, outside the purge set) must survive in git history AND
    on disk, with its live `index.md` bullet intact, while the target's raw
    + concept files are gone."""
    sibling_name = "other.txt"
    (tmp_git_repo.root / sibling_name).write_text("other content", encoding="utf-8")
    ingest_result = runner.invoke(app, ["ingest", sibling_name, "--auto"])
    assert ingest_result.exit_code == 0
    sibling_id = "sources/other"
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add sibling source"], cwd=tmp_git_repo.root)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert not _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )
    assert not _blob_history_contains(tmp_git_repo.root, "raw/notes.txt")

    assert _blob_history_contains(tmp_git_repo.root, f"bundle/{sibling_id}.md")
    assert _tree_contains_path(tmp_git_repo.root, "raw/other.txt")
    assert (tmp_git_repo.root / "bundle" / f"{sibling_id}.md").exists()
    assert (tmp_git_repo.root / "raw" / "other.txt").exists()

    index_text = (tmp_git_repo.root / "bundle" / "index.md").read_text(encoding="utf-8")
    assert sibling_id in index_text


def test_purge_source_scope_cascade_removes_all_blobs(
    tmp_git_repo: TmpGitRepo,
) -> None:
    _write_child_concept(
        tmp_git_repo.root,
        "concepts/child-a",
        provenance=[tmp_git_repo.source_id],
        title="Child A",
    )
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add child-a"], cwd=tmp_git_repo.root)

    phrase = f"purge {tmp_git_repo.source_id} (2 concepts)"
    result = runner.invoke(
        app,
        [
            "purge",
            tmp_git_repo.source_id,
            "--scope",
            "source",
            "--confirm-phrase",
            phrase,
        ],
    )

    assert result.exit_code == 0, result.output
    assert not _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )
    assert not _blob_history_contains(tmp_git_repo.root, "raw/notes.txt")
    assert not _blob_history_contains(tmp_git_repo.root, "bundle/concepts/child-a.md")
    assert _reflog_is_empty(tmp_git_repo.root)


def test_purge_deletes_and_rebuilds_index_no_tombstone(
    tmp_git_repo: TmpGitRepo,
) -> None:
    (tmp_git_repo.root / ".openkos").mkdir(exist_ok=True)
    (tmp_git_repo.root / ".openkos" / "vectors.db").write_bytes(b"stale")
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add stale vectors.db"], cwd=tmp_git_repo.root)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert not (tmp_git_repo.root / ".openkos" / "vectors.db").exists()
    assert (tmp_git_repo.root / ".openkos" / "fts.db").exists()
    assert (tmp_git_repo.root / ".openkos" / "graph.db").exists()
    log_text = (tmp_git_repo.root / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "Tombstone" not in log_text


def test_purge_rebuild_failure_does_not_fail_purge(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A best-effort FTS/graph rebuild failure is reported but the
    (already-irreversible, already-succeeded) purge still exits 0."""
    from openkos.state import reindex as reindex_module

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated rebuild failure")

    monkeypatch.setattr(reindex_module, "_reindex_fts", _boom)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert "failed to rebuild fts.db" in result.output.lower()
    assert not _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )


# --- Live index.md catalog cleanup (correction batch: CRITICAL 2) ----------


def test_purge_removes_live_index_catalog_bullet(tmp_git_repo: TmpGitRepo) -> None:
    """After a successful standalone purge (no prior forget), the LIVE
    `index.md` must no longer contain a catalog bullet for the purged
    concept -- otherwise the live catalog would keep pointing at a file
    that no longer exists in ANY commit, a broken bullet. A sibling
    concept's bullet must survive untouched."""
    _write_child_concept(
        tmp_git_repo.root,
        "concepts/sibling",
        provenance=[],
        title="Sibling Concept",
    )
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add unrelated sibling concept"], cwd=tmp_git_repo.root)

    index_before = (tmp_git_repo.root / "bundle" / "index.md").read_text(
        encoding="utf-8"
    )
    assert tmp_git_repo.source_id in index_before

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    index_after = (tmp_git_repo.root / "bundle" / "index.md").read_text(
        encoding="utf-8"
    )
    assert tmp_git_repo.source_id not in index_after
    assert "concepts/sibling" in index_after
    assert "Sibling Concept" in index_after


def test_purge_residual_warning_reflects_live_index_cleanup(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """The residual-leak warning must accurately reflect what purge does:
    it must NOT claim the purged id/title remain only in "historical blobs
    of index.md/log.md" -- the live index.md bullet is now removed by this
    correction, and only the HISTORY (past commits) of index.md/log.md
    (plus any prior forget tombstone still present in the LIVE log.md)
    remains a residual."""
    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert "NOT complete right-to-be-forgotten" in result.output
    assert "history" in result.output.lower()
    assert "historical blobs of index.md and log.md" not in result.output


# --- GitFinalizeError path (rewrite done, finalize failed) ------------------


def test_purge_finalize_error_surfaces_recoverability_warning(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `GitFinalizeError` (the rewrite SUCCEEDED but `git reflog
    expire`/`git gc` FAILED afterward) is a DISTINCT, non-fatal-to-the-
    already-done-rewrite path: the recoverability warning is surfaced,
    index cleanup STILL runs, and the process exits non-zero to flag that
    manual git-level follow-up is needed."""

    def _raise_finalize_error(root: Path, rel_paths: list[str]) -> None:
        raise vcs_git.GitFinalizeError(
            "git gc failed after a successful rewrite: boom\n"
            "may still be recoverable -- run: git reflog expire "
            "--expire=now --all && git gc --prune=now"
        )

    monkeypatch.setattr(vcs_git, "expunge_paths", _raise_finalize_error)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 1
    assert "succeeded" in result.output.lower()
    assert "finalize" in result.output.lower()
    assert "may still be recoverable" in result.output.lower()
    # Index cleanup still ran despite the finalize failure.
    assert (tmp_git_repo.root / ".openkos" / "fts.db").exists()


# --- Phase A writes nothing before Phase B ----------------------------------


def test_purge_phase_a_writes_nothing_before_phase_b(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before `vcs_git.expunge_paths` is ever called, Phase A (preview,
    rail evaluation) must not have written/deleted anything: monkeypatch
    `expunge_paths` to raise BEFORE doing any real work, then assert every
    workspace file (including derived `.openkos/*.db`, if any existed) is
    untouched."""

    def _boom(root: Path, rel_paths: list[str]) -> None:
        raise AssertionError("expunge_paths must be the ONLY write trigger")

    monkeypatch.setattr(vcs_git, "expunge_paths", _boom)

    before = _committed_snapshot(tmp_git_repo.root)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, AssertionError)
    after = _committed_snapshot(tmp_git_repo.root)
    assert before == after
    assert not (tmp_git_repo.root / ".openkos" / "fts.db").exists()


# --- Raw-path resolution: malformed/derived cases ---------------------------


def test_purge_derived_concept_has_no_raw_path_to_skip(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """A derived concept (no `resource` frontmatter) contributes only its
    own `bundle/<id>.md` -- no raw path is ever targeted for it."""
    _write_child_concept(
        tmp_git_repo.root,
        "concepts/child-a",
        provenance=[tmp_git_repo.source_id],
        title="Child A",
    )

    result = runner.invoke(app, ["purge", "concepts/child-a", "--scope", "self"])

    assert "bundle/concepts/child-a.md" in result.output
    assert "raw/" not in result.output


def test_purge_malformed_resource_warns_not_refuses(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """A Source whose `resource` frontmatter is malformed (escapes `raw/`)
    is WARNED about, not refused -- its own bundle file is still targeted,
    but its raw path is skipped."""
    source_path = tmp_git_repo.root / "bundle" / f"{tmp_git_repo.source_id}.md"
    text = source_path.read_text(encoding="utf-8")
    assert "resource: raw/notes.txt" in text
    source_path.write_text(
        text.replace("resource: raw/notes.txt", "resource: ../outside.txt"),
        encoding="utf-8",
    )
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Malform resource"], cwd=tmp_git_repo.root)

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code != 0  # refuses later (no --confirm-phrase), not here
    assert "malformed" in result.output.lower()
    assert f"bundle/{tmp_git_repo.source_id}.md" in result.output
