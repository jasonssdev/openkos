"""Direct unit tests for `openkos.application.list_service`: the `list`
command's read core, extracted out of `cli/main.py` (issue #995, PR 5 of
MVP 3's "prerequisite zero" -- the read verbs need an application service
before an MCP adapter can be a thin layer instead of a second
implementation).

Mirrors `test_status_service.py`'s posture: these exercise the service
functions directly against a real (tmp-path) workspace, never a CLI
invocation. `tests/unit/cli/test_list.py` stays the black-box, rendered-
output contract; this file is the one that can see the raw
`listing.BundleObject` rows and the typed validation outcomes the CLI
adapter later renders as `typer.echo` + `typer.Exit`.

Unlike `status`, `list` has no partial-output property to preserve: the
pre-extraction body performs exactly ONE disk-reading call
(`listing.list_objects`) on the ordinary path, and it runs AFTER every
usage-validation check and the workspace gate, with every `typer.echo`
coming after it returns -- there is nothing echoed before a later read
that could still fail, so one service call per mode is the faithful shape
here (the same conclusion `application/lint.py`'s own module docstring
recorded for `lint`, on the same kind of read-top-to-bottom evidence)."""

from pathlib import Path

import pytest

from openkos import config
from openkos.application import list_service
from openkos.bundle import listing


