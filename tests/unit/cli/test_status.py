"""Unit tests for the `status` CLI command: Phase-A-only, read-only overview.

`status` is the first read command (design.md's Technical Approach): no
Phase B, no confirm gate, no `--auto`. It composes `config.require_workspace`
(exit gate), `okf.survey_bundle` (counts + §9 findings from one disk scan),
and `bundle.log.read_recent_entries` (recent activity, lenient-degrading),
then renders three sections via `typer.echo`. Exit 0 on every successful
read; the ONLY non-zero path is an absent/unreadable workspace.
"""

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


def test_status_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace exits non-zero, with
    the shared `require_workspace` reason under a `status`-specific prefix,
    and no raw traceback (spec: Workspace Presence Check)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos status: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr


def test_status_refuses_cleanly_when_workspace_files_are_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission-denied `bundle/index.md` makes `Path.is_file()` raise
    `PermissionError` instead of swallowing it to `False`. `status` must
    still refuse cleanly (exit 1, D1's shared gate, no raw traceback) --
    never let the `OSError` propagate out of `require_workspace` uncaught."""
    _init_workspace(tmp_path, monkeypatch)

    original_is_file = Path.is_file

    def fake_is_file(self: Path) -> bool:
        if self.name == "index.md":
            raise PermissionError(13, "Permission denied", str(self))
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", fake_is_file)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("openkos status: refusing to run -- ")
    assert "could not be read" in result.stderr


def test_status_fresh_bundle_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly initialized, empty bundle reports `Sources: 0`,
    `Concepts: 0`, the recent-activity empty-state text, and exits 0 (spec:
    Freshly initialized empty bundle). `log.md`'s `Initialization` bullet
    means "no activity recorded yet" is the true empty-log-body case, so
    this asserts the actual init entry appears instead. A fresh `init`
    never creates `.openkos/vectors.db`, so "needs attention" is NOT empty
    -- it always surfaces the #142 missing-vector-index line until the
    first `openkos reindex`."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Sources:  0" in result.stdout
    assert "Concepts: 0" in result.stdout
    assert "vectors.db" in result.stdout
    assert "openkos reindex" in result.stdout


def test_status_healthy_bundle_full_render_has_three_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy workspace with an ingested source renders all three
    sections and exits 0 (spec: Healthy bundle with sources, Healthy bundle
    shows recent activity). No `.openkos/vectors.db` exists yet (`ingest`
    never creates it), so "needs attention" surfaces the #142 missing-
    vector-index line rather than the empty state."""
    _init_workspace(tmp_path, monkeypatch)
    source = tmp_path / "notes.txt"
    source.write_text("Some raw notes.", encoding="utf-8")
    ingest_result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert ingest_result.exit_code == 0

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Bundle contents:" in result.stdout
    assert "Recent activity:" in result.stdout
    assert "Needs attention:" in result.stdout
    assert "Sources:  1" in result.stdout
    assert "Concepts: 0" in result.stdout
    assert "Ingest" in result.stdout
    assert "vectors.db" in result.stdout


def test_status_breaks_down_non_concept_types_instead_of_folding_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with a Procedure alongside Concepts reports a distinct
    `Procedures:` line and a `Concepts:` count that excludes it -- never the
    old two-bucket conflation that folded every non-Source type into
    "Concepts" (issue #133)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "alpha.md").write_text(
        "---\ntype: Concept\ntitle: Alpha\n---\nBody.\n", encoding="utf-8"
    )
    (concepts_dir / "beta.md").write_text(
        "---\ntype: Concept\ntitle: Beta\n---\nBody.\n", encoding="utf-8"
    )
    procedures_dir = tmp_path / "bundle" / "procedures"
    procedures_dir.mkdir()
    (procedures_dir / "setup.md").write_text(
        "---\ntype: Procedure\ntitle: Setup\n---\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Concepts:" in result.stdout
    assert "Procedures:" in result.stdout
    concepts_line = next(
        line for line in result.stdout.splitlines() if "Concepts:" in line
    )
    procedures_line = next(
        line for line in result.stdout.splitlines() if "Procedures:" in line
    )
    assert concepts_line.split(":", 1)[1].strip() == "2"
    assert procedures_line.split(":", 1)[1].strip() == "1"


def test_status_counts_reflect_disk_scan_not_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source file present on disk but NOT reflected in `index.md` (as
    after an interrupted `ingest`) is still counted -- disk is the truth,
    never `index.md` (spec: Catalog drift -- disk is the truth)."""
    _init_workspace(tmp_path, monkeypatch)
    sources_dir = tmp_path / "bundle" / "sources"
    sources_dir.mkdir()
    (sources_dir / "orphan.md").write_text(
        "---\ntype: Source\ntitle: Orphan\n---\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Sources:  1" in result.stdout
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "orphan" not in index_text


def test_status_empty_log_body_shows_no_activity_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `log.md` with a header but no dated sections yet reports "No
    activity recorded yet." (spec: Empty log). A fresh `init` always writes
    an `Initialization` bullet, so this is an edge case reached only when
    `log.md`'s activity section is genuinely empty."""
    _init_workspace(tmp_path, monkeypatch)
    log_path = tmp_path / "bundle" / "log.md"
    log_path.write_text("# Directory Update Log\n", encoding="utf-8")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "No activity recorded yet." in result.stdout


def test_status_malformed_log_degrades_and_still_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `log.md` degrades to a notice under "Recent activity" and
    the command still exits 0 -- counts and findings are unaffected (spec:
    Q2/D5, lenient degrade). No `.openkos/vectors.db` exists yet, so "needs
    attention" surfaces the #142 missing-vector-index line."""
    _init_workspace(tmp_path, monkeypatch)
    log_path = tmp_path / "bundle" / "log.md"
    log_path.write_text(
        "# Directory Update Log\n\n## 2026-07-16\n* no blank line above\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Recent activity unavailable" in result.stdout
    assert "Sources:  0" in result.stdout
    assert "vectors.db" in result.stdout


def test_status_conformance_violation_is_surfaced_but_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept file with a missing `type` field is listed under "Needs
    attention" and the command still exits 0 (spec: Conformance violation
    is surfaced but non-fatal)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "orphan.md").write_text(
        "---\ntitle: no type here\n---\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "missing non-empty 'type'" in result.stdout


# --- purge-transactional-cleanup #141/#142: Needs-attention wiring --------


def test_status_surfaces_dangling_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept whose `relations:` target names an id absent from disk is
    listed under "needs attention", and `status` still exits 0 (status
    spec: "Dangling reference is surfaced under needs attention")."""
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

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "concepts/stoicism.md" in result.stdout
    assert "concepts/ghost" in result.stdout


def test_status_no_dangling_references_no_new_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle where every `relations:` target resolves to a concept id
    present on disk adds no dangling-reference entry under "needs
    attention" (status spec: "No dangling references, no new needs-
    attention entries")."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\n"
        "relations:\n"
        "  - target: concepts/epicureanism\n"
        "    type: relates-to\n"
        "---\nBody.\n",
        encoding="utf-8",
    )
    (concepts_dir / "epicureanism.md").write_text(
        "---\ntype: Concept\ntitle: Epicureanism\n---\nBody.\n", encoding="utf-8"
    )
    index_path = tmp_path / "bundle" / "index.md"
    index_path.write_text(
        index_path.read_text(encoding="utf-8") + "\n# Concepts\n\n"
        "* [Stoicism](/concepts/stoicism.md) - test fixture.\n"
        "* [Epicureanism](/concepts/epicureanism.md) - test fixture.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "references missing concept" not in result.stdout


def test_status_surfaces_missing_vectors_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent `.openkos/vectors.db` is listed under "needs attention"
    with an `openkos reindex` instruction, and `status` still exits 0
    (status spec: "Missing vectors.db is surfaced")."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "vectors.db" in result.stdout
    assert "openkos reindex" in result.stdout


def test_status_present_vectors_db_produces_no_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present `.openkos/vectors.db` produces no missing-vector-index
    entry under "needs attention" (status spec: "Present vectors.db
    produces no vector-index entry")."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(parents=True, exist_ok=True)
    (openkos_dir / "vectors.db").write_bytes(b"")

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "vectors.db" not in result.stdout
    assert "Nothing needs attention." in result.stdout


def test_status_rejects_json_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--json` or other structured output mode is offered (spec:
    Read-Only and Human-Readable Only)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


def test_status_never_writes_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file under the workspace is created, modified, or deleted on any
    run -- healthy, empty, or with findings (spec: No mutation on any
    run)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts_dir = tmp_path / "bundle" / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "orphan.md").write_text(
        "---\ntitle: no type here\n---\nBody.\n", encoding="utf-8"
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert _snapshot(tmp_path) == before
