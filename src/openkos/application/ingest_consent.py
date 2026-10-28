"""A person's ingest as quote, consent, run (#1496; ADR-0059's protocol).

The terminal's `ingest` asks its cost question on a TTY. A client with no
terminal asks it the way judging does: `quote_ingest` prices adding the files a
person chose, with no model call and no write, and `run_ingest` adds them held
to the token the person consented to. Both reuse `ingest_service`, the use case
the CLI's `ingest` and the folder watch already share, so raw/ immutability,
the `origin_key` rules, the sensitivity defaults and the resolution of a
`watch_refusal` row whose bytes land are exactly the CLI's.

**The bound.** A quote's `calls` is an upper bound, not the ordinary-path
estimate the CLI's batch gate prints: `extraction.concept.max_extraction_calls`
counts every retry, re-ask and judge attempt a reply could provoke, whatever
title the Source takes. A run enforces it per file: the client it hands the
service refuses the call past that file's share with `CallCeilingReached`, a
`BackendError`, so the extraction degrades to a Source-only add the way an
unreachable backend does, and never spends past the consent. A file the quote
priced at zero (unchanged, not text, blank, below the confidential floor) has a
ceiling of zero.

**What a quote refuses.** A path that is not absolute, does not exist, is not a
regular file or lies inside the workspace's `bundle/` or `.openkos/` is refused
before any of its bytes are read. Two chosen files with one name are both
refused (the destination is the basename, as on the CLI). Then the service's
own Phase A runs in probe mode, so a file whose bytes changed after it was
added is refused as `changed_after_import`: a person's ingest keeps that
refusal (raw/ is immutable), and the remedy is a rename, not a retry.

**Staleness.** The token binds the extraction model, the backend, the host's
locality, the settings that move the bound (`union_judge`,
`transcript_decisions`, `default_sensitivity`) and every file's bytes. A run
whose binding moved returns `IngestQuoteStale` before any call or write; a
missing token returns `RunRefused`. The workspace itself may move between quote
and run: a file that is now unchanged costs nothing, and a refusal the service
raises at run time is reported for that file and the run goes on.

**Stopping.** The service has no stop point inside one file (a model call is
never preempted, ADR-0021), so the `StopToken` is observed between files: a
stop waits for the file in flight. A workspace that stays busy at a file's
commit ends the run, since spending on files that cannot be written would waste
the consent; what was written stays.

After the last file, a run that wrote anything refreshes the derived stores
once, incrementally, as the CLI's `ingest` does; a refresh that fails leaves
them stale and never fails the run.
"""

from __future__ import annotations

import hashlib
import stat as stat_module
import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, cast

from openkos import config, fsio, lock, sensitivity
from openkos.application import backends as application_backends
from openkos.application import curate_queue, ingest_service, reindex_service
from openkos.application.curate_decisions import Stale
from openkos.application.judging import RunRefused
from openkos.application.lock_wait import CommitSection
from openkos.application.runtime import StopToken
from openkos.errors import OpenKosError
from openkos.extraction.concept import max_extraction_calls
from openkos.graph import proximity
from openkos.llm.base import (
    BackendError,
    BackendHostLocality,
    Embedder,
    LLMBackend,
    Message,
)
from openkos.state.vectorstore import open_vector_store

__all__ = [
    "CallCeilingReached",
    "IngestBinding",
    "IngestChangedField",
    "IngestConsentPorts",
    "IngestFileQuote",
    "IngestFileResult",
    "IngestProgress",
    "IngestQuote",
    "IngestQuoteStale",
    "IngestQuoteToken",
    "IngestRefused",
    "IngestRunOutcome",
    "IngestRunResult",
    "IngestTokenFile",
    "RunRefused",
    "Stale",
    "quote_ingest",
    "quote_refused_file",
    "refresh_derived_stores",
    "run_ingest",
]

_TASK: Final = "extraction"
_REFUSAL_KIND: Final = "watch_refusal"

