"""The `list` command's read core (issue #995, PR 5 of MVP 3's
"prerequisite zero" -- the read verbs need an application service before an
MCP adapter can be a thin layer instead of a second implementation).

**No partial-output property to preserve.** Read top to bottom (the same
evidence-first method `application/lint.py`'s own module docstring records
for `lint`, not assumed by analogy to `status`): the pre-extraction
`list_objects_cmd` body performs exactly ONE disk-reading call on its
ordinary path, `listing.list_objects(layout.bundle_dir)` (design D3,
"Exactly One Bundle Walk"), and every `typer.echo` in that body runs AFTER
that call returns. Nothing is printed, and nothing could be printed, before
a later read that might still fail -- unlike `status`, whose header,
`Bundle contents:` and `Recent activity:` sections rendered from a cheap
guarded read BEFORE later unguarded reads ran. `list --sources` (the
`_run_list_sources` helper) has the same shape one level down: it resolves
the concept id, walks the bundle for provenance edges, and only THEN
echoes anything -- including its own `rows_by_id` lookup, computed only
after the `ancestors` check, never before it. One service call per mode is
therefore the faithful shape here, exactly as it was for `lint`.

**Where the five `typer.Exit`s went.** The pre-extraction body had four,
plus one in `_run_list_sources`:

1. `--sources` combined with a TYPE filter. Pure argument validation --
   reads no workspace, touches no disk. Moved here as
   `SourcesModeTakesTypeFilter`, raised by `validate_list_arguments`.
2. An unresolvable TYPE filter. Also pure -- `listing.resolve_link_dir` is
   an in-memory vocabulary lookup over `model.types.REGISTRY`, not a disk
   read. Moved here as `UnknownTypeFilter`.
3. `--limit <= 0`. Also pure. Moved here as `NonPositiveLimit`.
4. The workspace gate (`config.require_workspace`). Stays adapter-side,
   unchanged -- the same call `query` and `status` both keep in
   `cli/main.py` rather than wrapping in a typed outcome, because it is a
   PRESENCE check on the CURRENT PROCESS's cwd, not a fact about the
   arguments the caller passed; a headless adapter (MCP, API) resolves its
   own workspace root through its own mechanism and calls
   `config.require_workspace` (or equivalent) itself, exactly as the CLI
   adapter does here.
5. `_run_list_sources`'s bad-concept-id refusal
   (`application_lifecycle.resolve_concept_path` raising `ValueError`).
   Deliberately NOT wrapped by this module. The pre-extraction `try/except
   (OSError, ValueError)` around that one call, and ONLY that call, is what
   let the previous slice's mistake (widening a `try` and silently
   relabelling an unrelated in-memory `ValueError` as a read failure) get
   caught in review; the fix here is to preserve that exact boundary
   rather than re-litigate it. `list_provenance_sources` below therefore
   takes an ALREADY-RESOLVED `canonical_id`, not the raw `object_id` --
   resolution and its `except (OSError, ValueError)` stay in the CLI
   adapter's `_run_list_sources`, calling `application_lifecycle.
   resolve_concept_path` directly, exactly as it already did before this
   extraction (that function already lived in `application/lifecycle.py`).
   Widening this function's own contract to also catch that error would
   risk the identical mistake: `provenance_source_ancestors` and
   `listing.list_objects`, both called below, are not known to raise
   `OSError`/`ValueError` in the ordinary course, and catching around them
   too would relabel a genuinely different failure as "bad concept id".

Validation (1-3) is exposed as one function, `validate_list_arguments`,
rather than three, so the exact ladder ORDER the pre-extraction body
enforced through control flow (`--sources`+TYPE conflict, then TYPE
resolution, then `--limit`) stays enforced in exactly one place instead of
being an invariant the calling adapter must reconstruct by calling three
functions in the right sequence.

This module shares no helper with any other command -- `list` is, like
`lint`, entirely self-contained; there is nothing to add to
`tests/unit/application/test_layering.py`'s shared-definition guards.

**Error handling is otherwise unchanged.** `list_bundle_objects` performs
no error handling of its own beyond what `listing.list_objects` already
provides (an unreadable/unparseable document becomes a row marked
`readable=False`, never an exception -- unchanged from before this
extraction). The CLI adapter keeps every `typer.echo`, every wording, the
`ljust` column layout, and every exit code (ADR-0018, `application/
status.py`'s own precedent: raw facts here, rendered strings there)."""

