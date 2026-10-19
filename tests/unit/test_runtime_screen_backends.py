"""The OpenAI-compatible transport the runtime screen (#1586) adds to the two
harnesses it replays: #1557's concept screen (`screen_pipeline`) and #1560's
Ask screen (`run_ask_model_screen`).

Both harnesses keep Ollama as their default; these tests pin that the new
path sends the production request (the served model, seed, generation
ceiling, no `num_ctx`), keeps the registered outcome classes, and refuses to
run without a weights digest, since the cache key rests on it.
"""

import email.message
import importlib.util
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from openkos.llm.openai_compatible import OpenAICompatibleClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVALS = _REPO_ROOT / "evals"


def _load(name: str, path: Path, extra: list[Path]) -> ModuleType:
    for entry in extra:
        if str(entry) not in sys.path:
            sys.path.append(str(entry))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None, path
    assert spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sp = _load(
    "screen_pipeline",
    _EVALS / "concept_model_screen" / "screen_pipeline.py",
    [_EVALS / "concept_model_screen", _EVALS],
)
ask = _load(
    "run_ask_model_screen",
    _EVALS / "ask_model_screen" / "run_ask_model_screen.py",
    [],
)


class _Response:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body


class _FakeOpen:
    def __init__(
        self, body: dict[str, Any] | None = None, exc: Exception | None = None
    ):
        self.body = body
        self.exc = exc
        self.requests: list[tuple[urllib.request.Request, float]] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> _Response:
        self.requests.append((request, timeout))
        if self.exc is not None:
            raise self.exc
        assert self.body is not None
        return _Response(self.body)


