"""Whitespace names are renamed to slugs on import (okf-import, slice 4b).

The graph link reader cannot represent a link to a name with whitespace in any
spelling, so an adopted document or directory whose foreign name holds
whitespace is renamed: whitespace runs become `-` and the segment is
casefolded (NFC in and out). Only a segment WITH whitespace is touched; every
other segment keeps its bytes. Two names that land on the same slug are
refused with `rename-collision`, never merged.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping
from pathlib import Path

import pytest

from openkos.model import okf

PREFIX = "imports/demo"
ANCHOR = "imports/demo--private"
SHA = hashlib.sha256(b"foreign bytes").hexdigest()


# --- the slug rule -----------------------------------------------------------


class TestRenameForeignSegment:
    @pytest.mark.parametrize(
        ("segment", "expected"),
        [
            ("Mi Nota", "mi-nota"),
            ("My Folder", "my-folder"),
            ("My   Folder", "my-folder"),  # a run is ONE hyphen
            ("a\u00a0b", "a-b"),  # no-break space
            ("A\u3000B", "a-b"),  # ideographic space
            ("Straße Café", "strasse-café"),  # casefold, not lower
            ("Cafe\u0301 Bar", "café-bar"),  # NFC out
            ("Mi Nota_v1.2", "mi-nota_v1.2"),  # only whitespace is replaced
            ("A - B", "a---b"),  # an existing hyphen is kept, not collapsed
        ],
    )
    def test_a_segment_with_whitespace_becomes_a_slug(
        self, segment: str, expected: str
    ) -> None:
        assert okf.rename_foreign_segment(segment) == expected

    @pytest.mark.parametrize(
        "segment", ["MiNota", "Mi_Nota", "mi-nota", "Cafe\u0301", "Straße", "a.b"]
    )
    def test_a_segment_without_whitespace_keeps_its_bytes(self, segment: str) -> None:
        assert okf.rename_foreign_segment(segment) == segment

    def test_the_rule_is_idempotent(self) -> None:
        once = okf.rename_foreign_segment("My  Folder\u00a0X")
        assert okf.rename_foreign_segment(once) == once


class TestRenamedForeignId:
    @pytest.mark.parametrize(
        ("foreign_id", "expected"),
        [
            ("Mi Nota", "mi-nota"),
            ("My Folder/Mi Nota", "my-folder/mi-nota"),
            ("Keep/Mi Nota", "Keep/mi-nota"),  # only the spaced segment changes
            ("My Folder/Plain", "my-folder/Plain"),
            ("Plain/Name", "Plain/Name"),
        ],
    )
    def test_each_segment_is_renamed_on_its_own(
        self, foreign_id: str, expected: str
    ) -> None:
        assert okf.renamed_foreign_id(foreign_id) == expected


# --- collisions after the rename ---------------------------------------------


def _rename_collision(paths: list[str]) -> okf.ForeignRefusal:
    with pytest.raises(okf.ForeignRefusal) as info:
        okf.validate_foreign_renames(paths)
    assert info.value.code == "rename-collision"
    return info.value


class TestRenameCollisions:
    def test_a_renamed_file_against_an_unchanged_one_is_a_collision(self) -> None:
        err = _rename_collision(["Mi Nota.md", "mi-nota.md"])
        assert "Mi Nota.md" in err.path
        assert "mi-nota.md" in err.path

    def test_two_renamed_files_that_land_together_are_a_collision(self) -> None:
        err = _rename_collision(["Mi Nota.md", "MI  NOTA.md"])
        assert "Mi Nota.md" in err.path
        assert "MI  NOTA.md" in err.path

    def test_the_comparison_is_casefolded(self) -> None:
        # `MI-NOTA` is unchanged; `Mi Nota` renames to `mi-nota`: equal only
        # after casefolding, which is exactly what the existing check does
        err = _rename_collision(["Mi Nota.md", "MI-NOTA.md"])
        assert "MI-NOTA.md" in err.path

    def test_the_comparison_is_nfc(self) -> None:
        nfd = unicodedata.normalize("NFD", "café-x.md")
        err = _rename_collision(["Café X.md", nfd])
        assert "Café X.md" in err.path

    def test_a_renamed_directory_is_a_collision_and_names_both(self) -> None:
        err = _rename_collision(["My Folder/a.md", "my-folder/b.md"])
        assert "My Folder" in err.path
        assert "my-folder" in err.path

    def test_the_verdict_does_not_depend_on_the_order(self) -> None:
        first = _rename_collision(["Mi Nota.md", "mi-nota.md"])
        second = _rename_collision(["mi-nota.md", "Mi Nota.md"])
        assert first.path == second.path

    @pytest.mark.parametrize(
        "paths",
        [
            ["Mi Nota.md", "Otra Nota.md"],
            ["Mi Nota.md", "x/mi nota.md"],  # same slug, different directories
            ["a b.md", "a-b/c.md"],  # a file and a directory are two names
            ["My Folder/a.md", "My Folder/b.md"],  # one directory, two files
            ["Mi Nota.md", "Mi Nota.md"],  # the same path twice
        ],
    )
    def test_distinct_names_pass(self, paths: list[str]) -> None:
        okf.validate_foreign_renames(paths)

    def test_a_pair_no_rename_touches_is_not_this_checks_concern(self) -> None:
        # `a.md` / `A.md` is the reader's `case-collision`, raised earlier
        okf.validate_foreign_renames(["a.md", "A.md"])

    def test_a_slug_that_grows_past_the_segment_cap_is_refused(self) -> None:
        # `İ` (2 bytes) casefolds to `i` + U+0307 (3 bytes)
        name = "İ" * 100 + " x.md"
        okf.validate_foreign_path(name)  # fine as written
        with pytest.raises(okf.ForeignRefusal) as info:
            okf.validate_foreign_renames([name])
        assert info.value.code == "name-too-long"
        assert info.value.path == name

    def test_an_unrenamed_nfd_segment_is_measured_in_its_nfc_form(self) -> None:
        nfd = unicodedata.normalize("NFD", "é") * 120 + ".md"  # NFC: 2*120+3
        okf.validate_foreign_path(nfd)
        okf.validate_foreign_renames([nfd])

    def test_a_slug_exactly_at_the_segment_cap_passes(self) -> None:
        name = "x" * 250 + " b.md"  # 255 bytes as written and renamed
        assert len(name.encode()) == okf.FOREIGN_MAX_SEGMENT_BYTES
        okf.validate_foreign_renames([name])

    def test_a_renamed_path_past_the_path_cap_is_refused(self) -> None:
        # each segment stays under the segment cap renamed (242 bytes), but
        # four of them plus the namespace reserve pass the 1024-byte path cap
        segment = "İ" * 80 + " a"
        path = "/".join([segment] * 4) + "/x.md"
        okf.validate_foreign_path(path)  # fine as written
        with pytest.raises(okf.ForeignRefusal) as info:
            okf.validate_foreign_renames([path])
        assert (info.value.code, info.value.path) == ("name-too-long", path)

    def test_a_renamed_path_exactly_at_the_path_cap_passes(self) -> None:
        budget = okf.FOREIGN_MAX_PATH_BYTES - okf.FOREIGN_NAMESPACE_RESERVE_BYTES
        segment = "x" * 200 + " a"  # 202 bytes, unchanged in length by the rename
        tail = budget - 4 * (len(segment) + 1)
        path = "/".join([segment] * 4) + "/" + "y" * tail
        assert len(path.encode()) == budget
        okf.validate_foreign_renames([path])


def _write(root: Path, rel: str, text: str = "---\ntype: Concept\n---\nbody\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestReader:
    def test_a_spaced_name_is_read_as_written(self, tmp_path: Path) -> None:
        _write(tmp_path, "My Folder/Mi Nota.md")
        document = okf.read_foreign_bundle(tmp_path).documents[0]
        assert document.path == "My Folder/Mi Nota.md"
        assert document.foreign_id == "My Folder/Mi Nota"

    @pytest.mark.cross_platform_smoke
    def test_a_rename_collision_refuses_the_whole_tree(self, tmp_path: Path) -> None:
        _write(tmp_path, "Mi Nota.md")
        _write(tmp_path, "mi-nota.md")
        with pytest.raises(okf.ForeignRefusal) as info:
            okf.read_foreign_bundle(tmp_path)
        assert info.value.code == "rename-collision"
        assert "Mi Nota.md" in info.value.path
        assert "mi-nota.md" in info.value.path
        assert str(tmp_path) not in str(info.value)


# --- adoption ----------------------------------------------------------------


def _doc(
    mapping: Mapping[str, object], foreign_id: str = "My Folder/Mi Nota"
) -> okf.ForeignDocument:
    return okf.ForeignDocument(
        path=f"{foreign_id}.md",
        foreign_id=foreign_id,
        doc_type=str(mapping.get("type", "Concept")),
        mapping=dict(mapping),
        body="# Body\n",
        sha256=SHA,
    )


def _imported(doc: okf.ForeignDocument) -> dict[str, object]:
    text = okf.adopt_foreign_document(
        doc, body=doc.body, sensitivity="private", anchor_id=ANCHOR, prefix=PREFIX
    )
    meta, _ = okf.load_frontmatter(text)
    block = meta[okf.IMPORTED_KEY]
    assert isinstance(block, dict)
    return block


class TestAdoption:
    def test_a_renamed_document_keeps_its_original_path(self) -> None:
        block = _imported(_doc({"type": "Concept"}))
        assert block["path"] == "My Folder/Mi Nota.md"
        assert block["id"] == "My Folder/Mi Nota"  # the foreign id is unchanged

    def test_an_unrenamed_document_records_no_path(self) -> None:
        block = _imported(_doc({"type": "Concept"}, "concepts/plain"))
        assert "path" not in block

    def test_the_imported_block_has_a_closed_set_of_keys(self) -> None:
        block = _imported(_doc({"type": "Concept", "status": "stable"}))
        assert set(block) == {"namespace", "id", "sha256", "path", "frontmatter"}

    def test_a_relation_to_a_spaced_name_follows_the_rename(self) -> None:
        doc = _doc(
            {
                "type": "Concept",
                "relations": [{"target": "My Folder/Mi Nota", "type": "see-also"}],
            },
            "other",
        )
        text = okf.adopt_foreign_document(
            doc, body=doc.body, sensitivity="private", anchor_id=ANCHOR, prefix=PREFIX
        )
        relations = okf.decode_relations(okf.load_frontmatter(text)[0])
        assert [r.target for r in relations] == ["imports/demo/my-folder/mi-nota"]

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            ("/My Folder/Mi Nota.md", "imports/demo/my-folder/mi-nota"),
            ("Mi Nota", "imports/demo/mi-nota"),
            ("/keep/Mi Nota", "imports/demo/keep/mi-nota"),
            ("/a b/../c d.md", "imports/demo/c-d"),
        ],
    )
    def test_the_namespaced_id_renames_each_spaced_segment(
        self, target: str, expected: str
    ) -> None:
        assert okf.namespaced_concept_id(target, PREFIX) == expected


class TestAnchorListsTheRenamedName:
    def test_the_link_uses_the_slug_and_the_text_keeps_the_foreign_id(self) -> None:
        text = okf.build_import_anchor(
            namespace="demo",
            label="private",
            entries=[okf.AnchorEntry("My Folder/Mi Nota", "Mi Nota", SHA)],
            bundle_sha256="1" * 64,
            okf_version=None,
            generated=okf.Generated("openkos/x", "2026-10-06T09:00:00Z"),
        )
        assert "[Mi Nota](/imports/demo/my-folder/mi-nota.md)" in text
        assert "foreign id `My Folder/Mi Nota`" in text
