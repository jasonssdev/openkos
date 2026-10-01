"""Read dependencies of a verb's commit phase (ADR-0036, issue #1137).

A split verb computes its plan with no lock and takes the workspace lock only
for its commit phase. `drift.describe_drift` guards the files the verb will
WRITE or UNLINK, but a plan also rests on documents it only READ: the inputs
of a sensitivity high-water mark, the whole-bundle scan that decided which
documents reference a concept, the provenance closure of a Source. Under the
old whole-verb lock another writer could not change those between the scan and
the write; without it they can, and a stale read silently under-classifies a
concept or leaves a reference dangling. This module is the second guarded
mapping the commit phase re-validates beside the drift targets.

`ReadDependencies` is what a verb DECLARES it read:

* `present` -- documents whose bytes the plan used, with the bytes it saw;
* `absent` -- paths the plan assumed did not exist (a restore target);
* `documents` -- every bundle document the plan's scan could see, so a
  document that appeared afterwards (additive drift has no baseline) is
  refused too: a new derived concept would otherwise escape a sensitivity
  raise or a reference scan.

`describe_read_drift` is the pure decision, mirroring `describe_drift`: it
returns the refusal message (exit 3 for the CLI, nothing written) or `None`.
Nothing here prints, prompts, locks or imports `typer`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path

from openkos import config
from openkos.model import okf


@dataclass(frozen=True)
class ReadDependencies:
    """The documents a plan read but will not write, as the plan saw them."""

    present: Mapping[Path, bytes] = field(default_factory=dict)
    absent: AbstractSet[Path] = frozenset()
    documents: AbstractSet[Path] | None = None
    """Every non-reserved bundle document the plan's scan could see; `None`
    disables new-document detection for a verb whose plan did not scan."""

    def merged_with(self, other: ReadDependencies) -> ReadDependencies:
        """The union of two declarations (one verb, several scans)."""
        if self.documents is None:
            documents = other.documents
        elif other.documents is None:
            documents = self.documents
        else:
            documents = self.documents | other.documents
        return ReadDependencies(
            present={**self.present, **other.present},
            absent=frozenset(self.absent) | frozenset(other.absent),
            documents=documents,
        )


def _bundle_documents(bundle_dir: Path) -> set[Path]:
    return {
        path
        for path in okf.iter_bundle_markdown(bundle_dir)
        if path.name not in okf.RESERVED_FILENAMES
    }


def capture_bundle_documents(
    bundle_dir: Path, *, exclude: AbstractSet[Path] = frozenset()
) -> ReadDependencies:
    """Declare EVERY non-reserved bundle document a dependency, reading each
    once. Call it BEFORE the scan whose result feeds the plan: a document
    that changes between this read and the scan then compares unequal at
    commit time, which fails closed. `exclude` names paths the verb guards as
    write targets already; they stay in `documents` (they are known, so never
    reported as new) but are not duplicated into `present`."""
    documents = _bundle_documents(bundle_dir)
    present: dict[Path, bytes] = {}
    for path in sorted(documents - set(exclude)):
        try:
            present[path] = path.read_bytes()
        except OSError:
            # Unreadable now means unreadable to the scan too; recording no
            # baseline would hide it, so the path is left out of `present`
            # and kept in `documents`, and a later successful read is not
            # "drift" -- the plan simply never depended on its content.
            continue
    return ReadDependencies(present=present, documents=frozenset(documents))


def describe_read_drift(
    layout: config.WorkspaceLayout, dependencies: ReadDependencies, verb: str
) -> str | None:
    """Describe the refusal a run owes (exit 3, nothing written) when any
    document its plan only READ changed, vanished or newly appeared since the
    plan was computed. `None` when nothing moved."""
    changed: list[str] = []
    vanished: list[str] = []
    for path, expected in dependencies.present.items():
        rel = _relative(layout, path)
        try:
            current = path.read_bytes()
        except OSError:
            vanished.append(rel)
            continue
        if current != expected:
            changed.append(rel)

    new_documents: list[str] = []
    if dependencies.documents is not None:
        new_documents = [
            _relative(layout, path)
            for path in sorted(
                _bundle_documents(layout.bundle_dir) - set(dependencies.documents)
            )
        ]

    appeared = [
        _relative(layout, path) for path in sorted(dependencies.absent) if _exists(path)
    ]

    clauses: list[str] = []
    if changed:
        clauses.append(
            f"{len(changed)} read dependency(ies) changed on disk after this "
            f"run computed its plan: {', '.join(sorted(changed))}"
        )
    if vanished:
        clauses.append(
            f"{len(vanished)} read dependency(ies) vanished from disk (deleted "
            f"or unreadable): {', '.join(sorted(vanished))}"
        )
    if new_documents:
        clauses.append(
            f"{len(new_documents)} new document(s) appeared: {', '.join(new_documents)}"
        )
    if appeared:
        clauses.append(
            f"{len(appeared)} path(s) that must not exist appeared: "
            f"{', '.join(appeared)}"
        )
    if not clauses:
        return None
    return (
        f"openkos {verb}: refusing to write -- {'; '.join(clauses)}. "
        "Nothing was written. Re-run to recompute over the current bundle."
    )


def _relative(layout: config.WorkspaceLayout, path: Path) -> str:
    try:
        return path.relative_to(layout.bundle_dir).as_posix()
    except ValueError:
        try:
            return path.relative_to(layout.root).as_posix()
        except ValueError:
            return str(path)


def _exists(path: Path) -> bool:
    """`Path.exists()` re-raises `EACCES`; an unprobeable path is treated as
    present, which fails closed (the refusal is retry-safe)."""
    try:
        return path.exists()
    except OSError:
        return True


@contextlib.contextmanager
def unlocked_section() -> Iterator[None]:
    """The default `CommitSection` of a service port: holds nothing.

    A service called with no section (a unit test, a caller that already
    holds the workspace lock around the whole call) commits unlocked, which is
    exactly the behaviour every split verb had before the commit phase
    existed. The CLI always passes a real section."""
    yield


def no_after_commit() -> None:
    """The default `after_commit` port: no derived refresh to run."""