from __future__ import annotations

from dataclasses import dataclass

from openkos import config
from openkos.bundle import listing
from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf, types


class ListUsageError(Exception):
    """Base for `list`'s three pre-workspace, pre-disk argument-validation
    refusals (see module docstring, "Where the five `typer.Exit`s went",
    items 1-3). Never raised for anything workspace- or bundle-related --
    those stay `ValueError`/`OSError` from the calls that already raise
    them, unchanged."""


class SourcesModeTakesTypeFilter(ListUsageError):
    """`--sources` was combined with a TYPE filter (#628): `--sources` is a
    whole mode with nothing for a TYPE filter to narrow.

    Carries the two conflicting values and a formatted message (R2-usage-
    error-family-is-not-uniform, issue #995 PR 6 review): this was
    previously the only one of the three `ListUsageError` members raised
    with no arguments, so `str(exc)` was empty while its siblings
    (`UnknownTypeFilter`, `NonPositiveLimit`) both carry a formatted
    message and structured fields. The CLI adapter's own `except` clause
    for this member does not read either -- it prints its own hardcoded
    text -- so this is a data-uniformity fix for any OTHER caller (a
    future MCP adapter, a test) that catches this family generically and
    logs `str(exc)`, not a CLI-visible behaviour change."""

    def __init__(self, concept_type: str, sources_of: str) -> None:
        self.concept_type = concept_type
        """The TYPE filter value that was combined with `--sources`."""
        self.sources_of = sources_of
        """The `--sources` target id it was combined with."""
        super().__init__(
            f"--sources {sources_of!r} cannot be combined with a TYPE filter "
            f"({concept_type!r}) -- --sources is a whole mode with nothing "
            "for a TYPE filter to narrow"
        )


class UnknownTypeFilter(ListUsageError):
    """`concept_type` resolved to no canonical `link_dir` via
    `listing.resolve_link_dir` (spec: Type Filter Vocabulary)."""

    def __init__(self, concept_type: str, valid_link_dirs: tuple[str, ...]) -> None:
        self.concept_type = concept_type
        """The raw, unresolved value the caller passed."""
        self.valid_link_dirs = valid_link_dirs
        """Sorted canonical `link_dir` names only -- never `REGISTRY.name`
        aliases, matching the pre-extraction refusal message exactly."""
        super().__init__(
            f"{concept_type!r} is not a known object type "
            f"(expected one of {list(valid_link_dirs)})"
        )


