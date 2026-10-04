"""The unattended 'what changed' digest (#1268).

A daemon pass that wrote to the bundle ends by listing each automatic action,
so a person who was not watching can see what happened and take it back. An
action is one git commit the engine made on its own; the digest names its short
sha and the bundle documents it touched on the SAME line as the command that
undoes it. The ledger holds no state of its own: the commit is the source of
truth, and `git log` reproduces the list.

Pure text and values; the CLI owns terminal presentation (ADR-0042).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from openkos.model import okf

MAX_CONCEPTS_LISTED = 4
"""Concept ids named per action; the rest are counted. The sha is always named."""

_MESSAGE_LEAD = "openkos: "


@dataclass(frozen=True)
class AutomaticAction:
    """One commit the engine made unattended."""

    sha: str
    summary: str
    concept_ids: tuple[str, ...]

    @property
    def undo(self) -> str:
        return f"git revert {self.sha}"


def concept_ids(paths: Sequence[str]) -> tuple[str, ...]:
    """The Concept IDs among workspace-relative `paths`: a bundle document's
    path minus `bundle/` and `.md`; `raw/` files and the reserved `index.md` /
    `log.md` are not concepts."""
    prefix = "bundle/"
    ids: list[str] = []
    for path in paths:
        if not (path.startswith(prefix) and path.endswith(".md")):
            continue
        rel = path[len(prefix) : -len(".md")]
        if PurePosixPath(path).name in okf.RESERVED_FILENAMES or rel in ids:
            continue
        ids.append(rel)
    return tuple(sorted(ids))


def record_action(sha: str, paths: Sequence[str], message: str) -> AutomaticAction:
    summary = message.removeprefix(_MESSAGE_LEAD)
    return AutomaticAction(sha, summary, concept_ids(paths))


def action_line(action: AutomaticAction) -> str:
    """`<sha> <what> [<concept ids>] -- undo: <command>`: one line, so a reader
    (or a parser) never has to join the sha to its undo."""
    shown = action.concept_ids[:MAX_CONCEPTS_LISTED]
    hidden = len(action.concept_ids) - len(shown)
    parts = [action.sha, action.summary]
    if shown:
        more = f", +{hidden} more" if hidden else ""
        parts.append(f"[{', '.join(shown)}{more}]")
    parts.append(f"-- undo: {action.undo}")
    return " ".join(parts)


def render(
    actions: Sequence[AutomaticAction],
) -> tuple[str, tuple[str, ...], str] | None:
    """`(summary, item lines newest first, ordering note)`, or `None` when the
    pass made no automatic commit (nothing to say is nothing printed)."""
    if not actions:
        return None
    count = len(actions)
    noun = "commit" if count == 1 else "commits"
    ordering = ", newest first" if count > 1 else ""
    header = f"what changed -- {count} automatic {noun}{ordering}"
    items = tuple(action_line(a) for a in reversed(actions))
    note = (
        "undo newest first: every commit appends to log.md, so a revert is "
        "safe only while that commit is the latest one."
    )
    return header, items, note


@dataclass
class ActionLedger:
    """Collects the commits one job made. Wraps an `autocommit` port so the
    sha it returns is recorded where the commit is made, not rebuilt later."""

    actions: list[AutomaticAction] = field(default_factory=list)

    def wrap(
        self, autocommit: Callable[[Path, Sequence[str], str], object]
    ) -> Callable[[Path, Sequence[str], str], object]:
        def recording(root: Path, paths: Sequence[str], message: str) -> object:
            sha = autocommit(root, paths, message)
            if isinstance(sha, str) and sha:
                self.actions.append(record_action(sha, paths, message))
            return sha

        return recording


__all__ = [
    "MAX_CONCEPTS_LISTED",
    "ActionLedger",
    "AutomaticAction",
    "action_line",
    "concept_ids",
    "record_action",
    "render",
]
