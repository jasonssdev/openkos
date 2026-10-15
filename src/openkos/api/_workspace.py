"""The `Workspace` handle and its two open failures.

A handle is the workspace root, validated once. It deliberately caches nothing
the services read from `openkos.yaml`: every operation lets the service re-read
the configuration, so a change of model, backend or locality made while a
long-lived caller (a desktop app) holds the handle is observed, never hidden.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from openkos import config
from openkos.errors import OpenKosError


class WorkspaceNotFound(OpenKosError):
    """`path` is not the root of an OpenKOS workspace (absent, a nested
    directory, or not a workspace at all). Nothing was created."""

    path: Path
    reason: str

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


class WorkspaceUnreadable(OpenKosError):
    """`path` is a workspace that cannot be read: permission denied, a
    symlinked path segment, or an unreadable or invalid `openkos.yaml`."""

    path: Path
    reason: str

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def _validate(root: Path) -> None:
    """Raise the typed open failure for `root`, or return when it opens.

    Read-only: it creates and writes nothing."""
    reason = config.require_workspace(root)
    if reason is not None:
        if config.workspace_absent(root):
            raise WorkspaceNotFound(root, reason)
        raise WorkspaceUnreadable(root, reason)
    try:
        config.read_config(root)
    except (OSError, ValueError) as exc:
        raise WorkspaceUnreadable(
            root, f"openkos.yaml could not be read ({exc})"
        ) from exc


@dataclass(frozen=True)
class Workspace:
    """An open OpenKOS workspace, identified by its root directory.

    `Workspace(root=path)` and `Workspace.open(path)` are equivalent: both make
    the path absolute (never symlink-resolved, so a symlinked segment is still
    refused) and validate it. `path` must be the workspace root itself; the
    surface never searches upward from a nested directory."""

    root: Path
    _layout: config.WorkspaceLayout = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        root = Path(self.root).absolute()
        _validate(root)
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "_layout", config.WorkspaceLayout(root))

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> Workspace:
        """Open the workspace rooted at `path`.

        Raises `WorkspaceNotFound` when `path` is not a workspace root and
        `WorkspaceUnreadable` when it is one that cannot be read."""
        return cls(root=Path(path))
