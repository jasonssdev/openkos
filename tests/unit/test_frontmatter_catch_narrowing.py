"""A guard around a frontmatter parse catches `okf.FrontmatterError` only.

The behavioural point of the typed error: a site that skips, fails closed,
or preserves on an UNPARSEABLE file must still do so, and must no longer
swallow an unrelated failure (a programming error) that happens to be raised
from the same call. One representative site per policy class.
"""

from pathlib import Path

import pytest

from openkos import lint
from openkos.bundle import provenance, source_titles
from openkos.model import okf
from openkos.resolution import volatility_typing

_MALFORMED = "---\na: [\n---\nbody"
_SOURCE = "---\ntype: Source\ntitle: T\nresource: raw/a.txt\n---\nbody"


def _boom(_text: str) -> tuple[dict[str, object], str]:
    raise RuntimeError("a programming error, not a parse failure")


# -- class 1: skip the file ---------------------------------------------------


def test_scan_source_titles_skips_a_malformed_file() -> None:
    result = source_titles.scan_source_titles(
        {"sources/bad.md": _MALFORMED, "sources/ok.md": _SOURCE}
    )
    classified = [
        *(c.concept_id for c in result.candidates),
        *(s.concept_id for s in result.skipped),
        *(w.concept_id for w in result.warned),
    ]
    assert classified == ["sources/ok"]


def test_scan_source_titles_does_not_swallow_a_programming_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(okf, "load_frontmatter", _boom)
    with pytest.raises(RuntimeError, match="programming error"):
        source_titles.scan_source_titles({"sources/ok.md": _SOURCE})


# -- class 2: preserve (a None means "skip, keep it out of the purge set") ----


def test_parse_provenance_entry_preserves_a_malformed_file() -> None:
    assert provenance.parse_provenance_entry(_MALFORMED) is None


def test_parse_provenance_entry_does_not_swallow_a_programming_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(okf, "load_frontmatter", _boom)
    with pytest.raises(RuntimeError, match="programming error"):
        provenance.parse_provenance_entry("---\nprovenance: []\n---\n")


# -- class 3: fail closed -----------------------------------------------------


def _doc(path: Path) -> lint.LintDoc:
    return lint.LintDoc(
        path=path,
        identity="a",
        rel_dir="",
        body="",
        freshness="",
        type="Person",
        volatility="",
    )


def test_reread_guard_fails_closed_on_malformed_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text(_MALFORMED, encoding="utf-8")
    assert (
        volatility_typing._reread_sensitivity_blocked(
            _doc(path), include_confidential=False
        )
        is True
    )


def test_reread_guard_does_not_swallow_a_programming_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "a.md"
    path.write_text("---\ntype: Person\n---\nbody", encoding="utf-8")
    monkeypatch.setattr(okf, "load_frontmatter", _boom)
    with pytest.raises(RuntimeError, match="programming error"):
        volatility_typing._reread_sensitivity_blocked(
            _doc(path), include_confidential=False
        )
