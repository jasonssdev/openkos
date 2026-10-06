"""`openkos import` end to end (okf-import, #1314; ADR-0050): the real verb, a
real git repository with a pinned identity, and a poisoned `OLLAMA_HOST`, over
the canonical example's export, a mixed-label bundle, the workspace's own
export, a hand-built third-party bundle and the hostile fixtures.

The service is proven in `tests/unit/application/test_import_service.py` and
the verb's surface in `tests/unit/cli/test_import_cmd.py`; these prove the
stages compose: export, import, `lint`, `status`, export again, `git revert`.
"""

import shutil
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from openkos.cli.main import app
from openkos.model import okf
from tests.unit.application.test_import_service import (
    foreign_copy,
    rewrite_config,
    tree_state,
)
from tests.unit.cli.import_support import (
    commit_count,
    foreign_doc,
    git,
    import_args,
    new_workspace,
    pin_environment,
    small_foreign,
    tree_oid,
    write_text,
)

runner = CliRunner()

_EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "good-life-demo"


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def _concept_ids(bundle: Path) -> list[str]:
    return sorted(
        p.relative_to(bundle).with_suffix("").as_posix()
        for p in bundle.rglob("*.md")
        if p.name not in ("index.md", "log.md")
    )


def _lint_section(output: str, heading: str) -> str:
    """The first line under `heading:` in `openkos lint`'s report."""
    lines = output.splitlines()
    return lines[lines.index(f"{heading}:") + 1].strip()


def _inert(metadata: dict[str, object]) -> dict[str, object]:
    """The foreign frontmatter an adopted document keeps, under `imported`."""
    block = metadata["imported"]
    assert isinstance(block, dict)
    kept = block["frontmatter"]
    assert isinstance(kept, dict)
    return kept


def _export(out: Path, *flags: str) -> Result:
    return runner.invoke(app, ["export", str(out), *flags, "--auto"])


