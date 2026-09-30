"""Direct unit tests for `openkos.application.backends` (mcp-read-surface
slice 8, design Decision 7): chat-client construction and local-exemption
resolution, relocated out of `cli/main.py` so a non-CLI adapter (the MCP
server) can build a backend without importing `openkos.cli`. The concrete
client class always arrives as an injected `factory` -- this module binds
no concrete `openkos.llm.*` backend of its own, matching every other
`application/` module's own layering guard
(`tests/unit/application/test_layering.py`).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import backends
from openkos.llm.openai_compatible import OpenAICompatibleClient


def _cfg(tmp_path: Path, **overrides: Any) -> config.Config:
    config.write_config(tmp_path)
    cfg = config.read_config(tmp_path)
    return dataclasses.replace(cfg, **overrides) if overrides else cfg


class _RecordingFactory:
    """A structural stand-in for a chat client class: records the exact
    kwargs it was constructed with -- never a real `OllamaClient`, so this
    test proves the composition, not the transport."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def test_chat_client_passes_six_kwargs(tmp_path: Path) -> None:
    """`chat_client(cfg, factory=recording_factory, task=...)` calls the
    factory with exactly `model` (via `resolve_task_model`), `timeout`,
    `max_generation_tokens`, `context_window`, `temperature`, `seed` -- no
    more, no fewer. `task="edge_typing"` with a matching `models:` override
    proves the model is resolved THROUGH `resolve_task_model`, not read
    from `cfg.model` directly. Covers "The definition takes the client
    class as an injected factory". RED today: `ModuleNotFoundError` --
    `backends.py` does not exist. Kills dropping `timeout=` (or any of the
    six)."""
    cfg = _cfg(tmp_path, models={"edge_typing": "task-model:latest"})
    factories = backends.BackendFactories(
        ollama=_RecordingFactory, openai_compatible=_RecordingFactory
    )

    client = backends.chat_client(cfg, factories=factories, task="edge_typing")

    assert isinstance(client, _RecordingFactory)
    assert client.kwargs == {
        "model": "task-model:latest",
        "timeout": cfg.chat_timeout,
        "max_generation_tokens": cfg.max_generation_tokens,
        "context_window": cfg.context_window,
        "temperature": cfg.temperature,
        "seed": cfg.seed,
    }
    assert set(client.kwargs) == {
        "model",
        "timeout",
        "max_generation_tokens",
        "context_window",
        "temperature",
        "seed",
    }


class _StubClient:
    """A structural stand-in for `HasLocality`: a client whose `locality`
    reports a fixed `is_local`."""

    def __init__(self, *, is_local: bool) -> None:
        self._is_local = is_local

    @property
    def locality(self) -> _StubClient:
        return self

    @property
    def is_local(self) -> bool:
        return self._is_local


def test_resolve_local_exemption_truth_table(tmp_path: Path) -> None:
    """The 4 combinations of `client.locality.is_local` and
    `cfg.confidential_local_exemption`, asserting `and` (not `or`)
    semantics. RED today: same reason as above. Kills using `or` instead of
    `and`."""
    base_cfg = _cfg(tmp_path)
    for is_local, policy, expected in (
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ):
        cfg = dataclasses.replace(base_cfg, confidential_local_exemption=policy)
        client = _StubClient(is_local=is_local)
        assert backends.resolve_local_exemption(client, cfg) is expected, (
            is_local,
            policy,
        )


def test_confidential_exemption_independent_per_purpose_for_openai_compatible(
    tmp_path: Path,
) -> None:
    """Two `OpenAICompatibleClient` instances -- one remote chat endpoint,
    one loopback embedding endpoint -- each resolve
    `resolve_local_exemption` independently, matching existing Ollama
    behavior for two separately classified endpoints (issue #1057 task
    7.8, Threat Matrix "Confidential exemption"; proposal's "per client
    (chat and embedding endpoints separately)" rule). Wired through
    `HasLocality`'s structural Protocol -- `resolve_local_exemption` takes
    no `OpenAICompatibleClient`-specific branch, so this is a fixture-only
    addition, not new resolver wiring (that lands in Phase 9)."""
    cfg = _cfg(tmp_path)
    remote_chat_client = OpenAICompatibleClient(
        model="qwen3", base_url="http://example.com:8080"
    )
    loopback_embed_client = OpenAICompatibleClient(
        model="bge-m3", base_url="http://127.0.0.1:8080"
    )

    assert backends.resolve_local_exemption(remote_chat_client, cfg) is False
    assert backends.resolve_local_exemption(loopback_embed_client, cfg) is True


