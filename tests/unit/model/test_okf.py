"""Unit tests for the OKF adapter: the one seam that knows the format's shape.

`model/okf.py` owns frontmatter framing, reserved filenames, and the
conformance rules of OKF §9 (docs/okf-alignment.md, AGENTS.md:41). Nothing
else in the engine parses or emits frontmatter.
"""

import os
import stat
from collections.abc import Callable, Iterator
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


def test_check_conformance_round_trip_regression(tmp_path: Path) -> None:
    """Characterization test pinning `check_conformance`'s current output on a
    realistic mixed bundle (clean file, missing-`type` file, unparseable
    frontmatter, reserved files skipped): a clean bundle's non-reserved,
    conformant file contributes NO violation; a missing-`type` file
    contributes the exact rule-2 string; an unparseable-frontmatter file
    contributes a rule-1 violation naming the file; `index.md`/`log.md` are
    skipped entirely; and the two violations are returned in `sorted(rglob())`
    order (`malformed.md` before `missing-type.md`). This test MUST pass
    unmodified both before AND after the `_iter_docs` refactor (Phase
    1.2/D2) -- it is the regression guard for that refactor's
    byte-identical-output requirement (RISK-1)."""
    (tmp_path / "index.md").write_text(
        '---\nokf_version: "0.1"\n---\n', encoding="utf-8"
    )
    (tmp_path / "log.md").write_text("# Directory Update Log\n", encoding="utf-8")
    (tmp_path / "clean.md").write_text(
        "---\ntype: concept\n---\nBody.\n", encoding="utf-8"
    )
    (tmp_path / "missing-type.md").write_text(
        "---\ntitle: no type here\n---\nBody.\n", encoding="utf-8"
    )
    (tmp_path / "malformed.md").write_text(
        "---\ntype: [unclosed\n---\nBody.\n", encoding="utf-8"
    )

    violations = okf.check_conformance(tmp_path)

    assert len(violations) == 2
    malformed_path = tmp_path / "malformed.md"
    missing_type_path = tmp_path / "missing-type.md"
    assert violations[0].startswith(f"{malformed_path}: no parseable frontmatter (")
    assert violations[1] == f"{missing_type_path}: missing non-empty 'type'"
    assert not any(str(tmp_path / "clean.md") in v for v in violations)
    assert not any("index.md" in v or "log.md" in v for v in violations)


def test_check_conformance_still_raises_oserror_after_refactor(
    tmp_path: Path,
) -> None:
    """Regression: `check_conformance` still RAISES `OSError` on an unreadable
    file rather than degrading it to a finding -- only `survey_bundle`
    (Phase 2/D3) is allowed to degrade read errors. Duplicates the intent of
    `test_check_conformance_raises_oserror_on_unreadable_file` under an
    explicit post-refactor-regression name, so Phase 1.2's diff cannot
    silently change `check_conformance`'s raise contract."""
    target = tmp_path / "unreadable.md"
    target.write_text("---\ntype: concept\n---\nBody.\n", encoding="utf-8")
    if os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("permission-based read failures require a POSIX non-root user")
    original_mode = stat.S_IMODE(target.stat().st_mode)
    target.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            okf.check_conformance(tmp_path)
    finally:
        target.chmod(original_mode)


def test_survey_bundle_fresh_empty_bundle(tmp_path: Path) -> None:
    """A fresh, empty bundle surveys to `BundleSurvey(0, 0, [])` (scenario:
    freshly initialized empty bundle)."""
    assert okf.survey_bundle(tmp_path) == okf.BundleSurvey(0, 0, [])


def test_survey_bundle_counts_source_type_as_source(tmp_path: Path) -> None:
    """A file with frontmatter `type: Source` is counted as a source, not a
    concept."""
    (tmp_path / "call.md").write_text(
        "---\ntype: Source\n---\nBody.\n", encoding="utf-8"
    )

    survey = okf.survey_bundle(tmp_path)

    assert survey == okf.BundleSurvey(1, 0, [])


def test_survey_bundle_counts_other_non_empty_type_as_concept(
    tmp_path: Path,
) -> None:
    """A file with any other non-empty `type` (e.g. `concept`) is counted as
    a concept, not a source."""
    (tmp_path / "note.md").write_text(
        "---\ntype: concept\n---\nBody.\n", encoding="utf-8"
    )

    survey = okf.survey_bundle(tmp_path)

    assert survey == okf.BundleSurvey(0, 1, [])


def test_survey_bundle_mixed_sources_and_concepts(tmp_path: Path) -> None:
    """Sources and concepts are counted independently in a mixed bundle
    (scenario: healthy bundle with sources)."""
    (tmp_path / "source-a.md").write_text(
        "---\ntype: Source\n---\nA.\n", encoding="utf-8"
    )
    (tmp_path / "source-b.md").write_text(
        "---\ntype: Source\n---\nB.\n", encoding="utf-8"
    )
    (tmp_path / "concept-a.md").write_text(
        "---\ntype: Person\n---\nA.\n", encoding="utf-8"
    )

    survey = okf.survey_bundle(tmp_path)

    assert survey.sources == 2
    assert survey.concepts == 1
    assert survey.findings == []


def test_survey_bundle_missing_type_is_a_finding_not_counted(tmp_path: Path) -> None:
    """A missing/empty `type` produces a finding and is counted as NEITHER a
    source nor a concept (D3)."""
    (tmp_path / "orphan.md").write_text(
        "---\ntitle: no type here\n---\nBody.\n", encoding="utf-8"
    )

    survey = okf.survey_bundle(tmp_path)

    assert survey.sources == 0
    assert survey.concepts == 0
    assert len(survey.findings) == 1
    assert "missing non-empty 'type'" in survey.findings[0]