IngestFileStatus = Literal["extracts", "source_only", "unchanged", "refused"]
"""What adding one file would do: extract with the model, add the Source with
no model call, nothing (already added, unchanged), or nothing (refused)."""

SourceOnlyReason = Literal["not_text", "blank", "confidential_floor"]
"""Why a file is added with no model call: its bytes are not text, it holds no
text, or the workspace's sensitivity floor keeps it from the model without the
confidential consent."""

IngestRefusal = Literal[
    "not_absolute",
    "not_found",
    "not_a_file",
    "inside_workspace",
    "duplicate_name",
    "changed_after_import",
    "inconsistent_workspace",
    "symlinked_destination",
    "unreadable",
]
"""Why a file is not added. `changed_after_import`: a file of that name was
added before with different bytes; raw/ is immutable, so the remedy is a rename.
`duplicate_name`: two chosen files share a name. `inconsistent_workspace`: its
Source exists without its raw copy. `symlinked_destination`: the destination
passes through a symlink inside the workspace."""

IngestChangedField = Literal["model", "backend", "locality", "settings", "files"]
"""The binding fields `IngestQuoteStale` names when they moved."""

IngestFileOutcomeStatus = Literal[
    "added",
    "updated",
    "unchanged",
    "refused",
    "interrupted",
    "busy",
    "failed",
    "not_reached",
]
"""What a run did with one file: `added` (a new Source), `updated` (an existing
Source rewritten, as a re-ingest that finishes earlier work does), `unchanged`
(nothing written), `refused`, `interrupted` (workspace files changed while it
was prepared; nothing written; adding it again can work), `busy` (the workspace
stayed locked; nothing written), `failed` (the write stopped part way; what
landed stays) and `not_reached` (a stop or a busy workspace ended the run
first)."""

IngestPhase = Literal["reading", "extracting", "writing", "refreshing"]


class IngestRefused(OpenKosError):
    """The workspace cannot be read, so nothing can be quoted or run. Raised,
    never printed."""


class CallCeilingReached(BackendError):
    """A call past a file's quoted share was refused before it was made. The
    extraction degrades as it does for an unreachable backend."""


# -- Values --------------------------------------------------------------------


@dataclass(frozen=True)
class IngestBinding:
    """What consent was given to, beside the files. A change is stale."""

    model: str
    """`config.resolve_task_model(cfg, "extraction")`."""
    backend: str
    is_local: bool
    """The extraction client's own locality verdict (construction only)."""
    include_confidential: bool
    """The caller's consent to send text above the workspace's floor."""
    settings_digest: str
    """sha256 over the settings that move the bound."""


@dataclass(frozen=True)
class IngestTokenFile:
    """One file the consent covers: its path, its bytes' sha256 and the most
    calls it may spend."""

    path: str
    digest: str
    calls: int


@dataclass(frozen=True)
class IngestQuoteToken:
    """The context consent was given for. Refused files are not in it."""

    binding: IngestBinding
    files: tuple[IngestTokenFile, ...]
    calls: int
    """The sum of the files' bounds."""


@dataclass(frozen=True)
class IngestFileQuote:
    """One chosen file, priced."""

    path: str
    name: str
    size: int | None
    """Bytes; `None` when the file was refused before it was read."""
    status: IngestFileStatus
    calls: int
    """The most chat calls adding it can make; 0 unless `extracts`."""
    source_only: SourceOnlyReason | None = None
    refusal: IngestRefusal | None = None


@dataclass(frozen=True)
class IngestQuote:
    """What adding the chosen files would cost, and the token to run it."""

    files: tuple[IngestFileQuote, ...]
    calls: int
    """An upper bound on the chat calls the run makes; it never exceeds it."""
    model: str
    backend: str
    is_local: bool
    host: str
    """The model host as it may be shown: credentials are never part of it."""
    include_confidential: bool
    token: IngestQuoteToken