# ---------------------------------------------------------------------------
# Phase 9 -- resolver seam: BackendFactories, Endpoint, resolve_endpoint,
# chat_client/embed_client/diagnostics_client dispatch, API key read
# (issue #1057).
# ---------------------------------------------------------------------------


def test_backend_factories_dataclass_shape() -> None:
    """`BackendFactories(ollama=..., openai_compatible=...)` constructs, and
    is frozen/slots (task 9.1). RED today: `BackendFactories` doesn't exist."""
    factories = backends.BackendFactories(
        ollama=_RecordingFactory, openai_compatible=_RecordingFactory
    )
    assert factories.ollama is _RecordingFactory
    assert factories.openai_compatible is _RecordingFactory
    assert dataclasses.is_dataclass(factories)
    with pytest.raises(dataclasses.FrozenInstanceError):
        factories.ollama = _RecordingFactory  # type: ignore[misc]


@pytest.mark.parametrize(
    ("ollama_host", "base_url", "expected_url", "expected_source"),
    [
        ("http://envhost:1234", "http://cfghost:8080", None, "OLLAMA_HOST"),
        (None, "http://cfghost:8080", "http://cfghost:8080", "base_url"),
        (None, None, None, "default"),
    ],
    ids=["ollama_host_wins", "base_url_used", "packaged_default"],
)
def test_resolve_endpoint_ollama_chat_precedence_table(
    tmp_path: Path,
    ollama_host: str | None,
    base_url: str | None,
    expected_url: str | None,
    expected_source: str,
) -> None:
    """`OLLAMA_HOST` > `base_url` > default for `backend="ollama"`,
    `purpose="chat"` (task 9.3, backend-selection "Endpoint Resolution
    Precedence"). RED today: `resolve_endpoint` doesn't exist."""
    cfg = _cfg(tmp_path, base_url=base_url)
    environ = {"OLLAMA_HOST": ollama_host} if ollama_host else {}

    endpoint = backends.resolve_endpoint(cfg, purpose="chat", environ=environ)

    assert endpoint.url == expected_url
    assert endpoint.source == expected_source


@pytest.mark.parametrize(
    (
        "ollama_host",
        "embedding_base_url",
        "base_url",
        "expected_url",
        "expected_source",
    ),
    [
        (
            "http://envhost:1234",
            "http://embed:9000",
            "http://cfghost:8080",
            None,
            "OLLAMA_HOST",
        ),
        (
            None,
            "http://embed:9000",
            "http://cfghost:8080",
            "http://embed:9000",
            "embedding_base_url",
        ),
        (None, None, "http://cfghost:8080", "http://cfghost:8080", "base_url"),
        (None, None, None, None, "default"),
    ],
    ids=[
        "ollama_host_wins",
        "embedding_base_url_used",
        "base_url_fallback",
        "packaged_default",
    ],
)
def test_resolve_endpoint_ollama_embed_precedence_table(
    tmp_path: Path,
    ollama_host: str | None,
    embedding_base_url: str | None,
    base_url: str | None,
    expected_url: str | None,
    expected_source: str,
) -> None:
    """`OLLAMA_HOST` > `embedding_base_url` > `base_url` > default, for
    `purpose="embed"` (task 9.4)."""
    cfg = _cfg(tmp_path, base_url=base_url, embedding_base_url=embedding_base_url)
    environ = {"OLLAMA_HOST": ollama_host} if ollama_host else {}

    endpoint = backends.resolve_endpoint(cfg, purpose="embed", environ=environ)

    assert endpoint.url == expected_url
    assert endpoint.source == expected_source


