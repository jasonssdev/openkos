"""Unit tests for `bundle/source_titles.py`'s pure core (design D1/D4).
`titleize`, `retitle_document`, `scan_source_titles`, and
`resolve_source_title_backfill`.
"""

import pytest

from openkos import source_title
from openkos.bundle import source_titles
from openkos.model import okf


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("01-Introduction", "01 Introduction"),
        ("__weird--stem_name__", "weird stem name"),
        ("notes", "notes"),
    ],
)
def test_titleize_matches_pre_promotion_behavior(stem: str, expected: str) -> None:
    """`titleize` matches pre-promotion `_titleize`: `-`/`_` -> spaces."""
    assert source_titles.titleize(stem) == expected


def _source_doc(*, title: str, first_line: str, description: str = "Desc.") -> str:
    metadata: dict[str, object] = {
        "type": "Source",
        "title": title,
        "description": description,
        "resource": "raw/notes.md",
        "tags": [],
        "timestamp": "2026-07-31T00:00:00+00:00",
        "status": "active",
        "version": 1,
        "freshness": "snapshot",
        "sensitivity": "public",
        "provenance": ["sources/notes"],
    }
    body = f"{first_line}\n\n{description}\n\n## Source content\n\nBody text.\n\n# Citations\n"
    return okf.dump_frontmatter(metadata, body)


_NON_CANONICAL_DOC = """---
type: Source
title: Reading notes — Enchiridion, 2026-07-05
description: First pass through Epictetus's Enchiridion and its introduction.
resource: raw/notes-on-the-enchiridion-2026-07-05.txt
tags: [reading-notes, philosophy]
timestamp: 2026-07-05T20:00:00Z
status: active
version: 1
freshness: snapshot
sensitivity: private
---

# Reading notes — Enchiridion, 2026-07-05

Notes body text here.
"""


def test_retitle_document_patches_only_two_lines_in_a_non_canonical_document() -> None:
    """Exactly Two Byte-Level Edits: pinned on the full output STRING over a
    NON-CANONICAL fixture (unsorted keys, inline `tags`, unquoted `Z`
    timestamp) -- a `dump_frontmatter`-built fixture is already canonical
    and cannot observe a re-dump regression."""
    current_title = "Reading notes — Enchiridion, 2026-07-05"
    new_title = "Enchiridion, first pass"

    result = source_titles.retitle_document(
        _NON_CANONICAL_DOC, current_title=current_title, new_title=new_title
    )

    expected = _NON_CANONICAL_DOC.replace(
        f"title: {current_title}\n", f"title: {new_title}\n", 1
    ).replace(f"# {current_title}\n", f"# {new_title}\n", 1)
    assert result == expected  # proves order, `tags`, and `timestamp` untouched
    assert "tags: [reading-notes, philosophy]\n" in result
    assert "timestamp: 2026-07-05T20:00:00Z\n" in result


def test_retitle_document_preserves_trailing_body_whitespace() -> None:
    """A full re-dump strips trailing body whitespace on parse; the
    surgical patch never reparses the body, so it survives (point 7)."""
    text = "---\ntitle: Notes\n---\n\n# Notes\n\nBody text.\n\n\n"

    result = source_titles.retitle_document(
        text, current_title="Notes", new_title="New Notes"
    )

    expected = text.replace("title: Notes\n", "title: New Notes\n", 1).replace(
        "# Notes\n", "# New Notes\n", 1
    )
    assert result == expected
    assert result.endswith("\n\n\n")


