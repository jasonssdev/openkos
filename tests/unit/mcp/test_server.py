"""Unit tests for `mcp/server.py`: lifecycle, dispatch, protocol errors,
cancellation, and end-of-input handling (design Decisions 12-13, ADR-0027).

Async code is driven with `asyncio.run(...)` inside ordinary synchronous
tests (no `pytest-asyncio`); a worker that needs to block uses a
`threading.Event`, never a sleep. Every wait on a background thread or task
is bounded, so a regression fails promptly instead of hanging the suite.
"""

from __future__ import annotations

import asyncio
import io
import itertools
import json
import logging
import os
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.llm.base import Embedder, LLMBackend, Message
from openkos.llm.ollama import (
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.mcp import server, tools, transport
from openkos.state import fts
from openkos.state.fts import FtsUnavailable

# -- shared fixtures --------------------------------------------------------


def _never_called_llm(cfg: config.Config) -> LLMBackend:
    raise AssertionError("this test's tool never calls make_llm")


def _never_called_embedder(cfg: config.Config) -> Embedder:
    raise AssertionError("this test's tool never calls make_embedder")


def _never_called_local_exemption(client: LLMBackend, cfg: config.Config) -> bool:
    raise AssertionError("this test's tool never calls local_exemption_for")


def _ctx() -> tools.ToolContext:
    return tools.ToolContext(
        layout=config.WorkspaceLayout(root=Path("/nonexistent")),
        expose_confidential=False,
        make_llm=_never_called_llm,
        make_embedder=_never_called_embedder,
        local_exemption_for=_never_called_local_exemption,
    )


def _msg(**kwargs: object) -> bytes:
    return json.dumps(kwargs, ensure_ascii=False).encode("utf-8") + b"\n"


def _make_server(
    buffer: io.BytesIO, registry: Mapping[str, tools.Tool] | None = None
) -> server.Server:
    return server.Server(registry or {}, _ctx(), transport.MessageWriter(buffer))


def _responses(buffer: io.BytesIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


async def _initialized_server(
    buffer: io.BytesIO, registry: Mapping[str, tools.Tool] | None = None
) -> server.Server:
    """An initialized `Server`, with `buffer` reset afterward so a test's
    own assertions see only the responses it sends itself, not the
    `initialize` handshake's own result."""
    srv = _make_server(buffer, registry)
    await srv.handle_raw(
        _msg(
            jsonrpc="2.0",
            id="__init__",
            method="initialize",
            params={"protocolVersion": "2025-11-25"},
        )
    )
    buffer.seek(0)
    buffer.truncate(0)
    return srv


def _blocking_tool(
    name: str, event: threading.Event, *, started: threading.Event | None = None
) -> tools.Tool:
    def _run(
        arguments: Mapping[str, object],
        ctx: tools.ToolContext,
        progress: tools.ProgressSink | None,
    ) -> object:
        if started is not None:
            started.set()
        event.wait(timeout=5)
        return "done"

    return tools.Tool(
        name=name,
        title=name.title(),
        description="Test-only tool that blocks on an Event.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        run=_run,
        disclose=lambda raw, snapshot: {
            "withheld": 0,
            "warnings": [],
            "not_run": [],
            "value": raw,
        },
    )


# -- 3.1: the lifecycle table ------------------------------------------------


def test_lifecycle_table() -> None:
    """Covers mcp's "initialize negotiates the supported revision", "A
    different requested revision is answered with the supported one", "A
    request before initialize is rejected", "ping receives a response", and
    "initialize cannot be cancelled". Kills accepting `tools/list` before
    `initialize`, and echoing the client's requested protocol version
    instead of the server's own."""

    async def _scenario() -> list[dict[str, Any]]:
        buffer = io.BytesIO()
        srv = _make_server(buffer)

        # `ping` IS answered before `initialize`.
        await srv.handle_raw(_msg(jsonrpc="2.0", id=1, method="ping"))
        # A request other than initialize/ping before initialize is
        # rejected.
        await srv.handle_raw(_msg(jsonrpc="2.0", id=2, method="tools/list"))
        # `initialize`, naming an unsupported revision.
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=3,
                method="initialize",
                params={"protocolVersion": "2024-11-05"},
            )
        )
        # `tools/list` works without `notifications/initialized` ever sent.
        await srv.handle_raw(_msg(jsonrpc="2.0", id=4, method="tools/list"))
        # A second `initialize`, naming a different unsupported revision.
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=5,
                method="initialize",
                params={"protocolVersion": "2099-01-01"},
            )
        )
        # `notifications/cancelled` naming the already-answered `initialize`
        # request: `initialize` is never tracked as in-flight, so this must
        # be a no-op, not an error and not a crash.
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                method="notifications/cancelled",
                params={"requestId": 3},
            )
        )
        await srv.handle_raw(_msg(jsonrpc="2.0", id=6, method="ping"))

        return _responses(buffer)

    responses = asyncio.run(_scenario())
    by_id = {response["id"]: response for response in responses}

    assert by_id[1] == {"jsonrpc": "2.0", "id": 1, "result": {}}

    assert by_id[2]["error"]["code"] == -32600

    assert by_id[3]["result"]["protocolVersion"] == "2025-11-25"
    assert by_id[3]["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert by_id[3]["result"]["serverInfo"]["name"] == "openkos"

    assert by_id[4]["result"] == {"tools": []}

    assert by_id[5]["error"]["code"] == -32600

    assert by_id[6] == {"jsonrpc": "2.0", "id": 6, "result": {}}


# -- 3.2: protocol-error rows -------------------------------------------------


def test_protocol_error_rows() -> None:
    """Covers "A batch request is rejected", "An unparseable frame yields
    -32700", "An unknown tool name yields -32602", and "A malformed request
    envelope yields -32602". Kills treating a boolean id as a valid integer
    id, and answering a batch element-wise instead of with one `-32600`."""

    async def _scenario() -> list[dict[str, Any]]:
        buffer = io.BytesIO()
        srv = await _initialized_server(buffer)

        await srv.handle_raw(b"\xff\xfe not valid utf-8\n")  # -32700
        await srv.handle_raw(b"[1, 2]\n")  # batch -> -32600
        await srv.handle_raw(b"42\n")  # not an object -> -32600
        # jsonrpc != "2.0", but the id itself is a valid type -- echoed.
        await srv.handle_raw(_msg(jsonrpc="1.0", id=10, method="ping"))
        # method not a string, valid id -- echoed.
        await srv.handle_raw(_msg(jsonrpc="2.0", id=11, method=7))
        # A boolean id.
        await srv.handle_raw(_msg(jsonrpc="2.0", id=True, method="ping"))
        # A float id.
        await srv.handle_raw(_msg(jsonrpc="2.0", id=1.5, method="ping"))
        # A response object (no `method`) -- silently ignored.
        await srv.handle_raw(_msg(jsonrpc="2.0", id=12, result={}))
        # An unknown request method.
        await srv.handle_raw(_msg(jsonrpc="2.0", id=13, method="frobnicate"))
        # An unknown notification -- silently ignored.
        await srv.handle_raw(_msg(jsonrpc="2.0", method="notifications/unknown"))
        # tools/call malformed envelopes.
        await srv.handle_raw(
            _msg(jsonrpc="2.0", id=14, method="tools/call", params="not-an-object")
        )
        await srv.handle_raw(
            _msg(jsonrpc="2.0", id=15, method="tools/call", params={"name": 7})
        )
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=16,
                method="tools/call",
                params={"name": "get", "arguments": "nope"},
            )
        )
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=17,
                method="tools/call",
                params={"name": "get", "_meta": {"progressToken": 1.5}},
            )
        )
        # An unknown tool name.
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=18,
                method="tools/call",
                params={"name": "no-such-tool"},
            )
        )

        return _responses(buffer)

    responses = asyncio.run(_scenario())

    codes_and_ids = [
        (response.get("id"), response["error"]["code"]) for response in responses
    ]
    assert codes_and_ids == [
        (None, -32700),
        (None, -32600),
        (None, -32600),
        (10, -32600),
        (11, -32600),
        (None, -32600),
        (None, -32600),
        (13, -32601),
        (14, -32602),
        (15, -32602),
        (16, -32602),
        (17, -32602),
        (18, -32602),
    ]


