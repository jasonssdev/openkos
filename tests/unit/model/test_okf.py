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

_REPO_ROOT = Path(__file__).resolve().parents[3]


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


def test_check_conformance_passes_on_root_index_with_frontmatter(
    tmp_path: Path,
) -> None:
    """§9 rule 3 + §11: the bundle-ROOT `index.md` is exempt from the
    frontmatter ban -- an `okf_version` frontmatter block there is not a
    violation."""
    (tmp_path / "index.md").write_text(
        '---\nokf_version: "0.1"\n---\n', encoding="utf-8"
    )

    assert okf.check_conformance(tmp_path) == []


def test_check_conformance_fails_on_nested_index_with_parseable_frontmatter(
    tmp_path: Path,
) -> None:
    """§9 rule 3 + §6: a non-root `index.md` MUST NOT carry a frontmatter
    block at all -- one with parseable YAML is a violation."""
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "index.md").write_text('---\nokf_version: "0.1"\n---\n', encoding="utf-8")

    violations = okf.check_conformance(tmp_path)

    assert len(violations) == 1
    assert str(nested / "index.md") in violations[0]


def test_check_conformance_fails_on_nested_index_with_malformed_fence(
    tmp_path: Path,
) -> None:
    """§9 rule 3 detects frontmatter by FENCE PRESENCE, not parseability: a
    non-root `index.md` opening with a `---` delimiter and a closing `---`
    fence is a violation even when the YAML between them does not parse --
    §6 forbids frontmatter entirely, so a malformed block is still
    frontmatter."""
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "index.md").write_text(
        "---\nokf_version: [unclosed\n---\n", encoding="utf-8"
    )

    violations = okf.check_conformance(tmp_path)

    assert len(violations) == 1
    assert str(nested / "index.md") in violations[0]


def test_check_conformance_passes_on_nested_index_without_frontmatter(
    tmp_path: Path,
) -> None:
    """A non-root `index.md` with no frontmatter block at all is conformant
    (the only shape §6 allows for a nested `index.md`)."""
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "index.md").write_text(
        "# Sub-index\n\nNo frontmatter here.\n", encoding="utf-8"
    )

    assert okf.check_conformance(tmp_path) == []


def test_check_conformance_passes_on_log_with_valid_iso_date_heading(
    tmp_path: Path,
) -> None:
    """§9 rule 3 + §7: a `log.md` `## ` heading matching `YYYY-MM-DD` is
    conformant."""
    (tmp_path / "log.md").write_text(
        "# Directory Update Log\n\n## 2026-07-14\n\n* Entry.\n", encoding="utf-8"
    )

    assert okf.check_conformance(tmp_path) == []


def test_check_conformance_fails_on_log_with_malformed_date_heading(
    tmp_path: Path,
) -> None:
    """§9 rule 3 + §7: a `log.md` `## ` heading that is not an ISO-8601 date
    is a violation naming the file and the offending heading."""
    (tmp_path / "log.md").write_text(
        "# Directory Update Log\n\n## July 2026\n\n* Entry.\n", encoding="utf-8"
    )

    violations = okf.check_conformance(tmp_path)

    assert len(violations) == 1
    assert str(tmp_path / "log.md") in violations[0]
    assert "July 2026" in violations[0]


def test_check_conformance_passes_on_reference_bundle() -> None:
    """The reference bundle at `examples/good-life-demo/bundle` satisfies all
    three §9 rules, including the rule-3 reserved-file structure this test
    adds -- runs in CI's existing `test` job with no `ci.yml` change."""
    bundle_dir = _REPO_ROOT / "examples" / "good-life-demo" / "bundle"

    assert okf.check_conformance(bundle_dir) == []


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


def _build_call_concept(**overrides: object) -> str:
    """Build a derived Concept/Entity document with realistic defaults,
    letting tests override individual keyword arguments -- mirrors
    `_build_call_source`."""
    kwargs: dict[str, object] = {
        "type": "Concept",
        "title": "Stoicism",
        "description": (
            "Hellenistic school holding that virtue is the only good, and "
            "that freedom comes from knowing what is up to us."
        ),
        "body": "The dichotomy of control separates what is up to us from what is not.",
        "provenance": ["sources/call-with-maria-salazar"],
        "sensitivity": "confidential",
        "timestamp": "2026-07-14T18:30:00Z",
    }
    kwargs.update(overrides)
    return okf.build_concept(**kwargs)  # type: ignore[arg-type]


