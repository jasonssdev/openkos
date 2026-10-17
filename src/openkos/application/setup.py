"""Guided setup's engine half: whether this machine is ready to run the
workspace's models, and pulling one that is missing (ADR-0057).

Readiness is a model-free read. It talks only to the configured runtime host,
to read the runtime's version and its installed models, and never writes,
takes the workspace lock or calls a model. It reuses `doctor`'s primitives
rather than `doctor` itself: the same resolver (through the injected client
factory, as `doctor`'s `build_client` is), the same tag matching and the same
task-model enumeration (`doctor.configured_task_models`). `run_diagnostics`
returns labelled strings and runs git, vector and ledger probes a setup screen
does not need, so calling it would mean parsing its labels.

The pull asks the configured runtime to fetch one model the workspace names,
and nothing else: an unconfigured tag is refused before any client is built,
so setup's network use stays the runtime's own model download. No user content
is sent anywhere. Progress is summed over the layers the runtime reports, so it
never moves backwards, and a stop is checked once before the stream and after
every line (ADR-0057: Ollama resumes a stopped download).

The service never builds a client: the caller injects a factory, so this
module imports only `openkos.llm.base` (ADR-0018).
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

from openkos import config
from openkos.application import backends, doctor
from openkos.application.runtime import StopToken
from openkos.errors import OpenKosError
from openkos.llm.base import (
    BackendDiagnostics,
    BackendError,
    ModelRuntime,
    PullEvent,
    model_tag_matches,
)

MEMORY_FLOOR_BYTES: Final = 32 * 1024**3
"""ADR-0047's measured floor, 32 GB of unified memory, as the 32 GiB a 32 GB
Mac reports. Reported against, never enforced."""

OLLAMA_DOWNLOAD_URL: Final = "https://ollama.com/download"
"""Where the app sends a user who has no Ollama (ADR-0057). Readiness only
carries the address; it never fetches it."""

PROBE_TIMEOUT_SECONDS: Final = 5.0
"""The runtime probe's timeout: the CLI's `init` and `doctor` preflight value."""

PULL_TIMEOUT_SECONDS: Final = 600.0
"""How long one read of a pull stream may wait: the Ollama client's own default.
A stream that stalls for longer ends as unavailable; the next pull resumes."""

Backend = Literal["ollama", "openai-compatible"]


class SetupConfigUnreadable(OpenKosError):
    """The workspace's `openkos.yaml` could not be read for setup."""

    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RuntimeReadiness:
    """The configured model runtime, as setup sees it.

    `host` is the userinfo-redacted display host, the only form of it that may
    be shown. `version` is the runtime's own version on `ollama` when it
    answered, otherwise `None`. `download_url` is where to get Ollama, on the
    `ollama` backend only."""

    backend: Backend
    host: str
    is_local: bool
    reachable: bool
    version: str | None
    download_url: str | None


@dataclass(frozen=True)
class ModelReadiness:
    """One configured model tag.

    `roles` names what the tag is for (`chat`, `embedding`, or task names).
    `required` is true for the chat and embedding models, which ingest and ask
    need; a task model fails only the stage that names it. `installed` is
    `None` when the runtime's model list could not be read. `pullable` is true
    where setup can pull it (the `ollama` backend)."""

    tag: str
    roles: tuple[str, ...]
    required: bool
    installed: bool | None
    pullable: bool


@dataclass(frozen=True)
class MemoryReadiness:
    """The machine's physical memory against ADR-0047's floor. `total_bytes`
    and `meets_floor` are `None` where the memory cannot be read."""

    total_bytes: int | None
    floor_bytes: int
    meets_floor: bool | None


@dataclass(frozen=True)
class SetupReadiness:
    """Whether the workspace's models can run here. `ready` is true exactly
    when the runtime answered and every required model is installed."""

    runtime: RuntimeReadiness
    models: tuple[ModelReadiness, ...]
    memory: MemoryReadiness
    ready: bool


def physical_memory_bytes() -> int | None:
    """Total physical memory, or `None` where the platform cannot say."""
    try:
        page, pages = os.sysconf("SC_PAGE_SIZE"), os.sysconf("SC_PHYS_PAGES")
    except AttributeError, ValueError, OSError:
        return None
    # `sysconf` answers -1 for a value it does not know.
    return page * pages if page > 0 and pages > 0 else None


@dataclass(frozen=True)
class SetupPorts:
    """What setup reaches: a client factory, called with the freshly read
    configuration and a timeout, and the memory probe."""

    client: Callable[[config.Config, float], BackendDiagnostics]
    total_memory: Callable[[], int | None] = physical_memory_bytes


def read_setup_config(root: Path) -> config.Config:
    """The workspace configuration, or `SetupConfigUnreadable`."""
    try:
        return config.read_config(root)
    except (OSError, ValueError) as exc:
        raise SetupConfigUnreadable(f"openkos.yaml could not be read ({exc})") from exc


def configured_models(
    cfg: config.Config,
) -> tuple[tuple[str, tuple[str, ...], bool], ...]:
    """`(tag, roles, required)` for every distinct configured tag: the chat
    model, the embedding model, then each task model that differs from the
    chat model, by task name, as `doctor` enumerates them."""
    roles: dict[str, list[str]] = {cfg.model: ["chat"]}
    roles.setdefault(cfg.embedding_model, []).append("embedding")
    for task, tag in sorted(doctor.configured_task_models(cfg).items()):
        if tag != cfg.model:
            roles.setdefault(tag, []).append(task)
    required = {cfg.model, cfg.embedding_model}
    return tuple((tag, tuple(names), tag in required) for tag, names in roles.items())