# -- 3.3: internal errors never echo exception text ---------------------------


def test_internal_error_never_echoes_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Covers "An internal error still logs only to stderr" and "An
    internal error never echoes exception text". Kills using `str(exc)` as
    the `-32603` message."""
    confidential_text = "a message naming a confidential path /bundle/concepts/zq.md"

    def _raise(
        arguments: Mapping[str, object],
        ctx: tools.ToolContext,
        progress: tools.ProgressSink | None,
    ) -> object:
        raise RuntimeError(confidential_text)

    boom_tool = tools.Tool(
        name="boom",
        title="Boom",
        description="Test-only tool that always raises.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        run=_raise,
        disclose=lambda raw, snapshot: {"withheld": 0, "warnings": [], "not_run": []},
    )

    async def _scenario() -> tuple[bytes, list[dict[str, Any]]]:
        buffer = io.BytesIO()
        srv = await _initialized_server(buffer, {"boom": boom_tool})
        await srv.handle_raw(
            _msg(jsonrpc="2.0", id=1, method="tools/call", params={"name": "boom"})
        )
        # `handle_raw` only schedules the tool's task; give the loop a
        # bounded chance to actually run it before the scenario ends.
        for _ in range(500):
            if _responses(buffer):
                break
            await asyncio.sleep(0.01)
        return buffer.getvalue(), _responses(buffer)

    with caplog.at_level(logging.INFO, logger="openkos.mcp"):
        raw_bytes, responses = asyncio.run(_scenario())

    assert len(responses) == 1
    error = responses[0]["error"]
    assert error["code"] == -32603
    assert error["message"] == "internal error"
    assert confidential_text not in error["message"]
    assert confidential_text.encode() not in raw_bytes

    assert confidential_text in caplog.text


# -- 5.12: the tool-error table (design Decision 12, slice 5's two rows) -----


def _raising_tool(name: str, exc: BaseException) -> tools.Tool:
    def _run(
        arguments: Mapping[str, object],
        ctx: tools.ToolContext,
        progress: tools.ProgressSink | None,
    ) -> object:
        raise exc

    return tools.Tool(
        name=name,
        title=name.title(),
        description="Test-only tool that always raises a specific exception.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        run=_run,
        disclose=lambda raw, snapshot: {"withheld": 0, "warnings": [], "not_run": []},
    )


async def _call_and_collect(tool_name: str, tool: tools.Tool) -> dict[str, Any]:
    buffer = io.BytesIO()
    srv = await _initialized_server(buffer, {tool_name: tool})
    await srv.handle_raw(
        _msg(jsonrpc="2.0", id=1, method="tools/call", params={"name": tool_name})
    )
    for _ in range(500):
        if _responses(buffer):
            break
        await asyncio.sleep(0.01)
    responses = _responses(buffer)
    assert len(responses) == 1
    return responses[0]


def test_concept_not_found_is_a_tool_error_not_retryable() -> None:
    """`ConceptNotFound` maps to a tool result (`isError: true`,
    `structuredContent.error.code == "concept_not_found"`, `retryable:
    false`) -- an ordinary JSON-RPC result, never `-32603` or a protocol
    error. Covers mcp's "A missing concept is reported as not retryable".
    Kills leaving `ConceptNotFound` unmapped (it would fall through to the
    generic `-32603`, which has no `error.code` field at all)."""
    from openkos.application import concept_read

    response = asyncio.run(
        _call_and_collect(
            "missing", _raising_tool("missing", concept_read.ConceptNotFound("nope"))
        )
    )

    assert "error" not in response  # not a JSON-RPC protocol error
    result = response["result"]
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "concept_not_found"
    assert error["retryable"] is False
    assert "nope" not in error["message"]


def test_os_error_is_a_retryable_read_failed_tool_error() -> None:
    """Any `OSError` maps to `read_failed` (`retryable: true`), with a
    FIXED message -- never `str(exc)`, which may carry a confidential path.
    Covers "Tool Errors Are Structured And Retryable-Tagged"'s `read_failed`
    row."""
    response = asyncio.run(
        _call_and_collect(
            "broken", _raising_tool("broken", OSError("cannot read /bundle/zq.md"))
        )
    )

    result = response["result"]
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "read_failed"
    assert error["retryable"] is True
    assert "/bundle/zq.md" not in error["message"]


def test_tool_error_table_is_subclass_ordered() -> None:
    """Design Decision 12: "The table is ordered, subclass first." No
    earlier row's exception type may be a superclass of a LATER row's
    type -- that would let the earlier, more general row shadow the later,
    more specific one."""
    table = server._TOOL_ERROR_TABLE
    for earlier_index, (earlier_type, *_rest) in enumerate(table):
        for later_type, *_rest2 in table[earlier_index + 1 :]:
            assert not issubclass(later_type, earlier_type), (
                f"{later_type} is a subclass of {earlier_type} but is listed after it"
            )


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (OllamaUnavailable("boom"), "ollama_unavailable", True),
        (OllamaModelNotFound("boom"), "model_not_found", False),
        (
            OllamaEmbeddingDimensionMismatch("boom"),
            "embedding_dimension_mismatch",
            False,
        ),
        (FtsUnavailable("boom"), "fts_unavailable", False),
        (OllamaError("boom"), "ollama_error", True),
    ],
)
def test_tool_error_table_ordered_and_fixed_message(
    exc: BaseException, code: str, retryable: bool
) -> None:
    """Each of `OllamaUnavailable`, `OllamaModelNotFound`,
    `OllamaEmbeddingDimensionMismatch`, `FtsUnavailable`, and `OllamaError`
    maps to its code and `retryable` flag from Decision 12's table with a
    fixed message, never `str(exc)`. Covers "An unreachable Ollama server
    is reported as retryable" (full table, alongside
    `test_concept_not_found_is_a_tool_error_not_retryable` and
    `test_os_error_is_a_retryable_read_failed_tool_error` for the other two
    rows). Kills moving `OllamaError` above one of its subclasses in the
    lookup order, which would mis-map the subclass to the superclass's
    code."""
    response = asyncio.run(_call_and_collect("failing", _raising_tool("failing", exc)))

    result = response["result"]
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == code
    assert error["retryable"] is retryable
    assert "boom" not in error["message"]


def test_unmapped_exception_still_falls_through_to_internal_error() -> None:
    """An exception NOT in the tool-error table (e.g. a bare
    `RuntimeError`) still becomes the generic `-32603`, exactly as before
    -- the tool-error table is additive, not a replacement for the
    fallback (mirrors `test_internal_error_never_echoes_exception_text`)."""
    response = asyncio.run(
        _call_and_collect("boom", _raising_tool("boom", RuntimeError("secret")))
    )

    assert response["error"]["code"] == -32603
    assert response["error"]["message"] == "internal error"


# -- 3.7: duplicate in-flight ids ---------------------------------------------


def test_duplicate_inflight_id_rejected() -> None:
    """Covers the "a request id already in flight" row. Kills overwriting
    the first in-flight entry with the second, which would silently
    abandon the first request's eventual response."""
    event = threading.Event()
    blocked_tool = _blocking_tool("blocked", event)

    async def _scenario() -> list[dict[str, Any]]:
        buffer = io.BytesIO()
        srv = await _initialized_server(buffer, {"blocked": blocked_tool})

        await srv.handle_raw(
            _msg(jsonrpc="2.0", id=1, method="tools/call", params={"name": "blocked"})
        )
        # The first request is now in flight (blocked on `event`); a
        # second request naming the same id must be rejected as a
        # duplicate, without touching the first.
        await srv.handle_raw(
            _msg(jsonrpc="2.0", id=1, method="tools/call", params={"name": "blocked"})
        )

        event.set()
        for _ in range(500):
            if len(_responses(buffer)) >= 2:
                break
            await asyncio.sleep(0.01)

        return _responses(buffer)

    responses = asyncio.run(_scenario())

    assert len(responses) == 2
    duplicate, completed = responses
    assert duplicate["id"] == 1
    assert duplicate["error"]["code"] == -32600
    assert completed["id"] == 1
    assert "error" not in completed
    assert completed["result"]["structuredContent"]["value"] == "done"