def test_resolve_endpoint_openai_compatible_precedence_table(tmp_path: Path) -> None:
    """chat -> `base_url` only (no default -- constructed directly,
    bypassing `read_config`'s refusal, to exercise the resolver's own
    defensive behavior); embed -> `embedding_base_url` > `base_url` (task
    9.5)."""
    chat_cfg = _cfg(
        tmp_path, backend="openai-compatible", base_url="http://127.0.0.1:8080"
    )
    assert backends.resolve_endpoint(chat_cfg, purpose="chat") == backends.Endpoint(
        url="http://127.0.0.1:8080", source="base_url"
    )

    embed_cfg = dataclasses.replace(
        chat_cfg, embedding_base_url="http://127.0.0.1:9000"
    )
    assert backends.resolve_endpoint(embed_cfg, purpose="embed") == backends.Endpoint(
        url="http://127.0.0.1:9000", source="embedding_base_url"
    )

    assert backends.resolve_endpoint(chat_cfg, purpose="embed") == backends.Endpoint(
        url="http://127.0.0.1:8080", source="base_url"
    )


def test_resolve_endpoint_openai_compatible_never_consults_ollama_host(
    tmp_path: Path,
) -> None:
    """`OLLAMA_HOST` set, `backend="openai-compatible"`, `base_url` set to a
    DIFFERENT host; `resolve_endpoint` returns the `base_url` value,
    `source != "OLLAMA_HOST"`, and the returned `Endpoint` never carries the
    `OLLAMA_HOST` value under any field (task 9.6, Threat Matrix +
    backend-selection's "OLLAMA_HOST never redirects the openai-compatible
    backend" scenario). Security-property sentinel test."""
    cfg = _cfg(tmp_path, backend="openai-compatible", base_url="http://127.0.0.1:8080")
    environ = {"OLLAMA_HOST": "http://evil-host.invalid:9999"}

    endpoint = backends.resolve_endpoint(cfg, purpose="chat", environ=environ)

    assert endpoint.url == "http://127.0.0.1:8080"
    assert endpoint.source == "base_url"
    assert "evil-host" not in (endpoint.url or "")


def test_default_path_kwargs_are_byte_identical(tmp_path: Path) -> None:
    """`chat_client` called with a default `Config` (no `backend`/
    `base_url`/`OLLAMA_HOST`) constructs the Ollama factory with `host`
    ABSENT from the kwargs entirely, not `host=None` (task 9.8)."""
    cfg = _cfg(tmp_path)
    factories = backends.BackendFactories(
        ollama=_RecordingFactory, openai_compatible=_RecordingFactory
    )

    client = backends.chat_client(cfg, factories=factories, environ={})

    assert isinstance(client, _RecordingFactory)
    assert "host" not in client.kwargs


@pytest.mark.parametrize(
    ("environ", "base_url", "expect_host"),
    [
        ({"OLLAMA_HOST": "http://envhost:1234"}, "http://cfghost:8080", False),
        ({}, "http://cfghost:8080", True),
        ({}, None, False),
    ],
    ids=["source_is_ollama_host", "source_is_base_url", "source_is_default"],
)
def test_host_passed_only_when_source_is_config_key(
    tmp_path: Path,
    environ: dict[str, str],
    base_url: str | None,
    expect_host: bool,
) -> None:
    """`source="OLLAMA_HOST"` -> no `host=` kwarg (the Ollama client
    resolves `OLLAMA_HOST` itself, unchanged); `source="base_url"` ->
    `host=<base_url>`; `source="default"` -> no `host=` kwarg (task 9.9)."""
    cfg = _cfg(tmp_path, base_url=base_url)
    factories = backends.BackendFactories(
        ollama=_RecordingFactory, openai_compatible=_RecordingFactory
    )

    client = backends.chat_client(cfg, factories=factories, environ=environ)

    assert isinstance(client, _RecordingFactory)
    assert ("host" in client.kwargs) is expect_host
    if expect_host:
        assert client.kwargs["host"] == base_url


