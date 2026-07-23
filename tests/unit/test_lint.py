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

from openkos import config, lint
from openkos.model import okf


def _write_doc(
    path: Path,
    *,
    doc_type: str = "Concept",
    body: str = "",
    volatility: str | None = None,
    sensitivity_value: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = f"---\ntype: {doc_type}\ntitle: Stub\n"
    if volatility is not None:
        frontmatter += f"volatility: {volatility}\n"
    if sensitivity_value is not None:
        frontmatter += f"sensitivity: {sensitivity_value}\n"
    frontmatter += "---\n"
    path.write_text(f"{frontmatter}{body}", encoding="utf-8")


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


# --- freshness-lint-v1: LintDoc `type`/`volatility` fields ---


def test_collect_docs_reads_type_field(tmp_path: Path) -> None:
    """`collect_docs` reads the doc's frontmatter `type` field into
    `LintDoc.type` (design: "LintDoc before/after")."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "procedures" / "deploy.md", doc_type="Procedure")

    docs, _skipped = lint.collect_docs(bundle_dir)

    assert docs[0].type == "Procedure"


def test_collect_docs_reads_volatility_field_when_present(tmp_path: Path) -> None:
    """`collect_docs` reads the doc's frontmatter `volatility` field into
    `LintDoc.volatility` when present."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(
        bundle_dir / "procedures" / "deploy.md",
        doc_type="Procedure",
        volatility="static",
    )

    docs, _skipped = lint.collect_docs(bundle_dir)

    assert docs[0].volatility == "static"


def test_collect_docs_defaults_volatility_to_empty_string_when_absent(
    tmp_path: Path,
) -> None:
    """`LintDoc.volatility` defaults to `""` when the frontmatter field is
    absent -- absent-by-default (design: never emitted at ingest, never
    required at read time)."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "stoicism.md")

    docs, _skipped = lint.collect_docs(bundle_dir)

    assert docs[0].volatility == ""


def test_collect_docs_defaults_type_to_empty_string_when_absent(
    tmp_path: Path,
) -> None:
    """`LintDoc.type` defaults to `""` when the frontmatter field is absent
    (malformed/hand-authored doc with no `type:` line)."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)
    (bundle_dir / "concepts" / "untyped.md").write_text(
        "---\ntitle: Stub\n---\nBody.\n", encoding="utf-8"
    )

    docs, _skipped = lint.collect_docs(bundle_dir)

    assert docs[0].type == ""


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


def _doc(
    identity: str,
    body: str,
    *,
    freshness: str = "current",
    type: str = "",
    volatility: str = "",
) -> lint.LintDoc:
    rel_dir = str(PurePosixPath(identity).parent)
    if rel_dir == ".":
        rel_dir = ""
    return lint.LintDoc(
        path=Path(f"/bundle/{identity}.md"),
        identity=identity,
        rel_dir=rel_dir,
        body=body,
        freshness=freshness,
        type=type,
        volatility=volatility,
    )


def test_check_stale_stamps_flags_a_stamp_beyond_the_window() -> None:
    """A stamp older than `window` is flagged as a stale-stamp finding."""
    docs = [_doc("concepts/stoicism", "Body text (as of 2026-07-01).")]

    findings = lint.check_stale_stamps(
        docs,
        today=date(2026, 7, 20),
        windows=lint.VolatilityWindows(
            slow=timedelta(days=7),
            volatile=timedelta(days=7),
            fallback=timedelta(days=7),
        ),
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
        docs,
        today=date(2026, 7, 20),
        windows=lint.VolatilityWindows(
            slow=timedelta(days=7),
            volatile=timedelta(days=7),
            fallback=timedelta(days=7),
        ),
    )

    assert findings == []


def test_check_stale_stamps_exact_boundary_is_not_stale() -> None:
    """A stamp exactly `window` old is NOT stale -- strictly greater flags."""
    docs = [_doc("concepts/stoicism", "Body text (as of 2026-07-13).")]

    findings = lint.check_stale_stamps(
        docs,
        today=date(2026, 7, 20),
        windows=lint.VolatilityWindows(
            slow=timedelta(days=7),
            volatile=timedelta(days=7),
            fallback=timedelta(days=7),
        ),
    )

    assert findings == []


