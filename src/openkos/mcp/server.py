"""JSON-RPC lifecycle, dispatch, and error mapping (design Decisions 12-13,
ADR-0027).

Targets protocol revision 2025-11-25 only. `initialize` and `ping` are
handled inline on the loop thread, so neither is cancellable; every
`tools/call` runs on its own daemon worker thread (`run_in_worker`), with
cancellation treated as abandonment (ADR-0021 D3) -- no response is ever
sent for a cancelled or abandoned request. `serve_streams` is this
module's testable core: it drives one session to completion over an
already-claimed `transport.StdioStreams`, independent of whether those
streams are real stdio (`serve()`) or an in-memory pipe (the unit tests).

The tool-error table (design Decision 12) maps a service exception raised
from `tools.execute` to a structured tool result (`isError: true`,
`structuredContent.error = {code, retryable, message}`) instead of the
generic `-32603` fallback -- a distinct, EXPECTED failure a client can act
on (retry, or not), never an unexpected internal error. It is ordered
subclass-first (`_tool_error_table_is_subclass_ordered` pins this), and
grows incrementally: this slice adds `get`'s two rows
(`ConceptNotFound`/`OSError`); the Ollama-related rows land in slice 9.
Every `message` is a fixed string, never `str(exc)` -- the same reason the
`-32603` fallback's message is fixed.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from json import dumps as _json_dumps
from pathlib import Path
from typing import Final, Literal, TypeGuard

from openkos import config
from openkos.application import concept_read
from openkos.llm.base import Embedder, LLMBackend
from openkos.mcp import tools as mcp_tools
from openkos.mcp import transport

logger = logging.getLogger("openkos.mcp")

PROTOCOL_VERSION: Final = "2025-11-25"

_PARSE_ERROR: Final = -32700
_INVALID_REQUEST: Final = -32600
_METHOD_NOT_FOUND: Final = -32601
_INVALID_PARAMS: Final = -32602
_INTERNAL_ERROR: Final = -32603
_INTERNAL_ERROR_MESSAGE: Final = "internal error"

_TOOL_ERROR_TABLE: Final[tuple[tuple[type[BaseException], str, bool, str], ...]] = (
    (
        concept_read.ConceptNotFound,
        "concept_not_found",
        False,
        "the requested concept does not exist",
    ),
    (
        OSError,
        "read_failed",
        True,
        "a read failed",
    ),
)
"""Rows land incrementally (this slice's two, then slice 9's Ollama-related
ones); ordering is subclass-first so a specific row is matched before a
more general one that would also `isinstance`-match it."""


def _mapped_tool_error(exc: BaseException) -> tuple[str, bool, str] | None:
    """The `(code, retryable, message)` row matching `exc`, or `None` when
    no row applies -- the caller then falls through to the generic
    `-32603`."""
    for exc_type, code, retryable, message in _TOOL_ERROR_TABLE:
        if isinstance(exc, exc_type):
            return code, retryable, message
    return None


_INSTRUCTIONS: Final = (
    "This server exposes one OpenKOS workspace as four read-only tools: "
    "query, get, navigate, and pending. Confidential objects are withheld "
    "unless the server was launched with --expose-confidential."
)

RequestKey = tuple[Literal["s", "i"], str | int]
"""Distinguishes a string id `"1"` from an integer id `1` -- JSON-RPC
treats them as different ids."""


def _request_key(raw_id: str | int) -> RequestKey:
    return ("s", raw_id) if isinstance(raw_id, str) else ("i", raw_id)


def _valid_id(value: object) -> TypeGuard[str | int]:
    """A JSON-RPC id (or a `progressToken`) must be a string or a
    non-boolean integer -- `bool` is an `int` subclass in Python, and
    design Decision 12 explicitly rejects a boolean id."""
    return isinstance(value, str) or (
        isinstance(value, int) and not isinstance(value, bool)
    )


def _echoable_id(value: Mapping[str, object]) -> str | int | None:
    """The id to echo on an envelope-level `-32600`: the request's own id
    when it was a valid type, else `null` (design Decision 12)."""
    if "id" not in value:
        return None
    raw_id = value["id"]
    return raw_id if _valid_id(raw_id) else None


@dataclass
class InFlight:
    """One in-flight `tools/call` request: its task, whether it has
    already finished, and (from slice 9) the last progress value posted
    for it."""

    task: asyncio.Task[None]
    finished: bool = False
    last_progress: int = -1


def _server_version() -> str:
    """The `serverInfo.version` `initialize` reports, read from installed
    distribution metadata. `PackageNotFoundError` -- realistically only a
    raw `sys.path` run with no install step -- degrades to `"0+unknown"`
    (design's wire-shape note), never a hardcoded constant that could drift
    from the built artifact."""
    try:
        return _pkg_version("openkos")
    except PackageNotFoundError:
        return "0+unknown"


def _unwired_make_llm(cfg: config.Config) -> LLMBackend:
    raise NotImplementedError(
        "query's LLM factory is wired once the query tool is registered (slice 9)"
    )


def _unwired_make_embedder(cfg: config.Config) -> Embedder:
    raise NotImplementedError(
        "query's embedder factory is wired once the query tool is registered (slice 9)"
    )


def _unwired_local_exemption_for(client: LLMBackend, cfg: config.Config) -> bool:
    raise NotImplementedError(
        "the local-exemption check is wired once the query tool is registered (slice 9)"
    )


def _build_context(root: Path, *, expose_confidential: bool) -> mcp_tools.ToolContext:
    """Build the `ToolContext` `serve()` hands to every tool call.

    `make_llm`/`make_embedder`/`local_exemption_for` are placeholders: the
    registry is empty in this slice, so nothing ever calls them. `query`
    (slice 9) is what replaces these three with real factories.
    """
    return mcp_tools.ToolContext(
        layout=config.WorkspaceLayout(root=root),
        expose_confidential=expose_confidential,
        make_llm=_unwired_make_llm,
        make_embedder=_unwired_make_embedder,
        local_exemption_for=_unwired_local_exemption_for,
    )


async def run_in_worker[T](fn: Callable[[], T]) -> T:
    """Run `fn` on its own daemon worker thread and await its result on the
    calling loop (design Decision 13: one worker thread per tool call).

    Daemon, and never joined: on end of input the process must be able to
    exit without waiting for an abandoned read to finish (ADR-0027 -- safe
    only because every tool here is read-only). If the awaiting future is
    already resolved -- because the task awaiting it was cancelled -- the
    thread's eventual result or exception is dropped instead of raising
    `InvalidStateError`, and a closed loop's `RuntimeError` from
    `call_soon_threadsafe` is swallowed the same way `transport.
    start_reader` swallows it.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def _post(callback: Callable[..., None], *args: object) -> None:
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(callback, *args)

    def _resolve_result(result: T) -> None:
        if not future.done():
            future.set_result(result)

    def _resolve_exception(exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)

    def _run() -> None:
        try:
            result = fn()
        except Exception as exc:  # forwarded to the awaiting caller
            _post(_resolve_exception, exc)
        else:
            _post(_resolve_result, result)

    threading.Thread(target=_run, daemon=True, name="mcp-tool-worker").start()
    return await future


def _tool_call_result(
    is_error: bool, structured_content: dict[str, object]
) -> dict[str, object]:
    """Wrap a tool's disclosure-safe payload into the shape `tools/call`
    returns: `structuredContent`, plus the same JSON in a `text` content
    block for a client that ignores structured content (design Decision
    12)."""
    text = _json_dumps(structured_content, ensure_ascii=False)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured_content,
        "isError": is_error,
    }