# -- cancellation: notifications/cancelled abandons and sends no response ----


def test_cancelled_tools_call_sends_no_response_and_unblocks_later_requests() -> None:
    """Design Decision 13: a cancelled request is abandoned, sends no
    response, and does not block a later request. Kills writing a response
    after `CancelledError`."""
    event = threading.Event()
    started = threading.Event()
    blocked_tool = _blocking_tool("blocked", event, started=started)

    async def _scenario() -> list[dict[str, Any]]:
        buffer = io.BytesIO()
        srv = await _initialized_server(buffer, {"blocked": blocked_tool})

        await srv.handle_raw(
            _msg(jsonrpc="2.0", id=1, method="tools/call", params={"name": "blocked"})
        )
        # Bounded: wait for the worker to actually start (i.e. for the task
        # to genuinely be suspended awaiting it) before cancelling, so this
        # exercises cancelling a request that is really in flight, not one
        # that never got its first scheduling step.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, started.wait, 5)

        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                method="notifications/cancelled",
                params={"requestId": 1},
            )
        )

        for _ in range(500):
            if not srv._inflight:
                break
            await asyncio.sleep(0.01)

        # A later request must be served normally, unblocked by the
        # abandoned worker.
        await srv.handle_raw(_msg(jsonrpc="2.0", id=2, method="ping"))

        event.set()  # release the abandoned worker so it does not linger
        return _responses(buffer)

    responses = asyncio.run(_scenario())

    assert responses == [{"jsonrpc": "2.0", "id": 2, "result": {}}]


