"""Setting one page's `sensitivity` from a stateless client (#1517).

`openkos set-sensitivity` is the terminal's way to set the field; this is the
same write for a caller with no terminal, in two phases. A preview plans from
the bytes on disk and returns a content digest; the apply takes the workspace
lock once, plans again from the bytes it will write over, and writes only when
the digest is the one the caller previewed. One write, one commit.

The plan is the verb's: the level must be one of the three, an exact match of
the stored value is no change, and a raise of a `Source` raises (never lowers)
every provenance descendant through `bundle.provenance.resolve_source_raises`
(ADR-0009). A lowering never cascades. The log line and the commit message are
the verb's own (`log_line`, `commit_message`), so the history reads the same
whichever surface made the change. The CLI keeps its confirmation, its
downgrade gate and its notes: here the preview is the review a lowering rides
on (ADR-0008), because the caller shows it to a person before applying.

No queue row is touched, no derived store is refreshed and no model is
reached: a frontmatter-only change leaves the vectors current, and `status`
reports the lexical indexes stale until a rebuild.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from openkos import config, fsio
from openkos.application import concept_read, curate_decisions
from openkos.application import lifecycle as application_lifecycle
from openkos.application.write_gate import WriteFailed
from openkos.bundle import log as bundle_log
from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf

Level = Literal["public", "private", "confidential"]
Direction = Literal["raise", "same", "lower"]
SensitivityReason = Literal["not_found", "unchanged"]

_VERB: Literal["set_sensitivity"] = "set_sensitivity"


def log_line(
    concept_id: str,
    level: str,
    previous: object,
    raised: Sequence[tuple[str, str]] = (),
) -> str:
    """The `log.md` entry a sensitivity change appends, on every surface.

    `raised` is the derived pages raised with it, each as its id and the level
    it had before. It is the only record of that set (#1706): an undo reads it
    back. A change that raised nothing writes the line it always did."""
    line = (
        f"**Set-sensitivity**: Set [{concept_id}](/{concept_id}.md) "
        f"sensitivity to {level!r} (was {previous!r})."
    )
    if raised:
        pages = ", ".join(f"[{page}](/{page}.md) (was {was!r})" for page, was in raised)
        line += f" Also raised ({len(raised)}): {pages}."
    return line


_ENTRY_RE = re.compile(
    r"^\* \*\*(?P<kind>Set|Undo)-sensitivity\*\*: (?:Set|Put) "
    r"\[(?P<id>[^\]]+)\]\(/(?P=id)\.md\) sensitivity (?:back )?to "
    r"'(?P<level>[^']*)' \(was (?P<was>'[^']*'|None|[^)]*)\)\.(?P<rest>.*)$"
)
_RAISED_RE = re.compile(r"\[(?P<id>[^\]]+)\]\(/(?P=id)\.md\) \(was '(?P<was>[^']*)'\)")
_ALSO_RAISED = re.compile(r" Also raised \((?P<count>\d+)\): ")


def undo_log_line(concept_id: str, level: str, previous: str) -> str:
    """The `log.md` entry an undo of a sensitivity change appends."""
    return (
        f"**Undo-sensitivity**: Put [{concept_id}](/{concept_id}.md) "
        f"sensitivity back to {level!r} (was {previous!r})."
    )


@dataclass(frozen=True)
class RecordedChange:
    """The newest `log.md` entry about one page, when it is a sensitivity change
    that recorded what it raised: the level it set (`level`), the stored value
    it replaced (`previous`, `None` when there was none) and the derived pages
    raised with it, each with the level it had before (`raised`)."""

    concept_id: str
    level: str
    previous: str | None
    raised: tuple[tuple[str, str], ...]


def recorded_change(log_text: str, concept_id: str) -> RecordedChange | None:
    """The newest entry in `log_text` about `concept_id`, as a recorded change.

    `None` when the page has no entry, when the newest is an undo, or when it is
    a change that did not record what it raised (a change from before the record
    existed, one that raised nothing, or a line rewritten or edited, which a
    count of the raised pages in the line itself detects). Entries are
    newest first, so the first one found is the newest."""
    for line in log_text.splitlines():
        match = _ENTRY_RE.match(line)
        if match is None or match["id"] != concept_id:
            continue
        rest = match["rest"]
        also = _ALSO_RAISED.match(rest)
        if match["kind"] != "Set" or also is None:
            return None
        body = rest[also.end() :]
        if not body.endswith("."):
            return None
        items = body[:-1].split(", [")
        raised: list[tuple[str, str]] = []
        for index, item in enumerate(items):
            item_match = _RAISED_RE.fullmatch(item if index == 0 else f"[{item}")
            if item_match is None or item_match["was"] not in okf.SENSITIVITY_ORDER:
                return None
            raised.append((item_match["id"], item_match["was"]))
        if len(raised) != int(also["count"]):
            return None  # an item was removed from the record: do not trust the rest
        was = match["was"]
        previous = was[1:-1] if was.startswith("'") else None
        if was != "None" and previous is None:
            return None
        if match["level"] not in okf.SENSITIVITY_ORDER:
            return None
        return RecordedChange(concept_id, match["level"], previous, tuple(raised))
    return None


def commit_message(concept_id: str, level: str) -> str:
    """The commit message of a sensitivity change, on every surface."""
    return f"openkos: set-sensitivity {concept_id} -> {level}"


@dataclass(frozen=True)
class SensitivityRefusal:
    """Why the change cannot be planned as asked; nothing was written.

    `not_found`: not a valid Concept ID, or no such page. `unchanged`: the
    page's stored value already is the level asked for."""

    reason: SensitivityReason


@dataclass(frozen=True)
class SensitivityStale:
    """The bytes the plan rests on changed since the preview, or the echoed
    version was altered; nothing was written."""


@dataclass(frozen=True)
class RaisedPage:
    """A page raised with a Source: its level `before` (fail-closed) and
    `after`."""

    concept_id: str
    title: str
    before: Level
    after: Level


@dataclass(frozen=True)
class SensitivityPlan:
    concept_id: str
    title: str
    current: Level
    """The page's level now, ranked fail-closed (missing is `private`)."""
    previous: str | None
    """The stored value as written, `None` when the field is absent or not a
    string."""
    level: Level
    direction: Direction
    also_raised: tuple[RaisedPage, ...]
    content_digest: str


@dataclass(frozen=True)
class _Planned:
    plan: SensitivityPlan
    concept_path: Path
    new_text: str
    raises: tuple[okf.DescendantRaise, ...]
    raw_current: object


def _validated(level: str) -> Level:
    if level not in okf.SENSITIVITY_ORDER:
        raise ValueError(
            f"{level!r} is not a sensitivity level "
            f"(expected one of {sorted(okf.SENSITIVITY_ORDER)})"
        )
    return cast(Level, level)


def _title(metadata: dict[str, object], concept_id: str) -> str:
    title = metadata.get("title")
    return title if isinstance(title, str) and title.strip() else concept_id


def _plan(root: Path, concept_id: str, level: Level) -> _Planned | SensitivityRefusal:
    """The change from the bytes on disk now, as `set-sensitivity` plans it."""
    layout = config.WorkspaceLayout(root)
    try:
        concept_path, canonical = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, concept_id
        )
    except ValueError:
        return SensitivityRefusal("not_found")
    try:
        concept_bytes, text = fsio.snapshot_read(concept_path)
    except OSError as exc:
        raise WriteFailed(_VERB, (canonical,), str(exc)) from exc
    metadata, body = okf.load_frontmatter(text)
    current = metadata.get("sensitivity")
    if current == level:
        return SensitivityRefusal("unchanged")
    direction = okf.sensitivity_direction(current, level)
    lines = [f"page\t{canonical}\t{level}", _hash("target", concept_bytes)]
    raises: list[okf.DescendantRaise] = []
    titles: dict[str, str] = {}
    if metadata.get("type") == "Source" and direction == "raise":
        # The closure is resolved from a whole-bundle snapshot, so every page
        # in it is a read dependency: a page that starts citing the Source
        # after the preview would escape the raise. The digest covers them all.
        files: dict[str, str] = {}
        try:
            for path in okf.iter_bundle_markdown(layout.bundle_dir):
                if path.name in okf.RESERVED_FILENAMES or path == concept_path:
                    continue
                rel = path.relative_to(layout.bundle_dir).as_posix()
                data, files[rel] = fsio.snapshot_read(path)
                lines.append(_hash(rel, data))
        except OSError as exc:
            raise WriteFailed(_VERB, (canonical,), str(exc)) from exc
        raises = bundle_provenance.resolve_source_raises(
            files, source_id=canonical, level=level
        )
        for raised in raises:
            raised_meta = okf.load_frontmatter(files[f"{raised.concept_id}.md"])[0]
            titles[raised.concept_id] = _title(raised_meta, raised.concept_id)
    metadata["sensitivity"] = level
    new_text = okf.dump_frontmatter(metadata, body)
    plan = SensitivityPlan(
        concept_id=canonical,
        title=_title(metadata, canonical),
        current=concept_read.sensitivity_level(current),
        previous=current if isinstance(current, str) else None,
        level=level,
        direction=direction,
        also_raised=tuple(
            RaisedPage(
                concept_id=raised.concept_id,
                title=titles[raised.concept_id],
                before=concept_read.sensitivity_level(raised.current),
                after=cast(Level, raised.new_level),
            )
            for raised in raises
        ),
        content_digest=hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
    )
    return _Planned(plan, concept_path, new_text, tuple(raises), current)


