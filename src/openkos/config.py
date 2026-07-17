"""Workspace root: `openkos.yaml`, `AGENTS.md`, and the four refusal conditions.

A workspace is `openkos.yaml`, `AGENTS.md`, `raw/`, and `bundle/` at some
root directory (docs/architecture.md:141-154). `bundle/` is not a workspace
on its own -- `raw/` sits outside it by design, so the workspace root is
where the engine's own files live, not the OKF bundle root.
"""

from dataclasses import dataclass
from importlib import resources
from pathlib import Path


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


def is_workspace(root: Path) -> bool:
    """True if `root` already looks like an initialized (or partially seeded) workspace.

    Checks the four refusal conditions the spec names: an existing
    `openkos.yaml`, an existing `AGENTS.md`, or a non-empty `raw/` or
    `bundle/`. A directory holding unrelated files but none of these is NOT
    a workspace -- init may adopt it.
    """
    layout = WorkspaceLayout(root)
    return (
        layout.config_path.exists()
        or layout.agents_path.exists()
        or _non_empty_dir(layout.raw_dir)
        or _non_empty_dir(layout.bundle_dir)
    )


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
    with layout.agents_path.open("x", encoding="utf-8") as agents_file:
        agents_file.write(content)


def write_config(root: Path) -> None:
    """Write `openkos.yaml` at `root`, generated from the packaged template.

    Placeholder substitution, not a YAML dumper (D5): the template's
    explanatory comments would not survive round-tripping through a dumper
    that does not preserve them. `name` is `root`'s basename; the rest of
    the fields are fixed. Exclusive-create mode ("x") never overwrites an
    existing file (D2).
    """
    template = _read_template("openkos.yaml.template")
    content = template.format(name=root.name)
    layout = WorkspaceLayout(root)
    with layout.config_path.open("x", encoding="utf-8") as config_file:
        config_file.write(content)
