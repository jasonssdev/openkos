"""The bundle markdown walk does not carry reads through a symlink (#1126).

`config.symlinked_segment` (#926/#937) guarded `forget`/`get` only. The walk
every other reader shares -- `okf.iter_bundle_markdown`, behind `query`,
`reindex`, `lint` and the disclosure gate -- still yielded a symlinked `.md`
leaf, so external bytes (with an external `sensitivity: public`) were admitted
as knowledge. A leaf link is excluded from the walk and surfaced by `lint`.
"""

from pathlib import Path

import pytest

from openkos import lint, sensitivity
from openkos.model import okf

pytestmark = pytest.mark.cross_platform_smoke

_PUBLIC = "---\ntype: Concept\ntitle: N\nsensitivity: public\n---\n\nsecret\n"


@pytest.fixture(autouse=True)
def _skip_if_symlinks_unsupported(tmp_path: Path) -> None:
    probe = tmp_path / ".probe"
    target = tmp_path / ".probe-target"
    target.write_text("x", encoding="utf-8")
    try:
        probe.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink privilege unavailable: {exc}")
    probe.unlink()


def _bundle_with_links(tmp_path: Path) -> tuple[Path, Path]:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "real.md").write_text(_PUBLIC, encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "n.md").write_text(_PUBLIC, encoding="utf-8")
    (bundle / "leak.md").symlink_to(outside / "n.md")
    (bundle / "linked").symlink_to(outside, target_is_directory=True)
    return bundle, outside


def _rel(paths: object, bundle: Path) -> list[str]:
    return sorted(p.relative_to(bundle).as_posix() for p in paths)  # type: ignore[attr-defined]


def test_walk_drops_a_symlinked_leaf_and_a_symlinked_directory(tmp_path: Path) -> None:
    bundle, _ = _bundle_with_links(tmp_path)

    assert _rel(okf.iter_bundle_markdown(bundle), bundle) == ["real.md"]


def test_disclosure_gate_does_not_admit_a_symlinked_public_target(
    tmp_path: Path,
) -> None:
    bundle, _ = _bundle_with_links(tmp_path)

    assert sensitivity.disclosable_concept_ids(bundle) == frozenset({"real"})


def test_symlink_scan_names_each_link_and_not_the_real_file(tmp_path: Path) -> None:
    bundle, _ = _bundle_with_links(tmp_path)

    found = okf.scan_symlinked_bundle_entries(bundle)

    assert _rel(found, bundle) == ["leak.md", "linked"]


def test_lint_reports_each_symlinked_entry(tmp_path: Path) -> None:
    bundle, _ = _bundle_with_links(tmp_path)

    findings = lint.check_symlinked_markdown(bundle)

    assert {(f.kind, f.path) for f in findings} == {
        ("symlinked-markdown", "leak.md"),
        ("symlinked-markdown", "linked"),
    }
    assert all("symlink" in f.detail for f in findings)


def test_lint_reports_nothing_for_a_clean_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "real.md").write_text(_PUBLIC, encoding="utf-8")

    assert lint.check_symlinked_markdown(bundle) == []