# -- 3.8: end of input abandons in-flight requests and exits 0 ---------------


def test_end_of_input_abandons_and_exits_zero() -> None:
    """Covers "Closing stdin abandons in-flight requests and exits 0" at
    the unit level. Kills joining or waiting on the abandoned worker thread
    before returning, which would hang past end of input."""
    event = threading.Event()
    started = threading.Event()

    def _run(
        arguments: Mapping[str, object],
        ctx: tools.ToolContext,
        progress: tools.ProgressSink | None,
    ) -> object:
        started.set()
        event.wait(timeout=5)
        return "done"

    blocked_tool = tools.Tool(
        name="blocked",
        title="Blocked",
        description="Test-only tool that blocks on an Event.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        run=_run,
        disclose=lambda raw, snapshot: {"withheld": 0, "warnings": [], "not_run": []},
    )

    async def _scenario() -> tuple[int, list[dict[str, Any]]]:
        buffer = io.BytesIO()
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        streams = transport.StdioStreams(reader=reader, writer=buffer)

        os.write(
            write_fd,
            _msg(
                jsonrpc="2.0",
                id="__init__",
                method="initialize",
                params={"protocolVersion": "2025-11-25"},
            ),
        )
        os.write(
            write_fd,
            _msg(jsonrpc="2.0", id=1, method="tools/call", params={"name": "blocked"}),
        )

        run_task = asyncio.ensure_future(
            server.serve_streams(streams, {"blocked": blocked_tool}, _ctx())
        )

        loop = asyncio.get_running_loop()
        # Bounded: wait for the worker to actually start, off the loop
        # thread, before closing the write end.
        await loop.run_in_executor(None, started.wait, 5)
        os.close(write_fd)

        exit_code = await asyncio.wait_for(run_task, timeout=5)
        event.set()  # release the abandoned worker so it does not linger
        return exit_code, _responses(buffer)

    exit_code, responses = asyncio.run(_scenario())

    assert exit_code == 0
    # No response for the abandoned `tools/call` (id 1) -- only the
    # `initialize` handshake's own result was ever written.
    assert [response.get("id") for response in responses] == ["__init__"]


