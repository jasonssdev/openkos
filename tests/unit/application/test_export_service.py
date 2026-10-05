"""The export service (okf-export, #1301): plan, stage, self-check, publish."""

import dataclasses
from datetime import date
from pathlib import Path

import pytest

from openkos.application import export_service
from openkos.model import okf
from openkos.sensitivity import ExportReason

_TODAY = date(2026, 10, 5)

_INDEX = (
    "---\nokf_version: '0.2'\n---\n\n"
    "# Concepts\n\n"
    "* [Public](/concepts/public.md) - A public concept.\n"
    "* [Private](/concepts/private.md) - A private concept.\n"
    "* [Canary ZQ](/concepts/canary-zq.md) - CANARY-MARKER-ZQ.\n"
)


def _write(bundle: Path, rel: str, metadata: dict[str, object], body: str) -> None:
    path = bundle / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")


def _concept(title: str, sensitivity: str, **extra: object) -> dict[str, object]:
    meta: dict[str, object] = {
        "type": "Concept",
        "title": title,
        "sensitivity": sensitivity,
        "status": "stable",
    }
    meta.update(extra)
    return meta


def _bundle(tmp_path: Path) -> Path:
    """A bundle with a public, a private and a confidential canary concept;
    the public one points at the canary through every channel there is."""
    bundle = tmp_path / "ws" / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "index.md").write_text(_INDEX, encoding="utf-8")
    (bundle / "log.md").write_text(
        "# Directory Update Log\n\n## 2026-10-01\n\n"
        "* **Creation**: Compiled [Canary ZQ](/concepts/canary-zq.md) from "
        "`raw/canary.txt`.\n",
        encoding="utf-8",
    )
    _write(
        bundle,
        "concepts/canary-zq.md",
        _concept("Canary ZQ", "confidential", origin_key="0" * 32),
        "# Canary ZQ\n\nCANARY-MARKER-ZQ body.\n",
    )
    _write(
        bundle,
        "concepts/public.md",
        _concept(
            "Public",
            "public",
            origin_key="f" * 32,
            relations=[
                {"target": "concepts/canary-zq", "type": "related_to"},
                {"target": "concepts/private", "type": "related_to"},
            ],
            provenance=["concepts/private", "concepts/canary-zq"],
            sources=[
                {"id": "concepts/private", "resource": "/concepts/private.md"},
                {"id": "concepts/canary-zq", "resource": "/concepts/canary-zq.md"},
            ],
        ),
        "# Public\n\n"
        "- [Canary ZQ](/concepts/canary-zq.md) — related.\n"
        "- [Canary ZQ](./canary-zq.md) — again, relatively.\n"
        "- [Canary ZQ][cz] — by reference.\n"
        "- [Private](/concepts/private.md) — kept when private leaves.\n"
        "\n"
        "[cz]: /concepts/canary-zq.md\n",
    )
    _write(
        bundle,
        "concepts/private.md",
        _concept("Private", "private"),
        "# Private\n\nA private body.\n",
    )
    (bundle / ".state" / "ledger").mkdir(parents=True)
    (bundle / ".state" / "ledger" / "concepts").mkdir()
    (bundle / ".state" / "ledger" / "concepts" / "public.ledger.okf").write_text(
        "---\nentries: CANARY-MARKER-ZQ\n---\n", encoding="utf-8"
    )
    (bundle / "concepts" / "picture.png").write_bytes(b"\x89PNG")
    return bundle