def test_build_concept_emits_required_frontmatter_fields() -> None:
    """`build_concept` emits every OKF + OpenKOS-layer field
    `build_source_concept` emits, plus an empty `tags` list (design: no
    tagging step in this slice) (Phase 3.1)."""
    text = _build_call_concept()

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["type"] == "Concept"
    assert metadata["title"] == "Stoicism"
    assert metadata["description"] == (
        "Hellenistic school holding that virtue is the only good, and "
        "that freedom comes from knowing what is up to us."
    )
    assert metadata["tags"] == []
    assert metadata["timestamp"] == "2026-07-14T18:30:00Z"
    assert metadata["status"] == "active"
    assert metadata["version"] == 1
    assert metadata["freshness"] == "snapshot"
    assert metadata["sensitivity"] == "confidential"
    assert metadata["provenance"] == ["sources/call-with-maria-salazar"]


def test_build_concept_accepts_entity_type() -> None:
    """`type: Entity` is another member of the closed vocabulary and
    builds a conformant document just like `Concept` (Phase 3.1)."""
    text = _build_call_concept(type="Entity", title="Zettelkasten")

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["type"] == "Entity"
    assert metadata["title"] == "Zettelkasten"


def test_build_concept_accepts_person_type() -> None:
    """`type: Person` is a member of the widened closed vocabulary and
    builds a conformant document just like `Concept`/`Entity`."""
    text = _build_call_concept(type="Person", title="Epictetus")

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["type"] == "Person"
    assert metadata["title"] == "Epictetus"
    assert metadata["freshness"] == "snapshot"


def test_build_concept_accepts_organization_type() -> None:
    """`type: Organization` is a member of the widened closed vocabulary and
    builds a conformant document just like `Concept`/`Entity`."""
    text = _build_call_concept(type="Organization", title="Praxis Foundation")

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["type"] == "Organization"
    assert metadata["title"] == "Praxis Foundation"
    assert metadata["freshness"] == "snapshot"


def test_build_concept_accepts_place_type() -> None:
    """`type: Place` is a member of the closed 5-value vocabulary and builds
    a conformant document just like the other classifiable types (spec:
    "Place routes end-to-end through ingest")."""
    text = _build_call_concept(type="Place", title="Yellowstone National Park")

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["type"] == "Place"
    assert metadata["title"] == "Yellowstone National Park"
    assert metadata["freshness"] == "snapshot"


def test_build_concept_accepts_event_type() -> None:
    """`type: Event` is a member of the widened classifiable vocabulary and
    builds a conformant document just like the other classifiable types
    (spec: "Event and Procedure Route to Dedicated Catalog Sections")."""
    text = _build_call_concept(type="Event", title="Stoicon 2026")

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["type"] == "Event"
    assert metadata["title"] == "Stoicon 2026"
    assert metadata["freshness"] == "snapshot"


def test_build_concept_accepts_procedure_type() -> None:
    """`type: Procedure` is a member of the widened classifiable vocabulary
    and builds a conformant document just like the other classifiable types
    (spec: "Event and Procedure Route to Dedicated Catalog Sections")."""
    text = _build_call_concept(type="Procedure", title="Morning Journaling Routine")

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["type"] == "Procedure"
    assert metadata["title"] == "Morning Journaling Routine"
    assert metadata["freshness"] == "snapshot"


def test_build_concept_accepts_decision_type() -> None:
    """`type: Decision` is a member of the widened classifiable vocabulary
    (the KOM "why" tier) and builds a conformant document just like the
    other classifiable types; `freshness` stays uniform `"snapshot"` even
    though a real Decision's status is mutable (design: Non-Goals, no
    freshness change)."""
    text = _build_call_concept(type="Decision", title="Frame the Essay Around Control")

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["type"] == "Decision"
    assert metadata["title"] == "Frame the Essay Around Control"
    assert metadata["freshness"] == "snapshot"