def _hash(name: str, data: bytes) -> str:
    return f"{name}\t{hashlib.sha256(data).hexdigest()}"


def preview_sensitivity(
    root: Path, concept_id: str, level: str
) -> SensitivityPlan | SensitivityRefusal:
    """Plan setting `concept_id`'s sensitivity to `level`, exactly as
    `set-sensitivity` would. A level outside the three raises `ValueError`.
    Writes nothing and takes no lock."""
    planned = _plan(root, concept_id, _validated(level))
    return planned if isinstance(planned, SensitivityRefusal) else planned.plan


@dataclass(frozen=True)
class SensitivityChange:
    """The change was written. `commit_sha` is `None` when the commit could not
    be made (the write itself landed)."""

    concept_id: str
    level: Level
    previous: str | None
    also_raised: tuple[str, ...]
    commit_sha: str | None


def apply_sensitivity(
    root: Path,
    concept_id: str,
    level: str,
    content_digest: str,
    *,
    ports: curate_decisions.DecisionPorts,
) -> SensitivityChange | SensitivityRefusal | SensitivityStale:
    """Set the level previewed as `content_digest`, under one lock.

    Plans again inside the commit section, so the plan and the write see the
    same bytes; a refusal or a moved digest writes nothing. Writes the raised
    descendants first, then the page, then `log.md` -- the verb's order, so a
    failure part-way leaves the bundle over-classified, never under -- and
    commits only those paths."""
    asked = _validated(level)
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"
    with ports.commit_section():
        planned = _plan(root, concept_id, asked)
        if isinstance(planned, SensitivityRefusal):
            return planned
        plan = planned.plan
        if plan.content_digest != content_digest:
            return SensitivityStale()
        landed: list[str] = []
        try:
            log_text = log_path.read_text(encoding="utf-8")
            new_log = bundle_log.insert_log_entry(
                log_text,
                datetime.now(UTC).astimezone().date(),
                log_line(
                    plan.concept_id,
                    plan.level,
                    planned.raw_current,
                    [(r.concept_id, r.before) for r in plan.also_raised],
                ),
            )
            for raised in planned.raises:
                fsio.write_atomic(
                    layout.bundle_dir / f"{raised.concept_id}.md", raised.content
                )
                landed.append(raised.concept_id)
            fsio.write_atomic(planned.concept_path, planned.new_text)
            landed.append(plan.concept_id)
            fsio.write_atomic(log_path, new_log)
        except (OSError, ValueError) as exc:
            raise WriteFailed(
                _VERB, tuple(landed) or (plan.concept_id,), str(exc)
            ) from exc
        paths = [f"bundle/{raised.concept_id}.md" for raised in planned.raises]
        paths += [f"bundle/{plan.concept_id}.md", "bundle/log.md"]
        commit_sha = ports.autocommit(
            root, paths, commit_message(plan.concept_id, plan.level)
        )
    return SensitivityChange(
        concept_id=plan.concept_id,
        level=plan.level,
        previous=plan.previous,
        also_raised=tuple(raised.concept_id for raised in planned.raises),
        commit_sha=commit_sha,
    )
