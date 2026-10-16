"""The launch sequence: token reader, bind, ready line, lifelines.

In-process, with injected `stdin` and `stdout` and `install_signals=False`. The
unit network guard refuses a loopback `connect`, so no test opens a client
connection; the real-process lifecycle is proven by the subprocess tests. Where a
listener is not the point, uvicorn is replaced by a recording fake; one test
runs the real server on the bound socket and never connects to it.
"""

import io
import json
import os
import signal
import socket
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from openkos import api
from openkos.localapi import _runs, launch
from tests.unit.application.curation_support import make_workspace

TOKEN = "A" * 24 + "b-._~+/9" + "=="
WAIT = 10.0
"""A bound on every wait, so a defect fails the test instead of hanging it."""


class _Tty(io.BytesIO):
    def isatty(self) -> bool:
        return True


class _Stdout(io.StringIO):
    """A stdout that announces its first write, so a test can wait without sleeping."""

    def __init__(self) -> None:
        super().__init__()
        self.written = threading.Event()

    def write(self, text: str) -> int:
        count = super().write(text)
        self.written.set()
        return count


class FakeServer:
    """Stands in for `uvicorn.Server`: `run` records, then waits for `should_exit`."""

    instances: ClassVar[list[FakeServer]] = []
    starts: ClassVar[bool] = True
    raises: ClassVar[BaseException | None] = None

    def __init__(self, config: Any) -> None:
        self.config = config
        self.started = False
        self.exit = threading.Event()
        self.socket_names: list[Any] = []
        self.calls = 0
        FakeServer.instances.append(self)

    @property
    def should_exit(self) -> bool:
        return self.exit.is_set()

    @should_exit.setter
    def should_exit(self, value: bool) -> None:
        if value:
            self.exit.set()

    def run(self, sockets: list[socket.socket]) -> None:
        self.calls += 1
        self.socket_names = [s.getsockname() for s in sockets]
        if self.raises is not None:
            raise self.raises
        if not self.starts:
            return
        self.started = True
        assert self.exit.wait(WAIT), "nothing asked the server to exit"


@pytest.fixture
def fake_uvicorn(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace uvicorn inside `launch`; returns the `Config` keyword calls."""
    configs: list[dict[str, Any]] = []

    def config(app: object, **kwargs: Any) -> dict[str, Any]:
        configs.append(kwargs)
        return {"app": app, **kwargs}

    FakeServer.instances = []
    FakeServer.starts = True
    FakeServer.raises = None
    monkeypatch.setattr(
        launch, "uvicorn", SimpleNamespace(Config=config, Server=FakeServer)
    )
    return configs


@pytest.fixture
def sockets_created(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Record every socket `launch` creates; the real class is still used."""
    created: list[object] = []
    real = socket.socket

    def make(*args: Any, **kwargs: Any) -> socket.socket:
        sock = real(*args, **kwargs)
        created.append(sock)
        return sock

    monkeypatch.setattr(
        launch,
        "socket",
        SimpleNamespace(
            socket=make, AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM
        ),
    )
    return created


def stdin_with(data: bytes) -> io.BytesIO:
    return io.BytesIO(data)


def run_serve(root: Path, stdin: io.BytesIO) -> tuple[int, _Stdout]:
    out = _Stdout()
    code = launch.serve(root, stdin=stdin, stdout=out, install_signals=False)
    return code, out


# --- S5a.1: the token reader ------------------------------------------------


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
def test_read_token_accepts_a_newline_terminated_line(ending: bytes) -> None:
    assert launch.read_token(io.BytesIO(TOKEN.encode() + ending)) == TOKEN


def test_read_token_leaves_what_follows_the_line_unread() -> None:
    stream = io.BytesIO(TOKEN.encode() + b"\nrest")
    launch.read_token(stream)
    assert stream.read() == b"rest"


def test_read_token_accepts_the_longest_token() -> None:
    longest = "x" * 1024
    assert launch.read_token(io.BytesIO(longest.encode() + b"\r\n")) == longest


MARKER = "SECRETMARKER"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty-stream"),
        pytest.param(TOKEN.encode(), id="no-newline"),
        pytest.param(b"\n", id="empty-line"),
        pytest.param(b"\r\n", id="only-cr"),
        pytest.param(("x" * 1025).encode() + b"\n", id="too-long"),
        pytest.param(("x" * 2000).encode(), id="far-too-long-no-newline"),
        pytest.param((MARKER + "!" * 30).encode() + b"\n", id="bad-character"),
        pytest.param((MARKER * 3).encode() + b" tail\n", id="space"),
        pytest.param(("é" + MARKER * 4).encode() + b"\n", id="non-ascii"),
        pytest.param(("x" * 31).encode() + b"\n", id="too-short"),
        pytest.param(("x" * 40 + "=a").encode() + b"\n", id="padding-then-data"),
    ],
)
def test_read_token_refuses_a_bad_line_without_echoing_it(data: bytes) -> None:
    with pytest.raises(launch.TokenError) as caught:
        launch.read_token(io.BytesIO(data))
    message = str(caught.value)
    assert message
    assert MARKER not in message
    assert "x" * 5 not in message