class Server:
    """One JSON-RPC session's lifecycle, dispatch table, and in-flight
    request map (design Decisions 12-13)."""

    def __init__(
        self,
        registry: Mapping[str, mcp_tools.Tool],
        ctx: mcp_tools.ToolContext,
        writer: transport.MessageWriter,
    ) -> None:
        self._registry = registry
        self._ctx = ctx
        self._writer = writer
        self._initialize_seen = False
        self._initialized = False
        self._inflight: dict[RequestKey, InFlight] = {}

    async def handle_raw(self, raw: bytes) -> None:
        """Decode and dispatch one line. A parse failure answers `-32700`
        with `id: null` (design Decision 12); a blank line is ignored."""
        try:
            value = transport.decode_line(raw)
        except transport.ParseError:
            self._send_error(None, _PARSE_ERROR, "parse error")
            return
        if value is None:
            return
        await self._dispatch_value(value)

    async def abandon_all(self) -> int:
        """Cancel and await every in-flight task -- abandonment, not a
        stop (ADR-0021 D3) -- and return how many were abandoned. The
        worker thread behind each task is never joined; only its awaiting
        task is cancelled."""
        pending = list(self._inflight.values())
        for entry in pending:
            entry.task.cancel()
        if pending:
            await asyncio.gather(
                *(entry.task for entry in pending), return_exceptions=True
            )
        return len(pending)

    # -- envelope classification ------------------------------------

    async def _dispatch_value(self, value: object) -> None:
        if isinstance(value, list):
            self._send_error(None, _INVALID_REQUEST, "batch requests are not supported")
            return
        if not isinstance(value, dict):
            self._send_error(None, _INVALID_REQUEST, "invalid request")
            return
        if "method" not in value:
            return  # a response from the client -- we send no requests

        method = value.get("method")
        if value.get("jsonrpc") != "2.0" or not isinstance(method, str):
            self._send_error(_echoable_id(value), _INVALID_REQUEST, "invalid request")
            return

        if "id" not in value:
            self._dispatch_notification(method, value.get("params"))
            return

        raw_id = value["id"]
        if not _valid_id(raw_id):
            self._send_error(None, _INVALID_REQUEST, "invalid request")
            return

        await self._dispatch_request(raw_id, method, value.get("params"))

    # -- requests ------------------------------------------------------

    async def _dispatch_request(
        self, request_id: str | int, method: str, params: object
    ) -> None:
        if method == "initialize":
            self._handle_initialize(request_id, params)
            return
        if method == "ping":
            self._send_result(request_id, {})
            return
        if not self._initialized:
            self._send_error(request_id, _INVALID_REQUEST, "server not initialized")
            return
        if method == "tools/list":
            self._send_result(request_id, self._tools_list_result())
            return
        if method == "tools/call":
            await self._dispatch_tool_call(request_id, params)
            return
        self._send_error(request_id, _METHOD_NOT_FOUND, f"unknown method: {method}")

    def _handle_initialize(self, request_id: str | int, params: object) -> None:
        if self._initialize_seen:
            self._send_error(request_id, _INVALID_REQUEST, "server already initialized")
            return
        if not isinstance(params, dict) or not isinstance(
            params.get("protocolVersion"), str
        ):
            self._send_error(
                request_id, _INVALID_PARAMS, "protocolVersion must be a string"
            )
            return

        self._initialize_seen = True
        self._initialized = True
        self._send_result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "openkos", "version": _server_version()},
                "instructions": _INSTRUCTIONS,
            },
        )

    def _tools_list_result(self) -> dict[str, object]:
        return {
            "tools": [
                {
                    "name": tool.name,
                    "title": tool.title,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                    "outputSchema": tool.output_schema,
                    "annotations": {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": True,
                        "openWorldHint": False,
                    },
                }
                for tool in self._registry.values()
            ]
        }

    async def _dispatch_tool_call(self, request_id: str | int, params: object) -> None:
        if not isinstance(params, dict):
            self._send_error(request_id, _INVALID_PARAMS, "params must be an object")
            return
        name = params.get("name")
        if not isinstance(name, str):
            self._send_error(request_id, _INVALID_PARAMS, "name must be a string")
            return

        raw_arguments = params.get("arguments")
        arguments: Mapping[str, object]
        if raw_arguments is None:
            arguments = {}
        elif isinstance(raw_arguments, dict):
            arguments = raw_arguments
        else:
            self._send_error(request_id, _INVALID_PARAMS, "arguments must be an object")
            return

        meta = params.get("_meta")
        if (
            isinstance(meta, dict)
            and "progressToken" in meta
            and not _valid_id(meta["progressToken"])
        ):
            self._send_error(
                request_id,
                _INVALID_PARAMS,
                "progressToken must be a string or an integer",
            )
            return

        tool = self._registry.get(name)
        if tool is None:
            self._send_error(request_id, _INVALID_PARAMS, f"unknown tool: {name}")
            return

        key = _request_key(request_id)
        if key in self._inflight:
            self._send_error(request_id, _INVALID_REQUEST, "duplicate request id")
            return

        task = asyncio.ensure_future(self._run_tool(request_id, key, tool, arguments))
        self._inflight[key] = InFlight(task=task)

    async def _run_tool(
        self,
        request_id: str | int,
        key: RequestKey,
        tool: mcp_tools.Tool,
        arguments: Mapping[str, object],
    ) -> None:
        try:
            is_error, structured_content = await run_in_worker(
                lambda: mcp_tools.execute(tool, arguments, self._ctx, None)
            )
        except asyncio.CancelledError:
            logger.info("request %r cancelled; its worker was abandoned", request_id)
            raise
        except Exception as exc:
            mapped = _mapped_tool_error(exc)
            if mapped is not None:
                code, retryable, message = mapped
                self._finish_inflight(key)
                self._send_result(
                    request_id,
                    _tool_call_result(
                        True,
                        {
                            "error": {
                                "code": code,
                                "retryable": retryable,
                                "message": message,
                            },
                            "withheld": 0,
                            "warnings": [],
                            "not_run": [],
                        },
                    ),
                )
                return
            logger.exception(
                "internal error handling tools/call for request %r", request_id
            )
            self._finish_inflight(key)
            self._send_error(request_id, _INTERNAL_ERROR, _INTERNAL_ERROR_MESSAGE)
            return
        self._finish_inflight(key)
        self._send_result(request_id, _tool_call_result(is_error, structured_content))

    def _finish_inflight(self, key: RequestKey) -> None:
        entry = self._inflight.pop(key, None)
        if entry is not None:
            entry.finished = True

    # -- notifications --------------------------------------------------

    def _dispatch_notification(self, method: str, params: object) -> None:
        if method == "notifications/cancelled":
            self._handle_cancel(params)
        # Every other notification -- known (`notifications/initialized`,
        # optional per design Decision 12) or unknown -- is silently
        # ignored.

    def _handle_cancel(self, params: object) -> None:
        if not isinstance(params, dict):
            return
        raw_id = params.get("requestId")
        if not _valid_id(raw_id):
            return
        entry = self._inflight.get(_request_key(raw_id))
        if entry is None or entry.finished:
            # Unknown id, an already-finished request, or an `initialize`
            # id (never tracked here, since `initialize` is handled
            # inline and is not cancellable) -- all ignored.
            return
        entry.task.cancel()
        logger.info("request %r cancelled; its worker was abandoned", raw_id)

    # -- wire I/O --------------------------------------------------------

    def _send_result(self, request_id: str | int, result: object) -> None:
        self._writer.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _send_error(
        self, request_id: str | int | None, code: int, message: str
    ) -> None:
        self._writer.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )


