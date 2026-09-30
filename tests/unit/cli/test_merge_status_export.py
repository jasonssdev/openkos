"""CLI-level tests for the deprecated-status export's interaction with
`merge`/`unmerge` (deprecated-status-export, issue #1075, Phase 5): the
absorbed side's export must never cross into the survivor, and after
relations rewire, the survivor's status is decided by the export
projection over its post-merge superseded state (spec:
`entity-resolution-merge` "Frontmatter-Conflict Resolution", "Unmerge
Achieves Round-Trip Parity")."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.bundle import index as bundle_index
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import commit_pending_fixture_docs

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _write_concept(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    metadata_extra: dict[str, object] | None = None,
    relations: list[dict[str, str]] | None = None,
    section: str = "Concepts",
    body: str = "Body.\n",
) -> None:
    """Write a concept file directly to the bundle and hand-author its
    matching `index.md` bullet (mirrors `test_merge.py::_write_concept`,
    extended with arbitrary extra frontmatter and `relations:`)."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {"type": "Concept", "title": title}
    if metadata_extra:
        metadata.update(metadata_extra)
    if relations is not None:
        metadata[okf.RELATIONS_KEY] = relations
    concept_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")

    link_dir, slug = concept_id.rsplit("/", 1)
    index_path = tmp_path / "bundle" / "index.md"
    index_text = index_path.read_text(encoding="utf-8")
    new_index_text = bundle_index.insert_index_entry(
        index_text,
        section=section,
        link_dir=link_dir,
        title=title,
        slug=slug,
        description=f"{title}.",
    )
    index_path.write_text(new_index_text, encoding="utf-8")
    commit_pending_fixture_docs()


def _metadata_of(tmp_path: Path, concept_id: str) -> dict[str, object]:
    text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(text)
    return metadata


def test_pin_absorbed_status_fills_survivor_gap_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 5.1 PIN (pre-Phase-5 behavior): a survivor with NO `status` key
    and an absorbed object carrying a plain, human-authored
    `status: deprecated` (no marker) -- today's generic scalar
    fill-the-gap rule copies it onto the merged survivor. This is the
    documented, UNCHANGED behavior for a human-authored value (spec:
    'A human-authored absorbed status (no valid marker) still follows the
    generic scalar rule')."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        metadata_extra={"status": "deprecated"},
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.output
    metadata = _metadata_of(tmp_path, "concepts/survivor")
    assert metadata["status"] == "deprecated"
    assert okf.STATUS_DERIVED_FROM_KEY not in metadata


def test_merge_exports_a_survivor_newly_superseded_by_a_retargeted_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: 'A survivor newly superseded by the merge is exported' -- a
    third party holds a `supersedes` edge to the absorbed object; merging
    retargets it to the survivor, which is now superseded and gets
    exported, named in the preview."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept(
        tmp_path,
        "concepts/third-party",
        title="Third Party",
        relations=[{"target": "concepts/absorbed", "type": "supersedes"}],
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.output
    assert "status → deprecated" in result.output
    metadata = _metadata_of(tmp_path, "concepts/survivor")
    assert metadata["status"] == "deprecated"
    assert metadata[okf.STATUS_DERIVED_FROM_KEY] == "supersedes"


def test_merge_withdraws_a_survivor_export_whose_edge_self_loop_drops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec-adjacent (design Decision 6): the absorbed object's own
    outbound `supersedes` edge targeting the survivor becomes a SELF-LOOP
    after merge and is dropped by `merge_relations`; if that was the
    survivor's ONLY superseding edge and it already carried an export
    (because of that edge, applied by `repair` before the merge), the
    merge withdraws it."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path,
        "concepts/survivor",
        title="Survivor",
        metadata_extra={
            "status": "deprecated",
            okf.STATUS_DERIVED_FROM_KEY: "supersedes",
        },
    )
    _write_concept(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        relations=[{"target": "concepts/survivor", "type": "supersedes"}],
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.output
    assert "status → stable" in result.output
    metadata = _metadata_of(tmp_path, "concepts/survivor")
    assert metadata["status"] == "stable"
    assert okf.STATUS_DERIVED_FROM_KEY not in metadata


def _bytes_of(tmp_path: Path, concept_id: str) -> bytes:
    return (tmp_path / "bundle" / f"{concept_id}.md").read_bytes()


def test_merge_then_unmerge_parity_on_an_export_consistent_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 5.6 (spec: 'Unmerge parity holds on an export-consistent
    bundle'): a third party already supersedes the absorbed object, which
    already carries a valid export -- `merge` then `unmerge` leaves every
    bundle file, survivor and absorbed included, byte-for-byte identical
    to its pre-merge state."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        metadata_extra={
            "status": "deprecated",
            okf.STATUS_DERIVED_FROM_KEY: "supersedes",
        },
    )
    _write_concept(
        tmp_path,
        "concepts/third-party",
        title="Third Party",
        relations=[{"target": "concepts/absorbed", "type": "supersedes"}],
    )
    before_survivor = _bytes_of(tmp_path, "concepts/survivor")
    before_absorbed = _bytes_of(tmp_path, "concepts/absorbed")
    before_third_party = _bytes_of(tmp_path, "concepts/third-party")

    merged = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merged.exit_code == 0, merged.output
    unmerged = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert unmerged.exit_code == 0, unmerged.output

    assert _bytes_of(tmp_path, "concepts/survivor") == before_survivor
    assert _bytes_of(tmp_path, "concepts/absorbed") == before_absorbed
    assert _bytes_of(tmp_path, "concepts/third-party") == before_third_party


