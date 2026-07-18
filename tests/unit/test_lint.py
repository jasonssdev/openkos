"""Unit tests for `lint.py`: the read-only freshness + orphan health check.

`lint` is the second read-only bundle-reader command (after `status`). It
adds its OWN vocabulary (`LintDoc`/`LintFinding`/`LintReport`), fully
separate from `okf.BundleSurvey`/`check_conformance` -- lint is not a
conformance checker. `collect_docs` wraps `okf._iter_docs` for the single
walk; `parse_window`/`resolve_window` parse the `freshness_window` duration
grammar; `check_stale_stamps` flags aging inline `(as of YYYY-MM-DD)`
stamps; `check_orphans` flags concept files unreferenced by any markdown
link. The clock and window are always injected -- `lint.py` never calls
`datetime.now()`.
"""

from datetime import date, timedelta
from pathlib import Path, PurePosixPath

import pytest

from openkos import lint
from openkos.model import okf


def _write_doc(path: Path, *, doc_type: str = "Concept", body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: Stub\n---\n{body}",
        encoding="utf-8",
    )


def test_collect_docs_computes_identity_rel_dir_and_body(tmp_path: Path) -> None:
    """`collect_docs` computes `identity` (bundle-relative path minus
    `.md`), `rel_dir` (its parent directory), and `body` (the text after
    frontmatter) for each doc."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md", body="Some body text.\n")

    docs, skipped = lint.collect_docs(bundle_dir)

    assert len(docs) == 1
    doc = docs[0]
    assert doc.identity == "concepts/stoicism"
    assert doc.rel_dir == "concepts"
    assert doc.body == "Some body text."
    assert doc.path == bundle_dir / "concepts" / "stoicism.md"
    assert skipped == []


def test_collect_docs_skips_reserved_filenames(tmp_path: Path) -> None:
    """`index.md` and `log.md` are reserved (§6/§7) and never returned as docs."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "index.md").write_text(
        '---\nokf_version: "0.1"\n---\n\n# Concepts\n', encoding="utf-8"
    )
    (bundle_dir / "log.md").write_text("# Directory Update Log\n", encoding="utf-8")
    _write_doc(bundle_dir / "concepts" / "stoicism.md")

    docs, skipped = lint.collect_docs(bundle_dir)

    assert [doc.identity for doc in docs] == ["concepts/stoicism"]
    assert skipped == []


def test_collect_docs_skips_unreadable_files(tmp_path: Path) -> None:
    """A `read_error` file is skipped, not raised, and gets a skip notice."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "readable.md")
    unreadable = bundle_dir / "concepts" / "unreadable.md"
    unreadable.write_bytes(b"\xff\xfe\x00\x01not-utf8")

    docs, skipped = lint.collect_docs(bundle_dir)

    assert [doc.identity for doc in docs] == ["concepts/readable"]
    assert skipped == ["concepts/unreadable.md: skipped (unreadable)"]


def test_collect_docs_skips_files_with_no_parseable_frontmatter(
    tmp_path: Path,
) -> None:
    """A `parse_error` file is skipped, not raised, and gets a skip notice."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "readable.md")
    (bundle_dir / "concepts" / "no-frontmatter.md").write_text(
        "Just plain text, no frontmatter block.\n", encoding="utf-8"
    )

    docs, skipped = lint.collect_docs(bundle_dir)

    assert [doc.identity for doc in docs] == ["concepts/readable"]
    assert skipped == ["concepts/no-frontmatter.md: skipped (unparseable frontmatter)"]


def test_collect_docs_skips_files_that_fail_the_body_reread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TOCTOU body re-read failure is skipped with a notice, not raised."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "flaky.md")
    target = bundle_dir / "concepts" / "flaky.md"
    original_read_text = Path.read_text
    call_count = {"n": 0}

    def flaky_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == target:
            call_count["n"] += 1
            if call_count["n"] > 1:
                raise OSError("simulated re-read failure")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    docs, skipped = lint.collect_docs(bundle_dir)

    assert docs == []
    assert skipped == ["concepts/flaky.md: skipped (unreadable)"]


