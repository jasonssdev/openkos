"""Shared test infrastructure for `tests/unit/**`.

Two concerns live here: the reindex-lock-handling error builders, and the
unit suite's fail-closed network guard (#217).

## Lock-error builders

`make_locked_error`/`make_non_lock_operational_error` build `sqlite3.
OperationalError` instances shaped like real SQLite failures, for injecting
at any write surface (store open, `upsert_many`/commit, `BEGIN IMMEDIATE`)
without needing a genuine concurrent second connection holding a lock.
Manual `sqlite_errorcode` assignment is confirmed reliable on this
interpreter (Python 3.13) by
`tests/unit/state/test_derived.py::test_sqlite_operational_error_supports_manual_errorcode_assignment`
(task 1.1's decision test) -- if that assumption ever regresses on a future
interpreter, the documented fallback is a real 2nd-connection `BEGIN
IMMEDIATE` + `busy_timeout=0` competing-lock scenario (design:
sdd/reindex-lock-handling), not a change to these helpers' call sites.

## Fail-closed network guard (#217)

A unit test that reaches a real service is not a unit test, and it fails
differently depending on whether the developer happens to have Ollama
running -- so a green run proves less than it looks. Worse, CI (no Ollama)
and a developer laptop (Ollama up) exercise DIFFERENT code paths, which is
how #217 was found: a `forget` test about TTY refusal failed once in five
full-suite runs and never reproduced.

`_no_network_by_default` makes that failure mode loud instead of
intermittent. It replaces the connecting `socket.socket` methods AND the
module-level name-resolution helpers with a raiser, so the first test to
reach for the network names itself and the address it wanted, rather than
quietly inheriting the host's state.

Resolution is guarded alongside connection because leaving it out would
reintroduce the exact silence being fixed: `getaddrinfo` runs BEFORE the
connect on every hostname-based path, and its failure (`socket.gaierror`)
is an `OSError` -- so the Ollama transport ladder would map it to
`OllamaUnavailable`, the broad handlers would degrade, and a suite pointed
at a hostname rather than the loopback default would do real DNS with
nothing to show for it.

SCOPE, stated precisely: the guard is function-scoped, so it covers
IN-TEST access only. Module import and collection run before any fixture,
which is why `tests/unit/llm/test_ollama_embed_norm.py`'s reachability
probe -- evaluated at import time by a `skipif` -- reaches the network on
every collection regardless of this guard. That is pre-existing and
deliberate; it is named here so nobody reads "the unit suite cannot reach
the network" more broadly than it holds.

The guard is a BACKSTOP, not the fix. Stubbing the seam is the fix
(`_offline_ollama_by_default` below); the guard exists so a seam nobody
stubbed yet cannot hide. Both are needed: patching only the seam leaves the
next new network call site free to reintroduce the problem, and guarding
only the socket would turn every such call site into a hard failure instead
of deterministic offline behavior.

Deliberately-live tests opt out with `@pytest.mark.live_backend`, which
lifts BOTH fixtures -- the socket guard and the seam stub. Lifting only the
guard would be a trap: sockets would open while the injected client still
refused to reach anything, so a marked CLI test would silently assert the
degraded path against a server it never contacted. Exactly one module uses
the marker today: `tests/unit/llm/test_ollama_embed_norm.py`, whose two
tests pin empirical properties of the real embedding backend and already
gate themselves on a reachability probe. They are assumption pins, not unit
tests, and blanket-banning sockets would break them for a reason unrelated
to what they measure.
"""

import socket
import sqlite3
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from openkos.llm.base import EMBED_DIM
from openkos.llm.ollama import InstalledModel, OllamaClient, OllamaUnavailable


class UnitSuiteNetworkAccessError(RuntimeError):
    """Raised when a `tests/unit/**` test attempts to resolve or connect.

    Deliberately NOT an `OSError` subclass. Every Ollama call site wraps its
    transport in `except OSError`-family or broad `except Exception` handlers
    to degrade gracefully, so an `OSError` here would be swallowed and the
    test would keep passing through a fallback path -- exactly the silent
    behavior the guard exists to end.

    A bare `except Exception` still absorbs it, so the guard is NOT loud at
    call sites that already degrade broadly -- which is most of them. That is
    accepted rather than solved: raising `BaseException` would make the
    refusal unabsorbable but would also sail past the same broad handlers
    that production relies on for genuine offline operation, turning a
    graceful-degradation path into a crash and making the guard's presence
    change behavior rather than merely remove nondeterminism. The loud signal
    for those call sites is the seam stub plus
    `test_offline_stub_covers_every_network_method`; this error's job is to
    stop the packet, not to narrate.
    """


BLOCKED_SOCKET_METHODS = ("connect", "connect_ex")
"""`socket.socket` methods that initiate a connection.

`bind`/`listen` are deliberately absent -- a test that talks to itself is
not reaching a real service. Note this does mean a loopback `connect` is
also refused; no test in the suite stands up a local server today, and the
refusal message points at the seam rather than at this nuance, so a future
loopback-server test will need to opt out explicitly.
"""

