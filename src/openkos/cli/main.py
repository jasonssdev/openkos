"""Typer application object exposed as the `openkos` console script."""

from datetime import datetime
from pathlib import Path

import typer

from openkos import config
from openkos.bundle import bundle

app = typer.Typer()


@app.callback()
def callback() -> None:
    """openkos: local-first engine that compiles text into a portable knowledge base."""


@app.command()
def init() -> None:
    """Create a fresh OKF workspace in the current directory.

    Refuses (exit 1) without writing anything if the current directory is
    already a workspace, per `config.is_workspace`'s four conditions
    (existing `openkos.yaml`, existing `AGENTS.md`, non-empty `raw/`,
    non-empty `bundle/`). This is Phase A (D1): a pure read, evaluated in
    full before any write is attempted.

    Phase B (D1) then writes, in order: `raw/`, the bundle (`index.md` then
    `log.md`), `AGENTS.md`, and `openkos.yaml` LAST (D3) -- the marker is
    written only once every other artifact already exists, so a crash
    mid-init never leaves a directory falsely claiming workspace status.
    `raw/` gets the filesystem's default directory permissions; no `chmod`
    is applied (spec: Default raw/ Permissions).
    """
    root = Path.cwd()
    if config.is_workspace(root):
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    layout.raw_dir.mkdir(parents=True, exist_ok=True)
    bundle.create(layout.bundle_dir, datetime.now().astimezone().date())
    config.write_agents(root)
    config.write_config(root)