def _workspace(tmp_path: Path) -> config.WorkspaceLayout:
    """A workspace root with a real (empty) `bundle/` directory -- `list`
    reads the bundle directly (not through a CLI `init`), so the directory
    must exist before any service function is called on it."""
    config.write_config(tmp_path)
    layout = config.WorkspaceLayout(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    return layout


def _write_doc(
    path: Path,
    *,
    type_: str = "Concept",
    title: str | None = "Stub",
    status: str | None = None,
    sensitivity: str | None = None,
    provenance: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"type: {type_}"]
    if title is not None:
        lines.append(f"title: {title}")
    if status is not None:
        lines.append(f"status: {status}")
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    if provenance is not None:
        lines.append("provenance:")
        lines.extend(f"  - {entry}" for entry in provenance)
    lines.append("---")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# validate_list_arguments -- the three pre-workspace usage refusals
# ---------------------------------------------------------------------------


def test_validate_list_arguments_rejects_sources_with_a_type_filter() -> None:
    """`--sources` together with a TYPE filter raises
    `SourcesModeTakesTypeFilter` -- a whole mode, not filterable (#628).
    Pure argument validation: no workspace or `layout` argument needed,
    which is itself part of the proof this check needs no disk access.

    Also pins R2-usage-error-family-is-not-uniform (issue #995 PR 6
    review): this was the only one of the three `ListUsageError` members
    raised with no arguments, so `str(exc)` was empty while its two
    siblings both carry a formatted message and structured fields -- it
    now carries both, matching them."""
    with pytest.raises(list_service.SourcesModeTakesTypeFilter) as excinfo:
        list_service.validate_list_arguments(
            concept_type="people", sources_of="concepts/a", limit=50
        )

    assert excinfo.value.concept_type == "people"
    assert excinfo.value.sources_of == "concepts/a"
    assert str(excinfo.value) != ""
    assert "people" in str(excinfo.value)
    assert "concepts/a" in str(excinfo.value)


def test_validate_list_arguments_rejects_an_unresolvable_type() -> None:
    """An unrecognized TYPE filter raises `UnknownTypeFilter`, carrying the
    original value and the sorted, canonical `link_dir` vocabulary the CLI
    adapter enumerates in its refusal message -- never the
    `REGISTRY.name` aliases (spec: Type Filter Vocabulary)."""
    with pytest.raises(list_service.UnknownTypeFilter) as excinfo:
        list_service.validate_list_arguments(
            concept_type="bogus-type", sources_of=None, limit=50
        )

    assert excinfo.value.concept_type == "bogus-type"
    assert "people" in excinfo.value.valid_link_dirs
    assert "sources" in excinfo.value.valid_link_dirs
    # Only canonical link_dir names, never a REGISTRY.name alias like "Person".
    assert "Person" not in excinfo.value.valid_link_dirs


def test_validate_list_arguments_resolves_a_canonical_link_dir() -> None:
    """A canonical `link_dir` filter resolves to itself."""
    resolved = list_service.validate_list_arguments(
        concept_type="people", sources_of=None, limit=50
    )
    assert resolved == "people"


def test_validate_list_arguments_resolves_a_registry_name_alias() -> None:
    """A case-sensitive `REGISTRY.name` alias resolves to its `link_dir`
    (spec: Filter by REGISTRY.name alias)."""
    resolved = list_service.validate_list_arguments(
        concept_type="Person", sources_of=None, limit=50
    )
    assert resolved == "people"


def test_validate_list_arguments_allows_no_type_filter() -> None:
    """Omitting TYPE resolves to `None` -- list everything."""
    resolved = list_service.validate_list_arguments(
        concept_type=None, sources_of=None, limit=50
    )
    assert resolved is None


@pytest.mark.parametrize("limit", [0, -1, -50])
def test_validate_list_arguments_rejects_non_positive_limit(limit: int) -> None:
    """`--limit 0` and any negative `--limit` raise `NonPositiveLimit`,
    carrying the offending value (spec: Output Bounding)."""
    with pytest.raises(list_service.NonPositiveLimit) as excinfo:
        list_service.validate_list_arguments(
            concept_type=None, sources_of=None, limit=limit
        )
    assert excinfo.value.limit == limit


def test_validate_list_arguments_sources_conflict_checked_before_type_resolution() -> (
    None
):
    """The `--sources`+TYPE conflict is raised even when TYPE is also
    unresolvable -- proving the ladder's first rung runs first, exactly as
    the pre-extraction body's `if sources_of is not None and concept_type
    is not None` ran before the TYPE-resolution block."""
    with pytest.raises(list_service.SourcesModeTakesTypeFilter):
        list_service.validate_list_arguments(
            concept_type="bogus-type", sources_of="concepts/a", limit=50
        )


def test_validate_list_arguments_type_resolution_precedes_the_limit_check() -> None:
    """An unresolvable TYPE is refused before a non-positive `--limit`,
    even though both are wrong in the same call.

    The ladder's rung ORDER decides which refusal an operator sees and
    which typed error an adapter receives. Nothing else pins the second
    rung against the third, so a reordering inside the extracted function
    would silently change both while every other test here stayed green.
    The order matches the pre-extraction body, where the TYPE-resolution
    block ran above the `limit <= 0` check."""
    with pytest.raises(list_service.UnknownTypeFilter):
        list_service.validate_list_arguments(
            concept_type="bogus-type", sources_of=None, limit=0
        )


# ---------------------------------------------------------------------------
# list_bundle_objects -- the ordinary listing's single-walk read core
# ---------------------------------------------------------------------------


def test_list_bundle_objects_returns_every_row_when_unfiltered(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "people" / "jane.md", title="Jane")
    _write_doc(layout.bundle_dir / "sources" / "book.md", title="A Book")

    result = list_service.list_bundle_objects(
        layout, resolved_type=None, limit=50, all_objects=False
    )

    ids = {row.concept_id for row in result.rows}
    assert ids == {"people/jane", "sources/book"}
    assert result.total == 2
    assert result.shown == result.rows


def test_list_bundle_objects_filters_by_resolved_link_dir(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "people" / "jane.md", title="Jane")
    _write_doc(layout.bundle_dir / "sources" / "book.md", title="A Book")

    result = list_service.list_bundle_objects(
        layout, resolved_type="people", limit=50, all_objects=False
    )

    assert [row.concept_id for row in result.rows] == ["people/jane"]
    assert result.total == 1


def test_list_bundle_objects_truncates_to_limit_and_reports_total(
    tmp_path: Path,
) -> None:
    """R3-shown-slice-identity-unproved (issue #995 PR 6 review): asserting
    only `len(result.shown) == 3` lets a slice returning the WRONG three
    rows (the last three, a shuffled three, three duplicates of the first)
    pass just as easily as the correct one. `listing.list_objects` documents
    its rows sorted by `concept_id` (#389), so the first three of ten
    `c000..c009` rows are deterministic -- assert identity AND order."""
    layout = _workspace(tmp_path)
    for i in range(10):
        _write_doc(layout.bundle_dir / "concepts" / f"c{i:03d}.md", title=f"C{i}")

    result = list_service.list_bundle_objects(
        layout, resolved_type=None, limit=3, all_objects=False
    )

    assert [row.concept_id for row in result.shown] == [
        "concepts/c000",
        "concepts/c001",
        "concepts/c002",
    ]
    assert result.total == 10
    assert len(result.rows) == 10


def test_list_bundle_objects_all_objects_ignores_limit(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    for i in range(10):
        _write_doc(layout.bundle_dir / "concepts" / f"c{i:03d}.md", title=f"C{i}")

    result = list_service.list_bundle_objects(
        layout, resolved_type=None, limit=3, all_objects=True
    )

    assert len(result.shown) == 10
    assert result.total == 10


def test_list_bundle_objects_walks_the_bundle_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The enumerator is invoked exactly once, even with a TYPE filter and
    `limit` applied -- filtering/limiting happen in memory (spec: Exactly
    One Bundle Walk)."""
    from collections.abc import Iterator

    from openkos.model import okf

    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "people" / "jane.md", title="Jane")

    calls: list[Path] = []
    original = okf._iter_docs

    def _counting_iter_docs(bundle_dir: Path) -> Iterator[okf.DocScan]:
        calls.append(bundle_dir)
        return original(bundle_dir)

    monkeypatch.setattr(okf, "_iter_docs", _counting_iter_docs)

    list_service.list_bundle_objects(
        layout, resolved_type="people", limit=5, all_objects=False
    )

    assert len(calls) == 1


def test_list_bundle_objects_never_calls_deprecated_concept_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status is derived entirely inside `listing.list_objects`'s own
    single pass (design D3) -- `lifecycle.deprecated_concept_ids` must
    never be called from this service function."""
    from openkos import lifecycle

    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "people" / "jane.md", title="Jane")

    calls: list[Path] = []
    real = lifecycle.deprecated_concept_ids

    def _counting(bundle_dir: Path) -> frozenset[str]:
        calls.append(bundle_dir)
        return real(bundle_dir)

    monkeypatch.setattr(lifecycle, "deprecated_concept_ids", _counting)

    list_service.list_bundle_objects(
        layout, resolved_type=None, limit=50, all_objects=False
    )

    # A counter, not a raising stub. A stub that raises proves nothing on
    # its own: if the patch ever stops intercepting -- a rename, or a
    # consumer switching to `from openkos.lifecycle import
    # deprecated_concept_ids`, which binds the object at import time and
    # is invisible to a module-attribute patch -- the stub is simply never
    # reached and the test passes having asserted nothing. Counting
    # records what happened, so the assertion below can fail.
    assert calls == []

    # And the counter is proved to be wired: calling through the patched
    # module attribute the way the production code does DOES record.
    assert lifecycle.deprecated_concept_ids(layout.bundle_dir) is not None
    assert calls == [layout.bundle_dir]


def test_list_bundle_objects_empty_bundle_returns_no_rows(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)

    result = list_service.list_bundle_objects(
        layout, resolved_type=None, limit=50, all_objects=False
    )

    assert result.rows == ()
    assert result.total == 0
    assert result.shown == ()


def test_list_bundle_objects_marks_unreadable_documents(tmp_path: Path) -> None:
    """A document with unparseable frontmatter still yields a row, marked
    unreadable, never raising."""
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "people" / "jane.md", title="Jane")
    broken = layout.bundle_dir / "people" / "broken.md"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("---\ntype: [unterminated\n---\nbody\n", encoding="utf-8")

    result = list_service.list_bundle_objects(
        layout, resolved_type=None, limit=50, all_objects=False
    )

    by_id = {row.concept_id: row for row in result.rows}
    assert by_id["people/broken"].readable is False
    assert by_id["people/jane"].readable is True


