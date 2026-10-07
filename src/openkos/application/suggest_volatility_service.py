"""The `suggest-volatility` use case, as an application service (ADR-0018,
issue #1168; a sibling of `suggest_relations_service`).

`suggest_volatility_tiers` suggests a volatility tier for every concept TYPE
present in the workspace at an explicit `root`. It returns a typed
`VolatilityOutcome` and raises the typed `SuggestionRefused` subclasses the
other suggestion service defines; it never prompts, renders, reads the current
directory or raises `typer.Exit`. The CLI `suggest-volatility` verb is one
adapter over it.

The run is read-only: it writes nothing under the workspace. Everything the
user reads goes through a `VolatilityObserver`, and the concrete effects
(`chat_client`, the typing seam) through `VolatilityPorts`, exactly as in
`suggest_relations_service`.

A no-model failure comes back INSIDE the returned batch (#441): the completed
suggestions are never discarded, and the outcome carries the failure. The
raise-path ladder is retained around the call for an injected backend that
raises outside `llm.chat`'s guarded seam.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from openkos import config, lock
from openkos.application import backends as application_backends
from openkos.application.lock_wait import CommitSection
from openkos.application.suggest_relations_service import (
    DOCTOR_HINT,
    BackendFailed,
    BackendNotReachable,
    ModelNotInstalled,
    batch_failure_message,
    unreadable_refusal,
    workspace_refusal,
)
from openkos.llm.base import (
    BackendError,
    BackendModelNotFound,
    BackendUnavailable,
    LLMBackend,
)
from openkos.model import types
from openkos.resolution import volatility_typing
from openkos.resolution.volatility_typing import TierSuggestion, TierSuggestionBatch
from openkos.state import derived
from openkos.state import volatility_suggestions as volatility_store

_VERB = "suggest-volatility"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VolatilityRequest:
    include_confidential: bool = False
    max_calls: int | None = None
    """The most chat calls this run may issue (a budgeted, runner-started run
    passes what it has left). `None` is unbounded: the only value a CLI run
    passes, and byte-identical to a run that never had the bound."""
    use_cache: bool = False
    """Serve a type whose exact prompt was already answered from `findings.db`
    and persist every fresh answer (#1332). Off for the read-only CLI verb,
    which writes nothing under the workspace."""


class VolatilityObserver(Protocol):
    """The adapter's window onto a run."""

    def walk_incomplete(
        self, bundle_dir: Path, *, include_confidential: bool, local_exemption: bool
    ) -> None:
        """Called once the local exemption is known, before any type is
        sampled, so the adapter may warn that the bundle scan was incomplete."""

    def progress_callback(self) -> Callable[[int, int, TierSuggestion], None] | None:
        """The per-type progress hook, or `None` for silence."""


@dataclass(frozen=True)
class VolatilityPorts:
    chat_client: Callable[[config.Config, str | None], LLMBackend]
    resolve_local_exemption: Callable[
        [application_backends.HasLocality, config.Config], bool
    ] = application_backends.resolve_local_exemption
    suggest_volatility: Callable[..., TierSuggestionBatch] = (
        volatility_typing.suggest_volatility
    )
    commit_section: CommitSection = contextlib.nullcontext
    """Entered around the persist of fresh answers (`use_cache` only)."""


def cached_answers(
    layout: config.WorkspaceLayout,
    model: str,
    type_tiers: Mapping[str, str] | None = None,
) -> Callable[[str, str], TierSuggestion | None]:
    """The `served` lookup over `findings.db`: the answer already held for
    `(type, prompt digest)` under `model`, or `None`. Fail-open: a missing or
    unreadable store serves nothing, so every type is asked."""
    rows: tuple[volatility_store.PersistedVolatilitySuggestion, ...] = ()
    if layout.findings_db_path.exists():
        try:
            conn = derived.open_derived_connection(layout.findings_db_path)
            try:
                rows = volatility_store.open_volatility_suggestions(conn)
            finally:
                conn.close()
        except (OSError, sqlite3.Error) as exc:
            log.warning("persisted volatility suggestions unreadable (%s)", exc)
    held = {
        (row.type_name, row.prompt_digest): row
        for row in rows
        if row.model == model and row.suggested_tier in types.VOLATILITY_TIERS
    }

    def lookup(type_name: str, digest: str) -> TierSuggestion | None:
        row = held.get((type_name, digest))
        if row is None:
            return None
        return TierSuggestion(
            type_name=type_name,
            current_default=volatility_typing.effective_tier(type_name, type_tiers),
            suggested_tier=row.suggested_tier,
            rationale=row.rationale,
        )

    return lookup


def record_answers(
    layout: config.WorkspaceLayout,
    model: str,
    batch: TierSuggestionBatch,
    *,
    commit_section: CommitSection = contextlib.nullcontext,
) -> None:
    """Persist every non-degraded answer the run just paid for, fail-open: a
    failed persist costs a log line and a later re-ask, never the run. A
    degrade is a failure, not a verdict, and is never stored."""
    answers = {s.type_name: s for s in batch.results}
    rows: list[volatility_store.PersistedVolatilitySuggestion] = []
    for type_name, computed in batch.computed.items():
        answer = answers.get(type_name)
        if answer is None or answer.suggested_tier is None:
            continue
        rows.append(
            volatility_store.PersistedVolatilitySuggestion(
                type_name=type_name,
                model=model,
                prompt_digest=computed.prompt_digest,
                suggested_tier=answer.suggested_tier,
                rationale=answer.rationale,
                input_refs=computed.input_refs,
            )
        )
    if not rows:
        return
    try:
        with commit_section():
            conn = derived.open_derived_connection(layout.findings_db_path)
            try:
                volatility_store.record_volatility_suggestions(conn, rows)
            finally:
                conn.close()
    except (OSError, sqlite3.Error, lock.WorkspaceBusyError) as exc:
        log.warning("volatility suggestions not persisted (%s)", exc)


@dataclass(frozen=True)
class VolatilityOutcome:
    batch: TierSuggestionBatch
    model: str
    cfg: config.Config | None = None
    """The run's config, so a partial-batch message words the backend."""

    @property
    def deferred_by_bound(self) -> int:
        """Types a `max_calls` bound left unasked; `0` for an unbounded run."""
        return self.batch.deferred

    @property
    def results(self) -> tuple[TierSuggestion, ...]:
        return tuple(self.batch.results)

    @property
    def failure(self) -> BackendError | None:
        return self.batch.failure


def volatility_batch_failure_message(
    batch: TierSuggestionBatch, *, model: str, cfg: config.Config | None = None
) -> str:
    """One line for a partial batch (#441). Unlike its siblings the count has no
    of-total: `suggest_volatility` derives its type queue INSIDE the leaf, so
    the verb holds no pre-flight total and fabricating one would cost a second
    full bundle walk for an error line."""
    context = (
        f"openkos {_VERB}: failed after suggesting {len(batch.results)} concept type(s)"
    )
    return batch_failure_message(context, batch.failure, model, cfg)


def suggest_volatility_tiers(
    root: Path,
    request: VolatilityRequest,
    ports: VolatilityPorts,
    observer: VolatilityObserver,
) -> VolatilityOutcome:
    """Suggest a tier per concept type of the workspace at `root`."""
    reason = config.require_workspace(root)
    if reason is not None:
        raise workspace_refusal(_VERB, reason)

    if request.max_calls is not None and request.max_calls < 0:
        raise ValueError(f"max_calls must be >= 0, got {request.max_calls}")

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        raise unreadable_refusal(_VERB, exc) from exc

    llm = ports.chat_client(cfg, "volatility_typing")
    # The model THIS task resolved (#1294), not the global `model:`.
    task_model = config.resolve_task_model(cfg, "volatility_typing")
    local_exemption = ports.resolve_local_exemption(
        cast(application_backends.HasLocality, llm), cfg
    )
    observer.walk_incomplete(
        layout.bundle_dir,
        include_confidential=request.include_confidential,
        local_exemption=local_exemption,
    )
    # The bound is forwarded only when one was set, so every unbounded (CLI)
    # call reaches the port with exactly the arguments it always had.
    bound: dict[str, object] = (
        {} if request.max_calls is None else {"max_calls": request.max_calls}
    )
    if cfg.type_tiers:
        # Forwarded only when the workspace overrides a tier, so a workspace
        # without overrides reaches the port with exactly the arguments it
        # always had.
        bound["type_tiers"] = cfg.type_tiers
    if request.use_cache:
        bound["served"] = cached_answers(layout, task_model, cfg.type_tiers)
    try:
        batch = ports.suggest_volatility(
            layout.bundle_dir,
            llm=llm,
            include_confidential=request.include_confidential,
            local_exemption=local_exemption,
            # #812: the mirror of `suggest-relations`' own forwarding.
            rationale_language=cfg.rationale_language,
            on_progress=observer.progress_callback(),
            **bound,
        )
    except BackendUnavailable as exc:
        raise BackendNotReachable(
            f"openkos {_VERB}: failed -- {exc}. "
            f"{application_backends.start_hint(cfg)}, "
            f"then try again.{DOCTOR_HINT}"
        ) from exc
    except BackendModelNotFound as exc:
        raise ModelNotInstalled(
            f"openkos {_VERB}: failed -- model '{task_model}' is "
            f"not installed. {application_backends.install_hint(cfg, task_model)}, "
            "then try again."
        ) from exc
    # The two specific handlers above MUST precede this generic handler: both
    # subclass `BackendError`, so reordering would funnel them into this
    # fallback and lose their actionable remediation messages.
    except BackendError as exc:
        raise BackendFailed(f"openkos {_VERB}: failed -- {exc}.") from exc

    if request.use_cache:
        record_answers(layout, task_model, batch, commit_section=ports.commit_section)
    return VolatilityOutcome(batch=batch, model=task_model, cfg=cfg)
