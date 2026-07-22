"""Unit tests for the `lint` CLI command: Phase-A-only, read-only health check.

`lint` is the SECOND read command (after `status`): no Phase B, no confirm
gate, no `--auto`. It composes `config.require_workspace` (exit gate),
`config.read_config` (`freshness_window`/`volatility_windows`),
`lint.resolve_windows`, `lint.collect_docs`, `lint.check_stale_stamps`, and
`lint.check_orphans`, then renders two sections via `typer.echo`. Exit 0 on
every successful read; the ONLY non-zero exit path is an absent/unreadable
workspace.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app

runner = CliRunner()


def _snapshot_entry(path: Path) -> bytes | None:
    if path.is_dir():
        return None
    return path.read_bytes()


def _snapshot(root: Path) -> dict[Path, bytes | None]:
    """Capture every entry under `root`, keyed by relative path."""
    return {path.relative_to(root): _snapshot_entry(path) for path in root.rglob("*")}


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _set_config_field(tmp_path: Path, old: str, new: str) -> None:
    config_path = tmp_path / "openkos.yaml"
    content = config_path.read_text(encoding="utf-8")
    assert old in content
    config_path.write_text(content.replace(old, new), encoding="utf-8")


def test_lint_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace exits non-zero, with
    the shared `require_workspace` reason under a `lint`-specific prefix,
    and no raw traceback (spec: Workspace Presence Check)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["lint"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos lint: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr


def test_lint_refuses_cleanly_when_index_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission-denied `bundle/index.md`, readable enough to pass the
    `is_file()` gate but not `read_text()`, still exits 1 with no raw
    traceback -- the only additional non-zero path beyond an absent
    workspace (design's Data Flow: `OSError -> Exit 1`)."""
    _init_workspace(tmp_path, monkeypatch)

    original_read_text = Path.read_text

    def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "index.md":
            raise PermissionError(13, "Permission denied", str(self))
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    result = runner.invoke(app, ["lint"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("openkos lint: ")


def test_lint_fresh_bundle_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly initialized, empty bundle reports both empty states and
    exits 0 (spec: Empty or fresh bundle has no findings)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "Stale stamps:" in result.stdout
    assert "  No stale stamps." in result.stdout
    assert "Orphan pages:" in result.stdout
    assert "  No orphan pages." in result.stdout


def test_lint_flags_a_stale_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept body with a stamp older than the window is reported as a
    stale-stamp finding, and the command still exits 0 (spec: Stale stamp
    is flagged; Bundle with findings still exits 0)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "old.md").write_text(
        "---\ntype: Concept\ntitle: Old\n---\nSome fact recorded (as of 2000-01-01).\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "bundle" / "index.md"
    index_text = index_path.read_text(encoding="utf-8")
    index_path.write_text(
        index_text + "\n# Concepts\n\n* [Old](/concepts/old.md) - stale example.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "concepts/old.md" in result.stdout
    assert "2000-01-01" in result.stdout


def test_lint_flags_an_orphan_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept file not referenced by `index.md` or any other concept is
    reported as an orphan-page finding, and the command still exits 0
    (spec: Unreferenced concept is flagged as orphan)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "orphan.md").write_text(
        "---\ntype: Concept\ntitle: Orphan\n---\nNever linked anywhere.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "concepts/orphan.md" in result.stdout
    assert "not referenced by index.md or any concept" in result.stdout


def test_lint_falls_back_and_prints_notice_on_bad_freshness_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid `freshness_window` in `openkos.yaml` degrades to the
    default `7d` and prints a fallback notice line -- `lint` never crashes
    on bad config (Q4)."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "freshness_window: not-a-duration\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "not-a-duration" in result.stdout
    assert "not a valid duration" in result.stdout
    assert "using default 7d" in result.stdout


def test_lint_falls_back_and_prints_notice_on_non_string_freshness_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-string `freshness_window` (e.g. a bare `7`) degrades to `7d`
    with a fallback notice, NO traceback, and exit 0."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "freshness_window: 7\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    assert "not a valid duration" in result.stdout
    assert "using default 7d" in result.stdout


def test_lint_surfaces_a_skipped_unparseable_file_as_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unparseable concept file is excluded but surfaced as a notice."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "broken.md").write_text(
        "Just plain text, no frontmatter block.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "concepts/broken.md: skipped (unparseable frontmatter)" in result.stdout


def test_lint_pure_ingest_bundle_shows_both_empty_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle containing only a freshly ingested Source (no `(as of ...)`
    stamp, cataloged in `index.md`) reports zero findings (spec: Pure-ingest
    bundle produces zero stale findings; cataloged Source is not orphan)."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")
    ingest_result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert ingest_result.exit_code == 0

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "  No stale stamps." in result.stdout
    assert "  No orphan pages." in result.stdout


def test_lint_rejects_json_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--json` or other structured output mode is offered (spec:
    Read-Only and Human-Readable Only)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["lint", "--json"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


def test_lint_wires_volatility_windows_into_the_stale_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`lint` resolves `windows` via `lint.resolve_windows(cfg)` (not the
    old `resolve_window(cfg.freshness_window)`) and passes them to
    `check_stale_stamps` -- a `Concept` (`slow`-tier default, packaged 90d
    window) with a 30-day-old stamp is NOT flagged, even though 30 days
    exceeds the OLD global 7d default (freshness-lint-v1, design: "CLI
    wiring")."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    stamp = (datetime.now(UTC).date() - timedelta(days=30)).isoformat()
    (concepts_dir / "stoicism.md").write_text(
        f"---\ntype: Concept\ntitle: Stoicism\n---\nRecorded (as of {stamp}).\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "bundle" / "index.md"
    index_path.write_text(
        index_path.read_text(encoding="utf-8")
        + "\n# Concepts\n\n* [Stoicism](/concepts/stoicism.md) - test fixture.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "  No stale stamps." in result.stdout


def test_lint_surfaces_a_notice_on_malformed_volatility_window_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `volatility_windows.slow` value in `openkos.yaml`
    degrades to the packaged `DEFAULT_FRESHNESS_WINDOW` and surfaces a
    fallback notice, alongside any skip notices -- `lint` never crashes on
    bad `volatility_windows` config."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "volatility_windows:\n  slow: not-a-duration\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "not-a-duration" in result.stdout
    assert "not a valid duration" in result.stdout
    assert "using default 7d" in result.stdout


def test_lint_never_writes_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file under the workspace is created, modified, or deleted on any
    run -- healthy, empty, or with findings (spec: No mutation on any
    run)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "orphan.md").write_text(
        "---\ntype: Concept\ntitle: Orphan\n---\n"
        "Stale fact (as of 2000-01-01), never linked.\n",
        encoding="utf-8",
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert _snapshot(tmp_path) == before