def test_chat_client_dispatches_to_openai_compatible_factory(tmp_path: Path) -> None:
    """`cfg.backend == "openai-compatible"`, both factories injected as
    recording spies; `chat_client(...)` constructs via
    `factories.openai_compatible`, not `factories.ollama` (task 9.11,
    backend-selection "Chat construction dispatches by cfg.backend")."""
    cfg = _cfg(tmp_path, backend="openai-compatible", base_url="http://127.0.0.1:8080")
    ollama_calls: list[dict[str, object]] = []
    openai_calls: list[dict[str, object]] = []

    class _OllamaSpy:
        def __init__(self, **kwargs: object) -> None:
            ollama_calls.append(kwargs)

    class _OpenAISpy:
        def __init__(self, **kwargs: object) -> None:
            openai_calls.append(kwargs)

    factories = backends.BackendFactories(
        ollama=_OllamaSpy, openai_compatible=_OpenAISpy
    )

    client = backends.chat_client(cfg, factories=factories)

    assert isinstance(client, _OpenAISpy)
    assert ollama_calls == []
    assert len(openai_calls) == 1
    assert openai_calls[0]["base_url"] == "http://127.0.0.1:8080"


def test_embed_client_mirrors_chat_client_dispatch_shape(tmp_path: Path) -> None:
    """Same dispatch assertion for a new `embed_client(cfg, *, factories)`,
    using `purpose="embed"` endpoint resolution (task 9.12)."""
    cfg = _cfg(
        tmp_path,
        backend="openai-compatible",
        base_url="http://127.0.0.1:8080",
        embedding_base_url="http://127.0.0.1:9000",
    )
    ollama_calls: list[dict[str, object]] = []
    openai_calls: list[dict[str, object]] = []

    class _OllamaSpy:
        def __init__(self, **kwargs: object) -> None:
            ollama_calls.append(kwargs)

    class _OpenAISpy:
        def __init__(self, **kwargs: object) -> None:
            openai_calls.append(kwargs)

    factories = backends.BackendFactories(
        ollama=_OllamaSpy, openai_compatible=_OpenAISpy
    )

    client = backends.embed_client(cfg, factories=factories)

    assert isinstance(client, _OpenAISpy)
    assert ollama_calls == []
    assert len(openai_calls) == 1
    assert openai_calls[0]["base_url"] == "http://127.0.0.1:9000"
    assert openai_calls[0]["model"] == cfg.embedding_model


def test_embed_client_default_path_kwargs(tmp_path: Path) -> None:
    """`embed_client` passes `model=cfg.embedding_model` only -- no
    `chat_timeout`, no per-task model resolution (task 9.13, design's Data
    Flow note): embedding clients keep the transport default timeout."""
    cfg = _cfg(tmp_path)
    factories = backends.BackendFactories(
        ollama=_RecordingFactory, openai_compatible=_RecordingFactory
    )

    client = backends.embed_client(cfg, factories=factories, environ={})

    assert isinstance(client, _RecordingFactory)
    assert client.kwargs == {"model": cfg.embedding_model}


