"""Unit tests for the `repair` CLI command (durable-derived-state slice
1b): migrates a legacy, frontmatter-embedded merge ledger verbatim into
`bundle/.state/ledger/`, refusing -- with NO override flag at all -- on any
sign of a torn write (Check A) or cross-survivor pollution risk (any
survivor bundle-wide carrying 2+ entries).
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.bundle import ledger as bundle_ledger
from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _make_entry(
    absorbed_id: str = "concepts/absorbed",
) -> okf.MergeLedgerEntry:
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id=absorbed_id,
        absorbed_snapshot="absorbed text",
        survivor_before="survivor text",
        index_before="index text",
        log_before="log text",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )


def _write_legacy_survivor(
    bundle_dir: Path, concept_id: str, *, entries: list[okf.MergeLedgerEntry]
) -> Path:
    """Write a survivor concept whose ledger is STILL embedded in its OWN
    frontmatter -- the pre-relocation shape `repair` migrates."""
    path = bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Survivor",
                "description": "A legacy survivor.",
                "merged_from": okf.encode_merged_from(entries),
            },
            "Survivor body.\n",
        ),
        encoding="utf-8",
    )
    return path


def test_repair_refuses_outside_a_workspace(tmp_path: Path) -> None:
    runner_result = None
    import os

    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        runner_result = runner.invoke(app, ["repair"])
    finally:
        os.chdir(old_cwd)

    assert runner_result.exit_code == 1
    assert "refusing" in runner_result.output.lower()


def test_repair_nothing_to_migrate_is_a_graceful_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "nothing to migrate" in result.output.lower()


def test_repair_refuses_with_no_override_when_a_torn_write_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.1: repair refuses on Check A (torn `.pending` present) with
    no override -- writes NOTHING."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    survivor_path = bundle_dir / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True, exist_ok=True)
    survivor_text = "---\ntype: Concept\ntitle: Survivor\n---\nBody.\n"
    survivor_path.write_text(survivor_text, encoding="utf-8")
    bundle_ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[_make_entry()],
        expected_survivor_sha256=bundle_ledger.survivor_sha256(survivor_text),
    )
    before = survivor_path.read_bytes()

    # `repair` accepts no flag to override this refusal at all.
    result = runner.invoke(app, ["repair", "--force"])
    assert result.exit_code != 0  # unknown option: typer rejects it outright

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 1
    assert "refusing" in result.output.lower()
    assert survivor_path.read_bytes() == before
    assert not bundle_ledger.ledger_path_for("concepts/survivor", bundle_dir).exists()


def test_repair_refuses_with_no_override_when_any_survivor_has_two_or_more_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.2: repair refuses whenever ANY survivor bundle-wide carries
    2+ entries -- regardless of Check B's per-ledger result, and this
    survivor's own ledger is left completely untouched. The refusal states
    the reset-and-replay path and the reversibility caveat (spec: Repair
    Verb Refuses On Any Sign Of Cross-Survivor Pollution Risk, #603)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    entries = [_make_entry("concepts/absorbed-0"), _make_entry("concepts/absorbed-1")]
    survivor_path = _write_legacy_survivor(
        bundle_dir, "concepts/survivor", entries=entries
    )
    before = survivor_path.read_bytes()

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 1
    assert "refusing" in result.output.lower()
    assert "git reset --hard <first-merge>~1" in result.output
    assert "openkos reindex" in result.output
    assert "not guaranteed" in result.output
    assert survivor_path.read_bytes() == before
    assert not bundle_ledger.ledger_path_for("concepts/survivor", bundle_dir).exists()


def test_repair_refuses_when_a_different_survivor_has_two_or_more_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is bundle-wide: an otherwise-clean single-entry survivor is
    STILL refused if some OTHER survivor in the bundle carries 2+ entries
    (cross-survivor-pollution risk, design Decision 5)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    clean_path = _write_legacy_survivor(
        bundle_dir,
        "concepts/clean-survivor",
        entries=[_make_entry("concepts/absorbed-a")],
    )
    _write_legacy_survivor(
        bundle_dir,
        "concepts/dirty-survivor",
        entries=[
            _make_entry("concepts/absorbed-b"),
            _make_entry("concepts/absorbed-c"),
        ],
    )
    before = clean_path.read_bytes()

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 1
    assert clean_path.read_bytes() == before
    assert not bundle_ledger.ledger_path_for(
        "concepts/clean-survivor", bundle_dir
    ).exists()


def test_repair_migrates_a_clean_single_entry_ledger_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.3: on a clean, single-entry-per-survivor bundle, repair
    extracts the entry out of frontmatter into `bundle/.state/ledger/`
    verbatim, and strips `merged_from` from the survivor's own
    frontmatter -- nothing else about the survivor changes."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    entry = _make_entry()
    _write_legacy_survivor(bundle_dir, "concepts/survivor", entries=[entry])

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "migrated 1 ledger" in result.output.lower()

    sidecar_entries = bundle_ledger.read_entries("concepts/survivor", bundle_dir)
    assert sidecar_entries == [entry]

    survivor_text = (bundle_dir / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    metadata, body = okf.load_frontmatter(survivor_text)
    assert "merged_from" not in metadata
    assert metadata["title"] == "Survivor"
    assert metadata["description"] == "A legacy survivor."
    assert body == "Survivor body."


def test_repair_prints_the_reset_hard_inverse_when_a_reset_point_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.4: repair prints the `git reset --hard` inverse before
    writing when a reset point exists."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_legacy_survivor(bundle_dir, "concepts/survivor", entries=[_make_entry()])
    monkeypatch.setattr("openkos.cli.main.vcs_git.repo_root", lambda root: root)
    monkeypatch.setattr("openkos.cli.main.vcs_git.has_reset_point", lambda root: True)

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "git reset --hard" in result.output


def test_repair_warns_no_reset_point_available_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.4's other branch: no reset point (e.g. no configured git
    identity, per the orchestrator-flagged gap) prints an explicit warning
    instead of an unusable `git reset --hard` promise, and the migration
    still runs (repair's own writes are independent of `_autocommit`)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    entry = _make_entry()
    _write_legacy_survivor(bundle_dir, "concepts/survivor", entries=[entry])
    monkeypatch.setattr("openkos.cli.main.vcs_git.repo_root", lambda root: None)

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "no git reset point is available" in result.output.lower()
    assert bundle_ledger.read_entries("concepts/survivor", bundle_dir) == [entry]


def test_repair_migrates_multiple_unmigrated_survivors_in_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    entry_a = _make_entry("concepts/absorbed-a")
    entry_b = _make_entry("concepts/absorbed-b")
    _write_legacy_survivor(bundle_dir, "concepts/survivor-a", entries=[entry_a])
    _write_legacy_survivor(bundle_dir, "concepts/survivor-b", entries=[entry_b])

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "migrated 2 ledgers" in result.output.lower()
    assert bundle_ledger.read_entries("concepts/survivor-a", bundle_dir) == [entry_a]
    assert bundle_ledger.read_entries("concepts/survivor-b", bundle_dir) == [entry_b]
