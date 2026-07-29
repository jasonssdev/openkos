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
    assert "concepts/old: " in result.stdout
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
    assert "concepts/orphan: " in result.stdout
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


# --- purge-transactional-cleanup #141: Dangling references section --------


def test_lint_renders_empty_dangling_references_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh, empty bundle renders the `Dangling references:` section with
    its own empty-state line (lint spec: findings don't change exit
    contract)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "Dangling references:" in result.stdout
    assert "  No dangling references." in result.stdout


def test_lint_flags_a_dangling_relations_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept whose `relations:` target names an id absent from disk is
    reported under `Dangling references:`, and the command still exits 0
    (lint spec: "relations: target absent from disk is flagged")."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\n"
        "relations:\n"
        "  - target: concepts/ghost\n"
        "    type: relates-to\n"
        "---\nBody.\n",
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
    assert "concepts/stoicism: " in result.stdout
    assert "concepts/ghost" in result.stdout


# --- issue #187: Unextracted sources section -------------------------------


def test_lint_renders_empty_unextracted_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh, empty bundle renders the `Unextracted sources:` section with
    its own empty-state line."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "Unextracted sources:" in result.stdout
    assert "  No unextracted sources." in result.stdout


def test_lint_flags_a_failed_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Source with `extraction_status: failed` is reported under
    `Unextracted sources:`, naming the exact retry command, and the command
    still exits 0 (lint spec: "failed Source produces an unextracted
    finding"; "lint exits 0 with unextracted findings present")."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "notes.md").write_text(
        "---\ntype: Source\ntitle: Notes\nresource: raw/notes.txt\n"
        "extraction_status: failed\n---\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "sources/notes:" in result.stdout
    assert "openkos ingest raw/notes.txt" in result.stdout
    # #247: findings name the object by its Concept ID, the same spelling
    # `list` and `set-sensitivity` use -- never the `.md` file path. The
    # retry hint still names a real raw file, which keeps its extension.
    assert "sources/notes.md" not in result.stdout


def test_lint_ignores_blocked_by_sensitivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Source with `extraction_status: blocked-by-sensitivity` produces no
    `unextracted` finding -- a deliberate policy outcome, never debt (lint
    spec: "blocked-by-sensitivity produces no finding")."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "notes.md").write_text(
        "---\ntype: Source\ntitle: Notes\nresource: raw/notes.txt\n"
        "extraction_status: blocked-by-sensitivity\n---\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "  No unextracted sources." in result.stdout
    assert "openkos ingest" not in result.stdout


# --- issue #231 (PR2): below-source-sensitivity / multi-source-uncovered ---


def test_lint_flags_below_source_sensitivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A descendant whose `sensitivity` the `backfill-sensitivity` sweep
    would raise (per `okf.combine_sensitivity`) is reported under a
    distinct below-source-sensitivity section, and `lint` still exits 0
    (design D3; #231)."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "a.md").write_text(
        "---\ntype: Source\ntitle: A\nresource: raw/a.txt\n"
        "sensitivity: confidential\n---\nBody.\n",
        encoding="utf-8",
    )
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "derived.md").write_text(
        "---\ntype: Concept\ntitle: Derived\nsensitivity: public\n"
        "provenance:\n  - sources/a\n---\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "Below-source sensitivity:" in result.stdout
    section = result.stdout.split("Below-source sensitivity:", 1)[1]
    section = section.split("Multi-source uncovered:", 1)[0]
    assert "concepts/derived: " in section


def test_lint_flags_multi_source_uncovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A doc citing one Source plus a concept derived from a DIFFERENT
    Source is a member of no single-Source closure and is reported as
    `multi-source-uncovered`, marked as not covered by
    `backfill-sensitivity` (design D3; #231)."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "a.md").write_text(
        "---\ntype: Source\ntitle: A\nresource: raw/a.txt\n"
        "sensitivity: public\n---\nBody.\n",
        encoding="utf-8",
    )
    (sources_dir / "c.md").write_text(
        "---\ntype: Source\ntitle: C\nresource: raw/c.txt\n"
        "sensitivity: confidential\n---\nBody.\n",
        encoding="utf-8",
    )
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "from-c.md").write_text(
        "---\ntype: Concept\ntitle: From C\nsensitivity: confidential\n"
        "provenance:\n  - sources/c\n---\nBody.\n",
        encoding="utf-8",
    )
    (concepts_dir / "mixed.md").write_text(
        "---\ntype: Concept\ntitle: Mixed\nsensitivity: public\n"
        "provenance:\n  - sources/a\n  - concepts/from-c\n---\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "Multi-source uncovered:" in result.stdout
    section = result.stdout.split("Multi-source uncovered:", 1)[1]
    assert "concepts/mixed: " in section
    assert "not covered by" in section
    # `concepts/from-c` is below-source-sensitivity (single-Source closure
    # member), not multi-source-uncovered -- it must not appear here.
    # Not a finding SUBJECT -- it may still be named inside another
    # finding's `cites:` detail, so match the rendered "<id>: " prefix.
    assert "concepts/from-c: " not in section


def test_lint_clean_bundle_reports_zero_below_source_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh, empty bundle reports zero below-source-sensitivity /
    multi-source-uncovered findings; `lint` still exits 0 and creates no
    file (design D3; #231)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "Below-source sensitivity:" in result.stdout
    assert "  No below-source sensitivity findings." in result.stdout
    assert "Multi-source uncovered:" in result.stdout
    assert "  No multi-source uncovered findings." in result.stdout
    assert _snapshot(tmp_path) == before


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