@pytest.mark.parametrize(
    ("block", "current_title", "expected"),
    [
        # ANCHOR is the load-bearing row. `yaml.safe_load("title: &a Notes")`
        # yields "Notes", which EQUALS `current_title`, so the round-trip check
        # passes and ONLY the scalar-shape guard can refuse. Without the guard
        # the anchor definition is deleted while a later `*a` alias survives,
        # and the emitted document no longer loads at all.
        ("title: &a Notes\nauthor: *a\n", "Notes", "not a rewritable scalar"),
        ("title: >-\n  Notes\n", "Notes", "not a rewritable scalar"),
        ("title: Notes\ntitle: Notes\n", "Notes", "line count is 2"),
        ("author: x\n", "Notes", "line count is 0"),
        # Nested and embedded `title:` keys are invisible to the top-level
        # pattern, so the count is 0 rather than a wrong-key rewrite.
        ("meta:\n  title: Inner\n", "Inner", "line count is 0"),
        ("note: |\n  title: fake\n", "Notes", "line count is 0"),
        ("title: Notes  # keep me\n", "Notes", "trailing comment"),
        # #310: a comment glued to a quoted scalar with NO preceding
        # whitespace still parses to just "Notes", so the old ` #` substring
        # guard never fired and the comment was silently re-emitted away --
        # the exact third edit the guard exists to refuse.
        ('title: "Notes"#comment\n', "Notes", "trailing comment"),
        # #310: a TAB before `#` kills the YAML scanner outright ("found
        # character '\\t' that cannot start any token"), so this shape is
        # refused at the parse guard, not the comment guard -- pinned here
        # so a future laxer parser cannot turn it into silent deletion.
        ("title: Notes\t# comment\n", "Notes", "did not parse"),
    ],
)
def test_retitle_document_fails_closed_on_unrewritable_title(
    block: str, current_title: str, expected: str
) -> None:
    """Fail-closed (point 4). Each row refuses for a DISTINCT reason and
    asserts that reason's own message, so no row can pass because a
    different guard fired first -- the defect the review caught in the
    single block-scalar test this replaces."""
    text = f"---\n{block}---\n\n# {current_title}\n"

    with pytest.raises(ValueError, match=expected):
        source_titles._patch_title_line(
            f"---\n{block}---\n", current_title=current_title, new_title="New Title"
        )
    assert text  # the document is never rewritten; nothing is returned


def test_retitle_document_refuses_an_anchored_title_through_the_public_api() -> None:
    """#311 gap 1: every other fail-closed row drives `_patch_title_line`
    directly, so a mutant that swallowed the helper's `ValueError` INSIDE
    `retitle_document` (e.g. a broad `except` falling back to the original
    block) would keep the whole parametrized table green. One row through
    the PUBLIC entry point makes that mutant die. The anchored title is the
    load-bearing shape: it parses to `current_title`, so only the
    scalar-shape guard can refuse it."""
    text = "---\ntitle: &a Notes\nauthor: *a\n---\n\n# Notes\n\nBody.\n"

    with pytest.raises(ValueError, match="not a rewritable scalar"):
        source_titles.retitle_document(
            text, current_title="Notes", new_title="New Title"
        )


def test_retitle_document_patches_exactly_two_lines_for_a_long_title() -> None:
    """#310: the dumper's default width (~80 columns) folds a long title
    onto a continuation line, turning the frontmatter patch into TWO changed
    lines and the whole edit into three -- against "Exactly Two Byte-Level
    Edits Per Staged Source". `_TITLE_MAX_CHARS` is 120, so a >100-char
    title is squarely in the stageable range, not a hypothetical."""
    new_title = (
        "A Deliberately Overlong Title That Comfortably Exceeds One Hundred "
        "Characters To Force The Dumper Past Its Default Wrap Width"
    )
    assert len(new_title) > 100
    text = "---\ntitle: Notes\n---\n\n# Notes\n\nBody.\n"

    result = source_titles.retitle_document(
        text, current_title="Notes", new_title=new_title
    )

    before_lines = text.split("\n")
    after_lines = result.split("\n")
    assert len(after_lines) == len(before_lines)
    changed = [(a, b) for a, b in zip(before_lines, after_lines, strict=True) if a != b]
    assert len(changed) == 2  # the `title:` line and the first body line, only
    metadata, body = okf.load_frontmatter(result)
    assert metadata["title"] == new_title
    assert body.split("\n")[0] == f"# {new_title}"


def test_retitle_document_refuses_a_crlf_framed_document() -> None:
    """A CRLF-framed document is REFUSED, not silently normalised. The
    pre-surgery implementation rewrote it by round-tripping every line
    ending to LF; that was a whole-file mutation, so refusing is the
    fail-closed reading of "exactly two byte-level edits"."""
    text = "---\r\ntitle: Notes\r\n---\r\n\r\n# Notes\r\n\r\nBody.\r\n"

    with pytest.raises(ValueError, match="missing or malformed frontmatter"):
        source_titles.retitle_document(
            text, current_title="Notes", new_title="New Title"
        )


