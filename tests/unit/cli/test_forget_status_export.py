"""CLI-level tests for `forget`'s deprecated-status export withdrawal
(deprecated-status-export, issue #1075, Phase 4): a resurrected target
outside the purge set has its export withdrawn in the SAME Phase B write as
the catalog update, before any delete (spec: `forget-command` "Resurrection
Interaction Disclosure", "Catalog-Before-File Write Ordering"). Concepts are
hand-written so their initial `status`/marker is controlled precisely,
mirroring `test_repair_status_export.py`."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import fsio
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import commit_pending_fixture_docs

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
    commit_pending_fixture_docs()
    return path


def _metadata_of(tmp_path: Path, concept_id: str) -> dict[str, object]:
    text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(text)
    return metadata


def test_forget_withdraws_the_resurrected_targets_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'A resurrected target's export is withdrawn' -- Y carries
    `status: stable` and no marker after M is forgotten, and the preview
    named Y's status change before the confirm gate."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(
        bundle_dir,
        "concepts/m",
        {
            "type": "Concept",
            "title": "M",
            "relations": [{"target": "concepts/y", "type": "supersedes"}],
        },
    )
    _write_concept(
        bundle_dir,
        "concepts/y",
        {
            "type": "Concept",
            "title": "Y",
            "status": "deprecated",
            okf.STATUS_DERIVED_FROM_KEY: "supersedes",
        },
    )

    result = runner.invoke(app, ["forget", "concepts/m", "--auto"])

    assert result.exit_code == 0
    assert "status → stable" in result.output
    metadata = _metadata_of(tmp_path, "concepts/y")
    assert metadata["status"] == "stable"
    assert okf.STATUS_DERIVED_FROM_KEY not in metadata


def test_forget_keeps_export_when_target_still_superseded_by_survivor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'A target superseded by a surviving concept keeps its export'
    -- Y is superseded by BOTH M (forgotten) and N (survives); Y's bytes
    are unchanged."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(
        bundle_dir,
        "concepts/m",
        {
            "type": "Concept",
            "title": "M",
            "relations": [{"target": "concepts/y", "type": "supersedes"}],
        },
    )
    _write_concept(
        bundle_dir,
        "concepts/n",
        {
            "type": "Concept",
            "title": "N",
            "relations": [{"target": "concepts/y", "type": "supersedes"}],
        },
    )
    _write_concept(
        bundle_dir,
        "concepts/y",
        {
            "type": "Concept",
            "title": "Y",
            "status": "deprecated",
            okf.STATUS_DERIVED_FROM_KEY: "supersedes",
        },
    )
    before = (bundle_dir / "concepts" / "y.md").read_bytes()

    result = runner.invoke(app, ["forget", "concepts/m", "--auto"])

    assert result.exit_code == 0
    assert (bundle_dir / "concepts" / "y.md").read_bytes() == before


def test_forget_keeps_a_human_authored_deprecation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'A human-authored deprecation survives the forget' -- Y carries
    `status: deprecated` with NO marker; forgetting M leaves Y's bytes
    unchanged."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(
        bundle_dir,
        "concepts/m",
        {
            "type": "Concept",
            "title": "M",
            "relations": [{"target": "concepts/y", "type": "supersedes"}],
        },
    )
    _write_concept(
        bundle_dir,
        "concepts/y",
        {"type": "Concept", "title": "Y", "status": "deprecated"},
    )
    before = (bundle_dir / "concepts" / "y.md").read_bytes()

    result = runner.invoke(app, ["forget", "concepts/m", "--auto"])

    assert result.exit_code == 0
    assert (bundle_dir / "concepts" / "y.md").read_bytes() == before


def test_write_ordering_export_lands_before_the_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'Catalog-Before-File Write Ordering' -- the export withdrawal
    is written BEFORE the delete: fault-injecting `fsio.remove_file` to fail
    proves the catalog and Y's withdrawn export already landed while M's
    concept file still exists."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(
        bundle_dir,
        "concepts/m",
        {
            "type": "Concept",
            "title": "M",
            "relations": [{"target": "concepts/y", "type": "supersedes"}],
        },
    )
    _write_concept(
        bundle_dir,
        "concepts/y",
        {
            "type": "Concept",
            "title": "Y",
            "status": "deprecated",
            okf.STATUS_DERIVED_FROM_KEY: "supersedes",
        },
    )

    def raising_remove_file(path: Path) -> None:
        raise OSError("simulated delete failure")

    monkeypatch.setattr(fsio, "remove_file", raising_remove_file)

    result = runner.invoke(app, ["forget", "concepts/m", "--auto"])

    assert result.exit_code == 1
    assert (bundle_dir / "concepts" / "m.md").is_file()
    metadata = _metadata_of(tmp_path, "concepts/y")
    assert metadata["status"] == "stable"
    assert okf.STATUS_DERIVED_FROM_KEY not in metadata
    index_text = (bundle_dir / "index.md").read_text(encoding="utf-8")
    assert "concepts/m.md" not in index_text


def test_incomplete_walk_keeps_the_export_and_reports_the_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'Withdrawal Requires A Complete Edge Walk' -- an unrelated
    malformed bystander document (never mentioning M or Y, so the
    unverifiable-referrer gate does not block) makes the post-forget edge
    walk incomplete; Y's export is kept and the preview reports the skip."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(
        bundle_dir,
        "concepts/m",
        {
            "type": "Concept",
            "title": "M",
            "relations": [{"target": "concepts/y", "type": "supersedes"}],
        },
    )
    _write_concept(
        bundle_dir,
        "concepts/y",
        {
            "type": "Concept",
            "title": "Y",
            "status": "deprecated",
            okf.STATUS_DERIVED_FROM_KEY: "supersedes",
        },
    )
    bystander_path = bundle_dir / "concepts" / "bystander.md"
    bystander_path.parent.mkdir(parents=True, exist_ok=True)
    bystander_path.write_text(
        "---\n"
        "type: Concept\n"
        "title: Bad\n"
        "relations: [target: concepts/unrelated, type: depends_on\n"
        "---\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["forget", "concepts/m", "--auto"])

    assert result.exit_code == 0
    assert "skipped" in result.output.lower()
    metadata = _metadata_of(tmp_path, "concepts/y")
    assert metadata["status"] == "deprecated"
    assert metadata[okf.STATUS_DERIVED_FROM_KEY] == "supersedes"


def test_no_out_of_set_edge_no_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'No out-of-set supersedes edge, no disclosure' -- forgetting a
    concept with no outbound `supersedes` edge prints no resurrection line
    and no status-change line."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts/m", {"type": "Concept", "title": "M"})

    result = runner.invoke(app, ["forget", "concepts/m", "--auto"])

    assert result.exit_code == 0
    assert "re-enters retrieval" not in result.output
    assert "status → stable" not in result.output
