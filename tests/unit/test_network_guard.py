"""Tests for the unit suite's fail-closed network guard (#217).

A guard nobody tests is a guard that can quietly stop working: rename the
fixture, drop the `autouse=True`, patch the wrong attribute, and the suite
goes back to reaching a real Ollama with nothing to say about it. These
tests are the guard's own alarm.

Most tests here assert the guard's OBSERVABLE behavior -- that a connect or a
resolution raises, what type it raises, and what the message names -- rather
than how the fixture patches, so it stays free to change mechanism as long as
it still blocks.

Three deliberate design choices are worth knowing before editing:

1. Each guarded surface is named as a LITERAL call (`connect`, `connect_ex`,
   `getaddrinfo`, `gethostbyname`) instead of being parametrized over the
   guard's own constants. Driving a test from `BLOCKED_SOCKET_FUNCTIONS` reads
   tidier but is self-defeating: deleting an entry would remove the protection
   AND the test that noticed, leaving the suite green. The constants are still
   used, but only by `test_every_declared_surface_is_actually_guarded`, which
   catches the opposite mistake -- a surface declared as guarded that the
   fixture never patches.

2. The install checks (`test_guard_is_installed_for_an_ordinary_unit_test`,
   `test_offline_seam_is_installed_for_an_ordinary_unit_test`, and their
   `live_backend` opposites) read a marker attribute or an identity rather
   than attempting a connection. Deciding "is the guard on?" by watching a
   real socket fail would make them depend on host networking and on which
   errno the platform returns -- precisely the fragility this change removes
   everywhere else. Both directions are pinned in each pair, because a
   lifted-by-marker test alone cannot distinguish "the marker lifted it" from
   "it was never installed".

3. `test_offline_stub_covers_every_network_method` guards the OTHER half. The
   socket guard is a backstop; stubbing the seam is the fix, and #217 was
   caused by a stub that covered two of three network methods. That test
   derives the set from the client's own source, so the same omission cannot
   recur silently -- and it checks the derivation both ways, so the derivation
   itself cannot silently narrow.
"""

import inspect
import socket
from collections.abc import Iterator

import pytest

from openkos.llm.ollama import OllamaClient
from tests.unit.conftest import (
    BLOCKED_SOCKET_FUNCTIONS,
    BLOCKED_SOCKET_METHODS,
    GUARD_ATTRIBUTE,
    OfflineOllama,
    UnitSuiteNetworkAccessError,
)

_UNROUTABLE = ("192.0.2.1", 11434)
"""TEST-NET-1 (RFC 5737): reserved for documentation and never routed.

Chosen so that a regression which disables the guard cannot accidentally
succeed against something real. Paired with `_CONNECT_TIMEOUT` below,
because unroutable does not mean fast -- a black-holed address fails by
exhausting SYN retries, which is 75s+ per attempt on darwin and longer on
Linux.
"""

_CONNECT_TIMEOUT = 0.01
"""Bound on how long a guard REGRESSION may take to surface.

With the guard installed this is never consulted: the raiser replaces
`connect` before any I/O happens, so these tests are instant. It matters
only in the failure mode these tests exist to detect -- without it, a
dropped guard turns each connect test into a multi-minute stall and the
suite reads as hung rather than red.
"""


_HOSTNAME = "ollama.invalid"
"""A name in the reserved `.invalid` TLD (RFC 2606): guaranteed not to resolve."""