BLOCKED_SOCKET_FUNCTIONS = ("getaddrinfo", "gethostbyname")
"""Module-level `socket` helpers that perform name resolution.

Guarded because resolution precedes connection and its native failure type
is an `OSError`, which the production degradation handlers absorb -- see the
module docstring.
"""

GUARD_ATTRIBUTE = "_openkos_unit_suite_network_guard"
"""Marker attribute set on every replacement the guard installs.

Lets `tests/unit/test_network_guard.py` assert whether the guard is installed
WITHOUT opening a socket. Checking "is the guard active" by attempting a real
connection would make the guard's own tests depend on host networking and on
which errno a given platform returns for an unroutable address -- the class
of fragility this whole change exists to remove.
"""


def _wants_live_backend(request: pytest.FixtureRequest) -> bool:
    """Whether this test carries `@pytest.mark.live_backend`.

    Shared by BOTH autouse fixtures so the opt-out can never drift into
    covering one and not the other -- the asymmetry that would let a marked
    CLI test open sockets while still talking to an injected offline client.
    """
    return request.node.get_closest_marker("live_backend") is not None


RESOLVER_TARGET_KEYWORDS = {
    "getaddrinfo": "host",
    "gethostbyname": "hostname",
}
"""Per-surface keyword naming the resolution target.

Keyed by surface because the two resolvers DISAGREE: `getaddrinfo(host,
port, ...)` against `gethostbyname(hostname)`. A single shared keyword would
cover whichever surface it was written for and silently report `''` for the
other -- the same empty-target defect this fallback exists to remove, just
moved to the surface nobody checked.

Only the unbound convention consults this. The bound family
(`socket.socket.connect`/`connect_ex`) takes an address TUPLE rather than a
named host, so there is no keyword to fall back to.

Worth stating because it is counter-intuitive: unpatched, both resolvers are
C callables that reject keywords outright, so none of these shapes can occur
in production. The guard REPLACES them with a plain Python `*args, **kwargs`
function, which accepts keywords -- so the guard itself widens the calling
convention of the surface it guards, and the refusal message has to handle
the wider one.
"""


def _refusal(
    request: pytest.FixtureRequest, surface: str, *, bound: bool
) -> Callable[..., Any]:
    """Build a tagged replacement that refuses `surface` and names the test.

    `bound` selects the calling convention, and it is REQUIRED rather than
    inferred because the two guarded families disagree about what their first
    positional argument means. A replacement installed on the `socket.socket`
    class is called as `sock.connect(address)`, arriving as
    `(sock, address)`; one installed as a module-level function is called as
    `socket.getaddrinfo(host, port, ...)`, arriving as `(host, port, ...)`.

    Guessing from arity instead -- "if there are 2+ args, the second is the
    target" -- looks equivalent and is not: `getaddrinfo(host, port)` would
    then report the PORT and drop the hostname, which is the single datum the
    resolution guard exists to surface. Passing the convention makes the
    distinction explicit at both call sites and impossible to get wrong by
    accident.

    The target may also arrive with NO positional slot at all --
    `getaddrinfo(host=...)` and `gethostbyname(hostname=...)` are both legal
    against the replacement -- so the unbound convention falls back to this
    surface's entry in `RESOLVER_TARGET_KEYWORDS` before giving up. Without
    it the message degrades to `attempted socket.getaddrinfo('')`, which
    names a test that reached the network but not where it reached.
    Fail-closed behavior never depended on this: the raise happens either
    way, and this is observability only.
    """
    keyword = None if bound else RESOLVER_TARGET_KEYWORDS.get(surface)

    def _raiser(*args: Any, **kwargs: Any) -> Any:
        positional = args[1:] if bound else args
        if positional:
            target = positional[0]
        elif keyword is not None:
            target = kwargs.get(keyword, "")
        else:
            target = ""
        raise UnitSuiteNetworkAccessError(
            f"{request.node.nodeid} attempted socket.{surface}({target!r}). "
            "Unit tests must not reach a real service -- stub the seam "
            "(e.g. `openkos.cli.main.OllamaClient`) instead. If this test "
            "genuinely needs a live backend, mark it "
            "`@pytest.mark.live_backend`. See #217."
        )

    setattr(_raiser, GUARD_ATTRIBUTE, True)
    return _raiser


