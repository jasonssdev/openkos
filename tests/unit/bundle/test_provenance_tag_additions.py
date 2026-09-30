"""Unit tests for `bundle.provenance.resolve_source_tag_additions` (design:
source-tag-sync, "Pure resolver (canonical layer)"; ADR-0033), the pure
per-Source tag-union resolver `sync-tags`'s Phase A calls, mirroring
`resolve_source_raises`'s shape with a different per-member computation.

The write set is `find_provenance_descendants`' subset closure, minus the
root and minus every `type: Source` member (spec: "The Write Set Is The
Source's Provenance Closure, Minus Sources"). Each remaining member is
classified into exactly one of: staged (`TagAddition`), skipped for a
malformed `tags` value, skipped for ranking below the Source's sensitivity,
or silently complete (the union adds nothing, so it is not staged and
carries no skip either -- an already-current member is not a failure).
"""

from openkos.bundle import provenance
from openkos.model import okf


def _doc(metadata: dict[str, object], body: str = "Body.") -> str:
    base: dict[str, object] = {"type": "Concept"}
    base.update(metadata)
    return okf.dump_frontmatter(base, body)


# -- 1.3: single-Source descendant is staged ---------------------------------


def test_single_source_descendant_is_staged() -> None:
    """Requirement: The Write Set Is The Source's Provenance Closure, Minus
    Sources -- a single-Source descendant is a candidate and gains the
    Source's tags."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha"]}),
        "concepts/a.md": _doc({"provenance": ["sources/notes"]}),
    }

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    assert skips == []
    assert len(additions) == 1
    assert additions[0].concept_id == "concepts/a"
    assert additions[0].added == ("alpha",)


# -- 1.4: transitive descendant + multi-Source exclusion ---------------------


def test_transitive_descendant_is_staged() -> None:
    """Requirement: The Write Set Is The Source's Provenance Closure, Minus
    Sources -- a descendant reached through an intermediate concept is a
    candidate (spec scenario "A descendant reached through an intermediate
    concept is a candidate")."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha"]}),
        "concepts/a.md": _doc({"provenance": ["sources/notes"]}),
        "concepts/b.md": _doc({"provenance": ["concepts/a"]}),
    }

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    assert skips == []
    staged_ids = {addition.concept_id for addition in additions}
    assert staged_ids == {"concepts/a", "concepts/b"}


def test_multi_source_concept_is_not_a_candidate() -> None:
    """Requirement: The Write Set Is The Source's Provenance Closure, Minus
    Sources -- a concept citing two Sources is not a candidate of either
    (spec scenario). PRECONDITION: the same concept IS staged when its
    provenance is only `sources/a`, proving the exclusion below is caused by
    the second citation, not by some unrelated defect."""
    files_precondition = {
        "sources/a.md": _doc({"type": "Source", "tags": ["x"]}),
        "concepts/joint.md": _doc({"provenance": ["sources/a"]}),
    }
    precondition_additions, _ = provenance.resolve_source_tag_additions(
        files_precondition,
        source_id="sources/a",
        source_tags=["x"],
        source_level="private",
    )
    assert [a.concept_id for a in precondition_additions] == ["concepts/joint"]

    files = {
        "sources/a.md": _doc({"type": "Source", "tags": ["x"]}),
        "sources/b.md": _doc({"type": "Source", "tags": ["y"]}),
        "concepts/joint.md": _doc({"provenance": ["sources/a", "sources/b"]}),
    }

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/a", source_tags=["x"], source_level="private"
    )

    assert additions == []
    assert skips == []


# -- 1.5: a Source-typed member is never written -----------------------------


def test_source_typed_member_is_never_written() -> None:
    """Requirement: The Write Set Is The Source's Provenance Closure, Minus
    Sources -- a `type: Source` member of the closure is absent from both
    result lists. PRECONDITION: an otherwise identical `type: Concept`
    member IS staged, proving the exclusion is about `type`, not about the
    fixture shape."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha"]}),
        "concepts/twin.md": _doc({"provenance": ["sources/notes"]}),
        "sources/nested.md": _doc({"type": "Source", "provenance": ["sources/notes"]}),
    }

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    staged_ids = {addition.concept_id for addition in additions}
    skipped_ids = {skip.concept_id for skip in skips}
    assert "concepts/twin" in staged_ids
    assert "sources/nested" not in staged_ids
    assert "sources/nested" not in skipped_ids


# -- 1.7-1.10: union rule (Decision 2, ADR-0033) -----------------------------


def test_union_appends_after_existing() -> None:
    """Requirement: Union Only, Never Remove -- the descendant's `tags` is
    exactly the existing tags first, in order, then the missing Source tags
    in Source order (spec scenario "Missing tags are appended after the
    existing ones")."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha", "beta"]}),
        "concepts/a.md": _doc(
            {"provenance": ["sources/notes"], "tags": ["gamma", "alpha"]}
        ),
    }

    additions, skips = provenance.resolve_source_tag_additions(
        files,
        source_id="sources/notes",
        source_tags=["alpha", "beta"],
        source_level="private",
    )

    assert skips == []
    assert len(additions) == 1
    addition = additions[0]
    assert addition.added == ("beta",)
    metadata, _ = okf.load_frontmatter(addition.content)
    assert metadata["tags"] == ["gamma", "alpha", "beta"]