def test_diagnostics_client_shape(tmp_path: Path) -> None:
    """`diagnostics_client(cfg_or_none, *, model, timeout, factories,
    purpose="chat")` dispatches identically, usable by the init picker/
    preflight/doctor probe sites (Phase 10) with `cfg=None` defaulting to
    Ollama (task 9.14)."""
    factories = backends.BackendFactories(
        ollama=_RecordingFactory, openai_compatible=_RecordingFactory
    )

    default_client = backends.diagnostics_client(
        None, model="m", timeout=5.0, factories=factories
    )
    assert isinstance(default_client, _RecordingFactory)
    assert default_client.kwargs == {"model": "m", "timeout": 5.0}

    cfg = _cfg(tmp_path, backend="openai-compatible", base_url="http://127.0.0.1:8080")
    oc_client = backends.diagnostics_client(
        cfg, model="m2", timeout=3.0, factories=factories, purpose="chat", environ={}
    )
    assert isinstance(oc_client, _RecordingFactory)
    assert oc_client.kwargs["model"] == "m2"
    assert oc_client.kwargs["timeout"] == 3.0
    assert oc_client.kwargs["base_url"] == "http://127.0.0.1:8080"
    assert oc_client.kwargs["api_key"] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" secret ", "secret"), (None, None), ("", None), ("   ", None)],
    ids=["set_and_stripped", "unset", "empty", "whitespace_only"],
)
def test_api_key_read_from_environ_stripped_and_empty_as_absent(
    tmp_path: Path, raw: str | None, expected: str | None
) -> None:
    """`OPENKOS_OPENAI_API_KEY=" secret "` -> `"secret"` passed as
    `api_key=`; unset -> `api_key=None`; set to `""`/whitespace-only ->
    treated as absent (task 9.16)."""
    cfg = _cfg(tmp_path, backend="openai-compatible", base_url="http://127.0.0.1:8080")
    factories = backends.BackendFactories(
        ollama=_RecordingFactory, openai_compatible=_RecordingFactory
    )
    environ = {"OPENKOS_OPENAI_API_KEY": raw} if raw is not None else {}

    client = backends.chat_client(cfg, factories=factories, environ=environ)

    assert isinstance(client, _RecordingFactory)
    assert client.kwargs["api_key"] == expected


def test_api_key_only_passed_for_openai_compatible_backend(tmp_path: Path) -> None:
    """`cfg.backend == "ollama"`, `OPENKOS_OPENAI_API_KEY` set -- the Ollama
    factory call's kwargs never include an API key (task 9.17). Security-
    property regression pin."""
    cfg = _cfg(tmp_path)
    factories = backends.BackendFactories(
        ollama=_RecordingFactory, openai_compatible=_RecordingFactory
    )
    environ = {"OPENKOS_OPENAI_API_KEY": "secret"}

    client = backends.chat_client(cfg, factories=factories, environ=environ)

    assert isinstance(client, _RecordingFactory)
    assert "api_key" not in client.kwargs


# ---------------------------------------------------------------------------
# Phase 13a -- wording functions + `insecure_key_warning` (issue #1057,
# design Decision 8/9). `doctor` never calls these (tasks-phase decision 2);
# only the CLI/curate/MCP wiring in Phase 13b does.
# ---------------------------------------------------------------------------


def test_start_hint_ollama_byte_identical(tmp_path: Path) -> None:
    """`start_hint(cfg)` for `backend="ollama"` returns the exact existing
    `` `ollama serve` `` literal (task 13.1). RED today: function doesn't
    exist."""
    cfg = _cfg(tmp_path)
    assert backends.start_hint(cfg) == "Start it with `ollama serve`"


def test_start_hint_ollama_byte_identical_for_none_cfg() -> None:
    """`start_hint(None)` (no workspace yet) defaults to the `ollama`
    wording too."""
    assert backends.start_hint(None) == "Start it with `ollama serve`"


def test_start_hint_openai_compatible(tmp_path: Path) -> None:
    """`start_hint(cfg)` for `backend="openai-compatible"` names the
    resolved chat endpoint's display host (task 13.2)."""
    cfg = _cfg(
        tmp_path, backend="openai-compatible", base_url="http://example.com:8080"
    )
    assert backends.start_hint(cfg) == (
        "Start your OpenAI-compatible server at example.com:8080"
    )


def test_install_hint_ollama_byte_identical(tmp_path: Path) -> None:
    """`install_hint(cfg, model)` for `backend="ollama"` returns the exact
    existing `` `ollama pull <model>` `` literal (task 13.4)."""
    cfg = _cfg(tmp_path)
    assert backends.install_hint(cfg, "qwen3:8b") == (
        "Pull it with `ollama pull qwen3:8b`"
    )


def test_install_hint_openai_compatible(tmp_path: Path) -> None:
    """`install_hint(cfg, model)` for `backend="openai-compatible"` never
    says `ollama pull` (task 13.4)."""
    cfg = _cfg(
        tmp_path, backend="openai-compatible", base_url="http://example.com:8080"
    )
    hint = backends.install_hint(cfg, "gemma2:27b")
    assert "ollama pull" not in hint
    assert "gemma2:27b" in hint
    assert "openkos doctor" in hint