class NonPositiveLimit(ListUsageError):
    """`--limit` was zero or negative (spec: Output Bounding)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(
            f"--limit must be positive (got {limit}); use --all to print "
            "every row instead"
        )


def validate_list_arguments(
    *, concept_type: str | None, sources_of: str | None, limit: int
) -> str | None:
    """Run `list`'s three pure, pre-workspace usage checks in the SAME
    order the pre-extraction body's control flow enforced, returning the
    resolved TYPE filter (a canonical `link_dir`, or `None` when no TYPE
    was given) on success. Raises the corresponding `ListUsageError`
    subclass on the first check that fails; performs no workspace or disk
    access of its own (`listing.resolve_link_dir` is an in-memory
    vocabulary lookup)."""
    if sources_of is not None and concept_type is not None:
        raise SourcesModeTakesTypeFilter(concept_type, sources_of)

    resolved_type: str | None = None
    if concept_type is not None:
        resolved_type = listing.resolve_link_dir(concept_type)
        if resolved_type is None:
            valid_link_dirs = tuple(
                sorted(ot.link_dir for ot in types.REGISTRY if ot.link_dir)
            )
            raise UnknownTypeFilter(concept_type, valid_link_dirs)

    if limit <= 0:
        raise NonPositiveLimit(limit)

    return resolved_type


@dataclass(frozen=True)
class ListedObjects:
    """The ordinary `list` mode's raw result: every field a `tuple`, never
    a `list` -- the dataclass is frozen, and a `list` field on a frozen
    dataclass is still mutable in place (same reasoning as `application/
    status.py`'s `StatusReport`)."""

    rows: tuple[listing.BundleObject, ...]
    """Every row after the TYPE filter (if any) -- BEFORE the `--limit`/
    `--all` slice. Empty exactly when nothing matched (or the bundle is
    empty), which is the CLI adapter's cue to print the "No objects
    found." empty-state line instead of a header and no rows."""

    total: int
    """`len(rows)` -- the post-filter, pre-slice count the CLI adapter's
    truncation footer reports as "of N"."""

    shown: tuple[listing.BundleObject, ...]
    """`rows`, sliced to `limit` unless `all_objects` was `True` -- what
    the CLI adapter actually renders a row for."""


def list_bundle_objects(
    layout: config.WorkspaceLayout,
    *,
    resolved_type: str | None,
    limit: int,
    all_objects: bool,
) -> ListedObjects:
    """The ordinary `list` mode's read core: the SAME single call to
    `listing.list_objects(layout.bundle_dir)` the pre-extraction body
    made -- the ONLY disk-reading call this function performs. Filtering
    by `resolved_type` and slicing to `limit` both happen on its in-memory
    result (design D3: Exactly One Bundle Walk); `lifecycle.
    deprecated_concept_ids` is never called, because status is already
    derived inside `listing.list_objects`'s own single pass.

    `resolved_type` and `limit` are assumed ALREADY validated by
    `validate_list_arguments` -- this function performs no argument
    validation of its own, and a non-positive `limit` here is a caller
    bug (an empty/degenerate slice), not a value this function guards
    against."""
    rows = listing.list_objects(layout.bundle_dir)
    if resolved_type is not None:
        rows = [row for row in rows if row.link_dir == resolved_type]
    total = len(rows)
    shown = rows if all_objects else rows[:limit]
    return ListedObjects(rows=tuple(rows), total=total, shown=tuple(shown))


@dataclass(frozen=True)
class ProvenanceSources:
    """The `list --sources <id>` mode's raw result (#628), gathered by
    `list_provenance_sources` -- see that function's docstring for why it
    takes an already-resolved `canonical_id` rather than performing id
    resolution itself."""

    ancestors: tuple[str, ...]
    """Every `sources/` id whose provenance chain reaches the target,
    sorted (`bundle_provenance.provenance_source_ancestors`'s own
    contract) -- includes a dangling id with no file behind it."""

    rows: tuple[listing.BundleObject, ...]
    """Every bundle object, for the CLI adapter to build an `id ->
    BundleObject` lookup from -- empty when `ancestors` is empty, because
    the pre-extraction body never ran `listing.list_objects` in that
    branch either (it returned immediately after the "No Source reaches
    ..." line)."""


def list_provenance_sources(
    layout: config.WorkspaceLayout, canonical_id: str
) -> ProvenanceSources:
    """Everything `list --sources` needs AFTER the concept id is already
    resolved (see module docstring, item 5): walks the bundle's
    provenance chains upward via `bundle_provenance.
    provenance_source_ancestors` to find every `sources/` id reaching
    `canonical_id`, then -- ONLY when at least one was found, mirroring
    the pre-extraction body's `if not ancestors: ... return` exactly --
    reads every bundle object once more so the CLI adapter can render
    each ancestor's current sensitivity and title.

    Deliberately takes the already-resolved `canonical_id`, not the raw
    `object_id`: id resolution (`application_lifecycle.
    resolve_concept_path`) and its `except (OSError, ValueError)` stay in
    the CLI adapter, unchanged from before this extraction, because
    widening this function to also perform and guard that resolution
    would widen the `try` scope that guard originally covered -- exactly
    the mistake a previous extraction made and review caught (see module
    docstring)."""
    files: dict[str, str] = {}
    for path in okf.iter_bundle_markdown(layout.bundle_dir):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        rel = path.relative_to(layout.bundle_dir).as_posix()
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # An unreadable doc contributes no provenance edges; mirrors
            # `_parse_provenance_by_id`'s skip-not-crash contract.
            continue

    ancestors = tuple(
        bundle_provenance.provenance_source_ancestors(files, object_id=canonical_id)
    )
    rows: tuple[listing.BundleObject, ...] = ()
    if ancestors:
        rows = tuple(listing.list_objects(layout.bundle_dir))
    return ProvenanceSources(ancestors=ancestors, rows=rows)
