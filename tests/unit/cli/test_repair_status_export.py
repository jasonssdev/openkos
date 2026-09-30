"""CLI-level tests for `repair`'s deprecated-status export pass
(deprecated-status-export, issue #1075, Phase 2): the reported summary
lines, the one-commit write, and the v0.1-migration + export interaction
(spec: "repair exports a superseded concept the edge was hand-written
for", "repair is idempotent over exports")."""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a git identity: a CI runner configures none, so both the
    fixture's own `git commit` and `repair`'s auto-commit would otherwise
    fail or take the "identity unset" path instead of the commit path
    under test."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "openkos tests")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "tests@openkos.invalid")


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _write_concept(
    bundle_dir: Path,
    concept_id: str,
    metadata: dict[str, object],
    body: str = "Body.\n",
) -> Path:
    path = bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")
    return path


def _commit_pending(tmp_path: Path) -> None:
    _git(["add", "-A"], cwd=tmp_path)
    _git(["commit", "-m", "fixture: plant hand-written docs"], cwd=tmp_path)


def test_repair_export_summary_line_and_one_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'repair exports a superseded concept the edge was hand-written
    for' -- reported in the summary, written in exactly ONE commit beside
    the ledger/document/index writes `repair` already makes atomically."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(
        bundle_dir,
        "concepts/a",
        {
            "type": "Concept",
            "title": "A",
            "relations": [{"target": "concepts/b", "type": "supersedes"}],
        },
    )
    _write_concept(
        bundle_dir, "concepts/b", {"type": "Concept", "title": "B", "status": "stable"}
    )
    _commit_pending(tmp_path)
    before_head = _git(["rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert (
        "deprecated-status export -- 1 exported, 0 withdrawn, 0 marker(s) dropped."
        in (result.output)
    )
    metadata, _ = okf.load_frontmatter(
        (bundle_dir / "concepts" / "b.md").read_text(encoding="utf-8")
    )
    assert metadata["status"] == "deprecated"
    assert metadata[okf.STATUS_DERIVED_FROM_KEY] == "supersedes"

    after_head = _git(["rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()
    assert after_head != before_head
    commit_count = _git(
        ["rev-list", "--count", f"{before_head}..{after_head}"], cwd=tmp_path
    ).stdout.strip()
    assert commit_count == "1"


def test_repair_blocked_and_skipped_are_reported_even_with_nothing_else_to_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle whose ONLY drift is a BLOCKED superseded draft still prints
    the blocked disclosure, even though `has_work` is `False` (spec: 'repair
    leaves a blocked draft alone and says so' -- 'MUST be reported')."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(
        bundle_dir,
        "concepts/a",
        {
            "type": "Concept",
            "title": "A",
            "relations": [{"target": "concepts/b", "type": "supersedes"}],
        },
    )
    _write_concept(
        bundle_dir, "concepts/b", {"type": "Concept", "title": "B", "status": "draft"}
    )
    _commit_pending(tmp_path)

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "blocked -- 1 concept(s)" in result.output
    assert "concepts/b" in result.output
    assert "nothing to repair" in result.output.lower()


def test_repair_migrates_and_exports_a_v01_document_in_one_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: v0.1 doc with `status: active` that is superseded is migrated
    AND exported in one rewrite; a second `repair` writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(
        bundle_dir,
        "concepts/a",
        {
            "type": "Concept",
            "title": "A",
            "relations": [{"target": "concepts/b", "type": "supersedes"}],
        },
    )
    b_path = bundle_dir / "concepts" / "b.md"
    b_path.parent.mkdir(parents=True, exist_ok=True)
    b_path.write_text(
        "---\n"
        "type: Concept\n"
        "title: B\n"
        "status: active\n"
        'timestamp: "2026-07-14T09:00:00Z"\n'
        "---\n"
        "Body.\n",
        encoding="utf-8",
    )
    _commit_pending(tmp_path)

    first = runner.invoke(app, ["repair"])
    assert first.exit_code == 0

    metadata, _ = okf.load_frontmatter(b_path.read_text(encoding="utf-8"))
    assert metadata["status"] == "deprecated"
    assert metadata[okf.STATUS_DERIVED_FROM_KEY] == "supersedes"
    assert "generated" in metadata
    assert "timestamp" not in metadata

    before_head = _git(["rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()
    second = runner.invoke(app, ["repair"])
    assert second.exit_code == 0
    assert "nothing to repair" in second.output.lower()
    after_head = _git(["rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()
    assert after_head == before_head