def test_check_stale_stamps_skips_malformed_calendar_dates() -> None:
    """A shape-matching but invalid calendar date (`2026-13-45`) is silently
    skipped -- never flagged, never crashes (Q5, MVP-1 lenient)."""
    docs = [_doc("concepts/stoicism", "Body text (as of 2026-13-45).")]

    findings = lint.check_stale_stamps(
        docs,
        today=date(2026, 7, 20),
        windows=lint.VolatilityWindows(
            slow=timedelta(days=7),
            volatile=timedelta(days=7),
            fallback=timedelta(days=7),
        ),
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
        docs,
        today=date(2026, 7, 20),
        windows=lint.VolatilityWindows(
            slow=timedelta(days=7),
            volatile=timedelta(days=7),
            fallback=timedelta(days=7),
        ),
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
        docs,
        today=date(2026, 7, 20),
        windows=lint.VolatilityWindows(
            slow=timedelta(days=7),
            volatile=timedelta(days=7),
            fallback=timedelta(days=7),
        ),
    )

    assert findings == []


def test_check_stale_stamps_skips_snapshot_docs_with_stamp_shaped_text() -> None:
    """A `freshness: snapshot` doc whose embedded verbatim content
    coincidentally contains an `(as of YYYY-MM-DD)`-shaped string produces
    ZERO stale-stamp findings -- that text is embedded source content, not
    a maintained freshness stamp (D4, scenario: snapshot concept with an
    embedded stamp-shaped string is not flagged)."""
    docs = [
        _doc(
            "sources/notes",
            "Meeting notes mention (as of 2000-01-01) as a historical quote.",
            freshness="snapshot",
        )
    ]

    findings = lint.check_stale_stamps(
        docs,
        today=date(2026, 7, 20),
        windows=lint.VolatilityWindows(
            slow=timedelta(days=7),
            volatile=timedelta(days=7),
            fallback=timedelta(days=7),
        ),
    )

    assert findings == []


def test_check_stale_stamps_still_flags_non_snapshot_docs() -> None:
    """The SAME stale stamp shape, on a doc whose `freshness` is NOT
    `snapshot`, is still flagged -- pinning the new snapshot-skip to
    exactly `freshness == "snapshot"` (D4, scenario: stale stamp is
    flagged)."""
    docs = [
        _doc(
            "sources/notes",
            "Meeting notes mention (as of 2000-01-01) as a historical quote.",
            freshness="current",
        )
    ]

    findings = lint.check_stale_stamps(
        docs,
        today=date(2026, 7, 20),
        windows=lint.VolatilityWindows(
            slow=timedelta(days=7),
            volatile=timedelta(days=7),
            fallback=timedelta(days=7),
        ),
    )

    assert len(findings) == 1
    assert findings[0].kind == "stale"


# --- freshness-lint-v1: check_stale_stamps volatility-aware windows ---


_MIXED_WINDOWS = lint.VolatilityWindows(
    slow=timedelta(days=90), volatile=timedelta(days=7), fallback=timedelta(days=7)
)


def test_check_stale_stamps_static_tier_never_flagged() -> None:
    """A `static`-tier doc (by type default) is NEVER flagged, however
    ancient its stamp (spec: "static-tier concept is never flagged")."""
    docs = [
        _doc(
            "events/founding",
            "Founded (as of 1900-01-01).",
            type="Event",
        )
    ]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), windows=_MIXED_WINDOWS
    )

    assert findings == []


def test_check_stale_stamps_per_concept_override_beats_type_default() -> None:
    """A per-concept `volatility: static` override on a normally
    `volatile`-tier `Procedure` wins over its type default -- old stamp is
    NOT flagged (spec: "Per-concept override wins over type default")."""
    docs = [
        _doc(
            "procedures/legacy",
            "Deploy steps (as of 2000-01-01).",
            type="Procedure",
            volatility="static",
        )
    ]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), windows=_MIXED_WINDOWS
    )

    assert findings == []


def test_check_stale_stamps_slow_tier_default_wins_over_shorter_fallback() -> None:
    """A `slow`-tier (`Concept`) doc uses the 90d `slow` window, not the
    shorter 7d global fallback -- a stamp 30 days old is within the `slow`
    window (spec: "Type default wins over global fallback")."""
    docs = [
        _doc(
            "concepts/stoicism",
            "Recorded (as of 2026-06-20).",
            type="Concept",
        )
    ]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), windows=_MIXED_WINDOWS
    )

    assert findings == []