@dataclass(frozen=True)
class IngestQuoteStale:
    """A token whose binding no longer matches. Returned, never raised, before
    any call or write."""

    changed: tuple[IngestChangedField, ...]


@dataclass(frozen=True)
class IngestProgress:
    """One step of a run: the file (1-based) and the phase it reached, with the
    calls spent so far. `refreshing` comes once, after the last file, with
    `index == total`."""

    index: int
    total: int
    path: str
    phase: IngestPhase
    calls: int


@dataclass(frozen=True)
class IngestFileResult:
    path: str
    name: str
    status: IngestFileOutcomeStatus
    source_id: str | None
    """The Source the file landed as (`added`, `updated`), else `None`."""
    created: int
    """New pages extracted from it."""
    revised: int
    """Existing pages it revised instead of duplicating."""
    extraction_degraded: bool
    """Extraction did not complete (the backend failed or the call ceiling was
    reached) and the file was added as a Source only."""
    calls: int
    refusal: IngestRefusal | None = None


@dataclass(frozen=True)
class IngestRunOutcome:
    status: Literal["completed", "stopped", "busy"]
    files: tuple[IngestFileResult, ...]
    calls: int
    """Chat calls made; never more than the token's."""
    indexes: Literal["refreshed", "not_needed", "stale"]
    """The derived stores after the run: refreshed, untouched because nothing
    was written, or left stale because the refresh failed."""
    observer_failed: bool = False


IngestRunResult = IngestRunOutcome | IngestQuoteStale | RunRefused
"""What a run returns."""


@dataclass(frozen=True)
class IngestConsentPorts:
    """The effects a quote and a run reach."""

    chat_client: Callable[[config.Config], LLMBackend]
    """Builds the extraction client; a quote only reads its locality."""
    autocommit: Callable[[Path, Sequence[str], str], object]
    commit_section: CommitSection = nullcontext
    refresh_derived: Callable[[Path], object] = lambda root: None
    """The incremental derived refresh after a run that wrote; raises on
    failure, which the run reports as stale indexes."""


# -- Shared --------------------------------------------------------------------


def _open(root: Path) -> tuple[config.WorkspaceLayout, config.Config]:
    reason = config.require_workspace(root)
    if reason is not None:
        raise IngestRefused(f"openkos ingest: refusing to run -- {reason}.")
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        raise IngestRefused(
            f"openkos ingest: failed while reading the workspace -- {exc}."
        ) from exc
    return config.WorkspaceLayout(root), cfg


def _settings_digest(cfg: config.Config) -> str:
    parts = (
        f"union_judge={int(cfg.union_judge)}",
        f"selector_judge={int(cfg.selector_judge)}",
        f"transcript_decisions={int(cfg.transcript_decisions)}",
        f"default_sensitivity={cfg.default_sensitivity}",
    )
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _binding(
    cfg: config.Config, client: LLMBackend, *, include_confidential: bool
) -> IngestBinding:
    locality = cast(application_backends.HasLocality, client).locality
    return IngestBinding(
        model=config.resolve_task_model(cfg, _TASK),
        backend=cfg.backend,
        is_local=locality.is_local,
        include_confidential=include_confidential,
        settings_digest=_settings_digest(cfg),
    )


def _changed(
    consented: IngestBinding, current: IngestBinding
) -> list[IngestChangedField]:
    moved: list[IngestChangedField] = []
    if consented.model != current.model:
        moved.append("model")
    if consented.backend != current.backend:
        moved.append("backend")
    if consented.is_local != current.is_local:
        moved.append("locality")
    if consented.settings_digest != current.settings_digest:
        moved.append("settings")
    return moved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_path(raw: str, layout: config.WorkspaceLayout) -> IngestRefusal | None:
    """Why `raw` may not be read, or `None`. Reads metadata only, never bytes."""
    path = Path(raw)
    if not path.is_absolute():
        return "not_absolute"
    try:
        info = path.stat()
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return "not_found"
    except OSError:
        return "unreadable"
    for protected in (layout.bundle_dir, layout.openkos_dir):
        if resolved.is_relative_to(protected.resolve()):
            return "inside_workspace"
    if not stat_module.S_ISREG(info.st_mode):
        return "not_a_file"
    return None