class _RaisesKeyboardInterrupt:
    """A stand-in for `transport.claim_stdio` that raises on `__enter__`,
    simulating a `KeyboardInterrupt` during `serve()` without touching any
    real file descriptor."""

    def __enter__(self) -> transport.StdioStreams:
        raise KeyboardInterrupt

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_keyboard_interrupt_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers "A KeyboardInterrupt exits distinctly"."""
    monkeypatch.setattr(transport, "claim_stdio", _RaisesKeyboardInterrupt)

    exit_code = server.serve(Path("/nonexistent"), expose_confidential=False)

    assert exit_code == 130


# -- 9.4/9.5: query progress and cancellation, against the REAL query tool --


def _build_query_workspace(root: Path) -> config.WorkspaceLayout:
    """A minimal real workspace with one FTS-matched public concept, and
    `sufficiency_check: false` so `answer()` makes exactly ONE `llm.chat`
    call (design.md's own sequence diagram uses the same 3-phase total)."""
    bundle = root / "bundle"
    concepts = bundle / "concepts"
    concepts.mkdir(parents=True)
    (bundle / "index.md").write_text("# Index\n", encoding="utf-8")
    (bundle / "log.md").write_text("# Log\n", encoding="utf-8")
    (concepts / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: public\n---\n"
        "This concept is about the mcp progress test widget.\n",
        encoding="utf-8",
    )
    (root / "openkos.yaml").write_text("sufficiency_check: false\n", encoding="utf-8")
    layout = config.WorkspaceLayout(root=root)
    fts.write_fts_index(layout.fts_db_path, layout.bundle_dir)
    return layout


class _BlockingLLM:
    """A structural `LLMBackend` whose `chat()` blocks on a
    `threading.Event`, signalling `started` (if given) first -- mirrors
    `_blocking_tool`'s own Event-based blocking convention, but through a
    REAL `query` call's synthesis step rather than a test-only tool."""

    def __init__(
        self, event: threading.Event, *, started: threading.Event | None = None
    ) -> None:
        self._event = event
        self._started = started
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if self._started is not None:
            self._started.set()
        self._event.wait(timeout=5)
        return "the reply"


class _FakeReplyLLM:
    """A structural `LLMBackend` returning a fixed reply immediately --
    no blocking, for the progress-notification test."""

    def __init__(self, reply: str = "the reply") -> None:
        self.reply = reply
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self.reply


class _UnusedEmbedder:
    """A structural `Embedder` `query`'s `run()` always CONSTRUCTS (design
    Decision 9) but this fixture's no-`vectors.db` workspace never actually
    invokes: dense retrieval degrades to FTS-only before ever calling
    `embed()`, so this raises if that assumption is ever violated."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("this fixture has no vectors.db to embed against")


def _query_ctx(layout: config.WorkspaceLayout, llm: LLMBackend) -> tools.ToolContext:
    return tools.ToolContext(
        layout=layout,
        expose_confidential=False,
        make_llm=lambda cfg: llm,
        make_embedder=lambda cfg: _UnusedEmbedder(),
        local_exemption_for=_never_called_local_exemption,
    )


def test_cancellation_abandons_worker_and_unblocks_later(tmp_path: Path) -> None:
    """Covers "A cancelled query sends no response" and "A cancelled
    request does not block later requests" against a REAL `query` call
    blocked inside the LLM synthesis call (design Decision 13), not a
    generic test-only tool. Kills writing a response after `CancelledError`
    is caught, and kills omitting `_finish_inflight` from the
    `CancelledError` branch (`InFlight`'s own contract: "the entry is
    removed when the task completes" -- a cancellation IS a completion)."""
    layout = _build_query_workspace(tmp_path)
    event = threading.Event()
    started = threading.Event()
    llm = _BlockingLLM(event, started=started)
    ctx = _query_ctx(layout, llm)

    async def _scenario() -> list[dict[str, Any]]:
        buffer = io.BytesIO()
        srv = server.Server(tools.REGISTRY, ctx, transport.MessageWriter(buffer))
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id="__init__",
                method="initialize",
                params={"protocolVersion": "2025-11-25"},
            )
        )
        buffer.seek(0)
        buffer.truncate(0)

        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params={"name": "query", "arguments": {"question": "widget"}},
            )
        )
        loop = asyncio.get_running_loop()
        # Bounded: wait for the worker to actually reach the blocked
        # `llm.chat` call before cancelling, so this cancels a request that
        # is genuinely in flight.
        await loop.run_in_executor(None, started.wait, 5)

        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                method="notifications/cancelled",
                params={"requestId": 1},
            )
        )
        for _ in range(500):
            if not srv._inflight:
                break
            await asyncio.sleep(0.01)
        else:
            raise TimeoutError(
                "cancelled request 1's in-flight entry was never removed"
            )

        # A subsequent ping AND tools/call are answered normally, unblocked
        # by the still-running abandoned worker.
        await srv.handle_raw(_msg(jsonrpc="2.0", id=2, method="ping"))
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=3,
                method="tools/call",
                params={"name": "get", "arguments": {"concept_id": "concepts/a"}},
            )
        )
        for _ in range(500):
            ids = {response.get("id") for response in _responses(buffer)}
            if {2, 3} <= ids:
                break
            await asyncio.sleep(0.01)

        event.set()  # release the abandoned worker so it does not linger
        # Bounded settle window: prove releasing it produces no late write.
        await asyncio.sleep(0.05)

        return _responses(buffer)

    responses = asyncio.run(_scenario())
    ids = [response.get("id") for response in responses]

    assert 1 not in ids
    assert 2 in ids
    assert 3 in ids


