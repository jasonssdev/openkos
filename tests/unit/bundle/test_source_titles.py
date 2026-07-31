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


def test_retitle_document_changes_only_title_and_first_line() -> None:
    """Requirement: Exactly Two Byte-Level Edits Per Staged Source."""
    text = _source_doc(title="01 Introduction", first_line="# 01 Introduction")

    result = source_titles.retitle_document(
        text, current_title="01 Introduction", new_title="Chapter One"
    )

    metadata, body = okf.load_frontmatter(result)
    old_metadata, old_body = okf.load_frontmatter(text)
    old_metadata["title"] = "Chapter One"
    assert metadata == old_metadata
    assert body.split("\n")[0] == "# Chapter One"
    assert body.split("\n")[1:] == old_body.split("\n")[1:]


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
