"""Unit tests for the merge-ledger OKF v0.2 migration
(`bundle/ledger.migrate_sidecars_to_okf_v02`, okf-v02-migration Phase 5,
design.md Decision 1): whole-document snapshot migration (recursive over
any embedded, pre-relocation `merged_from`), the V1-V4 `index_before`
`okf_version` flip, and the cross-sidecar link-offset shift.

Every fixture is built with the REAL merge core (`application.lifecycle.
prepare_merge`/`merge_core` -- exactly the two functions `openkos merge`
itself calls), never a hand-faked ledger. Reaching an older schema version
is done by `dataclasses.replace`-downgrading a REAL entry a real merge
produced (mirrors `test_unmerge.py::
test_unmerge_snapshot_entry_still_warns_on_interleaved_drift`), so every
snapshot byte stays genuinely real -- only the `schema` tag and the
fields a given schema does or does not carry are adjusted, per
`MergeLedgerEntry`'s own schema-shape docstrings in `model/okf.py`.
"""

from __future__ import annotations

import dataclasses
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openkos import config
from openkos.application import lifecycle as lifecycle_service
from openkos.bundle import bundle
from openkos.bundle import ledger as bundle_ledger
from openkos.model import okf


def _workspace(root: Path) -> config.WorkspaceLayout:
    config.write_config(root)
    layout = config.WorkspaceLayout(root)
    bundle.create(layout.bundle_dir, datetime(2026, 1, 1, tzinfo=UTC).date())
    return layout


def _write_v01_concept(
    bundle_dir: Path,
    concept_id: str,
    *,
    title: str,
    body: str = "Body.",
    extra: dict[str, object] | None = None,
) -> Path:
    """A v0.1-shaped concept (`timestamp` + `status: active`, no
    `generated`/`sources`) -- so every snapshot the merges below record is
    genuinely v0.1-shaped input for `okf.migrate_document`."""
    concept_path = bundle_dir / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": title,
        "timestamp": "2026-07-14T09:00:00Z",
        "status": "active",
    }
    if extra is not None:
        metadata.update(extra)
    concept_path.write_text(
        okf.dump_frontmatter(metadata, f"# {title}\n\n{body}\n"), encoding="utf-8"
    )
    return concept_path


def _real_merge(
    layout: config.WorkspaceLayout,
    survivor_id: str,
    absorbed_id: str,
    *,
    now: datetime,
) -> lifecycle_service.MergeResult:
    """Run the REAL merge core (`prepare_merge` + `merge_core`) -- exactly
    the two functions `openkos merge` itself calls -- so every snapshot the
    ledger records is byte-real, never hand-authored."""
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    survivor_path, survivor_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, survivor_id
    )
    absorbed_path, absorbed_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, absorbed_id
    )
    prepared = lifecycle_service.prepare_merge(
        layout.bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        layout.root,
        now=now,
    )
    return lifecycle_service.merge_core(
        layout.bundle_dir, index_path, log_path, prepared
    )


def _current_texts(bundle_dir: Path) -> dict[str, str]:
    """Every non-reserved bundle markdown file's CURRENT full text, keyed
    the same way `okf.LinkRewrite.file` is (bundle-relative path, `.md`
    suffix included) -- mirrors `prepare_merge`'s own `other_files`
    construction (`application/lifecycle.py`)."""
    texts: dict[str, str] = {}
    for path in okf.iter_bundle_markdown(bundle_dir):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        rel = path.relative_to(bundle_dir).as_posix()
        texts[rel] = path.read_text(encoding="utf-8")
    return texts


def _body_start(text: str) -> int:
    """Test-local, independent re-derivation of a text's body-start offset
    (never imports `bundle_ledger`'s own private helper) -- the length of
    its verbatim frontmatter block."""
    block, _ = okf.split_frontmatter_verbatim(text, label="test")
    return len(block)


def _v01_index_snapshot(layout: config.WorkspaceLayout) -> str:
    """A v0.1-shaped whole-`index.md` snapshot: the REAL index body
    `bundle.create` wrote, with `okf_version` downgraded to `"0.1"` --
    exactly what a real v0.1 bundle's `index.md` looked like before OKF
    v0.2 shipped, used as a V1-V4 `index_before` fixture."""
    index_path = layout.bundle_dir / "index.md"
    metadata, body = okf.load_frontmatter(index_path.read_text(encoding="utf-8"))
    downgraded = dict(metadata)
    downgraded["okf_version"] = "0.1"
    return okf.dump_frontmatter(downgraded, body)