def test_collect_docs_skips_body_reread_parse_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TOCTOU re-read whose frontmatter no longer parses is skipped with a
    notice, not raised. `okf.load_frontmatter` can raise an exception that is
    neither `OSError` nor `UnicodeDecodeError` if a concurrent edit corrupts
    the file between `_iter_docs`'s parse and this re-read, so the guard must
    be broad."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "racy.md")

    def raise_parse_error(text: str) -> tuple[dict[str, object], str]:
        raise RuntimeError("simulated frontmatter parse failure")

    monkeypatch.setattr(okf, "load_frontmatter", raise_parse_error)

    docs, skipped = lint.collect_docs(bundle_dir)

    assert docs == []
    assert skipped == ["concepts/racy.md: skipped (unparseable frontmatter)"]


def test_collect_docs_top_level_doc_has_empty_rel_dir(tmp_path: Path) -> None:
    """A non-reserved `.md` file directly under `bundle_dir` has `rel_dir == ""`."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "readme.md")

    docs, _skipped = lint.collect_docs(bundle_dir)

    assert docs[0].identity == "readme"
    assert docs[0].rel_dir == ""


@pytest.mark.parametrize(
    ("raw", "expected_days"),
    [
        ("7d", 7),
        ("2w", 14),
        ("1d", 1),
        ("  7d  ", 7),
        ("\t14d\n", 14),
    ],
)
def test_parse_window_parses_days_and_weeks(raw: str, expected_days: int) -> None:
    """`parse_window` parses `<N>d`/`<N>w` (N a positive int, `w` = x7d),
    tolerating surrounding whitespace."""
    assert lint.parse_window(raw) == timedelta(days=expected_days)


@pytest.mark.parametrize(
    "raw",
    ["0d", "0w", "-1d", "-7d", "garbage", "", "7", "d", "7x", "7.5d"],
)
def test_parse_window_rejects_zero_negative_and_garbage(raw: str) -> None:
    """`parse_window` raises `ValueError` on a zero, negative, or unparseable value."""
    with pytest.raises(ValueError, match="freshness_window"):
        lint.parse_window(raw)


def test_resolve_window_passes_through_valid_raw(tmp_path: Path) -> None:
    """A valid raw duration resolves to `(window, None)` -- no fallback notice."""
    window, notice = lint.resolve_window("14d")

    assert window == timedelta(days=14)
    assert notice is None


@pytest.mark.parametrize("raw", ["0d", "-1d", "garbage", ""])
def test_resolve_window_falls_back_on_invalid_raw(raw: str) -> None:
    """An unparseable, zero, or negative raw duration falls back to
    `DEFAULT_FRESHNESS_WINDOW` and returns a non-`None` notice (Q4)."""
    window, notice = lint.resolve_window(raw)

    assert window == timedelta(days=7)
    assert notice is not None
    assert raw in notice or repr(raw) in notice or "not a valid duration" in notice


def test_resolve_window_never_raises() -> None:
    """`resolve_window` never raises on bad config -- lint is read-only-never-fail (Q4)."""
    lint.resolve_window("nonsense")


@pytest.mark.parametrize("raw", [7, True, None, 3.5])
def test_parse_window_rejects_non_string_input(raw: object) -> None:
    """A non-`str` `freshness_window` raises `ValueError`, never `AttributeError`."""
    with pytest.raises(ValueError, match="freshness_window"):
        lint.parse_window(raw)


@pytest.mark.parametrize("raw", [7, True, None])
def test_resolve_window_falls_back_on_non_string_input(raw: object) -> None:
    """A non-`str` `freshness_window` falls back to the default, never crashes."""
    window, notice = lint.resolve_window(raw)

    assert window == timedelta(days=7)
    assert notice is not None
    assert "not a valid duration" in notice


def _doc(identity: str, body: str) -> lint.LintDoc:
    rel_dir = str(PurePosixPath(identity).parent)
    if rel_dir == ".":
        rel_dir = ""
    return lint.LintDoc(
        path=Path(f"/bundle/{identity}.md"),
        identity=identity,
        rel_dir=rel_dir,
        body=body,
    )


def test_check_stale_stamps_flags_a_stamp_beyond_the_window() -> None:
    """A stamp older than `window` is flagged as a stale-stamp finding."""
    docs = [_doc("concepts/stoicism", "Body text (as of 2026-07-01).")]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), window=timedelta(days=7)
    )

    assert len(findings) == 1
    assert findings[0].kind == "stale"
    assert findings[0].path == "concepts/stoicism.md"
    assert "2026-07-01" in findings[0].detail
    assert "19" in findings[0].detail


def test_check_stale_stamps_does_not_flag_a_stamp_within_the_window() -> None:
    """A stamp within `window` is NOT flagged."""
    docs = [_doc("concepts/stoicism", "Body text (as of 2026-07-18).")]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), window=timedelta(days=7)
    )

    assert findings == []


def test_check_stale_stamps_exact_boundary_is_not_stale() -> None:
    """A stamp exactly `window` old is NOT stale -- strictly greater flags."""
    docs = [_doc("concepts/stoicism", "Body text (as of 2026-07-13).")]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), window=timedelta(days=7)
    )

    assert findings == []


def test_check_stale_stamps_skips_malformed_calendar_dates() -> None:
    """A shape-matching but invalid calendar date (`2026-13-45`) is silently
    skipped -- never flagged, never crashes (Q5, MVP-1 lenient)."""
    docs = [_doc("concepts/stoicism", "Body text (as of 2026-13-45).")]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), window=timedelta(days=7)
    )

    assert findings == []


def test_check_stale_stamps_dedupes_duplicate_stamps() -> None:
    """The same `(path, stamp-date)` appearing twice in a body yields ONE finding."""
    docs = [
        _doc(
            "concepts/stoicism",
            "First mention (as of 2026-07-01). Second mention (as of 2026-07-01).",
        )
    ]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), window=timedelta(days=7)
    )

    assert len(findings) == 1


def test_check_stale_stamps_pure_ingest_bundle_has_zero_findings() -> None:
    """A `freshness: snapshot` Source concept with no `(as of ...)` stamp
    produces zero stale-stamp findings."""
    docs = [
        _doc(
            "sources/notes",
            "Raw source imported from 'notes.txt' as raw/notes.txt; "
            "not yet compiled or extracted.",
        )
    ]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), window=timedelta(days=7)
    )

    assert findings == []


def test_normalize_link_rooted_slash() -> None:
    """A `/`-rooted link resolves via `lstrip('/')` (Q1)."""
    assert lint.normalize_link("/concepts/stoicism.md", "people") == "concepts/stoicism"


def test_normalize_link_plain_relative() -> None:
    """A plain-relative link resolves against `source_rel_dir` (Q1)."""
    assert lint.normalize_link("stoicism.md", "concepts") == "concepts/stoicism"


def test_normalize_link_dot_slash_relative() -> None:
    """A `./`-relative link resolves against `source_rel_dir`."""
    assert lint.normalize_link("./stoicism.md", "concepts") == "concepts/stoicism"


def test_normalize_link_dot_dot_relative() -> None:
    """A `../`-relative link resolves against `source_rel_dir`, normalizing `..`."""
    assert (
        lint.normalize_link("../concepts/stoicism.md", "people") == "concepts/stoicism"
    )


def test_normalize_link_extension_less_matches_md_counterpart() -> None:
    """An extension-less link resolves to the SAME identity as its `.md`
    counterpart -- no false orphans on link-form drift."""
    rooted = lint.normalize_link("/people/maria-salazar", "concepts")
    with_ext = lint.normalize_link("/people/maria-salazar.md", "concepts")

    assert rooted == with_ext == "people/maria-salazar"


def test_normalize_link_strips_fragment() -> None:
    """A `#fragment` suffix is stripped before normalization."""
    assert (
        lint.normalize_link("/concepts/stoicism.md#related", "people")
        == "concepts/stoicism"
    )


def test_normalize_link_strips_title() -> None:
    """A ` "title"` suffix (markdown link title syntax) is stripped."""
    assert (
        lint.normalize_link('/concepts/stoicism.md "Stoicism"', "people")
        == "concepts/stoicism"
    )


@pytest.mark.parametrize(
    "target",
    [
        "https://plato.stanford.edu/entries/stoicism/",
        "http://example.com/x.md",
        "mailto:someone@example.com",
    ],
)
def test_normalize_link_external_scheme_returns_none(target: str) -> None:
    """An external `scheme:` URL (http/https/mailto) normalizes to `None`."""
    assert lint.normalize_link(target, "concepts") is None


def test_normalize_link_escaping_bundle_root_returns_none() -> None:
    """A relative link that escapes the bundle root normalizes to `None`."""
    assert lint.normalize_link("../../evil.md", "concepts") is None


def test_normalize_link_empty_after_stripping_fragment_returns_none() -> None:
    """A pure in-page anchor (`#heading`, no path) normalizes to `None`."""
    assert lint.normalize_link("#heading", "concepts") is None


_INDEX_TEXT = (
    '---\nokf_version: "0.1"\n---\n\n'
    "# Concepts\n\n"
    "* [Stoicism](/concepts/stoicism.md) - Hellenistic school.\n\n"
    "# Sources\n\n"
    "* [Notes](/sources/notes.md) - Raw notes.\n"
)


def test_check_orphans_cataloged_concept_is_not_orphan() -> None:
    """A concept cataloged in `index.md` is NOT an orphan."""
    docs = [_doc("concepts/stoicism", "No inbound links needed, cataloged.")]

    findings = lint.check_orphans(docs, index_text=_INDEX_TEXT)

    assert findings == []


def test_check_orphans_referenced_only_from_another_concepts_body_is_not_orphan() -> (
    None
):
    """A concept referenced only from another concept's body is NOT an orphan."""
    docs = [
        _doc("concepts/stoicism", "See [Epicureanism](/concepts/epicureanism.md)."),
        _doc("concepts/epicureanism", "No inbound catalog entry."),
    ]

    findings = lint.check_orphans(docs, index_text='---\nokf_version: "0.1"\n---\n')

    assert [f.path for f in findings] == ["concepts/stoicism.md"]


