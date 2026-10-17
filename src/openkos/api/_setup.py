"""Guided setup through the API: whether this machine can run the workspace's
models, and pulling one that is missing.

Readiness talks only to the configured runtime host, to read its version and
installed models. It calls no model, writes nothing and takes no lock, so a busy
workspace never blocks it. The client is built by the API's own composition
root (`_wiring.setup_client`) from the configuration the service has just read.

A pull fetches only a model the workspace configures, through the configured
runtime; it writes nothing under the workspace and takes no lock. A stop is
observed between stream lines.
"""

from openkos.api import _wiring
from openkos.api._workspace import Workspace, WorkspaceUnreadable
from openkos.application import setup
from openkos.application.runtime import StopToken
from openkos.application.setup import (
    PullObserver,
    PullOutcome,
    PullRefused,
    SetupReadiness,
)


def setup_readiness(workspace: Workspace) -> SetupReadiness:
    """Whether the runtime answers, which configured models are installed, and
    the machine's memory against the measured floor.

    An unreachable runtime is reported as `runtime.reachable` false, never
    raised. Raises `WorkspaceUnreadable` when `openkos.yaml` cannot be read."""
    try:
        return setup.setup_readiness(workspace.root, ports=_wiring.setup_ports())
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc


def pull_model(
    workspace: Workspace,
    model: str,
    *,
    stop: StopToken | None = None,
    observer: PullObserver | None = None,
) -> PullOutcome | PullRefused:
    """Pull `model`, one of the tags `setup_readiness` lists, with progress.

    Returns `PullRefused` for an unconfigured tag or a backend without a pull,
    and `already_installed` when the runtime already has it. Raises
    `BackendUnavailable` when the runtime does not answer,
    `BackendModelNotFound` when it has no such model, any other `BackendError`
    for a failed pull, and `WorkspaceUnreadable` when `openkos.yaml` cannot be
    read."""
    try:
        return setup.pull_model(
            workspace.root,
            model,
            ports=_wiring.setup_ports(),
            stop=stop if stop is not None else StopToken(),
            observer=observer,
        )
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc
