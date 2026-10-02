"""retire-superseded-sources, tasks 3.2-3.4 (#1259, #1263): retiring a Source
that another Source supersedes is a clean forget. The superseding Source's
`supersedes` edge and the surviving concepts' GENERATED references to the old
Source (provenance entry, `## Related` bullet) are historical, so they are
removed in the same confirmed forget instead of blocking it; every other
reference still blocks."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import lifecycle
from openkos.bundle import provenance as bundle_provenance
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import (
    changed_paths,
    commit_pending_fixture_docs,
    confirm_after,
    snapshot_with_mtime,
)

runner = CliRunner()

_GENERATED = okf.Generated(by="openkos-test", at="2026-01-01T00:00:00Z")


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _write(bundle_dir: Path, concept_id: str, text: str) -> Path:
    path = bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _source(bundle_dir: Path, slug: str, *, supersedes: str | None = None) -> Path:
    metadata: dict[str, object] = {
        "type": "Source",
        "title": slug.upper(),
        "sensitivity": "private",
    }
    if supersedes is not None:
        metadata["relations"] = [{"target": supersedes, "type": "supersedes"}]
    return _write(
        bundle_dir, f"sources/{slug}", okf.dump_frontmatter(metadata, "Body.\n")
    )


def _concept(
    bundle_dir: Path,
    slug: str,
    provenance: list[str],
    *,
    sensitivity: str = "private",
    version: int | None = None,
) -> Path:
    text = okf.build_concept(
        type="Concept",
        title=slug.title(),
        description=f"About {slug}.",
        body="Body text.",
        provenance=provenance,
        sensitivity=sensitivity,
        generated=_GENERATED,
    )
    if version is not None:
        metadata, body = okf.load_frontmatter(text)
        metadata["version"] = version
        text = okf.dump_frontmatter(metadata, body)
    return _write(bundle_dir, f"concepts/{slug}", text)


def _two_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, Path]:
    """v2 supersedes v1; c1 rests only on v1, c2 on both."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _source(bundle_dir, "v1")
    v2 = _source(bundle_dir, "v2", supersedes="sources/v1")
    c1 = _concept(bundle_dir, "c1", ["sources/v1"])
    c2 = _concept(bundle_dir, "c2", ["sources/v1", "sources/v2"])
    commit_pending_fixture_docs()
    return bundle_dir, v2, c1, c2


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def test_retiring_the_old_version_needs_no_force_and_leaves_nothing_dangling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1263 reproduced end to end: the engine recommends `relate v2
    supersedes v1`, then `forget v1`. It used to refuse on that very edge."""
    bundle_dir, v2, c1, c2 = _two_versions(tmp_path, monkeypatch)

    result = runner.invoke(app, ["forget", "sources/v1", "--scope", "source", "--auto"])

    assert result.exit_code == 0, result.output
    assert not (bundle_dir / "sources" / "v1.md").exists()
    assert not c1.exists()  # sole-source: deleted, never edited
    v2_meta, _ = okf.load_frontmatter(v2.read_text(encoding="utf-8"))
    assert okf.RELATIONS_KEY not in v2_meta
    c2_meta, c2_body = okf.load_frontmatter(c2.read_text(encoding="utf-8"))
    assert c2_meta["provenance"] == ["sources/v2"]
    assert "sources/v1" not in c2_body
    assert "sources/v1" not in c2.read_text(encoding="utf-8")
    lint = runner.invoke(app, ["lint"])
    assert "sources/v1" not in lint.output
    assert "No dangling references" in lint.output


def test_the_edits_are_previewed_beside_the_deletions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_versions(tmp_path, monkeypatch)

    result = runner.invoke(app, ["forget", "sources/v1", "--scope", "source", "--auto"])

    assert result.exit_code == 0
    assert "~ bundle/sources/v2.md (remove supersedes -> sources/v1)" in result.output
    assert (
        "~ bundle/concepts/c2.md (detach sources/v1 from provenance)" in result.output
    )
    assert "- bundle/sources/v1.md" in result.output
    assert "- bundle/concepts/c1.md" in result.output
    assert "Total: 2 concept(s) to delete." in result.output
    assert "!" not in "".join(
        line for line in result.output.splitlines() if "bundle/sources/v2" in line
    )


def test_a_detached_concept_keeps_its_sensitivity_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _source(bundle_dir, "v1")
    _source(bundle_dir, "v2", supersedes="sources/v1")
    c2 = _concept(
        bundle_dir,
        "c2",
        ["sources/v1", "sources/v2"],
        sensitivity="confidential",
        version=3,
    )
    commit_pending_fixture_docs()

    result = runner.invoke(app, ["forget", "sources/v1", "--scope", "source", "--auto"])

    assert result.exit_code == 0, result.output
    meta, _ = okf.load_frontmatter(c2.read_text(encoding="utf-8"))
    assert meta["sensitivity"] == "confidential"
    assert meta["version"] == 3


def test_a_hand_written_link_still_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir, *_ = _two_versions(tmp_path, monkeypatch)
    _write(
        bundle_dir,
        "concepts/h",
        okf.dump_frontmatter(
            {"type": "Concept", "title": "H", "sensitivity": "private"},
            "See [the original](/sources/v1.md).\n",
        ),
    )
    commit_pending_fixture_docs()
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["forget", "sources/v1", "--scope", "source", "--auto"])

    assert result.exit_code == 1
    assert "refusing to forget" in result.stderr
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == set()


def test_a_hand_edited_related_bullet_still_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir, _, _, c2 = _two_versions(tmp_path, monkeypatch)
    c2.write_text(
        c2.read_text(encoding="utf-8").replace(
            "[sources/v1](/sources/v1.md) — source this was extracted from",
            "[sources/v1](/sources/v1.md) — my own notes",
        ),
        encoding="utf-8",
    )
    commit_pending_fixture_docs()
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["forget", "sources/v1", "--scope", "source", "--auto"])

    assert result.exit_code == 1
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == set()
    assert bundle_dir.exists()


def test_a_supersedes_edge_from_a_non_source_still_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _source(bundle_dir, "v1")
    _write(
        bundle_dir,
        "concepts/d",
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "D",
                "sensitivity": "private",
                "relations": [{"target": "sources/v1", "type": "supersedes"}],
            },
            "Body.\n",
        ),
    )
    commit_pending_fixture_docs()
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["forget", "sources/v1", "--scope", "source", "--auto"])

    assert result.exit_code == 1
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == set()


def test_another_relation_type_from_the_superseding_source_still_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _source(bundle_dir, "v1")
    metadata: dict[str, object] = {
        "type": "Source",
        "title": "V2",
        "sensitivity": "private",
        "relations": [
            {"target": "sources/v1", "type": "supersedes"},
            {"target": "sources/v1", "type": "references"},
        ],
    }
    _write(bundle_dir, "sources/v2", okf.dump_frontmatter(metadata, "Body.\n"))
    commit_pending_fixture_docs()
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["forget", "sources/v1", "--scope", "source", "--auto"])

    assert result.exit_code == 1
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == set()


def test_a_forget_with_no_superseded_source_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `supersedes` edge: a surviving concept's generated reference to the
    forgotten Source still blocks exactly as before."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _source(bundle_dir, "v1")
    _source(bundle_dir, "v2")
    _concept(bundle_dir, "c2", ["sources/v1", "sources/v2"])
    commit_pending_fixture_docs()
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["forget", "sources/v1", "--scope", "source", "--auto"])

    assert result.exit_code == 1
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == set()


@pytest.mark.parametrize(
    "target",
    ["bundle/sources/v2.md", "bundle/concepts/c2.md"],
)
def test_a_changed_historical_target_refuses_the_whole_forget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    _two_versions(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    target_path = tmp_path / target
    concurrent = target_path.read_text(encoding="utf-8") + "\nhand edit\n"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, lambda: target_path.write_text(concurrent, "utf-8"))

    result = runner.invoke(
        app, ["forget", "sources/v1", "--scope", "source"], input="y\n"
    )

    assert result.exit_code == 3
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == {Path(target)}


def test_1259_edited_watched_source_flow_ends_with_only_the_live_version_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1259 + #1263 end to end: v2 supersedes v1 -> v1's sole-source concept
    is deprecated at read time (no frontmatter written) -> forgetting v1
    needs no `--force` -> `lint` is clean and nothing derived from v1 is
    left active."""
    bundle_dir, _v2, c1, _c2 = _two_versions(tmp_path, monkeypatch)
    c1_before = c1.read_bytes()

    assert lifecycle.deprecated_concept_ids(bundle_dir) == frozenset(
        {"sources/v1", "concepts/c1"}
    )
    assert c1.read_bytes() == c1_before  # computed at read time, written nowhere

    result = runner.invoke(app, ["forget", "sources/v1", "--scope", "source", "--auto"])

    assert result.exit_code == 0, result.output
    assert lifecycle.deprecated_concept_ids(bundle_dir) == frozenset()
    live_provenance = {
        path.stem: bundle_provenance.parse_provenance_entry(
            path.read_text(encoding="utf-8")
        )
        for path in (bundle_dir / "concepts").glob("*.md")
    }
    assert live_provenance == {"c2": frozenset({"sources/v2"})}
    lint = runner.invoke(app, ["lint"])
    assert "No dangling references" in lint.output