def test_build_concept_accepts_project_type() -> None:
    """`type: Project` is a member of the widened classifiable vocabulary
    (the KOM "why" tier) and builds a conformant document just like the
    other classifiable types."""
    text = _build_call_concept(type="Project", title="Stoicism Essay Series")

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["type"] == "Project"
    assert metadata["title"] == "Stoicism Essay Series"
    assert metadata["freshness"] == "snapshot"


def test_build_concept_sensitivity_inherited_verbatim() -> None:
    """`sensitivity` is passed straight through, unmodified -- the caller
    (main.py, a later slice) is responsible for reading it off the Source
    (Phase 3.1, scenario: provenance and sensitivity inherited)."""
    text = _build_call_concept(sensitivity="public")

    metadata, _ = okf.load_frontmatter(text)

    assert metadata["sensitivity"] == "public"


def test_build_concept_body_included_when_non_blank() -> None:
    """A non-blank `body` is embedded in the document body, alongside the
    title and description (Phase 3.1)."""
    text = _build_call_concept(body="The dichotomy of control is central.")

    _, body = okf.load_frontmatter(text)

    assert "# Stoicism" in body
    assert "The dichotomy of control is central." in body


def test_build_concept_blank_body_falls_back_to_description() -> None:
    """A blank/whitespace-only `body` falls back to `description`, per the
    `ExtractionResult` contract (`extraction/concept.py`) that leaves
    "the builder falls back to description when this is blank" (Phase 3.1,
    Testing Strategy: body-fallback)."""
    text = _build_call_concept(body="   ")

    _, body = okf.load_frontmatter(text)

    description = (
        "Hellenistic school holding that virtue is the only good, and "
        "that freedom comes from knowing what is up to us."
    )
    assert description in body
    # A blank body must not render the description paragraph twice.
    assert body.count(description) == 1


def test_build_concept_related_section_backlinks_to_source() -> None:
    """The body carries a `## Related` section citing every `provenance`
    entry as the source this object was extracted from, bundle-relative per
    docs/knowledge-object-model.md's link shape (Phase 3.1)."""
    text = _build_call_concept(provenance=["sources/call-with-maria-salazar"])

    _, body = okf.load_frontmatter(text)

    assert "## Related" in body
    assert (
        "[sources/call-with-maria-salazar](/sources/call-with-maria-salazar.md)" in body
    )
    assert "source this was extracted from" in body


def test_build_concept_frontmatter_round_trips() -> None:
    """`build_concept`'s output round-trips through `load_frontmatter` with
    no data loss (Phase 3.1)."""
    text = _build_call_concept()

    metadata, body = okf.load_frontmatter(text)

    assert metadata["type"] == "Concept"
    assert body.strip() != ""


def test_build_concept_passes_check_conformance(tmp_path: Path) -> None:
    """A `build_concept` document passes §9 rules 1-2, same as
    `build_source_concept` (Phase 3.1)."""
    text = _build_call_concept()
    (tmp_path / "stoicism.md").write_text(text, encoding="utf-8")

    assert okf.check_conformance(tmp_path) == []


def test_build_concept_raises_on_invalid_type() -> None:
    """`type` outside the closed `{Concept, Entity, Place, Person,
    Organization}` set fails closed with `ValueError`. `"Animal"` is a
    genuinely invalid sentinel (spec: "Builder raises on unknown type")."""
    with pytest.raises(ValueError, match="type"):
        _build_call_concept(type="Animal")


def test_build_concept_raises_on_blank_title() -> None:
    """An empty or whitespace-only `title` fails closed with `ValueError`
    (Phase 3.2)."""
    with pytest.raises(ValueError, match="title"):
        _build_call_concept(title="   ")


def test_build_concept_raises_on_blank_description() -> None:
    """An empty or whitespace-only `description` fails closed with
    `ValueError` (Phase 3.2)."""
    with pytest.raises(ValueError, match="description"):
        _build_call_concept(description="")


