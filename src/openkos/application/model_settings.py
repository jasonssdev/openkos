"""The model settings: which backend the workspace uses, where it is, and which
chat and embedding models it serves (`model-settings` change).

The read is model-free: it parses `openkos.yaml` and resolves each endpoint the
way the clients would, with no client, no network, no lock and no write.
Whether an endpoint is on this machine is `classify_backend_host`'s verdict,
the same one that decides whether a confidential concept may be sent to it.

A change rewrites only the five lines `config.MODEL_SETTINGS_KEYS` names, by
`config.set_model_settings`'s text surgery, after the existing validators have
checked every value and before the rewritten text has been read back by
`config.parse_config`. The preview plans from the bytes on disk and returns a
version carrying the normalized change and a digest over it and those bytes;
the apply takes the workspace lock once, plans again from the bytes it will
write over, and writes only when the digest is the one previewed. One write,
one commit.
"""

import hashlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from openkos import config, fsio
from openkos.application import backends, curate_decisions, setup
from openkos.application.lock_wait import CommitSection
from openkos.application.write_gate import Refused, WriteFailed
from openkos.llm.base import classify_backend_host
from openkos.state import reindex

ModelBackend = Literal["ollama", "openai-compatible"]
"""The backends a workspace can select (`config.SELECTABLE_BACKENDS`)."""

SettingField = Literal[
    "backend", "base_url", "embedding_base_url", "model", "embedding_model"
]


@dataclass(frozen=True)
class ModelSettings:
    """The workspace's model setup.

    `base_url` and `embedding_base_url` are what `openkos.yaml` says (`None`
    when unset). `remote_hosts` is the redacted display host of each resolved
    endpoint that is not on this machine, the chat endpoint first; `is_local`
    is true exactly when it is empty. `takes_api_key` is true for the
    `openai-compatible` backend, whose key the engine reads only from
    `OPENKOS_OPENAI_API_KEY`; nothing here ever carries a key."""

    backend: ModelBackend
    base_url: str | None
    embedding_base_url: str | None
    model: str
    embedding_model: str
    remote_hosts: tuple[str, ...]
    is_local: bool
    takes_api_key: bool


@dataclass(frozen=True)
class ModelSettingsChange:
    """The model setup asked for. A `None` URL removes its line, so the
    backend's own default applies (`ollama` only: `openai-compatible` needs a
    `base_url`)."""

    backend: ModelBackend
    base_url: str | None
    embedding_base_url: str | None
    model: str
    embedding_model: str


@dataclass(frozen=True)
class SettingChange:
    """One field that differs: what it is now and what it would become."""

    field: SettingField
    before: str | None
    after: str | None


@dataclass(frozen=True)
class ModelSettingsVersion:
    """The version a stateless caller applies a change against: echo it
    unchanged. `change` is the normalized request; `content_digest` covers it
    and the bytes of `openkos.yaml`."""

    change: ModelSettingsChange
    content_digest: str


@dataclass(frozen=True)
class ModelSettingsPreview:
    """What the change would do. `changes` lists every field that differs, in
    the order of `ModelSettingsChange`. `search_rebuild` is true when the
    embedding identity changes, so the stored vectors no longer match and
    search needs rebuilding."""

    current: ModelSettings
    proposed: ModelSettings
    changes: tuple[SettingChange, ...]
    search_rebuild: bool
    version: ModelSettingsVersion


ModelSettingsRefusalReason = Literal[
    "invalid_base_url",
    "invalid_embedding_base_url",
    "invalid_model",
    "invalid_embedding_model",
    "base_url_required",
    "unchanged",
]


@dataclass(frozen=True)
class ModelSettingsRefused:
    """Why the change cannot be made as asked; nothing was written.

    `invalid_*`: the value fails the check `openkos.yaml` applies to it.
    `base_url_required`: `openai-compatible` has no default endpoint.
    `unchanged`: the settings already are the ones asked for."""

    reason: ModelSettingsRefusalReason


@dataclass(frozen=True)
class ModelSettingsStale:
    """`openkos.yaml` changed since the preview, or the echoed version was
    altered; nothing was written. Preview again."""

    reason: Literal["content_changed"] = "content_changed"


@dataclass(frozen=True)
class ModelSettingsChanged:
    """The settings were written. `commit_sha` is `None` when the commit could
    not be made (the write itself landed)."""

    settings: ModelSettings
    commit_sha: str | None