def _service_ports(ports: IngestConsentPorts) -> ingest_service.IngestPorts:
    return ingest_service.IngestPorts(
        chat_client=ports.chat_client,
        autocommit=ports.autocommit,
        after_commit=lambda layout, cfg: None,
        commit_section=ports.commit_section,
    )


def _no_model(cfg: config.Config) -> LLMBackend:
    raise AssertionError("a quote never builds the extraction client to call it")


_REFUSALS: Final[
    tuple[tuple[type[ingest_service.IngestRefused], IngestRefusal], ...]
] = (
    (ingest_service.RawImmutabilityRefused, "changed_after_import"),
    (ingest_service.InconsistentWorkspace, "inconsistent_workspace"),
    (ingest_service.SymlinkedDestination, "symlinked_destination"),
    (ingest_service.SourceNotReadable, "not_found"),
)


def _refusal_of(exc: ingest_service.IngestRefused) -> IngestRefusal:
    for kind, reason in _REFUSALS:
        if isinstance(exc, kind):
            return reason
    return "unreadable"


# -- The quote -----------------------------------------------------------------


def _price(
    root: Path,
    path: Path,
    cfg: config.Config,
    *,
    include_confidential: bool,
    ports: IngestConsentPorts,
) -> tuple[IngestFileStatus, SourceOnlyReason | None, IngestRefusal | None, int]:
    """Status, source-only reason, refusal and bound of one readable file."""
    policy = ingest_service.IngestPolicy(
        include_confidential=include_confidential, skip_confirmation=True
    )
    probe_ports = ingest_service.IngestPorts(
        chat_client=_no_model,
        autocommit=lambda root, paths, message: None,
        after_commit=lambda layout, cfg: None,
    )
    try:
        unchanged = ingest_service.probe_source(root, path, policy, ports=probe_ports)
    except ingest_service.IngestRefused as exc:
        return "refused", None, _refusal_of(exc), 0
    if unchanged:
        return "unchanged", None, None, 0
    try:
        decoded = fsio.read_source_text(path)
    except OSError:
        return "refused", None, "unreadable", 0
    if decoded is None:
        return "source_only", "not_text", None, 0
    if not decoded.text.strip():
        return "source_only", "blank", None, 0
    if not include_confidential and sensitivity.blocks_llm_send(
        cfg.default_sensitivity
    ):
        return "source_only", "confidential_floor", None, 0
    bound = max_extraction_calls(
        decoded.text,
        union_judge=cfg.union_judge,
        transcript_decisions=cfg.transcript_decisions,
        selector_judge=cfg.selector_judge,
    )
    return "extracts", None, None, bound


def quote_ingest(
    root: Path,
    paths: Sequence[str],
    *,
    include_confidential: bool = False,
    ports: IngestConsentPorts,
) -> IngestQuote:
    """Price adding `paths` to the workspace at `root`: no model call, no write.

    The extraction client is built only to read its model and locality. Each
    path keeps its first position; a repeat of the same file is dropped. Raises
    `ValueError` for an empty `paths` and `IngestRefused` when `root` is not a
    readable workspace."""
    if not paths:
        raise ValueError("an ingest quote needs at least one path")
    return _quote(
        root, paths, include_confidential=include_confidential, ports=ports, forced={}
    )