def test_check_stale_stamps_slow_tier_still_flags_beyond_its_own_window() -> None:
    """The SAME `slow`-tier doc IS flagged once the stamp exceeds the
    90d `slow` window itself -- pinning that the wider window is real, not
    a blanket exemption."""
    docs = [
        _doc(
            "concepts/stoicism",
            "Recorded (as of 2020-01-01).",
            type="Concept",
        )
    ]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), windows=_MIXED_WINDOWS
    )

    assert len(findings) == 1
    assert findings[0].kind == "stale"


def test_check_stale_stamps_unresolvable_volatility_degrades_to_fallback() -> None:
    """An unknown type AND an invalid `volatility` value degrades to the
    global fallback window and never raises (spec: "Unresolvable
    volatility... still degrades to the global fallback window")."""
    docs = [
        _doc(
            "misc/mystery",
            "Recorded (as of 2026-07-01).",
            type="UnknownType",
            volatility="not-a-real-tier",
        )
    ]

    findings = lint.check_stale_stamps(
        docs, today=date(2026, 7, 20), windows=_MIXED_WINDOWS
    )

    assert len(findings) == 1
    assert findings[0].kind == "stale"
    assert "window 7d" in findings[0].detail


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


# --- freshness-lint-v1: resolve_windows(cfg) (load-bearing, never-raising) ---


def _cfg(
    *,
    freshness_window: str = "7d",
    volatility_windows: object = None,
    type_tiers: object = None,
) -> config.Config:
    return config.Config(
        model="qwen3:8b",
        review=True,
        default_sensitivity="private",
        freshness_window=freshness_window,
        embedding_model="bge-m3",
        volatility_windows=({} if volatility_windows is None else volatility_windows),  # type: ignore[arg-type]
        type_tiers=({} if type_tiers is None else type_tiers),  # type: ignore[arg-type]
    )


def test_resolve_windows_valid_map_values() -> None:
    """A fully valid `volatility_windows` map resolves every tier verbatim,
    with zero notices."""
    cfg = _cfg(
        freshness_window="7d",
        volatility_windows={"slow": "30d", "volatile": "3d"},
    )

    windows, notices = lint.resolve_windows(cfg)

    assert windows.slow == timedelta(days=30)
    assert windows.volatile == timedelta(days=3)
    assert windows.fallback == timedelta(days=7)
    assert notices == []


def test_resolve_windows_absent_keys_fall_to_packaged_defaults() -> None:
    """An empty `volatility_windows` map falls back to
    `config.DEFAULT_VOLATILITY_WINDOWS` per tier (design: "absent key falls
    to DEFAULT_VOLATILITY_WINDOWS[tier]")."""
    cfg = _cfg(volatility_windows={})

    windows, notices = lint.resolve_windows(cfg)

    assert windows.slow == timedelta(days=90)
    assert windows.volatile == timedelta(days=7)
    assert notices == []


@pytest.mark.parametrize("tier", ["slow", "volatile"])
def test_resolve_windows_malformed_tier_value_degrades_with_notice(
    tier: str,
) -> None:
    """A malformed per-tier value degrades to `DEFAULT_FRESHNESS_WINDOW`
    (reusing `resolve_window`'s existing fallback), never raises, and
    returns a notice (design: "malformed tier window in config
    degrades... reuses resolve_window")."""
    cfg = _cfg(volatility_windows={tier: "not-a-duration"})

    windows, notices = lint.resolve_windows(cfg)

    assert getattr(windows, tier) == timedelta(days=7)
    assert len(notices) == 1
    assert "not-a-duration" in notices[0]


@pytest.mark.parametrize(
    "bogus_map",
    [None, [], "not-a-map", 42, ["slow", "90d"]],
)
def test_resolve_windows_non_map_treated_as_empty(bogus_map: object) -> None:
    """A `volatility_windows` that is not a map at all (`null`, a list, a
    scalar) is treated as empty -- every tier falls to its packaged default,
    never raises (design: "non-map/null treated empty")."""
    cfg = _cfg(volatility_windows=bogus_map)

    windows, notices = lint.resolve_windows(cfg)

    assert windows.slow == timedelta(days=90)
    assert windows.volatile == timedelta(days=7)
    assert notices == []


def test_resolve_windows_fallback_uses_freshness_window() -> None:
    """`windows.fallback` resolves from `cfg.freshness_window`, exactly like
    today's global-window path."""
    cfg = _cfg(freshness_window="14d")

    windows, _notices = lint.resolve_windows(cfg)

    assert windows.fallback == timedelta(days=14)