def test_progress_notifications_monotonic_and_gated_by_token(tmp_path: Path) -> None:
    """Covers "A progressToken produces progress notifications", "No
    progressToken means no progress notifications", and "get, navigate,
    and pending never emit progress". Kills sending a progress notification
    with no token present, and omitting the "already finished" guard."""
    layout = _build_query_workspace(tmp_path)
    ctx = _query_ctx(layout, _FakeReplyLLM())

    async def _scenario() -> list[dict[str, Any]]:
        buffer = io.BytesIO()
        srv = server.Server(tools.REGISTRY, ctx, transport.MessageWriter(buffer))
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id="__init__",
                method="initialize",
                params={"protocolVersion": "2025-11-25"},
            )
        )
        buffer.seek(0)
        buffer.truncate(0)

        async def _await_id(target: object) -> None:
            for _ in range(500):
                if any(response.get("id") == target for response in _responses(buffer)):
                    return
                await asyncio.sleep(0.01)
            raise TimeoutError(f"request {target!r} never completed")

        # 1. WITH a progressToken: query emits progress before its result.
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params={
                    "name": "query",
                    "arguments": {"question": "widget"},
                    "_meta": {"progressToken": "p1"},
                },
            )
        )
        await _await_id(1)

        # 2. WITHOUT a progressToken: no notifications at all for this call.
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=2,
                method="tools/call",
                params={"name": "query", "arguments": {"question": "widget"}},
            )
        )
        await _await_id(2)

        # 3. get/navigate/pending never emit progress, even WITH a token.
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=3,
                method="tools/call",
                params={
                    "name": "get",
                    "arguments": {"concept_id": "concepts/a"},
                    "_meta": {"progressToken": "p3"},
                },
            )
        )
        await _await_id(3)

        return _responses(buffer)

    responses = asyncio.run(_scenario())

    notifications = [
        r for r in responses if r.get("method") == "notifications/progress"
    ]
    p1_notifications = [
        n for n in notifications if n["params"]["progressToken"] == "p1"
    ]
    assert p1_notifications, "expected at least one progress notification for token p1"

    progresses = [n["params"]["progress"] for n in p1_notifications]
    assert all(a < b for a, b in itertools.pairwise(progresses)), progresses

    result_index = next(i for i, r in enumerate(responses) if r.get("id") == 1)
    for notification in p1_notifications:
        assert responses.index(notification) < result_index

    # No other token (in particular, none for id 2's tokenless call or id
    # 3's get call) ever produced a notification.
    assert {n["params"]["progressToken"] for n in notifications} == {"p1"}

    # `get`/`navigate`/`pending` never emit progress even WITH a token
    # present: their own `run()` implementations never call the sink at
    # all (proven above against `get`), and their registrations never set
    # `emits_progress=True` in the first place -- both halves of "confirmed
    # by construction" pinned directly, since a shared-default regression
    # (e.g. flipping `Tool.emits_progress`'s default) would not otherwise
    # be observable through a tool whose `run()` never calls the sink.
    assert tools.REGISTRY["get"].emits_progress is False
    assert tools.REGISTRY["navigate"].emits_progress is False
    assert tools.REGISTRY["pending"].emits_progress is False
    assert tools.REGISTRY["query"].emits_progress is True