# ---------------------------------------------------------------------------
# list_provenance_sources -- the `--sources` reverse-provenance mode
# ---------------------------------------------------------------------------


def test_list_provenance_sources_names_every_reaching_source(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _write_doc(
        layout.bundle_dir / "people" / "jane.md",
        type_="Person",
        title="Jane",
        provenance=["sources/transcription1", "concepts/mid"],
    )
    _write_doc(
        layout.bundle_dir / "sources" / "transcription1.md",
        type_="Source",
        title="Transcription 1",
        sensitivity="private",
        provenance=["raw1.txt"],
    )
    _write_doc(
        layout.bundle_dir / "concepts" / "mid.md",
        title="Mid",
        provenance=["sources/deep"],
    )
    _write_doc(
        layout.bundle_dir / "sources" / "deep.md",
        type_="Source",
        title="Deep",
        provenance=["raw2.txt"],
    )

    result = list_service.list_provenance_sources(layout, "people/jane")

    # Order, not membership: `ProvenanceSources.ancestors` is documented
    # sorted and the CLI adapter renders it row by row in exactly this
    # iteration order, so a set comparison would let an ordering
    # regression produce non-deterministic user-visible output with every
    # test still green.
    assert result.ancestors == ("sources/deep", "sources/transcription1")
    rows_by_id = {row.concept_id: row for row in result.rows}
    assert rows_by_id["sources/transcription1"].sensitivity == "private"


def test_list_provenance_sources_skips_reserved_filenames(tmp_path: Path) -> None:
    """`index.md`/`log.md` (`okf.RESERVED_FILENAMES`) must never be read
    into `files`, even when one carries its own `provenance:` entry
    (R3-provenance-skip-paths-untested, issue #995 PR 6 review).

    `concepts/solo.md` names "index" (the reserved file's own bare id) as
    its provenance parent -- an odd entry no real workspace would write,
    chosen deliberately so the exploit is unambiguous: if `index.md` were
    read despite the skip, walking `concepts/solo` -> "index" would reach
    `index.md`'s own declared `sources/hidden` parent and report it as an
    ancestor. Skipped correctly, "index" is never a key in the parsed
    provenance map, so the walk dead-ends there and `ancestors` stays
    empty."""
    layout = _workspace(tmp_path)
    _write_doc(
        layout.bundle_dir / "concepts" / "solo.md",
        provenance=["index"],
    )
    _write_doc(
        layout.bundle_dir / "sources" / "hidden.md",
        type_="Source",
        title="Hidden",
    )
    (layout.bundle_dir / "index.md").write_text(
        "---\ntype: Concept\nprovenance:\n  - sources/hidden\n---\n# Index\n",
        encoding="utf-8",
    )

    result = list_service.list_provenance_sources(layout, "concepts/solo")

    assert result.ancestors == ()


def test_list_provenance_sources_skips_unreadable_files_without_crashing(
    tmp_path: Path,
) -> None:
    """Pre-existing behaviour (`except (OSError, UnicodeDecodeError):
    continue`), pinned rather than changed
    (R3-provenance-skip-paths-untested, issue #995 PR 6 review): a file
    that fails to decode as UTF-8 contributes no provenance edges instead
    of raising, while a sibling READABLE file's edges still resolve
    normally."""
    layout = _workspace(tmp_path)
    _write_doc(
        layout.bundle_dir / "concepts" / "solo.md",
        provenance=["sources/good"],
    )
    _write_doc(
        layout.bundle_dir / "sources" / "good.md",
        type_="Source",
        title="Good",
    )
    bad_path = layout.bundle_dir / "concepts" / "bad.md"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    # 0xFF is not a valid UTF-8 lead byte -- `read_text(encoding="utf-8")`
    # raises `UnicodeDecodeError`.
    bad_path.write_bytes(b"\xff\xfe---\ntype: Concept\n---\n")

    result = list_service.list_provenance_sources(layout, "concepts/solo")

    assert result.ancestors == ("sources/good",)


def test_list_provenance_sources_reports_no_ancestors_as_empty(
    tmp_path: Path,
) -> None:
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "concepts" / "solo.md")

    result = list_service.list_provenance_sources(layout, "concepts/solo")

    assert result.ancestors == ()
    # No rows lookup is built when there is nothing to look up.
    assert result.rows == ()