@dataclass(frozen=True)
class SettingsPorts:
    """The effects the apply sequences but does not own.

    `plan_commit(root, edit, baseline)` decides, from the working tree and git,
    how the edit is committed: a plan, a refusal, or `None` for the ordinary
    path-scoped commit. `commit(root, message, plan)` commits `openkos.yaml`
    and returns the abbreviated sha or `None`."""

    commit_section: CommitSection
    plan_commit: Callable[
        [Path, Callable[[str], str], bytes],
        curate_decisions.ConfigCommitPlan | curate_decisions.ConfigCommitRefusal | None,
    ]
    commit: Callable[[Path, str, curate_decisions.ConfigCommitPlan | None], str | None]


_FIELDS: Final[tuple[SettingField, ...]] = (
    "backend",
    "base_url",
    "embedding_base_url",
    "model",
    "embedding_model",
)


def _settings(cfg: config.Config, environ: Mapping[str, str]) -> ModelSettings:
    """`cfg`'s model setup, each endpoint resolved as the clients resolve it."""
    remote: list[str] = []
    for purpose in ("chat", "embed"):
        endpoint = backends.resolve_endpoint(cfg, purpose=purpose, environ=environ)
        url = (
            environ.get("OLLAMA_HOST")
            if endpoint.source == "OLLAMA_HOST"
            else endpoint.url
        )
        locality = classify_backend_host(url)
        if not locality.is_local and locality.display_host not in remote:
            remote.append(locality.display_host)
    backend: ModelBackend = (
        "openai-compatible"
        if cfg.backend == backends.BACKEND_OPENAI_COMPATIBLE
        else "ollama"
    )
    return ModelSettings(
        backend=backend,
        base_url=cfg.base_url,
        embedding_base_url=cfg.embedding_base_url,
        model=cfg.model,
        embedding_model=cfg.embedding_model,
        remote_hosts=tuple(remote),
        is_local=not remote,
        takes_api_key=backend == backends.BACKEND_OPENAI_COMPATIBLE,
    )


def read_model_settings(
    root: Path, *, environ: Mapping[str, str] = os.environ
) -> ModelSettings:
    """The model setup of the workspace at `root`. Model-free and read-only.
    Raises `setup.SetupConfigUnreadable` when `openkos.yaml` cannot be read."""
    return _settings(setup.read_setup_config(root), environ)


