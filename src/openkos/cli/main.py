"""Typer application object exposed as the `openkos` console script."""

import contextvars
import dataclasses
import functools
import glob
import inspect
import json
import os
import sqlite3
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path, PurePosixPath
from typing import Final, Literal, NamedTuple, NoReturn, TypedDict, TypeVar, cast

import typer
from rich.console import Console

from openkos import (
    config,
    fsio,
    lock,
    logsetup,
    read_outcome,
    source_date,
    source_title,
)
from openkos import lint as lint_check
from openkos.application import backends as application_backends
from openkos.application import (
    contradictions_service,
    duplicates_service,
    ingest_service,
    lock_wait,
    merge_service,
    reconcile_service,
    reindex_service,
    unmerge_service,
    write_gate,
)
from openkos.application import doctor as application_doctor
from openkos.application import drift as application_drift
from openkos.application import ingest as application_ingest
from openkos.application import lifecycle as application_lifecycle
from openkos.application import lint as application_lint
from openkos.application import list_service as application_list
from openkos.application import next_action as next_action_module
from openkos.application import pending as application_pending
from openkos.application import query as application_query
from openkos.application import repair as application_repair
from openkos.application import revisions as revisions_service
from openkos.application import status as application_status
from openkos.application import suggest_relations_service as relations_service
from openkos.application import suggest_volatility_service as volatility_service
from openkos.application.revisions_report import revisions_report
from openkos.bundle import bundle, listing, source_titles
from openkos.bundle import decisions as bundle_decisions
from openkos.bundle import index as bundle_index
from openkos.bundle import ledger as bundle_ledger
from openkos.bundle import log as bundle_log
from openkos.bundle import provenance as bundle_provenance
from openkos.cli import curate as curate_module
from openkos.cli import observability
from openkos.extraction import judge as judge_mod
from openkos.extraction.concept import (
    ExtractionReport,
    estimate_extraction_calls,
)
from openkos.graph import proximity, sqlite_graph
from openkos.graph.base import Edge, GraphStore
from openkos.graph.sqlite_graph import build_graph
from openkos.graph.summary import graph_edge_summary
from openkos.llm.base import (
    BackendDiagnostics,
    BackendEmbeddingDimensionMismatch,
    BackendError,
    BackendModelNotFound,
    BackendUnavailable,
    Embedder,
    LLMBackend,
)
from openkos.llm.ollama import (
    BackendHostLocality,
    InstalledModel,
    OllamaClient,
    is_embedding_model,
    model_tag_matches,
)
from openkos.llm.openai_compatible import OpenAICompatibleClient
from openkos.model import okf, types
from openkos.model.relations import (
    relation_type_note,
    validate_relation_type,
)
from openkos.model.types import INSIGHT_TYPE as _INSIGHT_TYPE
from openkos.model.types import TYPE_TO_SECTION as _TYPE_TO_SECTION
from openkos.resolution import find_candidates_report
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
    adjudicate_candidates,
    rubric_digest,
    withdraw_self_refuting_same,
)
from openkos.resolution.candidates import (
    CandidateGroup,
    Tier,
    candidate_group_truncation_notice,
)
from openkos.resolution.contradiction import (
    ContradictionBatch,
    ContradictionVerdict,
    find_contradictions,
    is_high_confidence_finding,
    plan_candidates,
)
from openkos.resolution.decision_revision import (
    DecisionDate,
    RevisionVerdict,
    RevisionVerdictValue,
    relation_for,
    revision_truncation_notice,
)
from openkos.resolution.edge_typing import (
    EdgeSuggestion,
    candidate_edges,
    suggest_edge_types,
)
from openkos.resolution.reconciliation import reconcile_merged_body
from openkos.resolution.volatility_typing import (
    TierSuggestion,
    suggest_volatility,
)
from openkos.retrieval.answer import NO_MATCH, NoMatchCause
from openkos.state import adjudications as adjudications_store
from openkos.state import derived, findings
from openkos.state import edge_suggestions as edge_suggestions_store
from openkos.state import reindex as reindex_module
from openkos.state import revision_findings as revision_findings_store
from openkos.state.fts import FtsUnavailable
from openkos.state.vectorstore import open_vector_store
from openkos.vcs import git as vcs_git

_T = TypeVar("_T")

app = typer.Typer()

# Every command sets `help=` and `rich_help_panel=` (#389).
#
# `help=` exists so Typer publishes THAT text instead of the raw `__doc__`.
# The docstrings keep their design, spec and issue references for
# maintainers; the published surface stays free of them. This is not only
# about `--help`: MCP tool descriptions are usually derived from the same
# source, and text that does not help a person will not help an agent.
#
# Panels group by WHAT THE READER IS TRYING TO DO -- not by write-ness, not
# by cost. "Get started" is the first hour. "Explore" asks the bundle
# questions. "Curate" decides things about its contents. "Maintain" tends
# the machinery behind it. "Remove" deletes. Use that rule when adding a
# command rather than matching the nearest-looking neighbour; before #389
# the listing was declaration order, which put `purge` -- irreversible and
# rare -- fourth, and `query`, the value moment, near the bottom.

# doctor and init's Ollama preflight are both fast interactive diagnostics:
# use a short timeout so a hung/firewalled host fails quickly instead of
# blocking on OllamaClient's DEFAULT_TIMEOUT.
_PREFLIGHT_TIMEOUT = 5.0


def _backend_factories() -> application_backends.BackendFactories:
    """Build this adapter's `BackendFactories` from THIS module's own
    globals, read at call time (issue #1057 Phase 9, design Decision 4):
    every chat AND embed construction site in `cli/main.py`/`cli/curate.py`
    goes through `application_backends.chat_client`/`embed_client` with
    this value, so patching `openkos.cli.main.OllamaClient`/
    `openkos.cli.main.OpenAICompatibleClient` (as
    `tests/unit/conftest.py`'s autouse network guard does) keeps
    intercepting every one of them -- the ~144 existing
    `openkos.cli.main.OllamaClient` test patches never had to move."""
    return application_backends.BackendFactories(
        ollama=OllamaClient, openai_compatible=OpenAICompatibleClient
    )


_INSECURE_KEY_WARNING_PRINTED = False
"""Module-level once-per-process guard (issue #1057 Phase 13b, design
Decision 8): a curate/ingest run can build many chat/embed clients in one
process, and printing `insecure_key_warning`'s advisory on every one would
drown it out. `tests/unit/conftest.py`'s autouse `_offline_ollama_by_default`
fixture resets this to `False` before every test, so it never leaks across
the test suite the way a bare process-lifetime flag normally would."""


_REMOTE_KEY_NOTICED: set[str] = set()
"""Origins whose non-local API-key notice was already printed this process:
one line per distinct host, however many clients are built. Reset by
`tests/unit/conftest.py` alongside `_INSECURE_KEY_WARNING_PRINTED`."""


def _echo_warning(message: str) -> None:
    """Render a warning a library function returned to us, on stderr -- the
    one place the CLI decides how those notes look. Passed as the
    `on_warning` callback of the `bundle` readers/writers, which never write
    to a stream themselves."""
    typer.echo(message, err=True)


def _echo_warning_once() -> Callable[[str], None]:
    """An `_echo_warning` that says each distinct message once per call
    site: a reader consulted once per candidate group would otherwise repeat
    the same note for every group."""
    seen: set[str] = set()

    def _emit(message: str) -> None:
        if message not in seen:
            seen.add(message)
            _echo_warning(message)

    return _emit


def _maybe_warn_insecure_key(cfg: config.Config) -> None:
    """Print `application_backends.insecure_key_warning(cfg)` to stderr, at
    most once per process, and one `remote_key_notices` line per distinct
    non-local origin the key is about to be sent to (an `http://` chat host
    gets only the stronger warning). Called from both `_chat_client` and
    `_embed_client` so every construction site is covered without each one
    remembering to call it itself."""
    global _INSECURE_KEY_WARNING_PRINTED
    if not _INSECURE_KEY_WARNING_PRINTED:
        warning = application_backends.insecure_key_warning(cfg)
        if warning is not None:
            _INSECURE_KEY_WARNING_PRINTED = True
            typer.echo(f"openkos: {warning}", err=True)
    for origin, message in application_backends.remote_key_notices(cfg):
        if origin in _REMOTE_KEY_NOTICED:
            continue
        _REMOTE_KEY_NOTICED.add(origin)
        typer.echo(f"openkos: {message}", err=True)


def _chat_client(cfg: config.Config, *, task: str | None = None) -> LLMBackend:
    """One-line delegator (mcp-read-surface slice 8, design Decision 7; issue
    #1057 Phase 9, design Decision 4): the real definition, and its full
    docstring, now live in `application/backends.py` -- moved there so a
    non-CLI adapter (the MCP server) can build a chat client without
    importing `openkos.cli`. Kept under this name, reading both concrete
    classes from THIS module's own globals at call time via
    `_backend_factories()`, so every existing test that patches
    `openkos.cli.main.OllamaClient` (most load-bearingly,
    `tests/unit/conftest.py`'s autouse network guard) keeps intercepting.

    Also prints the once-per-process insecure-key warning (issue #1057
    Phase 13b) before construction -- the warning is about how the client
    about to be built will send its key, not about the client itself."""
    _maybe_warn_insecure_key(cfg)
    return application_backends.chat_client(
        cfg, factories=_backend_factories(), task=task
    )


def _embed_client(cfg: config.Config) -> Embedder:
    """One-line delegator (issue #1057 Phase 10, design Decision 4): every
    embed construction site in `cli/main.py` goes through
    `application_backends.embed_client` with this module's own
    `_backend_factories()`, so patching `openkos.cli.main.OllamaClient`/
    `openkos.cli.main.OpenAICompatibleClient` (as `tests/unit/conftest.py`'s
    autouse network guard does) keeps intercepting every one of them --
    mirrors `_chat_client`'s existing shape and rationale exactly, including
    the once-per-process insecure-key warning (Phase 13b)."""
    _maybe_warn_insecure_key(cfg)
    return application_backends.embed_client(cfg, factories=_backend_factories())


# Shared remediation clause appended to the BackendUnavailable handlers of
# query, adjudicate, and suggest-relations -- kept as a single constant so
# the three verbs cannot drift from each other in wording.
_DOCTOR_HINT = " Or run `openkos doctor` to diagnose the environment."

_MAX_PICKER_ATTEMPTS = 3
"""Bounded reprompt count for `_pick_chat_model`'s numeric-choice loop --
an invalid answer reprompts up to this many times before the picker gives
up and silently falls back to `config.DEFAULT_MODEL`, so a non-interactive
or misbehaving stdin can never hang `init` forever (design D3)."""

# Uniform lock-contention message: `reindex_service`'s two error ladders
# (vectors/fts and graph) and `_guard_workspace_lock`'s catch-all for every
# other verb all format it, so a locked vectors.db/fts.db/graph.db/findings.db
# always reads identically regardless of which store hit the lock or which
# verb noticed (reindex-lock-handling, decision 5).
_LOCK_CONTENTION_TEMPLATE = reindex_service.LOCK_CONTENTION_TEMPLATE


def _version_line() -> str:
    """The single `openkos {version}` line emitted by both `--version` and
    `doctor`'s banner, read from installed distribution metadata (never from a
    constant, so it cannot drift from the built artifact). `PackageNotFoundError`
    -- realistically only a raw `sys.path` run with no install step -- degrades to
    `openkos unknown`: `unknown` cannot be misread as a released build the way
    `0.0.0-dev` would. Staleness (a bumped `pyproject.toml` without `uv sync`) is
    an explicit NON-GOAL: this reports what is installed, not what is checked out."""
    try:
        return f"openkos {_pkg_version('openkos')}"
    except PackageNotFoundError:
        return "openkos unknown"


def _version_callback(value: bool) -> None:
    """Eager `--version` handler: print and exit 0 before Typer resolves any
    subcommand, so the flag works standalone and outside a workspace."""
    if value:
        typer.echo(_version_line())
        raise typer.Exit(code=0)


@app.callback()
def callback(
    # `version` is never read in the body and looks dead, but it is load-bearing:
    # Typer derives the `--version` option from this signature, so deleting the
    # parameter deletes the flag. All the work happens in `_version_callback`,
    # which fires eagerly during parsing. Ruff's `ARG` rules are not enabled, so
    # nothing but this comment protects it from a well-meaning cleanup.
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the installed openkos version and exit.",
        is_eager=True,
        callback=_version_callback,
    ),
) -> None:
    """openkos: local-first engine that compiles text into a portable knowledge base."""
    logsetup.configure_logging("cli")


_READ_ONLY_COMMANDS = frozenset(
    {
        "status",
        "next",
        "list",
        "lint",
        "doctor",
        "mcp",
    }
)
"""The commands that never write to the workspace, and so take no lock (#925).

This list is the OPT-OUT side of a fail-safe classification: every command not
named here is locked. That direction is deliberate. A roster of things to
protect rots open -- a new mutating verb that nobody remembers to add races
silently, which is the same "green by absence" failure #928 describes for the
eval sweep. A roster of things to EXEMPT rots closed: the worst a forgotten
entry can do is make a read-only command wait.

`test_every_command_is_classified` asserts the two sides cover every registered
command, and that each name here is really a registered one, so neither side
can drift without a red test.
"""


_SELF_LOCKING_COMMANDS: frozenset[str] = frozenset()
"""Long-running commands that take the lock per unit of work (a commit phase
each), never for their own lifetime, so the guard must not wrap them. Empty
until such a command exists (`daemon`); a name here must be registered, and
no command is in two classes (`test_every_command_is_classified`)."""


_WAIT_PARAM = "wait"


def _add_wait_option(wrapper: Callable[..., object], fn: Callable[..., object]) -> None:
    """Give a locked verb's published signature `--wait <seconds>` (#1137).

    The option is added HERE, by the guard, so the set of verbs that accept it
    is exactly the set that is locked and cannot drift from it. Typer reads the
    wrapper's `__signature__` and annotations, not the wrapped function's, so
    the body never sees the parameter: the wrapper pops it before delegating.
    Click validates it, which is what makes a bad value a usage error (exit 2)
    before any work.
    """
    signature = inspect.signature(fn)
    if _WAIT_PARAM in signature.parameters:
        raise TypeError(f"{fn.__name__} already declares a {_WAIT_PARAM!r} parameter")
    option = inspect.Parameter(
        _WAIT_PARAM,
        inspect.Parameter.KEYWORD_ONLY,
        default=typer.Option(
            0,
            "--wait",
            min=0,
            max=lock_wait.MAX_WAIT_SECONDS,
            metavar="SECONDS",
            help=(
                "When another OpenKOS process holds the workspace lock, retry "
                "for up to this many seconds before refusing (exit 3). The "
                "default 0 refuses at once."
            ),
        ),
        annotation=int,
    )
    wrapper.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=[*signature.parameters.values(), option]
    )
    wrapper.__annotations__ = {**fn.__annotations__, _WAIT_PARAM: int}


_COMMIT_SECTION: contextvars.ContextVar[lock_wait.CommitSection | None] = (
    contextvars.ContextVar("openkos_commit_section", default=None)
)
"""The commit section a SPLIT verb's guard publishes for its body (#1137): the
section that takes the workspace lock under that invocation's `--wait`."""


def _commit_section_for(root: Path) -> lock_wait.CommitSection:
    """The commit section the running verb was given, or one with no wait when
    the body runs outside a guard (a direct call, a test)."""
    published = _COMMIT_SECTION.get()
    if published is not None:
        return published
    return lock_wait.locked_commit_section(root, wait_seconds=0)


def _guard_workspace_lock(
    command_name: str,
    *,
    commit_phase: bool = False,
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Hold the workspace's exclusive mutation lock for one command's body (#925).

    Applied UNDER `@app.command(...)`, so Typer registers the wrapper and reads
    its signature through `functools.wraps` -- options, arguments, and the
    published help are unchanged. It wraps the BODY, which is exactly the scope
    wanted: `openkos <verb> --help` never runs the body, so asking for help
    never takes the lock and never fails against a busy workspace.

    A run with no usable workspace is passed straight through, unlocked. The
    body's own `config.require_workspace` refusal is then the one the operator
    sees, so error precedence -- and the distinct wording #926 added for a
    symlinked workspace -- is unchanged by this decorator. `init` rides the
    same rule: there is no workspace to lock yet, and two concurrent inits are
    already refused by `write_config`'s exclusive create.

    Contention exits 3, not 1, because exit 3 is this CLI's documented
    RETRY-SAFE refusal (`docs/cli.md`, Conventions): nothing was written, and a
    plain re-run is exactly equivalent once the other process finishes. That is
    precisely a busy workspace's contract, so it reuses the code scripts
    already treat as retryable rather than inventing a second one.

    `commit_phase=True` marks a SPLIT verb (ADR-0036): the guard does not hold
    the lock around the body. It publishes a commit section (`_commit_section_for`)
    that takes the lock under this invocation's `--wait`, and the body enters it
    only around its commit phase, so extraction and every other slow step run
    unlocked. The refusal mapping below is unchanged: the section raises the same
    `WorkspaceBusyError`, mapped to the same exit codes.
    """

    def decorate(fn: Callable[..., _T]) -> Callable[..., _T]:
        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> _T:
            wait = cast(int, kwargs.pop(_WAIT_PARAM, 0))
            root = Path.cwd()
            if config.require_workspace(root) is not None:
                return fn(*args, **kwargs)

            def announce_wait() -> None:
                typer.echo(
                    f"openkos {command_name}: the workspace is busy; "
                    f"waiting up to {wait} s for it.",
                    err=True,
                )

            holder: AbstractContextManager[object]
            token: contextvars.Token[lock_wait.CommitSection | None] | None = None
            if commit_phase:
                token = _COMMIT_SECTION.set(
                    lock_wait.locked_commit_section(
                        root, wait_seconds=wait, on_wait=announce_wait
                    )
                )
                holder = nullcontext()
            else:
                holder = lock_wait.acquire_with_backoff(
                    root, wait_seconds=wait, on_wait=announce_wait
                )
            try:
                with holder:
                    return fn(*args, **kwargs)
            except lock.WorkspaceBusyError as exc:
                typer.echo(
                    f"openkos {command_name}: refusing to run -- {exc}.",
                    err=True,
                )
                raise typer.Exit(code=3) from exc
            except lock.WorkspaceLockUnavailableError as exc:
                # Exit 1, not 3: a re-run refuses again until the directory is
                # fixed, so the retry-safe code would be a false promise.
                typer.echo(
                    f"openkos {command_name}: refusing to run -- {exc}.",
                    err=True,
                )
                raise typer.Exit(code=1) from exc
            except sqlite3.OperationalError as exc:
                # The one place a derived store's lock contention (a writer or
                # opener still blocked after `busy_timeout`) becomes a
                # refusal, for every locked verb, so no verb has to remember
                # its own handler. A verb that handles the error itself
                # (`reindex`'s ladders, the persist-time advisories) never
                # reaches here. Any other operational failure is re-raised
                # unchanged.
                if not derived.is_lock_contention(exc):
                    raise
                typer.echo(
                    _LOCK_CONTENTION_TEMPLATE.format(command=command_name),
                    err=True,
                )
                raise typer.Exit(code=3) from exc
            finally:
                if token is not None:
                    _COMMIT_SECTION.reset(token)

        _add_wait_option(wrapper, fn)
        wrapper.__openkos_locked_command__ = command_name  # type: ignore[attr-defined]
        return wrapper

    return decorate


def _probe_installed_models() -> list[InstalledModel]:
    """Single reachability probe shared by BOTH the chat-model and the
    embedding-model pickers (spec: Graceful Degradation Of The Embedding
    Picker -- MUST reuse the chat picker's existing probe call, MUST NOT
    issue a second, separate reachability request). Wrapped in a broad
    `except Exception` so an unreachable Ollama server (or any other probe
    failure) never blocks `init`; returns an empty list on any failure,
    letting each picker fall back to its own default resolution
    independently."""
    try:
        probe = application_backends.diagnostics_client(
            None,
            model=config.DEFAULT_MODEL,
            timeout=_PREFLIGHT_TIMEOUT,
            factories=_backend_factories(),
        )
        return probe.list_models()
    except Exception:  # noqa: BLE001 -- an unreachable or misbehaving probe means no models, never a crash
        return []


def _resolve_model(flag: str | None, installed: list[InstalledModel]) -> str:
    """Resolve the model tag to write, precedence flag > interactive picker > default.

    `flag` (already the raw `--model` value, or `None` if not given) wins
    outright -- no prompt or picker is shown even on a TTY. Otherwise, if
    stdin is a TTY, `_pick_chat_model` offers a numbered list of `installed`
    chat models (falling back to a typed prompt if `installed` is empty or
    no chat model is among it). If stdin is not a TTY (e.g. piped, or a
    non-interactive CI run), neither prompt nor picker is shown and the
    default is used silently. Every path runs through `config.validate_model`,
    which raises `ValueError` for a blank or unsafe value -- callers must
    catch it before any file is written.

    `installed` is the single shared reachability probe's result (see
    `_probe_installed_models`), passed in rather than probed here again.
    """
    if flag is not None:
        return config.validate_model(flag)
    if sys.stdin.isatty():
        return _pick_chat_model(installed)
    return config.DEFAULT_MODEL


def _is_selectable_model_tag(tag: str) -> bool:
    """`True` iff `tag` would pass `config.validate_model` -- used to drop a
    server-reported tag the picker could otherwise list but that would
    hard-fail `init` the moment the user selected it."""
    try:
        config.validate_model(tag)
        return True
    except ValueError:
        return False


def _pick_chat_model(installed: list[InstalledModel]) -> str:
    """Interactive numbered picker over Ollama's installed chat models.

    `installed` comes from the shared `_probe_installed_models` probe (run
    strictly before any workspace write, Phase A) -- this function issues no
    reachability request of its own. An empty `installed` list (probe
    failed, or Ollama reported nothing) falls back to the pre-picker typed
    prompt below (spec: Graceful Degradation). Embedding models (per
    `is_embedding_model`) are excluded from the candidate list; if that
    leaves zero chat models, the picker falls back the same way.
    `config.DEFAULT_MODEL` is always listed first, marked "(recommended)"
    -- prepended if the probe didn't report it installed.

    `typer.prompt("Model", default=str(<recommended index>))` reads the
    choice: pressing Enter re-supplies that default, so it resolves to the
    recommended tag with no special-casing needed. An in-range digit picks
    that list entry; anything else reprompts, up to `_MAX_PICKER_ATTEMPTS`
    times, rather than silently accepting garbage or hanging forever on a
    misbehaving/non-interactive stdin -- after which it falls back to
    `config.DEFAULT_MODEL`. The final choice is still validated by
    `config.validate_model`, same as every other `_resolve_model` path.
    """
    candidates = [
        m.tag
        for m in installed
        if not is_embedding_model(m) and _is_selectable_model_tag(m.tag)
    ]

    if not candidates:
        return config.validate_model(
            typer.prompt("Model", default=config.DEFAULT_MODEL)
        )

    if config.DEFAULT_MODEL in candidates:
        candidates.remove(config.DEFAULT_MODEL)
    candidates.insert(0, config.DEFAULT_MODEL)

    typer.echo("Installed chat models:")
    for index, tag in enumerate(candidates, start=1):
        suffix = " (recommended)" if tag == config.DEFAULT_MODEL else ""
        typer.echo(f"  {index}) {tag}{suffix}")

    for _ in range(_MAX_PICKER_ATTEMPTS):
        choice = typer.prompt("Model", default="1")
        if (
            choice.isascii()
            and choice.isdigit()
            and 1 <= int(choice) <= len(candidates)
        ):
            return config.validate_model(candidates[int(choice) - 1])
        typer.echo(
            f"openkos init: '{choice}' isn't a valid choice -- enter a "
            f"number from 1 to {len(candidates)}, or press Enter for the "
            "recommended model.",
            err=True,
        )

    return config.validate_model(config.DEFAULT_MODEL)


def _canonical_allowlist_spelling(tag: str) -> str:
    """Return the `EMBEDDING_MODEL_ALLOWLIST` spelling `tag` names, or `tag`.

    One source of truth for D3 normalization, shared by the flag path and the
    picker so the two entry points can never disagree about whether a value
    is on the allowlist. A tag matching nothing is returned unchanged -- D6's
    off-allowlist escape hatch stays verbatim, never coerced."""
    for allowed in config.EMBEDDING_MODEL_ALLOWLIST:
        if model_tag_matches(allowed, [tag]):
            return allowed
    return tag


def _resolve_embedding_model(flag: str | None, installed: list[InstalledModel]) -> str:
    """Resolve the embedding model tag to write, precedence flag >
    interactive picker over the vetted allowlist > `DEFAULT_EMBEDDING_MODEL`.

    `flag` wins outright -- validated for YAML-safety via
    `config.validate_embedding_model` but NOT gated on
    `config.EMBEDDING_MODEL_ALLOWLIST` membership (D6): an off-allowlist
    value is still written, with a non-fatal stderr warning (see
    `init`). Otherwise, if stdin is a TTY, `_pick_embedding_model` offers a
    numbered list of `installed` models that are ALSO on the allowlist
    (falling back to the silent default if none qualify). If stdin is not a
    TTY, no prompt or picker is shown and the default is used silently.

    `installed` is the single shared reachability probe's result (see
    `_probe_installed_models`) -- this resolver never issues its own
    reachability request.

    A flag value that MATCHES an allowlist entry under `model_tag_matches`
    normalization resolves to the allowlist spelling, exactly as the picker
    does (D3): `--embedding-model bge-m3:latest` names the vetted model, so
    writing the raw server-style tag would make `cfg.embedding_model` differ
    from `DEFAULT_EMBEDDING_MODEL` and trip the model-tag re-embed gate into
    a full corpus re-embed for a no-op change. This is canonicalizing one
    model's spelling, NOT the silent coercion to the default that D6
    forbids -- an off-allowlist value still matches nothing here and is
    written verbatim.
    """
    if flag is not None:
        validated = config.validate_embedding_model(flag)
        return _canonical_allowlist_spelling(validated)
    if sys.stdin.isatty():
        return _pick_embedding_model(installed)
    return config.DEFAULT_EMBEDDING_MODEL


def _pick_embedding_model(installed: list[InstalledModel]) -> str:
    """Interactive numbered picker over the vetted embedding-model allowlist.

    Candidates are filtered on `config.EMBEDDING_MODEL_ALLOWLIST` ALONE --
    deliberately NOT `is_embedding_model(m) and allowlisted` (D2): `bge-m3`
    has no `embed` substring in its tag, so `_EMBEDDING_TAG_MARKER` never
    fires for it, and it classifies as an embedding model only via
    `family == "bert"`. An installed entry reporting no `details.family`
    (`InstalledModel(tag="bge-m3", family=None)`) would silently drop the
    recommended default from its own picker if the heuristic classifier
    were stacked on top of the allowlist -- the allowlist is stronger
    evidence on its own and must gate alone.

    Matching uses `ollama.model_tag_matches` (D3): a server-reported
    `bge-m3:latest` still matches the allowlisted `bge-m3` entry via
    Ollama's `:latest` normalization, and the ALLOWLIST spelling -- never
    the raw server tag -- is what gets listed/written, so `cfg.
    embedding_model` never diverges from `DEFAULT_EMBEDDING_MODEL` for a
    no-op selection.

    Graceful degradation (spec: Graceful Degradation Of The Embedding
    Picker) differs from the chat picker: an empty `installed` list or zero
    allowlisted candidates falls back SILENTLY to `DEFAULT_EMBEDDING_MODEL`
    -- no prompt of any kind, unlike `_pick_chat_model`'s typed-prompt
    fallback -- since a fresh workspace has nothing to lose by a silent
    default and the spec explicitly forbids a second reachability request
    just to ask a question the user never opted into.
    """
    installed_tags = [m.tag for m in installed]
    candidates = [
        allowed
        for allowed in config.EMBEDDING_MODEL_ALLOWLIST
        if model_tag_matches(allowed, installed_tags)
    ]

    if not candidates:
        return config.DEFAULT_EMBEDDING_MODEL

    if config.DEFAULT_EMBEDDING_MODEL in candidates:
        candidates.remove(config.DEFAULT_EMBEDDING_MODEL)
    candidates.insert(0, config.DEFAULT_EMBEDDING_MODEL)

    typer.echo("Installed embedding models:")
    for index, tag in enumerate(candidates, start=1):
        suffix = " (recommended)" if tag == config.DEFAULT_EMBEDDING_MODEL else ""
        typer.echo(f"  {index}) {tag}{suffix}")

    for _ in range(_MAX_PICKER_ATTEMPTS):
        choice = typer.prompt("Embedding model", default="1")
        if (
            choice.isascii()
            and choice.isdigit()
            and 1 <= int(choice) <= len(candidates)
        ):
            return config.validate_embedding_model(candidates[int(choice) - 1])
        typer.echo(
            f"openkos init: '{choice}' isn't a valid choice -- enter a "
            f"number from 1 to {len(candidates)}, or press Enter for the "
            "recommended embedding model.",
            err=True,
        )

    return config.validate_embedding_model(config.DEFAULT_EMBEDDING_MODEL)


def _commit_has_confidential(root: Path, paths: Sequence[str]) -> bool:
    """`True` iff any of the given (workspace-relative, POSIX) `paths` is a
    concept file whose frontmatter `sensitivity` equals the canonical top
    rank (`okf.SENSITIVITY_ORDER[-1]`, `"confidential"`) -- design: "Confidential
    detection reads frontmatter, not `blocks_llm_send`". `blocks_llm_send`
    is a FAIL-CLOSED gate that also treats a missing/blank/unreadable
    `sensitivity` as confidential; this predicate is transparency, not a
    security gate, so it looks for an EXPLICIT `confidential` value only --
    a source with no `sensitivity` field must never trigger a false
    "confidential committed" alarm.

    `bundle/index.md`/`bundle/log.md` (catalog files, never concept
    frontmatter), any path under `raw/` (source copies, not concept
    documents), and any path missing on disk (a staged deletion has
    nothing to read) are all skipped without raising."""
    reserved = {"bundle/index.md", "bundle/log.md"}
    for rel_path in paths:
        if rel_path in reserved or rel_path.startswith("raw/"):
            continue
        file_path = root / rel_path
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
            metadata, _ = okf.load_frontmatter(text)
        except (OSError, ValueError):
            continue
        if str(metadata.get("sensitivity", "")).strip() == okf.SENSITIVITY_ORDER[-1]:
            return True
    return False


def _snapshot_read(path: Path) -> tuple[bytes, str]:
    """Delegates to `fsio.snapshot_read` (issue #918 Slice 1, design D5),
    promoted there because `application/lifecycle.py`'s `prepare_merge`
    needs it and cannot import `openkos.cli` (the layering invariant).
    Kept here as a one-line delegator, not deleted, because ~10 unrelated
    verbs (`ingest`, `relate`, `set-sensitivity`, `query --save`, ...)
    still call this name directly -- mirrors `_slugify`'s own delegation to
    `bundle.source_titles.slugify`."""
    return fsio.snapshot_read(path)


def _excise_merged_sections(snapshot: str, purge_ids: set[str]) -> str:
    """Remove every purge-set member's delimited `## Merged content (<id>)`
    section from a ledger snapshot string (issue #602, leak 1).

    `build_merged_document` APPENDS `{MERGED_CONTENT_HEADING_PREFIX}{id})`
    plus the absorbed body, so entry *k*'s `survivor_before` (and the
    `absorbed_snapshot` of any merge that later absorbed that survivor)
    embeds every earlier absorbed body under a deterministic, delimited
    heading. Excision runs from that exact heading to the NEXT merged-
    content heading (or EOF) -- a nested section belonging to a DIFFERENT,
    non-forgotten concept is therefore never removed, only the purge
    member's own segment. Structural, never a substring match on body
    text: a generic body (`Body.`) contained coincidentally in an
    unrelated snapshot must not vaporize that entry's restore data."""
    for purge_id in purge_ids:
        separator = f"{okf.merged_content_heading(purge_id)}\n\n"
        while True:
            start = snapshot.find(separator)
            if start == -1:
                break
            next_heading = snapshot.find(
                okf.MERGED_CONTENT_HEADING_PREFIX, start + len(separator)
            )
            end = len(snapshot) if next_heading == -1 else next_heading
            snapshot = snapshot[:start] + snapshot[end:]
    return snapshot


def _scrub_referring_bullets(snapshot: str, purge_ids: set[str]) -> str:
    """Drop every bullet in a ledger snapshot whose FIRST markdown link (or
    `(id: <x>)` anchor) resolves to a purge-set member (issue #689).

    The companion to `_excise_merged_sections`, covering the OTHER way a
    member's identity enters a surviving entry's snapshots: not as absorbed
    body text under a `## Merged content (<id>)` delimiter, but as an
    ordinary REFERENCE -- a `## Related` bullet in a snapshotted concept
    body, a catalog bullet in `index_before`, an `**Ingest**: Extracted
    [...]` line or a `forget` tombstone in `log_before`. Each carries the
    member's TITLE, its one-line description and a link to its former path,
    which is precisely the fragment an erasure exists to remove.

    REUSES `bundle.log.remove_log_entry` rather than re-implementing the
    match: it is the strict superset of `bundle.index.remove_index_entry`'s
    matcher (it imports that module's `_LINK_RE`, `_BULLET_MARKERS` and
    `_link_identity`, and adds the tombstone anchor on top) and, unlike
    `remove_index_entry`, needs no frontmatter split -- so it applies
    uniformly to concept bodies, `index.md` and `log.md` snapshots alike,
    and cannot raise on a snapshot whose frontmatter is malformed.

    Structural, never a substring match: a bullet is dropped on resolved
    LINK IDENTITY, so a bullet that merely MENTIONS a purged title in its
    description text is left alone, and a YAML `- ` sequence item in a
    snapshot's frontmatter (which carries neither a markdown link nor an
    anchor) can never match."""
    for purge_id in purge_ids:
        snapshot, _ = bundle_log.remove_log_entry(snapshot, purge_id)
    return snapshot


def _scrub_entry_snapshots(
    entry: okf.MergeLedgerEntry,
    purge_ids: set[str],
    *,
    prior_absorbed_ids: frozenset[str] = frozenset(),
) -> okf.MergeLedgerEntry:
    """Scrub one SURVIVING ledger entry's snapshot fields of every purge-set
    member's content (issue #602; forget-command spec: "Deletion Sweep
    Includes Ledger Storage" enumerates all six fields).

    Three structural scrubs, matching the mechanisms by which a member's
    body or IDENTITY ever enters another entry's snapshots:

    - the delimited `## Merged content (<id>)` section that
      `build_merged_document`'s append accumulates into later
      `survivor_before`/`absorbed_snapshot` strings (`_excise_merged_sections`;
      applied to all four whole-file string fields for uniformity --
      `index_before`/`log_before` are catalog files that never carry one,
      so there the excision is a provable no-op);
    - a `relation_rewrites`/`provenance_rewrites` element whose `file` IS
      the member's own file (the member was snapshotted whole as a third
      party whose `relations:`/`provenance:` targeted the absorbed id) --
      dropped outright; a KEPT rewrite's snapshot is still excised, since a
      third party that was itself a survivor embeds merged sections too;
    - a REFERENCE bullet naming the member -- a `## Related` link in a
      snapshotted body, a catalog bullet in `index_before`, an ingest line
      or tombstone in `log_before` -- dropped by `_scrub_referring_bullets`
      (#689). This is the leak an absorbed-body excision structurally
      cannot reach: the member was never absorbed by THIS survivor, merely
      linked from it, so no delimiter marks it and the entry itself is
      rightly kept.

    Returns the entry unchanged (same object) when nothing matched, so the
    caller's no-op detection stays cheap and a byte-identical rewrite is
    never performed.

    Carried-content redaction (#667): a #645-reconciled merge weaves an
    absorbed body into the live survivor WITHOUT the delimiter, so a LATER
    merge's `survivor_before` embeds it where the structural excision above
    cannot reach. Two detection channels, evaluated BEFORE excision (the
    excision itself removes the delimiter this decision reads):

    - a V4 entry's own `carried_content_ids` annotation, recorded by
      `plan_merge` at snapshot time (authoritative for V4);
    - for pre-V4 entries only, the conservative history fallback: a purge
      id in `prior_absorbed_ids` (absorbed EARLIER into this same sidecar)
      whose delimited section is absent from this entry's ORIGINAL
      `survivor_before`.

    On a hit, `survivor_before` is replaced wholesale with
    `okf.REDACTED_SNAPSHOT_SENTINEL` -- privacy over reversibility, #602's
    own rule; `plan_unmerge` refuses the sentinel rather than restoring
    it. The entry's other fields keep the ordinary structural scrub."""

    def _keeps(file: str) -> bool:
        return unicodedata.normalize("NFC", file.removesuffix(".md")) not in purge_ids

    carried_hit = any(
        carried in purge_ids for carried in entry.carried_content_ids
    ) or (
        entry.schema != okf.MERGE_LEDGER_SCHEMA_V4
        and any(
            okf.merged_content_heading(purge_id) not in entry.survivor_before
            for purge_id in purge_ids & prior_absorbed_ids
        )
    )

    def _scrub(snapshot: str) -> str:
        """Both structural scrubs, in the order they must run: absorbed BODY
        text under its delimiter first (#602), then any surviving REFERENCE
        bullet naming a purge-set member (#689)."""
        return _scrub_referring_bullets(
            _excise_merged_sections(snapshot, purge_ids), purge_ids
        )

    if carried_hit:
        survivor_before = okf.REDACTED_SNAPSHOT_SENTINEL
    else:
        survivor_before = _scrub(entry.survivor_before)
    absorbed_snapshot = _scrub(entry.absorbed_snapshot)
    index_before = _scrub(entry.index_before)
    log_before = _scrub(entry.log_before)
    # #758: a V5 entry keeps no catalog snapshots -- its catalog data is the
    # recorded delta, and it is swept by the SAME structural matcher. A
    # restore is dropped whole rather than blanked, on either of its two
    # fields: `line` IS the member's own catalog bullet (title, description,
    # link), and `preceded_by` is a verbatim copy of a NEIGHBOURING
    # concept's bullet, which carries that concept's data just as fully when
    # it is the one being forgotten. Dropping it costs the ability to put
    # that bullet back, which is the trade the spec already names -- privacy
    # over reversibility (#602's rule) -- and the surgical reversal then
    # restores the rest of the catalog without the forgotten line rather
    # than refusing outright.
    index_restores = [
        restore
        for restore in entry.index_restores
        if _scrub_referring_bullets(restore.line, purge_ids) == restore.line
        and _scrub_referring_bullets(restore.preceded_by, purge_ids)
        == restore.preceded_by
    ]
    relation_rewrites = [
        dataclasses.replace(rewrite, snapshot=_scrub(rewrite.snapshot))
        for rewrite in entry.relation_rewrites
        if _keeps(rewrite.file)
    ]
    provenance_rewrites = [
        dataclasses.replace(rewrite, snapshot=_scrub(rewrite.snapshot))
        for rewrite in entry.provenance_rewrites
        if _keeps(rewrite.file)
    ]
    scrubbed = dataclasses.replace(
        entry,
        survivor_before=survivor_before,
        absorbed_snapshot=absorbed_snapshot,
        index_before=index_before,
        log_before=log_before,
        index_restores=index_restores,
        relation_rewrites=relation_rewrites,
        provenance_rewrites=provenance_rewrites,
    )
    return entry if scrubbed == entry else scrubbed


def _sweep_ledger_sidecars_for_ids(
    bundle_dir: Path, purge_ids: Iterable[str]
) -> list[Path]:
    """Privacy sweep of `bundle/.state/ledger/` for `purge_ids` membership
    (forget-command spec: "Deletion Sweep Includes Ledger Storage";
    privacy-purge spec: "Whole-History Expunge Covers The Ledger Sidecar
    Store" -- shared by `forget`'s and `purge`'s own Phase B, so the sweep
    is written exactly once):

    - Each purge-set member's OWN ledger sidecar (if it is/was itself a
      merge survivor) is deleted OUTRIGHT -- the member's own merge history
      is no longer meaningful once the member itself is gone.
    - Every OTHER live sidecar has any entry whose `absorbed_id` is in
      `purge_ids` dropped (`ledger.write_entries` with the remaining
      entries, or removed entirely when none remain) -- so a purge-set
      member's pre-merge body does not survive merely because it was
      absorbed into a DIFFERENT survivor that is not itself being
      forgotten/purged.
    - Every SURVIVING entry is additionally scrubbed via
      `_scrub_entry_snapshots` (issue #602): dropping entry *k* alone is
      not enough, because `build_merged_document` APPENDS -- entry *k+1*'s
      `survivor_before` already embeds the *k* bodies absorbed before it,
      and a member's whole file can sit in a THIRD survivor's entry as a
      `relation_rewrites`/`provenance_rewrites` snapshot under an
      unrelated `absorbed_id`. Privacy over reversibility, by design: a
      later `unmerge` over a scrubbed entry either restores content
      WITHOUT the forgotten body or refuses on its drift check -- both
      acceptable; resurrecting the forgotten body is not.

    Returns every ledger path touched (deleted, or rewritten), bundle-dir-
    relative-capable via the caller, so it can be folded into the SAME
    Phase B write/`_autocommit` the concept-file deletion already uses --
    never a second, independent write pass.

    A sidecar whose `survivor_id` field is missing or non-string is skipped
    defensively rather than guessed at -- this sweep only ever REMOVES
    content, so a malformed sidecar it cannot safely identify is left for
    `doctor` to flag, not silently rewritten under an invented id."""
    purge_ids_set = set(purge_ids)
    touched: list[Path] = []
    deleted: set[Path] = set()
    for member in sorted(purge_ids_set):
        own_path = bundle_ledger.ledger_path_for(member, bundle_dir)
        if own_path.is_file():
            fsio.remove_file(own_path)
            touched.append(own_path)
            deleted.add(own_path)
    for ledger_path in bundle_ledger.iter_ledgers(bundle_dir):
        if ledger_path in deleted:
            continue
        metadata, _ = okf.load_frontmatter(ledger_path.read_text(encoding="utf-8"))
        survivor_id = metadata.get("survivor_id")
        if not isinstance(survivor_id, str) or not survivor_id:
            continue
        entries = okf.decode_merged_from(metadata)
        # `prior` accumulates EVERY earlier entry's absorbed id -- dropped
        # ones included, since their content contributed to later snapshots
        # regardless of whether their own entry survives this sweep (#667's
        # pre-V4 history fallback reads this set).
        remaining = []
        prior: set[str] = set()
        for e in entries:
            if e.absorbed_id not in purge_ids_set:
                remaining.append(
                    _scrub_entry_snapshots(
                        e, purge_ids_set, prior_absorbed_ids=frozenset(prior)
                    )
                )
            prior.add(e.absorbed_id)
        if remaining == entries:
            continue
        # Write back to the WALKED path, never to a path rebuilt from the
        # sidecar's own `survivor_id` content: a drifted or hostile id must
        # not steer this rewrite off the file it came from (path traversal +
        # silent scrub miss). `survivor_id` is content only.
        bundle_ledger.rewrite_entries_at(
            ledger_path, survivor_id=survivor_id, entries=remaining
        )
        touched.append(ledger_path)
    return touched


def _sweep_findings_for_ids(
    layout: config.WorkspaceLayout, purge_ids: Iterable[str]
) -> None:
    """Privacy sweep of `.openkos/findings.db` for `purge_ids` membership
    (#685 item 1; forget-command spec: "Deletion Sweep Includes Persisted
    Findings"): `finding_claims` persists verbatim claim text quoted from
    concept bodies, so a finding referencing a purge-set member must not
    survive the member -- the same class of leak the ledger
    (`_sweep_ledger_sidecars_for_ids`, #602/#667) and decisions
    (`_sweep_decisions_for_ids`) sweeps already close, one store over.

    `forget`-only: `purge` deletes `findings.db` wholesale in
    `_purge_rebuild_indexes`, a strictly stronger erasure.

    A missing store is a no-op (never created here --
    `findings_db_path`'s pure-derivation contract). A corrupt, locked, or
    unreadable store degrades to one LOUD stderr warning naming the
    residue and the remedy rather than aborting a forget whose bundle
    writes already landed -- a privacy scrub that fails silently would be
    worse than one that fails out loud, and `findings.db` is recomputable
    derived state."""
    # `stat()`, never `exists()`, for the presence probe (review lineage
    # review-66cd062e562f43bb, R3): the two answers this sweep must tell
    # apart are "never persisted anything" (silent no-op, per
    # `findings_db_path`'s pure-derivation contract) and "present but
    # unreachable" (loud warning -- there may be residue nobody can
    # scrub). `exists()` collapses them, and WHICH errnos it collapses is
    # version-dependent: 3.12/3.13 re-raise EACCES while 3.14 suppresses
    # it, so the same unreadable store aborted forget on one interpreter
    # and vanished silently on another. `stat()` never suppresses, so the
    # two cases stay distinguishable on every supported interpreter.
    try:
        try:
            layout.findings_db_path.stat()
        except FileNotFoundError:
            return
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            findings.delete_findings_referencing(conn, set(purge_ids))
            # #779: the adjudications tables are the same file's second
            # tenant and their rationales can quote member bodies verbatim
            # -- same sweep, same erasure discipline, same connection.
            adjudications_store.delete_adjudications_referencing(conn, set(purge_ids))
            # #799: the edge_suggestions tables are the same file's THIRD
            # tenant and their rationales quote both endpoints' bodies --
            # same sweep, same erasure discipline, same connection.
            edge_suggestions_store.delete_edge_suggestions_referencing(
                conn, set(purge_ids)
            )
            # #1014 Phase B P3: the revision_findings tables are the same
            # file's FOURTH tenant, and a finding's rationale/quotes can
            # embed verbatim text from either Decision's body (or a Source
            # either reaches) -- same sweep, same erasure discipline, same
            # connection.
            revision_findings_store.delete_revision_findings_referencing(
                conn, set(purge_ids)
            )
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        typer.echo(
            "openkos forget: warning -- failed to sweep persisted findings/"
            "adjudications/edge suggestions/revision findings "
            f"({exc}); '.openkos/findings.db' "
            "may still quote the forgotten concept(s). Delete the file to "
            "clear the residue (all four stores are recomputable at LLM "
            "cost).",
            err=True,
        )


def _sweep_decisions_for_ids(bundle_dir: Path, purge_ids: Iterable[str]) -> list[Path]:
    """Privacy sweep of `bundle/.state/decisions/` for `purge_ids`
    membership (privacy-purge spec: "Whole-History Expunge Covers The
    Pending-Work Decision Subtree"; forget-command spec: "Forget Sweeps
    Live Decision Entries Referencing The Purge Set" -- shared by
    `forget`'s and `purge`'s own Phase B, mirroring
    `_sweep_ledger_sidecars_for_ids`'s two-branch shape exactly, one
    primitive written once):

    - Each purge-set member's OWN decisions sidecar
      (`bundle.decisions.decisions_path_for(member, bundle_dir)`) is
      deleted OUTRIGHT -- once the concept itself is gone, decisions keyed
      on it (`pair_ids[0] == member`) are meaningless.
    - Every OTHER live decisions sidecar has any record whose `pair_ids`
      or `merged_absorbed_id` names a purge-set member dropped
      (`bundle.decisions.write_decisions` with the remaining records, or
      the file removed entirely when none remain) -- so a purge-set
      member's participation in a contradiction decision does not survive
      merely because the record lives under a DIFFERENT (live) concept's
      sidecar.

    Returns every decisions path touched (deleted, or rewritten),
    bundle-dir-relative-capable via the caller, so it can be folded into
    the SAME Phase B write/`_autocommit` the concept-file deletion already
    uses -- never a second, independent write pass.

    For `purge`, `_decisions_history_targets` ALSO puts every one of these
    same paths into `expunge_targets` before the `git filter-repo` pass
    (a stronger guarantee than the ledger sweep's own-file-only history
    coverage, per the privacy-purge spec delta); this function is the
    LIVE-tree half of that coverage, and it is the ENTIRE sweep for
    `forget`, which performs no history rewrite at all.

    A sidecar whose `concept_id` field is missing or non-string is skipped
    defensively rather than guessed at, matching
    `_sweep_ledger_sidecars_for_ids`'s own defensive posture."""
    purge_ids_set = set(purge_ids)
    touched: list[Path] = []
    deleted: set[Path] = set()
    for member in sorted(purge_ids_set):
        own_path = bundle_decisions.decisions_path_for(member, bundle_dir)
        if own_path.is_file():
            fsio.remove_file(own_path)
            touched.append(own_path)
            deleted.add(own_path)
    for decisions_path in bundle_decisions.iter_decisions(bundle_dir):
        if decisions_path in deleted:
            continue
        metadata, _ = okf.load_frontmatter(decisions_path.read_text(encoding="utf-8"))
        concept_id = metadata.get("concept_id")
        if not isinstance(concept_id, str) or not concept_id:
            continue
        # Read AND rewrite the WALKED path, never a path rebuilt from the
        # sidecar's own `concept_id` content (path traversal + silent scrub
        # miss). `concept_id` is preserved as container content only.
        records = bundle_decisions.read_decisions_at(decisions_path)
        remaining = [
            record
            for record in records
            if record.pair_ids[0] not in purge_ids_set
            and record.pair_ids[1] not in purge_ids_set
            and record.merged_absorbed_id not in purge_ids_set
        ]
        # #797: the identity list is swept on its own terms -- a
        # keep-distinct ruling names EVERY member, so any member landing in
        # the purge set drops the whole record.
        identity_records = bundle_decisions.read_identity_decisions_at(
            decisions_path, on_warning=_echo_warning
        )
        identity_remaining = [
            record
            for record in identity_records
            if not any(member in purge_ids_set for member in record.member_ids)
        ]
        if len(remaining) == len(records) and len(identity_remaining) == len(
            identity_records
        ):
            continue
        bundle_decisions.rewrite_both_at(
            decisions_path,
            concept_id=concept_id,
            records=remaining,
            identity_records=identity_remaining,
        )
        touched.append(decisions_path)
    return touched


def _reject_drifted_targets(
    layout: config.WorkspaceLayout,
    expected: Mapping[Path, bytes],
    verb: str,
    *,
    deletes: AbstractSet[Path] = frozenset(),
    remedy: str | None = None,
    hint: str | None = None,
) -> None:
    """Refuse the whole run (exit 3, nothing written, nothing deleted) when
    any target this run intends to WRITE or UNLINK changed on disk after
    the plan was computed from it (issues #306, #313, #319, #329).

    The decision -- and the full account of the three drift buckets, the
    snapshot-bytes pairing, `deletes`, `remedy` and `hint` -- lives in
    `application.drift.describe_drift`; this wrapper only prints its
    message and exits. Exit code 3, and only here (#319): a drift refusal
    is the ONE failure a script may safely retry when the message says so,
    while every other failure keeps exit 1. Every caller invokes this
    strictly AFTER its confirm gate and strictly BEFORE its first write,
    unconditionally -- `--auto` and `review: false` skip the prompt but not
    the window it stood in."""
    message = application_drift.describe_drift(
        layout, expected, verb, deletes=deletes, remedy=remedy, hint=hint
    )
    if message is None:
        return
    typer.echo(message, err=True)
    raise typer.Exit(code=3)


def _require_member_baseline(
    verb: str, other_bytes: Mapping[str, bytes], member: str
) -> bytes:
    """A purge-set member's Phase-A snapshot bytes, or a clean exit-3
    refusal when the scan somehow produced none.

    DEFENSIVE-ONLY, deliberately: today `purge_ids` and `other_bytes` are
    built from the SAME bundle scan, and Phase A's own `member_texts`
    lookup would have crashed on the missing key long before the guard
    mapping is built -- so this branch cannot be reached end-to-end, and no
    integration test contorts the suite to pretend it can; the helper's
    unit tests pin the behavior directly instead. It exists because both
    callers used to index `other_bytes[f"{member}.md"]` bare, which a
    future refactor computing `purge_ids` from anything other than the
    scanned files would turn into a `KeyError` traceback in the middle of
    the post-confirm gate. A member with no same-observation baseline
    (#318) cannot be validated against drift, so the fail-closed answer is
    the guard's own shape: refuse the whole run (exit 3), name the member,
    write nothing. `_reject_drifted_targets`' contract is untouched --
    this refusal fires while its mapping is being BUILT, before the guard
    ever sees it.
    """
    baseline = other_bytes.get(f"{member}.md")
    if baseline is None:
        typer.echo(
            f"openkos {verb}: refusing to write -- 'bundle/{member}.md' is "
            "in the delete plan but has no Phase-A snapshot to validate "
            "against, so post-confirm drift on it cannot be ruled out. "
            "Nothing was written. Re-run to recompute over the current "
            "bundle.",
            err=True,
        )
        raise typer.Exit(code=3)
    return baseline


def _echo_commit_disclosure(sha: str, *, prefix: str = "") -> None:
    """Print the ONE sentence naming a commit openkos just wrote and the way
    back out of it (issue #800).

    `forget`, `merge` and `curate` share this instead of building five
    f-strings of their own. They are the verbs whose writes a user most
    often wants back -- a deleted concept, a document folded into another, a
    batch of applied relations -- and none of them had any way of saying
    that the engine had already made the undo available. There is no shared
    renderer for those verbs' other trailing success lines, so each of the
    five call sites would have spelled this one by hand and the five
    spellings would have drifted; one literal here is what makes that
    impossible.

    Callers pass `prefix` to place the line in their own output: the
    top-level verbs use their `openkos <verb>: ` prefix, and `curate`'s
    per-item stages use the two-space indent their `  rationale:` lines
    already use, since their commit is per accepted item.

    Only ever called with a real sha. `_autocommit` returns `None` on every
    degradation (not a repo, identity unset, commit failed, sha unreadable),
    and a caller that printed this anyway would be pointing the user at a
    commit that does not exist.

    `sha` stays NON-OPTIONAL, and each of the five call sites keeps its own
    `if <x>_sha is not None:` (issue #817, item 3, decided deliberately).
    Widening the parameter to `str | None` with an early return would
    delete those five lines, and the reason not to is not style: `sha: str`
    is a contract the TYPE CHECKER enforces. `_autocommit` returns
    `str | None`, so a caller that forgets the check is an `arg-type` error
    today, and mypy would accept that same forgetful caller in silence the
    moment this accepted `str | None` -- trading a machine-checked
    guarantee for five lines. The guard also puts the degradation where the
    output is composed, which is where a reader looks to see what a verb
    does and does not print."""
    typer.echo(f"{prefix}committed as {sha} -- undo with `git revert {sha}`.")


def _autocommit(root: Path, paths: Sequence[str], message: str) -> str | None:
    """Best-effort, non-fatal auto-commit after a mutating verb's Phase B
    (git-lifecycle Slice 2), structurally cloned from `init`'s own
    best-effort git-setup block below. Every mutating verb calls this
    exactly once, on the success path -- `grep _autocommit(` is exact and
    never goes stale, where the enumeration this sentence replaces had
    quietly stopped at six callers while the real list kept growing past
    it (the same rot a count written here would start over) -- strictly
    AFTER its own confirm gate and Phase-B writes
    have already landed on disk -- so no failure mode here ever changes
    the caller's exit code or leaves a canonical write unfinished; the
    worst outcome is a stderr WARNING pointing at `git status`.

    `paths` MUST be workspace-relative, POSIX paths, taken literally (a
    name containing `[`, `*` or `?` is a name, not a glob). Staging and the
    commit both go through `commit_paths`' pathspec (`git add -- <paths>`
    then `git commit -- <paths>`, never `-A`/`-a`), so the commit contains
    ONLY `paths`: an unrelated file elsewhere in the workspace, whether
    unstaged OR already staged by the user (or an editor plugin), is neither
    committed nor unstaged. A `git add`/`git commit` that hangs on a prompt
    is abandoned after a bounded timeout and reported as the same WARNING
    as any other failure.

    Returns the new commit's abbreviated sha, or `None` on EVERY degradation
    path -- not a repository, identity unset, `commit_paths` raising, or a
    sha that could not be read back (issue #800). The value is what lets a
    caller name the commit and the `git revert` that undoes it; `None` means
    there is no commit to name, so a caller must print nothing rather than
    invent one. Every pre-existing caller ignores the return value, so this
    is purely additive."""
    repo = vcs_git.repo_root(root)
    if repo is None:
        typer.echo(
            "openkos: WARNING -- not a git repository; skipped auto-commit "
            "(writes are on disk).",
            err=True,
        )
        return None
    if not vcs_git.has_git_identity(root):
        typer.echo(
            "openkos: WARNING -- git identity unset; skipped auto-commit "
            "(writes are on disk).",
            err=True,
        )
        return None
    try:
        sha = vcs_git.commit_paths(root, paths, message)
    except (vcs_git.GitError, OSError) as exc:
        typer.echo(
            f"openkos: WARNING -- auto-commit did not complete ({exc}); "
            "run `git status` to inspect.",
            err=True,
        )
        return None
    if _commit_has_confidential(root, paths):
        typer.echo(
            "openkos: NOTICE -- this commit includes content marked "
            "'sensitivity: confidential'. openkos commits to LOCAL git "
            "only and never pushes to a remote.",
            err=True,
        )
    return sha


@app.command(
    help=(
        "Create a new OpenKOS workspace in the current directory, with its "
        "bundle layout, config file and local index stores."
    ),
    rich_help_panel="Get started",
)
@_guard_workspace_lock("init")
def init(
    model: str | None = typer.Option(
        None,
        "--model",
        help="Ollama model tag to write into openkos.yaml. "
        "Prompted on a TTY, defaults to qwen3:8b otherwise.",
    ),
    embedding_model: str | None = typer.Option(
        None,
        "--embedding-model",
        help="Ollama embedding model tag to write into openkos.yaml. "
        "Prompted on a TTY over the vetted allowlist, defaults to bge-m3 "
        "otherwise. A value off the allowlist is still accepted, with a "
        "warning.",
    ),
) -> None:
    """Create a fresh OKF workspace in the current directory.

    Refuses (exit 1) without writing anything if the current directory
    cannot become a workspace, per the conditions `config.refusal_reason`
    checks (existing `openkos.yaml`, existing `AGENTS.md`, `raw/` or
    `bundle/` non-empty, or `raw/` or `bundle/` existing as a plain file or
    a symlink), OR if the resolved model (see `_resolve_model`: `--model`
    flag > TTY prompt > default `qwen3:8b`) or the resolved embedding model
    (see `_resolve_embedding_model`: `--embedding-model` flag > TTY picker
    over the vetted allowlist > default `bge-m3`) is blank or contains
    whitespace, a quote, or `#`.
    The refusal reason is printed to stderr so the user knows which
    condition triggered it. This is Phase A (D1): a pure read plus model
    resolution/validation, evaluated in full before any write is attempted.

    Phase A itself can fail to even read the directory (e.g. a pre-existing
    `raw/` or `bundle/` with no read permission) -- that is neither a
    refusal (no workspace was found; the check itself errored) nor a
    write failure (Phase B never started), so it gets its own message.

    Phase B (D1) then writes, in order: `raw/`, the bundle (`index.md` then
    `log.md`), `AGENTS.md`, and `openkos.yaml` LAST (D3) -- the marker is
    written only once every other artifact already exists, so a crash
    mid-init never leaves a directory falsely claiming workspace status.
    `raw/` gets the filesystem's default directory permissions; no `chmod`
    is applied (spec: Default raw/ Permissions). Any write failure
    (permissions, disk full, a collision winning the Phase A -> B race) is
    caught and reported on stderr rather than surfacing a raw traceback.
    """
    root = Path.cwd()
    try:
        reason = config.refusal_reason(root)
    except OSError as exc:
        typer.echo(
            f"openkos init: failed while checking the workspace -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc
    if reason is not None:
        typer.echo(f"openkos init: refusing to initialize -- {reason}.", err=True)
        raise typer.Exit(code=1)

    # Single shared reachability probe (spec: Graceful Degradation Of The
    # Embedding Picker -- MUST reuse the chat picker's existing probe call,
    # MUST NOT issue a second, separate reachability request). Only needed
    # when at least one of the two pickers might actually run: a TTY with at
    # least one of the two flags unset. Skipping it otherwise avoids an
    # unnecessary network call when both flags are given, or neither picker
    # can ever be shown (non-TTY).
    installed_models: list[InstalledModel] = []
    if sys.stdin.isatty() and (model is None or embedding_model is None):
        installed_models = _probe_installed_models()

    # State the stickiness BEFORE the embedding picker runs (#389), not as a
    # postscript once the answer is already on disk. This note used to land
    # after the choice AND below the call to action, which is after both
    # moments it exists to inform. The concrete note naming the resolved tag
    # still prints later; this one reaches the reader while they are deciding.
    stickiness_stated_at_the_picker = sys.stdin.isatty() and embedding_model is None
    if stickiness_stated_at_the_picker:
        typer.echo(
            "openkos init: note -- the embedding model you pick here is "
            "sticky: changing it in this workspace later forces a full "
            "corpus re-embed on the next `openkos reindex`.",
            err=True,
        )

    try:
        resolved_model = _resolve_model(model, installed_models)
        resolved_embedding_model = _resolve_embedding_model(
            embedding_model, installed_models
        )
    except ValueError as exc:
        typer.echo(f"openkos init: refusing to initialize -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    if (
        embedding_model is not None
        and resolved_embedding_model not in config.EMBEDDING_MODEL_ALLOWLIST
    ):
        typer.echo(
            f"openkos init: WARNING -- '{resolved_embedding_model}' is not "
            "on the vetted embedding-model allowlist; writing it anyway.",
            err=True,
        )

    layout = config.WorkspaceLayout(root)
    try:
        layout.raw_dir.mkdir(parents=True, exist_ok=True)
        bundle.create(layout.bundle_dir, datetime.now().astimezone().date())
        config.write_agents(root)
        config.write_config(
            root, model=resolved_model, embedding_model=resolved_embedding_model
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos init: failed while creating the workspace -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos init: created workspace in {root} "
        f"({layout.raw_dir.name}/, {layout.bundle_dir.name}/index.md, "
        f"{layout.bundle_dir.name}/log.md, {layout.agents_path.name}, "
        f"{layout.config_path.name})."
    )
    # ONE stickiness message per run (review finding on this change). Moving
    # the warning earlier is worthless if the reader then meets the same
    # sentence again a few lines down: when the picker already carried the
    # explanation, this line only confirms which tag it applies to.
    if stickiness_stated_at_the_picker:
        typer.echo(
            f"openkos init: the sticky embedding model is "
            f"'{resolved_embedding_model}'.",
            err=True,
        )
    else:
        typer.echo(
            f"openkos init: note -- the embedding model "
            f"('{resolved_embedding_model}') is sticky: editing it in this "
            "workspace's openkos.yaml later forces a full corpus re-embed the "
            "next time `openkos reindex` runs.",
            err=True,
        )
    # Best-effort git setup (Slice 1, git-lifecycle): runs strictly AFTER
    # Phase B's last write (`openkos.yaml`, just above), so any git failure
    # happens only once the workspace is already valid -- mirroring the
    # Ollama preflight's non-fatal shape below. `git init` only runs when
    # `repo_root` reports `cwd` is not already inside a git working tree
    # (never nests a repo inside a parent one); an existing `.gitignore` is
    # never overwritten; the initial commit stages ONLY the paths `init`
    # itself just created (never `-A`/`-a`, so unrelated dirty content in a
    # host repo is never swept in) and is skipped entirely -- with a stderr
    # WARNING, no fallback bot identity -- when git identity is unset. Any
    # `GitError`/`GitUnavailable`/`OSError` here is caught and reported as a
    # non-fatal stderr WARNING; `init`'s exit code and the workspace-write
    # guarantee above are unaffected either way.
    try:
        repo = vcs_git.repo_root(root)
        if repo is None:
            vcs_git.init_repo(root)

        gitignore_path = root / ".gitignore"
        wrote_gitignore = False
        if not gitignore_path.exists():
            gitignore_path.write_text(vcs_git._GITIGNORE_TEMPLATE, encoding="utf-8")
            wrote_gitignore = True

        git_paths = [
            layout.config_path.name,
            layout.agents_path.name,
            layout.raw_dir.name,
            layout.bundle_dir.name,
        ]
        if wrote_gitignore:
            git_paths.append(gitignore_path.name)

        if vcs_git.has_git_identity(root):
            vcs_git.commit_paths(
                root, git_paths, "chore(openkos): initialize workspace"
            )
            # Version-control disclosure (issue #800). It is emitted from
            # INSIDE this block, not folded into the enumeration line above,
            # and the choice is the whole point: that line runs BEFORE any
            # of this, so at the moment it prints, the code has established
            # nothing about `.git/`, nothing about `.gitignore`, and nothing
            # about whether a commit will happen at all. Naming them there
            # would publish a claim the code had not earned -- the exact
            # defect #794 removed from `purge`'s help. Moving the whole
            # enumeration below the git block was the alternative, and it
            # loses more than it buys: the workspace-created confirmation
            # would then arrive after a stderr WARNING on every degraded
            # run, reading as though the warning came first.
            #
            # So the sentence is assembled from what actually happened, per
            # branch, and it is printed ONLY on the path where the commit
            # itself succeeded. Identity unset, a `git commit` failure, and
            # every other degradation fall through to the WARNINGs below
            # with no disclosure at all: `_autocommit` skips on the same
            # identity probe, so in that state "every openkos command
            # commits its own changes" is false and `git revert <commit>`
            # would name a commit that does not exist.
            if repo is None and wrote_gitignore:
                setup = "created .git/ and .gitignore"
            elif repo is None:
                setup = "created .git/"
            elif wrote_gitignore:
                setup = "already a git repository; created .gitignore"
            else:
                setup = "already a git repository"
            typer.echo(
                f"openkos init: the workspace is version-controlled "
                f"({setup}) and the files above are committed. Every openkos "
                "command commits its own changes: `git log` reviews them, "
                "`git revert <commit>` undoes one."
            )
        else:
            typer.echo(
                "openkos init: WARNING -- git identity unset; skipped the "
                "initial commit (the workspace and .gitignore are still "
                "created).",
                err=True,
            )
    except (vcs_git.GitError, OSError) as exc:
        # Honest for ALL failure modes: a repo/.gitignore may already have
        # been created and files staged before this error hit, so "skipped"
        # would be misleading here. Actionable: points at `git status` to
        # inspect and finish setup manually.
        typer.echo(
            f"openkos init: WARNING -- git setup did not complete cleanly ({exc}). "
            "The workspace itself is still valid; run `git status` in it to "
            "inspect and finish git setup manually if needed.",
            err=True,
        )

    # The call to action lands AFTER the git block (issue #800), because the
    # disclosure that block emits has to reach the reader before it. #389
    # settled this shape for the stickiness note: a line printed below "here
    # is what to do next" has already lost the reader it was written for,
    # and that applies with more force to a safety net than to a warning.
    # Moving it here also puts the degradation WARNINGs above the hint
    # instead of orphaning them under it.
    typer.echo("Next: run `openkos ingest <path>` to import your first source.")

    # A second call to action, for the audience the first one misses
    # (issue #982). `openkos ingest` is the right pointer for someone who
    # intends to keep working in a terminal, and the wrong one for the
    # reader who will never run a second command: their whole path is to
    # open the bundle and read it, and the bundle already works as an
    # Obsidian vault with no code, no plugin and no configuration.
    #
    # It names `bundle/` and rules the workspace root OUT, because that is
    # the half that decides whether the vault works at all: bundle
    # documents link with bundle-root-absolute paths
    # (`/concepts/some-concept.md`), which resolve only when the vault root
    # IS the bundle. Opened at the workspace root, `/concepts/...` points
    # at a directory that does not exist and nothing links to anything --
    # the reader concludes the output is a pile of disconnected files.
    #
    # Placed AFTER `Next:` rather than above it, unlike the git (#800) and
    # stickiness (#389) notes: those are disclosures that inform a choice
    # presented above them, and this is a sibling call to action. The two
    # arrive together at the end of the run, each audience served by one.
    #
    # Relative, like every other path this command prints (`raw/`,
    # `bundle/index.md`, `openkos.yaml`): `init` runs in the directory it
    # is initializing, so `bundle/` is unambiguous, and interpolating
    # `layout.bundle_dir` would put a machine-specific absolute path into
    # output that users and tests compare verbatim.
    typer.echo(
        "To read your knowledge in an editor, open `bundle/` (not the "
        "workspace root) as an Obsidian vault or a VS Code folder."
    )

    # Non-fatal Ollama preflight (D2): purely observational, runs strictly
    # after the workspace already exists. `except Exception` (not
    # `BaseException`) deliberately catches BackendUnavailable/
    # BackendModelNotFound/BackendError AND any unexpected probe error while
    # still letting Ctrl-C/SystemExit propagate; nothing here ever raises
    # `typer.Exit` or pulls a model/spawns a server -- init's exit code
    # stays 0 on every outcome, and the file-writer guarantee above is
    # unaffected either way.
    try:
        probe = application_backends.diagnostics_client(
            None,
            model=resolved_model,
            timeout=_PREFLIGHT_TIMEOUT,
            factories=_backend_factories(),
        )
        ready = model_tag_matches(resolved_model, [m.tag for m in probe.list_models()])
    except Exception:  # noqa: BLE001 -- an unreachable or misbehaving probe means not ready; init notes it below
        ready = False
    if not ready:
        typer.echo(
            "openkos init: note -- Ollama isn't ready for model "
            f"'{resolved_model}' yet. Run `openkos doctor` to diagnose "
            "(ingest and query need it; the workspace was still created).",
            err=True,
        )

    # Sticky re-embed warning (spec: Sticky Re-Embed Warning On Every
    # Successful Init): printed UNCONDITIONALLY on every successful init,
    # regardless of TTY/non-TTY or which embedding model was resolved --
    # never conditioned on a prior corpus existing, since a fresh workspace
    # has nothing to re-embed yet. Worded about FUTURE cost only: this
    # workspace has never re-embedded anything, so it must never claim a
    # re-embed already happened.
    #
    # It also names only causes that can affect THIS workspace. An earlier
    # revision blamed "a future init of a different workspace", which cannot
    # force a re-embed here and read as a non-sequitur to anyone who did not
    # already know the model-tag gate is per-workspace.


def _plural(n: int) -> str:
    """Return `""` for `n == 1`, else `"s"` -- English plural suffix helper
    shared by the `query` command's stderr rendering."""
    return "" if n == 1 else "s"


def _reembed_trigger_wording(
    previous_tag: str | None, effective_tag: str | None
) -> str:
    """Name the REAL trigger for a forced full re-embed (#888, widened by
    issue #1057 Phase 11; reindex-command: Reindex Discloses The Real
    Re-Embed Trigger, Not A False Model-Change Claim). Parses BOTH
    `previous_tag` (the PREVIOUSLY stored effective tag) and `effective_tag`
    (THIS run's tag) via `state.reindex.parse_embedding_tag` -- never a bare
    string partition or a comparison against the bare configured model name,
    either of which can report a false "embedding model changed" on a
    composition-only bump, or fail to name a genuine backend change at all.

    Four branches, checked in this exact order, mutually exclusive: no
    previous tag at all (fresh store, or one `purge` just dropped) is named
    explicitly rather than folded into "model changed"; a backend-kind
    difference (a legacy, backend-unqualified stored tag is read as
    `ollama` by `parse_embedding_tag`, so upgrading to a version that emits
    backend-qualified tags while staying on `ollama` is never reported as a
    backend change); a genuine model-name difference (same backend); and a
    composition-only difference with the SAME backend and model -- the
    fallback when none of the first three differ. No appended model clause
    when both backend and model differ; the backend-changed branch fires
    alone, superseding an earlier proposal sketch."""
    if previous_tag is None:
        return "no embedding-model tag stored (fresh or dropped store)"
    old = reindex_module.parse_embedding_tag(previous_tag)
    new = reindex_module.parse_embedding_tag(effective_tag or "")
    if old.backend != new.backend:
        return f"embedding backend changed ({old.backend} -> {new.backend})"
    if old.model != new.model:
        return f"embedding model changed ({old.model} -> {new.model})"
    return (
        f"embed text composition changed ({old.composition} -> {new.composition}); "
        f"your embedding model is unchanged ({old.model})"
    )


def _format_type_tally(counts: dict[str, int]) -> str:
    """Render a per-type derived-object tally line from a `type -> count`
    dict, decoupled from any `ingest`-specific internals so other commands
    MAY reuse it (spec: Reusable Type-Tally Formatting Helper).

    Returns `""` for an empty (or all-zero) dict, signaling "no line to
    print" to the caller. Otherwise returns `extracted {N} objects -- {c}
    {Type}, ...`, ordered by canonical `_TYPE_TO_SECTION` registry order
    (NOT insertion order), so identical input always renders the same
    string."""
    total = sum(counts.values())
    if total == 0:
        return ""
    order = {t: i for i, t in enumerate(_TYPE_TO_SECTION)}
    parts = ", ".join(
        f"{counts[t]} {t}" for t in sorted(counts, key=lambda t: order[t])
    )
    return f"extracted {total} object{_plural(total)} — {parts}"


def _format_group_tally(high: int, acronym: int, low: int) -> str:
    """Render the leading candidate-group tally line from per-tier counts,
    decoupled from `CandidateGroup` internals so callers pass primitive
    counts (spec: Reusable Group-Tally Formatting Helper).

    Returns `""` for all-zero counts, signaling "no line to print" to the
    caller. Otherwise returns
    `N candidate group(s) (X exact, Y acronym, Z near)`.

    ACRONYM is counted separately rather than folded into `near` (#397
    follow-up): it is a distinct match METHOD, and reporting a
    deterministic initials match as a fuzzy similarity score misdescribes
    the evidence a reader is about to adjudicate."""
    total = high + acronym + low
    if total == 0:
        return ""
    return (
        f"{total} candidate group{_plural(total)} "
        f"({high} exact, {acronym} acronym, {low} near)"
    )


def _format_verdict_tally(same: int, different: int, uncertain: int) -> str:
    """Render the leading adjudication verdict-tally line from per-verdict
    counts (spec: Reusable Verdict-Tally Formatting Helper).

    Returns `""` for all-zero counts, signaling "no line to print" to the
    caller. Otherwise returns `adjudicated N: x SAME, y DIFFERENT`, with a
    `, z UNCERTAIN` segment appended only when `uncertain > 0`."""
    total = same + different + uncertain
    if total == 0:
        return ""
    parts = f"{same} SAME, {different} DIFFERENT"
    if uncertain > 0:
        parts += f", {uncertain} UNCERTAIN"
    return f"adjudicated {total}: {parts}"


class AdjudicationPayload(TypedDict):
    """The `adjudicate --json` envelope (issue #468 item 5).

    `results` was the WHOLE payload until #468: a bare JSON array. A partial
    batch (#441) emits the completed verdicts and reports the failure on
    stderr with exit 1, which means `openkos adjudicate --json > out.json`
    wrote a valid-looking but truncated array whose incompleteness lived
    ONLY in an exit code the redirect discarded. These three counters put
    that fact in the file itself.

    `adjudicated` and `total` describe the RUN -- groups the model answered
    for, and groups queued -- so they are deliberately NOT affected by
    `--same-only`, which filters `results` alone. Conflating them would
    report a complete run as truncated merely because the operator asked
    for a narrower view."""

    partial: bool
    adjudicated: int
    total: int
    results: list[dict[str, object]]


def _adjudication_payload(
    results: Sequence[AdjudicatedCandidate],
    *,
    same_only: bool,
    total: int,
    partial: bool,
    cross_source_flags: Sequence[bool],
    cross_type_flags: Sequence[bool],
) -> AdjudicationPayload:
    """Build the pure, I/O-free `adjudicate --json` payload from `results`,
    preserving `results` order and omitting `confidence` and any
    survivor/absorbed field (spec: Machine-Readable `--json` Output Mode).

    `tier` MUST be rendered via `.name` (uppercase `"HIGH"`/`"LOW"`), NOT
    `.value` (lowercase) -- mirrors the human path's ternary but sourced
    directly from the enum member's name. `verdict` mirrors the human path's
    `.value.upper()` rendering. `same_only=True` keeps only `Verdict.SAME`
    entries, the same predicate the human `--same-only` display filter uses.

    `total` is the count of candidate groups QUEUED and `partial` comes from
    `batch.failure is not None` -- both are the caller's to supply, because
    neither is recoverable from `results` alone: a batch that failed on its
    very first group and one that completed a single-group run produce the
    same `results` list (issue #468 item 5).

    `cross_source_flags` (#776) is aligned 1:1 with `results` and supplied
    by the caller for the same purity reason as `total`: whether a SAME
    pair's provenance sets are disjoint is a bundle read this I/O-free
    builder must not perform. The unattended pipelines `--json` serves are
    exactly where the human-only note would otherwise be invisible.

    `cross_type_flags` (#904) is aligned the same way and supplied for the
    same reason: which OKF type each member declares is a bundle read, and
    the joined `okf_type` label already on the payload is display-only by
    `candidates._type_label`'s own contract -- never to be parsed back
    into member types."""
    return {
        "partial": partial,
        "adjudicated": len(results),
        "total": total,
        "results": [
            {
                "member_ids": list(result.candidate.member_ids),
                "okf_type": result.candidate.okf_type,
                "tier": result.candidate.tier.name,
                "verdict": result.verdict.value.upper(),
                "rationale": result.rationale,
                "cross_source": cross_source,
                "cross_type": cross_type,
            }
            for result, cross_source, cross_type in zip(
                results, cross_source_flags, cross_type_flags, strict=True
            )
            if not same_only or result.verdict is Verdict.SAME
        ],
    }


def _render_adjudicate_report(
    root: Path, results: Sequence[AdjudicatedCandidate], *, same_only: bool
) -> None:
    """The human `adjudicate` report over `results`, byte-identical to the
    pre-#441 inline body -- extracted so the partial-batch failure epilogue
    can run AFTER every output mode instead of fighting this path's early
    returns."""
    typer.echo(f"openkos adjudicate: workspace at {root}")
    typer.echo()
    if not results:
        typer.echo("No candidates found.")
        return

    displayed = [
        result for result in results if not same_only or result.verdict is Verdict.SAME
    ]
    if not displayed:
        typer.echo("No SAME-verdict candidates to display (--same-only).")
        return

    verdict_counts = Counter(result.verdict for result in results)
    typer.echo(
        _format_verdict_tally(
            verdict_counts[Verdict.SAME],
            verdict_counts[Verdict.DIFFERENT],
            verdict_counts[Verdict.UNCERTAIN],
        )
    )
    typer.echo(
        "Legend: [tier] type -- trigger, then verdict and rationale. "
        "The tier is the MATCH METHOD, not a strength ranking: "
        "HIGH = exact normalized key, LOW = weakest per-token match "
        "of the smaller title (1.000 = every token matched, NOT "
        "identical titles)."
    )
    bundle_dir = config.WorkspaceLayout(root).bundle_dir
    for result in displayed:
        group = result.candidate
        tier_label = group.tier.name
        typer.echo(f"[{tier_label}] {group.okf_type} -- {group.trigger}")
        for member_id in group.member_ids:
            typer.echo(f"  - {member_id}")
        # Confidence is intentionally NOT shown: a local model returns a
        # flat, uncalibrated value (issue #138), so a fake-precise two-decimal
        # number would invite trust it has not earned. The value is still
        # parsed and kept on `AdjudicatedCandidate` for future thresholding.
        typer.echo(f"  verdict: {result.verdict.value.upper()}")
        typer.echo(f"  rationale: {result.rationale}")
        # #776: a SAME verdict over disjoint provenance is the risky class
        # -- named here, in the read-only listing, so the operator's eye
        # lands on it BEFORE any apply mode is invoked. 2-member groups
        # only: the merge unit is the pair, and an N>2 group's GLOBAL
        # intersection can be empty while every adjacent pair shares a
        # source, which would make "share no source" a false statement.
        if (
            result.verdict is Verdict.SAME
            and len(group.member_ids) == 2
            and application_lifecycle.cross_source_same_pair(
                bundle_dir, group.member_ids
            )
        ):
            typer.echo(_CROSS_SOURCE_REPORT_NOTE)
        # #904: the other risky class, named in the same read-only slot and
        # independently of the one above -- a pair can be BOTH (different
        # types AND disjoint provenance), and each note answers a different
        # question, so neither suppresses the other. Not restricted to
        # 2-member groups the way the cross-source note is: "share no
        # source" is a claim about a global intersection that an N>2 group
        # can make false, while "these members declare different types" is
        # true of the members it names however many there are.
        if result.verdict is Verdict.SAME:
            cross_type_concern = application_lifecycle.cross_type_concern(
                bundle_dir, group.member_ids
            )
            if cross_type_concern is not None:
                typer.echo(_cross_type_report_note(cross_type_concern))
        typer.echo()
    typer.echo("Next: openkos merge <survivor> <absorbed>")


def _echo_adjudicate_batch_failure(
    batch: AdjudicationBatch, *, total: int, model: str
) -> None:
    """One stderr line for a partial `AdjudicationBatch` (#441): the same
    3-tier cause-specific wording the raise-path handlers use, prefixed with
    how much paid-for work survived. The `isinstance` dispatch mirrors the
    handlers' ORDER for the same reason they are ordered: both specific
    classes subclass `BackendError`, so the generic branch must come last or
    their actionable remediation is lost."""
    failure = batch.failure
    context = (
        f"openkos adjudicate: failed after adjudicating {len(batch.results)} "
        f"of {total} candidate group(s)"
    )
    if isinstance(failure, BackendUnavailable):
        typer.echo(
            f"{context} -- {failure}. Start it with `ollama serve`, then try "
            f"again.{_DOCTOR_HINT}",
            err=True,
        )
    elif isinstance(failure, BackendModelNotFound):
        typer.echo(
            f"{context} -- model '{model}' is not installed. Pull it with "
            f"`ollama pull {model}`, then try again.",
            err=True,
        )
    else:
        typer.echo(f"{context} -- {failure}.", err=True)


def _echo_contradictions_batch_failure(
    batch: ContradictionBatch, *, total: int, model: str
) -> None:
    """One stderr line for a partial `ContradictionBatch` (#441): the same
    3-tier cause-specific wording the raise-path handlers use, prefixed with
    how much paid-for work survived (mirrors
    `_echo_adjudicate_batch_failure`). `total` is `plan.llm_calls` -- the
    judged-candidate budget the verb already holds, so the count needs no
    second planning pass; the line says "planned" (#685 item 6) so the
    denominator reads as the plan and never overstates what was sent. The `isinstance` dispatch mirrors the handlers'
    ORDER for the same reason they are ordered: both specific classes
    subclass `BackendError`, so the generic branch must come last or their
    actionable remediation is lost."""
    failure = batch.failure
    context = (
        f"openkos contradictions: failed after judging {len(batch.results)} "
        f"of {total} planned candidate(s)"
    )
    if isinstance(failure, BackendUnavailable):
        typer.echo(
            f"{context} -- {failure}. Start it with `ollama serve`, then try "
            f"again.{_DOCTOR_HINT}",
            err=True,
        )
    elif isinstance(failure, BackendModelNotFound):
        typer.echo(
            f"{context} -- model '{model}' is not installed. Pull it with "
            f"`ollama pull {model}`, then try again.",
            err=True,
        )
    else:
        typer.echo(f"{context} -- {failure}.", err=True)


# `_member_body_length`/`_ordered_merge_pair`/`_cross_source_same_pair`/
# `_cross_type_concern` moved verbatim into `application/lifecycle.py`
# (issue #918 Slice 5) and are not aliased back onto this module -- every
# call site below reaches them through `application_lifecycle.<name>`, and
# all four of those names are PUBLIC there, the underscores above being
# the spelling they carried on THIS module before the move
# (`_member_body_length` was the last to be promoted, issue #974). The
# reason that alias was removed is stated once, at the relocation block
# further down this module (issue #955); it is not restated here.


_CROSS_SOURCE_REPORT_NOTE = (
    "  note: cross-source -- members share no source; review before merging"
)
"""The listing's #776 marker, on SAME verdicts only: the risky class must
catch the operator's eye BEFORE any apply mode is invoked."""

_CROSS_SOURCE_WALK_NOTE = (
    "  note: cross-source SAME -- members share no source; "
    "the two may be distinct real-world items"
)
"""The per-item walks' #776 warning, rendered BEFORE the [y/N] prompt --
ONE constant shared by `adjudicate --apply` and `curate`'s Identity stage
so the two surfaces cannot drift apart."""


def _cross_type_report_note(reason: str) -> str:
    """The listing's #904 marker, on SAME verdicts only -- the risky class
    must catch the operator's eye BEFORE any apply mode is invoked."""
    return f"  note: cross-type -- {reason}; review before merging"


def _cross_type_walk_note(reason: str) -> str:
    """The per-item walks' #904 warning, rendered BEFORE the [y/N] prompt
    -- ONE helper shared by `adjudicate --apply`, `merge`, and `curate`'s
    Identity stage, for the reason `_CROSS_SOURCE_WALK_NOTE` is one
    constant: #796 showed a guard reaching the batch door while the door
    the tool recommends stayed open."""
    return f"  note: cross-type SAME -- {reason}; review this pair before consenting"


# `_prepare_one_merge`/`_reconcile_planned` moved verbatim into
# `application/lifecycle.py` (issue #918 Slice 5) and, like the four names
# above, are reached through `application_lifecycle.<name>` rather than an
# alias on this module.


_RECONCILE_CONFLICT_MESSAGE: Final = (
    "--reconcile and --no-reconcile are mutually exclusive."
)
"""The refusal text shared by the three verbs that take both levers
(issue #803), so the wording cannot drift between them.

A contradiction is REFUSED rather than silently resolved: either
precedence rule would carry out half of what the operator asked for while
discarding the other half, with no signal that it did."""


_RECONCILE_PLAN_NOTE: Final = (
    "reconcile merged body: one model call rewrites it as a single coherent "
    "document (falls back to the stacked form on failure; pass "
    "--no-reconcile to skip)"
)
"""The plan-time disclosure text, shared so `merge`'s multi-line plan and
the one-line preview every other consenting caller prints cannot drift."""


def _apply_reconciliation(
    root: Path,
    prepared: "PreparedMerge",
    *,
    no_reconcile: bool,
    verb: str,
    reconcile: bool = False,
) -> "PreparedMerge":
    """Run the #645 pass when `_reconcile_planned`, else return `prepared`
    unchanged -- the shared post-consent half of the disclosure above.

    Called AFTER consent (the plan disclosed it) and BEFORE the drift
    re-check, so the slow model call sits inside the window the drift guard
    re-validates rather than after it. Any failure keeps the stacked body
    and notices on stderr: the merge itself never gains a new failure mode
    from an improvement pass.

    `reconcile` (issue #803) is threaded through to the same predicate the
    disclosure read, never re-tested here: a second inline condition is
    exactly how the #688 defect got in."""
    if not application_lifecycle.reconcile_planned(
        prepared, no_reconcile=no_reconcile, reconcile=reconcile
    ):
        return prepared
    prepared, failure = _reconcile_merged_survivor(root, prepared)
    if isinstance(failure, _SensitivitySkip):
        typer.echo(
            f"openkos {verb}: skipped body reconciliation -- "
            f"{failure.concept_id} is confidential and the backend is not "
            "local; the stacked body was kept.",
            err=True,
        )
    elif failure is not None:
        typer.echo(
            f"openkos {verb}: notice -- reconciliation failed ({failure}); "
            "kept the stacked body.",
            err=True,
        )
    return prepared


def _format_merge_preview_line(
    prepared: "PreparedMerge", *, no_reconcile: bool = False, reconcile: bool = False
) -> str:
    """The "merge X into Y (...)" preview line for one prepared merge,
    extracted verbatim from the former inline body (issue #137 closing
    slice, Phase 1 refactor). Gains an optional stacked-body clause (issue
    #409, report half) only when `prepared.stacked_body` is non-`None` --
    a merge that stacks nothing says nothing extra here either.

    Also carries the #645 reconciliation disclosure (issue #688), on the
    same reasoning the #559 guardrail warning rides here: every consenting
    caller shows it without having to remember to. `no_reconcile` DEFAULTS
    to `False` deliberately -- a caller that forgets to thread its opt-out
    through over-discloses rather than under-discloses, and `merge`'s own
    multi-line plan is the one caller that renders the note itself.
    `reconcile` (issue #803) defaults to `False` on the mirrored reasoning:
    a caller that forgets to thread the opt-IN through discloses the pass
    exactly as the thresholds decided, never a pass it was not asked for."""
    stacked_note = ""
    guardrail_note = ""
    if prepared.stacked_body is not None:
        stacked_note = (
            f", stacks {prepared.stacked_body.absorbed_chars} unreconciled "
            f"body char(s) ({prepared.stacked_body.share:.0%} of merged body)"
        )
        # Issue #559: a merge whose result would be dominated by the
        # absorbed side is the measured signature of merging a document
        # ABOUT the survivor rather than a second description of it. The
        # warning rides the shared preview line so every consenting caller
        # (`adjudicate --apply`, curate's Identity stage, `merge`) shows it
        # without remembering to.
        if prepared.stacked_body.exceeds_guardrail:
            guardrail_note = (
                f"\n  warning: {prepared.stacked_body.share:.0%} of the "
                "merged body would be unreconciled absorbed content -- this "
                "usually means a document ABOUT the survivor, not the same "
                "object. Verify before accepting."
            )
    reconcile_note = ""
    if application_lifecycle.reconcile_planned(
        prepared, no_reconcile=no_reconcile, reconcile=reconcile
    ):
        reconcile_note = f"\n  ~ {_RECONCILE_PLAN_NOTE}"
    return (
        f"  merge {prepared.absorbed_canonical} into {prepared.survivor_canonical} "
        f"(sensitivity {prepared.sensitivity_before}->"
        f"{prepared.sensitivity_after}, {len(prepared.touched_files)} "
        f"rewrite(s), removes bundle/{prepared.absorbed_canonical}.md"
        f"{stacked_note}){guardrail_note}{reconcile_note}"
    )


def _echo_n_gt2_skip(bundle_dir: Path, group: "CandidateGroup") -> None:
    """The SAME-verdict N>2 skip report shared by `_run_adjudicate_apply`
    and `_run_adjudicate_apply_same` (issue #191) -- ONE helper so the two
    walks can never drift apart again.

    Keeps the pre-#191 skip line byte-identical, then prints the exact
    pairwise merge commands the operator would otherwise have to
    reconstruct by hand: since #776 the suggested survivor is the member
    with the RICHEST body (ties keep ascending-id order, so a group whose
    members cannot be measured falls back to `member_ids[0]`, the pre-#776
    convention), matching the 2-member walks' own `_ordered_merge_pair`
    rule; each remaining member is absorbed into it with one
    `openkos merge <survivor> <absorbed>` line, in member order.
    Sequential pairwise merges into one survivor are safe to run in order
    because each individual merge is reversible via `unmerge` -- a mistake
    at step k never strands steps 1..k-1 (issue #191). Print-only:
    counters and summary lines stay with the callers, byte-identical to
    the pre-#191 output."""
    typer.echo(f"[{group.okf_type}] {group.member_ids}: skipped (N>2, merge manually)")
    # max() keeps the FIRST of equally-long members, so an all-tie group
    # (including all-unresolvable) preserves the ascending-id convention.
    survivor_id = max(
        group.member_ids,
        key=lambda mid: application_lifecycle.member_body_length(bundle_dir, mid),
    )
    typer.echo("  run in order (each reversible via unmerge):")
    for absorbed_id in group.member_ids:
        if absorbed_id == survivor_id:
            continue
        typer.echo(f"    openkos merge {survivor_id} {absorbed_id}")


def _run_adjudicate_apply(
    root: Path,
    layout: config.WorkspaceLayout,
    index_path: Path,
    log_path: Path,
    results: Sequence[AdjudicatedCandidate],
    *,
    no_reconcile: bool = False,
    reconcile: bool = False,
) -> None:
    """The interactive `adjudicate --apply` merge walk (issue #137 Slice
    2b-ii, design D2-D9): per SAME 2-member group (D3), re-verify both
    member ids still exist (D4, since an earlier merge this same run may
    have absorbed a later group's member), preview what `prepare_merge`
    would fuse (D5), prompt `[y/N]` (D6, reshaped by issue #483), and on
    `y` execute `merge_core` + `_autocommit` (D7) -- reusing every 2b-i
    building block verbatim. A mid-run write failure (D8) stops the loop
    immediately; prior per-merge commits remain intact and reversible via
    `unmerge`. A final summary line (D9) always prints, even when nothing
    is eligible, followed by one `  declined: absorbed -> survivor` line
    per operator-declined merge (issue #483, mirroring #398's decline
    listing) so a typo-free decline set is revisitable.

    The prompt itself is `curate._confirm` -- the SAME validating helper
    curate's Identity stage routes this same merge decision through since
    PR #482 (issue #483, closing the #398 gap here): `y`/`yes` accepts,
    `n`/`no`/Enter declines, and any other answer re-prompts with a notice
    naming the accepted tokens instead of being silently counted as a
    decline. Reusing the private helper across the module boundary is
    deliberate, like `_type_label` in #479 -- one prompt contract, one
    source of truth (`main` already imports `curate_module` at module
    scope; the docstring warning in curate.py is about the OPPOSITE
    direction).

    Between the accepted `y` and the write sits the same TOCTOU window
    every drift-guarded verb closes (the #306/#313/#319 arc): every byte
    `merge_service.commit_merge` writes was computed by `_prepare_one_merge` BEFORE
    the `[y/N]` prompt, so an edit landing on any target while the
    prompt waited -- likeliest on the survivor, worst on the absorbed
    file, which is UNLINKED rather than overwritten -- would be silently
    destroyed. Each accepted pair therefore hands `_merge_drift_targets`'
    baseline mapping to `_reject_drifted_targets` strictly after its
    prompt and strictly before its write (issue #346, closing the gap
    `merge` and `curate`'s Identity stage already closed): drift refuses
    with exit 3, nothing is written for that pair, and the run ends --
    prior per-pair commits remain intact and reversible via `unmerge`,
    exactly like the D8 failure path."""
    applied = 0
    skipped_n_gt2 = 0
    skipped_already_merged = 0
    declined: list[str] = []

    for result in results:
        group = result.candidate
        if result.verdict is not Verdict.SAME:
            continue
        if len(group.member_ids) != 2:
            if len(group.member_ids) > 2:
                _echo_n_gt2_skip(layout.bundle_dir, group)
                skipped_n_gt2 += 1
            continue

        survivor_id, absorbed_id, survivor_criterion = (
            application_lifecycle.ordered_merge_pair(
                layout.bundle_dir, group.member_ids
            )
        )

        try:
            prepared = application_lifecycle.prepare_one_merge(
                root,
                layout,
                index_path,
                log_path,
                group,
                ordered_pair=(survivor_id, absorbed_id),
            )
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos adjudicate --apply: failed while merging "
                f"{absorbed_id} into {survivor_id} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        if prepared is None:
            typer.echo(
                f"{survivor_id} / {absorbed_id}: skipped (member unresolved "
                "-- already merged or missing)"
            )
            skipped_already_merged += 1
            continue

        typer.echo(
            _format_merge_preview_line(
                prepared, no_reconcile=no_reconcile, reconcile=reconcile
            )
        )
        # #776: the choice is deterministic and STATED -- an unexplained
        # arbitrary survivor is what the issue reports.
        typer.echo(f"  survivor: {prepared.survivor_canonical} ({survivor_criterion})")
        if application_lifecycle.cross_source_same_pair(
            layout.bundle_dir, group.member_ids
        ):
            # #776: the interactive walk keeps the pair -- the operator
            # consents per item -- but the risky class is named BEFORE the
            # prompt, not discovered in the wreckage afterwards.
            typer.echo(_CROSS_SOURCE_WALK_NOTE)
        # #904: same slot, same reasoning, independent class. Ordered from
        # `prepared`, not from raw `member_ids`: the survivor line printed
        # directly above names one direction, and the note must not name
        # the other one back.
        cross_type_concern = application_lifecycle.cross_type_concern(
            layout.bundle_dir,
            (prepared.survivor_canonical, prepared.absorbed_canonical),
        )
        if cross_type_concern is not None:
            typer.echo(_cross_type_walk_note(cross_type_concern))
        # Issue #483: `curate._confirm` is the one validating per-item
        # write-consent prompt (#398 contract) -- private-helper reuse
        # across the boundary is deliberate, as with `_type_label` (#479).
        # Issue #958: the prompt comes from `application_lifecycle.
        # merge_walk_confirmation`, shared with curate's Identity stage.
        confirmation = application_lifecycle.merge_walk_confirmation(
            survivor_canonical=prepared.survivor_canonical,
            absorbed_canonical=prepared.absorbed_canonical,
        )
        if not curate_module._confirm(confirmation.prompt):
            declined.append(
                f"{prepared.absorbed_canonical} -> {prepared.survivor_canonical}"
            )
            # #797: recorded HERE too, not only in `curate`'s Identity
            # walk. The two walks share a prompt and a write path by
            # design; a decline persisted in one and forgotten in the other
            # is exactly the drift the shared `_CROSS_SOURCE_WALK_NOTE`
            # constant above exists to prevent.
            _record_identity_decline_from_walk(
                root, layout, group.member_ids, verb="adjudicate --apply"
            )
            continue

        # #688: same post-consent, pre-drift-check slot `merge` and
        # curate's Identity stage use, via the same helper -- this walk
        # drives `_prepare_one_merge`/`merge_service.commit_merge` directly too, so
        # it had the identical silent-stacking gap.
        prepared = _apply_reconciliation(
            root,
            prepared,
            no_reconcile=no_reconcile,
            reconcile=reconcile,
            verb="adjudicate --apply",
        )

        # Issue #346: every byte `merge_service.commit_merge` writes below was
        # computed before the prompt, so re-validate each target now --
        # after the accepted `y`, before the first write. The absorbed
        # file rides in `deletes=` because it is UNLINKED, not overwritten
        # (#329), mirroring `merge`'s own call site.
        # The commit phase (#1137): the prompt and the reconciliation call
        # above held no workspace lock.
        with _commit_section_for(root)():
            absorbed_path = layout.bundle_dir / f"{prepared.absorbed_canonical}.md"
            _reject_drifted_targets(
                layout,
                application_lifecycle.merge_drift_targets(layout, prepared),
                "adjudicate --apply",
                deletes=frozenset({absorbed_path}),
            )

            try:
                merge_service.commit_merge(
                    root, layout, prepared, autocommit=_autocommit
                )
            except (OSError, ValueError) as exc:
                typer.echo(
                    "openkos adjudicate --apply: failed while merging "
                    f"{prepared.absorbed_canonical} into "
                    f"{prepared.survivor_canonical} -- {exc}.",
                    err=True,
                )
                raise typer.Exit(code=1) from exc
        applied += 1

    skipped_total = skipped_n_gt2 + skipped_already_merged + len(declined)
    prefix = "nothing to apply -- " if applied == 0 and skipped_total == 0 else ""
    typer.echo(
        f"openkos adjudicate --apply: {prefix}applied {applied}, skipped "
        f"{skipped_total} (N>2: {skipped_n_gt2}, "
        f"already-merged: {skipped_already_merged}, declined: {len(declined)})"
    )
    for item in declined:
        typer.echo(f"  declined: {item}")

    # #640: once per invocation, after the whole walk -- never inside
    # `merge_service.commit_merge`, which runs per accepted pair.
    if applied:
        _refresh_derived_after_write(layout, None, verb="adjudicate")


def _refused_stacked_line(
    report: "StackedBodyReport", absorbed_canonical: str, survivor_canonical: str
) -> str:
    """The `--apply-same` refusal line for one guardrail-crossing merge
    (issue #559): names the share, the threshold, and the per-item-consent
    route that can still accept it deliberately. One helper so Pass 1 and
    Pass 2 can never phrase the same refusal differently. Takes the report
    itself (never an optional field) so a `None` cannot reach the format."""
    return (
        f"  refused (stacked-body {report.share:.0%} >= "
        f"{application_lifecycle.STACKED_SHARE_GUARDRAIL:.0%}): {absorbed_canonical} -> "
        f"{survivor_canonical} -- the batch gate consents to a "
        "count, not to this pair; review it via `openkos adjudicate --apply`."
    )


def _echo_batch_pass1_skips(
    bundle_dir: Path, preview: "application_lifecycle.BatchApplyPreview"
) -> None:
    """Render every Pass-1 classification exclusion from `preview.skips`
    (issue #918 Slice 5), in the exact wording and relative order
    `_run_adjudicate_apply_same`'s former single classification loop
    produced -- N>2, cross-source (#776), and cross-type (#904) exclusions
    interleave in `results`' own order, since one loop used to decide all
    three together."""
    for skip in preview.skips:
        if isinstance(skip, application_lifecycle.NGt2Skip):
            _echo_n_gt2_skip(bundle_dir, skip.group)
        elif isinstance(skip, application_lifecycle.CrossSourceSkip):
            # #776: a SAME verdict over members with DISJOINT provenance is
            # the risky class -- the typed-count gate consents to a batch,
            # not to fusing two real-world items, so the pair is routed to
            # per-item review unless `--include-cross-source` opts in.
            typer.echo(
                f"{skip.member_ids[0]} / {skip.member_ids[1]}: skipped "
                "(cross-source SAME -- members share no source; review "
                "and merge manually with `openkos merge "
                f"{skip.ordered[0]} {skip.ordered[1]}`, or re-run with "
                "--include-cross-source)"
            )
        else:
            # #904: a SAME verdict over members declaring DIFFERENT OKF
            # types is the second risky class the typed count must not
            # consent to, unless `--include-cross-type` opts in.
            typer.echo(
                f"{skip.member_ids[0]} / {skip.member_ids[1]}: skipped "
                f"(cross-type SAME -- {skip.reason}; review and "
                f"merge manually with `openkos merge {skip.ordered[0]} "
                f"{skip.ordered[1]}`, or re-run with --include-cross-type)"
            )


def _echo_batch_pass1_items(
    preview: "application_lifecycle.BatchApplyPreview",
    *,
    no_reconcile: bool,
    reconcile: bool,
) -> None:
    """Render every Pass-1 preview-build outcome from `preview.items`
    (issue #918 Slice 5): a stacked-body-guardrail refusal (#559) or a
    clean previewed pair, in `eligible_groups` order -- the two interleave
    in this same pass in the pre-move code, and this preserves that exact
    order."""
    for item in preview.items:
        if isinstance(item, application_lifecycle.StackedRefusal):
            # Issue #559: the typed-count gate consents to a batch, not to
            # any single pair, so the guardrail is HARD here -- a dominated
            # merge is excluded from the preview, the count, and Pass 2,
            # and routed to the per-item-consent path instead.
            typer.echo(
                _refused_stacked_line(
                    item.report, item.absorbed_canonical, item.survivor_canonical
                )
            )
        else:
            typer.echo(
                _format_merge_preview_line(
                    item.prepared, no_reconcile=no_reconcile, reconcile=reconcile
                )
            )
            # #776: the batch survivor rule is deterministic and STATED.
            typer.echo(
                f"  survivor: {item.prepared.survivor_canonical} "
                f"({item.survivor_criterion})"
            )


def _run_adjudicate_apply_same(
    root: Path,
    layout: config.WorkspaceLayout,
    index_path: Path,
    log_path: Path,
    results: Sequence[AdjudicatedCandidate],
    *,
    confirm_count: str | None,
    no_reconcile: bool = False,
    reconcile: bool = False,
    include_cross_source: bool = False,
    include_cross_type: bool = False,
) -> None:
    """The guarded batch `adjudicate --apply-same` merge (issue #137
    closing slice; de-presented onto `application.lifecycle.
    preview_apply_same`, issue #918 Slice 5): Pass 1 builds ONE aggregate
    preview over every eligible SAME 2-member group (spec: Aggregate
    Preview Before Any Write). `total` -- the number printed after the
    preview and required by the gate -- equals the number of preview lines
    ACTUALLY DISPLAYED (i.e. the eligible groups that still resolve right
    now), never the raw structural eligible-group count; a group that is
    already unresolvable when the preview is built (bogus/missing id,
    unrelated to this run) is silently excluded from the preview, the
    total, and Pass 2 (4R fix: preview/Total consistency). Zero eligible
    groups short-circuits with a "nothing to apply" summary and exit 0 --
    BEFORE the confirm gate -- mirroring `_run_adjudicate_apply`'s own
    empty-state handling, so an empty batch never triggers the non-TTY
    refusal or forces typing "0" on a TTY (4R fix: zero-eligible spurious
    failure). The confirmation gate (spec: Typed-Count Confirmation Gate)
    then requires the operator to type that EXACT count, via
    `--confirm-count`, an interactive TTY prompt, or refuses outright on a
    non-TTY without the flag -- any mismatch aborts with ZERO writes.
    Pass 2 RE-RESOLVES and RE-PREPARES each previewed pair immediately
    before applying it (spec: Stale-Id Guard Across Batch, issue #776's
    pinned direction -- `application_lifecycle.prepare_one_merge` is
    called again with `ordered_pair=pair.ordered`, NEVER reusing Pass 1's
    `PreparedMerge`), since an earlier merge in THIS SAME batch may already
    have absorbed a later pair's member; that legitimate case is still
    skipped, not crashed on, and still yields applied < previewed.
    Accepted merges commit sequentially via `merge_service.commit_merge`; a
    mid-batch failure stops the run but keeps every prior commit intact
    and reversible via `unmerge` -- and, before raising, echoes a partial
    summary (applied so far / previewed, and that the remainder was never
    attempted) so the operator can drive that recovery without
    reconstructing the count themselves (spec: Sequential Execution And
    Mid-Batch Failure Semantics; 4R fix: mid-batch failure hides the
    applied count).

    Pass 2's re-prepare narrows the batch's TOCTOU window but does not
    close it (the #306/#313/#319 arc): an edit landing during the confirm
    gate or an earlier pair's commit IS re-read and recomputed over, but
    every byte `merge_service.commit_merge` writes for pair k was still captured by
    that pair's re-prepare BEFORE the write, so an edit landing in the
    re-prepare-to-write gap would be silently destroyed -- likeliest on
    the survivor, worst on the absorbed file, which is UNLINKED rather
    than overwritten. Each pair therefore hands `_merge_drift_targets`'
    baseline mapping to `_reject_drifted_targets` strictly between its
    re-prepare and its write (issue #346, closing the gap `merge` and
    `curate`'s Identity stage already closed). A drift refusal on pair k
    aborts the batch exactly like a mid-batch write failure: the partial
    summary (applied so far of total, remainder never attempted, applied
    merges committed and reversible via `unmerge`) is echoed before the
    guard's exit 3 propagates, so the refusal keeps the same recovery
    affordance the failure path already has."""
    try:
        preview = application_lifecycle.preview_apply_same(
            root,
            layout,
            index_path,
            log_path,
            results,
            include_cross_source=include_cross_source,
            include_cross_type=include_cross_type,
        )
    except application_lifecycle.PreviewMergeFailure as exc:
        _echo_batch_pass1_skips(layout.bundle_dir, exc.partial)
        _echo_batch_pass1_items(
            exc.partial, no_reconcile=no_reconcile, reconcile=reconcile
        )
        typer.echo(
            "openkos adjudicate --apply-same: failed while previewing "
            f"{exc.absorbed_id} into {exc.survivor_id} -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    _echo_batch_pass1_skips(layout.bundle_dir, preview)
    _echo_batch_pass1_items(preview, no_reconcile=no_reconcile, reconcile=reconcile)
    total = len(preview.previewed)
    typer.echo(f"Total: {total}")

    skipped_n_gt2 = sum(
        1 for s in preview.skips if isinstance(s, application_lifecycle.NGt2Skip)
    )
    skipped_cross_source = sum(
        1 for s in preview.skips if isinstance(s, application_lifecycle.CrossSourceSkip)
    )
    skipped_cross_type = sum(
        1 for s in preview.skips if isinstance(s, application_lifecycle.CrossTypeSkip)
    )
    refused_stacked = sum(
        1 for i in preview.items if isinstance(i, application_lifecycle.StackedRefusal)
    )

    if total == 0:
        skipped_total = (
            skipped_n_gt2 + refused_stacked + skipped_cross_source + skipped_cross_type
        )
        prefix = "nothing to apply -- " if skipped_total == 0 else ""
        stacked_note = f", stacked-body: {refused_stacked}" if refused_stacked else ""
        cross_note = (
            f", cross-source: {skipped_cross_source}" if skipped_cross_source else ""
        )
        cross_type_note = (
            f", cross-type: {skipped_cross_type}" if skipped_cross_type else ""
        )
        typer.echo(
            f"openkos adjudicate --apply-same: {prefix}applied 0, skipped "
            f"{skipped_total} (N>2: {skipped_n_gt2}, already-merged: 0"
            f"{stacked_note}{cross_note}{cross_type_note})"
        )
        return

    if confirm_count is not None:
        typed_count = confirm_count
    elif sys.stdin.isatty():
        typed_count = typer.prompt(preview.confirmation.prompt)
    else:
        typer.echo(preview.confirmation.non_tty_refusal, err=True)
        raise typer.Exit(code=1)

    if not preview.confirmation.matches(typed_count):
        typer.echo(preview.confirmation.mismatch_abort, err=True)
        raise typer.Exit(code=1)

    applied = 0
    skipped_already_merged = 0
    for pair in preview.previewed:
        survivor_id, absorbed_id = pair.ordered
        try:
            prepared = application_lifecycle.prepare_one_merge(
                root,
                layout,
                index_path,
                log_path,
                pair.group,
                ordered_pair=pair.ordered,
            )
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos adjudicate --apply-same: failed while merging "
                f"{absorbed_id} into {survivor_id} -- {exc}.",
                err=True,
            )
            typer.echo(
                "openkos adjudicate --apply-same: stopped after failure -- "
                f"applied {applied} of {total} previewed before this "
                "failure; the remaining pairs were not attempted. Applied "
                "merges remain committed and reversible via `unmerge`.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        if prepared is None:
            typer.echo(
                f"{survivor_id} / {absorbed_id}: skipped (member unresolved "
                "-- already merged or missing)"
            )
            skipped_already_merged += 1
            continue

        # Issue #559: an earlier merge in THIS batch may have changed the
        # survivor's body, so the share is re-derived from Pass 2's
        # re-prepare -- a pair that crossed the guardrail since the preview
        # is refused here exactly as it would have been in Pass 1.
        if (
            prepared.stacked_body is not None
            and prepared.stacked_body.exceeds_guardrail
        ):
            typer.echo(
                _refused_stacked_line(
                    prepared.stacked_body,
                    prepared.absorbed_canonical,
                    prepared.survivor_canonical,
                )
            )
            refused_stacked += 1
            continue

        # #688: the batch's consent was the typed count above, so this is
        # the post-consent slot here too -- still before the drift
        # re-validation, matching every other consenting caller.
        prepared = _apply_reconciliation(
            root,
            prepared,
            no_reconcile=no_reconcile,
            reconcile=reconcile,
            verb="adjudicate --apply-same",
        )

        # Issue #346: every byte `merge_service.commit_merge` writes below was
        # captured by this pair's re-prepare above, so re-validate each
        # target now -- after the baseline capture, before the first
        # write. The absorbed file rides in `deletes=` because it is
        # UNLINKED, not overwritten (#329), mirroring `merge`'s own call
        # site. The guard's exit 3 is re-raised unchanged; the wrapper
        # exists only to echo the same partial summary the mid-batch
        # failure paths echo, so a drift abort leaves the operator the
        # same recovery affordance.
        # The commit phase (#1137): the reconciliation call above held no
        # workspace lock.
        with _commit_section_for(root)():
            absorbed_path = layout.bundle_dir / f"{prepared.absorbed_canonical}.md"
            try:
                _reject_drifted_targets(
                    layout,
                    application_lifecycle.merge_drift_targets(layout, prepared),
                    "adjudicate --apply-same",
                    deletes=frozenset({absorbed_path}),
                )
            except typer.Exit:
                typer.echo(
                    "openkos adjudicate --apply-same: stopped after drift "
                    f"refusal -- applied {applied} of {total} previewed before "
                    "this refusal; the remaining pairs were not attempted. "
                    "Applied merges remain committed and reversible via "
                    "`unmerge`.",
                    err=True,
                )
                raise

            try:
                merge_service.commit_merge(
                    root, layout, prepared, autocommit=_autocommit
                )
            except (OSError, ValueError) as exc:
                typer.echo(
                    "openkos adjudicate --apply-same: failed while merging "
                    f"{prepared.absorbed_canonical} into "
                    f"{prepared.survivor_canonical} -- {exc}.",
                    err=True,
                )
                typer.echo(
                    "openkos adjudicate --apply-same: stopped after failure -- "
                    f"applied {applied} of {total} previewed before this "
                    "failure; the remaining pairs were not attempted. Applied "
                    "merges remain committed and reversible via `unmerge`.",
                    err=True,
                )
                raise typer.Exit(code=1) from exc
        applied += 1

    skipped_total = (
        skipped_n_gt2
        + skipped_already_merged
        + refused_stacked
        + skipped_cross_source
        + skipped_cross_type
    )
    stacked_note = f", stacked-body: {refused_stacked}" if refused_stacked else ""
    cross_note = (
        f", cross-source: {skipped_cross_source}" if skipped_cross_source else ""
    )
    cross_type_note = (
        f", cross-type: {skipped_cross_type}" if skipped_cross_type else ""
    )
    typer.echo(
        f"openkos adjudicate --apply-same: applied {applied} of {total} "
        f"previewed, skipped {skipped_total} (N>2: {skipped_n_gt2}, "
        f"already-merged: {skipped_already_merged}{stacked_note}{cross_note}"
        f"{cross_type_note})"
    )

    # #640: once per batch, after Pass 2 -- never per `merge_service.commit_merge`.
    if applied:
        _refresh_derived_after_write(layout, None, verb="adjudicate")


def _slugify(stem: str) -> str:
    """Sanitize a filename stem OR a title into a slug.

    Delegates to `bundle.source_titles.slugify` (issue #918 Slice 2), which
    `application.query`'s `--save` filing composition also calls -- promoted
    there because the application layer may not import `openkos.cli` (the
    layering invariant), so this stays the single implementation rather than
    a duplicate that could silently drift (mirrors `_titleize`'s own
    delegation to `source_titles.titleize`, design D1). See
    `source_titles.slugify`'s docstring for the full Unicode-safety and
    identity rationale; every caller here (`ingest`, `application_ingest.stage_derived_objects`,
    tarball extraction) keeps its own empty-slug branch.
    """
    return source_titles.slugify(stem)


def _titleize(stem: str) -> str:
    """Turn a filename stem into a human-readable title: `-`/`_` -> spaces.

    Delegates to `bundle.source_titles.titleize` (design D1), promoted
    there so `ingest` and `backfill-source-titles` share exactly ONE
    implementation.
    """
    return source_titles.titleize(stem)


# `_TYPE_TO_LINK_DIR`/`_TYPE_TO_SECTION` are now derived from
# `openkos.model.types.REGISTRY` -- see that module for the single source of
# truth. `extraction.ExtractionResult.type` -> catalog section / bundle
# subdirectory (design: Path/Catalog).


_CAP_NOTICE_TITLE_LIMIT = 3
"""How many discarded titles the cap notice names before counting the rest.
A source that proposed 61 objects would otherwise dump 56 titles into the
terminal -- name enough to judge whether the loss mattered, then count."""


def _judge_cause_clause(report: ExtractionReport) -> str:
    """` (cause, cause)` for a notice, or `""` when none were recorded.

    #795 asks for the underlying error rather than only the outcome, because
    "timeout, parse failure, and backend refusal need different fixes and are
    currently indistinguishable". One entry per FAILED attempt, in attempt
    order, so two attempts failing differently both show -- collapsing them
    to the last would report a parse failure as if the backend had been fine.

    Returns the EMPTY string rather than a placeholder when nothing was
    recorded: a report from a path that never collected causes must not
    render a dangling `because:` with nothing after it.
    """
    if not report.judge_failure_causes:
        return ""
    return f" ({', '.join(report.judge_failure_causes)})"


def _unfiltered_source_notice(report: ExtractionReport) -> str | None:
    """Render the compound degrade (#795 point 3), or `None`.

    > Treat "ceiling truncation AND judge unavailable in the same
    > extraction" as its own louder state: individually each is degraded,
    > together the source is effectively unfiltered.

    On the reported file 3 both fired: 5 merged candidates never reached the
    judge, and the judge then failed, so all 24 survivors were kept with the
    positional cap skipped too. That source produced 24 objects filtered by
    nothing at all -- action items typed as `Decision` among them -- and the
    two existing lines each described only their own half.

    This ADDS a line; it never replaces either. Both halves remain true and
    an operator grepping for the ceiling notice must still find it, so the
    compound state is a third sentence rather than a rewrite of the other
    two. Kept as its own predicate for the same reason `_judge_cause_clause`
    is: a condition spelled inside an f-string is one no test can fail.
    """
    if report.judge_status != "failed" or report.pre_judge_dropped <= 0:
        return None
    return (
        f"this source is effectively UNFILTERED: {report.pre_judge_dropped} "
        "candidate(s) never reached the judge AND the judge gave up on the "
        f"rest, so none of the {report.retained} stored object(s) passed any "
        "quality gate"
    )


def _judge_failure_notice(report: ExtractionReport) -> str | None:
    """Render the union+judge failure-degrade notice (#456/#456), or `None`
    when the judge succeeded or was never invoked.

    Distinct wording from `_extraction_cap_notice` (a numeric cap firing)
    and from `_judge_selection_notice` (a successful judge selection) --
    spec: "a `_judge_failure_notice` distinct from `_judge_selection_notice`/
    `_extraction_cap_notice`". Fires on BOTH degrade statuses that keep the
    merged union instead of filtering it: `"failed"` (every attempt raised,
    returned an empty reply, or returned an unparseable/wrong-shape reply)
    and `"empty"` (#456 gate finding: a valid-shaped reply whose admitted set
    was empty). Each status renders distinct wording so the two degrade
    causes stay tellable apart in the terminal -- and the `"empty"` wording
    must be honest that the judge REPLIED (#644: a full-line echo landing
    here as "unavailable"-adjacent wording sent the reporter chasing a
    cold-load timeout that measurement falsified; the reply arrived fine, it
    just named no candidate).

    The two statuses no longer describe the same OUTCOME, which is why the
    shared sentence they used to share is gone (#754): `"empty"` is still
    backstop-capped, while `"failed"` is not capped at all. A single
    parametrized sentence would have had to stay silent about the difference
    or state it wrongly for one of the two."""
    if report.judge_status == "failed":
        # #754: the earlier wording -- "kept the full merged extraction union
        # (N object(s)) unfiltered" -- was accurate and still misled. It
        # named an OUTCOME and left the two consequences unsaid: that no
        # quality gate ran at all, and that a positional cap then cut the
        # unranked set by arrival order. The second is no longer true (the
        # cap is skipped now), and the first is what the operator has to know
        # to judge whether the stored objects were ever filtered.
        return (
            "judge selection unavailable after "
            f"{judge_mod.JUDGE_ATTEMPTS} attempts"
            f"{_judge_cause_clause(report)}; no quality selection ran, "
            f"so all {report.retained} merged candidate(s) were kept and the "
            "positional cap was NOT applied to them; marking the Source "
            f"(extraction_notice: {okf.EXTRACTION_NOTICE_JUDGE_UNAVAILABLE})"
        )
    if report.judge_status == "empty":
        return (
            "judge reply matched no candidate; kept the full merged "
            f"extraction union ({report.retained} object(s)) unfiltered"
            f"{_judge_cause_clause(report)}; "
            "marking the Source (extraction_notice: "
            f"{okf.EXTRACTION_NOTICE_JUDGE_EMPTY})"
        )
    if report.judge_failure_causes:
        # #795: the judge SUCCEEDED, on a retry, after failing at least once.
        # Reported anyway, because the retry is exactly what made the failure
        # rate invisible: a source whose first attempt failed reads identical
        # to one that never failed, so an operator watching a batch could not
        # see the 2-of-3 rate the issue measured. Advisory only -- nothing
        # degraded, nothing is marked on the Source.
        #
        # LAST of the branches, and that ordering is load-bearing. Causes are
        # recorded on every run that called the judge, INCLUDING the two that
        # degrade, so testing them earlier swallowed the `"failed"` and
        # `"empty"` notices whenever a retry had recovered along the way --
        # replacing a real degrade with "the selection itself is unaffected",
        # which is false exactly when it matters. Those two branches carry
        # the causes in their own wording instead.
        attempts = len(report.judge_failure_causes)
        return (
            f"judge selection succeeded on retry after {attempts} failed "
            f"attempt(s){_judge_cause_clause(report)}; the selection itself "
            "is unaffected"
        )
    return None


def _pre_judge_ceiling_notice(report: ExtractionReport) -> str | None:
    """Render the pre-judge ceiling drop notice, or `None` when the
    24-candidate ceiling (`concept._MAX_JUDGE_CANDIDATES`) cut nothing.

    Distinct wording from `_extraction_cap_notice` (the FINAL backstop cap
    firing on what survived selection) and from both judge notices (what the
    judge did or failed to do): these candidates were cut BEFORE the judge
    ever saw them, so they were never judged, dropped, or cap-discarded --
    they simply never reached the judge."""
    if report.pre_judge_dropped <= 0:
        return None
    line = (
        "merged extraction union exceeded the 24-candidate pre-judge "
        f"ceiling; {report.pre_judge_dropped} merged candidate(s) never "
        "reached the judge"
    )
    # #885: name what was cut, in the same shape every sibling notice uses.
    # ADDITIVE -- the count and the ceiling explanation above are what an
    # operator already reads to understand why extraction stopped short, and
    # a stored run from before `pre_judge_dropped_titles` existed carries
    # `()` here and still renders that half unchanged.
    if not report.pre_judge_dropped_titles:
        return line
    shown = report.pre_judge_dropped_titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(report.pre_judge_dropped_titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return f"{line}: {listed}"


def _chunk_skip_notice(report: ExtractionReport) -> str | None:
    """Name the `_chunk_lines` windows a chunked extraction skipped after
    their retry also failed (#1053), or `None` when every window answered
    -- the common case, and the ONLY case reachable before this change (a
    single window's `BackendError`-family failure used to discard the whole
    source's extraction instead of costing just that window).

    Named positions ("chunk N of M"), mirroring every sibling notice in
    this file: the reader has to be able to tell WHICH part of the source
    is missing from what got stored, not merely that something is.
    Positioned FIRST in `_render_staged_derived_objects`'s tuple, ahead of
    the wrong-language/judge/cap notices below -- a skipped chunk is the
    earliest thing that can go wrong in the pipeline, chronologically, and
    every later notice describes a decision made over whatever this one
    left behind."""
    if not report.skipped_chunks:
        return None
    positions = ", ".join(
        f"chunk {position} of {report.chunks}" for position in report.skipped_chunks
    )
    return (
        f"{len(report.skipped_chunks)} of {report.chunks} chunk(s) failed "
        "extraction after one retry and were skipped "
        f"({positions}); objects from every other chunk were kept -- "
        f"marking the Source (extraction_notice: "
        f"{okf.EXTRACTION_NOTICE_CHUNK_PARTIAL})."
    )


def _wrong_language_notice(report: ExtractionReport) -> str | None:
    """Render the deterministic wrong-language-title drop (#618), or `None`
    when the gate dropped nothing -- the common case, and the only possible
    case on the single-call and unchunked union paths, where the gate never
    runs.

    Mirrors `_extraction_cap_notice`'s shape (a count plus a bounded list
    of named titles), because the failure it discloses is the same kind: a
    candidate the model produced that the pipeline decided not to store.
    The titles are load-bearing here, not decoration -- a wrong-language
    title and a genuine subject are told apart by READING them, and the
    slug this gate protects is the permanent Concept ID."""
    if not report.wrong_language_dropped_titles:
        return None
    shown = report.wrong_language_dropped_titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(report.wrong_language_dropped_titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return (
        f"dropped {len(report.wrong_language_dropped_titles)} wrong-language "
        f"title(s) not quoted from the source: {listed}"
    )


def _recombined_title_notice(report: ExtractionReport) -> str | None:
    """Render the #630 recombination drop (#780), or `None` when that arm
    cut nothing. Separate from `_wrong_language_notice` because the two
    branches of the same gate diagnose opposite failures: a wrong-language
    vote says the model is leaking the other language (check the language
    anchor, consider a different model), a recombination says the model is
    inventing non-verbatim titles (look at extraction quality). The
    pre-#780 merged notice reported Spanish-titled recombinations as
    "wrong-language" drops on a Spanish source, pointing a whole
    investigation at the wrong subsystem."""
    if not report.recombined_dropped_titles:
        return None
    shown = report.recombined_dropped_titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(report.recombined_dropped_titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return (
        f"dropped {len(report.recombined_dropped_titles)} title(s) recombined "
        f"from the source's words, not quoted from it: {listed}"
    )


def _bounded_prompt_notice(report: ExtractionReport) -> str | None:
    """Render the whole-source prompt bound advisory (#866), or `None` when
    every prompt fit -- which is the common case.

    Advisory, not a degrade: the bound is what KEEPS a call's instructions
    intact where the server-side truncation it replaces silently cut them.
    It says nothing about whether each named call then SUCCEEDED -- a
    bounded judge can still fail to parse, and that failure reports through
    its own channel (`judge_status`, the causes, the optional-call
    failures) exactly as before. It is still surfaced, for the #795 reason
    the recovered-retry line is: a
    judge that read a quarter of the source reads identically to one that
    read all of it in the run's other output, and the operator deciding
    whether to raise `context_window` needs the fact and its cause on one
    line."""
    if not report.bounded_prompt_calls:
        return None
    calls = ", ".join(report.bounded_prompt_calls)
    return (
        "the source is larger than the model's context window, so "
        f"{len(report.bounded_prompt_calls)} call(s) ({calls}) read an "
        "even-coverage excerpt of it instead of the full text; raise "
        "context_window in openkos.yaml to widen what they see"
    )


def _reask_notice(report: ExtractionReport) -> str | None:
    """Render the bounded sole-twin re-ask notice (#584), or `None` when no
    re-ask was spent -- which is the common case.

    An extra model call is a cost the user pays, so it is reported rather
    than hidden, exactly like the pre-judge ceiling reports candidates the
    judge never saw. Distinct wording from every other notice here: nothing
    was dropped, judged, or capped -- a call was ADDED, and what it found
    was added with it.

    Both outcomes are surfaced, including "found nothing further": that is
    the answer the re-ask prompt names as correct for a genuinely
    single-subject source, and a spent call that changed nothing is still a
    spent call."""
    if report.reask_runs <= 0:
        return None
    if not report.reask_added_titles:
        return (
            "extraction returned one object restating the source title; "
            "1 extra re-ask call found nothing further"
        )
    shown = report.reask_added_titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(report.reask_added_titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return (
        "extraction returned one object restating the source title; "
        f"1 extra re-ask call added {len(report.reask_added_titles)} "
        f"object(s): {listed}"
    )


def _optional_call_failure_notice(report: ExtractionReport) -> str | None:
    """Name the optional extraction calls that added nothing BECAUSE their
    backend failed (#828), or `None` when none did -- the common case.

    `_reask_notice` and the participant-capture accounting report the COST
    of these calls, and both read the same whether the call answered or
    never got an answer at all. That is the gap: `_reask_for_further_subjects`
    and `_capture_further_participants` swallowed every backend failure into
    `[]`, so a runaway that burned 222 seconds against the 8192-token
    generation ceiling printed as a bonus call that honestly found nothing
    further. An operator watching a batch could not see it.

    ADVISORY, and worded to say so: nothing degraded beyond the bonus call
    adding nothing, no object was lost, and no `extraction_notice` is
    stamped on the Source. The degrade vocabulary belongs to the judge
    notices, which describe a source whose objects skipped quality
    selection; borrowing it here would send an operator looking for damage
    that is not there.

    Its OWN function and its OWN call site, deliberately NOT a branch inside
    `_judge_failure_notice`. That function's branch ordering is documented
    as load-bearing -- its recovered-retry branch is last precisely so it
    cannot swallow a real degrade -- and it is reached through an `or`
    chain, so any branch added there would suppress a sibling notice.

    Every entry is named, with no `_CAP_NOTICE_TITLE_LIMIT` truncation. The
    bound is structural rather than a count written down here: `extraction`
    contributes at most ONE entry per OPTIONAL call -- the calls
    `concept.OPTIONAL_CALL_REASK` and
    `concept.OPTIONAL_CALL_PARTICIPANT_CAPTURE` name -- so the tuple's length
    is the number of those calls and nothing a source's content can inflate.
    That, not a literal two, is what rules out the terminal-flooding case
    `_CAP_NOTICE_TITLE_LIMIT` exists for; a cap here would only hide one of
    the calls this notice exists to tell apart. A later third optional call
    therefore widens this line by exactly one NAMED entry, which is the
    intended behavior and is pinned rather than assumed -- see
    `test_optional_call_failure_notice_names_every_entry_it_is_given`.
    """
    if not report.optional_call_failures:
        return None
    failures = report.optional_call_failures
    return (
        f"{len(failures)} optional extraction call(s) failed and added "
        f"nothing ({', '.join(failures)}); advisory only -- the extracted "
        "objects are unaffected and the Source is not marked"
    )


def _sole_object_notice(report: ExtractionReport) -> str | None:
    """Render the honest-degrade notice for a source whose SOLE derived
    object restates it (#585), or `None` -- the common case.

    Distinct from `_reask_notice`, and the two deliberately co-occur on the
    run that matters. That one reports a COST (an extra call was spent);
    this one reports an OUTCOME (what the bundle ended up storing). A
    re-ask that found nothing prints both, and neither is redundant: the
    user paid for a second question, and the answer was that there is
    genuinely nothing else here.

    It also fires without any re-ask at all -- on the union path where the
    judge reduced the set, or wherever `sole_object_restates_source`
    survived -- so it must not be folded into the re-ask notice's branch.

    Wording states BOTH halves of #585's chosen criterion, because a line
    that named only the problem would read as a warning about something the
    tool failed to do. Keeping the object is the decision, not a fallback:
    a genuinely single-subject source is indistinguishable from this defect
    by title alone, so dropping would emit `[]` for real content."""
    if not report.sole_object_restates_source:
        return None
    return (
        "the only derived object restates this source; keeping it and "
        "marking the Source (extraction_notice: "
        f"{okf.EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES})"
    )


def _judge_selection_notice(report: ExtractionReport) -> str | None:
    """Render the union+judge SUCCESSFUL-selection notice (#456), naming
    what the judge dropped, or `None` when the judge kept everything, was
    never invoked, or failed (handled by `_judge_failure_notice` instead).

    Mirrors `_extraction_cap_notice`'s shape (a count plus a bounded list of
    named titles) for the judge's OWN drop, distinct from the FINAL numeric
    cap: a judge-dropped title is never also a cap-discarded title, since
    `report.discarded_titles` is built from what SURVIVED judge selection
    (see `extract_concept_union`'s docstring).

    The titles are QUOTED (issue #805, item 2), and this is the FIRST
    notice in this block to quote them -- every sibling here lists titles
    bare, so there was no existing style to match and this one sets it.
    The report that asked for it read:

        openkos ingest: judge dropped 1 candidate(s): Schema migration ownership
        openkos ingest: proposed changes:
          + bundle/decisions/schema-migration-ownership-decision.md

    Nothing was wrong there -- the surviving title is one word longer, and
    re-admission is `Person`/`Organization`-only, so it could not have
    restored the dropped candidate -- but two lines differing by a single
    word read as the tool contradicting itself. The ambiguity is exactly
    where each title ENDS, which is what a quote settles; single quotes by
    default, because that is how this module already quotes a name or a
    path.

    That default is `repr`'s, not a hand-written `f"'{title}'"` (issue
    #814, item 3), and `repr` is what supplies the ONE case where the
    delimiter is not a single quote. A title carrying its own apostrophe
    -- `Marta's plan` -- rendered as `'Marta's plan'` under the
    hand-written form, which reads as ending after `Marta`: the case that
    most needs the delimiter was the one it failed on, and the reliability
    lens flagged it on two independent candidates. `repr` resolves that
    from one rule -- swap to double quotes when, and only when, the title
    holds an apostrophe, and escape only when it holds BOTH shapes -- so
    the single-quote default above still describes every other title, and
    an ordinary one renders byte-identically to the form this replaces.
    Non-ASCII is left intact either way.

    It also escapes a newline or other control character the old form
    passed through verbatim, which keeps this notice to ONE line. That is
    a narrowing, not a cost: titles come from model extraction over
    arbitrary source text, and a title carrying a newline used to split
    the notice so that its tail arrived looking like a line openkos had
    printed itself.
    """
    if report.judge_status != "ok" or not report.judged_out_titles:
        return None
    shown = report.judged_out_titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(report.judged_out_titles) - len(shown)
    # Quote each title INDIVIDUALLY, and leave `(+N more)` outside the
    # quotes: it is this notice's own bookkeeping, never part of a title.
    listed = ", ".join(repr(title) for title in shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    # The rejected alternative, recorded so it is not re-opened: printing
    # this list AFTER the proposed-changes block instead. (1) This line
    # goes to stderr and that block to stdout, so their adjacency in the
    # report is a terminal interleave, not a stream ordering -- moving it
    # would change only what a TTY user happens to see. (2) The line sits
    # in a notice block whose order is deliberate and documented
    # (wrong-language -> recombined -> re-ask -> pre-judge ceiling ->
    # judge -> participant-unreadmitted -> participant-ungrounded -> cap),
    # and `_participant_unreadmitted_notice` is documented as rendering
    # immediately after the judge notice it qualifies; moving the judge
    # line alone would separate that pair.
    return f"judge dropped {len(report.judged_out_titles)} candidate(s): {listed}"


def _participant_unreadmitted_notice(report: ExtractionReport) -> str | None:
    """Name what actually discarded a `Person`/`Organization` candidate, or
    `None` when nothing did (issue #690).

    Without this the operator sees only `judge dropped 2 candidate(s): Jason
    Sepulveda, Gustavo Martínez` and reasonably concludes the judge rejected
    them on merit. What actually happened is TWO decisions: the judge did not
    select them, AND re-admission declined to save them.

    Those are different findings with different remedies -- "the judge has
    poor taste in people" invites tuning the judge, while the real second
    decision points somewhere else entirely. Reporting the first when the
    second is true is what made the earlier runs unfalsifiable.

    #712 retired the anchor gate, and with it the only reason this list could
    have two causes. Since then a participant reaches this list for exactly
    one reason: the SOURCE was not meeting-shaped, so participant re-admission
    never applied to it. The wording says that, because the old text -- "no
    role, affiliation, or relation cue" -- would now send the operator to look
    for a cue that is no longer read anywhere.

    The field itself is a COMPLEMENT (`judge_input` minus `kept`), not a cause,
    so this notice states a cause the field cannot prove on its own. It holds
    because the re-admission conjunct admits EVERY `_PARTICIPANT_TYPES`
    candidate once `meeting_shaped` is true, which leaves a non-meeting source
    as the only way in. Narrow that conjunct again -- a budget lane that drops
    participants past a cap is the obvious candidate, and slice 3 is exactly
    that -- and this wording becomes a lie before any test notices. Re-derive
    it there; `test_participant_unreadmitted_notice_names_the_real_second_decision`
    records the reasoning but cannot detect a second cause appearing."""
    if not report.participant_unreadmitted_discarded_titles:
        return None
    titles = report.participant_unreadmitted_discarded_titles
    shown = titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return (
        f"{len(titles)} participant candidate(s) not re-admitted after the "
        f"judge dropped them -- this source is not meeting-shaped, so "
        f"participant re-admission does not apply to it: {listed}."
    )


def _participant_ungrounded_notice(report: ExtractionReport) -> str | None:
    """Name participants the SOURCE never writes, or `None` when every name
    is grounded (#712 design D5).

    Advisory, and worded to say so: these objects were stored. The check is
    deliberately not a filter -- the owner ruling is that every named person
    is identified, and a rejecting version would delete a real person the
    moment a source writes `G. Vega` and the model proposes `Germán Vega`.

    It points at the SOURCE on purpose. The retired anchor gate reported a
    candidate as lacking a 'role, affiliation, or relation cue', which sent
    the operator to a lexicon they could not act on. A name the source never
    writes is checkable by opening the source."""
    if not report.participant_names_absent_from_source:
        return None
    titles = report.participant_names_absent_from_source
    shown = titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return (
        f"{len(titles)} participant name(s) not found in the source text -- "
        f"stored anyway, but worth checking against the source: {listed}."
    )


def _unevidenced_notice(report: ExtractionReport) -> str | None:
    """Name the stored objects whose written text quotes no line of the
    source (#801), or `None` when every object quotes one -- the common
    case, and the one #801 measured (seven of eight objects in the reported
    run carried a quoted line).

    Mirrors `_participant_ungrounded_notice`'s shape, because it discloses
    the same kind of thing: objects that WERE stored and are worth checking
    against the source. Advisory, and worded to say so.

    The titles are load-bearing, not decoration. A notice that only counted
    would leave the operator opening every derived document to find the
    one, and the whole value of this check is that it discriminates -- an
    advisory naming the healthy majority alongside the defect is one nobody
    reads twice.

    The line says what the reader can DO about it, which for this notice is
    not a re-run: an object with no quoted span cannot support a citation,
    so the check is against the source itself. `lint` names the same
    Source under `Unevidenced objects:` once the run is over."""
    if not report.unevidenced_titles:
        return None
    titles = report.unevidenced_titles
    shown = titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return (
        f"{len(titles)} derived object(s) carry no line quoted from the "
        f"source -- stored anyway, but they cannot support a citation; "
        f"check them against the source: {listed}."
    )


def _extraction_cap_notice(report: ExtractionReport) -> str | None:
    """Render the `_MAX_OBJECTS_PER_SOURCE` truncation notice, or `None` when
    the cap did not fire (#404).

    Mirrors `resolution.edge_typing.candidate_truncation_notice` -- the same
    `"{retained} of {produced} ... (cap reached)"` shape #378 established for
    candidate edges, so the two truncations in this product read alike -- and
    extends it with the discarded titles, because the measurement behind #404
    showed a bare count cannot tell a reader whether the cap cost them a real
    subject or trimmed a decayed tail of near-duplicates.

    Returns `None` on the healthy path rather than an empty string, so the
    caller renders on truncation alone with no special-casing, and an
    advisory never fires when there is nothing to advise.
    """
    if report.produced <= report.retained:
        return None
    shown = report.discarded_titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(report.discarded_titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return (
        f"{report.retained} of {report.produced} extracted object(s) kept "
        f"(cap reached); discarded: {listed}"
    )


# `_stale_index_names` moved verbatim into `application/status.py` as the
# public `stale_index_names` (issue #995, PR 3) -- shared by `status`'s own
# service and this module's `query` command (below), which imports it as
# `application_status.stale_index_names` rather than forking a local copy
# (`tests/unit/application/test_layering.py::
# test_shared_read_predicates_are_never_forked` is what makes that
# checkable).

# `DerivedPlan` and the three collision helpers moved verbatim into
# `application/ingest.py` (issue #918, Slice 1) -- bound back here under
# their original private names via plain assignment (not an `import ... as`
# with a renamed target) so mypy's strict `no_implicit_reexport` does not
# flag `main._collision_family` et al., which `tests/unit/cli/test_ingest.py`
# still calls directly. `_stage_derived_objects` itself moved into
# `application/ingest.py` in Slice 2, de-presented -- see
# `application_ingest.stage_derived_objects` and this module's
# `_render_staged_derived_objects` (the adapter half: renders exactly the
# same wording the old function body used to echo, from the typed
# `StagedDerivedObjects` the service now returns).
_DerivedPlan = application_ingest.DerivedPlan
_collision_family = application_ingest.collision_family
_family_owns_source = application_ingest.family_owns_source
_first_free_disambiguated_slug = application_ingest.first_free_disambiguated_slug


# `StackedBodyReport`/`PreparedMerge`/`MergeResult` and the id-resolution
# helpers moved verbatim into `application/lifecycle.py` (issue #918 Slice
# 1). The three TYPES are bound back here under their original names, the
# same "plain assignment, not a renamed import" shape as `_DerivedPlan`
# above, so every quoted forward-ref annotation still elsewhere in this
# module (`_apply_reconciliation`, `merge_service.commit_merge`,
# `_refused_stacked_line`) resolves unchanged.
# `_canonicalize_concept_id`/`_resolve_concept_path`/`_merge_drift_targets`
# and `_member_body_length`/`_ordered_merge_pair`/`_cross_source_same_pair`/
# `_cross_type_concern`/`_prepare_one_merge`/`_reconcile_planned` are NOT
# aliased back onto this module (issue #955; they briefly were after issue
# #918 Slice 5). `application/lifecycle.py`'s own internal calls to these
# names (e.g. `preview_apply_same` calling `ordered_merge_pair`,
# `cross_source_same_pair`, `cross_type_concern`, `prepare_one_merge`, and
# `resolve_concept_path`, or `ordered_merge_pair`'s own two calls to
# `member_body_length`) resolve by module-local name inside
# `application/lifecycle.py`, so a
# `monkeypatch.setattr("openkos.cli.main._X", ...)` patch only ever reached
# this module's call sites and silently diverged from the service-internal
# walk -- the two paths disagreed on which function ran while the patched
# test stayed green. Every call site -- in this module and in
# `cli/curate.py` -- goes through `application_lifecycle.<name>` by module
# attribute instead.
#
# `prepare_merge`/`merge_core` are, and remain, DELIBERATELY NOT aliased
# (design: "the one thing that would re-open the trap") -- both carry two
# dangerous `test_adjudicate.py` patch sites apiece, where a stale
# `monkeypatch.setattr("openkos.cli.main.prepare_merge"/"merge_core", ...)`
# must raise `AttributeError` rather than silently no-op. Every call site in
# this module -- `merge` itself, `application.lifecycle.prepare_one_merge`,
# `merge_service.commit_merge` -- therefore calls
# `application_lifecycle.prepare_merge`/`merge_core` by module attribute.
StackedBodyReport = application_lifecycle.StackedBodyReport
PreparedMerge = application_lifecycle.PreparedMerge
MergeResult = application_lifecycle.MergeResult


# `PreparedRelate`/`prepare_relate`/`relate_core` and
# `PreparedSetVolatility`/`prepare_set_volatility`/`set_volatility_core`
# moved verbatim into `application/lifecycle.py` (issue #959). NONE of the
# six is aliased back onto this module, and that includes the two
# dataclasses -- which is where this block's shape deliberately does NOT
# apply.
#
# `PreparedMerge` and its two siblings above are bound back because quoted
# forward-ref annotations elsewhere in this module (`_apply_reconciliation`,
# `merge_service.commit_merge`, `_refused_stacked_line`) still name them. These two
# have no such reader: after the move, `grep PreparedRelate` and `grep
# PreparedSetVolatility` find nothing in this module but this comment, and
# every call site -- here, in `cli/curate.py`, and in the tests -- reaches
# the pair through `application_lifecycle.<name>`. An alias nothing reads
# is the injection seam issue #955 deleted nine of and issue #974 promoted
# the last private helper out of; adding two fresh ones here, justified by
# a forward-ref that does not exist, would re-open that trap. So a stale
# `monkeypatch.setattr("openkos.cli.main.PreparedRelate"/"prepare_relate"/
# "relate_core"/"prepare_set_volatility"/"set_volatility_core", ...)`
# raises `AttributeError` rather than silently no-op, for all six names.


def _render_staging_drop(drop: application_ingest.StagingDrop) -> None:
    """Render one `StagingDrop`'s exact original wording (issue #918 Slice
    2) -- the per-candidate `typer.echo` calls that used to live inline in
    `_stage_derived_objects`'s staging loop, now driven by `drop.kind`
    instead."""
    if drop.kind == "empty-slug":
        typer.echo(
            "openkos ingest: extracted title could not be turned into a "
            "slug; skipping this candidate.",
            err=True,
        )
    elif drop.kind == "in-batch-collision":
        typer.echo(
            f"openkos ingest: duplicate slug '{drop.slug}' within "
            "this extraction batch; keeping the first, skipping this "
            "candidate.",
            err=True,
        )
    elif drop.kind == "already-exists":
        typer.echo(
            f"openkos ingest: '{drop.slug}' already exists; "
            "skipping this candidate (create-only).",
            err=True,
        )
    elif drop.kind == "disambiguated":
        typer.echo(
            f"openkos ingest: '{drop.slug}' already exists for a "
            f"different source; disambiguating this candidate to "
            f"'{drop.disambiguated_to}'.",
            err=True,
        )
    else:  # "build-failed"
        typer.echo(
            f"openkos ingest: extracted content failed validation -- "
            f"{drop.error}; skipping this candidate.",
            err=True,
        )


def _render_staged_derived_objects(
    staged: application_ingest.StagedDerivedObjects,
) -> None:
    """Render every echo `stage_derived_objects` used to print itself
    before issue #918 Slice 2 (design: "the service returns typed
    disclosure data; the adapter owns every word") -- byte-identical
    wording, in the SAME order, now driven by `StagedDerivedObjects`
    instead of inline `typer.echo` calls inside the (former) function body.

    Called by `_ingest_single` AFTER the `Console(...).status(...)` spinner
    context exits, on both the success and the `BackendError` path (design:
    "Ordering invariant that makes this byte-identical") -- exactly mirrors
    the pre-move function, where every echo except the two pre-extraction
    degrades already ran after that `with` block unwound.

    The `"failed"` `skip_reason` (an `BackendError` was caught) is
    deliberately NOT handled here: that echo (plus the #746 concurrency
    advisory) fires at the `except BackendError` call site itself, because it
    needs the caught exception, which `StagedDerivedObjects` never carries.
    """
    if staged.report is None:
        if staged.skip_reason == "no-extractable-text":
            typer.echo(
                "openkos ingest: source has no extractable text; keeping "
                "the Source only.",
                err=True,
            )
        elif staged.skip_reason == "blocked-by-sensitivity":
            typer.echo(
                "openkos ingest: workspace default_sensitivity floor is "
                "confidential; skipping concept extraction, keeping the "
                "Source only. The Source is still added to the embedding "
                "index so search and candidate relations keep working -- "
                "the sensitivity floor governs `llm.chat`, not embeddings.",
                err=True,
            )
        return

    report = staged.report
    for notice_text in (
        _chunk_skip_notice(report),
        _wrong_language_notice(report),
        _recombined_title_notice(report),
        _bounded_prompt_notice(report),
        _reask_notice(report),
        _optional_call_failure_notice(report),
        _pre_judge_ceiling_notice(report),
        (_judge_failure_notice(report) or _judge_selection_notice(report)),
        _unfiltered_source_notice(report),
        _participant_unreadmitted_notice(report),
        _participant_ungrounded_notice(report),
        _unevidenced_notice(report),
        _extraction_cap_notice(report),
        _sole_object_notice(report),
    ):
        if notice_text is not None:
            typer.echo(f"openkos ingest: {notice_text}", err=True)

    if staged.skip_reason == "no-concepts-found":
        typer.echo(
            "openkos ingest: no concept extracted from this source; "
            "keeping the Source only.",
            err=True,
        )
        return

    for drop in staged.drops:
        _render_staging_drop(drop)

    if staged.lost_in_staging:
        typer.echo(
            f"openkos ingest: {staged.lost_in_staging} extracted "
            "candidate(s) could not be staged and were dropped; marking "
            "the Source (extraction_notice: "
            f"{okf.EXTRACTION_NOTICE_CANDIDATES_DROPPED}).",
            err=True,
        )


def _resolve_local_exemption(
    client: application_backends.HasLocality, cfg: config.Config
) -> bool:
    """One-line delegator (mcp-read-surface slice 8, design Decision 7): the
    real definition, and its full docstring, now live in
    `application/backends.py` -- moved there for the same reason as
    `_chat_client` above. Kept under this name so the ~200 existing
    references to `_resolve_local_exemption` by name are unaffected.

    Typed against `HasLocality` (not `OllamaClient`, issue #1057 Phase 9):
    `_chat_client` may now return an `OpenAICompatibleClient` too, and both
    satisfy this narrower structural Protocol."""
    return application_backends.resolve_local_exemption(client, cfg)


def _warn_if_nonlocal_embed_host(
    command: str, locality: BackendHostLocality, cfg: config.Config
) -> None:
    """One stderr advisory when the embedding host is not literally this
    machine (issue #199): document text and embedding vectors are about to
    be POSTed to it, and a user who exported `OLLAMA_HOST` for some other
    tool may not realize openkos inherits it.

    ADVISORY only, by contract: never blocks, never raises, never changes
    an exit code -- `classify_backend_host` is a pure literal check (no DNS,
    no network) that degrades unparseable values to "warn" instead of
    raising, and its `display_host` is userinfo-redacted on every path, so
    a credentialed value can never leak a password here (the withdrawn
    #183-PR3 predecessor's two CRITICALs). An unset/empty `OLLAMA_HOST`
    means Ollama's own local default: silent. Over-warning is the accepted
    failure direction; staying silent about data leaving the machine is
    not.

    `locality` is now PASSED IN rather than computed from `os.environ` here
    (issue #240): it comes from the embedding client's own
    `OllamaClient.locality`, i.e. the host the embed will actually POST to.
    The env read this replaced ignored an explicit `host=` argument, so it
    could stay silent about a client demonstrably sending off-machine. The
    advisory's own contract is untouched -- only the source of the fact it
    reports changed, and it is now the same authority the confidential local
    exemption reads, so the two can never disagree about one host."""
    if locality.is_local:
        return
    endpoint_name = application_backends.endpoint_label(cfg, purpose="embed")
    typer.echo(
        f"openkos {command}: note -- embedding host '{locality.display_host}' "
        f"is not this machine ({endpoint_name}); document text and embedding "
        "vectors will leave this machine.",
        err=True,
    )


def _warn_withheld_from_embedding(
    command: str, withheld: int, cfg: config.Config
) -> None:
    """One stderr line naming the documents the embed gate held back (#922).

    The counterpart to `_warn_if_nonlocal_embed_host`, and needed for the
    same reason that advisory was: silence here is indistinguishable from
    success. A withheld document has no vector, so it is invisible to dense
    retrieval -- an operator who is not told will meet the loss later, as an
    answer that inexplicably never cites the document they were asking
    about.

    It names the two levers rather than only the fact, because both are
    legitimate: point the backend at this machine, or lower the document's
    sensitivity if it was mis-classified. Silent on zero -- a local backend
    withholds nothing and must stay quiet.

    `cfg` (issue #1057 Phase 13b) names WHICH config key/environment
    variable the operator should point at this machine, via
    `endpoint_label` -- `OLLAMA_HOST` for the `ollama` backend
    (byte-identical to before), `base_url`/`embedding_base_url` for
    `openai-compatible`."""
    if withheld <= 0:
        return
    endpoint_name = application_backends.endpoint_label(cfg, purpose="embed")
    typer.echo(
        f"openkos {command}: {withheld} document{_plural(withheld)} withheld "
        "from embedding -- their sensitivity blocks sending them to a backend "
        "that is not this machine, so they have no vector and dense retrieval "
        f"will not surface them. Point {endpoint_name} at this machine and "
        "re-run, or lower the sensitivity if it is wrong.",
        err=True,
    )


def _embed_after_ingest(
    layout: config.WorkspaceLayout,
    embedder: Embedder,
    *,
    cfg: config.Config,
    model_tag: str,
    embedding_backend: str = config.DEFAULT_BACKEND,
    warn_nonlocal_host: bool = True,
    local_exemption: bool = False,
    commit_section: lock_wait.CommitSection | None = None,
) -> None:
    """Embed the concepts `ingest` just wrote, so candidate edges are
    available in the SAME run (#183).

    Without this, `vectors.db` stays absent until a separate `openkos
    reindex`, so a user's first `suggest-relations` after ingesting always
    reports an empty graph -- the symptom issue #183 opens with.

    FAIL-OPEN, and deliberately so. Embeddings are an enhancement layered
    onto ingest; the Source and its concepts are already written and
    COMMITTED by the time this runs. Losing embeddings must never cost the
    user the ingest itself, so every ordinary exception from the embed
    itself degrades to one stderr notice and an unchanged exit code.

    Scope of that promise, stated precisely rather than overclaimed: it
    covers the embed. It does not cover a failure to WRITE the notice --
    a `BrokenPipeError` from a closed downstream pipe still propagates, as
    it does from every other `typer.echo` in this module. Making stderr
    writes unkillable is a repo-wide concern, not this function's.

    The `except Exception` is broad ON PURPOSE, mirroring
    `vectorstore.probe_vec_loadable`'s rationale: not just the three mapped
    `BackendError` subclasses, but any exception a backend might raise that
    nobody anticipated. `KeyboardInterrupt` and `SystemExit` derive from
    `BaseException`, so a user's Ctrl-C still interrupts the command rather
    than being mistaken for a degraded embed.

    Reuses `state.reindex.reindex` rather than embedding here (design
    Decision D), and passes NO `fts_db_path`: this helper runs once PER
    FILE inside a batch, so building FTS here would make an N-file ingest
    pay N full-text rebuilds. The FTS build ingest DOES perform (issue
    #553) lives in `_refresh_derived_after_write` (#640 widened it to
    graph+vectors), invoked exactly once at the END of each `ingest` run.

    `warn_nonlocal_host=False` suppresses ONLY the non-local embedding-host
    advisory: `_ingest_batch` emits that advisory itself, once per batch
    invocation (the batch cost-gate precedent), so its per-file runs must
    not repeat it N times (issue #353, item 4). Everything else here --
    the embed, its fail-open degrade notices -- is unchanged either way.

    `local_exemption` is threaded straight through to `reindex` (#922) and
    NOT re-derived here: it is resolved at the call site, from the very
    client passed in as `embedder`. Its `False` default is fail-closed, so a
    future caller that forgets it withholds a confidential document rather
    than sending one.

    `commit_section` (#1137) is the section that takes the workspace lock:
    the embedding calls run without it and only the vector-store write enters
    it, with a content-hash re-check (`state.reindex.reindex`). `None` holds
    nothing, for a caller already inside the lock."""
    # BEFORE the embed attempt, so the notice lands even when the embed
    # itself then degrades: the advisory is about where the data is headed,
    # not about whether it arrived (#199).
    if warn_nonlocal_host:
        _warn_if_nonlocal_embed_host(
            "ingest", cast(BackendDiagnostics, embedder).locality, cfg
        )
    try:
        with open_vector_store(layout.vectors_db_path) as db:
            report = reindex_module.reindex(
                layout.bundle_dir,
                db,
                embedder,
                model_tag=model_tag,
                embedding_backend=embedding_backend,
                local_exemption=local_exemption,
                commit_section=commit_section,
            )
    except Exception as exc:  # noqa: BLE001 -- a failed embedding refresh degrades to a notice, never aborts the ingest
        typer.echo(
            f"openkos ingest: embeddings not updated -- {exc}; candidate "
            "relations unavailable until `openkos reindex` succeeds.",
            err=True,
        )
        return

    # An exception is not the only way embedding degrades. `reindex` treats
    # a generic `BackendError` as a PER-DOC transient failure and folds it
    # into `embed_failed` instead of raising -- only `BackendUnavailable` and
    # `BackendModelNotFound` are fatal enough to propagate. Reporting solely
    # on exceptions would therefore let a run where nothing was embedded
    # look identical to a clean one, and the user would meet the silence
    # later, as an inexplicably empty `suggest-relations`.
    if report.embed_failed:
        typer.echo(
            f"openkos ingest: embeddings not updated for {report.embed_failed} "
            f"doc{_plural(report.embed_failed)}; candidate relations may be "
            "incomplete until `openkos reindex` succeeds.",
            err=True,
        )
    _warn_withheld_from_embedding("ingest", report.withheld_confidential, cfg)


def _refresh_derived_after_write(
    layout: config.WorkspaceLayout,
    cfg: config.Config | None,
    *,
    verb: str,
    warn_nonlocal_host: bool = True,
    commit_section: lock_wait.CommitSection | None = None,
) -> bool:
    """Refresh the three derived stores as part of the write that just
    invalidated them (issue #640), returning True iff every store is fresh.

    Before #640, only `reindex`, `purge`, and `ingest`'s end-of-run FTS
    build (#553) ever wrote a derived store -- every other mutating verb
    left them behind, and the stale-index warnings (`status`/`next`/query's
    stale line) fired only AFTER a degraded answer. This helper generalizes
    #553's proven end-of-run pattern to every bundle-writing verb: called
    ONCE per invocation, at END of run, strictly AFTER the verb's
    successful write + autocommit -- never per file or per stage, the cost
    decision that shaped #553 in the first place. Call sites are all on the
    success path: a refused, declined, or failed write invalidated nothing
    and must not refresh (nor pay for one).

    CHEAP-FIRST ordering, and it is load-bearing: FTS and graph are pure
    SQLite projections of the bundle (no Ollama), refreshed before the
    vector attempt, so an embedder failure can never leave them stale --
    the degrade path keeps lexical retrieval and the graph current even
    when embeddings die. One consequence, accepted deliberately: the graph
    rebuild nominates proximity-candidate edges from the PRE-refresh
    `vectors.db`. For a frontmatter-only write that store is unchanged
    anyway (#554 excludes frontmatter from embeddings -- zero embed calls,
    pure cache hits), and for content writes the next content change or a
    manual `openkos reindex --force` heals the nomination set.

    Each stage degrades independently (a dead FTS5 module must not cost the
    graph its refresh), but every failure folds into ONE stderr advisory
    naming what was skipped and the manual fallback -- never N lines, never
    the exit code. FAIL-OPEN with `except Exception` ON PURPOSE, mirroring
    `_embed_after_ingest`'s rationale verbatim: the bundle write is already
    COMMITTED by the time this runs, so no refresh failure -- the full
    mapped ladder (`BackendUnavailable`, `BackendModelNotFound`,
    `BackendEmbeddingDimensionMismatch`, `VecUnavailable`, `FtsUnavailable`,
    `BackendError`, `sqlite3.Error` including lock contention) or anything
    nobody anticipated -- may cost the user the write itself. Ctrl-C still
    interrupts: `KeyboardInterrupt`/`SystemExit` derive from
    `BaseException`.

    `cfg` is optional because not every call site has one in scope (`merge`
    never reads config); `None` reads it here, inside the vector stage's
    own fail-open envelope, so a corrupt `openkos.yaml` degrades the
    refresh instead of crashing a verb that never needed config before.
    The vector refresh reuses the exact `state.reindex.reindex` wiring the
    `reindex` verb uses -- the content-hash cache keeps it incremental --
    with the same TTY-gated progress idiom (#190) and NO `fts_db_path`
    (stage 1 already owns FTS). The stale-index warning tiers are
    deliberately untouched: they remain the safety net for this helper's
    own degrade path.

    `commit_section` (#1137) is for a verb that does NOT hold the workspace lock
    for its whole run: the FTS and graph refreshes (no model call) run inside it,
    and the vector refresh runs its embedding calls outside it and enters it only
    for the store write. `None` (every other verb, which already holds the lock)
    changes nothing."""
    failures: list[str] = []
    section: lock_wait.CommitSection = (
        commit_section if commit_section is not None else nullcontext
    )

    try:
        with section():
            reindex_module._reindex_fts(
                layout.bundle_dir, layout.fts_db_path, force=False
            )
    except Exception as exc:  # noqa: BLE001 -- a failed FTS refresh is collected into the degrade summary
        failures.append(f"fts: {exc}")

    try:
        with section():
            with_candidates = _open_proximity_or_degrade(layout.vectors_db_path)
            try:
                sqlite_graph.reindex_graph(
                    layout.bundle_dir,
                    layout.graph_db_path,
                    force=False,
                    candidates=with_candidates,
                )
            finally:
                if with_candidates is not None:
                    with_candidates.close()
    except Exception as exc:  # noqa: BLE001 -- a failed graph refresh is collected into the degrade summary
        failures.append(f"graph: {exc}")

    try:
        if cfg is None:
            cfg = config.read_config(layout.root)
        embedder = _embed_client(cfg)
        # #922: this seam embedded every document and did not even emit the
        # non-local host advisory the other two embed paths have carried
        # since #199 -- a write-time refresh against a remote `OLLAMA_HOST`
        # sent document text off the machine in complete silence. Both the
        # advisory and the gate now match `ingest`'s and `reindex`'s.
        #
        # `warn_nonlocal_host=False` suppresses ONLY this line, for the verbs
        # that already emitted it themselves before reaching here -- exactly
        # the once-per-invocation contract #353 item 4 established for
        # `_embed_after_ingest`. The gate below is never suppressed.
        embedder_locality = cast(BackendDiagnostics, embedder)
        if warn_nonlocal_host:
            _warn_if_nonlocal_embed_host(verb, embedder_locality.locality, cfg)
        with open_vector_store(layout.vectors_db_path) as db:
            report = reindex_module.reindex(
                layout.bundle_dir,
                db,
                embedder,
                model_tag=cfg.embedding_model,
                embedding_backend=cfg.backend,
                on_progress=observability.progress_callback(verb, "embedding doc"),
                local_exemption=_resolve_local_exemption(embedder_locality, cfg),
                commit_section=commit_section,
            )
        _warn_withheld_from_embedding(verb, report.withheld_confidential, cfg)
        # An exception is not the only way embedding degrades: `reindex`
        # folds a generic per-doc `BackendError` into `embed_failed` instead
        # of raising (same trap `_embed_after_ingest` documents), so a run
        # that embedded nothing must not report a complete refresh.
        if report.embed_failed:
            failures.append(
                f"vectors: {report.embed_failed} doc"
                f"{_plural(report.embed_failed)} could not be embedded"
            )
    except Exception as exc:  # noqa: BLE001 -- a failed vector refresh is collected into the degrade summary
        failures.append(f"vectors: {exc}")

    if failures:
        typer.echo(
            f"openkos {verb}: derived-index refresh incomplete -- "
            f"{'; '.join(failures)}; run 'openkos reindex' to finish.",
            err=True,
        )
        return False
    return True


def _echo_event_date_preview_line(
    resolution: application_ingest.EventDateResolution,
) -> None:
    """Print the ONE `event_date` preview line (design.md Decision 7),
    stdout, right after the `bundle/sources/{slug}.md` line -- or nothing,
    when `resolution.value is None` (ingestion spec: "No line is printed
    when no event date is recorded"). Printed BEFORE the confirm gate, so
    an inferred or overwritten value is reviewed before anything is
    written.

    `origin == "flag"` prints the `replacing` clause only when the value
    actually CHANGED from a real prior value -- an unchanged re-supplied
    flag (`changed` is `False`) or a fresh flag with no prior value
    (`previous is None`) both print the plain form."""
    if resolution.value is None:
        return
    value = resolution.value.isoformat()
    if resolution.origin == "flag":
        if resolution.changed and resolution.previous is not None:
            typer.echo(
                f"    event date {value} (from --event-date, replacing "
                f"{resolution.previous.isoformat()})"
            )
        else:
            typer.echo(f"    event date {value} (from --event-date)")
    elif resolution.origin == "file name":
        typer.echo(f"    event date {value} (from the file name)")
    elif resolution.origin == "frontmatter":
        typer.echo(f"    event date {value} (from the source's frontmatter date)")
    else:
        # origin == "kept" -- the only remaining non-`None` origin
        # (`resolve_event_date`'s vocabulary: "flag" | "file name" | "kept"
        # | "frontmatter", design.md Decision 5).
        typer.echo(f"    event date {value} (kept from the existing Source)")


def _echo_type_alternative_summary(
    derived_count: int, pairs: Sequence[tuple[str, str]]
) -> None:
    """Emit the ONE aggregate disclosure line for torn classifications
    (#566): `{n} of {m} derived object(s) recorded a type_alternative`,
    naming the most common `{type}/{alternative}` pair. Silent when no
    object recorded one -- an advisory that fires on the healthy path is
    noise. stderr, like every other ingest notice, so the stdout batch
    contract (#349) is untouched."""
    if not pairs:
        return
    counts = Counter(pairs)
    (primary, alternative), _ = counts.most_common(1)[0]
    qualifier = "all" if len(counts) == 1 else "most common"
    typer.echo(
        f"openkos ingest: {len(pairs)} of {derived_count} derived object(s) "
        f"recorded a type_alternative on the document "
        f"({qualifier}: {primary}/{alternative}).",
        err=True,
    )


def _echo_type_floor_summary(
    derived_count: int, pairs: Sequence[tuple[str, str]]
) -> None:
    """Emit the ONE aggregate born-above-floor disclosure line (issue #669,
    design D4), and, only when at least one raised object landed on
    `confidential`, the #569 retrieval-exclusion consequence line.

    Mirrors `_echo_type_alternative_summary`'s shape exactly: silent when
    `pairs` is empty (an advisory that fires on the healthy path is
    noise), stderr like every other ingest notice so the stdout batch
    contract (#349) stays untouched, naming the most common `{type} ->
    {level}` pair when more than one distinct pair is present."""
    if not pairs:
        return
    counts = Counter(pairs)
    (primary_type, primary_level), _ = counts.most_common(1)[0]
    qualifier = "all" if len(counts) == 1 else "most common"
    typer.echo(
        f"openkos ingest: {len(pairs)} of {derived_count} derived object(s) "
        f"were born above the workspace sensitivity floor by type default "
        f"({qualifier}: {primary_type} -> {primary_level}).",
        err=True,
    )
    if any(level == "confidential" for _type, level in pairs):
        typer.echo(
            "openkos ingest: confidential objects are excluded from query, "
            "contradictions, and suggest-relations against a non-local "
            "backend (#569).",
            err=True,
        )


_GLOB_MAGIC_CHARS = frozenset("*?[")
"""Glob magic characters (the character class `glob.has_magic` matches): a
`src` that is neither an existing file nor a directory but contains one of
these is treated as a quoted glob pattern, not a missing file (issue #267)."""


_TEXT_SOURCE_EXTENSIONS = frozenset(
    {".adoc", ".markdown", ".md", ".org", ".rst", ".srt", ".text", ".txt", ".vtt"}
)
"""Extensions directory/glob EXPANSION keeps (#568): prose documents and
transcript formats. A user pointing `ingest` at a project folder must not
sweep `.DS_Store`, lockfiles, code, or binaries into the bundle -- the
degrade path handles them without crashing, but each one still becomes a
permanent Source document. Matched case-insensitively on `Path.suffix`, so
extensionless files (`LICENSE`, `Makefile`) are skipped too. An EXPLICIT
single-file path bypasses this entirely: naming one exact file is a choice,
expansion is a sweep."""


def _expand_batch_sources(src: Path) -> tuple[list[Path], int] | None:
    """Route `ingest`'s `src` argument (issue #267): return `None` when it
    must take the existing single-file path unchanged, or a `(matches,
    skipped_non_text)` pair for the batch path -- the SORTED list of
    text-source files kept, plus how many expanded files the
    `_TEXT_SOURCE_EXTENSIONS` allowlist dropped (#568), for the batch's
    pre-flight disclosure line.

    `None` covers two cases: an existing plain FILE (single-file behavior
    stays byte-identical -- checked FIRST, so even a filename containing a
    glob magic character keeps today's behavior when the file exists), and
    a nonexistent, magic-free path (which keeps today's exact "does not
    exist or is not a readable file" refusal).

    A DIRECTORY matches every readable file (`os.access(..., R_OK)`)
    directly inside it -- non-recursive, so subdirectories (`.git/`, nested
    workspaces, anything) are never walked into; recursion is available
    only via an explicit `**` glob. A path containing a glob magic
    character (`*`, `?`, `[`) is expanded with `glob.glob(...,
    recursive=True)` relative to the cwd, keeping only matched FILES (a
    bare `**` also matches directories; those are dropped).

    Both expansions sort by the path STRING (`key=str`), never filesystem
    order, so `log.md` entries and the per-file commits are reproducible
    across machines (settled decision 4). May raise `OSError` (an
    unreadable directory); the `ingest` command maps that to its usual
    stderr refusal."""
    if src.is_file():
        return None
    if src.is_dir():
        candidates = sorted(
            (
                entry
                for entry in src.iterdir()
                if entry.is_file() and os.access(entry, os.R_OK)
            ),
            key=str,
        )
    elif any(char in str(src) for char in _GLOB_MAGIC_CHARS):
        # `glob.glob`, deliberately not `Path.glob` (PTH207): pathlib
        # refuses an absolute pattern outright (`NotImplementedError`) and
        # rebases every relative match onto its base directory, where
        # `glob.glob` accepts both forms and echoes each match in the
        # pattern's OWN relative/absolute shape -- which is exactly what the
        # batch report and its outcome lines print back to the user.
        candidates = sorted(
            (
                match_path
                for match in glob.glob(str(src), recursive=True)  # noqa: PTH207
                if (match_path := Path(match)).is_file()
            ),
            key=str,
        )
    else:
        return None
    matches = [
        path for path in candidates if path.suffix.lower() in _TEXT_SOURCE_EXTENSIONS
    ]
    return matches, len(candidates) - len(matches)


def _refuse_basename_collisions(matches: Sequence[Path]) -> None:
    """Batch Phase A (issue #267, settled decision 1): the destination name
    and slug derive ONLY from each file's basename -- the single-file
    path-traversal defense (`Path(src).name`), deliberately not weakened
    here -- so two matched files sharing a basename would fight over the
    same `raw/<name>`: the second would refuse against (or silently
    re-ingest) the first's freshly written copy. Detect this BEFORE ANY
    write and refuse the WHOLE run (exit 1), naming every colliding path;
    nothing is written."""
    by_name: dict[str, list[Path]] = {}
    for path in matches:
        by_name.setdefault(path.name, []).append(path)
    collisions = {name: group for name, group in by_name.items() if len(group) > 1}
    if not collisions:
        return
    for name, group in collisions.items():
        paths = " and ".join(f"'{path}'" for path in group)
        typer.echo(
            f"openkos ingest: refusing the whole batch -- basename collision: "
            f"{paths} would all land as 'raw/{name}' (destination names "
            "derive only from the basename); nothing was written. Rename "
            "one, or ingest them separately.",
            err=True,
        )
    raise typer.Exit(code=1)


@dataclass(frozen=True)
class _BatchCallEstimate:
    """What the batch cost gate announces (#775): a fan-out-aware estimate
    of the model calls this run will spend, plus the two per-file facts the
    operator needs to judge it -- which sources will split into windows,
    and which will be skipped outright by #773's convergence short-circuit."""

    calls: int
    chunked_files: int
    chunked_windows: int
    """Window total across every chunking file -- the `~N window(s)` the
    gate names, same unit as the per-chunk progress counter."""
    skipped_files: int


def _reingest_will_skip(src: Path, layout: config.WorkspaceLayout) -> bool:
    """Predict #773's convergence skip for one batch file, read-only and
    fail-open (#775): `True` only when the SAME conditions `_ingest_single`
    checks all hold -- a byte-identical raw match resolving to an existing
    Source that records an `origin_key` and carries no retryable-debt
    marker (`application_ingest.extraction_retry_due`, the shared predicate). Any read or
    parse failure returns `False`, which merely over-states the estimate;
    the authoritative decision stays inside `_ingest_single`."""
    try:
        origin_key = okf.origin_key_for(src)
        destination = application_ingest.resolve_raw_destination(
            src, layout, origin_key
        )
        if not destination.regenerate:
            return False
        slug = _slugify(Path(destination.name).stem)
        concept_path = layout.bundle_dir / "sources" / f"{slug}.md"
        if not concept_path.exists():
            return False
        metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if metadata.get(okf.ORIGIN_KEY_KEY) is None:
        return False
    return not application_ingest.extraction_retry_due(metadata)


def _estimate_batch_calls(
    matches: list[Path],
    layout: config.WorkspaceLayout,
    cfg: config.Config,
    *,
    include_confidential: bool,
    re_extract: bool,
) -> _BatchCallEstimate:
    """Sum fan-out-aware per-file estimates for the batch cost gate (#775),
    replacing the `{n} file(s) -> {n} LLM call(s)` identity that was wrong
    by 7x on a chunking source: the unit of cost is the WINDOW (plus the
    judge and the participant pass), not the file, and
    `extraction.concept.estimate_extraction_calls` owns that arithmetic so
    this gate cannot re-derive the thresholds.

    Per-file zeros mirror the pipeline's own no-LLM paths: an undecodable
    or blank source degrades to Source-only, the confidential floor gate
    skips extraction wholesale unless `--include-confidential`, and a file
    `_reingest_will_skip` predicts converges under #773. The title fed to
    the estimator is derived exactly as `_ingest_single` derives it, so a
    meeting-shaped transcript is estimated on the meeting threshold."""
    calls = 0
    chunked_files = 0
    chunked_windows = 0
    skipped_files = 0
    floor_gated = cfg.default_sensitivity == "confidential" and not include_confidential
    for path in matches:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # undecodable -> Source-only, no model call
        except OSError:
            # Fail OPEN, distinct from undecodable: a file the gate cannot
            # READ right now (permissions, a race) may still extract fine
            # when the run reaches it, so it is billed at the unchunked
            # ordinary cost rather than silently dropped from the count --
            # the spec's "billed at the full estimate, never silently
            # dropped". Without the text there is no window arithmetic to
            # do, so the unchunked estimate is the honest floor.
            calls += 3 if cfg.union_judge else 1
            continue
        if not text.strip():
            continue  # blank -> no extractable text, no model call
        if floor_gated:
            continue  # S3b: extraction never reaches the LLM
        if not re_extract and _reingest_will_skip(path, layout):
            skipped_files += 1
            continue
        derived = source_title.derive_source_title(text)
        title = derived if derived is not None else _titleize(path.stem)
        estimate = estimate_extraction_calls(
            text, source_title=title, union_judge=cfg.union_judge
        )
        calls += estimate.calls
        if estimate.windows:
            chunked_files += 1
            chunked_windows += estimate.windows
    return _BatchCallEstimate(
        calls=calls,
        chunked_files=chunked_files,
        chunked_windows=chunked_windows,
        skipped_files=skipped_files,
    )


def _ingest_batch(
    src: Path,
    matches: list[Path],
    *,
    auto: bool,
    include_confidential: bool,
    skipped_non_text: int = 0,
    re_extract: bool = False,
) -> None:
    """Drive every file in `matches` (already expanded and sorted by
    `_expand_batch_sources`) through the EXISTING single-file pipeline
    (`_ingest_single`) -- reuse, never reimplementation: the per-file
    ingestion, its per-ingest auto-commit (settled decision 3: PER-FILE
    commit granularity, so an interrupted run leaves a committed,
    consistent workspace and a re-run is idempotent for completed files),
    and its post-commit embedding all run unchanged, once per file, with
    `--include-confidential` forwarded unchanged (issue #267).

    Batch Phase A, before ANY write: an empty match set refuses (exit 1);
    the workspace check runs once up front (the same
    `config.require_workspace` refusal each per-file run would hit, but
    surfaced BEFORE the cost gate can prompt); then
    `_refuse_basename_collisions` refuses the whole run on a basename
    collision (settled decision 1).

    Cost gate (settled decision 4, the #134 pattern): before any LLM
    contact, `{n} file(s) -> {n} LLM call(s)` is printed to stderr and ONE
    up-front confirmation is asked, with the single-file gate's exact
    precedence mirrored -- `--auto` skips it, config `review: false` skips
    it the same way, a TTY asks (`abort=True`: decline exits 1, nothing
    written), and non-TTY stdin without `--auto` refuses to write. That
    single batch-level consent covers every file: each per-file run is
    invoked with the prompt suppressed the way `--auto` suppresses it
    today (`auto=True`), never 40 per-file prompts.

    The non-local embedding-host advisory (#199) follows the same
    once-per-run consolidation (issue #353, item 4): it is emitted here,
    once, up front (before the loop), and every per-file run is invoked
    with `warn_nonlocal_embed_host=False` so `_embed_after_ingest` does
    not repeat it N times. Same wording, same stderr, still advisory-only;
    a local (or unset) host stays silent exactly as before.

    Per-file failure isolation (settled decision 2): a per-file refusal
    (`typer.Exit` from `_ingest_single`, its reason already on stderr --
    including a drift refusal's exit 3) SKIPS that file and CONTINUES; a
    per-file extraction failure stays non-fatal exactly as today
    (Source-only degrade, stderr note) and is only TALLIED here. Progress
    is `i/N` on stderr via the TTY-gated `observability.progress_callback`
    (issue #190) -- silent when piped. The run ends with per-file outcome
    lines plus an aggregate summary on stdout -- outcome lines FIRST, the
    summary as the batch's last word (issue #349).

    That summary carries five terms: ingested / re-ingested / skipped /
    extraction-degraded / with-extraction-notice(s) (issue #805, item 1).
    The last one is the newest and the widest: it counts a file whose
    Source finished carrying ANY `okf.ExtractionNotice` token, including
    #585's `sole-object-restates-source`, which no later surface flags.
    See the comment above the summary line itself for why that set is
    deliberately wider than `lint`'s "Unjudged extractions".

    Exit ladder (issue #349): 0 when every file succeeded (idempotent
    re-ingests count as success); 3 when EVERY skip was the per-file
    pipeline's drift refusal (exit 3, #319) -- nothing those files would
    have written was written, so the batch inherits the single-file retry
    guarantee a script relies on; 1 when ANY skip was a hard refusal --
    a plain re-run would refuse again, so the batch must not advertise
    retryability it cannot deliver (#234: distinct causes must not read
    alike)."""
    root = Path.cwd()
    if skipped_non_text:
        # Pre-flight disclosure (#568), BEFORE the empty-match refusal and
        # the cost gate: says what expansion actually kept, so a directory
        # full of code refusing with "no files matched" explains itself,
        # and the cost gate's count covers exactly what will be ingested.
        # Silent when the allowlist dropped nothing -- the cost gate
        # already names the matched count on the healthy path.
        typer.echo(
            f"openkos ingest: {len(matches)} file(s) matched; "
            f"{skipped_non_text} skipped as non-text.",
            err=True,
        )
    if not matches:
        typer.echo(
            f"openkos ingest: refusing to ingest -- no files matched '{src}'; "
            "nothing was written.",
            err=True,
        )
        raise typer.Exit(code=1)
    workspace_reason = config.require_workspace(root)
    if workspace_reason is not None:
        typer.echo(
            f"openkos ingest: refusing to ingest -- {workspace_reason}.",
            err=True,
        )
        raise typer.Exit(code=1)
    _refuse_basename_collisions(matches)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos ingest: failed while preparing the batch -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    total = len(matches)
    if not auto and cfg.review:
        # #775: a fan-out-aware estimate, computed only when the gate will
        # actually ask -- the unit of cost is the window, not the file, and
        # a gate wrong by 7x fails at its only job. `suggest-relations`
        # stays exact because there the unit really is 1:1; here the
        # number is honest about being an estimate.
        estimate = _estimate_batch_calls(
            matches,
            config.WorkspaceLayout(root),
            cfg,
            include_confidential=include_confidential,
            re_extract=re_extract,
        )
        details = []
        if estimate.skipped_files:
            details.append(
                f"{estimate.skipped_files} unchanged -- extraction will be skipped"
            )
        if estimate.chunked_files:
            details.append(
                f"{estimate.chunked_files} will be split into "
                f"~{estimate.chunked_windows} window(s)"
            )
        detail_note = f" ({'; '.join(details)})" if details else ""
        # #872's sibling: over ~0 calls (every file skipped or unbillable)
        # the pace warning is false; the count stays labelled an estimate.
        pace_note = "estimate; this can take a while" if estimate.calls else "estimate"
        typer.echo(
            f"{total} file(s){detail_note} -> ~{estimate.calls} LLM call(s) "
            f"({pace_note}). Pass --auto to skip this prompt.",
            err=True,
        )
        if sys.stdin.isatty():
            typer.confirm("Proceed?", abort=True)
        else:
            typer.echo(
                "openkos ingest: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    # ONE advisory for the whole batch (the cost-gate precedent), before
    # the loop: each per-file run below suppresses its own copy
    # (issue #353, item 4). The client is built here ONLY to read the host
    # it resolved (issue #240) -- construction performs no I/O, and every
    # per-file run builds its own identical client for the actual embed.
    _warn_if_nonlocal_embed_host(
        "ingest",
        cast(BackendDiagnostics, _embed_client(cfg)).locality,
        cfg,
    )

    progress = observability.progress_callback("ingest", "ingesting file")
    outcome_lines: list[str] = []
    ingested_count = 0
    reingested_count = 0
    degraded_count = 0
    noticed_count = 0
    skipped_count = 0
    hard_skip_count = 0
    derived_total = 0
    alternative_pairs: list[tuple[str, str]] = []
    type_floor_pairs: list[tuple[str, str]] = []
    for index, path in enumerate(matches, start=1):
        if progress is not None:
            progress(index, total, path)
        try:
            outcome = _ingest_single(
                path,
                auto=True,
                include_confidential=include_confidential,
                warn_nonlocal_embed_host=False,
                re_extract=re_extract,
            )
        except typer.Exit as exc:
            # The per-file pipeline already printed its own refusal reason
            # to stderr (unchanged single-file wording); this line only
            # records the skip in the batch report and moves on. Whether
            # the skip was a drift refusal (exit 3, the ONE retryable
            # failure, #319) or a hard refusal feeds the exit ladder below.
            skipped_count += 1
            if exc.exit_code != 3:
                hard_skip_count += 1
            outcome_lines.append(
                f"  ! {path} -- skipped (refused with exit code "
                f"{exc.exit_code}; its reason is on stderr above)"
            )
            continue
        derived_total += outcome.derived_count
        alternative_pairs.extend(outcome.alternative_pairs)
        type_floor_pairs.extend(outcome.type_floor_pairs)
        if outcome.extraction_notice:
            # #884: ONE count per FILE, however many conditions it carries.
            # The term measures files, which is what an operator reads it
            # as, so a Source disclosing two conditions must not inflate it
            # to two. `lint` is the surface that enumerates every condition.
            #
            # Counts EVERY member of `okf.ExtractionNotice`, not just the
            # two judge tokens -- see the summary's own docstring block
            # below for why this is deliberately a WIDER set than `lint`'s
            # "Unjudged extractions" section reports.
            noticed_count += 1
        if outcome.regenerated:
            reingested_count += 1
            marker, label = "~", "re-ingested"
        else:
            ingested_count += 1
            marker, label = "+", "ingested"
        suffix = ""
        if outcome.extraction_degraded:
            degraded_count += 1
            suffix = " (extraction degraded -- Source only; see stderr)"
        elif outcome.extraction_skipped:
            # #773: an unchanged, already-extracted source spent no model
            # call -- said on its outcome line, so a batch reader can tell
            # a converged no-op from a re-extraction at a glance.
            suffix = " (unchanged -- extraction skipped)"
        outcome_lines.append(f"  {marker} {path} -- {label}{suffix}")

    # End-of-run derived refresh (issue #553, widened to graph+vectors by
    # #640): ONCE for the whole batch, after the loop -- never per file.
    # Runs even when every file was skipped: the manifest gates make that a
    # hash check, and a workspace that never had an fts.db gets one built.
    # Fail-open (stderr only), so it can never change the exit ladder below.
    _refresh_derived_after_write(
        config.WorkspaceLayout(root),
        cfg,
        verb="ingest",
        # The batch already emitted the advisory once, up front (#353 item 4).
        warn_nonlocal_host=False,
        commit_section=_commit_section_for(root),
    )

    # ONE torn-classification aggregate for the WHOLE batch (#566), on
    # stderr before the stdout report so the stdout contract (#349) keeps
    # its promised shape: outcome lines first, batch summary last.
    _echo_type_alternative_summary(derived_total, alternative_pairs)
    _echo_type_floor_summary(derived_total, type_floor_pairs)

    # Per-file outcome lines FIRST, the aggregate summary as the batch's
    # last word -- the order the docstrings and docs/cli.md promise
    # (issue #349).
    for line in outcome_lines:
        typer.echo(line)
    # `noticed_count` counts a file whose Source finished carrying ANY
    # `okf.ExtractionNotice` token -- all SIX of them, not only the two
    # retryable judge causes. Still a WIDER set than any single `lint`
    # section, but the margin narrowed with #801/#843 and the reason
    # changed with them. `lint` reports four of the six across three
    # sections: the two judge tokens under "Unjudged extractions"
    # (`lint._UNJUDGED_NOTICE_CAUSES`, which `application_ingest.extraction_retry_due`
    # matches exactly), `objects-without-evidence` under "Unevidenced
    # objects" (`lint._UNEVIDENCED_NOTICE`), and
    # `candidates-dropped-in-staging` under "Staging-dropped candidates"
    # (`lint._STAGING_DROP_NOTICE`). #585's `sole-object-restates-source`
    # and #1053's `chunk-extraction-partial` are the two tokens no `lint`
    # section flags -- the sole-object one is a disclosure with no repair
    # to name; the chunk-skip one is disclosed on stderr (`_chunk_skip_
    # notice`, naming the exact chunk) and self-heals on the next plain
    # re-ingest (`extraction_retry_due`), so a dedicated `lint` section
    # would only repeat what the run already said and what the next
    # ordinary re-ingest already fixes -- which is precisely why the run's
    # last word must still say it happened for BOTH. Read the two numbers
    # as different questions: this term answers "how many files finished
    # with something disclosed on the Source", `lint` answers "how many of
    # those have a next step `lint` itself can name".
    #
    # The term never double-counts `extraction-degraded`. That one counts a
    # `skip_reason` (Source-only, zero derived objects), and
    # `application_ingest.stage_derived_objects` returns the two on mutually exclusive
    # paths. Since #843 a notice no longer presupposes a written object --
    # a run whose every candidate was lost in staging carries the staging
    # marker with zero objects -- but that state returns `skip_reason is
    # None`, so the exclusivity holds by construction, not by count.
    notice_pointer = ""
    if noticed_count:
        # Only when there is something to recover -- an advisory that fires
        # on the healthy path is noise (the `_echo_type_*_summary` rule).
        # It names BOTH surfaces honestly: the frontmatter key carries
        # every kind, `lint` flags all but #585's sole-object disclosure and
        # #1053's chunk-partial disclosure. It said "the retryable ones"
        # until #801 added a token that is NOT retryable debt and that
        # `lint.check_unevidenced` reports anyway -- wording a reader with
        # only that notice would have taken to mean `lint` had nothing for
        # them.
        notice_pointer = (
            " Their Sources carry `extraction_notice`; `openkos lint` "
            "names all but the sole-object and chunk-partial disclosures."
        )
    typer.echo(
        f"openkos ingest: batch summary -- {total} file(s): "
        f"{ingested_count} ingested, {reingested_count} re-ingested, "
        f"{skipped_count} skipped, {degraded_count} extraction-degraded, "
        f"{noticed_count} with extraction notice(s).{notice_pointer}"
    )
    if skipped_count:
        # Exit ladder (issue #349): every skip a drift refusal -> exit 3,
        # inheriting the single-file retry contract (#319); any hard
        # refusal in the mix -> exit 1, because a plain re-run would
        # refuse again.
        raise typer.Exit(code=1 if hard_skip_count else 3)


@app.command(
    help=(
        "Ingest a file, a directory, or a glob's matches into the bundle: "
        "extracts concepts, writes them as documents, and updates the "
        "catalog."
    ),
    rich_help_panel="Get started",
)
@_guard_workspace_lock("ingest", commit_phase=True)
def ingest(
    src: Path = typer.Argument(
        ...,
        help=(
            "Path to a raw source file, a directory of source files, or a "
            "quoted glob pattern to copy into the workspace."
        ),
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help=(
            "Bypass the workspace default_sensitivity floor gate on concept "
            "extraction (excluded by default)."
        ),
    ),
    re_extract: bool = typer.Option(
        False,
        "--re-extract",
        help=(
            "Run extraction again on an unchanged, already-extracted source "
            "(a byte-identical re-ingest skips it by default, #773)."
        ),
    ),
    event_date: str | None = typer.Option(
        None,
        "--event-date",
        metavar="YYYY-MM-DD",
        # Traceability (issue #1014c, ADR-0023) stays here in source, never
        # in the published `help=` text below (#389).
        help=(
            "The recorded Source's event_date -- a calendar date naming "
            "when the recorded event happened, distinct from the ingest "
            "timestamp. Precedence on re-ingest: this flag, then the "
            "stored value, then the file name, then unset. Applies to a "
            "single file only -- refused with a directory or glob src."
        ),
    ),
) -> None:
    """Ingest one source file, a whole directory, or a glob's matches into
    the workspace in a single invocation (issue #267).

    A plain existing FILE keeps the exact single-file behavior documented
    on `_ingest_single` -- copy into `raw/`, one OKF Source concept,
    bounded LLM extraction, one confirm gate, per-ingest auto-commit. A
    DIRECTORY ingests every readable TEXT-SOURCE file directly inside it
    (non-recursive; subdirectories are never walked into). A quoted GLOB
    (detected by its magic characters `*`, `?`, `[`; expanded relative to
    the cwd, recursion only via an explicit `**`) ingests every matched
    text-source file. Both expansions apply the `_TEXT_SOURCE_EXTENSIONS`
    allowlist (#568) and disclose any skips in one pre-flight line before
    the cost gate; an explicit single-file path bypasses the filter. Matched files are SORTED by path string -- never filesystem
    order -- so `log.md` and the per-file commits are reproducible across
    machines.

    The batch drives each matched file through the SAME single-file
    pipeline, in order, each with its own per-ingest auto-commit
    (an interrupted run leaves every completed file committed; re-running
    is idempotent for them). Before any write: a basename collision
    between two matched files (destination names derive only from the
    basename -- the path-traversal defense) refuses the WHOLE run, exit 1,
    naming both paths. Before any LLM contact: one up-front cost gate
    prints `{n} file(s) -> {n} LLM call(s)` and asks ONCE -- that single
    consent covers every file, so the per-file prompt is suppressed the
    way `--auto` suppresses it today; `--auto` (or config `review: false`)
    skips the gate, and non-TTY stdin without `--auto` refuses to write,
    mirroring the single-file convention. A per-file refusal skips that
    file (reason on stderr) and CONTINUES; per-file outcome lines plus an
    aggregate summary (ingested / re-ingested / skipped /
    extraction-degraded / with extraction notice(s)) close the run, in that
    order. The batch exits 0 when every file succeeded (re-ingests count as
    success), 3 when every skip was a drift refusal (the retryable failure,
    exit 3 per file), and 1 when any skip was a hard refusal (issue #349). An empty
    directory or a glob matching nothing refuses (exit 1, nothing
    written). See `_ingest_batch` for the full batch contract.

    `--event-date` (issue #1014c, ADR-0023, design.md Decision 5) is
    validated and refused, exit 2, BEFORE `_expand_batch_sources` runs and
    before any read of the workspace: an unparseable value, and a
    directory or glob `src` (regardless of how many files it matches,
    including exactly one -- decided by the input's SHAPE, not its match
    count) both refuse with no write of any kind.
    """
    parsed_event_date: date | None = None
    if event_date is not None:
        parsed_event_date = source_date.parse_event_date(event_date)
        if parsed_event_date is None:
            typer.echo(
                "openkos ingest: --event-date must be a calendar date "
                f"written YYYY-MM-DD, got {event_date!r}.",
                err=True,
            )
            raise typer.Exit(code=2)
        if not src.is_file() and (
            src.is_dir() or any(char in str(src) for char in _GLOB_MAGIC_CHARS)
        ):
            typer.echo(
                "openkos ingest: --event-date applies to a single file; "
                f"'{src}' is a directory or a glob. Ingest each file with "
                "its own --event-date, or name the date in each file name.",
                err=True,
            )
            raise typer.Exit(code=2)

    try:
        expansion = _expand_batch_sources(src)
    except OSError as exc:
        typer.echo(
            f"openkos ingest: failed while expanding '{src}' -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    if expansion is None:
        outcome = _ingest_single(
            src,
            auto=auto,
            include_confidential=include_confidential,
            re_extract=re_extract,
            event_date=parsed_event_date,
        )
        # ONE aggregate torn-classification line per run (#566), printed by
        # the command -- never by `_ingest_single`, which the batch path
        # calls once per file.
        _echo_type_alternative_summary(outcome.derived_count, outcome.alternative_pairs)
        _echo_type_floor_summary(outcome.derived_count, outcome.type_floor_pairs)
        # End-of-run derived refresh (issue #553, widened by #640): AFTER
        # the single-file pipeline returned -- a refusal raises `typer.Exit`
        # above and skips this, matching the batch path (nothing new was
        # committed). `cfg=None`: the single-file path reads config inside
        # `_ingest_single`, not here, and the helper reads its own copy
        # fail-open.
        _refresh_derived_after_write(
            config.WorkspaceLayout(Path.cwd()),
            None,
            verb="ingest",
            # Already emitted by this run's own embed (#353 item 4).
            warn_nonlocal_host=False,
            commit_section=_commit_section_for(Path.cwd()),
        )
        return
    matches, skipped_non_text = expansion
    _ingest_batch(
        src,
        matches,
        auto=auto,
        include_confidential=include_confidential,
        skipped_non_text=skipped_non_text,
        re_extract=re_extract,
    )


class _CliIngestObserver(ingest_service.IngestObserver):
    """Renders what `ingest_source` reports: advisory lines to stderr, the
    preview and the import summary to stdout, the extraction wait as a
    spinner on a TTY."""

    def notice(self, message: str) -> None:
        typer.echo(message, err=True)

    def extraction_starting(self) -> None:
        # ONE TTY-gated stage notice before the single long extraction call
        # (issue #190) -- `ingest` has no per-item loop to hook, so
        # `stage_notice` is the single-call sibling of `progress_callback`.
        observability.stage_notice(
            "ingest", "extracting derived objects (waiting on the LLM)..."
        )

    @contextmanager
    def extraction_progress(self) -> Iterator[ingest_service.PhaseHook | None]:
        with Console(stderr=True).status(
            "openkos ingest: extracting concepts…"
        ) as status:
            yield observability.phase_callback("ingest", status.update)

    def staged(self, staged: application_ingest.StagedDerivedObjects) -> None:
        _render_staged_derived_objects(staged)

    def preview(self, preview: ingest_service.IngestPreview) -> None:
        _echo_ingest_preview(preview)

    def imported(self, summary: ingest_service.ImportedSummary) -> None:
        typer.echo(
            f"openkos ingest: imported '{summary.source}' -> "
            f"{', '.join(summary.imported_paths)} "
            f"({summary.index_name}, {summary.log_name} updated)."
        )
        if summary.type_counts:
            typer.echo(_format_type_tally(summary.type_counts))


def _echo_ingest_preview(preview: ingest_service.IngestPreview) -> None:
    """Print the proposed changes before the confirmation gate."""
    name, slug = preview.name, preview.slug
    if preview.regenerate:
        typer.echo(
            "openkos ingest: proposed changes (re-ingest -- identical source "
            "already present):"
        )
        typer.echo(f"  ~ raw/{name} (existing copy reused -- not rewritten)")
        typer.echo(
            f"  ~ bundle/sources/{slug}.md (regenerated -- sensitivity "
            f"{preview.resolved_sensitivity} {preview.sensitivity_clause}"
            f"{preview.title_clause})"
        )
        _echo_event_date_preview_line(preview.event_date)
        # Each line prints only when its OWN specific delta fired on a
        # Source-only rewrite (design.md Decision 7).
        if preview.frontmatter_key_count is not None:
            typer.echo(
                f"    source frontmatter recorded ({preview.frontmatter_key_count} "
                "key(s))"
            )
        if preview.tags_added:
            typer.echo(f"    tags added: {', '.join(preview.tags_added)}")
        if preview.sensitivity_raised:
            typer.echo(
                "openkos ingest: this Source-only rewrite raised the "
                "Source's sensitivity -- existing derived objects keep "
                "their own already-stamped sensitivity; run 'openkos "
                "set-sensitivity' to raise them explicitly.",
                err=True,
            )
        # source-tag-sync (#1093), ADR-0033: existing derived objects keep the
        # tags they were created with (this rewrite never re-tags them), so a
        # tag-union delta needs its own advisory naming the verb that closes
        # the gap. Fires independently of the sensitivity advisory above.
        if preview.tags_added:
            typer.echo(
                "openkos ingest: this Source-only rewrite added tags to "
                "the Source -- existing derived objects keep the tags "
                f"they were created with; run 'openkos sync-tags "
                f"sources/{slug}' to add the Source's current tags to them.",
                err=True,
            )
        for plan in preview.derived:
            typer.echo(f"  + bundle/{plan.link_dir}/{plan.slug}.md")
        for obj in preview.adopted:
            typer.echo(
                f"  ~ bundle/{obj.link_dir}/{obj.slug}.md "
                "(written by an interrupted ingest -- now catalogued)"
            )
        typer.echo(f"  ~ {preview.index_name} (Source entry refreshed)")
        typer.echo(f"  ~ {preview.log_name} (new dated entry)")
    else:
        typer.echo("openkos ingest: proposed changes:")
        typer.echo(f"  + raw/{name}")
        typer.echo(f"  + bundle/sources/{slug}.md")
        _echo_event_date_preview_line(preview.event_date)
        for plan in preview.derived:
            typer.echo(f"  + bundle/{plan.link_dir}/{plan.slug}.md")
        typer.echo(f"  ~ {preview.index_name} (new Source entry)")
        typer.echo(f"  ~ {preview.log_name} (new dated entry)")


def _confirm_ingest(
    _preview: ingest_service.IngestPreview,
) -> ingest_service.ConfirmationAnswer:
    """The confirmation question, asked only on a TTY: decline is a no;
    without a TTY the question cannot be asked."""
    if not sys.stdin.isatty():
        return "unavailable"
    try:
        typer.confirm("Proceed with these changes?", abort=True)
    except typer.Abort:
        return "declined"
    return "proceed"


def _ingest_single(
    src: Path,
    *,
    auto: bool,
    include_confidential: bool,
    warn_nonlocal_embed_host: bool = True,
    re_extract: bool = False,
    event_date: date | None = None,
) -> ingest_service.IngestOutcome:
    """The `ingest` verb's single-file adapter over
    `application.ingest_service.ingest_source` (issue #1138; the full Phase
    A / confirm / drift-guard / Phase B contract is documented there).

    Resolves the workspace root from the current directory, builds the
    effect ports from this module's own seams (so a patched
    `OllamaClient`, `_autocommit`, `_embed_client` or `datetime` still
    intercepts), asks the confirmation question when a TTY is present, and
    maps each typed refusal to its exit code: 3 for drift (the one failure a
    script may retry, #319), 1 for everything else, and Typer's own abort
    for a declined prompt. `_ingest_batch` calls it once per matched file
    and catches the `typer.Exit` to skip that file; `ingest` ignores the
    outcome for a single file."""

    section = _commit_section_for(Path.cwd())

    def _after_commit(layout: config.WorkspaceLayout, cfg: config.Config) -> None:
        embedder = _embed_client(cfg)
        _embed_after_ingest(
            layout,
            embedder,
            cfg=cfg,
            model_tag=cfg.embedding_model,
            embedding_backend=cfg.backend,
            warn_nonlocal_host=warn_nonlocal_embed_host,
            # Resolved from the client that will do the sending, beside the cfg
            # that carries the workspace's opt-out (#922) -- the same two terms
            # `_resolve_local_exemption` ANDs for the five chat seams.
            local_exemption=_resolve_local_exemption(
                cast(BackendDiagnostics, embedder), cfg
            ),
            commit_section=section,
        )

    ports = ingest_service.IngestPorts(
        chat_client=lambda cfg: _chat_client(cfg, task="extraction"),
        autocommit=lambda root, paths, message: _autocommit(root, paths, message),
        after_commit=_after_commit,
        snapshot_read=lambda path: _snapshot_read(path),
        clock=lambda: datetime.now(UTC),
        commit_section=section,
    )
    policy = ingest_service.IngestPolicy(
        include_confidential=include_confidential,
        re_extract=re_extract,
        event_date=event_date,
        skip_confirmation=auto,
    )
    try:
        return ingest_service.ingest_source(
            Path.cwd(),
            src,
            policy,
            ports=ports,
            observer=_CliIngestObserver(),
            confirm=_confirm_ingest,
        )
    except ingest_service.ConfirmationDeclined as exc:
        raise typer.Abort() from exc
    except ingest_service.ConfirmationUnavailable as exc:
        # This intentionally diverges from `init`'s silent-on-non-TTY
        # behavior: `ingest` honors "review before save".
        typer.echo(
            "openkos ingest: refusing to write without confirmation -- "
            "stdin is not a TTY; re-run with --auto.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except ingest_service.DriftDetected as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(code=3) from exc
    except lock.WorkspaceBusyError as exc:
        # The commit phase could not take the lock within `--wait`. Nothing was
        # written, so it is the same retry-safe refusal the guard has always
        # given, raised HERE so a batch records this file as skipped and the
        # next one gets its own chance at the lock.
        typer.echo(f"openkos ingest: refusing to run -- {exc}.", err=True)
        raise typer.Exit(code=3) from exc
    except ingest_service.IngestRefused as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(code=1) from exc


_ForgetScope = Literal["self", "source"]


@app.command(
    help=(
        "Delete a concept and its catalog entry, leaving the source "
        "material it came from in place."
    ),
    rich_help_panel="Remove",
)
@_guard_workspace_lock("forget")
def forget(
    concept_id: str = typer.Argument(
        ..., help="Bundle-relative concept id (path minus '.md') to remove."
    ),
    scope: _ForgetScope = typer.Option(
        "self",
        "--scope",
        help=(
            "'self' (default) removes only <concept_id>, byte-identical to "
            "a single-concept forget. 'source' expands the purge set "
            "to <concept_id> plus every concept whose ENTIRE `provenance` "
            "resolves back to it -- the orphan-after-delete closure "
            "computed by `bundle.provenance.find_provenance_descendants`; "
            "a concept with ANY surviving provenance entry outside the "
            "purge set is preserved untouched."
        ),
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Proceed even when inbound references (markdown links or typed "
            "relations) -- or unverifiable referrers whose frontmatter "
            "could not be parsed but that may reference a purge-set "
            "member -- were detected; they are left dangling, never "
            "retargeted. Independent of --auto -- it never skips the "
            "confirmation prompt."
        ),
    ),
) -> None:
    """Delete a concept file and remove its `index.md` catalog entry: the
    mirror-image of `ingest` (MVP-1 simplified delete, decision #717),
    reference-aware (MVP-3 gap #8 S2a) and, since `--scope source`,
    cascade-aware over a concept's provenance descendants (MVP-3 gap #8
    S2b).

    Phase A (pure, no writes) validates and builds the entire result in
    memory, in order: the current directory must already be a workspace
    (the same `config.require_workspace` gate `ingest`/`status`/`lint`
    share), or this refuses; `concept_id` (the ROOT of the purge set) is
    resolved via `_resolve_concept_path`, which rejects an absolute id, any
    `..` segment, a reserved basename (`index`/`log`), or a nonexistent
    concept file -- all as `ValueError`, all refusing BEFORE any read tied
    to `concept_id`, and BEFORE any descendant resolution (threat matrix:
    path-traversal deletion; spec: "Path safety runs before descendant
    resolution"). Descendant ids, by construction, are never user input --
    they are drawn only from real `other_files` keys discovered under
    `bundle_dir`.

    Once path-safety clears, Phase A reads the root's own text and takes
    ONE whole-bundle snapshot of every other `*.md` file (mirroring
    `merge`'s `other_files` construction: reserved filenames and the
    root's own file excluded). This SAME snapshot feeds every step below,
    for both scopes -- no extra bundle scan (design: Technical Approach).

    The PURGE SET is then resolved (design decision 6, unified Phase-A
    data path): `--scope self` (default) collapses it to `{concept_id}`,
    reproducing S2a byte-for-byte; `--scope source` expands it via
    `bundle_provenance.find_provenance_descendants` -- a concept C (C !=
    root) joins iff its `provenance` is NON-EMPTY and a SUBSET of the
    purge set, iterated to a fixed point (spec: "Provenance Descendant
    Resolution"; the non-empty guard is the critical over-deletion
    barrier).

    For EVERY purge-set member, Phase A collects: (1) outbound
    `supersedes` edges targeting a concept OUTSIDE the purge set --
    resurrection disclosures, each naming the target AND the causing
    member (spec: "Resurrection Interaction Disclosure"); (2) inbound
    references via `bundle_references.find_inbound_references` (S2a's own
    scanner, called once per member over the SAME snapshot), from which
    any reference whose REFERRER is itself a purge-set member is dropped
    -- the set-difference gate (design decision 2): an intra-set backlink
    (e.g. a cascade child's `## Related` link back to its Source) is
    expected and must never block, while an EXTERNAL reference or
    unverifiable referrer still does. `unverifiable` referrers -- files
    whose frontmatter could not be parsed at all but whose raw text
    mentions a purge-set member's id, fail-CLOSED per S2a -- are deduped
    by `referrer_id` across members, so one malformed file mentioning
    several member ids surfaces once, not once per member.

    `index.md` is rewritten via `bundle_index.remove_index_entry`, once
    per purge-set member (a pure text transform; call order does not
    affect the result). `log.md` gets one TOMBSTONE-marked entry per
    member (`**Tombstone** (HH:MM:SSZ): Removed [<title>](/<id>.md)
    (id: <id>).`), all sharing the same timestamp (spec: "Log Entry on
    Forget" -- N lines for a cascade, exactly one for `self`).

    The preview prints every purge-set id as `- bundle/<id>.md`, the
    catalog/log edit lines, one `!`/`?` line per surviving EXTERNAL
    reference, one `~` line per resurrection disclosure, and -- for
    `--scope source` only -- a trailing count line (spec: "Full-Set
    Preview and Count Confirmation"). `--scope self`'s preview and every
    downstream string is UNCHANGED from S2a (byte-identity, design
    decision 6): the member-suffix on reference lines and the count line
    are both scope-conditional and never appear for `self`.

    TWO ORTHOGONAL gates follow, in order (spec: "`--force` Is Orthogonal
    to the Confirm Gate"): gate 1 refuses (exit 1, no write) iff a
    surviving verified reference OR unverifiable referrer was detected AND
    `--force` was not passed -- `--force` bypasses ONLY this refusal,
    never retargeting/rewriting the dangling references it leaves behind.
    Gate 2 is the confirm gate, identical precedence to `ingest`: `--auto`
    skips the prompt outright; otherwise config `review: false` skips it
    the same way; otherwise, on a TTY, `typer.confirm` asks (stating the
    delete COUNT for `--scope source`; S2a's verbatim text for `self`) and
    aborts (exit 1) on decline; otherwise (non-TTY, no `--auto`) this
    refuses to write (exit 1). `--force` does NOT auto-confirm gate 2 --
    the two gates stay fully orthogonal for both scopes.

    Past both gates -- and on the runs that skip gate 2, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every path this run intends to touch
    and refuses the WHOLE run (exit 3, nothing written, nothing unlinked)
    if any changed or vanished since Phase A read it (issues #306, #313,
    #319).

    That set includes the DELETE targets, not just `index.md` and `log.md`.
    The why lives in ONE place -- the comment on the
    `_reject_drifted_targets` call in the body (#320) -- in short: an edit
    landing on a purge-set member during the prompt would be destroyed
    outright rather than overwritten, and that member-side protection is
    all the guard delivers; referrer-side drift is out of its reach.

    Phase B (after both gates) writes `index.md` then `log.md`
    (`write_atomic`, catalog FIRST, covering every purge-set member) and
    deletes each member's concept file (`fsio.remove_file`) LAST, in
    deterministic `sorted(purge_ids)` order (design decision 5) -- so
    `index.md`/`log.md` never reference a file that does not exist. This
    is NOT transactional as a whole: a failure partway through the N
    unlinks leaves a benign, git-recoverable partial result -- the catalog
    already fully updated, one or more concept files possibly still
    present as orphans -- never silent corruption. Any failure, Phase A or
    Phase B, is caught and reported on stderr (exit 1), not a raw
    traceback; `except (OSError, ValueError)`, matching `ingest`'s
    convention.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos forget: refusing to forget -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)

        # Path-safety on the ROOT id runs FIRST, before any descendant
        # resolution (spec: "Path safety runs before descendant
        # resolution") -- descendant ids are disk-discovered later, never
        # user input.
        concept_path, canonical_id = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, concept_id
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos forget: refusing to forget -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    now = datetime.now(UTC)

    try:
        cfg = config.read_config(root)
        plan = application_lifecycle.prepare_forget(
            root, layout, canonical_id, scope=scope, now=now, cfg=cfg
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos forget: failed while preparing the forget -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos forget: proposed changes:")
    if plan.total_removed >= 1:
        typer.echo(f"  ~ {index_path.name} (remove entry)")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")
    for member in plan.purge_ids:
        typer.echo(f"  - bundle/{member}.md")
    # #567: `plan.references` is already aggregated per (member, referrer,
    # kind, relation type) -- a referrer linking the target 24 times is ONE
    # `ReferenceDisclosure` with a count, not 24 identical lines. Tuple
    # order is the first-seen order the service's per-reference loop
    # discovered in, and a count of 1 keeps the exact singular wording this
    # preview always had.
    for ref in plan.references:
        if ref.kind == "link":
            detail = "inbound link" if ref.count == 1 else f"{ref.count} inbound links"
            line = f"  ! bundle/{ref.referrer_id}.md ({detail})"
        elif ref.kind == "relation":
            detail = (
                f"inbound relation: {ref.relation_type}"
                if ref.count == 1
                else f"{ref.count} inbound relations: {ref.relation_type}"
            )
            line = f"  ! bundle/{ref.referrer_id}.md ({detail})"
        else:
            detail = (
                "unverifiable"
                if ref.count == 1
                else f"{ref.count} unverifiable references"
            )
            line = (
                f"  ? bundle/{ref.referrer_id}.md "
                f"({detail}: could not parse; may reference {ref.member})"
            )
        if scope == "source" and ref.kind != "unverifiable":
            line += f" -> {ref.member}"
        typer.echo(line)
    status_outcome_by_target = {
        withdrawal.target: withdrawal.outcome for withdrawal in plan.status_withdrawals
    }
    skipped_withdrawal_ids = set(plan.skipped_withdrawal_ids)
    for member, target in plan.resurrection_pairs:
        status_suffix = ""
        if target in status_outcome_by_target:
            outcome = status_outcome_by_target[target]
            status_suffix = (
                "; status → stable"
                if outcome is okf.ExportOutcome.WITHDRAW
                else "; stale export marker removed"
            )
        elif target in skipped_withdrawal_ids:
            unreadable = ", ".join(plan.incomplete_walk_unreadable)
            status_suffix = (
                f"; status export withdrawal skipped -- {unreadable} could "
                "not be read (run `openkos repair` after resolving it)"
            )
        typer.echo(
            f"  ~ bundle/{target}.md (re-enters retrieval: no longer "
            f"superseded by {member}{status_suffix})"
        )
    if scope == "source":
        typer.echo(f"  Total: {len(plan.purge_ids)} concept(s) to delete.")

    # Gate 1 (spec: "Refuse Forget When Inbound References Exist, Unless
    # --force"): refuses iff a surviving (external, set-difference-
    # filtered) verified reference OR unverifiable referrer was detected
    # AND --force was not passed -- fully independent of gate 2 below
    # (spec: "--force Is Orthogonal to the Confirm Gate"). A hard refusal,
    # never a `ConfirmationRequest` (design D2): `plan.surviving_refs`/
    # `unverifiable_refs` are plain counts, read directly here, never
    # threaded through `plan.confirmation`. `target_desc` is scope-
    # conditional ONLY in wording; for `self` it reproduces S2a's exact
    # `'<canonical_id>'` phrasing byte-for-byte.
    if (plan.surviving_refs or plan.unverifiable_refs) and not force:
        messages: list[str] = []
        target_desc = (
            f"the {len(plan.purge_ids)}-concept purge set rooted at '{canonical_id}'"
            if scope == "source"
            else f"'{canonical_id}'"
        )
        if plan.surviving_refs:
            messages.append(
                f"{plan.surviving_refs} inbound reference(s) to {target_desc} found"
            )
        if plan.unverifiable_refs:
            messages.append(
                f"could not verify {plan.unverifiable_refs} referrer(s) "
                f"that may reference {target_desc}"
            )
        typer.echo(
            "openkos forget: refusing to forget -- "
            + "; ".join(messages)
            + "; re-run with --force to proceed (references will be left "
            "dangling).",
            err=True,
        )
        raise typer.Exit(code=1)

    # Gate 2: the confirm gate, untouched by --force. `plan.confirmation`
    # is a `BooleanConfirmation` (design D1) whose `prompt` is already
    # scope-conditional (`--scope source` names the delete COUNT, `self`
    # keeps S2a's verbatim prompt, byte-identity design decision 6) -- the
    # adapter still decides WHETHER to ask (`cfg.review`) and HOW to ask
    # (TTY confirm vs. non-TTY refusal); the service only supplied WHAT is
    # asked.
    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm(plan.confirmation.prompt, abort=True)
        else:
            typer.echo(plan.confirmation.non_tty_refusal, err=True)
            raise typer.Exit(code=1)

    # Issue #313: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    #
    # The DELETE targets are in here too, not just the two `write_atomic`
    # ones -- and this comment is the ONE copy of the why (#320: three
    # copies of this rationale each over-claimed). `forget` picks its purge
    # set from the Phase-A bundle snapshot and then unlinks those exact
    # paths, so an edit landing during the prompt is destroyed outright --
    # strictly worse than being overwritten, since nothing survives to
    # recover from -- and a `provenance:` edit on a member is drift in that
    # member's own claim to purge-set membership. Either alone justifies
    # guarding the delete targets. It is also ALL the guard delivers: it
    # re-reads only this mapping's paths, so an inbound reference gained
    # during the prompt -- which lives in a REFERRER file, by construction
    # outside the purge set, since the gate above drops intra-set referrers
    # -- is not caught, and neither is a brand-new `.md` file created
    # during the prompt: additive drift has no baseline here.
    _reject_drifted_targets(
        layout,
        {
            index_path: plan.index_bytes,
            log_path: plan.log_bytes,
            concept_path: plan.concept_bytes,
            **{
                # Defensive fail-closed lookup (see `_require_member_baseline`):
                # today the key exists by construction, but a missing baseline
                # must refuse cleanly, never `KeyError` mid-gate.
                layout.bundle_dir / f"{member}.md": _require_member_baseline(
                    "forget", plan.other_bytes, member
                )
                for member in plan.purge_ids
                if member != canonical_id
            },
            # deprecated-status-export (issue #1075): every resurrection
            # target this run will REWRITE is also a write target, so its
            # pre-prompt baseline joins the guard exactly like a purge-set
            # member's does.
            **{
                layout.bundle_dir / f"{withdrawal.target}.md": _require_member_baseline(
                    "forget", plan.other_bytes, withdrawal.target
                )
                for withdrawal in plan.status_withdrawals
            },
        },
        "forget",
        # #319: the purge-set members are UNLINKED below, not written --
        # `deletes` is what makes the refusal say so. Built the same way the
        # unlink loop builds its paths (`bundle_dir / f"{member}.md"`, with
        # `concept_path` standing in for the canonical root), so the labels
        # track Phase B by construction.
        deletes=frozenset(
            {concept_path}
            | {
                layout.bundle_dir / f"{member}.md"
                for member in plan.purge_ids
                if member != canonical_id
            }
        ),
    )

    ledger_touched: list[Path] = []
    decisions_touched: list[Path] = []
    try:
        application_lifecycle.forget_core(layout, plan)
        # Merge-ledger sidecar privacy sweep (forget-command spec:
        # "Deletion Sweep Includes Ledger Storage"), same Phase B write:
        # a purge-set member's content must not survive `forget` merely
        # because it was previously absorbed into (or is the survivor of)
        # a merge. Stays adapter-side (shared with `purge`'s own Phase B),
        # so it runs immediately after `forget_core`'s write, inside the
        # SAME try/except.
        ledger_touched = _sweep_ledger_sidecars_for_ids(
            layout.bundle_dir, plan.purge_ids
        )
        # Pending-work decision sweep (forget-command spec: "Forget Sweeps
        # Live Decision Entries Referencing The Purge Set"), same Phase B
        # write: a purge-set member's contradiction decision must not
        # survive `forget` merely because the record lives under a
        # different (live) concept's sidecar. `forget` performs no history
        # rewrite, so this call IS the entire sweep for it (unlike
        # `purge`, which also puts these paths into `expunge_targets`).
        decisions_touched = _sweep_decisions_for_ids(layout.bundle_dir, plan.purge_ids)
        # Persisted-findings privacy sweep (#685 item 1), same Phase B:
        # a derived cache only (never autocommitted), and it degrades to a
        # loud warning internally rather than raising into this block --
        # the bundle deletes above must not be reported as failed over a
        # recomputable cache.
        _sweep_findings_for_ids(layout, plan.purge_ids)
    except (OSError, ValueError) as exc:
        message = f"openkos forget: failed while writing the forget -- {exc}."
        # K-of-N observability on a mid-cascade unlink failure (`--scope
        # source`): only enrich when there is more than one purge-set
        # member to report on, so the `self`/single-member message stays
        # byte-identical. `forget_core` carries the exact count out on
        # `PartialForgetWrite`; anything else reaching this arm came from
        # the three sweeps BELOW `forget_core`, by which point every unlink
        # had already succeeded -- hence the full-count default. Do not
        # re-derive this by probing the filesystem here: `Path.exists()`
        # re-raises `EACCES` (see `_purge_store_is_gone`), and a probe
        # inside this handler would replace the operator's diagnosis with a
        # traceback in exactly the permission failure that opened it.
        if len(plan.purge_ids) > 1:
            unlinked_count = getattr(exc, "unlinked_count", len(plan.purge_ids))
            remaining = len(plan.purge_ids) - unlinked_count
            message += (
                f" removed {unlinked_count} of {len(plan.purge_ids)} concept(s) "
                f"before failing; {remaining} remain (recover with git or "
                "'openkos lint')."
            )
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc

    if scope == "source":
        deleted_paths = ", ".join(f"bundle/{member}.md" for member in plan.purge_ids)
        typer.echo(
            f"openkos forget: removed {len(plan.purge_ids)} concept(s) "
            f"({deleted_paths}) ({index_path.name}, {log_path.name} updated)."
        )
    else:
        typer.echo(
            f"openkos forget: removed 'bundle/{canonical_id}.md' "
            f"({index_path.name}, {log_path.name} updated)."
        )

    forget_message = f"openkos: forget {canonical_id}"
    if len(plan.purge_ids) > 1:
        forget_message += f" (+{len(plan.purge_ids) - 1} descendants)"
    forget_sha = _autocommit(
        root,
        [
            "bundle/index.md",
            "bundle/log.md",
            *(f"bundle/{member}.md" for member in plan.purge_ids),
            *(
                f"bundle/{p.relative_to(layout.bundle_dir).as_posix()}"
                for p in (*ledger_touched, *decisions_touched)
            ),
        ],
        forget_message,
    )
    # #800: after the removal line above, never instead of it -- `forget`
    # echoes what it removed before `_autocommit` runs, so this reads as the
    # postscript it is. Silent when `_autocommit` degraded: a workspace with
    # no git identity must not be sent after a commit that was never made.
    if forget_sha is not None:
        _echo_commit_disclosure(forget_sha, prefix="openkos forget: ")

    # #640: also prunes the forgotten concept(s) from `vectors.db` via the
    # vector stage's prune pass, not only the manifest-gated stores.
    _refresh_derived_after_write(layout, cfg, verb="forget")


_PurgeScope = Literal["self", "source"]
"""`purge_confirm_phrase`/`prepare_purge` moved into `application.lifecycle`
(issue #918 S4) and take the same two-value `Literal["self", "source"]`
directly rather than importing this CLI-local alias -- `application/*`
modules must not import `openkos.cli`. This alias stays here only for
`purge`'s own `--scope` option type."""


def _purge_clean_live_index(
    layout: config.WorkspaceLayout, purge_ids: list[str]
) -> None:
    """After the (already irreversible) history rewrite has succeeded,
    remove the LIVE `index.md` catalog bullet for EVERY purge-set member --
    reusing `forget`'s own `bundle_index.remove_index_entry` +
    `fsio.write_atomic` write path.

    Without this, the live catalog would keep a bullet pointing at a
    concept whose file no longer exists in ANY commit -- a broken catalog
    entry, and the purged id/title staying visible in the LIVE workspace
    (not merely history).

    This runs as an ordinary working-tree edit AFTER `git filter-repo` has
    already committed the rewritten history and checked out the new HEAD --
    there is no dirty-tree rail left to satisfy at this point (Phase B has
    already begun; spec: Irreversibility -- No Rollback After Rewrite
    Begins), so this is simply the next write in the same irreversible
    operation, not a new gated action.

    A failure here is reported but does NOT fail the (already-succeeded)
    purge -- the erasure already happened; a stale catalog bullet left
    behind by a failed write is a correctness issue to fix with
    `openkos lint`, not a data-leak one."""
    index_path = layout.bundle_dir / "index.md"
    try:
        index_text = index_path.read_text(encoding="utf-8")
        new_index_text = index_text
        for member in purge_ids:
            new_index_text, _ = bundle_index.remove_index_entry(new_index_text, member)
        if new_index_text != index_text:
            fsio.write_atomic(index_path, new_index_text)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos purge: warning -- failed to clean the live index.md "
            f"catalog: {exc}. Run 'openkos lint' to detect/fix a dangling "
            "bullet.",
            err=True,
        )


def _purge_clean_live_log(layout: config.WorkspaceLayout, purge_ids: list[str]) -> None:
    """After the (already irreversible) history rewrite has succeeded,
    remove any LIVE `log.md` `forget` tombstone entry for EVERY purge-set
    member -- mirroring `_purge_clean_live_index` exactly, but via
    `bundle_log.remove_log_entry`.

    Without this, a concept that was `forget`-ed before being `purge`-d
    would leave its tombstone visible in the LIVE `log.md` even though the
    concept itself, and now (Slice 2) every HISTORICAL mention of it in
    `index.md`/`log.md`, is gone.

    A failure here is reported but does NOT fail the (already-succeeded)
    purge, matching `_purge_clean_live_index`'s same non-fatal contract."""
    log_path = layout.bundle_dir / "log.md"
    try:
        log_text = log_path.read_text(encoding="utf-8")
        new_log_text = log_text
        for member in purge_ids:
            new_log_text, _ = bundle_log.remove_log_entry(new_log_text, member)
        if new_log_text != log_text:
            fsio.write_atomic(log_path, new_log_text)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos purge: warning -- failed to clean the live log.md "
            f"tombstone(s): {exc}. Run 'openkos lint' to detect/fix a "
            "dangling entry.",
            err=True,
        )


def _purge_rebuilt_store_paths(
    layout: config.WorkspaceLayout,
) -> tuple[Path, ...]:
    """The derived stores `purge` deletes and then rebuilds IN-LINE.

    Both rebuilds are free -- neither needs a model -- which is the whole
    reason these two are separated from the dropped set below."""
    return (layout.fts_db_path, layout.graph_db_path)


def _purge_dropped_stores(
    layout: config.WorkspaceLayout,
) -> tuple[tuple[Path, str], ...]:
    """Every derived store `purge` deletes and LEAVES deleted, paired with
    what restoring it costs (#886).

    THE ONE STRUCTURE. The sidecar sweep and the operator-facing notice both
    walk it, and the cost travels WITH the path rather than in a lookup
    keyed on filename -- a separate table would let a store be added here
    and silently arrive in the notice with no cost, or raise on a missing
    key. Drift of exactly that shape produced this issue: the delete loop
    grew from two stores to five while the warning kept naming one.

    The three costs are genuinely different, which is why this returns them
    per store instead of one shared "run `openkos reindex`" line -- a
    reindex restores none of the findings verdicts, and the question cache
    needs nothing run at all."""
    return (
        (
            layout.vectors_db_path,
            "dense retrieval degraded. Run `openkos reindex` to restore it: "
            "a full re-embed of every surviving document (one embedding "
            "call per chunk). That run will report 'no embedding-model tag "
            "stored (fresh or dropped store)' — not an embedding model "
            "change — because the model tag lived in the dropped store.",
        ),
        (
            layout.findings_db_path,
            "persisted contradiction verdicts, identity adjudications and "
            "edge-typing suggestions are gone. Every one is recomputable, "
            "but only by paying its model call again: the next "
            "`openkos contradictions`, `openkos adjudicate` and "
            "`openkos suggest-relations` run judges everything fresh. "
            "`openkos reindex` restores none of them.",
        ),
        (
            layout.insight_questions_db_path,
            "cached question embeddings for `query --save`'s near-duplicate "
            "scan. Free to restore and nothing to run: a miss re-embeds on "
            "the next save.",
        ),
    )


def _purge_store_is_gone(db_path: Path) -> bool:
    """Whether `db_path` is verifiably absent -- never raising.

    `Path.exists()` is NOT total: it swallows a short list of errnos and
    RE-RAISES the rest, `EACCES` among them. Every probe here runs AFTER the
    irreversible rewrite, so an unguarded one turns an unreadable
    `.openkos` entry into a crash that takes the whole success report with
    it -- the expunge summary and the dropped-store disclosure both.

    Fails CLOSED, and that direction is the point: a store whose absence
    cannot be verified is reported as still present, so the notice never
    claims a destruction it could not confirm and the sidecar sweep never
    runs against a database that may still be live."""
    try:
        return not db_path.exists()
    except OSError:
        return False


def _purge_sweep_store_sidecars(db_path: Path) -> None:
    """Remove the `-wal`/`-shm` sidecars SQLite leaves beside `db_path`.

    Hygiene, NOT erasure, and the distinction is worth keeping straight:
    the WAL measured in the reported run was 0 bytes, so no data residue
    survived in it. What survived was litter -- a sidecar pair with no
    database, which makes `.openkos/` misreport what still exists.

    Swept only for the stores that stay deleted. A rebuilt store's sidecars
    belong to a live database and removing them would be reaching past the
    delete."""
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        try:
            sidecar.unlink(missing_ok=True)
        except OSError as exc:
            typer.echo(
                f"openkos purge: warning -- failed to delete '{sidecar.name}': {exc}.",
                err=True,
            )


@dataclass(frozen=True)
class _PurgeIndexOutcome:
    """What Phase B's index cleanup destroyed, and what it failed to destroy.

    Two findings, deliberately not collapsed into one: `dropped` prices a
    RESTORE the operator may choose to pay, while `undeleted` reports an
    erasure that did not complete. They are opposite kinds of news and only
    the second is a failure."""

    dropped: tuple[tuple[Path, str], ...]
    """Stores this purge actually destroyed and leaves deleted, each with
    its own restore cost (#886)."""

    undeleted: tuple[Path, ...]
    """Stores whose `unlink` raised, so pre-purge content is still on disk
    (#923). Non-empty means the erasure is incomplete."""


def _purge_rebuild_indexes(
    layout: config.WorkspaceLayout,
) -> _PurgeIndexOutcome:
    """Phase B's index cleanup (spec: Index Cleanup Is Delete-And-Rebuild, No
    Tombstone): physically DELETE `.openkos/{fts,vectors,graph,findings}.db`
    -- row-level `DELETE` would leave SQLite freelist-recoverable pages,
    which defeats the point of an erasure -- then best-effort rebuild FTS +
    graph ONLY (never the full `state.reindex.reindex`, which hard-depends
    on a running Ollama embedder `purge` must never require). `vectors.db`
    and `findings.db` are BOTH deliberately left deleted, never rebuilt
    in-line: `vectors.db` for the next `openkos reindex` to lazily
    re-embed, and `findings.db` because regenerating a contradiction
    finding costs LLM calls (pending-work design Decision 1's rebuild-
    posture table -- `findings.db` shares `vectors.db`'s posture, not
    `fts.db`'s).

    A REBUILD failure here is reported but MUST NOT fail the (already
    irreversible, already-succeeded) purge: the rebuild is a best-effort
    convenience over the survivors (design: Index cleanup decision).

    A failed DELETE is the opposite case and is reported as such (#923).
    That same non-failure rationale used to cover both, but the delete IS
    the security-critical erasure -- the one this function unlinks rather
    than `DELETE`s so no recoverable pages survive. When it raises, the
    store still holds pre-purge content, and the caller must not report a
    completed erasure. The failed paths travel back in `undeleted` rather
    than raising here, because everything after this point is
    post-irreversible bookkeeping the operator still needs to see.

    A rebuilt store counts too. `fts.db` and `graph.db` never appear in the
    dropped-store notice -- they are rebuilt, so nothing is lost -- but
    rebuilding content over a file that was never unlinked leaves the
    pre-purge pages just as recoverable. The erasure gap does not care
    whether the store comes back."""
    droppable = _purge_dropped_stores(layout)
    # Captured BEFORE the delete: a store that was never there was not
    # destroyed by this purge, and only destruction is worth disclosing.
    existed = {path for path, _ in droppable if not _purge_store_is_gone(path)}
    dropped = [path for path, _ in droppable]
    undeleted: list[Path] = []
    for db_path in (*_purge_rebuilt_store_paths(layout), *dropped):
        try:
            db_path.unlink(missing_ok=True)
        except OSError as exc:
            undeleted.append(db_path)
            typer.echo(
                f"openkos purge: warning -- failed to delete '{db_path.name}': {exc}.",
                err=True,
            )
    # #886: sweep the sidecars of the dropped stores only, and only for the
    # ones that ACTUALLY went. A rebuilt store's `-wal`/`-shm` belong to a
    # database that is about to exist again -- and sweeping the sidecars of
    # a store whose `unlink` FAILED is worse than litter: a `-wal` holds
    # committed pages not yet checkpointed back, so removing it out from
    # under a live database can destroy data the purge was never asked to
    # touch.
    for db_path in dropped:
        if _purge_store_is_gone(db_path):
            _purge_sweep_store_sidecars(db_path)

    try:
        reindex_module._reindex_fts(layout.bundle_dir, layout.fts_db_path, force=True)
    except (OSError, sqlite3.Error, FtsUnavailable) as exc:
        typer.echo(
            f"openkos purge: warning -- failed to rebuild fts.db: {exc}. "
            "Run `openkos reindex` to restore search.",
            err=True,
        )

    try:
        sqlite_graph.reindex_graph(layout.bundle_dir, layout.graph_db_path, force=True)
    except (OSError, sqlite3.Error) as exc:
        typer.echo(
            f"openkos purge: warning -- failed to rebuild graph.db: {exc}. "
            "Run `openkos reindex` to restore search.",
            err=True,
        )

    # Returned LAST, after both rebuilds: the caller's disclosure describes
    # what this purge destroyed, and a store is only that if it existed
    # before the delete and is still absent once the rebuilds have run.
    return _PurgeIndexOutcome(
        dropped=tuple(
            (path, cost)
            for path, cost in droppable
            if path in existed and _purge_store_is_gone(path)
        ),
        undeleted=tuple(undeleted),
    )


@app.command(
    help=(
        # This one line is the ONLY user-facing summary of what `purge`
        # removes -- it feeds both the `openkos --help` group listing and
        # `openkos purge --help` (a `help=` argument replaces the docstring
        # on the command's own page), so the docstring's accurate account
        # below never reaches a reader. It must therefore carry the
        # CONDITION, not just the promise: the raw source material goes only
        # when the purged member is a Source with a resolvable `resource`,
        # and a derived concept -- the common case -- contributes only its
        # own bundle file. It must also stay ONE paragraph: the group
        # listing renders only the first paragraph of this text (Typer
        # splits on a blank line and collapses single newlines), and it
        # WRAPS rather than truncates -- so the qualification survives the
        # listing as a second sentence, but would vanish from it if a blank
        # line were ever put in front of it.
        "Irreversibly expunge a concept -- and, when that concept is a "
        "Source, the raw source material behind it. There is no undo."
    ),
    rich_help_panel="Remove",
)
@_guard_workspace_lock("purge")
def purge(
    concept_id: str = typer.Argument(
        ..., help="Bundle-relative concept id (path minus '.md') to purge."
    ),
    scope: _PurgeScope = typer.Option(
        "self",
        "--scope",
        help=(
            "'self' (default) purges only <concept_id>. 'source' expands the "
            "purge set to <concept_id> plus every concept whose ENTIRE "
            "`provenance` resolves back to it -- the SAME orphan-after-delete "
            "closure `forget --scope source` uses "
            "(`bundle.provenance.find_provenance_descendants`)."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Proceed even when inbound references (markdown links or typed "
            "relations) -- or unverifiable referrers -- to a purge-set "
            "member were detected; they are left dangling. Bypasses ONLY "
            "the reference-aware rail -- every other rail (git-root, clean "
            "tree, no published commits, typed confirmation) still runs."
        ),
    ),
    confirm_phrase: str | None = typer.Option(
        None,
        "--confirm-phrase",
        help=(
            "The exact typed confirmation phrase (see the printed preview), "
            "for non-interactive/test use. On a TTY, omitting this prompts "
            "interactively instead. There is NO --auto bypass for this "
            "phrase -- purge is irreversible."
        ),
    ),
) -> None:
    """Irreversibly whole-file-expunge a concept's source `raw/<name>` and
    bundle file from ALL git history via `git-filter-repo`, ALSO content-
    scrubbing every historical `bundle/index.md`/`bundle/log.md` blob of the
    purge-set member(s) -- the true-erasure counterpart to `forget`,
    completing right-to-be-forgotten (Slice 1 whole-file expunge + Slice 2
    history content-scrub).

    Phase A (pure, no writes) is IDENTICAL to `forget`'s: `require_workspace`
    gate, `_resolve_concept_path` path-safety on the root id, the purge-set
    resolution (`--scope self|source` via
    `bundle_provenance.find_provenance_descendants`), and the SAME reference-
    aware inbound-reference detection. On top of that, for every purge-set
    member this also resolves its raw source path from a Source's
    `resource: raw/<name>` frontmatter (a derived concept, with no
    `resource`, contributes only its own `bundle/<id>.md`; a Source whose
    `resource` is absent or fails validation -- must start with `raw/`, no
    `..` segment, resolve under `raw/` -- is WARNED about, not refused, and
    simply contributes no raw path).

    Six fail-closed safety rails run, in this EXACT order, ALL before any
    write: (1) reference-aware refusal (unless `--force`) -- reused from
    `forget`'s own gate; (2) `git`/`git-filter-repo` availability; (3) the
    workspace root must BE a git repository root (`vcs.repo_root`); (4) the
    working tree must be clean (`vcs.dirty_paths`, the refusal naming the
    offending paths); (5) the local repo must
    have NO commits already published on any remote (`vcs.has_published_commits`
    -- history rewriting cannot retroactively change what a remote already
    has); (6) a TYPED CONFIRMATION PHRASE, printed alongside the preview,
    must match EXACTLY (never a bare `y`/`yes`) -- there is no `--auto`
    bypass for this rail, since purge is irreversible. The first failing
    rail refuses immediately (exit 1, nothing written); no later rail is
    evaluated.

    Past rail 6 -- reached without pausing when `--confirm-phrase` is
    given, so the check is unconditional -- `_reject_drifted_targets`
    re-reads `index.md`, `log.md`, and every purge-set member's bundle
    file, and refuses the WHOLE run (exit 3, nothing written, no history
    rewritten) if any changed or vanished since Phase A read it (issues
    #313, #319, #321). Rail 4 pinned the tree clean BEFORE the typed-phrase
    prompt -- the widest prompt window of any verb -- so without this an
    edit landing while the operator typed the phrase would be destroyed by
    the checkout of rewritten history, unrecoverably. The `raw/<name>`
    targets are deliberately not in the mapping: Phase A never reads their
    content, so they have no same-observation baseline (#318) and remain
    covered by rail 4 alone.

    Phase B (the point of no return, reached only once all six rails pass):
    `vcs.expunge_paths` rewrites every purge-set member's `raw/<name>` and
    `bundle/<id>.md` out of ALL git history and the working tree, and, in
    the SAME pass, content-scrubs every historical `index.md`/`log.md` blob
    of the purge-set member(s)' catalog bullet, log entries, and any prior
    `forget` tombstone (Slice 2), then finalizes (reflog expire + gc). A
    `GitFinalizeError` (the rewrite SUCCEEDED but finalize failed) is
    surfaced distinctly, and live-index/live-log cleanup still runs -- the
    rewrite already happened and cannot be undone. Index cleanup then
    deletes `.openkos/{fts,vectors,graph}.db` and best-effort rebuilds FTS +
    graph (never `vectors.db`, and never through the Ollama-dependent full
    `reindex()`) -- a rebuild failure is reported but does NOT fail the
    already-irreversible purge. After a successful purge, the purged id/
    title no longer appears anywhere in `index.md` or `log.md`, live or
    historical -- no residual warning is printed.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos purge: refusing to purge -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)

        # Path-safety on the ROOT id runs FIRST, before any descendant
        # resolution -- identical to `forget` (threat matrix: path-traversal
        # deletion).
        concept_path, canonical_id = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, concept_id
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos purge: refusing to purge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    now = datetime.now(UTC)

    try:
        plan = application_lifecycle.prepare_purge(
            root, layout, canonical_id, scope=scope, now=now
        )
        # Threat matrix ("Shell / subprocess"): concept ids are user-
        # controlled, and a decisions path derived from one could contain
        # `==>` (git-filter-repo's rename delimiter) or another rejected
        # sequence -- validate the WHOLE `expunge_targets` list here so a
        # malformed path refuses cleanly (this except clause) rather than
        # raising an uncaught `ValueError` from deep inside
        # `vcs_git.expunge_paths` after the point of no return.
        # `vcs_git.expunge_paths` re-validates this same list itself
        # (defense in depth, never trusted to be skipped), so this call
        # can never desync from what the real rewrite enforces. Stays
        # adapter-side: `application/lifecycle.py` must never import
        # `openkos.vcs`.
        vcs_git._validate_rel_paths(plan.disclosure.expunge_targets)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos purge: failed while preparing the purge -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    # Preview: every path targeted for expunge, any raw-path resolution
    # warnings, the absence of raw material when none resolved, and the
    # cascade count (source scope only) -- all printed before rail 1.
    # Slice 2 removes the (now-obsolete) mandatory residual-leak warning:
    # the history content-scrub below means no residual is left to warn
    # about.
    typer.echo("openkos purge: proposed IRREVERSIBLE history rewrite:")
    for target in plan.disclosure.expunge_targets:
        typer.echo(f"  - {target}")
    for warning in plan.disclosure.resource_warnings:
        # Stream-consistent with the rest of the pre-confirmation preview
        # (stdout, not stderr) -- an operator capturing only stdout must
        # not silently lose a malformed-resource warning printed here.
        typer.echo(f"  ! {warning}")
    if plan.disclosure.raw_absence:
        # State the ABSENCE, do not merely omit the line. See
        # `PurgeDisclosure.raw_absence`'s own docstring (design:
        # Interfaces/Contracts "S4 -- purge") for the full rationale.
        typer.echo(
            "  ! no raw source material is part of this purge -- no "
            "purge-set member contributes a raw source path (only a "
            "Source does)"
        )
    if plan.disclosure.cascade_total is not None:
        typer.echo(f"  Total: {plan.disclosure.cascade_total} concept(s) to purge.")
    typer.echo()

    # Rail 1: reference-aware refusal, unless --force (spec req 2, rail 1).
    # A hard refusal, never a `ConfirmationRequest` (design D2):
    # `plan.verified_refs`/`unverifiable_refs` are plain counts, read
    # directly here, never threaded through `plan.confirmation`.
    if (plan.verified_refs or plan.unverifiable_refs) and not force:
        messages: list[str] = []
        target_desc = (
            f"the {len(plan.purge_ids)}-concept purge set rooted at "
            f"'{plan.canonical_id}'"
            if scope == "source"
            else f"'{plan.canonical_id}'"
        )
        if plan.verified_refs:
            messages.append(
                f"{plan.verified_refs} inbound reference(s) to {target_desc} found"
            )
        if plan.unverifiable_refs:
            messages.append(
                f"could not verify {plan.unverifiable_refs} referrer(s) "
                f"that may reference {target_desc}"
            )
        typer.echo(
            "openkos purge: refusing to purge -- "
            + "; ".join(messages)
            + "; re-run with --force to proceed (references will be left "
            "dangling).",
            err=True,
        )
        raise typer.Exit(code=1)

    # Rail 2: git/git-filter-repo availability (spec req 2, rail 2 in this
    # implementation's ordering -- cheap, deterministic, no repo assumption).
    if not vcs_git.git_available():
        typer.echo(
            "openkos purge: refusing to purge -- git is not available on "
            "PATH. Install git (e.g. https://git-scm.com/downloads, or "
            "`brew install git`), then try again.",
            err=True,
        )
        raise typer.Exit(code=1)
    if not vcs_git.filter_repo_available():
        typer.echo(
            "openkos purge: refusing to purge -- git-filter-repo is not "
            "available. Install it (e.g. `pip install git-filter-repo`, or "
            "`brew install git-filter-repo`), then try again.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Rail 3: the workspace root MUST be a git repository root (threat
    # matrix: git repository selection) -- always run in cwd, never
    # `git -C <userpath>`.
    try:
        found_root = vcs_git.repo_root(root)
    except vcs_git.GitError as exc:
        typer.echo(f"openkos purge: refusing to purge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc
    if found_root is None:
        typer.echo(
            "openkos purge: refusing to purge -- the workspace is not "
            "inside a git repository.",
            err=True,
        )
        raise typer.Exit(code=1)
    if found_root != root.resolve():
        typer.echo(
            "openkos purge: refusing to purge -- the workspace root is not "
            "the git repository root (a nested or ancestor repo cannot be "
            "safely rewritten).",
            err=True,
        )
        raise typer.Exit(code=1)

    # Rail 4: the working tree must be clean. The engine owns this repo and
    # commits on the user's behalf, so the refusal names WHAT is dirty
    # (#647) instead of sending the user to `git status` to interpret an
    # engine message. Capped at 10 paths so a large drift never floods
    # stderr.
    try:
        dirty = vcs_git.dirty_paths(root)
    except vcs_git.GitError as exc:
        typer.echo(f"openkos purge: refusing to purge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc
    if dirty:
        shown = ", ".join(dirty[:10])
        overflow = f" (and {len(dirty) - 10} more)" if len(dirty) > 10 else ""
        typer.echo(
            "openkos purge: refusing to purge -- the working tree has "
            f"uncommitted changes: {shown}{overflow}; commit or stash "
            "them, then try again.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Rail 5: no commits already published on any remote -- history
    # rewriting cannot retroactively change what a remote already has.
    try:
        published = vcs_git.has_published_commits(root)
    except vcs_git.GitError as exc:
        typer.echo(f"openkos purge: refusing to purge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc
    if published:
        typer.echo(
            "openkos purge: refusing to purge -- commits are already "
            "present on a remote; purge cannot rewrite published history.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Rail 6: the typed confirmation phrase, EXACT match only -- no --auto
    # bypass (irreversible). `--confirm-phrase` serves both non-interactive
    # use and tests; on a TTY without it, `typer.prompt` asks interactively.
    #
    # `expected_phrase` is deliberately recomputed HERE via a LIVE call to
    # `application_lifecycle.purge_confirm_phrase`, not read off
    # `plan.confirmation.expected` -- see `PurgePlan.confirmation`'s own
    # docstring: this is the exact call-site position
    # `test_drift_on_the_unprompted_path_is_refused` patches to prove the
    # post-gate drift guard covers this window, and that proof depends on
    # the call landing strictly AFTER rail 4, not during Phase A.
    expected_phrase = application_lifecycle.purge_confirm_phrase(
        plan.canonical_id, plan.purge_ids, scope
    )
    if confirm_phrase is not None:
        typed_phrase = confirm_phrase
    elif sys.stdin.isatty():
        typed_phrase = typer.prompt(f"Type '{expected_phrase}' to proceed")
    else:
        typer.echo(plan.confirmation.non_tty_refusal, err=True)
        raise typer.Exit(code=1)
    if typed_phrase != expected_phrase:
        typer.echo(plan.confirmation.mismatch_abort, err=True)
        raise typer.Exit(code=1)

    # Issue #321: rail 4 pinned the tree clean BEFORE the typed-phrase
    # prompt, and typing a whole sentence makes this the WIDEST prompt
    # window of any verb -- so an edit landing during it is invisible to
    # every rail, and `git filter-repo`'s history rewrite plus checkout
    # would destroy it outright, unrecoverably. Re-validate each target
    # now -- after the phrase gate (which `--confirm-phrase` reaches
    # without pausing, hence unconditionally), before the first write.
    #
    # The DELETE targets are in here alongside `index.md`/`log.md`: the
    # purge set is a claim about the Phase-A bundle, and a member edited
    # while the prompt waited is a state the operator was never shown.
    # The `raw/<name>` expunge targets are NOT in `plan.drift_targets`:
    # Phase A never reads their content (they may legitimately be absent
    # from the live tree), so there is no same-observation baseline to
    # compare -- they stay under rail 4's clean-tree protection alone.
    _reject_drifted_targets(
        layout,
        plan.drift_targets,
        "purge",
        # #319: the root concept and every cascade member are expunged --
        # DELETE targets, and the refusal must name them as such. Only
        # `index.md`/`log.md` are writes here.
        deletes=frozenset(
            {concept_path}
            | {
                layout.bundle_dir / f"{member}.md"
                for member in plan.purge_ids
                if member != plan.canonical_id
            }
        ),
    )

    # Phase B: the point of no return. No rail evaluation, no abort path,
    # from here on (spec: Irreversibility -- No Rollback After Rewrite
    # Begins). `expunge_paths` itself is silent and can run for a while on
    # a large history -- print an explicit "do not interrupt" line FIRST,
    # so an operator who sees no output does not mistake it for a hang and
    # Ctrl-C into the catastrophic mid-rewrite state.
    typer.echo(
        "openkos purge: beginning the irreversible history rewrite now -- "
        "do not interrupt.",
        err=True,
    )
    try:
        vcs_git.expunge_paths(
            root, plan.disclosure.expunge_targets, scrub_identities=plan.purge_ids
        )
    except vcs_git.GitFinalizeError as exc:
        typer.echo(
            f"openkos purge: the history rewrite SUCCEEDED, but finalize "
            f"failed -- {exc}",
            err=True,
        )
        _purge_clean_live_index(layout, plan.purge_ids)
        _purge_clean_live_log(layout, plan.purge_ids)
        _sweep_ledger_sidecars_for_ids(layout.bundle_dir, plan.purge_ids)
        _sweep_decisions_for_ids(layout.bundle_dir, plan.purge_ids)
        # This path already exits 1, so the residue changes no status here --
        # but an operator on a failing purge still needs to know a store was
        # left holding pre-purge content, and this is the only place it is
        # said (#923).
        residual = application_lifecycle.residual_store_notice(
            _purge_rebuild_indexes(layout).undeleted
        )
        if residual is not None:
            typer.echo(residual, err=True)
        raise typer.Exit(code=1) from exc
    except vcs_git.GitError as exc:
        typer.echo(
            f"openkos purge: failed -- the history rewrite did not complete -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    _purge_clean_live_index(layout, plan.purge_ids)
    _purge_clean_live_log(layout, plan.purge_ids)
    # Whole-History Expunge Covers The Ledger Sidecar Store
    # (privacy-purge spec): each purge-set member's OWN sidecar was already
    # removed from the working tree by `expunge_paths`' filter-repo checkout
    # (it was in `expunge_targets` above); this is the LIVE-tree half --
    # dropping any OTHER survivor's sidecar entry whose `absorbed_id` is a
    # purge-set member, reusing the exact same primitive `forget`'s Phase B
    # calls, so the sweep is written exactly once.
    ledger_touched = _sweep_ledger_sidecars_for_ids(layout.bundle_dir, plan.purge_ids)
    # Whole-History Expunge Covers The Pending-Work Decision Subtree
    # (privacy-purge spec): each purge-set member's OWN decisions sidecar,
    # and every FOREIGN sidecar referencing it, was already removed from
    # the working tree by `expunge_paths`' filter-repo checkout (both were
    # in `expunge_targets` above, via `application_lifecycle`'s own
    # `_decisions_history_targets`); this is the LIVE-tree half --
    # reconstructing any foreign sidecar's surviving (unrelated) records,
    # reusing the exact same primitive `forget`'s Phase B calls, so the
    # sweep is written exactly once.
    decisions_touched = _sweep_decisions_for_ids(layout.bundle_dir, plan.purge_ids)
    # deprecated-status-export (issue #1075, `privacy-purge` spec: "Purge
    # Withdraws The Deprecated-Status Export Of Resurrected Targets"):
    # write each resurrection target's WITHDRAW/DROP-MARKER outcome as part
    # of this SAME live-tree cleanup pass. A per-target `OSError` is a
    # non-fatal WARNING -- the irreversible rewrite already landed, and
    # export drift never changes retrieval (`deprecated-status-export`) --
    # so it neither raises nor changes `purge`'s exit code, matching every
    # other step in this post-erasure bookkeeping block.
    status_touched: list[Path] = []
    for withdrawal in sorted(plan.status_withdrawals, key=lambda w: w.target):
        target_path = layout.bundle_dir / f"{withdrawal.target}.md"
        try:
            fsio.write_atomic(target_path, withdrawal.new_text)
        except OSError:
            typer.echo(
                f"openkos purge: WARNING -- failed to withdraw the "
                f"deprecated-status export of '{withdrawal.target}'; run "
                "`openkos repair` to fix it.",
                err=True,
            )
            continue
        status_touched.append(target_path)
    index_outcome = _purge_rebuild_indexes(layout)
    dropped_stores = index_outcome.dropped
    # #886: the disclosure is the operator's only account of what this
    # irreversible operation destroyed, so it must not be lost to a
    # failure in the steps that come after the delete. The stores are
    # ALREADY gone by the line above; everything between here and the
    # notice is post-erasure bookkeeping -- including the `paths_dirty`
    # probe and auto-commit below, whose own comment records that the probe
    # can raise `GitError` on a genuinely broken repository -- and losing
    # the notice to it would leave
    # an operator who paid for those verdicts with no record that they
    # went. `finally`, not a reorder: the expunge summary below reads
    # first for a reason, and moving the notice above it to make it
    # safe would trade one defect for worse output.
    try:
        # Post-rewrite live-tree auto-commit (design: "purge empty-diff guard",
        # load-bearing): `_purge_clean_live_*` frequently leaves `index.md`/
        # `log.md` byte-identical to filter-repo's own rewrite (a no-op), and
        # `_autocommit` -> `commit_paths` runs `git commit` UNCONDITIONALLY,
        # raising `GitError` on an empty diff -- so a scoped `paths_dirty` probe
        # gates the call, avoiding a spurious WARNING on the common clean-purge
        # path. If the probe itself raises `GitError` (e.g. a genuinely broken
        # repo), fall through and attempt `_autocommit` anyway -- its own
        # try/except keeps that non-fatal too, matching this whole step's
        # never-fail-the-already-irreversible-purge contract.
        commit_paths_rel = [
            "bundle/index.md",
            "bundle/log.md",
            *(
                f"bundle/{p.relative_to(layout.bundle_dir).as_posix()}"
                for p in (*ledger_touched, *decisions_touched, *status_touched)
            ),
        ]
        try:
            should_commit = vcs_git.paths_dirty(root, commit_paths_rel)
        except vcs_git.GitError:
            should_commit = True
        if should_commit:
            commit_message = f"openkos: purge {plan.canonical_id}"
            if len(plan.purge_ids) > 1:
                commit_message += f" (+{len(plan.purge_ids) - 1})"
            _autocommit(root, commit_paths_rel, commit_message)

        if scope == "source":
            typer.echo(
                f"openkos purge: permanently expunged {len(plan.purge_ids)} "
                "concept(s) from ALL git history."
            )
        else:
            typer.echo(
                f"openkos purge: permanently expunged "
                f"'bundle/{plan.canonical_id}.md' from ALL git history."
            )
        # #886: every store `purge` actually destroyed is named here, each with
        # its own restore cost. This block used to name `vectors.db` alone while
        # THREE stores were being dropped. The wording now lives in
        # `application_lifecycle.dropped_store_notice`, rendering exactly what
        # `_purge_rebuild_indexes` reports it destroyed, so the disclosure
        # cannot fall behind the delete set the way it did between #142 and
        # #886 -- nor run ahead of it by naming a store whose delete failed or
        # one that was never there.
        #
        # The #698 substance the old block carried is carried by that helper's
        # `vectors.db` cost line: the re-embed is full rather than incremental,
        # and the next `reindex` reports "no embedding-model tag stored (fresh
        # or dropped store)" rather than an embedding model change, because the
        # tag lived in the dropped store. Same claims, re-worded to sit in a
        # per-store list; the quoted reindex wording is the part that is
        # verbatim, because pre-empting it is the point.
    finally:
        dropped_notice = application_lifecycle.dropped_store_notice(dropped_stores)
        if dropped_notice is not None:
            typer.echo(dropped_notice)
        # #923: the residue report shares the dropped notice's protection --
        # it is the only account of an INCOMPLETE erasure, and losing it to
        # a failure in the bookkeeping above would leave an operator
        # believing the expunge summary they just read was the whole story.
        residual_notice = application_lifecycle.residual_store_notice(
            index_outcome.undeleted
        )
        if residual_notice is not None:
            typer.echo(residual_notice, err=True)

    # OUTSIDE the `finally`, deliberately: raising in there would swallow an
    # in-flight exception from the bookkeeping, and that failure is the
    # louder one. Reached only on the path where everything above succeeded
    # except the erasure itself -- the history rewrite is done and reported,
    # so this is a partial-completion status, not a failed purge. Exit 1
    # matches the `GitFinalizeError` path above, which is the same shape:
    # the irreversible part succeeded, a later step did not.
    if index_outcome.undeleted:
        raise typer.Exit(code=1)


@app.command(
    help=(
        "Write one typed relation between two concepts, exactly as given. "
        "No inference, no model call."
    ),
    rich_help_panel="Curate",
)
@_guard_workspace_lock("relate")
def relate(
    source_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') to add the relation to.",
    ),
    rel: str = typer.Argument(
        ..., help="Relation type, e.g. 'references', 'depends_on'."
    ),
    target_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') the relation points to.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Write one deterministic typed edge -- `{target: target_id, type: rel}`
    -- into `source_id`'s `relations:` frontmatter (no LLM this slice, spec:
    "`relate` CLI Verb Writes A Typed Relation").

    Phase A (pure, no writes) mirrors `forget`'s gate shape: the current
    directory must already be a workspace (the same `config.require_workspace`
    gate every other write verb shares), or this refuses; `source_id` and
    `target_id` are EACH resolved via the same `_resolve_concept_path`
    `forget`/`merge` use -- rejecting an absolute id, any `..` segment, a
    reserved basename, or a nonexistent concept file, all as `ValueError`,
    all before any read (fail-closed existence on BOTH ends, spec: "Target
    Containment Consistent With Existing Verbs"). The two ids MUST resolve
    to DISTINCT concept files, else this refuses too, mirroring `merge`'s
    same-id guard. `rel` is validated via
    `model.relations.validate_relation_type`: rejected (no write) if empty
    or whitespace-only; accepted -- with an advisory note on stderr -- if it
    is not one of the seeded defaults (spec: "Seeded-But-Extensible Relation
    Vocabulary").

    The rest of Phase A builds the entire result in memory: `source_id`'s
    frontmatter is parsed (`okf.load_frontmatter`), its existing
    `relations:` decoded (`okf.decode_relations`), and the new
    `{target: target_id, type: rel}` edge appended UNLESS an identical
    `(target, type)` pair is already present -- in which case the existing
    list is kept as-is, so a repeated `relate` call is idempotent (spec:
    duplicate edge is not written twice). The full list is then
    re-encoded (`okf.encode_relations`, sorted, deterministic) and the
    source document re-rendered via `okf.dump_frontmatter`. A `log.md`
    entry is built in memory via `bundle_log.insert_log_entry` (a plain
    `**Relate**` line; no `index.md` entry -- a relation is an edit to an
    EXISTING catalog entry, not a new one, design decision 3).

    The preview printed before the confirm gate shows the source file, the
    relation being added, and the `relations:` entry count before/after.

    Confirm gate, identical precedence and mechanism to `forget`/`ingest`/
    `merge`: `--auto` skips the prompt outright; otherwise config
    `review: false` skips it the same way; otherwise, on a TTY,
    `typer.confirm` asks and aborts (exit 1) on decline; otherwise
    (non-TTY, no `--auto`) this refuses to write (exit 1), telling the user
    to re-run with `--auto`. Declining or refusing leaves the bundle
    completely untouched -- Phase A never writes anything.

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every path this run intends to write
    (the source concept and `log.md`) and refuses the WHOLE run (exit 3,
    nothing written) if either changed or vanished since Phase A read it
    (issues #306, #313, #319). Any run, prompted or not, can therefore reach this
    point and still end without writing.

    Phase B (after confirm) writes the source concept file
    (`fsio.write_atomic`, since it already exists) then `log.md`
    (`fsio.write_atomic`) -- content before the audit trail, mirroring
    `ingest`'s content-then-catalog ordering. Not transactional as a whole,
    matching every other write verb's documented limitation: a failure
    partway through is a benign, git-recoverable partial result, never
    silent corruption. Any failure, Phase A or Phase B, is caught and
    reported on stderr (exit 1), not a raw traceback.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos relate: refusing to relate -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)

        source_path, source_canonical = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, source_id
        )
        target_path, target_canonical = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, target_id
        )
        if source_canonical == target_canonical:
            raise ValueError(
                "source and target concept-ids must be distinct, both "
                f"resolved to {source_canonical!r}"
            )
        rel_type = validate_relation_type(rel)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos relate: refusing to relate -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc
    rel_note = relation_type_note(rel_type)
    if rel_note is not None:
        typer.echo(rel_note, err=True)

    now = datetime.now(UTC)

    try:
        prepared = application_lifecycle.prepare_relate(
            source_path,
            log_path,
            source_canonical,
            target_canonical,
            rel_type,
            root,
            now=now,
            target_path=target_path,
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos relate: failed while preparing the relate -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos relate: proposed changes:")
    if prepared.already_present:
        preview_line = (
            f"  ~ bundle/{prepared.source_canonical}.md (relations: "
            f"{prepared.existing_relations_count} -> "
            f"{prepared.updated_relations_count} entries; "
            f"unchanged: {{target: {prepared.target_canonical}, "
            f"type: {prepared.rel_type}}} already present)"
        )
    else:
        preview_line = (
            f"  ~ bundle/{prepared.source_canonical}.md (relations: "
            f"{prepared.existing_relations_count} -> "
            f"{prepared.updated_relations_count} entries; "
            f"+{{target: {prepared.target_canonical}, type: {prepared.rel_type}}})"
        )
    typer.echo(preview_line)
    if prepared.status_outcome is not None:
        suffix = _status_export_preview_suffix(prepared.status_outcome)
        typer.echo(f"  ~ bundle/{prepared.target_canonical}.md ({suffix.lstrip('; ')})")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if not auto and prepared.review:
        if sys.stdin.isatty():
            typer.confirm(prepared.confirmation.prompt, abort=True)
        else:
            # #959: the wording comes from the staged request, not a
            # literal here, so an api/mcp adapter driving this gate
            # headlessly reads the same sentence the CLI prints.
            typer.echo(prepared.confirmation.non_tty_refusal, err=True)
            raise typer.Exit(code=1)

    # Issue #313: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    drift_baselines = {
        source_path: prepared.source_bytes,
        log_path: prepared.log_bytes,
    }
    if prepared.target_bytes is not None:
        drift_baselines[target_path] = prepared.target_bytes
    _reject_drifted_targets(layout, drift_baselines, "relate")

    try:
        application_lifecycle.relate_core(
            source_path, log_path, prepared, target_path=target_path
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos relate: failed while writing the relate -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos relate: added a {prepared.rel_type!r} relation from "
        f"'bundle/{prepared.source_canonical}.md' to "
        f"'bundle/{prepared.target_canonical}.md' ({log_path.name} updated)."
    )

    commit_paths = [f"bundle/{prepared.source_canonical}.md", "bundle/log.md"]
    if prepared.new_target_text is not None:
        commit_paths.insert(1, f"bundle/{prepared.target_canonical}.md")
    _autocommit(
        root,
        commit_paths,
        f"openkos: relate {prepared.source_canonical} -> "
        f"{prepared.target_canonical} ({prepared.rel_type})",
    )

    # #640: `cfg=None` -- `relate` never reads config at this layer.
    _refresh_derived_after_write(layout, None, verb="relate")


@app.command(
    "set-sensitivity",
    help=(
        "Set one concept's sensitivity level directly, without a sweep or a "
        "model call. Scope is exactly the named concept and never its "
        "siblings -- except that raising a Source's level also raises every "
        "concept derived from it, and only ever upward."
    ),
    rich_help_panel="Curate",
)
@_guard_workspace_lock("set-sensitivity")
def set_sensitivity_cmd(
    concept_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') to update.",
    ),
    level: str = typer.Argument(
        ...,
        help="New sensitivity level: one of 'public', 'private', 'confidential'.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
    allow_downgrade: bool = typer.Option(
        False,
        "--allow-downgrade",
        help=(
            "Permit a lowering assignment on a path where the confirm "
            "prompt does not run (--auto, or config review: false)."
        ),
    ),
) -> None:
    """Set exactly one existing concept's `sensitivity` field directly --
    the write layer of the sensitivity-config domain (write-verb #185).
    Touches `<concept-id>`'s own frontmatter, PLUS, when `<concept-id>`
    resolves to a Source-typed concept, raises (never lowers) the
    `sensitivity` of every provenance descendant found by
    `bundle.provenance.find_provenance_descendants`, each combined via
    `okf.combine_sensitivity` (ADR-0003, ADR-0009). No sibling concept and
    no non-Source target's derived concept is ever read for propagation or
    written.

    Vocabulary validation happens FIRST, before any read of the concept
    file or the workspace: `level` must exact-match one of
    `okf.SENSITIVITY_ORDER`. `config.require_workspace` and
    `config.read_config` run next, then `concept_id` is resolved via the
    same `_resolve_concept_path` `forget`/`relate` use -- rejecting an
    absolute id, any `..` segment, a reserved basename, or a nonexistent
    concept file, all as `ValueError`, all before any write.

    Idempotence is checked by EXACT equality against the raw, unstripped
    current `sensitivity` value: if it already equals `level`, this is a
    no-op -- a message is printed, exit 0, no write, no commit. A dirty
    value (missing, blank, or unrecognized) never short-circuits here, so
    it always reaches `okf.sensitivity_direction`'s fail-closed ranking.

    The downgrade gate runs next, BEFORE the preview: `okf
    .sensitivity_direction(current, level) == "lower"` is permitted
    whenever the confirm prompt will actually run (interactive TTY,
    `--auto` not passed, and config `review` not `false`). On every path
    where the prompt does NOT run -- `--auto`, or workspace config
    `review: false`, which silences the prompt for every verb -- a
    lowering additionally requires `--allow-downgrade`; without it this
    refuses in Phase A (exit 1, no write, no commit, no preview), naming
    the required flag on stderr (ADR-0008).

    The preview line shows the concept file, the direction (raising/
    lowering/normalizing), the raw current value (`!r`), and the new
    level. The confirm gate mirrors `relate`'s exact precedence: `--auto`
    skips it; otherwise config `review: false` skips it; otherwise a TTY
    prompts via `typer.confirm` and aborts on decline; otherwise
    (non-TTY, no `--auto`) this refuses to write.

    When `<concept-id>` resolves to a Source-typed concept (`metadata.get
    ("type") == "Source"`) AND the assignment itself raises (`direction ==
    "raise"`), Phase A additionally reads a whole-bundle snapshot, resolves
    its provenance descendants, and computes `okf.combine_sensitivity
    (descendant_current, level)` per descendant -- staging a write only
    when that is a strict raise over the descendant's current value. Every
    staged raise appears in the preview and the success message. A
    provenance reference that resolves to no file in the snapshot emits a
    stderr WARNING naming it and is excluded -- fail-closed, never lowered,
    never blocking the Source's own write.

    That WARNING is SCOPED to the invoked Source, not to the bundle (issue
    #232): only a concept reachable from `<concept-id>` through provenance
    is reported. The snapshot it reads stays whole-bundle -- an id existing
    anywhere in the bundle is resolvable, and narrowing the snapshot would
    invent warnings -- so only the reporting scope narrows. Without that
    scope, every OTHER Source's own raw `resource` entry (never a bundle
    id) produced a WARNING on every run. Reachability here is
    `bundle.provenance.provenance_reachable`'s non-empty-INTERSECTION
    relation, deliberately WIDER than the subset closure that gates the
    writes: a descendant citing both `<concept-id>` and a dangling id is
    excluded from that closure, and is precisely the case this WARNING
    exists for. A concept citing ONLY an unresolvable id is unreachable and
    therefore silent here -- and NOTHING in the toolchain reports that case
    today: `lint`'s dangling check scans `relations:` and body links only,
    never `provenance:`. `lint` is the INTENDED FUTURE owner of that
    bundle-wide detection; the work is tracked as issue #257 and is not part
    of this change.

    A non-Source target, or a Source assignment that is a lowering or a
    same-rank normalization, skips this scan entirely -- a downgrade must
    never cascade even when `combine_sensitivity` would compute a raise for
    some individual descendant sitting below the new (lower) level.

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every path this run intends to write
    (the target concept, each staged descendant, and `log.md`) and refuses
    the WHOLE run (exit 3, nothing written) if any changed or vanished since
    Phase A read it (issues #306, #313, #319).

    A confirmed write re-renders the frontmatter (`okf.dump_frontmatter`,
    changing only `sensitivity`), appends a `log.md` entry (no
    `index.md` change -- editing an existing catalog entry, not a new
    one). Phase B writes in this order: every staged descendant raise,
    then the target concept, then `log.md` -- via `fsio.write_atomic` --
    then one `_autocommit` covering every changed path, with message
    `openkos: set-sensitivity <id> -> <level>`. There is no cross-file
    rollback (matching `relate`/`merge`): a mid-way failure leaves the
    bundle over-classified, never under-classified. Any failure, Phase A or
    Phase B, is caught (`OSError`/`ValueError`) and reported on stderr
    (exit 1), never a raw traceback.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    try:
        if level not in okf.SENSITIVITY_ORDER:
            raise ValueError(
                f"{level!r} is not a valid sensitivity level (expected one "
                f"of {sorted(okf.SENSITIVITY_ORDER)})"
            )

        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise ValueError(workspace_reason)
        cfg = config.read_config(root)

        concept_path, canonical_id = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, concept_id
        )
        # One `_snapshot_read` observation: the decoded text feeds the
        # parsers below, the raw bytes feed `_reject_drifted_targets`
        # (issues #306, #318).
        concept_bytes, concept_text = _snapshot_read(concept_path)
        metadata, body = okf.load_frontmatter(concept_text)
        current = metadata.get("sensitivity")
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos set-sensitivity: refusing to set -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    if current == level:
        typer.echo(
            f"openkos set-sensitivity: {canonical_id!r} already has "
            f"sensitivity {level!r}; no change made."
        )
        return

    direction = okf.sensitivity_direction(current, level)
    # ADR-0008: lowering rides on the confirm prompt as its whole friction
    # budget, so the gate must key on whether a human is ACTUALLY asked --
    # not merely on whether review is enabled. `confirm_enabled` answers
    # "is review on for this run"; `prompt_will_run` additionally requires
    # an interactive stdin. Dropping the TTY term here would let a piped
    # `review: true` run skip the gate, print the preview, and then refuse
    # via the Phase-B ladder naming `--auto` -- a remedy that still
    # refuses. Both gates below read these two names; never re-spell either
    # predicate inline, or the security rule acquires a second copy that
    # can drift.
    confirm_enabled = not auto and cfg.review
    prompt_will_run = confirm_enabled and sys.stdin.isatty()

    if direction == "lower" and not prompt_will_run and not allow_downgrade:
        typer.echo(
            "openkos set-sensitivity: refusing to lower "
            f"{canonical_id} from {current!r} to {level} without "
            "confirmation -- no confirm prompt will run (--auto, config "
            "review: false, or a non-interactive stdin); re-run with "
            "--allow-downgrade.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Raise-only propagation to provenance descendants (design: "Set-time
    # propagation"; ADR-0009). Source detection is the OKF `type` field of
    # record, never a path convention -- a non-Source target skips this
    # whole-bundle scan entirely and behaves byte-identically to today.
    # Propagation ALSO requires the Source's own assignment to be a raise
    # (`direction`, computed above) so a downgrade never cascades, even
    # when `combine_sensitivity` would compute a raise for some individual
    # descendant below the new (lower) level.
    descendant_raises: list[okf.DescendantRaise] = []
    bundle_snapshot: dict[str, str] = {}
    bundle_bytes: dict[str, bytes] = {}
    if metadata.get("type") == "Source" and direction == "raise":
        try:
            for path in okf.iter_bundle_markdown(layout.bundle_dir):
                if path.name in okf.RESERVED_FILENAMES:
                    continue
                if path == concept_path:
                    continue
                rel = path.relative_to(layout.bundle_dir).as_posix()
                bundle_bytes[rel], bundle_snapshot[rel] = _snapshot_read(path)

            # Unresolvable provenance (design: "Unresolvable provenance"):
            # `known_extra_ids={canonical_id}` paired with the
            # target-excluding `bundle_snapshot` above reproduces the exact
            # historical pairing (design D7) that keeps the target's own
            # `provenance` from ever being warned about. Each unresolvable
            # entry is reported on stderr; the citing concept is
            # fail-closed excluded -- `resolve_source_raises`'s own
            # non-empty-subset rule already keeps it out of the raises
            # below, so this is purely reporting, never an extra write
            # gate.
            #
            # `root_ids={canonical_id}` narrows that REPORTING to what is
            # reachable from the invoked Source (issue #232); the snapshot
            # itself stays whole-bundle, because narrowing it would make an
            # id that exists elsewhere look unresolvable. Every OTHER Source
            # cites its own raw `resource` (`provenance=[resource]`, built
            # above at the `ingest` call site), which never normalizes to a
            # bundle id, so an unscoped scan emitted one bogus WARNING per
            # unrelated Source on every run. See
            # `find_unresolvable_provenance`'s docstring for why that scope
            # is a reachability relation rather than the subset closure
            # gating the writes.
            for member_id, entry_id in bundle_provenance.find_unresolvable_provenance(
                bundle_snapshot,
                known_extra_ids={canonical_id},
                root_ids={canonical_id},
            ):
                typer.echo(
                    "openkos set-sensitivity: WARNING -- "
                    f"{member_id!r} cites unresolvable provenance "
                    f"{entry_id!r}; excluded from propagation.",
                    err=True,
                )

            descendant_raises = bundle_provenance.resolve_source_raises(
                bundle_snapshot, source_id=canonical_id, level=level
            )
        except (OSError, ValueError) as exc:
            typer.echo(
                f"openkos set-sensitivity: failed while resolving the "
                f"descendant closure of {canonical_id!r} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc

    now = datetime.now(UTC)

    try:
        log_bytes, log_text = _snapshot_read(log_path)
        metadata["sensitivity"] = level
        new_concept_text = okf.dump_frontmatter(metadata, body)
        log_line = (
            f"**Set-sensitivity**: Set [{canonical_id}](/{canonical_id}.md) "
            f"sensitivity to {level!r} (was {current!r})."
        )
        new_log_text = bundle_log.insert_log_entry(
            log_text, now.astimezone().date(), log_line
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos set-sensitivity: failed while preparing the "
            f"set-sensitivity -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    direction_word = {
        "raise": "raising",
        "lower": "lowering",
        "same": "normalizing",
    }[direction]
    typer.echo("openkos set-sensitivity: proposed changes:")
    typer.echo(
        f"  ~ bundle/{canonical_id}.md (sensitivity: {direction_word} "
        f"{current!r} -> {level})"
    )
    for descendant_raise in descendant_raises:
        typer.echo(
            f"  ~ bundle/{descendant_raise.concept_id}.md (sensitivity: "
            f"raising {descendant_raise.current!r} -> "
            f"{descendant_raise.new_level})"
        )
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if confirm_enabled:
        if prompt_will_run:
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            # A lowering reaches here only when `--allow-downgrade` was
            # passed -- without it the Phase-A gate already refused, naming
            # that flag. So this refusal is about the WRITE lacking
            # confirmation, not about the downgrade lacking authorization,
            # and `--auto` is the correct remedy to name.
            typer.echo(
                "openkos set-sensitivity: refusing to write without "
                "confirmation -- stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    # Issue #306: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    _reject_drifted_targets(
        layout,
        {
            **{
                layout.bundle_dir / f"{descendant_raise.concept_id}.md": bundle_bytes[
                    f"{descendant_raise.concept_id}.md"
                ]
                for descendant_raise in descendant_raises
            },
            concept_path: concept_bytes,
            log_path: log_bytes,
        },
        "set-sensitivity",
    )

    landed: list[str] = []
    try:
        # Write order: descendants BEFORE the target concept BEFORE
        # `log.md` (design: "Descendants are written BEFORE the target
        # concept"). A mid-way failure then leaves the bundle
        # over-classified, never under-classified -- there is no
        # cross-file rollback, matching `relate`/`merge`. `landed` records
        # each path only AFTER its `write_atomic` call returns, so a
        # failure names exactly the paths already on disk (design D9,
        # issue #233).
        for descendant_raise in descendant_raises:
            descendant_path = f"bundle/{descendant_raise.concept_id}.md"
            fsio.write_atomic(
                layout.bundle_dir / f"{descendant_raise.concept_id}.md",
                descendant_raise.content,
            )
            landed.append(descendant_path)
        fsio.write_atomic(concept_path, new_concept_text)
        landed.append(f"bundle/{canonical_id}.md")
        fsio.write_atomic(log_path, new_log_text)
        landed.append("bundle/log.md")
    except (OSError, ValueError) as exc:
        # Distinct from the two phases above on purpose: this one is
        # reached only after the write phase began, so the concept file may
        # already be on disk while `log.md` is not. "refusing" would tell an
        # operator nothing happened, which is exactly wrong here.
        landed_suffix = (
            f"Already written (left over-classified, not rolled back): "
            f"{', '.join(landed)}."
            if landed
            else "No path was written."
        )
        typer.echo(
            f"openkos set-sensitivity: failed while writing the "
            f"set-sensitivity -- {exc}. {landed_suffix}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if descendant_raises:
        propagated = ", ".join(
            f"'bundle/{descendant_raise.concept_id}.md' -> {descendant_raise.new_level}"
            for descendant_raise in descendant_raises
        )
        typer.echo(
            f"openkos set-sensitivity: set 'bundle/{canonical_id}.md' "
            f"sensitivity to {level} ({log_path.name} updated). Also raised "
            f"{len(descendant_raises)} provenance descendant(s): "
            f"{propagated}."
        )
    else:
        typer.echo(
            f"openkos set-sensitivity: set 'bundle/{canonical_id}.md' "
            f"sensitivity to {level} ({log_path.name} updated). Only this "
            "concept was changed; no sibling or derived object was touched."
        )
        # #571: raising a DERIVED object protects one file while extraction
        # replicated its content into siblings -- the ADR-0009 containment
        # lever is the Source, whose raise-only propagation covers every
        # descendant. Name that lever, or the success message implies a
        # protection the user did not get. Raise-only: on a lowering the
        # note would read as advice to lower the Source too.
        raw_provenance = metadata.get("provenance")
        provenance_sources = [
            entry
            for entry in (raw_provenance if isinstance(raw_provenance, list) else [])
            if isinstance(entry, str) and entry.startswith("sources/")
        ]
        if (
            direction == "raise"
            and metadata.get("type") != "Source"
            and provenance_sources
        ):
            named = ", ".join(provenance_sources)
            if len(provenance_sources) == 1:
                lever = (
                    "raise the Source: openkos set-sensitivity "
                    f"{provenance_sources[0]} {level}"
                )
            else:
                lever = f"raise each Source: {named}"
            typer.echo(
                f"openkos set-sensitivity: note -- '{canonical_id}' was "
                f"derived from {named}; raising it does not contain content "
                "its source(s) replicated into sibling objects. To contain "
                f"everything derived from them, {lever}. For every Source "
                "that reaches this object, including through intermediate "
                f"objects: openkos list --sources {canonical_id}."
            )

    _autocommit(
        root,
        [
            f"bundle/{descendant_raise.concept_id}.md"
            for descendant_raise in descendant_raises
        ]
        + [f"bundle/{canonical_id}.md", "bundle/log.md"],
        f"openkos: set-sensitivity {canonical_id} -> {level}",
    )

    # #640: a frontmatter-only write is a vector cache-hit (#554 excludes
    # frontmatter from embeddings), so this costs FTS+graph rebuilds only.
    _refresh_derived_after_write(layout, cfg, verb="set-sensitivity")


@app.command(
    "backfill-sensitivity",
    help=(
        "Raise sensitivity across the whole bundle where a concept sits "
        "below the level its source requires. Never lowers one."
    ),
    rich_help_panel="Maintain",
)
@_guard_workspace_lock("backfill-sensitivity")
def backfill_sensitivity_cmd(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Dedicated, raise-only, bundle-wide sweep that closes the sensitivity
    gap left by bundles or descendants created before Source-to-descendant
    propagation existed (issue #219/#231). Wires the pure
    `bundle.provenance.resolve_backfill_raises` sweep core (design D4/D5)
    into Typer's confirm-gate and write scaffold, mirroring
    `set_sensitivity_cmd`'s Phase A/Phase B shape exactly.

    Unlike `set-sensitivity`, this command takes no `<concept-id>`
    argument -- it treats every `type: Source` concept in the bundle as an
    independent closure root in a single pass. It is bundle-wide only;
    `set-sensitivity` already covers the single-Source case. There is no
    `--allow-downgrade` equivalent: the sweep is raise-only by construction
    and never lowers a descendant. There is no `--dry-run` flag either --
    the preview shown before confirmation, or declining the prompt, already
    serves as the dry run (spec Non-Goals).

    SINCE ISSUE #697 the sweep has TWO producers, merged by max inside
    `resolve_backfill_raises`. The per-Source closure walk above is one; the
    other is `resolve_cited_high_water_raises`, which maintains ADR-0003's
    high-water mark for a document whose provenance spans more than one
    Source. ADR-0012 deferred that case ("stays reported, not resolved") and
    ADR-0016 closes it: raising a cited Source used to leave every
    multi-source insight below its own inputs, repairable only by hand, so
    the `sensitivity` gate that withholds a document from an LLM send
    (`sensitivity.blocks_llm_send`) kept passing content the operator had
    just reclassified. `lint`'s `multi-source-uncovered` finding still
    reports those documents -- it now points HERE as well as at
    `set-sensitivity`, rather than ruling this verb out.

    Phase A: `require_workspace` -> `read_config` -> one `sorted(rglob)`
    bundle snapshot (reserved filenames skipped) -> `resolve_backfill_raises`
    computes every merged-by-max raise across every Source (design D4/D5).
    When the result is empty, this prints an explicit "nothing to
    backfill" message, writes nothing, creates no commit, and exits 0 --
    idempotent by construction, since a second run over an already-swept
    bundle recomputes zero raises. Otherwise, one preview lists every
    staged `(concept_id, current -> new_level)` raise (sorted by
    `concept_id`, matching `resolve_backfill_raises`'s own order), then the
    confirm gate mirrors `set_sensitivity_cmd`'s exact precedence: `--auto`
    skips it; otherwise config `review: false` skips it; otherwise a TTY
    prompts via `typer.confirm` and aborts on decline; otherwise (non-TTY,
    no `--auto`) this refuses to write.

    Deliberately does NOT call `find_unresolvable_provenance` (design D8):
    every Source cites its raw `resource`, which never resolves to a bundle
    id, so a bundle-wide run would emit one WARNING per Source on every
    invocation, including the no-op path above. That signal is delivered by
    `lint`'s existing `dangling` finding, never this sweep.

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every staged descendant plus `log.md`
    and refuses the WHOLE run (exit 3, nothing written) if any changed or
    vanished since Phase A read it (issues #306, #313, #319).

    Phase B writes every merged raise (sorted by `concept_id`), then
    appends exactly one dated `log.md` entry summarizing the whole sweep,
    then issues exactly one `_autocommit` covering every changed path.
    There is no cross-file rollback (matching `set-sensitivity`/`relate`/
    `merge`): a mid-way failure leaves the bundle over-classified, never
    under-classified, and the failure message names every path already
    written before the failure (design D9, mirrors the #233 fix). Any
    failure, Phase A or Phase B, is caught (`OSError`/`ValueError`) and
    reported on stderr (exit 1), never a raw traceback.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise ValueError(workspace_reason)
        cfg = config.read_config(root)

        bundle_snapshot: dict[str, str] = {}
        bundle_bytes: dict[str, bytes] = {}
        for path in okf.iter_bundle_markdown(layout.bundle_dir):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            bundle_bytes[rel], bundle_snapshot[rel] = _snapshot_read(path)

        descendant_raises = bundle_provenance.resolve_backfill_raises(bundle_snapshot)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos backfill-sensitivity: refusing to run -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    if not descendant_raises:
        typer.echo(
            "openkos backfill-sensitivity: nothing to backfill -- every "
            "document already meets or exceeds both its Source's sensitivity "
            "and the high-water mark of everything it cites."
        )
        return

    confirm_enabled = not auto and cfg.review
    prompt_will_run = confirm_enabled and sys.stdin.isatty()

    now = datetime.now(UTC)
    try:
        log_bytes, log_text = _snapshot_read(log_path)
        propagated = ", ".join(
            f"'bundle/{descendant_raise.concept_id}.md' -> {descendant_raise.new_level}"
            for descendant_raise in descendant_raises
        )
        log_line = (
            f"**Backfill-sensitivity**: Raised {len(descendant_raises)} "
            f"document(s) to their Source's sensitivity or to the high-water "
            f"mark of what they cite: {propagated}."
        )
        new_log_text = bundle_log.insert_log_entry(
            log_text, now.astimezone().date(), log_line
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos backfill-sensitivity: failed while preparing the "
            f"backfill-sensitivity -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos backfill-sensitivity: proposed changes:")
    for descendant_raise in descendant_raises:
        typer.echo(
            f"  ~ bundle/{descendant_raise.concept_id}.md (sensitivity: "
            f"raising {descendant_raise.current!r} -> "
            f"{descendant_raise.new_level})"
        )
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if confirm_enabled:
        if prompt_will_run:
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos backfill-sensitivity: refusing to write without "
                "confirmation -- stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    # Issue #306: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    _reject_drifted_targets(
        layout,
        {
            **{
                layout.bundle_dir / f"{descendant_raise.concept_id}.md": bundle_bytes[
                    f"{descendant_raise.concept_id}.md"
                ]
                for descendant_raise in descendant_raises
            },
            log_path: log_bytes,
        },
        "backfill-sensitivity",
    )

    landed: list[str] = []
    try:
        # Write order: every staged descendant raise, then `log.md`
        # (design D4 Phase B). `landed` records each path only AFTER its
        # `write_atomic` call returns, so a failure names exactly the
        # paths already on disk (design D9, mirrors #233).
        for descendant_raise in descendant_raises:
            descendant_path = f"bundle/{descendant_raise.concept_id}.md"
            fsio.write_atomic(
                layout.bundle_dir / f"{descendant_raise.concept_id}.md",
                descendant_raise.content,
            )
            landed.append(descendant_path)
        fsio.write_atomic(log_path, new_log_text)
        landed.append("bundle/log.md")
    except (OSError, ValueError) as exc:
        landed_suffix = (
            f"Already written (left over-classified, not rolled back): "
            f"{', '.join(landed)}."
            if landed
            else "No path was written."
        )
        typer.echo(
            f"openkos backfill-sensitivity: failed while writing the "
            f"backfill-sensitivity -- {exc}. {landed_suffix}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    propagated = ", ".join(
        f"'bundle/{descendant_raise.concept_id}.md' -> {descendant_raise.new_level}"
        for descendant_raise in descendant_raises
    )
    typer.echo(
        f"openkos backfill-sensitivity: raised {len(descendant_raises)} "
        f"document(s) ({log_path.name} updated): {propagated}."
    )

    _autocommit(
        root,
        [
            f"bundle/{descendant_raise.concept_id}.md"
            for descendant_raise in descendant_raises
        ]
        + ["bundle/log.md"],
        "openkos: backfill-sensitivity",
    )


@app.command(
    "sync-tags",
    help=(
        "Add a Source's current tags to the derived concepts it grounds. "
        "Union only -- never removes a tag. No LLM."
    ),
    rich_help_panel="Maintain",
)
@_guard_workspace_lock("sync-tags", commit_phase=True)
def sync_tags_cmd(
    source_id: str | None = typer.Argument(
        None,
        help=(
            "Bundle-relative Source concept id (path minus '.md') to sync "
            "tags from. Exactly one of this or --all is required."
        ),
    ),
    all_: bool = typer.Option(
        False,
        "--all",
        help=(
            "Sync every Source in the bundle in one run. Exactly one of "
            "the positional <source-id> or this flag is required."
        ),
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Add each Source's current `tags` to the derived concepts its
    provenance closure grounds (ADR-0033; source-tag-sync design), mirroring
    `set-sensitivity`'s Source branch and `backfill-sensitivity`'s
    bundle-wide sweep shape. Closes the drift `ingest` deliberately never
    repairs on re-ingest (ingestion: "Derived Object Provenance and
    Sensitivity Inheritance").

    Argument validation runs FIRST, before any bundle read: exactly one of
    the positional `<source-id>` or `--all` is required -- supplying both,
    or neither, refuses with exit 1 naming the two accepted forms.

    Phase A delegates entirely to `application.prepare_sync_tags`: it
    resolves the target Source(s) (a single named Source via the same
    `resolve_concept_path` id-safety every other write verb uses, or every
    `type: Source` concept for `--all`), computes the write set as each
    Source's `bundle.provenance.find_provenance_descendants` closure minus
    the Source itself and minus any `type: Source` member, and stages a
    union-only tag write (`okf.union_tags`) for every member whose union
    adds a tag it does not already carry. A member with a malformed `tags`
    value, or one ranked strictly below its Source's sensitivity
    (`okf.sensitivity_direction`), is skipped and reported instead of
    rewritten; skip lines print on stderr BEFORE the preview, each naming
    the concept once even when multiple Sources reach it under `--all`.

    An empty result (nothing staged) prints that there is nothing to sync
    and exits 0 -- no write, no commit. Otherwise the preview lists every
    staged file as `~ bundle/<id>.md (tags added: <a>, <b>)` in concept-id
    order, then `~ log.md (new dated entry)`. The confirm gate mirrors
    every other mutating verb's exact precedence: `--auto` skips it;
    otherwise config `review: false` skips it; otherwise an interactive TTY
    prompts via `typer.confirm` and a decline aborts (exit 1) with nothing
    written; otherwise (non-TTY, no `--auto`) this refuses to write (exit
    1), naming `--auto`.

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every staged descendant, every root
    Source that contributed a staged tag, and `log.md`, and refuses the
    WHOLE run (exit 3, nothing written) if any changed or vanished since
    Phase A read it (issues #306, #313, #319) -- the Source is a drift
    baseline even though it is never itself written (design Decision 5).

    Phase B (`application.sync_tags_core`) writes every staged descendant in
    concept-id order, then `log.md`, then one `_autocommit` covering every
    changed path with message `openkos: sync-tags <source-id>` or
    `openkos: sync-tags --all`. Neither the `log.md` entry nor the commit
    message names any tag value (ADR-0033 Decision 6) -- both name only the
    Source id (or Source count, for `--all`) and a concept count. There is
    no cross-file rollback: a mid-way write failure names every path
    already landed. A successful write refreshes the derived stores once,
    as every other bundle-writing verb does.
    """
    if source_id is not None and all_:
        typer.echo(
            "openkos sync-tags: refusing to sync -- supply a <source-id> "
            "argument or --all, not both.",
            err=True,
        )
        raise typer.Exit(code=1)
    if source_id is None and not all_:
        typer.echo(
            "openkos sync-tags: refusing to sync -- supply a <source-id> "
            "argument or --all.",
            err=True,
        )
        raise typer.Exit(code=1)

    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise ValueError(workspace_reason)
        cfg = config.read_config(root)
        prepared = application_lifecycle.prepare_sync_tags(
            layout, source_id, now=datetime.now(UTC)
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos sync-tags: refusing to sync -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    for skip in prepared.skips:
        if skip.reason == "malformed-tags":
            typer.echo(
                f"openkos sync-tags: WARNING -- 'bundle/{skip.concept_id}.md' "
                "has a malformed 'tags' value; left unchanged.",
                err=True,
            )
        else:
            typer.echo(
                f"openkos sync-tags: note -- 'bundle/{skip.concept_id}.md' is "
                "below its Source's sensitivity; run 'openkos "
                "set-sensitivity' (or 'openkos backfill-sensitivity') to "
                "raise it first.",
                err=True,
            )

    if not prepared.additions:
        typer.echo("openkos sync-tags: nothing to sync.")
        return

    typer.echo("openkos sync-tags: proposed changes:")
    for addition in prepared.additions:
        typer.echo(
            f"  ~ bundle/{addition.concept_id}.md (tags added: "
            f"{', '.join(addition.added)})"
        )
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm(prepared.confirmation.prompt, abort=True)
        else:
            typer.echo(prepared.confirmation.non_tty_refusal, err=True)
            raise typer.Exit(code=1)

    # The commit phase (#1137): everything above ran without the workspace lock.
    with _commit_section_for(root)():
        # Issue #306: every byte below was computed from a pre-prompt read, so
        # re-validate each target now -- after the gate, before the first
        # write. The read dependencies ride along: a member that stopped being
        # grounded in its Source while the prompt waited must not be tagged.
        _reject_drifted_targets(
            layout,
            {
                root / rel: content
                for rel, content in (
                    *prepared.read_dependencies.items(),
                    *prepared.baselines.items(),
                )
            },
            "sync-tags",
        )

        try:
            landed = application_lifecycle.sync_tags_core(layout, prepared)
        except (OSError, ValueError) as exc:
            typer.echo(
                f"openkos sync-tags: failed while writing the sync-tags -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc

        typer.echo(
            f"openkos sync-tags: added tags to {len(prepared.additions)} "
            f"concept(s) ({log_path.name} updated)."
        )

        commit_subject = (
            f"openkos: sync-tags {prepared.roots[0]}"
            if source_id is not None
            else "openkos: sync-tags --all"
        )
        _autocommit(root, landed, commit_subject)

    # #640: `cfg` already read above -- a tag write changes the embedding
    # input (design Decision 7), so this is not suppressed.
    _refresh_derived_after_write(layout, cfg, verb="sync-tags")


@app.command(
    "normalize-names",
    help=(
        "Rename on-disk files and directories whose names are not in "
        "normalized Unicode form, so tools compare them consistently."
    ),
    rich_help_panel="Maintain",
)
@_guard_workspace_lock("normalize-names", commit_phase=True)
def normalize_names_cmd(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Rename every on-disk name (file OR directory) under `bundle_dir`
    that is not NFC to its NFC form -- the dedicated mutating verb that
    remediates what `lint`'s `non-nfc-name` finding only reports (issue
    #474 part 2). Structural twin of `backfill-sensitivity`
    (design D6): Phase A snapshot -> preview -> confirm gate -> drift
    re-check -> Phase B writes -> one `log.md` entry -> one `_autocommit`.

    Phase A obtains its candidate set from `lint_check.scan_non_nfc_entries`
    -- the SAME scan `openkos lint`'s `non-nfc-name` finding uses (design
    D1) -- plus `lint_check.scan_stranded_rename_temps`, whose result is
    printed as a stderr WARNING per stranded entry and never touched
    (design D3: a temp can be stranded by a hard kill between
    `fsio.rename_two_step`'s two hops or by a double fault in its guard
    or hop-2 branch where the suppressed restore also fails, PR #492 --
    its post-rename verification branch strands no temp, issue #495 --
    and auto-deleting or
    auto-renaming it would be data loss or a guess). Every candidate is
    classified as a planned rename or a non-fatal skip (collision: an
    NFC-spelled sibling already exists; symlink: never followed;
    vanished: the entry's parent listing became unreadable between the
    scan and classification) -- design D4/D5 -- then
    sorted `(-depth, rel_posix)` so a child renames before its ancestor.
    An empty or all-skip plan prints an explicit no-op line, writes
    nothing, and exits 0 -- which is also the idempotency property (a
    second run over an already-normalized bundle plans zero renames).

    The preview lists every planned rename (raw -> NFC target) and skip
    (with its reason) in apply order; a decomposed directory previews as
    ONE entry noting its subtree moves with it, never one line per
    descendant (design D4). The confirm gate mirrors
    `backfill_sensitivity_cmd`'s exact precedence: `--auto` skips it;
    otherwise config `review: false` skips it; otherwise a TTY prompts via
    `typer.confirm` and aborts (exit 1) on decline; otherwise (non-TTY, no
    `--auto`) this refuses to write.

    Immediately before Phase B, a PURPOSE-BUILT drift re-check
    re-validates every planned rename against current on-disk state
    (design D4): each entry's `raw_name` must still be present
    byte-exactly, its `nfc_name` must still be absent byte-exactly, and it
    must not have become a symlink. Any failure demotes that entry to a
    reported skip, never a crash. `log.md` alone goes through the
    existing `_reject_drifted_targets` (exit 3), matching every other
    mutating verb. If the drift re-check empties the plan, the run writes
    nothing, appends no log entry, and creates no commit.

    Phase B applies `fsio.rename_two_step` per entry in apply order, then
    appends exactly one dated `log.md` entry summarizing the run (design
    D6's bounded line: counts always; the renamed pairs are listed inline
    only when the total entry count is <= 5, so the line stays bounded
    and single -- `insert_log_entry` rejects newlines), then issues
    exactly one `_autocommit` scoped to every renamed entry's OLD and NEW
    path plus `log.md` (design D7) -- staging scope, not resulting diff:
    on a git configuration where the old and new spellings were already
    recorded identically (e.g. macOS `core.precomposeunicode=true`,
    design.md S1 Q7/Q8), staging both paths can legitimately produce no
    diff, and `log.md`'s entry keeps the commit non-empty regardless.
    `_autocommit` stays best-effort and non-fatal (not a repo, no git
    identity, or any `GitError` -- including the untracked-old-path case,
    Key Decisions Recorded a) -> stderr WARNING, exit code unchanged,
    renames already applied stay on disk. There is no cross-file rollback:
    a mid-Phase-B failure names every entry already landed by its OLD
    path only -- final paths are resolved strictly after the whole batch
    (review R3-001), so they do not exist yet at failure time --
    mirroring `backfill_sensitivity_cmd`'s
    design D9 pattern; any failure is caught (`OSError`/`ValueError`) and
    reported on stderr (exit 1), never a raw traceback.

    `index.md` is never touched (no concept id, `relations:` target,
    `provenance:` reference, or file body content changes -- design D6);
    no reindex is triggered (design D7).
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise ValueError(workspace_reason)
        cfg = config.read_config(root)
        entries = lint_check.scan_non_nfc_entries(layout.bundle_dir)
        stranded_temps = lint_check.scan_stranded_rename_temps(layout.bundle_dir)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos normalize-names: refusing to run -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    for stranded in stranded_temps:
        rel = stranded.relative_to(root).as_posix()
        typer.echo(
            f"openkos normalize-names: WARNING -- {rel!r} looks like a "
            "rename left stranded by an interrupted run (temp prefix "
            f"{fsio.RENAME_TEMP_PREFIX!r}); its original spelling is not "
            "recoverable from the temp name, so it is left untouched -- "
            "rename it by hand once you know what it should be.",
            err=True,
        )

    planned: list[lint_check.NonNfcEntry] = []
    skips: list[tuple[lint_check.NonNfcEntry, str, str]] = []
    for entry in entries:
        if entry.is_symlink:
            skips.append((entry, "symlink", "symlink"))
            continue
        try:
            sibling_listing = os.listdir(entry.path.parent)  # noqa: PTH208
        except OSError:
            skips.append((entry, "vanished", "vanished"))
            continue
        if entry.nfc_name in sibling_listing:
            skips.append((entry, "collision", f"{entry.nfc_name!r} already exists"))
            continue
        planned.append(entry)
    planned.sort(key=lambda entry: (-entry.depth, entry.rel_posix))

    if not planned:
        if skips:
            skip_kind_counts = Counter(kind for _entry, kind, _reason in skips)
            skip_detail = ", ".join(
                f"{kind}: {count}" for kind, count in sorted(skip_kind_counts.items())
            )
            typer.echo(
                "openkos normalize-names: nothing to normalize -- every "
                f"on-disk name under {layout.bundle_dir.name}/ is already "
                f"NFC ({len(skips)} skipped -- {skip_detail})."
            )
        else:
            typer.echo(
                "openkos normalize-names: nothing to normalize -- every "
                f"on-disk name under {layout.bundle_dir.name}/ is already NFC."
            )
        return

    confirm_enabled = not auto and cfg.review
    prompt_will_run = confirm_enabled and sys.stdin.isatty()

    typer.echo(
        f"openkos normalize-names: proposed renames ({len(planned)}, "
        f"deepest first), {len(skips)} skipped:"
    )
    for entry in planned:
        suffix = (
            "  (directory -- its whole subtree moves with it)" if entry.is_dir else ""
        )
        typer.echo(f"  ~ {entry.rel_posix!r} -> {entry.nfc_name!r}{suffix}")
    for entry, _kind, reason in skips:
        typer.echo(f"  ! {entry.rel_posix!r} -- skipped: {reason}")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if confirm_enabled:
        if prompt_will_run:
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos normalize-names: refusing to write without "
                "confirmation -- stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    # The commit phase (#1137): the scan, the preview and the prompt above held
    # no workspace lock.
    with _commit_section_for(root)():
        try:
            log_bytes, log_text = _snapshot_read(log_path)
        except (OSError, ValueError) as exc:
            typer.echo(
                f"openkos normalize-names: failed while reading {log_path.name} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc

        # Issue #306-style guard: `log.md` is re-validated against its
        # pre-prompt snapshot immediately before the first write.
        _reject_drifted_targets(layout, {log_path: log_bytes}, "normalize-names")

        # Purpose-built drift re-check (design D4), immediately before Phase
        # B: nothing here rewrites file BYTES, so `_reject_drifted_targets`'
        # bytes-comparison contract does not apply to the rename targets
        # themselves. Each planned entry is re-validated against current
        # on-disk state; any failure demotes it to a reported skip, never a
        # crash.
        final_renames: list[lint_check.NonNfcEntry] = []
        drift_skips: list[tuple[lint_check.NonNfcEntry, str, str]] = []
        for entry in planned:
            try:
                current_listing = os.listdir(entry.path.parent)  # noqa: PTH208
            except OSError:
                drift_skips.append((entry, "vanished", "vanished"))
                continue
            if entry.raw_name not in current_listing:
                drift_skips.append((entry, "vanished", "vanished"))
                continue
            if entry.nfc_name in current_listing:
                drift_skips.append(
                    (entry, "collision", f"{entry.nfc_name!r} already exists")
                )
                continue
            try:
                drifted_to_symlink = entry.path.is_symlink()
            except OSError:
                drifted_to_symlink = False
            if drifted_to_symlink:
                drift_skips.append((entry, "symlink", "symlink"))
                continue
            final_renames.append(entry)

        if not final_renames:
            typer.echo(
                "openkos normalize-names: every planned rename drifted away "
                "before it could be applied -- nothing was written, no log "
                "entry was appended, and no commit was created."
            )
            return

        all_skips = skips + drift_skips
        pairs = ", ".join(
            f"{entry.rel_posix!r} -> {entry.nfc_name!r}" for entry in final_renames
        )
        skip_kind_counts = Counter(kind for _entry, kind, _reason in all_skips)
        skip_detail = ", ".join(
            f"{kind}: {count}" for kind, count in sorted(skip_kind_counts.items())
        )
        # Design D6's bounded log line: counts always; the renamed pairs are
        # listed inline only for a small batch (<= 5 total entries), so the
        # line stays single (`insert_log_entry` rejects newlines) and never
        # grows unbounded with the batch size (Key Decisions Recorded, b).
        total_entries = len(final_renames) + len(all_skips)
        if total_entries <= 5:
            log_line = (
                f"**Normalize-names**: Renamed {len(final_renames)} on-disk "
                f"name(s) to NFC: {pairs}. Skipped {len(all_skips)}"
                + (f" ({skip_detail})" if all_skips else "")
                + "."
            )
        else:
            log_line = (
                f"**Normalize-names**: Renamed {len(final_renames)} on-disk "
                f"name(s) to NFC. Skipped {len(all_skips)}"
                + (f" ({skip_detail})" if all_skips else "")
                + "."
            )
        try:
            new_log_text = bundle_log.insert_log_entry(
                log_text, datetime.now(UTC).astimezone().date(), log_line
            )
        except (OSError, ValueError) as exc:
            typer.echo(
                f"openkos normalize-names: failed while preparing the "
                f"normalize-names -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc

        landed: list[str] = []
        applied_raw_rels: list[str] = []
        try:
            for entry in final_renames:
                # `entry.path` is the RAW spelling captured at Phase A/scan
                # time; `entry.rel_posix` is already NFC-normalized (design
                # D1), so it names the entry's NEW path, never its old one --
                # using it for `old_rel` would stage the wrong pathspec.
                old_rel = entry.path.relative_to(root).as_posix()
                fsio.rename_two_step(entry.path, entry.nfc_name)
                landed.append(old_rel)
                applied_raw_rels.append(old_rel)
            # New paths are resolved only AFTER the whole batch: the path
            # `rename_two_step` returns names the entry under its ancestors'
            # spellings AT RENAME TIME, and deepest-first means a later
            # ancestor rename carries the entry along, so that momentary
            # spelling goes stale before `_autocommit` ever sees it (review
            # R3-001). The final spelling normalizes exactly the segments
            # whose own rename APPLIED -- never a blanket NFC over the whole
            # path, because an ancestor skipped at drift time (collision)
            # keeps its raw spelling, and its NFC twin names the COLLIDING
            # sibling, not this entry.
            applied = set(applied_raw_rels)

            def _final_rel(raw_rel: str) -> str:
                parts = raw_rel.split("/")
                return "/".join(
                    unicodedata.normalize("NFC", part)
                    if "/".join(parts[: index + 1]) in applied
                    else part
                    for index, part in enumerate(parts)
                )

            # Strictly AFTER the write (issue #495): `landed` doubles as the
            # failure report, which promises OLD paths only, and as
            # `_autocommit`'s staging scope, which needs the final spellings
            # too. Extending before the write let a failure AT the write
            # report both spellings for the same entry.
            fsio.write_atomic(log_path, new_log_text)
            landed.extend(_final_rel(raw_rel) for raw_rel in applied_raw_rels)
            landed.append("bundle/log.md")
        except (OSError, ValueError) as exc:
            landed_suffix = (
                f"Already landed (left renamed, not rolled back): {', '.join(landed)}."
                if landed
                else "No path was written."
            )
            typer.echo(
                f"openkos normalize-names: failed while writing the "
                f"normalize-names -- {exc}. {landed_suffix}",
                err=True,
            )
            raise typer.Exit(code=1) from exc

        typer.echo(
            f"openkos normalize-names: renamed {len(final_renames)} on-disk "
            f"name(s) ({log_path.name} updated): {pairs}."
        )

        _autocommit(root, landed, "openkos: normalize-names")

        # #640: a rename changes concept ids, which every derived store keys on.
    _refresh_derived_after_write(layout, cfg, verb="normalize-names")


@app.command(
    "backfill-source-titles",
    help=(
        "Re-derive the title of every source-type concept from its own "
        "content, across the whole bundle."
    ),
    rich_help_panel="Maintain",
)
@_guard_workspace_lock("backfill-source-titles")
def backfill_source_titles_cmd(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Bundle-wide sweep that re-derives each `type: Source` concept's
    `title` from its immutable `raw/` bytes (design D2/D3), mirroring
    `backfill_sensitivity_cmd`'s Phase A/Phase B shape. No `<concept-id>`
    argument -- bundle-wide only.

    Phase A: snapshot -> `scan_source_titles` (candidates/skipped/warned
    from frontmatter alone) -> read `raw/<name>` per candidate into
    `raw_texts` (an absent key means unreadable, an explicit `None` means
    undecodable -- `UnicodeDecodeError` is caught before the outer
    `except (OSError, ValueError)`, `ingest`'s ordering, since it
    subclasses `ValueError`) -> `resolve_source_title_backfill`.

    Empty `staged` short-circuits: "nothing was staged", no write, no
    commit, exit 0. Otherwise a THREE-bucket preview (staged/skipped/
    warned, unlike `backfill-sensitivity`'s stage-or-nothing preview) is
    printed -- closed by the `index.md`/`log.md` aggregate disclosure with
    the real relabel count (#308) -- then the confirm gate mirrors
    `backfill_sensitivity_cmd`'s precedence exactly: `--auto` /
    `review: false` / TTY `typer.confirm` / non-TTY refuse.

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads `index.md`, every staged Source, and
    `log.md`, and refuses the WHOLE run (exit 3, nothing written) if any
    changed or vanished since Phase A read it (issues #306, #313, #319).

    Phase B writes `index.md` first, then each staged Source, then `log.md`,
    then one `_autocommit` (design D6); both write-bound texts are computed
    before the preview so a malformed `index.md` refuses before any write.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise ValueError(workspace_reason)
        cfg = config.read_config(root)

        bundle_snapshot: dict[str, str] = {}
        bundle_bytes: dict[str, bytes] = {}
        for path in okf.iter_bundle_markdown(layout.bundle_dir):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            bundle_bytes[rel], bundle_snapshot[rel] = _snapshot_read(path)

        scan = source_titles.scan_source_titles(bundle_snapshot)

        raw_texts: dict[str, str | None] = {}
        for candidate in scan.candidates:
            raw_path = layout.raw_dir / PurePosixPath(candidate.resource).name
            try:
                raw_texts[candidate.resource] = raw_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Subclasses `ValueError`: caught before `except (OSError,
                # ValueError)` below, or one binary raw file fails the sweep.
                raw_texts[candidate.resource] = None
            except OSError:
                pass  # absent key -> `raw-unreadable` (design D2)

        backfill = source_titles.resolve_source_title_backfill(scan, raw_texts)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos backfill-source-titles: refusing to run -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    if not backfill.staged:
        typer.echo(
            "openkos backfill-source-titles: nothing was staged -- no Source "
            "has a mechanical title with a differing re-derivation."
        )
        return

    # Both write-bound texts are computed HERE, before the preview, so a
    # malformed `index.md` refuses before any write rather than halfway
    # through Phase B (design D6).
    try:
        # Both originals come out of one `_snapshot_read` observation each:
        # the decoded text is what the rewrites are computed from, and the
        # raw bytes are what `_reject_drifted_targets` compares against
        # below (issues #306, #318).
        index_bytes, index_text = _snapshot_read(index_path)
        new_index_text = index_text
        relabeled_total = 0
        uncataloged: list[str] = []
        for retitle in backfill.staged:
            # The count is BOUND, not discarded (#308): zero matches means
            # this staged Source has no catalog bullet at all, so the
            # `index.md` write below is byte-identical for it -- claiming
            # "catalog updated" would be a lie the operator cannot see.
            new_index_text, relabel_count = bundle_index.relabel_index_entry(
                new_index_text, retitle.concept_id, retitle.new_title
            )
            relabeled_total += relabel_count
            if relabel_count == 0:
                uncataloged.append(retitle.concept_id)

        retitled = ", ".join(
            f"'bundle/{retitle.concept_id}.md' -> {retitle.new_title!r}"
            for retitle in backfill.staged
        )
        log_line = (
            f"**Backfill-source-titles**: Re-derived {len(backfill.staged)} "
            f"Source title(s) from their raw content: {retitled}."
        )
        log_bytes, log_text = _snapshot_read(log_path)
        new_log_text = bundle_log.insert_log_entry(
            log_text,
            datetime.now(UTC).astimezone().date(),
            log_line,
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos backfill-source-titles: failed while preparing the "
            f"backfill-source-titles -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos backfill-source-titles: proposed changes:")
    for retitle in backfill.staged:
        typer.echo(
            f"  ~ bundle/{retitle.concept_id}.md (title: "
            f"{retitle.current_title!r} -> {retitle.new_title!r})"
        )
    for skipped in backfill.skipped:
        typer.echo(f"  = bundle/{skipped.concept_id}.md (skipped: {skipped.reason})")
    for warned in backfill.warned:
        typer.echo(f"  ! bundle/{warned.concept_id}.md (warned: {warned.reason})")
    # Deliberately NOT a `(warned: ...)` line (#308): those carry the closed
    # reason vocabulary of Sources the sweep will NOT touch, while these
    # Sources ARE retitled -- only their catalog bullet is missing, so the
    # relabel changes nothing for them.
    for concept_id in uncataloged:
        typer.echo(f"  ! bundle/{concept_id}.md (no index.md catalog entry to relabel)")
    # Aggregate disclosure (#308): the buckets above name the Sources, but
    # `index.md` and `log.md` are write targets of this run too -- every
    # sibling verb says so before its confirm gate, and the count keeps the
    # `index.md` line honest when some staged Source has no bullet.
    typer.echo(f"  ~ {index_path.name} ({relabeled_total} catalog label(s) relabeled)")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    confirm_enabled = not auto and cfg.review
    prompt_will_run = confirm_enabled and sys.stdin.isatty()

    if confirm_enabled:
        if prompt_will_run:
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos backfill-source-titles: refusing to write without "
                "confirmation -- stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    # Issue #306: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    _reject_drifted_targets(
        layout,
        {
            index_path: index_bytes,
            **{
                layout.bundle_dir / f"{retitle.concept_id}.md": bundle_bytes[
                    f"{retitle.concept_id}.md"
                ]
                for retitle in backfill.staged
            },
            log_path: log_bytes,
        },
        "backfill-source-titles",
    )

    landed: list[str] = []
    try:
        # `index.md` FIRST, then each staged Source, then `log.md` --
        # the OPPOSITE of `backfill-sensitivity`'s items-then-aggregate
        # order (design D6). The classifier keys on a Source document's own
        # `title`; once a document is written, a mid-sweep failure before
        # `index.md` lands would leave its bullet unrevisitable on re-run.
        # Index-first is the order a re-run repairs -- but only for the
        # index-versus-Source pair (#307): the `log.md` stage sits OUTSIDE
        # that guarantee, because once every Source is written, a re-run
        # classifies them all as curated and cannot re-stage the lost
        # entry (hence the dedicated failure report below). Do NOT "fix"
        # this order to match `backfill-sensitivity`.
        fsio.write_atomic(index_path, new_index_text)
        landed.append("bundle/index.md")
        for retitle in backfill.staged:
            source_path = f"bundle/{retitle.concept_id}.md"
            fsio.write_atomic(
                layout.bundle_dir / f"{retitle.concept_id}.md", retitle.content
            )
            landed.append(source_path)
    except (OSError, ValueError) as exc:
        landed_suffix = (
            f"Already written (left partially retitled, not rolled back): "
            f"{', '.join(landed)}."
            if landed
            else "No path was written."
        )
        typer.echo(
            f"openkos backfill-source-titles: failed while writing the "
            f"backfill -- {exc}. {landed_suffix}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    try:
        fsio.write_atomic(log_path, new_log_text)
        landed.append("bundle/log.md")
    except (OSError, ValueError) as exc:
        # #307: a failure HERE is not one more mid-sweep hole -- it is the
        # one the sweep cannot repair. Every retitle landed, so a re-run
        # classifies each Source as curated and short-circuits "nothing was
        # staged": silently clean, no log entry, no commit. The message
        # therefore states exactly what landed, that the dated entry is
        # gone for good, and that git holds the only record -- worded
        # apart from the mid-sweep message above per #234, because a bug
        # report quoting either must identify its phase unambiguously.
        typer.echo(
            f"openkos backfill-source-titles: failed while writing the "
            f"sweep's log entry -- {exc}. Landed and left in place: "
            f"{', '.join(landed)}. The dated log.md entry for this sweep "
            f"was NOT written and a re-run will NOT recreate it -- every "
            f"retitled Source now classifies as curated, so a re-run "
            f"reports nothing staged. Nothing was committed: inspect the "
            f"partial result with `git diff` and commit it manually.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos backfill-source-titles: retitled {len(backfill.staged)} "
        f"Source(s) ({index_path.name}: {relabeled_total} catalog label(s) "
        f"relabeled, {log_path.name} updated): {retitled}."
    )

    _autocommit(root, landed, "openkos: backfill-source-titles")


@app.command(
    "set-volatility",
    help=(
        "Set the freshness window for one kind of concept, recorded in the "
        "workspace config."
    ),
    rich_help_panel="Curate",
)
@_guard_workspace_lock("set-volatility")
def set_volatility_cmd(
    concept_type: str = typer.Argument(
        ..., help="Exact PascalCase REGISTRY type name, e.g. 'Person'."
    ),
    tier: str = typer.Argument(
        ..., help="Volatility tier: one of 'static', 'slow', 'volatile'."
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Write `type_tiers[<ConceptType>] = <tier>` into `openkos.yaml` --
    the write half of `suggest-volatility`'s read-only recommendation
    (freshness-suggest-windows, write-verb #140).

    Vocabulary validation happens FIRST, before any read or write:
    `tier` must exact-match one of `types.VOLATILITY_TIERS`; `concept_type`
    must exact-match, case-sensitive, one of the 10 PascalCase `REGISTRY`
    type names (including `Source`, since `suggest-volatility` can suggest a
    tier for it even though it is not LLM-classifiable). Either failure
    refuses with a clear stderr message and non-zero exit, with zero
    read/write of the workspace.

    The shared `config.require_workspace` gate runs next, then
    `config.read_config` -- both `except (OSError, ValueError)`, matching
    every other write verb's convention. Idempotence is then checked against
    the PARSED `type_tiers` map (design: "Idempotence detected in CLI via
    parsed map, not the core"): if `concept_type` already maps to `tier`
    there, this is a no-op -- a message is printed, exit 0, and NEITHER
    `config.set_type_tier` NOR any write/commit happens. An explicit
    override equal to the type's REGISTRY default is NOT idempotent (it is
    not present in the parsed map), so it still proceeds as a real write.

    `openkos.yaml`'s raw text is read and passed to the pure
    `config.set_type_tier` text-surgery core (comment-safe, no YAML
    round-trip). Any un-editable existing shape (inline flow-mapping,
    multiple headers, non-mapping scalar, tab-indented block, inconsistent
    indent, duplicate entry) makes that core raise `ValueError`, caught here
    and reported as a refusal on stderr, exit 1, `openkos.yaml` left
    byte-identical -- the file is never touched on this path.

    A preview line `<ConceptType>: <old-or-default> -> <new>` is printed
    before the same confirm gate every other mutating verb shares (`--auto`
    skips it; otherwise config `review: false` skips it the same way;
    otherwise a TTY prompts via `typer.confirm` and aborts on decline;
    otherwise, non-TTY with no `--auto`, this refuses to write). Declining
    or refusing leaves `openkos.yaml` untouched.

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads `openkos.yaml` and refuses (exit 3,
    nothing written) if it changed or vanished since the plan was rendered
    from it (issues #313, #319, #335).

    A confirmed write goes through `fsio.write_atomic`, then
    `_autocommit(root, ["openkos.yaml"], ...)` with message `openkos:
    set-volatility <ConceptType> -> <tier>`, mirroring every other mutating
    verb's commit-message convention (`openkos: <verb> ...`).
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)

    valid_types = {ot.name for ot in types.REGISTRY}
    if tier not in types.VOLATILITY_TIERS:
        typer.echo(
            f"openkos set-volatility: refusing to set -- {tier!r} is not a "
            f"valid tier (expected one of {sorted(types.VOLATILITY_TIERS)}).",
            err=True,
        )
        raise typer.Exit(code=1)
    if concept_type not in valid_types:
        typer.echo(
            f"openkos set-volatility: refusing to set -- {concept_type!r} is "
            f"not a known concept type (expected one of {sorted(valid_types)}).",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos set-volatility: refusing to set -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos set-volatility: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if cfg.type_tiers.get(concept_type) == tier:
        typer.echo(
            f"openkos set-volatility: {concept_type!r} already maps to "
            f"{tier!r}; no change made."
        )
        return

    old_tier = cfg.type_tiers.get(
        concept_type, types.TYPE_TO_DEFAULT_VOLATILITY[concept_type]
    )

    try:
        # `read_config` above parsed a separate read, but the plan is
        # computed from `prepare_set_volatility`'s own
        # `fsio.snapshot_read`, so the guard's baseline sits beside it
        # (issues #313, #318, #335).
        prepared = application_lifecycle.prepare_set_volatility(
            layout.config_path, concept_type, tier
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos set-volatility: refusing to set -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("openkos set-volatility: proposed changes:")
    typer.echo(f"  {concept_type}: {old_tier} -> {tier}")

    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm(prepared.confirmation.prompt, abort=True)
        else:
            # #959: the wording comes from the staged request, not a
            # literal here, so an api/mcp adapter driving this gate
            # headlessly reads the same sentence the CLI prints.
            typer.echo(prepared.confirmation.non_tty_refusal, err=True)
            raise typer.Exit(code=1)

    # Issue #335: `new_config_text` is the ENTIRE file, rendered from a
    # pre-prompt read, so an edit landing while the prompt waited --
    # possibly a safety setting like `review:` or `default_sensitivity:` --
    # would be silently reverted by the whole-file write below. Re-validate
    # the one target now -- after the gate, before the write.
    _reject_drifted_targets(
        layout, {layout.config_path: prepared.config_bytes}, "set-volatility"
    )

    try:
        application_lifecycle.set_volatility_core(layout.config_path, prepared)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos set-volatility: failed while writing -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos set-volatility: set {concept_type} -> {tier} in "
        f"{layout.config_path.name}."
    )

    _autocommit(
        root,
        ["openkos.yaml"],
        f"openkos: set-volatility {concept_type} -> {tier}",
    )


@dataclass(frozen=True)
class _SensitivitySkip:
    """A DELIBERATE skip of the reconciliation pass (#1124): `concept_id` is
    confidential and the backend is not local. Distinct from a failure
    reason string so the notice never reads as something having broken."""

    concept_id: str


def _reconcile_merged_survivor(
    root: Path, prepared: "PreparedMerge"
) -> tuple["PreparedMerge", "str | _SensitivitySkip | None"]:
    """Run the #645 reconciliation pass over `prepared`'s merged survivor:
    returns `(updated_prepared, None)` on success, or `(prepared,
    failure_reason)` -- the caller keeps the stacked body and notices.

    Splits the merged body back at the exact `## Merged content (<id>)`
    separator `okf.build_merged_document` wrote, hands both halves to
    `reconcile_merged_body` on the workspace's global chat model
    (`task=None`: reconciliation has no harness, so #508's rule forbids a
    per-task key), and rebuilds the survivor text with the reconciled body
    under the UNCHANGED merged frontmatter. Every failure -- unreadable
    config, transport errors, a reply that failed validation -- is a
    reason string, never an exception: the merge itself must not gain a
    new failure mode from an improvement pass.

    The ledger entry is untouched: `unmerge` restores `survivor_before`/
    `absorbed_snapshot`, so reversibility is identical either way."""
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        return prepared, f"workspace config unreadable ({exc})"

    merged_text = prepared.plan.merged_survivor
    metadata, merged_body = okf.load_frontmatter(merged_text)
    separator = f"{okf.merged_content_heading(prepared.absorbed_canonical)}\n\n"
    if separator not in merged_body:
        return prepared, "stacked heading not found in the merged body"
    survivor_body, absorbed_body = merged_body.split(separator, 1)
    title = str(metadata.get("title") or "") or prepared.survivor_canonical

    try:
        client = _chat_client(cfg)
        # Egress gate (#1124): a merge involving any confidential member
        # never sends its bodies to a backend that is not verifiably local.
        blocker = application_lifecycle.reconcile_sensitivity_blocker(
            prepared,
            local_exemption=_resolve_local_exemption(
                cast(application_backends.HasLocality, client), cfg
            ),
        )
        if blocker is not None:
            return prepared, _SensitivitySkip(blocker)
        reconciled = reconcile_merged_body(
            survivor_title=title,
            survivor_body=survivor_body,
            absorbed_body=absorbed_body,
            llm=client,
        )
    except BackendError as exc:
        return prepared, str(exc)
    if reconciled is None:
        return prepared, "the model reply failed validation"

    if not reconciled.endswith("\n"):
        reconciled += "\n"
    new_text = okf.dump_frontmatter(metadata, reconciled)
    new_plan = dataclasses.replace(prepared.plan, merged_survivor=new_text)
    return dataclasses.replace(prepared, plan=new_plan), None


def _ask_confirmation(prompt: str) -> write_gate.ConfirmationAnswer:
    """The confirmation question the curation write services ask, answered on
    a TTY only: a decline is a no, and without a TTY the question cannot be
    asked (the service then refuses with its own text)."""
    if not sys.stdin.isatty():
        return "unavailable"
    try:
        typer.confirm(prompt, abort=True)
    except typer.Abort:
        return "declined"
    return "proceed"


def _exit_for_write_refusal(exc: write_gate.WriteRefused) -> NoReturn:
    """Render a curation service's typed refusal and map its TYPE to the exit
    contract: Typer's own abort for a declined prompt, 3 for drift (the one
    failure a script may retry, #319), 1 for everything else."""
    if isinstance(exc, write_gate.ConfirmationDeclined):
        raise typer.Abort() from exc
    typer.echo(exc.message, err=True)
    raise typer.Exit(
        code=3 if isinstance(exc, write_gate.DriftDetected) else 1
    ) from exc


class _CliMergeObserver(merge_service.MergeObserver):
    """Renders what `merge_concepts` reports: the plan and the closing lines
    to stdout, in the order the verb has always printed them."""

    def proposed(self, preview: merge_service.MergePreview) -> None:
        prepared = preview.prepared
        survivor_canonical = prepared.survivor_canonical
        absorbed_canonical = prepared.absorbed_canonical
        typer.echo("openkos merge: proposed changes:")
        typer.echo(
            f"  ~ sensitivity: {prepared.sensitivity_before} -> "
            f"{prepared.sensitivity_after}"
        )
        for relation in prepared.dropped_self_loops:
            typer.echo(f"  - drop self-loop: {relation.target} ({relation.type})")
        for relation in prepared.deduped_collisions:
            typer.echo(f"  ~ dedupe collision: {relation.target} ({relation.type})")
        if prepared.stacked_body is not None:
            typer.echo(
                f"  + stack absorbed body: {prepared.stacked_body.absorbed_chars} "
                f"unreconciled char(s) ({prepared.stacked_body.share:.0%} of "
                "merged body -- bodies were appended, not reconciled)"
            )
        if preview.reconcile_planned:
            typer.echo(f"  ~ {_RECONCILE_PLAN_NOTE}")
        for rel in prepared.rewritten_files:
            typer.echo(f"  ~ bundle/{rel} (rewrite inbound link(s) to survivor)")
        for rel in prepared.relation_rewritten_files:
            typer.echo(f"  ~ bundle/{rel} (retarget relation to survivor)")
        for rel in prepared.provenance_rewritten_files:
            typer.echo(f"  ~ bundle/{rel} (retarget provenance to survivor)")
        if prepared.removed >= 1:
            typer.echo(f"  ~ {preview.index_name} (remove entry)")
        typer.echo(f"  ~ {preview.log_name} (new dated entry)")
        status_suffix = ""
        if prepared.status_outcome is not None:
            status_suffix = _status_export_preview_suffix(prepared.status_outcome)
        typer.echo(
            f"  ~ bundle/{survivor_canonical}.md (merged content{status_suffix})"
        )
        typer.echo(f"  - bundle/{absorbed_canonical}.md")
        # #796: `merge` is the command `duplicates` and `adjudicate` BOTH name
        # in their closing hints, and it was the one path #776's cross-source
        # guardrail never reached -- the batch door was locked while the door
        # the tool recommends stayed open. Printed after the plan and before
        # the gate, so it is the last thing read before consenting.
        if preview.cross_source_same_pair:
            typer.echo(_CROSS_SOURCE_WALK_NOTE)
        # #904 inherits #796's lesson verbatim: `merge` is the command the
        # cross-type skip message itself prints, so guarding only the batch
        # would send the operator through an unguarded door with the exact
        # arguments the guard just refused. The label's `member_ids` order is
        # `(survivor, absorbed)` here, so it also states the direction.
        if preview.cross_type_concern is not None:
            typer.echo(_cross_type_walk_note(preview.cross_type_concern))

    def merged(self, summary: merge_service.MergeSummary) -> None:
        typer.echo(
            f"openkos merge: merged 'bundle/{summary.absorbed_canonical}.md' into "
            f"'bundle/{summary.survivor_canonical}.md' "
            f"({summary.index_name}, {summary.log_name} updated)."
        )

    def committed(self, sha: str) -> None:
        _echo_commit_disclosure(sha, prefix="openkos merge: ")


@app.command(
    help=(
        "Fuse two concepts into one, keeping a ledger entry that makes the "
        "merge reversible with unmerge."
    ),
    rich_help_panel="Curate",
)
@_guard_workspace_lock("merge")
def merge(
    survivor_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') that survives the merge.",
    ),
    absorbed_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') absorbed into the survivor.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Bypass the doctor-flagged ledger-integrity refusal (Check B, "
            "post-merge mutation) for the survivor's sidecar. Independent "
            "of --auto -- it never skips the confirmation prompt."
        ),
    ),
    no_reconcile: bool = typer.Option(
        False,
        "--no-reconcile",
        help=(
            "Skip the reconciliation pass (#645): keep the merged body as "
            "the survivor's text with the absorbed text appended under a "
            "'## Merged content' heading, with no model call."
        ),
    ),
    reconcile: bool = typer.Option(
        False,
        "--reconcile",
        help=(
            "Force the reconciliation pass (#645) even below the share "
            "and merged-length thresholds that decide it by default. "
            "Refused together with --no-reconcile."
        ),
    ),
) -> None:
    """Fuse two distinct concept-ids into one: the first DESTRUCTIVE
    entity-resolution write (spec: Merge Fuses Two Distinct Concept-IDs).

    Phase A (pure, no writes) mirrors `forget`'s gate shape exactly: the
    current directory must already be a workspace (the same
    `config.require_workspace` gate `ingest`/`forget`/`status` share), or
    this refuses; both `survivor_id`/`absorbed_id` are resolved via the
    same `_resolve_concept_path` `forget` uses -- rejecting an absolute id,
    any `..` segment, a reserved basename, or a nonexistent concept file,
    all as `ValueError`, all before any read. The two ids MUST resolve to
    DISTINCT concept files, else this refuses too (spec: Same-id or unknown
    id rejected) -- checked right after resolution, before any bundle file
    beyond the two concepts themselves is even read.

    The rest of Phase A builds the entire result in memory:
    `bundle.merge.plan_merge` (U2) computes the merged survivor document --
    body appended (never overwritten), scalar conflicts survivor-wins, list
    fields unioned deduped order-preserving, freshness/timestamp taken from
    whichever side is strictly more recent, and `sensitivity` RECOMPUTED via
    `combine_sensitivity` (never copied, high-water-mark) -- plus the full
    `merged_from` ledger entry (ADR-0002) capturing the pre-merge snapshot
    set `unmerge` needs for round-trip parity.
    `bundle.links.find_inbound_link_rewrites` (U3) then scans every OTHER
    bundle concept file (never the survivor or absorbed file themselves,
    and never `index.md`/`log.md`) for a markdown link resolving to
    `absorbed_id`, recording the rewrite each needs to instead point at
    `survivor_id`; a link inside a fenced code block is never matched.
    `index.md`'s bullet for `absorbed_id` is dropped via the same
    `bundle_index.remove_index_entry` `forget` uses (zero matches is drift,
    not an error); a `log.md` entry describing the merge is built via
    `bundle_log.insert_log_entry`.

    `bundle.merge.plan_merge` moves any outbound `relations:` the absorbed
    object bears onto the survivor -- retargeted, self-loops dropped,
    collisions deduped (spec: Reversible Typed-Relation Rewiring; ADR-0005)
    -- `merge` never refuses or blocks on typed relations.
    `bundle.relations.find_inbound_relation_rewrites` (D3) scans the SAME
    `other_files` whole-bundle snapshot `find_inbound_link_rewrites` already
    captured -- taken BEFORE any write, so both scans see identical
    pre-merge bytes -- for a bundle file OTHER than the survivor/absorbed
    pair whose OWN `relations:` targets `absorbed_id`, recording the
    whole-file snapshot `unmerge` needs to reverse it later (design D1/D3).

    The preview printed before the confirm gate surfaces exactly what a
    reviewer needs to approve a DESTRUCTIVE, hard-to-undo-by-hand write:
    the recomputed sensitivity outcome (`before -> after`), any dropped
    self-loop or deduped collision from the OUTBOUND merge (design D2/
    "Preview"), every OTHER file whose inbound link OR inbound relation
    will be rewritten, the catalog/log updates, the merged survivor file,
    and the absorbed file that will be removed.

    Confirm gate, identical precedence and mechanism to `forget`/`ingest`:
    `--auto` skips the prompt outright; otherwise config `review: false`
    skips it the same way; otherwise, on a TTY, `typer.confirm` asks and
    aborts (exit 1) on decline; otherwise (non-TTY, no `--auto`) this
    refuses to write (exit 1), telling the user to re-run with `--auto`.
    Declining or refusing leaves the bundle completely untouched -- Phase A
    never writes anything.

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every path this run intends to touch
    and refuses the WHOLE run (exit 3, nothing written, nothing removed) if
    any changed or vanished since Phase A read it (issues #313, #319,
    #334).

    That set is `index.md`, `log.md`, every touched third-party file, the
    survivor, AND the absorbed file. The absorbed file is the worst case:
    it is deleted, not overwritten, so an edit landing on it during the
    prompt would be destroyed outright with nothing left to recover from.
    Files the whole-bundle scan read but the plan will neither write nor
    delete are not guard targets -- they feed rewrite detection only.

    Phase B (after confirm) writes, in order: `index.md` then `log.md`
    (`write_atomic`, catalog FIRST, mirroring `forget`'s ordering
    invariant), then applies EVERY OTHER file's inbound-link rewrite AND/OR
    inbound-relation retarget -- a file present in BOTH touches disjoint
    regions (body link vs. frontmatter `relations:`), so applying both to
    the same in-memory text is safe (design D5) -- then the merged
    survivor file (carrying the `merged_from` ledger, now including
    `relation_rewrites`), and finally removes the absorbed file LAST. The
    survivor/ledger is deliberately committed only AFTER every rewrite has
    succeeded: if a rewrite fails partway through, the survivor has NO
    ledger entry yet, so a clean re-run of this same command is never
    refused by `plan_merge`'s "already merged" guard, and the absorbed file
    -- untouched until the very last step -- is still there to retry
    against. Rewriting a file that some earlier, partial attempt already
    migrated to `survivor_id` is a no-op skip, not a failure, so a re-run
    after a partial rewrite failure completes cleanly. Not transactional
    as a whole, matching `forget`'s documented limitation: a failure
    partway through is a benign, git-recoverable partial result, never
    silent corruption. Any failure, Phase A or Phase B, is caught and
    reported on stderr (exit 1), not a raw traceback.

    Residual recovery note: a failure while rewriting inbound links
    (before the survivor/ledger is written) leaves no trace, so a plain
    re-run of `merge` completes it. A failure at or after the
    survivor/ledger write (including a failed absorbed-file removal) has
    already committed the `merged_from` entry, so a re-run is refused by
    `_reject_already_merged`; that narrow window is recoverable only via
    `git` or `unmerge`, same as `forget`'s own non-transactional
    limitation.
    """
    if reconcile and no_reconcile:
        # #803: rejected up front, before any workspace gate or read,
        # matching the shape `adjudicate` uses for its own contradictory
        # flag pairs.
        typer.echo(f"openkos merge: {_RECONCILE_CONFLICT_MESSAGE}", err=True)
        raise typer.Exit(code=2)

    root = Path.cwd()
    ports = merge_service.MergePorts(
        autocommit=lambda root, paths, message: _autocommit(root, paths, message),
        has_reset_point=lambda root: (
            vcs_git.repo_root(root) is not None and vcs_git.has_reset_point(root)
        ),
        apply_reconciliation=lambda root, prepared, policy: _apply_reconciliation(
            root,
            prepared,
            no_reconcile=policy.no_reconcile,
            reconcile=policy.reconcile,
            verb="merge",
        ),
        clock=lambda: datetime.now(UTC),
    )
    try:
        merge_service.merge_concepts(
            root,
            survivor_id,
            absorbed_id,
            merge_service.MergePolicy(
                auto=auto,
                force=force,
                no_reconcile=no_reconcile,
                reconcile=reconcile,
            ),
            ports=ports,
            observer=_CliMergeObserver(),
            confirm=_ask_confirmation,
        )
    except write_gate.WriteRefused as exc:
        _exit_for_write_refusal(exc)

    # #640: `cfg=None` -- `merge` never reads config; the helper reads its
    # own copy inside the vector stage's fail-open envelope.
    _refresh_derived_after_write(config.WorkspaceLayout(root), None, verb="merge")


class _CliUnmergeObserver(unmerge_service.UnmergeObserver):
    """Renders what the unmerge service reports: each step's plan, the
    `--to` plan and its per-step banners, and the closing lines, in the order
    the verb has always printed them."""

    def proposed(self, preview: unmerge_service.UnmergePreview) -> None:
        prepared = preview.prepared
        plan = prepared.plan
        survivor_canonical = preview.survivor_canonical
        absorbed_canonical = preview.absorbed_canonical
        typer.echo("openkos unmerge: proposed changes:")
        for rel in prepared.rewritten_files:
            typer.echo(f"  ~ bundle/{rel} (reverse inbound link rewrite)")
        for rel in prepared.relation_rewrite_files:
            typer.echo(f"  ~ bundle/{rel} (restore pre-merge relations snapshot)")
        for rel in prepared.provenance_rewrite_files:
            typer.echo(f"  ~ bundle/{rel} (restore pre-merge provenance snapshot)")
        if plan.entry.schema == okf.MERGE_LEDGER_SCHEMA_V5:
            typer.echo(f"  ~ {preview.index_name} (restore this merge's catalog entry)")
            typer.echo(
                f"  ~ {preview.log_name} (remove this merge's entry, append unmerge)"
            )
        else:
            typer.echo(f"  ~ {preview.index_name} (restore pre-merge contents)")
            typer.echo(
                f"  ~ {preview.log_name} (restore pre-merge contents, append "
                "unmerge entry)"
            )
        survivor_status_suffix = ""
        if prepared.survivor_status_outcome is not None:
            survivor_status_suffix = _status_export_preview_suffix(
                prepared.survivor_status_outcome
            )
        typer.echo(
            f"  ~ bundle/{survivor_canonical}.md (restore pre-merge contents"
            f"{survivor_status_suffix})"
        )
        absorbed_status_suffix = ""
        if prepared.absorbed_status_outcome is not None:
            absorbed_status_suffix = _status_export_preview_suffix(
                prepared.absorbed_status_outcome
            )
        typer.echo(
            f"  + bundle/{absorbed_canonical}.md (restore{absorbed_status_suffix})"
        )
        if prepared.catalog_log_drifted:
            typer.echo(
                "Warning: index.md/log.md changed since the merge; unmerge "
                "restores the pre-merge snapshot and will discard those changes."
            )
        if prepared.survivor_drift_unverifiable:
            typer.echo(
                f"Warning: {survivor_canonical!r}'s merge ledger entry predates "
                "the survivor-edit check (#1110); cannot confirm its current "
                "bytes still match what the merge wrote, proceeding anyway."
            )
        if prepared.survivor_edits_discarded:
            typer.echo(
                f"Warning: {survivor_canonical!r}'s post-merge edits are being "
                "discarded (--discard-survivor-edits) -- it will be restored to "
                "its pre-merge state, and anything changed on it since the "
                "merge is gone unless you copied it somewhere safe first."
            )

    def restored(self, summary: unmerge_service.UnmergeSummary) -> None:
        typer.echo(
            f"openkos unmerge: restored 'bundle/{summary.absorbed_canonical}.md' "
            f"from 'bundle/{summary.survivor_canonical}.md' "
            f"({summary.index_name}, {summary.log_name} updated)."
        )

    def unwind_planned(self, plan: unmerge_service.UnwindPlan) -> None:
        total = len(plan.steps)
        typer.echo(
            f"openkos unmerge: unwind plan for '{plan.survivor_canonical}' -- "
            f"{total} step{'s' if total != 1 else ''}, newest merge first:"
        )
        for step_number, step in enumerate(plan.steps, start=1):
            typer.echo(f"step {step_number}: restore '{step.absorbed_id}'")
            for line in step.preview_lines:
                typer.echo(line)

    def step_starting(self, step_number: int, total: int, absorbed_id: str) -> None:
        typer.echo(
            f"openkos unmerge: step {step_number} of {total} -- restoring "
            f"'{absorbed_id}'"
        )


_UNMERGE_ARGUMENT_RULE: Final = "exactly one of the two is required"
"""The ONE spelling of `unmerge`'s argument rule (issue #805, item 4).

Naming the survivor does not identify a merge on it, so `unmerge` refuses
without an absorbed id -- and the published one-liner used to read
"Reverse the most recent merge on a concept", which says the opposite.
Shared here so the three PUBLISHED help strings -- the command help, the
positional argument's help, and `--to`'s help -- cannot drift into three
near-synonyms of the same rule.

The command's docstring below and `docs/cli.md` spell the same clause
verbatim, but by hand: neither can interpolate this constant. A docstring
assembled from an f-string is not a literal, so Python stores no `__doc__`
for it at all, and a Markdown file interpolates nothing. Prose is the
right place to accept that duplication -- it is one short clause a reader
sees whole -- but it is duplication, so a change here is a change to make
in those two places too."""


@app.command(
    help=(
        "Reverse a recorded merge on a concept, restoring documents to "
        "their pre-merge state. Name the absorbed id: the positional "
        "<absorbed-id> reverses the survivor's most recent merge, or --to "
        f"<absorbed-id> unwinds the chain down to it -- {_UNMERGE_ARGUMENT_RULE}."
    ),
    rich_help_panel="Curate",
)
@_guard_workspace_lock("unmerge")
def unmerge(
    survivor_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') that survived a prior merge.",
    ),
    absorbed_id: str | None = typer.Argument(
        None,
        help=(
            "Concept id expected to be the LIFO-tail absorbed_id of "
            "survivor's merged_from ledger. This argument or --to: "
            f"{_UNMERGE_ARGUMENT_RULE}, never both."
        ),
    ),
    to: str | None = typer.Option(
        None,
        "--to",
        help=(
            "Unwind the survivor's merge ledger tail-first, one unmerge per "
            "entry, down to and including the entry that absorbed this id -- "
            "one confirm gate for the whole plan. This option or the "
            f"positional absorbed-id: {_UNMERGE_ARGUMENT_RULE}, never both."
        ),
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
    discard_survivor_edits: bool = typer.Option(
        False,
        "--discard-survivor-edits",
        help=(
            "Bypass the survivor-edit refusal (#1110): proceed even though "
            "the survivor's current bytes no longer match what the merge "
            "wrote, discarding that edit. Independent of --auto -- it never "
            "skips the confirmation prompt or any OTHER refusal (the "
            "absorbed-path collision, a rewrite-file's own drift check, or "
            "the post-confirm drift guard)."
        ),
    ),
) -> None:
    """Reverse a recorded `merge` on `survivor_id`, restoring both concept
    files to byte parity with their pre-merge state (spec: Unmerge Achieves
    Round-Trip Parity) -- the reversal `merged_from` (ADR-0002) exists to
    make possible. Two mutually exclusive forms (issue #562), and exactly
    one of the two is required: supplying BOTH the positional
    `absorbed_id` and `--to`, or NEITHER, is a clean exit-1 refusal before
    any other gate.

    The absorbed id stays REQUIRED rather than defaulting to the ledger's
    most recent entry (issue #805, item 4, which offered that as the
    alternative fix). `unmerge` is a destructive restore, and this
    project's canon is "human curates, engine maintains -- consequential
    changes stay reviewable, not silently automatic". An implicit target
    would let `openkos unmerge <survivor> --auto` reverse a merge the
    operator never named, on a survivor whose ledger they may not have
    read. The refusal below is immediate and costs one round trip, which
    is the cheap half of that trade; only the HELP was wrong, and it is
    the help that was fixed.

    `unmerge <survivor-id> <absorbed-id>` is the classic two-arg,
    LIFO-ENFORCED form: it targets ONLY the most-recent unreversed
    `merged_from` entry (the LIFO tail). `absorbed_id` MUST equal that tail
    entry's `absorbed_id`, else this refuses with a clean error and no
    write -- reversing a non-tail entry IN PLACE is unsafe, since a later
    merge's snapshots/rewrites may nest on top of an earlier one's (spec
    scenario: Absorbed-id is not the LIFO tail). That refusal is no longer
    a dead end: when the requested id IS buried deeper in the ledger, the
    error lists the full LIFO unwind sequence (tail down to and including
    the request, in execution order) and names `--to` as the one-command
    alternative (`bundle.merge.plan_unmerge`, issue #562).

    `unmerge <survivor-id> --to <absorbed-id>` unwinds the survivor's
    ledger tail-first, one FULL single-step unmerge per entry, down to AND
    INCLUDING the entry whose `absorbed_id` matches `--to`
    (`bundle.merge.plan_unwind_sequence`). The whole plan -- one block per
    step in execution order, each listing every file that step touches,
    derived from the ledger entries alone -- is previewed BEFORE one
    single confirm gate with the same precedence as the two-arg form:
    `--auto` skips the prompt; otherwise config `review: false` skips it;
    otherwise a TTY prompts ONCE for the whole plan via `typer.confirm`
    and aborts (exit 1) on decline; otherwise (non-TTY, no `--auto`) this
    refuses to write. Execution is a sequential loop over
    `unmerge_service`: each step re-runs the COMPLETE single-step
    machinery -- Phase A recomputed from CURRENT disk state, every
    fail-closed drift/collision check included, then Phase B's writes in
    their documented order, the per-step `**Unmerge**` audit line and the
    sidecar tail pop included -- with `confirmed=True`, so no per-step
    prompt fires. Deliberately NOT a whole-chain in-memory composition:
    every intermediate state after a completed step is a consistent
    bundle, so `plan_unmerge`'s LIFO-tail safety argument holds unchanged
    at each step. If step N fails (Phase A or Phase B), the chain stops
    immediately (exit 1), reporting which step failed and that steps
    1..N-1 completed and left a consistent, git-recoverable bundle --
    completed steps are NOT rolled back. `--to` naming the tail itself
    degenerates to exactly the classic two-arg behavior; `--to` an id
    present nowhere in the ledger, or a survivor with no ledger at all,
    refuses cleanly with no write.

    A `survivor_id` whose concept file does not exist gets a
    reverse-provenance lookup across every ledger sidecar
    (`bundle.ledger.find_absorber`, issue #562): if some other survivor's
    ledger records absorbing it, the "does not exist" refusal names that
    absorber and the exact `openkos unmerge <absorber> <survivor>` command
    to run first -- a chained merge leaves the absorbed ex-survivor's OWN
    sidecar intact on disk, so the nested chain is recoverable and the
    error now says how. Path-safety rejections (an absolute id, a `..`
    segment, a reserved basename) are unchanged and never trigger the
    lookup.

    The single-step machinery itself -- Phase A's gates and fail-closed
    checks, the preview, the confirm gate, the post-confirm drift guard,
    and Phase B's write order -- is documented on
    `application.unmerge_service`, which both forms share.
    """
    root = Path.cwd()

    target_input = absorbed_id if absorbed_id is not None else to
    if target_input is None:
        typer.echo(
            "openkos unmerge: refusing to unmerge -- supply an <absorbed-id> "
            "argument, or --to <absorbed-id> to unwind a chain.",
            err=True,
        )
        raise typer.Exit(code=1)
    if absorbed_id is not None and to is not None:
        typer.echo(
            "openkos unmerge: refusing to unmerge -- supply either an "
            "<absorbed-id> argument or --to <absorbed-id>, not both.",
            err=True,
        )
        raise typer.Exit(code=1)

    policy = unmerge_service.UnmergePolicy(
        auto=auto, discard_survivor_edits=discard_survivor_edits
    )
    ports = unmerge_service.UnmergePorts(
        autocommit=lambda root, paths, message: _autocommit(root, paths, message),
        clock=lambda: datetime.now(UTC),
    )
    observer = _CliUnmergeObserver()
    try:
        if to is None:
            # Classic two-arg path: one single-step unmerge, its own preview
            # and confirm gate included -- byte-identical behavior to the
            # pre-#562 command.
            unmerge_service.unmerge_concept(
                root,
                survivor_id,
                target_input,
                policy,
                ports=ports,
                observer=observer,
                confirm=_ask_confirmation,
            )
        else:
            unmerge_service.unwind_merges(
                root,
                survivor_id,
                target_input,
                policy,
                ports=ports,
                observer=observer,
                confirm=_ask_confirmation,
            )
    except unmerge_service.UnwindStopped as exc:
        # The step already owes its own refusal on stderr (the single-step
        # machinery never lets a raw traceback out); the stop line adds the
        # chain-level accounting the operator needs next.
        if not isinstance(exc.cause, write_gate.ConfirmationDeclined):
            typer.echo(exc.cause.message, err=True)
        typer.echo(exc.message, err=True)
        # The step's own exit code survives the chain wrapper: exit 3 (the
        # post-confirm drift refusal) is the ONE documented exit a script may
        # safely retry, and collapsing it to 1 here would silently revoke that
        # contract mid-chain (review finding, issue #562). A declined prompt
        # has no code and stays the conventional 1.
        raise typer.Exit(
            code=3 if isinstance(exc.cause, write_gate.DriftDetected) else 1
        ) from exc
    except write_gate.WriteRefused as exc:
        _exit_for_write_refusal(exc)

    # #640: once per invocation, after the single step -- or the WHOLE chain --
    # completed. A stopped chain raised above and leaves the stale-index
    # warnings as its safety net.
    _refresh_derived_after_write(config.WorkspaceLayout(root), None, verb="unmerge")


@app.command(
    help=(
        "Record how you resolved a contradiction between two concepts, so "
        "the decision is kept rather than repeated."
    ),
    rich_help_panel="Curate",
)
@_guard_workspace_lock("reconcile", commit_phase=True)
def reconcile(
    id_a: str | None = typer.Argument(
        None,
        help="Bundle-relative concept id (path minus '.md') of one concept in the pair.",
    ),
    id_b: str | None = typer.Argument(
        None,
        help="Bundle-relative concept id (path minus '.md') of the other concept in the pair.",
    ),
    winner: str | None = typer.Option(
        None,
        "--winner",
        help=(
            "Concept id (must resolve to id_a or id_b) that supersedes its "
            "counterpart. Omit for a symmetric 'reconciled_with' reconciliation."
        ),
    ),
    revision: str | None = typer.Option(
        None,
        "--revision",
        help=(
            "Concept id (must resolve to id_a or id_b) that revises (refines) "
            "its counterpart; both remain current. Cannot be combined with "
            "--winner."
        ),
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
    from_findings: bool = typer.Option(
        False,
        "--from-findings",
        help="Walk the persisted open contradiction findings with a per-item "
        "[y/N] consent prompt, writing each accepted pair's symmetric "
        "reconciliation through the same write path -- no ids to transcribe "
        "(#567). Also walks fresh, actionable revision findings from "
        "'openkos revisions': a directed REVERSES/REFINES offers a "
        "supersedes/revises edge held by the later Decision, an undirected "
        "one asks once which Decision is later and which relation type to "
        "record (#1014).",
    ),
) -> None:
    """Record a human's resolution of a contradiction between two concepts:
    the first WRITE verb of the freshness-lint-v1 arc (spec: Reconcile
    Command Specification). No LLM in the write path -- `id_a`/`id_b`/
    `--winner` are plain concept-id arguments; this never invokes
    contradiction detection.

    Phase A (pure, no writes) mirrors `relate`'s gate shape: the current
    directory must already be a workspace (the same `config.require_workspace`
    gate every other write verb shares), or this refuses; `id_a` and `id_b`
    are EACH resolved via the same `_resolve_concept_path` `forget`/`relate`/
    `merge` use -- rejecting an absolute id, any `..` segment, a reserved
    basename, or a nonexistent concept file, all as `ValueError`, all before
    any read. The two ids MUST resolve to DISTINCT concept files, else this
    refuses too (self-pair rejected) -- checked BOTH as canonical strings
    (the literal duplicate, clearer message) and as `samefile` device+inode
    identity (#324), since a case-insensitive filesystem or a symlink can
    make two differing ids denote one file, which the byte-comparing drift
    guard cannot detect. If `--winner <id>` or `--revision <id>` is given,
    it is resolved the same way (`_resolve_pair_member`, shared by both
    flags) and its canonical id MUST equal EXACTLY one of the two pair
    members -- else this refuses (no write, spec: "--winner gamma (not in
    pair {alpha,beta})"; "--revision id not in pair"); the other pair member
    becomes the loser (`--winner`) or the refined counterpart
    (`--revision`). `--winner` and `--revision` are mutually exclusive
    (refuse, exit 1, zero writes) -- a reconciliation is either a reversal
    or a refinement, never both.

    Before building any new edge, `_existing_reconciliation_state` gathers
    the pair's EXISTING reconciliation edges (any `RESOLUTION_RELATION_
    TYPES` member already linking `id_a`/`id_b`, in either direction) and
    classifies them as `"none"`, `"symmetric"`, `"directional"` (with a
    holder), `"revision"` (with a holder), or `"mixed"` (disagreeing edges,
    only reachable by hand-editing). This is compared to what THIS
    invocation requests: if the pair carries NO prior reconciliation, this
    proceeds as a fresh write; if the prior state matches the request
    EXACTLY (same mode, same holder for `--winner`/`--revision`), this
    proceeds to the ordinary idempotent no-op path below; if the prior state
    DIFFERS (a mode switch, e.g. symmetric then `--winner`, an opposite
    `--winner`/`--revision`, or a `"mixed"` prior state), this REFUSES here
    (`ValueError`, exit 1, ZERO writes) rather than adding a second,
    contradictory resolution -- a pair can carry AT MOST ONE reconciliation
    resolution written by `reconcile` (CRITICAL fix: a mode-switch re-run
    used to dedup the new edge only on `(target, type)`, so a DIFFERENT edge
    type was added alongside the stale one, while the anchor-gated note
    below matches on `target` alone and is blind to `role`, so it silently
    kept describing the earlier resolution -- frontmatter and body note went
    out of sync with no way to repair it on a later run).

    The rest of Phase A builds the entire result in memory. With no
    `--winner`/`--revision`, a SYMMETRIC `reconciled_with` edge is added to
    BOTH concepts (each targeting the other, design: "Symmetric edge = one
    outbound edge per side"); with `--winner`, a single DIRECTIONAL
    `supersedes` edge is added on the winner's document only, pointing at
    the loser -- no `superseded_by` back-edge; `supersedes` is LABEL-ONLY,
    this verb never writes `status` or any deprecation field (spec:
    Additive-Only, No Status/Lifecycle Write). With `--revision`, a single
    DIRECTIONAL `revises` edge is added on the refining concept's document
    only, pointing at its counterpart -- no back-edge either, and nothing is
    hidden: `revises` deprecates neither end (ADR-0024). Either edge shape
    dedups on `(target, type)` (`_add_relation_if_absent`), mirroring
    `relate`'s idempotency -- safe now that the refuse-on-conflict gate
    above has already ruled out a mode switch reaching this point. Each side
    then gets
    a `## Reconciliation` body note appended -- unless a hidden `<!--
    okos:reconcile target=<counterpart> ... -->` anchor for that counterpart
    is already present (`_reconcile_anchor_present`), in which case the note
    is skipped (idempotent re-run, spec: Idempotent Re-run). All writes are
    additive: existing body content and relations are preserved verbatim,
    never overwritten. A `log.md` entry is built via
    `bundle_log.insert_log_entry`, in one of three shapes: symmetric-new,
    winner-new, or no-change (when nothing on either side actually changed
    -- a clean re-run).

    Confirm gate, identical precedence and mechanism to `relate`/`merge`/
    `forget`: `--auto` skips the prompt outright; otherwise config
    `review: false` skips it the same way; otherwise, on a TTY,
    `typer.confirm` asks and aborts (exit 1) on decline; otherwise
    (non-TTY, no `--auto`) this refuses to write (exit 1), telling the user
    to re-run with `--auto`. Declining or refusing leaves the bundle
    completely untouched -- Phase A never writes anything.

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every path this run intends to write
    (both concept documents and `log.md`) and refuses the WHOLE run (exit 3,
    nothing written) if any changed or vanished since Phase A read it
    (issues #306, #313, #319). Distinct from the partial result the next paragraph
    describes: nothing is written at all, so there is nothing to complete on
    re-run.

    Phase B (after confirm) writes, in order: `id_a`'s document, then
    `id_b`'s document (both `fsio.write_atomic`, since both already exist),
    then `log.md` -- content before the audit trail, mirroring every other
    write verb's ordering. Not transactional as a whole, matching every
    other write verb's documented limitation: a failure partway through is
    a benign, git-recoverable partial result -- and, since every write here
    is additive, a re-run safely completes whatever landed without
    duplicating it (idempotency above). Any failure, Phase A or Phase B, is
    caught and reported on stderr (exit 1), not a raw traceback.

    Reversibility is git-undo only: no ledger, no `unreconcile` (design:
    "Reversibility = git-undo only -- NO ledger, NO unreconcile"), unlike
    `merge`/`unmerge`'s `merged_from` ledger, which exists only because
    `merge` is lossy; `reconcile` never deletes or overwrites content, so a
    ledger here would be over-engineering.

    Threat matrix: N/A -- no routing, shell, subprocess, VCS/PR automation,
    or process-integration boundary. Write safety is the confirm-gate +
    atomic writes + additive/git-undo, same as every prior write verb.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    if from_findings:
        # `--from-findings` is a whole mode (#567): it takes no ids, no
        # `--winner`, no `--revision` and no `--auto` -- a batch walk gets its
        # consent per item and deliberately has no unattended bulk path.
        try:
            reconcile_service.check_workspace(root)
            reconcile_service.validate_request(
                id_a=id_a,
                id_b=id_b,
                winner=winner,
                revision=revision,
                from_findings=True,
                auto=auto,
            )
        except write_gate.WriteRefused as exc:
            _exit_for_write_refusal(exc)
        _run_reconcile_from_findings(root, layout, log_path)
        return

    try:
        outcome = reconcile_service.reconcile_concepts(
            root,
            id_a,
            id_b,
            winner=winner,
            revision=revision,
            auto=auto,
            ports=_reconcile_ports(),
            observer=_CliReconcileObserver(),
            confirm=_ask_confirmation,
        )
    except write_gate.WriteRefused as exc:
        _exit_for_write_refusal(exc)
    # #655: the last write verb joins #640's contract -- once, end of run,
    # only when a concept document actually changed (the idempotent
    # no-change re-run invalidated nothing).
    if outcome.changed:
        _refresh_derived_after_write(layout, None, verb="reconcile")


def _status_export_preview_suffix(outcome: okf.ExportOutcome) -> str:
    """Preview text naming a deprecated-status export outcome for a
    document a write is about to touch (deprecated-status-export, issue
    #1075, design Decision 5's reconcile/relate/merge sequences). `EXPORT`
    names the status change; `WITHDRAW` names the reverse; `DROP_MARKER`
    says only the stale marker goes; `BLOCKED` states the human value stays
    and the concept is hidden regardless; `UNCHANGED` adds nothing -- this
    write did not change that concept's superseded-ness."""
    if outcome is okf.ExportOutcome.EXPORT:
        return "; status → deprecated"
    if outcome is okf.ExportOutcome.WITHDRAW:
        return "; status → stable"
    if outcome is okf.ExportOutcome.DROP_MARKER:
        return "; stale export marker removed"
    if outcome is okf.ExportOutcome.BLOCKED:
        return "; own status preserved (hidden from retrieval regardless)"
    return ""


class _CliReconcileObserver(reconcile_service.ReconcileObserver):
    """Renders what the reconcile service reports: one pair's plan and its
    closing line, in the order the verb has always printed them."""

    def proposed(self, preview: reconcile_service.ReconcilePreview) -> None:
        pair = preview.pair
        status_suffix_a = ""
        status_suffix_b = ""
        if preview.status_outcome is not None:
            suffix = _status_export_preview_suffix(preview.status_outcome)
            if preview.target_is_a:
                status_suffix_a = suffix
            else:
                status_suffix_b = suffix
        typer.echo("openkos reconcile: proposed changes:")
        if pair.holder_canonical is not None:
            # Directed mode: name the edge itself first, so the reviewer sees
            # the decision (which concept wins or refines) before the per-file
            # detail. `!r` mirrors how every other message here quotes ids.
            typer.echo(
                f"  = {pair.holder_canonical!r} {pair.edge_type} "
                f"{pair.target_canonical!r}"
            )
        typer.echo(
            f"  ~ bundle/{pair.canonical_a}.md (relation "
            f"{'added' if preview.edge_added_a else 'unchanged'}; note "
            f"{'appended' if preview.note_added_a else 'already present'}"
            f"{status_suffix_a})"
        )
        typer.echo(
            f"  ~ bundle/{pair.canonical_b}.md (relation "
            f"{'added' if preview.edge_added_b else 'unchanged'}; note "
            f"{'appended' if preview.note_added_b else 'already present'}"
            f"{status_suffix_b})"
        )
        typer.echo(f"  ~ {preview.log_name} (new dated entry)")

    def written(self, written: reconcile_service.ReconcileWritten) -> None:
        pair = written.pair
        if pair.holder_canonical is None:
            typer.echo(
                "openkos reconcile: recorded a symmetric reconciliation between "
                f"'bundle/{pair.canonical_a}.md' and "
                f"'bundle/{pair.canonical_b}.md' ({written.log_name} updated)."
            )
        elif pair.edge_type == "supersedes":
            typer.echo(
                f"openkos reconcile: recorded '{pair.holder_canonical}' as "
                f"superseding '{pair.target_canonical}'; "
                f"'{pair.target_canonical}' now lists as deprecated "
                f"({written.log_name} updated)."
            )
        else:
            typer.echo(
                f"openkos reconcile: recorded '{pair.holder_canonical}' as "
                f"revising '{pair.target_canonical}'; both remain current "
                f"({written.log_name} updated)."
            )


def _reconcile_ports() -> reconcile_service.ReconcilePorts:
    return reconcile_service.ReconcilePorts(
        autocommit=lambda root, paths, message: _autocommit(root, paths, message),
        snapshot_read=lambda path: _snapshot_read(path),
        clock=lambda: datetime.now(UTC),
        commit_section=lambda: _commit_section_for(Path.cwd())(),
    )


def _record_pair_reconciliation(
    root: Path,
    cfg: config.Config,
    path_a: Path,
    canonical_a: str,
    path_b: Path,
    canonical_b: str,
    holder_canonical: str | None,
    target_canonical: str | None,
    *,
    auto: bool,
    announce_preview: bool = True,
    edge_type: Literal["supersedes", "revises"] = "supersedes",
) -> bool:
    """One pair's reconcile transaction for the `--from-findings` walks: the
    adapter over `reconcile_service.reconcile_pair` (the full Phase A /
    confirm / drift-guard / Phase B contract is documented there), so both
    walks write through the SAME path as the two-id form. Renders the typed
    refusals and raises `typer.Exit` exactly as the two-id form always did:
    exit 1 for a prepare/conflict/write failure, exit 3 for post-consent
    target drift -- the walks catch it to skip a bad pair, re-raising 3.

    Returns whether this run CHANGED a concept document (#655): the
    idempotent no-change re-run writes only the log entry."""
    pair = reconcile_service.PairRequest(
        path_a=path_a,
        canonical_a=canonical_a,
        path_b=path_b,
        canonical_b=canonical_b,
        holder_canonical=holder_canonical,
        target_canonical=target_canonical,
        edge_type=edge_type,
    )
    try:
        return reconcile_service.reconcile_pair(
            root,
            cfg,
            pair,
            auto=auto,
            announce_preview=announce_preview,
            ports=_reconcile_ports(),
            observer=_CliReconcileObserver(),
            confirm=_ask_confirmation,
        ).changed
    except write_gate.WriteRefused as exc:
        _exit_for_write_refusal(exc)


def _ask_later_decision_and_type(a: str, b: str) -> tuple[str, str, str] | None:
    """The combined "which Decision is later, which relation type" prompt
    an UNDIRECTED REVERSES/REFINES finding routes to (design.md Decision 9,
    step 7; #1014 Plan 2) -- there is no separate y/N consent step here,
    unlike the directed case: the human's single keystroke IS the consent,
    since they are choosing the exact edge to write rather than confirming
    one the engine already picked.

    Returns `(holder, target, edge_type)` for one of the four numbered
    choices: `[1]` -> `(b, a, "supersedes")`; `[2]` -> `(b, a, "revises")`;
    `[3]` -> `(a, b, "supersedes")`; `[4]` -> `(a, b, "revises")`. `s` or
    empty input (the prompt's own `Enter = s` default) returns `None` --
    the skip sentinel, writing nothing. Loops on anything else, mirroring
    `_confirm`'s own loop (`curate.py:690-713`), instead of silently
    treating an unrecognized answer as a skip or a choice."""
    prompt_text = (
        f"[1] {b} replaces {a}  [2] {b} adjusts {a}  "
        f"[3] {a} replaces {b}  [4] {a} adjusts {b}  [s] skip (Enter = s)"
    )
    while True:
        answer = typer.prompt(prompt_text, default="s", show_default=False)
        choice = answer.strip().lower()
        if choice in {"s", ""}:
            return None
        if choice == "1":
            return (b, a, "supersedes")
        if choice == "2":
            return (b, a, "revises")
        if choice == "3":
            return (a, b, "supersedes")
        if choice == "4":
            return (a, b, "revises")
        typer.echo(
            f"Unrecognized answer '{answer.strip()}' -- expected 1-4 or s "
            "(Enter = s). Asking again."
        )


def _revision_verdict_from_finding(
    finding: revision_findings_store.RevisionFinding,
) -> RevisionVerdict:
    """Reconstruct a `RevisionVerdict` from a persisted `RevisionFinding` so
    `reconcile --from-findings` (design.md Decision 9, #1014 Plan 2) reads
    `.direction`/`relation_for` off the SAME single authority
    (`pair_direction`, ADR-0025) `revisions_report._view_from_finding`
    already uses to render the `revisions` verb's own report -- never a
    second, independently-typed direction check."""
    date_0 = DecisionDate(
        value=date.fromisoformat(finding.dates[0]) if finding.dates[0] else None,
        state=finding.date_states[0],  # type: ignore[arg-type]
    )
    date_1 = DecisionDate(
        value=date.fromisoformat(finding.dates[1]) if finding.dates[1] else None,
        state=finding.date_states[1],  # type: ignore[arg-type]
    )
    return RevisionVerdict(
        pair_ids=finding.pair_ids,
        verdict=RevisionVerdictValue(finding.verdict),
        confidence=finding.confidence,
        rationale=finding.rationale,
        quotes=finding.quotes,
        dates=(date_0, date_1),
    )


def _run_reconcile_from_findings(
    root: Path, layout: config.WorkspaceLayout, log_path: Path
) -> None:
    """The `reconcile --from-findings` batch walk (#567): read the persisted
    contradiction findings, narrow to the ACTIONABLE set -- open (not
    declined), not stale, typed-edge shape (`merged_absorbed_id is None`;
    a merged-content finding names an absorbed doc that no longer exists as
    a pair member), and a high-confidence CONTRADICTS verdict (the same
    `is_high_confidence_finding` threshold the live display uses) -- then
    prompt per item and write each accepted pair's SYMMETRIC reconciliation
    through `_record_pair_reconciliation`, the exact transaction the two-id form runs.

    Consent is per item and TTY-only, mirroring curate's Identity walk: a
    reconciliation is a semantic judgment, so there is deliberately no
    unattended bulk path. A pair whose transaction refuses (e.g. already
    reconciled differently -- the at-most-one-resolution gate) is counted
    as skipped and the walk continues; post-consent target drift (exit 3)
    still refuses the whole run, exactly as everywhere else in the
    #306/#313/#319 arc.

    A SECOND walk follows (design.md Decision 9, #1014 Plan 2): fresh,
    actionable revision findings (`decision-revision-detection`), read via
    `revisions_service.actionable_revision_findings`. It shares this
    function's `applied`/`skipped`/`declined`/`changed_pairs` counters and
    its final summary/refresh with the contradiction walk above, which
    itself runs first and is otherwise unaffected -- only its own early
    `return` on an empty list became "print the line, then continue" so
    execution can reach the second walk. A DIRECTED finding (known
    direction) is offered as a `[y/N]` consent naming the exact directional
    edge (`supersedes` for REVERSES, `revises` for REFINES) held by the
    LATER Decision; an UNDIRECTED finding (`RevisionVerdict.is_untyped_
    change`) routes instead to `_ask_later_decision_and_type`'s combined
    "which is later, which relation type" prompt, with no separate y/N
    step. Each item's freshness is re-checked immediately (`is_fresh`) --
    an earlier item in EITHER walk may have rewritten one of its
    Decisions -- and a stale finding is skipped without a prompt.
    REAFFIRMS/UNRELATED findings never reach this walk at all
    (`is_actionable_revision`, Phase A leaf, is `False` for both)."""
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos reconcile: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if not sys.stdin.isatty():
        typer.echo(
            "openkos reconcile --from-findings: non-interactive write "
            "consent unavailable -- run `openkos reconcile <id_a> <id_b>` "
            "per pair instead.",
            err=True,
        )
        raise typer.Exit(code=1)

    seen_keys: set[str] = set()
    actionable: list[findings.PersistedFinding] = []
    for finding in application_pending.persisted_findings(layout):
        if (
            finding.stale
            or finding.merged_absorbed_id is not None
            or not is_high_confidence_finding(finding.verdict, finding.confidence)
            or application_pending.is_contradiction_declined(
                layout, finding.pair_ids, finding.merged_absorbed_id
            )
        ):
            continue
        # A pair judged on several runs has several rows; offer it once,
        # under the same identity a decision record is keyed on.
        key = bundle_decisions.decision_key_for(
            finding.pair_ids, finding.merged_absorbed_id
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        actionable.append(finding)

    typer.echo(f"openkos reconcile --from-findings: workspace at {root}")
    typer.echo()

    applied = 0
    changed_pairs = 0
    skipped = 0
    declined: list[str] = []

    if not actionable:
        typer.echo(
            "No open contradiction findings to reconcile. Findings are "
            "recorded by `openkos curate` (Contradictions stage) and "
            "`openkos contradictions`."
        )
    for finding in actionable:
        finding_a, finding_b = finding.pair_ids
        try:
            path_a, canonical_a = application_lifecycle.resolve_concept_path(
                layout.bundle_dir, finding_a
            )
            path_b, canonical_b = application_lifecycle.resolve_concept_path(
                layout.bundle_dir, finding_b
            )
        except (OSError, ValueError) as exc:
            typer.echo(f"  skipping {finding_a} <-> {finding_b} -- {exc}.")
            skipped += 1
            continue

        typer.echo(f"{canonical_a} <-> {canonical_b}")
        typer.echo(
            f"  verdict: {finding.verdict} (confidence: {finding.confidence:.2f})"
        )
        typer.echo(f"  rationale: {finding.rationale}")
        if not curate_module._confirm(
            f"Reconcile {canonical_a} <-> {canonical_b} as symmetric "
            "'reconciled_with'? [y/N]"
        ):
            declined.append(f"{canonical_a} <-> {canonical_b}")
            continue

        try:
            pair_changed = _record_pair_reconciliation(
                root,
                cfg,
                path_a,
                canonical_a,
                path_b,
                canonical_b,
                None,
                None,
                auto=True,
                announce_preview=False,
            )
        except typer.Exit as exc:
            if exc.exit_code == 3:
                raise
            # The transaction already printed its refusal (e.g. the pair is
            # reconciled differently); one bad pair never ends the walk.
            skipped += 1
            continue
        applied += 1
        if pair_changed:
            changed_pairs += 1

    # design.md Decision 9: the second walk, over fresh/actionable revision
    # findings (decision-revision-detection). Shares this function's
    # `applied`/`skipped`/`declined`/`changed_pairs` counters -- the summary
    # and end-of-run refresh below count BOTH walks together.
    revision_actionable = revisions_service.actionable_revision_findings(layout)
    if not revision_actionable:
        typer.echo(
            "No open revision findings to apply. Findings are recorded by "
            "`openkos revisions`."
        )
    for revision_finding in revision_actionable:
        finding_a, finding_b = revision_finding.pair_ids
        # Step 4: re-check freshness immediately before any prompt -- an
        # earlier item in EITHER walk may have rewritten one of this
        # finding's Decisions.
        if not revisions_service.is_fresh(layout, revision_finding):
            typer.echo(
                f"  skipping {finding_a} <-> {finding_b} -- changed since "
                "it was judged."
            )
            skipped += 1
            continue

        try:
            path_a, canonical_a = application_lifecycle.resolve_concept_path(
                layout.bundle_dir, finding_a
            )
            path_b, canonical_b = application_lifecycle.resolve_concept_path(
                layout.bundle_dir, finding_b
            )
        except (OSError, ValueError) as exc:
            typer.echo(f"  skipping {finding_a} <-> {finding_b} -- {exc}.")
            skipped += 1
            continue

        verdict = _revision_verdict_from_finding(revision_finding)
        typer.echo(f"{canonical_a} <-> {canonical_b}")
        typer.echo(
            f"  verdict: {revision_finding.verdict} "
            f"(confidence: {revision_finding.confidence:.2f})"
        )
        typer.echo(
            f"  {canonical_a} ({revision_finding.dates[0] or 'unknown date'}): "
            f"{revision_finding.quotes[0]}"
        )
        typer.echo(
            f"  {canonical_b} ({revision_finding.dates[1] or 'unknown date'}): "
            f"{revision_finding.quotes[1]}"
        )
        typer.echo(f"  rationale: {revision_finding.rationale}")

        edge_type: Literal["supersedes", "revises"]
        holder: str
        target: str
        if verdict.direction.holder is not None:
            # Step 6: direction known -- holder = later, target = earlier,
            # edge_type = `relation_for(verdict)` (never overridable here;
            # a human who disagrees with the detected kind must skip and
            # use `reconcile --winner`/`--revision` by hand).
            later = (
                canonical_a if verdict.direction.holder == finding_a else canonical_b
            )
            earlier = canonical_b if later == canonical_a else canonical_a
            edge_type = cast(Literal["supersedes", "revises"], relation_for(verdict))
            if edge_type == "supersedes":
                prompt_text = (
                    f"Record {later} supersedes {earlier} (reversal; "
                    f"{earlier} is hidden as current)? [y/N]"
                )
            else:
                prompt_text = (
                    f"Record {later} revises {earlier} (refinement; both "
                    "remain current)? [y/N]"
                )
            if not curate_module._confirm(prompt_text):
                declined.append(f"{later} {edge_type} {earlier}")
                continue
            holder, target = later, earlier
        else:
            # Step 7: direction unknown (an untyped change) -- the combined
            # prompt supplies BOTH holder/target and edge_type in one
            # answer; there is no separate y/N step after it.
            choice = _ask_later_decision_and_type(canonical_a, canonical_b)
            if choice is None:
                declined.append(
                    f"{canonical_a} <-> {canonical_b} (revision, order not chosen)"
                )
                continue
            raw_holder, raw_target, raw_edge_type = choice
            holder, target = raw_holder, raw_target
            edge_type = cast(Literal["supersedes", "revises"], raw_edge_type)

        try:
            pair_changed = _record_pair_reconciliation(
                root,
                cfg,
                path_a,
                canonical_a,
                path_b,
                canonical_b,
                holder,
                target,
                auto=True,
                announce_preview=False,
                edge_type=edge_type,
            )
        except typer.Exit as exc:
            if exc.exit_code == 3:
                raise
            # Step 9: the at-most-one-resolution gate is the authority; a
            # pair already resolved differently refuses there, and one bad
            # pair never ends the walk.
            skipped += 1
            continue
        applied += 1
        if pair_changed:
            changed_pairs += 1

    typer.echo()
    typer.echo(
        f"openkos reconcile --from-findings: applied {applied}, "
        f"skipped {skipped}, declined {len(declined)}."
    )
    for item in declined:
        typer.echo(f"  declined: {item}")

    # #655: ONE end-of-run refresh for the whole walk, mirroring `curate`'s
    # own once-per-invocation call -- never per accepted pair -- and only
    # when at least one pair's documents actually changed.
    if changed_pairs:
        _refresh_derived_after_write(layout, cfg, verb="reconcile")


def _bundle_content_lines(survey: okf.BundleSurvey) -> list[tuple[str, int]]:
    """Build `status`'s per-type "Bundle contents" rows from a survey (#133).

    `Sources` first, then `Concepts` ALWAYS (even at 0, preserving the
    familiar summary), then every OTHER classifiable type that is actually
    present, in canonical `_TYPE_TO_SECTION` order using its plural section
    label -- so a Procedure, Decision, etc. gets its own line instead of
    being folded into "Concepts". Any non-Source raw `type` outside the
    classifiable vocabulary (e.g. a malformed lowercase `concept`) is
    surfaced last, sorted, labelled by its raw string, so nothing is hidden.
    """
    by_type = survey.by_type
    lines: list[tuple[str, int]] = [("Sources", survey.sources)]
    for type_name, section in _TYPE_TO_SECTION.items():
        count = by_type.get(type_name, 0)
        if type_name == "Concept" or count > 0:
            lines.append((section, count))
    known = set(_TYPE_TO_SECTION) | {"Source"}
    for type_name in sorted(by_type):
        if type_name not in known:
            lines.append((type_name, by_type[type_name]))
    return lines


@app.command(
    help=(
        "Report what the bundle contains right now: counts by type, recent "
        "activity, and anything needing attention."
    ),
    rich_help_panel="Explore",
)
def status() -> None:
    """Report what the bundle currently contains: read-only, Phase-A only.

    Refuses (exit 1) via the shared `config.require_workspace` gate (D1) if
    the current directory is not an initialized workspace -- the SAME check
    `ingest` uses -- printing the reason to stderr with no raw traceback.
    This is the ONLY non-zero exit path.

    On a workspace, every read is gathered by two `application.status`
    calls, in a deliberate order: `read_status_overview` runs FIRST and
    covers only the cheap, already-guarded reads (`okf.survey_bundle`, the
    lenient-degrading `log.md` read); this command renders the workspace
    header, `Bundle contents:`, and `Recent activity:` from that result
    BEFORE calling `build_status_report`, which performs every remaining
    (unguarded) read. See `application.status`'s module docstring for why
    that ordering is load-bearing, not incidental (issue #995 PR 3, review
    findings R3-partial-output-on-read-failure / R4-status-no-partial-output).
    This command's own job is rendering the two results as plain text via
    `typer.echo` and exit codes -- it takes no flags. Note that the reads
    across both calls perform FIVE independent `bundle/**/*.md` walks, not
    one:
    `okf.survey_bundle` (source/concept counts and §11 findings, D2) --
    counts always reflect the disk scan, never `index.md` alone, so catalog
    drift after an interrupted `ingest` is still visible;
    `lint_check.collect_docs` (dangling-reference AND unextracted-source
    findings, #141/#187 -- both reuse this SAME `docs` list, no extra walk);
    `resolution.find_exact_title_groups` (exact-title candidate groups,
    #186), run UNCONDITIONALLY -- unlike the edge-count line below, it is
    never gated on `vectors_missing`, because it never touches embeddings --
    which is TWO walks by itself, not one: `_iter_eligible`, plus
    `lifecycle.deprecated_concept_ids` under the default
    `include_deprecated=False`; and -- only when `vectors.db` is non-empty --
    `build_graph`'s walk behind the untyped-edge needs-attention line and
    the empty-graph notice (#387).
    Consolidating the remaining walks has no open owner: #195 already
    landed, and what it guaranteed is that `status` calls `build_graph`
    exactly once; #216 landed too, and what it removed was the O(n^2)
    pairwise `near_match_score` pass this line paid for and discarded -- NOT
    either of the two walks above, which are unchanged.
    `log.md` is read and passed through `bundle_log.read_recent_entries` for
    the most recent `application_status.RECENT_ACTIVITY_LIMIT` entries,
    newest-first -- an unreadable or malformed `log.md` degrades to a
    notice (`except (OSError,
    ValueError)`) rather than failing the whole command (D5), because recent
    activity is the one nice-to-have `status` exists to show, not the
    counts or the conformance findings. `survey_bundle`'s findings
    (missing/unparseable frontmatter, unreadable files) are informational:
    their presence never changes the exit code (spec: Needs-Attention via §11
    Conformance).

    No file under the workspace is ever created, modified, or deleted, and
    no `--json` or other structured output mode is offered (spec: Read-Only
    and Human-Readable Only).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos status: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    # Called FIRST, deliberately -- see the docstring above for why.
    # `overview` carries RAW facts; every string below (labels, `_plural`,
    # the command names) is presentation, computed here, never in the
    # service.
    overview = application_status.read_status_overview(layout)

    typer.echo(f"openkos status: workspace at {root}")
    typer.echo()
    typer.echo("Bundle contents:")
    content_lines = _bundle_content_lines(overview.survey)
    label_width = max(len(f"{label}:") for label, _ in content_lines)
    for label, count in content_lines:
        typer.echo(f"  {(label + ':').ljust(label_width)} {count}")
    typer.echo()
    typer.echo("Recent activity:")
    if overview.recent_entries is None:
        typer.echo("  Recent activity unavailable — log.md could not be read/parsed.")
    elif not overview.recent_entries:
        typer.echo("  No activity recorded yet.")
    else:
        for entry in overview.recent_entries:
            typer.echo(f"  {entry.date}  {entry.text}")
    typer.echo()
    # This header goes out BEFORE `build_status_report` (called below),
    # not after -- rendering it after would emit strictly less on the
    # failure path than the pre-extraction body did, one line short of the
    # partial-output regression the docstring above explains (review
    # finding R3-needs-attention-header-lost-on-failure).
    typer.echo("Needs attention:")
    report = application_status.build_status_report(layout)
    for warning in report.warnings:
        _echo_warning(warning)

    needs_attention: list[str] = [*overview.survey.findings]
    needs_attention.extend(
        f"{finding.concept_id}: {finding.detail}" for finding in report.dangling
    )
    needs_attention.extend(
        f"{finding.concept_id}: {finding.detail}" for finding in report.unextracted
    )
    needs_attention.extend(
        f"{finding.concept_id}: {finding.detail}" for finding in report.unjudged
    )
    needs_attention.extend(
        f"{finding.concept_id}: {finding.detail}" for finding in report.unevidenced
    )
    needs_attention.extend(
        f"{finding.concept_id}: {finding.detail}" for finding in report.staging_dropped
    )
    needs_attention.extend(
        f"{finding.concept_id}: [{finding.kind}] {finding.detail}"
        for finding in report.sensitivity_findings
    )
    needs_attention.extend(
        f"{finding.concept_id}: [{finding.kind}] {finding.detail}"
        for finding in report.dangling_provenance
    )
    needs_attention.extend(
        f"{finding.concept_id}: [{finding.kind}] {finding.detail}"
        for finding in report.unbacked_provenance
    )
    # #186: pending duplicate groups are ACTIONABLE -- name `duplicates` as
    # the next step. Exact-title matches only; near-match (LOW) is a
    # deliberate high-recall review queue, not an alert (similarity.py).
    # #797: a group the human ruled distinct is already excluded from
    # `report.exact_title_group_count` -- the service computes the
    # suppression, this line only renders the count.
    if report.exact_title_group_count:
        needs_attention.append(
            f"{report.exact_title_group_count} candidate group"
            f"{_plural(report.exact_title_group_count)} with "
            "identical titles — run `openkos duplicates` to review."
        )
    # #598: open and stale are separate lines because they need different
    # verbs -- `contradictions` reviews an open one, only a recompute
    # clears a stale one -- and because `next` ranks the open ones and
    # excludes the stale ones, which would leave stale work reported by
    # nothing at all.
    if report.open_contradictions:
        needs_attention.append(
            f"{report.open_contradictions} open contradiction"
            f"{_plural(report.open_contradictions)} "
            "— run `openkos contradictions` to review."
        )
    if report.stale_contradictions:
        needs_attention.append(
            f"{report.stale_contradictions} stale contradiction"
            f"{_plural(report.stale_contradictions)} — run `openkos curate` "
            "to recompute."
        )
    # issue #183 (Slice 0): a missing/empty `vectors.db` is genuinely
    # ACTIONABLE (spec: "Needs-Attention Surfaces Missing Vector Index"), so
    # it belongs in `needs_attention` itself -- unlike the empty-graph line
    # below, which stays purely INFORMATIONAL. #386: gated on
    # `has_eligible_docs`: reindexing a bundle with nothing to index is
    # meaningless, and `next` owns naming the real first step
    # (`openkos ingest`) in that state.
    if report.vectors_missing and report.has_eligible_docs:
        needs_attention.append(
            "Dense retrieval and candidate edges unavailable — run "
            "`openkos reindex` (vectors.db missing)."
        )
    # #381: an index older than the bundle is ACTIONABLE in exactly the way
    # the missing-`vectors.db` line above is -- it names the command that
    # fixes it -- so it belongs here rather than among the informational
    # lines.
    if report.stale_indexes:
        needs_attention.append(
            f"Derived indexes are stale ({', '.join(report.stale_indexes)}) — run "
            "`openkos reindex` to refresh retrieval."
        )
    # #387: an UNTYPED concept-to-concept edge is pending curation work, so
    # it earns a needs-attention line that says how many and names the verb
    # that types them (`openkos curate`). A fully-typed edge count is a
    # graph-density metric with no action, which is exactly what this
    # section must not carry -- and `status` has no informational section
    # for derived-graph metrics ("Bundle contents" is pinned to the disk
    # scan), so the fully-typed count is dropped rather than moved.
    # `report.edge_summary` is `None` exactly when `vectors_missing`,
    # mirroring the pre-extraction `if not vectors_missing:` gate.
    if report.edge_summary is not None:
        total, typed = report.edge_summary
        untyped = total - typed
        if untyped:
            needs_attention.append(
                f"{untyped} of {total} concept-to-concept edge(s) untyped — "
                "run `openkos curate` to type them."
            )
    if not needs_attention:
        typer.echo("  Nothing needs attention.")
    else:
        for line in needs_attention:
            typer.echo(f"  {line}")
    # The empty-graph notice stays a separate, purely INFORMATIONAL line
    # (spec: "or an adjacent informational line") -- never appended to
    # `needs_attention`, so a healthy workspace still prints "Nothing needs
    # attention." above.
    if (
        report.edge_summary is not None
        and report.edge_summary[0] == 0
        and not report.asserted_relations
    ):
        typer.echo("  No concept relationships yet.")
    # #593: the duplicate check above counts identical-title groups ONLY.
    # Widening it to `duplicates`' full candidate set was measured and
    # rejected: the pairwise near-match pass costs 5.3s at 400 docs and
    # 24.8s at 1000, which this read-only command must not pay. Disclosure
    # is what keeps a near-match-only backlog from reading as an empty one
    # -- printed as an adjacent informational line (never inside
    # `needs_attention`, so a healthy workspace keeps its "Nothing needs
    # attention."), and only when the identical-titles line did NOT fire,
    # which already names the same verb.
    if not report.exact_title_group_count:
        typer.echo(
            "  Similar-title candidates are not counted here — run "
            "`openkos duplicates` for the full scan."
        )


@app.command(
    "next",
    help=(
        "Print the single command worth running next, chosen from the "
        "bundle's current state. Read-only and deterministic."
    ),
    rich_help_panel="Get started",
)
def next_cmd() -> None:
    """Print the one command worth running next: read-only, deterministic.

    Refuses (exit 1) via the SAME shared `config.require_workspace` gate
    (D1) `status` uses if the current directory is not an initialized
    workspace, printing the reason to stderr with no raw traceback. This is
    the ONLY non-zero exit path -- every other workspace state, including a
    freshly initialized, empty bundle, exits 0.

    Delegates the whole ranked decision to
    `openkos.application.next_action.next_action` (the ordered `_TIERS` tuple over
    a lazily-memoized `BundleSignals` holder) and echoes
    `next_action.render_lines`'s output verbatim. `status`'s body is not
    read or touched here (design D2): every signal `next` reads comes from
    a function `status`/`lint` already ship, so no walk logic is
    duplicated. Named `next_cmd` internally only because `next` shadows a
    Python builtin -- the command itself is registered as `next`.

    No file under the workspace is ever created, modified, or deleted, no
    model backend is ever constructed, and no `--json` or other structured
    output mode is offered (spec: Read-Only and Human-Readable Only, No
    Model Backend Constructed).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos next: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    result = next_action_module.next_action(layout)
    for warning in result.warnings:
        _echo_warning(warning)
    for line in next_action_module.render_lines(result):
        typer.echo(line)


def _run_list_sources(layout: config.WorkspaceLayout, object_id: str) -> None:
    """The `list --sources <id>` reverse-provenance mode (#628): resolve
    the id through the same gate every id-taking verb uses, then read
    `application_list.list_provenance_sources`'s raw result and render one
    row per reaching Source -- `ID  SENSITIVITY  TITLE`, `ljust`-aligned
    like the ordinary listing, sensitivity front and center because 'which
    of these still needs raising' is the question this mode answers. A
    `sources/` provenance entry with no file behind it renders as `(not in
    bundle)` rather than vanishing. Read-only, exactly like the ordinary
    listing.

    Id resolution and its `except (OSError, ValueError)` stay HERE,
    unchanged, rather than moving into the application service
    (`application_list.list_provenance_sources`'s own docstring explains
    why): the `try` below wraps ONLY `resolve_concept_path`, exactly as it
    did before this extraction, so a failure in the provenance walk that
    follows is never mislabelled as a bad concept id.

    #1002 item D, ADR-0022: a bundle document the service could not read
    while walking for provenance edges (`result.not_run`) used to vanish
    silently -- "no Source reaches this object" and "the document that
    proves one does could not be read" rendered identically. Now the
    count and each unreadable document's path are printed, and the run
    exits `2` (the same incomplete-report exit `doctor`/`lint` give an
    incomplete report), same as those two verbs -- but ONLY when
    `not_run` is non-empty: a clean bundle prints no incompleteness line
    and exits `0`, exactly as before this change."""
    try:
        _, canonical_id = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, object_id
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos list: refusing to list -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    result = application_list.list_provenance_sources(layout, canonical_id)

    if result.not_run:
        typer.echo(
            f"{len(result.not_run)} document(s) could not be read while "
            "walking for provenance -- this report may be incomplete:"
        )
        for not_run in result.not_run:
            typer.echo(f"  {not_run.label}: {not_run.reason}")

    if not result.ancestors:
        typer.echo(f"No Source reaches '{canonical_id}' through provenance.")
    else:
        rows_by_id = {row.concept_id: row for row in result.rows}
        typer.echo(f"Sources whose provenance reaches '{canonical_id}':")
        id_w = max(len("ID"), *(len(ancestor) for ancestor in result.ancestors))
        sens_w = max(
            len("SENSITIVITY"),
            *(
                len(rows_by_id[a].sensitivity) if a in rows_by_id else 0
                for a in result.ancestors
            ),
        )
        typer.echo(f"{'ID'.ljust(id_w)}  {'SENSITIVITY'.ljust(sens_w)}  TITLE")
        for ancestor in result.ancestors:
            row = rows_by_id.get(ancestor)
            if row is None:
                typer.echo(f"{ancestor.ljust(id_w)}  (not in bundle)")
                continue
            title = row.title or ("(unreadable)" if not row.readable else "(untitled)")
            typer.echo(
                f"{ancestor.ljust(id_w)}  {row.sensitivity.ljust(sens_w)}  {title}"
            )

    # Exit rule (ADR-0022, design.md Decision 4, Non-Gating Exit Contract,
    # same posture as `lint`/`doctor`): an incomplete read is the only
    # thing that gates here -- which Sources were found does not.
    if result.not_run:
        raise typer.Exit(code=2)


@app.command(
    "list",
    help=(
        "List bundle objects with their id, type, sensitivity and lifecycle "
        "status. Optionally filtered to one type."
    ),
    rich_help_panel="Explore",
)
def list_objects_cmd(
    concept_type: str | None = typer.Argument(
        None,
        help=(
            "Optional type filter: a canonical link_dir (e.g. 'people') or a "
            "REGISTRY.name alias (e.g. 'Person', case-sensitive). Omit to "
            "list every object."
        ),
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        help="Print at most this many rows (must be positive unless --all is given).",
    ),
    all_objects: bool = typer.Option(
        False,
        "--all",
        help="Print every matching row, ignoring --limit, with no truncation footer.",
    ),
    sources_of: str | None = typer.Option(
        None,
        "--sources",
        help="Reverse-provenance lookup (#628): list every Source whose "
        "provenance chain reaches this concept id -- transitively, with "
        "each Source's current sensitivity -- so 'protect this object' "
        "finds the Sources that need raising. A whole mode: takes no TYPE "
        "filter.",
    ),
) -> None:
    """List every bundle object's id, sensitivity, lifecycle status, and
    title -- the read-only discovery counterpart to the id-taking write
    verbs (`forget`, `relate`, `merge`, `unmerge`, `set-sensitivity`)
    (issue #184, `openspec/changes/discover-concept-ids/`).

    **Exit ladder, in this exact order (spec: Workspace Presence Check).**
    An unrecognized `TYPE` filter or an out-of-range `--limit` refuses
    (exit 1) BEFORE any workspace or disk access is attempted -- mirroring
    `set-volatility`'s vocabulary-then-workspace precedent
    (`cli/main.py` `set_volatility_cmd`). Only after both usage checks pass
    does `config.require_workspace` run; its failure is the only remaining
    non-zero path. Once past both refusals, no bundle content -- however
    malformed -- can make `list` fail.

    `TYPE` resolves via `listing.resolve_link_dir`: a canonical `link_dir`
    exact match first, then a case-sensitive `REGISTRY.name` alias. An
    unresolved value refuses with a message enumerating only canonical
    `link_dir` names, never the `REGISTRY.name` aliases (spec: Type Filter
    Vocabulary).

    **Read core extracted (issue #995, PR 5).** The three checks above,
    plus the `--sources`+TYPE conflict, are pure argument validation --
    no workspace or disk access -- and now live in `application_list.
    validate_list_arguments`, raising a typed `ListUsageError` subclass
    this adapter catches and renders as the same `typer.echo` + `typer.Exit`
    pair it always has (a headless adapter gets the typed exception
    instead). The workspace gate right below stays HERE, unchanged, for
    the same reason `query` and `status` keep theirs adapter-side: it is a
    fact about the CURRENT PROCESS, not about the arguments. See
    `application/list_service.py`'s module docstring for the full account
    of where every exit went, including `_run_list_sources`'s own.

    **Exactly one bundle walk.** `application_list.list_bundle_objects`'s
    single call to `listing.list_objects(layout.bundle_dir)` is the ONLY
    disk-reading call this command makes -- filtering by resolved
    `link_dir` and slicing to the limit both happen on its in-memory
    result. `lifecycle.deprecated_concept_ids` is never called: status is
    already derived inside `listing.list_objects`'s own single pass
    (spec: Exactly One Bundle Walk; design D3).

    **No partial-output property.** Read top to bottom before this
    extraction: every `typer.echo` in this command ran AFTER the single
    read above returned, so -- unlike `status` -- there is nothing to
    preserve by splitting the read into an early cheap half and a later
    unguarded half; one service call per mode is the faithful shape (see
    `application/list_service.py`'s module docstring for the line-by-line
    evidence).

    Rows are `ID  TYPE  SENSITIVITY  STATUS  TITLE`, `ljust`-aligned over
    the header labels and the rows actually shown (post-filter,
    post-truncation) -- the same pattern `status`'s bundle-contents
    section uses (its `label_width` block over `_bundle_content_lines`,
    design D6; cited by symbol, not by line number, because the line
    range this docstring used to name had drifted into an unrelated
    function). `TYPE` sits beside
    `ID` because both answer "what am I looking at" (#399): without it two
    objects of different kinds with the same title print identically, and
    the only discriminator is the directory prefix buried inside the id.
    It is rendered from `listing.LINK_DIR_TO_TYPE_NAME` over the
    structurally derived `link_dir`, never by re-reading a document's
    `type` field, and falls back to `(unknown)` for an object living
    outside the registry's directories. A title is rendered
    `(unreadable)` when the underlying document failed to read/parse, or
    `(untitled)` when it read fine but declared no title -- two distinct
    markers for two distinct follow-ups. Deprecated and superseded objects
    are shown by default, marked via `STATUS`, with no flag to hide them
    (spec: Deprecated and Superseded Visibility).

    **Confidential titles print in full.** `sensitivity` is a column, not a
    gate: there is no redaction, no flag, and no omitted row based on
    sensitivity level -- output is byte-identical in shape regardless of
    it (spec: Confidential Titles Are Printed in Full). `sensitivity`
    governs what LEAVES the machine via `--include-confidential`
    (`sensitivity.py:78-99`), an LLM-send gate this command never touches
    -- `list` performs no LLM send at all.

    Default `--limit` is 50; a truncated result prints a footer reporting
    how many rows were shown out of the total match count. `--all` prints
    every matching row with no footer. `--limit 0` or any negative
    `--limit` is a usage refusal, not a bundle result (spec: Output
    Bounding). An empty bundle, or a filter matching nothing, prints a
    friendly empty-state line and exits 0 (spec: Empty Bundle and
    Unparseable Document Handling).

    Read-only: no file under the workspace is ever created, modified, or
    deleted, and no `--json` or other structured output mode is offered
    (spec: Read-Only, No Structured Output; deferred, not banned -- the
    deferral was recorded in the list-verb work, #184, and no issue tracks
    it yet).
    """
    try:
        resolved_type = application_list.validate_list_arguments(
            concept_type=concept_type, sources_of=sources_of, limit=limit
        )
    except application_list.SourcesModeTakesTypeFilter as exc:
        # #628: `--sources` is a whole mode -- a TYPE filter alongside it
        # has nothing to filter, so it refuses in the same usage-first
        # ladder slot the unknown-type refusal occupies. This adapter keeps
        # its own hardcoded wording rather than `str(exc)` (unchanged
        # behaviour); `exc` is now chained (issue #995 PR 6 review:
        # R2-usage-error-family-is-not-uniform) so the exception carries a
        # real message for any caller/traceback that inspects it, matching
        # its two siblings below.
        typer.echo(
            "openkos list: refusing to list -- --sources takes no TYPE "
            "filter; it lists the Sources reaching one object.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except application_list.UnknownTypeFilter as exc:
        typer.echo(
            f"openkos list: refusing to list -- {exc.concept_type!r} is not a "
            f"known object type (expected one of {list(exc.valid_link_dirs)}).",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except application_list.NonPositiveLimit as exc:
        typer.echo(
            f"openkos list: refusing to list -- --limit must be positive "
            f"(got {exc.limit}); use --all to print every row instead.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos list: refusing to list -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)

    if sources_of is not None:
        _run_list_sources(layout, sources_of)
        return

    result = application_list.list_bundle_objects(
        layout, resolved_type=resolved_type, limit=limit, all_objects=all_objects
    )

    if not result.rows:
        typer.echo("No objects found.")
        return

    shown = result.shown

    type_names = {
        row.concept_id: listing.LINK_DIR_TO_TYPE_NAME.get(row.link_dir, "(unknown)")
        for row in shown
    }

    id_w = max(len("ID"), *(len(row.concept_id) for row in shown))
    type_w = max(len("TYPE"), *(len(name) for name in type_names.values()))
    sens_w = max(len("SENSITIVITY"), *(len(row.sensitivity) for row in shown))
    stat_w = max(len("STATUS"), *(len(row.status) for row in shown))

    typer.echo(
        f"{'ID'.ljust(id_w)}  {'TYPE'.ljust(type_w)}  "
        f"{'SENSITIVITY'.ljust(sens_w)}  {'STATUS'.ljust(stat_w)}  TITLE"
    )
    for row in shown:
        title = row.title or ("(unreadable)" if not row.readable else "(untitled)")
        typer.echo(
            f"{row.concept_id.ljust(id_w)}  {type_names[row.concept_id].ljust(type_w)}  "
            f"{row.sensitivity.ljust(sens_w)}  "
            f"{row.status.ljust(stat_w)}  {title}"
        )

    if not all_objects and result.total > len(shown):
        typer.echo(
            f"Showing {len(shown)} of {result.total} — use --all to see the rest."
        )


@app.command(
    help=(
        "Health-check the bundle's contents: stale stamps, orphan pages, "
        "malformed names and other findings you may want to act on."
    ),
    rich_help_panel="Explore",
)
def lint() -> None:
    """Health-check the bundle for stale stamps and orphan pages: read-only, Phase-A only.

    The SECOND read command, mirroring `status`'s shape exactly: no Phase B,
    no confirm gate, no `--auto`. Refuses (exit 1) via the shared
    `config.require_workspace` gate (D1) if the current directory is not an
    initialized workspace -- the SAME check `ingest`/`status` use -- printing
    the reason to stderr with no raw traceback. A permission-denied
    `bundle/index.md` that passes `require_workspace`'s `is_file()` check but
    fails to `read_text()` is the only OTHER non-zero path: caught here and
    reported the same way, never left to raise a raw traceback.

    On a workspace, every read and check is gathered by ONE
    `application.lint.build_lint_report` call (issue #995, PR 4): unlike
    `status`, `lint`'s pre-extraction body computed everything before its
    first `typer.echo`, so there is no partial-output property to preserve
    here and no need to split the service into two calls -- see that
    module's docstring for the evidence. The service raises
    `LintInputUnavailable` for its three INPUT reads (`read_config`, the
    `bundle/index.md` read, `collect_docs`) and lets every later check
    propagate uncaught; this command catches that ONE type, which is its
    ONE additional non-zero exit path (the "failed while reading the
    workspace" message below). An `OSError` from a later check -- the
    name-only walks behind `check_non_nfc_names`,
    `check_state_dir_contains_no_markdown` and `check_dot_dir_markdown`
    each walk the tree themselves -- is NOT caught here, exactly as it was
    not caught before the extraction.

    Inside that call: `read_config(root)`'s `freshness_window` and
    `volatility_windows` are resolved together via `lint.resolve_windows`
    (freshness-lint-v1, Q4) into one `lint.VolatilityWindows` -- an
    invalid/zero/negative/non-mapping value, for any tier, never raises; it
    falls back to the packaged default and prints a fallback-notice line
    instead. `today` is computed ONCE via `datetime.now(UTC).date()` and
    injected into `lint.check_stale_stamps` (the clock is never read inside
    `lint.py` itself, keeping every scan deterministic and testable).
    `lint.collect_docs` reuses `okf._iter_docs` for the single walk,
    returning `(docs, skip_notices)` so a skipped file never silently
    shrinks the scan; `lint.check_stale_stamps` scans inline
    `(as of YYYY-MM-DD)` body stamps (never the `freshness` field),
    resolving each doc's own stale window via `lint.window_for_doc`'s
    per-concept-override -> per-type-default -> global-fallback precedence
    (a `static`-tier doc, by override or type default, is never flagged);
    `lint.check_orphans` scans markdown links from `index.md` and every
    doc body (never `log.md` -- see its docstring for why).

    The volatility-window and skip notices feed one `lint.LintReport`, rendered
    under `Stale stamps:`, `Orphan pages:`, `Dangling references:`,
    `Dangling provenance:` (issue #257:
    `lint_check.check_dangling_provenance`, reusing this SAME `docs` list --
    no new walk), `Unextracted sources:` (issue #187:
    `lint_check.check_unextracted`, reusing this SAME `docs` list -- no new
    walk), `Below-source sensitivity:`, `Multi-source uncovered:`
    (issue #231, PR2: `lint_check.check_below_source_sensitivity`, reusing
    this SAME `docs` list too -- design D3's no-fifth-walk guard), and
    `Unbacked provenance:` (issue #421:
    `lint_check.check_unbacked_provenance`, this SAME `docs` list again --
    an engine-owned `relations:` type, `derived_from`, naming a target the
    document's own `provenance:` never records), and `Non-NFC names:`
    (issue #474: `lint_check.check_non_nfc_names` -- a NAMES-ONLY
    incremental `rglob` walk over `layout.bundle_dir` that never opens a
    file, which is why it is NOT a violation of design D3's no-fifth-walk
    guard: that guard protects the read+parse walk, and this one must see
    what `collect_docs` cannot -- a decomposed name on a directory, a
    non-`.md` file, or an unreadable doc. Rendered via `finding.path`,
    never `.concept_id`, because the finding names an on-disk entry, not
    a concept object. `lint` stays read-only and never renames; the
    detail points at `openkos normalize-names`, the dedicated verb that
    does (#474 part 2)), each with its own empty-state line when there is
    nothing to report. Every
    successful read exits 0, whether the bundle is clean or
    has findings (spec: Non-Gating Exit Contract) -- `lint` is NOT a CI
    gate in MVP-1. No file under the workspace is ever created, modified,
    or deleted, and no `--json` or other structured output mode is offered
    (spec: Read-Only and Human-Readable Only).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos lint: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    # Catches `LintInputUnavailable` and NOTHING else: the service raises
    # it for its three input reads (`read_config`, `index.md`,
    # `collect_docs`) and lets every later check propagate uncaught, which
    # is what the pre-extraction body did. Do NOT widen this to
    # `except (OSError, ValueError)` -- see the `except` block below.
    try:
        report = application_lint.build_lint_report(layout)
    except application_lint.LintInputUnavailable as exc:
        # Exactly the three workspace reads the pre-extraction body guarded,
        # and nothing else. A failure from any later check propagates
        # uncaught, as it did before -- see `application.lint`'s module
        # docstring for why widening this to the whole call was tried and
        # reverted (it named an in-memory `ValueError` a read failure).
        typer.echo(
            f"openkos lint: failed while reading the workspace -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo(f"openkos lint: workspace at {root}")
    for notice_line in report.notices:
        typer.echo(notice_line)
    typer.echo()
    # Checks that did not run (design.md Decision 5, ADR-0022): rendered
    # FIRST -- after notices, before the findings sections below -- so a
    # partial report announces itself before its content, not after. Only
    # the three late name walks (L1/L2/L3) can land here; the eleven other
    # already-computed finding sections below always render regardless.
    typer.echo("Checks that did not run:")
    if not report.not_run:
        typer.echo("  No checks failed to run.")
    else:
        for not_run_check in report.not_run:
            typer.echo(f"  {not_run_check.label}: {not_run_check.reason}")
    typer.echo()
    typer.echo("Stale stamps:")
    if not report.stale:
        typer.echo("  No stale stamps.")
    else:
        for finding in report.stale:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Orphan pages:")
    if not report.orphans:
        typer.echo("  No orphan pages.")
    else:
        for finding in report.orphans:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Dangling references:")
    if not report.dangling:
        typer.echo("  No dangling references.")
    else:
        for finding in report.dangling:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Dangling provenance:")
    if not report.dangling_provenance:
        typer.echo("  No dangling provenance findings.")
    else:
        for finding in report.dangling_provenance:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Unextracted sources:")
    if not report.unextracted:
        typer.echo("  No unextracted sources.")
    else:
        for finding in report.unextracted:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Unjudged extractions:")
    if not report.unjudged:
        typer.echo("  No unjudged extractions.")
    else:
        for finding in report.unjudged:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    # #801: its OWN section, immediately after the one it is most likely to
    # be confused with. The judge tokens mean no quality selection ran over
    # the set; this means some object the run stored quotes nothing from
    # its source. Different question, different repair -- and one shared
    # heading would leave the reader unable to tell which they have.
    typer.echo("Unevidenced objects:")
    if not report.unevidenced:
        typer.echo("  No unevidenced objects.")
    else:
        for finding in report.unevidenced:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    # #843: its OWN section, beside the two other extraction_notice
    # readers. The judge tokens mean the stored set skipped selection,
    # #801's means a stored object quotes nothing, this one means content
    # extraction produced was never stored at all -- three questions,
    # three repairs, three headings.
    typer.echo("Staging-dropped candidates:")
    if not report.staging_dropped:
        typer.echo("  No staging-dropped candidates.")
    else:
        for finding in report.staging_dropped:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Below-source sensitivity:")
    if not report.below_source:
        typer.echo("  No below-source sensitivity findings.")
    else:
        for finding in report.below_source:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Multi-source uncovered:")
    if not report.multi_source_uncovered:
        typer.echo("  No multi-source uncovered findings.")
    else:
        for finding in report.multi_source_uncovered:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Unbacked provenance:")
    if not report.unbacked_provenance:
        typer.echo("  No unbacked provenance claims.")
    else:
        for finding in report.unbacked_provenance:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Non-NFC names:")
    if not report.non_nfc:
        typer.echo("  No non-NFC on-disk names.")
    else:
        # #474: `finding.path`, never `.concept_id` -- this kind names an
        # on-disk entry (possibly a directory or non-`.md` file), not a
        # concept object, so the path is the honest spelling.
        for finding in report.non_nfc:
            typer.echo(f"  {finding.path}: {finding.detail}")
    typer.echo()
    typer.echo("State-dir markdown:")
    if not report.state_dir_markdown:
        typer.echo("  No `.md` files under bundle/.state/.")
    else:
        for finding in report.state_dir_markdown:
            typer.echo(f"  {finding.path}: {finding.detail}")
    typer.echo()
    typer.echo("Dot-directory markdown:")
    if not report.dot_dir_markdown:
        typer.echo("  No `.md` files under a dot-directory.")
    else:
        for finding in report.dot_dir_markdown:
            typer.echo(f"  {finding.path}: {finding.detail}")
    typer.echo()
    typer.echo("Symlinked markdown:")
    if not report.symlinked_markdown:
        typer.echo("  No symlinked `.md` files or directories under bundle/.")
    else:
        for finding in report.symlinked_markdown:
            typer.echo(f"  {finding.path}: {finding.detail}")
    typer.echo()
    typer.echo("Deprecated-status exports:")
    if not report.status_export:
        typer.echo("  No deprecated-status export findings.")
    else:
        for finding in report.status_export:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")

    # Completed/not-run counts (design.md Decision 5, ADR-0022): against
    # `application_lint.TOTAL_CHECKS` (15 calls), NOT the 16 `LintReport`
    # finding-list fields -- `check_below_source_sensitivity` is one call
    # feeding two fields, so counting fields would overstate how many
    # checks ran.
    typer.echo(
        f"{application_lint.TOTAL_CHECKS - len(report.not_run)} check(s) "
        f"completed, {len(report.not_run)} did not run."
    )

    # Exit rule (design.md Decision 4, ADR-0022, Non-Gating Exit Contract):
    # findings alone NEVER gate -- only an incomplete run does. A `not_run`
    # entry means at least one late walk (L1/L2/L3) could not run, so the
    # report the operator is reading may be missing signal `lint` would
    # otherwise have surfaced.
    if report.not_run:
        raise typer.Exit(code=2)


@app.command(
    help=(
        "Report concepts from different sources that look like duplicates, "
        "without judging or changing anything."
    ),
    rich_help_panel="Explore",
)
@_guard_workspace_lock("duplicates")
def duplicates(
    include_deprecated: bool = typer.Option(
        False,
        "--include-deprecated",
        help="Include deprecated and superseded concepts (excluded by default).",
    ),
    keep_distinct: list[str] | None = typer.Option(
        None,
        "--keep-distinct",
        help=(
            "Record that these concepts are NOT the same entity and stop "
            "offering to merge them (#797). Repeat the flag once per member, "
            "at least twice. Reversible with --reopen; listed by "
            "--kept-distinct."
        ),
    ),
    reopen: list[str] | None = typer.Option(
        None,
        "--reopen",
        help=(
            "Undo a --keep-distinct ruling for these concepts, so the group "
            "is offered for review again. Repeat once per member."
        ),
    ),
    kept_distinct: bool = typer.Option(
        False,
        "--kept-distinct",
        help="List every group a human has ruled distinct, and exit.",
    ),
) -> None:
    """Report cross-source candidate duplicates.

    The REPORT is read-only and Phase-A only. Since #797 the command also
    exposes three decision verbs that DO write -- under `bundle/.state/`
    only, never to a concept file -- so "read-only" describes the report,
    not every flag.

    `--keep-distinct`/`--reopen`/`--kept-distinct` (#797) are the three
    write/list verbs this command additionally exposes, mirroring
    `contradictions --decline/--reopen/--declined` exactly: each
    short-circuits BEFORE the bundle walk, writes or reads
    `bundle/.state/decisions/**` only, and never adjudicates. An ordinary
    run hides any group already ruled distinct -- the human's answer
    outranks the model's, and re-offering a merge the human refused is what
    turned a wrong SAME verdict into an eventual certainty.

    A THIRD read command, mirroring `status`/`lint`'s shape exactly: no
    Phase B, no confirm gate, no `--auto`. Refuses (exit 1) via the shared
    `config.require_workspace` gate (D1) if the current directory is not an
    initialized workspace -- the SAME check `status`/`lint` use -- printing
    the reason to stderr with no raw traceback.

    On a workspace, `resolution.find_candidates_report` performs one
    read-only, whole-bundle pass and returns a `CandidateGroupReport` of
    candidate groups: same-type OKF objects that MIGHT be the same
    real-world entity, tiered by HOW they matched -- HIGH (an exact
    normalized title) or LOW (a near-match). The tier records the match
    METHOD, never a strength ranking, which is why a LOW group can carry
    a similarity score of 1.000 without contradiction (issue #192). This is
    a REPORT ONLY -- `duplicates` never merges, deletes, or otherwise
    adjudicates a candidate; it points at the SHIPPED `merge` verb
    (`cli/main.py:3957`) through its trailing hint instead (spec: Read-Only
    CLI Candidate Report Verb).

    `find_candidates_report` bounds its returned groups to
    `_MAX_CANDIDATE_GROUPS` (curate-call-budget); it does NOT return every
    group a pathological corpus would otherwise produce. WHEN the cap
    binds, `duplicates` echoes `candidate_group_truncation_notice` to
    stderr before rendering the (bounded) report -- never silently.

    Output is grouped by OKF `type`, then by tier, mirroring
    `find_candidates_report`'s own stable ordering: each group renders its
    type, tier, member concept_ids, and the trigger (the shared normalized
    key for HIGH, the similarity score for LOW). An empty result renders a
    clear "No candidates found." line instead of an empty section. Every
    successful read exits 0, whether or not any candidates are found (spec:
    No candidates still exits 0). No file under the workspace is ever
    created, modified, or deleted, and no `--json` or other structured
    output mode is offered (spec: Read-Only and Human-Readable Only).

    Unless `--include-deprecated` is passed, deprecated/superseded concepts
    (status-aware-retrieval) are excluded from every candidate group --
    `duplicates` shares `adjudicate`'s `find_candidates_report` call and,
    per the locked scope decision, gets the SAME `--include-deprecated`
    flag for consistency.
    """
    root = Path.cwd()
    try:
        # #797: the three decision verbs short-circuit BEFORE the whole-bundle
        # walk, mirroring `contradictions --decline/--reopen/--declined`. A human
        # ruling must be recordable on a workspace whose candidate set is
        # expensive to compute, or slow to write is slow to use.
        if keep_distinct:
            ruling = duplicates_service.record_identity_ruling(
                root,
                keep_distinct,
                flag="--keep-distinct",
                target_state="declined",
                on_warning=_echo_warning,
            )
            typer.echo(
                f"openkos duplicates: keeping distinct {' + '.join(ruling.members)}."
            )
            _autocommit(
                root,
                [ruling.rel_path],
                f"openkos: keep distinct {'/'.join(ruling.members)}",
            )
            return
        if reopen:
            ruling = duplicates_service.record_identity_ruling(
                root,
                reopen,
                flag="--reopen",
                target_state="open",
                on_warning=_echo_warning,
            )
            typer.echo(f"openkos duplicates: reopened {' + '.join(ruling.members)}.")
            _autocommit(
                root,
                [ruling.rel_path],
                f"openkos: reopen identity {'/'.join(ruling.members)}",
            )
            return
        if kept_distinct:
            _duplicates_kept_distinct_view(root)
            return

        report = duplicates_service.report_duplicates(
            root,
            include_deprecated=include_deprecated,
            find_candidates_report=find_candidates_report,
            on_warning=_echo_warning_once(),
        )
    except duplicates_service.InvalidMembers as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(code=2) from exc
    except duplicates_service.DuplicatesRefused as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(code=1) from exc

    if report.truncation_notice is not None:
        typer.echo(report.truncation_notice, err=True)

    typer.echo(f"openkos duplicates: workspace at {root}")
    typer.echo()
    if report.suppressed:
        typer.echo(
            f"Hiding {report.suppressed} group{_plural(report.suppressed)} you "
            "ruled distinct (`openkos duplicates --kept-distinct` lists them)."
        )
        typer.echo()
    groups = report.groups
    if not groups:
        typer.echo("No candidates found.")
        return

    high_count = sum(1 for group in groups if group.tier is Tier.HIGH)
    acronym_count = sum(1 for group in groups if group.tier is Tier.ACRONYM)
    low_count = len(groups) - high_count - acronym_count
    typer.echo(_format_group_tally(high_count, acronym_count, low_count))
    typer.echo(
        "Legend: [tier] type -- trigger. The tier is the MATCH METHOD, "
        "not a strength ranking: HIGH = exact normalized key, "
        "ACRONYM = one title's token is the initials of a word run in the "
        "other, LOW = weakest per-token match of the smaller title "
        "(1.000 = every token matched, NOT identical titles)."
    )
    for group in groups:
        tier_label = group.tier.name
        typer.echo(f"[{tier_label}] {group.okf_type} -- {group.trigger}")
        for member_id in group.member_ids:
            typer.echo(f"  - {member_id}")
        typer.echo()
    typer.echo("Next: openkos merge <survivor> <absorbed>")


def _duplicates_kept_distinct_view(root: Path) -> None:
    """`--kept-distinct`: every group a human ruled distinct, so the ruling is
    visible and reversible rather than an invisible suppression (#797)."""
    records = duplicates_service.list_kept_distinct(root, on_warning=_echo_warning)
    typer.echo(f"openkos duplicates --kept-distinct: workspace at {root}")
    typer.echo()
    if not records:
        typer.echo("No groups kept distinct.")
        return
    for record in records:
        typer.echo(f"[KEPT DISTINCT] {' + '.join(record.member_ids)}")
        typer.echo(f"  decided: {record.decided_at}")
        typer.echo()
    typer.echo("Reopen one with: openkos duplicates --reopen <id> --reopen <id>")


@app.command(
    help=(
        "Report which candidate duplicates the model judges to be the same "
        "concept, with its reasoning. Read-only unless you pass an apply flag."
    ),
    rich_help_panel="Curate",
)
@_guard_workspace_lock("adjudicate", commit_phase=True)
def adjudicate(
    same_only: bool = typer.Option(
        False,
        "--same-only",
        help="Only show SAME-verdict groups in the printed report.",
    ),
    include_deprecated: bool = typer.Option(
        False,
        "--include-deprecated",
        help="Include deprecated and superseded concepts (excluded by default).",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit adjudication verdicts as JSON to stdout; suppress human output.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Interactively merge each SAME 2-member group after previewing it.",
    ),
    apply_same: bool = typer.Option(
        False,
        "--apply-same",
        help=(
            "Batch-merge every eligible SAME 2-member group after one "
            "guarded confirmation (see --confirm-count)."
        ),
    ),
    confirm_count: str | None = typer.Option(
        None,
        "--confirm-count",
        help=(
            "The exact eligible-merge count (see the printed preview), for "
            "non-interactive/test use with --apply-same. On a TTY, "
            "omitting this prompts interactively instead. There is NO "
            "bypass for this count -- it must match exactly."
        ),
    ),
    no_reconcile: bool = typer.Option(
        False,
        "--no-reconcile",
        help=(
            "Skip the reconciliation pass (#645) on merges applied by "
            "--apply/--apply-same: keep the merged body as the survivor's "
            "text with the absorbed text appended under a '## Merged "
            "content' heading, with no model call. The same opt-out `merge` "
            "and `curate` take."
        ),
    ),
    reconcile: bool = typer.Option(
        False,
        "--reconcile",
        help=(
            "Force the reconciliation pass (#645) on merges applied by "
            "--apply/--apply-same, even below the share and merged-length "
            "thresholds that decide it by default. The same opt-in `merge` "
            "and `curate` take. Refused together with --no-reconcile."
        ),
    ),
    include_cross_source: bool = typer.Option(
        False,
        "--include-cross-source",
        help=(
            "Let --apply-same batch-merge SAME pairs whose members share "
            "no provenance source (#776) -- excluded by default because "
            "that is the class that fuses distinct real-world items."
        ),
    ),
    include_cross_type: bool = typer.Option(
        False,
        "--include-cross-type",
        help=(
            "Let --apply-same batch-merge SAME pairs whose members declare "
            "different OKF types (#904) -- excluded by default because "
            "that is the class that absorbs one kind of thing into another."
        ),
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help=(
            "Bypass the persisted-adjudications serve and re-judge every "
            "candidate group with the model (#779), re-persisting the "
            "fresh verdicts -- the same lever `contradictions --fresh` is."
        ),
    ),
) -> None:
    """LLM-adjudicate cross-source candidate duplicates: read-only by default.

    A FOURTH read command, mirroring `query`'s wiring exactly: the shared
    `config.require_workspace` gate (D1), then a Phase-A `read_config` guard
    (`except (OSError, ValueError)`, lint parity), then a real
    `OllamaClient(model=cfg.model)` is built and injected -- as the
    `LLMBackend` -- into `resolution.find_candidates_report` followed by
    `resolution.adjudication.adjudicate_candidates`. Invoked WITHOUT
    `--apply`/`--apply-same` it never merges, writes, or decides -- it only
    prints a verdict for human review and points at the SHIPPED `merge` verb
    (`cli/main.py:3957`) through its own `Next:` hint, exactly as
    `duplicates` does. Those two flags are the only paths that write, and
    both are gated on an explicit confirmation; see their paragraphs below.

    `find_candidates_report` bounds its returned groups to
    `_MAX_CANDIDATE_GROUPS` (curate-call-budget); it does NOT hand
    `adjudicate_candidates` every group a pathological corpus would
    otherwise produce. WHEN the cap binds, `adjudicate` echoes
    `candidate_group_truncation_notice` to stderr before the LLM pass
    begins -- never silently.

    `--json` emits the adjudication results as a single pretty-printed JSON
    array on stdout and fully suppresses all human output (tally, legend,
    per-group detail, `Next:` hint, and both empty-state messages). It emits
    every verdict by default; passing `--same-only` filters the array to
    `SAME` entries, mirroring the human display filter. On a partial batch
    (#441) the array holds the completed verdicts and the run still exits 1
    after the stderr failure line below -- a machine consumer that ignores
    the exit code reads valid, paid-for verdicts, never a fabricated
    complete run.

    Output mirrors `duplicates`'s grouped render (type, tier, trigger,
    members) with each group's verdict and rationale appended. The parsed
    confidence is intentionally NOT rendered (issue #138): a local model
    returns a flat, uncalibrated value, so a two-decimal number would imply
    a precision it does not have.
    `--same-only` is a DISPLAY-only filter: it hides non-`SAME` verdicts from
    the printed report, but `adjudicate_candidates` always receives -- and
    returns -- every candidate group regardless of the flag; the library
    itself never filters.

    A no-model/no-Ollama failure comes back INSIDE the returned
    `AdjudicationBatch` (#441) and maps onto the SAME 3-tier ORDERED wording
    `query` uses -- `BackendUnavailable`, then `BackendModelNotFound`, then
    the generic `BackendError` fallback -- each with its own actionable
    stderr message and exit 1. The completed verdicts are NEVER discarded:
    every output mode (report, `--json`, `--apply`, `--apply-same`) first
    processes `batch.results` exactly as a complete run over that list,
    THEN one stderr line reports the failure with completed-of-total counts
    and the run exits 1. The raise-path handler ladder is retained around
    the call itself for an injected backend that raises outside `llm.chat`'s
    guarded seam -- same wording, no counts, zero writes.

    Unless `--include-deprecated` is passed, deprecated/superseded concepts
    (status-aware-retrieval) are excluded from the `find_candidates_report`
    call that feeds `adjudicate_candidates` -- `adjudicate` uses candidates,
    so it threads the flag into `find_candidates_report`, not into
    `adjudicate_candidates` itself.

    Unless `--include-confidential` is passed, confidential concepts
    (sensitivity-fail-closed-filter) are excluded at the MEMBER level, inside
    `adjudicate_candidates` itself -- distinct from the deprecated axis above,
    a confidential member is dropped from a group's `member_ids` before its
    content is ever read, rather than dropping the whole group upstream.

    Without `--apply`/`--apply-same`, no file under the workspace is ever
    created, modified, or deleted (spec: Verb renders verdicts with zero
    writes, whose scenario is scoped to the flagless invocation).

    `--apply` switches to an INTERACTIVE merge walk over the same
    adjudication results (issue #137 Slice 2b-ii): mutually exclusive with
    `--json` (interactive vs. machine-readable output is contradictory), so
    that combination is rejected up front, before any workspace gate or
    read, with exit code 2.

    `--apply-same` switches to a GUARDED BATCH merge of every eligible SAME
    2-member group (issue #137 closing slice): prints one aggregate
    preview and total count, then requires the operator to type that exact
    count (via `--confirm-count`, an interactive TTY prompt, or refuses on
    a non-TTY without the flag) before applying anything -- a mismatch
    aborts with zero writes. Mutually exclusive with both `--apply` and
    `--json`, rejected up front with exit code 2.
    """
    if apply and json_output:
        typer.echo(
            "openkos adjudicate: --apply and --json are mutually exclusive.",
            err=True,
        )
        raise typer.Exit(code=2)
    if apply_same and apply:
        typer.echo(
            "openkos adjudicate: --apply-same and --apply are mutually exclusive.",
            err=True,
        )
        raise typer.Exit(code=2)
    if apply_same and json_output:
        typer.echo(
            "openkos adjudicate: --apply-same and --json are mutually exclusive.",
            err=True,
        )
        raise typer.Exit(code=2)
    if reconcile and no_reconcile:
        # #803: the same up-front, exit-2 shape as the pairs above.
        typer.echo(f"openkos adjudicate: {_RECONCILE_CONFLICT_MESSAGE}", err=True)
        raise typer.Exit(code=2)
    if include_cross_source and not apply_same:
        # #776: the flag consents to batch-merging the risky class; without
        # --apply-same it would consent to nothing, and a silently ignored
        # consent flag is worse than a refusal.
        typer.echo(
            "openkos adjudicate: --include-cross-source requires --apply-same.",
            err=True,
        )
        raise typer.Exit(code=2)
    if include_cross_type and not apply_same:
        # #904: the same shape as its #776 sibling above -- a consent flag
        # that consents to nothing is worse than a refusal.
        typer.echo(
            "openkos adjudicate: --include-cross-type requires --apply-same.",
            err=True,
        )
        raise typer.Exit(code=2)
    if confirm_count is not None and not (
        confirm_count.strip().isascii() and confirm_count.strip().isdigit()
    ):
        # #779: the typed-count rail is a COUNT, so a value that cannot
        # possibly match any count is refused before candidate discovery
        # and before any model call -- it used to be validated last, after
        # a full adjudication pass had been spent and discarded. A numeric
        # mismatch is still checked against the previewed total, which
        # only exists after adjudication (and, since #779, that repeat
        # pass is served from the store on an unchanged bundle).
        typer.echo(
            "openkos adjudicate: --confirm-count must be a whole number; "
            "nothing was adjudicated.",
            err=True,
        )
        raise typer.Exit(code=2)

    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos adjudicate: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos adjudicate: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    report = find_candidates_report(
        layout.bundle_dir, include_deprecated=include_deprecated
    )
    candidates = list(report.groups)
    notice = candidate_group_truncation_notice(report)
    if notice is not None:
        typer.echo(notice, err=True)
    llm = _chat_client(cfg, task="adjudication")
    local_exemption = _resolve_local_exemption(
        cast(application_backends.HasLocality, llm), cfg
    )
    # #779: serve digest-fresh persisted verdicts before any model contact,
    # exactly as `contradictions` has since #653 -- the two advisors no
    # longer disagree about whether a repeat run on an unchanged bundle
    # costs model calls. `--fresh` bypasses the serve; every fresh verdict
    # is (re-)persisted below. The store keys on the EFFECTIVE confidential
    # inclusion -- `--include-confidential` OR the verified local-backend
    # exemption (the same disjunction `sensitivity.should_block` applies)
    # -- which is why this partition runs only AFTER the exemption is
    # resolved: a verdict judged with confidential members included must
    # never serve a run that would exclude them, whichever lever included
    # them.
    effective_confidential = include_confidential or local_exemption
    served_by_key: dict[str, AdjudicatedCandidate] = {}
    to_judge = candidates
    rubric_stale = 0
    store_read = False
    if candidates and not fresh:
        served_by_key, to_judge, rubric_stale, store_read = (
            _partition_adjudication_serves(
                layout, candidates, include_confidential=effective_confidential
            )
        )
    # READ, not merely attempted (#809's gate, adopted by #867 when curate's
    # Identity became this line's second surface): a first-ever run has no
    # store to have consulted, and an unreadable one already printed its own
    # warning -- a `0 of N served` beneath either would be a count of "could
    # not look" in the words of "looked, found nothing".
    if store_read:
        typer.echo(
            f"openkos adjudicate: {len(served_by_key)} of {len(candidates)} "
            "candidate group(s) served from persisted adjudications; "
            f"{len(to_judge)} judged fresh.",
            err=True,
        )
    # #838: name the reason a rubric change empties the serve. The split
    # line above is honest but silent about WHY, and a sudden `0 of N
    # served` after an upgrade reads as a surprising cost; naming the
    # rubric turns it into an explained, deliberate one. Absent on the
    # healthy path and on ordinary member drift.
    if rubric_stale:
        typer.echo(
            f"openkos adjudicate: {rubric_stale} cached verdict(s) predate "
            "the current judgment rubric; re-judging them fresh.",
            err=True,
        )
    observability.warn_if_walk_incomplete(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    # #1137: the judging call below holds no workspace lock, so pin each
    # member's digest BEFORE it -- the persist keeps a verdict only for content
    # that is still what was judged.
    _digest_of = application_pending.current_finding_digest(layout.bundle_dir)
    judged_digests = {
        member_id: _digest_of(member_id)
        for group in to_judge
        for member_id in group.member_ids
    }
    try:
        # Still called with an empty `to_judge` (a fully-served run): zero
        # groups means zero `llm.chat` calls by construction, and the
        # pre-#779 seam contract (e.g. the on_progress threading pin)
        # stays byte-identical.
        batch = adjudicate_candidates(
            to_judge,
            bundle_dir=layout.bundle_dir,
            llm=llm,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
            # TTY-gated per-group progress on stderr; `None` (silent) when
            # output is piped (issue #190, mirrors `suggest-relations`' #134
            # per-edge line).
            on_progress=observability.progress_callback(
                "adjudicate", "adjudicating group"
            ),
        )
    except BackendUnavailable as exc:
        typer.echo(
            f"openkos adjudicate: failed -- {exc}. "
            f"{application_backends.start_hint(cfg)}, "
            f"then try again.{_DOCTOR_HINT}",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except BackendModelNotFound as exc:
        typer.echo(
            f"openkos adjudicate: failed -- model '{cfg.model}' is not "
            f"installed. {application_backends.install_hint(cfg, cfg.model)}, "
            "then try again.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    # The two specific handlers above MUST precede this generic handler:
    # both `BackendUnavailable` and `BackendModelNotFound` subclass
    # `BackendError`, so reordering would silently funnel them into this
    # fallback and lose their actionable remediation messages (mirrors
    # `query`'s ordering).
    except BackendError as exc:
        typer.echo(f"openkos adjudicate: failed -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    # #779: fresh verdicts persist even on a partial batch (the paid-for
    # work is kept, mirroring #441's own posture), then the run's results
    # are rebuilt in candidate order -- served verdict, else fresh one;
    # groups past a mid-batch failure appear in neither and stay absent.
    _persist_adjudications(
        layout,
        batch.results,
        include_confidential=effective_confidential,
        judged_digests=judged_digests,
    )
    if served_by_key:
        results = _reassemble_adjudications(candidates, served_by_key, batch.results)
    else:
        # Nothing served -> the batch IS the run, byte-identical to the
        # pre-#779 contract (and tolerant of a test double returning
        # results the discovery report never listed).
        results = list(batch.results)
    if json_output:
        # #776: the machine surface carries the same cross-source signal
        # the human listing renders as a note -- computed HERE, so the
        # payload builder stays I/O-free.
        cross_source_flags = tuple(
            result.verdict is Verdict.SAME
            and len(result.candidate.member_ids) == 2
            and application_lifecycle.cross_source_same_pair(
                layout.bundle_dir, result.candidate.member_ids
            )
            for result in results
        )
        # #904: same reasoning as #776's flag above. `okf_type` already
        # renders the joined `"Event+Project"` label, but `_type_label`'s
        # own contract forbids parsing that label back into member types,
        # so a conforming pipeline has no other way to see the class.
        cross_type_flags = tuple(
            result.verdict is Verdict.SAME
            and application_lifecycle.cross_type_concern(
                layout.bundle_dir, result.candidate.member_ids
            )
            is not None
            for result in results
        )
        typer.echo(
            json.dumps(
                _adjudication_payload(
                    results,
                    same_only=same_only,
                    total=len(candidates),
                    partial=batch.failure is not None,
                    cross_source_flags=cross_source_flags,
                    cross_type_flags=cross_type_flags,
                ),
                indent=2,
            )
        )
    elif apply:
        _run_adjudicate_apply(
            root,
            layout,
            index_path,
            log_path,
            results,
            no_reconcile=no_reconcile,
            reconcile=reconcile,
        )
    elif apply_same:
        _run_adjudicate_apply_same(
            root,
            layout,
            index_path,
            log_path,
            results,
            confirm_count=confirm_count,
            no_reconcile=no_reconcile,
            reconcile=reconcile,
            include_cross_source=include_cross_source,
            include_cross_type=include_cross_type,
        )
    else:
        _render_adjudicate_report(root, results, same_only=same_only)

    if batch.failure is not None:
        # Partial batch (#441): every output mode above already processed the
        # completed verdicts exactly as a complete run over that list -- the
        # paid-for work is never discarded -- so all that remains is the one
        # stderr failure line and the BackendError-family exit code.
        _echo_adjudicate_batch_failure(batch, total=len(candidates), model=cfg.model)
        raise typer.Exit(code=1) from batch.failure


_CANDIDATES_UNAVAILABLE_MESSAGE = (
    "Candidate relations unavailable — run `openkos reindex` (vectors.db missing)."
)
"""Slice 0 (issue #183) state-3 message, shared verbatim by
`suggest-relations` and `contradictions` -- design.md's Slice 0 table marks
both sites' state-3 message identical, and both key it on
`vector_store_is_empty(layout.vectors_db_path)` (absent OR empty; Slice 1's
`neighbors()`/`proximity.py` plumbing is out of scope for this check)."""


def _open_proximity_or_degrade(
    vectors_db_path: Path,
) -> proximity.VectorProximitySource | None:
    """Resolve the embedding-proximity candidate source for this run, or
    `None` when embeddings cannot serve one (#183).

    The ONE place any command decides whether candidate edges are available.
    Every caller then derives the user-facing "embeddings missing" state
    from `is None` rather than probing `vectors.db` a second time -- that
    probe used to live inside `_zero_edge_state_message`, on a path taken
    every run.

    Returns the source rather than a `(source, unavailable)` pair on
    purpose: `unavailable` IS `source is None`, and a tuple carrying the
    same fact twice invites the two halves to drift. `status` (via
    `application.status.build_status_report`, issue #995 PR 3), which needs
    the state but never the source, calls `vector_store_is_empty` directly
    -- consistent by construction, because `open_proximity_source` is
    defined against that exact predicate.

    Never raises: `open_proximity_source` already absorbs an absent, empty,
    unreadable or extension-less store."""
    return proximity.open_proximity_source(vectors_db_path)


def _zero_edge_state_message(
    layout: config.WorkspaceLayout,
    *,
    store: GraphStore,
    use_typed_count: bool,
    none_survived: str,
    embeddings_missing: bool,
    all_excluded: str | None = None,
) -> str:
    """Select the Slice 0 (issue #183) three-state message for a
    zero-candidate outcome at `suggest-relations`/`contradictions`.

    `embeddings_missing` comes from the caller's `_open_proximity_or_degrade`
    result (`source is None`), never from a second probe of `vectors.db` --
    the seam already read it.

    `store` (graph-projection-reuse, issue #196): the SAME already-open
    `GraphStore` the caller built for its primary read -- REQUIRED, not
    optional, so a forgotten keyword fails a `TypeError` at import-time test
    collection rather than silently rebuilding the projection a second time.
    Both call sites now sit lexically inside their own `with build_graph(...)
    as store:` block, so this function never opens or closes a store itself.
    `layout` is retained for the state-3 early return's context even though
    its `bundle_dir` is not read again here.

    State 3 (embeddings absent OR empty) is checked FIRST: it also starves
    any embedding-sourced candidate edge, so it wins over whatever the
    typed/total edge count would otherwise say (design.md's Graceful
    Degradation table). Otherwise state 1 or state 2 (`none_survived`,
    formatted with `count=`) is picked from `graph_edge_summary`'s `(total,
    typed)` -- `use_typed_count` selects which of the two
    `suggest-relations` counts total edges while `contradictions` counts
    only typed ones, since a typed-but-excluded edge (e.g. `derived_from`)
    is still "nothing to contradict" but is NOT "nothing to type". State 1's
    wording also tracks `use_typed_count`: the typed-count mode (`contradictions`)
    says "no typed edges yet" (the graph may still have untyped
    concept-to-concept edges -- a DIFFERENT, non-contradictory claim from
    `status`'s total-count wording), while the total-count mode
    (`suggest-relations`) says "no concept relationships yet".

    `all_excluded` (total-count mode only) covers the case where the graph
    DOES still hold untyped rows but none of them survived the caller's
    filtering -- `_candidate_edges`'s PAIR-level exclusion (`relate` adds a
    typed `relations:` row without ever removing the original untyped
    body-link row, `edge_typing.py:116-138`) or `candidate_edges`'s
    confidentiality gate. `none_survived`'s "none are untyped" wording would
    be factually FALSE there, because `graph_edge_summary` counts raw rows
    with neither filter applied. It is formatted with `count=` and
    `untyped=`; omitting it keeps the plain `none_survived` wording."""
    if embeddings_missing:
        return _CANDIDATES_UNAVAILABLE_MESSAGE
    total, typed = graph_edge_summary(layout.bundle_dir, store=store)
    count = typed if use_typed_count else total
    if count == 0:
        if use_typed_count:
            # #557: point forward instead of dead-ending -- these are the
            # verbs that create the typed edges this check consumes.
            return (
                "The graph has no typed edges yet. Apply relations first: "
                "`openkos suggest-relations` then `openkos relate`, or "
                "`openkos curate`."
            )
        return "No concept relationships in the graph yet."
    untyped = total - typed
    if all_excluded is not None and untyped > 0:
        return all_excluded.format(count=count, untyped=untyped)
    return none_survived.format(count=count)


_suggestion_caveat = relations_service.suggestion_caveat
"""Re-exported for the callers that reach the caveat through `cli.main`
(curate's Structure stage). The definition -- what a suggested type does NOT
establish (#778) -- lives in `application/suggest_relations_service.py`, beside
the `--apply` walk that renders it."""


def _relations_zero_state_message(
    layout: config.WorkspaceLayout, store: GraphStore, embeddings_missing: bool
) -> str:
    """`suggest-relations`' wording for a zero-candidate outcome, handed to the
    service as a port because `_zero_edge_state_message` is shared with
    `contradictions`."""
    return _zero_edge_state_message(
        layout,
        store=store,
        use_typed_count=False,
        embeddings_missing=embeddings_missing,
        none_survived="{count} relation(s) exist; none are untyped.",
        all_excluded=(
            "{count} relation(s) exist; {untyped} untyped, but every "
            "untyped pair is already typed elsewhere or filtered as "
            "confidential -- nothing left to suggest."
        ),
    )


class _SuggestRelationsObserver:
    """The CLI's rendering of a `suggest-relations` run (issue #1168): the
    service hands it typed data and this class owns every word and the cost
    question."""

    def walk_incomplete(
        self, bundle_dir: Path, *, include_confidential: bool, local_exemption: bool
    ) -> None:
        observability.warn_if_walk_incomplete(
            bundle_dir,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        )

    def workspace_header(self, root: Path) -> None:
        typer.echo(f"openkos suggest-relations: workspace at {root}")
        typer.echo()

    def empty_window(self, edge_offset: int) -> None:
        typer.echo(
            f"no candidate edges at --edge-offset {edge_offset}; "
            "re-run with a smaller offset."
        )

    def candidate_notices(self, truncation: str | None, quarantine: str | None) -> None:
        if truncation is not None:
            typer.echo(truncation)
            typer.echo()
        if quarantine is not None:
            typer.echo(quarantine)
            typer.echo()

    def no_candidates(self, message: str) -> None:
        typer.echo(message)

    def warn(self, message: str) -> None:
        typer.echo(message, err=True)

    def serve_split(self, served: int, total: int, fresh: int) -> None:
        typer.echo(
            f"openkos suggest-relations: {served} of {total} "
            "candidate edge(s) served from persisted suggestions; "
            f"{fresh} typed fresh.",
            err=True,
        )

    def confirm_cost(self, quote: relations_service.CostQuote) -> bool:
        served_clause = f", {quote.served} served" if quote.served else ""
        # #872: the pace clause rides the paid path only -- a fully-served run
        # makes zero calls, so "one per edge (this can take a while)" would be
        # false two tokens after the count said so. The `--auto` hint stays
        # either way: the prompt it names still fires.
        pace_note = ", one per edge (this can take a while)" if quote.to_type else ""
        typer.echo(
            f"{quote.total} untyped edge(s){served_clause} -> {quote.to_type} LLM "
            f"call(s){pace_note}. Pass --auto to skip this prompt.",
            err=True,
        )
        return typer.confirm("Proceed?")

    def edge_progress(self, index: int, count: int, suggestion: EdgeSuggestion) -> None:
        """Per-edge progress line to stderr (keeps stdout the clean report).

        `effective_edge`, not `edge` (#991 second review round): this renders
        the direction the type actually holds in, honoring a correction the
        same way every other rendering surface does."""
        edge = suggestion.effective_edge
        label = suggestion.suggested_type or "?"
        typer.echo(
            f"  [{index}/{count}] {edge.source_id} -> {edge.target_id}  [{label}]",
            err=True,
        )


class _ApplyObserver:
    """The CLI's rendering of the `suggest-relations --apply` walk: the same
    `[type] source -> target` + rationale block the read-only report prints,
    and the one validating per-item consent prompt (`curate._confirm`, the
    #398/#483 contract)."""

    def degraded(self, edge: Edge) -> None:
        typer.echo(f"[?] {edge.source_id} -> {edge.target_id}")
        typer.echo("  note: no valid type suggested")

    def preview(
        self, edge: Edge, suggested_type: str, caveat: str, rationale: str
    ) -> None:
        # #778: the SAME caveat curate's Structure stage spells (#624) -- an
        # asymmetric direction carries no evidence, and the surface that most
        # invites bulk application must not be the one surface missing the
        # documented warning. Rendered on the preview line AND inside the
        # consent prompt, mirroring curate exactly.
        typer.echo(f"[{suggested_type}] {edge.source_id} -> {edge.target_id}{caveat}")
        typer.echo(f"  rationale: {rationale}")

    def confirm_relate(self, edge: Edge, suggested_type: str, caveat: str) -> bool:
        return curate_module._confirm(
            f"Relate {edge.source_id} -> {edge.target_id} "
            f"[{suggested_type}]{caveat}? [y/N]"
        )

    def already_present(self) -> None:
        typer.echo("  note: already present -- nothing to write")

    def summary(self, outcome: relations_service.ApplyOutcome) -> None:
        prefix = "nothing to apply -- " if outcome.nothing_to_apply else ""
        typer.echo(
            f"openkos suggest-relations --apply: {prefix}applied {outcome.applied}, "
            f"skipped {outcome.skipped} (declined: {len(outcome.declined)})"
        )
        for item in outcome.declined:
            typer.echo(f"  declined: {item}")


def _suggest_relations_ports() -> relations_service.SuggestRelationsPorts:
    """The service's effects, each resolved through this module's globals AT
    CALL TIME (a lambda, not a bound reference) so a test that patches
    `openkos.cli.main.candidate_edges`, `suggest_edge_types`, `build_graph`,
    `_open_proximity_or_degrade` or `_chat_client` keeps intercepting."""
    return relations_service.SuggestRelationsPorts(
        chat_client=lambda cfg, task: _chat_client(cfg, task=task),
        zero_state_message=_relations_zero_state_message,
        autocommit=lambda root, paths, message: _autocommit(root, paths, message),
        refresh_derived=lambda layout: _refresh_derived_after_write(
            layout, None, verb="suggest-relations"
        ),
        resolve_local_exemption=lambda client, cfg: _resolve_local_exemption(
            client, cfg
        ),
        open_proximity=lambda path: _open_proximity_or_degrade(path),
        build_graph=lambda *args, **kwargs: build_graph(*args, **kwargs),
        candidate_edges=lambda *args, **kwargs: candidate_edges(*args, **kwargs),
        suggest_edge_types=lambda *args, **kwargs: suggest_edge_types(*args, **kwargs),
        commit_section=lambda: _commit_section_for(Path.cwd())(),
    )


def _refuse(exc: "relations_service.SuggestionRefused") -> "typer.Exit":
    """Print a typed suggestion refusal verbatim and map its TYPE to the exit
    code: 3 for drift (the one failure a script may safely retry, #319), 1 for
    everything else."""
    typer.echo(exc.message, err=True)
    return typer.Exit(code=3 if isinstance(exc, relations_service.DriftDetected) else 1)


@app.command(
    "suggest-relations",
    help=(
        "Suggest a type for every untyped link between concepts, for you to "
        "review before anything is written."
    ),
    rich_help_panel="Curate",
)
@_guard_workspace_lock("suggest-relations", commit_phase=True)
def suggest_relations_cmd(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation gate and type every untyped edge.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Walk the generated suggestions with a per-item [y/N] consent "
        "prompt and write each accepted relation -- the same write path "
        "`relate` uses.",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help=(
            "Bypass the persisted-suggestions serve and re-type every "
            "candidate edge with the model (#799), re-persisting the fresh "
            "suggestions -- the same lever `adjudicate --fresh` is."
        ),
    ),
    edge_offset: int = typer.Option(
        0,
        "--edge-offset",
        min=0,
        help="Skip the first N ranked candidate edges, so the batch beyond "
        "the cap is browsable without first typing the batch before it "
        "(#567). The capped run names the next batch's exact offset.",
    ),
) -> None:
    """LLM-suggest a relation `type` for every existing UNTYPED body-link
    edge: read-only, like `adjudicate`.

    A thin adapter over `application.suggest_relations_service` (issue #1168):
    the service owns the workspace gate, the candidate count, the serve
    partition, the typing run, the persistence and the `--apply` write
    sequence; this verb keeps the rendering, the cost question, the per-item
    consent prompts and the exit-code mapping.

    Cost gate (issue #134): each untyped edge costs one LLM inference, run
    sequentially, so a large bundle can take many minutes with the model
    resident. Before contacting the model, the command prints the count
    (`N untyped edges -> N LLM calls`) and asks for confirmation; `--auto`
    skips the prompt. A per-edge progress line is written to stderr as the
    run proceeds. Declining the prompt exits 0 with nothing generated.

    Without `--apply`, `suggest-relations` never writes, merges, or decides
    -- it only prints a suggested `type` + rationale per untyped edge for
    human review, plus a closing hint naming `--apply` and the existing
    `relate` verb (spec: Human-In-The-Loop Write Path Unchanged). The
    confirmation gate is read-only (it generates suggestions, never
    writes); there is no `--json` or other structured mode.

    `--apply` (issue #560, mirroring `adjudicate --apply` and reusing
    curate's Structure-stage walk verbatim): each VALID suggestion is
    rendered, then gated behind `curate._confirm`'s validating per-item
    `[y/N]` prompt (#398 contract); an accepted `y` writes through the SAME
    `prepare_relate` -> drift guard -> `relate_core` -> auto-commit path the
    `relate` verb and curate's Structure stage use, so the three write paths
    cannot drift apart. A degraded suggestion has nothing applicable and is
    never prompted; an already-present relation is reported and skipped
    without a write; declines are listed at the end (the #483
    revisitable-decline contract). Drift refuses with exit 3, prior per-item
    commits remain intact.

    A degraded suggestion (`suggested_type=None` -- a malformed LLM reply,
    or a suggested type that failed `validate_relation_type`) renders as
    `[?]` plus a `note: no valid type suggested` line, never as if it were a
    valid suggestion (spec: Invalid suggested type is not surfaced as
    valid). Already-typed edges never appear at all -- `candidate_edges`
    filters them out before this command ever sees them (spec: Already-typed
    edges are excluded from suggestions).

    A no-model/no-Ollama failure comes back INSIDE the returned
    `EdgeSuggestionBatch` (#441) and maps onto the SAME 3-tier ORDERED
    wording `adjudicate`/`query` use -- `BackendUnavailable`, then
    `BackendModelNotFound`, then the generic `BackendError` fallback -- each
    with its own actionable stderr message and exit 1. The completed
    suggestions are NEVER discarded: the report first renders
    `batch.results` exactly as a complete run over that list, THEN one
    stderr line reports the failure with completed-of-total counts and the
    run exits 1. The raise-path handler ladder is retained around the call
    itself for an injected backend that raises outside `llm.chat`'s guarded
    seam -- same wording, no counts, zero writes either way.

    Unless `--include-confidential` is passed, an untyped edge with a
    confidential endpoint (sensitivity-fail-closed-filter) is excluded from
    candidates -- dropped by `candidate_edges` before `llm.chat` is ever
    called for it.

    No file under the workspace is ever created, modified, or deleted
    (spec: Verb performs zero writes) -- except `--apply`'s accepted
    relations, and the persisted suggestions in `.openkos/findings.db`.
    """
    root = Path.cwd()
    ports = _suggest_relations_ports()
    try:
        outcome = relations_service.suggest_relations(
            root,
            relations_service.SuggestRelationsRequest(
                include_confidential=include_confidential,
                fresh=fresh,
                edge_offset=edge_offset,
                skip_confirmation=auto,
            ),
            ports,
            _SuggestRelationsObserver(),
        )
    except relations_service.SuggestionRefused as exc:
        raise _refuse(exc) from exc

    if outcome.status == "declined":
        typer.echo("Aborted -- no suggestions generated.")
        return
    if outcome.status != "completed":
        return

    if apply:
        try:
            relations_service.apply_relation_suggestions(
                root, outcome.results, ports, _ApplyObserver()
            )
        except relations_service.SuggestionRefused as exc:
            raise _refuse(exc) from exc
    else:
        for result in outcome.results:
            # `effective_edge`, not `edge` (#991 second review round): the
            # candidate identity `edge` stays fixed for persistence/
            # reassembly, but this listing must show the direction the
            # type actually holds in.
            edge = result.effective_edge
            if result.suggested_type is None:
                typer.echo(f"[?] {edge.source_id} -> {edge.target_id}")
                typer.echo("  note: no valid type suggested")
            else:
                # #778: the read-only listing carries the same caveat as
                # the --apply prompt and curate -- docs/testing.md promises
                # it wherever a relation direction is presented.
                typer.echo(
                    f"[{result.suggested_type}] {edge.source_id} -> "
                    f"{edge.target_id}{_suggestion_caveat(result.suggested_type)}"
                )
                typer.echo(f"  rationale: {result.rationale}")
            typer.echo()

        typer.echo(
            "Next: openkos suggest-relations --apply (per-item consent), or "
            "openkos relate <source> <type> <target>"
        )

    if outcome.truncation_notice is not None:
        # Issue #560: the cap is not a dead end -- an applied/related pair
        # becomes a typed edge and leaves the candidate set, so the next
        # run's cap budget reaches the candidates dropped this time.
        typer.echo(
            "Candidates beyond the cap are not lost: type the edges shown "
            "(--apply or relate), then re-run suggest-relations to surface "
            "the next batch."
        )
        if outcome.next_offset is not None:
            # #567: browsing without typing -- name the exact offset the
            # next ranked batch starts at, gated on a visible pair actually
            # existing beyond this run's window.
            typer.echo(
                f"Or browse it without typing these: re-run with "
                f"--edge-offset {outcome.next_offset}."
            )

    if outcome.failure is not None and outcome.batch is not None:
        # Partial batch (#441): the report above already rendered the
        # completed suggestions exactly as a complete run over that list --
        # the paid-for work is never discarded -- so all that remains is the
        # one stderr failure line and the BackendError-family exit code.
        typer.echo(
            relations_service.relations_batch_failure_message(
                outcome.batch,
                total=outcome.total,
                model=outcome.model,
                cfg=outcome.cfg,
            ),
            err=True,
        )
        raise typer.Exit(code=1) from outcome.failure


class _VolatilityObserver:
    """The CLI's rendering of a `suggest-volatility` run (issue #1168)."""

    def walk_incomplete(
        self, bundle_dir: Path, *, include_confidential: bool, local_exemption: bool
    ) -> None:
        observability.warn_if_walk_incomplete(
            bundle_dir,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        )

    def progress_callback(self) -> Callable[[int, int, TierSuggestion], None] | None:
        # TTY-gated per-type progress on stderr; `None` (silent) when output is
        # piped (issue #190, mirrors `suggest-relations`' #134 per-edge line).
        return observability.progress_callback("suggest-volatility", "suggesting type")


@app.command(
    "suggest-volatility",
    help=(
        "Suggest how quickly each kind of concept goes stale, so freshness "
        "checks use a sensible window per type. Advisory; writes nothing on "
        "its own."
    ),
    rich_help_panel="Curate",
)
@_guard_workspace_lock("suggest-volatility")
def suggest_volatility_cmd(
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
) -> None:
    """LLM-suggest a volatility `tier` for every concept TYPE present in the
    bundle: read-only, like `suggest-relations`.

    A thin adapter over `application.suggest_volatility_service` (issue
    #1168): the service owns the workspace gate, the client wiring and the
    typing run; this verb keeps the rendering and the exit-code mapping.

    `suggest-volatility` never writes, merges, or decides -- it only prints
    a suggested `tier` + rationale per concept type present for human
    review, plus a closing hint pointing at `openkos set-volatility
    <ConceptType> <tier>` (write-verb #140) to apply an accepted suggestion.
    No `--auto`, no confirmation gate, no `--json` or other structured mode.

    A degraded suggestion (`suggested_tier=None` -- a malformed LLM reply,
    or a suggested tier that is not a member of `types.VOLATILITY_TIERS`)
    renders as `[?]` plus a `note: no valid tier suggested` line, never as
    if it were a valid suggestion (spec: Fail-Closed Per-Type Suggestion
    Parsing). One other type's degraded reply never stops the run -- every
    other type present is still reported.

    A no-model/no-Ollama failure comes back INSIDE the returned
    `TierSuggestionBatch` (#441) and maps onto the SAME 3-tier ORDERED
    wording `suggest-relations`/`adjudicate`/`query` use --
    `BackendUnavailable`, then `BackendModelNotFound`, then the generic
    `BackendError` fallback -- each with its own actionable stderr message
    and exit 1. The completed suggestions are NEVER discarded: the report
    first renders `batch.results` exactly as a complete run over that list,
    THEN one stderr line reports the failure with the completed count (no
    of-total -- see `volatility_batch_failure_message` for why this verb
    cannot state one) and the run exits 1. The raise-path handler ladder is
    retained around the call itself for an injected backend that raises
    outside `llm.chat`'s guarded seam -- same wording, no counts, zero
    writes either way.

    Unless `--include-confidential` is passed, a confidential concept
    (sensitivity-fail-closed-filter) is excluded from sampling for its type
    -- dropped by `suggest_volatility` before its body is ever shown to the
    LLM. A type whose docs are all confidential yields no suggestion for
    that type at all.

    No file under the workspace is ever created, modified, or deleted
    (spec: Verb performs zero writes).
    """
    root = Path.cwd()
    try:
        outcome = volatility_service.suggest_volatility_tiers(
            root,
            volatility_service.VolatilityRequest(
                include_confidential=include_confidential
            ),
            volatility_service.VolatilityPorts(
                chat_client=lambda cfg, task: _chat_client(cfg, task=task),
                resolve_local_exemption=lambda client, cfg: _resolve_local_exemption(
                    client, cfg
                ),
                suggest_volatility=lambda *args, **kwargs: suggest_volatility(
                    *args, **kwargs
                ),
            ),
            _VolatilityObserver(),
        )
    except relations_service.SuggestionRefused as exc:
        raise _refuse(exc) from exc

    typer.echo(f"openkos suggest-volatility: workspace at {root}")
    typer.echo()
    if not outcome.results and outcome.failure is None:
        # Guarded on a clean run only (#441): a first-type failure also
        # carries zero results, and "No concept types found." would then
        # claim an empty bundle the failure, not the walk, produced.
        typer.echo("No concept types found.")
        return

    for result in outcome.results:
        if result.suggested_tier is None:
            typer.echo(f"[?] {result.type_name}")
            typer.echo("  note: no valid tier suggested")
        else:
            typer.echo(f"[{result.suggested_tier}] {result.type_name}")
            typer.echo(f"  rationale: {result.rationale}")
        typer.echo()

    typer.echo("Next: openkos set-volatility <ConceptType> <tier>")

    if outcome.failure is not None:
        # Partial batch (#441): the report above already rendered the
        # completed suggestions exactly as a complete run over that list --
        # the paid-for work is never discarded -- so all that remains is the
        # one stderr failure line and the BackendError-family exit code.
        typer.echo(
            volatility_service.volatility_batch_failure_message(
                outcome.batch, model=outcome.model, cfg=outcome.cfg
            ),
            err=True,
        )
        raise typer.Exit(code=1) from outcome.failure


def _record_identity_decline_from_walk(
    root: Path,
    layout: config.WorkspaceLayout,
    member_ids: Sequence[str],
    *,
    verb: str,
) -> None:
    """Persist a human "keep distinct" ruling observed at a per-item merge
    prompt (#797), fail-open with one stderr advisory.

    ONE helper for both interactive walks -- `adjudicate --apply` here and
    `curate`'s Identity stage -- so the two cannot record the same answer
    differently. Fail-open because the decline already took effect: no
    merge happened, and the worst case is that the group is offered again,
    which is precisely the pre-#797 behaviour. Losing the operator's place
    mid-walk to persist a suppression would be the larger harm."""
    try:
        rel_path = _apply_identity_decision(
            layout, tuple(member_ids), target_state="declined"
        )
        _autocommit(root, [rel_path], f"openkos: keep distinct {'/'.join(member_ids)}")
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos {verb}: warning -- failed to record the keep-distinct "
            f"ruling ({exc}); this group will be offered again.",
            err=True,
        )


def _apply_identity_decision(
    layout: config.WorkspaceLayout,
    member_ids: tuple[str, ...],
    *,
    target_state: bundle_decisions.DecisionState,
) -> str:
    """Write (or update in place) the single identity ruling for `member_ids`
    (#797), returning the workspace-relative path for the caller's
    `_autocommit` list. The write lives in `application/duplicates_service.py`;
    this adapter only chooses how the notes it reads are shown."""
    return duplicates_service.apply_identity_decision(
        layout, member_ids, target_state=target_state, on_warning=_echo_warning
    )


class AdjudicationServes(NamedTuple):
    """What `_partition_adjudication_serves` answers (#779, widened by
    #838). A NamedTuple for `EdgeSuggestionServes`' exact reason: a widening
    positional tuple makes every unpack order-sensitive by convention
    alone, and `rubric_stale` is a count a caller could silently swap with
    nothing but a type checker noticing."""

    served: "dict[str, AdjudicatedCandidate]"
    to_judge: "list[CandidateGroup]"
    rubric_stale: int
    """How many groups re-judge with a persisted row predating the current
    judgment rubric (#838) -- the caller names the reason so the re-spend
    is announced, never a surprising `0 of N served`. Counted whether or
    not the group's members ALSO drifted: the rubric check runs before the
    costlier per-member hashing, and the claim the stderr line makes (the
    row predates the rubric) is true either way."""

    store_read: bool
    """Whether the store was actually READ (#809's fact, adopted here by
    #867 when curate's Identity became this partition's second caller).
    An absent file and an unreadable one both serve nothing, but neither
    is a store that was read and held nothing for these groups -- and the
    split line renders only for the last of the three, on BOTH surfaces,
    so the two can never disagree the way #809 found the edge family's
    had."""


def _reassemble_adjudications(
    candidates: "list[CandidateGroup]",
    served: "dict[str, AdjudicatedCandidate]",
    fresh: "Sequence[AdjudicatedCandidate]",
) -> "list[AdjudicatedCandidate]":
    """Rebuild a run's verdicts in CANDIDATE order, merging what the store
    served with what the model just judged (#867) -- the adjudication twin
    of `_reassemble_edge_suggestions`, extracted from the `adjudicate` verb
    for the same #809 reason: `curate`'s Identity stage is now a second
    caller, and a later fix to the merge would otherwise land in one copy
    and silently miss the other.

    A served verdict wins over a fresh one for the same key -- a tiebreak
    the callers' own partition makes unreachable, not a policy. Groups past
    a mid-batch failure (#441) appear in neither map and stay absent.
    Ordering follows `candidates`, so a served verdict and a fresh one are
    indistinguishable downstream."""
    fresh_by_key = {
        adjudications_store.group_key_for(result.candidate.member_ids): result
        for result in fresh
    }
    rebuilt: list[AdjudicatedCandidate] = []
    for group in candidates:
        key = adjudications_store.group_key_for(group.member_ids)
        found = served.get(key) or fresh_by_key.get(key)
        if found is not None:
            rebuilt.append(found)
    return rebuilt


def _partition_adjudication_serves(
    layout: config.WorkspaceLayout,
    candidates: "list[CandidateGroup]",
    *,
    include_confidential: bool,
    warn_on_failure: bool = True,
    surface: str = "adjudicate",
) -> AdjudicationServes:
    """Split `candidates` into verdicts servable from `.openkos/findings.db`
    and the groups that still need a model call (#779) -- the adjudication
    twin of `_partition_persisted_serves`, same posture throughout.

    A group is SERVED iff its latest persisted row matches this run's
    `include_confidential` bit (a verdict computed over a different member
    subset must never serve), was computed under THIS build's judgment
    rubric (#838: `rubric_digest` equality -- the same argument one input
    wider, since a different rubric is a different prompt; a row carrying
    no digest predates the column and is never servable), carries at
    least one digest row, every stored `(member, digest)` equals the
    member's CURRENT content hash, and the stored verdict is in the enum.
    Everything else re-judges, conservatively -- including a
    present-but-corrupt store, which degrades to one stderr advisory and
    a full fresh judge rather than crashing before any model spend (the
    #685 item-4 posture).

    `rubric_stale` counts the groups refused with a row predating the
    current rubric (mismatched or absent digest) -- whether or not their
    members also drifted, since the rubric gate runs first -- so the
    caller can name the reason: a rubric change re-judging every cached
    group would otherwise read as a surprising `0 of N served` with no
    stated cause (#838's announced-re-spend ruling)."""
    if not layout.findings_db_path.exists():
        return AdjudicationServes({}, candidates, 0, store_read=False)
    try:
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            persisted = adjudications_store.open_adjudications(conn)
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        # `warn_on_failure=False` is curate's pricing probe (#867 review):
        # the stage RUN rebuilds this partition minutes later and warns
        # then, so an unreadable store costs one warning per curate run,
        # not one per read -- the standalone verb partitions once and
        # always warns. `surface` names the command the user actually ran
        # (#867 review): a warning during a curate run must not be
        # attributed to the standalone verb.
        if warn_on_failure:
            typer.echo(
                f"openkos {surface}: warning -- failed to read persisted "
                f"adjudications ({exc}); judging every group fresh.",
                err=True,
            )
        return AdjudicationServes({}, candidates, 0, store_read=False)
    latest: dict[str, adjudications_store.Adjudication] = {}
    for row in persisted:
        latest[adjudications_store.group_key_for(row.member_ids)] = row

    current_digest = application_pending.current_finding_digest(layout.bundle_dir)
    current_rubric = rubric_digest()
    served: dict[str, AdjudicatedCandidate] = {}
    to_judge: list[CandidateGroup] = []
    rubric_stale = 0
    for group in candidates:
        key = adjudications_store.group_key_for(group.member_ids)
        stored = latest.get(key)
        if stored is None or stored.include_confidential != include_confidential:
            to_judge.append(group)
            continue
        # #838: a row from a different (or unknown, pre-column) rubric is
        # a verdict this build would not produce; the operator who
        # upgraded to a judgment fix must not keep being served the exact
        # verdict the fix exists to replace. Checked BEFORE the member
        # digests because it is the cheap comparison -- so a group whose
        # members also drifted still counts here, which stays honest: the
        # row does predate the rubric, whatever else is stale about it.
        if stored.rubric_digest != current_rubric:
            rubric_stale += 1
            to_judge.append(group)
            continue
        # The stored ref SET must equal the group's current member set
        # (#779 review, two lenses): a row carrying digests for fewer or
        # different members than the group now holds was computed over a
        # DIFFERENT prompt, however fresh each stored digest is.
        if {digest.input_ref for digest in stored.input_digests} != set(
            group.member_ids
        ) or any(
            current_digest(digest.input_ref) != digest.digest
            for digest in stored.input_digests
        ):
            to_judge.append(group)
            continue
        try:
            verdict = Verdict(stored.verdict)
        except ValueError:
            to_judge.append(group)
            continue
        # The SAME judgment path a fresh verdict goes through (#796). A
        # row stored before that withdrawal existed still carries the
        # verdict its own rationale argues against, and serving it would
        # hand the operator a merge the engine now refuses to propose.
        # Re-deciding costs nothing here: the rule is pure, reads only the
        # rationale the row already carries, and is idempotent, so a row
        # written after the rule shipped passes through untouched.
        verdict, rationale = withdraw_self_refuting_same(verdict, stored.rationale)
        served[key] = AdjudicatedCandidate(
            candidate=group,
            verdict=verdict,
            confidence=stored.confidence,
            rationale=rationale,
        )
    return AdjudicationServes(served, to_judge, rubric_stale, store_read=True)


EdgeSuggestionServes = relations_service.EdgeSuggestionServes
"""Re-exported for the callers that unpack it by name (curate's Structure
stage). The definition lives in `application/suggest_relations_service.py`,
beside the partition it answers."""


def _reassemble_edge_suggestions(
    edges: "list[Edge]",
    served: "dict[str, EdgeSuggestion]",
    fresh: "Sequence[EdgeSuggestion]",
) -> "list[EdgeSuggestion]":
    """One-line delegator (issue #1168): the merge lives in
    `application/suggest_relations_service.py`, shared by `suggest-relations`
    and `curate`'s Structure stage, and is kept under this name for the
    callers that reach it through `cli.main`."""
    return relations_service.reassemble_edge_suggestions(edges, served, fresh)


def _partition_edge_suggestion_serves(
    layout: config.WorkspaceLayout,
    edges: "list[Edge]",
    *,
    include_confidential: bool,
    warn_on_failure: bool = True,
    surface: str = "suggest-relations",
) -> EdgeSuggestionServes:
    """Delegator to `relations_service.partition_edge_suggestion_serves`
    (issue #1168), which owns the serve rule (#799, #809). This wrapper only
    renders the service's advisory, to stderr.

    `warn_on_failure=False` is curate's pricing probe (#867 review): the stage
    RUN rebuilds this partition minutes later and warns then, so an unreadable
    store costs one warning per curate run, not one per read -- the standalone
    verb partitions once and always warns. `surface` names the command the user
    actually ran, so a warning during a curate run is not attributed to the
    standalone verb."""
    return relations_service.partition_edge_suggestion_serves(
        layout,
        edges,
        include_confidential=include_confidential,
        on_warning=_echo_stderr if warn_on_failure else None,
        surface=surface,
    )


def _persist_edge_suggestions(
    layout: config.WorkspaceLayout,
    results: "Sequence[EdgeSuggestion]",
    *,
    include_confidential: bool,
    surface: str = "suggest-relations",
) -> None:
    """Delegator to `relations_service.persist_edge_suggestions` (issue
    #1168), which owns the persist rule (#799). This wrapper only renders the
    service's advisory, to stderr; `surface` names the command the user
    actually ran (#867 review), since curate's Structure stage persists
    through this helper too."""
    relations_service.persist_edge_suggestions(
        layout,
        results,
        include_confidential=include_confidential,
        on_warning=_echo_stderr,
        surface=surface,
    )


def _echo_stderr(message: str) -> None:
    typer.echo(message, err=True)


def _persist_adjudications(
    layout: config.WorkspaceLayout,
    results: "Sequence[AdjudicatedCandidate]",
    *,
    include_confidential: bool,
    surface: str = "adjudicate",
    judged_digests: "Mapping[str, str | None] | None" = None,
) -> None:
    """Persist freshly judged adjudication verdicts (#779), fail-open: a
    failed persist costs one stderr advisory, never the run -- the same
    #684 posture curate's findings persist takes. A result any of whose
    members has no current digest (unreadable -- including the
    no-readable-member UNCERTAIN short-circuit) is skipped: a row whose
    staleness can never be checked would serve forever.

    `judged_digests` is each member's content digest as it stood BEFORE the
    judging call (#1137): the judging call holds no workspace lock, so a
    member edited, raised or forgotten meanwhile has a different digest now,
    and its verdict is dropped rather than stored against content nobody
    judged. Without it (curate, which holds the lock throughout) the digests
    are read here, as before.

    The persist is a commit phase: it takes the workspace lock, and a busy
    workspace costs the same advisory a failed persist does."""
    if not results:
        return
    try:
        # A whole-lock caller (curate) publishes no section and already holds
        # the lock, so only a split verb's published section is entered.
        published = _COMMIT_SECTION.get()
        with published() if published is not None else nullcontext():
            current_digest = application_pending.current_finding_digest(
                layout.bundle_dir
            )
            # #838: every fresh verdict records the rubric it was computed under,
            # so the serve gate can refuse it after a judgment fix ships. Computed
            # once -- it is constant within a build.
            current_rubric = rubric_digest()
            batch: list[adjudications_store.Adjudication] = []
            for result in results:
                digests: list[adjudications_store.InputDigest] = []
                for member_id in result.candidate.member_ids:
                    digest = current_digest(member_id)
                    if digest is None or (
                        judged_digests is not None
                        and judged_digests.get(member_id) != digest
                    ):
                        break
                    digests.append(
                        adjudications_store.InputDigest(
                            input_ref=member_id, digest=digest
                        )
                    )
                else:
                    batch.append(
                        adjudications_store.Adjudication(
                            member_ids=tuple(result.candidate.member_ids),
                            verdict=result.verdict.value,
                            confidence=result.confidence,
                            rationale=result.rationale,
                            include_confidential=include_confidential,
                            input_digests=tuple(digests),
                            rubric_digest=current_rubric,
                        )
                    )
            if not batch:
                return
            try:
                conn = derived.open_derived_connection(layout.findings_db_path)
                try:
                    adjudications_store.record_adjudications(conn, batch)
                finally:
                    conn.close()
            except (OSError, sqlite3.Error) as exc:
                # `surface` names the command the user actually ran (#867 review):
                # curate's Identity stage persists through this helper too.
                typer.echo(
                    f"openkos {surface}: warning -- failed to persist adjudication "
                    f"verdicts ({exc}); the next run will re-judge them.",
                    err=True,
                )
    except lock.WorkspaceBusyError as exc:
        typer.echo(
            f"openkos {surface}: warning -- the workspace is busy, so the "
            f"adjudication verdicts were not persisted ({exc}); the next run "
            "will re-judge them.",
            err=True,
        )


# `_contradiction_finding_counts` moved verbatim into `application/status.py`
# as the public `contradiction_finding_counts` (issue #995, PR 3); `status`
# is its only caller, via `application_status.contradiction_finding_counts`.


def _echo_declined_finding(
    record: bundle_decisions.DecisionRecord,
    finding: "findings.PersistedFinding | None",
) -> None:
    """One `--declined` listing entry (pending-work spec: "The declined-
    listing view surfaces it"; Scenario "A stale finding remains visible as
    stale" -- `[stale]` is appended whenever a matching persisted finding
    is stale, never silently omitted)."""
    stale_suffix = " [stale]" if finding is not None and finding.stale else ""
    if record.merged_absorbed_id is not None:
        survivor_id, _ = record.pair_ids
        typer.echo(
            f"[DECLINED] {survivor_id} (merged content, absorbed "
            f"{record.merged_absorbed_id}){stale_suffix}"
        )
    else:
        source_id, target_id = record.pair_ids
        typer.echo(f"[DECLINED] {source_id} <-> {target_id}{stale_suffix}")
    if finding is not None:
        typer.echo(
            f"  verdict: {finding.verdict} (confidence: {finding.confidence:.2f})"
        )
        typer.echo(f"  rationale: {finding.rationale}")
    else:
        typer.echo(
            "  (no persisted finding on record -- it may have been purged "
            "or never recomputed)"
        )
    typer.echo()


def render_contradiction_header(result: ContradictionVerdict) -> None:
    """Echo one contradiction verdict's HEADER, plus the `unmerge` remedy
    when the verdict is an intra-document (merged-body) one (#409/#445).

    Shared by `contradictions` and `curate`'s Contradictions stage because
    they DRIFTED (#883): `curate` rendered every verdict from `pair_ids`,
    so a merged-body verdict came out as the `A <-> A` self-pair, losing
    both the absorbed id and the only command that resolves the finding --
    on the path `openkos next` actually recommends. A comment claiming the
    two echo paths "cannot drift apart" sat in `curate.py` while they had.
    One function is the fix; two call sites reading the same field are not.

    `merged_absorbed_id` is the SOLE discriminator between a typed-edge and
    a merged-body verdict (`ContradictionVerdict.merged_absorbed_id`'s
    docstring WARNING). NEVER branch on `pair_ids` equality: a `(x, x)`
    typed self-loop is separately reachable (#411), so pair shape conflates
    the two.

    Claims and rationale stay with each caller: `contradictions` closes its
    entry with a blank line and `curate` does not, and folding that
    difference in here would change one command's output to share code with
    the other."""
    if result.merged_absorbed_id is not None:
        survivor_id, _ = result.pair_ids
        typer.echo(
            f"[{result.verdict.value.upper()}] {survivor_id} "
            f"(merged content, absorbed {result.merged_absorbed_id}) "
            f"(confidence: {result.confidence:.2f})"
        )
        # Name the verb that resolves the condition (#445, same shape as
        # #386's advisory ladder). A pair verdict needs no pointer: the
        # operator can open both files. A merged-content verdict has ONE
        # node, and the disagreeing second body lives in the ledger where
        # no ordinary read will surface it -- `unmerge` is the only verb
        # that separates them, and it takes exactly these two ids.
        #
        # The LIFO qualifier is not decoration (review finding on this
        # change): `resolution.contradiction` raises ONE candidate PER
        # `merged_from` entry, not just the newest, while
        # `bundle.merge.plan_unmerge` refuses any `absorbed_id` that is
        # not the ledger's tail. On a survivor with two unreversed
        # merges, the older verdict's command therefore REFUSES -- so the
        # line states the precondition instead of promising success.
        typer.echo(
            f"  next: openkos unmerge {survivor_id} {result.merged_absorbed_id}"
            " (LIFO-enforced: refuses unless this is the survivor's "
            "most recent unreversed merge)"
        )
    else:
        source_id, target_id = result.pair_ids
        typer.echo(
            f"[{result.verdict.value.upper()}] {source_id} <-> {target_id} "
            f"(confidence: {result.confidence:.2f})"
        )


class _CliContradictionsObserver(contradictions_service.ContradictionsObserver):
    """Renders the advisories a `contradictions` run raises before any verdict
    exists; everything the report shows comes back in the outcome."""

    def __init__(
        self,
        layout: config.WorkspaceLayout,
        *,
        include_confidential: bool,
    ) -> None:
        self._layout = layout
        self._include_confidential = include_confidential

    def exemption_resolved(self, local_exemption: bool) -> None:
        observability.warn_if_walk_incomplete(
            self._layout.bundle_dir,
            include_confidential=self._include_confidential,
            local_exemption=local_exemption,
        )

    def vacuous_coverage(self, notice: str) -> None:
        typer.echo(f"openkos contradictions: {notice}", err=True)

    def persisted_findings_unreadable(self, error: Exception) -> None:
        typer.echo(
            "openkos contradictions: warning -- failed to read persisted "
            f"findings ({error}); judging every candidate fresh.",
            err=True,
        )

    def progress_callback(self) -> Callable[[int, int, object], None] | None:
        # TTY-gated per-pair progress on stderr; `None` (silent) when output is
        # piped (issue #190, mirrors `suggest-relations`' #134 per-edge line).
        return observability.progress_callback("contradictions", "checking pair")

    def persist_failed(self, error: Exception) -> None:
        typer.echo(
            "openkos contradictions: warning -- failed to persist "
            f"findings ({error}); this run's verdicts are shown below "
            "but will not be served from the store on a later run.",
            err=True,
        )


def _record_contradiction_decision(
    root: Path,
    pair: tuple[str, str],
    merged_absorbed_id: str | None,
    *,
    target_state: bundle_decisions.DecisionState,
) -> None:
    """`contradictions --decline`/`--reopen`: persist one decision through the
    service, say so, then stage ONLY its sidecar."""
    ruling = contradictions_service.record_contradiction_decision(
        root,
        pair,
        merged_absorbed_id,
        target_state=target_state,
        on_warning=_echo_warning,
    )
    pair_a, pair_b = ruling.pair
    verb, label = (
        ("declined", "decline")
        if target_state == "declined"
        else ("reopened", "reopen")
    )
    typer.echo(
        f"openkos contradictions: {verb} {pair_a} <-> {pair_b}"
        + (
            f" (merged content, absorbed {merged_absorbed_id})"
            if merged_absorbed_id is not None
            else ""
        )
        + "."
    )
    _autocommit(
        root,
        [ruling.rel_path],
        f"openkos: {label} contradiction {pair_a}/{pair_b}",
    )


def _contradictions_declined_view(root: Path) -> None:
    """`contradictions --declined`: every declined record, joined to its
    persisted finding. Short-circuits before the graph build and the LLM client
    -- every record comes from the decisions sidecars, never from a fresh
    judgment."""
    entries = contradictions_service.list_declined(root)
    typer.echo(f"openkos contradictions --declined: workspace at {root}")
    typer.echo()
    if not entries:
        typer.echo("No declined findings.")
        return
    for entry in entries:
        _echo_declined_finding(entry.record, entry.finding)


def _run_contradictions_report(
    root: Path, options: contradictions_service.ContradictionsOptions
) -> None:
    """The judged `contradictions` run: call the service, then render its
    outcome (and the partial-batch failure, which is the only non-zero exit
    that still prints a report)."""
    layout = config.WorkspaceLayout(root)
    ports = contradictions_service.ContradictionsPorts(
        chat_client=lambda cfg: _chat_client(cfg, task="contradiction"),
        local_exemption=_resolve_local_exemption,
        open_proximity=_open_proximity_or_degrade,
        build_graph=build_graph,
        plan_candidates=plan_candidates,
        find_contradictions=find_contradictions,
        persist_findings=curate_module.persist_findings,
        finding_input_digests=curate_module.finding_input_digests,
        zero_state_message=lambda ws_layout, store, embeddings_missing: (
            _zero_edge_state_message(
                ws_layout,
                store=store,
                use_typed_count=True,
                embeddings_missing=embeddings_missing,
                none_survived=(
                    "{count} typed relation(s); none are contradiction candidates."
                ),
            )
        ),
    )
    outcome = contradictions_service.run_contradictions(
        root,
        options=options,
        ports=ports,
        observer=_CliContradictionsObserver(
            layout, include_confidential=options.include_confidential
        ),
    )

    typer.echo(f"openkos contradictions: workspace at {root}")
    typer.echo()
    if not options.fresh and outcome.plan.specs:
        # #685 item 6: the judged-fresh count is what actually HAPPENED
        # (`batch.results`), never the planned `judged_plan.specs` -- on a
        # partial batch the two differ, and printing the plan here overstated
        # the spend the stderr epilogue then contradicted.
        typer.echo(
            f"{outcome.served_count} of {len(outcome.plan.specs)} candidate(s) "
            "served from persisted findings; "
            f"{outcome.fresh_count} judged fresh."
        )
        typer.echo()
    if outcome.candidate_notice is not None:
        typer.echo(outcome.candidate_notice)
        typer.echo()
    if outcome.quarantine_notice is not None:
        typer.echo(outcome.quarantine_notice)
        typer.echo()
    if outcome.zero_state is not None:
        typer.echo(outcome.zero_state)
        return

    if outcome.truncation_notice is not None:
        typer.echo(outcome.truncation_notice)
        typer.echo()
    if not outcome.displayed:
        # No early return (#441): the partial-batch failure epilogue below must
        # run after every display path, exactly as in `adjudicate`.
        # Vacuous-coverage guard (#557): a clean line over a run that judged
        # zero typed-edge pairs must not read as an all-clear -- stderr may be
        # discarded (piped runs), so the qualification rides the stdout line
        # itself.
        if outcome.vacuous_notice is not None:
            typer.echo(
                "No high-confidence contradictions found -- NOT an "
                "all-clear: zero typed-edge pairs were judged (the graph "
                "has no applied relations)."
            )
        else:
            typer.echo("No high-confidence contradictions found.")

    for result in outcome.displayed:
        render_contradiction_header(result)
        for claim in result.conflicting_claims:
            typer.echo(f"  - {claim}")
        typer.echo(f"  rationale: {result.rationale}")
        typer.echo()

    if outcome.batch.failure is not None:
        # Partial batch (#441): the report above already rendered the completed
        # verdicts exactly as a complete run over that list -- the paid-for work
        # is never discarded -- so all that remains is the one stderr failure
        # line and the BackendError-family exit code. #653: the
        # completed-of-total counts describe what was actually SENT to the model
        # this run -- the judged subset, not the full plan.
        _echo_contradictions_batch_failure(
            outcome.batch,
            total=outcome.judged_plan.llm_calls,
            model=outcome.model,
        )
        raise typer.Exit(code=1) from outcome.batch.failure


@app.command(
    help=(
        "Report concepts whose content disagrees, using the model to judge "
        "already-related pairs. Advisory only; writes nothing."
    ),
    rich_help_panel="Explore",
)
@_guard_workspace_lock("contradictions")
def contradictions(
    show_all: bool = typer.Option(
        False,
        "--all",
        help="Show every verdict (CONTRADICTS, CONSISTENT, UNCERTAIN) "
        "regardless of confidence.",
    ),
    include_deprecated: bool = typer.Option(
        False,
        "--include-deprecated",
        help="Include deprecated and superseded concepts (excluded by default).",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
    decline: tuple[str, str] | None = typer.Option(
        None,
        "--decline",
        metavar="PAIR_ID_A PAIR_ID_B",
        help="Decline the contradiction finding for this concept pair "
        "(sorted internally), hiding it from ordinary output. Succeeds "
        "even with no matching findings row. Combine with "
        "--merged-absorbed-id for a merged-body candidate.",
    ),
    reopen: tuple[str, str] | None = typer.Option(
        None,
        "--reopen",
        metavar="PAIR_ID_A PAIR_ID_B",
        help="Reopen a previously declined finding for this concept pair, "
        "restoring its ranking eligibility. Combine with "
        "--merged-absorbed-id for a merged-body candidate.",
    ),
    merged_absorbed_id: str | None = typer.Option(
        None,
        "--merged-absorbed-id",
        help="With --decline/--reopen: the absorbed concept id that "
        "distinguishes a merged-body candidate from a typed-edge candidate "
        "over the same pair.",
    ),
    declined: bool = typer.Option(
        False,
        "--declined",
        help="List every declined finding instead of running LLM "
        "contradiction detection.",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Re-judge every candidate pair with the model. By default, a "
        "pair whose persisted finding is digest-fresh is served from "
        ".openkos/findings.db without a model call (#653).",
    ),
) -> None:
    """LLM-detect contradictions between already-related concepts: read-only,
    like `adjudicate`/`suggest-relations`/`suggest-volatility`.

    A SEVENTH read command, mirroring `suggest-relations`'s wiring exactly:
    the shared `config.require_workspace` gate (D1), then a Phase-A
    `read_config` guard (`except (OSError, ValueError)`, lint parity), then
    a real `OllamaClient(model=cfg.model)` is built and injected -- as the
    `LLMBackend` -- into `resolution.contradiction.find_contradictions`,
    which owns the candidate-narrowing logic. This command builds the graph
    projection ONCE per invocation via `graph.sqlite_graph.build_graph` and
    threads the open store into every reader it calls, including the
    zero-result `_zero_edge_state_message` path that used to trigger a
    second full build (#196). Holding an open `openkos.graph` store here is
    established practice (`query`, `reindex`); the live layering rule
    forbids only canonical-layer imports of `openkos.graph` and a `graph`
    CLI verb.

    `contradictions` never writes the BUNDLE -- no merge, no reconcile, no
    file under `bundle/` is ever touched; it prints a verdict, confidence,
    rationale, and cited conflicting claims per candidate pair for human
    review. No `--auto`, no confirmation gate, no `--json` or other
    structured mode. Since #653 it DOES persist its freshly judged
    verdicts to `.openkos/findings.db`, through `cli.curate`'s exact
    `persist_findings` write path -- the same "persisting a finding is not
    a bundle write" carve-out curate's Contradictions stage already holds.

    Serving before judging (#653): by default, a candidate pair whose
    LATEST persisted finding carries input digests exactly matching the
    pair's current bytes (`cli.curate.finding_input_digests`, the function
    that recorded them) is served from the store with NO model call --
    `consistent` rows included, since they are precisely what proves a
    pair needs no re-judging. New pairs, digest-drifted pairs, unreadable
    inputs, and unrecognized stored verdicts re-judge conservatively; a
    "N of M candidate(s) served" line reports the split. `--fresh`
    bypasses the store and re-judges everything (and re-persists, so the
    store converges to the newest verdicts either way).

    By DEFAULT only high-confidence `CONTRADICTS` verdicts are shown
    (`is_high_confidence_contradiction`); `CONSISTENT` and `UNCERTAIN`, and
    low-confidence `CONTRADICTS`, are hidden (spec: Default view hides
    CONSISTENT/UNCERTAIN). `--all` is a DISPLAY-only filter: it reveals
    every verdict -- served or freshly judged -- regardless of type or
    confidence, and never changes which pairs are judged (spec: `--all`
    Reveals Every Verdict).

    A candidate set truncated by the engine leaf's pair cap is reported as
    an explicit "N of M pairs shown (cap reached)" line -- never silent
    (spec: Pair Cap With Explicit Truncation Notice). A bundle with zero
    candidate pairs prints a clear "No candidate pairs found." line and
    exits 0 without ever calling `llm.chat` (spec: Empty Graph Yields Clear
    Message, No Crash).

    A no-model/no-Ollama failure comes back INSIDE the returned
    `ContradictionBatch` (#441) and maps onto the SAME 3-tier ORDERED
    wording `suggest-relations`/`adjudicate`/`query` use --
    `BackendUnavailable`, then `BackendModelNotFound`, then the generic
    `BackendError` fallback -- each with its own actionable stderr message
    and exit 1. The completed verdicts are NEVER discarded: the report
    first renders `batch.results` exactly as a complete run over that list
    (the `--all`/high-confidence display filter included), THEN one stderr
    line reports the failure with completed-of-total counts -- the total is
    `plan.llm_calls`, the same number the truncation notice describes --
    and the run exits 1. The raise-path handler ladder is retained around
    the call itself for an injected backend that raises outside `llm.chat`'s
    guarded seam -- same wording, no counts, zero writes either way.

    Unless `--include-deprecated` is passed, deprecated/superseded concepts
    (status-aware-retrieval) never appear in a candidate pair -- dropped by
    `find_contradictions` before any pair is judged, so the LLM is never
    invoked on them.

    Unless `--include-confidential` is passed, confidential concepts
    (sensitivity-fail-closed-filter) likewise never appear in a candidate
    pair, dropped by `find_contradictions` the same way.

    No file under the workspace is ever created, modified, or deleted
    (spec: Read-Only `contradictions` CLI Verb).

    `--decline`/`--reopen`/`--declined` (pending-work design, PR #3/Slice
    B2) are the three write/list verbs this command additionally exposes,
    each short-circuiting BEFORE the graph build and the `OllamaClient`
    (design File changes table): they write or read
    `bundle/.state/decisions/**` only, never build a graph projection, and
    never call `llm.chat`. An ordinary judged run additionally hides any
    verdict whose `decision_key` (sorted `pair_ids` + `merged_absorbed_id`,
    Decision 3) already carries a `declined` decision -- the SAME identity
    `--decline`/`--reopen` key on -- from the `--all`/high-confidence
    display filter (pending-work spec: "Declined Findings Are Hidden By
    Default").
    """
    root = Path.cwd()
    try:
        if decline is not None:
            _record_contradiction_decision(
                root, decline, merged_absorbed_id, target_state="declined"
            )
            return
        if reopen is not None:
            _record_contradiction_decision(
                root, reopen, merged_absorbed_id, target_state="open"
            )
            return
        if declined:
            _contradictions_declined_view(root)
            return
        _run_contradictions_report(
            root,
            contradictions_service.ContradictionsOptions(
                show_all=show_all,
                include_deprecated=include_deprecated,
                include_confidential=include_confidential,
                fresh=fresh,
            ),
        )
    except contradictions_service.ContradictionsRefused as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(code=1) from exc


class _RevisionsObserver:
    """The CLI's rendering of a `revisions` run (issue #1168): the service
    hands it typed data and this class owns every word and the TTY question."""

    def started(self) -> None:
        typer.echo(revisions_service.EXPERIMENTAL_NOTICE, err=True)

    def truncation_notice(self, notice: str) -> None:
        typer.echo(notice, err=True)

    def cost_gate(self, plan: revisions_service.RevisionPlan) -> None:
        typer.echo(
            f"{len(plan.candidate_plan.candidates)} candidate pair(s), "
            f"{len(plan.served)} served -> {len(plan.to_judge)} LLM "
            "call(s) to judge (this can take a while). Pass --auto to "
            "skip this prompt.",
            err=True,
        )

    def confirm_judging(self) -> revisions_service.ConfirmationAnswer:
        # The same three branches every other cost gate in this CLI has:
        # a TTY asks, and non-TTY stdin without `--auto` cannot ask.
        if not sys.stdin.isatty():
            return "unavailable"
        return "proceed" if typer.confirm("Proceed?") else "declined"

    def progress_callback(
        self,
    ) -> Callable[[int, int, RevisionVerdict], None] | None:
        return observability.progress_callback("revisions", "judging pair")


@app.command(
    help=(
        "\\[experimental] Find Decisions that a later Decision reverses, "
        "refines or reaffirms. Suggestions only; writes nothing to the "
        "bundle."
    ),
    rich_help_panel="Explore",
)
@_guard_workspace_lock("revisions")
def revisions(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the pair-judgment confirmation prompt.",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential Decisions (excluded by default). "
        "Releases the judge's chat send only, never an embedding call.",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Re-judge every candidate pair with the model, bypassing "
        "persisted findings.",
    ),
    show_all: bool = typer.Option(
        False,
        "--all",
        help="Show every verdict, including REAFFIRMS/UNRELATED, "
        "low-confidence, and malformed results.",
    ),
) -> None:
    """[EXPERIMENTAL] LLM-detect Decisions that a later Decision reverses,
    refines, or reaffirms (#1014 piece (a), Phase B re-plan): read-only over
    the bundle, like `contradictions`/`suggest-relations`.

    A thin adapter over `application.revisions.run_revisions` (issue #1168):
    the service owns the workspace gate, the planning, the ONE cost gate's
    WHEN and the judging; this verb keeps the rendering, the TTY question and
    the exit-code mapping.

    Candidate pairs are blocked by embedding similarity over each eligible
    Decision's document vector, read directly from `.openkos/vectors.db`
    (`application.revisions.read_decision_vectors`) -- this verb makes NO
    embedding call, ever (design.md Decision B1). A whole-run degrade (the
    store is absent or empty, or its stored embedding-model tag does not
    match the currently configured one) prints one remedy line naming
    `openkos reindex` and exits 0 -- nothing failed; the store is simply
    not built for the current model.

    Persisted revision findings are served from `.openkos/findings.db`
    (design.md Decision 2) with no judge call when a candidate pair's input
    digests -- both Decisions' bodies and their reached Sources -- are
    unchanged since it was last judged; `--fresh` bypasses that serve and
    re-judges everything. The ONE remaining cost gate (design.md's Phase B
    re-plan, Decision B4 -- the original subject-derivation pass and its own
    gate were dropped entirely, Decision B3) fires only when there is at
    least one candidate pair left to judge, prints the exact judge-call
    count computed with zero LLM calls and zero embedding calls, and has
    the same three branches every other cost gate in this CLI has:
    `--auto` proceeds, a TTY asks and a decline exits 0 with nothing
    judged, and non-TTY stdin without `--auto` refuses (exit 1).

    `revisions` writes ONLY `.openkos/findings.db` -- never a file under
    `bundle/`, and never any other derived store. A `REAFFIRMS` or
    `UNRELATED` verdict is persisted as a finding but writes no relation to
    either Decision's document; only `openkos reconcile --from-findings`
    (a later slice) ever writes a relation from a revision finding.

    A partial batch (a mid-run `BackendError`) renders every verdict judged
    so far exactly as a complete run over that list would, then reports the
    failure and exits 1 -- the same #441 posture `contradictions`/
    `suggest-relations` already follow; already-judged, non-malformed
    verdicts are persisted regardless of the failure.

    Detection quality is UNMEASURED on real bundles -- only a synthetic
    harness fixture has been measured (`evals/decision_revisions/`) -- so
    one stderr line says so on every invocation, and every finding should
    be reviewed before it is applied.
    """
    try:
        run = revisions_service.run_revisions(
            Path.cwd(),
            revisions_service.RevisionsRequest(
                skip_confirmation=auto,
                include_confidential=include_confidential,
                fresh=fresh,
            ),
            revisions_service.RevisionsPorts(
                chat_client=lambda cfg, task: _chat_client(cfg, task=task),
                resolve_local_exemption=lambda client, cfg: _resolve_local_exemption(
                    client, cfg
                ),
                truncation_notice=lambda candidate_plan: revision_truncation_notice(
                    candidate_plan
                ),
            ),
            _RevisionsObserver(),
        )
    except revisions_service.RevisionsRefused as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(code=1) from exc

    if run.status == "no_decisions":
        typer.echo("No Decision objects found.")
        return
    if run.status == "vectors_absent":
        typer.echo(revisions_service.NO_VECTORS_MESSAGE, err=True)
        return
    if run.status == "model_mismatch":
        typer.echo(revisions_service.MODEL_MISMATCH_MESSAGE, err=True)
        return
    if run.status == "declined":
        typer.echo("Aborted -- no revisions judged.")
        return

    report = run.report
    if report is None:
        return
    typer.echo(
        revisions_report(
            report.plan,
            report.outcome,
            excluded=report.decisions.bad_relations,
            show_all=show_all,
        )
    )

    if report.outcome.failure is not None:
        # Partial batch (#441 posture, mirrored from `contradictions`): the
        # report above already rendered every verdict judged so far exactly
        # as a complete run over that list would -- the paid-for work is
        # never discarded -- so all that remains is the one stderr failure
        # line and the BackendError-family exit code.
        typer.echo(
            revisions_service.revisions_batch_failure_message(
                report.outcome,
                total=len(report.plan.to_judge),
                model=run.model,
                cfg=run.cfg,
            ),
            err=True,
        )
        raise typer.Exit(code=1) from report.outcome.failure


def _no_match_message(cause: NoMatchCause, fts_hit_count: int) -> str:
    """Map `AnswerResult.no_match_cause` to an actionable STDOUT message,
    distinguishing the three causes `query` must not conflate: nothing
    matched, matches existed but were unreadable, or no question was asked.

    Only the three real no-match causes are expected here; the caller guards
    against `"none"`. An unhandled cause raises rather than silently falling
    through to a misleading message, so a future `NoMatchCause` value fails
    loudly instead of rendering the wrong text."""
    if cause == "zero_hits":
        return (
            f"{NO_MATCH} Try different wording, or run `openkos status` "
            "to see what the bundle contains."
        )
    if cause == "all_unreadable":
        return (
            f"Found {fts_hit_count} matching concept{_plural(fts_hit_count)}, "
            "but none could be read from the compiled bundle — it may be "
            "corrupted. Run `openkos lint` to check bundle health."
        )
    if cause == "empty_query":
        return (
            "No question was provided. Pass a question to answer, e.g. "
            'openkos query "what is stoicism?".'
        )
    if cause == "insufficient_context":
        # Deliberately NOT the `zero_hits` wording. Retrieval succeeded here:
        # concepts were found and read, and then judged unable to answer. A
        # user told to "try different wording" would rephrase a question the
        # bundle simply does not cover, which is the wrong instruction and
        # the reason #760 keeps this cause separate.
        return (
            f"Found {fts_hit_count} matching concept{_plural(fts_hit_count)}, "
            "but none of them answers this question — the compiled bundle "
            "does not cover it. Answering anyway would be the model's own "
            "knowledge wearing the bundle's citations. Ingest a source that "
            "covers it, or set `sufficiency_check: false` in openkos.yaml to "
            "answer regardless."
        )
    raise ValueError(f"unexpected no_match_cause: {cause!r}")


@app.command(
    help=(
        "Answer a natural-language question from the bundle, with citations "
        "back to the documents the answer came from."
    ),
    rich_help_panel="Explore",
)
@_guard_workspace_lock("query", commit_phase=True)
def query(
    question: str = typer.Argument(
        ..., help="Natural-language question to answer from the bundle."
    ),
    limit: int = typer.Option(
        5, "--limit", help="Max concepts to retrieve as context."
    ),
    include_deprecated: bool = typer.Option(
        False,
        "--include-deprecated",
        help="Include deprecated and superseded concepts (excluded by default).",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
    save: bool = typer.Option(
        False,
        "--save",
        help=(
            "File the cited answer back as an Insight -- the filed-synthesis "
            "type (opt-in; off by default keeps query read-only)."
        ),
    ),
    title: str | None = typer.Option(
        None,
        "--title",
        help="Title for the filed document (default: a declarative title "
        "derived from the answer's first sentence, falling back to the "
        "question).",
    ),
    description: str | None = typer.Option(
        None,
        "--description",
        help="Description for the filed document (default: the question).",
    ),
    save_type: str = typer.Option(
        _INSIGHT_TYPE,
        "--type",
        help="Type for the filed document (default: Insight -- the filed-"
        "synthesis type; classifiable types remain accepted).",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="With --save, skip the confirmation prompt and write immediately.",
    ),
    allow_unattributed: bool = typer.Option(
        False,
        "--allow-unattributed",
        help=(
            "With --save, file an answer whose citation list is the "
            "retrieval fallback (attribution absent or unparsed) without "
            "the #774 gate -- the citations become provenance without the "
            "model ever accounting for them."
        ),
    ),
) -> None:
    """Answer a natural-language question from the compiled bundle, with citations.

    Read-only WITHOUT `--save`, like `status` and `lint`: no writes, no
    confirmation prompt, no `--auto`. `--save` is the sole exception and
    brings all three with it (see below). Must be run inside an initialized
    workspace; outside one it refuses (exit 1) with a short reason on
    stderr. Retrieval fuses TWO lists: lexical (FTS5) hits and dense
    (`vectors.db`) hits -- both read PERSISTED, read-only on-disk indexes
    under `.openkos/` (`fts.db`, `vectors.db`) that `reindex` maintains,
    rather than rebuilding anything in-process per call (Slice 5, PR3).
    `query` never WRITES to either derived store -- an absent or
    unavailable/corrupt store degrades cleanly (FTS falls back to dense-only;
    dense falls back to FTS-only), never creating or repairing one; only
    `reindex` writes.

    There was a THIRD list until issue #434: a second-stage seeded
    personalized-PageRank pool read from `.openkos/graph.db`. It is gone
    from retrieval because centrality is not relevance -- it repeatedly
    seated the corpus's most central concept at the cost of a real hit, once
    evicting the very document that answered the question. `graph.db` is
    still built by `reindex` and still backs contradiction candidates;
    `query` simply no longer opens it.

    Every completed run (successful answer or no-match) prints a one-line
    `retrieval:` summary to STDERR reporting the raw FTS hit count, the raw
    dense hit count, the fused count, whether the LLM was invoked, and how
    many sources were cited -- so a silent short-circuit (e.g. zero hits, so
    the LLM never ran) is always visible, even though STDOUT stays
    pipe-clean. When either derived index is absent or unavailable/corrupt
    (FTS or dense), an additional stderr line hints at running
    `openkos reindex` to enable full retrieval -- `query` itself never
    recomputes or compares the bundle's manifest hash to reach this
    decision; staleness detection is `reindex`'s exclusive job (D2). When
    the FTS index build skipped any unreadable/unparseable files (at the
    LAST `reindex` run), an `index:` skip-notice block follows the summary
    on stderr, worded as a whole-bundle build diagnostic -- it never implies
    the skipped files were candidates for THIS query's match.

    On a successful answer, STDOUT carries exactly the answer text, then
    (only when at least one concept was cited) a blank line, `Citations:`,
    and one `  → {concept_id} ({title})` line per citation, in the order
    they were used -- unchanged from prior behavior. When nothing in the
    bundle matches, STDOUT instead carries a cause-specific message (zero
    hits, hits found but all unreadable, or an empty/whitespace question)
    and the command still exits 0 -- "no answer found" is a valid result,
    not an error.

    Use `--limit` to cap how many concepts are retrieved as context
    (default 5). Answering needs a local Ollama server running the model
    configured in `openkos.yaml`. A workspace/config problem, an unreachable
    Ollama, or an unusable search index is reported on stderr with no
    traceback and exits 1.

    Unless `--include-deprecated` is passed, deprecated/superseded concepts
    (status-aware-retrieval) are excluded from both retrieval channels
    (lexical, dense) BEFORE fusion -- the `retrieval:` stderr summary and
    every count in it (FTS/dense/fused/cited) already report the POST-filter
    values, since filtering happens inside `answer()` before those counts
    are captured.

    Unless `--include-confidential` is passed, confidential concepts
    (sensitivity-fail-closed-filter) are likewise excluded from every
    retrieval channel before fusion, exactly like a deprecated concept.

    `--save` files the just-printed cited answer as a new `Insight` -- the
    filed-synthesis type (issue #570): a declarative title derived from the
    answer's first sentence (never the interrogative question, which stays
    as the description), under `bundle/insights/`, with provenance to every
    cited concept so the freshness machinery can flag it when they change.
    `--type` still accepts any classifiable type. It is the only writing
    path here. It previews the three paths it would touch,
    then gates on the usual precedence: `--auto` skips the prompt outright;
    otherwise config `review: false` skips it the same way; otherwise a TTY
    prompts via `typer.confirm`; otherwise it refuses (exit 1).

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads `index.md` and `log.md` and refuses
    the WHOLE run (exit 3, nothing written) if either changed or vanished
    since Phase A read it (issues #306, #313, #319). The answer document
    itself is written create-only (`fsio.write_exclusive`), which already
    fails closed if something appeared at that path meanwhile, so it needs
    no entry in the guard and has no Phase-A bytes to give one.

    Phase B is NOT transactional, matching `ingest`/`set-sensitivity`'s
    documented limitation (#331): three writes -- the answer document, then
    `index.md`, then `log.md` -- with no rollback across the sequence.
    Content-before-catalog ordering is why a partial result is benign: the
    catalog never references a missing file; the worst partial state is an
    uncataloged answer document (or a filed-and-indexed one missing only
    its `log.md` line). A mid-sequence failure names exactly the paths that
    already landed ("Already written (left partially filed, not rolled
    back): ..."), or "No path was written." when the first write failed --
    so the operator knows which state they are in without diffing. On
    success the three paths are auto-committed like every other mutating
    verb (workspace-autocommit; the previous exclusion had no documented
    rationale, #331); a PARTIAL result is not captured in a commit, since
    `_autocommit` runs only on the success path -- recover a partial with
    `git status`/`git checkout` as with any sibling's mid-write failure.
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos query: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos query: failed while reading the workspace -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    llm = _chat_client(cfg)
    embedder = _embed_client(cfg)
    embedder_locality = cast(BackendDiagnostics, embedder).locality
    _warn_if_nonlocal_embed_host("query", embedder_locality, cfg)
    if save and not embedder_locality.is_local:
        # #764 finding 3. The standing advisory above was written when
        # `query` embedded ONE string -- the question just typed. Since #762
        # a save also ships every comparable filed insight's SOURCE QUESTION
        # to the same host, which is other content, written on other days,
        # about other subjects: a different disclosure, not a louder one.
        #
        # Said here, at command start, because this is the last point BEFORE
        # the send. The scan runs at preview time, so a notice printed there
        # would describe a transmission that already happened. It names the
        # bound too -- "up to N" is the honest ceiling, and the same number
        # the truncation notice discloses.
        typer.echo(
            "openkos query: note -- --save also sends already-filed source "
            f"questions to '{embedder_locality.display_host}' to check this "
            "one for duplicates: at most one per filed insight, and only the "
            "first time each is seen. They are cached after that, so a warm "
            "workspace sends only the question you just asked.",
            err=True,
        )
    # The CHAT client decides the exemption, not the embedder: the
    # confidential concept bodies travel in the `llm.chat` payload (#240).
    local_exemption = _resolve_local_exemption(
        cast(application_backends.HasLocality, llm), cfg
    )
    observability.warn_if_walk_incomplete(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    # #381: named BEFORE the LLM call, not after it -- the user is told
    # their answer is suspect while they are still waiting for it, rather
    # than after having read it and trusted it. This is the CLI seam that
    # already owns the open-failure-to-`None` decision, so the D2 binding
    # contract holds: `run_query` below still never computes or compares a
    # manifest hash of its own. #436: `query` declares only `fts` -- it
    # stopped reading `graph.db` in #434, so graph staleness cannot degrade
    # THIS answer and must not be blamed here (`status`/`next` still report
    # it as workspace state). Kept in the CLI adapter rather than the
    # service (design D1) so stderr ordering stays byte-identical.
    stale_stores = application_status.stale_index_names(layout, reads=("fts",))
    if stale_stores:
        typer.echo(
            f"warning: derived indexes are stale ({', '.join(stale_stores)}) "
            "-- this answer may be degraded; run `openkos reindex`.",
            err=True,
        )
    # ONE TTY-gated stage notice before the single long retrieval+answer
    # call (issue #190) -- `query`'s `llm.chat` runs inside `run_query`, so
    # this CLI seam is where the wait becomes visible; `stage_notice` is the
    # single-call sibling of `progress_callback`.
    observability.stage_notice("query", "answering (waiting on the LLM)...")
    try:
        outcome = application_query.run_query(
            question,
            layout=layout,
            cfg=cfg,
            llm=llm,
            embedder=embedder,
            limit=limit,
            include_deprecated=include_deprecated,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        )
    except BackendUnavailable as exc:
        typer.echo(
            f"openkos query: failed -- {exc}. "
            f"{application_backends.start_hint(cfg)}, "
            f"then try again.{_DOCTOR_HINT}",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except BackendModelNotFound as exc:
        # Names the REAL failing model from the exception text -- `query`
        # now builds TWO backend-backed seams (chat `llm` + `embedder`), so
        # a hardcoded `cfg.model` would be wrong whenever the embedding
        # model is the one that actually 404'd. `install_hint`'s own
        # `<model>` literal placeholder (issue #1057 Phase 13b) preserves
        # that same "name it generically, the exception text already named
        # the real one" shape for the `openai-compatible` branch.
        typer.echo(
            f"openkos query: failed -- {exc}. "
            f"{application_backends.install_hint(cfg, '<model>')}, then try "
            "again.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    # `BackendEmbeddingDimensionMismatch` is a PERMANENT, non-healing
    # misconfiguration (issue #209): the configured `embedding_model` does
    # not emit `EMBED_DIM`-dimensional vectors, so dense retrieval is
    # structurally impossible, not merely unhelpful this run -- `run_query`
    # therefore propagates it instead of degrading to a silent FTS-only
    # answer at exit 0. Like `reindex`'s own branch, it names a concrete
    # remediation: restore the working `embedding_model` value in
    # `openkos.yaml`. It deliberately does NOT point at `openkos reindex` --
    # reindex fails with this very same error until the config is fixed, so
    # that hint would be actively misleading here. MUST NOT say "will retry
    # next run" (phrasing reserved for a transient `embed_failed` skip).
    # Placed BEFORE the generic tuple below for the same ordering reason as
    # the two handlers above: `BackendEmbeddingDimensionMismatch` subclasses
    # `BackendError`, so reordering would swallow it into the bare message
    # and lose this remediation.
    except BackendEmbeddingDimensionMismatch as exc:
        typer.echo(
            f"openkos query: failed -- {exc} Restore the working "
            "'embedding_model' value in openkos.yaml, then try again.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    # The three specific handlers above MUST precede this generic tuple:
    # `BackendUnavailable`, `BackendModelNotFound`, and
    # `BackendEmbeddingDimensionMismatch` all subclass `BackendError`, so
    # reordering would silently funnel them into this fallback and lose
    # their actionable remediation messages.
    except (FtsUnavailable, BackendError) as exc:
        typer.echo(f"openkos query: failed -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    result = outcome.result
    cited_count = len(result.citations)
    # #760, issue #1003 Slice B: see `application_query.resolve_llm_status`'s
    # docstring for the refused/invoked/skipped rationale.
    llm_status = application_query.resolve_llm_status(result)
    # Two retrieval terms, because there are two retrieval channels. The
    # summary used to carry a third, `<n> graph-added` from
    # `graph_contributed_count` -- how many reserved tail slots the seeded
    # personalized-PageRank channel filled with concepts FTS and dense never
    # found. Issue #434 removed the channel: measured over 10 questions the
    # slot it claimed was 7 times harmful, 3 times neutral and never
    # beneficial, because PageRank centrality is a property of the corpus,
    # not of the question. There is no term to print because there is no
    # third list, and no graph-degrade note below for the same reason.
    typer.echo(
        f"retrieval: {result.fts_hit_count} FTS + {result.dense_hit_count} "
        f"dense → "
        f"{result.fused_count} fused → LLM {llm_status} → {cited_count} cited",
        err=True,
    )
    if (
        outcome.vector_store_unavailable
        or outcome.fts_unavailable
        or result.dense_degraded
    ):
        typer.echo(
            "hint: one or more derived indexes are unavailable this run -- "
            "run `openkos reindex` to enable full retrieval.",
            err=True,
        )
    if result.excerpted_titles:
        # #882: the retrieval path had #866's defect and neither its bound
        # nor its disclosure. Silent overflow is the worse half: Ollama
        # discards the excess without raising (measured at
        # `prompt_eval_count: 6146` on a 184,000-char prompt), so an
        # operator had NO surface -- not `query`, not `lint`, not `status`
        # -- on which to discover that the answer rests on a fraction of
        # the documents it cites. Named, not counted, and worded to match
        # the ingest advisory an operator has already met.
        clipped = ", ".join(result.excerpted_titles)
        # Worded to stay true in both directions. It does NOT promise a
        # [partial] marker: #753 filters the citation list down to what the
        # answer reported using, so a model that cited nothing renders no
        # list at all and the promise would name a marker the reader cannot
        # find. And it says "part of" rather than "an excerpt of": when the
        # prompt overhead swallows a block's whole share, the part the model
        # read is none of it -- claiming an excerpt there would be the same
        # kind of overclaim this issue exists to remove.
        typer.echo(
            "openkos query: the retrieved context is larger than the "
            f"model's context window, so the model read only part of "
            f"{len(result.excerpted_titles)} document(s) ({clipped}); any "
            "citation of these below is marked [partial]. Raise "
            "context_window in openkos.yaml to widen what the model sees.",
            err=True,
        )
    if result.omitted_titles:
        # #882: shown NONE of these, so they are not in the prompt and not
        # in the citation list either. Reported separately from the
        # partial-read line above because the operator's reading of the
        # answer differs: a partially read document still contributed, an
        # omitted one did not contribute at all and its absence may be why
        # the answer is thin.
        dropped = ", ".join(result.omitted_titles)
        typer.echo(
            f"openkos query: {len(result.omitted_titles)} retrieved "
            f"document(s) ({dropped}) did not fit the model's context window "
            "at all and were left out of the prompt entirely; they are not "
            "cited. Raise context_window in openkos.yaml.",
            err=True,
        )
    if result.history_truncated_titles:
        # #1014 piece b, design Decision 6: printed after the omitted-context
        # notice above, and only when `revision_history` is on and at least
        # one retrieved successor's chain continues beyond what was shown
        # (the block cap or the depth bound) -- `[]` on every disabled run.
        truncated = ", ".join(result.history_truncated_titles)
        typer.echo(
            "openkos query: the revision history of "
            f"{len(result.history_truncated_titles)} document(s) "
            f"({truncated}) goes back further than the earlier versions "
            "shown; the answer did not see the rest.",
            err=True,
        )
    if result.sufficiency_degraded:
        # #764: the check failed OPEN, which is deliberate -- a backend error
        # is not evidence that the bundle cannot answer. But an operator whose
        # backend has been flaky has no other way to learn that the guard they
        # configured has not run, so the degradation is announced exactly like
        # the retrieval one above rather than left silent.
        typer.echo(
            "openkos query: notice -- the sufficiency check could not run this "
            "call, so this answer was not checked against the retrieved "
            "context before it was written.",
            err=True,
        )
    # #777: the attribution fallback is announced, not silent. Under `absent`
    # or `unparsed` every retrieved concept is cited -- the pre-#753 behavior,
    # byte for byte -- so the `N cited` term above is indistinguishable from a
    # compliant answer that genuinely drew on all N. Same #764 principle as
    # the sufficiency notice: a guard that could not run says so. The two
    # states keep separate wording because they answer different questions
    # about a model (ignored the instruction vs. tried and failed the format).
    # Gated on `llm_invoked`: `attribution` defaults to `"absent"` on every
    # short-circuit result, where no citation list was ever decided.
    if result.llm_invoked and result.attribution == "absent":
        typer.echo(
            "openkos query: notice -- the answer reported no attribution "
            "line, so every retrieved concept is cited (the citation list is "
            "the retrieval set, not the model's own accounting).",
            err=True,
        )
    elif result.llm_invoked and result.attribution == "unparsed":
        typer.echo(
            "openkos query: notice -- the answer's attribution line named "
            "nothing usable, so every retrieved concept is cited (the "
            "citation list is the retrieval set, not the model's own "
            "accounting).",
            err=True,
        )
    if result.skip_notices:
        typer.echo(
            f"index: {len(result.skip_notices)} "
            f"doc{_plural(len(result.skip_notices))} skipped while building "
            "the search index (whole-bundle, not this query's hits):",
            err=True,
        )
        for notice in result.skip_notices:
            typer.echo(f"  {notice}", err=True)

    if result.no_match_cause != "none":
        typer.echo(_no_match_message(result.no_match_cause, result.fts_hit_count))
        return

    typer.echo(result.answer)
    if result.citations:
        typer.echo()
        typer.echo("Citations:")
        for citation in result.citations:
            marker = " [confidential]" if citation.confidential else ""
            # #882: the model was shown an EXCERPT of this document, not all
            # of it. Rendered here because this list is what a reader trusts
            # and what `--save` files as provenance -- a citation that looked
            # identical to a fully-read one IS the false provenance claim.
            partial = " [partial]" if citation.excerpted else ""
            # Issue #570, issue #1003 Slice B: see
            # `application_query.is_synthesis_citation`'s docstring for the
            # `insights/` identity-by-link-dir rationale.
            synthesis = (
                " [synthesis]"
                if application_query.is_synthesis_citation(citation)
                else ""
            )
            # #1014 piece b, design Decision 6: rendered FIRST in the marker
            # sequence -- an ordinary hit citation's `history` is `None` and
            # renders neither marker.
            history = (
                " [superseded]"
                if citation.history == "superseded"
                else " [refined]"
                if citation.history == "refined"
                else ""
            )
            typer.echo(
                f"  → {citation.concept_id} ({citation.title})"
                f"{history}{synthesis}{partial}{marker}"
            )
    elif result.attribution == "reported":
        # #753: the answer itself reported drawing on none of the concepts
        # retrieved for it. Announced rather than merely rendered as a missing
        # section, because a bare reply with no `Citations:` block reads like a
        # rendering bug instead of the finding it is -- and this exact reply,
        # before the citation fix, arrived carrying `limit` citations that its
        # own text did not support.
        #
        # Gated on `reported`, never on an empty list alone: under `absent` the
        # citation list was not decided by the answer at all, so its emptiness
        # says nothing about support and this would fire on every backend that
        # ignores the instruction.
        typer.echo(
            "openkos query: warning -- this answer drew on none of the "
            f"{result.context_block_count} concepts placed in its context, so "
            "it stands on nothing in the bundle. Treat it as the model's own "
            "knowledge.",
            err=True,
        )

    # Issue #570: compounding on sources is the product's thesis;
    # compounding on model output with no source underneath is how a
    # knowledge base rots. #649 made the signal PROPORTIONAL: the
    # all-or-nothing guard fired only when every citation was a synthesis,
    # a threshold a drifting base approaches without ever crossing. Now the
    # share warns at or above the service's threshold (Slice 2:
    # `application_query.synthesis_share_warrants_warning`), with the
    # all-synthesis case keeping its stronger wording -- stderr, like every
    # advisory here. The count itself is
    # `application_query.synthesis_citation_count` (issue #1003 Slice B):
    # the same derivation the threshold check uses internally, asserted
    # once rather than re-derived at this call site.
    synthesis_count = application_query.synthesis_citation_count(result.citations)
    if application_query.synthesis_share_warrants_warning(result.citations):
        if synthesis_count == len(result.citations):
            typer.echo(
                "openkos query: warning -- every citation is itself a filed "
                "synthesis (Insight); nothing beneath this answer reaches a "
                "Source.",
                err=True,
            )
        else:
            typer.echo(
                f"openkos query: warning -- {synthesis_count} of "
                f"{len(result.citations)} citations are themselves filed "
                "syntheses (Insights); this answer increasingly stands on "
                "model output rather than sources.",
                err=True,
            )

    # Read-path disclosure (issue #569), the mirror of `_autocommit`'s
    # commit NOTICE: the fail-closed gate already decided ADMISSION (the
    # confidential local exemption or --include-confidential let these
    # concepts in by design), but a user reading the answer had no way to
    # know it was confidential material -- and the obvious next action,
    # pasting it into an email or a ticket, moves it off this machine with
    # no signal from the tool. stderr, like every other advisory here, so
    # piped stdout stays answer-only.
    if any(citation.confidential for citation in result.citations):
        typer.echo(
            "openkos: NOTICE -- this answer cites content marked "
            "'sensitivity: confidential'; sharing it forward moves that "
            "content off this machine.",
            err=True,
        )

    if not save:
        return

    try:
        plan = application_query.stage_filed_answer(
            question=question,
            answer_text=result.answer,
            citations=result.citations,
            bundle_dir=layout.bundle_dir,
            default_sensitivity=cfg.default_sensitivity,
            timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            title=title,
            description=description,
            doc_type=save_type,
            cfg=cfg,
        )
    except ValueError as exc:
        typer.echo(f"openkos query: refusing to save -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    save_index_path = layout.bundle_dir / "index.md"
    save_log_path = layout.bundle_dir / "log.md"
    now = datetime.now(UTC)
    try:
        # One `_snapshot_read` observation per target: the decoded text
        # feeds `compose_filed_answer_catalog_update` below, the raw bytes
        # feed `_reject_drifted_targets` (issues #306, #313, #318).
        index_bytes, index_text = _snapshot_read(save_index_path)
        log_bytes, log_text = _snapshot_read(save_log_path)
        # Issue #1003 Slice B: see
        # `application_query.compose_filed_answer_catalog_update`'s
        # docstring for the staging rationale.
        catalog_update = application_query.compose_filed_answer_catalog_update(
            index_text, log_text, plan, now.astimezone().date()
        )
        new_index_text = catalog_update.new_index_text
        new_log_text = catalog_update.new_log_text
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos query: failed while preparing the save -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos query: proposed changes (--save):")
    # High-water-mark disclosure (issue #569), extended to a three-way
    # branch by the per-type default (issue #669, design D4): the type
    # default cause outranks the citation cause when both could apply --
    # it is the NEW, surprising one -- and the citation wording is
    # preserved byte-for-byte for the cases it still owns, so #569's
    # existing assertions stay green. Neither branch fires when the
    # resolved level is at the workspace default: printing it
    # unconditionally would bury the one case that matters.
    if plan.type_floor_raised:
        typer.echo(
            f"  + bundle/{plan.link_dir}/{plan.slug}.md "
            f"(sensitivity: {plan.sensitivity}, raised by the {save_type} "
            "type default)"
        )
    elif plan.sensitivity != cfg.default_sensitivity:
        typer.echo(
            f"  + bundle/{plan.link_dir}/{plan.slug}.md "
            f"(sensitivity: {plan.sensitivity}, inherited from citations)"
        )
    else:
        typer.echo(f"  + bundle/{plan.link_dir}/{plan.slug}.md")
    # The `!` consequence line prints whenever the resolved level is
    # `confidential`, by either route -- the consequence belongs to the
    # level, not to the cause (design D4).
    if plan.sensitivity == "confidential":
        typer.echo(
            "  ! confidential: excluded from query, contradictions, and "
            "suggest-relations against a non-local backend."
        )
    # #774: when the attribution fell back (`absent`/`unparsed`), the
    # citations about to become permanent provenance are the retrieval set,
    # not anything the answer accounted for. Measured on the field bundle
    # that produced the issue: 30 of 30 fabricated answers were `absent`
    # while 63 of 63 grounded or compliant answers were `reported`
    # (evals/query_entailment/). Disclosed in the plan, then gated below.
    # The predicate is the service's policy (design D4, Slice 2), asserted
    # once rather than re-derived at this call site.
    unattributed = application_query.grounding_unverified(result)
    if unattributed:
        typer.echo(
            "  ! unverified grounding: the answer never accounted for its "
            f"citations (attribution: {result.attribution}), so the "
            f"{len(result.citations)} citation(s) above are the retrieval "
            "set and would be filed as provenance verbatim."
        )
    if result.excerpted_titles:
        # #882: `--save` is the seam that makes the overflow permanent, so
        # the plan the human approves has to carry it. #774's line above
        # covers "the answer never accounted for these citations"; this one
        # covers "the model never read all of what it is about to cite" --
        # different failures, and a partially-read citation can be filed
        # under a perfectly `reported` attribution.
        clipped = ", ".join(result.excerpted_titles)
        typer.echo(
            f"  ! partially read: {len(result.excerpted_titles)} document(s) "
            f"({clipped}) did not fit the model's context window, so the "
            "model read only part of each; they would be filed as "
            "provenance for text the model never saw."
        )
    typer.echo(f"  ~ {save_index_path.name} (new entry)")
    typer.echo(f"  ~ {save_log_path.name} (new dated entry)")

    # #762: disclose insights already filed from a question resembling this
    # one, BEFORE the confirmation gate, so the human deciding has the
    # duplicate in front of them. Advisory only -- nothing is merged,
    # renamed or refused.
    #
    # Every comparable filed insight is compared; there is no bound to
    # disclose, because #764's cap was retired once caching made the
    # comparison cheap.
    #
    # The separating margin measured in
    # `evals/query_identity/` is NEGATIVE over eleven families -- the classes
    # overlap and no threshold splits them. What survives is that the shipped
    # threshold discloses zero of 526 different-subject pairs while reaching
    # 11 of 35 paraphrases: low recall, no strangers. That is evidence for
    # "worth showing a person" and nowhere near enough for "act on it
    # automatically".
    #
    # The slug is the permanent Concept ID, so a duplicate filed here is
    # permanent too; this is the last moment it costs nothing to notice.
    # The question-vector cache is what lets this compare the WHOLE bundle
    # instead of the 100 most recently filed: a stored question's embedding
    # never changes, and reusing it is ~220x cheaper than recomputing it
    # (`evals/insight_scan_bound/`). `application_query.scan_for_duplicates`
    # (Slice 2, design D3) owns opening and closing the cache connection --
    # the scan itself takes a structural Protocol and never touches sqlite
    # directly.
    #
    # A store that cannot open degrades to `cache=None`, which the scan
    # reports as "could not check" -- the same already-disclosed state a down
    # embedding backend produces. Deliberately NOT a fallback to embedding
    # every filed question: that is the linear cost this design removes, and
    # it would stall the confirmation gate with nothing on screen saying why.
    duplicate_scan = application_query.scan_for_duplicates(
        question, layout=layout, cfg=cfg, embedder=embedder
    )
    for duplicate in duplicate_scan.candidates:
        typer.echo(
            f"  ? possible duplicate of bundle/{duplicate.concept_id}.md "
            f"({duplicate.title}) -- filed from "
            f"{duplicate.question!r} ({duplicate.similarity:.2f} similar)"
        )
    if duplicate_scan.unavailable:
        # #764: "scanned and found nothing" and "could not scan" used to look
        # identical from here -- both an empty list -- so a down embedding
        # backend silently retired the disclosure and nobody could tell. Said
        # on stderr, above the confirmation gate, because the human is about
        # to decide with one fewer piece of information than they think.
        typer.echo(
            "openkos query: notice -- could not check this question against "
            "already-filed insights, so no duplicate was looked for.",
            err=True,
        )

    # #774 gate first: it is deliberately STRONGER than the ordinary review
    # gate -- `--auto` and `review: false` bypass the prompt below, and an
    # unattended pipeline filing fabricated provenance is the exact harm the
    # issue documents. On a TTY its question REPLACES the ordinary one (one
    # prompt, covering more); off a TTY it refuses unless the caller opted
    # in with `--allow-unattributed` -- the escape hatch that keeps a
    # backend that never emits the attribution line (the deliberate
    # pre-#753 fallback population, where EVERY answer is `absent`) usable
    # unattended, by saying so explicitly.
    if unattributed and not allow_unattributed:
        if sys.stdin.isatty():
            typer.confirm(
                "File it with these unverified citations as provenance?",
                abort=True,
            )
        else:
            typer.echo(
                "openkos query: refusing to save -- the answer's grounding "
                f"is unverified (attribution: {result.attribution}) and "
                "stdin is not a TTY. Re-run with --allow-unattributed to "
                "accept the retrieval set as provenance.",
                err=True,
            )
            raise typer.Exit(code=1)
    elif not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos query: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    # The commit phase (#1137): everything above -- retrieval, the model call,
    # the duplicate scan, the preview and the prompt -- ran with no lock held.
    # Only the re-validation and the writes below hold it, so a human reading
    # the preview never starves another writer.
    with _commit_section_for(root)():
        # Read dependencies first: the insight's level was folded from the
        # cited concepts' sensitivity at staging, and a concurrent raise is
        # not a drifted WRITE target, so the guard below cannot see it.
        cited_drift = application_query.describe_cited_drift(plan, layout.bundle_dir)
        if cited_drift is not None:
            typer.echo(cited_drift, err=True)
            raise typer.Exit(code=3)

        # Issue #313: every byte below was computed from a pre-prompt read, so
        # re-validate each target now -- after the gate, before the first write.
        # `plan.path` is absent by necessity, not oversight -- see the docstring.
        _reject_drifted_targets(
            layout,
            {save_index_path: index_bytes, save_log_path: log_bytes},
            "query",
        )

        answer_rel = f"bundle/{plan.link_dir}/{plan.slug}.md"
        landed: list[str] = []
        try:
            # Write order: answer document BEFORE `index.md` BEFORE `log.md`
            # (content before catalog, mirroring `ingest`'s D3): a mid-sequence
            # failure can leave an uncataloged file on disk, never a catalog
            # entry pointing at a file that does not exist. There is no
            # cross-file rollback, matching every other mutating verb's
            # documented limitation. `landed` records each path only AFTER its
            # write returns, so a failure names exactly the paths already on
            # disk (#331, mirroring `set-sensitivity`'s D9 shape).
            plan.path.parent.mkdir(parents=True, exist_ok=True)
            fsio.write_exclusive(plan.path, plan.content)
            landed.append(answer_rel)
            fsio.write_atomic(save_index_path, new_index_text)
            landed.append("bundle/index.md")
            fsio.write_atomic(save_log_path, new_log_text)
            landed.append("bundle/log.md")
        except (OSError, ValueError) as exc:
            # Distinct from the refusal phases above on purpose (#234): this is
            # reached only after the write phase began, so the answer document
            # may already be on disk while the catalog is not. "refusing" would
            # tell an operator nothing happened, which is exactly wrong here.
            landed_suffix = (
                f"Already written (left partially filed, not rolled back): "
                f"{', '.join(landed)}."
                if landed
                else "No path was written."
            )
            typer.echo(
                f"openkos query: failed while saving the answer -- {exc}. {landed_suffix}",
                err=True,
            )
            raise typer.Exit(code=1) from exc

        typer.echo(
            f"openkos query: filed answer as bundle/{plan.link_dir}/{plan.slug}.md "
            f"({save_index_path.name}, {save_log_path.name} updated)."
        )
        # Write-Time Advisory (issue #669, design D4): the spec-required
        # success-message advisory, the `query --save` mirror of ingest's
        # run-summary line -- fires even when `--auto` skips the confirmation
        # prompt, since the preview block above can, in principle, be bypassed
        # in ways the success message must never depend on. stdout, like
        # `query`'s own success line -- `query --save` has no batch stdout
        # contract to protect (unlike ingest's stderr notices, #349).
        if plan.type_floor_raised:
            typer.echo(
                f"openkos query: 1 concept was born above the workspace "
                f"sensitivity floor by type default ({save_type} -> "
                f"{plan.sensitivity})."
            )
            if plan.sensitivity == "confidential":
                typer.echo(
                    "openkos query: confidential concepts are excluded from "
                    "query, contradictions, and suggest-relations against a "
                    "non-local backend (#569)."
                )

        # #331: `query --save` was the ONE mutating path without the
        # workspace-autocommit safety net, for no documented reason -- the
        # Slice-2 exclusion list (workspace-autocommit spec: "Exclusions and
        # Unconditional Behavior") names `reindex` output, `init`, and
        # read-only verbs only, and `query --save` simply postdated the
        # planning that produced the six-verb roster. Same call shape as every
        # sibling: the exact Phase-B paths, workspace-relative POSIX, scoped
        # `git add -- <paths>`, best-effort and non-fatal.
        _autocommit(
            root,
            [answer_rel, "bundle/index.md", "bundle/log.md"],
            f"openkos: query --save {plan.link_dir}/{plan.slug}",
        )

        # #640: this verb used to end with "Run `openkos reindex` to make it
        # searchable." -- with the write-time refresh, that instruction is FALSE
        # on the success path, so searchability is claimed only when the refresh
        # actually completed; the degrade path's advisory (inside the helper)
        # carries the manual `openkos reindex` pointer instead.
        # `query` printed the advisory before it embedded the question (#199),
        # so the refresh must not repeat it (#353 item 4).
        if _refresh_derived_after_write(
            layout, cfg, verb="query", warn_nonlocal_host=False
        ):
            typer.echo("openkos query: the filed insight is indexed and searchable.")


@app.command(
    help=(
        "Rebuild the local search indexes from the bundle's documents, so "
        "query and duplicate detection see current content."
    ),
    rich_help_panel="Maintain",
)
@_guard_workspace_lock("reindex")
def reindex(
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Re-embed every discovered doc, ignoring the content-hash cache, "
            "and rebuild the FTS and graph indexes unconditionally."
        ),
    ),
) -> None:
    """Backfill `.openkos/vectors.db`, `.openkos/fts.db` and
    `.openkos/graph.db` from the compiled bundle -- the sole writer of every
    derived store's data (spec: reindex-command).

    A thin adapter over `application/reindex_service.reindex_workspace`: it
    supplies the current directory as the workspace root, the effects (the
    embedding client, the vector store, the proximity source), and a
    `_ReindexObserver` that renders the summary and the advisories, then maps
    every typed `ReindexRefused` to its message on stderr and an exit code: 3
    (the retry-safe refusal) for derived-store contention, 1 for the rest. The
    orchestration -- the ordered backend error ladder, the lock-contention
    discrimination, the summary-before-graph ordering -- lives in the service.
    """
    try:
        reindex_service.reindex_workspace(
            Path.cwd(),
            force=force,
            ports=reindex_service.ReindexPorts(
                embed_client=_embed_client,
                open_vector_store=open_vector_store,
                open_proximity=_open_proximity_or_degrade,
                local_exemption=_resolve_local_exemption,
            ),
            observer=_ReindexObserver(),
        )
    except reindex_service.ReindexRefused as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(
            code=3 if isinstance(exc, reindex_service.LockContention) else 1
        ) from exc


def _render_reindex_summary(
    report: reindex_module.ReindexReport,
    previous_model_tag: str | None,
    cfg: config.Config,
) -> None:
    """Print the vectors/FTS summary and its follow-up notices for one
    `reindex` run. Called by the service BEFORE the graph write (see
    `_ReindexObserver.vectors_indexed`)."""
    # The vectors.db/fts.db summary is printed HERE, BEFORE the graph write
    # attempt below -- not after it, as an earlier revision did (review
    # finding R4). `report` already reflects durably-committed work at this
    # point (`state.reindex.reindex` returned successfully); if the graph
    # write below then fails, the user must still see what DID happen
    # (embedded/cache-hit/pruned/skipped counts, and the `prune_skipped`
    # follow-up notice) rather than losing that signal behind the graph
    # error -- printing it first guarantees it always reaches the user,
    # regardless of what happens next.
    typer.echo(
        f"openkos reindex: {report.embedded} embedded, {report.cache_hits} "
        f"cache-hit{_plural(report.cache_hits)}, {report.pruned} pruned, "
        f"{report.skipped} skipped, {report.embed_failed} embed-failed, "
        f"{report.withheld_confidential} withheld."
    )
    _warn_withheld_from_embedding("reindex", report.withheld_confidential, cfg)
    if report.prune_skipped:
        typer.echo(
            "openkos reindex: prune pass was skipped this run -- a "
            "directory-scan error made part of the bundle unreadable, so no "
            "concept was pruned even if some appeared absent (review "
            "carry-over, fold-in #3)."
        )
    # Model-tag force observability (review correction, WARNING finding):
    # a model-tag mismatch triggers an operationally heavy full re-embed
    # that is otherwise indistinguishable from an ordinary large content
    # change -- name the REAL trigger explicitly (reindex-command: Reindex
    # Discloses The Real Re-Embed Trigger, Not A False Model-Change Claim;
    # #888 corrects the comparison from stored-effective-tag-vs-bare-model,
    # which falsely reported "embedding model changed" on a composition-only
    # bump such as this change's own `compose-v1` -> `chunk-v1`). The
    # wording must stay ACCURATE to whether the re-embed actually covered
    # every doc this run (round-2 review correction, WARNING finding):
    # claiming "re-embedded all vectors" while ALSO reporting docs that
    # could not be re-embedded is self-contradictory, so the complete
    # (`skipped == 0 AND embed_failed == 0`) and incomplete (`skipped > 0 OR
    # embed_failed > 0`) cases get distinct, non-overlapping wording instead
    # of one unconditional line plus a caveat. The success branch's gate
    # MUST mirror `state.reindex`'s tag-persist gate exactly (`skipped == 0
    # AND embed_failed == 0`) -- reindex-embedding-resilience widened the
    # tag-persist gate to also withhold on `embed_failed > 0`, so a
    # `skipped == 0`-only success check here would print a false success
    # while the tag was actually withheld (review correction, CRITICAL
    # finding). #922 widened that tag-persist gate a third time, to include
    # `withheld_confidential`, so this sum follows it for the same reason:
    # a run that withheld a confidential document did NOT re-embed every
    # vector, and saying it did is the same self-contradiction.
    incomplete_count = (
        report.skipped + report.embed_failed + report.withheld_confidential
    )
    if report.model_reembedded and incomplete_count == 0:
        typer.echo(
            "openkos reindex: re-embedded all vectors -- "
            f"{_reembed_trigger_wording(previous_model_tag, report.effective_model_tag)}; "
            f"embed_calls={report.embed_calls}."
        )
    elif report.model_reembedded:
        typer.echo(
            f"openkos reindex: "
            f"{_reembed_trigger_wording(previous_model_tag, report.effective_model_tag)}; "
            "re-embedding all vectors -- INCOMPLETE: "
            f"{incomplete_count} doc{_plural(incomplete_count)} could not be "
            f"re-embedded, will retry next run; embed_calls={report.embed_calls}."
        )
    # Actionable re-run notice (reindex-embedding-resilience): keys ONLY on
    # `embed_failed` -- transient embed-EOF skips (retry budget exhausted at
    # the OllamaClient layer) are self-healing, unlike the permanent
    # `skipped` diagnostics above (unreadable/parse/decode failures a re-run
    # will NOT fix). Deliberately NEVER keys on `skipped` alone, so the two
    # skip kinds stay distinct on stderr, matching `ReindexReport.skipped`
    # vs `embed_failed`'s separation. This only reaches an exit-0 run: the
    # fatal ladder above (`BackendUnavailable`/`BackendModelNotFound`) exits 1
    # before the summary is ever printed.
    if report.embed_failed > 0:
        typer.echo(
            "openkos reindex: INCOMPLETE -- "
            f"{report.embed_failed} doc{_plural(report.embed_failed)} could "
            "not be embedded (transient failure). Run `openkos reindex` "
            "again to complete it.",
            err=True,
        )


class _ReindexObserver:
    """Renders what `reindex_service.reindex_workspace` reports as it goes."""

    def embedder_ready(self, locality: BackendHostLocality, cfg: config.Config) -> None:
        _warn_if_nonlocal_embed_host("reindex", locality, cfg)

    def progress_callback(self) -> Callable[[int, int, object], None] | None:
        # TTY-gated per-doc embedding progress on stderr; `None` (silent)
        # when output is piped (issue #190).
        return observability.progress_callback("reindex", "embedding doc")

    def vectors_indexed(
        self,
        report: reindex_module.ReindexReport,
        previous_model_tag: str | None,
        cfg: config.Config,
    ) -> None:
        _render_reindex_summary(report, previous_model_tag, cfg)


def _render_check(r: application_doctor.CheckResult) -> None:
    """Print one `CheckResult` as `[PASS]`/`[FAIL]`/`[SKIP] <label>`, with an
    optional ` — <detail>` suffix and, only under a `[FAIL]`, an indented
    `  -> <remediation>` line naming the user's own next command.

    `CheckResult` moved into `application/doctor.py` (issue #995, PR 6) --
    this function stays adapter-side, unchanged, since it is a render loop,
    not a read. `"not-run"` (ADR-0022) gains its own tag, `[NOT RUN]`; the
    reason rides in `detail` like every other status, so it needs no second
    branch here -- the `-> remediation` line below stays `fail`-only."""
    tag = {
        "pass": "[PASS]",
        "fail": "[FAIL]",
        "skip": "[SKIP]",
        "not-run": "[NOT RUN]",
    }[r.status]
    line = f"{tag} {r.label}"
    if r.detail:
        line += f" — {r.detail}"
    typer.echo(line)
    if r.status == "fail" and r.remediation:
        typer.echo(f"  -> {r.remediation}")


@app.command(
    help=(
        "Check this machine's environment: whether the model backend is "
        "reachable, correctly configured, and running where you expect."
    ),
    rich_help_panel="Maintain",
)
def doctor() -> None:
    """Read-only environment health scan: fixed checks against the local
    workspace and local Ollama, printed as `[PASS]`/`[FAIL]`/`[SKIP]` lines
    with actionable remediation, usable even before `openkos init`.

    Deliberately NEW control-flow shape versus `status`/`lint`/`query`:
    instead of exiting on the first failure, this runs ALL fifteen checks
    (thirteen numbered plus two lettered sub-checks, 5b and 7b -- the
    docstring here previously said "twelve", stale even before the checks
    themselves moved; `tests/unit/cli/test_doctor.py` already asserted 15
    `[PASS]` lines on a healthy workspace, issue #995 PR 6), appends each
    to a `list[CheckResult]`, renders every line unconditionally, then
    exits ONCE (`code=1`) if any CRITICAL check failed (spec: Doctor Runs
    And Prints All Applicable Checks). Remediation TEXT lives in
    `application/doctor.py`, not in this adapter, because that is where
    every check's own pass/fail/skip branching now lives (issue #995,
    PR 6): this command body only supplies a `build_client` factory over
    the one concrete `OllamaClient` (WALL 1, issue #1002 item B -- see
    below), computes the two `openkos.vcs` booleans and the one
    `reset_point_available` thunk `run_diagnostics` needs injected
    (WALL 2, `application/doctor.py`'s own module docstring), calls
    `application_doctor.run_diagnostics` once, and renders. `llm/` stays
    config-free (D1).

    Output leads with an `openkos {version}` banner -- the same line
    `--version` prints (cli-version-flag, #181). It is informational only,
    never a `CheckResult`, and precedes both the header and the check lines,
    so it affects neither the check count nor the exit code.

    Checks, in order: (1) workspace-initialized -- informational, via the
    shared `config.require_workspace` gate; (2) config-valid -- critical,
    workspace-only, `[SKIP]` outside a workspace; (3) Ollama-reachable --
    critical, always, via `OllamaClient.list_models()`; (4) model-installed
    -- critical, always, via `model_tag_matches`; `[SKIP]` (never `[FAIL]`)
    when Ollama is unreachable, since the two share one root cause (D6);
    (5) embedding-model-installed -- informational, always, via the SAME
    already-fetched `installed` list and `model_tag_matches`; `[SKIP]`
    (never `[FAIL]`) when Ollama is unreachable, for the same D6 reason --
    Slice 1 does not wire embeddings into any consumed feature yet, so a
    failure here must not flip the exit code; (6) bundle-readable --
    informational, workspace-only, `[SKIP]` outside a workspace; (7)
    workspace-vector-index-present -- informational, workspace-only,
    `[SKIP]` outside a workspace, via `layout.vectors_db_path.exists()`
    (purge-transactional-cleanup #142) -- distinct from (8): this checks
    THIS workspace's own `.openkos/vectors.db` file, not a throwaway
    `:memory:` probe; a `[FAIL]` here always names `openkos reindex` as its
    remediation; (8) vector-extension-loadable -- informational, always, via
    `state.vectorstore.probe_vec_loadable()` against a throwaway `:memory:`
    connection; UNLIKE (5), this check has NO `[SKIP]` branch -- it depends
    on neither workspace state nor Ollama reachability, so it shares no root
    cause with any other check (embedding-vector-store, Slice 2a; the
    scaffolding this checks has no consumed feature yet either); (9)
    git-available -- informational, always, via `vcs.git.git_available()`;
    (10) git-filter-repo-available -- informational, always, via
    `vcs.git.filter_repo_available()`. Checks (9)/(10) exist for the
    not-yet-wired `purge` verb (privacy-purge Slice 1, PR2): like (8), they
    have no `[SKIP]` branch -- they depend on neither workspace state nor
    Ollama; (11) backend-host-locality -- informational, always, via the
    check-(3) client's own `OllamaClient.locality` (issue #240), reporting
    the REDACTED `display_host`, whether it is this machine, and whether the
    confidential local exemption is consequently active. It is `[PASS]` while
    Ollama is reachable and `[SKIP]` when it is not (#389) -- not because it
    cannot answer without the server, but because a green line printed
    directly beneath `[FAIL] Ollama reachable` reads as a contradiction to
    anyone scanning the column. It is NEVER `[FAIL]`: a non-local backend is
    a legitimate configuration, not a fault, so the status only reports
    whether the check was verified and the DETAIL carries the finding, on
    both branches. It can therefore never change the exit code.
    Outside a workspace, checks (3)/(4)/(5)/(8)/(9)/(10)/(11) still run
    against `config.DEFAULT_MODEL`/`config.DEFAULT_EMBEDDING_MODEL`/
    `config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION` and (3)/(4) still
    determine the exit code (spec: Doctor Works Outside An Initialized
    Workspace).

    Never creates, modifies, or deletes any file, and never runs a
    remediation command itself (spec: Doctor Is Read-Only).
    """
    root = Path.cwd()

    # WALL 2 (`application/doctor.py`'s own module docstring): the
    # `openkos.vcs` answers checks 9/10/13 need, produced here because
    # `application/doctor.py` must never import `openkos.vcs`
    # (`tests/unit/application/test_layering.py`).
    #
    # Two shapes, and the split is by COST, not by taste. `git_available`
    # and `filter_repo_available` are `shutil.which` probes that cost
    # nothing, so they are computed now and injected as plain booleans. The
    # reset-point probe shells out to `git` twice (`repo_root` +
    # `has_reset_point`), and the pre-extraction body paid that ONLY inside
    # check 13's `if violations:` branch, so it is injected as a thunk the
    # service calls in that same branch and nowhere else. Its own docstring
    # below carries the reasoning; `tests/unit/application/
    # test_doctor_service.py` pins the call count at zero on the common
    # path, so collapsing it back into an eager `bool` reddens a test.
    git_available_ok = vcs_git.git_available()
    filter_repo_ok = vcs_git.filter_repo_available()

    def _reset_point_available() -> bool:
        """Whether a git reset point exists, probed ON DEMAND.

        A thunk rather than a value because these two `git` subprocess
        calls are not free, and the pre-extraction body paid them ONLY
        inside check 13's `if violations:` branch. Passing an
        already-computed `bool` would move them onto every in-workspace
        `doctor` invocation -- a cost added to the command people run
        precisely when their workspace is already misbehaving.

        `GitError` IS caught here (ADR-0022, design.md Decision 2), and
        re-raised as `application_doctor.ProbeUnavailable` -- an
        application-owned type, since `application/*` may not import
        `openkos.vcs` (AST-enforced, `tests/unit/application/
        test_layering.py`). Check 13's own `try/except ProbeUnavailable`
        degrades the merge-ledger-integrity check to `not-run` instead of
        losing every other accumulated `CheckResult`. This translation sits
        at the CLI boundary rather than the pre-extraction call site
        precisely because the layering ban forces it here -- see D2 in
        design.md for the full reasoning."""
        try:
            return vcs_git.repo_root(root) is not None and vcs_git.has_reset_point(root)
        except vcs_git.GitError as exc:
            raise application_doctor.ProbeUnavailable(str(exc)) from exc

    # WALL 1 (`application/doctor.py`'s own module docstring): the CLI
    # adapter never builds a client itself; it supplies HOW to build one --
    # `build_client`, a factory over the one concrete `OllamaClient` as a
    # `BackendDiagnostics`. Check 2's `config.read_config` call, inside
    # `run_diagnostics`, is the ONLY read of `openkos.yaml` a `doctor` run
    # performs, and `build_client` is called with that SAME `cfg.model` (or
    # `config.DEFAULT_MODEL` on its existing fallback), so the model
    # PROBED and the model REPORTED can never be two different reads
    # disagreeing with each other (issue #1002 item B; `resolve_diagnostic_model`,
    # which used to read `openkos.yaml` a second time just to produce this
    # client BEFORE `run_diagnostics` ran, is gone).
    def _build_client(cfg: config.Config | None, model: str) -> BackendDiagnostics:
        return application_backends.diagnostics_client(
            cfg,
            model=model,
            timeout=_PREFLIGHT_TIMEOUT,
            factories=_backend_factories(),
        )

    results = application_doctor.run_diagnostics(
        root,
        build_client=_build_client,
        git_available=git_available_ok,
        filter_repo_available=filter_repo_ok,
        reset_point_available=_reset_point_available,
    )

    # Leading version banner (cli-version-flag, #181): informational only, NOT
    # a CheckResult -- it is deliberately outside `results` so it can never
    # affect the check count or the exit code.
    typer.echo(_version_line())
    typer.echo(f"openkos doctor: checking environment at {root}")
    typer.echo()
    for r in results:
        _render_check(r)

    # Completed/not-run counts (design.md Decision 5, ADR-0022): `skip`
    # counts as completed, matching the exit rule below so the printed line
    # and the exit code always agree about what "completed" means.
    n = sum(1 for r in results if r.status == read_outcome.NOT_RUN)
    typer.echo(f"{len(results) - n} check(s) completed, {n} did not run.")

    # Exit rule (ADR-0022, design.md Decision 4): precedence is the whole
    # decision. A critical failure is a known, actionable diagnosis and
    # DOMINATES an incomplete report -- not-run alongside a critical fail
    # still exits `1`, never `2`, because "we could not tell you" is a
    # weaker claim than a known failure the operator can already act on.
    critical_failed = any(r.status == "fail" and r.critical for r in results)
    incomplete = any(r.status == read_outcome.NOT_RUN for r in results)
    if critical_failed:
        raise typer.Exit(code=1)
    if incomplete:
        raise typer.Exit(code=2)


@app.command(
    help=(
        "Migrate legacy, frontmatter-embedded merge ledgers into "
        "bundle/.state/ledger/, and an OKF v0.1 bundle to v0.2 shape, "
        "refusing on any sign of a torn write, cross-survivor pollution "
        "risk, or a document that cannot be migrated deterministically."
    ),
    rich_help_panel="Maintain",
)
@_guard_workspace_lock("repair", commit_phase=True)
def repair() -> None:
    """Read-write migration verb, thin over `application.repair` (ADR-0018,
    okf-v02-migration Phase 6): extracts every survivor's OWN frontmatter-
    embedded `merged_from` ledger (pre-relocation, unmigrated) into its
    `bundle/.state/ledger/` sidecar, VERBATIM, strips the `merged_from` key
    from the survivor's own frontmatter, and migrates every OKF v0.1-shaped
    concept document and merge-ledger sidecar to v0.2 shape (legacy
    `timestamp` -> `generated`, `status: active` -> `stable`, `sources`
    (re)generated from `provenance`, a bare empty `# Citations` heading
    removed) -- flipping `bundle/index.md`'s `okf_version` to `"0.2"` in the
    SAME commit as every rewritten concept, never a separate later step.

    Every refusal happens in `application.repair.plan_repair`, before any
    write, with NO override flag at all (unlike `merge`'s `--force`):
    migrating a corrupted ledger or an ambiguous document verbatim would
    convert a git-revertible bug into a permanent durable fact, so this
    verb is deliberately MORE conservative than `merge`/`unmerge`'s own
    refusals.

    Gate 1 (Check A, torn write): any `.pending` marker anywhere in the
    bundle refuses the WHOLE run -- `openkos doctor` names the affected
    survivor(s); `openkos repair` does not repair a torn write itself
    (`bundle_ledger.recover` does, on the NEXT `merge`/`unmerge` that
    touches that survivor).

    Gate 2 (cross-survivor-pollution gate, design Decision 5, scoped by the
    `entity-resolution-merge` delta's Decision 2): refuses the WHOLE run
    whenever ANY survivor bundle-wide -- migrated OR unmigrated -- carries
    2 or more entries, regardless of what Check B's per-ledger nested-
    prefix check would have found on its own, but ONLY when this run has
    at least one pre-relocation, frontmatter-embedded ledger left to
    extract. A bundle whose ledgers are already relocated to sidecars can
    still be OKF-migrated even if a twice-merged survivor's sidecar carries
    2+ entries. Deliberately coarser than Check B: a merge of X into Y can
    rewrite bytes inside a THIRD survivor Z's embedded snapshot
    (`merge_core`'s `other_files`, `cli/main.py:6542`), a corruption Check
    B cannot see at every index.

    Before writing, reports whether this run's own effect is undoable via
    `git reset --hard` (`vcs_git.has_reset_point`, the same gap-fix probe
    `doctor` uses): `_autocommit` is best-effort and silently no-ops with
    no repo, no configured git identity, or any `GitError`/`OSError`, so a
    workspace that never committed has no safety net for THIS run either.

    Writes, in order (torn-write safety, design.md Decision 9): ledger
    extraction -> sidecar OKF migrations -> concept documents ->
    `index.md`'s `okf_version` flip LAST, so a crash never leaves a bundle
    claiming v0.2 while holding v0.1 documents; each artifact is idempotent,
    so a re-run completes an interrupted migration.
    """
    root = Path.cwd()
    workspace_reason = config.require_workspace(root)
    if workspace_reason is not None:
        typer.echo(f"openkos repair: refusing to run -- {workspace_reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    bundle_dir = layout.bundle_dir

    plan = application_repair.plan_repair(bundle_dir)
    if isinstance(plan, application_repair.RepairRefusal):
        typer.echo(plan.message, err=True)
        raise typer.Exit(code=1)

    # deprecated-status export, issue #1075: reported unconditionally, even
    # when there is otherwise `nothing to repair` below -- BLOCKED and a
    # skipped withdrawal are never written (spec: "MUST be reported by
    # count and id" / "MUST be reported"), so their disclosure cannot wait
    # behind the has_work early return.
    if plan.blocked_export_ids:
        n = len(plan.blocked_export_ids)
        typer.echo(
            f"openkos repair: blocked -- {n} concept(s) superseded but not "
            "exported (own status is neither absent/stable/legacy active "
            f"nor an existing valid export): {', '.join(plan.blocked_export_ids)}"
        )
    if plan.skipped_withdrawal_ids:
        n = len(plan.skipped_withdrawal_ids)
        typer.echo(
            f"openkos repair: skipped -- {n} withdrawal(s) could not be "
            "confirmed (the edge walk is incomplete): "
            f"{', '.join(plan.skipped_withdrawal_ids)}"
        )

    if not plan.has_work:
        typer.echo(
            "openkos repair: nothing to repair -- no unmigrated merge "
            "ledger, no OKF 0.1 content, and no deprecated-status export "
            "drift found."
        )
        return

    if vcs_git.repo_root(root) is not None and vcs_git.has_reset_point(root):
        typer.echo(
            "openkos repair: this run's writes can be undone with `git "
            "reset --hard HEAD` before this run's own auto-commit lands, "
            "or `git reset --hard <commit-before-this-run>` after."
        )
    else:
        typer.echo(
            "openkos repair: WARNING -- no git reset point is available in "
            "this workspace (no repository, no configured git identity, or "
            "no commit history); this run's writes cannot be undone via "
            "git.",
            err=True,
        )

    # The commit phase (#1137): `plan_repair`'s whole-bundle walk above held no
    # workspace lock. Issue #313's precedent: every byte in `plan.baselines`
    # was computed from `plan_repair`'s own reads, so re-validate each target
    # now -- before the first write -- exactly like every other mutating verb;
    # the read dependencies (the documents whose `supersedes` edges decided an
    # export) are re-validated with them.
    with _commit_section_for(root)():
        _reject_drifted_targets(
            layout, {**plan.read_dependencies, **plan.baselines}, "repair"
        )

        try:
            outcome = application_repair.apply_repair(root, plan)
        except (OSError, ValueError) as exc:
            typer.echo(
                f"openkos repair: failed while writing the migration -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc

        if plan.extraction:
            n = len(plan.extraction)
            typer.echo(
                f"openkos repair: migrated {n} ledger{'s' if n != 1 else ''} to "
                "bundle/.state/ledger/."
            )
        # A rewrite whose ONLY change is its deprecated-status export (issue
        # #1075) touched no OKF v0.1->v0.2 migration rule at all, so it must
        # not inflate this "migrated N documents to OKF 0.2" count -- filtered
        # to rewrites where at least one migration rule actually fired.
        migrated_rewrites = [
            rewrite
            for rewrite in plan.document_rewrites
            if rewrite.changes.generated
            or rewrite.changes.status
            or rewrite.changes.sources
            or rewrite.changes.citations_removed
        ]
        if migrated_rewrites:
            n = len(migrated_rewrites)
            generated = sum(rewrite.changes.generated for rewrite in migrated_rewrites)
            status = sum(rewrite.changes.status for rewrite in migrated_rewrites)
            sources = sum(rewrite.changes.sources for rewrite in migrated_rewrites)
            citations_removed = sum(
                rewrite.changes.citations_removed for rewrite in migrated_rewrites
            )
            typer.echo(
                f"openkos repair: migrated {n} document{'s' if n != 1 else ''} to "
                f"OKF 0.2 (generated: {generated}, status: {status}, sources: "
                f"{sources}, empty # Citations removed: {citations_removed})."
            )
        exported = sum(
            1
            for rewrite in plan.document_rewrites
            if rewrite.changes.export is okf.ExportOutcome.EXPORT
        )
        withdrawn = sum(
            1
            for rewrite in plan.document_rewrites
            if rewrite.changes.export is okf.ExportOutcome.WITHDRAW
        )
        dropped_marker = sum(
            1
            for rewrite in plan.document_rewrites
            if rewrite.changes.export is okf.ExportOutcome.DROP_MARKER
        )
        if exported or withdrawn or dropped_marker:
            typer.echo(
                f"openkos repair: deprecated-status export -- {exported} "
                f"exported, {withdrawn} withdrawn, {dropped_marker} marker(s) "
                "dropped."
            )
        if plan.sidecar_rewrites:
            n = len(plan.sidecar_rewrites)
            typer.echo(
                f"openkos repair: migrated {n} merge-ledger sidecar"
                f"{'s' if n != 1 else ''} to OKF 0.2."
            )
        if plan.index_new_text is not None:
            typer.echo("openkos repair: okf_version 0.1 -> 0.2 in bundle/index.md.")
        if plan.legacy_citations_ids:
            n = len(plan.legacy_citations_ids)
            noun = "document" if n == 1 else "documents"
            verb = "keeps" if n == 1 else "keep"
            typer.echo(
                f"openkos repair: left in place -- {n} {noun} {verb} a "
                "hand-written # Citations list (legacy, OKF 0.2 section 13.1): "
                f"{', '.join(plan.legacy_citations_ids)}"
            )

        parts: list[str] = []
        if plan.extraction:
            parts.append(
                f"migrate {len(plan.extraction)} ledger(s) to bundle/.state/ledger/"
            )
        if (
            plan.document_rewrites
            or plan.sidecar_rewrites
            or plan.index_new_text is not None
        ):
            parts.append(
                f"migrate {len(plan.document_rewrites)} document(s) and "
                f"{len(plan.sidecar_rewrites)} ledger sidecar(s) to OKF 0.2"
            )

        _autocommit(root, outcome.touched, f"openkos: repair ({'; '.join(parts)})")
    _refresh_derived_after_write(layout, None, verb="repair")


@app.command(
    help=(
        "Work through every pending decision in one guided session, in "
        "dependency order, so each answer informs the next."
    ),
    rich_help_panel="Curate",
)
@_guard_workspace_lock("curate")
def curate(
    auto: bool = typer.Option(
        False,
        "--auto",
        help=(
            "Auto-accept every stage's cost gate (model spend, never a "
            "per-item write consent -- see the write-consent note below)."
        ),
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
    include_deprecated: bool = typer.Option(
        False,
        "--include-deprecated",
        help="Include deprecated and superseded concepts (excluded by default).",
    ),
    accept: str | None = typer.Option(
        None,
        "--accept",
        metavar="STAGES",
        help=(
            "Comma-separated stages whose per-item prompts are accepted in "
            "bulk (structure, metadata). Identity is never accepted in "
            "bulk: its merges delete a concept."
        ),
    ),
    no_reconcile: bool = typer.Option(
        False,
        "--no-reconcile",
        help=(
            "Skip the reconciliation pass (#645) on Identity's merges: keep "
            "the merged body as the survivor's text with the absorbed text "
            "appended under a '## Merged content' heading, with no model "
            "call. The same opt-out `merge` takes."
        ),
    ),
    reconcile: bool = typer.Option(
        False,
        "--reconcile",
        help=(
            "Force the reconciliation pass (#645) on Identity's merges, "
            "even below the share and merged-length thresholds that decide "
            "it by default. The same opt-in `merge` takes. Refused together "
            "with --no-reconcile."
        ),
    ),
) -> None:
    """One dependency-ordered decision session over the five kinds of
    pending human judgment: Preconditions, Identity, Structure, Metadata,
    Contradictions (ADR-0005/ADR-0011 ordering; issue #266).

    A THIN command, mirroring `next`'s shape: the shared
    `config.require_workspace` gate, then `config.read_config`
    (`except (OSError, ValueError)`, the same lint parity every other verb
    keeps), then `observability.warn_if_walk_incomplete` exactly ONCE for
    the whole run (design D8) -- never per stage, since five identical
    incomplete-walk paragraphs in one session would be noise. The entire
    ranked engine -- `_STAGES`, the cost gate, the sequencer, and the
    end-of-run summary -- lives in `cli/curate.py`; this command builds one
    `CurateContext`, calls `run_curate`, and echoes `render_summary`
    verbatim.

    Preconditions probes `vectors.db` before Identity: missing or empty, it
    prints the starved-candidate-edges consequence plus an `openkos
    reindex` pointer and halts the ENTIRE run (exit 0, no later stage runs
    -- spec: Preconditions Stage Halts The Run). Every other stage's
    decline, empty queue, or `live=False` skip is scoped to that stage
    alone and never aborts the rest (spec: Stage Order Is A Product
    Invariant).

    Each LLM-costing stage prints its own cost line (`{n} {noun}(s) -> {n}
    LLM call(s)`) and asks for confirmation before contacting the model,
    unless `--auto` is passed; `--auto` consents to model SPEND only, NEVER
    to a per-item write. On a non-TTY run with `--auto`, a write stage
    (`writes: true`, e.g. Identity) declines its write walk and prints the
    corresponding standalone verb for unattended use instead
    (`openkos adjudicate --apply-same --confirm-count <n>`), while a
    read-only stage (Contradictions) runs and reports normally (spec:
    Per-Stage Cost Gate).

    Identity reuses the exact `find_candidates` / `adjudicate_candidates` /
    `_prepare_one_merge` / `merge_service.commit_merge` / `_reject_drifted_targets`
    building blocks `adjudicate --apply` already exercises (design D4/D6):
    an accepted SAME 2-member pair commits per-item, auto-committing before
    the next candidate; an N>2 group is never auto-merged -- the exact
    pairwise `openkos merge` commands are printed instead (spec: Identity
    Stage Reuses Merge Cores).

    `--include-confidential`/`--include-deprecated` are forwarded into
    every stage's underlying call, fail-closed by default (spec:
    Sensitivity Threading Is Fail-Closed).

    All five stages run fully as of slice 2 (design D10): Preconditions and
    Identity shipped in slice 1; Structure, Metadata, and Contradictions
    went `live=True` in slice 2 with real `probe`/`run` implementations, so
    each now states its cost and, once accepted, spends real model calls
    (spec: Slice Boundary). `curate` is not a CI gate: pending work never
    sets a non-zero exit. Exit codes: 0 normal (including every decline,
    empty queue, and the Preconditions halt), 1 on a workspace/config
    failure or a failed mid-walk write, 2 on a Typer usage error, 3 on a
    drift refusal (#319, propagated unchanged from
    `_reject_drifted_targets`)."""
    if reconcile and no_reconcile:
        # #803: rejected up front, before any workspace gate or read,
        # matching the shape `adjudicate` uses for its contradictory flag
        # pairs.
        typer.echo(f"openkos curate: {_RECONCILE_CONFLICT_MESSAGE}", err=True)
        raise typer.Exit(code=2)

    # `--accept`'s vocabulary is checked BEFORE the workspace gate, so a
    # typo is reported as itself rather than as a missing workspace --
    # `list`'s TYPE and `set-volatility`'s tier already refuse in this
    # order (issue #385).
    explicit_accept = curate_module.parse_accepted_stages(accept)

    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos curate: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos curate: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    # Resolved from a client identical to the one `run_curate` builds lazily
    # for its `needs_llm` stages (same `cfg.model`, same host resolution),
    # because the stage PROBES apply the sensitivity filter before any
    # client exists -- see `CurateContext.local_exemption` (issue #240).
    # Constructing a client performs no I/O. Resolved BEFORE
    # `warn_if_walk_incomplete` (not just before `CurateContext`) so the
    # advisory can be told about this hatch too, the same way the other
    # five verbs already are.
    accepted_stages = curate_module.resolve_accepted_stages(
        explicit_accept, review=cfg.review
    )
    local_exemption = _resolve_local_exemption(
        cast(application_backends.HasLocality, _chat_client(cfg)), cfg
    )
    observability.warn_if_walk_incomplete(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )

    ctx = curate_module.CurateContext(
        root=root,
        layout=layout,
        cfg=cfg,
        auto=auto,
        include_confidential=include_confidential,
        include_deprecated=include_deprecated,
        local_exemption=local_exemption,
        accepted_stages=accepted_stages,
        no_reconcile=no_reconcile,
        reconcile=reconcile,
        backend_factories=_backend_factories(),
    )
    outcomes = curate_module.run_curate(ctx)
    for line in curate_module.render_summary(outcomes):
        typer.echo(line)

    # #640: once at END of run, only when some stage actually applied a
    # write -- an all-declined/empty session invalidated nothing. NOT per
    # stage and NOT inside `merge_service.commit_merge` (Identity commits per item).
    if any(outcome.applied for outcome in outcomes):
        _refresh_derived_after_write(layout, cfg, verb="curate")


@app.command(
    "mcp",
    help="Serve this workspace to an MCP client over stdio (read-only).",
    rich_help_panel="Explore",
)
def mcp_cmd(
    workspace: Path = typer.Option(
        Path(),
        "--workspace",
        help="Workspace directory to serve (default: the current directory).",
    ),
    expose_confidential: bool = typer.Option(
        False,
        "--expose-confidential",
        help=(
            "Disclose confidential objects to the connecting client. Read "
            "once at launch, with no per-request override. Off by "
            "default, meaning every confidential object is withheld."
        ),
    ),
) -> None:
    """Serve one workspace over stdio to a Model Context Protocol client,
    exposing four read-only tools: `query`, `get`, `navigate`, `pending`
    (design Decision 14). Never writes, never acquires the workspace
    lock -- `mcp` is read-only (`_READ_ONLY_COMMANDS`), the same class
    `status`/`next`/`list`/`lint`/`doctor` belong to.

    `--workspace` (default: the current directory) is validated with the
    same `config.require_workspace` + `config.read_config` gate every read
    command uses, and BEFORE any stdio activity starts -- an invalid
    workspace exits 1 with the refusal on stderr and never emits a
    protocol frame on stdout, matching the "Workspace root selection"
    threat-matrix row.

    `--expose-confidential` is read ONCE, here, at launch: there is no
    per-request override anywhere in the protocol surface this command
    hands off to. Confidential objects stay withheld unless this flag was
    passed.

    `openkos.mcp` is imported LAZILY, inside this function's own body, so
    `asyncio` never loads on any other verb's ordinary startup path
    (`tests/unit/mcp/test_layering.py` pins the import-shape half;
    `tests/unit/cli/test_mcp_cmd.py` pins that `openkos.mcp` stays absent
    from `sys.modules` after a plain `import openkos.cli.main`).
    """
    root = workspace.resolve()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos mcp: refusing to serve -- {reason}.", err=True)
        raise typer.Exit(code=1)

    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos mcp: failed while reading the workspace -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    # Advisory only (issue #199), mirroring `query`/`ingest`/`reindex`: the
    # embedding client is constructed only to read its resolved locality --
    # it makes no network call here, and `query` (slice 9) is what actually
    # uses one during serving.
    embedder = _embed_client(cfg)
    _warn_if_nonlocal_embed_host(
        "mcp", cast(BackendDiagnostics, embedder).locality, cfg
    )

    from openkos.mcp import (
        server as mcp_server,  # lazy: asyncio stays off every other verb
    )

    raise typer.Exit(
        code=mcp_server.serve(root, expose_confidential=expose_confidential)
    )


PANEL_ORDER: Final[tuple[str, ...]] = (
    "Get started",
    "Explore",
    "Curate",
    "Maintain",
    "Remove",
)
"""The order help panels are printed in (#389).

Grouping alone did not fix the ordering half of that issue. Rich prints
panels in the order it FIRST meets a command belonging to each, which is
declaration order -- and `forget`/`purge` are declared early, so "Remove"
landed second and made the irreversible verbs MORE prominent than the flat
list did. This is the reading order instead: start, then ask, then decide,
then maintain, and only then delete.

Sorting the registry is what makes it explicit rather than a side effect of
where a function happens to sit in this file. The sort is stable, so order
WITHIN a panel is still declaration order."""


def _panel_rank(info: typer.models.CommandInfo) -> int:
    """Rank one command for the panel sort, failing LOUDLY and legibly.

    A bare `PANEL_ORDER.index(...)` raises at import time, which takes the
    whole CLI down -- every command, including `--help` -- for one typo, and
    the built-in message names neither the command nor the bad value
    (review finding on this change). Failing hard is still right: a
    misplaced command should not ship quietly. What was wrong was failing
    hard and mutely."""
    try:
        return PANEL_ORDER.index(info.rich_help_panel)
    except ValueError:
        name = info.name or (info.callback.__name__ if info.callback else "<unnamed>")
        raise RuntimeError(
            f"command {name!r} declares rich_help_panel="
            f"{info.rich_help_panel!r}, which is not one of {PANEL_ORDER}. "
            "Put the command in an existing panel, or add the new panel to "
            "PANEL_ORDER at the position it should be read in."
        ) from None


app.registered_commands.sort(key=_panel_rank)