def test_check_orphans_log_md_link_does_not_count_as_a_reference() -> None:
    """A concept referenced ONLY via a `log.md`-shaped link is still flagged
    orphan -- `log.md` is structurally excluded from the referenced-set
    (there is no `log_text` parameter; only `index_text` and doc bodies
    feed the referenced-set), which is the invariant this locks in."""
    docs = [_doc("concepts/stoicism", "Not linked from any concept body.")]
    index_text = '---\nokf_version: "0.1"\n---\n'

    findings = lint.check_orphans(docs, index_text=index_text)

    assert [f.path for f in findings] == ["concepts/stoicism.md"]


def test_check_orphans_wholly_unreferenced_concept_is_orphan() -> None:
    """A concept referenced nowhere is flagged as an orphan-page finding."""
    docs = [_doc("concepts/orphaned", "Nobody links here.")]

    findings = lint.check_orphans(docs, index_text=_INDEX_TEXT)

    assert len(findings) == 1
    assert findings[0].kind == "orphan"
    assert findings[0].path == "concepts/orphaned.md"


def test_check_orphans_uncataloged_source_is_orphan() -> None:
    """A `type: Source` doc uncataloged anywhere is flagged as orphan --
    uniform treatment, no `type` exemption (Q3)."""
    docs = [_doc("sources/uncataloged", "A stray source, never cataloged.")]

    findings = lint.check_orphans(docs, index_text=_INDEX_TEXT)

    assert [f.path for f in findings] == ["sources/uncataloged.md"]