def _clean_url(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def _normalized(
    change: ModelSettingsChange,
) -> ModelSettingsChange | ModelSettingsRefused:
    """The change trimmed and checked by `openkos.yaml`'s own validators."""
    if change.backend not in config.SELECTABLE_BACKENDS:
        raise ValueError(
            f"{change.backend!r} is not a backend "
            f"(expected one of {sorted(config.SELECTABLE_BACKENDS)})"
        )
    try:
        model = config.validate_model(change.model)
    except ValueError:
        return ModelSettingsRefused("invalid_model")
    try:
        embedding_model = config.validate_embedding_model(change.embedding_model)
    except ValueError:
        return ModelSettingsRefused("invalid_embedding_model")
    base_url = _clean_url(change.base_url)
    embedding_base_url = _clean_url(change.embedding_base_url)
    if base_url is not None:
        try:
            base_url = config.validate_base_url(base_url)
        except ValueError:
            return ModelSettingsRefused("invalid_base_url")
    if embedding_base_url is not None:
        try:
            embedding_base_url = config.validate_base_url(
                embedding_base_url, "embedding_base_url"
            )
        except ValueError:
            return ModelSettingsRefused("invalid_embedding_base_url")
    if change.backend == backends.BACKEND_OPENAI_COMPATIBLE and base_url is None:
        return ModelSettingsRefused("base_url_required")
    return ModelSettingsChange(
        backend=change.backend,
        base_url=base_url,
        embedding_base_url=embedding_base_url,
        model=model,
        embedding_model=embedding_model,
    )


@dataclass(frozen=True)
class _Planned:
    preview: ModelSettingsPreview
    config_bytes: bytes
    new_text: str


def _digest(change: ModelSettingsChange, config_bytes: bytes) -> str:
    lines = [f"{name}\t{getattr(change, name) or '-'}" for name in _FIELDS]
    lines.append(f"config\t{hashlib.sha256(config_bytes).hexdigest()}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _edit(change: ModelSettingsChange) -> Callable[[str], str]:
    def edit(text: str) -> str:
        return config.set_model_settings(
            text,
            backend=change.backend,
            base_url=change.base_url,
            embedding_base_url=change.embedding_base_url,
            model=change.model,
            embedding_model=change.embedding_model,
        )

    return edit


def _plan(
    root: Path, change: ModelSettingsChange, environ: Mapping[str, str]
) -> _Planned | ModelSettingsRefused:
    normalized = _normalized(change)
    if isinstance(normalized, ModelSettingsRefused):
        return normalized
    config_path = config.WorkspaceLayout(root).config_path
    try:
        config_bytes, text = fsio.snapshot_read(config_path)
        current_cfg = config.parse_config(root, text)
    except (OSError, ValueError) as exc:
        raise setup.SetupConfigUnreadable(
            f"openkos.yaml could not be read ({exc})"
        ) from exc
    current = _settings(current_cfg, environ)
    before = {name: getattr(current, name) for name in _FIELDS}
    after = {name: getattr(normalized, name) for name in _FIELDS}
    if before == after:
        return ModelSettingsRefused("unchanged")
    try:
        new_text = _edit(normalized)(text)
        proposed_cfg = config.parse_config(root, new_text)
    except ValueError as exc:
        raise WriteFailed("model_settings", ("openkos.yaml",), str(exc)) from exc
    proposed = _settings(proposed_cfg, environ)
    if {name: getattr(proposed, name) for name in _FIELDS} != after:
        raise WriteFailed(
            "model_settings",
            ("openkos.yaml",),
            "the rewritten openkos.yaml did not read back as asked",
        )
    search_rebuild = reindex.embedding_tag(
        current_cfg.embedding_model, current_cfg.backend
    ) != reindex.embedding_tag(proposed_cfg.embedding_model, proposed_cfg.backend)
    preview = ModelSettingsPreview(
        current=current,
        proposed=proposed,
        changes=tuple(
            SettingChange(name, before[name], after[name])
            for name in _FIELDS
            if before[name] != after[name]
        ),
        search_rebuild=search_rebuild,
        version=ModelSettingsVersion(
            change=normalized, content_digest=_digest(normalized, config_bytes)
        ),
    )
    return _Planned(preview, config_bytes, new_text)


def preview_model_settings(
    root: Path,
    change: ModelSettingsChange,
    *,
    environ: Mapping[str, str] = os.environ,
) -> ModelSettingsPreview | ModelSettingsRefused:
    """What `change` would do to the workspace at `root`. Writes nothing, takes
    no lock and builds no client.

    A backend outside `ModelBackend` raises `ValueError`; an unreadable
    `openkos.yaml` raises `setup.SetupConfigUnreadable`; a shape the change
    cannot edit raises `WriteFailed`."""
    planned = _plan(root, change, environ)
    return planned if isinstance(planned, ModelSettingsRefused) else planned.preview


def apply_model_settings(
    root: Path,
    version: ModelSettingsVersion,
    *,
    ports: SettingsPorts,
    environ: Mapping[str, str] = os.environ,
) -> ModelSettingsChanged | ModelSettingsRefused | ModelSettingsStale:
    """Write the change `version` was previewed for, under one lock.

    Plans again inside the commit section; a refusal or a moved digest writes
    nothing. An uncommitted edit to the same lines is refused through
    `ports.plan_commit` (`Refused`). Otherwise `openkos.yaml` is written and
    only this change is committed."""
    config_path = config.WorkspaceLayout(root).config_path
    with ports.commit_section():
        planned = _plan(root, version.change, environ)
        if isinstance(planned, ModelSettingsRefused):
            return planned
        if planned.preview.version.content_digest != version.content_digest:
            return ModelSettingsStale()
        change = planned.preview.version.change
        decision = ports.plan_commit(root, _edit(change), planned.config_bytes)
        if isinstance(decision, curate_decisions.ConfigCommitRefusal):
            raise Refused(decision.message)
        try:
            fsio.write_atomic(config_path, planned.new_text)
        except OSError as exc:
            raise WriteFailed("model_settings", ("openkos.yaml",), str(exc)) from exc
        commit_sha = ports.commit(
            root, f"openkos: model settings -> {change.backend}", decision
        )
    return ModelSettingsChanged(
        settings=planned.preview.proposed, commit_sha=commit_sha
    )