def test_list_provenance_sources_does_not_build_rows_lookup_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors the pre-extraction body precisely: `rows_by_id` (here,
    `.rows`) is only ever computed when `ancestors` is non-empty --
    `listing.list_objects` must not run a second time for an object no
    Source reaches."""
    layout = _workspace(tmp_path)
    _write_doc(layout.bundle_dir / "concepts" / "solo.md")

    calls: list[Path] = []
    original = listing.list_objects

    def _counting_list_objects(bundle_dir: Path) -> list[listing.BundleObject]:
        calls.append(bundle_dir)
        return original(bundle_dir)

    monkeypatch.setattr(listing, "list_objects", _counting_list_objects)

    list_service.list_provenance_sources(layout, "concepts/solo")

    assert calls == []

    # And the counter is proved wired: an empty `calls` only means "the
    # service did not call this" if the patch actually intercepts. Calling
    # through the patched module attribute the way the production code does
    # must record -- otherwise a binding change (a rename, or a consumer
    # switching to `from openkos.bundle.listing import list_objects`, which
    # binds at import time and is invisible to a module-attribute patch)
    # would leave `calls` empty for the wrong reason and this assertion
    # would pass having proved nothing.
    listing.list_objects(layout.bundle_dir)
    assert calls == [layout.bundle_dir]


def test_list_provenance_sources_includes_a_dangling_source_id(
    tmp_path: Path,
) -> None:
    """A provenance entry naming a Source with no file behind it is still
    reported among `ancestors`, with no corresponding row (#628)."""
    layout = _workspace(tmp_path)
    _write_doc(
        layout.bundle_dir / "people" / "jane.md",
        type_="Person",
        provenance=["sources/gone"],
    )

    result = list_service.list_provenance_sources(layout, "people/jane")

    assert "sources/gone" in result.ancestors
    rows_by_id = {row.concept_id: row for row in result.rows}
    assert "sources/gone" not in rows_by_id


def test_list_provenance_sources_excludes_dot_directory_sources(
    tmp_path: Path,
) -> None:
    """#984: a Source under a dot-directory supplies no provenance edges,
    matching the shared bundle walk's dot-directory exclusion."""
    layout = _workspace(tmp_path)
    _write_doc(
        layout.bundle_dir / "people" / "jane.md",
        type_="Person",
        title="Jane",
        provenance=["sources/.drafts/stray", "sources/real"],
    )
    _write_doc(
        layout.bundle_dir / "sources" / ".drafts" / "stray.md",
        type_="Source",
        title="Stray Source",
        provenance=["sources/behind-the-stray"],
    )
    _write_doc(
        layout.bundle_dir / "sources" / "behind-the-stray.md",
        type_="Source",
        title="Behind The Stray",
        provenance=["raw1.txt"],
    )
    _write_doc(
        layout.bundle_dir / "sources" / "real.md",
        type_="Source",
        title="Real Source",
        provenance=["raw2.txt"],
    )

    result = list_service.list_provenance_sources(layout, "people/jane")

    assert "sources/real" in result.ancestors
    assert "sources/behind-the-stray" not in result.ancestors
