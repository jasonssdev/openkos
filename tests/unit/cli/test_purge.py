"""Unit tests for the `purge` CLI command: the irreversible, true-erasure
counterpart to `forget` (MVP-2 right-to-be-forgotten, Slice 1). Reuses
`forget`'s Phase A (path safety, purge-set resolution, reference-aware
detection) unchanged, then runs six fail-closed safety rails, ALL before any
write, before invoking `vcs.git.expunge_paths` -- the point of no return.

Most tests shell out to a REAL git repository via the `tmp_git_repo` fixture
(`tests/unit/vcs/conftest.py`) -- `purge`'s whole job is to safely drive real
`git`/`git-filter-repo`, so its tests must prove that against the real
binary, not a mock."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner, Result, _NamedTextIOWrapper

from openkos.bundle import decisions as bundle_decisions
from openkos.bundle import index as bundle_index
from openkos.bundle import ledger as bundle_ledger
from openkos.cli import main
from openkos.cli.main import app
from openkos.vcs import git as vcs_git
from tests.unit.cli.conftest import changed_paths, snapshot_with_mtime
from tests.unit.vcs.conftest import TmpGitRepo, _git, isolate_git_identity, tmp_git_repo

__all__ = ["tmp_git_repo"]

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _write_plain_concept(
    tmp_path: Path, concept_id: str, *, title: str = "Concept"
) -> None:
    """Write a bare concept file, no `index.md` bullet -- used to build a
    `merge` fixture (a survivor's own inbound/index state does not matter
    for these tests)."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    concept_path.write_text(
        f"---\ntype: Concept\ntitle: {title}\n---\n\n# {title}\n\nBody.\n",
        encoding="utf-8",
    )


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


# --- #142: dense-retrieval-degraded warning ---------------------------------


def test_purge_success_output_warns_dense_retrieval_degraded(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """Successful `purge` output includes a warning that dense retrieval is
    degraded and an `openkos reindex` instruction (privacy-purge spec:
    "Successful purge warns about degraded dense retrieval")."""
    phrase = f"purge {tmp_git_repo.source_id}"

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert "dense retrieval" in result.output.lower()
    assert "degraded" in result.output.lower()
    assert "openkos reindex" in result.output


def test_purge_does_not_prompt_or_auto_reindex(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful purge that dropped `vectors.db` never prompts for
    confirmation to reindex and never invokes `reindex` itself -- message-
    only (privacy-purge spec: "No interactive prompt or auto-reindex
    occurs")."""
    called = {"reindex": False}

    def _spy_reindex(*args: object, **kwargs: object) -> None:
        called["reindex"] = True

    monkeypatch.setattr(main, "reindex", _spy_reindex)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert called["reindex"] is False


# --- Post-rewrite live-tree auto-commit -------------------------------------


def _commit_count(root: Path) -> int:
    result = vcs_git._run(["git", "rev-list", "--count", "HEAD"], cwd=root)
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip())


def _last_commit_subject(root: Path) -> str:
    result = vcs_git._run(["git", "log", "-1", "--format=%s"], cwd=root)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _last_commit_files(root: Path) -> set[str]:
    result = vcs_git._run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], cwd=root
    )
    assert result.returncode == 0, result.stderr
    return {line for line in result.stdout.splitlines() if line}