@pytest.fixture
def demo_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The canonical example's export with its private half, made from a copy
    of the example (read-only source)."""
    pin_environment(monkeypatch)
    demo = tmp_path / "good-life-demo"
    shutil.copytree(_EXAMPLE, demo)
    monkeypatch.chdir(demo)
    out = tmp_path / "demo-export"
    result = _export(out, "--include-private")
    assert result.exit_code == 0, result.output
    return out


# --------------------------------------------------------------------------- #
# the canonical example: export, init, import, lint, status, export again
# --------------------------------------------------------------------------- #


def test_the_example_round_trips_into_a_fresh_workspace(
    demo_export: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exported = _concept_ids(demo_export)
    assert exported, "the fixture exported nothing"
    root = new_workspace(tmp_path, monkeypatch, "fresh")
    commits = commit_count(root)

    result = runner.invoke(app, import_args(demo_export))

    assert result.exit_code == 0, result.stderr
    bundle = root / "bundle"
    imported = bundle / "imports" / "demo"
    for concept_id in exported:
        assert (imported / f"{concept_id}.md").is_file(), concept_id
    assert okf.check_conformance(bundle) == []
    assert commit_count(root) == commits + 1
    assert git(root, "status", "--porcelain") == ""

    lint = runner.invoke(app, ["lint"])
    assert lint.exit_code == 0, lint.stderr
    for heading in (
        "Orphan pages",
        "Dangling references",
        "Dangling provenance",
        "Unbacked provenance",
        "Below-source sensitivity",
        "Dot-directory markdown",
        "Non-NFC names",
    ):
        assert _lint_section(lint.stdout, heading).startswith("No "), (
            heading,
            lint.stdout,
        )
    assert runner.invoke(app, ["status"]).exit_code == 0

    again = tmp_path / "second-export"
    second = _export(again, "--include-private")
    assert second.exit_code == 0, second.stderr
    assert "below their sources" not in second.stdout
    assert set(_concept_ids(again)) == {
        *(f"imports/demo/{c}" for c in exported),
        "imports/demo--private",
    }


# --------------------------------------------------------------------------- #
# the ADR-0048 regression: mixed labels export without below-source withholding
# --------------------------------------------------------------------------- #


def _mixed_bundle(tmp_path: Path) -> Path:
    return small_foreign(
        tmp_path,
        "mixed",
        pub=foreign_doc(title="Pub", sensitivity="public"),
        priv=foreign_doc(title="Priv", sensitivity="private"),
        conf=foreign_doc(title="Conf", sensitivity="confidential"),
    )


def _public_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = new_workspace(tmp_path, monkeypatch, "pubws")
    rewrite_config(root, default="public")
    git(root, "add", "openkos.yaml")
    git(root, "commit", "-q", "-m", "public default")
    return root


def test_each_label_gets_its_own_anchor_and_each_document_cites_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _public_workspace(tmp_path, monkeypatch)

    assert runner.invoke(app, import_args(_mixed_bundle(tmp_path))).exit_code == 0

    imports = root / "bundle" / "imports"
    for label in ("public", "private", "confidential"):
        assert (imports / f"demo--{label}.md").is_file(), label
    assert "imports/demo--public" in (imports / "demo" / "pub.md").read_text("utf-8")
    assert "imports/demo--private" in (imports / "demo" / "priv.md").read_text("utf-8")
    assert "imports/demo--confidential" in (imports / "demo" / "conf.md").read_text(
        "utf-8"
    )


def test_a_mixed_label_import_exports_without_below_source_withholding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _public_workspace(tmp_path, monkeypatch)

    assert runner.invoke(app, import_args(_mixed_bundle(tmp_path))).exit_code == 0

    plain = tmp_path / "plain-export"
    default = _export(plain)
    assert default.exit_code == 0, default.stderr
    assert "below their sources" not in default.stdout
    # The document and its anchor, at each label.
    assert "will export 2 concept(s)" in default.stdout
    assert "2 confidential" in default.stdout
    assert "2 private (pass --include-private" in default.stdout
    exported = _concept_ids(plain)
    assert "imports/demo/pub" in exported
    assert "imports/demo/priv" not in exported
    assert "imports/demo/conf" not in exported

    both = tmp_path / "private-export"
    wider = _export(both, "--include-private")
    assert wider.exit_code == 0, wider.stderr
    assert "below their sources" not in wider.stdout
    assert {"imports/demo/pub", "imports/demo/priv"} <= set(_concept_ids(both))
    assert "imports/demo/conf" not in _concept_ids(both)


def test_a_document_raised_above_its_anchor_exports_as_withheld_in_the_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _public_workspace(tmp_path, monkeypatch)
    assert runner.invoke(app, import_args(_mixed_bundle(tmp_path))).exit_code == 0
    raised = runner.invoke(
        app, ["set-sensitivity", "imports/demo/pub", "confidential", "--auto"]
    )
    assert raised.exit_code == 0, raised.stderr
    anchor = root / "bundle" / "imports" / "demo--public.md"
    assert "Pub" in anchor.read_text("utf-8")  # its title stays in the lower anchor

    out = tmp_path / "raised-export"
    result = _export(out)

    assert result.exit_code == 0, result.stderr
    exported_anchor = (out / "imports" / "demo--public.md").read_text("utf-8")
    assert "[withheld]" in exported_anchor
    assert "(/imports/demo/pub.md)" not in exported_anchor
    assert "imports/demo/pub" not in _concept_ids(out)


def test_a_document_lowered_below_its_anchor_is_withheld_unless_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = new_workspace(tmp_path, monkeypatch)
    foreign = small_foreign(tmp_path, doc=foreign_doc(title="Doc"))
    assert runner.invoke(app, import_args(foreign)).exit_code == 0
    lowered = runner.invoke(
        app,
        [
            "set-sensitivity",
            "imports/demo/doc",
            "public",
            "--auto",
            "--allow-downgrade",
        ],
    )
    assert lowered.exit_code == 0, lowered.stderr
    assert git(root, "status", "--porcelain") == ""

    out = tmp_path / "plain"
    withheld = _export(out, "--include-private")
    assert withheld.exit_code == 0, withheld.stderr
    assert "1 below their sources" in withheld.stdout
    assert "--allow-below-source" in withheld.stdout
    assert "imports/demo/doc" not in _concept_ids(out)

    allowed = _export(tmp_path / "allowed", "--include-private", "--allow-below-source")
    assert allowed.exit_code == 0, allowed.stderr
    assert "imports/demo/doc" in _concept_ids(tmp_path / "allowed")


# --------------------------------------------------------------------------- #
# the workspace's own export, imported back; the commit reverts cleanly
# --------------------------------------------------------------------------- #


def _origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git-backed copy of the example, committed clean, process inside."""
    pin_environment(monkeypatch)
    root = tmp_path / "origin"
    shutil.copytree(_EXAMPLE, root)
    monkeypatch.chdir(root)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "origin")
    return root