@pytest.fixture
def short_timeout_socket() -> Iterator[socket.socket]:
    """A closing TCP socket with a short connect timeout.

    Named for what it PROVIDES, not for refusal: refusal is ambient, installed
    by the autouse guard in `tests/unit/conftest.py`, and this fixture does no
    patching at all. The timeout it sets matters only when the guard is ABSENT
    -- see `_CONNECT_TIMEOUT`.

    A fixture rather than three copies of the same create/try/finally
    scaffold, so each test below is its distinguishing call plus its
    assertion.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(_CONNECT_TIMEOUT)
    try:
        yield sock
    finally:
        sock.close()


def test_outbound_connect_is_refused(short_timeout_socket: socket.socket) -> None:
    """`socket.connect` raises instead of reaching the network.

    Calls `connect` LITERALLY rather than driving it from
    `BLOCKED_SOCKET_METHODS`, so removing that entry from the guard breaks this
    test. A test parametrized over the guard's own constant would vanish
    together with the protection it checks.
    """
    with pytest.raises(UnitSuiteNetworkAccessError):
        short_timeout_socket.connect(_UNROUTABLE)


def test_outbound_connect_ex_is_refused(short_timeout_socket: socket.socket) -> None:
    """`connect_ex` is blocked too, not just `connect`.

    `connect_ex` reports failure as a returned errno rather than by raising,
    so a guard covering only `connect` would let it through AND leave the
    caller reading the result as an ordinary connection refusal -- silent by
    construction.
    """
    with pytest.raises(UnitSuiteNetworkAccessError):
        short_timeout_socket.connect_ex(_UNROUTABLE)


def test_getaddrinfo_is_refused() -> None:
    """Resolution is blocked, not merely the connection that follows it.

    `getaddrinfo` runs BEFORE `connect` on every hostname-based path, so a
    guard that only blocked connecting would still emit real DNS queries --
    leaking hostnames and inheriting the host resolver's latency. Worse, the
    native failure (`socket.gaierror`) is an `OSError`, so the Ollama
    transport ladder would map it to `OllamaUnavailable` and the broad
    degradation handlers would absorb it: green suite, real network, nothing
    reported.

    Named literally, for the same reason as the connect tests above.
    """
    with pytest.raises(UnitSuiteNetworkAccessError):
        socket.getaddrinfo(_HOSTNAME, 11434)


def test_gethostbyname_is_refused() -> None:
    """The older resolution entry point is blocked too.

    Named literally so dropping it from the guard fails here.
    """
    with pytest.raises(UnitSuiteNetworkAccessError):
        socket.gethostbyname(_HOSTNAME)


def test_every_declared_surface_is_actually_guarded() -> None:
    """No entry in either constant may be declared but left unpatched.

    Complements the literal tests above rather than replacing them: those
    catch a SHRUNK constant (protection silently removed), this catches a
    GROWN one (a surface listed as guarded that the fixture never patches).
    Neither alone is sufficient.
    """
    unguarded = [
        name
        for name, surface in (
            [(n, getattr(socket.socket, n)) for n in BLOCKED_SOCKET_METHODS]
            + [(n, getattr(socket, n)) for n in BLOCKED_SOCKET_FUNCTIONS]
        )
        if not getattr(surface, GUARD_ATTRIBUTE, False)
    ]
    assert not unguarded, f"declared as guarded but left unpatched: {unguarded}"


def test_resolution_refusal_names_the_host_not_the_port() -> None:
    """`getaddrinfo(host, port)` must report the HOST.

    The module-level resolvers take `(host, port, ...)` with no `self`, while
    the patched socket methods take `(sock, address)`. A single positional
    index cannot serve both: reading "the second argument" would report the
    port here and drop the hostname -- the one datum that distinguishes an
    Ollama lookup from an unexpected third-party endpoint, and the whole
    reason resolution is guarded.

    Passes two positional arguments deliberately. That is the shape real
    transport code uses (`socket.create_connection` calls
    `getaddrinfo(host, port, 0, SOCK_STREAM)`); a one-argument call would pass
    even with the convention confused, so it would prove nothing.
    """
    with pytest.raises(UnitSuiteNetworkAccessError) as excinfo:
        socket.getaddrinfo(_HOSTNAME, 11434)

    message = str(excinfo.value)
    assert _HOSTNAME in message
    assert "11434" not in message


def test_refusal_is_not_an_oserror() -> None:
    """The guard's error must NOT be an `OSError`.

    Every Ollama call site degrades gracefully on transport failure, and the
    client's transport ladder maps `OSError` into `OllamaUnavailable`. An
    `OSError` here would therefore be absorbed into a normal fallback and the
    offending test would keep passing -- the exact silence this guard exists
    to break. Pinned as a test because the reasoning is invisible at the
    `raise` site.
    """
    assert not issubclass(UnitSuiteNetworkAccessError, OSError)


def test_refusal_message_names_the_test_and_the_address(
    request: pytest.FixtureRequest, short_timeout_socket: socket.socket
) -> None:
    """The message must identify WHO reached out and WHERE.

    Without both, a full-suite failure tells you the suite touched the
    network but not which test to fix -- and the address is what reveals
    whether it was Ollama or something unexpected.

    Uses `request.node.name` rather than this function's name as a literal:
    a literal would silently stop matching on a rename, and the failure would
    read as "the guard does not name the test" instead of "this assertion is
    stale".
    """
    with pytest.raises(UnitSuiteNetworkAccessError) as excinfo:
        short_timeout_socket.connect(_UNROUTABLE)

    message = str(excinfo.value)
    assert request.node.name in message
    assert _UNROUTABLE[0] in message
    assert str(_UNROUTABLE[1]) in message


def _guarded_surfaces() -> list[object]:
    """Every replacement the guard is expected to have installed."""
    return [getattr(socket.socket, name) for name in BLOCKED_SOCKET_METHODS] + [
        getattr(socket, name) for name in BLOCKED_SOCKET_FUNCTIONS
    ]


def _tagged_count() -> int:
    """How many guarded surfaces currently carry the guard tag."""
    return sum(
        1 for surface in _guarded_surfaces() if getattr(surface, GUARD_ATTRIBUTE, False)
    )


def test_guard_is_installed_for_an_ordinary_unit_test() -> None:
    """The guard is active by DEFAULT, with no marker and no opt-in.

    The companion to the opt-out test below: together they pin both
    directions, so a fixture that silently stopped applying (a rename, a
    dropped `autouse=True`) fails here rather than going unnoticed until the
    next flaky run.
    """
    assert _tagged_count() == len(_guarded_surfaces())


@pytest.mark.live_backend
def test_live_backend_marker_opts_out_of_the_guard() -> None:
    """`@pytest.mark.live_backend` leaves EVERY real surface in place.

    Asserts ZERO tagged surfaces, not merely "fewer than all". The negation
    of "all installed" would also be satisfied by a half-removed guard, so a
    future per-surface opt-out that got one branch wrong would keep this test
    green while still blocking the two assumption pins in
    `tests/unit/llm/test_ollama_embed_norm.py` -- the precise outcome the
    marker exists to prevent.
    """
    assert _tagged_count() == 0


def _cli_ollama_seam() -> object:
    """The class the CLI module will construct clients from, read dynamically.

    `getattr` rather than attribute access: the seam is a name the CLI module
    imports for its own use, not part of its public API, so mypy rightly
    refuses the direct form. Reading it dynamically is also exactly what the
    fixture does when it patches.
    """
    from openkos.cli import main

    return getattr(main, "OllamaClient")  # noqa: B009


def test_offline_seam_is_installed_for_an_ordinary_unit_test() -> None:
    """The seam stub is active by DEFAULT.

    This is the direction that actually protects the suite, and it was the
    missing half: without it, a rename or a dropped `autouse=True` on the seam
    fixture trips nothing, since the marker test below passes either way (it
    cannot distinguish "the marker lifted the stub" from "the stub was never
    installed"). In CI, with no server, the regression is silent in exactly the
    way #217 was -- the real `list_models` raises a transport-derived
    `OllamaUnavailable` instead of the stub's, so observable behavior is
    identical while every probe goes back to the network.
    """
    assert _cli_ollama_seam() is OfflineOllama


@pytest.mark.live_backend
def test_live_backend_marker_also_lifts_the_offline_ollama_seam() -> None:
    """The marker lifts the seam stub too, not just the socket guard.

    Lifting only the guard is a trap rather than an escape hatch: sockets
    would open while the CLI still received an injected client that refuses
    to talk, so a marked CLI test would assert the degraded path believing it
    had reached a live server. Pinned because the two fixtures are separate
    and could easily drift apart.
    """
    seam = _cli_ollama_seam()

    assert seam is OllamaClient
    assert seam is not OfflineOllama


def test_offline_stub_covers_every_network_method() -> None:
    """The offline stub must override EVERY network method of the real client.

    This is the regression test for #217's actual root cause. The previous
    stub overrode `chat` and `embed` but not `list_models`, so every test
    that ran a model probe made a real HTTP request -- and because the probe
    call sites degrade through broad handlers, the suite stayed green and
    said nothing.

    Derives the expected set from the client's own source instead of
    hardcoding names, so ADDING a fourth network method to the client fails
    here until the stub covers it. A hardcoded list would have to be
    remembered, which is exactly what went wrong the first time.

    The derivation is a two-level closure over `self._urlopen(` -- a CALL, so
    the constructor's `self._urlopen = urlopen` assignment is correctly
    excluded. One level is not enough: `chat` and `list_models` open the
    connection themselves, but `embed` delegates to a private helper that
    does, so a direct-callers-only rule would miss it and silently narrow
    what this test protects.
    """
    sources = {
        name: inspect.getsource(member)
        for name, member in inspect.getmembers(OllamaClient, inspect.isfunction)
    }
    direct = {name for name, src in sources.items() if "self._urlopen(" in src}
    private_direct = {name for name in direct if name.startswith("_")}
    network_methods = {name for name in direct if not name.startswith("_")} | {
        name
        for name, src in sources.items()
        if not name.startswith("_")
        and any(f"self.{helper}(" in src for helper in private_direct)
    }

    overridden = {name for name in vars(OfflineOllama) if not name.startswith("__")}

    # Non-emptiness alone is too weak a vacuity check: it still passes if the
    # derivation silently loses ONE method -- say `embed`'s transport moves a
    # level deeper, past this two-level closure -- because the other two keep
    # the set non-empty. Requiring the derivation to account for everything
    # the stub bothers to override turns that narrowing into a failure, since
    # the stub would then override a method the derivation no longer claims is
    # a network method. That is the same silent-omission class as #217, just
    # one level up in the test itself.
    undetected = overridden - network_methods
    assert not undetected, (
        f"the stub overrides {sorted(undetected)} but the derivation no longer "
        "classifies them as network methods -- the `self._urlopen(` closure has "
        "drifted (transport moved deeper than one private hop?) and this test is "
        "now protecting less than it appears to"
    )

    missing = network_methods - overridden
    assert not missing, (
        f"the offline stub does not override {sorted(missing)}, so those reach "
        "the real network in every unit test that calls them -- this is exactly "
        "the #217 defect"
    )
