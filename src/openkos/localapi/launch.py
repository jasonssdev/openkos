"""Launch the local API: token in, loopback socket bound, one ready line out.

The order is fixed, because each step is a promise to the shell that spawned this
process: open the workspace, read the bearer token from stdin (never argv,
environment, file or URL), bind `127.0.0.1` on an ephemeral port ourselves, hand
that socket to uvicorn, and only once uvicorn is listening write the single JSON
line that tells the shell the port. Nothing else is ever written to stdout.

Two lifelines end the server gracefully: EOF on stdin (the shell died or closed
the pipe, so there is no orphan) and SIGTERM or SIGINT.

Exit codes: 0 graceful shutdown; 1 runtime failure (workspace refused, bind or
startup failure); 2 usage failure (token missing or invalid).
"""

import hashlib
import json
import os
import re
import signal
import socket
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Any, BinaryIO, Final, TextIO

import uvicorn

from openkos import api
from openkos.localapi._app import ServerSettings, create_app

HOST: Final = "127.0.0.1"
BASE_PATH: Final = "/v1"
GRACEFUL_SHUTDOWN_SECONDS: Final = 5
READY_POLL_SECONDS: Final = 0.02

# RFC 6750 b64token, with a 32-character floor. The line adds at most `\r\n`.
_TOKEN: Final = re.compile(r"[A-Za-z0-9\-._~+/]{32,1024}=*")
_MAX_LINE_BYTES: Final = 1026
_DRAIN_BYTES: Final = 4096


class TokenError(ValueError):
    """The token line is missing or malformed. The message never carries input."""


def read_token(stream: BinaryIO) -> str:
    """Read the one `\\n`-terminated token line from `stream`."""
    line = stream.readline(_MAX_LINE_BYTES + 1)
    if len(line) > _MAX_LINE_BYTES:
        raise TokenError("the token line is too long")
    if not line.endswith(b"\n"):
        raise TokenError("no token was received on stdin")
    text = line[:-1]
    if text.endswith(b"\r"):
        text = text[:-1]
    try:
        token = text.decode("ascii")
    except UnicodeDecodeError:
        raise TokenError("the token is not valid") from None
    if _TOKEN.fullmatch(token) is None:
        raise TokenError("the token is not valid")
    return token


def ready_line(port: int) -> str:
    """The single stdout line that announces the bound port."""
    return json.dumps(
        {
            "event": "ready",
            "host": HOST,
            "port": port,
            "api_version": api.API_VERSION,
            "base_path": BASE_PATH,
        }
    )


def _watch_stdin(stream: BinaryIO, server: uvicorn.Server) -> None:
    """Discard stdin until EOF, then ask the server to exit."""
    try:
        try:
            fd = stream.fileno()
        except OSError, ValueError:  # not fd-backed (an in-memory stream)
            while stream.read(_DRAIN_BYTES):
                pass
        else:
            # Raw `os.read`, not the buffered reader: a daemon thread parked in
            # `BufferedReader.read` holds its lock, and interpreter shutdown
            # after SIGTERM aborts ("could not acquire lock") when it does.
            while os.read(fd, _DRAIN_BYTES):
                pass
    except OSError, ValueError:
        pass  # a closed stream is the shell going away, the same as EOF
    server.should_exit = True


def _announce_when_started(
    server: uvicorn.Server, stdout: TextIO, port: int, finished: threading.Event
) -> None:
    """Write the ready line once the server is listening, never before."""
    while not server.started:
        if finished.is_set():
            return
        finished.wait(READY_POLL_SECONDS)
    stdout.write(ready_line(port) + "\n")
    stdout.flush()


def serve(
    root: Path,
    *,
    stdin: BinaryIO | None = None,
    stdout: TextIO | None = None,
    install_signals: bool = True,
) -> int:
    """Run the local API for the workspace at `root` until a lifeline fires."""
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout

    try:
        # `absolute()`, never `resolve()`: a symlinked path must still be refused.
        workspace = api.Workspace.open(Path(root).absolute())
    except (api.WorkspaceNotFound, api.WorkspaceUnreadable) as exc:
        print(
            f"openkos serve: refusing to serve -- {exc.reason.rstrip('.')}.",
            file=sys.stderr,
        )
        return 1

    if stdin.isatty():
        print("openkos serve: reading the token from stdin.", file=sys.stderr)
    try:
        token = read_token(stdin)
    except TokenError as exc:
        print(f"openkos serve: {exc}.", file=sys.stderr)
        return 2
    digest = hashlib.sha256(token.encode("ascii")).digest()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            sock.bind((HOST, 0))
        except OSError as exc:
            print(
                f"openkos serve: could not bind {HOST} ({exc.strerror or 'failed'}).",
                file=sys.stderr,
            )
            return 1
        port = int(sock.getsockname()[1])
        return _run(
            create_app(
                ServerSettings(workspace=workspace, port=port, token_digest=digest)
            ),
            sock,
            port,
            stdin,
            stdout,
            install_signals,
        )
    finally:
        sock.close()


def _run(
    app: object,
    sock: socket.socket,
    port: int,
    stdin: BinaryIO,
    stdout: TextIO,
    install_signals: bool,
) -> int:
    server = uvicorn.Server(
        uvicorn.Config(
            app,  # type: ignore[arg-type]  # a FastAPI app, an ASGI callable
            lifespan="on",
            loop="asyncio",
            http="h11",
            ws="none",
            proxy_headers=False,
            server_header=False,
            access_log=False,
            log_config=None,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )
    )

    def on_signal(_signum: int, _frame: FrameType | None) -> None:
        server.should_exit = True

    previous: dict[signal.Signals, Any] = {}
    if install_signals:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, on_signal)

    finished = threading.Event()
    announcer = threading.Thread(
        target=_announce_when_started,
        args=(server, stdout, port, finished),
        daemon=True,
    )
    # Daemon: a blocking read on the real stdin cannot be joined at exit.
    threading.Thread(target=_watch_stdin, args=(stdin, server), daemon=True).start()
    announcer.start()
    try:
        server.run(sockets=[sock])
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- uvicorn exits 1 on a startup failure
        print(
            f"openkos serve: the server stopped ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 1
    finally:
        finished.set()
        announcer.join()
        for restored, handler in previous.items():
            signal.signal(restored, handler)
    return 0 if server.started else 1