def test_importing_an_export_back_changes_only_index_log_and_the_new_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _origin(tmp_path, monkeypatch)
    out = tmp_path / "out"
    assert _export(out, "--include-private").exit_code == 0
    before = _tree(root)
    pre_import_tree = tree_oid(root)
    commits = commit_count(root)

    result = runner.invoke(app, import_args(out, "again"))

    assert result.exit_code == 0, result.stderr
    after = _tree(root)
    changed = {rel for rel, data in before.items() if after.get(rel) != data}
    assert changed == {"bundle/index.md", "bundle/log.md"}
    added = set(after) - set(before)
    assert added
    assert all(rel.startswith("bundle/imports/again") for rel in added), added
    assert commit_count(root) == commits + 1
    assert git(root, "status", "--porcelain") == ""

    sha = git(root, "rev-parse", "HEAD")
    refused = tree_state(root)
    second = runner.invoke(app, import_args(out, "again"))
    assert second.exit_code == 1
    assert "re-import into an existing namespace is not supported" in second.stderr
    assert tree_state(root) == refused
    assert commit_count(root) == commits + 1

    git(root, "revert", "--no-edit", sha)
    assert tree_oid(root) == pre_import_tree
    assert git(root, "status", "--porcelain") == ""
    assert _tree(root) == before


# --------------------------------------------------------------------------- #
# a hand-built third-party shape
# --------------------------------------------------------------------------- #


def test_a_third_party_shaped_bundle_is_adopted_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = new_workspace(tmp_path, monkeypatch)
    foreign = small_foreign(
        tmp_path,
        "third-party",
        **{
            "notes__v01": okf_v01_note(),
            "my notes": foreign_doc(
                title="My notes", body="# My notes\n\nSee [gone](/missing/page.md).\n"
            ),
        },
    )
    write_text(foreign, "assets/diagram.png", "not markdown")
    write_text(foreign, "README.txt", "not a concept")

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "    README.txt (not-markdown)" in lines
    assert "    assets/diagram.png (not-markdown)" in lines
    assert "    my notes.md -> my-notes.md" in lines
    imported = root / "bundle" / "imports" / "demo"
    assert not (imported / "README.txt").exists()
    assert not (imported / "assets").exists()
    # The v0.1 `timestamp` is kept inert, under the `imported` block.
    meta = okf.load_frontmatter((imported / "notes" / "v01.md").read_text("utf-8"))[0]
    assert _inert(meta)["timestamp"] == "2026-01-02T03:04:05Z"
    assert "timestamp" not in {k for k in meta if k != "imported"}
    # The broken link is tolerated and stays inside the namespace.
    renamed = (imported / "my-notes.md").read_text("utf-8")
    assert "[gone](/imports/demo/missing/page.md)" in renamed
    assert runner.invoke(app, ["lint"]).exit_code == 0
    assert okf.check_conformance(root / "bundle") == []


def okf_v01_note() -> str:
    return (
        "---\ntype: Concept\ntitle: A v0.1 note\n"
        'timestamp: "2026-01-02T03:04:05Z"\n---\n\n# A v0.1 note\n\nOld shape.\n'
    )


def test_the_shared_third_party_fixture_reports_every_skip_and_stays_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = new_workspace(tmp_path, monkeypatch)
    foreign = foreign_copy(tmp_path)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "  skipped (5):" in lines
    legacy = (
        root / "bundle" / "imports" / "demo" / "concepts" / "legacy.md"
    ).read_text("utf-8")
    metadata = okf.load_frontmatter(legacy)[0]
    assert not okf.declares_deprecated(metadata)
    assert _inert(metadata)["status"] == "deprecated"


# --------------------------------------------------------------------------- #
# hostile trees, through the verb: refused with their own code, nothing written
# --------------------------------------------------------------------------- #


def _refused_with(
    root: Path, foreign: Path, code: str, path: str | None = None
) -> None:
    before = tree_state(root)
    oid = tree_oid(root)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 1, result.output
    assert f"the foreign bundle was refused ({code})" in result.stderr
    if path is not None:
        assert f"({code}): {path}." in result.stderr
    assert str(foreign) not in result.stderr
    assert tree_state(root) == before
    assert tree_oid(root) == oid
    assert git(root, "status", "--porcelain") == ""


def _hostile_symlink(foreign: Path) -> None:
    (foreign / "link.md").symlink_to(foreign / "a.md")