def _quote(
    root: Path,
    paths: Sequence[str],
    *,
    include_confidential: bool,
    ports: IngestConsentPorts,
    forced: dict[str, IngestRefusal],
) -> IngestQuote:
    """`quote_ingest`'s body; `forced` refuses the named paths without a look."""
    layout, cfg = _open(root)
    client = ports.chat_client(cfg)
    binding = _binding(cfg, client, include_confidential=include_confidential)
    locality: BackendHostLocality = cast(Any, client).locality
    host = locality.display_host

    seen: set[str] = set()
    checked: list[tuple[str, IngestRefusal | None]] = []
    for raw in paths:
        refusal = forced.get(raw) or _check_path(raw, layout)
        key = str(Path(raw).resolve()) if refusal is None else raw
        if key in seen:
            continue
        seen.add(key)
        checked.append((raw, refusal))

    names: dict[str, int] = {}
    for raw, refusal in checked:
        if refusal is None:
            names[Path(raw).name] = names.get(Path(raw).name, 0) + 1

    files: list[IngestFileQuote] = []
    token_files: list[IngestTokenFile] = []
    for raw, refusal in checked:
        path = Path(raw)
        if refusal is None and names[path.name] > 1:
            refusal = "duplicate_name"
        if refusal is not None:
            files.append(
                IngestFileQuote(raw, path.name, None, "refused", 0, refusal=refusal)
            )
            continue
        try:
            size = path.stat().st_size
            digest = _sha256(path)
        except OSError:
            files.append(
                IngestFileQuote(
                    raw, path.name, None, "refused", 0, refusal="unreadable"
                )
            )
            continue
        status, source_only, refused, bound = _price(
            root, path, cfg, include_confidential=include_confidential, ports=ports
        )
        files.append(
            IngestFileQuote(
                raw,
                path.name,
                size,
                status,
                bound,
                source_only=source_only,
                refusal=refused,
            )
        )
        if status != "refused":
            token_files.append(IngestTokenFile(raw, digest, bound))

    total = sum(f.calls for f in token_files)
    return IngestQuote(
        files=tuple(files),
        calls=total,
        model=binding.model,
        backend=binding.backend,
        is_local=binding.is_local,
        host=host,
        include_confidential=include_confidential,
        token=IngestQuoteToken(binding=binding, files=tuple(token_files), calls=total),
    )


def quote_refused_file(
    root: Path,
    decision_key: str,
    *,
    include_confidential: bool = False,
    ports: IngestConsentPorts,
) -> IngestQuote | Stale:
    """Quote adding the file an open `watch_refusal` decision refused.

    The row names the file by its path inside the configured inbox; the quote
    is `quote_ingest` over that path, so a run of its token resolves the row
    when the refused bytes land. A key whose row was settled is
    `Stale("row_resolved")` and one no row ever carried `Stale("unknown_key")`.
    With no inbox configured the file cannot be found and is quoted refused as
    `not_found`. Raises `ValueError` for a key that is not a refused file's and
    `IngestRefused` when `root` is not a readable workspace."""
    if decision_key.partition(":")[0] != _REFUSAL_KIND:
        raise ValueError(f"{decision_key!r} is not a refused file's decision")
    layout, cfg = _open(root)
    row = curate_queue.open_row_by_key(layout, decision_key)
    if row is None:
        reason: Literal["row_resolved", "unknown_key"] = (
            "row_resolved"
            if curate_queue.key_was_queued(layout, decision_key)
            else "unknown_key"
        )
        return Stale(decision_key, reason)
    name = str(row.payload.get("inbox_path", ""))
    inbox = cfg.unattended.inbox
    if inbox is None or not name:
        return _quote(
            root,
            [name],
            include_confidential=include_confidential,
            ports=ports,
            forced={name: "not_found"},
        )
    return _quote(
        root,
        [str(inbox / name)],
        include_confidential=include_confidential,
        ports=ports,
        forced={},
    )


# -- The run -------------------------------------------------------------------


