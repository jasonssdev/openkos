"""Chat-client construction and local-exemption resolution (mcp-read-surface
slice 8, design Decision 7): the definitions `cli/main.py`'s
`_chat_client`/`_resolve_local_exemption` used to own directly, relocated
here so a non-CLI adapter (the MCP server) can build a chat backend without
importing `openkos.cli`. `cli/main.py` keeps both names as one-line
delegators (`cli/curate.py`'s own client construction is the design's
stated exception and is left untouched).

Imports `config` and `typing` only -- no concrete `openkos.llm.*` backend,
so `tests/unit/application/test_layering.py`'s "no concrete backend bound
inside application/" guard holds for this module exactly as it holds for
every other one under `application/`. The concrete client class always
arrives as an injected `factory` argument; both the CLI and the MCP adapter
build their backend through this same, non-CLI-importable definition."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from openkos import config


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


def chat_client[ClientT](
    cfg: config.Config, *, factory: Callable[..., ClientT], task: str | None = None
) -> ClientT:
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
    (`model=cfg.embedding_model`) keep the transport default, and the
    liveness probes keep `_PREFLIGHT_TIMEOUT`, which answers "is anything
    listening" and must stay short -- a 600s probe would hang the CLI on a
    firewalled host instead of failing fast.

    `curate.py` builds its own client from `ctx.cfg` rather than calling
    this: `main.py` already imports `curate`, so the dependency cannot run
    the other way. `tests/unit/cli/test_chat_timeout_wiring.py` pins both
    sites so the pair cannot drift.

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

    `factory` is the concrete client class, injected by the caller
    (mcp-read-surface, design Decision 7): this module binds no concrete
    backend of its own, so it stays importable by an adapter that must not
    import `openkos.cli`.
    """
    return factory(
        model=config.resolve_task_model(cfg, task),
        timeout=cfg.chat_timeout,
        max_generation_tokens=cfg.max_generation_tokens,
        context_window=cfg.context_window,
        temperature=cfg.temperature,
        seed=cfg.seed,
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
