"""The import service (okf-import, slice 4, design D7): `plan_import` (Phase A,
no lock, no write) and `publish_import` (Phase B, under the lock).

Every refusal leaves the workspace byte-identical, asserted by hashing the
whole tree (directories included) before and after. Nothing here reaches a
model: the backend resolvers are patched to raise where it matters.
"""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from openkos import config, lock
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

    @pytest.mark.parametrize("rel", ["My Note.md", "My Folder/note.md", "a\u00a0b.md"])
    def test_a_name_with_internal_whitespace_is_refused(
        self, workspace: Ws, tmp_path: Path, rel: str
    ) -> None:
        root, _, _ = workspace
        foreign = tmp_path / "foreign"
        write_foreign(foreign, rel, doc())
        refusal = refusal_of(root, foreign)
        assert refusal.code == "whitespace-in-name"
        assert rel.split("/")[0] in refusal.reason
        assert str(tmp_path) not in refusal.reason
        assert refusal.retry_safe is False


class TestPathologicalNamesCannotBeLinked:
    """Why a foreign Concept ID with whitespace is refused rather than linked:
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
            link = f"(/imports/{NS}/{adopted.foreign_id}.md)"
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