def test_build_concept_raises_on_newline_in_title() -> None:
    """A `title` with an embedded newline (plausible from untrusted LLM
    output) would corrupt the Markdown heading, so it fails closed."""
    with pytest.raises(ValueError, match="title"):
        _build_call_concept(title="Stoicism\n# Injected heading")


def test_build_concept_raises_on_newline_in_description() -> None:
    """A `description` is a single-line lede; an embedded newline fails
    closed rather than emitting a stray paragraph."""
    with pytest.raises(ValueError, match="description"):
        _build_call_concept(description="A school.\nInjected line.")


def test_build_concept_raises_on_empty_provenance() -> None:
    """A derived object always cites the Source it came from, so an empty
    `provenance` list fails closed rather than emitting a dangling
    `## Related` section."""
    with pytest.raises(ValueError, match="provenance"):
        _build_call_concept(provenance=[])


def test_build_concept_backlinks_every_provenance_entry() -> None:
    """The `## Related` section renders one backlink per `provenance` entry,
    in input order."""
    text = _build_call_concept(
        provenance=["sources/first-source", "sources/second-source"]
    )

    _, body = okf.load_frontmatter(text)

    first = body.index("[sources/first-source](/sources/first-source.md)")
    second = body.index("[sources/second-source](/sources/second-source.md)")
    assert first < second


def test_sensitivity_order_pins_the_adr_0003_ordering() -> None:
    """`SENSITIVITY_ORDER` is the canonical least-to-most-restrictive
    ordering ADR-0003 pins; `combine_sensitivity` ranks against this exact
    tuple."""
    assert okf.SENSITIVITY_ORDER == ("public", "private", "confidential")


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("public", "public", "public"),
        ("public", "private", "private"),
        ("public", "confidential", "confidential"),
        ("private", "private", "private"),
        ("private", "confidential", "confidential"),
        ("confidential", "confidential", "confidential"),
    ],
)
def test_combine_sensitivity_returns_the_more_restrictive_side(
    a: str, b: str, expected: str
) -> None:
    """ADR-0003: the combined result is always the MORE sensitive (max-rank)
    of the two inputs, per the full pairwise table."""
    assert okf.combine_sensitivity(a, b) == expected


@pytest.mark.parametrize(
    ("a", "b"), [("public", "private"), ("private", "confidential")]
)
def test_combine_sensitivity_is_commutative(a: str, b: str) -> None:
    """`combine_sensitivity(a, b) == combine_sensitivity(b, a)` -- order of
    the two inputs never affects the result."""
    assert okf.combine_sensitivity(a, b) == okf.combine_sensitivity(b, a)


def test_combine_sensitivity_missing_value_defaults_to_private() -> None:
    """A missing (`None`) sensitivity ranks as `private`, the documented
    config default floor -- never the least-restrictive `public`."""
    assert okf.combine_sensitivity(None, "public") == "private"


def test_combine_sensitivity_blank_value_defaults_to_private() -> None:
    """A present but blank/whitespace-only sensitivity ranks as `private`,
    same as a missing value."""
    assert okf.combine_sensitivity("   ", "public") == "private"


def test_combine_sensitivity_both_missing_is_private() -> None:
    """Two missing values combine to the `private` default, not `public`."""
    assert okf.combine_sensitivity(None, None) == "private"


def test_combine_sensitivity_unrecognized_string_fails_closed_to_confidential() -> None:
    """An unrecognized (malformed) string value ranks as `confidential` --
    the most restrictive level -- rather than being silently ignored."""
    assert okf.combine_sensitivity("top-secret", "public") == "confidential"


def test_combine_sensitivity_non_string_value_fails_closed_to_confidential() -> None:
    """A present but non-string value (e.g. an int or list from dirty
    frontmatter) ranks as `confidential`, never crashes and never silently
    ranks as the least-restrictive level."""
    assert okf.combine_sensitivity(42, "public") == "confidential"
    assert okf.combine_sensitivity(["confidential"], "private") == "confidential"


def test_combine_sensitivity_confidential_dominates_regardless_of_position() -> None:
    """A single `confidential` input always wins, regardless of argument
    order or what the other value is."""
    assert okf.combine_sensitivity("confidential", "public") == "confidential"
    assert okf.combine_sensitivity("public", "confidential") == "confidential"


