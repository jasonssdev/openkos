"""Chat-client construction, endpoint resolution, API-key reading, and
local-exemption resolution (issue #1057 Phase 9, design Decision 4-6, 8;
mcp-read-surface slice 8): the definitions `cli/main.py`'s
`_chat_client`/`_resolve_local_exemption` used to own directly, relocated
here so a non-CLI adapter (the MCP server) can build a chat backend without
importing `openkos.cli`. `cli/main.py` keeps both names as one-line
delegators.

Backend selection, endpoint resolution and API-key reading are all owned
HERE (design Decision 4): the concrete classes never live in this module --
they arrive in a `BackendFactories` value each adapter builds from its own
module globals at call time (`cli/main.py::_backend_factories()`,
`mcp/server.py::_backend_factories()`). `cli/curate.py` no longer
constructs a client of its own either (Phase 9): its stage loop calls
`chat_client(ctx.cfg, factories=ctx.backend_factories, task=stage.task)`
exactly like every other chat verb, which is what retires the "curate is
the stated exception" note this module's docstring used to carry.

Imports `config`/`os`/`typing`/`dataclasses` and, for the return-type
Protocols only, `openkos.llm.base` (`LLMBackend`/`Embedder`/
`BackendDiagnostics`) -- never a concrete `openkos.llm.*` backend module, so
`tests/unit/application/test_layering.py`'s "no concrete backend bound
inside application/" guard holds for this module exactly as it holds for
every other one under `application/` (that guard permits exactly
`openkos.llm.base`, the Protocol seam). The concrete client class always
arrives as an injected `factory`/`factories` argument; every adapter builds
its backend through this same, non-CLI-importable definition. A
source-derived guard
(`tests/unit/test_backend_construction_guard.py`) additionally fails if
`OllamaClient(`/`OpenAICompatibleClient(` is called anywhere under
`src/openkos/` outside `llm/` and the `BackendFactories(...)` expressions
those two `_backend_factories()` functions build -- so "every construction
goes through the resolver" is enforced, not remembered."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from openkos import config
from openkos.llm.base import (
    BackendDiagnostics,
    Embedder,
    LLMBackend,
    classify_backend_host,
)

BACKEND_OLLAMA: Literal["ollama"] = "ollama"
"""The `cfg.backend` value naming the Ollama family, mirroring
`config.DEFAULT_BACKEND` -- kept as its own constant here (rather than
importing `config.DEFAULT_BACKEND` at every dispatch site) so this
module's own backend-dispatch branches read as comparisons against a named
family, not a re-derivation of the packaged default."""

BACKEND_OPENAI_COMPATIBLE: Literal["openai-compatible"] = "openai-compatible"
"""The `cfg.backend` value naming the OpenAI-compatible family (issue
#1057). Not yet in `config.SELECTABLE_BACKENDS` -- `read_config` still
refuses it until Phase 14 -- but the resolver itself must recognize and
dispatch on this value from Phase 9 onward, since a `Config` constructed
directly (bypassing `read_config`, as this module's own tests and future
evals do) can already carry it."""

API_KEY_ENV: Literal["OPENKOS_OPENAI_API_KEY"] = "OPENKOS_OPENAI_API_KEY"
"""The ONLY environment variable an `openai-compatible` API key is ever
read from (backend-selection spec: "The API Key Is Read Only From An
Environment Variable"). Never `OPENAI_API_KEY` (no prefix) -- that name is
never read anywhere in this module, on purpose (Threat Matrix "Credential
confusion")."""

EndpointSource = Literal["OLLAMA_HOST", "embedding_base_url", "base_url", "default"]
"""Which precedence rung `resolve_endpoint` resolved an endpoint from
(design Decision 5). `doctor` (Phase 12) reads this to disclose the
effective endpoint's source; `resolve_endpoint`'s own docstring owns the
full precedence table."""


class _Locality(Protocol):
    @property
    def is_local(self) -> bool: ...


class HasLocality(Protocol):
    """The one property `resolve_local_exemption` reads off a chat client --
    deliberately narrower than `llm.base.LLMBackend`'s full Protocol, so
    this module needs no `openkos.llm.*` import at all, concrete or
    otherwise."""

    @property
    def locality(self) -> _Locality: ...


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One resolved endpoint: the URL to pass the client constructor (or
    `None` when the client should resolve its own default/env-var, design
    Decision 5), and which precedence rung produced it."""

    url: str | None
    source: EndpointSource


@dataclass(frozen=True, slots=True)
class BackendFactories:
    """The concrete client classes for both backend families, injected by
    the caller (design Decision 4): this module binds no concrete backend
    of its own, so it stays importable by an adapter that must not import
    `openkos.cli` (the MCP server) or drag `openkos.llm.ollama`/
    `openkos.llm.openai_compatible` into `application/`'s own layering
    guard (`tests/unit/application/test_layering.py`).

    Each adapter builds exactly one of these, from its OWN module globals,
    at call time -- `cli/main.py::_backend_factories()` and
    `mcp/server.py::_backend_factories()` -- so every existing test that
    patches a concrete class by name in one of those two modules keeps
    intercepting."""

    ollama: Callable[..., Any]
    openai_compatible: Callable[..., Any]


def resolve_endpoint(
    cfg: config.Config,
    *,
    purpose: Literal["chat", "embed"],
    environ: Mapping[str, str] = os.environ,
) -> Endpoint:
    """Resolve the effective endpoint for `cfg.backend`/`purpose` (design
    Decision 5, backend-selection spec "Endpoint Resolution Precedence"):

    | backend | purpose | precedence (first non-empty wins) |
    | --- | --- | --- |
    | `ollama` | chat | `OLLAMA_HOST` > `base_url` > default |
    | `ollama` | embed | `OLLAMA_HOST` > `embedding_base_url` > `base_url` > default |
    | `openai-compatible` | chat | `base_url` (required) |
    | `openai-compatible` | embed | `embedding_base_url` > `base_url` |

    `url` is `None` whenever the winning rung is `OLLAMA_HOST` or
    `default` -- the Ollama client resolves both cases itself, exactly as
    it does today (`OllamaClient.__init__`'s own `host` precedence); `url`
    carries the actual value only when a config key won, so a caller can
    tell "pass `host=`/`base_url=`" apart from "let the client resolve its
    own default" (task 9.8's byte-identity requirement).

    `OLLAMA_HOST` is NEVER consulted for `backend="openai-compatible"`
    (Threat Matrix "Locality"/"Credential confusion";
    `test_resolve_endpoint_openai_compatible_never_consults_ollama_host`):
    the `environ` lookup below sits behind the Ollama-only branch, so an
    `openai-compatible` config can never even reach it. `openai-compatible`
    has no packaged default for either purpose -- an absent `base_url` is a
    `read_config`-time refusal in the normal path; a `Config` built
    directly (bypassing that refusal, as this resolver's own tests and
    future evals do) still resolves defensively to `source="base_url"`
    with `url=None` rather than raising here, since raising would duplicate
    `read_config`'s own validation in a second place."""
    if cfg.backend == BACKEND_OPENAI_COMPATIBLE:
        if purpose == "embed" and cfg.embedding_base_url:
            return Endpoint(url=cfg.embedding_base_url, source="embedding_base_url")
        return Endpoint(url=cfg.base_url, source="base_url")

    if environ.get("OLLAMA_HOST"):
        return Endpoint(url=None, source="OLLAMA_HOST")
    if purpose == "embed" and cfg.embedding_base_url:
        return Endpoint(url=cfg.embedding_base_url, source="embedding_base_url")
    if cfg.base_url:
        return Endpoint(url=cfg.base_url, source="base_url")
    return Endpoint(url=None, source="default")


def _read_api_key(environ: Mapping[str, str]) -> str | None:
    """Read the `openai-compatible` API key from `environ` (design Decision
    8, backend-selection spec "The API Key Is Read Only From An Environment
    Variable"): stripped, empty/whitespace-only treated as absent.

    Reads `API_KEY_ENV` (`OPENKOS_OPENAI_API_KEY`) ONLY -- never
    `OPENAI_API_KEY` (Threat Matrix "Credential confusion"): a user with
    that variable already exported for an unrelated cloud tool must not
    have its value silently forwarded, as a credential, to whatever
    `base_url` names."""
    raw = environ.get(API_KEY_ENV, "")
    stripped = raw.strip()
    return stripped or None


def _apply_endpoint_host(kwargs: dict[str, Any], endpoint: Endpoint) -> None:
    """Add `host=` to `kwargs` only when `endpoint.source` is a config key
    (`base_url`/`embedding_base_url`) -- never for `OLLAMA_HOST`/`default`,
    so the Ollama factory's default-path kwargs stay byte-identical (task
    9.8/9.9; `test_backends_delegation.py` and `test_chat_timeout_wiring.py`
    assert the exact kwargs)."""
    if endpoint.source not in ("OLLAMA_HOST", "default"):
        kwargs["host"] = endpoint.url


def chat_client(
    cfg: config.Config,
    *,
    factories: BackendFactories,
    task: str | None = None,
    environ: Mapping[str, str] = os.environ,
) -> LLMBackend:
    """Build the CHAT client for a workspace, honoring its `chat_timeout`.

    Every chat verb goes through here (issue #405). Constructing
    `OllamaClient(model=cfg.model)` inline instead reads fine and silently
    ignores the workspace's `chat_timeout`, falling back to the transport
    default -- a defect no type checker or ordinary test can see, across the
    eight-odd verbs that make chat calls. One seam means a workspace that
    raises its deadline raises it for `ingest`, `curate`, `query`,
    `adjudicate`, `suggest-relations`, and `contradictions` alike, rather
    than for whichever verbs someone remembered.

    Deliberately NOT used for the two other client kinds: embedding clients
    (`embed_client`, `model=cfg.embedding_model`) keep the transport
    default, and the liveness probes (`diagnostics_client`) keep
    `_PREFLIGHT_TIMEOUT`, which answers "is anything listening" and must
    stay short -- a 600s probe would hang the CLI on a firewalled host
    instead of failing fast.

    `curate.py`'s stage loop calls this same function (Phase 9) --
    `main.py` already imports `curate`, so `tests/unit/cli/
    test_chat_timeout_wiring.py` pins both call sites so the pair cannot
    drift.

    Also honors `cfg.max_generation_tokens` (issue #422): the safety rail
    on how much a single chat call may GENERATE, distinct from
    `chat_timeout`'s bound on how long the client WAITS.

    `task` (issue #515) names which measured task this client is for, so
    `config.resolve_task_model` can honor a `models:` override. Omitting it
    keeps `cfg.model` -- and the two callers that omit it do so
    deliberately, not by oversight:

    - `query` synthesizes an answer, which is NOT one of the five keys in
      `TASK_MODEL_KEYS`. It has no harness, so #508's rule ("a per-task
      default must be justified on a fixture") forbids inventing a key for
      it here.
    - `curate`'s `_resolve_local_exemption` probe asks the client for its
      `locality`, a property of the HOST it would connect to. That answer
      is identical whichever model tag the client carries, so resolving a
      task model for it would imply a per-task locality that does not
      exist.

    The per-task tag changes WHICH model runs and nothing else: both safety
    rails still apply, which matters most precisely for the large models
    #516's sweep favors at edge typing.

    Also honors `cfg.temperature`/`cfg.seed` (issue #1013): the sampling
    pins `OllamaClient` already forwards as `options.temperature`/
    `options.seed` (`llm/ollama.py`) once handed a non-`None` value. Both
    default to `None`, so an unset workspace sends a request byte-identical
    to before this key existed; pinning them REDUCES run-to-run variance, it
    does not guarantee identical output.

    `factories` carries both concrete client classes, injected by the
    caller (design Decision 4): this module binds no concrete backend of
    its own, so it stays importable by an adapter that must not import
    `openkos.cli`. Dispatch is by `cfg.backend` (backend-selection spec
    "Chat construction dispatches by cfg.backend"): the `openai-compatible`
    factory additionally receives `base_url=`/`api_key=`, resolved by
    `resolve_endpoint`/`_read_api_key`; the Ollama factory receives `host=`
    only when `resolve_endpoint` reports a config-key source (task
    9.8/9.9), so the default path's kwargs stay byte-identical to before
    this backend existed. An API key is read from `environ` and passed
    ONLY to the `openai_compatible` factory call (task 9.17) -- the Ollama
    factory's kwargs never carry one.
    """
    endpoint = resolve_endpoint(cfg, purpose="chat", environ=environ)
    if cfg.backend == BACKEND_OPENAI_COMPATIBLE:
        return cast(
            LLMBackend,
            factories.openai_compatible(
                model=config.resolve_task_model(cfg, task),
                timeout=cfg.chat_timeout,
                max_generation_tokens=cfg.max_generation_tokens,
                context_window=cfg.context_window,
                temperature=cfg.temperature,
                seed=cfg.seed,
                base_url=endpoint.url,
                api_key=_read_api_key(environ),
            ),
        )
    host_kwargs: dict[str, Any] = {}
    _apply_endpoint_host(host_kwargs, endpoint)
    return cast(
        LLMBackend,
        factories.ollama(
            model=config.resolve_task_model(cfg, task),
            timeout=cfg.chat_timeout,
            max_generation_tokens=cfg.max_generation_tokens,
            context_window=cfg.context_window,
            temperature=cfg.temperature,
            seed=cfg.seed,
            **host_kwargs,
        ),
    )


def embed_client(
    cfg: config.Config,
    *,
    factories: BackendFactories,
    environ: Mapping[str, str] = os.environ,
) -> Embedder:
    """Build the EMBEDDING client for a workspace (design Decision 4,
    backend-selection spec "embed_client mirrors chat_client's dispatch
    shape"): mirrors `chat_client`'s dispatch shape exactly, resolving the
    EMBED endpoint (`purpose="embed"`) instead of the chat one.

    Passes `model=cfg.embedding_model` only -- no `chat_timeout`, no
    per-task model resolution (task 9.13, design's Data Flow note):
    embedding clients keep the transport default timeout, and there is no
    per-task embedding model to resolve. `context_window`/`temperature`/
    `seed`/`max_generation_tokens` are chat-only sampling/generation
    controls that do not apply to an embedding request either.

    Like `chat_client`, the `openai-compatible` factory additionally
    receives `base_url=`/`api_key=`, and the Ollama factory receives
    `host=` only when `resolve_endpoint` reports a config-key source."""
    endpoint = resolve_endpoint(cfg, purpose="embed", environ=environ)
    if cfg.backend == BACKEND_OPENAI_COMPATIBLE:
        return cast(
            Embedder,
            factories.openai_compatible(
                model=cfg.embedding_model,
                base_url=endpoint.url,
                api_key=_read_api_key(environ),
            ),
        )
    host_kwargs: dict[str, Any] = {}
    _apply_endpoint_host(host_kwargs, endpoint)
    return cast(Embedder, factories.ollama(model=cfg.embedding_model, **host_kwargs))


def diagnostics_client(
    cfg: config.Config | None,
    *,
    model: str,
    timeout: float,
    factories: BackendFactories,
    purpose: Literal["chat", "embed"] = "chat",
    environ: Mapping[str, str] = os.environ,
) -> BackendDiagnostics:
    """Build a liveness-probe/diagnostics client (task 9.14-9.15), usable by
    the init picker, init preflight and doctor's reachability probe (Phase
    10 wiring): unlike `chat_client`/`embed_client`, the caller supplies
    `model`/`timeout` directly (a probe's model tag and timeout are its own
    concern, e.g. `_PREFLIGHT_TIMEOUT`, never `cfg.chat_timeout`).

    `cfg=None` defaults to Ollama with no endpoint resolution at all (O1):
    several probe sites run before any workspace config exists (`init`'s
    picker). When `cfg` is given, dispatches by `cfg.backend` exactly like
    `chat_client`/`embed_client`, resolving `purpose`'s endpoint and (for
    `openai-compatible`) the API key."""
    if cfg is None:
        return cast(BackendDiagnostics, factories.ollama(model=model, timeout=timeout))
    endpoint = resolve_endpoint(cfg, purpose=purpose, environ=environ)
    if cfg.backend == BACKEND_OPENAI_COMPATIBLE:
        return cast(
            BackendDiagnostics,
            factories.openai_compatible(
                model=model,
                timeout=timeout,
                base_url=endpoint.url,
                api_key=_read_api_key(environ),
            ),
        )
    host_kwargs: dict[str, Any] = {}
    _apply_endpoint_host(host_kwargs, endpoint)
    return cast(
        BackendDiagnostics,
        factories.ollama(model=model, timeout=timeout, **host_kwargs),
    )


def resolve_local_exemption(client: HasLocality, cfg: config.Config) -> bool:
    """The ONE place the confidential local exemption is decided (issue
    #240): `True` only when `client`'s own resolved host is verifiably this
    machine AND the workspace has not opted out.

    Both terms are required and neither is inferable from the other. The
    workspace key alone is a POLICY (`confidential_local_exemption`, default
    `true`); the client's `locality.is_local` is a verified FACT about the
    host this command's `llm.chat` will actually reach. An exemption granted
    on policy alone would rest on an assumption, which is precisely what
    #240 refuses.

    `client`, not `os.environ["OLLAMA_HOST"]`, because the two answer
    different questions: the env read ignores an explicit `host=` argument,
    so a client aimed at a remote host while `OLLAMA_HOST` happens to be
    loopback would be granted an exemption for a send that leaves the
    machine. Reading the client that will do the sending closes that by
    construction.

    Fails closed on every axis and never raises: `classify_backend_host`
    degrades an unparseable or unrecognized host to non-local, so unknown
    locality is treated as remote. The returned boolean is threaded into the
    five `llm.chat` seams as `local_exemption=`; what it MEANS there is
    `sensitivity.should_block`'s contract, never re-derived at a call
    site."""
    return client.locality.is_local and cfg.confidential_local_exemption


# ---------------------------------------------------------------------------
# issue #1057 Phase 13a -- backend-conditional wording (design Decision 9)
# and the insecure-key-over-plain-HTTP warning (Decision 8). Each function
# returns today's exact `ollama` bytes for `backend == "ollama"` (or a
# `None`/default `cfg`) -- a caller keeps its OWN surrounding sentence and
# calls the matching function ONLY for the backend-specific clause (Phase
# 13b). `doctor` (Phase 12) deliberately does NOT call any of these: it
# keeps its own command-form strings (`shutil.which("ollama")`'s three-way
# branching has no equivalent here) -- see tasks.md's "Wording functions
# land where consumed" decision.
# ---------------------------------------------------------------------------


def start_hint(cfg: config.Config | None) -> str:
    """The actionable clause for "the backend is not responding" messages
    (`query`/`adjudicate`/`suggest-relations`/`curate`, Phase 13b):
    ``Start it with `ollama serve` `` for `ollama` (byte-identical to every
    existing call site's own literal), or "Start your OpenAI-compatible
    server at <display_host>" for `openai-compatible`, naming the resolved
    CHAT endpoint's host (userinfo-redacted, #355) -- never a raw
    unclassified `base_url`."""
    if cfg is None or cfg.backend != BACKEND_OPENAI_COMPATIBLE:
        return "Start it with `ollama serve`"
    endpoint = resolve_endpoint(cfg, purpose="chat")
    display_host = classify_backend_host(endpoint.url).display_host
    return f"Start your OpenAI-compatible server at {display_host}"


def install_hint(cfg: config.Config | None, model: str) -> str:
    """The actionable clause for "this model is not available" messages:
    ``Pull it with `ollama pull <model>` `` for `ollama` (byte-identical),
    or a server-agnostic "make it available" clause for `openai-compatible`
    -- no `ollama pull` reference, since it does not apply across
    llama.cpp/LM Studio/vLLM/LocalAI."""
    if cfg is None or cfg.backend != BACKEND_OPENAI_COMPATIBLE:
        return f"Pull it with `ollama pull {model}`"
    return (
        f"Make sure your OpenAI-compatible server serves '{model}' (run "
        "`openkos doctor` to see the models it reports)"
    )


def endpoint_label(cfg: config.Config, *, purpose: Literal["chat", "embed"]) -> str:
    """Which config key/environment variable actually produced the
    effective endpoint -- `resolve_endpoint`'s own `.source`, reused rather
    than re-derived, so this can never disagree with what `chat_client`/
    `embed_client` actually resolved. For `ollama`, this is `OLLAMA_HOST`
    the one time a non-local Ollama endpoint could arise before this
    change, but now genuinely dynamic (`base_url` also qualifies); for
    `openai-compatible`, always `base_url`/`embedding_base_url`."""
    return resolve_endpoint(cfg, purpose=purpose).source


def backend_label(cfg: config.Config | None) -> str:
    """The backend family's display name: `"Ollama"` (default, matching
    every existing advisory's wording) or `"OpenAI-compatible server"`."""
    if cfg is None or cfg.backend != BACKEND_OPENAI_COMPATIBLE:
        return "Ollama"
    return "OpenAI-compatible server"


def insecure_key_warning(
    cfg: config.Config, *, environ: Mapping[str, str] = os.environ
) -> str | None:
    """A warning string when an `openai-compatible` API key is configured,
    the resolved CHAT endpoint is `http://`, and that endpoint classifies
    non-local (design Decision 8, Threat Matrix "Secret over plain HTTP") --
    `None` in every other case, including for `backend == "ollama"` (no key
    exists there) and whenever no key is set.

    Never includes the key's VALUE (task 13.12, sentinel-proof) -- only
    `locality.display_host`, already userinfo-redacted (#355). Pure and
    side-effect-free: callers (Phase 13b) own printing it, at most once per
    process."""
    if cfg.backend != BACKEND_OPENAI_COMPATIBLE:
        return None
    if _read_api_key(environ) is None:
        return None
    endpoint = resolve_endpoint(cfg, purpose="chat")
    url = endpoint.url or ""
    if not url.lower().startswith("http://"):
        return None
    locality = classify_backend_host(url)
    if locality.is_local:
        return None
    return (
        "warning: OPENKOS_OPENAI_API_KEY is being sent to a non-local server "
        f"({locality.display_host}) over plain HTTP -- consider using "
        "https:// or a loopback endpoint"
    )