@pytest.mark.parametrize(
    "new_title",
    [
        "Title: with colon",
        '"quoted" title',
        "# hashy title",
        "123456",
        "Café résumé",
    ],
)
def test_retitle_document_round_trips_awkward_new_titles(new_title: str) -> None:
    """Point 5: reparsing the patched document yields exactly `new_title`,
    whatever quoting `okf.dump_frontmatter` emits."""
    text = _source_doc(title="Notes", first_line="# Notes")

    result = source_titles.retitle_document(
        text, current_title="Notes", new_title=new_title
    )

    metadata, _ = okf.load_frontmatter(result)
    assert metadata["title"] == new_title


@pytest.mark.parametrize(
    ("doc_title", "first_line", "call_current_title", "match"),
    [
        ("01 Introduction", "# Something Else", "01 Introduction", "Something Else"),
        ("01 Introduction", "", "01 Introduction", "01 Introduction"),
        ("01 Introduction", "# 01 Introduction", "Wrong Title", "Wrong Title"),
    ],
)
def test_retitle_document_refuses_a_mismatch(
    doc_title: str, first_line: str, call_current_title: str, match: str
) -> None:
    """Requirement: Body First-Line Safety Property -- a hand-edited, blank,
    or absent first line, or a `current_title` not matching the on-disk
    `title`, raises `ValueError` naming what was found; nothing is written."""
    text = _source_doc(title=doc_title, first_line=first_line)

    with pytest.raises(ValueError, match=match):
        source_titles.retitle_document(
            text, current_title=call_current_title, new_title="Chapter One"
        )


def test_retitle_document_normalizes_a_crlf_first_line() -> None:
    """`load_frontmatter` already normalizes `\\r\\n` to `\\n` (design D4)."""
    text = _source_doc(title="01 Introduction", first_line="# 01 Introduction\r")

    result = source_titles.retitle_document(
        text, current_title="01 Introduction", new_title="Chapter One"
    )

    _, body = okf.load_frontmatter(result)
    assert body.split("\n")[0] == "# Chapter One"


_NO_RESOURCE = object()


def _concept_doc(
    *, type_: str = "Source", title: object = "Notes", resource: object = "raw/notes.md"
) -> str:
    metadata: dict[str, object] = {"type": type_, "title": title}
    if resource is not _NO_RESOURCE:
        metadata["resource"] = resource
    return okf.dump_frontmatter(metadata, "# Notes\n")


@pytest.mark.parametrize(
    ("resource", "expected_reason"),
    [
        (_NO_RESOURCE, "resource-missing"),
        (42, "resource-missing"),
        ("notes.md", "resource-malformed"),
        ("raw/../notes.md", "resource-malformed"),
        ("raw/sub/notes.md", "resource-malformed"),
        ("/raw/notes.md", "resource-malformed"),
        ("raw\\notes.md", "resource-malformed"),
    ],
)
def test_scan_source_titles_warns_on_malformed_resource(
    resource: object, expected_reason: str
) -> None:
    """Bucket 1, evaluated before curated/mechanical."""
    files = {"sources/notes.md": _concept_doc(resource=resource)}
    result = source_titles.scan_source_titles(files)

    assert (result.candidates, result.skipped) == ((), ())
    assert result.warned == (
        source_titles.WarnedSource(concept_id="sources/notes", reason=expected_reason),
    )


@pytest.mark.parametrize(
    ("concept_id", "title", "resource", "curated"),
    [
        ("sources/notes", "My Curated Title", "raw/notes.md", True),
        ("sources/01-introduction", "01 Introduction", "raw/01-Introduction.md", False),
    ],
)
def test_scan_source_titles_curated_vs_candidate(
    concept_id: str, title: str, resource: str, curated: bool
) -> None:
    """Bucket 2 (curated) vs bucket 3 (candidate): decided by
    `title == titleize(Path(resource).stem)`. Row 2 is the
    `01-Introduction.md` counterexample -- `titleize(slug)` must NOT
    decide; it classifies as a candidate, never curated."""
    files = {f"{concept_id}.md": _concept_doc(title=title, resource=resource)}
    result = source_titles.scan_source_titles(files)

    assert result.warned == ()
    if curated:
        expected_skip = source_titles.SkippedSource(
            concept_id=concept_id, current_title=title, reason="curated"
        )
        assert (result.candidates, result.skipped) == ((), (expected_skip,))
    else:
        expected_hit = source_titles.SourceCandidate(
            concept_id=concept_id, current_title=title, resource=resource
        )
        assert (result.candidates, result.skipped) == ((expected_hit,), ())
        # `document_text` is `compare=False`, so the equality above is blind to
        # it -- and it is the one field carrying the exact bytes
        # `retitle_document` rewrites. Pin it directly: an empty or wrong-keyed
        # value would file every Source under `heading-mismatch` and rewrite
        # nothing, with a fully green suite.
        assert result.candidates[0].document_text == files[f"{concept_id}.md"]


