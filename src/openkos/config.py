"""Workspace root: `openkos.yaml`, `AGENTS.md`, and the four refusal conditions.

A workspace is `openkos.yaml`, `AGENTS.md`, `raw/`, and `bundle/` at some
root directory (docs/architecture.md:141-154). `bundle/` is not a workspace
on its own -- `raw/` sits outside it by design, so the workspace root is
where the engine's own files live, not the OKF bundle root.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import NamedTuple

from openkos import fsio


@dataclass(frozen=True)
class WorkspaceLayout:
    """The four paths init reads and writes at a workspace root."""

    root: Path

    @property
    def config_path(self) -> Path:
        """`openkos.yaml`: the workspace marker (Q7.6) and layout declaration."""
        return self.root / "openkos.yaml"

    @property
    def agents_path(self) -> Path:
        """`AGENTS.md`: the engine's operating manual for this workspace."""
        return self.root / "AGENTS.md"

    @property
    def raw_dir(self) -> Path:
        """`raw/`: immutable sources, outside the OKF bundle."""
        return self.root / "raw"

    @property
    def bundle_dir(self) -> Path:
        """`bundle/`: the OKF bundle root."""
        return self.root / "bundle"


class RefusalCondition(NamedTuple):
    """One reason `init` might refuse to write, with its workspace classification."""

    marks_workspace: bool
    reason: str


def _refusal_conditions(root: Path) -> Iterator[RefusalCondition]:
    """The ONE place that defines every reason `init` might refuse to write at `root`.

    Yields `RefusalCondition(marks_workspace, reason)` in priority order --
    the first item is the reason `refusal_reason` reports. `marks_workspace`
    is `True` for the four conditions the spec names as "already a
    workspace" (existing `openkos.yaml`, existing `AGENTS.md`, non-empty
    `raw/`, non-empty `bundle/`) and `False` for the two that answer a
    different question: `raw` or `bundle` already exists as a plain file, or
    as a symlink. Neither is a workspace, yet init still cannot write there.
    For the plain-file case, `Path.mkdir` would raise `FileExistsError` --
    an `OSError` Phase B (`cli/main.py`) DOES catch, so nothing goes
    uncaught, but without this pre-flight condition the failure would only
    surface as the generic "failed while creating the workspace" message,
    and only after any earlier Phase-B writes had already landed. For the
    symlink case, `Path.mkdir`/`open("x")` would follow the link, letting
    init write through it into whatever directory or file the symlink
    targets -- potentially outside the workspace root entirely -- instead
    of refusing outright. `is_workspace` and `refusal_reason` both read this
    generator, so extending what counts as either question changes both at
    once instead of the two silently drifting apart.
    """
    layout = WorkspaceLayout(root)
    if layout.config_path.exists():
        yield RefusalCondition(
            True, f"'{layout.config_path.name}' already exists in this directory"
        )
    if layout.agents_path.exists():
        yield RefusalCondition(
            True, f"'{layout.agents_path.name}' already exists in this directory"
        )
    for path in (layout.raw_dir, layout.bundle_dir):
        if path.is_symlink():
            yield RefusalCondition(False, f"'{path.name}' is a symlink")
        elif path.exists() and not path.is_dir():
            yield RefusalCondition(
                False, f"'{path.name}' exists and is not a directory"
            )
        elif path == layout.bundle_dir and _non_empty_dir(path):
            yield RefusalCondition(
                True,
                f"'{path.name}/' already exists and is not empty; a previous init "
                "may have crashed mid-write -- inspect and remove it before retrying",
            )
        elif _non_empty_dir(path):
            yield RefusalCondition(
                True, f"'{path.name}/' already exists and is not empty"
            )


def is_workspace(root: Path) -> bool:
    """True if `root` already looks like an initialized (or partially seeded) workspace.

    Checks the four refusal conditions the spec names: an existing
    `openkos.yaml`, an existing `AGENTS.md`, or a non-empty `raw/` or
    `bundle/`. A directory holding unrelated files but none of these is NOT
    a workspace -- init may adopt it.
    """
    return any(marks_workspace for marks_workspace, _ in _refusal_conditions(root))


def refusal_reason(root: Path) -> str | None:
    """Return why `init` must refuse to write at `root`, or `None` if it may proceed."""
    return next((reason for _, reason in _refusal_conditions(root)), None)


def _non_empty_dir(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _read_template(filename: str) -> str:
    """Read a packaged template from `src/openkos/templates/` (D4).

    Uses `importlib.resources`, never `__file__` or a relative path, so this
    works identically from an editable install and from an installed wheel.
    """
    return (resources.files("openkos") / "templates" / filename).read_text(
        encoding="utf-8"
    )


def write_agents(root: Path) -> None:
    """Write a byte-identical copy of the packaged `AGENTS.md` template at `root`.

    Exclusive-create mode ("x"): a colliding file raises `FileExistsError`
    instead of being overwritten (D2), matching `bundle.create`'s guarantee.
    """
    content = _read_template("agents.md.template")
    layout = WorkspaceLayout(root)
    fsio.write_exclusive(layout.agents_path, content)


def write_config(root: Path) -> None:
    """Write a byte-identical copy of the packaged `openkos.yaml` template at `root`.

    Same shape as `write_agents` (D5): no substitution, no dumper, no
    per-workspace field. The directory itself is the single source of
    truth for the workspace's identity, so nothing in `openkos.yaml` is
    derived from `root`. Exclusive-create mode ("x") never overwrites an
    existing file (D2).
    """
    content = _read_template("openkos.yaml.template")
    layout = WorkspaceLayout(root)
    fsio.write_exclusive(layout.config_path, content)
