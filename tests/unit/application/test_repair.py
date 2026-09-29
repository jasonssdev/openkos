"""Unit tests for `application/repair.py` (okf-v02-migration Phase 6,
design.md Decision 9): the pure plan phase (`plan_repair`) and the
write-only apply phase (`apply_repair`), exercised directly as a library.
CLI-level behavior (the reset-point note, the post-confirm drift guard, the
printed report, `_autocommit`, the commit message) stays in
`tests/unit/cli/test_repair.py`. Write-order and crash-injection tests
live here rather than there, following this project's established
`tests/unit/bundle/test_ledger_crash_injection.py` pattern of injecting a
failure directly on the write primitive rather than through the CLI."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from openkos import config, fsio
from openkos.application import repair as application_repair
from openkos.bundle import bundle
from openkos.bundle import ledger as bundle_ledger
from openkos.model import okf


def _workspace(root: Path) -> config.WorkspaceLayout:
    config.write_config(root)
    layout = config.WorkspaceLayout(root)
    bundle.create(layout.bundle_dir, date(2026, 1, 1))
    return layout


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


def test_plan_repair_gate1_pending_marker_refuses_unconditionally(
    tmp_path: Path,
) -> None:
    """Task 6.4: a `.pending` marker anywhere refuses the whole plan,
    regardless of OKF or ledger state -- unchanged text, computed entirely
    by reads."""
    layout = _workspace(tmp_path)
    survivor_path = _write_concept(
        layout.bundle_dir, "concepts/survivor", {"type": "Concept", "title": "Survivor"}
    )
    bundle_ledger.write_pending(
        "concepts/survivor",
        layout.bundle_dir,
        survivor_id="concepts/survivor",
        entries=[],
        expected_survivor_sha256=bundle_ledger.survivor_sha256(
            survivor_path.read_text(encoding="utf-8")
        ),
    )

    plan = application_repair.plan_repair(layout.bundle_dir)

    assert isinstance(plan, application_repair.RepairRefusal)
    assert "pending marker" in plan.message
    assert "no override" in plan.message


def test_plan_repair_okf_scan_refuses_whole_run_on_any_refused_document(
    tmp_path: Path,
) -> None:
    """Task 6.5: a document `migrate_document` refuses (a non-scalar
    `timestamp` with no `generated`) makes `plan_repair` return a
    `RepairRefusal` naming that concept's id and the refusal reason, for
    the whole bundle -- no partial plan, no override."""
    layout = _workspace(tmp_path)
    _write_concept(
        layout.bundle_dir,
        "concepts/broken",
        {"type": "Concept", "title": "Broken", "timestamp": {"nested": "mapping"}},
    )

    plan = application_repair.plan_repair(layout.bundle_dir)

    assert isinstance(plan, application_repair.RepairRefusal)
    assert "concepts/broken" in plan.message
    assert "timestamp is not a scalar" in plan.message
    assert "no override" in plan.message


def test_plan_repair_detects_bundle_version_flip_needed(tmp_path: Path) -> None:
    """Task 6.6: a bundle whose `index.md` declares an `okf_version` other
    than `"0.2"` is correctly reflected in the plan's flip-needed flag; a
    bundle with no `index.md` at all plans no flip (OKF §11 tolerance)."""
    layout = _workspace(tmp_path)
    index_path = layout.bundle_dir / "index.md"
    metadata, body = okf.load_frontmatter(index_path.read_text(encoding="utf-8"))
    metadata["okf_version"] = "0.1"
    index_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")

    plan = application_repair.plan_repair(layout.bundle_dir)

    assert isinstance(plan, application_repair.RepairPlan)
    assert plan.index_new_text is not None
    new_metadata, _ = okf.load_frontmatter(plan.index_new_text)
    assert new_metadata["okf_version"] == okf.OKF_VERSION
    assert plan.has_work

    index_path.unlink()
    plan_no_index = application_repair.plan_repair(layout.bundle_dir)
    assert isinstance(plan_no_index, application_repair.RepairPlan)
    assert plan_no_index.index_new_text is None


def test_plan_repair_nothing_to_migrate_when_bundle_is_fully_v2(
    tmp_path: Path,
) -> None:
    """Task 6.7: a bundle where every document is already v0.2-shaped, no
    ledger extraction needed, and `okf_version` already `"0.2"` yields a
    `RepairPlan` with nothing to do (`has_work` is `False`) -- not a hard
    error, and `apply_repair` is never called on it."""
    layout = _workspace(tmp_path)
    _write_concept(
        layout.bundle_dir,
        "concepts/already-v2",
        {
            "type": "Concept",
            "title": "Already v2",
            "generated": {"by": "openkos/0.3.0", "at": "2026-07-14T09:00:00Z"},
            "status": "stable",
        },
    )

    plan = application_repair.plan_repair(layout.bundle_dir)

    assert isinstance(plan, application_repair.RepairPlan)
    assert not plan.has_work
    assert plan.extraction == []
    assert plan.document_rewrites == []
    assert plan.sidecar_rewrites == []
    assert plan.index_new_text is None


def _write_legacy_survivor(
    bundle_dir: Path, concept_id: str, *, entries: list[okf.MergeLedgerEntry]
) -> Path:
    """A survivor whose ledger is still embedded in its OWN frontmatter --
    the pre-relocation shape the extraction step migrates (mirrors
    `tests/unit/cli/test_repair.py`'s own fixture)."""
    path = bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Survivor",
                "merged_from": okf.encode_merged_from(entries),
            },
            "Survivor body.\n",
        ),
        encoding="utf-8",
    )
    return path