def test_late_progress_after_finish_is_dropped() -> None:
    """A progress event that arrives AFTER the response is written
    (simulated via a direct, late `_send_progress` call) is dropped by the
    finished guard -- never written. `_finish_inflight` REMOVES the entry
    (design's "the entry is removed when the task completes"), so a late
    post for it lands on the `entry is None` branch of the guard. Kills
    omitting the "already finished" removal from `_finish_inflight`, which
    would leave the entry behind for a late post to find."""
    buffer = io.BytesIO()

    async def _scenario() -> bytes:
        srv = _make_server(buffer)
        key = server._request_key(1)
        noop_task = asyncio.ensure_future(asyncio.sleep(0))
        await noop_task
        srv._inflight[key] = server.InFlight(task=noop_task)
        srv._finish_inflight(key)  # the request is already finished
        srv._send_progress(key, "tok", "retrieving", 0, 3)
        return buffer.getvalue()

    assert asyncio.run(_scenario()) == b""


def test_progress_dropped_when_not_monotonically_increasing() -> None:
    """A progress event whose `completed` has NOT strictly increased since
    the last one posted for this request is dropped -- the monotonic half
    of the guard, exercised directly against a STILL-in-flight entry (never
    popped), so this is independent of the finished-removal behavior
    `test_late_progress_after_finish_is_dropped` covers. Kills dropping the
    `completed <= entry.last_progress` half of the guard."""
    buffer = io.BytesIO()

    async def _scenario() -> list[dict[str, Any]]:
        srv = _make_server(buffer)
        key = server._request_key(1)
        task = asyncio.ensure_future(asyncio.sleep(5))
        srv._inflight[key] = server.InFlight(task=task)

        srv._send_progress(key, "tok", "retrieving", 0, 3)
        srv._send_progress(key, "tok", "retrieving", 0, 3)  # not increased: dropped
        srv._send_progress(key, "tok", "assembling", 1, 3)
        srv._send_progress(key, "tok", "synthesizing", 0, 3)  # decreased: dropped
        srv._send_progress(key, "tok", "synthesizing", 2, 3)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return _responses(buffer)

    notifications = asyncio.run(_scenario())

    assert [n["params"]["progress"] for n in notifications] == [0, 1, 2]


