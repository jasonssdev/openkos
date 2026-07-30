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
   AND the test that noticed, leaving the suite green.

   The constants are still read here -- by `_guarded_surfaces()`, and
   therefore by both install/opt-out pins -- but every one of those checks is
   a count or an all-quantified predicate, so all of them pass trivially if
   the constants are emptied. `test_declared_surfaces_match_the_ones_tested_literally`
   is the assertion that does not, which is why it is expressed as literals.

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
   caused by a stub that covered every network method but one. That test
   derives the set from the client's own source, so the same omission cannot
   recur silently -- and it checks the derivation both ways, so the derivation
   itself cannot silently narrow.
"""

import inspect
import socket
from collections.abc import Iterator, Sequence
from typing import ClassVar

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


def test_declared_surfaces_match_the_ones_tested_literally() -> None:
    """The declarations must name exactly the surfaces tested literally above.

    This is the anti-vacuity pin for the declared set. Exactly two things in
    this module read those constants: this test, and `_guarded_surfaces()` --
    which reaches the install and opt-out pins only as a COUNT
    (`_tagged_count() == len(_guarded_surfaces())`, `_tagged_count() == 0`).
    Empty both constants and every count collapses to `0 == 0` and passes
    while the guard protects nothing, so this is the one check over the
    declared set that still fails.

    Other tests here would also notice, but only by attempting real network
    and getting the wrong exception -- which is the failure mode this module
    exists to prevent, not a signal to rely on. That is what makes a check
    over the declaration itself worth having.

    Pinned against literals rather than derived from the constants, for the
    reason stated in the module docstring: a check driven by the thing it
    checks disappears together with it.

    It also catches the GROWN case -- adding a surface to a constant without
    adding a literal call test for it fails here, naming the omission,
    instead of silently widening what the module claims to cover.

    The "declared but never patched" case the previous version of this test
    aimed at is already impossible: the fixture loops over these same
    constants, and `monkeypatch.setattr` raises `AttributeError` for a name
    the target does not have, so an entry is either patched by construction
    or fails loudly at setup.
    """
    assert set(BLOCKED_SOCKET_METHODS) == {"connect", "connect_ex"}
    assert set(BLOCKED_SOCKET_FUNCTIONS) == {"getaddrinfo", "gethostbyname"}


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


def test_resolution_refusal_names_a_keyword_host() -> None:
    """`getaddrinfo(host=..., port=...)` must still name the HOST.

    `socket.getaddrinfo` is pure Python and therefore keyword-callable, so a
    raiser that reads only positional arguments reports `''` for this shape --
    a refusal that says a test reached the network but not where.

    No production path produces this shape today: the only transport is the
    stdlib HTTP stack via `socket.create_connection`, which passes host and
    port positionally. The direct resolver calls that do exist in this
    repository are this module's own guard tests, a few lines above and
    below. Pinned anyway because the guard's whole job is to be legible on
    the day production code does reach a resolver, and observability that is
    never exercised is observability nobody notices has broken.

    The residual half of the convention bug fixed in #263: `bound` fixed WHICH
    positional slot is read, not the case where there is no positional slot.
    """
    with pytest.raises(UnitSuiteNetworkAccessError) as excinfo:
        socket.getaddrinfo(host=_HOSTNAME, port=11434)

    message = str(excinfo.value)
    assert _HOSTNAME in message
    assert "11434" not in message


def test_gethostbyname_refusal_names_a_keyword_hostname() -> None:
    """`gethostbyname(hostname=...)` must name the host too.

    The two guarded resolvers agree on neither the keyword's NAME nor whether
    a keyword call is reachable unpatched. `RESOLVER_TARGET_KEYWORDS` in
    `conftest.py` owns that statement; this test is the executable half of it
    for `gethostbyname`, and `test_resolution_refusal_names_a_keyword_host`
    is the other half.

    The `type: ignore` below is the proof, not a workaround: mypy resolves
    `gethostbyname` to its positional-only C signature in `_socket` and
    rejects the keyword, which is precisely the static fact that makes this
    shape impossible in production and possible only against the guard's
    replacement.
    """
    with pytest.raises(UnitSuiteNetworkAccessError) as excinfo:
        socket.gethostbyname(hostname=_HOSTNAME)  # type: ignore[call-arg]

    assert _HOSTNAME in str(excinfo.value)


def test_stub_coverage_reads_public_callables_only() -> None:
    """The stub-coverage check must ignore private helpers and constants.

    `test_offline_stub_covers_every_network_method` compares two sets that
    must be drawn from the SAME namespace. Its `network_methods` side is
    filtered to public names; if the `overridden` side is not filtered
    identically, any legitimate addition to the stub that is not a derived
    public network method lands in `undetected` and fails the run -- with a
    message blaming the derivation for drifting, sending the reader to inspect
    a client transport that never changed.

    Not hypothetical shapes: a shared fixed-vector helper behind `embed`, a
    class-level constant so a test can reference the stub's canned value, or
    making the stub inherit a Protocol/ABC -- `ABCMeta` writes `_abc_impl`
    into the subclass `__dict__`, and the client being a plain class today is
    the only reason that does not already fire.

    Pinned on a deliberately polluted subclass rather than on `OfflineOllama`
    itself, so the invariant holds regardless of what the real stub happens to
    contain right now.

    The stub carries one member per branch of the filter, so neither half can
    rot unnoticed. `PUBLIC_VECTOR` is the one that earns the `callable` check:
    a private constant is already excluded by the underscore rule, so a stub
    polluted only with private members would leave `callable` unproved and let
    it be deleted with every assertion still green. `chat` pins the positive
    direction -- a public callable override must still be COUNTED, or the
    filter would exclude the very methods the coverage check exists to compare.
    """

    class _PollutedStub(OfflineOllama):
        PUBLIC_VECTOR: ClassVar[list[float]] = [1.0, 0.0]
        _FIXED: ClassVar[list[float]] = [1.0, 0.0]

        def _vector(self) -> list[float]:
            return list(self._FIXED)

        def chat(self, messages: Sequence[object]) -> str:
            return '{"extract": false}'

    assert _overridden_network_overrides(_PollutedStub) == {"chat"}


def test_live_backend_marker_is_registered(pytestconfig: pytest.Config) -> None:
    """The opt-out marker must be REGISTERED, not merely spelled.

    The whole escape hatch is a marker name compared as a string in
    `_wants_live_backend`. Under `--strict-markers` an unregistered name is a
    collection error rather than a warning, so this test and that flag protect
    different halves: the flag catches a typo at the USE site, this catches the
    registration being dropped from `pyproject.toml` while uses remain.

    Reads the collected registry rather than the file, so it measures what
    pytest actually loaded.
    """
    registered = {
        entry.split(":", 1)[0].strip() for entry in pytestconfig.getini("markers")
    }

    assert "live_backend" in registered


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


def _overridden_network_overrides(stub: type) -> set[str]:
    """The stub's own PUBLIC CALLABLE overrides -- the comparable half.

    Filtered to public callables so it is drawn from the same namespace as the
    derived `network_methods` set below, which is itself public-only. A looser
    rule (everything non-dunder) would sweep in private helpers, class
    constants, and machinery a base class writes into `__dict__` -- `ABCMeta`
    contributes `_abc_impl` -- and each would then read as a network method the
    derivation failed to classify.

    Takes the class as a parameter so the invariant can be pinned against a
    deliberately polluted subclass rather than against whatever `OfflineOllama`
    happens to contain today.
    """
    return {
        name
        for name, member in vars(stub).items()
        if not name.startswith("_") and callable(member)
    }


def test_offline_stub_covers_every_network_method() -> None:
    """The offline stub must override EVERY network method of the real client.

    This is the regression test for #217's actual root cause. The previous
    stub overrode `chat` and `embed` but not `list_models`, so every test
    that ran a model probe made a real HTTP request -- and because the probe
    call sites degrade through broad handlers, the suite stayed green and
    said nothing.

    Derives the expected set from the client's own source instead of
    hardcoding names, so ADDING a network method to the client fails here
    until the stub covers it. A hardcoded list would have to be remembered,
    which is exactly what went wrong the first time.

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

    overridden = _overridden_network_overrides(OfflineOllama)

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