# --- S5a.2: the ready line and the bind --------------------------------------


def test_ready_line_is_one_json_object_with_exactly_the_documented_fields() -> None:
    line = launch.ready_line(51234)
    assert "\n" not in line
    assert json.loads(line) == {
        "event": "ready",
        "host": "127.0.0.1",
        "port": 51234,
        "api_version": api.API_VERSION,
        "base_path": "/v1",
    }


def test_the_bound_socket_is_loopback_v4_and_its_port_is_announced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    code, out = run_serve(root, stdin_with(TOKEN.encode() + b"\n"))
    assert code == 0
    (server,) = FakeServer.instances
    ((host, port),) = server.socket_names
    assert host == "127.0.0.1"
    assert port > 0
    assert json.loads(out.getvalue())["port"] == port


# --- S5a.3: ordering and exits ------------------------------------------------


def test_an_invalid_workspace_exits_1_before_anything_is_bound_or_written(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fake_uvicorn: list[dict[str, Any]],
    sockets_created: list[object],
) -> None:
    code, out = run_serve(tmp_path / "nowhere", stdin_with(TOKEN.encode() + b"\n"))
    assert code == 1
    assert "openkos serve: refusing to serve -- " in capsys.readouterr().err
    assert out.getvalue() == ""
    assert sockets_created == []
    assert FakeServer.instances == []


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="missing"),
        pytest.param(b"\n", id="empty"),
        pytest.param(("t" * 5 + MARKER).encode() + b"\n", id="malformed"),
    ],
)
def test_a_bad_token_exits_2_before_any_socket_exists(
    data: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_uvicorn: list[dict[str, Any]],
    sockets_created: list[object],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    code, out = run_serve(root, stdin_with(data))
    err = capsys.readouterr().err
    assert code == 2
    assert out.getvalue() == ""
    assert MARKER not in err
    assert err.startswith("openkos serve: ")
    assert sockets_created == []
    assert FakeServer.instances == []


def test_a_terminal_stdin_prints_the_one_hint_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    code, out = run_serve(root, _Tty(TOKEN.encode() + b"\n"))
    err = capsys.readouterr().err
    assert code == 0
    assert err.count("reading the token from stdin") == 1
    assert TOKEN not in err
    assert TOKEN not in out.getvalue()


def test_a_pipe_stdin_prints_no_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    run_serve(root, stdin_with(TOKEN.encode() + b"\n"))
    assert capsys.readouterr().err == ""


def test_a_workspace_with_a_symlinked_segment_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_uvicorn: list[dict[str, Any]],
    sockets_created: list[object],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "raw").rmdir()
    (root / "raw").symlink_to(outside, target_is_directory=True)
    code, out = run_serve(root, stdin_with(TOKEN.encode() + b"\n"))
    assert code == 1
    assert "refusing to serve" in capsys.readouterr().err
    assert out.getvalue() == ""
    assert sockets_created == []


def test_the_workspace_is_opened_with_absolute_not_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    monkeypatch.chdir(root.parent)
    opened: list[Path] = []
    real = api.Workspace.open

    def recording(path: str | os.PathLike[str]) -> api.Workspace:
        opened.append(Path(path))
        return real(path)

    monkeypatch.setattr(api.Workspace, "open", staticmethod(recording))
    code, _ = run_serve(Path(root.name), stdin_with(TOKEN.encode() + b"\n"))
    assert code == 0
    assert opened == [root.absolute()]


def test_a_bind_failure_exits_1_with_nothing_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)

    class Refusing:
        def bind(self, _address: object) -> None:
            raise OSError(98, "Address already in use")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        launch,
        "socket",
        SimpleNamespace(
            socket=lambda *_a, **_k: Refusing(),
            AF_INET=socket.AF_INET,
            SOCK_STREAM=socket.SOCK_STREAM,
        ),
    )
    code, out = run_serve(root, stdin_with(TOKEN.encode() + b"\n"))
    assert code == 1
    assert "could not bind 127.0.0.1" in capsys.readouterr().err
    assert out.getvalue() == ""
    assert FakeServer.instances == []


# --- S5a.4: the server configuration and the ready ordering --------------------


def test_uvicorn_is_configured_for_a_loopback_only_quiet_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    run_serve(root, stdin_with(TOKEN.encode() + b"\n"))
    (kwargs,) = fake_uvicorn
    assert kwargs == {
        "lifespan": "on",
        "loop": "asyncio",
        "http": "h11",
        "ws": "none",
        "proxy_headers": False,
        "server_header": False,
        "access_log": False,
        "log_config": None,
        "timeout_graceful_shutdown": 5,
    }