class _Ceiling:
    """Counts one file's chat calls and refuses the one past its share. Every
    other attribute is the real client's, so locality and the context window
    are read from it unchanged."""

    def __init__(self, inner: LLMBackend, limit: int) -> None:
        self._inner = inner
        self._limit = limit
        self._lock = threading.Lock()
        self.calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        with self._lock:
            if self.calls >= self._limit:
                raise CallCeilingReached(
                    f"the quote covered {self._limit} model call(s) for this file"
                )
            self.calls += 1
        return self._inner.chat(messages)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _Progress:
    """Forwards progress to the caller's callback; its first failure is
    recorded and it is not called again, so a display never costs paid work."""

    def __init__(self, sink: Callable[[IngestProgress], None] | None) -> None:
        self._sink = sink
        self.failed = False

    def emit(self, event: IngestProgress) -> None:
        if self._sink is None or self.failed:
            return
        try:
            self._sink(event)
        except Exception:  # noqa: BLE001 -- the caller's display must not cost paid work
            self.failed = True


class _FileObserver(ingest_service.IngestObserver):
    """One file's window onto the service: its phases become progress events
    carrying the calls spent so far, and the import names its Source."""

    def __init__(
        self,
        reporter: _Progress,
        *,
        index: int,
        total: int,
        path: str,
        ceiling: _Ceiling,
        spent_before: int,
    ) -> None:
        self._reporter = reporter
        self._index = index
        self._total = total
        self._path = path
        self._ceiling = ceiling
        self._spent_before = spent_before
        self.source_id: str | None = None

    def phase(self, name: IngestPhase) -> None:
        self._reporter.emit(
            IngestProgress(
                self._index,
                self._total,
                self._path,
                name,
                self._spent_before + self._ceiling.calls,
            )
        )

    def extraction_starting(self) -> None:
        self.phase("extracting")

    def imported(self, summary: ingest_service.ImportedSummary) -> None:
        source_path = summary.imported_paths[1]  # bundle/sources/<slug>.md
        self.source_id = source_path.removeprefix("bundle/").removesuffix(".md")
        self.phase("writing")


def _serving(ceiling: _Ceiling) -> Callable[[config.Config], LLMBackend]:
    """The service's client factory for one file: always that file's ceiling."""

    def build(cfg: config.Config) -> LLMBackend:
        return cast(LLMBackend, ceiling)

    return build


def _stale_files(token: IngestQuoteToken) -> bool:
    for entry in token.files:
        try:
            if _sha256(Path(entry.path)) != entry.digest:
                return True
        except OSError:
            return True
    return False


def _result(
    entry: IngestTokenFile,
    status: IngestFileOutcomeStatus,
    *,
    calls: int = 0,
    refusal: IngestRefusal | None = None,
) -> IngestFileResult:
    return IngestFileResult(
        path=entry.path,
        name=Path(entry.path).name,
        status=status,
        source_id=None,
        created=0,
        revised=0,
        extraction_degraded=False,
        calls=calls,
        refusal=refusal,
    )


