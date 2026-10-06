"""The import service (okf-import, slice 4, design D7): `plan_import` (Phase A,
no lock, no write) and `publish_import` (Phase B, under the lock).

Every refusal leaves the workspace byte-identical, asserted by hashing the
whole tree (directories included) before and after. Nothing here reaches a
model: the backend resolvers are patched to raise where it matters.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import os
import shutil
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from openkos import config, fsio, lock
from openkos.application import backends, import_service
from openkos.bundle import imports as bundle_imports
from openkos.bundle import index as bundle_index
from openkos.bundle import links as bundle_links
from openkos.bundle import log as bundle_log
from openkos.model import okf

NOW = datetime(2026, 10, 6, 9, 0, 0, tzinfo=UTC)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "okf_third_party_v02"
V01_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "good_life_demo_v01" / "bundle"
)
NS = "demo"

Ws = tuple[Path, config.WorkspaceLayout, config.Config]
"""What the `workspace`, `git_workspace` and `imported_sources` fixtures give."""


def rewrite_config(
    root: Path, *, default: str = "private", offsets: dict[str, int] | None = None
) -> None:
    """Rewrite `openkos.yaml` alone, leaving the bundle as it is."""
    lines = [f"default_sensitivity: {default}"]
    if offsets:
        lines.append("type_sensitivity_defaults:")
        lines.extend(f"  {name}: {offset}" for name, offset in offsets.items())
    (root / "openkos.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_workspace(
    root: Path,
    *,
    default: str = "private",
    offsets: dict[str, int] | None = None,
) -> tuple[config.WorkspaceLayout, config.Config]:
    """A minimal initialized workspace (config, `raw/`, `bundle/` with its two
    reserved files), no git repository."""
    root.mkdir(parents=True, exist_ok=True)
    rewrite_config(root, default=default, offsets=offsets)
    layout = config.WorkspaceLayout(root)
    layout.raw_dir.mkdir(exist_ok=True)
    layout.bundle_dir.mkdir(exist_ok=True)
    (layout.bundle_dir / "index.md").write_text(
        bundle_index.render_index(), encoding="utf-8"
    )
    (layout.bundle_dir / "log.md").write_text(
        bundle_log.render_log(NOW.date()), encoding="utf-8"
    )
    return layout, config.read_config(root)


def tree_state(root: Path) -> dict[str, str]:
    """Every path under `root` (outside `.git`), directories included, mapped
    to a digest of its bytes (`<dir>` for a directory)."""
    state: dict[str, str] = {}
    for current, dirs, files in os.walk(root):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in dirs:
            path = Path(current) / name
            state[path.relative_to(root).as_posix()] = (
                "<symlink>" if path.is_symlink() else "<dir>"
            )
        for name in files:
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            state[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return state


def foreign_copy(tmp_path: Path, name: str = "foreign") -> Path:
    """A copy of the third-party fixture, with the `.git` directory the fixture
    cannot carry created at run time."""
    target = tmp_path / name
    shutil.copytree(FIXTURE, target)
    (target / ".git").mkdir()
    (target / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    return target


def write_foreign(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def doc(type_: str = "Concept", **extra: object) -> str:
    return okf.dump_frontmatter({"type": type_, **extra}, "# Body\n")


def small_bundle(tmp_path: Path, **docs: str) -> Path:
    """A foreign bundle of `docs` (relative path to text), named `foreign`."""
    root = tmp_path / "foreign"
    root.mkdir()
    for rel, text in docs.items():
        write_foreign(root, rel.replace("__", "/"), text)
    return root


def plan(
    root: Path,
    source: Path,
    *,
    namespace: str = NS,
    flag: str | None = None,
    layout: config.WorkspaceLayout | None = None,
    cfg: config.Config | None = None,
) -> import_service.ImportPlan:
    layout = layout or config.WorkspaceLayout(root)
    cfg = cfg or config.read_config(root)
    return import_service.plan_import(
        root,
        layout,
        cfg,
        source,
        namespace=namespace,
        sensitivity_flag=flag,
        now=NOW,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, config.WorkspaceLayout, config.Config]:
    root = tmp_path / "ws"
    layout, cfg = make_workspace(root)
    return root, layout, cfg


def refusal_of(
    root: Path, source: Path | str, **kwargs: object
) -> import_service.ImportRefusal:
    before = tree_state(root)
    with pytest.raises(import_service.ImportRefusal) as caught:
        plan(root, Path(source), **kwargs)  # type: ignore[arg-type]
    assert tree_state(root) == before, "a refused plan changed the workspace"
    return caught.value


# --- 4.4 the Phase A refusals -------------------------------------------------


class TestInputRefusals:
    def test_a_regular_file_is_refused(self, workspace: Ws, tmp_path: Path) -> None:
        root, _, _ = workspace
        target = tmp_path / "notes.txt"
        target.write_text("x", encoding="utf-8")
        refusal = refusal_of(root, target)
        assert refusal.code == "input-not-directory"
        assert "directory" in refusal.reason
        assert refusal.retry_safe is False

    def test_an_archive_is_refused(self, workspace: Ws, tmp_path: Path) -> None:
        root, _, _ = workspace
        target = tmp_path / "bundle.zip"
        target.write_bytes(b"PK")
        refusal = refusal_of(root, target)
        assert refusal.code == "input-archive"
        assert "archive" in refusal.reason
        assert refusal.retry_safe is False

    def test_a_url_is_refused(self, workspace: Ws) -> None:
        root, _, _ = workspace
        refusal = refusal_of(root, "https://example.com/bundle")
        assert refusal.code == "input-url"
        assert "URL" in refusal.reason

    def test_a_missing_path_is_refused_without_its_absolute_form(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        refusal = refusal_of(root, tmp_path / "nowhere")
        assert refusal.code == "input-missing"
        assert str(tmp_path) not in refusal.reason

    def test_a_directory_inside_the_bundle_is_refused(self, workspace: Ws) -> None:
        root, layout, _ = workspace
        inside = layout.bundle_dir / "concepts"
        inside.mkdir()
        refusal = refusal_of(root, inside)
        assert refusal.code == "input-inside-bundle"
        assert refusal.retry_safe is False

    def test_the_bundle_itself_is_refused(self, workspace: Ws) -> None:
        root, layout, _ = workspace
        assert refusal_of(root, layout.bundle_dir).code == "input-inside-bundle"

    def test_an_ancestor_of_the_workspace_is_refused(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        refusal = refusal_of(root, tmp_path)
        assert refusal.code == "input-contains-workspace"
        assert str(tmp_path) not in refusal.reason

    def test_the_workspace_itself_is_refused(self, workspace: Ws) -> None:
        root, _, _ = workspace
        assert refusal_of(root, root).code == "input-contains-workspace"


class TestNamespaceRefusals:
    def test_an_invalid_namespace_is_refused_before_the_foreign_read(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        missing = tmp_path / "does-not-exist"
        refusal = refusal_of(root, missing, namespace="Not Valid")
        assert refusal.code == "namespace-invalid"
        assert "lowercase" in refusal.reason

    @pytest.mark.parametrize("kind", ["directory", "empty-directory", "file"])
    def test_a_taken_namespace_is_refused_however_it_is_taken(
        self, workspace: Ws, tmp_path: Path, kind: str
    ) -> None:
        root, layout, _ = workspace
        imports_dir = layout.bundle_dir / "imports"
        imports_dir.mkdir()
        taken = imports_dir / NS
        if kind == "file":
            taken.write_text("x", encoding="utf-8")
        else:
            taken.mkdir()
            if kind == "directory":
                (taken / "a.md").write_text("x", encoding="utf-8")
        refusal = refusal_of(root, foreign_copy(tmp_path))
        assert refusal.code == "namespace-exists"
        assert refusal.retry_safe is False
        assert NS in refusal.reason
        assert "re-import" in refusal.reason
        assert "git status" in refusal.reason

    def test_an_anchor_path_held_by_another_file_is_refused(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        imports_dir = layout.bundle_dir / "imports"
        imports_dir.mkdir()
        (imports_dir / f"{NS}--private.md").write_text(
            doc("Source", title="Mine"), encoding="utf-8"
        )
        refusal = refusal_of(root, foreign_copy(tmp_path))
        assert refusal.code == "anchor-path-taken"
        assert refusal.retry_safe is False
        assert f"imports/{NS}--private" in refusal.reason

    def test_an_anchor_of_another_namespace_at_the_path_is_not_ours(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        imports_dir = layout.bundle_dir / "imports"
        imports_dir.mkdir()
        other = okf.build_import_anchor(
            namespace="other",
            label="private",
            entries=[okf.AnchorEntry("a", "A", "0" * 64)],
            bundle_sha256="1" * 64,
            okf_version=None,
            generated=okf.Generated("openkos/x", "2026-10-06T09:00:00Z"),
        )
        (imports_dir / f"{NS}--private.md").write_text(other, encoding="utf-8")
        assert refusal_of(root, foreign_copy(tmp_path)).code == "anchor-path-taken"

    def test_an_undecodable_file_at_an_anchor_path_is_not_ours(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        imports_dir = layout.bundle_dir / "imports"
        imports_dir.mkdir()
        (imports_dir / f"{NS}--private.md").write_bytes(b"\xff\xfe not utf-8")
        refusal = refusal_of(root, foreign_copy(tmp_path))
        assert refusal.code == "anchor-path-taken"

    def test_a_symlink_standing_in_for_an_own_anchor_is_refused(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        imports_dir = layout.bundle_dir / "imports"
        imports_dir.mkdir()
        own = okf.build_import_anchor(
            namespace=NS,
            label="private",
            entries=[okf.AnchorEntry("a", "A", "0" * 64)],
            bundle_sha256="1" * 64,
            okf_version=None,
            generated=okf.Generated("openkos/x", "2026-10-06T09:00:00Z"),
        )
        real = tmp_path / "real-anchor.md"
        real.write_text(own, encoding="utf-8")
        try:
            (imports_dir / f"{NS}--private.md").symlink_to(real)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available here")
        assert refusal_of(root, foreign_copy(tmp_path)).code == "anchor-path-taken"

    def test_a_symlinked_imports_directory_is_refused(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        try:
            (layout.bundle_dir / "imports").symlink_to(
                elsewhere, target_is_directory=True
            )
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available here")
        refusal = refusal_of(root, foreign_copy(tmp_path))
        assert refusal.code == "symlink"
        assert refusal.retry_safe is False
        assert "symlink" in refusal.reason

    def test_an_imports_directory_under_a_symlink_is_refused(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "ws"
        make_workspace(root)
        real = tmp_path / "real-bundle"
        (root / "bundle").rename(real)
        try:
            (root / "bundle").symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available here")
        assert refusal_of(root, foreign_copy(tmp_path)).code == "symlink"

    def test_an_imports_path_that_is_a_file_is_refused(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        (layout.bundle_dir / "imports").write_text("x", encoding="utf-8")
        refusal = refusal_of(root, foreign_copy(tmp_path))
        assert refusal.code == "imports-not-directory"


class TestForeignRefusalsSurface:
    @pytest.mark.parametrize(
        ("name", "build", "code"),
        [
            ("symlink", None, "symlink"),
            (
                "alias",
                lambda root: write_foreign(
                    root, "a.md", "---\ntype: &a Concept\nalso: *a\n---\nbody\n"
                ),
                "frontmatter-alias",
            ),
        ],
    )
    def test_a_foreign_refusal_keeps_the_readers_code_and_path(
        self,
        workspace: Ws,
        tmp_path: Path,
        name: str,
        build: Callable[[Path], object] | None,
        code: str,
    ) -> None:
        root, _, _ = workspace
        foreign = small_bundle(tmp_path, **{"ok.md": doc()})
        if build is None:
            try:
                (foreign / "link.md").symlink_to(foreign / "ok.md")
            except (OSError, NotImplementedError):
                pytest.skip("symlinks are not available here")
            rel = "link.md"
        else:
            build(foreign)
            rel = "a.md"
        refusal = refusal_of(root, foreign)
        assert refusal.code == code
        assert rel in refusal.reason
        assert str(tmp_path) not in refusal.reason
        assert refusal.retry_safe is False


class TestOtherRefusals:
    def test_a_bundle_with_nothing_to_adopt_is_refused(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = small_bundle(tmp_path, **{"notype.md": "---\ntitle: T\n---\nbody\n"})
        refusal = refusal_of(root, foreign)
        assert refusal.code == "nothing-to-import"
        assert refusal.retry_safe is False


class TestPathologicalNamesCannotBeLinked:
    """Why a foreign Concept ID with whitespace is renamed rather than linked:
    an anchor link to it cannot be read by every engine link reader in ANY
    spelling, so the anchor-to-document edge would silently vanish."""

    TARGET = "imports/ns/My Note"
    FORMS: ClassVar[dict[str, str]] = {
        "plain": "/imports/ns/My Note.md",
        "angle": "</imports/ns/My Note.md>",
        "encoded": "/imports/ns/My%20Note.md",
    }

    def resolved_by_each_reader(self, form: str) -> dict[str, set[str]]:
        from tests.unit.bundle.test_link_recognizer_inventory import READERS

        text = f"- [My Note]({form}) - foreign id `My Note`\n"
        found: dict[str, set[str]] = {}
        for name, reader in READERS.items():
            found[name] = {
                resolved
                for _, resolved in reader(text, "imports/ns--private")
                if resolved is not None
            }
        return found

    @pytest.mark.parametrize("name", list(FORMS))
    def test_some_reader_loses_the_edge_in_every_spelling(self, name: str) -> None:
        found = self.resolved_by_each_reader(self.FORMS[name])
        assert any(self.TARGET not in resolved for resolved in found.values()), found

    @pytest.mark.parametrize("name", list(FORMS))
    def test_the_graph_reader_loses_the_edge_in_every_spelling(self, name: str) -> None:
        found = self.resolved_by_each_reader(self.FORMS[name])
        assert self.TARGET not in found["graph/sqlite_graph.py:_LINK_RE"]

    def test_a_plain_name_is_read_by_every_reader_so_the_check_is_not_vacuous(
        self,
    ) -> None:
        found = self.resolved_by_each_reader("/imports/ns/MyNote.md")
        graph = found["graph/sqlite_graph.py:_LINK_RE"]
        assert graph == {"imports/ns/MyNote"}


# --- 4b names with whitespace are renamed to slugs ----------------------------


class TestWhitespaceNamesAreRenamed:
    """A foreign name with whitespace is adopted under its slug (owner
    decision, #1314): every link follows, a collision refuses, and the
    original path is kept in the inert `imported` block."""

    @pytest.mark.parametrize(
        ("rel", "concept"),
        [
            ("My Note.md", "my-note"),
            ("My Folder/note.md", "my-folder/note"),
            ("a\u00a0b.md", "a-b"),
            ("Keep/Mi  Nota.md", "Keep/mi-nota"),
        ],
    )
    def test_the_document_is_planned_under_its_slug(
        self, workspace: Ws, tmp_path: Path, rel: str, concept: str
    ) -> None:
        root, _, _ = workspace
        foreign = tmp_path / "foreign"
        write_foreign(foreign, rel, doc())
        adopted = plan(root, foreign).adopted[0]
        assert adopted.concept_id == f"imports/{NS}/{concept}"
        assert adopted.foreign_id == rel.removesuffix(".md")  # the origin, as written
        block = imported_block(adopted.text)
        assert block["path"] == rel
        assert block["id"] == adopted.foreign_id

    def test_an_unspaced_document_is_not_renamed_and_records_no_path(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = small_bundle(tmp_path, **{"Plain.md": doc()})
        adopted = plan(root, foreign).adopted[0]
        assert adopted.concept_id == f"imports/{NS}/Plain"
        assert "path" not in imported_block(adopted.text)

    def test_the_preview_reports_each_rename(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = small_bundle(
            tmp_path,
            **{
                "My Folder__Mi Nota.md": doc(),
                "plain.md": doc(),
                "Other One.md": doc(),
            },
        )
        assert plan(root, foreign).renames == (
            ("My Folder/Mi Nota.md", "my-folder/mi-nota.md"),
            ("Other One.md", "other-one.md"),
        )

    def test_a_bundle_with_nothing_renamed_reports_none(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        assert plan(root, foreign_copy(tmp_path)).renames == ()

    @pytest.mark.parametrize(
        ("names", "both"),
        [
            (["Mi Nota.md", "mi-nota.md"], ("Mi Nota.md", "mi-nota.md")),
            (["Mi Nota.md", "MI  NOTA.md"], ("Mi Nota.md", "MI  NOTA.md")),
            (["My Folder/a.md", "my-folder/b.md"], ("My Folder", "my-folder")),
        ],
    )
    def test_a_collision_after_the_rename_is_refused_naming_both(
        self, workspace: Ws, tmp_path: Path, names: list[str], both: tuple[str, str]
    ) -> None:
        root, _, _ = workspace
        foreign = tmp_path / "foreign"
        for rel in names:
            write_foreign(foreign, rel, doc())
        refusal = refusal_of(root, foreign)
        assert refusal.code == "rename-collision"
        assert all(name in refusal.reason for name in both)
        assert "renamed to slugs" in refusal.reason
        assert str(tmp_path) not in refusal.reason
        assert refusal.retry_safe is False

    def test_a_document_that_cannot_be_adopted_still_counts_for_a_collision(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        # the reader judges every `.md` path, adopted or not, so a skipped file
        # cannot hide a collision
        root, _, _ = workspace
        foreign = small_bundle(
            tmp_path,
            **{"Mi Nota.md": doc(), "mi-nota.md": "---\ntitle: no type\n---\nx\n"},
        )
        assert refusal_of(root, foreign).code == "rename-collision"

    def test_a_renamed_name_cannot_land_on_a_local_document(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        # the namespace keeps every adopted name away from the local concepts
        root, layout, _ = workspace
        local = layout.bundle_dir / "mi-nota.md"
        local.write_text(doc(title="Local"), encoding="utf-8")
        foreign = small_bundle(tmp_path, **{"Mi Nota.md": doc(title="Foreign")})
        before = tree_state(root)
        import_plan = plan(root, foreign)
        assert [a.concept_id for a in import_plan.adopted] == [f"imports/{NS}/mi-nota"]
        assert tree_state(root) == before

    LINKING = (
        "[plain](/My%20Folder/Mi%20Nota.md) [angle](</My Folder/Mi Nota.md>)\n"
        "[relative](<My Folder/Mi Nota.md>) [encoded](My%20Folder/Mi%20Nota.md#s)\n"
        "Read [X][ref].\n\n[ref]: </My Folder/Mi Nota.md>\n"
    )

    def bundle_with_links(self, tmp_path: Path) -> Path:
        return small_bundle(
            tmp_path,
            **{
                "entry.md": okf.dump_frontmatter({"type": "Concept"}, self.LINKING),
                "My Folder__Mi Nota.md": doc(title="Mi Nota"),
                "My Folder__sibling.md": okf.dump_frontmatter(
                    {"type": "Concept"}, "[a](<Mi Nota.md>) [b](c.md)\n"
                ),
            },
        )

    def test_every_link_form_to_a_renamed_name_is_rewritten(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        by_id = {
            a.foreign_id: a
            for a in plan(root, self.bundle_with_links(tmp_path)).adopted
        }
        _, body = okf.load_frontmatter(by_id["entry"].text)
        # three absolute links, then the two relative ones keep their form
        assert body.count("(/imports/demo/my-folder/mi-nota.md)") == 1
        assert body.count("</imports/demo/my-folder/mi-nota.md>") == 2
        assert "[relative](<my-folder/mi-nota.md>)" in body
        assert "[encoded](my-folder/mi-nota.md#s)" in body
        assert "Mi Nota" not in body.replace("[plain]", "")
        _, sibling = okf.load_frontmatter(by_id["My Folder/sibling"].text)
        assert "[a](<mi-nota.md>) [b](c.md)" in sibling

    def test_the_proof_holds_against_every_engine_reader(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        from tests.unit.bundle.test_link_recognizer_inventory import (
            INDEX_READERS,
            READERS,
            _in_index_domain,
            _inside,
        )

        root, _, _ = workspace
        import_plan = plan(root, self.bundle_with_links(tmp_path))
        extracted = dict.fromkeys(READERS, 0)
        outside: list[str] = []
        texts = [(a.concept_id, a.text) for a in import_plan.adopted]
        texts += [(a.concept_id, a.text) for a in import_plan.anchors]
        for concept_id, text in texts:
            _, body = okf.load_frontmatter(text)
            for name, reader in READERS.items():
                if name in INDEX_READERS and not _in_index_domain(body, concept_id):
                    continue
                for target, resolved in reader(body, concept_id):
                    extracted[name] += 1
                    if not _inside(resolved, f"imports/{NS}"):
                        outside.append(f"{name} {concept_id}: {target!r}")
        assert outside == []
        assert all(count for count in extracted.values()), extracted

    def test_the_anchor_lists_the_slug_and_keeps_the_foreign_id_in_text(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, self.bundle_with_links(tmp_path))
        text = import_plan.anchors[0].text
        assert "(/imports/demo/my-folder/mi-nota.md)" in text
        assert "foreign id `My Folder/Mi Nota`" in text
        assert "(/imports/demo/My Folder" not in text

    def test_the_anchor_digest_still_covers_the_foreign_ids(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, self.bundle_with_links(tmp_path))
        manifest = dict(import_plan.manifest)
        expected = okf.import_bundle_digest(
            (a.foreign_id, manifest[f"{a.foreign_id}.md"]) for a in import_plan.adopted
        )
        assert imported_block(import_plan.anchors[0].text)["bundle_sha256"] == expected

    def test_a_relation_to_a_spaced_name_follows_the_rename(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = small_bundle(
            tmp_path,
            **{
                "a.md": doc(
                    relations=[{"target": "My Folder/Mi Nota", "type": "uses"}]
                ),
                "My Folder__Mi Nota.md": doc(),
            },
        )
        adopted = {a.foreign_id: a for a in plan(root, foreign).adopted}
        relations = okf.decode_relations(okf.load_frontmatter(adopted["a"].text)[0])
        assert [r.target for r in relations] == [f"imports/{NS}/my-folder/mi-nota"]

    def test_a_published_import_writes_the_slug_and_the_graph_edge_exists(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        from openkos.graph import sqlite_graph

        root, layout, _ = git_workspace
        import_plan = plan(root, self.bundle_with_links(tmp_path))
        publish(root, import_plan)
        namespace_dir = layout.bundle_dir / "imports" / NS
        assert (namespace_dir / "my-folder" / "mi-nota.md").is_file()
        assert not (namespace_dir / "My Folder").exists()
        assert okf.check_conformance(layout.bundle_dir) == []
        store = sqlite_graph.build_graph(layout.bundle_dir)
        try:
            edges = {(e.source_id, e.target_id) for e in store.edges()}
        finally:
            store.close()
        anchor = bundle_imports.anchor_id(NS, "private")
        assert (anchor, f"imports/{NS}/my-folder/mi-nota") in edges
        assert (f"imports/{NS}/entry", f"imports/{NS}/my-folder/mi-nota") in edges


# --- 4.5 the proof gate -------------------------------------------------------


class TestProofGate:
    def test_a_clean_fixture_plans(self, workspace: Ws, tmp_path: Path) -> None:
        root, _, _ = workspace
        assert plan(root, foreign_copy(tmp_path)).adopted

    def test_a_rewrite_that_leaves_a_link_outside_the_namespace_is_refused(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, _, _ = workspace
        hit: list[str] = []

        def unchanged(
            body: str, *, foreign_id: str, prefix: str
        ) -> bundle_links.NamespacedBody:
            hit.append(foreign_id)
            return bundle_links.NamespacedBody(body, 0, 0, False)

        monkeypatch.setattr(bundle_links, "rewrite_links_into_namespace", unchanged)
        refusal = refusal_of(root, foreign_copy(tmp_path))
        assert hit, "the patched rewrite was never called"
        assert refusal.code == "link-outside-namespace"
        assert refusal.retry_safe is False
        assert f"imports/{NS}/concepts/overview" in refusal.reason
        assert "/concepts/skills/python.md" in refusal.reason

    def test_an_adopted_label_below_the_floor_is_refused(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, _, _ = workspace
        real = okf.adopt_foreign_document
        hit: list[str] = []

        def too_low(document: okf.ForeignDocument, **kwargs: object) -> str:
            hit.append(document.path)
            return real(document, **{**kwargs, "sensitivity": "public"})  # type: ignore[arg-type]

        monkeypatch.setattr(okf, "adopt_foreign_document", too_low)
        refusal = refusal_of(root, foreign_copy(tmp_path))
        assert hit, "the patched adopt step was never called"
        assert refusal.code == "adopted-violation"
        assert "below the floor" in refusal.reason
        assert refusal.retry_safe is False


# --- 4.6 what a plan holds ----------------------------------------------------


def imported_block(text: str) -> dict[str, Any]:
    """The `imported` mapping of an engine-written document, as the tests read it."""
    block = okf.load_frontmatter(text)[0][okf.IMPORTED_KEY]
    assert isinstance(block, dict)
    return block


def anchor_texts(import_plan: import_service.ImportPlan) -> dict[str, str]:
    return {anchor.label: anchor.text for anchor in import_plan.anchors}


class TestPlanContent:
    def test_the_fixture_is_adopted_with_its_skips_reported(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        assert sorted(a.foreign_id for a in import_plan.adopted) == [
            "concepts/legacy",
            "concepts/overview",
            "concepts/skills/python",
            "legacy/v01-note",
            "people/ada",
        ]
        assert dict(import_plan.skipped) == {
            ".git": "dot-entry",
            "index.md": "reserved-file",
            "log.md": "reserved-file",
            "references/data.csv": "not-markdown",
            "viz.html": "not-markdown",
        }
        assert import_plan.okf_version == "0.2"
        assert import_plan.namespace == NS

    def test_concept_ids_live_under_the_namespace(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        ids = {a.concept_id for a in plan(root, foreign_copy(tmp_path)).adopted}
        assert f"imports/{NS}/concepts/skills/python" in ids
        assert all(i.startswith(f"imports/{NS}/") for i in ids)

    def test_an_unknown_type_is_kept_verbatim(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        adopted = {a.foreign_id: a for a in plan(root, foreign_copy(tmp_path)).adopted}
        assert adopted["concepts/skills/python"].doc_type == "Cooking Recipe"
        metadata, _ = okf.load_frontmatter(adopted["concepts/skills/python"].text)
        assert metadata["type"] == "Cooking Recipe"

    def test_labels_never_fall_below_the_default_and_anchors_follow_them(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        labels = {a.foreign_id: a.label for a in import_plan.adopted}
        assert labels["concepts/skills/python"] == "private"  # foreign public, floor
        assert labels["people/ada"] == "confidential"
        assert {a.label for a in import_plan.anchors} == {"private", "confidential"}
        assert import_plan.floor == "private"

    def test_the_foreign_label_is_kept_beside_the_effective_one(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = {
            a.foreign_id: a.foreign_label
            for a in plan(root, foreign_copy(tmp_path)).adopted
        }
        assert foreign["concepts/skills/python"] == "public"
        assert foreign["people/ada"] == "confidential"
        assert foreign["concepts/overview"] is None

    def test_the_flag_raises_the_floor(self, workspace: Ws, tmp_path: Path) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path), flag="confidential")
        assert {a.label for a in import_plan.adopted} == {"confidential"}
        assert [a.label for a in import_plan.anchors] == ["confidential"]
        assert import_plan.floor == "confidential"

    def test_the_flag_cannot_lower_the_floor(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path), flag="public")
        assert import_plan.floor == "private"
        assert "public" not in {a.label for a in import_plan.adopted}

    def test_a_type_offset_raises_and_is_recorded(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        _, cfg = make_workspace(root, offsets={"Person": 1})
        import_plan = plan(root, foreign_copy(tmp_path), cfg=cfg)
        adopted = {a.foreign_id: a for a in import_plan.adopted}
        assert adopted["people/ada"].label == "confidential"
        assert adopted["people/ada"].type_raised is False  # already confidential
        assert adopted["concepts/overview"].type_raised is False
        ada = write_foreign(tmp_path / "other", "people/bob.md", doc("Person"))
        other = plan(root, ada.parents[1], cfg=cfg)
        bob = other.adopted[0]
        assert (bob.label, bob.type_raised) == ("confidential", True)
        assert other.type_raises == (bob,)

    def test_every_document_cites_exactly_its_own_anchor_and_is_listed_there(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        anchors = anchor_texts(import_plan)
        for adopted in import_plan.adopted:
            metadata, _ = okf.load_frontmatter(adopted.text)
            assert metadata["provenance"] == [
                bundle_imports.anchor_id(NS, adopted.label)
            ]
            link = f"(/{adopted.concept_id}.md)"
            listing = [label for label, text in anchors.items() if link in text]
            assert listing == [adopted.label]

    def test_the_counts_and_the_label_distribution(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        assert import_plan.label_counts == {"private": 4, "confidential": 1}
        assert import_plan.links_rewritten >= 1
        assert import_plan.links_clamped >= 1  # the escaping `../../outside.md`
        assert import_plan.dropped_keys == 0
        assert import_plan.html_link_documents == 0

    def test_dropped_keys_are_counted(self, workspace: Ws, tmp_path: Path) -> None:
        root, _, _ = workspace
        foreign = small_bundle(
            tmp_path, **{"a.md": doc(origin_key="0" * 32, merged_from=["x"])}
        )
        assert plan(root, foreign).dropped_keys == 2

    def test_the_fingerprint_names_the_labelling_inputs(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        _, cfg = make_workspace(root, default="public", offsets={"Person": 1})
        import_plan = plan(root, foreign_copy(tmp_path), cfg=cfg)
        assert import_plan.label_fingerprint == ("public", (("Person", 1),))

    def test_the_manifest_is_the_readers_manifest(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = foreign_copy(tmp_path)
        import_plan = plan(root, foreign)
        assert import_plan.manifest == okf.read_foreign_bundle(foreign).manifest

    def test_the_plan_is_deterministic(self, workspace: Ws, tmp_path: Path) -> None:
        root, _, _ = workspace
        foreign = foreign_copy(tmp_path)
        assert plan(root, foreign) == plan(root, foreign)

    def test_a_foreign_path_equal_to_a_local_one_plans_a_namespaced_id(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        local = layout.bundle_dir / "concepts" / "foo.md"
        local.parent.mkdir()
        local.write_text(doc(title="Local"), encoding="utf-8")
        foreign = small_bundle(tmp_path, **{"concepts__foo.md": doc(title="Foreign")})
        before = tree_state(root)
        import_plan = plan(root, foreign)
        assert [a.concept_id for a in import_plan.adopted] == [
            f"imports/{NS}/concepts/foo"
        ]
        assert tree_state(root) == before

    def test_the_anchor_never_names_a_local_path(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = foreign_copy(tmp_path, "private-corpus-zq")
        import_plan = plan(root, foreign)
        assert import_plan.anchors
        for anchor in import_plan.anchors:
            assert str(tmp_path) not in anchor.text
            assert "private-corpus-zq" not in anchor.text

    def test_the_anchor_digest_covers_only_its_own_label(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        by_label = {
            label: imported_block(text)["bundle_sha256"]
            for label, text in anchor_texts(import_plan).items()
        }
        digests = {
            a.foreign_id: dict(import_plan.manifest)[f"{a.foreign_id}.md"]
            for a in import_plan.adopted
        }
        expected = {
            label: okf.import_bundle_digest(
                (a.foreign_id, digests[a.foreign_id])
                for a in import_plan.adopted
                if a.label == label
            )
            for label in by_label
        }
        assert by_label == expected
        assert len(set(by_label.values())) == 2

    def test_the_link_counts_are_exact(self, workspace: Ws, tmp_path: Path) -> None:
        root, _, _ = workspace
        body = (
            "[a](/concepts/x.md) [b](../../up.md) [c](https://e.org) [d](rel.md)\n"
            '<a href="/concepts/raw.md">raw</a>\n'
        )
        foreign = small_bundle(
            tmp_path,
            **{
                "n__a.md": okf.dump_frontmatter({"type": "Concept"}, body),
                "plain.md": doc(),
            },
        )
        import_plan = plan(root, foreign)
        assert import_plan.links_rewritten == 2
        assert import_plan.links_clamped == 1
        assert import_plan.html_link_documents == 1

    @pytest.mark.parametrize("generated", ["reference_agent/1.2", ["a"], {"by": 3}, {}])
    def test_a_generated_value_without_a_string_by_is_not_recorded(
        self, workspace: Ws, tmp_path: Path, generated: object
    ) -> None:
        root, _, _ = workspace
        foreign = small_bundle(tmp_path, **{"a.md": doc(generated=generated)})
        imported = imported_block(plan(root, foreign).anchors[0].text)
        assert imported["generated_by"] == []

    def test_the_anchor_records_the_foreign_generator_and_version(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        imported = imported_block(anchor_texts(import_plan)["private"])
        assert imported["generated_by"] == ["reference_agent/1.2"]
        assert imported["okf_version"] == "0.2"
        assert imported["documents"] == 4

    def test_anchor_titles_are_one_sanitized_line(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = small_bundle(
            tmp_path, **{"a.md": doc(title="Line one\nline [two]"), "b.md": doc()}
        )
        text = plan(root, foreign).anchors[0].text
        assert "- [Line one line (two)](/imports/demo/a.md)" in text
        assert "- [b](/imports/demo/b.md)" in text  # no title: the foreign id


class TestPlanIsLocalAndModelFree:
    def test_it_takes_no_lock_and_writes_nothing(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, _, _ = workspace

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("plan_import touched the workspace lock")

        monkeypatch.setattr(lock, "workspace_lock", refuse)
        foreign = foreign_copy(tmp_path)
        before = tree_state(root)
        plan(root, foreign)
        assert tree_state(root) == before

    def test_it_runs_with_every_backend_resolver_raising(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, _, _ = workspace

        def boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("a model backend was resolved")

        for name in ("chat_client", "embed_client", "diagnostics_client"):
            monkeypatch.setattr(backends, name, boom)
        assert plan(root, foreign_copy(tmp_path)).adopted

    def test_the_module_imports_no_model_code(self) -> None:
        source = Path(import_service.__file__).read_text(encoding="utf-8")
        imported: list[str] = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                imported.append(base)
                imported.extend(f"{base}.{alias.name}" for alias in node.names)
        forbidden = (
            "openkos.llm",
            "openkos.application.backends",
            "openkos.extraction",
        )
        assert [name for name in imported if name.startswith(forbidden)] == []
        assert not any(
            name.startswith(("openkos.cli", "typer", "rich")) for name in imported
        )


# --- 4.9 publish_import: the happy path --------------------------------------


class Section:
    """A `CommitSection` that counts how often it is entered."""

    def __init__(self, *, refuse: Exception | None = None) -> None:
        self.entered = 0
        self.refuse = refuse

    @contextlib.contextmanager
    def __call__(self) -> Iterator[None]:
        if self.refuse is not None:
            raise self.refuse
        self.entered += 1
        yield


class Commits:
    """An `autocommit` port that records every call and, unless degraded,
    makes the real pathspec-scoped commit."""

    def __init__(self, *, degraded: bool = False) -> None:
        self.degraded = degraded
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, root: Path, paths: Sequence[str], message: str) -> str | None:
        from openkos.vcs import git as vcs_git

        self.calls.append((list(paths), message))
        if self.degraded:
            return None
        return vcs_git.commit_paths(root, list(paths), message)


def pin_git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """`GIT_CONFIG_COUNT` is what `git config` reads back; `GIT_AUTHOR_*` is not."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "openkos tests")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "tests@openkos.invalid")


def git(root: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def git_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, config.WorkspaceLayout, config.Config]:
    """A workspace that is a real git repository with one commit."""
    from openkos.vcs import git as vcs_git

    pin_git_identity(monkeypatch)
    root = tmp_path / "ws"
    layout, cfg = make_workspace(root)
    vcs_git.init_repo(root)
    vcs_git.commit_paths(
        root, ["openkos.yaml", "bundle/index.md", "bundle/log.md"], "init"
    )
    return root, layout, cfg


def publish(
    root: Path,
    import_plan: import_service.ImportPlan,
    *,
    section: Section | None = None,
    commits: Commits | None = None,
    load_config: Callable[[Path], config.Config] = config.read_config,
) -> import_service.ImportOutcome:
    return import_service.publish_import(
        root,
        config.WorkspaceLayout(root),
        import_plan,
        commit_section=section or Section(),
        load_config=load_config,
        autocommit=commits or Commits(degraded=True),
    )


def expected_anchor_ids(import_plan: import_service.ImportPlan) -> list[str]:
    return [anchor.concept_id for anchor in import_plan.anchors]


class TestPublishHappyPath:
    def test_the_commit_section_is_entered_exactly_once(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = git_workspace
        section = Section()
        publish(root, plan(root, foreign_copy(tmp_path)), section=section)
        assert section.entered == 1

    def test_every_document_lands_under_the_namespace_as_planned(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = git_workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        publish(root, import_plan)
        for adopted in import_plan.adopted:
            path = layout.bundle_dir / f"{adopted.concept_id}.md"
            assert path.read_text(encoding="utf-8") == adopted.text
        for anchor in import_plan.anchors:
            path = layout.bundle_dir / f"{anchor.concept_id}.md"
            assert path.read_text(encoding="utf-8") == anchor.text
        assert (layout.bundle_dir / "imports" / NS / "concepts" / "skills").is_dir()

    def test_the_bundle_stays_conformant(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = git_workspace
        publish(root, plan(root, foreign_copy(tmp_path)))
        assert okf.check_conformance(layout.bundle_dir) == []

    def test_the_index_lists_one_bullet_per_anchor_and_no_concept(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = git_workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        publish(root, import_plan)
        index_text = (layout.bundle_dir / "index.md").read_text(encoding="utf-8")
        assert bundle_index.indexed_concept_ids(index_text) == set(
            expected_anchor_ids(import_plan)
        )
        assert f"/imports/{NS}/" not in index_text
        assert index_text.count("# Sources") == 1

    def test_the_log_gets_one_import_entry(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = git_workspace
        foreign = small_bundle(
            tmp_path,
            **{
                "a.md": doc(),
                "b.md": doc(),
                "c.md": doc(sensitivity="confidential"),
                "skip.txt": "x",
                "other.html": "x",
            },
        )
        import_plan = plan(root, foreign)
        publish(root, import_plan)
        log_text = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
        entries = [line for line in log_text.splitlines() if "**Import**" in line]
        assert len(entries) == 1
        entry = entries[0]
        assert f"imports/{NS}" in entry
        assert "3 documents" in entry
        assert "2 skipped" in entry
        for anchor in import_plan.anchors:
            assert f"(/{anchor.concept_id}.md)" in entry
        assert f"## {NOW.date().isoformat()}" in log_text

    def test_one_autocommit_with_one_directory_pathspec(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = git_workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        commits = Commits()
        outcome = publish(root, import_plan, commits=commits)
        assert commits.calls == [
            (
                [
                    f"bundle/imports/{NS}",
                    f"bundle/imports/{NS}--private.md",
                    f"bundle/imports/{NS}--confidential.md",
                    "bundle/index.md",
                    "bundle/log.md",
                ],
                f"openkos: import {NS} (+5 concepts)",
            )
        ]
        assert outcome.commit is not None
        assert git(root, "log", "-1", "--format=%s").strip() == (
            f"openkos: import {NS} (+5 concepts)"
        )
        assert git(root, "rev-list", "--count", "HEAD").strip() == "2"

    def test_the_commit_holds_every_file_and_leaves_the_tree_clean(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = git_workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        publish(root, import_plan, commits=Commits())
        committed = set(git(root, "show", "--name-only", "--format=", "HEAD").split())
        assert committed == {
            *(f"bundle/{a.concept_id}.md" for a in import_plan.adopted),
            *(f"bundle/{a.concept_id}.md" for a in import_plan.anchors),
            "bundle/index.md",
            "bundle/log.md",
        }
        assert git(root, "status", "--porcelain").strip() == ""

    def test_a_prestaged_unrelated_file_stays_staged_and_uncommitted(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = git_workspace
        (root / "notes.txt").write_text("mine\n", encoding="utf-8")
        git(root, "add", "notes.txt")
        publish(root, plan(root, foreign_copy(tmp_path)), commits=Commits())
        assert git(root, "diff", "--cached", "--name-only").split() == ["notes.txt"]
        assert "notes.txt" not in git(root, "show", "--name-only", "--format=", "HEAD")

    def test_a_degraded_commit_still_succeeds(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = git_workspace
        commits = Commits(degraded=True)
        outcome = publish(root, plan(root, foreign_copy(tmp_path)), commits=commits)
        assert outcome.commit is None
        assert len(commits.calls) == 1
        assert (layout.bundle_dir / "imports" / NS).is_dir()

    def test_the_outcome_reports_the_counts_and_the_anchors(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = git_workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        outcome = publish(root, import_plan)
        assert outcome.namespace == NS
        assert outcome.adopted == 5
        assert outcome.skipped == 5
        assert list(outcome.anchors) == expected_anchor_ids(import_plan)

    def test_no_derived_store_is_written(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = git_workspace
        publish(root, plan(root, foreign_copy(tmp_path)))
        assert not (root / ".openkos").exists()

    def test_the_local_directory_path_appears_nowhere(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = git_workspace
        foreign = foreign_copy(tmp_path, "private-corpus-zq")
        publish(root, plan(root, foreign), commits=Commits())
        needles = [str(tmp_path).encode(), b"private-corpus-zq"]
        for current, dirs, files in os.walk(root):
            if ".git" in dirs:
                dirs.remove(".git")
            for name in files:
                data = (Path(current) / name).read_bytes()
                assert not any(needle in data for needle in needles), name
        assert "private-corpus-zq" not in git(root, "log", "-p", "-1")

    def test_a_foreign_git_directory_and_git_file_are_skipped_and_untouched(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = git_workspace
        with_dir = small_bundle(tmp_path, **{"a.md": doc()})
        git(with_dir, "init", "-q")
        dir_state = {
            p.relative_to(with_dir).as_posix(): p.read_bytes()
            for p in sorted((with_dir / ".git").rglob("*"))
            if p.is_file()
        }
        first = plan(root, with_dir)
        assert dict(first.skipped)[".git"] == "dot-entry"
        publish(root, first, commits=Commits())
        assert {
            p.relative_to(with_dir).as_posix(): p.read_bytes()
            for p in sorted((with_dir / ".git").rglob("*"))
            if p.is_file()
        } == dir_state
        assert (
            git(root, "log", "-1", "--format=%s").strip().startswith("openkos: import")
        )

        other = tmp_path / "foreign-file"
        write_foreign(other, "b.md", doc())
        write_foreign(other, ".git", "gitdir: ../elsewhere\n")
        second = plan(root, other, namespace="second")
        assert dict(second.skipped)[".git"] == "dot-entry"
        publish(root, second, commits=Commits())
        assert (other / ".git").read_text(encoding="utf-8") == "gitdir: ../elsewhere\n"
        assert git(root, "log", "-1", "--format=%s").strip() == (
            "openkos: import second (+1 concepts)"
        )

    def test_the_published_namespace_directory_has_the_mode_of_its_siblings(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        """A staging directory made by `tempfile.mkdtemp` is private (0700) and
        would be published as the namespace directory."""
        root, layout, _ = git_workspace
        publish(root, plan(root, foreign_copy(tmp_path)))
        if os.name != "posix":
            return
        namespace_mode = (layout.bundle_dir / "imports" / NS).stat().st_mode & 0o777
        sibling_mode = (layout.bundle_dir / "imports").stat().st_mode & 0o777
        assert namespace_mode == sibling_mode


# --- 4.10 Phase B drift and races --------------------------------------------


def publish_refused(
    root: Path,
    import_plan: import_service.ImportPlan,
    *,
    section: Section | None = None,
    commits: Commits | None = None,
    load_config: Callable[[Path], config.Config] = config.read_config,
    writes_allowed: bool = False,
) -> import_service.ImportRefusal:
    """Publish `import_plan`, expect a refusal, and assert the tree is
    byte-identical and nothing was committed. Unless `writes_allowed`, also
    assert no write was even attempted: a guard that only fires after the
    writes (and is rolled back) is not the guard the spec asks for."""
    before = tree_state(root)
    commits = commits or Commits(degraded=True)
    attempted: list[str] = []
    real_exclusive = fsio.write_exclusive
    real_atomic = fsio.write_atomic

    def note_exclusive(path: Path, content: str) -> None:
        attempted.append(path.name)
        real_exclusive(path, content)

    def note_atomic(path: Path, content: str) -> None:
        attempted.append(path.name)
        real_atomic(path, content)

    with pytest.MonkeyPatch.context() as spy:
        spy.setattr(fsio, "write_exclusive", note_exclusive)
        spy.setattr(fsio, "write_atomic", note_atomic)
        with pytest.raises(import_service.ImportRefusal) as caught:
            publish(
                root,
                import_plan,
                section=section,
                commits=commits,
                load_config=load_config,
            )
    assert tree_state(root) == before, "a refused publish changed the workspace"
    assert commits.calls == [], "a refused publish committed"
    if not writes_allowed:
        assert attempted == [], f"a refusal came only after writing {attempted}"
    return caught.value


class TestPublishDrift:
    def test_an_edited_foreign_file_is_refused_retry_safe(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = foreign_copy(tmp_path)
        import_plan = plan(root, foreign)
        (foreign / "people" / "ada.md").write_text(
            doc("Person", title="Changed"), encoding="utf-8"
        )
        refusal = publish_refused(root, import_plan)
        assert refusal.code == "foreign-changed"
        assert refusal.retry_safe is True
        assert "changed since the preview" in refusal.reason

    def test_an_added_foreign_document_is_refused_retry_safe(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = foreign_copy(tmp_path)
        import_plan = plan(root, foreign)
        write_foreign(foreign, "extra.md", doc())
        refusal = publish_refused(root, import_plan)
        assert (refusal.code, refusal.retry_safe) == ("foreign-changed", True)

    def test_a_removed_foreign_document_is_refused_retry_safe(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = foreign_copy(tmp_path)
        import_plan = plan(root, foreign)
        (foreign / "people" / "ada.md").unlink()
        refusal = publish_refused(root, import_plan)
        assert (refusal.code, refusal.retry_safe) == ("foreign-changed", True)

    def test_a_changed_skip_list_is_refused_retry_safe(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = foreign_copy(tmp_path)
        import_plan = plan(root, foreign)
        (foreign / "notes.txt").write_text("a new non-markdown file", encoding="utf-8")
        refusal = publish_refused(root, import_plan)
        assert (refusal.code, refusal.retry_safe) == ("foreign-changed", True)

    def test_a_foreign_file_that_became_hostile_is_refused_retry_safe(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        foreign = foreign_copy(tmp_path)
        import_plan = plan(root, foreign)
        try:
            (foreign / "link.md").symlink_to(foreign / "index.md")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available here")
        refusal = publish_refused(root, import_plan)
        assert (refusal.code, refusal.retry_safe) == ("foreign-changed", True)
        assert str(tmp_path) not in refusal.reason

    def test_a_changed_default_sensitivity_is_refused_retry_safe(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        rewrite_config(root, default="confidential")
        refusal = publish_refused(root, import_plan)
        assert refusal.code == "labels-changed"
        assert refusal.retry_safe is True

    def test_a_changed_type_offset_is_refused_retry_safe(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        rewrite_config(root, offsets={"Concept": 1})
        refusal = publish_refused(root, import_plan)
        assert (refusal.code, refusal.retry_safe) == ("labels-changed", True)

    def test_a_namespace_created_after_the_preview_is_refused_not_retry_safe(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        (layout.bundle_dir / "imports" / NS).mkdir(parents=True)
        refusal = publish_refused(root, import_plan)
        assert refusal.code == "namespace-exists"
        assert refusal.retry_safe is False
        assert "git status" in refusal.reason

    def test_an_anchor_path_claimed_after_the_preview_is_refused(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        imports_dir = layout.bundle_dir / "imports"
        imports_dir.mkdir()
        (imports_dir / f"{NS}--private.md").write_text(doc("Source"), encoding="utf-8")
        refusal = publish_refused(root, import_plan)
        assert (refusal.code, refusal.retry_safe) == ("anchor-path-taken", False)

    def test_an_imports_directory_that_became_a_symlink_is_refused(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        try:
            (layout.bundle_dir / "imports").symlink_to(
                elsewhere, target_is_directory=True
            )
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available here")
        refusal = publish_refused(root, import_plan)
        assert refusal.code == "symlink"
        assert list(elsewhere.iterdir()) == []

    def test_a_busy_workspace_propagates_before_anything_is_written(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        before = tree_state(root)
        busy = lock.WorkspaceBusyError("another openkos process holds the lock")
        commits = Commits()
        with pytest.raises(lock.WorkspaceBusyError):
            publish(root, import_plan, section=Section(refuse=busy), commits=commits)
        assert tree_state(root) == before
        assert commits.calls == []

    def test_the_foreign_tree_is_judged_again_inside_the_section(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        """A drift refusal needs the commit section entered first: the guards
        run under the lock, not before it."""
        root, _, _ = workspace
        foreign = foreign_copy(tmp_path)
        import_plan = plan(root, foreign)
        write_foreign(foreign, "extra.md", doc())
        section = Section()
        publish_refused(root, import_plan, section=section)
        assert section.entered == 1


class TestNamespaceAppearsDuringTheRun:
    def test_an_empty_namespace_directory_created_mid_run_is_not_replaced(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POSIX `rename` replaces an EMPTY directory: the last look before
        the rename is what keeps a directory someone else made."""
        root, layout, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        before = tree_state(root)
        real = okf.check_conformance

        def create_namespace(bundle_dir: Path) -> list[str]:
            (layout.bundle_dir / "imports" / NS).mkdir()
            return real(bundle_dir)

        monkeypatch.setattr(okf, "check_conformance", create_namespace)
        with pytest.raises(import_service.ImportRefusal) as caught:
            publish(root, import_plan)
        assert caught.value.code == "namespace-exists"
        assert list((layout.bundle_dir / "imports" / NS).iterdir()) == []
        after = tree_state(root)
        after.pop(f"bundle/imports/{NS}")
        # the directory the run made around the namespace someone else made
        # stays (it is not empty), and nothing else changed
        assert after == {**before, "bundle/imports": "<dir>"}


# --- 4.11 torn runs: a failure at each step of the publish sequence ----------


def torn_anchor(namespace: str, label: str) -> str:
    return okf.build_import_anchor(
        namespace=namespace,
        label=label,
        entries=[okf.AnchorEntry("old", "Old", "0" * 64)],
        bundle_sha256="1" * 64,
        okf_version=None,
        generated=okf.Generated("openkos/old", "2026-10-01T00:00:00Z"),
    )


def stage_prior_state(layout: config.WorkspaceLayout) -> None:
    """The state a torn earlier run leaves that a retry must overwrite: an
    existing `bundle/imports/` holding one of this namespace's anchors, and
    an index that already lists it."""
    imports_dir = layout.bundle_dir / "imports"
    imports_dir.mkdir()
    (imports_dir / f"{NS}--private.md").write_text(
        torn_anchor(NS, "private"), encoding="utf-8"
    )
    index_path = layout.bundle_dir / "index.md"
    index_path.write_text(
        bundle_index.insert_index_entry(
            index_path.read_text(encoding="utf-8"),
            section="Sources",
            link_dir="imports",
            title="Import demo (private)",
            slug=f"{NS}--private",
            description="Torn.",
        ),
        encoding="utf-8",
    )


class Injection:
    """One place to make the publish sequence fail, applied to a monkeypatch.

    `install` patches the exact call target the service uses and records every
    hit in `hits`, so a test can assert the patch was live."""

    def __init__(self) -> None:
        self.hits = 0


def inject_remove_tree(monkeypatch: pytest.MonkeyPatch) -> Injection:
    injection = Injection()

    def failing(path: Path) -> None:
        injection.hits += 1
        raise OSError("injected: cannot remove a stale staging directory")

    monkeypatch.setattr(import_service, "_remove_tree", failing)
    return injection


def inject_snapshot(monkeypatch: pytest.MonkeyPatch) -> Injection:
    injection = Injection()

    def failing(*_args: object, **_kwargs: object) -> object:
        injection.hits += 1
        raise OSError("injected: cannot snapshot")

    monkeypatch.setattr(import_service, "_take_snapshot", failing)
    return injection


def inject_document_write(monkeypatch: pytest.MonkeyPatch) -> Injection:
    injection = Injection()
    real = fsio.write_exclusive

    def failing(path: Path, content: str) -> None:
        injection.hits += 1
        if injection.hits == 3:
            raise OSError("injected: disk full after two documents")
        real(path, content)

    monkeypatch.setattr(fsio, "write_exclusive", failing)
    return injection


def inject_conformance(monkeypatch: pytest.MonkeyPatch) -> Injection:
    injection = Injection()
    real = okf.check_conformance
    seen: list[int] = []

    def failing(bundle_dir: Path) -> list[str]:
        injection.hits += 1
        assert real(bundle_dir) == []
        survey = okf.survey_bundle(bundle_dir)
        seen.append(survey.concepts + survey.sources)
        assert seen[-1] == 5, "conformance did not see the staged documents"
        return [f"{bundle_dir}/concepts/x.md: injected violation"]

    monkeypatch.setattr(okf, "check_conformance", failing)
    return injection


def inject_atomic_write(
    monkeypatch: pytest.MonkeyPatch, *, name_prefix: str, nth: int = 1
) -> Injection:
    injection = Injection()
    real = fsio.write_atomic
    matched = 0

    def failing(path: Path, content: str) -> None:
        nonlocal matched
        if path.name.startswith(name_prefix):
            matched += 1
            if matched == nth:
                injection.hits += 1
                raise OSError(f"injected: cannot write {path.name}")
        real(path, content)

    monkeypatch.setattr(fsio, "write_atomic", failing)
    return injection


def inject_publish_directory(monkeypatch: pytest.MonkeyPatch) -> Injection:
    injection = Injection()

    def failing(staging: Path, target: Path) -> None:
        injection.hits += 1
        raise OSError("injected: the final rename failed")

    monkeypatch.setattr(import_service, "_publish_directory", failing)
    return injection


INJECTIONS: dict[str, Callable[[pytest.MonkeyPatch], Injection]] = {
    "snapshot": inject_snapshot,
    "document-write": inject_document_write,
    "conformance": inject_conformance,
    "first-anchor": lambda mp: inject_atomic_write(mp, name_prefix=f"{NS}--", nth=1),
    "second-anchor": lambda mp: inject_atomic_write(mp, name_prefix=f"{NS}--", nth=2),
    "index": lambda mp: inject_atomic_write(mp, name_prefix="index.md"),
    "log": lambda mp: inject_atomic_write(mp, name_prefix="log.md"),
    "final-rename": inject_publish_directory,
}


class TestTornRuns:
    @pytest.mark.parametrize("prior", ["clean", "torn-anchor-and-index"])
    @pytest.mark.parametrize("step", list(INJECTIONS))
    def test_a_failure_restores_the_tree_byte_for_byte_and_a_retry_succeeds(
        self,
        workspace: Ws,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        step: str,
        prior: str,
    ) -> None:
        root, layout, _ = workspace
        if prior != "clean":
            stage_prior_state(layout)
        import_plan = plan(root, foreign_copy(tmp_path))
        injection = INJECTIONS[step](monkeypatch)

        refusal = publish_refused(root, import_plan, writes_allowed=True)

        assert injection.hits >= 1, "the injected failure was never reached"
        assert refusal.retry_safe is False
        monkeypatch.undo()
        outcome = publish(root, plan(root, foreign_copy(tmp_path, "foreign-again")))
        assert outcome.adopted == 5
        assert (layout.bundle_dir / "imports" / NS).is_dir()
        assert okf.check_conformance(layout.bundle_dir) == []

    def test_a_conformance_violation_is_a_defect_named_as_such(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        inject_conformance(monkeypatch)
        refusal = publish_refused(root, import_plan, writes_allowed=True)
        assert refusal.code == "conformance"
        assert "injected violation" in refusal.reason
        assert "concepts/x.md" in refusal.reason
        assert str(tmp_path) not in refusal.reason
        assert ".openkos-import-" not in refusal.reason

    def test_a_write_failure_is_reported_as_one(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        inject_document_write(monkeypatch)
        refusal = publish_refused(root, import_plan, writes_allowed=True)
        assert refusal.code == "write-failed"
        assert "disk full" in refusal.reason

    def test_a_failed_stale_staging_removal_refuses_and_leaves_it_alone(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, layout, _ = workspace
        stale = layout.bundle_dir / "imports" / f".{NS}.openkos-import-stale"
        stale.mkdir(parents=True)
        (stale / "leftover.md").write_text("half a document", encoding="utf-8")
        import_plan = plan(root, foreign_copy(tmp_path))
        injection = inject_remove_tree(monkeypatch)
        refusal = publish_refused(root, import_plan)
        assert injection.hits == 1
        assert refusal.code == "stale-staging"
        assert (stale / "leftover.md").is_file()

    def test_a_failing_restore_says_so_and_commits_nothing(
        self, git_workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, _, _ = git_workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        inject_atomic_write(monkeypatch, name_prefix="log.md")
        restores: list[Path] = []

        def failing_restore(path: Path, data: bytes | None) -> None:
            restores.append(path)
            raise OSError("injected: cannot restore")

        monkeypatch.setattr(import_service, "_restore_bytes", failing_restore)
        commits = Commits()
        with pytest.raises(import_service.ImportRefusal) as caught:
            publish(root, import_plan, commits=commits)
        assert restores, "the restore was never attempted"
        assert caught.value.code == "restore-failed"
        assert "git status" in caught.value.reason
        assert commits.calls == []
        assert git(root, "rev-list", "--count", "HEAD").strip() == "1"

    def test_an_interrupt_cleans_up_and_is_not_swallowed(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        before = tree_state(root)

        def interrupted(staging: Path, target: Path) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(import_service, "_publish_directory", interrupted)
        with pytest.raises(KeyboardInterrupt):
            publish(root, import_plan)
        assert tree_state(root) == before

    def test_staging_lives_beside_the_namespace_under_a_dot_name(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, _, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        seen: list[Path] = []
        real = import_service._publish_directory

        def spy(staging: Path, target: Path) -> None:
            seen.append(staging)
            assert staging.parent == target.parent
            assert staging.name.startswith(bundle_imports.staging_name_prefix(NS))
            assert not target.exists()
            real(staging, target)

        monkeypatch.setattr(import_service, "_publish_directory", spy)
        publish(root, import_plan)
        assert len(seen) == 1
        assert not seen[0].exists()

    def test_anchors_and_index_are_written_before_the_rename(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rename is the completion point, so everything else is already
        on disk when it happens."""
        root, layout, _ = workspace
        import_plan = plan(root, foreign_copy(tmp_path))
        real = import_service._publish_directory
        at_rename: dict[str, bool] = {}

        def spy(staging: Path, target: Path) -> None:
            at_rename["anchors"] = all(
                (layout.bundle_dir / f"{a.concept_id}.md").is_file()
                for a in import_plan.anchors
            )
            text = (layout.bundle_dir / "index.md").read_text(encoding="utf-8")
            at_rename["index"] = all(
                a.concept_id in bundle_index.indexed_concept_ids(text)
                for a in import_plan.anchors
            )
            at_rename["log"] = "**Import**" in (layout.bundle_dir / "log.md").read_text(
                encoding="utf-8"
            )
            real(staging, target)

        monkeypatch.setattr(import_service, "_publish_directory", spy)
        publish(root, import_plan)
        assert at_rename == {"anchors": True, "index": True, "log": True}


# --- 4.12 torn-run recovery ----------------------------------------------------


def simulate_kill_before_the_rename(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave exactly what a killed process leaves: no cleanup runs, and the
    rename never happens."""

    def kill(staging: Path, target: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(import_service, "_publish_directory", kill)
    monkeypatch.setattr(import_service, "_remove_tree", lambda path: None)
    monkeypatch.setattr(import_service, "_restore_bytes", lambda path, data: None)


class TestTornRunRecovery:
    def test_a_kill_before_the_rename_never_blocks_the_retry(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, layout, _ = workspace
        imports_dir = layout.bundle_dir / "imports"
        import_plan = plan(root, foreign_copy(tmp_path))
        with monkeypatch.context() as killed:
            simulate_kill_before_the_rename(killed)
            with pytest.raises(KeyboardInterrupt):
                publish(root, import_plan)
        leftovers = sorted(p.name for p in imports_dir.iterdir())
        assert any(name.startswith(f".{NS}.openkos-import-") for name in leftovers)
        assert f"{NS}--private.md" in leftovers
        assert NS not in leftovers

        retry = plan(root, foreign_copy(tmp_path, "foreign-again"))
        outcome = publish(root, retry)
        assert outcome.adopted == 5
        assert sorted(p.name for p in imports_dir.iterdir()) == [
            NS,
            f"{NS}--confidential.md",
            f"{NS}--private.md",
        ]
        index_text = (layout.bundle_dir / "index.md").read_text(encoding="utf-8")
        for anchor in retry.anchors:
            assert index_text.count(f"(/{anchor.concept_id}.md)") == 1
        log_text = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
        assert log_text.count("**Import**") == 2
        assert okf.check_conformance(layout.bundle_dir) == []

    def test_a_torn_anchor_is_overwritten_by_the_retry(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        stage_prior_state(layout)
        import_plan = plan(root, foreign_copy(tmp_path))
        publish(root, import_plan)
        anchor = next(a for a in import_plan.anchors if a.label == "private")
        written = (layout.bundle_dir / f"{anchor.concept_id}.md").read_text(
            encoding="utf-8"
        )
        assert written == anchor.text
        assert "Old" not in written

    def test_a_torn_anchor_of_a_label_no_longer_present_is_removed(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        stage_prior_state(layout)  # a torn `private` anchor, indexed
        rewrite_config(root, default="confidential")
        import_plan = plan(root, foreign_copy(tmp_path))
        assert [a.label for a in import_plan.anchors] == ["confidential"]
        publish(root, import_plan)
        imports_dir = layout.bundle_dir / "imports"
        assert not (imports_dir / f"{NS}--private.md").exists()
        index_text = (layout.bundle_dir / "index.md").read_text(encoding="utf-8")
        assert bundle_index.indexed_concept_ids(index_text) == {
            f"imports/{NS}--confidential"
        }

    def test_a_kill_after_the_rename_leaves_a_complete_uncommitted_import(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = git_workspace
        import_plan = plan(root, foreign_copy(tmp_path))

        def killed(_root: Path, _paths: Sequence[str], _message: str) -> str | None:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            import_service.publish_import(
                root,
                layout,
                import_plan,
                commit_section=Section(),
                load_config=config.read_config,
                autocommit=killed,
            )
        assert (layout.bundle_dir / "imports" / NS / "people" / "ada.md").is_file()
        assert okf.check_conformance(layout.bundle_dir) == []
        refusal = refusal_of(root, foreign_copy(tmp_path, "foreign-again"))
        assert refusal.code == "namespace-exists"
        assert "git status" in refusal.reason
        assert git(root, "rev-list", "--count", "HEAD").strip() == "1"

    def test_stale_staging_removal_touches_real_directories_directly_under_imports_only(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, layout, _ = workspace
        imports_dir = layout.bundle_dir / "imports"
        imports_dir.mkdir()
        stale = imports_dir / f".{NS}.openkos-import-aaaa"
        stale.mkdir()
        (stale / "half.md").write_text("half", encoding="utf-8")
        other_namespace = imports_dir / ".other.openkos-import-aaaa"
        other_namespace.mkdir()
        nested = imports_dir / "sub" / f".{NS}.openkos-import-bbbb"
        nested.mkdir(parents=True)
        plain_file = imports_dir / f".{NS}.openkos-import-file"
        plain_file.write_text("a file, not a directory", encoding="utf-8")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "keep.md").write_text("keep", encoding="utf-8")
        linked = imports_dir / f".{NS}.openkos-import-link"
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            linked = None  # type: ignore[assignment]

        publish(root, plan(root, foreign_copy(tmp_path)))

        assert not stale.exists()
        assert other_namespace.is_dir()
        assert nested.is_dir()
        assert plain_file.read_text(encoding="utf-8") == "a file, not a directory"
        assert (outside / "keep.md").read_text(encoding="utf-8") == "keep"
        if linked is not None:
            assert linked.is_symlink()


# --- 4.15 the guarded read, the third-party shape and the v0.1 shape ---------


class TestGuardedReadPin:
    def test_the_patch_is_live(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pin that cannot fail proves nothing: with the parser patched to
        raise, the unguarded walk over an ordinary bundle must fail."""
        bundle = tmp_path / "bundle"
        write_foreign(bundle, "a.md", doc())

        def boom(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("the unguarded parser ran")

        monkeypatch.setattr(okf, "_parse_post", boom)
        with pytest.raises(AssertionError, match="unguarded parser"):
            okf.check_conformance(bundle)

    def test_foreign_bytes_never_reach_the_unguarded_parsers(
        self, workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The engine re-reads its OWN adopted output with the ordinary
        parser (the proof does), so the pin is that no RAW foreign file text
        is ever handed to either parser, not that neither runs."""
        import frontmatter

        root, _, _ = workspace
        foreign = foreign_copy(tmp_path)
        raw = {p.read_text(encoding="utf-8") for p in foreign.rglob("*.md")}
        assert len(raw) >= 5
        recorded: list[str] = []
        real_loads = frontmatter.loads
        real_post = okf._parse_post

        def loads(text: str, *args: object, **kwargs: object) -> object:
            recorded.append(text)
            return real_loads(text, *args, **kwargs)  # type: ignore[arg-type]

        def parse_post(text: str) -> object:
            recorded.append(text)
            return real_post(text)

        monkeypatch.setattr(frontmatter, "loads", loads)
        monkeypatch.setattr(okf, "_parse_post", parse_post)
        import_plan = plan(root, foreign)
        assert import_plan.adopted
        assert recorded, "the patches were never reached"
        assert set(recorded) & raw == set()


class TestThirdPartyShape:
    def test_every_skip_is_reported_with_its_code(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        skipped = dict(plan(root, foreign_copy(tmp_path)).skipped)
        assert skipped["viz.html"] == "not-markdown"
        assert skipped["references/data.csv"] == "not-markdown"
        assert skipped[".git"] == "dot-entry"
        assert skipped["index.md"] == skipped["log.md"] == "reserved-file"

    def test_the_deprecated_document_is_not_deprecated_here(
        self, workspace: Ws, tmp_path: Path
    ) -> None:
        root, _, _ = workspace
        adopted = {a.foreign_id: a for a in plan(root, foreign_copy(tmp_path)).adopted}
        metadata, _ = okf.load_frontmatter(adopted["concepts/legacy"].text)
        assert not okf.is_marked_deprecated(metadata)
        assert not okf.declares_deprecated(metadata)
        assert "status" not in metadata
        assert (
            imported_block(adopted["concepts/legacy"].text)["frontmatter"]["status"]
            == "deprecated"
        )

    def test_repair_migrates_none_of_the_imported_documents(
        self, git_workspace: Ws, tmp_path: Path
    ) -> None:
        from openkos.application import repair

        root, layout, _ = git_workspace
        publish(root, plan(root, foreign_copy(tmp_path)))
        repair_plan = repair.plan_repair(layout.bundle_dir)
        assert isinstance(repair_plan, repair.RepairPlan)
        assert repair_plan.document_rewrites == []
        assert repair_plan.has_work is False

    def test_the_v01_fixture_plans_best_effort_and_records_its_version(
        self, workspace: Ws
    ) -> None:
        """Its root index declares `okf_version: "0.1"`, a version this engine
        does not write: it is read best-effort and the observed version is
        carried for the preview."""
        root, _, _ = workspace
        before = {p: p.read_bytes() for p in V01_FIXTURE.rglob("*") if p.is_file()}
        import_plan = plan(root, V01_FIXTURE)
        assert import_plan.okf_version == "0.1"
        assert import_plan.adopted
        assert {
            p: p.read_bytes() for p in V01_FIXTURE.rglob("*") if p.is_file()
        } == before

    @pytest.mark.parametrize(
        "root_index", [None, "# Concepts\n", "---\ntitle: x\n---\n"]
    )
    def test_an_absent_version_is_recorded_as_absent(
        self, workspace: Ws, tmp_path: Path, root_index: str | None
    ) -> None:
        root, _, _ = workspace
        foreign = small_bundle(tmp_path, **{"a.md": doc()})
        if root_index is not None:
            write_foreign(foreign, "index.md", root_index)
        import_plan = plan(root, foreign)
        assert import_plan.okf_version is None
        assert imported_block(import_plan.anchors[0].text)["okf_version"] is None


# --- 4.16 the resource audit (design D4) --------------------------------------


@pytest.fixture
def imported_sources(
    git_workspace: Ws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, config.WorkspaceLayout, config.Config]:
    """A workspace holding a LOCAL `raw/notes.txt` and an import of two Source
    documents: one whose foreign `resource` names `raw/notes.txt` (the
    dangerous one: it would name the local raw file), one with a URL
    `resource`. Both also carry the foreign retry markers every local
    consumer keys on."""
    root, layout, cfg = git_workspace
    (layout.raw_dir / "notes.txt").write_text("the local original\n", encoding="utf-8")
    markers = {
        "ingest_pending": True,
        "extraction_status": "failed",
        "extraction_notice": "judge-degraded",
    }
    foreign = small_bundle(
        tmp_path,
        **{
            "sources__notes.md": okf.dump_frontmatter(
                {"type": "Source", "title": "Notes", "resource": "raw/notes.txt"}
                | markers,
                "# Notes\n",
            ),
            "sources__web.md": okf.dump_frontmatter(
                {
                    "type": "Source",
                    "title": "Web",
                    "resource": "https://example.org/page",
                }
                | markers,
                "# Web\n",
            ),
        },
    )
    publish(root, plan(root, foreign), commits=Commits())
    monkeypatch.setattr(
        "openkos.application.next_action.fts_index_present", lambda _path: True
    )
    monkeypatch.setattr(
        "openkos.application.next_action.vector_store_is_empty", lambda _path: False
    )
    return root, layout, cfg


class TestResourceAudit:
    DANGEROUS = f"imports/{NS}/sources/notes"
    LINKED = f"imports/{NS}/sources/web"

    def test_a_foreign_path_resource_lands_only_under_the_inert_key(
        self, imported_sources: Ws
    ) -> None:
        _, layout, _ = imported_sources
        text = (layout.bundle_dir / f"{self.DANGEROUS}.md").read_text(encoding="utf-8")
        metadata, _ = okf.load_frontmatter(text)
        assert "resource" not in metadata
        assert imported_block(text)["frontmatter"]["resource"] == "raw/notes.txt"
        for retry_key in ("ingest_pending", "extraction_status", "extraction_notice"):
            assert retry_key not in metadata

    def test_a_url_resource_stays_where_it_is(self, imported_sources: Ws) -> None:
        _, layout, _ = imported_sources
        metadata, _ = okf.load_frontmatter(
            (layout.bundle_dir / f"{self.LINKED}.md").read_text(encoding="utf-8")
        )
        assert metadata["resource"] == "https://example.org/page"

    @pytest.mark.parametrize("which", ["DANGEROUS", "LINKED"])
    def test_purge_never_resolves_a_raw_path(
        self, imported_sources: Ws, which: str
    ) -> None:
        from openkos.application import lifecycle

        root, layout, _ = imported_sources
        purge_plan = lifecycle.prepare_purge(
            root, layout, getattr(self, which), scope="self", now=NOW
        )
        assert [
            target
            for target in purge_plan.disclosure.expunge_targets
            if target.startswith("raw/")
        ] == []
        assert purge_plan.disclosure.raw_absence is True
        assert (layout.raw_dir / "notes.txt").read_text(encoding="utf-8") == (
            "the local original\n"
        )

    @pytest.mark.parametrize("which", ["DANGEROUS", "LINKED"])
    def test_forget_never_names_a_local_raw_copy(
        self, imported_sources: Ws, which: str
    ) -> None:
        from openkos.application import lifecycle

        root, layout, cfg = imported_sources
        forget_plan = lifecycle.prepare_forget(
            root, layout, getattr(self, which), scope="self", now=NOW, cfg=cfg
        )
        assert forget_plan.orphaned_raw == ()

    def test_lint_neither_counts_the_local_raw_file_nor_hints_a_retry(
        self, imported_sources: Ws
    ) -> None:
        from openkos.application import lint as lint_service

        _, layout, _ = imported_sources
        report = lint_service.build_lint_report(layout)
        assert [f.path for f in report.unreferenced_raw] == ["raw/notes.txt"]
        for findings in (
            report.unextracted,
            report.unjudged,
            report.unevidenced,
            report.staging_dropped,
        ):
            assert [f for f in findings if "imports/" in f.path] == []
        assert report.dangling == []
        assert report.orphans == []

    def test_next_recommends_nothing_about_an_imported_document(
        self, imported_sources: Ws
    ) -> None:
        from openkos.application import next_action

        _, layout, _ = imported_sources
        result = next_action.next_action(layout)
        if result.action is not None:
            assert not any(
                subject.startswith("imports/")
                for subject in result.action.subjects or ()
            )
            assert "imports/" not in result.action.command
            assert "retry" not in result.action.reason.lower()
        assert not any("imports/" in line for line in result.declinations)


# --- the cross-platform smoke: the real staging and rename path --------------


@pytest.mark.cross_platform_smoke
def test_staging_and_the_final_rename_work_on_this_platform(tmp_path: Path) -> None:
    """Plan and publish through the real reader, the real staging directory and
    the real `os.replace` of a directory, with no git: a Windows runner cannot
    replace an existing directory or an open file, so this is the path that
    differs between platforms."""
    root = tmp_path / "ws"
    layout, _ = make_workspace(root)
    foreign = foreign_copy(tmp_path)
    import_plan = plan(root, foreign)
    outcome = import_service.publish_import(
        root,
        layout,
        import_plan,
        commit_section=contextlib.nullcontext,
        load_config=config.read_config,
        autocommit=lambda _root, _paths, _message: None,
    )
    imports_dir = layout.bundle_dir / "imports"
    assert outcome.adopted == 5
    assert (imports_dir / NS / "concepts" / "skills" / "python.md").is_file()
    assert sorted(p.name for p in imports_dir.iterdir()) == [
        NS,
        f"{NS}--confidential.md",
        f"{NS}--private.md",
    ]
    assert okf.check_conformance(layout.bundle_dir) == []
    # a second run into the same namespace is refused, never merged
    with pytest.raises(import_service.ImportRefusal) as caught:
        plan(root, foreign)
    assert caught.value.code == "namespace-exists"