def test_check_orphans_cataloged_source_is_not_orphan() -> None:
    """A `type: Source` doc cataloged in `index.md`'s `# Sources` is NOT an
    orphan -- uniform treatment (Q3)."""
    docs = [_doc("sources/notes", "Raw source imported, cataloged above.")]

    findings = lint.check_orphans(docs, index_text=_INDEX_TEXT)

    assert findings == []


def test_check_orphans_ignores_external_links_in_index() -> None:
    """An external link inside `index.md` (normalizing to `None`) is skipped,
    not added to the referenced-set."""
    index_text = (
        '---\nokf_version: "0.1"\n---\n\n'
        "# Sources\n\n"
        "* [Reference](https://example.com/page) - external context.\n"
    )
    docs = [_doc("concepts/orphaned", "Nobody links here.")]

    findings = lint.check_orphans(docs, index_text=index_text)

    assert [f.path for f in findings] == ["concepts/orphaned.md"]


def test_check_orphans_self_link_does_not_prevent_orphan_status() -> None:
    """A doc whose ONLY inbound link is its OWN self-link is still orphan."""
    docs = [_doc("concepts/self-linker", "See [itself](/concepts/self-linker.md).")]

    findings = lint.check_orphans(docs, index_text='---\nokf_version: "0.1"\n---\n')

    assert [f.path for f in findings] == ["concepts/self-linker.md"]


def test_check_orphans_ignores_external_links_in_doc_body() -> None:
    """An external link inside a doc body (normalizing to `None`) is
    skipped, not added to the referenced-set."""
    docs = [_doc("concepts/stoicism", "See [source](https://example.com) for detail.")]

    findings = lint.check_orphans(docs, index_text='---\nokf_version: "0.1"\n---\n')

    assert [f.path for f in findings] == ["concepts/stoicism.md"]