# -- U2: `build_merged_document` (Phase 2, tasks 2.1/2.2) -----------------


def _survivor_metadata(**overrides: object) -> dict[str, object]:
    """A realistic survivor-side frontmatter dict for merge tests, letting
    callers override individual keys."""
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Stoicism",
        "description": "Survivor description.",
        "status": "active",
        "version": 1,
        "tags": ["philosophy", "stoicism"],
        "timestamp": "2026-07-10T09:00:00Z",
        "freshness": "snapshot",
        "sensitivity": "private",
        "provenance": ["sources/call-a"],
    }
    metadata.update(overrides)
    return metadata


def _absorbed_metadata(**overrides: object) -> dict[str, object]:
    """A realistic absorbed-side frontmatter dict for merge tests, mirroring
    `_survivor_metadata` with distinct default values so conflicts are
    exercised by default."""
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Stoic Philosophy",
        "description": "Absorbed description.",
        "status": "draft",
        "version": 3,
        "tags": ["stoicism", "ethics"],
        "timestamp": "2026-07-14T09:00:00Z",
        "freshness": "verified",
        "sensitivity": "confidential",
        "provenance": ["sources/call-b"],
    }
    metadata.update(overrides)
    return metadata


def test_build_merged_document_scalar_fields_survivor_wins() -> None:
    """Requirement: Frontmatter-Conflict Resolution -- a scalar present on
    both sides keeps the SURVIVOR's value."""
    merged, _ = okf.build_merged_document(
        _survivor_metadata(),
        "Survivor body.",
        _absorbed_metadata(),
        "Absorbed body.",
        "absorbed-id",
    )

    assert merged["title"] == "Stoicism"
    assert merged["description"] == "Survivor description."
    assert merged["status"] == "active"
    assert merged["version"] == 1


def test_build_merged_document_scalar_only_on_absorbed_fills_the_gap() -> None:
    """A scalar present ONLY on the absorbed side (missing from the
    survivor) fills the gap rather than being dropped."""
    survivor = _survivor_metadata()
    del survivor["status"]

    merged, _ = okf.build_merged_document(
        survivor,
        "Survivor body.",
        _absorbed_metadata(),
        "Absorbed body.",
        "absorbed-id",
    )

    assert merged["status"] == "draft"


def test_build_merged_document_list_fields_union_deduped_order_preserving() -> None:
    """List-valued fields (`tags`, `provenance`) union, deduped, preserving
    first-seen order across survivor-then-absorbed."""
    merged, _ = okf.build_merged_document(
        _survivor_metadata(tags=["philosophy", "stoicism"]),
        "Survivor body.",
        _absorbed_metadata(tags=["stoicism", "ethics"]),
        "Absorbed body.",
        "absorbed-id",
    )

    assert merged["tags"] == ["philosophy", "stoicism", "ethics"]
    assert merged["provenance"] == ["sources/call-a", "sources/call-b"]


def test_build_merged_document_freshness_and_timestamp_from_most_recent() -> None:
    """`freshness`+`timestamp` are taken TOGETHER from whichever side has the
    more recent `timestamp` -- here the absorbed side."""
    merged, _ = okf.build_merged_document(
        _survivor_metadata(timestamp="2026-07-10T09:00:00Z", freshness="snapshot"),
        "Survivor body.",
        _absorbed_metadata(timestamp="2026-07-14T09:00:00Z", freshness="verified"),
        "Absorbed body.",
        "absorbed-id",
    )

    assert merged["timestamp"] == "2026-07-14T09:00:00Z"
    assert merged["freshness"] == "verified"


def test_build_merged_document_freshness_survivor_wins_when_more_recent() -> None:
    """The survivor's own `freshness`/`timestamp` is kept when it is the more
    recent of the two."""
    merged, _ = okf.build_merged_document(
        _survivor_metadata(timestamp="2026-07-20T09:00:00Z", freshness="verified"),
        "Survivor body.",
        _absorbed_metadata(timestamp="2026-07-01T09:00:00Z", freshness="snapshot"),
        "Absorbed body.",
        "absorbed-id",
    )

    assert merged["timestamp"] == "2026-07-20T09:00:00Z"
    assert merged["freshness"] == "verified"