def _hostile_alias(foreign: Path) -> None:
    write_text(foreign, "bomb.md", "---\ntype: A\na: &x [1, 2]\nb: *x\n---\nbody\n")


def _hostile_non_mapping(foreign: Path) -> None:
    write_text(foreign, "odd.md", "---\n- a\n- b\n---\nbody\n")


@pytest.mark.parametrize(
    ("code", "path", "build"),
    [
        pytest.param("symlink", "link.md", _hostile_symlink, id="symlink"),
        pytest.param("frontmatter-alias", "bomb.md", _hostile_alias, id="alias-bomb"),
        pytest.param(
            "frontmatter-not-a-mapping",
            "odd.md",
            _hostile_non_mapping,
            id="non-mapping-root",
        ),
    ],
)
def test_a_hostile_tree_is_refused_with_its_own_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    path: str,
    build: Callable[[Path], None],
) -> None:
    root = new_workspace(tmp_path, monkeypatch)
    foreign = small_foreign(tmp_path, a=foreign_doc())
    build(foreign)

    _refused_with(root, foreign, code, path)


@pytest.mark.parametrize(
    ("code", "cap", "value", "build"),
    [
        ("file-too-large", "FOREIGN_MAX_FILE_BYTES", 120, "big"),
        ("bundle-too-large", "FOREIGN_MAX_TOTAL_BYTES", 45, "two"),
        ("too-many-files", "FOREIGN_MAX_DOCUMENTS", 1, "two"),
    ],
)
def test_a_cap_refuses_through_the_verb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    cap: str,
    value: int,
    build: str,
) -> None:
    root = new_workspace(tmp_path, monkeypatch)
    foreign = small_foreign(tmp_path, a=foreign_doc())
    if build == "big":
        write_text(foreign, "a.md", foreign_doc(body="# A\n\n" + "x" * 400 + "\n"))
    else:
        write_text(foreign, "b.md", foreign_doc())
    monkeypatch.setattr(okf, cap, value)

    _refused_with(root, foreign, code)


def test_a_case_collision_is_refused_through_the_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = new_workspace(tmp_path, monkeypatch)
    foreign = small_foreign(tmp_path, a=foreign_doc())
    write_text(foreign, "Notes/Foo.md", foreign_doc())
    write_text(foreign, "notes/foo.md", foreign_doc())
    if len([p for p in foreign.iterdir() if p.name.lower() == "notes"]) != 2:
        pytest.skip(
            "case-insensitive filesystem; the reader's string-level tests cover it"
        )

    _refused_with(root, foreign, "case-collision")


def test_an_nfc_nfd_collision_is_refused_through_the_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = new_workspace(tmp_path, monkeypatch)
    foreign = small_foreign(tmp_path, a=foreign_doc())
    nfc = "café.md"
    write_text(foreign, "n/" + nfc, foreign_doc())
    write_text(foreign, "n/" + unicodedata.normalize("NFD", nfc), foreign_doc())
    if len(list((foreign / "n").iterdir())) != 2:
        pytest.skip("APFS normalizes names; Linux CI is the first real run")

    _refused_with(root, foreign, "nfc-collision")


def test_a_foreign_public_document_in_a_stock_workspace_is_private_and_exports_with_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stock workspace folds a foreign `public` to its `private` floor, so the
    document exports with `--include-private` and is not a below-source case.
    Making it public afterwards is a downgrade below its private anchor."""
    root = new_workspace(tmp_path, monkeypatch)
    foreign = small_foreign(
        tmp_path, doc=foreign_doc(title="Doc", sensitivity="public")
    )
    assert runner.invoke(app, import_args(foreign)).exit_code == 0
    meta = okf.load_frontmatter(
        (root / "bundle/imports/demo/doc.md").read_text("utf-8")
    )[0]
    assert meta["sensitivity"] == "private"

    kept = tmp_path / "kept"
    result = _export(kept, "--include-private")

    assert result.exit_code == 0, result.stderr
    assert "below their sources" not in result.stdout
    assert "imports/demo/doc" in _concept_ids(kept)

    assert (
        runner.invoke(
            app,
            [
                "set-sensitivity",
                "imports/demo/doc",
                "public",
                "--auto",
                "--allow-downgrade",
            ],
        ).exit_code
        == 0
    )
    plain = _export(tmp_path / "plain")
    assert plain.exit_code == 1
    assert "no concept is exportable" in plain.stderr