def _plan(bundle: Path, **flags: bool) -> export_service.ExportPlan:
    """`concepts/public` cites the confidential canary in its provenance, so
    it sits below its sources; these tests pass `allow_below_source` by
    default to exercise every pointer channel, and
    `test_below_source_is_withheld_without_the_flag` covers the default."""
    return export_service.plan_export(
        bundle,
        include_private=flags.get("include_private", False),
        allow_below_source=flags.get("allow_below_source", True),
        today=_TODAY,
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# --- plan_export ---------------------------------------------------------------


def test_the_plan_holds_only_allowed_concepts_and_the_reserved_files(
    tmp_path: Path,
) -> None:
    plan = _plan(_bundle(tmp_path))
    assert list(plan.files) == ["concepts/public.md", "index.md", "log.md"]
    assert plan.boundary.withheld == {
        "concepts/canary-zq": ExportReason.CONFIDENTIAL,
        "concepts/private": ExportReason.PRIVATE,
    }
    assert plan.withheld_ids == frozenset({"concepts/canary-zq", "concepts/private"})


def test_below_source_is_withheld_without_the_flag(tmp_path: Path) -> None:
    plan = _plan(_bundle(tmp_path), allow_below_source=False)
    assert plan.boundary.withheld["concepts/public"] is ExportReason.BELOW_SOURCE
    assert plan.boundary.below_source == ("concepts/public",)
    assert list(plan.files) == ["index.md", "log.md"]


def test_engine_state_and_non_markdown_files_are_never_planned(
    tmp_path: Path,
) -> None:
    plan = _plan(_bundle(tmp_path), include_private=True)
    assert not any(rel.startswith(".") for rel in plan.files)
    assert "concepts/picture.png" not in plan.files
    assert plan.skipped == ("concepts/picture.png",)


def test_the_exported_document_carries_no_pointer_into_withheld_objects(
    tmp_path: Path,
) -> None:
    plan = _plan(_bundle(tmp_path))
    text = plan.files["concepts/public.md"]
    meta, body = okf.load_frontmatter(text)
    assert okf.RELATIONS_KEY not in meta
    assert "provenance" not in meta
    assert okf.SOURCES_KEY not in meta
    assert okf.ORIGIN_KEY_KEY not in meta
    assert "canary" not in text.lower()
    assert body.count("[withheld]") == 4


def test_private_pointers_survive_when_private_leaves(tmp_path: Path) -> None:
    plan = _plan(_bundle(tmp_path), include_private=True)
    meta, _ = okf.load_frontmatter(plan.files["concepts/public.md"])
    assert meta[okf.RELATIONS_KEY] == [
        {"target": "concepts/private", "type": "related_to"}
    ]
    assert meta["provenance"] == ["concepts/private"]


def test_the_index_and_log_are_rebuilt(tmp_path: Path) -> None:
    plan = _plan(_bundle(tmp_path))
    assert "Canary" not in plan.files["index.md"]
    assert "Private" not in plan.files["index.md"]
    assert "Public" in plan.files["index.md"]
    assert plan.files["log.md"].count("**Export**") == 1
    assert "raw/" not in plan.files["log.md"]


def test_a_superseded_concept_is_projected_and_reported(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _write(
        bundle,
        "concepts/newer.md",
        _concept(
            "Newer",
            "confidential",
            relations=[{"target": "concepts/public", "type": "supersedes"}],
        ),
        "# Newer\n",
    )
    plan = _plan(bundle)
    meta, _ = okf.load_frontmatter(plan.files["concepts/public.md"])
    assert meta["status"] == "deprecated"
    assert plan.status_projected == ("concepts/public",)
    # The workspace copy is untouched.
    on_disk, _ = okf.load_frontmatter(
        (bundle / "concepts/public.md").read_text(encoding="utf-8")
    )
    assert on_disk["status"] == "stable"


def test_a_symlinked_document_is_never_read_or_exported(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text(
        okf.dump_frontmatter(_concept("Out", "public"), "# Out\n"), encoding="utf-8"
    )
    (bundle / "concepts" / "linked.md").symlink_to(outside)
    plan = _plan(bundle)
    assert "concepts/linked.md" not in plan.files
    assert "concepts/linked.md" in plan.skipped
    assert outside not in plan.inputs


def test_an_unparseable_document_is_withheld(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "concepts" / "broken.md").write_text("no frontmatter\n", "utf-8")
    plan = _plan(bundle)
    assert plan.boundary.withheld["concepts/broken"] is ExportReason.UNREADABLE


# --- publish_export ------------------------------------------------------------


def _no_staging_left(parent: Path) -> bool:
    return not [p for p in parent.iterdir() if ".openkos-export-" in p.name]


def test_publish_writes_the_planned_tree(tmp_path: Path) -> None:
    plan = _plan(_bundle(tmp_path))
    target = tmp_path / "out"
    export_service.publish_export(plan, target)
    assert _tree(target) == {
        rel: text.encode("utf-8") for rel, text in plan.files.items()
    }
    assert okf.check_conformance(target) == []
    assert _no_staging_left(tmp_path)


def test_publish_into_an_existing_empty_directory(tmp_path: Path) -> None:
    plan = _plan(_bundle(tmp_path))
    target = tmp_path / "out"
    target.mkdir()
    export_service.publish_export(plan, target)
    assert (target / "concepts" / "public.md").is_file()


def test_a_non_conformant_document_refuses_and_leaves_nothing(
    tmp_path: Path,
) -> None:
    plan = _plan(_bundle(tmp_path))
    broken = dict(plan.files)
    broken["concepts/public.md"] = okf.dump_frontmatter({"title": "No type"}, "x\n")
    target = tmp_path / "out"
    with pytest.raises(export_service.ExportRefusal) as refusal:
        export_service.publish_export(dataclasses.replace(plan, files=broken), target)
    assert refusal.value.kind == "conformance"
    assert "concepts/public.md" in str(refusal.value)
    assert str(tmp_path) not in str(refusal.value)
    assert not target.exists()
    assert _no_staging_left(tmp_path)


def test_a_withheld_id_in_fenced_code_refuses_via_the_leak_check(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    _write(
        bundle,
        "concepts/code.md",
        _concept("Code", "public"),
        "# Code\n\n```\nsee /concepts/canary-zq.md\n```\n",
    )
    target = tmp_path / "out"
    with pytest.raises(export_service.ExportRefusal) as refusal:
        export_service.publish_export(_plan(bundle), target)
    assert refusal.value.kind == "leak"
    assert "concepts/code.md" in str(refusal.value)
    assert not target.exists()
    assert _no_staging_left(tmp_path)


@pytest.mark.parametrize(
    "text",
    [
        "target: concepts/canary-zq\n",
        "see (/concepts/canary-zq.md#x)\n",
        "see [x](canary-zq.md)\n",
        "see `../concepts/canary-zq.md`\n",
        "- concepts/canary-zq\n",
    ],
)
def test_the_leak_scan_finds_every_pointer_form(text: str) -> None:
    findings = export_service.leak_findings(
        {"concepts/a.md": text}, frozenset({"concepts/canary-zq"})
    )
    assert findings == ["concepts/a.md: concepts/canary-zq"]


@pytest.mark.parametrize(
    "text",
    [
        "concepts/canary-zq-2 is a different concept\n",
        "the canary-zq is just a word\n",
        "[withheld] — related\n",
        "https://example.com/concepts/canary-zq\n",
    ],
)
def test_the_leak_scan_does_not_flag_lookalikes(text: str) -> None:
    findings = export_service.leak_findings(
        {"concepts/a.md": text}, frozenset({"concepts/canary-zq"})
    )
    assert findings == []


def test_a_root_level_id_is_matched_only_as_a_path() -> None:
    # A withheld concept at the bundle root has an id with no `/`; a bare
    # prose word equal to it is not a pointer, but its path forms are.
    withheld = frozenset({"notes"})
    assert export_service.leak_findings({"a.md": "my notes here\n"}, withheld) == []
    assert export_service.leak_findings({"a.md": "see /notes.md\n"}, withheld) == [
        "a.md: notes"
    ]


def test_an_input_changed_after_planning_refuses_as_drift(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    plan = _plan(bundle)
    (bundle / "concepts" / "private.md").write_text(
        okf.dump_frontmatter(_concept("Private", "public"), "# now public\n"),
        encoding="utf-8",
    )
    target = tmp_path / "out"
    with pytest.raises(export_service.ExportRefusal) as refusal:
        export_service.publish_export(plan, target)
    assert refusal.value.kind == "drift"
    assert not target.exists()
    assert _no_staging_left(tmp_path)


def test_a_document_created_after_planning_refuses_as_drift(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    plan = _plan(bundle)
    _write(bundle, "concepts/late.md", _concept("Late", "public"), "# Late\n")
    with pytest.raises(export_service.ExportRefusal) as refusal:
        export_service.publish_export(plan, tmp_path / "out")
    assert refusal.value.kind == "drift"


# --- check_target ----------------------------------------------------------------


def test_a_target_inside_the_workspace_is_refused(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    reason = export_service.check_target(ws / "bundle" / "out", workspace_root=ws)
    assert reason is not None
    assert "inside the workspace" in reason
    assert export_service.check_target(ws, workspace_root=ws) is not None


def test_a_non_empty_target_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    reason = export_service.check_target(out, workspace_root=tmp_path / "ws")
    assert reason is not None
    assert "not empty" in reason


def test_a_file_target_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.write_text("x", encoding="utf-8")
    reason = export_service.check_target(out, workspace_root=tmp_path / "ws")
    assert reason is not None
    assert "not a directory" in reason


def test_a_target_whose_parent_is_missing_is_refused(tmp_path: Path) -> None:
    reason = export_service.check_target(
        tmp_path / "missing" / "out", workspace_root=tmp_path / "ws"
    )
    assert reason is not None
    assert "parent" in reason


def test_an_absent_or_empty_target_outside_is_accepted(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    assert export_service.check_target(tmp_path / "out", workspace_root=ws) is None
    (tmp_path / "empty").mkdir()
    assert export_service.check_target(tmp_path / "empty", workspace_root=ws) is None


# --- the canary guard (ADR-0028 parity) ----------------------------------------


@pytest.mark.parametrize("include_private", [False, True])
def test_no_byte_of_the_canary_leaves(tmp_path: Path, include_private: bool) -> None:
    plan = _plan(_bundle(tmp_path), include_private=include_private)
    target = tmp_path / "out"
    export_service.publish_export(plan, target)
    for rel, data in _tree(target).items():
        lowered = data.decode("utf-8").lower()
        assert "canary-zq" not in lowered, rel
        assert "canary zq" not in lowered, rel
        assert "canary-marker-zq" not in lowered, rel
        assert "0" * 32 not in lowered, rel


def test_exports_are_deterministic(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    export_service.publish_export(_plan(bundle, include_private=True), tmp_path / "a")
    export_service.publish_export(_plan(bundle, include_private=True), tmp_path / "b")
    assert _tree(tmp_path / "a") == _tree(tmp_path / "b")
