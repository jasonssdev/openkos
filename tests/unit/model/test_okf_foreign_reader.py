"""Unit tests for `model/okf.py`'s bounded foreign-bundle reader (okf-import,
slice 1, design D1).

Every hostile-input reason code has its OWN test asserting the exact `code`,
the foreign-relative path, and that no absolute path leaks into the message
(a mutation of that one guard turns exactly that test red). The collision and
path validators are pure functions over strings, so they behave the same on
every filesystem; only the on-disk NFD variant is gated on a runtime probe.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import os
import stat
import threading
import unicodedata
from pathlib import Path

import frontmatter
import pytest

from openkos import fsio
from openkos.model import okf

# --- 1.1 `split_incoming_document` ------------------------------------------


class TestSplitIncomingDocument:
    def test_body_excludes_exactly_the_judged_block(self) -> None:
        incoming, body = okf.split_incoming_document(
            "---\ntype: Concept\ntitle: T\n---\n# Heading\n\ntext\n"
        )
        assert incoming.status == "parsed"
        assert incoming.mapping == {"type": "Concept", "title": "T"}
        assert body == "# Heading\n\ntext\n"

    def test_second_fence_inside_the_body_stays_in_the_body(self) -> None:
        _, body = okf.split_incoming_document("---\ntype: A\n---\nbody\n---\nmore\n")
        assert body == "body\n---\nmore\n"

    def test_a_single_leading_bom_is_stripped(self) -> None:
        incoming, body = okf.split_incoming_document("﻿---\ntype: A\n---\nb\n")
        assert incoming.status == "parsed"
        assert body == "b\n"

    def test_only_one_bom_is_stripped(self) -> None:
        incoming, _ = okf.split_incoming_document("﻿﻿---\ntype: A\n---\nb\n")
        assert incoming.status == "absent"

    @pytest.mark.parametrize(
        ("text", "status"),
        [
            pytest.param("# just a body\n", "absent", id="no-fence"),
            pytest.param("---\n---\nbody\n", "empty", id="empty-block"),
            pytest.param(
                "---\ntype: A\nbody never closes\n", "absent", id="unterminated"
            ),
            pytest.param('+++\ntype = "A"\n+++\nbody\n', "absent", id="toml-fence"),
            pytest.param('{"type": "A"}\n\nbody\n', "absent", id="json-block"),
        ],
    )
    def test_guarded_statuses_never_raise(self, text: str, status: str) -> None:
        incoming, body = okf.split_incoming_document(text)
        assert incoming.status == status
        assert incoming.mapping is None
        if status == "absent":
            assert body == text


def _refusal(path: str, **kwargs: int) -> okf.ForeignRefusal:
    with pytest.raises(okf.ForeignRefusal) as info:
        okf.validate_foreign_path(path, **kwargs)
    return info.value


# --- 1.3 the pure path validator --------------------------------------------


class TestValidateForeignPath:
    def test_a_clean_nested_path_passes(self) -> None:
        okf.validate_foreign_path("concepts/skills/python.md")

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("../x.md", id="dotdot-first"),
            pytest.param("a/../x.md", id="dotdot-middle"),
            pytest.param("a/./x.md", id="dot-component"),
            pytest.param("/abs/x.md", id="absolute"),
            pytest.param("a//x.md", id="empty-component"),
            pytest.param("", id="empty"),
        ],
    )
    def test_traversal_shapes_are_traversal(self, path: str) -> None:
        err = _refusal(path)
        assert err.code == "traversal"
        assert err.path == path

    def test_dotdot_alone_is_traversal(self) -> None:
        assert _refusal("a/..").code == "traversal"

    @pytest.mark.parametrize(
        "char",
        [*'\\:[]()<>#%?*"|', "\x00", "\x07", "\n", "\t", "\x7f"],
        ids=lambda c: f"U+{ord(c):04X}",
    )
    def test_each_denied_character_is_unsafe_name(self, char: str) -> None:
        path = f"a/b{char}c.md"
        err = _refusal(path)
        assert err.code == "unsafe-name"
        assert err.path == path

    @pytest.mark.parametrize("name", [" lead.md", "trail.md ", "x /y.md"])
    def test_leading_or_trailing_whitespace_is_unsafe_name(self, name: str) -> None:
        assert _refusal(name).code == "unsafe-name"

    def test_inner_space_is_fine(self) -> None:
        okf.validate_foreign_path("my notes/a b.md")

    def test_segment_of_255_bytes_passes_and_256_refuses(self) -> None:
        okf.validate_foreign_path("a" * 252 + ".md")
        err = _refusal("a" * 253 + ".md")
        assert err.code == "name-too-long"

    def test_segment_length_is_in_utf8_bytes_not_characters(self) -> None:
        # 85 three-byte characters = 255 bytes (+3 for ".md" would exceed)
        okf.validate_foreign_path("€" * 84 + ".md")  # 252 + 3 = 255
        assert _refusal("€" * 85 + ".md").code == "name-too-long"

    def test_namespaced_path_of_1024_bytes_passes_and_1025_refuses(self) -> None:
        # reserve 0 isolates the cap: five 200-byte directories + a leaf.
        segment = "d" * 200
        rest = 1024 - 5 * 201  # bytes left for the leaf segment, minus nothing
        leaf = "f" * (rest - 3) + ".md"
        path = "/".join([segment] * 5 + [leaf])
        assert len(path.encode()) == 1024
        okf.validate_foreign_path(path, reserve_bytes=0)
        assert _refusal(path + "x", reserve_bytes=0).code == "name-too-long"

    def test_the_default_reserve_is_the_longest_namespace_prefix(self) -> None:
        assert len("imports/") + 64 + 1 == okf.FOREIGN_NAMESPACE_RESERVE_BYTES
        segment = "d" * 200
        leaf_budget = 1024 - okf.FOREIGN_NAMESPACE_RESERVE_BYTES - 4 * 201
        path = "/".join([segment] * 4 + ["f" * (leaf_budget - 3) + ".md"])
        okf.validate_foreign_path(path)
        assert _refusal(path + "x").code == "name-too-long"

    def test_non_nfc_length_is_measured_in_the_written_nfc_form(self) -> None:
        nfd = unicodedata.normalize("NFD", "é") * 120 + ".md"  # NFC: 2*120+3
        okf.validate_foreign_path(nfd)


# --- 1.6 the pure collision validator ---------------------------------------

_NFC_E = "café.md"
_NFD_E = unicodedata.normalize("NFD", _NFC_E)


def _collision(paths: list[str]) -> okf.ForeignRefusal:
    with pytest.raises(okf.ForeignRefusal) as info:
        okf.validate_foreign_collisions(paths)
    return info.value


class TestValidateForeignCollisions:
    def test_distinct_paths_pass(self) -> None:
        okf.validate_foreign_collisions(["a/b.md", "a/c.md", "d/b.md"])

    def test_identical_nfc_names_are_not_a_collision(self) -> None:
        okf.validate_foreign_collisions([_NFC_E, "x/" + _NFC_E])

    def test_the_same_path_twice_is_not_a_collision(self) -> None:
        okf.validate_foreign_collisions(["a/b.md", "a/b.md"])

    def test_nfc_and_nfd_spellings_are_an_nfc_collision(self) -> None:
        assert _NFC_E != _NFD_E
        err = _collision([_NFC_E, _NFD_E])
        assert err.code == "nfc-collision"
        assert err.path in (_NFC_E, _NFD_E)

    def test_nfd_directory_prefix_collides_with_nfc_directory(self) -> None:
        nfd_dir = unicodedata.normalize("NFD", "café")
        err = _collision(["café/a.md", f"{nfd_dir}/b.md"])
        assert err.code == "nfc-collision"

    def test_case_variants_are_a_case_collision(self) -> None:
        err = _collision(["Concepts/Foo.md", "concepts/foo.md"])
        assert err.code == "case-collision"
        # the first colliding name is the directory prefix, a relative path
        assert err.path in (
            "concepts",
            "Concepts",
            "Concepts/Foo.md",
            "concepts/foo.md",
        )

    def test_a_file_against_a_directory_prefix_is_a_case_collision(self) -> None:
        assert _collision(["a/b.md", "A/c.md"]).code == "case-collision"

    def test_a_directory_prefix_against_a_file_name_is_a_case_collision(self) -> None:
        assert _collision(["Notes", "notes/x.md"]).code == "case-collision"

    def test_the_two_codes_are_distinct(self) -> None:
        assert _collision([_NFC_E, _NFD_E]).code != _collision(["A.md", "a.md"]).code

    def test_casefold_applies_after_nfc(self) -> None:
        upper_nfd = unicodedata.normalize("NFD", "CAFÉ.md")
        # lower NFC vs upper NFD: equal only after NFC AND casefold.
        assert _collision([_NFC_E, upper_nfd]).code == "case-collision"

    def test_casefold_catches_what_lower_would_miss(self) -> None:
        assert _collision(["straße.md", "STRASSE.md"]).code == "case-collision"


# --- tree fixtures -----------------------------------------------------------


def _doc(type_: str = "Concept", *, size: int | None = None) -> bytes:
    """A valid foreign document; padded to exactly `size` bytes when given."""
    text = f"---\ntype: {type_}\ntitle: T\n---\nbody\n"
    data = text.encode()
    if size is not None:
        assert size >= len(data), (size, len(data))
        data = data[:-1] + b"x" * (size - len(data)) + b"\n"
    return data


def _write(root: Path, rel: str, data: bytes | str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A one-document-valid baseline foreign tree."""
    root = tmp_path / "foreign-src"
    _write(root, "concepts/ok.md", _doc())
    return root


