"""Judging through the API: a quote, then a run against that quote.

A quote prices the work and binds the consent a caller gives; a run judges at
most what the quote covered. Both go through the judging service. This module
adds the one thing that service leaves to its caller, the chat client: when the
caller supplies none, the API builds the default from the configuration the
service has just read (`_wiring.default_chat_client`), and a caller's factory
(task name to client) replaces it.

A quote builds a client to read its locality and the task model and never calls
it. A stale quote and a missing token are returned values, never raised; a
backend that cannot be reached is raised unchanged as the neutral
`BackendUnavailable` or `BackendModelNotFound`. A stop is observed between
chunks and never preempts a model call in flight.
"""

from collections.abc import Callable
from typing import Protocol

from openkos.api import _wiring
from openkos.api._workspace import Workspace
from openkos.application import judging
from openkos.application.judging import (
    JudgingKind,
    JudgingObserver,
    JudgingOutcome,
    JudgingQuote,
    QuoteStale,
    QuoteToken,
    RunRefused,
)
from openkos.application.runtime import StopToken
from openkos.llm.base import BackendHostLocality, LLMBackend


class ChatClient(LLMBackend, Protocol):
    """A chat client the judging service can use: it answers `chat` and says
    whether its host is local, which decides whether confidential concepts may
    be sent to it."""

    @property
    def locality(self) -> BackendHostLocality: ...


ChatClientFactory = Callable[[str], ChatClient]
"""Task name to client. Replaces the API's default client when supplied."""

RunResult = JudgingOutcome | QuoteStale | RunRefused
"""What a run returns: the outcome, a stale quote or a refusal."""


def quote_judging(
    workspace: Workspace,
    kind: JudgingKind,
    *,
    include_confidential: bool = False,
    include_uncertain: bool = False,
    chat_client: ChatClientFactory | None = None,
) -> JudgingQuote:
    """Price judging `kind` for `workspace`, with no model call.

    The returned quote carries the token a run needs. Raises `JudgingRefused`
    when the workspace cannot be read and `ValueError` for an unknown `kind`."""
    return judging.quote_judging(
        workspace.root,
        kind,
        include_confidential=include_confidential,
        include_uncertain=include_uncertain,
        ports=_wiring.judging_ports(workspace, chat_client=chat_client, wait_seconds=0),
    )


def run_judging(
    workspace: Workspace,
    token: QuoteToken | None,
    *,
    stop: StopToken | None = None,
    observer: JudgingObserver | None = None,
    chat_client: ChatClientFactory | None = None,
    wait_seconds: float = 0.0,
) -> RunResult:
    """Judge at most what `token` was quoted for, persisting each chunk.

    `None` for `token` returns `RunRefused`; a binding that moved since the
    quote returns `QuoteStale`; neither makes a call. `stop` is observed between
    chunks, so a stop waits for the chunk in flight. A chunk that cannot be
    persisted because the workspace stays busy ends the run as `unpersisted`
    rather than raising. `wait_seconds` (0 to the documented ceiling) bounds how
    long each persist waits for the workspace lock. Raises `JudgingRefused`,
    `BackendUnavailable` and `BackendModelNotFound`, and `ValueError` for a
    `wait_seconds` out of range."""
    wait = _wiring.validated_wait(wait_seconds)
    return judging.run_judging(
        workspace.root,
        token,
        stop=stop if stop is not None else StopToken(),
        ports=_wiring.judging_ports(
            workspace, chat_client=chat_client, wait_seconds=wait
        ),
        observer=observer,
    )