def test_hand_added_tag_survives() -> None:
    """Requirement: Union Only, Never Remove -- a hand-added tag the Source
    does not carry survives the union (spec scenario)."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha"]}),
        "concepts/a.md": _doc({"provenance": ["sources/notes"], "tags": ["reviewed"]}),
    }

    additions, _ = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    metadata, _ = okf.load_frontmatter(additions[0].content)
    assert metadata["tags"] == ["reviewed", "alpha"]


def test_tag_removed_from_source_is_kept() -> None:
    """Requirement: Union Only, Never Remove -- a tag no longer on the
    Source stays on a descendant that already carries it (spec scenario "A
    tag removed from the Source is kept downstream"). The Source's CURRENT
    tags no longer include `alpha`; a descendant carrying `alpha` plus a
    still-missing `beta` gains only `beta`, keeping `alpha`."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["beta"]}),
        "concepts/a.md": _doc({"provenance": ["sources/notes"], "tags": ["alpha"]}),
    }

    additions, _ = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["beta"], source_level="private"
    )

    metadata, _ = okf.load_frontmatter(additions[0].content)
    assert metadata["tags"] == ["alpha", "beta"]


def test_complete_member_is_not_staged() -> None:
    """Requirement: Union Only, Never Remove -- a descendant already
    carrying every Source tag is not staged (spec scenario "A descendant
    already carrying every tag is not staged"). PRECONDITION: the member IS
    a closure member of this Source, via `find_provenance_descendants`
    directly -- so the empty result below is the union rule, not a closure
    miss."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha"]}),
        "concepts/a.md": _doc({"provenance": ["sources/notes"], "tags": ["alpha"]}),
    }
    closure = provenance.find_provenance_descendants(files, root_ids=["sources/notes"])
    assert "concepts/a" in closure

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    assert additions == []
    assert skips == []


def test_only_tags_changes() -> None:
    """Requirement: Union Only, Never Remove -- only `tags` changes; body,
    `sensitivity`, `provenance`, and `title` re-parse equal to the original
    (spec scenario "Only the tags field changes")."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha"]}),
        "concepts/a.md": _doc(
            {
                "provenance": ["sources/notes"],
                "sensitivity": "private",
                "title": "A",
            },
            body="# A\n\nSome body text.\n",
        ),
    }

    additions, _ = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    original_metadata, original_body = okf.load_frontmatter(files["concepts/a.md"])
    new_metadata, new_body = okf.load_frontmatter(additions[0].content)
    assert new_body == original_body
    assert new_metadata["sensitivity"] == original_metadata["sensitivity"]
    assert new_metadata["provenance"] == original_metadata["provenance"]
    assert new_metadata["title"] == original_metadata["title"]
    assert new_metadata["tags"] == ["alpha"]


# -- 1.12-1.13: malformed tags (Decision 3) ----------------------------------


def _malformed_tags_files(raw_tags: object) -> dict[str, str]:
    return {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha"]}),
        "concepts/a.md": _doc({"provenance": ["sources/notes"], "tags": raw_tags}),
    }


def test_malformed_tags_are_skipped_mapping() -> None:
    """Requirement: A Descendant With A Malformed Tags Value Is Never
    Rewritten -- a mapping-valued `tags` is skipped with a reason, never
    rewritten (spec scenario "A mapping-valued tags field is skipped with a
    warning")."""
    files = _malformed_tags_files({"a": 1})

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    assert additions == []
    assert skips == [okf.TagSkip(concept_id="concepts/a", reason="malformed-tags")]


def test_malformed_tags_are_skipped_bare_string() -> None:
    """Same requirement, bare-string shape: the engine always emits a list,
    so a string value is hand-written and must not be silently normalized
    (design Decision 3)."""
    files = _malformed_tags_files("alpha, beta")

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    assert additions == []
    assert skips == [okf.TagSkip(concept_id="concepts/a", reason="malformed-tags")]


def test_malformed_tags_are_skipped_number() -> None:
    files = _malformed_tags_files(3)

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    assert additions == []
    assert skips == [okf.TagSkip(concept_id="concepts/a", reason="malformed-tags")]


def test_malformed_tags_are_skipped_mixed_list() -> None:
    files = _malformed_tags_files(["alpha", 3])

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    assert additions == []
    assert skips == [okf.TagSkip(concept_id="concepts/a", reason="malformed-tags")]