def test_resolve_windows_malformed_fallback_degrades_with_notice() -> None:
    """A malformed `freshness_window` still degrades via `resolve_window`,
    surfacing its own notice alongside any tier notices."""
    cfg = _cfg(freshness_window="not-a-duration")

    windows, notices = lint.resolve_windows(cfg)

    assert windows.fallback == timedelta(days=7)
    assert any("not-a-duration" in notice for notice in notices)


def test_resolve_windows_never_raises() -> None:
    """`resolve_windows` never raises on any combination of bad config
    (Q4/read-only-never-fail contract)."""
    cfg = _cfg(
        freshness_window="garbage",
        volatility_windows={"slow": "garbage", "volatile": None},
    )

    lint.resolve_windows(cfg)


# --- freshness-suggest-windows: resolve_windows(cfg) type_tiers guard ---


def test_resolve_windows_type_tiers_passes_through_verbatim() -> None:
    """A valid `type_tiers` map is threaded onto the resolved
    `VolatilityWindows` verbatim."""
    cfg = _cfg(type_tiers={"Person": "volatile"})

    windows, notices = lint.resolve_windows(cfg)

    assert windows.type_tiers == {"Person": "volatile"}
    assert notices == []


@pytest.mark.parametrize(
    "bogus_map",
    [None, [], "not-a-map", 42, ["Person", "volatile"]],
)
def test_resolve_windows_type_tiers_non_map_treated_as_empty(
    bogus_map: object,
) -> None:
    """A `type_tiers` that is not a map at all (`null`, a list, a scalar)
    degrades to `{}` on the resolved `VolatilityWindows`, mirroring
    `volatility_windows`'s non-mapping guard (lint.py:212-213) -- never
    raises."""
    cfg = _cfg(type_tiers=bogus_map)

    windows, notices = lint.resolve_windows(cfg)

    assert windows.type_tiers == {}
    assert notices == []


# --- freshness-lint-v1: window_for_doc(doc, windows) precedence ---


_WINDOWS = lint.VolatilityWindows(
    slow=timedelta(days=90), volatile=timedelta(days=7), fallback=timedelta(days=7)
)


@pytest.mark.parametrize(
    ("doc_type", "volatility", "expected"),
    [
        # Per-concept override wins over type default.
        ("Concept", "volatile", _WINDOWS.volatile),  # slow default overridden
        ("Procedure", "static", None),  # volatile default overridden to static
        # Absent volatility -> per-type default.
        ("Procedure", "", _WINDOWS.volatile),
        ("Concept", "", _WINDOWS.slow),
        ("Place", "", None),  # static type default -> never flagged
        # Unknown volatility value -> per-type default.
        ("Person", "not-a-tier", _WINDOWS.slow),
        # Unknown/absent type + no override -> global fallback.
        ("", "", _WINDOWS.fallback),
        ("SomeUnknownType", "", _WINDOWS.fallback),
        # static tier (override or default) -> never flagged.
        ("Event", "", None),
        ("Source", "", None),
        ("Decision", "", None),
    ],
)
def test_window_for_doc_precedence_table(
    doc_type: str, volatility: str, expected: timedelta | None
) -> None:
    """Exhaustive precedence table (design: "Resolution algorithm"): per-
    concept override -> per-type default -> global fallback; `static`
    (by override or type default) always resolves to `None` (never
    flagged)."""
    doc = _doc("concepts/x", "body", type=doc_type, volatility=volatility)

    assert lint.window_for_doc(doc, _WINDOWS) == expected


# --- freshness-suggest-windows: window_for_doc `type_tiers` override step ---


def _windows_with_tiers(type_tiers: dict[str, str]) -> lint.VolatilityWindows:
    return lint.VolatilityWindows(
        slow=timedelta(days=90),
        volatile=timedelta(days=7),
        fallback=timedelta(days=7),
        type_tiers=type_tiers,
    )


def test_window_for_doc_type_tiers_override_wins_over_registry_default() -> None:
    """A valid `type_tiers` entry overrides the per-type registry default
    when no per-concept `volatility` is present (concept-volatility spec,
    ADDED requirement: `type_tiers` Config Override Layer)."""
    windows = _windows_with_tiers({"Person": "volatile"})
    doc = _doc("people/ada", "body", type="Person", volatility="")

    # Person's registry default is "slow"; type_tiers overrides to "volatile".
    assert lint.window_for_doc(doc, windows) == windows.volatile


