"""Unit tests for the OKF adapter: the one seam that knows the format's shape.

`model/okf.py` owns frontmatter framing, reserved filenames, and the
conformance rules of OKF §9 (docs/okf-alignment.md, AGENTS.md:41). Nothing
else in the engine parses or emits frontmatter.
"""

from pathlib import Path

from openkos.model import okf


def test_okf_version_is_0_1() -> None:
    """The engine targets OKF v0.1, per docs/okf-alignment.md:65."""
    assert okf.OKF_VERSION == "0.1"


def test_reserved_filenames() -> None:
    """§6/§7 give `index.md` and `log.md` a fixed structure; nothing else is reserved."""
    assert frozenset({"index.md", "log.md"}) == okf.RESERVED_FILENAMES


def test_frontmatter_round_trip() -> None:
    """Dumping then loading returns the same parsed value; byte quoting is not asserted."""
    text = okf.dump_frontmatter({"okf_version": okf.OKF_VERSION})

    metadata, body = okf.load_frontmatter(text)

    assert metadata == {"okf_version": "0.1"}
    assert body == ""


def test_check_conformance_passes_on_valid_frontmatter(tmp_path: Path) -> None:
    """§9 rules 1-2 pass on a non-reserved file with a non-empty `type`."""
    (tmp_path / "concept.md").write_text(
        "---\ntype: concept\n---\nBody.\n", encoding="utf-8"
    )

    assert okf.check_conformance(tmp_path) == []


def test_check_conformance_fails_on_missing_frontmatter(tmp_path: Path) -> None:
    """§9 rule 1 fails when a non-reserved file has no frontmatter block at all."""
    (tmp_path / "note.md").write_text("Just text, no frontmatter.\n", encoding="utf-8")

    violations = okf.check_conformance(tmp_path)

    assert len(violations) == 1


def test_check_conformance_fails_on_malformed_yaml(tmp_path: Path) -> None:
    """§9 rule 1 fails when a frontmatter block exists but its YAML does not parse."""
    (tmp_path / "broken.md").write_text(
        "---\ntype: [unclosed\n---\nBody.\n", encoding="utf-8"
    )

    violations = okf.check_conformance(tmp_path)

    assert len(violations) == 1


def test_check_conformance_fails_on_empty_type(tmp_path: Path) -> None:
    """§9 rule 2 fails when frontmatter is present but `type` is missing."""
    (tmp_path / "orphan.md").write_text(
        "---\ntitle: no type here\n---\nBody.\n", encoding="utf-8"
    )

    violations = okf.check_conformance(tmp_path)

    assert len(violations) == 1


def test_check_conformance_skips_reserved_filenames(tmp_path: Path) -> None:
    """Reserved files never need frontmatter, per §9 rule 1's exemption."""
    (tmp_path / "index.md").write_text(
        '---\nokf_version: "0.1"\n---\n', encoding="utf-8"
    )
    (tmp_path / "log.md").write_text("# Directory Update Log\n", encoding="utf-8")

    assert okf.check_conformance(tmp_path) == []


def test_check_conformance_passes_vacuously_on_empty_bundle(tmp_path: Path) -> None:
    """No non-reserved `.md` files means rules 1-2 hold vacuously (scenario 14)."""
    assert okf.check_conformance(tmp_path) == []