def test_build_merged_document_freshness_falls_back_to_survivor_on_malformed_timestamp() -> (
    None
):
    """An unparseable timestamp on either side fails closed to the same
    survivor-wins default as any other scalar, rather than crashing."""
    merged, _ = okf.build_merged_document(
        _survivor_metadata(timestamp="not-a-timestamp", freshness="snapshot"),
        "Survivor body.",
        _absorbed_metadata(timestamp="2026-07-14T09:00:00Z", freshness="verified"),
        "Absorbed body.",
        "absorbed-id",
    )

    assert merged["timestamp"] == "not-a-timestamp"
    assert merged["freshness"] == "snapshot"


def test_build_merged_document_freshness_falls_back_to_survivor_on_non_string_timestamp() -> (
    None
):
    """A non-string `timestamp` (e.g. dirty frontmatter carrying an int)
    also fails closed to survivor-wins, same as an unparseable string."""
    merged, _ = okf.build_merged_document(
        _survivor_metadata(timestamp="2026-07-10T09:00:00Z", freshness="snapshot"),
        "Survivor body.",
        _absorbed_metadata(timestamp=12345, freshness="verified"),
        "Absorbed body.",
        "absorbed-id",
    )

    assert merged["timestamp"] == "2026-07-10T09:00:00Z"
    assert merged["freshness"] == "snapshot"


def test_build_merged_document_sensitivity_recomputed_via_combine_sensitivity() -> None:
    """Sensitivity is RECOMPUTED (high-water-mark), never copied from either
    side verbatim."""
    merged, _ = okf.build_merged_document(
        _survivor_metadata(sensitivity="private"),
        "Survivor body.",
        _absorbed_metadata(sensitivity="confidential"),
        "Absorbed body.",
        "absorbed-id",
    )

    assert merged["sensitivity"] == "confidential"


def test_build_merged_document_body_appends_absorbed_under_delimited_heading() -> None:
    """The merged body is the survivor's body, followed by a delimited
    `## Merged content ({absorbed_id})` heading, followed by the absorbed
    body -- never an overwrite."""
    _, body = okf.build_merged_document(
        _survivor_metadata(),
        "# Survivor\n\nSurvivor body.",
        _absorbed_metadata(),
        "# Absorbed\n\nAbsorbed body.",
        "concepts/absorbed-id",
    )

    assert "Survivor body." in body
    assert "## Merged content (concepts/absorbed-id)" in body
    assert "Absorbed body." in body
    assert body.index("Survivor body.") < body.index("## Merged content")
    assert body.index("## Merged content") < body.index("Absorbed body.")


def test_build_merged_document_body_already_newline_terminated_is_not_doubled() -> None:
    """When the absorbed body already ends with a trailing newline, no
    extra blank line is appended on top of it."""
    _, body = okf.build_merged_document(
        _survivor_metadata(),
        "Survivor body.",
        _absorbed_metadata(),
        "Absorbed body.\n",
        "absorbed-id",
    )

    assert body.endswith("Absorbed body.\n")
    assert not body.endswith("Absorbed body.\n\n")


def test_build_merged_document_list_union_handles_unhashable_items() -> None:
    """A frontmatter list containing unhashable items (e.g. dicts -- OKF's
    unknown-key tolerance permits arbitrary structured values) must union
    without crashing: `_union_dedup` must not require hashability."""
    merged, _ = okf.build_merged_document(
        _survivor_metadata(related=[{"ref": "a"}]),
        "Survivor body.",
        _absorbed_metadata(related=[{"ref": "a"}, {"ref": "b"}]),
        "Absorbed body.",
        "absorbed-id",
    )

    assert merged["related"] == [{"ref": "a"}, {"ref": "b"}]