def test_window_for_doc_per_concept_volatility_still_wins_over_type_tiers() -> None:
    """A per-concept `volatility` override still wins over a `type_tiers`
    entry for the same type -- `type_tiers` sits BETWEEN the per-concept
    override and the registry default in the precedence chain, never above
    it."""
    windows = _windows_with_tiers({"Person": "volatile"})
    doc = _doc("people/ada", "body", type="Person", volatility="static")

    assert lint.window_for_doc(doc, windows) is None


def test_window_for_doc_type_tiers_invalid_tier_value_falls_through() -> None:
    """A `type_tiers` entry whose value is not a member of
    `types.VOLATILITY_TIERS` is ignored -- resolution falls through to the
    per-type registry default, never raises."""
    windows = _windows_with_tiers({"Person": "bogus-tier"})
    doc = _doc("people/ada", "body", type="Person", volatility="")

    # Person's registry default ("slow") applies; the invalid entry is ignored.
    assert lint.window_for_doc(doc, windows) == windows.slow


def test_window_for_doc_type_tiers_unhashable_list_value_degrades() -> None:
    """A `type_tiers` entry whose value is an unhashable list (e.g. a
    hand-edited `openkos.yaml` with `type_tiers: {Person: [slow]}`) never
    raises `TypeError` from the `VOLATILITY_TIERS` membership check --
    resolution degrades to the per-type registry default, same as any
    other invalid tier value (resilience fix, freshness-suggest-windows
    PR1 correction)."""
    windows = _windows_with_tiers({"Person": ["slow"]})  # type: ignore[dict-item]
    doc = _doc("people/ada", "body", type="Person", volatility="")

    # Person's registry default ("slow") applies; the unhashable entry is ignored.
    assert lint.window_for_doc(doc, windows) == windows.slow


def test_window_for_doc_type_tiers_unhashable_dict_value_degrades() -> None:
    """A `type_tiers` entry whose value is an unhashable dict never raises
    `TypeError` from the `VOLATILITY_TIERS` membership check -- resolution
    degrades to the per-type registry default (resilience fix,
    freshness-suggest-windows PR1 correction)."""
    windows = _windows_with_tiers({"Person": {"tier": "slow"}})  # type: ignore[dict-item]
    doc = _doc("people/ada", "body", type="Person", volatility="")

    assert lint.window_for_doc(doc, windows) == windows.slow


def test_window_for_doc_type_tiers_unknown_type_key_is_ignored() -> None:
    """A `type_tiers` entry for a type absent from the registry never
    matches any doc, and never raises -- resolution for an unregistered
    type falls straight through to the global fallback, same as with no
    `type_tiers` at all."""
    windows = _windows_with_tiers({"UnknownTypeName": "slow"})
    doc = _doc("x/y", "body", type="UnknownTypeName", volatility="")

    assert lint.window_for_doc(doc, windows) == windows.fallback


def test_window_for_doc_type_tiers_resolving_to_static_is_never_flagged() -> None:
    """A `type_tiers` entry resolving to `static` is never flagged stale,
    identical to any other `static` resolution (concept-volatility spec
    scenario: "`type_tiers` resolving to `static` is never flagged")."""
    windows = _windows_with_tiers({"Person": "static"})
    doc = _doc("people/ada", "body", type="Person", volatility="")

    assert lint.window_for_doc(doc, windows) is None


def test_window_for_doc_absent_type_tiers_reproduces_s1_precedence_table() -> None:
    """Regression pin: a `VolatilityWindows` built with no `type_tiers`
    (default `{}`) reproduces byte-identical S1 precedence for every case in
    `test_window_for_doc_precedence_table` (concept-volatility spec
    scenario: "Absent `type_tiers` reproduces exact S1 behavior")."""
    windows = lint.VolatilityWindows(
        slow=timedelta(days=90), volatile=timedelta(days=7), fallback=timedelta(days=7)
    )
    assert windows.type_tiers == {}
    cases = [
        ("Concept", "volatile", windows.volatile),
        ("Procedure", "static", None),
        ("Procedure", "", windows.volatile),
        ("Concept", "", windows.slow),
        ("Place", "", None),
        ("Person", "not-a-tier", windows.slow),
        ("", "", windows.fallback),
        ("SomeUnknownType", "", windows.fallback),
        ("Event", "", None),
        ("Source", "", None),
        ("Decision", "", None),
    ]
    for doc_type, volatility, expected in cases:
        doc = _doc("concepts/x", "body", type=doc_type, volatility=volatility)
        assert lint.window_for_doc(doc, windows) == expected
