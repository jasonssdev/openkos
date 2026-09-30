"""CLI-level tests for `reconcile`'s deprecated-status export write
(deprecated-status-export, issue #1075, Phase 3): a directional `--winner`
resolution exports the loser's `status` in the SAME Phase B write as the
`supersedes` edge (spec: `reconcile-command` "Additive-Only, Status Written
Only As The Supersedes Export"). Concepts are hand-written (not `ingest`ed)
so their initial `status` is controlled precisely, mirroring
`test_repair_status_export.py`'s fixture style."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()


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


def _metadata_of(tmp_path: Path, concept_id: str) -> dict[str, object]:
    text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(text)
    return metadata


def _bytes_of(tmp_path: Path, concept_id: str) -> bytes:
    return (tmp_path / "bundle" / f"{concept_id}.md").read_bytes()


def test_winner_reconcile_exports_the_losers_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'A directional supersede exports the loser's status' -- `beta`
    carries `status: deprecated` + the marker, written in the same commit as
    the edge, and the preview named the change before the confirm gate."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts/alpha", {"type": "Concept", "title": "Alpha"})
    _write_concept(
        bundle_dir,
        "concepts/beta",
        {"type": "Concept", "title": "Beta", "status": "stable"},
    )

    result = runner.invoke(
        app,
        [
            "reconcile",
            "concepts/alpha",
            "concepts/beta",
            "--winner",
            "concepts/alpha",
            "--auto",
        ],
    )

    assert result.exit_code == 0
    assert "status → deprecated" in result.output
    metadata = _metadata_of(tmp_path, "concepts/beta")
    assert metadata["status"] == "deprecated"
    assert metadata[okf.STATUS_DERIVED_FROM_KEY] == "supersedes"


def test_winner_reconcile_keeps_a_draft_losers_own_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'A loser with a human-authored draft keeps it' -- the edge is
    still written, but `beta`'s own `status: draft` is untouched, and the
    preview states its own status is preserved."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts/alpha", {"type": "Concept", "title": "Alpha"})
    _write_concept(
        bundle_dir,
        "concepts/beta",
        {"type": "Concept", "title": "Beta", "status": "draft"},
    )

    result = runner.invoke(
        app,
        [
            "reconcile",
            "concepts/alpha",
            "concepts/beta",
            "--winner",
            "concepts/alpha",
            "--auto",
        ],
    )

    assert result.exit_code == 0
    assert "own status" in result.output
    metadata = _metadata_of(tmp_path, "concepts/beta")
    assert metadata["status"] == "draft"
    assert okf.STATUS_DERIVED_FROM_KEY not in metadata
    relations = okf.decode_relations(_metadata_of(tmp_path, "concepts/alpha"))
    assert relations == [okf.Relation(target="concepts/beta", type="supersedes")]


def test_symmetric_and_revision_reconcile_write_no_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'Symmetric and revision reconciles write no status' -- neither
    a plain (symmetric) reconcile nor `--revision` touches `status` or
    `status_derived_from`."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(
        bundle_dir, "concepts/a", {"type": "Concept", "title": "A", "status": "stable"}
    )
    _write_concept(
        bundle_dir, "concepts/b", {"type": "Concept", "title": "B", "status": "stable"}
    )
    _write_concept(
        bundle_dir, "concepts/c", {"type": "Concept", "title": "C", "status": "stable"}
    )
    _write_concept(
        bundle_dir, "concepts/d", {"type": "Concept", "title": "D", "status": "stable"}
    )

    symmetric = runner.invoke(app, ["reconcile", "concepts/a", "concepts/b", "--auto"])
    assert symmetric.exit_code == 0
    revision = runner.invoke(
        app,
        [
            "reconcile",
            "concepts/c",
            "concepts/d",
            "--revision",
            "concepts/c",
            "--auto",
        ],
    )
    assert revision.exit_code == 0

    for concept_id in ("concepts/a", "concepts/b", "concepts/c", "concepts/d"):
        metadata = _metadata_of(tmp_path, concept_id)
        assert metadata["status"] == "stable"
        assert okf.STATUS_DERIVED_FROM_KEY not in metadata


def test_idempotent_winner_rerun_writes_no_status_the_second_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: an idempotent re-run (edge present, export missing) writes no
    status -- pre-existing drift stays `repair`'s concern, not a re-run's."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(
        bundle_dir,
        "concepts/alpha",
        {
            "type": "Concept",
            "title": "Alpha",
            "relations": [{"target": "concepts/beta", "type": "supersedes"}],
        },
    )
    _write_concept(
        bundle_dir,
        "concepts/beta",
        {"type": "Concept", "title": "Beta", "status": "stable"},
    )

    result = runner.invoke(
        app,
        [
            "reconcile",
            "concepts/alpha",
            "concepts/beta",
            "--winner",
            "concepts/alpha",
            "--auto",
        ],
    )

    assert result.exit_code == 0
    metadata = _metadata_of(tmp_path, "concepts/beta")
    assert metadata["status"] == "stable"
    assert okf.STATUS_DERIVED_FROM_KEY not in metadata


def test_declined_supersede_writes_no_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'A declined supersede writes no status' -- a non-`--auto`,
    `review: false` config skips the prompt without writing anything when
    Phase A itself is never reached... this exercises the ACTUAL declined
    gate: `--auto` omitted, `review: true` (default), non-TTY refuses before
    any write."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts/alpha", {"type": "Concept", "title": "Alpha"})
    _write_concept(
        bundle_dir,
        "concepts/beta",
        {"type": "Concept", "title": "Beta", "status": "stable"},
    )
    before = _bytes_of(tmp_path, "concepts/beta")

    result = runner.invoke(
        app,
        ["reconcile", "concepts/alpha", "concepts/beta", "--winner", "concepts/alpha"],
    )

    assert result.exit_code == 1
    assert _bytes_of(tmp_path, "concepts/beta") == before


def test_lint_reports_zero_drift_after_reconcile_and_relate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.8: a bundle built ONLY by `reconcile --winner` and
    `relate supersedes` never carries `status-export-drift` -- both writers
    keep their exports consistent as they go."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts/alpha", {"type": "Concept", "title": "Alpha"})
    _write_concept(
        bundle_dir,
        "concepts/beta",
        {"type": "Concept", "title": "Beta", "status": "stable"},
    )
    _write_concept(bundle_dir, "concepts/gamma", {"type": "Concept", "title": "Gamma"})
    _write_concept(
        bundle_dir,
        "concepts/delta",
        {"type": "Concept", "title": "Delta", "status": "stable"},
    )

    reconciled = runner.invoke(
        app,
        [
            "reconcile",
            "concepts/alpha",
            "concepts/beta",
            "--winner",
            "concepts/alpha",
            "--auto",
        ],
    )
    assert reconciled.exit_code == 0
    related = runner.invoke(
        app, ["relate", "concepts/gamma", "supersedes", "concepts/delta", "--auto"]
    )
    assert related.exit_code == 0

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "status-export-drift" not in result.output