def test_purge_clean_cleanup_creates_no_commit_and_no_warning(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """The DEFAULT self-scope purge is the "clean" case: `git-filter-repo`'s
    own history content-scrub already removed the live catalog bullet/log
    entry from the checked-out tip, so `_purge_clean_live_*` is a no-op,
    `paths_dirty` reports `False`, and NO auto-commit (hence no WARNING) is
    attempted (privacy-purge spec: "Empty diff after filter-repo's own
    rewrite still succeeds")."""
    before = _commit_count(tmp_git_repo.root)
    phrase = f"purge {tmp_git_repo.source_id}"

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert _commit_count(tmp_git_repo.root) == before
    assert "auto-commit" not in result.output.lower()
    assert vcs_git.is_clean(tmp_git_repo.root) is True


def test_purge_non_no_op_cleanup_creates_exactly_one_commit(
    tmp_git_repo: TmpGitRepo,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """When the live-tree cleanup DOES produce a real change (simulated
    here since filter-repo's own scrub usually already covers it -- design:
    "frequently a no-op", not always), `purge` commits exactly once,
    staging only `bundle/index.md`/`bundle/log.md`, message `openkos:
    purge <id>`, leaving a clean tree (privacy-purge spec: "Successful
    purge leaves a clean working tree via commit")."""
    # Isolated identity config lives OUTSIDE `tmp_git_repo.root` -- writing
    # it inside the workspace itself would leave a forever-untracked file,
    # breaking every clean-tree assertion for a reason unrelated to
    # auto-commit (mirrors `test_main_autocommit.py`'s `_init_workspace`).
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    real_remove = bundle_index.remove_index_entry

    def _fake_remove(index_text: str, concept_id: str) -> tuple[str, int]:
        text, count = real_remove(index_text, concept_id)
        return text + "\n<!-- purge-cleanup-marker -->\n", count + 1

    monkeypatch.setattr(bundle_index, "remove_index_entry", _fake_remove)
    before = _commit_count(tmp_git_repo.root)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert _commit_count(tmp_git_repo.root) == before + 1
    assert _last_commit_subject(tmp_git_repo.root) == (
        f"openkos: purge {tmp_git_repo.source_id}"
    )
    # `commit_paths` stages BOTH scoped paths (`git add -- <paths>`), but
    # `git diff-tree` only lists paths that actually CHANGED between this
    # commit and its parent -- `log.md` was untouched here (only
    # `remove_index_entry` was monkeypatched to force a real diff), so only
    # `index.md` appears; the assertion is a subset check, not an exact
    # match, so it stays valid whether or not log.md also changed.
    committed_files = _last_commit_files(tmp_git_repo.root)
    assert committed_files <= {"bundle/index.md", "bundle/log.md"}
    assert "bundle/index.md" in committed_files
    assert vcs_git.is_clean(tmp_git_repo.root) is True


def test_purge_autocommit_message_includes_cascade_count(
    tmp_git_repo: TmpGitRepo,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """`--scope source` with additional cascaded members uses the `(+N)`
    commit-message form, `N = len(purge_ids) - 1` (design: "Message")."""
    _write_child_concept(
        tmp_git_repo.root,
        "concepts/child-a",
        provenance=[tmp_git_repo.source_id],
        title="Child A",
    )
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add child"], cwd=tmp_git_repo.root)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    real_remove = bundle_index.remove_index_entry

    def _fake_remove(index_text: str, concept_id: str) -> tuple[str, int]:
        text, count = real_remove(index_text, concept_id)
        return text + "\n<!-- purge-cleanup-marker -->\n", count + 1

    monkeypatch.setattr(bundle_index, "remove_index_entry", _fake_remove)

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
    assert _last_commit_subject(tmp_git_repo.root) == (
        f"openkos: purge {tmp_git_repo.source_id} (+1)"
    )


def test_purge_autocommit_failure_is_non_fatal(
    tmp_git_repo: TmpGitRepo,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A commit-step `GitError` (e.g. `commit_paths` raising) prints a non-
    fatal WARNING to stderr, and `purge` still exits with its normal
    success code -- the history rewrite and index cleanup already
    irreversibly landed (privacy-purge spec: "Commit failure does not fail
    the already-irreversible purge")."""
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    monkeypatch.setattr(vcs_git, "paths_dirty", lambda cwd, rel_paths: True)

    def _raise(cwd: Path, rel_paths: list[str], message: str) -> None:
        raise vcs_git.GitError("simulated commit failure")

    monkeypatch.setattr(vcs_git, "commit_paths", _raise)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert "WARNING" in result.output
    assert "simulated commit failure" in result.output


def test_purge_falls_through_to_autocommit_when_dirty_probe_raises(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `paths_dirty` itself raises `GitError` (e.g. a broken repo probe),
    `purge` falls through and still attempts `_autocommit` -- non-fatal
    either way, never blocking the already-irreversible purge (design:
    "purge empty-diff guard")."""

    def _raise_probe(cwd: Path, rel_paths: list[str]) -> bool:
        raise vcs_git.GitError("simulated probe failure")

    monkeypatch.setattr(vcs_git, "paths_dirty", _raise_probe)
    autocommit_calls: list[tuple[Path, list[str], str]] = []
    real_autocommit = main._autocommit

    def _spy_autocommit(root: Path, paths: list[str], message: str) -> None:
        autocommit_calls.append((root, list(paths), message))
        real_autocommit(root, paths, message)

    monkeypatch.setattr(main, "_autocommit", _spy_autocommit)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert len(autocommit_calls) == 1
    assert autocommit_calls[0][1] == ["bundle/index.md", "bundle/log.md"]


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


def test_purge_force_leaves_dangling_reference_detected_by_lint_and_status(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """End-to-end acceptance for #141: after `purge <id> --force` erases a
    concept another document still references, the now-dangling reference is
    detected by BOTH `lint` (under "Dangling references") and `status`
    (under "Needs attention") -- neither did before this change. The
    referrer is committed first so the tree is clean for purge's rails; the
    detect-only remedy means purge itself leaves the referrer untouched
    (exactly the dangling state #141 reports), and the follow-up read
    commands surface it."""
    referrer = tmp_git_repo.root / "bundle" / "concepts" / "referrer.md"
    referrer.parent.mkdir(parents=True, exist_ok=True)
    referrer.write_text(
        "---\ntype: Concept\ntitle: Referrer\n"
        "relations:\n"
        f"  - target: {tmp_git_repo.source_id}\n"
        "    type: relates-to\n"
        "---\n\n# Referrer\n\nBody.\n",
        encoding="utf-8",
    )
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "add referrer"], cwd=tmp_git_repo.root)

    phrase = f"purge {tmp_git_repo.source_id}"
    purge_result = runner.invoke(
        app,
        ["purge", tmp_git_repo.source_id, "--force", "--confirm-phrase", phrase],
    )
    assert purge_result.exit_code == 0, purge_result.output

    lint_result = runner.invoke(app, ["lint"])
    assert lint_result.exit_code == 0
    assert "concepts/referrer" in lint_result.stdout
    assert tmp_git_repo.source_id in lint_result.stdout

    status_result = runner.invoke(app, ["status"])
    assert status_result.exit_code == 0
    assert tmp_git_repo.source_id in status_result.stdout


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
    # `ingest` builds `fts.db` at the end of its run since #553, so the
    # no-mutation assertion below compares bytes rather than absence.
    fts_db = workspace / ".openkos" / "fts.db"
    fts_bytes_before = fts_db.read_bytes()

    result = runner.invoke(app, ["purge", "sources/notes"])

    assert result.exit_code == 1
    assert "git repository root" in result.output.lower()
    # No-mutation: refusal at rail 3 must leave every file untouched, and
    # must never attempt to delete/rebuild the derived indexes.
    assert concept_path.is_file()
    assert raw_path.is_file()
    assert fts_db.read_bytes() == fts_bytes_before


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


def test_purge_dirty_tree_refusal_names_the_offending_paths(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """#647: the engine owns this repo and commits on the user's behalf, so
    its refusal must name WHAT is dirty rather than sending the user to
    `git status` to interpret an engine message."""
    (tmp_git_repo.root / "bundle" / "index.md").write_text(
        (tmp_git_repo.root / "bundle" / "index.md").read_text(encoding="utf-8")
        + "\nstray edit\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code == 1
    assert "bundle/index.md" in result.output


def test_purge_dirty_tree_refusal_names_an_untracked_editor_dir(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """The exact #647 chain: an untracked `.obsidian/` vault blocks purge in
    a workspace whose `.gitignore` predates the #647 template (simulated by
    stripping the entry) -- the refusal must name the vault directory."""
    gitignore = tmp_git_repo.root / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8").replace(".obsidian/\n", ""),
        encoding="utf-8",
    )
    _git(["commit", "-am", "strip the #647 gitignore entry"], cwd=tmp_git_repo.root)
    vault = tmp_git_repo.root / ".obsidian"
    vault.mkdir()
    (vault / "workspace.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code == 1
    assert ".obsidian/" in result.output


def test_purge_dirty_tree_refusal_caps_the_path_list(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """Many dirty paths must not flood stderr: at most 10 are named, with
    an honest `... and N more` tail."""
    for i in range(13):
        (tmp_git_repo.root / f"stray-{i:02d}.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code == 1
    assert "stray-00.txt" in result.output
    assert "stray-09.txt" in result.output
    assert "stray-10.txt" not in result.output
    assert "and 3 more" in result.output


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
    # `ingest` builds `fts.db` since #553: compare bytes, not absence.
    fts_db = tmp_git_repo.root / ".openkos" / "fts.db"
    fts_bytes_before = fts_db.read_bytes()

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", "wrong phrase"]
    )

    assert result.exit_code == 1
    assert "did not match" in result.output.lower()
    assert _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )
    assert (tmp_git_repo.root / "bundle" / f"{tmp_git_repo.source_id}.md").is_file()
    assert fts_db.read_bytes() == fts_bytes_before


def test_purge_non_tty_without_confirm_phrase_refuses(
    tmp_git_repo: TmpGitRepo,
) -> None:
    # `ingest` builds `fts.db` since #553: compare bytes, not absence.
    fts_db = tmp_git_repo.root / ".openkos" / "fts.db"
    fts_bytes_before = fts_db.read_bytes()

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
    assert fts_db.read_bytes() == fts_bytes_before


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
    once every rail passes, `vcs_git.expunge_paths` is actually invoked, with
    the purge-set as its `scrub_identities` kwarg (Slice 2: threading the
    scrub-set into the SAME `expunge_paths` call, never omitted)."""
    called: dict[str, object] = {}
    real_expunge = vcs_git.expunge_paths

    def _spy(
        root: Path, rel_paths: list[str], *, scrub_identities: list[str] | None = None
    ) -> None:
        called["rel_paths"] = list(rel_paths)
        called["scrub_identities"] = (
            list(scrub_identities) if scrub_identities is not None else None
        )
        real_expunge(root, rel_paths, scrub_identities=scrub_identities)

    monkeypatch.setattr(vcs_git, "expunge_paths", _spy)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert called["rel_paths"] is not None
    assert called["scrub_identities"] == [tmp_git_repo.source_id]


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
    history (rev-list/reflog/cat-file), indexes rebuilt, no tombstone
    written (spec req 3 scenario 1, req 4, req 5)."""
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

    log_text = (tmp_git_repo.root / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "Tombstone" not in log_text

    assert (tmp_git_repo.root / ".openkos" / "fts.db").exists()
    assert (tmp_git_repo.root / ".openkos" / "graph.db").exists()
    assert not (tmp_git_repo.root / ".openkos" / "vectors.db").exists()


def test_purge_announces_the_restore_is_a_full_re_embed(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """Issue #698: dropping `vectors.db` takes the `vector_meta` content-hash
    cache AND the `meta` embedding-model tag with it -- both tables live in
    that one file -- so the next `openkos reindex` re-embeds EVERY surviving
    document at one embedding call each and reports it as a model change
    (`unset -> <model>`) rather than as the store loss it actually is.

    The old closing line said only that a reindex was needed, which reads as
    a cheap repair. On a corpus of a few thousand documents it is not, and it
    arrives right after an irreversible operation. The line must therefore
    disclose the COST and the reason the model-change wording will appear."""
    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert not (tmp_git_repo.root / ".openkos" / "vectors.db").exists()
    assert "full re-embed" in result.output, (
        "the closing line must name the full re-embed, not merely that a "
        "reindex is needed"
    )
    assert "no embedding-model tag stored" in result.output, (
        "and must pre-empt reindex's corrected absent-tag wording, so an "
        "operator does not read the store loss as a configuration change"
    )
    assert "embedding model changed" not in result.output, (
        "must NOT quote the retired 'embedding model changed' wording -- "
        "there is no old tag left to compare against a new one once "
        "vectors.db (which held it) is dropped"
    )


def test_purging_a_merge_survivor_removes_its_ledger_sidecar_from_history(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """privacy-purge spec: "Whole-History Expunge Covers The Ledger Sidecar
    Store", scenario "Purging a merge survivor removes its ledger sidecar
    from history" -- the survivor's `bundle/.state/ledger/` sidecar is gone
    from `git rev-list --objects --all` and the reflog, in the SAME single
    `git filter-repo` pass as the concept's own file expunge (task 3.4's
    threat-matrix row)."""
    _write_plain_concept(tmp_git_repo.root, "concepts/survivor", title="Survivor")
    _write_plain_concept(tmp_git_repo.root, "concepts/absorbed", title="Absorbed")
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add survivor + absorbed"], cwd=tmp_git_repo.root)

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.output
    sidecar_path = bundle_ledger.ledger_path_for(
        "concepts/survivor", tmp_git_repo.root / "bundle"
    )
    assert sidecar_path.is_file(), "fixture setup: merge must create a sidecar"
    sidecar_rel = sidecar_path.relative_to(tmp_git_repo.root).as_posix()
    # `merge`'s own `_autocommit` is best-effort and this fixture's identity
    # is deliberately UNSET (`isolate_git_identity`, no name/email) outside
    # `_git`'s pinned env, so land the merge in history explicitly -- mirrors
    # `test_purge_sibling_survives_no_over_delete`'s own manual commit.
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Merge survivor <- absorbed"], cwd=tmp_git_repo.root)
    assert _blob_history_contains(tmp_git_repo.root, sidecar_rel)

    phrase = "purge concepts/survivor"
    result = runner.invoke(
        app, ["purge", "concepts/survivor", "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert not _blob_history_contains(tmp_git_repo.root, sidecar_rel)
    assert _reflog_is_empty(tmp_git_repo.root)
    assert not sidecar_path.exists()


def test_purge_scrubs_referring_bullets_from_surviving_ledger_sidecars(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """Issue #689, as reported: `purge` expunged the target from ALL git
    history while its title, description and link survived in a THIRD
    concept's ledger sidecar -- the purged object was never absorbed by that
    survivor, only REFERENCED from its `## Related` section, so neither the
    entry-drop (`absorbed_id` does not match) nor `_excise_merged_sections`
    (no `## Merged content` delimiter) touched it.

    `purge` is the irreversible right-to-be-forgotten verb and cannot be
    re-run to correct a partial erasure, so this is asserted end to end
    against the real `git filter-repo` binary, not only on the primitive."""
    root = tmp_git_repo.root
    _write_plain_concept(root, "concepts/purge-target", title="Filosofía del Proyecto")
    survivor_path = root / "bundle" / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True, exist_ok=True)
    survivor_path.write_text(
        "---\ntype: Concept\ntitle: Survivor\n---\n\n# Survivor\n\nBody.\n\n"
        "## Related\n\n"
        "* [Filosofía del Proyecto](/concepts/purge-target.md) - Objetivo central\n",
        encoding="utf-8",
    )
    _write_plain_concept(root, "concepts/absorbed", title="Absorbed")
    _git(["add", "-A"], cwd=root)
    _git(["commit", "-m", "Add referring survivor + absorbed"], cwd=root)

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.output
    sidecar = bundle_ledger.ledger_path_for("concepts/survivor", root / "bundle")
    assert "Filosofía del Proyecto" in sidecar.read_text(encoding="utf-8"), (
        "fixture precondition: the reference must be snapshotted pre-purge"
    )
    _git(["add", "-A"], cwd=root)
    _git(["commit", "-m", "Merge survivor <- absorbed"], cwd=root)

    # `--force`: the survivor's LIVE `## Related` bullet is an inbound
    # reference, so the ordinary gate refuses. Leaving that link dangling is
    # exactly the documented `--force` contract, and it is the shape the
    # #689 report was filed from -- a purged object still referenced
    # elsewhere is precisely when the snapshot residue matters.
    phrase = "purge concepts/purge-target"
    result = runner.invoke(
        app, ["purge", "concepts/purge-target", "--confirm-phrase", phrase, "--force"]
    )

    assert result.exit_code == 0, result.output
    assert sidecar.is_file(), "the unrelated survivor's sidecar itself must remain"
    after = sidecar.read_text(encoding="utf-8")
    assert "Filosofía del Proyecto" not in after
    assert "concepts/purge-target" not in after


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
    # `-f` is required: `openkos init` (Slice 1, git-lifecycle) now writes a
    # `.gitignore` that ignores `.openkos/` (it is the engine's own derived
    # cache, never meant to be committed) -- a plain `git add -A` would
    # silently stage nothing here, without `-f` to override the ignore for
    # this test's own deliberate "stale committed vectors.db" setup.
    _git(["add", "-f", "-A"], cwd=tmp_git_repo.root)
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


def test_purge_no_residual_warning_printed(tmp_git_repo: TmpGitRepo) -> None:
    """Slice 2: after a successful purge, NO warning claiming purged content
    remains in `index.md`/`log.md` (live or historical) is printed -- the
    history content-scrub (git.py `_FILE_INFO_CALLBACK_SNIPPET`) plus the
    live index/log cleanup means no such residual exists. Asserts by
    substring absence of the OLD warning's distinctive wording, so this
    test still fails if a similar warning were reintroduced under a new
    name."""
    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert "NOT complete right-to-be-forgotten" not in result.output
    assert "REMAIN only in the" not in result.output
    assert "content-scrub) closes this residual" not in result.output


# --- GitFinalizeError path (rewrite done, finalize failed) ------------------


def test_purge_finalize_error_surfaces_recoverability_warning(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `GitFinalizeError` (the rewrite SUCCEEDED but `git reflog
    expire`/`git gc` FAILED afterward) is a DISTINCT, non-fatal-to-the-
    already-done-rewrite path: the recoverability warning is surfaced,
    index cleanup STILL runs, and the process exits non-zero to flag that
    manual git-level follow-up is needed."""

    def _raise_finalize_error(
        root: Path, rel_paths: list[str], *, scrub_identities: list[str] | None = None
    ) -> None:
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

    def _boom(
        root: Path, rel_paths: list[str], *, scrub_identities: list[str] | None = None
    ) -> None:
        raise AssertionError("expunge_paths must be the ONLY write trigger")

    monkeypatch.setattr(vcs_git, "expunge_paths", _boom)

    before = _committed_snapshot(tmp_git_repo.root)
    # `ingest` builds `fts.db` at the end of its run since #553, so the
    # fixture workspace arrives here WITH a derived store on disk. Phase A
    # must leave its bytes untouched -- the old `not exists()` assertion
    # proved a weaker thing (that Phase A never CREATED one).
    fts_db = tmp_git_repo.root / ".openkos" / "fts.db"
    fts_bytes_before = fts_db.read_bytes()

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, AssertionError)
    after = _committed_snapshot(tmp_git_repo.root)
    assert before == after
    assert fts_db.read_bytes() == fts_bytes_before


# --- Live log.md tombstone cleanup (Slice 2) --------------------------------


def test_purge_removes_live_log_tombstone(tmp_git_repo: TmpGitRepo) -> None:
    """A prior `forget` tombstone for the purge target, still present in the
    LIVE `log.md`, is removed once `purge` completes -- while an unrelated
    sibling tombstone survives untouched (spec: "Prior forget tombstone
    removed from live log.md")."""
    log_path = tmp_git_repo.root / "bundle" / "log.md"
    sibling_id = "sources/sibling-notes"
    log_path.write_text(
        log_path.read_text(encoding="utf-8").replace(
            "* **Initialization**",
            f"* **Tombstone** (12:00:00Z): Removed [Notes]"
            f"(/{tmp_git_repo.source_id}.md) (id: {tmp_git_repo.source_id}).\n"
            f"* **Tombstone** (12:00:00Z): Removed [Sibling]"
            f"(/{sibling_id}.md) (id: {sibling_id}).\n"
            "* **Initialization**",
        ),
        encoding="utf-8",
    )
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add prior tombstones"], cwd=tmp_git_repo.root)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    log_after = (tmp_git_repo.root / "bundle" / "log.md").read_text(encoding="utf-8")
    assert tmp_git_repo.source_id not in log_after
    assert sibling_id in log_after


def test_purge_finalize_error_still_cleans_live_log_tombstone(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the `GitFinalizeError` path (rewrite succeeded, finalize failed),
    the live `log.md` tombstone cleanup STILL runs -- mirroring
    `_purge_clean_live_index`'s same-path contract."""
    log_path = tmp_git_repo.root / "bundle" / "log.md"
    log_path.write_text(
        log_path.read_text(encoding="utf-8").replace(
            "* **Initialization**",
            f"* **Tombstone** (12:00:00Z): Removed [Notes]"
            f"(/{tmp_git_repo.source_id}.md) (id: {tmp_git_repo.source_id}).\n"
            "* **Initialization**",
        ),
        encoding="utf-8",
    )
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add prior tombstone"], cwd=tmp_git_repo.root)

    def _raise_finalize_error(
        root: Path, rel_paths: list[str], *, scrub_identities: list[str] | None = None
    ) -> None:
        raise vcs_git.GitFinalizeError("boom -- may still be recoverable")

    monkeypatch.setattr(vcs_git, "expunge_paths", _raise_finalize_error)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 1
    log_after = (tmp_git_repo.root / "bundle" / "log.md").read_text(encoding="utf-8")
    assert tmp_git_repo.source_id not in log_after


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


def test_purge_preview_names_the_absence_of_raw_material(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """When the purge set resolves NO raw source path, the preview says so
    in words instead of merely omitting one.

    The preview is the last thing an operator reads before typing the
    irreversible confirmation phrase, and an omission reads exactly like a
    short list: the distinction that decides whether the source material
    survives -- is this id a Source or a derived concept -- is invisible at
    the call site. A member whose `resource` is MALFORMED already earns a
    `!` warning, so the case that stayed silent was the common one."""
    _write_child_concept(
        tmp_git_repo.root,
        "concepts/child-a",
        provenance=[tmp_git_repo.source_id],
        title="Child A",
    )

    result = runner.invoke(app, ["purge", "concepts/child-a", "--scope", "self"])

    assert (
        "no raw source material is part of this purge -- no purge-set member "
        "contributes a raw source path (only a Source does)"
    ) in result.output


def test_purge_preview_omits_the_absence_line_when_a_raw_path_resolves(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """The absence line is CONDITIONAL: purging a Source whose `resource`
    resolves lists that raw path and says nothing about an absence.

    Without this negative case the line could be printed unconditionally --
    contradicting the very targets printed a line above it -- and the
    positive test would not notice."""
    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert "raw/notes.txt" in result.output
    assert "no raw source material" not in result.output


def test_purge_malformed_resource_warns_not_refuses(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """A Source whose `resource` frontmatter is malformed (escapes `raw/`)
    is WARNED about, not refused -- its own bundle file is still targeted,
    but its raw path is skipped.

    This is also the COMBINED path: the member is a Source, so the
    malformed-resource warning fires, AND nothing resolved, so the absence
    line fires too. Both are asserted here because the absence line is
    worded as a general rule ("only a Source does") precisely so that it
    stays true when the set DOES contain a Source whose `resource` merely
    failed validation -- a wording that would be false if it claimed the
    set held no Source. The specific reason is the `!` warning's job; the
    absence line's job is that the operator never reads a short list and
    mistakes it for a complete one."""
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
    assert "no raw source material is part of this purge" in result.output


# -- #321: re-validate every write AND delete target after the typed phrase --


def _prompt_after(
    monkeypatch: pytest.MonkeyPatch, edit: Callable[[], object], *, answer: str
) -> None:
    """`confirm_after`'s analog for `purge`'s rail-6 typed-phrase prompt:
    `purge` confirms via `typer.prompt`, never `typer.confirm`, so the
    conftest helper cannot reach its window. The stub runs `edit` and then
    types `answer` (the exact expected phrase) back, so a refusal after it
    ran is the guard's own, never a mistyped phrase.

    The typed phrase makes this window WIDER than any yes/no prompt in
    wall-clock terms -- the operator re-reads the preview and types a whole
    sentence -- which is what makes `purge` the verb where prompt-window
    drift is likeliest, not least (#321).
    """

    def _prompt(*args: object, **kwargs: object) -> str:
        edit()
        return answer

    monkeypatch.setattr(typer, "prompt", _prompt)


def _cascade_child(tmp_git_repo: TmpGitRepo) -> str:
    """One committed cascade child, so `--scope source` resolves a 2-member
    purge set and the mapping's DYNAMIC descendant loop is exercised, not
    just the root's fixed `concept_path` entry."""
    child_id = "concepts/child-a"
    _write_child_concept(
        tmp_git_repo.root, child_id, provenance=[tmp_git_repo.source_id]
    )
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add cascade child"], cwd=tmp_git_repo.root)
    return child_id


@pytest.mark.parametrize("target", ["bundle/index.md", "bundle/log.md"])
def test_a_write_target_edited_during_the_prompt_is_refused(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#321: rail 4 (clean tree) runs BEFORE the typed-phrase prompt, so an
    edit landing while the operator types the phrase is invisible to every
    rail -- and `git filter-repo` then checks out rewritten history over it.
    `index.md`/`log.md` are the live files purge itself rewrites afterwards,
    so they are the mapping's fixed write-target keys."""
    _simulate_tty(monkeypatch)
    target_path = tmp_git_repo.root / target
    concurrent = "hand-edited while the phrase was typed\n"
    before = snapshot_with_mtime(tmp_git_repo.root)
    _prompt_after(
        monkeypatch,
        lambda: target_path.write_text(concurrent, encoding="utf-8"),
        answer=f"purge {tmp_git_repo.source_id}",
    )

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    # The edit survives, nothing was expunged, history is intact.
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert _tree_contains_path(tmp_git_repo.root, "raw/notes.txt")
    after = snapshot_with_mtime(tmp_git_repo.root)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_the_root_delete_target_edited_during_the_prompt_is_refused(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mapping's fixed `concept_path` entry, on the DEFAULT scope --
    where the purge set collapses to the root alone, so the descendant loop
    is empty and this one key is the entire delete-target protection
    (mirrors `test_forget.py`'s root-concept lesson, #313 review R3)."""
    _simulate_tty(monkeypatch)
    target = f"bundle/{tmp_git_repo.source_id}.md"
    target_path = tmp_git_repo.root / target
    concurrent = "hand-edited while the phrase was typed\n"
    before = snapshot_with_mtime(tmp_git_repo.root)
    _prompt_after(
        monkeypatch,
        lambda: target_path.write_text(concurrent, encoding="utf-8"),
        answer=f"purge {tmp_git_repo.source_id}",
    )

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    # #319: the root concept is a DELETE target -- the message must say the
    # verb was about to unlink it, and the footer must cover both halves.
    assert "delete target(s)" in result.stderr
    assert "nothing was deleted" in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert _blob_history_contains(
        tmp_git_repo.root, f"bundle/{tmp_git_repo.source_id}.md"
    )
    after = snapshot_with_mtime(tmp_git_repo.root)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_a_cascade_delete_target_edited_during_the_prompt_is_refused(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mapping's DYNAMIC descendant loop: a `--scope source` cascade
    member edited during the prompt would be expunged from history with the
    edit inside it -- destroyed outright, strictly worse than overwritten."""
    child_id = _cascade_child(tmp_git_repo)
    _simulate_tty(monkeypatch)
    target = f"bundle/{child_id}.md"
    target_path = tmp_git_repo.root / target
    concurrent = "hand-edited while the phrase was typed\n"
    before = snapshot_with_mtime(tmp_git_repo.root)
    _prompt_after(
        monkeypatch,
        lambda: target_path.write_text(concurrent, encoding="utf-8"),
        answer=f"purge {tmp_git_repo.source_id} (2 concepts)",
    )

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id, "--scope", "source"])

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert _blob_history_contains(tmp_git_repo.root, f"bundle/{child_id}.md")
    after = snapshot_with_mtime(tmp_git_repo.root)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_a_delete_target_deleted_during_the_prompt_is_refused(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member that VANISHED is drift too: the preview named it and the
    history rewrite would still expunge it, so proceeding would erase
    history for a live state the operator was never shown."""
    child_id = _cascade_child(tmp_git_repo)
    _simulate_tty(monkeypatch)
    deleted_path = tmp_git_repo.root / "bundle" / f"{child_id}.md"
    before = snapshot_with_mtime(tmp_git_repo.root)
    _prompt_after(
        monkeypatch,
        deleted_path.unlink,
        answer=f"purge {tmp_git_repo.source_id} (2 concepts)",
    )

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id, "--scope", "source"])

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert f"bundle/{child_id}.md" in result.stderr
    # #319: the VANISHED bucket, on a delete target -- and the advice must
    # be restore-or-confirm, never a plain re-run that refuses again.
    assert "delete target(s) vanished" in result.stderr
    assert "restore" in result.stderr
    # Nothing else was deleted and history is intact.
    assert (tmp_git_repo.root / "bundle" / f"{tmp_git_repo.source_id}.md").is_file()
    assert _tree_contains_path(tmp_git_repo.root, "raw/notes.txt")
    after = snapshot_with_mtime(tmp_git_repo.root)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle") / f"{child_id}.md"}


def test_a_crlf_rewrite_of_a_delete_target_during_the_prompt_is_refused(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#306's constraint, re-pinned for `purge`: a line-ending-only rewrite
    is a real edit, `read_text`'s universal-newline translation would make
    it compare equal to its own LF snapshot, and the history rewrite would
    then destroy it."""
    _simulate_tty(monkeypatch)
    target = f"bundle/{tmp_git_repo.source_id}.md"
    target_path = tmp_git_repo.root / target
    concurrent = target_path.read_bytes().replace(b"\n", b"\r\n")
    assert concurrent != target_path.read_bytes()
    before = snapshot_with_mtime(tmp_git_repo.root)
    _prompt_after(
        monkeypatch,
        lambda: target_path.write_bytes(concurrent),
        answer=f"purge {tmp_git_repo.source_id}",
    )

    result = runner.invoke(app, ["purge", tmp_git_repo.source_id])

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_bytes() == concurrent
    after = snapshot_with_mtime(tmp_git_repo.root)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_targets_that_were_already_crlf_are_not_drift(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: guarded targets already CRLF at rest, COMMITTED
    (rail 4 requires a clean tree), untouched during the run, must not be
    reported as drift -- otherwise `purge` refuses forever on a CRLF
    workspace."""
    # Repo-local, so it wins over any HOST-global `core.autocrlf` for the
    # out-of-band `_git` commit below (whose env snapshot predates the
    # fixture's config isolation) -- without it, `git add` on such a host
    # normalizes the CRLF away and this test commits nothing.
    _git(["config", "core.autocrlf", "false"], cwd=tmp_git_repo.root)
    for rel in (
        "bundle/index.md",
        "bundle/log.md",
        f"bundle/{tmp_git_repo.source_id}.md",
    ):
        path = tmp_git_repo.root / rel
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Normalize to CRLF"], cwd=tmp_git_repo.root)
    phrase = f"purge {tmp_git_repo.source_id}"

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert "refusing to write" not in result.stderr
    assert not (tmp_git_repo.root / "bundle" / f"{tmp_git_repo.source_id}.md").exists()


def test_drift_on_the_unprompted_path_is_refused(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#321: `--confirm-phrase` skips the prompt but not the window it stood
    in, so the guard must run unconditionally.

    There is no prompt to hang the edit on here, so it lands inside rail 6
    itself -- via a delegating wrap of `_purge_confirm_phrase`, the last
    pre-guard step BOTH confirmation paths share, which runs strictly after
    rail 4's clean-tree check. That placement is the point: an edit landing
    there is invisible to every rail, and only the drift guard is left to
    refuse it."""
    real_phrase = main._purge_confirm_phrase
    target = "bundle/index.md"
    target_path = tmp_git_repo.root / target
    concurrent = "hand-edited after the clean-tree rail\n"

    def _phrase_and_edit(
        canonical_id: str, purge_ids: list[str], scope: main._PurgeScope
    ) -> str:
        target_path.write_text(concurrent, encoding="utf-8")
        return real_phrase(canonical_id, purge_ids, scope)

    monkeypatch.setattr(main, "_purge_confirm_phrase", _phrase_and_edit)
    before = snapshot_with_mtime(tmp_git_repo.root)
    phrase = f"purge {tmp_git_repo.source_id}"

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert _tree_contains_path(tmp_git_repo.root, "raw/notes.txt")
    after = snapshot_with_mtime(tmp_git_repo.root)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_an_edit_landing_after_the_snapshot_observation_is_refused_by_rail_4(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318's race, pinned for `purge` (#327 follow-up) -- with a twist no
    other guarded verb has: `purge` CANNOT host the exit-3 drift-guard pin
    for this interleaving, and that is worth pinning in itself.

    Every other verb's `_snapshot_read` is followed only by pure planning,
    so an edit landing the instant the snapshot returns is caught by
    nothing until the drift guard refuses (exit 3). `purge`'s snapshots
    are followed by rail 4 (`vcs.is_clean`), which runs BEFORE the typed
    phrase and the guard -- so the same edit dirties the working tree and
    rail 4 refuses first, exit 1, and the guard never runs. That is the
    system fail-closed by an EARLIER mechanism, not a gap: the guard's own
    remit starts after rail 4, inside the phrase window, which
    `test_drift_on_the_unprompted_path_is_refused` covers.

    This test pins the rail-4 half so the interleaving can never fall
    between the two mechanisms: if the snapshots ever move AFTER rail 4,
    this test starts failing (the run would succeed or exit 3), and
    whoever moves them must consciously re-point it at the guard.
    """
    target = "bundle/index.md"
    target_path = tmp_git_repo.root / target
    concurrent = "hand-edited the instant the snapshot returned\n"
    real_snapshot_read = main._snapshot_read
    fired = False

    def racing_snapshot_read(path: Path) -> tuple[bytes, str]:
        nonlocal fired
        snapshot = real_snapshot_read(path)
        if not fired and path == target_path:
            fired = True
            target_path.write_text(concurrent, encoding="utf-8")
        return snapshot

    before = snapshot_with_mtime(tmp_git_repo.root)
    monkeypatch.setattr(main, "_snapshot_read", racing_snapshot_read)
    phrase = f"purge {tmp_git_repo.source_id}"

    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert fired, "the racing wrapper never saw the index.md snapshot"
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "uncommitted changes" in result.stderr
    assert "refusing to write --" not in result.stderr
    # The edit survives, nothing was expunged, history is intact.
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert _tree_contains_path(tmp_git_repo.root, "raw/notes.txt")
    assert changed_paths(before, snapshot_with_mtime(tmp_git_repo.root)) == {
        Path(target)
    }


# -- #313 wave-2 R3: a purge-set member without a Phase-A baseline -----------


def test_a_member_without_a_phase_a_baseline_refuses_instead_of_tracebacking(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`purge`'s guard mapping looks up every non-root member's snapshot
    bytes by `<member>.md`. Today that key exists by construction --
    `purge_ids` and the snapshot are built from the same bundle scan, and
    Phase A's own `member_texts` lookup would have crashed first -- so this
    lookup is defensive-only, and it CANNOT be forced end-to-end without
    first breaking that earlier lookup. But a future refactor that computes
    `purge_ids` from anything other than the scanned files would turn a
    bare `[]` lookup into a `KeyError` traceback in the middle of the
    post-confirmation gate. The helper pins the fail-closed alternative: a
    member with no same-observation baseline (#318) cannot be validated, so
    it is treated as drift -- refuse (exit 3), name the member, write
    nothing."""
    with pytest.raises(typer.Exit) as excinfo:
        main._require_member_baseline("purge", {}, "concepts/ghost")

    assert excinfo.value.exit_code == 3
    err = capsys.readouterr().err
    assert "openkos purge: refusing to write --" in err
    assert "concepts/ghost" in err
    assert "no Phase-A snapshot" in err
    assert "Nothing was written." in err
    assert "Re-run to recompute over the current bundle." in err


def test_a_member_with_a_baseline_returns_it_unchanged() -> None:
    """The happy path is a plain lookup: the helper hands back the exact
    snapshot bytes so the guard's mapping is byte-for-byte what the direct
    indexing built before it existed."""
    baseline = main._require_member_baseline(
        "purge", {"concepts/child.md": b"snapshot\n"}, "concepts/child"
    )

    assert baseline == b"snapshot\n"


# --- Pending-work decision subtree sweep (privacy-purge spec: "Whole-
# History Expunge Covers The Pending-Work Decision Subtree") ----------------


def _write_decision(
    bundle_dir: Path,
    concept_id: str,
    *,
    pair_ids: tuple[str, str],
    merged_absorbed_id: str | None = None,
    state: bundle_decisions.DecisionState = "declined",
) -> Path:
    """Construct one `bundle/.state/decisions/<concept_id>.decisions.okf`
    sidecar holding a single record, via `bundle.decisions.write_decisions`
    directly -- no CLI writer verb exists yet in this slice (D6 slicing
    rationale), so every fixture in this section builds decision files this
    way, exactly as the tasks file specifies."""
    decision_key = bundle_decisions.decision_key_for(pair_ids, merged_absorbed_id)
    record = bundle_decisions.DecisionRecord(
        decision_key=decision_key,
        pair_ids=pair_ids,
        merged_absorbed_id=merged_absorbed_id,
        state=state,
        decided_at="2026-08-12T00:00:00Z",
    )
    return bundle_decisions.write_decisions(concept_id, bundle_dir, records=[record])


def test_purging_a_concept_removes_its_decision_from_history(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """privacy-purge spec: "Whole-History Expunge Covers The Pending-Work
    Decision Subtree", scenario "Purging a concept removes its decision
    from history" -- the purge target's OWN decisions sidecar
    (`pair_ids[0] == <purged concept>`) is gone from `git rev-list
    --objects --all` and the reflog, in the SAME single `git filter-repo`
    pass as the concept's own file expunge."""
    _write_plain_concept(tmp_git_repo.root, "concepts/purge-target", title="Target")
    _write_plain_concept(tmp_git_repo.root, "concepts/other", title="Other")
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add purge-target + other"], cwd=tmp_git_repo.root)

    bundle_dir = tmp_git_repo.root / "bundle"
    decision_path = _write_decision(
        bundle_dir,
        "concepts/purge-target",
        pair_ids=("concepts/purge-target", "concepts/other"),
    )
    assert decision_path.is_file(), "fixture setup: write_decisions must create a file"
    decision_rel = decision_path.relative_to(tmp_git_repo.root).as_posix()
    _git(["add", "-f", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add decision"], cwd=tmp_git_repo.root)
    assert _blob_history_contains(tmp_git_repo.root, decision_rel)

    phrase = "purge concepts/purge-target"
    result = runner.invoke(
        app, ["purge", "concepts/purge-target", "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert not _blob_history_contains(tmp_git_repo.root, decision_rel)
    assert _reflog_is_empty(tmp_git_repo.root)
    assert not decision_path.exists()


def test_purging_a_concept_named_as_the_foreign_partner_removes_the_decision_from_history(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """The stronger half of the same requirement: a decisions sidecar OWNED
    by a concept that is NOT itself purged, but whose record's `pair_ids`
    names the purge target, is ALSO gone from history -- not merely
    rewritten live. This is what distinguishes the decisions sweep from the
    merge-ledger sidecar's own-file-only history coverage (design Decision
    5's threat-matrix row)."""
    _write_plain_concept(tmp_git_repo.root, "concepts/purge-target", title="Target")
    _write_plain_concept(tmp_git_repo.root, "concepts/owner", title="Owner")
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add purge-target + owner"], cwd=tmp_git_repo.root)

    bundle_dir = tmp_git_repo.root / "bundle"
    decision_path = _write_decision(
        bundle_dir,
        "concepts/owner",
        pair_ids=("concepts/owner", "concepts/purge-target"),
    )
    decision_rel = decision_path.relative_to(tmp_git_repo.root).as_posix()
    _git(["add", "-f", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add decision"], cwd=tmp_git_repo.root)
    assert _blob_history_contains(tmp_git_repo.root, decision_rel)

    phrase = "purge concepts/purge-target"
    result = runner.invoke(
        app, ["purge", "concepts/purge-target", "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert not _blob_history_contains(tmp_git_repo.root, decision_rel)
    assert _reflog_is_empty(tmp_git_repo.root)


def test_purging_an_unrelated_concept_leaves_decision_untouched(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """privacy-purge spec, scenario "An unrelated decision entry is
    untouched" -- a decision file referencing a concept OUTSIDE the purge
    set stays byte-identical in every historical commit."""
    _write_plain_concept(tmp_git_repo.root, "concepts/unrelated-a", title="A")
    _write_plain_concept(tmp_git_repo.root, "concepts/unrelated-b", title="B")
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add unrelated pair"], cwd=tmp_git_repo.root)

    bundle_dir = tmp_git_repo.root / "bundle"
    decision_path = _write_decision(
        bundle_dir,
        "concepts/unrelated-a",
        pair_ids=("concepts/unrelated-a", "concepts/unrelated-b"),
    )
    decision_rel = decision_path.relative_to(tmp_git_repo.root).as_posix()
    _git(["add", "-f", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add unrelated decision"], cwd=tmp_git_repo.root)
    before_bytes = decision_path.read_bytes()

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert decision_path.is_file()
    assert decision_path.read_bytes() == before_bytes
    assert _blob_history_contains(tmp_git_repo.root, decision_rel)


def test_purging_a_concept_containing_rename_delimiter_in_decision_path_refuses(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """Threat matrix ("Shell / subprocess"): a decisions path derived from
    a `==>`-containing concept id is rejected by `_validate_rel_paths`
    BEFORE it reaches `expunge_targets`'s `git filter-repo` call, exactly
    as `vcs/git.py:515-551`'s own docstring warns -- the purge preparation
    step refuses (exit 1, nothing written), rather than silently
    mis-parsing the rename directive."""
    _write_plain_concept(tmp_git_repo.root, "concepts/purge-target", title="Target")
    weird_id = "concepts/weird==>id"
    _write_plain_concept(tmp_git_repo.root, weird_id, title="Weird")
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add purge-target + weird owner"], cwd=tmp_git_repo.root)

    bundle_dir = tmp_git_repo.root / "bundle"
    _write_decision(
        bundle_dir,
        weird_id,
        pair_ids=(weird_id, "concepts/purge-target"),
    )
    _git(["add", "-f", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add weird decision"], cwd=tmp_git_repo.root)

    phrase = "purge concepts/purge-target"
    result = runner.invoke(
        app, ["purge", "concepts/purge-target", "--confirm-phrase", phrase]
    )

    assert result.exit_code == 1, result.output
    assert "==>" in result.output


def test_purge_deletes_findings_db_without_rebuild(tmp_git_repo: TmpGitRepo) -> None:
    """pending-work design Decision 1's rebuild-posture table: `purge`
    physically deletes `.openkos/findings.db` and does NOT rebuild it
    in-line (it shares `vectors.db`'s posture, not `fts.db`'s -- rebuilding
    a finding costs LLM calls)."""
    (tmp_git_repo.root / ".openkos").mkdir(exist_ok=True)
    (tmp_git_repo.root / ".openkos" / "findings.db").write_bytes(b"stale")
    _git(["add", "-f", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Add stale findings.db"], cwd=tmp_git_repo.root)

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert not (tmp_git_repo.root / ".openkos" / "findings.db").exists()
    assert (tmp_git_repo.root / ".openkos" / "fts.db").exists()
    assert (tmp_git_repo.root / ".openkos" / "graph.db").exists()


def test_purge_drops_the_filed_question_cache(tmp_git_repo: TmpGitRepo) -> None:
    """`purge` deletes `.openkos/insight_questions.db` with the other stores.

    That store holds embeddings of the SOURCE QUESTION every filed insight
    was saved from — private text, at rest, that did not exist before the
    near-duplicate cache. A `purge` that forgets a Source but leaves the
    vector of a question about it behind would be a hole in exactly the
    guarantee `purge` sells.

    Deleted and NOT rebuilt in-line, like `vectors.db` and `findings.db`: a
    missing row is a cache miss the next save re-embeds, so restoring it
    costs embedding time and never correctness.
    """
    cache_path = tmp_git_repo.root / ".openkos" / "insight_questions.db"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"stale question vectors")

    phrase = f"purge {tmp_git_repo.source_id}"
    result = runner.invoke(
        app, ["purge", tmp_git_repo.source_id, "--confirm-phrase", phrase]
    )

    assert result.exit_code == 0, result.output
    assert not cache_path.exists()


# --- #886: every dropped store is named, with its own restore cost ---------


def _seed_derived_stores(root: Path) -> dict[str, Path]:
    """Create all five `.openkos` stores plus the `-wal`/`-shm` sidecars
    SQLite leaves beside an open database, so a purge has something real to
    drop and to tidy up after."""
    openkos_dir = root / ".openkos"
    openkos_dir.mkdir(exist_ok=True)
    made: dict[str, Path] = {}
    for name in (
        "fts.db",
        "vectors.db",
        "graph.db",
        "findings.db",
        "insight_questions.db",
    ):
        path = openkos_dir / name
        path.write_bytes(b"SQLite format 3\x00")
        (openkos_dir / f"{name}-wal").write_bytes(b"")
        (openkos_dir / f"{name}-shm").write_bytes(b"")
        made[name] = path
    return made


def _purge_self(source_id: str) -> Result:
    return runner.invoke(
        app, ["purge", source_id, "--confirm-phrase", f"purge {source_id}"]
    )


def test_purge_names_every_store_it_leaves_deleted(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """`purge` drops five stores and rebuilds two; the warning named only
    `vectors.db`, so `findings.db` and `insight_questions.db` were destroyed
    in silence. #142's own justification for the vectors warning -- "warn
    every time so an operator is never left assuming dense retrieval is
    still intact" -- applies identically to the other two and was never
    applied to them."""
    _seed_derived_stores(tmp_git_repo.root)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    assert "vectors.db" in result.output
    assert "findings.db" in result.output
    assert "insight_questions.db" in result.output


def test_purge_names_the_distinct_restore_cost_of_each_dropped_store(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """The three costs are genuinely different and a single "run reindex"
    line would misprice two of them: `vectors.db` costs a full re-embed,
    `findings.db` costs an LLM call per verdict on the next
    `contradictions`/`adjudicate`/`suggest-relations` run, and
    `insight_questions.db` is free -- a cache miss the next save re-embeds."""
    _seed_derived_stores(tmp_git_repo.root)

    result = _purge_self(tmp_git_repo.source_id)
    output = result.output.lower()

    assert result.exit_code == 0, result.output
    assert "openkos reindex" in result.output
    assert "contradictions" in output
    assert "adjudicate" in output
    assert "suggest-relations" in output
    assert "free" in output


def test_purge_does_not_name_the_stores_it_rebuilds(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """`fts.db` and `graph.db` are rebuilt in-line, so naming them in a
    "left deleted" notice would send the operator to restore something that
    is already back."""
    _seed_derived_stores(tmp_git_repo.root)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    assert "fts.db" not in result.output
    assert "graph.db" not in result.output


def test_purge_removes_the_orphan_wal_and_shm_sidecars(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """Deleting `x.db` leaves `x.db-wal`/`x.db-shm` behind. They held no data
    residue in the reported run (0-byte WAL), so this is hygiene rather than
    erasure -- but a sidecar with no database is litter that makes the
    directory misreport what exists."""
    _seed_derived_stores(tmp_git_repo.root)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    openkos_dir = tmp_git_repo.root / ".openkos"
    for name in ("vectors.db", "findings.db", "insight_questions.db"):
        assert not (openkos_dir / name).exists(), name
        assert not (openkos_dir / f"{name}-wal").exists(), f"{name}-wal"
        assert not (openkos_dir / f"{name}-shm").exists(), f"{name}-shm"


def test_purge_keeps_the_sidecars_of_the_stores_it_rebuilt(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep must not reach past the stores that were dropped: `fts.db`
    and `graph.db` are rebuilt in-line and a live database's sidecars belong
    to it.

    An earlier version of this test asserted only that the two DATABASES
    came back and never looked at a sidecar -- the one thing its name
    promises. Asserting that the sidecar FILES survive was the next wrong
    answer: the rebuild reopens each store in WAL mode and SQLite removes
    its own `-wal`/`-shm` on a clean close, so those files are legitimately
    absent afterwards and nothing about purge is proved by their state.

    What must hold is that PURGE's sweep is scoped -- it is called for the
    dropped stores and for nothing else. Whatever the rebuild then does with
    its own sidecars is the rebuild's business."""
    _seed_derived_stores(tmp_git_repo.root)
    swept: list[str] = []
    real_sweep = main._purge_sweep_store_sidecars

    def recording_sweep(path: Path) -> None:
        swept.append(path.name)
        real_sweep(path)

    monkeypatch.setattr(main, "_purge_sweep_store_sidecars", recording_sweep)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    openkos_dir = tmp_git_repo.root / ".openkos"
    assert (openkos_dir / "fts.db").exists()
    assert (openkos_dir / "graph.db").exists()
    assert sorted(swept) == [
        "findings.db",
        "insight_questions.db",
        "vectors.db",
    ]


def test_purge_leaves_operator_identity_rulings_intact(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """#886 states that purge destroyed "the operator's own recorded rulings
    (two declined identity merges)". It does not, and the distinction
    matters for what the warning may claim: a `--keep-distinct` ruling is
    written to `bundle/.state/decisions/` and committed, so it lives in the
    BUNDLE, not in the dropped `findings.db`. The three findings.db tenants
    are all MACHINE-computed verdicts.

    An operator told their rulings were gone would go looking to re-enter
    decisions that are still on disk, so the notice says so explicitly and
    this test is what licenses that sentence."""
    _seed_derived_stores(tmp_git_repo.root)
    survivor = "concepts/keeps-its-ruling"
    _write_plain_concept(tmp_git_repo.root, survivor, title="Keeps Its Ruling")
    record = bundle_decisions.IdentityDecisionRecord(
        decision_key=bundle_decisions.identity_decision_key_for(
            [survivor, "concepts/other"]
        ),
        member_ids=(survivor, "concepts/other"),
        state="declined",
        decided_at="2026-08-26T00:00:00Z",
    )
    ruling_path = bundle_decisions.write_identity_decisions(
        survivor, tmp_git_repo.root / "bundle", records=[record]
    )
    _git(["add", "-A"], cwd=tmp_git_repo.root)
    _git(["commit", "-m", "Record a keep-distinct ruling"], cwd=tmp_git_repo.root)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    # The purge really ran -- without this the survival assertion below
    # would also hold for a purge that did nothing at all.
    assert not (tmp_git_repo.root / "bundle" / f"{tmp_git_repo.source_id}.md").exists()
    assert Path(ruling_path).is_file(), "the operator's ruling must survive"
    survived = bundle_decisions.read_identity_decisions_at(Path(ruling_path))
    assert [r.state for r in survived] == ["declined"]
    assert survived[0].member_ids == (survivor, "concepts/other")


def test_purge_notice_count_matches_the_stores_it_lists(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """The opening line's count must be DERIVED from the list it introduces.
    A literal would drift from the list the moment a store is added or
    removed -- which is the exact defect #886 reports, reproduced one line
    higher up."""
    _seed_derived_stores(tmp_git_repo.root)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    header = next(
        line for line in result.output.splitlines() if "derived store(s)" in line
    )
    listed = [
        line
        for line in result.output.splitlines()
        if line.startswith("  - ") and ".db:" in line
    ]
    assert len(listed) == 3
    assert f"{len(listed)} derived store(s)" in header


def test_purge_does_not_claim_a_store_it_failed_to_delete_was_dropped(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice is the operator's account of what is gone. Announcing a
    store as dropped when its `unlink` raised sends them to pay for a
    restore of something still on disk -- and the warning about the failure
    goes to stderr while the claim goes to stdout, so the two are easy to
    read apart."""
    _seed_derived_stores(tmp_git_repo.root)
    real_unlink = Path.unlink

    def refuse_findings(self: Path, missing_ok: bool = False) -> None:
        if self.name == "findings.db":
            raise OSError("device busy")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse_findings)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    assert "vectors.db" in result.stdout
    assert "insight_questions.db" in result.stdout
    listed = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("  - ") and ".db:" in line
    ]
    assert not any("findings.db" in line for line in listed), result.stdout
    # The header counts what is ACTUALLY gone. This is the case that
    # discriminates a derived count from a hard-coded 3 -- the happy path
    # cannot, because there the two agree.
    assert len(listed) == 2
    assert "2 derived store(s)" in result.stdout


def test_purge_leaves_the_sidecars_of_a_store_it_failed_to_delete(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sweeping the sidecars of a database that is STILL THERE is worse than
    leaving litter: a `-wal` holds committed pages not yet checkpointed
    back, so deleting it out from under a live database can destroy data the
    purge was never asked to touch."""
    _seed_derived_stores(tmp_git_repo.root)
    real_unlink = Path.unlink

    def refuse_findings(self: Path, missing_ok: bool = False) -> None:
        if self.name == "findings.db":
            raise OSError("device busy")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse_findings)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    openkos_dir = tmp_git_repo.root / ".openkos"
    assert (openkos_dir / "findings.db").exists()
    assert (openkos_dir / "findings.db-wal").exists()
    assert (openkos_dir / "findings.db-shm").exists()


def test_purge_notice_does_not_promise_that_a_purged_concepts_ruling_survives(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """The survival claim must be QUALIFIED. A decision path referencing a
    purge-set member is expunged in the same rewrite pass, by design and by
    requirement -- so an unqualified "your rulings survive" is false exactly
    where the privacy guarantee is strongest, and would read as a promise
    that the erasure had missed something."""
    _seed_derived_stores(tmp_git_repo.root)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    sentence = next(
        line for line in result.output.splitlines() if "--keep-distinct" in line
    )
    # Read from the ruling sentence itself. An earlier version of this test
    # searched the WHOLE output for "surviving", which the vectors cost line
    # ("every surviving document") satisfies on its own -- it passed without
    # the qualification ever being written.
    assert "OUTSIDE the purge set" in sentence
    assert "expunged with it" in result.output


def test_purge_does_not_report_a_store_that_never_existed_as_dropped(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """ "Gone after the purge" and "destroyed by the purge" are different
    facts, and only the second is worth telling an operator. A workspace
    that never ran `curate` has no `findings.db` at all; reporting it as
    dropped invents a loss and prices a restore for verdicts that were never
    computed -- the same false claim as the omission this issue fixes, only
    pointing the other way."""
    openkos_dir = tmp_git_repo.root / ".openkos"
    openkos_dir.mkdir(exist_ok=True)
    # Only the vector store was ever built here.
    (openkos_dir / "vectors.db").write_bytes(b"SQLite format 3\x00")

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    listed = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("  - ") and ".db:" in line
    ]
    assert len(listed) == 1
    assert "vectors.db" in listed[0]
    assert "1 derived store(s)" in result.stdout


def test_purge_prints_no_dropped_store_notice_when_nothing_was_dropped(
    tmp_git_repo: TmpGitRepo,
) -> None:
    """A workspace with no derived stores at all loses nothing, so there is
    nothing to disclose. A notice that fired anyway would train the operator
    to skip the one that matters.

    The fixture's own `init`/ingest leaves a `vectors.db` behind, so the
    directory is cleared first -- an earlier version of this test asserted
    silence without doing that and was simply wrong about its own premise."""
    shutil.rmtree(tmp_git_repo.root / ".openkos", ignore_errors=True)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    assert "derived store(s) were dropped" not in result.stdout


def test_purge_still_discloses_dropped_stores_when_the_autocommit_raises(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stores are already gone by the time the post-erasure bookkeeping
    runs, so a failure there must not swallow the only record that they
    went. The `try/finally` exists for exactly this, and an untested
    guarantee protecting an irreversible operation is not a guarantee."""
    _seed_derived_stores(tmp_git_repo.root)

    def explode(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("bookkeeping blew up")

    # The `paths_dirty` probe, not `_autocommit`: on the common clean-purge
    # path the probe short-circuits the commit, so patching `_autocommit`
    # produces a test that never enters the branch it claims to cover.
    monkeypatch.setattr(vcs_git, "paths_dirty", explode)

    result = _purge_self(tmp_git_repo.source_id)

    assert isinstance(result.exception, RuntimeError)
    assert "derived store(s) were dropped" in result.output
    assert "findings.db" in result.output
    assert "insight_questions.db" in result.output


def test_purge_does_not_crash_when_a_store_path_cannot_be_stat_ed(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Path.exists()` is not total: it swallows a handful of errnos and
    RE-RAISES the rest, `EACCES` among them. These probes run AFTER the
    irreversible rewrite, so an unguarded one turns an unreadable
    `.openkos` entry into a crash that takes the whole success report with
    it -- the expunge summary and the dropped-store disclosure both.

    Fail CLOSED: a store whose absence cannot be verified is not claimed as
    destroyed."""
    _seed_derived_stores(tmp_git_repo.root)
    real_exists = Path.exists

    def hostile_exists(self: Path, *, follow_symlinks: bool = True) -> bool:
        if self.name == "findings.db":
            raise PermissionError("EACCES")
        return bool(real_exists(self))

    monkeypatch.setattr(Path, "exists", hostile_exists)

    result = _purge_self(tmp_git_repo.source_id)

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "permanently expunged" in result.output
    listed = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("  - ") and ".db:" in line
    ]
    assert not any("findings.db" in line for line in listed), result.stdout
