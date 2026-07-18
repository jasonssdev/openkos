"""Unit tests for the OKF adapter: the one seam that knows the format's shape.

`model/okf.py` owns frontmatter framing, reserved filenames, and the
conformance rules of OKF §9 (docs/okf-alignment.md, AGENTS.md:41). Nothing
else in the engine parses or emits frontmatter.
"""

import os
import stat
from pathlib import Path

import pytest

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


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission-based read failures require a POSIX non-root user",
)
def test_check_conformance_raises_oserror_on_unreadable_file(tmp_path: Path) -> None:
    """An unreadable non-reserved `.md` raises `OSError`, not a conformance violation.

    Root is exempted (`geteuid() == 0`) because root bypasses POSIX
    permission bits, making this an untestable claim on that platform
    rather than a false one, matching the pattern already used for
    `raw/`'s permission tests in `test_init.py`.
    """
    target = tmp_path / "unreadable.md"
    target.write_text("---\ntype: concept\n---\nBody.\n", encoding="utf-8")
    original_mode = stat.S_IMODE(target.stat().st_mode)
    target.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            okf.check_conformance(tmp_path)
    finally:
        target.chmod(original_mode)


def test_check_conformance_raises_unicode_decode_error_on_bad_encoding(
    tmp_path: Path,
) -> None:
    """A non-reserved `.md` with bytes invalid as utf-8 raises `UnicodeDecodeError`."""
    target = tmp_path / "bad-encoding.md"
    target.write_bytes(b"---\ntype: concept\n---\n\xff\xfe invalid utf-8 body\n")

    with pytest.raises(UnicodeDecodeError):
        okf.check_conformance(tmp_path)


def _build_call_source(**overrides: object) -> str:
    """Build a Source concept with realistic defaults, letting tests override
    individual keyword arguments."""
    kwargs: dict[str, object] = {
        "title": "Call with Maria Salazar",
        "description": (
            "Raw source imported from raw/call-with-maria.txt; not yet "
            "compiled or extracted into concepts."
        ),
        "resource": "raw/call-with-maria.txt",
        "tags": ["call", "philosophy"],
        "timestamp": "2026-07-14T18:30:00Z",
        "sensitivity": "private",
        "provenance": ["raw/call-with-maria.txt"],
    }
    kwargs.update(overrides)
    return okf.build_source_concept(**kwargs)  # type: ignore[arg-type]


def test_build_source_concept_emits_required_frontmatter_fields() -> None:
    """`build_source_concept` emits every field the spec requires, plus a
    `# Citations` body (scenario: successful ingest, all required fields)."""
    text = _build_call_source()

    metadata, body = okf.load_frontmatter(text)

    assert metadata["type"] == "Source"
    assert metadata["title"] == "Call with Maria Salazar"
    assert metadata["description"] == (
        "Raw source imported from raw/call-with-maria.txt; not yet "
        "compiled or extracted into concepts."
    )
    assert metadata["resource"] == "raw/call-with-maria.txt"
    assert metadata["tags"] == ["call", "philosophy"]
    assert metadata["timestamp"] == "2026-07-14T18:30:00Z"
    assert metadata["status"] == "active"
    assert metadata["version"] == 1
    assert metadata["freshness"] == "snapshot"
    assert metadata["sensitivity"] == "private"
    assert metadata["provenance"] == ["raw/call-with-maria.txt"]
    assert "# Citations" in body


def test_build_source_concept_passes_check_conformance(tmp_path: Path) -> None:
    """The generated concept passes `check_conformance` (§9 rules 1-2)."""
    text = _build_call_source()
    (tmp_path / "call-with-maria.md").write_text(text, encoding="utf-8")

    assert okf.check_conformance(tmp_path) == []


def test_build_source_concept_description_makes_no_extraction_claim() -> None:
    """The `description` states the source was imported and not yet
    compiled/extracted -- it must not claim extraction occurred (null-compiler
    scope, this slice)."""
    text = _build_call_source(
        description="Raw source imported; not yet compiled or extracted."
    )

    metadata, _ = okf.load_frontmatter(text)

    description = str(metadata["description"])
    assert "not yet" in description
    assert "compiled" in description or "extracted" in description


def test_build_source_concept_sensitivity_equals_passed_value() -> None:
    """`sensitivity` on the generated concept equals the passed value
    (scenario: sensitivity matches config default)."""
    text = _build_call_source(sensitivity="confidential")

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["sensitivity"] == "confidential"