@pytest.fixture(autouse=True)
def _no_network_by_default(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed on outbound network for every `tests/unit/**` test.

    Patches the connecting `socket.socket` methods and the module-level
    name-resolution helpers -- see the module docstring for why resolution is
    included and for the precise (in-test only) scope.

    Skipped for `@pytest.mark.live_backend` tests.
    """
    if _wants_live_backend(request):
        return

    for method in BLOCKED_SOCKET_METHODS:
        monkeypatch.setattr(
            socket.socket, method, _refusal(request, method, bound=True)
        )
    for function in BLOCKED_SOCKET_FUNCTIONS:
        monkeypatch.setattr(socket, function, _refusal(request, function, bound=False))


class OfflineOllama(OllamaClient):
    """A real `OllamaClient` with EVERY network method stubbed.

    SUBCLASSES rather than replaces, because several tests legitimately
    assert the CLI constructs a genuine `OllamaClient` from the configured
    model (`isinstance(..., OllamaClient)` plus host/model resolution). A
    structural stand-in would break that real contract; this keeps every
    constructor behavior intact and removes only the network.

    "Every" is not a promise on trust:
    `test_offline_stub_covers_every_network_method` derives the client's
    network methods from its source and fails if this class misses one. That
    test exists because the bug #217 fixed was precisely a MISSING override
    -- the earlier stub covered `chat` and `embed` but not `list_models`, so
    the installed-models probe, `init`'s preflight and `doctor` each made a
    real HTTP request in every test that ran them. Nothing detected it: the
    suite stayed green while most of it quietly depended on whether the
    developer happened to have a server running.

    `chat` declines extraction and `embed` returns a fixed unit vector --
    deliberately uninteresting, so no test accidentally depends on these
    values.

    `list_models` RAISES `OllamaUnavailable` rather than returning a
    populated list, reproducing the no-server state that CI already validates
    green: behavior is therefore unchanged for the tests this newly covers,
    since each probe call site already wraps it and degrades. A populated
    list would instead flip `doctor`'s reachability output and break the
    tests that assert the unreachable path.
    """

    def chat(self, messages: Sequence[object]) -> str:
        return '{"extract": false}'

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * (EMBED_DIM - 1) for _ in texts]

    def list_models(self) -> list[InstalledModel]:
        raise OllamaUnavailable(
            "offline test default: no Ollama server (tests/unit/conftest.py)"
        )


@pytest.fixture(autouse=True)
def _offline_ollama_by_default(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch the CLI's Ollama seam so no unit test reaches the network by
    accident. Same constructor signature, so call sites are unaffected.

    Every `OllamaClient(...)` construction site in `cli/main.py` resolves the
    class through this one module global, so patching the single name covers
    all of them.

    Skipped for `@pytest.mark.live_backend` tests, in lockstep with the socket
    guard: a marked test that got sockets back but kept this stub would reach
    an injected client that refuses to talk, and would then assert the
    degraded path while believing it had contacted a live server.

    Lives at `tests/unit/` rather than `tests/unit/cli/` (where it started,
    #183) because `ingest --auto` is also driven from outside the CLI test
    package -- `tests/unit/vcs/conftest.py`'s `tmp_git_repo` fixture and
    `tests/unit/retrieval/test_answer.py` -- and those were reaching the
    network for real.

    Modules needing specific behavior override it: `test_ingest.py` has its
    own autouse fixture, and individual tests patch the same seam directly --
    both take precedence, since module-level and function-level fixtures
    resolve after this one.

    One consequence worth naming rather than discovering later:
    `test_ingest.py` overrides this with `_FakeLLM`, which serves `chat()`
    but NOT `embed()`. Its tests therefore reach `_embed_after_ingest`, raise
    `AttributeError` inside `reindex`, and degrade through the broad guard to
    a stderr notice with an unchanged exit code. That is the fail-open
    contract working, not a fixture gap -- those tests assert on extraction,
    not embedding, and the ones that do care use `_EmbeddingLLM`. It does
    mean their stderr carries an extra embedding notice, so a strict
    full-stderr equality assertion added there would fail for a reason that
    has nothing to do with what it is testing.
    """
    if _wants_live_backend(request):
        return
    monkeypatch.setattr("openkos.cli.main.OllamaClient", OfflineOllama)


def make_locked_error(
    message: str = "database is locked",
    *,
    errorcode: int = sqlite3.SQLITE_BUSY,
) -> sqlite3.OperationalError:
    """Build an `OperationalError` classified as lock contention by
    `state.derived.is_lock_contention` -- `errorcode` defaults to
    `SQLITE_BUSY` (database-level lock); pass `sqlite3.SQLITE_LOCKED` for
    the table-level variant."""
    exc = sqlite3.OperationalError(message)
    exc.sqlite_errorcode = errorcode
    return exc


def make_non_lock_operational_error(
    message: str = "no such module: fts5",
) -> sqlite3.OperationalError:
    """Build an `OperationalError` NOT classified as lock contention (e.g.
    a genuine fts5-module-absent failure) -- `errorcode` is `SQLITE_ERROR`,
    proving `is_lock_contention`-based discrimination never mislabels a
    non-lock operational failure as lock contention."""
    exc = sqlite3.OperationalError(message)
    exc.sqlite_errorcode = sqlite3.SQLITE_ERROR
    return exc