def _refuses(root: Path, code: str, path: str) -> okf.ForeignRefusal:
    with pytest.raises(okf.ForeignRefusal) as info:
        okf.read_foreign_bundle(root)
    err = info.value
    assert err.code == code, f"{err.code!r} != {code!r} ({err})"
    assert err.path == path
    assert str(root) not in str(err), "an absolute path leaked into the message"
    assert str(root.parent) not in str(err)
    return err


def _fs_distinguishes(where: Path, first: str, second: str) -> bool:
    """Whether this filesystem keeps two names as two distinct files."""
    probe = where / "probe-names"
    probe.mkdir(exist_ok=True)
    (probe / first).write_text("1")
    (probe / second).write_text("2")
    return len(list(probe.iterdir())) == 2


def _refuses_any(root: Path, code: str) -> okf.ForeignRefusal:
    with pytest.raises(okf.ForeignRefusal) as info:
        okf.read_foreign_bundle(root)
    assert info.value.code == code
    return info.value


def _skipped(bundle: okf.ForeignBundle) -> dict[str, str]:
    return dict(bundle.skipped)


def _ids(bundle: okf.ForeignBundle) -> list[str]:
    return [doc.foreign_id for doc in bundle.documents]


def test_a_baseline_tree_reads_one_document(tree: Path) -> None:
    bundle = okf.read_foreign_bundle(tree)
    assert _ids(bundle) == ["concepts/ok"]
    doc = bundle.documents[0]
    assert doc.path == "concepts/ok.md"
    assert doc.doc_type == "Concept"
    assert doc.mapping == {"type": "Concept", "title": "T"}
    assert doc.body == "body\n"
    assert bundle.skipped == ()