def _expect_migrated(text: str) -> str:
    """Assert `okf.migrate_document(text)` actually rewrote something and
    return its bytes -- the shared "what SHOULD this snapshot look like"
    oracle every test below compares `migrate_sidecars_to_okf_v02`'s
    output against."""
    result = okf.migrate_document(text)
    assert isinstance(result, okf.Migrated)
    return result.text


# --- 5.1: every whole-document snapshot field ------------------------------


def test_migrate_sidecars_migrates_every_whole_document_snapshot(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    _write_v01_concept(
        layout.bundle_dir,
        "concepts/relator",
        title="Relator",
        extra={"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
    )
    _write_v01_concept(
        layout.bundle_dir,
        "concepts/prov",
        title="Provenance holder",
        extra={"provenance": ["concepts/absorbed"]},
    )
    original_absorbed_text = (layout.bundle_dir / "concepts" / "absorbed.md").read_text(
        encoding="utf-8"
    )
    original_survivor_text = (layout.bundle_dir / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    original_relator_text = (layout.bundle_dir / "concepts" / "relator.md").read_text(
        encoding="utf-8"
    )
    original_prov_text = (layout.bundle_dir / "concepts" / "prov.md").read_text(
        encoding="utf-8"
    )

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )

    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.absorbed_snapshot == original_absorbed_text
    assert entry.survivor_before == original_survivor_text
    assert len(entry.relation_rewrites) == 1
    assert entry.relation_rewrites[0].snapshot == original_relator_text
    assert len(entry.provenance_rewrites) == 1
    assert entry.provenance_rewrites[0].snapshot == original_prov_text

    changed = bundle_ledger.migrate_sidecars_to_okf_v02(
        layout.bundle_dir, current_texts=_current_texts(layout.bundle_dir)
    )

    assert len(changed) == 1
    _, survivor_id, migrated_entries = changed[0]
    assert survivor_id == "concepts/survivor"
    migrated_entry = migrated_entries[0]

    assert migrated_entry.absorbed_snapshot == _expect_migrated(original_absorbed_text)
    assert migrated_entry.survivor_before == _expect_migrated(original_survivor_text)
    assert migrated_entry.relation_rewrites[0].snapshot == _expect_migrated(
        original_relator_text
    )
    assert migrated_entry.provenance_rewrites[0].snapshot == _expect_migrated(
        original_prov_text
    )


# --- 5.2: recursion into an embedded, pre-relocation `merged_from` --------


def test_migrate_sidecars_recurses_into_embedded_merged_from_snapshots(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _write_v01_concept(layout.bundle_dir, "concepts/x", title="X")
    _write_v01_concept(layout.bundle_dir, "concepts/y", title="Y")
    _write_v01_concept(layout.bundle_dir, "concepts/z", title="Z")
    original_x_text = (layout.bundle_dir / "concepts" / "x.md").read_text(
        encoding="utf-8"
    )
    original_y_text = (layout.bundle_dir / "concepts" / "y.md").read_text(
        encoding="utf-8"
    )

    _real_merge(
        layout, "concepts/y", "concepts/x", now=datetime(2026, 7, 20, tzinfo=UTC)
    )
    inner_entries = bundle_ledger.read_entries("concepts/y", layout.bundle_dir)
    assert len(inner_entries) == 1
    assert inner_entries[0].absorbed_snapshot == original_x_text
    assert inner_entries[0].survivor_before == original_y_text

    _real_merge(
        layout, "concepts/z", "concepts/y", now=datetime(2026, 7, 21, tzinfo=UTC)
    )
    outer_entries = bundle_ledger.read_entries("concepts/z", layout.bundle_dir)
    assert len(outer_entries) == 1
    outer_entry = outer_entries[0]

    # Simulate the migration-era (pre-#550) shape: y's real ledger entry
    # embedded directly in z's `survivor_before` frontmatter, instead of
    # living in its own sidecar.
    metadata, body = okf.load_frontmatter(outer_entry.survivor_before)
    embedded_metadata = dict(metadata)
    embedded_metadata["merged_from"] = okf.encode_merged_from(inner_entries)
    embedded_survivor_before = okf.dump_frontmatter(embedded_metadata, body)
    entry_with_embedded_history = dataclasses.replace(
        outer_entry, survivor_before=embedded_survivor_before
    )
    bundle_ledger.write_entries(
        "concepts/z",
        layout.bundle_dir,
        survivor_id="concepts/z",
        entries=[entry_with_embedded_history],
    )

    changed = bundle_ledger.migrate_sidecars_to_okf_v02(
        layout.bundle_dir, current_texts=_current_texts(layout.bundle_dir)
    )

    # Both y's own sidecar (still v0.1-shaped) AND z's sidecar (whose
    # survivor_before embeds y's history) need migration -- select z's.
    assert {survivor_id for _, survivor_id, _ in changed} == {
        "concepts/y",
        "concepts/z",
    }
    _, _, migrated_entries = next(c for c in changed if c[1] == "concepts/z")
    migrated_outer = migrated_entries[0]
    migrated_metadata, _ = okf.load_frontmatter(migrated_outer.survivor_before)
    migrated_embedded = okf.decode_merged_from(migrated_metadata)
    assert len(migrated_embedded) == 1
    assert migrated_embedded[0].absorbed_snapshot == _expect_migrated(original_x_text)
    assert migrated_embedded[0].survivor_before == _expect_migrated(original_y_text)


# --- 5.4/5.5: V1-V4 `index_before` `okf_version` flip -----------------------


@pytest.mark.parametrize(
    "schema",
    [
        okf.MERGE_LEDGER_SCHEMA_V1,
        okf.MERGE_LEDGER_SCHEMA_V2,
        okf.MERGE_LEDGER_SCHEMA_V3,
        okf.MERGE_LEDGER_SCHEMA_V4,
    ],
)
def test_migrate_sidecars_flips_index_before_okf_version_v1_through_v4(
    tmp_path: Path, schema: str
) -> None:
    layout = _workspace(tmp_path)
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    v01_index_snapshot = _v01_index_snapshot(layout)

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    assert len(entries) == 1
    real_entry = entries[0]
    assert real_entry.relation_rewrites == []
    assert real_entry.provenance_rewrites == []
    assert real_entry.carried_content_ids == []

    downgraded = dataclasses.replace(
        real_entry,
        schema=schema,
        index_before=v01_index_snapshot,
        log_before="log text",
        index_restores=[],
    )
    bundle_ledger.write_entries(
        "concepts/survivor",
        layout.bundle_dir,
        survivor_id="concepts/survivor",
        entries=[downgraded],
    )

    changed = bundle_ledger.migrate_sidecars_to_okf_v02(
        layout.bundle_dir, current_texts=_current_texts(layout.bundle_dir)
    )

    assert len(changed) == 1
    _, _, migrated_entries = changed[0]
    migrated_entry = migrated_entries[0]

    migrated_metadata, migrated_body = okf.load_frontmatter(migrated_entry.index_before)
    assert migrated_metadata["okf_version"] == "0.2"
    _, original_body = okf.load_frontmatter(v01_index_snapshot)
    assert migrated_body == original_body
    assert migrated_entry.log_before == "log text"


def test_migrate_sidecars_skips_a_v5_entry_with_no_index_before(
    tmp_path: Path,
) -> None:
    """A V5 entry (`index_before`/`log_before` decode to `""` -- see
    `MERGE_LEDGER_SCHEMA_V5`'s replacement mechanism) is skipped by the
    flip rule entirely: nothing to flip, and its (empty) `index_before`
    stays empty after migration."""
    layout = _workspace(tmp_path)
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    assert entries[0].schema == okf.MERGE_LEDGER_SCHEMA_V5
    assert entries[0].index_before == ""
    assert entries[0].log_before == ""

    changed = bundle_ledger.migrate_sidecars_to_okf_v02(
        layout.bundle_dir, current_texts=_current_texts(layout.bundle_dir)
    )

    assert len(changed) == 1
    _, _, migrated_entries = changed[0]
    assert migrated_entries[0].index_before == ""
    assert migrated_entries[0].log_before == ""


# --- 5.6: Check B still passes after migration ------------------------------


def test_migrate_sidecars_check_b_still_passes_after_migration(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _write_v01_concept(layout.bundle_dir, "concepts/z", title="Z")
    _write_v01_concept(layout.bundle_dir, "concepts/a1", title="A1")
    _write_v01_concept(layout.bundle_dir, "concepts/a2", title="A2")

    _real_merge(
        layout, "concepts/z", "concepts/a1", now=datetime(2026, 7, 20, tzinfo=UTC)
    )
    _real_merge(
        layout, "concepts/z", "concepts/a2", now=datetime(2026, 7, 21, tzinfo=UTC)
    )
    entries = bundle_ledger.read_entries("concepts/z", layout.bundle_dir)
    assert len(entries) == 2

    # entries[1]'s survivor_before is simulated as a migration-era snapshot
    # embedding entries[0] directly -- built from entries[0] as it REALLY
    # is, so the nested-prefix equality genuinely holds pre-migration.
    metadata, body = okf.load_frontmatter(entries[1].survivor_before)
    embedded_metadata = dict(metadata)
    embedded_metadata["merged_from"] = okf.encode_merged_from(entries[:1])
    embedded_survivor_before = okf.dump_frontmatter(embedded_metadata, body)
    entries_with_embedded_history = [
        entries[0],
        dataclasses.replace(entries[1], survivor_before=embedded_survivor_before),
    ]
    bundle_ledger.write_entries(
        "concepts/z",
        layout.bundle_dir,
        survivor_id="concepts/z",
        entries=entries_with_embedded_history,
    )

    assert bundle_ledger.scan_nesting_violations(layout.bundle_dir) == []

    changed = bundle_ledger.migrate_sidecars_to_okf_v02(
        layout.bundle_dir, current_texts=_current_texts(layout.bundle_dir)
    )
    assert len(changed) == 1
    _, survivor_id, migrated_entries = changed[0]
    bundle_ledger.write_entries(
        survivor_id,
        layout.bundle_dir,
        survivor_id=survivor_id,
        entries=migrated_entries,
    )

    assert bundle_ledger.scan_nesting_violations(layout.bundle_dir) == []


# --- 5.7/5.8: link-offset shift ---------------------------------------------


def test_migrate_sidecars_shifts_link_rewrite_offsets_from_current_text(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    _write_v01_concept(
        layout.bundle_dir,
        "concepts/other",
        title="Other",
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    assert len(entries) == 1
    assert len(entries[0].link_rewrites) == 1
    original_offset = entries[0].link_rewrites[0].offset

    current_texts = _current_texts(layout.bundle_dir)
    other_current_text = current_texts["concepts/other.md"]

    changed = bundle_ledger.migrate_sidecars_to_okf_v02(
        layout.bundle_dir, current_texts=current_texts
    )
    assert len(changed) == 1
    _, _, migrated_entries = changed[0]
    migrated_rewrite = migrated_entries[0].link_rewrites[0]

    migrated_other_text = _expect_migrated(other_current_text)
    expected_shift = _body_start(migrated_other_text) - _body_start(other_current_text)
    assert expected_shift != 0
    assert migrated_rewrite.offset == original_offset + expected_shift


def test_migrate_sidecars_shifts_link_rewrite_offsets_from_a_later_snapshot(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    _write_v01_concept(
        layout.bundle_dir,
        "concepts/other",
        title="Other",
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )
    _write_v01_concept(layout.bundle_dir, "concepts/extra", title="Extra")

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    assert len(entries) == 1
    original_offset = entries[0].link_rewrites[0].offset

    # "concepts/other" ITSELF absorbs a third document later -- its own
    # sidecar records a real, still-v0.1-shaped `survivor_before` snapshot
    # of "concepts/other.md" as it stood right after the FIRST merge (link
    # already rewritten, frontmatter otherwise untouched). The merge WRITE
    # itself, per the already-shipped Phase 2 generation rule, emits v0.2
    # shape (`generated`/`status: stable`) for its own merged result -- so
    # by the time this call runs, "concepts/other.md"'s CURRENT text is
    # already v0.2 (a `migrate_document` no-op, shift 0), while the
    # snapshot this later entry recorded is still genuinely v0.1 (a
    # non-zero shift). Using the wrong source is therefore directly
    # observable: a shift of exactly 0 vs. the real, non-zero one.
    _real_merge(
        layout,
        "concepts/other",
        "concepts/extra",
        now=datetime(2026, 7, 21, tzinfo=UTC),
    )
    other_entries = bundle_ledger.read_entries("concepts/other", layout.bundle_dir)
    assert len(other_entries) == 1
    later_snapshot_text = other_entries[0].survivor_before

    current_texts = _current_texts(layout.bundle_dir)
    current_other_text = current_texts["concepts/other.md"]
    assert isinstance(okf.migrate_document(current_other_text), okf.Unchanged)
    assert current_other_text != later_snapshot_text

    changed = bundle_ledger.migrate_sidecars_to_okf_v02(
        layout.bundle_dir, current_texts=current_texts
    )
    survivor_change = next(c for c in changed if c[1] == "concepts/survivor")
    migrated_rewrite = survivor_change[2][0].link_rewrites[0]

    migrated_snapshot_text = _expect_migrated(later_snapshot_text)
    expected_shift_from_snapshot = _body_start(migrated_snapshot_text) - _body_start(
        later_snapshot_text
    )
    assert expected_shift_from_snapshot != 0
    assert migrated_rewrite.offset == original_offset + expected_shift_from_snapshot


def test_migrate_sidecars_leaves_an_offset_below_body_start_unshifted(
    tmp_path: Path,
) -> None:
    """A rewrite whose recorded `offset` sits BEFORE the old body start
    (a defensively-constructed case; a real `LinkRewrite` never points
    inside the frontmatter) is left untouched -- design.md Decision 1:
    "Offsets below the old body start are never shifted."."""
    layout = _workspace(tmp_path)
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    _write_v01_concept(
        layout.bundle_dir,
        "concepts/other",
        title="Other",
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    real_rewrite = entries[0].link_rewrites[0]
    below_body_start_rewrite = dataclasses.replace(real_rewrite, offset=0)
    forced_entry = dataclasses.replace(
        entries[0], link_rewrites=[below_body_start_rewrite]
    )
    bundle_ledger.write_entries(
        "concepts/survivor",
        layout.bundle_dir,
        survivor_id="concepts/survivor",
        entries=[forced_entry],
    )

    changed = bundle_ledger.migrate_sidecars_to_okf_v02(
        layout.bundle_dir, current_texts=_current_texts(layout.bundle_dir)
    )

    assert len(changed) == 1
    _, _, migrated_entries = changed[0]
    assert migrated_entries[0].link_rewrites[0].offset == 0


# --- 5.10-5.14: V1-V5 round-trip commutation (Phase 6, skipped for now) ----


def _copy_workspace(source_root: Path, target_root: Path) -> config.WorkspaceLayout:
    """A second, independent workspace holding the SAME pre-merge bytes as
    `source_root` -- the `repair(B)` reference build for the commutation
    invariant, computed with no merge involved."""
    shutil.copytree(source_root, target_root)
    return config.WorkspaceLayout(target_root)


def _downgrade_to_schema(
    entry: okf.MergeLedgerEntry, schema: str, *, index_before: str
) -> okf.MergeLedgerEntry:
    """Reshape a REAL (V5) merge entry into `schema`'s exact required-field
    shape, per each schema's own docstring in `model/okf.py` -- never a
    hand-faked field, only zeroing/populating the fields a given schema
    does or does not carry (mirrors `test_unmerge.py`'s own downgrade
    fixture technique)."""
    return dataclasses.replace(
        entry,
        schema=schema,
        index_before=index_before,
        log_before="log text",
        index_restores=[],
    )


@pytest.mark.skip(reason="okf-v02-migration Phase 6 not yet landed")
def test_merge_repair_unmerge_round_trip_v1_schema(tmp_path: Path) -> None:
    """`unmerge(repair(merge(B)))[d] == repair(B)[d]` for every concept `d`
    the merge touched (design.md Decision 1's commutation invariant),
    forcing a V1 ledger entry (no `relation_rewrites`/`provenance_rewrites`
    at all). `application/repair.py` (Phase 6) does not exist yet --
    **RED today**: `ModuleNotFoundError` on the deferred import below.
    Collected now, skip-marked; unskip once Phase 6 lands
    (task 6.16 references back to this task)."""
    # Phase 6 module -- does not exist yet, hence the ModuleNotFoundError
    # RED this task names; type: ignore is removed along with the skip
    # mark once Phase 6 lands.
    from openkos.application import (  # type: ignore[attr-defined]
        repair as application_repair,
    )

    layout = _workspace(tmp_path / "merged")
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    reference_layout = _copy_workspace(tmp_path / "merged", tmp_path / "reference")

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    downgraded = _downgrade_to_schema(
        entries[0],
        okf.MERGE_LEDGER_SCHEMA_V1,
        index_before=_v01_index_snapshot(layout),
    )
    bundle_ledger.write_entries(
        "concepts/survivor",
        layout.bundle_dir,
        survivor_id="concepts/survivor",
        entries=[downgraded],
    )

    reference_plan = application_repair.plan_repair(reference_layout.bundle_dir)
    assert isinstance(reference_plan, application_repair.RepairPlan)
    application_repair.apply_repair(reference_layout.root, reference_plan)

    plan = application_repair.plan_repair(layout.bundle_dir)
    assert isinstance(plan, application_repair.RepairPlan)
    application_repair.apply_repair(layout.root, plan)

    survivor_path, survivor_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/survivor"
    )
    prepared_unmerge = lifecycle_service.prepare_unmerge(
        layout.root,
        layout,
        survivor_path,
        survivor_canonical,
        "concepts/absorbed",
        now=datetime(2026, 7, 22, tzinfo=UTC),
        cfg=config.read_config(layout.root),
    )
    lifecycle_service.unmerge_core(layout, prepared_unmerge)

    assert survivor_path.read_text(encoding="utf-8") == (
        reference_layout.bundle_dir / "concepts" / "survivor.md"
    ).read_text(encoding="utf-8")
    for concept_id in ("concepts/absorbed",):
        assert (layout.bundle_dir / f"{concept_id}.md").read_text(encoding="utf-8") == (
            reference_layout.bundle_dir / f"{concept_id}.md"
        ).read_text(encoding="utf-8")


@pytest.mark.skip(reason="okf-v02-migration Phase 6 not yet landed")
def test_merge_repair_unmerge_round_trip_v2_schema(tmp_path: Path) -> None:
    """Same shape as the V1 case, forcing a V2 ledger entry
    (`relation_rewrites` populated, `provenance_rewrites` empty). Same
    skip-until-Phase-6 treatment; **RED today**: `ModuleNotFoundError`."""
    # Phase 6 module -- does not exist yet, hence the ModuleNotFoundError
    # RED this task names; type: ignore is removed along with the skip
    # mark once Phase 6 lands.
    from openkos.application import (  # type: ignore[attr-defined]
        repair as application_repair,
    )

    layout = _workspace(tmp_path / "merged")
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    _write_v01_concept(
        layout.bundle_dir,
        "concepts/relator",
        title="Relator",
        extra={"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
    )
    reference_layout = _copy_workspace(tmp_path / "merged", tmp_path / "reference")

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    assert entries[0].relation_rewrites
    downgraded = dataclasses.replace(
        _downgrade_to_schema(
            entries[0],
            okf.MERGE_LEDGER_SCHEMA_V2,
            index_before=_v01_index_snapshot(layout),
        ),
        provenance_rewrites=[],
    )
    bundle_ledger.write_entries(
        "concepts/survivor",
        layout.bundle_dir,
        survivor_id="concepts/survivor",
        entries=[downgraded],
    )

    reference_plan = application_repair.plan_repair(reference_layout.bundle_dir)
    assert isinstance(reference_plan, application_repair.RepairPlan)
    application_repair.apply_repair(reference_layout.root, reference_plan)

    plan = application_repair.plan_repair(layout.bundle_dir)
    assert isinstance(plan, application_repair.RepairPlan)
    application_repair.apply_repair(layout.root, plan)

    survivor_path, survivor_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/survivor"
    )
    prepared_unmerge = lifecycle_service.prepare_unmerge(
        layout.root,
        layout,
        survivor_path,
        survivor_canonical,
        "concepts/absorbed",
        now=datetime(2026, 7, 22, tzinfo=UTC),
        cfg=config.read_config(layout.root),
    )
    lifecycle_service.unmerge_core(layout, prepared_unmerge)

    assert survivor_path.read_text(encoding="utf-8") == (
        reference_layout.bundle_dir / "concepts" / "survivor.md"
    ).read_text(encoding="utf-8")


@pytest.mark.skip(reason="okf-v02-migration Phase 6 not yet landed")
def test_merge_repair_unmerge_round_trip_v3_schema(tmp_path: Path) -> None:
    """Same shape, forcing V3 (`provenance_rewrites` populated too). Same
    skip-until-Phase-6 treatment; **RED today**: `ModuleNotFoundError`."""
    # Phase 6 module -- does not exist yet, hence the ModuleNotFoundError
    # RED this task names; type: ignore is removed along with the skip
    # mark once Phase 6 lands.
    from openkos.application import (  # type: ignore[attr-defined]
        repair as application_repair,
    )

    layout = _workspace(tmp_path / "merged")
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    _write_v01_concept(
        layout.bundle_dir,
        "concepts/relator",
        title="Relator",
        extra={"relations": [{"target": "concepts/absorbed", "type": "depends_on"}]},
    )
    _write_v01_concept(
        layout.bundle_dir,
        "concepts/prov",
        title="Provenance holder",
        extra={"provenance": ["concepts/absorbed"]},
    )
    reference_layout = _copy_workspace(tmp_path / "merged", tmp_path / "reference")

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    assert entries[0].relation_rewrites
    assert entries[0].provenance_rewrites
    downgraded = _downgrade_to_schema(
        entries[0],
        okf.MERGE_LEDGER_SCHEMA_V3,
        index_before=_v01_index_snapshot(layout),
    )
    bundle_ledger.write_entries(
        "concepts/survivor",
        layout.bundle_dir,
        survivor_id="concepts/survivor",
        entries=[downgraded],
    )

    reference_plan = application_repair.plan_repair(reference_layout.bundle_dir)
    assert isinstance(reference_plan, application_repair.RepairPlan)
    application_repair.apply_repair(reference_layout.root, reference_plan)

    plan = application_repair.plan_repair(layout.bundle_dir)
    assert isinstance(plan, application_repair.RepairPlan)
    application_repair.apply_repair(layout.root, plan)

    survivor_path, survivor_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/survivor"
    )
    prepared_unmerge = lifecycle_service.prepare_unmerge(
        layout.root,
        layout,
        survivor_path,
        survivor_canonical,
        "concepts/absorbed",
        now=datetime(2026, 7, 22, tzinfo=UTC),
        cfg=config.read_config(layout.root),
    )
    lifecycle_service.unmerge_core(layout, prepared_unmerge)

    assert survivor_path.read_text(encoding="utf-8") == (
        reference_layout.bundle_dir / "concepts" / "survivor.md"
    ).read_text(encoding="utf-8")


@pytest.mark.skip(reason="okf-v02-migration Phase 6 not yet landed")
def test_merge_repair_unmerge_round_trip_v4_schema(tmp_path: Path) -> None:
    """Forcing V4 (`carried_content_ids` populated via a SECOND merge onto
    the same survivor -- the shape only a real double-absorption produces).
    Same skip-until-Phase-6 treatment; **RED today**:
    `ModuleNotFoundError`."""
    # Phase 6 module -- does not exist yet, hence the ModuleNotFoundError
    # RED this task names; type: ignore is removed along with the skip
    # mark once Phase 6 lands.
    from openkos.application import (  # type: ignore[attr-defined]
        repair as application_repair,
    )

    layout = _workspace(tmp_path / "merged")
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    _write_v01_concept(layout.bundle_dir, "concepts/second", title="Second")
    reference_layout = _copy_workspace(tmp_path / "merged", tmp_path / "reference")

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/second",
        now=datetime(2026, 7, 21, tzinfo=UTC),
    )
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    assert len(entries) == 2
    downgraded = [
        _downgrade_to_schema(
            entries[0],
            okf.MERGE_LEDGER_SCHEMA_V4,
            index_before=_v01_index_snapshot(layout),
        ),
        _downgrade_to_schema(
            entries[1],
            okf.MERGE_LEDGER_SCHEMA_V4,
            index_before=_v01_index_snapshot(layout),
        ),
    ]
    bundle_ledger.write_entries(
        "concepts/survivor",
        layout.bundle_dir,
        survivor_id="concepts/survivor",
        entries=downgraded,
    )

    reference_plan = application_repair.plan_repair(reference_layout.bundle_dir)
    assert isinstance(reference_plan, application_repair.RepairPlan)
    application_repair.apply_repair(reference_layout.root, reference_plan)

    plan = application_repair.plan_repair(layout.bundle_dir)
    assert isinstance(plan, application_repair.RepairPlan)
    application_repair.apply_repair(layout.root, plan)

    survivor_path, survivor_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/survivor"
    )
    cfg = config.read_config(layout.root)
    prepared_second_unmerge = lifecycle_service.prepare_unmerge(
        layout.root,
        layout,
        survivor_path,
        survivor_canonical,
        "concepts/second",
        now=datetime(2026, 7, 22, tzinfo=UTC),
        cfg=cfg,
    )
    lifecycle_service.unmerge_core(layout, prepared_second_unmerge)
    prepared_first_unmerge = lifecycle_service.prepare_unmerge(
        layout.root,
        layout,
        survivor_path,
        survivor_canonical,
        "concepts/absorbed",
        now=datetime(2026, 7, 23, tzinfo=UTC),
        cfg=cfg,
    )
    lifecycle_service.unmerge_core(layout, prepared_first_unmerge)

    assert survivor_path.read_text(encoding="utf-8") == (
        reference_layout.bundle_dir / "concepts" / "survivor.md"
    ).read_text(encoding="utf-8")


@pytest.mark.skip(reason="okf-v02-migration Phase 6 not yet landed")
def test_merge_repair_unmerge_round_trip_v5_schema(tmp_path: Path) -> None:
    """Using an UNFORCED (current-default) real merge, which per
    `MERGE_LEDGER_SCHEMA_V5` writes `index_restores` instead of
    `index_before`/`log_before` -- asserts the same invariant AND that no
    v0.1-shaped document remains anywhere in the bundle after `unmerge`
    (okf-format-migration: "Merge, Repair, And Unmerge Leave No V0.1-Shaped
    Document"). Same skip-until-Phase-6 treatment; **RED today**:
    `ModuleNotFoundError`."""
    # Phase 6 module -- does not exist yet, hence the ModuleNotFoundError
    # RED this task names; type: ignore is removed along with the skip
    # mark once Phase 6 lands.
    from openkos.application import (  # type: ignore[attr-defined]
        repair as application_repair,
    )

    layout = _workspace(tmp_path / "merged")
    _write_v01_concept(layout.bundle_dir, "concepts/survivor", title="Survivor")
    _write_v01_concept(layout.bundle_dir, "concepts/absorbed", title="Absorbed")
    reference_layout = _copy_workspace(tmp_path / "merged", tmp_path / "reference")

    _real_merge(
        layout,
        "concepts/survivor",
        "concepts/absorbed",
        now=datetime(2026, 7, 20, tzinfo=UTC),
    )
    entries = bundle_ledger.read_entries("concepts/survivor", layout.bundle_dir)
    assert entries[0].schema == okf.MERGE_LEDGER_SCHEMA_V5

    reference_plan = application_repair.plan_repair(reference_layout.bundle_dir)
    assert isinstance(reference_plan, application_repair.RepairPlan)
    application_repair.apply_repair(reference_layout.root, reference_plan)

    plan = application_repair.plan_repair(layout.bundle_dir)
    assert isinstance(plan, application_repair.RepairPlan)
    application_repair.apply_repair(layout.root, plan)

    survivor_path, survivor_canonical = lifecycle_service.resolve_concept_path(
        layout.bundle_dir, "concepts/survivor"
    )
    prepared_unmerge = lifecycle_service.prepare_unmerge(
        layout.root,
        layout,
        survivor_path,
        survivor_canonical,
        "concepts/absorbed",
        now=datetime(2026, 7, 22, tzinfo=UTC),
        cfg=config.read_config(layout.root),
    )
    lifecycle_service.unmerge_core(layout, prepared_unmerge)

    assert survivor_path.read_text(encoding="utf-8") == (
        reference_layout.bundle_dir / "concepts" / "survivor.md"
    ).read_text(encoding="utf-8")
    for path in okf.iter_bundle_markdown(layout.bundle_dir):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
        assert metadata.get("okf_version") != "0.1"