def test_scan_source_titles_only_considers_type_source_concepts() -> None:
    """Only `type: Source` is evaluated; a non-Source is invisible to every
    bucket, not just excluded from candidates."""
    files = {
        "concepts/entity.md": _concept_doc(type_="Entity", title="Entity Notes"),
        "sources/notes.md": _concept_doc(title="Notes"),
    }
    result = source_titles.scan_source_titles(files)

    buckets = (result.candidates, result.skipped, result.warned)
    assert [c.concept_id for bucket in buckets for c in bucket] == ["sources/notes"]


def test_scan_source_titles_orders_every_bucket_by_concept_id() -> None:
    """Every bucket is deterministically sorted by `concept_id`, regardless
    of input iteration order."""
    files = {
        "sources/z-warned.md": _concept_doc(resource=_NO_RESOURCE),
        "sources/a-warned.md": _concept_doc(resource=_NO_RESOURCE),
        "sources/z-skipped.md": _concept_doc(title="Curated", resource="raw/z.md"),
        "sources/a-skipped.md": _concept_doc(title="Curated", resource="raw/a.md"),
        "sources/z-candidate.md": _concept_doc(title="z", resource="raw/z.txt"),
        "sources/a-candidate.md": _concept_doc(title="a", resource="raw/a.txt"),
    }
    result = source_titles.scan_source_titles(files)

    got = (
        tuple(c.concept_id for c in result.candidates),
        tuple(s.concept_id for s in result.skipped),
        tuple(w.concept_id for w in result.warned),
    )
    assert got == (
        ("sources/a-candidate", "sources/z-candidate"),
        ("sources/a-skipped", "sources/z-skipped"),
        ("sources/a-warned", "sources/z-warned"),
    )
    # Each candidate must carry ITS OWN document bytes. This is the only
    # fixture here holding more than one candidate, so it is the only place a
    # cross-document mixup is expressible at all: a single-document fixture has
    # no second document whose bytes a wrong key could pick up. `document_text`
    # is `compare=False`, so no equality assertion elsewhere can see the swap --
    # and a candidate holding another concept's bytes is exactly what
    # `retitle_document` would go on to rewrite.
    assert tuple(c.document_text for c in result.candidates) == (
        files["sources/a-candidate.md"],
        files["sources/z-candidate.md"],
    )


def test_scan_output_drives_resolve_end_to_end() -> None:
    """The only test that crosses the scan/resolve seam with REAL scan output.

    Every other resolve test builds its candidates through `_candidate`, which
    fills `document_text` itself -- so without this test nothing proves
    `scan_source_titles` hands the resolver the bytes of the right document.
    """
    path = "sources/notes.md"
    files = {path: _source_doc(title="notes", first_line="# notes")}

    scan = source_titles.scan_source_titles(files)
    result = source_titles.resolve_source_title_backfill(
        scan, {"raw/notes.md": "# Derived Title\n\nBody.\n"}
    )

    assert (result.skipped, result.warned) == ((), ())
    assert [(s.concept_id, s.new_title) for s in result.staged] == [
        ("sources/notes", "Derived Title")
    ]
    assert okf.load_frontmatter(result.staged[0].content)[0]["title"] == "Derived Title"


def _candidate(
    *,
    concept_id: str = "sources/notes",
    current_title: str = "Notes",
    resource: str = "raw/notes.md",
    first_line: str | None = None,
) -> source_titles.SourceCandidate:
    line = first_line if first_line is not None else f"# {current_title}"
    return source_titles.SourceCandidate(
        concept_id=concept_id,
        current_title=current_title,
        resource=resource,
        document_text=_source_doc(title=current_title, first_line=line),
    )


def test_resolve_source_title_backfill_stages_a_differing_derivation() -> None:
    """A mechanical title with a differing derivation is staged, carrying
    the new title and the `retitle_document`-rewritten document."""
    scan = source_titles.ScanResult(candidates=(_candidate(),), skipped=(), warned=())

    result = source_titles.resolve_source_title_backfill(
        scan, {"raw/notes.md": "# New Title\n\nBody.\n"}
    )

    assert result.skipped == ()
    assert result.warned == ()
    assert len(result.staged) == 1
    staged = result.staged[0]
    assert (staged.concept_id, staged.new_title) == ("sources/notes", "New Title")
    _, body = okf.load_frontmatter(staged.content)
    assert body.split("\n")[0] == "# New Title"