def _reply(content: str, finish: str = "stop", **message: Any) -> dict[str, Any]:
    return {
        "choices": [
            {"message": {"content": content, **message}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 7},
    }


# --- concept screen ----------------------------------------------------------


def test_openai_sender_sends_the_served_model_seed_and_ceiling() -> None:
    opener = _FakeOpen(_reply("[]"))
    send = sp.openai_compatible_sender(
        "http://127.0.0.1:8081", served_model="/models/qwen3.gguf", urlopen=opener
    )
    content, entry = send(
        "qwen3:8b@llama.cpp", [{"role": "user", "content": "x"}], 3, 30.0
    )
    assert content == "[]"
    request, timeout = opener.requests[0]
    assert request.full_url == "http://127.0.0.1:8081/v1/chat/completions"
    assert timeout == 30.0
    sent = json.loads(request.data)  # type: ignore[arg-type]
    assert sent["model"] == "/models/qwen3.gguf"
    assert sent["seed"] == 3
    assert sent["max_tokens"] == sp.MAX_GENERATION_TOKENS
    assert "num_ctx" not in json.dumps(sent)
    assert entry["outcome"] == "ok"
    assert entry["prompt_eval_count"] == 120
    assert entry["eval_count"] == 7
    assert entry["done_reason"] == "stop"
    assert entry["thinking_chars"] == 0
    assert entry["content"] == "[]"


def test_openai_sender_counts_a_reasoning_segment() -> None:
    opener = _FakeOpen(_reply("[]", reasoning_content="hmm, let me think"))
    send = sp.openai_compatible_sender("http://h:1", served_model="m", urlopen=opener)
    _content, entry = send("label", [{"role": "user", "content": "x"}], 1, 5.0)
    assert entry["thinking_chars"] == len("hmm, let me think")


def test_openai_sender_classifies_a_length_stop_as_capped() -> None:
    opener = _FakeOpen(_reply("partial", finish="length"))
    send = sp.openai_compatible_sender("http://h:1", served_model="m", urlopen=opener)
    content, entry = send("label", [{"role": "user", "content": "x"}], 1, 5.0)
    assert content is None
    assert entry["outcome"] == "capped"
    assert "content" not in entry


def test_openai_sender_classifies_a_client_timeout_as_timeout() -> None:
    opener = _FakeOpen(exc=urllib.error.URLError(TimeoutError("timed out")))
    send = sp.openai_compatible_sender("http://h:1", served_model="m", urlopen=opener)
    content, entry = send("label", [{"role": "user", "content": "x"}], 1, 5.0)
    assert content is None
    assert entry["outcome"] == "timeout"


def test_openai_sender_reraises_a_refused_connection() -> None:
    opener = _FakeOpen(exc=urllib.error.URLError(ConnectionRefusedError("refused")))
    send = sp.openai_compatible_sender("http://h:1", served_model="m", urlopen=opener)
    with pytest.raises(sp.BackendUnavailable):
        send("label", [{"role": "user", "content": "x"}], 1, 5.0)


def test_openai_sender_records_a_server_error_as_error() -> None:
    error = urllib.error.HTTPError(
        "http://h:1/v1/chat/completions",
        500,
        "boom",
        email.message.Message(),
        io.BytesIO(b"{}"),
    )
    send = sp.openai_compatible_sender(
        "http://h:1", served_model="m", urlopen=_FakeOpen(exc=error)
    )
    content, entry = send("label", [{"role": "user", "content": "x"}], 1, 5.0)
    assert content is None
    assert entry["outcome"] == "error"


# --- Ask screen ----------------------------------------------------------------


def test_ask_parser_defaults_to_the_ollama_backend() -> None:
    args = ask.build_parser().parse_args(["--stage", "generate", "--model", "qwen3:8b"])
    assert args.backend == "ollama"


def test_ask_openai_factory_builds_the_production_client_per_seed() -> None:
    args = ask.build_parser().parse_args(
        [
            "--stage",
            "generate",
            "--model",
            "qwen3:8b@llama.cpp",
            "--backend",
            "openai-compatible",
            "--base-url",
            "http://127.0.0.1:8081",
            "--served-model",
            "/models/qwen3.gguf",
            "--digest",
            "sha256:abc",
            "--timeout",
            "60",
        ]
    )
    client = ask.inner_factory(args)(4)
    assert isinstance(client, OpenAICompatibleClient)
    opener = _FakeOpen(_reply("SUFFICIENT"))
    client._urlopen = opener  # the production client's own injection point
    assert client.chat([{"role": "user", "content": "q"}]) == "SUFFICIENT"
    sent = json.loads(opener.requests[0][0].data)  # type: ignore[arg-type]
    assert sent["model"] == "/models/qwen3.gguf"
    assert sent["seed"] == 4
    assert sent["max_tokens"] == ask.DEFAULT_MAX_GENERATION_TOKENS
    assert opener.requests[0][1] == 60.0


def test_ask_openai_identity_takes_the_given_digest() -> None:
    args = ask.build_parser().parse_args(
        [
            "--stage",
            "generate",
            "--model",
            "qwen3:8b@mlx",
            "--backend",
            "openai-compatible",
            "--base-url",
            "http://127.0.0.1:8082",
            "--served-model",
            "/models/mlx",
            "--digest",
            "sha256:def",
        ]
    )
    stamp: dict[str, Any] = {"model": {"name": "qwen3:8b@mlx", "digest": None}}
    assert ask.resolve_digest(args, stamp) == "sha256:def"
    assert stamp["model"] == {
        "name": "qwen3:8b@mlx",
        "digest": "sha256:def",
        "backend": "openai-compatible",
        "base_url": "http://127.0.0.1:8082",
        "served_model": "/models/mlx",
    }


@pytest.mark.parametrize("missing", ["--base-url", "--served-model", "--digest"])
def test_ask_openai_backend_refuses_a_missing_setting(missing: str) -> None:
    argv = [
        "--stage",
        "generate",
        "--model",
        "qwen3:8b@mlx",
        "--backend",
        "openai-compatible",
        "--base-url",
        "http://127.0.0.1:8082",
        "--served-model",
        "/models/mlx",
        "--digest",
        "sha256:def",
    ]
    at = argv.index(missing)
    del argv[at : at + 2]
    args = ask.build_parser().parse_args(argv)
    with pytest.raises(SystemExit, match=missing.removeprefix("--")):
        ask.resolve_digest(args, {"model": {}})


def test_ask_ollama_digest_still_comes_from_the_stamp() -> None:
    args = ask.build_parser().parse_args(["--stage", "generate", "--model", "qwen3:8b"])
    assert ask.resolve_digest(args, {"model": {"digest": "sha256:ol"}}) == "sha256:ol"


# --- concept screen CLI -------------------------------------------------------

concept = _load(
    "run_concept_screen",
    _EVALS / "concept_model_screen" / "run_concept_screen.py",
    [],
)


def test_concept_run_defaults_to_ollama_and_its_own_timeout() -> None:
    args = concept.parse_args(
        ["run", "--models", "qwen3:8b", "--pool", "DA", "--runs", "1"]
    )
    assert args.backend == "ollama"
    assert concept.timeout_key(args, "qwen3:8b") == "qwen3:8b"


def test_concept_openai_transport_takes_the_digest_and_the_borrowed_timeout() -> None:
    args = concept.parse_args(
        [
            "run",
            "--models",
            "qwen3:8b@llama.cpp",
            "--pool",
            "DA",
            "--runs",
            "1",
            "--backend",
            "openai-compatible",
            "--base-url",
            "http://127.0.0.1:8081",
            "--served-model",
            "/models/q.gguf",
            "--digest",
            "sha256:abc",
            "--timeout-from",
            "qwen3:8b",
        ]
    )
    assert concept.run_digest(args, "qwen3:8b@llama.cpp") == "sha256:abc"
    assert concept.timeout_key(args, "qwen3:8b@llama.cpp") == "qwen3:8b"
    send = concept.run_sender(args)
    opener = _FakeOpen(_reply("[]"))
    # the sender is the openai one: it reaches the base URL, not Ollama
    send_injected = sp.openai_compatible_sender(
        args.base_url, served_model=args.served_model, urlopen=opener
    )
    assert send.__qualname__ == send_injected.__qualname__


def test_concept_openai_transport_refuses_two_models_or_a_missing_digest() -> None:
    base = [
        "run",
        "--pool",
        "DA",
        "--runs",
        "1",
        "--backend",
        "openai-compatible",
        "--base-url",
        "http://127.0.0.1:8081",
        "--served-model",
        "/models/q.gguf",
    ]
    args = concept.parse_args([*base, "--models", "a@x"])
    with pytest.raises(SystemExit, match="digest"):
        concept.run_digest(args, "a@x")
    args = concept.parse_args([*base, "--models", "a@x", "b@x", "--digest", "d"])
    with pytest.raises(SystemExit, match="one model"):
        concept.run_digest(args, "a@x")
