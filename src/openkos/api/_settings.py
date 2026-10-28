"""The model settings through the API: which backend the workspace uses, where
it is, and which models it serves, read without a model and changed in two
phases.

The read builds no client and reaches no network. A change rewrites only the
five model lines of `openkos.yaml` and never a key: the engine reads an API key
only from `OPENKOS_OPENAI_API_KEY`, which the desktop shell sets from the
keychain (ADR-0070). Every operation reads `openkos.yaml` when it runs, so the
next ask or judging run uses the new settings without a restart.
"""

from collections.abc import Callable

from openkos.api import _wiring
from openkos.api._workspace import Workspace, WorkspaceUnreadable
from openkos.application import inbox_settings, setup
from openkos.application import model_settings as service
from openkos.application.inbox_settings import (
    InboxSettings,
    InboxSettingsChange,
    InboxSettingsChanged,
    InboxSettingsPreview,
    InboxSettingsRefused,
    InboxSettingsStale,
    InboxSettingsVersion,
)
from openkos.application.model_settings import (
    ModelSettings,
    ModelSettingsChange,
    ModelSettingsChanged,
    ModelSettingsPreview,
    ModelSettingsRefused,
    ModelSettingsStale,
    ModelSettingsVersion,
)


def model_settings(workspace: Workspace) -> ModelSettings:
    """The workspace's backend, endpoints and models, which endpoint hosts are
    not on this machine, and whether the backend takes an API key.

    Builds no client, takes no lock and writes nothing. Raises
    `WorkspaceUnreadable` when `openkos.yaml` cannot be read."""
    try:
        return service.read_model_settings(workspace.root)
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc


def preview_model_settings(
    workspace: Workspace, change: ModelSettingsChange
) -> ModelSettingsPreview | ModelSettingsRefused:
    """What `change` would do: the settings now and after, each field that
    differs, whether search must be rebuilt, and the version to apply.

    A value `openkos.yaml` would refuse, a cloud backend with no address, or a
    change to what is already set is the returned `ModelSettingsRefused`. A
    backend outside `ModelBackend` raises `ValueError`; a shape of
    `openkos.yaml` the change cannot edit raises `WriteFailed`; an unreadable
    one `WorkspaceUnreadable`. Writes nothing, takes no lock, builds no
    client."""
    try:
        return service.preview_model_settings(workspace.root, change)
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc


def apply_model_settings(
    workspace: Workspace,
    version: ModelSettingsVersion,
    *,
    wait_seconds: float = 0.0,
    on_warning: Callable[[str], None] | None = None,
) -> ModelSettingsChanged | ModelSettingsRefused | ModelSettingsStale:
    """Write the change `version` was previewed for and commit only it.

    The lock is taken once. A changed `openkos.yaml` or version is the returned
    `ModelSettingsStale`; settings that already hold, `ModelSettingsRefused`;
    neither writes anything. An uncommitted edit to the model lines raises
    `Refused`. A commit that cannot be made is reported once through
    `on_warning`."""
    wait = _wiring.validated_wait(wait_seconds)
    try:
        return service.apply_model_settings(
            workspace.root,
            version,
            ports=_wiring.settings_ports(
                workspace, wait_seconds=wait, on_warning=on_warning
            ),
        )
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc


def inbox_settings_read(workspace: Workspace) -> InboxSettings:
    """The folder the workspace watches (`None`: the watch is off) and the
    limits it works within. Reads `openkos.yaml` only: no model, no lock, no
    write. Raises `WorkspaceUnreadable` when it cannot be read."""
    try:
        return inbox_settings.read_inbox_settings(workspace.root)
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc


def preview_inbox_settings(
    workspace: Workspace, change: InboxSettingsChange
) -> InboxSettingsPreview | InboxSettingsRefused:
    """What `change` would do to the watch folder: the settings now and after,
    and the version to apply.

    A folder `openkos.yaml` would refuse, or a change to what is already set,
    is the returned `InboxSettingsRefused`; a shape of `openkos.yaml` the
    change cannot edit raises `WriteFailed`; an unreadable one
    `WorkspaceUnreadable`. Writes nothing and takes no lock."""
    try:
        return inbox_settings.preview_inbox_settings(workspace.root, change)
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc


def apply_inbox_settings(
    workspace: Workspace,
    version: InboxSettingsVersion,
    *,
    wait_seconds: float = 0.0,
    on_warning: Callable[[str], None] | None = None,
) -> InboxSettingsChanged | InboxSettingsRefused | InboxSettingsStale:
    """Write the change `version` was previewed for and commit only it.

    The lock is taken once. A changed `openkos.yaml` or version is the returned
    `InboxSettingsStale`; settings that already hold, `InboxSettingsRefused`;
    neither writes anything. An uncommitted edit to the inbox line raises
    `Refused`. A commit that cannot be made is reported once through
    `on_warning`. Starting or stopping the daemon that watches the folder is
    not done here."""
    wait = _wiring.validated_wait(wait_seconds)
    try:
        return inbox_settings.apply_inbox_settings(
            workspace.root,
            version,
            ports=_wiring.inbox_settings_ports(
                workspace, wait_seconds=wait, on_warning=on_warning
            ),
        )
    except setup.SetupConfigUnreadable as exc:
        raise WorkspaceUnreadable(workspace.root, exc.reason) from exc
