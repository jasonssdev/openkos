"""Unit tests for `classify_embed_host`: the pure, never-raising locality
check behind the non-local embedding-host advisory (issue #199).

The withdrawn #183-PR3 predecessor is the specification of the traps, and
every one of them is pinned here:

1. `urlsplit`-style `ValueError: Invalid IPv6 URL` on an unmatched bracket
   (`[::1:11434`, a plausible typo) -- the helper runs inside paths
   contracted to degrade, so it must NEVER raise: it degrades to
   "non-local" (over-warning is the accepted failure direction).
2. A bracket-less IPv6 literal (`fe80::1234:5678`) must NOT be split at the
   first colon into a host nobody configured (`fe80`); it classifies as a
   whole-value host.
3. Userinfo is ALWAYS redacted from `display_host` before any caller can
   print it -- including (especially) on the unparseable path, where the
   predecessor echoed a plaintext password.
4. `localhost.` (single trailing root dot) and uppercase forms resolve
   identically to `localhost` and count as local.

Locality is a LITERAL check -- no DNS resolution (explicitly rejected in
the issue: it adds a lookup to every ingest and can hang). Local means
loopback by literal form only: `localhost` (any case, one optional trailing
dot), `127.0.0.0/8` literals, and `::1` (bracketed or not). Anything else
-- link-local IPv6, hostnames, unparseable garbage -- is non-local.
"""

import pytest

from openkos.llm.ollama import EmbedHostLocality, classify_embed_host


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "localhost",
        "LOCALHOST",
        "localhost.",
        "LocalHost.",
        "localhost:11434",
        "localhost.:11434",
        "http://localhost:11434",
        "https://localhost:11434",
        "http://Localhost.:11434",
        "127.0.0.1",
        "127.0.0.1:11434",
        "http://127.0.0.1:11434",
        "127.255.255.254",
        "127.0.0.1.",
        "::1",
        "[::1]",
        "[::1]:11434",
        "http://[::1]:11434",
        "http://::1",
        ":11434",
    ],
)
def test_local_forms_classify_as_local(raw: str | None) -> None:
    """Every literal loopback form is local: `localhost` (any case, one
    optional trailing root dot), `127.0.0.0/8` literals, `::1` bracketed or
    not, with or without scheme/port. An empty/unset value means the
    default local host, and a port-only value overrides only the port --
    both local, no warning."""
    assert classify_embed_host(raw).is_local is True


@pytest.mark.parametrize(
    "raw",
    [
        "example.com",
        "example.com:11434",
        "http://example.com:11434",
        "https://example.com:11434",
        "128.0.0.1",
        "1270.0.0.1",
        "127.0.0.256",
        "127.0.0",
        "12.7.0.1",
        "fe80::1234:5678",
        "[fe80::1]:11434",
        "[::1:11434",
        "http://[::1:11434",
        "localhost..",
        "0:0:0:0:0:0:0:1",
        "localhost.example.com",
    ],
)
def test_nonlocal_forms_classify_as_nonlocal(raw: str) -> None:
    """Anything that is not a literal loopback form is non-local: real
    hostnames, near-miss IPv4 (`128.0.0.1`, out-of-range or short octets),
    link-local IPv6, unparseable values (unmatched bracket), a double
    trailing dot (only ONE root dot is normalized), and the expanded-zeros
    IPv6 loopback spelling (LITERAL check, not address equivalence --
    over-warning is the accepted direction)."""
    assert classify_embed_host(raw).is_local is False


def test_unmatched_bracket_never_raises() -> None:
    """Trap 1 (CRITICAL in the withdrawn predecessor): `urlsplit` raises
    `ValueError: Invalid IPv6 URL` on `[::1:11434`. The helper runs after
    ingest has already committed, inside a degrade-contracted path -- it
    must classify (as non-local), never raise."""
    result = classify_embed_host("[::1:11434")
    assert result == EmbedHostLocality(is_local=False, display_host="[::1:11434")


def test_bracketless_ipv6_is_whole_value_host_not_first_colon_split() -> None:
    """Trap 2: `urlsplit` partitions a bracket-less netloc at the FIRST
    colon, so `fe80::1234:5678` parses as host `fe80` -- a host nobody
    configured. The whole literal is the host, and the whole literal is
    what the warning names."""
    result = classify_embed_host("fe80::1234:5678")
    assert result.is_local is False
    assert result.display_host == "fe80::1234:5678"