def setup_readiness(root: Path, *, ports: SetupPorts) -> SetupReadiness:
    """Read whether the workspace at `root` can run its models here.

    An unreachable runtime is reported, never raised. Raises
    `SetupConfigUnreadable` when the configuration cannot be read."""
    cfg = read_setup_config(root)
    is_ollama = cfg.backend == backends.BACKEND_OLLAMA
    client = ports.client(cfg, PROBE_TIMEOUT_SECONDS)
    locality = client.locality

    version: str | None = None
    reachable = False
    installed: list[str] | None = None
    if is_ollama and isinstance(client, ModelRuntime):
        try:
            version = client.version()
            reachable = True
        except BackendError:
            pass
    if reachable or not is_ollama:
        try:
            installed = [m.tag for m in client.list_models()]
            reachable = True
        except BackendError:
            installed = None

    models = tuple(
        ModelReadiness(
            tag=tag,
            roles=roles,
            required=required,
            installed=None if installed is None else model_tag_matches(tag, installed),
            pullable=is_ollama,
        )
        for tag, roles, required in configured_models(cfg)
    )
    total = ports.total_memory()
    return SetupReadiness(
        runtime=RuntimeReadiness(
            backend="ollama" if is_ollama else "openai-compatible",
            host=locality.display_host,
            is_local=locality.is_local,
            reachable=reachable,
            version=version,
            download_url=OLLAMA_DOWNLOAD_URL if is_ollama else None,
        ),
        models=models,
        memory=MemoryReadiness(
            total_bytes=total,
            floor_bytes=MEMORY_FLOOR_BYTES,
            meets_floor=None if total is None else total >= MEMORY_FLOOR_BYTES,
        ),
        ready=reachable and all(m.installed is True for m in models if m.required),
    )


PullPhase = Literal["preparing", "downloading", "verifying", "finishing"]


@dataclass(frozen=True)
class PullProgress:
    """How far a pull has got. `completed_bytes` and `total_bytes` are summed
    over the layers reported so far; `total_bytes` is `None` until a layer
    reports its size."""

    model: str
    phase: PullPhase
    completed_bytes: int
    total_bytes: int | None


class PullObserver(Protocol):
    """Receives a pull's progress, once per stream line."""

    def progress(self, event: PullProgress) -> None: ...


@dataclass(frozen=True)
class PullOutcome:
    """How a pull ended: `completed`, `already_installed` (no pull was made) or
    `stopped`, with the bytes reported by then."""

    model: str
    status: Literal["completed", "already_installed", "stopped"]
    completed_bytes: int
    total_bytes: int | None


@dataclass(frozen=True)
class PullRefused:
    """A pull that was not started: the model is not one the workspace
    configures, or the backend has no pull."""

    model: str
    reason: Literal["not_configured", "backend_cannot_pull"]


def pull_phase(status: str) -> PullPhase:
    """The runtime's status line as a phase; its own text is never forwarded."""
    if status == "pulling manifest":
        return "preparing"
    if status.startswith("pulling "):
        return "downloading"
    if status.startswith("verifying"):
        return "verifying"
    return "finishing"


class _Progress:
    """The latest `(completed, total)` per layer, never moving backwards."""

    def __init__(self) -> None:
        self._layers: dict[str, tuple[int, int | None]] = {}

    def add(self, event: PullEvent) -> None:
        if event.digest is None:
            return
        completed, total = self._layers.get(event.digest, (0, None))
        self._layers[event.digest] = (
            max(completed, event.completed or 0),
            event.total if event.total is not None else total,
        )

    @property
    def completed(self) -> int:
        return sum(c for c, _ in self._layers.values())

    @property
    def total(self) -> int | None:
        known = [t for _, t in self._layers.values() if t is not None]
        return sum(known) if known else None


def pull_model(
    root: Path,
    model: str,
    *,
    ports: SetupPorts,
    stop: StopToken,
    observer: PullObserver | None = None,
) -> PullOutcome | PullRefused:
    """Pull `model`, one of the workspace's configured tags, through its runtime.

    Returns `PullRefused` for an unconfigured tag (no client is built) or a
    backend without a pull, and `already_installed` without pulling when the
    runtime lists the model. Raises `SetupConfigUnreadable`, and the
    `BackendError` family from the runtime."""
    cfg = read_setup_config(root)
    if model not in {tag for tag, _, _ in configured_models(cfg)}:
        return PullRefused(model=model, reason="not_configured")
    if cfg.backend != backends.BACKEND_OLLAMA:
        return PullRefused(model=model, reason="backend_cannot_pull")
    client = ports.client(cfg, PULL_TIMEOUT_SECONDS)
    if not isinstance(client, ModelRuntime):
        return PullRefused(model=model, reason="backend_cannot_pull")
    if model_tag_matches(model, [m.tag for m in client.list_models()]):
        return PullOutcome(model, "already_installed", 0, None)
    progress = _Progress()
    if stop.is_set():
        return PullOutcome(model, "stopped", 0, None)

    def on_event(event: PullEvent) -> None:
        progress.add(event)
        if observer is not None:
            observer.progress(
                PullProgress(
                    model, pull_phase(event.status), progress.completed, progress.total
                )
            )

    done = client.pull(model, on_event=on_event, should_stop=stop.is_set)
    return PullOutcome(
        model,
        "completed" if done else "stopped",
        progress.completed,
        progress.total,
    )