def test_survey_bundle_unparseable_frontmatter_is_a_finding_not_counted(
    tmp_path: Path,
) -> None:
    """Unparseable frontmatter produces a finding and is counted as neither
    a source nor a concept (D3)."""
    (tmp_path / "broken.md").write_text(
        "---\ntype: [unclosed\n---\nBody.\n", encoding="utf-8"
    )

    survey = okf.survey_bundle(tmp_path)

    assert survey.sources == 0
    assert survey.concepts == 0
    assert len(survey.findings) == 1
    assert "no parseable frontmatter" in survey.findings[0]


def test_survey_bundle_unreadable_file_is_a_finding_not_counted(
    tmp_path: Path,
) -> None:
    """An unreadable file degrades to a finding rather than raising -- unlike
    `check_conformance`, `survey_bundle` never crashes on a per-file read
    error (D3, Q3)."""
    if os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("permission-based read failures require a POSIX non-root user")
    target = tmp_path / "unreadable.md"
    target.write_text("---\ntype: concept\n---\nBody.\n", encoding="utf-8")
    original_mode = stat.S_IMODE(target.stat().st_mode)
    target.chmod(0o000)
    try:
        survey = okf.survey_bundle(tmp_path)
    finally:
        target.chmod(original_mode)

    assert survey.sources == 0
    assert survey.concepts == 0
    assert len(survey.findings) == 1
    assert "unreadable" in survey.findings[0]


def test_survey_bundle_reports_unreadable_subdirectory_as_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable subdirectory under `bundle_dir` never silently vanishes
    from `survey_bundle`'s output: `_iter_docs`'s `rglob("*.md")` swallows
    `OSError` from `scandir()` on a directory it cannot descend into (stdlib
    `pathlib`/`glob` behavior), so files under it are never yielded AND
    never counted -- but the walk failure itself must still surface as a
    finding, or `status` would report "Nothing needs attention" over an
    unscanned subtree.

    `os.walk`'s `onerror` callback is monkeypatched directly (deterministic,
    no `chmod`) rather than relying on real permission bits."""
    (tmp_path / "readable.md").write_text(
        "---\ntype: concept\n---\nBody.\n", encoding="utf-8"
    )
    locked_dir = tmp_path / "locked"
    locked_dir.mkdir()
    walk_error = OSError(13, "Permission denied", str(locked_dir))

    original_walk = os.walk

    def fake_walk(
        top: str | os.PathLike[str],
        topdown: bool = True,
        onerror: Callable[[OSError], object] | None = None,
        followlinks: bool = False,
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        if onerror is not None:
            onerror(walk_error)
        yield from original_walk(top, topdown, onerror, followlinks)

    monkeypatch.setattr(os, "walk", fake_walk)

    survey = okf.survey_bundle(tmp_path)

    assert survey.sources == 0
    assert survey.concepts == 1
    assert len(survey.findings) == 1
    assert str(locked_dir) in survey.findings[0]
    assert "unreadable directory" in survey.findings[0]


def test_survey_bundle_fully_readable_bundle_has_no_walk_error_findings(
    tmp_path: Path,
) -> None:
    """A fully readable bundle produces NO directory-walk-error findings --
    normal `survey_bundle` output (counts and per-file findings) is
    unchanged by the new walk-error collection."""
    (tmp_path / "clean.md").write_text(
        "---\ntype: concept\n---\nBody.\n", encoding="utf-8"
    )
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    (nested_dir / "source.md").write_text(
        "---\ntype: Source\n---\nBody.\n", encoding="utf-8"
    )

    survey = okf.survey_bundle(tmp_path)

    assert survey.sources == 1
    assert survey.concepts == 1
    assert survey.findings == []


def test_survey_bundle_skips_reserved_filenames(tmp_path: Path) -> None:
    """`index.md`/`log.md` are never counted or reported, matching
    `check_conformance`'s exemption."""
    (tmp_path / "index.md").write_text(
        '---\nokf_version: "0.1"\n---\n', encoding="utf-8"
    )
    (tmp_path / "log.md").write_text("# Directory Update Log\n", encoding="utf-8")

    assert okf.survey_bundle(tmp_path) == okf.BundleSurvey(0, 0, [])


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


def test_build_source_concept_embeds_text_content() -> None:
    """`raw_content` text is embedded verbatim under `## Source content`,
    positioned before `# Citations` (D1/D3, scenario: successful ingest
    embeds verbatim text)."""
    text = _build_call_source(raw_content="hello")

    _, body = okf.load_frontmatter(text)

    assert "## Source content" in body
    assert "hello" in body
    assert body.index("## Source content") < body.index("# Citations")
    assert body.index("hello") < body.index("# Citations")


def test_build_source_concept_binary_fallback_note() -> None:
    """`raw_content=None` (decode failure) renders the honest "could not be
    embedded as text" note, with no `## Source content` heading (D3,
    scenario: undecodable source falls back)."""
    text = _build_call_source(raw_content=None)

    _, body = okf.load_frontmatter(text)

    assert "could not be embedded as text" in body
    assert "## Source content" not in body


def test_build_source_concept_empty_source_note() -> None:
    """`raw_content=""`/whitespace renders a distinct "source is empty" note
    -- different from both the embedded-text and binary-fallback cases (D3,
    scenario: empty source renders a distinct body)."""
    text = _build_call_source(raw_content="   \n  ")

    _, body = okf.load_frontmatter(text)

    assert "file is empty" in body
    assert "## Source content" not in body
    assert "could not be embedded as text" not in body