def test_exactly_one_line_is_written_and_only_once_the_server_has_started(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    out = _Stdout()
    seen: list[tuple[str, bool]] = []
    original = FakeServer.run

    def run(self: FakeServer, sockets: list[socket.socket]) -> None:
        # Before the server reports started, nothing may have been written.
        seen.append((out.getvalue(), self.started))
        original(self, sockets)

    monkeypatch.setattr(FakeServer, "run", run)
    code = launch.serve(
        root,
        stdin=stdin_with(TOKEN.encode() + b"\n"),
        stdout=out,
        install_signals=False,
    )
    assert code == 0
    assert seen == [("", False)]
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "ready"
    assert out.getvalue().endswith("\n")


def test_a_server_that_stops_before_starting_writes_no_line_and_returns_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    FakeServer.starts = False
    code, out = run_serve(root, stdin_with(TOKEN.encode() + b"\n"))
    assert code == 1
    assert out.getvalue() == ""


@pytest.mark.parametrize("failure", [SystemExit(1), RuntimeError("boom " + MARKER)])
def test_a_server_that_raises_returns_1_and_names_only_the_type(
    failure: BaseException,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    FakeServer.raises = failure
    code, out = run_serve(root, stdin_with(TOKEN.encode() + b"\n"))
    err = capsys.readouterr().err
    assert code == 1
    assert out.getvalue() == ""
    assert type(failure).__name__ in err
    assert MARKER not in err


# --- S5a.5: the lifelines ------------------------------------------------------


def test_eof_after_the_token_line_stops_the_server_and_returns_0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    # The run only returns when the stdin watcher asked it to exit.
    code, out = run_serve(root, stdin_with(TOKEN.encode() + b"\n"))
    assert code == 0
    (server,) = FakeServer.instances
    assert server.should_exit
    assert len(out.getvalue().splitlines()) == 1


def test_data_after_the_token_line_is_discarded_without_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    junk = b"x" * (3 * launch._DRAIN_BYTES + 7)
    code, out = run_serve(root, stdin_with(TOKEN.encode() + b"\n" + junk))
    assert code == 0
    assert len(out.getvalue().splitlines()) == 1


def test_a_closed_stdin_counts_as_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)

    class Closes(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise ValueError("I/O operation on closed file")

    stdin = Closes(TOKEN.encode() + b"\n")
    stdin.readline = io.BytesIO(TOKEN.encode() + b"\n").readline  # type: ignore[method-assign]
    code, _ = run_serve(root, stdin)
    assert code == 0


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_handler_is_installed_for_the_run_and_restored_after(
    sig: signal.Signals,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    installed: list[Any] = []

    class Quiet(io.BytesIO):
        """A stdin that never reaches EOF, so only the signal can stop the run."""

        def read(self, size: int | None = -1) -> bytes:
            threading.Event().wait(WAIT)
            return b""

    def run(self: FakeServer, sockets: list[socket.socket]) -> None:
        handler = signal.getsignal(sig)
        installed.append(handler)
        assert not self.exit.is_set()
        assert callable(handler)
        handler(sig, None)
        assert self.exit.is_set()
        self.started = True

    monkeypatch.setattr(FakeServer, "run", run)
    stdin = Quiet(TOKEN.encode() + b"\n")
    stdin.readline = io.BytesIO(TOKEN.encode() + b"\n").readline  # type: ignore[method-assign]
    out = _Stdout()
    try:
        code = launch.serve(root, stdin=stdin, stdout=out, install_signals=True)
    finally:
        after = {s: signal.getsignal(s) for s in before}
        for s, handler in before.items():
            signal.signal(s, handler)
    assert code == 0
    assert len(installed) == 1
    assert installed[0] is not before[sig]
    assert after == before


def test_no_signal_handler_is_installed_when_not_asked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_uvicorn: list[dict[str, Any]],
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    before = signal.getsignal(signal.SIGTERM)
    seen: list[Any] = []
    original = FakeServer.run

    def run(self: FakeServer, sockets: list[socket.socket]) -> None:
        seen.append(signal.getsignal(signal.SIGTERM))
        original(self, sockets)

    monkeypatch.setattr(FakeServer, "run", run)
    run_serve(root, stdin_with(TOKEN.encode() + b"\n"))
    assert seen == [before]


def test_the_real_server_announces_once_and_shuts_down_the_registry_on_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real uvicorn on the bound socket, no client: EOF drives a graceful stop."""
    root = make_workspace(tmp_path, monkeypatch)
    graces: list[float] = []
    monkeypatch.setattr(
        _runs.RunRegistry, "shutdown", lambda _self, grace: graces.append(grace)
    )
    read_fd, write_fd = os.pipe()
    stdin = os.fdopen(read_fd, "rb")
    os.write(write_fd, TOKEN.encode() + b"\n")
    out = _Stdout()
    result: list[int] = []

    def target() -> None:
        result.append(
            launch.serve(root, stdin=stdin, stdout=out, install_signals=False)
        )

    thread = threading.Thread(target=target)
    thread.start()
    try:
        assert out.written.wait(WAIT), "the server never announced itself"
    finally:
        os.close(write_fd)
        thread.join(WAIT)
        stdin.close()
    assert not thread.is_alive()
    assert result == [0]
    (line,) = out.getvalue().splitlines()
    assert json.loads(line)["host"] == "127.0.0.1"
    assert graces == [_runs.SHUTDOWN_GRACE_SECONDS]
    assert launch.GRACEFUL_SHUTDOWN_SECONDS == _runs.SHUTDOWN_GRACE_SECONDS