@pytest.mark.parametrize(
    ("raw_texts", "first_line", "bucket", "reason"),
    [
        ({}, None, "warned", "raw-unreadable"),
        ({"raw/notes.md": None}, None, "warned", "raw-undecodable"),
        ({"raw/notes.md": "Plain text.\n"}, None, "skipped", "no-derivable-title"),
        ({"raw/notes.md": "# Notes\n\nBody.\n"}, None, "skipped", "already-current"),
        (
            {"raw/notes.md": "# New Title\n\nBody.\n"},
            "# Hand Edited",
            "warned",
            "heading-mismatch",
        ),
    ],
)
def test_resolve_source_title_backfill_files_every_non_staging_reason(
    raw_texts: dict[str, str | None], first_line: str | None, bucket: str, reason: str
) -> None:
    """Closed reason vocabulary (design D3): a missing `raw_texts` key vs.
    an explicit `None` value are distinct; a `None`/identical re-derivation
    stages nothing; a hand-edited first line is `heading-mismatch`, never
    a traceback."""
    scan = source_titles.ScanResult(
        candidates=(_candidate(first_line=first_line),), skipped=(), warned=()
    )

    result = source_titles.resolve_source_title_backfill(scan, raw_texts)

    assert result.staged == ()
    got = result.warned if bucket == "warned" else result.skipped
    assert len(got) == 1
    assert got[0].concept_id == "sources/notes"
    assert got[0].reason == reason


def test_resolve_files_an_unrewritable_scalar_as_unpatchable_title() -> None:
    """#309: `retitle_document` raises `ValueError` for ~six distinct
    frontmatter-shape causes, and the resolver used to file them ALL under
    `heading-mismatch` -- whose documented meaning is a hand-edited BODY
    heading, pointing operators at a line that is perfectly fine. An
    anchored `title:` is the sharpest such shape (it parses to the current
    title, so only the scalar-shape guard refuses): it must land in a
    distinct `unpatchable-title` bucket, leaving `heading-mismatch` to the
    body-heading case it names."""
    doc = (
        "---\ntype: Source\ntitle: &t Notes\nresource: raw/notes.md\n---\n"
        "\n# Notes\n\nBody.\n"
    )
    candidate = source_titles.SourceCandidate(
        concept_id="sources/notes",
        current_title="Notes",
        resource="raw/notes.md",
        document_text=doc,
    )
    scan = source_titles.ScanResult(candidates=(candidate,), skipped=(), warned=())

    result = source_titles.resolve_source_title_backfill(
        scan, {"raw/notes.md": "# New Title\n\nBody.\n"}
    )

    assert (result.staged, result.skipped) == ((), ())
    assert result.warned == (
        source_titles.WarnedSource(
            concept_id="sources/notes", reason="unpatchable-title"
        ),
    )


def test_resolve_source_title_backfill_skips_empty_raw_without_calling_derive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`derive_source_title` MUST NOT be called for blank raw text (D2)."""
    called = False

    def _spy(raw_content: str) -> str | None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(source_title, "derive_source_title", _spy)
    scan = source_titles.ScanResult(candidates=(_candidate(),), skipped=(), warned=())

    result = source_titles.resolve_source_title_backfill(scan, {"raw/notes.md": "  \n"})

    assert not called
    assert result.skipped == (
        source_titles.SkippedSource("sources/notes", "Notes", "empty-raw-source"),
    )


def test_resolve_source_title_backfill_orders_and_carries_scan_entries() -> None:
    """Scan's own `skipped`/`warned` entries pass through unchanged; every
    bucket stays sorted by `concept_id`."""
    scan = source_titles.ScanResult(
        candidates=(
            _candidate(concept_id="sources/z"),
            _candidate(concept_id="sources/a"),
        ),
        skipped=(source_titles.SkippedSource("sources/c", "X", "curated"),),
        warned=(source_titles.WarnedSource("sources/b", "resource-missing"),),
    )

    result = source_titles.resolve_source_title_backfill(
        scan, {"raw/notes.md": "# New Title\n\nBody.\n"}
    )

    assert [s.concept_id for s in result.staged] == ["sources/a", "sources/z"]
    assert result.skipped == scan.skipped
    assert result.warned == scan.warned