async def serve_streams(
    streams: transport.StdioStreams,
    registry: Mapping[str, mcp_tools.Tool],
    ctx: mcp_tools.ToolContext,
) -> int:
    """Drive one session to completion over `streams`.

    Reads frames until end of input, dispatching each through `Server`,
    then abandons whatever is still in flight and returns `0` (design
    Decision 13's end-of-input behavior). Independent of whether `streams`
    is real stdio (`serve()`) or an in-memory pipe -- this is the seam the
    lifecycle and cancellation tests drive directly.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    server = Server(registry, ctx, transport.MessageWriter(streams.writer))
    transport.start_reader(streams.reader, loop, queue)

    while True:
        line = await queue.get()
        if line is None:
            break
        await server.handle_raw(line)

    abandoned = await server.abandon_all()
    if abandoned:
        logger.info("end of input: abandoned %d in-flight request(s)", abandoned)
    return 0


def serve(root: Path, *, expose_confidential: bool) -> int:
    """Serve `root` over stdio until end of input or `KeyboardInterrupt`
    (design Decisions 12-14).

    The workspace itself is validated by the caller (the `openkos mcp`
    verb, slice 4) before this is ever called -- `serve()` does not
    re-validate it.
    """
    ctx = _build_context(root, expose_confidential=expose_confidential)
    handler = logging.StreamHandler(sys.stderr)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        with transport.claim_stdio() as streams:
            return asyncio.run(serve_streams(streams, mcp_tools.REGISTRY, ctx))
    except KeyboardInterrupt:
        return 130
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