# -- orchestrator review correction: withhold on ANY prompt object, not only
# a cited one (design Decision 9) -------------------------------------------


class _MidFlightRaiseLLM:
    """A structural `LLMBackend` reproducing, end to end, the exact race
    design Decision 9's fail-closed answer-withholding rule closes: `chat()`
    echoes the WHOLE prompt back as its reply (so a leak would be
    observable verbatim in the raw, unfiltered answer), flips the target
    concept's on-disk `sensitivity` to `confidential` BEFORE returning, and
    reports `USED: none` -- the model's own footer cites NOTHING, so
    `citations` narrows to empty and a citation-only withholding check would
    find zero withheld citations and never withhold the answer, even though
    the now-confidential concept's content is sitting verbatim in the
    echoed reply."""

    def __init__(self, concept_path: Path) -> None:
        self._concept_path = concept_path
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        text = "\n".join(message["content"] for message in messages)
        self._concept_path.write_text(
            self._concept_path.read_text(encoding="utf-8").replace(
                "sensitivity: public", "sensitivity: confidential"
            ),
            encoding="utf-8",
        )
        return f"{text}\n\nUSED: none"


def test_answer_withheld_when_a_prompt_object_is_raised_mid_flight(
    tmp_path: Path,
) -> None:
    """End-to-end proof, through the REAL server path, of the exact race
    design Decision 9 closes (orchestrator review finding): an object read
    as public by `_assemble_context` is raised to confidential DURING
    `llm.chat` -- literally on disk here, by the fake LLM itself, before it
    replies citing nothing. `citations` ends up empty (`USED: none`), so a
    citation-only check would see nothing to withhold and return the raw
    echoed reply verbatim, leaking the marker. `context_ids` (captured
    BEFORE that citation narrowing) closes it: the object entered the
    prompt regardless of citation, so the disclosure snapshot taken AFTER
    `run()` completes still catches it and withholds the whole answer.
    Kills reverting to a citation-only withholding check."""
    bundle = tmp_path / "bundle"
    concepts = bundle / "concepts"
    concepts.mkdir(parents=True)
    (bundle / "index.md").write_text("# Index\n", encoding="utf-8")
    (bundle / "log.md").write_text("# Log\n", encoding="utf-8")
    marker = "RACE-CANARY-MARKER-9C2A"
    concept_path = concepts / "a.md"
    concept_path.write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: public\n---\n"
        f"This concept mentions {marker} in its body.\n",
        encoding="utf-8",
    )
    (tmp_path / "openkos.yaml").write_text(
        "sufficiency_check: false\n", encoding="utf-8"
    )
    layout = config.WorkspaceLayout(root=tmp_path)
    fts.write_fts_index(layout.fts_db_path, layout.bundle_dir)

    llm = _MidFlightRaiseLLM(concept_path)
    ctx = _query_ctx(layout, llm)

    async def _scenario() -> tuple[bytes, dict[str, Any]]:
        buffer = io.BytesIO()
        srv = server.Server(tools.REGISTRY, ctx, transport.MessageWriter(buffer))
        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id="__init__",
                method="initialize",
                params={"protocolVersion": "2025-11-25"},
            )
        )
        buffer.seek(0)
        buffer.truncate(0)

        await srv.handle_raw(
            _msg(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params={"name": "query", "arguments": {"question": marker}},
            )
        )
        for _ in range(500):
            matching = [r for r in _responses(buffer) if r.get("id") == 1]
            if matching:
                return buffer.getvalue(), matching[0]
            await asyncio.sleep(0.01)
        raise TimeoutError("query never completed")

    raw_bytes, response = asyncio.run(_scenario())

    assert marker.encode("utf-8") not in raw_bytes
    result = response["result"]
    structured = result["structuredContent"]
    assert structured["answer"] == ""
    assert structured["answer_withheld"] is True