def test_a_missing_or_non_directory_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(okf.ForeignRefusal) as missing:
        okf.read_foreign_bundle(tmp_path / "nope")
    assert missing.value.code == "unreadable"
    file = _write(tmp_path, "f.md", _doc())
    with pytest.raises(okf.ForeignRefusal) as notdir:
        okf.read_foreign_bundle(file)
    assert notdir.value.code == "unreadable"
    assert str(tmp_path) not in str(notdir.value)


# --- 1.9 refuse class: tree hazards -----------------------------------------


class TestTreeRefusals:
    def test_symlink_to_a_file(self, tree: Path, tmp_path: Path) -> None:
        outside = _write(tmp_path, "outside.md", _doc())
        (tree / "concepts" / "link.md").symlink_to(outside)
        _refuses(tree, "symlink", "concepts/link.md")

    def test_symlink_to_a_directory(self, tree: Path, tmp_path: Path) -> None:
        (tmp_path / "outdir").mkdir()
        (tree / "dirlink").symlink_to(tmp_path / "outdir", target_is_directory=True)
        _refuses(tree, "symlink", "dirlink")

    def test_symlink_to_a_target_inside_the_tree(self, tree: Path) -> None:
        (tree / "alias.md").symlink_to(tree / "concepts" / "ok.md")
        _refuses(tree, "symlink", "alias.md")

    def test_a_dangling_symlink_is_a_symlink(self, tree: Path) -> None:
        (tree / "dangling.md").symlink_to(tree / "missing.md")
        _refuses(tree, "symlink", "dangling.md")

    def test_a_symlinked_root_is_read_through(self, tree: Path, tmp_path: Path) -> None:
        # Reaching the root through a link is ordinary use (fsio contract).
        alias = tmp_path / "root-alias"
        alias.symlink_to(tree, target_is_directory=True)
        assert _ids(okf.read_foreign_bundle(alias)) == ["concepts/ok"]

    def test_special_file_is_refused_without_hanging(self, tree: Path) -> None:
        os.mkfifo(tree / "concepts" / "pipe.md")
        outcome: list[BaseException | None] = []

        def run() -> None:
            try:
                okf.read_foreign_bundle(tree)
                outcome.append(None)
            except BaseException as exc:  # noqa: BLE001 -- relayed to the assertion below
                outcome.append(exc)

        _bounded(run)
        assert isinstance(outcome[0], okf.ForeignRefusal)
        assert outcome[0].code == "special-file"
        assert outcome[0].path == "concepts/pipe.md"

    def test_a_non_markdown_fifo_is_still_a_special_file(self, tree: Path) -> None:
        """Rule 2 precedes the `.md` rules: a FIFO is refused whatever its name,
        not skipped as `not-markdown`, and is never opened."""
        os.mkfifo(tree / "concepts" / "pipe")
        outcome: list[BaseException | None] = []

        def run() -> None:
            try:
                okf.read_foreign_bundle(tree)
                outcome.append(None)
            except BaseException as exc:  # noqa: BLE001 -- relayed to the assertion below
                outcome.append(exc)

        _bounded(run)
        assert isinstance(outcome[0], okf.ForeignRefusal)
        assert outcome[0].code == "special-file"
        assert outcome[0].path == "concepts/pipe"

    def test_depth_32_passes_and_33_refuses(self, tmp_path: Path) -> None:
        ok = tmp_path / "ok"
        _write(ok, "/".join(["d"] * 32) + "/leaf.md", _doc())
        assert _ids(okf.read_foreign_bundle(ok)) == ["/".join(["d"] * 32) + "/leaf"]
        deep = tmp_path / "deep"
        _write(deep, "/".join(["d"] * 33) + "/leaf.md", _doc())
        _refuses(deep, "too-deep", "/".join(["d"] * 33))

    def test_too_many_entries(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(okf, "FOREIGN_MAX_ENTRIES", 3)
        _write(tree, "concepts/b.md", _doc())  # concepts/, ok.md, b.md == 3
        assert len(okf.read_foreign_bundle(tree).documents) == 2
        _write(tree, "concepts/z.md", _doc())  # 4th entry, in sorted order
        _refuses(tree, "too-many-entries", "concepts/z.md")

    def test_dot_entries_count_toward_the_entry_cap(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(okf, "FOREIGN_MAX_ENTRIES", 2)
        assert len(okf.read_foreign_bundle(tree).documents) == 1  # 2 entries
        _write(tree, ".hidden", b"x")  # a skipped entry still counts
        _refuses(tree, "too-many-entries", "concepts/ok.md")

    def test_too_many_files(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(okf, "FOREIGN_MAX_DOCUMENTS", 2)
        _write(tree, "concepts/b.md", _doc())
        assert len(okf.read_foreign_bundle(tree).documents) == 2
        _write(tree, "concepts/z.md", _doc())
        _refuses(tree, "too-many-files", "concepts/z.md")

    def test_reserved_files_do_not_count_as_documents(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(okf, "FOREIGN_MAX_DOCUMENTS", 1)
        _write(tree, "index.md", b"x")
        _write(tree, "log.md", b"x")
        assert len(okf.read_foreign_bundle(tree).documents) == 1

    def test_file_too_large(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(okf, "FOREIGN_MAX_FILE_BYTES", 100)
        _write(tree, "concepts/ok.md", _doc(size=100))
        assert len(okf.read_foreign_bundle(tree).documents) == 1
        _write(tree, "concepts/ok.md", _doc(size=101))
        _refuses(tree, "file-too-large", "concepts/ok.md")

    def test_file_too_large_is_decided_by_fstat_before_reading(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(okf, "FOREIGN_MAX_FILE_BYTES", 100)
        _write(tree, "concepts/ok.md", _doc(size=101))
        reads: list[int] = []
        real_read = os.read

        def spy(fd: int, n: int) -> bytes:
            reads.append(n)
            return real_read(fd, n)

        monkeypatch.setattr(os, "read", spy)
        _refuses(tree, "file-too-large", "concepts/ok.md")
        assert reads == [], "the size was not decided before reading"

    def test_file_too_large_is_enforced_by_the_bounded_read_alone(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file that grows after `fstat` still cannot exceed cap + 1 bytes."""
        monkeypatch.setattr(okf, "FOREIGN_MAX_FILE_BYTES", 100)
        _write(tree, "concepts/ok.md", _doc(size=101))
        real_fstat = os.fstat
        patched: list[bool] = []

        def lying_fstat(fd: int) -> os.stat_result:
            real = real_fstat(fd)
            patched.append(True)
            fields = list(real)
            fields[stat.ST_SIZE] = 0
            return os.stat_result(fields)

        monkeypatch.setattr(os, "fstat", lying_fstat)
        _refuses(tree, "file-too-large", "concepts/ok.md")
        assert patched, "the fstat patch was never hit"

    def test_the_bounded_read_never_reads_past_cap_plus_one(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(okf, "FOREIGN_MAX_FILE_BYTES", 100)
        _write(tree, "concepts/ok.md", _doc(size=500))
        real_fstat = os.fstat
        monkeypatch.setattr(
            os,
            "fstat",
            lambda fd: os.stat_result(
                [0 if i == stat.ST_SIZE else v for i, v in enumerate(real_fstat(fd))]
            ),
        )
        real_read = os.read
        total: list[int] = []

        def spy(fd: int, n: int) -> bytes:
            data = real_read(fd, n)
            total.append(len(data))
            return data

        monkeypatch.setattr(os, "read", spy)
        _refuses(tree, "file-too-large", "concepts/ok.md")
        assert sum(total) == 101

    def test_bundle_too_large(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(okf, "FOREIGN_MAX_TOTAL_BYTES", 90)
        _write(tree, "concepts/ok.md", _doc(size=45))
        _write(tree, "concepts/two.md", _doc(size=45))
        assert len(okf.read_foreign_bundle(tree).documents) == 2
        _write(tree, "concepts/two.md", _doc(size=46))
        _refuses(tree, "bundle-too-large", "concepts/two.md")

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
    def test_unreadable_file(self, tree: Path) -> None:
        path = _write(tree, "concepts/secret.md", _doc())
        path.chmod(0)
        try:
            _refuses(tree, "unreadable", "concepts/secret.md")
        finally:
            path.chmod(0o644)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
    def test_unreadable_directory(self, tree: Path) -> None:
        sub = tree / "locked"
        _write(tree, "locked/a.md", _doc())
        sub.chmod(0)
        try:
            _refuses(tree, "unreadable", "locked")
        finally:
            sub.chmod(0o755)

    def test_unsafe_name_end_to_end(self, tree: Path) -> None:
        _write(tree, "concepts/bad[name].md", _doc())
        _refuses(tree, "unsafe-name", "concepts/bad[name].md")

    def test_segment_too_long_end_to_end(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(okf, "FOREIGN_MAX_SEGMENT_BYTES", 20)
        _write(tree, "concepts/" + "a" * 17 + ".md", _doc())  # 20 bytes
        assert len(okf.read_foreign_bundle(tree).documents) == 2
        _write(tree, "concepts/" + "b" * 18 + ".md", _doc())  # 21 bytes
        _refuses(tree, "name-too-long", "concepts/" + "b" * 18 + ".md")

    def test_namespaced_path_too_long_end_to_end(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        budget = 130 - okf.FOREIGN_NAMESPACE_RESERVE_BYTES
        monkeypatch.setattr(okf, "FOREIGN_MAX_PATH_BYTES", 130)
        name = "c/" + "a" * (budget - 2 - 3) + ".md"
        assert len(name) == budget
        _write(tree, name, _doc())
        assert len(okf.read_foreign_bundle(tree).documents) == 2
        _write(tree, name[:-3] + "x.md", _doc())
        _refuses(tree, "name-too-long", name[:-3] + "x.md")

    def test_a_case_collision_on_disk(self, tree: Path) -> None:
        _write(tree, "Concepts/Foo.md", _doc())
        _write(tree, "concepts/foo.md", _doc())
        if not _fs_distinguishes(tree.parent, "P.md", "p.md"):
            pytest.skip("case-insensitive filesystem; the string-level tests cover it")
        err = _refuses_any(tree, "case-collision")
        assert str(tree) not in str(err)

    def test_an_nfd_collision_on_disk(self, tree: Path) -> None:
        nfc = "caf\u00e9.md"
        _write(tree, "n/" + nfc, _doc())
        _write(tree, "n/" + unicodedata.normalize("NFD", nfc), _doc())
        if not _fs_distinguishes(tree.parent, nfc, unicodedata.normalize("NFD", nfc)):
            pytest.skip("APFS normalizes names; Linux CI is the first real run")
        _refuses_any(tree, "nfc-collision")

    def test_collision_is_refused_before_any_file_is_read(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two names equal under NFC, built as paths in a pure walk fixture.
        monkeypatch.setattr(
            okf,
            "validate_foreign_collisions",
            lambda paths: (_ for _ in ()).throw(
                okf.ForeignRefusal("case-collision", "x")
            ),
        )
        monkeypatch.setattr(
            os, "read", lambda *a: pytest.fail("a file was read before collisions")
        )
        with pytest.raises(okf.ForeignRefusal) as info:
            okf.read_foreign_bundle(tree)
        assert info.value.code == "case-collision"


def _bounded(fn: object, timeout: float = 10.0) -> None:
    """Run `fn` on a daemon thread; fail (never hang) when it does not return."""
    assert callable(fn)
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "the reader hung (a FIFO was opened blocking)"


# --- 1.10 refuse class: frontmatter hazards ---------------------------------


def _fm(block: str) -> bytes:
    return f"---\n{block}\n---\nbody\n".encode()


class TestFrontmatterRefusals:
    def test_alias_is_refused_without_expanding(self, tree: Path) -> None:
        _write(tree, "concepts/bomb.md", _fm("type: A\na: &x [1, 2]\nb: *x"))
        _refuses(tree, "frontmatter-alias", "concepts/bomb.md")

    def test_a_lone_anchor_is_refused_too(self, tree: Path) -> None:
        _write(tree, "concepts/anchor.md", _fm("type: A\na: &x 1"))
        _refuses(tree, "frontmatter-alias", "concepts/anchor.md")

    def test_too_deep(self, tree: Path) -> None:
        _write(tree, "concepts/deep.md", _fm("type: A\nx: " + "[" * 33 + "]" * 33))
        _refuses(tree, "frontmatter-too-deep", "concepts/deep.md")

    def test_too_large(self, tree: Path) -> None:
        _write(tree, "concepts/big.md", _fm("type: A\nx: " + "a" * 70_000))
        _refuses(tree, "frontmatter-too-large", "concepts/big.md")

    @pytest.mark.parametrize(
        "block", ["- a\n- b", "just a scalar"], ids=["list", "scalar"]
    )
    def test_non_mapping_root(self, tree: Path, block: str) -> None:
        _write(tree, "concepts/odd.md", _fm(block))
        _refuses(tree, "frontmatter-not-a-mapping", "concepts/odd.md")

    def test_unsupported_value(self, tree: Path) -> None:
        _write(tree, "concepts/set.md", _fm("type: A\nx: !!set\n  ? a"))
        _refuses(tree, "frontmatter-unsupported-value", "concepts/set.md")

    def test_a_hostile_document_refuses_even_beside_good_ones(self, tree: Path) -> None:
        _write(tree, "concepts/aaa.md", _doc())
        _write(tree, "concepts/zzz.md", _fm("type: A\nx: &x 1\ny: *x"))
        _refuses(tree, "frontmatter-alias", "concepts/zzz.md")


# --- 1.11 skip class ---------------------------------------------------------


class TestSkips:
    def test_dot_entries_are_skipped_and_never_descended(
        self, tree: Path, tmp_path: Path
    ) -> None:
        _write(tree, ".hidden.md", _doc())
        _write(tree, ".git/objects/x.md", _doc())
        # A symlink INSIDE a skipped directory is never seen: no descent.
        (tree / ".git" / "evil").symlink_to(tmp_path)
        _write(tree, ".obsidian/workspace.md", _doc())
        bundle = okf.read_foreign_bundle(tree)
        assert _ids(bundle) == ["concepts/ok"]
        skipped = _skipped(bundle)
        assert skipped[".hidden.md"] == "dot-entry"
        assert skipped[".git"] == "dot-entry"
        assert skipped[".obsidian"] == "dot-entry"
        assert not any(path.startswith((".git/", ".obsidian/")) for path in skipped)

    def test_a_dot_git_file_is_skipped_too(self, tree: Path) -> None:
        _write(tree, "sub/.git", "gitdir: ../elsewhere\n")
        assert _skipped(okf.read_foreign_bundle(tree))["sub/.git"] == "dot-entry"

    def test_non_markdown_files_are_skipped_and_never_opened(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("README.sh", "viz.html", "page.mdx", "references/data.csv"):
            _write(tree, name, b"\x00\x01 not text")
        opened: list[str] = []
        real_open = os.open

        def spy(path: str | Path, *args: int, **kwargs: int) -> int:
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(os, "open", spy)
        bundle = okf.read_foreign_bundle(tree)
        assert opened, "the open spy was never hit"
        assert not any(
            o.endswith(("README.sh", "viz.html", "page.mdx", ".csv")) for o in opened
        )
        skipped = _skipped(bundle)
        for name in ("README.sh", "viz.html", "page.mdx", "references/data.csv"):
            assert skipped[name] == "not-markdown"
        assert _ids(bundle) == ["concepts/ok"]

    def test_an_uppercase_md_extension_is_not_markdown(self, tree: Path) -> None:
        _write(tree, "concepts/Shout.MD", _doc())
        assert (
            _skipped(okf.read_foreign_bundle(tree))["concepts/Shout.MD"]
            == "not-markdown"
        )

    def test_an_executable_md_is_adopted_as_text(self, tree: Path) -> None:
        path = _write(tree, "concepts/run.md", _doc())
        path.chmod(0o755)
        assert "concepts/run" in _ids(okf.read_foreign_bundle(tree))

    def test_reserved_files_at_any_depth_are_skipped(self, tree: Path) -> None:
        _write(tree, "index.md", b"# root index, no frontmatter\n")
        _write(tree, "log.md", b"x")
        _write(tree, "a/INDEX.md", b"x")
        _write(tree, "b/Log.MD", b"x")
        _write(tree, "c/d/index.md", b"x")
        bundle = okf.read_foreign_bundle(tree)
        assert _ids(bundle) == ["concepts/ok"]
        skipped = _skipped(bundle)
        for name in ("index.md", "log.md", "a/INDEX.md", "b/Log.MD", "c/d/index.md"):
            assert skipped[name] == "reserved-file", name

    def test_not_utf8_is_skipped(self, tree: Path) -> None:
        _write(tree, "concepts/latin.md", b"---\ntype: A\n---\ncaf\xe9\n")
        bundle = okf.read_foreign_bundle(tree)
        assert _skipped(bundle)["concepts/latin.md"] == "not-utf8"
        assert _ids(bundle) == ["concepts/ok"]

    def test_frontmatter_absent(self, tree: Path) -> None:
        _write(tree, "concepts/bare.md", b"# no frontmatter\n")
        assert (
            _skipped(okf.read_foreign_bundle(tree))["concepts/bare.md"]
            == "frontmatter-absent"
        )

    def test_frontmatter_empty(self, tree: Path) -> None:
        _write(tree, "concepts/empty.md", b"---\n---\nbody\n")
        assert (
            _skipped(okf.read_foreign_bundle(tree))["concepts/empty.md"]
            == "frontmatter-empty"
        )

    def test_malformed_yaml_is_skipped_never_fatal(self, tree: Path) -> None:
        _write(tree, "concepts/broken.md", _fm("type: [unclosed"))
        bundle = okf.read_foreign_bundle(tree)
        assert _skipped(bundle)["concepts/broken.md"] == "frontmatter-malformed"
        assert _ids(bundle) == ["concepts/ok"]

    @pytest.mark.parametrize(
        "block",
        ["title: no type", 'type: ""', "type: 3", "type: [a]", "type:", 'type: "   "'],
        ids=["missing", "empty", "int", "list", "null", "blank"],
    )
    def test_missing_type(self, tree: Path, block: str) -> None:
        _write(tree, "concepts/typeless.md", _fm(block))
        bundle = okf.read_foreign_bundle(tree)
        assert _skipped(bundle)["concepts/typeless.md"] == "missing-type"
        assert _ids(bundle) == ["concepts/ok"]

    def test_an_unknown_type_is_adopted_verbatim(self, tree: Path) -> None:
        _write(tree, "concepts/r.md", _doc("Recipe"))
        _write(tree, "concepts/s.md", _doc("Two Words"))
        types = {
            d.foreign_id: d.doc_type for d in okf.read_foreign_bundle(tree).documents
        }
        assert types["concepts/r"] == "Recipe"
        assert types["concepts/s"] == "Two Words"


# --- 1.12 reader invariants --------------------------------------------------


class TestInvariants:
    def test_results_are_sorted_and_deterministic(self, tmp_path: Path) -> None:
        root = tmp_path / "src"
        for name in ("z.md", "m/b.md", "a.md", "m/a.md", "B.md"):
            _write(root, name, _doc())
        _write(root, "viz.html", b"x")
        _write(root, ".dot", b"x")
        _write(root, "m/notes.sh", b"x")  # a nested skip sorts before `viz.html`
        first = okf.read_foreign_bundle(root)
        assert [d.path for d in first.documents] == sorted(
            d.path for d in first.documents
        )
        assert list(first.skipped) == sorted(first.skipped)
        assert list(first.manifest) == sorted(first.manifest)
        assert first == okf.read_foreign_bundle(root)

    def test_the_manifest_digests_the_bytes_as_read(self, tree: Path) -> None:
        raw = b"\xef\xbb\xbf" + _doc()
        _write(tree, "concepts/bom.md", raw)
        _write(tree, "concepts/bad.md", b"\xff\xfe")
        _write(tree, "viz.html", b"x")
        _write(tree, "index.md", b"hello")
        bundle = okf.read_foreign_bundle(tree)
        manifest = dict(bundle.manifest)
        assert manifest["concepts/bom.md"] == hashlib.sha256(raw).hexdigest()
        assert manifest["concepts/bad.md"] == hashlib.sha256(b"\xff\xfe").hexdigest()
        assert manifest["index.md"] == hashlib.sha256(b"hello").hexdigest()
        assert manifest["viz.html"] == "skip:not-markdown"
        by_path = {d.path: d for d in bundle.documents}
        assert by_path["concepts/bom.md"].sha256 == hashlib.sha256(raw).hexdigest()
        assert by_path["concepts/bom.md"].body == "body\n"  # BOM stripped

    def test_a_changed_byte_changes_the_manifest(self, tree: Path) -> None:
        before = okf.read_foreign_bundle(tree).manifest
        _write(tree, "concepts/ok.md", _doc() + b"x")
        assert okf.read_foreign_bundle(tree).manifest != before

    def test_a_fifo_swapped_in_after_the_walk_cannot_hang(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tree / "concepts" / "ok.md"
        real = okf.validate_foreign_collisions
        swapped: list[bool] = []

        def swap_then_check(paths: object) -> None:
            target.unlink()
            os.mkfifo(target)
            swapped.append(True)
            real(paths)  # type: ignore[arg-type]

        monkeypatch.setattr(okf, "validate_foreign_collisions", swap_then_check)
        outcome: list[BaseException | None] = []

        def run() -> None:
            try:
                okf.read_foreign_bundle(tree)
                outcome.append(None)
            except BaseException as exc:  # noqa: BLE001 -- relayed to the assertion below
                outcome.append(exc)

        try:
            _bounded(run)
        finally:
            # unblock a reader stuck in a blocking open, if the guard is gone
            try:
                fd = os.open(target, os.O_WRONLY | os.O_NONBLOCK)
                os.close(fd)
            except OSError:
                pass
        assert swapped
        assert isinstance(outcome[0], okf.ForeignRefusal)
        assert outcome[0].code == "special-file"

    def test_a_symlink_swapped_in_after_the_walk_is_refused_by_nofollow(
        self, tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = _write(tmp_path, "outside.md", _doc())
        target = tree / "concepts" / "ok.md"
        real = okf.validate_foreign_collisions

        def swap_then_check(paths: object) -> None:
            target.unlink()
            target.symlink_to(outside)
            real(paths)  # type: ignore[arg-type]

        monkeypatch.setattr(okf, "validate_foreign_collisions", swap_then_check)
        # isolate O_NOFOLLOW from the segment check
        monkeypatch.setattr(fsio, "symlinked_segment", lambda path, boundary: None)
        _refuses(tree, "symlink", "concepts/ok.md")

    def test_a_linked_segment_found_by_fsio_is_refused(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hits: list[Path] = []

        def fake(path: Path, boundary: Path) -> Path | None:
            hits.append(path)
            return path

        monkeypatch.setattr(fsio, "symlinked_segment", fake)
        _refuses(tree, "symlink", "concepts/ok.md")
        assert hits

    def test_okf_version_known(self, tree: Path) -> None:
        _write(tree, "index.md", _fm('okf_version: "0.2"'))
        assert okf.read_foreign_bundle(tree).okf_version == "0.2"

    def test_okf_version_is_read_from_the_root_index_only(self, tree: Path) -> None:
        _write(tree, "sub/index.md", _fm('okf_version: "0.9"'))
        assert okf.read_foreign_bundle(tree).okf_version is None

    @pytest.mark.parametrize(
        ("block", "expected"),
        [
            pytest.param("title: no version", None, id="absent"),
            pytest.param('okf_version: "0.3"', "0.3", id="other-string"),
            pytest.param("okf_version: 2", 2, id="non-string"),
            pytest.param("okf_version: [0, 2]", [0, 2], id="list"),
        ],
    )
    def test_okf_version_is_returned_as_observed(
        self, tree: Path, block: str, expected: object
    ) -> None:
        _write(tree, "index.md", _fm(block))
        assert okf.read_foreign_bundle(tree).okf_version == expected

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param(b"# no frontmatter\n", id="absent"),
            pytest.param(b"---\n---\nx\n", id="empty"),
            pytest.param(_fm("okf_version: [unclosed"), id="malformed"),
            pytest.param(_fm("okf_version: &a '0.2'\nx: *a"), id="alias"),
            pytest.param(_fm("- a"), id="not-a-mapping"),
            pytest.param(b"\xff\xfe", id="not-utf8"),
        ],
    )
    def test_a_root_index_that_is_not_parsed_degrades_never_refuses(
        self, tree: Path, data: bytes
    ) -> None:
        _write(tree, "index.md", data)
        bundle = okf.read_foreign_bundle(tree)
        assert bundle.okf_version is None
        assert _ids(bundle) == ["concepts/ok"]
        assert _skipped(bundle)["index.md"] == "reserved-file"

    def test_an_oversize_root_index_still_refuses(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(okf, "FOREIGN_MAX_FILE_BYTES", 100)
        _write(tree, "concepts/ok.md", _doc(size=100))
        _write(tree, "index.md", b"x" * 101)
        _refuses(tree, "file-too-large", "index.md")

    def test_foreign_id_is_nfc_and_the_path_is_as_written(self, tree: Path) -> None:
        nfd = unicodedata.normalize("NFD", "café") + ".md"
        _write(tree, nfd, _doc())
        if not any(p.name == nfd for p in tree.iterdir()):
            pytest.skip("the filesystem normalizes names (APFS); first real run is CI")
        doc = next(
            d for d in okf.read_foreign_bundle(tree).documents if "caf" in d.path
        )
        assert doc.path == nfd
        assert doc.foreign_id == "café"


# --- 1.13 guarded-parse-only pin ---------------------------------------------


def _forbid_foreign_text(real: object) -> object:
    """Wrap a parser entry so it fails on anything but the guarded parser's own
    re-serialization of already-parsed plain data (`source_frontmatter:`)."""
    assert callable(real)

    def guard(text: str, *args: object, **kwargs: object) -> object:
        assert text.startswith("---\nsource_frontmatter:"), (
            f"an unguarded parser saw foreign bytes: {text[:40]!r}"
        )
        return real(text, *args, **kwargs)

    return guard


class TestGuardedParseOnly:
    def test_the_unguarded_loaders_never_see_foreign_bytes(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write(tree, "concepts/more.md", _fm("type: Concept\ntags: [a, b]"))
        _write(tree, "concepts/odd.md", _fm("type: [unclosed"))
        _write(tree, "index.md", _fm('okf_version: "0.2"'))
        monkeypatch.setattr(okf, "_parse_post", _forbid_foreign_text(okf._parse_post))
        monkeypatch.setattr(
            frontmatter, "loads", _forbid_foreign_text(frontmatter.loads)
        )

        # The patch is live: the unguarded `_iter_docs` over the same tree fails.
        with pytest.raises(AssertionError, match="unguarded parser saw foreign bytes"):
            list(okf._iter_docs(tree))

        bundle = okf.read_foreign_bundle(tree)
        assert _ids(bundle) == ["concepts/more", "concepts/ok"]
        assert bundle.okf_version == "0.2"

    def test_no_new_function_references_yaml(self) -> None:
        source = inspect.getsource(okf)
        module = ast.parse(source)
        names = {
            "split_incoming_document",
            "validate_foreign_path",
            "validate_foreign_collisions",
            "read_foreign_bundle",
        }
        names |= {
            node.name
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_foreign_")
        }
        found = {
            node.name: node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        }
        assert names <= set(found), names - set(found)
        for name, node in found.items():
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            assert "yaml" not in used, f"{name} reaches for yaml"
            assert not used & {"_parse_post", "load_frontmatter", "concept_metadata"}, (
                name
            )