@pytest.mark.parametrize(
    ("raw", "expected_display"),
    [
        ("http://user:s3cret@example.com:11434", "example.com:11434"),
        ("user:s3cret@example.com:11434", "example.com:11434"),
        ("user:s3cret@[::1", "[::1"),
        ("http://user:s3cret@[::1", "[::1"),
        ("alice@bob@remote.example:11434", "remote.example:11434"),
    ],
)
def test_userinfo_is_always_redacted_from_display(
    raw: str, expected_display: str
) -> None:
    """Trap 3 (CRITICAL in the withdrawn predecessor): credentials fell
    through the error path and were echoed raw -- a plaintext password in
    logs. Userinfo is redacted on EVERY path, parseable or not, and with
    multiple `@` the LAST one delimits the userinfo (urlsplit's own rule,
    pinned here as the implemented behavior)."""
    result = classify_embed_host(raw)
    assert result.display_host == expected_display
    assert "s3cret" not in result.display_host
    assert "alice" not in result.display_host


@pytest.mark.parametrize(
    ("raw", "expected_display"),
    [
        ("user:s3cret/x@host:11434", "host:11434"),
        ("user:s3cret?x@host:11434", "host:11434"),
        ("user:s3cret#x@host:11434", "host:11434"),
        ("http://user:s3cret/x@host:11434", "host:11434"),
        ("remote.example/x@localhost", "localhost"),
    ],
)
def test_separator_inside_userinfo_never_leaks_and_warns(
    raw: str, expected_display: str
) -> None:
    """Review finding R1-userinfo-redaction-bypass: a reserved separator
    (`/`, `?`, `#`) smuggled into userinfo ahead of the `@` must not let the
    authority cut discard the `@host` remainder and hand the credential to
    `display_host`. Such a value is malformed, so it classifies non-local
    outright (over-warning is the accepted direction) and the display comes
    from the redacted remainder -- never the credential fragment."""
    result = classify_embed_host(raw)
    assert result.is_local is False
    assert result.display_host == expected_display
    assert "s3cret" not in result.display_host
    assert "remote.example/x" not in result.display_host


@pytest.mark.parametrize(
    ("raw", "expected_display"),
    [
        ("user:s3cret", "user"),
        ("localhost:s3cret", "localhost"),
        ("[fe80::1]:s3cret", "[fe80::1]"),
        ("host:", "host"),
    ],
)
def test_non_numeric_port_is_never_displayed(raw: str, expected_display: str) -> None:
    """Same finding, port position: a non-numeric "port" is not a port --
    it can be a credential pasted without a host (`user:s3cret` bare). The
    display never includes it; only the host part survives."""
    result = classify_embed_host(raw)
    assert result.display_host == expected_display
    assert "s3cret" not in result.display_host


def test_userinfo_on_local_host_stays_local_and_redacted() -> None:
    """Userinfo never flips locality, and the display is redacted even for
    a local host (no caller prints it, but the invariant is unconditional)."""
    result = classify_embed_host("http://user:s3cret@localhost:11434")
    assert result.is_local is True
    assert "s3cret" not in result.display_host
    assert "user" not in result.display_host


def test_display_host_strips_surrounding_whitespace() -> None:
    """A padded env value classifies and displays on its stripped form."""
    result = classify_embed_host("  remote.example:11434  ")
    assert result.is_local is False
    assert result.display_host == "remote.example:11434"


@pytest.mark.parametrize(
    "raw",
    [
        "[::1:11434",
        "http://[",
        "[",
        "]",
        "[]",
        "user:pass@[::1",
        "http://",
        "://",
        "@",
        ":",
        "@@@",
        "http://user@",
        "%",
        "  [fe80::  ",
        "a:b:c:d",
        "127.0.0.1:port:extra",
    ],
)
def test_never_raises_on_hostile_input(raw: str) -> None:
    """The helper is contracted to degrade, never raise, on ANY input --
    it runs inside fail-open paths after ingest has already committed."""
    result = classify_embed_host(raw)
    assert isinstance(result, EmbedHostLocality)
    assert isinstance(result.is_local, bool)
    assert isinstance(result.display_host, str)
    assert "pass" not in result.display_host