def test_absent_or_null_tags_gain_source_tags() -> None:
    """Requirement: A Descendant With A Malformed Tags Value Is Never
    Rewritten -- an absent `tags` value is treated as no tags and gains the
    Source's tags (spec scenario "An absent tags field gains the Source's
    tags")."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha"]}),
        "concepts/a.md": _doc({"provenance": ["sources/notes"]}),
    }

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    assert skips == []
    assert additions[0].added == ("alpha",)
    metadata, _ = okf.load_frontmatter(additions[0].content)
    assert metadata["tags"] == ["alpha"]


# -- 1.15-1.16: sensitivity floor (Decision 4, ADR-0033) ---------------------


def test_below_source_sensitivity_is_skipped() -> None:
    """Requirement: A Descendant Below The Source's Sensitivity Is Not
    Tagged -- a `private` member is skipped under a `confidential` Source
    (spec scenario "A confidential Source's tags never reach a private
    descendant"). PRECONDITION: the same member at `confidential` IS
    staged, proving the skip is about sensitivity, not the fixture shape."""
    files_precondition = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["diagnosis"]}),
        "concepts/a.md": _doc(
            {"provenance": ["sources/notes"], "sensitivity": "confidential"}
        ),
    }
    precondition_additions, precondition_skips = (
        provenance.resolve_source_tag_additions(
            files_precondition,
            source_id="sources/notes",
            source_tags=["diagnosis"],
            source_level="confidential",
        )
    )
    assert precondition_skips == []
    assert [a.concept_id for a in precondition_additions] == ["concepts/a"]

    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["diagnosis"]}),
        "concepts/a.md": _doc(
            {"provenance": ["sources/notes"], "sensitivity": "private"}
        ),
    }

    additions, skips = provenance.resolve_source_tag_additions(
        files,
        source_id="sources/notes",
        source_tags=["diagnosis"],
        source_level="confidential",
    )

    assert additions == []
    assert skips == [
        okf.TagSkip(concept_id="concepts/a", reason="below-source-sensitivity")
    ]


def test_missing_member_sensitivity_under_confidential_is_skipped() -> None:
    """Requirement: A Descendant Below The Source's Sensitivity Is Not
    Tagged -- a missing member `sensitivity` ranks `private` (fail-closed
    floor) and is therefore below a `confidential` Source."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["diagnosis"]}),
        "concepts/a.md": _doc({"provenance": ["sources/notes"]}),
    }

    additions, skips = provenance.resolve_source_tag_additions(
        files,
        source_id="sources/notes",
        source_tags=["diagnosis"],
        source_level="confidential",
    )

    assert additions == []
    assert skips == [
        okf.TagSkip(concept_id="concepts/a", reason="below-source-sensitivity")
    ]


def test_unrecognized_member_sensitivity_is_not_below() -> None:
    """Requirement: A Descendant Below The Source's Sensitivity Is Not
    Tagged -- a missing or unrecognized `sensitivity` ranks fail-closed at
    `confidential` (`okf._rank`), so it is never *below* even a
    `confidential` Source, and IS staged."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["diagnosis"]}),
        "concepts/a.md": _doc(
            {"provenance": ["sources/notes"], "sensitivity": "not-a-real-level"}
        ),
    }

    additions, skips = provenance.resolve_source_tag_additions(
        files,
        source_id="sources/notes",
        source_tags=["diagnosis"],
        source_level="confidential",
    )

    assert skips == []
    assert [a.concept_id for a in additions] == ["concepts/a"]


def test_a_descendant_at_the_sources_level_is_tagged() -> None:
    """Requirement: A Descendant Below The Source's Sensitivity Is Not
    Tagged -- a descendant at the Source's own level is tagged (spec
    scenario "A descendant at the Source's level is tagged")."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha"]}),
        "concepts/a.md": _doc(
            {"provenance": ["sources/notes"], "sensitivity": "private"}
        ),
    }

    additions, skips = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    assert skips == []
    metadata, _ = okf.load_frontmatter(additions[0].content)
    assert metadata["tags"] == ["alpha"]


# -- ordering ------------------------------------------------------------


def test_results_are_sorted_by_concept_id() -> None:
    """`resolve_source_tag_additions` sorts its staged result by
    `concept_id` (design: "sorted by id"), mirroring
    `resolve_source_raises`'s determinism guarantee."""
    files = {
        "sources/notes.md": _doc({"type": "Source", "tags": ["alpha"]}),
        "concepts/zeta.md": _doc({"provenance": ["sources/notes"]}),
        "concepts/alpha.md": _doc({"provenance": ["sources/notes"]}),
    }

    additions, _ = provenance.resolve_source_tag_additions(
        files, source_id="sources/notes", source_tags=["alpha"], source_level="private"
    )

    assert [addition.concept_id for addition in additions] == [
        "concepts/alpha",
        "concepts/zeta",
    ]