def test_build_merged_document_freshness_fails_closed_on_mixed_aware_naive_timestamps() -> (
    None
):
    """A timezone-AWARE vs NAIVE timestamp pair must not crash the freshness
    comparison; per `_absorbed_is_more_recent`'s docstring, any comparison
    failure fails closed to `False` (survivor wins)."""
    merged, _ = okf.build_merged_document(
        _survivor_metadata(timestamp="2026-07-10T09:00:00", freshness="snapshot"),
        "Survivor body.",
        _absorbed_metadata(timestamp="2026-07-14T09:00:00Z", freshness="verified"),
        "Absorbed body.",
        "absorbed-id",
    )

    assert merged["timestamp"] == "2026-07-10T09:00:00"
    assert merged["freshness"] == "snapshot"


def test_build_merged_document_excludes_merged_from_key_from_generic_combine() -> None:
    """A pre-existing `merged_from` key on either side is never propagated by
    the generic combine -- the ledger is owned exclusively by `plan_merge`."""
    survivor = _survivor_metadata()
    survivor["merged_from"] = [{"schema": "x"}]

    merged, _ = okf.build_merged_document(
        survivor,
        "Survivor body.",
        _absorbed_metadata(),
        "Absorbed body.",
        "absorbed-id",
    )

    assert okf.MERGED_FROM_KEY not in merged


# -- U2: `merged_from` ledger encode/decode (Phase 2, tasks 2.3/2.4) ------


def _sample_ledger_entry(**overrides: object) -> okf.MergeLedgerEntry:
    """A realistic `MergeLedgerEntry`, including a snapshot whose OWN body
    contains a `---` divider and a fenced code block, so round-trip tests
    exercise the exact drift case ADR-0002 calls out."""
    tricky_snapshot = (
        "---\ntype: Concept\ntitle: Absorbed\n---\n"
        "Body with a fence:\n\n"
        "```python\nx = 1\n---\ny = 2\n```\n\n"
        "And a literal --- divider in text.\n"
    )
    defaults: dict[str, object] = {
        "schema": okf.MERGE_LEDGER_SCHEMA_V1,
        "merged_at": "2026-07-20T00:00:00Z",
        "absorbed_id": "concepts/absorbed-id",
        "absorbed_snapshot": tricky_snapshot,
        "survivor_before": "---\ntype: Concept\n---\nSurvivor before.\n",
        "index_before": "---\nokf_version: '0.1'\n---\n# Concepts\n",
        "log_before": "# Directory Update Log\n\n## 2026-07-19\n\n* Entry.\n",
        "link_rewrites": [
            okf.LinkRewrite(
                file="concepts/other.md",
                old_link="/concepts/absorbed-id.md",
                new_link="/concepts/survivor-id.md",
            )
        ],
        "sensitivity_before": "private",
        "sensitivity_after": "confidential",
    }
    defaults.update(overrides)
    return okf.MergeLedgerEntry(**defaults)  # type: ignore[arg-type]


def test_merge_ledger_entry_round_trips_through_frontmatter_losslessly() -> None:
    """A `merged_from` ledger entry -- including a snapshot whose OWN body
    contains a `---` divider and a fenced code block -- round-trips
    losslessly through `dump_frontmatter`/`load_frontmatter`: every string
    field is restored byte-for-byte, never re-dumped from a reparsed dict
    (D2/ADR-0002)."""
    entry = _sample_ledger_entry()

    metadata: dict[str, object] = {"type": "Concept"}
    metadata[okf.MERGED_FROM_KEY] = okf.encode_merged_from([entry])
    text = okf.dump_frontmatter(metadata, "Survivor body.")

    loaded_metadata, _ = okf.load_frontmatter(text)
    decoded = okf.decode_merged_from(loaded_metadata)

    assert decoded == [entry]


def test_decode_merged_from_absent_key_returns_empty_list() -> None:
    """A survivor with no prior merges (`merged_from` absent) decodes to []."""
    assert okf.decode_merged_from({"type": "Concept"}) == []


def test_decode_merged_from_rejects_non_list_value() -> None:
    """A malformed `merged_from` that is not a list fails closed."""
    with pytest.raises(ValueError, match="merged_from"):
        okf.decode_merged_from({"merged_from": "not-a-list"})


