"""CLI-level tests for `relate`'s deprecated-status export write
(deprecated-status-export, issue #1075, Phase 3, task 3.6): `relate <a>
supersedes <b>` exports `b`'s status in the SAME Phase B write as the edge
(spec: `typed-relationships` "`relate` Of A `supersedes` Edge Writes The
Deprecated-Status Export"). Concepts are hand-written so their initial
`status` is controlled precisely, mirroring `test_repair_status_export.py`."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import changed_paths, confirm_after, snapshot_with_mtime

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


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


def test_relate_supersedes_exports_the_targets_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'relate supersedes exports the target's status' -- `b` carries
    `status: deprecated` + the marker, written in the same commit."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts/a", {"type": "Concept", "title": "A"})
    _write_concept(
        bundle_dir, "concepts/b", {"type": "Concept", "title": "B", "status": "stable"}
    )

    result = runner.invoke(
        app, ["relate", "concepts/a", "supersedes", "concepts/b", "--auto"]
    )

    assert result.exit_code == 0
    metadata = _metadata_of(tmp_path, "concepts/b")
    assert metadata["status"] == "deprecated"
    assert metadata[okf.STATUS_DERIVED_FROM_KEY] == "supersedes"
    relations = okf.decode_relations(_metadata_of(tmp_path, "concepts/a"))
    assert relations == [okf.Relation(target="concepts/b", type="supersedes")]


def test_relate_of_any_other_type_writes_no_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'relate of any other type writes no status' -- `b`'s bytes are
    unchanged when the relation type is not `supersedes`."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts/a", {"type": "Concept", "title": "A"})
    _write_concept(
        bundle_dir, "concepts/b", {"type": "Concept", "title": "B", "status": "stable"}
    )
    before = (bundle_dir / "concepts" / "b.md").read_bytes()

    result = runner.invoke(
        app, ["relate", "concepts/a", "references", "concepts/b", "--auto"]
    )

    assert result.exit_code == 0
    assert (bundle_dir / "concepts" / "b.md").read_bytes() == before


def test_idempotent_relate_supersedes_writes_no_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'An idempotent relate supersedes writes no status' -- `a`
    already holds the edge, `b` carries pre-existing drift (`stable`); a
    repeat `relate` call leaves `b`'s bytes unchanged (drift stays
    `repair`'s concern)."""
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
    before = (bundle_dir / "concepts" / "b.md").read_bytes()

    result = runner.invoke(
        app, ["relate", "concepts/a", "supersedes", "concepts/b", "--auto"]
    )

    assert result.exit_code == 0
    assert (bundle_dir / "concepts" / "b.md").read_bytes() == before


def test_target_drift_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `supersedes` target edited during the confirm prompt is drift too:
    the whole run refuses (exit 3), writing nothing -- mirroring the
    existing source/log drift guard coverage in `test_relate.py`."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_concept(bundle_dir, "concepts/a", {"type": "Concept", "title": "A"})
    target_path = _write_concept(
        bundle_dir, "concepts/b", {"type": "Concept", "title": "B", "status": "stable"}
    )
    _simulate_tty(monkeypatch)
    before = snapshot_with_mtime(tmp_path)
    concurrent = "hand-edited while the prompt waited\n"
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(
        app, ["relate", "concepts/a", "supersedes", "concepts/b"], input="y\n"
    )

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/concepts/b.md")}
