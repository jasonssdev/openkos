"""Unit tests for `model/okf.py`'s foreign-document adoption (okf-import,
slice 3, design D4-D6): the label fold, the namespaced Concept ID, the key
classification, the per-label anchor, and the proof of the adopted frontmatter.

Everything here is a pure function over strings and mappings: no model, no
filesystem beyond one conformance round trip in `tmp_path`.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from openkos import config
from openkos.model import okf

_LEVELS = okf.SENSITIVITY_ORDER


def _cfg(
    tmp_path: Path, *, default: str, offsets: Mapping[str, int] | None = None
) -> config.Config:
    lines = [f"default_sensitivity: {default}"]
    if offsets:
        lines.append("type_sensitivity_defaults:")
        lines.extend(f"  {name}: {offset}" for name, offset in offsets.items())
    (tmp_path / "openkos.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config.read_config(tmp_path)


def _birth(cfg: config.Config) -> Callable[[str, object], str]:
    return functools.partial(config.type_birth_sensitivity, cfg)


# --- 3.1 `fold_foreign_sensitivity` -----------------------------------------


class TestFoldForeignSensitivity:
    def test_absent_key_contributes_nothing(self) -> None:
        assert okf.fold_foreign_sensitivity({"type": "Concept"}) is None

    def test_explicit_null_contributes_nothing(self) -> None:
        assert okf.fold_foreign_sensitivity({"sensitivity": None}) is None

    @pytest.mark.parametrize("label", ["public", "private", "confidential"])
    def test_recognized_labels_are_ranked(self, label: str) -> None:
        assert okf.fold_foreign_sensitivity({"sensitivity": label}) == label

    def test_a_recognized_label_is_stripped(self) -> None:
        assert okf.fold_foreign_sensitivity({"sensitivity": " public "}) == "public"

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("secret", id="unknown-string"),
            pytest.param(3, id="integer"),
            pytest.param(["public"], id="list"),
            pytest.param({"level": "public"}, id="mapping"),
            pytest.param(True, id="bool"),
        ],
    )
    def test_unknown_or_non_string_values_fail_closed(self, value: object) -> None:
        assert okf.fold_foreign_sensitivity({"sensitivity": value}) == "confidential"

    def test_a_blank_string_ranks_as_private(self) -> None:
        assert okf.fold_foreign_sensitivity({"sensitivity": "  "}) == "private"


# --- 3.3 the effective-label fold -------------------------------------------


class TestEffectiveImportSensitivity:
    """Run in `default_sensitivity: public` workspaces wherever it matters, so a
    fail-closed `confidential` is distinguishable from the floor."""

    def _label(
        self,
        cfg: config.Config,
        mapping: Mapping[str, object],
        *,
        flag: str | None = None,
        doc_type: str = "Concept",
    ) -> str:
        return okf.effective_import_sensitivity(
            mapping,
            doc_type,
            default_sensitivity=cfg.default_sensitivity,
            flag=flag,
            birth=_birth(cfg),
        )

    def test_absent_label_takes_the_floor(self, tmp_path: Path) -> None:
        public = _cfg(tmp_path, default="public")
        assert self._label(public, {}) == "public"
        private = _cfg(tmp_path, default="private")
        assert self._label(private, {}) == "private"

    def test_null_label_takes_the_floor_not_private(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="public")
        assert self._label(cfg, {"sensitivity": None}) == "public"

    def test_foreign_public_never_lowers_a_private_floor(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="private")
        assert self._label(cfg, {"sensitivity": "public"}) == "private"

    def test_foreign_confidential_raises(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="private")
        assert self._label(cfg, {"sensitivity": "confidential"}) == "confidential"

    def test_foreign_private_raises_a_public_floor(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="public")
        assert self._label(cfg, {"sensitivity": "private"}) == "private"

    @pytest.mark.parametrize("value", ["secret", 3, ["x"]])
    def test_unknown_and_numeric_labels_fail_closed(
        self, tmp_path: Path, value: object
    ) -> None:
        cfg = _cfg(tmp_path, default="public")
        assert self._label(cfg, {"sensitivity": value}) == "confidential"

    def test_blank_label_folds_to_private_over_a_public_floor(
        self, tmp_path: Path
    ) -> None:
        cfg = _cfg(tmp_path, default="public")
        assert self._label(cfg, {"sensitivity": ""}) == "private"

    def test_flag_raises_an_unlabelled_document(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="public")
        assert self._label(cfg, {}, flag="private") == "private"
        assert self._label(cfg, {}, flag="confidential") == "confidential"

    def test_flag_below_the_floor_changes_nothing(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="private")
        assert self._label(cfg, {}, flag="public") == "private"
        assert (
            self._label(cfg, {"sensitivity": "confidential"}, flag="public")
            == "confidential"
        )

    def test_flag_equal_to_the_floor_leaves_labels_alone(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="private")
        assert self._label(cfg, {}, flag="private") == "private"
        assert (
            self._label(cfg, {"sensitivity": "confidential"}, flag="private")
            == "confidential"
        )

    def test_flag_confidential_raises_everything(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="private")
        assert self._label(cfg, {}, flag="confidential") == "confidential"
        assert (
            self._label(cfg, {"sensitivity": "public"}, flag="confidential")
            == "confidential"
        )

    def test_person_is_raised_by_its_offset(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="public", offsets={"Person": 1})
        assert self._label(cfg, {}, doc_type="Person") == "private"

    def test_a_higher_foreign_label_beats_the_offset(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="public", offsets={"Person": 1})
        label = self._label(cfg, {"sensitivity": "confidential"}, doc_type="Person")
        assert label == "confidential"

    def test_the_offset_applies_to_a_foreign_public_label(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="public", offsets={"Person": 1})
        assert (
            self._label(cfg, {"sensitivity": "public"}, doc_type="Person") == "private"
        )

    def test_an_unconfigured_type_is_unaffected(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="public", offsets={"Person": 1})
        assert self._label(cfg, {}, doc_type="Concept") == "public"
        assert self._label(cfg, {}, doc_type="Recipe") == "public"

    def test_an_empty_mapping_applies_no_offset(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="public", offsets={})
        assert self._label(cfg, {}, doc_type="Person") == "public"

    def test_source_is_never_offset(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, default="public")
        hostile = dataclasses.replace(cfg, type_sensitivity_defaults={"Source": 1})
        # The control: the same hostile config raises a type that IS offset.
        assert config.type_birth_sensitivity(hostile, "Source", "public") == "private"
        assert self._label(hostile, {}, doc_type="Source") == "public"

    def test_the_result_equals_the_ingest_seam_for_the_same_inputs(
        self, tmp_path: Path
    ) -> None:
        cfg = _cfg(tmp_path, default="public", offsets={"Person": 2, "Event": 1})
        for doc_type in ("Person", "Event", "Concept"):
            for foreign in (None, "public", "private", "confidential", "secret"):
                mapping: dict[str, object] = (
                    {} if foreign is None else {"sensitivity": foreign}
                )
                base = (
                    "public"
                    if foreign is None
                    else okf.combine_sensitivity("public", foreign)
                )
                oracle = config.type_birth_sensitivity(cfg, doc_type, base)
                assert self._label(cfg, mapping, doc_type=doc_type) == oracle

    def test_no_import_is_ever_below_the_default(self, tmp_path: Path) -> None:
        for default in _LEVELS:
            cfg = _cfg(tmp_path, default=default, offsets={"Person": 1})
            for flag in (None, *_LEVELS):
                for foreign in (None, *_LEVELS, "secret", 3):
                    mapping: dict[str, object] = (
                        {} if foreign is None else {"sensitivity": foreign}
                    )
                    for doc_type in ("Person", "Concept"):
                        label = self._label(cfg, mapping, flag=flag, doc_type=doc_type)
                        assert _LEVELS.index(label) >= _LEVELS.index(default)

    def test_the_floor_is_the_default_combined_with_the_flag(self) -> None:
        assert okf.import_floor("private", None) == "private"
        assert okf.import_floor("private", "public") == "private"
        assert okf.import_floor("private", "confidential") == "confidential"
        assert okf.import_floor("public", "private") == "private"


# --- 3.5 `namespaced_concept_id` --------------------------------------------


class TestNamespacedConceptId:
    PREFIX = "imports/demo"

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            ("/concepts/x", "imports/demo/concepts/x"),
            ("/concepts/x.md", "imports/demo/concepts/x"),
            ("concepts/x", "imports/demo/concepts/x"),
            ("concepts/x.md.md", "imports/demo/concepts/x.md"),
            ("//concepts/x.md", "imports/demo/concepts/x"),
            ("./concepts/x", "imports/demo/concepts/x"),
            ("/a/./b/../c.md", "imports/demo/a/c"),
            ("/../../x.md", "imports/demo/x"),
            ("a/../../../b", "imports/demo/b"),
            ("/a//b", "imports/demo/a/b"),
        ],
    )
    def test_normalizes_clamps_and_prefixes(self, target: str, expected: str) -> None:
        assert okf.namespaced_concept_id(target, self.PREFIX) == expected

    @pytest.mark.parametrize("target", ["", "/", ".md", "/..", "..", "/./"])
    def test_a_target_naming_nothing_is_none(self, target: str) -> None:
        assert okf.namespaced_concept_id(target, self.PREFIX) is None

    def test_the_prefix_is_used_as_given(self) -> None:
        assert okf.namespaced_concept_id("/x", "imports/other") == "imports/other/x"


# --- shared fixtures for the document tests ---------------------------------

PREFIX = "imports/demo"
ANCHOR = "imports/demo--private"
SHA = hashlib.sha256(b"foreign bytes").hexdigest()


def _doc(
    mapping: Mapping[str, object],
    *,
    foreign_id: str = "concepts/stoicism",
    body: str = "# Stoicism\n\nA school of thought.\n",
) -> okf.ForeignDocument:
    return okf.ForeignDocument(
        path=f"{foreign_id}.md",
        foreign_id=foreign_id,
        doc_type=str(mapping.get("type", "Concept")),
        mapping=dict(mapping),
        body=body,
        sha256=SHA,
    )


def _adopt(
    mapping: Mapping[str, object],
    *,
    label: str = "private",
    body: str | None = None,
    anchor: str = ANCHOR,
) -> tuple[dict[str, object], dict[str, object], str]:
    """`(adopted frontmatter, imported block, whole text)`."""
    doc = _doc(mapping)
    text = okf.adopt_foreign_document(
        doc,
        body=doc.body if body is None else body,
        sensitivity=label,
        anchor_id=anchor,
        prefix=PREFIX,
    )
    meta, _ = okf.load_frontmatter(text)
    imported = meta[okf.IMPORTED_KEY]
    assert isinstance(imported, dict)
    return meta, imported, text


def _inert(imported: Mapping[str, object]) -> dict[str, object]:
    inert = imported.get("frontmatter", {})
    assert isinstance(inert, dict)
    return inert


# --- 3.6 the key-classification table ---------------------------------------


class TestKeysKeptVerbatim:
    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("type", "Recipe with space"),
            ("title", "Stoicism"),
            ("description", "A school."),
            ("tags", ["philosophy", "ethics"]),
            ("aliases", ["The Stoa"]),
            ("freshness", "timeless"),
            ("event_date", "2026-01-02"),
            ("type_alternative", "Event"),
            ("x_custom", {"nested": [1, 2, {"k": "v"}]}),
            ("another_unknown", "kept"),
        ],
    )
    def test_kept_as_written_and_not_duplicated_inert(
        self, key: str, value: object
    ) -> None:
        mapping = {"type": "Concept", key: value}
        meta, imported, _ = _adopt(mapping)
        assert meta[key] == value
        assert key not in _inert(imported)


class TestSensitivityKey:
    def test_the_effective_label_replaces_it_and_the_original_is_kept_inert(
        self,
    ) -> None:
        meta, imported, _ = _adopt(
            {"type": "Concept", "sensitivity": "public"}, label="private"
        )
        assert meta["sensitivity"] == "private"
        assert _inert(imported)["sensitivity"] == "public"

    def test_an_unknown_original_is_kept_verbatim_inert(self) -> None:
        meta, imported, _ = _adopt(
            {"type": "Concept", "sensitivity": "secret"}, label="confidential"
        )
        assert meta["sensitivity"] == "confidential"
        assert _inert(imported)["sensitivity"] == "secret"

    def test_an_absent_original_leaves_no_inert_trace(self) -> None:
        meta, imported, _ = _adopt({"type": "Concept"}, label="private")
        assert meta["sensitivity"] == "private"
        assert "sensitivity" not in _inert(imported)

    def test_an_explicit_null_original_is_preserved_as_null(self) -> None:
        _, imported, _ = _adopt({"type": "Concept", "sensitivity": None})
        assert "sensitivity" in _inert(imported)
        assert _inert(imported)["sensitivity"] is None


_INERT_VALUES: dict[str, object] = {
    "status": "deprecated",
    "generated": {"by": "reference_agent/1.2", "at": "2026-09-01T10:00:00Z"},
    "verified": [{"by": "someone", "at": "2026-01-01"}],
    "version": 7,
    "timestamp": "2026-02-02T00:00:00Z",
    "ingest_pending": True,
    "extraction_status": "degraded",
    "extraction_notice": ["chunk-extraction-partial"],
    "status_derived_from": ["concepts/other"],
    "source_frontmatter": {"title": "a source"},
    "okf_version": "0.2",
}


class TestKeysMovedInert:
    @pytest.mark.parametrize("key", sorted(_INERT_VALUES))
    def test_moved_verbatim_under_imported_and_absent_at_the_top(
        self, key: str
    ) -> None:
        value = _INERT_VALUES[key]
        meta, imported, _ = _adopt({"type": "Concept", key: value})
        assert key not in meta
        assert _inert(imported)[key] == value

    def test_every_inert_key_at_once(self) -> None:
        mapping = {"type": "Concept", **_INERT_VALUES}
        meta, imported, _ = _adopt(mapping)
        assert _inert(imported) == _INERT_VALUES
        assert set(meta) == {
            "type",
            "sensitivity",
            "provenance",
            "sources",
            okf.IMPORTED_KEY,
        }

    def test_foreign_provenance_and_sources_are_inert_and_replaced(self) -> None:
        foreign_sources = [{"resource": "https://example.org/a", "title": "A"}]
        meta, imported, _ = _adopt(
            {
                "type": "Concept",
                "provenance": ["sources/x"],
                "sources": foreign_sources,
            }
        )
        assert _inert(imported)["provenance"] == ["sources/x"]
        assert _inert(imported)["sources"] == foreign_sources
        assert meta["provenance"] == [ANCHOR]
        assert meta["sources"] == okf.project_sources([ANCHOR])
        assert meta["sources"] == [
            {"id": ANCHOR, "resource": f"/{ANCHOR}.md"},
        ]

    def test_an_absent_provenance_still_cites_the_anchor(self) -> None:
        meta, _, _ = _adopt({"type": "Concept"})
        assert meta["provenance"] == [ANCHOR]

    def test_a_foreign_deprecated_status_is_not_effective_deprecated(self) -> None:
        meta, _, _ = _adopt({"type": "Concept", "status": "deprecated"})
        assert not okf.declares_deprecated(meta)
        assert not okf.is_marked_deprecated(meta)

    def test_a_foreign_verified_is_not_a_local_attestation(self) -> None:
        meta, _, _ = _adopt({"type": "Concept", "verified": [{"by": "x"}]})
        assert "verified" not in meta

    def test_no_generated_is_stamped_when_the_foreign_one_is_absent(self) -> None:
        meta, _, _ = _adopt({"type": "Concept"})
        assert "generated" not in meta

    def test_no_generated_is_stamped_when_the_foreign_one_is_present(self) -> None:
        meta, _, _ = _adopt(
            {"type": "Concept", "generated": {"by": "x", "at": "2026-01-01"}}
        )
        assert "generated" not in meta
        assert "version" not in meta


class TestResourceKey:
    @pytest.mark.parametrize(
        "value",
        [
            "raw/notes.txt",
            "notes.txt",
            "/raw/notes.txt",
            "../raw/notes.txt",
            "//host/raw/notes.txt",
        ],
    )
    def test_a_resource_without_a_url_scheme_is_inert(self, value: str) -> None:
        meta, imported, _ = _adopt({"type": "Source", "resource": value})
        assert "resource" not in meta
        assert _inert(imported)["resource"] == value

    def test_a_non_string_resource_is_inert(self) -> None:
        meta, imported, _ = _adopt({"type": "Source", "resource": ["raw/a"]})
        assert "resource" not in meta
        assert _inert(imported)["resource"] == ["raw/a"]

    @pytest.mark.parametrize(
        "value", ["https://example.org/a", "urn:isbn:0451450523", "mailto:a@b.c"]
    )
    def test_a_resource_with_a_url_scheme_is_kept(self, value: str) -> None:
        meta, imported, _ = _adopt({"type": "Source", "resource": value})
        assert meta["resource"] == value
        assert "resource" not in _inert(imported)


class TestRelationsKey:
    def test_engine_owned_entries_are_inert_and_ordinary_ones_rewritten(
        self,
    ) -> None:
        provenance_entry = {"target": "sources/x", "type": "derived_from"}
        meta, imported, _ = _adopt(
            {
                "type": "Concept",
                "relations": [
                    provenance_entry,
                    {"target": "/concepts/x", "type": "related_to"},
                ],
            }
        )
        assert meta["relations"] == [
            {"target": "imports/demo/concepts/x", "type": "related_to"}
        ]
        assert _inert(imported)["relations"] == [provenance_entry]

    def test_only_ordinary_entries_leave_no_inert_relations(self) -> None:
        meta, imported, _ = _adopt(
            {
                "type": "Concept",
                "relations": [{"target": "b", "type": "part_of"}],
            }
        )
        assert meta["relations"] == [{"target": "imports/demo/b", "type": "part_of"}]
        assert "relations" not in _inert(imported)

    def test_only_engine_owned_entries_leave_no_top_level_relations(self) -> None:
        entry = {"target": "sources/x", "type": "derived_from"}
        meta, imported, _ = _adopt({"type": "Concept", "relations": [entry]})
        assert "relations" not in meta
        assert _inert(imported)["relations"] == [entry]

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("not a list", id="scalar"),
            pytest.param([{"type": "related_to"}], id="missing-target"),
            pytest.param(["a string entry"], id="non-mapping-entry"),
            pytest.param([{"target": "..", "type": "related_to"}], id="names-nothing"),
        ],
    )
    def test_an_undecodable_value_moves_inert_whole(self, value: object) -> None:
        meta, imported, _ = _adopt({"type": "Concept", "relations": value})
        assert "relations" not in meta
        assert _inert(imported)["relations"] == value

    def test_an_explicit_null_is_preserved_inert(self) -> None:
        meta, imported, _ = _adopt({"type": "Concept", "relations": None})
        assert "relations" not in meta
        assert "relations" in _inert(imported)
        assert _inert(imported)["relations"] is None

    def test_one_bad_entry_moves_the_whole_value_inert(self) -> None:
        good = {"target": "/concepts/x", "type": "related_to"}
        value = [good, {"type": "related_to"}]
        meta, imported, _ = _adopt({"type": "Concept", "relations": value})
        assert "relations" not in meta
        assert _inert(imported)["relations"] == value


class TestDroppedAndNestedKeys:
    def test_machine_local_keys_are_dropped_everywhere(self) -> None:
        mapping = {
            "type": "Concept",
            "origin_key": "abc123",
            "merged_from": [{"absorbed": "x"}],
        }
        meta, imported, text = _adopt(mapping)
        assert "origin_key" not in meta
        assert "merged_from" not in meta
        assert "origin_key" not in _inert(imported)
        assert "merged_from" not in _inert(imported)
        assert "abc123" not in text

    def test_dropped_keys_are_reported_sorted(self) -> None:
        assert okf.dropped_foreign_keys(
            {"type": "A", "merged_from": [], "origin_key": "x", "title": "t"}
        ) == ("merged_from", "origin_key")
        assert okf.dropped_foreign_keys({"type": "A", "title": "t"}) == ()

    def test_a_pre_existing_imported_key_moves_inert_and_nesting_composes(
        self,
    ) -> None:
        earlier = {"namespace": "earlier", "id": "old/id", "sha256": "00"}
        meta, imported, _ = _adopt({"type": "Concept", "imported": earlier})
        assert imported["namespace"] == "demo"
        assert _inert(imported)["imported"] == earlier
        assert meta[okf.IMPORTED_KEY]["namespace"] == "demo"  # type: ignore[index]


class TestImportedBlockShape:
    def test_it_carries_only_namespace_id_digest_and_moved_keys(self) -> None:
        _, imported, _ = _adopt({"type": "Concept", "status": "stable"})
        assert set(imported) == {"namespace", "id", "sha256", "frontmatter"}
        assert imported["namespace"] == "demo"
        assert imported["id"] == "concepts/stoicism"
        assert imported["sha256"] == SHA

    def test_frontmatter_is_omitted_when_nothing_moved(self) -> None:
        meta, imported, _ = _adopt({"type": "Concept", "title": "T"})
        assert set(imported) == {"namespace", "id", "sha256"}
        assert "sensitivity" in meta

    def test_the_namespace_is_read_from_the_prefix(self) -> None:
        doc = _doc({"type": "Concept"})
        text = okf.adopt_foreign_document(
            doc,
            body=doc.body,
            sensitivity="private",
            anchor_id="imports/other--private",
            prefix="imports/other",
        )
        meta, _ = okf.load_frontmatter(text)
        assert meta[okf.IMPORTED_KEY]["namespace"] == "other"  # type: ignore[index]

    def test_the_body_is_carried_as_given(self) -> None:
        _, _, text = _adopt({"type": "Concept"}, body="# Rewritten\n\n[l](/x.md)\n")
        _, body = okf.load_frontmatter(text)
        assert body.strip() == "# Rewritten\n\n[l](/x.md)".strip()

    def test_the_text_has_no_yaml_anchors_or_aliases(self) -> None:
        shared = ["a", "b"]
        _, _, text = _adopt(
            {"type": "Concept", "tags": shared, "x_other": shared, "verified": shared}
        )
        assert "&id" not in text
        assert "*id" not in text
        incoming = okf.parse_incoming_frontmatter(text)
        assert incoming.status == "parsed"


# --- 3.11 `build_import_anchor` ---------------------------------------------

GENERATED = okf.Generated(by="openkos/0.5.x", at="2026-10-06T09:00:00Z")
SENTINEL_PATH = "/home/a/private/bundle"


def _entry(
    foreign_id: str,
    *,
    title: str | None = None,
    sha: str | None = None,
    generated_by: str | None = None,
) -> okf.AnchorEntry:
    return okf.AnchorEntry(
        foreign_id=foreign_id,
        title=title or foreign_id.rsplit("/", 1)[-1].title(),
        sha256=sha or hashlib.sha256(foreign_id.encode()).hexdigest(),
        generated_by=generated_by,
    )


def _anchor(
    entries: list[okf.AnchorEntry],
    *,
    label: str = "private",
    okf_version: object = "0.2",
    bundle_sha256: str = "9a" * 32,
) -> tuple[dict[str, object], str, str]:
    text = okf.build_import_anchor(
        namespace="demo",
        label=label,
        entries=entries,
        bundle_sha256=bundle_sha256,
        okf_version=okf_version,
        generated=GENERATED,
    )
    meta, body = okf.load_frontmatter(text)
    return meta, body, text


class TestBuildImportAnchor:
    def test_the_frontmatter_follows_the_design(self) -> None:
        meta, _, _ = _anchor([_entry("concepts/a")])
        assert meta["type"] == "Source"
        assert meta["title"] == "Import demo (private)"
        assert meta["sensitivity"] == "private"
        assert meta["tags"] == ["import"]
        assert meta["freshness"] == "snapshot"
        assert meta["status"] == "stable"
        assert meta["version"] == 1
        assert meta["generated"] == {
            "by": "openkos/0.5.x",
            "at": "2026-10-06T09:00:00Z",
        }
        assert isinstance(meta["description"], str)
        assert "imports/demo" in str(meta["description"])
        assert "private" in str(meta["description"])

    def test_it_carries_no_resource_and_no_provenance(self) -> None:
        meta, _, _ = _anchor([_entry("concepts/a")])
        assert "resource" not in meta
        assert "provenance" not in meta
        assert "sources" not in meta

    def test_the_imported_mapping_records_the_origin_by_content(self) -> None:
        meta, _, _ = _anchor(
            [
                _entry("concepts/a", generated_by="reference_agent/1.2"),
                _entry("concepts/b", generated_by="another/9"),
                _entry("concepts/c", generated_by="reference_agent/1.2"),
                _entry("concepts/d"),
            ]
        )
        assert meta[okf.IMPORTED_KEY] == {
            "role": "anchor",
            "namespace": "demo",
            "label": "private",
            "bundle_sha256": "9a" * 32,
            "okf_version": "0.2",
            "generated_by": ["another/9", "reference_agent/1.2"],
            "documents": 4,
        }

    def test_an_unobserved_okf_version_is_null_and_generated_by_is_empty(self) -> None:
        meta, _, _ = _anchor([_entry("concepts/a")], okf_version=None)
        imported = meta[okf.IMPORTED_KEY]
        assert isinstance(imported, dict)
        assert "okf_version" in imported
        assert imported["okf_version"] is None
        assert imported["generated_by"] == []

    def test_an_observed_non_string_okf_version_is_recorded_as_observed(self) -> None:
        meta, _, _ = _anchor([_entry("concepts/a")], okf_version=0.1)
        assert meta[okf.IMPORTED_KEY]["okf_version"] == 0.1  # type: ignore[index]

    def test_the_body_lists_one_bullet_per_document_sorted(self) -> None:
        entries = [
            _entry("z/last", title="Last", sha="cc" * 32),
            _entry("a/first", title="First", sha="aa" * 32),
        ]
        _, body, _ = _anchor(entries)
        bullets = [line for line in body.splitlines() if line.startswith("- ")]
        assert bullets == [
            f"- [First](/imports/demo/a/first.md) — foreign id `a/first`, "
            f"sha256 `{'aa' * 32}`",
            f"- [Last](/imports/demo/z/last.md) — foreign id `z/last`, "
            f"sha256 `{'cc' * 32}`",
        ]

    def test_a_title_with_a_newline_is_refused(self) -> None:
        with pytest.raises(ValueError, match="newline"):
            _anchor([_entry("a/b", title="Two\nlines")])
        with pytest.raises(ValueError, match="newline"):
            _anchor([_entry("a/b", title="Carriage\rreturn")])

    @pytest.mark.parametrize("title", ["Forged](/concepts/x.md) [", "has [bracket"])
    def test_a_title_with_a_link_delimiter_is_refused(self, title: str) -> None:
        with pytest.raises(ValueError, match="sanitized"):
            _anchor([_entry("a/b", title=title)])

    def test_the_sanitizer_output_is_accepted(self) -> None:
        from openkos.bundle import index

        label = index.sanitize_link_label("Notes [1] (draft)")
        _, body, _ = _anchor([_entry("a/b", title=label)])
        assert "- [Notes (1) (draft)](/imports/demo/a/b.md)" in body

    def test_an_anchor_with_no_documents_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one document"):
            _anchor([])

    def test_an_unrecognized_label_is_refused(self) -> None:
        with pytest.raises(ValueError, match="label"):
            _anchor([_entry("a/b")], label="secret")

    def test_no_local_path_or_machine_identifier_appears_anywhere(self) -> None:
        _, _, text = _anchor([_entry("concepts/a"), _entry("concepts/b")])
        assert SENTINEL_PATH not in text
        assert "/home/" not in text
        assert "/Users/" not in text

    def test_two_labels_yield_two_anchors_over_disjoint_documents(self) -> None:
        public = [_entry("concepts/p1"), _entry("concepts/p2")]
        private = [_entry("concepts/q1")]
        pub_meta, pub_body, _ = _anchor(public, label="public")
        priv_meta, priv_body, _ = _anchor(private, label="private")
        assert pub_meta["sensitivity"] == "public"
        assert priv_meta["sensitivity"] == "private"
        assert pub_meta["title"] == "Import demo (public)"
        assert "concepts/p1" in pub_body
        assert "concepts/p2" in pub_body
        assert "concepts/q1" not in pub_body
        assert "concepts/q1" in priv_body
        assert "concepts/p1" not in priv_body

    def test_the_text_parses_through_the_guarded_parser(self) -> None:
        _, _, text = _anchor([_entry("concepts/a")])
        assert okf.parse_incoming_frontmatter(text).status == "parsed"


class TestImportBundleDigest:
    def test_it_is_over_the_sorted_id_tab_digest_lines(self) -> None:
        expected = hashlib.sha256(b"a/x\t11\nb/y\t22\n").hexdigest()
        assert okf.import_bundle_digest([("a/x", "11"), ("b/y", "22")]) == expected

    def test_it_is_order_independent(self) -> None:
        forward = okf.import_bundle_digest([("a", "1"), ("b", "2")])
        assert okf.import_bundle_digest([("b", "2"), ("a", "1")]) == forward

    def test_it_changes_with_an_id_or_a_digest(self) -> None:
        base = okf.import_bundle_digest([("a", "1")])
        assert okf.import_bundle_digest([("a", "2")]) != base
        assert okf.import_bundle_digest([("b", "1")]) != base


# --- 3.13 `adopted_violations` ----------------------------------------------


def _good_text() -> str:
    doc = _doc(
        {
            "type": "Concept",
            "title": "T",
            "relations": [{"target": "/concepts/y", "type": "related_to"}],
        }
    )
    return okf.adopt_foreign_document(
        doc, body=doc.body, sensitivity="private", anchor_id=ANCHOR, prefix=PREFIX
    )


def _violations(
    meta_edit: Mapping[str, object] | None = None,
    *,
    drop: tuple[str, ...] = (),
    floor: str = "private",
) -> list[str]:
    meta, body = okf.load_frontmatter(_good_text())
    meta.update(meta_edit or {})
    for key in drop:
        meta.pop(key, None)
    return okf.adopted_violations(
        okf.dump_frontmatter(meta, body), prefix=PREFIX, anchor_id=ANCHOR, floor=floor
    )


class TestAdoptedViolations:
    def test_a_good_document_has_none(self) -> None:
        assert (
            okf.adopted_violations(
                _good_text(), prefix=PREFIX, anchor_id=ANCHOR, floor="private"
            )
            == []
        )

    def test_a_relation_outside_the_namespace(self) -> None:
        found = _violations(
            {"relations": [{"target": "concepts/local", "type": "related_to"}]}
        )
        assert len(found) == 1
        assert "concepts/local" in found[0]

    def test_every_offending_relation_is_named(self) -> None:
        found = _violations(
            {
                "relations": [
                    {"target": "imports/demo/ok", "type": "related_to"},
                    {"target": "imports/demo2/x", "type": "related_to"},
                    {"target": "concepts/local", "type": "related_to"},
                ]
            }
        )
        assert len(found) == 2

    def test_an_undecodable_relations_value(self) -> None:
        found = _violations({"relations": "nope"})
        assert len(found) == 1
        assert "relations" in found[0]

    def test_a_provenance_naming_a_foreign_id(self) -> None:
        found = _violations({"provenance": ["concepts/stoicism"]})
        assert len(found) == 1
        assert "provenance" in found[0]

    def test_a_provenance_naming_the_wrong_anchor(self) -> None:
        found = _violations({"provenance": ["imports/demo--public"]})
        assert len(found) == 1
        assert "provenance" in found[0]

    def test_a_provenance_naming_more_than_the_anchor(self) -> None:
        found = _violations({"provenance": [ANCHOR, "sources/x"]})
        assert len(found) == 1

    def test_a_missing_provenance(self) -> None:
        found = _violations(drop=("provenance",))
        assert len(found) == 1
        assert "provenance" in found[0]

    def test_a_label_below_the_floor(self) -> None:
        found = _violations({"sensitivity": "public"})
        assert len(found) == 1
        assert "below" in found[0]

    def test_a_label_at_the_floor_is_fine(self) -> None:
        assert _violations({"sensitivity": "private"}, floor="private") == []
        assert _violations({"sensitivity": "confidential"}, floor="private") == []

    def test_a_missing_sensitivity(self) -> None:
        found = _violations(drop=("sensitivity",))
        assert len(found) == 1
        assert "sensitivity" in found[0]

    @pytest.mark.parametrize("value", ["secret", 3, None, ["private"], " private"])
    def test_an_unrecognized_sensitivity(self, value: object) -> None:
        found = _violations({"sensitivity": value})
        assert len(found) == 1
        assert "sensitivity" in found[0]

    @pytest.mark.parametrize(
        "key",
        [
            "status",
            "generated",
            "verified",
            "version",
            "timestamp",
            "ingest_pending",
            "extraction_status",
            "extraction_notice",
            "status_derived_from",
            "source_frontmatter",
            "origin_key",
            "merged_from",
            "okf_version",
        ],
    )
    def test_a_surviving_engine_read_key(self, key: str) -> None:
        found = _violations({key: "x"})
        assert len(found) == 1
        assert key in found[0]

    @pytest.mark.parametrize("value", ["raw/notes.txt", "notes.txt", ["raw/a"], 3])
    def test_a_surviving_path_resource(self, value: object) -> None:
        found = _violations({"resource": value})
        assert len(found) == 1
        assert "resource" in found[0]

    def test_a_url_resource_is_fine(self) -> None:
        assert _violations({"resource": "https://example.org/a"}) == []

    def test_unparseable_frontmatter(self) -> None:
        found = okf.adopted_violations(
            "---\nkey: [unclosed\n---\nbody\n",
            prefix=PREFIX,
            anchor_id=ANCHOR,
            floor="private",
        )
        assert len(found) == 1
        assert "frontmatter" in found[0]

    def test_a_document_with_no_frontmatter(self) -> None:
        found = okf.adopted_violations(
            "just a body\n", prefix=PREFIX, anchor_id=ANCHOR, floor="private"
        )
        assert found
        assert any("sensitivity" in line for line in found)


# --- 3.15 seam check ---------------------------------------------------------


@pytest.mark.cross_platform_smoke
def test_adopted_documents_and_anchors_are_a_conformant_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    namespace_dir = bundle / "imports" / "demo" / "concepts"
    namespace_dir.mkdir(parents=True)
    docs = {
        "concepts/stoicism": {"type": "Concept", "title": "Stoicism"},
        "concepts/recipe": {"type": "Recipe with a space", "title": "R"},
        "notes": {"type": "Source", "resource": "raw/notes.txt"},
    }
    entries = []
    for foreign_id, mapping in docs.items():
        doc = _doc(mapping, foreign_id=foreign_id)
        text = okf.adopt_foreign_document(
            doc, body=doc.body, sensitivity="private", anchor_id=ANCHOR, prefix=PREFIX
        )
        target = bundle / "imports" / "demo" / f"{foreign_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        entries.append(_entry(foreign_id))
    anchor = okf.build_import_anchor(
        namespace="demo",
        label="private",
        entries=entries,
        bundle_sha256=okf.import_bundle_digest(
            (e.foreign_id, e.sha256) for e in entries
        ),
        okf_version="0.2",
        generated=GENERATED,
    )
    (bundle / "imports" / "demo--private.md").write_text(anchor, encoding="utf-8")

    assert okf.check_conformance(bundle) == []
    # The control: the check really walked the adopted files.
    assert len(list(okf.iter_bundle_markdown(bundle))) == 4


# --- label-mix export pin (design D6; ADR-0048) ------------------------------


def _mixed_label_docs(
    *, public_cites: str = "public"
) -> dict[str, Mapping[str, object] | None]:
    """A concept at each label under ITS OWN anchor, as the export boundary
    would see the namespace: three anchors and three adopted documents. The
    `public_cites` knob lets a control point the public document at a higher
    anchor, the one-anchor-at-the-maximum design this change rejected."""
    docs: dict[str, Mapping[str, object] | None] = {}
    for label in _LEVELS:
        anchor_text = okf.build_import_anchor(
            namespace="demo",
            label=label,
            entries=[_entry(f"concepts/{label}")],
            bundle_sha256="00" * 32,
            okf_version="0.2",
            generated=GENERATED,
        )
        docs[f"imports/demo--{label}"] = okf.load_frontmatter(anchor_text)[0]
        cited = public_cites if label == "public" else label
        doc = _doc({"type": "Concept", "title": label}, foreign_id=f"concepts/{label}")
        text = okf.adopt_foreign_document(
            doc,
            body=doc.body,
            sensitivity=label,
            anchor_id=f"imports/demo--{cited}",
            prefix=PREFIX,
        )
        docs[f"imports/demo/concepts/{label}"] = okf.load_frontmatter(text)[0]
    return docs


class TestExportBoundaryOverAnImportedMix:
    def test_no_document_is_below_source_and_only_its_own_label_withholds(
        self,
    ) -> None:
        from openkos import sensitivity

        boundary = sensitivity.export_boundary(
            _mixed_label_docs(), include_private=True, allow_below_source=False
        )
        assert boundary.below_source == ()
        assert {
            "imports/demo/concepts/public",
            "imports/demo/concepts/private",
            "imports/demo--public",
            "imports/demo--private",
        } <= boundary.allowed
        assert boundary.withheld == {
            "imports/demo/concepts/confidential": sensitivity.ExportReason.CONFIDENTIAL,
            "imports/demo--confidential": sensitivity.ExportReason.CONFIDENTIAL,
        }

    def test_without_include_private_only_the_private_label_withholds(self) -> None:
        from openkos import sensitivity

        boundary = sensitivity.export_boundary(
            _mixed_label_docs(), include_private=False, allow_below_source=False
        )
        assert boundary.below_source == ()
        assert boundary.withheld["imports/demo/concepts/private"] is (
            sensitivity.ExportReason.PRIVATE
        )
        assert "imports/demo/concepts/public" in boundary.allowed

    def test_control_a_public_document_under_a_higher_anchor_is_below_source(
        self,
    ) -> None:
        """The rejected one-anchor-at-the-maximum design WOULD withhold: this
        proves the pin above can fail."""
        from openkos import sensitivity

        boundary = sensitivity.export_boundary(
            _mixed_label_docs(public_cites="private"),
            include_private=True,
            allow_below_source=False,
        )
        assert boundary.below_source == ("imports/demo/concepts/public",)
        assert boundary.withheld["imports/demo/concepts/public"] is (
            sensitivity.ExportReason.BELOW_SOURCE
        )


# --- `is_import_anchor_of` (slice 4: the torn-anchor ownership test) --------


class TestIsImportAnchorOf:
    """The OKF seam is the only reader of the `imported` key, so the service
    asks it whether a file at an anchor path is this namespace's own anchor."""

    def _text(self, *, label: str = "private") -> str:
        return _anchor([_entry("concepts/a")], label=label)[2]

    def test_an_anchor_built_for_the_namespace_and_label_is_its_own(self) -> None:
        assert okf.is_import_anchor_of(self._text(), namespace="demo", label="private")

    def test_another_namespace_is_not_its_own(self) -> None:
        assert not okf.is_import_anchor_of(
            self._text(), namespace="other", label="private"
        )

    def test_another_label_is_not_its_own(self) -> None:
        assert not okf.is_import_anchor_of(
            self._text(), namespace="demo", label="public"
        )

    def test_an_adopted_document_is_not_an_anchor(self) -> None:
        doc = okf.dump_frontmatter(
            {
                "type": "Concept",
                okf.IMPORTED_KEY: {"namespace": "demo", "label": "private", "id": "x"},
            },
            "body\n",
        )
        assert not okf.is_import_anchor_of(doc, namespace="demo", label="private")

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "not frontmatter at all\n",
            "---\ntype: Source\n---\nbody\n",
            "---\ntype: Source\nimported: just a string\n---\nbody\n",
            "---\ntype: Source\nimported: [1, 2]\n---\nbody\n",
            "---\ntype: [unclosed\n---\nbody\n",
        ],
    )
    def test_anything_else_is_not_an_anchor(self, text: str) -> None:
        assert not okf.is_import_anchor_of(text, namespace="demo", label="private")