def test_decode_merge_ledger_entry_rejects_missing_field() -> None:
    """A ledger entry dict missing a required field fails closed rather than
    silently defaulting."""
    with pytest.raises(ValueError, match="missing field"):
        okf.decode_merged_from(
            {"merged_from": [{"schema": okf.MERGE_LEDGER_SCHEMA_V1}]}
        )


def test_decode_merge_ledger_entry_rejects_non_mapping_item() -> None:
    """A `merged_from` list item that is not a mapping fails closed."""
    with pytest.raises(ValueError, match="mapping"):
        okf.decode_merged_from({"merged_from": ["not-a-dict"]})


def _valid_encoded_entry(**overrides: object) -> dict[str, object]:
    """A complete, valid plain-dict `merged_from` entry (as
    `encode_merge_ledger_entry` would produce), letting tests corrupt one
    field at a time."""
    entry = okf.encode_merge_ledger_entry(_sample_ledger_entry())
    entry.update(overrides)
    return entry


def test_decode_merge_ledger_entry_rejects_non_mapping_link_rewrite_item() -> None:
    """A `link_rewrites` list item that is not itself a mapping fails
    closed, distinct from a missing/malformed top-level entry field."""
    entry = _valid_encoded_entry(link_rewrites=["not-a-dict"])

    with pytest.raises(ValueError, match="link_rewrites entry must be a mapping"):
        okf.decode_merged_from({"merged_from": [entry]})


def test_decode_merge_ledger_entry_rejects_link_rewrite_missing_field() -> None:
    """A `link_rewrites` list item missing a required field fails closed."""
    entry = _valid_encoded_entry(link_rewrites=[{"file": "concepts/other.md"}])

    with pytest.raises(ValueError, match="link_rewrites entry missing field"):
        okf.decode_merged_from({"merged_from": [entry]})


@pytest.mark.parametrize(
    "snapshot",
    [
        pytest.param(
            "line one   \nline two\t\nline three   \n", id="trailing-whitespace"
        ),
        pytest.param(
            "---\ntitle: Ünïcödé résumé\n---\n日本語のコンテンツ\n\n🎉 emoji too.\n",
            id="unicode",
        ),
        pytest.param("line one\r\nline two\r\nline three\r\n", id="crlf"),
        pytest.param("no trailing newline at all", id="no-trailing-newline"),
    ],
)
def test_merge_ledger_entry_round_trips_adversarial_snapshot_content(
    snapshot: str,
) -> None:
    """Adversarial round-trip losslessness: `encode` -> frontmatter ->
    `decode` must return the EXACT, byte-identical snapshot string for
    trailing whitespace, unicode, CRLF line endings, and missing trailing
    newlines -- the ledger format is verbatim-lossless, per ADR-0002."""
    entry = _sample_ledger_entry(absorbed_snapshot=snapshot, survivor_before=snapshot)

    metadata: dict[str, object] = {"type": "Concept"}
    metadata[okf.MERGED_FROM_KEY] = okf.encode_merged_from([entry])
    text = okf.dump_frontmatter(metadata, "Survivor body.")

    loaded_metadata, _ = okf.load_frontmatter(text)
    decoded = okf.decode_merged_from(loaded_metadata)

    assert decoded == [entry]
    assert decoded[0].absorbed_snapshot == snapshot
    assert decoded[0].survivor_before == snapshot


def test_decode_merge_ledger_entry_rejects_unsupported_schema_version() -> None:
    """A `schema` value other than `MERGE_LEDGER_SCHEMA_V1` must be rejected
    rather than silently reinterpreted as v1 -- ADR-0002's "migrate rather
    than silently reinterpret" promise."""
    entry = _valid_encoded_entry(schema="openkos.merge_ledger/v2")

    with pytest.raises(ValueError, match="unsupported merged_from schema version"):
        okf.decode_merged_from({"merged_from": [entry]})


def test_decode_merge_ledger_entry_rejects_non_iterable_link_rewrites() -> None:
    """A `link_rewrites` value that is not even iterable (e.g. an int) fails
    closed with the "malformed" message, not a missing-field one."""
    entry = _valid_encoded_entry(link_rewrites=123)

    with pytest.raises(ValueError, match="merged_from entry malformed"):
        okf.decode_merged_from({"merged_from": [entry]})