def _make_entry(absorbed_id: str = "concepts/absorbed") -> okf.MergeLedgerEntry:
    concept_text = okf.dump_frontmatter({"type": "Concept", "title": "X"}, "Body.\n")
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V5,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id=absorbed_id,
        absorbed_snapshot=concept_text,
        survivor_before=concept_text,
        index_before="",
        log_before="",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )


def _write_v01_sidecar(bundle_dir: Path, survivor_id: str) -> Path:
    """An already-relocated sidecar whose one entry's whole-document
    snapshots are v0.1-shaped (`timestamp` + `status: active`) -- so
    `migrate_sidecars_to_okf_v02` finds real work to do, without needing a
    real merge."""
    v01_concept_text = okf.dump_frontmatter(
        {
            "type": "Concept",
            "title": "X",
            "timestamp": "2026-07-14T09:00:00Z",
            "status": "active",
        },
        "Body.\n",
    )
    entry = okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V5,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id=f"{survivor_id}-absorbed",
        absorbed_snapshot=v01_concept_text,
        survivor_before=v01_concept_text,
        index_before="",
        log_before="",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )
    return bundle_ledger.write_entries(
        survivor_id, bundle_dir, survivor_id=survivor_id, entries=[entry]
    )


def test_apply_repair_writes_in_order_sidecars_then_documents_then_index_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 6.10: writes land in the exact order design.md Decision 9
    requires -- ledger extraction -> sidecar OKF migrations -> concept
    documents -> `index.md` flip LAST -- confirmed by a spy over
    `fsio.write_atomic` (this project's established monkeypatch pattern,
    `tests/unit/bundle/test_ledger_crash_injection.py`)."""
    layout = _workspace(tmp_path)
    index_path = layout.bundle_dir / "index.md"
    index_metadata, index_body = okf.load_frontmatter(
        index_path.read_text(encoding="utf-8")
    )
    index_metadata["okf_version"] = "0.1"
    index_path.write_text(
        okf.dump_frontmatter(index_metadata, index_body), encoding="utf-8"
    )

    legacy_survivor_path = _write_legacy_survivor(
        layout.bundle_dir, "concepts/legacy-survivor", entries=[_make_entry()]
    )
    legacy_sidecar_path = bundle_ledger.ledger_path_for(
        "concepts/legacy-survivor", layout.bundle_dir
    )
    already_relocated_sidecar = _write_v01_sidecar(
        layout.bundle_dir, "concepts/relocated-survivor"
    )
    doc_path = _write_concept(
        layout.bundle_dir,
        "concepts/needs-migration",
        {
            "type": "Concept",
            "title": "Needs migration",
            "timestamp": "2026-07-14T09:00:00Z",
            "status": "active",
        },
    )

    plan = application_repair.plan_repair(layout.bundle_dir)
    assert isinstance(plan, application_repair.RepairPlan)
    assert plan.sidecar_rewrites

    written: list[Path] = []
    real_write_atomic = fsio.write_atomic

    def _spy(path: Path, content: str) -> None:
        written.append(path)
        real_write_atomic(path, content)

    monkeypatch.setattr("openkos.fsio.write_atomic", _spy)
    application_repair.apply_repair(tmp_path, plan)

    extraction_index = max(
        written.index(legacy_sidecar_path), written.index(legacy_survivor_path)
    )
    sidecar_index = written.index(already_relocated_sidecar)
    document_index = written.index(doc_path)
    index_index = written.index(index_path)

    assert extraction_index < sidecar_index < document_index < index_index


def test_apply_repair_crash_before_index_flip_leaves_okf_version_0_1_and_reruns_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 6.11: a failure injected immediately before the `index.md`
    write leaves the bundle with `okf_version: "0.1"` over a PARTIALLY
    migrated document -- never a bundle claiming v0.2 while holding v0.1
    content -- and a real re-run then completes cleanly (idempotent
    per-artifact detection)."""
    layout = _workspace(tmp_path)
    index_path = layout.bundle_dir / "index.md"
    index_metadata, index_body = okf.load_frontmatter(
        index_path.read_text(encoding="utf-8")
    )
    index_metadata["okf_version"] = "0.1"
    index_path.write_text(
        okf.dump_frontmatter(index_metadata, index_body), encoding="utf-8"
    )
    doc_path = _write_concept(
        layout.bundle_dir,
        "concepts/needs-migration",
        {
            "type": "Concept",
            "title": "Needs migration",
            "timestamp": "2026-07-14T09:00:00Z",
            "status": "active",
        },
    )

    plan = application_repair.plan_repair(layout.bundle_dir)
    assert isinstance(plan, application_repair.RepairPlan)
    assert plan.index_new_text is not None

    real_write_atomic = fsio.write_atomic

    def _crash_before_index(path: Path, content: str) -> None:
        if path == index_path:
            raise RuntimeError("simulated crash before the index.md flip")
        real_write_atomic(path, content)

    monkeypatch.setattr("openkos.fsio.write_atomic", _crash_before_index)
    with pytest.raises(
        RuntimeError, match=r"simulated crash before the index\.md flip"
    ):
        application_repair.apply_repair(tmp_path, plan)

    partial_index_metadata, _ = okf.load_frontmatter(
        index_path.read_text(encoding="utf-8")
    )
    assert partial_index_metadata["okf_version"] == "0.1"
    partial_doc_metadata, _ = okf.load_frontmatter(doc_path.read_text(encoding="utf-8"))
    assert "generated" in partial_doc_metadata
    assert partial_doc_metadata["status"] == "stable"

    monkeypatch.setattr("openkos.fsio.write_atomic", real_write_atomic)
    rerun_plan = application_repair.plan_repair(layout.bundle_dir)
    assert isinstance(rerun_plan, application_repair.RepairPlan)
    assert rerun_plan.document_rewrites == []
    assert rerun_plan.index_new_text is not None
    application_repair.apply_repair(tmp_path, rerun_plan)

    final_metadata, _ = okf.load_frontmatter(index_path.read_text(encoding="utf-8"))
    assert final_metadata["okf_version"] == okf.OKF_VERSION
