"""The inbox watch folder: whether the workspace watches a folder for new
sources, and which one (ADR-0078).

The watch is a job of `openkos daemon`; this module only reads and writes the
one `unattended.inbox` line of `openkos.yaml` that tells the daemon which
folder, and nothing starts or stops a process. It follows the model settings'
shape (`application/model_settings.py`): a preview that plans from the bytes on
disk and returns a version, and an apply that takes the workspace lock once,
plans again, and writes only when the digest is the one previewed. One write,
one commit.

The folder is validated by the same check `config.parse_config` applies -- an
existing directory outside `raw/`, the bundle, `.openkos/` and the workspace
root -- and its refusal is carried as a typed reason. The value written is the
resolved absolute path, so the folder a person chose is the folder the daemon
watches. Turning the watch off removes the key.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from openkos import config, fsio
from openkos.application import curate_decisions, setup
from openkos.application.model_settings import SettingsPorts
from openkos.application.write_gate import Refused, WriteFailed


@dataclass(frozen=True)
class InboxSettings:
    """The watch folder (`None`: the watch is off, the default) and the two
    limits the watch works within, as `unattended:` sets them: how long a file
    must stay unchanged before it is added, and how many model calls the
    unattended runs may make in a day."""

    inbox: str | None
    quiet_seconds: int
    max_calls_per_day: int


@dataclass(frozen=True)
class InboxSettingsChange:
    """The folder to watch, or `None` to stop watching."""

    inbox: str | None


@dataclass(frozen=True)
class InboxSettingsVersion:
    """The version a stateless caller applies a change against: echo it
    unchanged. `change` is the normalized request; `content_digest` covers it
    and the bytes of `openkos.yaml`."""

    change: InboxSettingsChange
    content_digest: str


@dataclass(frozen=True)
class InboxSettingsPreview:
    """What the change would do: the settings now and after, and the version to
    apply."""

    current: InboxSettings
    proposed: InboxSettings
    version: InboxSettingsVersion


InboxSettingsRefusalReason = Literal[
    "invalid", "inside_workspace", "not_a_directory", "unchanged"
]


@dataclass(frozen=True)
class InboxSettingsRefused:
    """Why the change cannot be made as asked; nothing was written.

    `invalid`: blank, or a `~` with no home. `inside_workspace`: `raw/`, the
    bundle, `.openkos/`, the workspace root or a folder holding it.
    `not_a_directory`: no such folder. `unchanged`: already set that way."""

    reason: InboxSettingsRefusalReason


@dataclass(frozen=True)
class InboxSettingsStale:
    """`openkos.yaml` changed since the preview, or the echoed version was
    altered; nothing was written. Preview again."""

    reason: Literal["content_changed"] = "content_changed"


@dataclass(frozen=True)
class InboxSettingsChanged:
    """The folder was written. `commit_sha` is `None` when the commit could not
    be made (the write itself landed)."""

    settings: InboxSettings
    commit_sha: str | None


_COMMIT_MESSAGES: Final = {
    True: "openkos: watch folder set",
    False: "openkos: watch folder removed",
}


def _settings(cfg: config.Config) -> InboxSettings:
    inbox = cfg.unattended.inbox
    return InboxSettings(
        inbox=None if inbox is None else str(inbox),
        quiet_seconds=cfg.unattended.quiet_seconds,
        max_calls_per_day=cfg.unattended.max_calls_per_day,
    )


def read_inbox_settings(root: Path) -> InboxSettings:
    """The watch setup of the workspace at `root`. Model-free and read-only.
    Raises `setup.SetupConfigUnreadable` when `openkos.yaml` cannot be read."""
    return _settings(setup.read_setup_config(root))


@dataclass(frozen=True)
class _Planned:
    preview: InboxSettingsPreview
    config_bytes: bytes
    new_text: str


def _digest(change: InboxSettingsChange, config_bytes: bytes) -> str:
    lines = [
        f"inbox\t{change.inbox or '-'}",
        f"config\t{hashlib.sha256(config_bytes).hexdigest()}",
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _edit(change: InboxSettingsChange) -> Callable[[str], str]:
    def edit(text: str) -> str:
        return config.set_unattended_inbox(text, change.inbox)

    return edit


def _plan(root: Path, change: InboxSettingsChange) -> _Planned | InboxSettingsRefused:
    config_path = config.WorkspaceLayout(root).config_path
    try:
        config_bytes, text = fsio.snapshot_read(config_path)
        current_cfg = config.parse_config(root, text)
    except (OSError, ValueError) as exc:
        raise setup.SetupConfigUnreadable(
            f"openkos.yaml could not be read ({exc})"
        ) from exc
    normalized = change
    if change.inbox is not None:
        try:
            resolved = config.resolve_unattended_inbox(root, change.inbox)
        except config.InboxRejected as exc:
            return InboxSettingsRefused(exc.reason)
        normalized = InboxSettingsChange(inbox=str(resolved))
    current = _settings(current_cfg)
    if current.inbox == normalized.inbox:
        return InboxSettingsRefused("unchanged")
    try:
        new_text = _edit(normalized)(text)
        proposed_cfg = config.parse_config(root, new_text)
    except ValueError as exc:
        raise WriteFailed("inbox_settings", ("openkos.yaml",), str(exc)) from exc
    proposed = _settings(proposed_cfg)
    if proposed.inbox != normalized.inbox:
        raise WriteFailed(
            "inbox_settings",
            ("openkos.yaml",),
            "the rewritten openkos.yaml did not read back as asked",
        )
    preview = InboxSettingsPreview(
        current=current,
        proposed=proposed,
        version=InboxSettingsVersion(
            change=normalized, content_digest=_digest(normalized, config_bytes)
        ),
    )
    return _Planned(preview, config_bytes, new_text)


def preview_inbox_settings(
    root: Path, change: InboxSettingsChange
) -> InboxSettingsPreview | InboxSettingsRefused:
    """What `change` would do to the workspace at `root`. Writes nothing and
    takes no lock.

    An unreadable `openkos.yaml` raises `setup.SetupConfigUnreadable`; a shape
    the change cannot edit raises `WriteFailed`."""
    planned = _plan(root, change)
    return planned if isinstance(planned, InboxSettingsRefused) else planned.preview


def apply_inbox_settings(
    root: Path,
    version: InboxSettingsVersion,
    *,
    ports: SettingsPorts,
) -> InboxSettingsChanged | InboxSettingsRefused | InboxSettingsStale:
    """Write the change `version` was previewed for, under one lock.

    Plans again inside the commit section; a refusal or a moved digest writes
    nothing. An uncommitted edit to the inbox line is refused through
    `ports.plan_commit` (`Refused`). Otherwise `openkos.yaml` is written and
    only this change is committed."""
    config_path = config.WorkspaceLayout(root).config_path
    with ports.commit_section():
        planned = _plan(root, version.change)
        if isinstance(planned, InboxSettingsRefused):
            return planned
        if planned.preview.version.content_digest != version.content_digest:
            return InboxSettingsStale()
        change = planned.preview.version.change
        decision = ports.plan_commit(root, _edit(change), planned.config_bytes)
        if isinstance(decision, curate_decisions.ConfigCommitRefusal):
            raise Refused(decision.message)
        try:
            fsio.write_atomic(config_path, planned.new_text)
        except OSError as exc:
            raise WriteFailed("inbox_settings", ("openkos.yaml",), str(exc)) from exc
        commit_sha = ports.commit(
            root, _COMMIT_MESSAGES[change.inbox is not None], decision
        )
    return InboxSettingsChanged(
        settings=planned.preview.proposed, commit_sha=commit_sha
    )