def test_endpoint_label_ollama_is_ollama_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`endpoint_label(cfg, purpose="chat")` returns the resolved source
    (task 13.6): `OLLAMA_HOST` when it wins precedence for `backend=
    "ollama"` -- the only source an existing non-local Ollama endpoint could
    ever have had before this change."""
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", "http://envhost:1234")
    assert backends.endpoint_label(cfg, purpose="chat") == "OLLAMA_HOST"


def test_endpoint_label_openai_compatible_names_the_config_key(
    tmp_path: Path,
) -> None:
    """`endpoint_label(cfg, purpose=...)` names `base_url`/
    `embedding_base_url` for `backend="openai-compatible"` (task 13.6)."""
    cfg = _cfg(
        tmp_path,
        backend="openai-compatible",
        base_url="http://example.com:8080",
        embedding_base_url="http://embed.example.com:9000",
    )
    assert backends.endpoint_label(cfg, purpose="chat") == "base_url"
    assert backends.endpoint_label(cfg, purpose="embed") == "embedding_base_url"


def test_backend_label_ollama_is_Ollama(tmp_path: Path) -> None:
    """`backend_label(cfg)` for `backend="ollama"` returns `"Ollama"` (task
    13.8)."""
    cfg = _cfg(tmp_path)
    assert backends.backend_label(cfg) == "Ollama"
    assert backends.backend_label(None) == "Ollama"


def test_backend_label_openai_compatible(tmp_path: Path) -> None:
    """`backend_label(cfg)` for `backend="openai-compatible"` returns
    `"OpenAI-compatible server"` (task 13.8)."""
    cfg = _cfg(
        tmp_path, backend="openai-compatible", base_url="http://example.com:8080"
    )
    assert backends.backend_label(cfg) == "OpenAI-compatible server"


@pytest.mark.parametrize(
    ("has_key", "base_url", "expect_warning"),
    [
        (True, "http://example.com:8080", True),
        (True, "http://127.0.0.1:8080", False),
        (True, "https://example.com:8080", False),
        (False, "http://example.com:8080", False),
    ],
    ids=["key_nonlocal_http", "key_local_http", "key_nonlocal_https", "no_key"],
)
def test_insecure_key_warning_matrix(
    tmp_path: Path, has_key: bool, base_url: str, expect_warning: bool
) -> None:
    """The full key x scheme x locality matrix (task 13.10, Threat Matrix
    "Secret over plain HTTP"): warns only for (key present, non-local,
    `http://`); every other combination is `None`."""
    cfg = _cfg(tmp_path, backend="openai-compatible", base_url=base_url)
    environ = {"OPENKOS_OPENAI_API_KEY": "secret"} if has_key else {}

    warning = backends.insecure_key_warning(cfg, environ=environ)

    if expect_warning:
        assert warning is not None
        assert "example.com:8080" in warning
    else:
        assert warning is None


def test_insecure_key_warning_never_includes_the_key_value(tmp_path: Path) -> None:
    """Sentinel test (task 13.12): the warning string never contains the
    key VALUE, only the fact that a credential is present. Mutation-proof
    below."""
    cfg = _cfg(
        tmp_path, backend="openai-compatible", base_url="http://example.com:8080"
    )
    environ = {"OPENKOS_OPENAI_API_KEY": "sk-super-secret-sentinel"}

    warning = backends.insecure_key_warning(cfg, environ=environ)

    assert warning is not None
    assert "sk-super-secret-sentinel" not in warning


_KEY_ENV = {"OPENKOS_OPENAI_API_KEY": "secret"}


@pytest.mark.parametrize(
    ("environ", "base_url", "embedding_base_url", "expected_origins"),
    [
        (_KEY_ENV, "https://api.example.com/v1", None, ["https://api.example.com"]),
        (
            _KEY_ENV,
            "https://api.example.com:8443/v1?x=1",
            None,
            ["https://api.example.com:8443"],
        ),
        (
            _KEY_ENV,
            "https://api.example.com/v1",
            "https://embed.example.org/v1",
            ["https://api.example.com", "https://embed.example.org"],
        ),
        (_KEY_ENV, "http://127.0.0.1:8080", None, []),
        (_KEY_ENV, "https://localhost:8080/v1", None, []),
        ({}, "https://api.example.com/v1", None, []),
        # plain http to a non-local host: the stronger warning owns that host.
        (_KEY_ENV, "http://example.com:8080", None, []),
        (
            _KEY_ENV,
            "http://example.com:8080",
            "https://embed.example.org",
            ["https://embed.example.org"],
        ),
    ],
    ids=[
        "https_remote",
        "port_kept_path_query_dropped",
        "chat_and_embed_differ",
        "loopback_ip",
        "localhost",
        "no_key",
        "http_remote_superseded",
        "http_chat_https_embed",
    ],
)
def test_remote_key_notices_matrix(
    tmp_path: Path,
    environ: dict[str, str],
    base_url: str,
    embedding_base_url: str | None,
    expected_origins: list[str],
) -> None:
    cfg = _cfg(
        tmp_path,
        backend="openai-compatible",
        base_url=base_url,
        embedding_base_url=embedding_base_url,
    )

    notices = backends.remote_key_notices(cfg, environ=environ)

    assert [origin for origin, _ in notices] == expected_origins
    for origin, message in notices:
        assert origin in message
        assert "secret" not in message
        assert "?" not in message
        assert "/v1" not in message


def test_remote_key_notices_absent_for_ollama(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, base_url="https://api.example.com/v1")

    assert backends.remote_key_notices(cfg, environ=_KEY_ENV) == ()


def test_remote_key_notices_dedupe_identical_chat_and_embed_origin(
    tmp_path: Path,
) -> None:
    cfg = _cfg(
        tmp_path,
        backend="openai-compatible",
        base_url="https://api.example.com/v1",
        embedding_base_url="https://api.example.com/embed",
    )

    notices = backends.remote_key_notices(cfg, environ=_KEY_ENV)

    assert [origin for origin, _ in notices] == ["https://api.example.com"]


@pytest.mark.parametrize(
    ("backend", "base_url", "environ", "expected"),
    [
        ("ollama", None, _KEY_ENV, None),
        ("openai-compatible", "https://api.example.com/v1", {}, None),
        (
            "openai-compatible",
            "https://api.example.com:8443/v1?q=1",
            _KEY_ENV,
            "https://api.example.com:8443",
        ),
        (
            "openai-compatible",
            "http://127.0.0.1:8080",
            _KEY_ENV,
            "http://127.0.0.1:8080",
        ),
    ],
)
def test_key_destination(
    tmp_path: Path,
    backend: str,
    base_url: str | None,
    environ: dict[str, str],
    expected: str | None,
) -> None:
    cfg = _cfg(tmp_path, backend=backend, base_url=base_url)

    assert backends.key_destinations(cfg, environ=environ) == (
        () if expected is None else (expected,)
    )


def test_openai_api_key_without_prefix_is_never_read(tmp_path: Path) -> None:
    """`OPENAI_API_KEY` (no prefix) set, `OPENKOS_OPENAI_API_KEY` unset;
    `chat_client`/`embed_client` construct the `openai-compatible` factory
    with `api_key=None` (task 9.18). Sentinel test -- covers
    backend-selection's "OPENAI_API_KEY is never read" AND Threat Matrix
    "Credential confusion"."""
    cfg = _cfg(tmp_path, backend="openai-compatible", base_url="http://127.0.0.1:8080")
    factories = backends.BackendFactories(
        ollama=_RecordingFactory, openai_compatible=_RecordingFactory
    )
    environ = {"OPENAI_API_KEY": "leaked-unrelated-cloud-key"}

    chat = backends.chat_client(cfg, factories=factories, environ=environ)
    embed = backends.embed_client(cfg, factories=factories, environ=environ)

    assert isinstance(chat, _RecordingFactory)
    assert isinstance(embed, _RecordingFactory)
    assert chat.kwargs["api_key"] is None
    assert embed.kwargs["api_key"] is None
