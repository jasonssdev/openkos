"""Undoing a whole sensitivity change (#1706): the page the person changed and
the derived pages raised with it go back together, in one commit.

What a change raised is read back from the `log.md` line it wrote
(`sensitivity_service.recorded_change`); a change that recorded nothing is not
offered an undo. The undo is two phases, as the change was: a preview plans from
the bytes on disk and returns a digest of every byte the plan rests on, and the
apply takes the workspace lock once, plans again and writes only when the digest
still matches.

The high-water mark holds (ADR-0003, ADR-0016). A page is put back by writing its
recorded level, then running the cited high-water rule over the result: a derived
page never ends below the level any page it cites requires, so a second source
that came to cite it since keeps it where it is. A page whose citations do not
all resolve is left as it is, and a page is never raised above where it stands.
The change is refused when the page or any raised page is no longer at the level
the change left it: something else was done to it since, and an undo would
overwrite that.

Nothing is refreshed and no model is reached: the change was frontmatter only.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from openkos import config, fsio
from openkos.application import concept_read, curate_decisions
from openkos.application import lifecycle as application_lifecycle
from openkos.application import sensitivity_service as sensitivity
from openkos.application.write_gate import WriteFailed
from openkos.bundle import log as bundle_log
from openkos.bundle import provenance as bundle_provenance
from openkos.model import okf

Level = Literal["public", "private", "confidential"]
UndoReason = Literal["not_found", "no_record", "changed_since"]

_VERB: Literal["set_sensitivity"] = "set_sensitivity"
"""An undo is a sensitivity write: it is reported as one."""


def commit_message(concept_id: str, level: str) -> str:
    """The commit message of an undo, on every surface."""
    return f"openkos: undo set-sensitivity {concept_id} -> {level}"


@dataclass(frozen=True)
class UndoRefusal:
    """Why the change cannot be undone; nothing was written. `not_found`: not a
    valid Concept ID, or no such page. `no_record`: the newest entry about the
    page is not a change that recorded what it raised. `changed_since`: the page
    or a page raised with it is no longer at the level the change left it."""

    reason: UndoReason


@dataclass(frozen=True)
class UndoStale:
    """The bytes the plan rests on changed since the preview, or the echoed
    version was altered; nothing was written."""


@dataclass(frozen=True)
class RestoredPage:
    """A derived page raised with the change: its level `now` and the level it
    ends at (`after`). `after` equals `now` when another source keeps it there."""

    concept_id: str
    title: str
    now: Level
    after: Level


@dataclass(frozen=True)
class UndoPlan:
    concept_id: str
    title: str
    now: Level
    restores: Level
    pages: tuple[RestoredPage, ...]
    content_digest: str


@dataclass(frozen=True)
class _Planned:
    plan: UndoPlan
    writes: tuple[tuple[str, str], ...]
    """Bundle-relative id and full text, the page first, then the restored pages."""
    log_line: str


def _hash(name: str, data: bytes) -> str:
    return f"{name}\t{hashlib.sha256(data).hexdigest()}"


def _title(metadata: dict[str, object], concept_id: str) -> str:
    title = metadata.get("title")
    return title if isinstance(title, str) and title.strip() else concept_id


def _plan(root: Path, concept_id: str) -> _Planned | UndoRefusal:
    layout = config.WorkspaceLayout(root)
    try:
        concept_path, canonical = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, concept_id
        )
    except ValueError:
        return UndoRefusal("not_found")
    log_path = layout.bundle_dir / "log.md"
    files: dict[str, str] = {}
    lines: list[str] = []
    try:
        log_bytes, log_text = fsio.snapshot_read(log_path)
        lines.append(_hash("log", log_bytes))
        for path in okf.iter_bundle_markdown(layout.bundle_dir):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            data, files[rel] = fsio.snapshot_read(path)
            lines.append(_hash(rel, data))
    except OSError as exc:
        raise WriteFailed(_VERB, (canonical,), str(exc)) from exc
    if concept_path.relative_to(layout.bundle_dir).as_posix() not in files:
        return UndoRefusal("not_found")
    recorded = sensitivity.recorded_change(log_text, canonical)
    if recorded is None:
        return UndoRefusal("no_record")
    restores = cast(Level, recorded.previous or "private")
    if recorded.previous is not None and recorded.previous not in okf.SENSITIVITY_ORDER:
        return UndoRefusal("no_record")

    metadata, body = okf.load_frontmatter(files[f"{canonical}.md"])
    if metadata.get("sensitivity") != recorded.level:
        return UndoRefusal("changed_since")
    raised: list[tuple[str, Level, dict[str, object], str]] = []
    for page_id, was in recorded.raised:
        text = files.get(f"{page_id}.md")
        if text is None:
            return UndoRefusal("changed_since")
        page_meta, page_body = okf.load_frontmatter(text)
        expected = okf.combine_sensitivity(was, recorded.level)
        if page_meta.get("sensitivity") != expected:
            return UndoRefusal("changed_since")
        raised.append((page_id, cast(Level, was), page_meta, page_body))

    # The pages as they would stand with the change taken back, then the cited
    # high-water rule over them: no derived page ends below what a page it cites
    # requires. This is the only thing that stops an undo from lowering a page
    # that something else came to require since.
    simulated = dict(files)
    for edited_id, edited_level, edited_meta, edited_body in [
        (canonical, restores, metadata, body),
        *((p, w, m, b) for p, w, m, b in raised),
    ]:
        updated = dict(edited_meta)
        updated["sensitivity"] = edited_level
        simulated[f"{edited_id}.md"] = okf.dump_frontmatter(updated, edited_body)
    required = {
        r.concept_id: r.new_level
        for r in bundle_provenance.resolve_cited_high_water_raises(simulated)
    }
    known = {rel.removesuffix(".md") for rel in files}

    pages: list[RestoredPage] = []
    writes: list[tuple[str, str]] = []
    metadata["sensitivity"] = restores
    writes.append((canonical, okf.dump_frontmatter(metadata, body)))
    for page_id, was, page_meta, page_body in raised:
        now = concept_read.sensitivity_level(page_meta.get("sensitivity"))
        parents = bundle_provenance.parse_provenance_entry(files[f"{page_id}.md"])
        final: str = okf.combine_sensitivity(was, required.get(page_id, was))
        unresolved = parents is None or any(
            bundle_provenance.normalize_provenance_id(parent) not in known
            for parent in parents
        )
        if unresolved or okf.combine_sensitivity(final, now) != now:
            final = now  # never lower on a citation we cannot read, never raise
        after = cast(Level, final)
        pages.append(RestoredPage(page_id, _title(page_meta, page_id), now, after))
        if after != now:
            page_meta["sensitivity"] = after
            writes.append((page_id, okf.dump_frontmatter(page_meta, page_body)))
    plan = UndoPlan(
        concept_id=canonical,
        title=_title(metadata, canonical),
        now=cast(Level, recorded.level),
        restores=restores,
        pages=tuple(pages),
        content_digest=hashlib.sha256(
            "\n".join([f"undo\t{canonical}", *lines]).encode("utf-8")
        ).hexdigest(),
    )
    return _Planned(
        plan,
        tuple(writes),
        sensitivity.undo_log_line(canonical, restores, recorded.level),
    )


def preview_undo(root: Path, concept_id: str) -> UndoPlan | UndoRefusal:
    """Plan taking back the newest sensitivity change to `concept_id`, with the
    raised pages it carries. Writes nothing and takes no lock."""
    planned = _plan(root, concept_id)
    return planned if isinstance(planned, UndoRefusal) else planned.plan


@dataclass(frozen=True)
class SensitivityUndone:
    """The change was taken back. `restored` are the raised pages that went back,
    `kept` those another source holds where they were. `commit_sha` is `None`
    when the commit could not be made (the writes themselves landed)."""

    concept_id: str
    level: Level
    restored: tuple[str, ...]
    kept: tuple[str, ...]
    commit_sha: str | None


def apply_undo(
    root: Path,
    concept_id: str,
    content_digest: str,
    *,
    ports: curate_decisions.DecisionPorts,
) -> SensitivityUndone | UndoRefusal | UndoStale:
    """Take back the change previewed as `content_digest`, under one lock.

    Plans again inside the commit section, so the plan and the write see the
    same bytes; a refusal or a moved digest writes nothing. The page is written
    first, then the pages restored with it, then `log.md`, so a failure part-way
    leaves the bundle over-classified, never under; one commit holds them all."""
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"
    with ports.commit_section():
        planned = _plan(root, concept_id)
        if isinstance(planned, UndoRefusal):
            return planned
        plan = planned.plan
        if plan.content_digest != content_digest:
            return UndoStale()
        landed: list[str] = []
        try:
            new_log = bundle_log.insert_log_entry(
                log_path.read_text(encoding="utf-8"),
                datetime.now(UTC).astimezone().date(),
                planned.log_line,
            )
            for page_id, text in planned.writes:
                fsio.write_atomic(layout.bundle_dir / f"{page_id}.md", text)
                landed.append(page_id)
            fsio.write_atomic(log_path, new_log)
        except (OSError, ValueError) as exc:
            raise WriteFailed(
                _VERB, tuple(landed) or (plan.concept_id,), str(exc)
            ) from exc
        paths = [f"bundle/{page_id}.md" for page_id, _ in planned.writes]
        paths.append("bundle/log.md")
        commit_sha = ports.autocommit(
            root, paths, commit_message(plan.concept_id, plan.restores)
        )
    return SensitivityUndone(
        concept_id=plan.concept_id,
        level=plan.restores,
        restored=tuple(p.concept_id for p in plan.pages if p.after != p.now),
        kept=tuple(p.concept_id for p in plan.pages if p.after == p.now),
        commit_sha=commit_sha,
    )