def test_unmerge_exports_a_restored_document_whose_pre_merge_state_had_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 5.7 (spec: 'Unmerge exports a restored document whose pre-merge
    state had drift'): a third party held a hand-written `supersedes` edge
    to the absorbed object BEFORE the merge, but absorbed's own `status`
    was never exported (`stable`, pre-existing drift). After `merge` then
    `unmerge`, the restored absorbed document IS exported (the edge is
    live again post-unmerge), the preview names it, and every other
    restored byte still matches its snapshot -- the ONE permitted
    deviation from byte-for-byte parity."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        metadata_extra={"status": "stable"},
    )
    _write_concept(
        tmp_path,
        "concepts/third-party",
        title="Third Party",
        relations=[{"target": "concepts/absorbed", "type": "supersedes"}],
    )
    before_third_party = _bytes_of(tmp_path, "concepts/third-party")
    before_survivor = _bytes_of(tmp_path, "concepts/survivor")

    merged = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merged.exit_code == 0, merged.output
    unmerged = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert unmerged.exit_code == 0, unmerged.output

    assert "status → deprecated" in unmerged.output
    metadata = _metadata_of(tmp_path, "concepts/absorbed")
    assert metadata["status"] == "deprecated"
    assert metadata[okf.STATUS_DERIVED_FROM_KEY] == "supersedes"
    assert _bytes_of(tmp_path, "concepts/third-party") == before_third_party
    assert _bytes_of(tmp_path, "concepts/survivor") == before_survivor


def test_unmerge_to_chain_leaves_lint_clean_at_every_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 5.10: `unmerge --to` unwinding a two-merge chain, where each
    absorbed member was itself superseded by its own third party, restores
    each member's export correctly and leaves `lint` reporting zero
    `status-export-drift` at the end."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/alpha", title="Alpha")
    _write_concept(tmp_path, "concepts/beta", title="Beta")
    _write_concept(
        tmp_path,
        "concepts/alpha-superseder",
        title="Alpha Superseder",
        relations=[{"target": "concepts/alpha", "type": "supersedes"}],
    )
    _write_concept(
        tmp_path,
        "concepts/beta-superseder",
        title="Beta Superseder",
        relations=[{"target": "concepts/beta", "type": "supersedes"}],
    )

    for absorbed in ("concepts/alpha", "concepts/beta"):
        merged = runner.invoke(app, ["merge", "concepts/survivor", absorbed, "--auto"])
        assert merged.exit_code == 0, merged.output

    unwound = runner.invoke(
        app, ["unmerge", "concepts/survivor", "--to", "concepts/alpha", "--auto"]
    )
    assert unwound.exit_code == 0, unwound.output

    for concept_id in ("concepts/alpha", "concepts/beta"):
        metadata = _metadata_of(tmp_path, concept_id)
        assert metadata["status"] == "deprecated"
        assert metadata[okf.STATUS_DERIVED_FROM_KEY] == "supersedes"

    lint_result = runner.invoke(app, ["lint"])
    assert lint_result.exit_code == 0, lint_result.output
    assert "status-export-drift" not in lint_result.output


def test_pin_unmerge_does_not_refuse_on_a_survivor_edited_after_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 5.1 PIN (design Open Question, resolved): `unmerge` does NOT
    refuse when the survivor was hand-edited after the merge -- it
    silently overwrites the survivor with the ledger's pre-merge snapshot,
    discarding the interleaved edit. There is no drift refusal for this
    window; `prepare_unmerge`'s `survivor_bytes` baseline is captured
    fresh at ITS OWN Phase A (whatever is on disk right now), never
    compared against the merge-time state."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    merged = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merged.exit_code == 0, merged.output

    survivor_path = tmp_path / "bundle" / "concepts" / "survivor.md"
    hand_edit = "hand-edited after the merge, before unmerge\n"
    survivor_path.write_text(hand_edit, encoding="utf-8")

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.output
    assert survivor_path.read_text(encoding="utf-8") != hand_edit
    metadata, _ = okf.load_frontmatter(survivor_path.read_text(encoding="utf-8"))
    assert metadata["title"] == "Survivor"