def run_ingest(
    root: Path,
    token: IngestQuoteToken | None,
    *,
    stop: StopToken,
    ports: IngestConsentPorts,
    progress: Callable[[IngestProgress], None] | None = None,
) -> IngestRunResult:
    """Add the files `token` covers, each held to its quoted calls.

    `None` for `token` returns `RunRefused`; a moved binding or a file whose
    bytes changed returns `IngestQuoteStale`; neither builds a client or
    writes. `stop` is observed before each file. Raises `IngestRefused` when
    `root` is not a readable workspace."""
    if token is None:
        return RunRefused("no_quote_token")
    layout, cfg = _open(root)
    client = ports.chat_client(cfg)
    moved = _changed(
        token.binding,
        _binding(cfg, client, include_confidential=token.binding.include_confidential),
    )
    if _stale_files(token):
        moved.append("files")
    if moved:
        return IngestQuoteStale(changed=tuple(moved))

    policy = ingest_service.IngestPolicy(
        include_confidential=token.binding.include_confidential,
        skip_confirmation=True,
    )
    reporter = _Progress(progress)
    total = len(token.files)
    results: list[IngestFileResult] = []
    spent = 0
    status: Literal["completed", "stopped", "busy"] = "completed"
    for index, entry in enumerate(token.files, start=1):
        if stop.is_set():
            status = "stopped"
        if status != "completed":
            results.append(_result(entry, "not_reached"))
            continue
        ceiling = _Ceiling(client, entry.calls)
        observer = _FileObserver(
            reporter,
            index=index,
            total=total,
            path=entry.path,
            ceiling=ceiling,
            spent_before=spent,
        )
        observer.phase("reading")
        refusal = _check_path(entry.path, layout)
        if refusal is not None:
            results.append(_result(entry, "refused", refusal=refusal))
            continue
        file_ports = ingest_service.IngestPorts(
            chat_client=_serving(ceiling),
            autocommit=ports.autocommit,
            after_commit=lambda layout, cfg: None,
            commit_section=ports.commit_section,
        )
        try:
            outcome = ingest_service.ingest_source(
                root, Path(entry.path), policy, ports=file_ports, observer=observer
            )
        except lock.WorkspaceBusyError:
            spent += ceiling.calls
            results.append(_result(entry, "busy", calls=ceiling.calls))
            status = "busy"
            continue
        except ingest_service.DriftDetected:
            spent += ceiling.calls
            results.append(_result(entry, "interrupted", calls=ceiling.calls))
            continue
        except ingest_service.WriteFailed:
            spent += ceiling.calls
            results.append(_result(entry, "failed", calls=ceiling.calls))
            continue
        except ingest_service.IngestRefused as exc:
            spent += ceiling.calls
            results.append(
                _result(entry, "refused", calls=ceiling.calls, refusal=_refusal_of(exc))
            )
            continue
        spent += ceiling.calls
        if isinstance(outcome, ingest_service.IngestUnchanged):
            results.append(_result(entry, "unchanged", calls=ceiling.calls))
            continue
        results.append(
            IngestFileResult(
                path=entry.path,
                name=Path(entry.path).name,
                status="updated" if outcome.regenerated else "added",
                source_id=observer.source_id,
                created=outcome.derived_count,
                revised=outcome.attached_count,
                extraction_degraded=outcome.extraction_degraded,
                calls=ceiling.calls,
            )
        )

    wrote = any(r.status in ("added", "updated", "failed") for r in results)
    indexes: Literal["refreshed", "not_needed", "stale"] = "not_needed"
    if wrote:
        reporter.emit(IngestProgress(total, total, "", "refreshing", spent))
        try:
            ports.refresh_derived(root)
        except Exception:  # noqa: BLE001 -- the files are committed; a refresh failure only leaves the indexes stale
            indexes = "stale"
        else:
            indexes = "refreshed"
    return IngestRunOutcome(
        status=status,
        files=tuple(results),
        calls=spent,
        indexes=indexes,
        observer_failed=reporter.failed,
    )


# -- The derived refresh ---------------------------------------------------------


class _SilentReindex:
    def embedder_ready(self, locality: object, cfg: config.Config) -> None:
        return None

    def progress_callback(self) -> Callable[[int, int, object], None] | None:
        return None

    def vectors_indexed(
        self, report: object, previous_model_tag: str | None, cfg: config.Config
    ) -> None:
        return None


def refresh_derived_stores(
    root: Path, *, embed_client: Callable[[config.Config], Embedder]
) -> None:
    """The incremental refresh of the three derived stores, as the daemon runs
    it (`reindex` without `--force`): the content-hash cache keeps it to what
    changed. Raises `reindex_service.ReindexRefused` on failure."""
    reindex_service.reindex_workspace(
        root,
        force=False,
        ports=reindex_service.ReindexPorts(
            embed_client=embed_client,
            open_vector_store=cast(
                Callable[[Path], AbstractContextManager[Any]], open_vector_store
            ),
            open_proximity=proximity.open_proximity_source,
            local_exemption=application_backends.resolve_local_exemption,
        ),
        observer=_SilentReindex(),
    )
