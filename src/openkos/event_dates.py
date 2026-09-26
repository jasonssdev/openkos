"""Bounded event-date resolver (superseded-history-in-query, slice 1,
design.md Decision 1).

A package-root leaf, like `lifecycle.py`: it imports only the standard
library and `openkos.model.okf`, so both `retrieval/` (slice 2b,
`answer.py`, via `retrieval/history.py`) and `resolution/` (the
decision-revision detector, via the `DateState` alias in
`resolution/decision_revision.py`) can depend on it with no cycle and no
retrieval<->resolution coupling.

`resolve_event_date` walks AT MOST one hop past a concept's own
`provenance:` field: its own `sources/`-prefixed entries resolve directly,
and any other (intermediate) entry is read once, and only ITS OWN
`sources/` entries are then resolved -- a Source reachable only through a
SECOND intermediate is never reached. This mirrors the decision-revision
detector's "Decision Event Date Resolution" rule and is reused unchanged by
`retrieval/history.py` (slice 2b) for a predecessor's displayed date.

The resolver never raises: any read/parse failure, or an explicit `admit`
refusal, makes a Source's own date `"missing"` and stops an intermediate
from being traversed further -- neither ever propagates an exception up to
the caller.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, Literal

from openkos.model import okf

DateState = Literal["dated", "missing", "multiple", "none-reached"]
"""One member's resolved event-date state (design.md Decision 1):
`"dated"` -- exactly one distinct date was reached; `"multiple"` -- more
than one distinct date was reached, across possibly several Sources;
`"missing"` -- at least one reached Source's date could not be used
(absent, malformed, unreadable, or refused by `admit`); `"none-reached"`
-- no Source was reached at all (empty `provenance:`, or `provenance:`
naming only intermediates whose own `provenance:` holds no `sources/`
entry)."""


@dataclass(frozen=True)
class ResolvedEventDate:
    """The result of resolving a member's event date (design.md Decision 1).
    `earliest`/`latest` are set for `"dated"` (where they are equal) and
    for `"multiple"`; both are `None` for `"missing"`/`"none-reached"`."""

    state: DateState
    earliest: date | None
    latest: date | None


_SOURCES_PREFIX: Final = "sources/"
"""The `sources/`-prefix convention a `provenance:` entry uses to name a
Source (design.md Decision 1 step 2), matching `bundle/provenance.py`'s own
literal -- deliberately NOT `openkos.model.types.TYPE_TO_LINK_DIR["Source"]`:
that dict is built only from `llm_classifiable`/builder-only registry
entries, and `Source` is neither, so the key does not exist there."""

_AdmitFn = Callable[[str, Mapping[str, object]], bool]


def _provenance_entries(metadata: Mapping[str, object]) -> list[str]:
    """Read `metadata["provenance"]` (design.md Decision 1 step 1): a
    non-list value is empty, non-string entries are skipped, and the
    result is de-duplicated -- its order does not matter, since the caller
    only ever asks whether a `sources/`-prefixed entry is present."""
    raw = metadata.get("provenance")
    if not isinstance(raw, list):
        return []
    return list({entry for entry in raw if isinstance(entry, str)})


def _guarded_read(
    bundle_dir: Path,
    concept_id: str,
    admit: _AdmitFn | None,
) -> Mapping[str, object] | None:
    """Read and parse `concept_id`'s frontmatter, returning `None` on any
    failure (an unreadable or unparsable file) or an explicit `admit`
    refusal -- the one guard shared by both a Source's own resolution and
    an intermediate's one-hop traversal (design.md Decision 1 step 4)."""
    try:
        text = okf.concept_path_for(concept_id, bundle_dir).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        metadata, _ = okf.load_frontmatter(text)
    except Exception:  # broad: any parse failure makes this read unusable
        return None
    if admit is not None and admit(concept_id, metadata) is False:
        return None
    return metadata


def _resolve_source_date(
    bundle_dir: Path,
    source_id: str,
    admit: _AdmitFn | None,
) -> date | None:
    """Resolve one Source's own `event_date`, or `None` on any guarded-read
    failure or a malformed/absent stored date (design.md Decision 1 step
    2/4) -- both cases make the Source's own date unusable, so
    `okf.read_event_date`'s `value` (already `None` in both the absent and
    the malformed case) is exactly the signal this resolver needs."""
    metadata = _guarded_read(bundle_dir, source_id, admit)
    if metadata is None:
        return None
    return okf.read_event_date(metadata).value


def resolve_event_date(
    bundle_dir: Path,
    metadata: Mapping[str, object],
    *,
    admit: _AdmitFn | None = None,
) -> ResolvedEventDate:
    """Resolve `metadata`'s event date per design.md Decision 1: at most
    one hop past its own `provenance:`. Never raises.

    `metadata` is the member's OWN already-read frontmatter -- reading its
    `provenance:` here costs no extra read. `admit`, when given, is called
    as `admit(concept_id, metadata)` for EVERY guarded read this resolver
    makes -- both a Source's own read and an intermediate's own read -- and
    a `False` return is indistinguishable from an unreadable file: the
    Source's date becomes `"missing"`, and a refused intermediate
    contributes nothing without its own `provenance:` ever being read.
    `None` (the default) admits everything, matching the decision-revision
    detector's local-only use, which needs no sensitivity gate at all."""
    reached_any_source = False
    any_source_unusable = False
    dates: set[date] = set()

    def _resolve_and_record(source_id: str) -> None:
        nonlocal reached_any_source, any_source_unusable
        reached_any_source = True
        value = _resolve_source_date(bundle_dir, source_id, admit)
        if value is None:
            any_source_unusable = True
        else:
            dates.add(value)

    for entry in _provenance_entries(metadata):
        if entry.startswith(_SOURCES_PREFIX):
            _resolve_and_record(entry)
            continue

        # An intermediate: one guarded read, then only ITS OWN `sources/`
        # entries are resolved (step 3) -- a refused/unreadable
        # intermediate contributes nothing and is never traversed further.
        intermediate_metadata = _guarded_read(bundle_dir, entry, admit)
        if intermediate_metadata is None:
            continue
        for nested in _provenance_entries(intermediate_metadata):
            if nested.startswith(_SOURCES_PREFIX):
                _resolve_and_record(nested)
            # A non-Source entry at the second hop is ignored -- the walk
            # stops here regardless (design.md Decision 1 step 3).

    if not reached_any_source:
        return ResolvedEventDate(state="none-reached", earliest=None, latest=None)
    if any_source_unusable:
        return ResolvedEventDate(state="missing", earliest=None, latest=None)
    if len(dates) > 1:
        return ResolvedEventDate(
            state="multiple", earliest=min(dates), latest=max(dates)
        )
    (only,) = dates
    return ResolvedEventDate(state="dated", earliest=only, latest=only)
