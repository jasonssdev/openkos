"""Guided setup through the API: whether this machine can run the workspace's
models.

Readiness talks only to the configured runtime host, to read its version and
installed models. It calls no model, writes nothing and takes no lock, so a busy
workspace never blocks it. The client is built by the API's own composition
root (`_wiring.setup_client`) from the configuration the service has just read.
"""

from openkos.api import _wiring
from openkos.api._workspace import Workspace, WorkspaceUnreadable
from openkos.application import setup
from openkos.application.setup import SetupReadiness


def setup_readiness(workspace: Workspace) -> SetupReadiness:
    """Whether the runtime answers, which configured models are installed, and
    the machine's memory against the measured floor.

    An unreachable runtime is reported as `runtime.reachable` false, never
    raised. Raises `WorkspaceUnreadable` when `openkos.yaml` cannot be read."""
    try:
        return setup.setup_readiness(workspace.root, ports=_wiring.setup_ports())
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc
