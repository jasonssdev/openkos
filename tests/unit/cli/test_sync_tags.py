"""Unit tests for the `sync-tags` CLI command: adds a Source's current
tags to the derived concepts it grounds, union only, never removing a tag
(ADR-0033; source-tag-sync design, mirroring `set-sensitivity`'s Source
branch and `backfill-sensitivity`'s bundle-wide sweep shape)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli import main
from openkos.cli.main import app
from openkos.model import okf
from openkos.vcs import git as vcs_git
from tests.unit.cli.conftest import changed_paths, confirm_after, snapshot_with_mtime
from tests.unit.cli.conftest import snapshot_bytes as _snapshot
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _init_workspace_git(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _write_source(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    tags: list[str] | None = None,
    sensitivity: str | None = None,
) -> str:
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {"type": "Source", "title": title}
    if tags is not None:
        metadata["tags"] = tags
    if sensitivity is not None:
        metadata["sensitivity"] = sensitivity
    concept_path.write_text(
        okf.dump_frontmatter(metadata, f"# {title}\n"), encoding="utf-8"
    )
    return concept_id


def _write_concept(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    provenance: list[str],
    tags: object = None,
    sensitivity: str | None = None,
) -> str:
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": title,
        "provenance": provenance,
    }
    if tags is not None:
        metadata["tags"] = tags
    if sensitivity is not None:
        metadata["sensitivity"] = sensitivity
    concept_path.write_text(
        okf.dump_frontmatter(metadata, f"# {title}\n"), encoding="utf-8"
    )
    return concept_id


def _tags_of(tmp_path: Path, concept_id: str) -> object:
    text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(text)
    return metadata.get("tags")


def _last_commit_subject(root: Path) -> str:
    result = vcs_git._run(["git", "log", "-1", "--format=%s"], cwd=root)
    return result.stdout.strip()


def _last_commit_files(root: Path) -> set[str]:
    result = vcs_git._run(["git", "show", "--name-only", "--format=", "-1"], cwd=root)
    return {line for line in result.stdout.splitlines() if line}


# -- 3.1: argument exclusivity -----------------------------------------------


def test_both_forms_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: Target Selection Is One Source Or Every Source --
    passing both a `<source-id>` and `--all` refuses (exit 1) before any
    bundle read."""
    _init_workspace(tmp_path, monkeypatch)
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha"])
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["sync-tags", "sources/notes", "--all"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--all" in result.stderr
    assert "sources/notes" in result.stderr or "source-id" in result.stderr.lower()
    assert _snapshot(tmp_path) == before


def test_neither_form_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: Target Selection Is One Source Or Every Source --
    passing neither form refuses (exit 1), no file changes."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["sync-tags"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_non_source_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: Target Selection Is One Source Or Every Source -- a
    resolved concept whose `type` is not `Source` refuses, naming the
    concept and stating that `sync-tags` takes a Source."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/alpha", title="Alpha", provenance=["x.txt"])
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["sync-tags", "concepts/alpha", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "concepts/alpha" in result.stderr
    assert "Source" in result.stderr
    assert _snapshot(tmp_path) == before


def test_unsafe_id_refuses_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec scenario: An unsafe id refuses before any write."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["sync-tags", "../outside", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_help_lists_sync_tags() -> None:
    """`sync-tags` is registered in the `Maintain` help panel alongside
    `backfill-sensitivity`/`backfill-source-titles`."""
    result = runner.invoke(app, ["sync-tags", "--help"])

    assert result.exit_code == 0
    assert "sync-tags" in result.output or "Add" in result.output


# -- 3.5: preview lines -------------------------------------------------------


def test_preview_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec: Preview, Confirm Gate, And --auto -- the preview names each
    staged file and its added tags, followed by the `log.md` line."""
    _init_workspace(tmp_path, monkeypatch)
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha", "beta"])
    _write_concept(tmp_path, "concepts/a", title="A", provenance=["sources/notes"])
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["sync-tags", "sources/notes"], input="n\n")

    assert "~ bundle/concepts/a.md (tags added: alpha, beta)" in result.output
    assert "~ log.md (new dated entry)" in result.output


# -- 3.6: confirm gate precedence --------------------------------------------


def test_decline_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha"])
    _write_concept(tmp_path, "concepts/a", title="A", provenance=["sources/notes"])
    _simulate_tty(monkeypatch)
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["sync-tags", "sources/notes"], input="n\n")

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert snapshot_with_mtime(tmp_path) == before


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha"])
    _write_concept(tmp_path, "concepts/a", title="A", provenance=["sources/notes"])
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["sync-tags", "sources/notes"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


def test_review_false_skips_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "review: true", "review: false"
        ),
        encoding="utf-8",
    )
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha"])
    _write_concept(tmp_path, "concepts/a", title="A", provenance=["sources/notes"])
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["sync-tags", "sources/notes"])

    assert result.exit_code == 0
    assert _tags_of(tmp_path, "concepts/a") == ["alpha"]


# -- 3.8: skip lines on stderr ------------------------------------------------


def test_skip_lines_on_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec: A Descendant With A Malformed Tags Value Is Never Rewritten /
    A Descendant Below The Source's Sensitivity Is Not Tagged -- each skip
    reason names the concept, once each, on stderr."""
    _init_workspace(tmp_path, monkeypatch)
    _write_source(
        tmp_path,
        "sources/notes",
        title="Notes",
        tags=["alpha"],
        sensitivity="confidential",
    )
    _write_concept(
        tmp_path,
        "concepts/malformed",
        title="Malformed",
        provenance=["sources/notes"],
        tags={"a": 1},
    )
    _write_concept(
        tmp_path,
        "concepts/low",
        title="Low",
        provenance=["sources/notes"],
        sensitivity="private",
    )

    result = runner.invoke(app, ["sync-tags", "sources/notes", "--auto"])

    assert "concepts/malformed" in result.stderr
    assert "concepts/low" in result.stderr
    assert "openkos set-sensitivity" in result.stderr
    assert result.stderr.count("concepts/malformed") == 1
    assert result.stderr.count("concepts/low") == 1


# -- 3.9: nothing to sync -----------------------------------------------------


def test_nothing_to_sync_exits_zero(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec: Union Only, Never Remove -- when no candidate is staged, the
    run prints that there is nothing to sync and exits 0, no write, no
    commit. PRECONDITION: the commit count is read before, so its being
    unchanged after is not an accident of the fixture."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha"])
    _write_concept(
        tmp_path,
        "concepts/a",
        title="A",
        provenance=["sources/notes"],
        tags=["alpha"],
    )
    before_count = int(
        vcs_git._run(
            ["git", "rev-list", "--count", "HEAD"], cwd=tmp_path
        ).stdout.strip()
    )

    result = runner.invoke(app, ["sync-tags", "sources/notes", "--auto"])

    assert result.exit_code == 0
    assert "nothing to sync" in result.output
    after_count = int(
        vcs_git._run(
            ["git", "rev-list", "--count", "HEAD"], cwd=tmp_path
        ).stdout.strip()
    )
    assert after_count == before_count


# -- 3.10-3.12: drift guard ---------------------------------------------------


def test_descendant_drift_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha"])
    _write_concept(tmp_path, "concepts/a", title="A", provenance=["sources/notes"])
    target_path = tmp_path / "bundle" / "concepts" / "a.md"
    concurrent = (
        target_path.read_text(encoding="utf-8")
        + "\nA paragraph added while the prompt waited.\n"
    )
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["sync-tags", "sources/notes"], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    assert changed_paths(before, after) == {Path("bundle/concepts/a.md")}


def test_source_drift_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec scenario: A Source edited while the prompt waits refuses the
    run -- the Source is a drift baseline even though it is not written
    (design Decision 5)."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha"])
    _write_concept(tmp_path, "concepts/a", title="A", provenance=["sources/notes"])
    target_path = tmp_path / "bundle" / "sources" / "notes.md"
    concurrent = (
        target_path.read_text(encoding="utf-8")
        + "\nA paragraph added while the prompt waited.\n"
    )
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["sync-tags", "sources/notes"], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    assert changed_paths(before, after) == {Path("bundle/sources/notes.md")}


# -- 3.13: commit message, refresh --------------------------------------------


def test_one_commit_message_and_refresh(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["secret-project"])
    _write_concept(tmp_path, "concepts/a", title="A", provenance=["sources/notes"])
    before_count = int(
        vcs_git._run(
            ["git", "rev-list", "--count", "HEAD"], cwd=tmp_path
        ).stdout.strip()
    )

    result = runner.invoke(app, ["sync-tags", "sources/notes", "--auto"])

    assert result.exit_code == 0
    after_count = int(
        vcs_git._run(
            ["git", "rev-list", "--count", "HEAD"], cwd=tmp_path
        ).stdout.strip()
    )
    assert after_count == before_count + 1
    assert _last_commit_subject(tmp_path) == "openkos: sync-tags sources/notes"
    assert "secret-project" not in _last_commit_subject(tmp_path)
    assert _last_commit_files(tmp_path) == {
        "bundle/concepts/a.md",
        "bundle/log.md",
    }
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "secret-project" not in log_text


def test_refresh_derived_after_write_called_once_with_sync_tags(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful write refreshes the derived stores exactly once with
    `verb="sync-tags"` (issue #640), mirroring
    `test_write_time_refresh.py`'s per-verb wiring pattern."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha"])
    _write_concept(tmp_path, "concepts/a", title="A", provenance=["sources/notes"])
    calls: list[str] = []

    def _recorder(layout: object, cfg: object, *, verb: str, **kwargs: object) -> bool:
        calls.append(verb)
        return True

    monkeypatch.setattr(main, "_refresh_derived_after_write", _recorder)

    result = runner.invoke(app, ["sync-tags", "sources/notes", "--auto"])

    assert result.exit_code == 0
    assert calls == ["sync-tags"]


def test_all_variant_commit_message(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha"])
    _write_concept(tmp_path, "concepts/a", title="A", provenance=["sources/notes"])

    result = runner.invoke(app, ["sync-tags", "--all", "--auto"])

    assert result.exit_code == 0
    assert _last_commit_subject(tmp_path) == "openkos: sync-tags --all"
