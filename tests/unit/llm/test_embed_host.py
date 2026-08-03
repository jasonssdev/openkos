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
        ("remote.example/x@localhost", "remote.example"),
    ],
)
def test_separator_inside_userinfo_never_leaks_and_warns(
    raw: str, expected_display: str
) -> None:
    """Review finding R1-userinfo-redaction-bypass: a reserved separator
    (`/`, `?`, `#`) smuggled into userinfo ahead of the `@` must not let the
    authority cut discard the `@host` remainder and hand the credential to
    `display_host`. A value whose authority is credential-shaped (non-numeric
    port) is malformed, so it classifies non-local outright (over-warning is
    the accepted direction) and the display comes from the redacted remainder
    -- never the credential fragment. When the authority is instead a CLEAN
    host[:port] (`remote.example/x@localhost`), the `@` belongs to the path,
    so the authority itself is what the warning names (issue #353, item 5)."""
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


# --- issue #353 item 1: multi-colon bare credential ---------------------------


@pytest.mark.parametrize(
    ("raw", "expected_display", "secret"),
    [
        ("user:s3cret:extra", "user", "s3cret"),
        ("token:abc123:x", "token", "abc123"),
    ],
)
def test_multicolon_bare_credential_never_leaks(
    raw: str, expected_display: str, secret: str
) -> None:
    """Issue #353 item 1 (same class as the corrected CRITICAL): a bare
    multi-colon value that is NOT a plausible IPv6 literal must not take the
    whole-value-host branch -- `user:s3cret:extra` would put the secret on
    stderr. It falls through to plain host:port handling, where the
    non-numeric-port guard drops EVERYTHING after the host part."""
    result = classify_embed_host(raw)
    assert result.is_local is False
    assert result.display_host == expected_display
    assert secret not in result.display_host


@pytest.mark.parametrize(
    ("raw", "expected_local"),
    [
        ("fe80::1234:5678", False),
        ("0:0:0:0:0:0:0:1", False),
        ("::1", True),
    ],
)
def test_plausible_bracketless_ipv6_keeps_whole_value_host(
    raw: str, expected_local: bool
) -> None:
    """Issue #353 item 1, the preserved side: a value that plausibly IS a
    bracket-less IPv6 literal (contains `::`, or is solely hex segments of
    at most 4 chars and colons) keeps the whole-value-host behavior."""
    result = classify_embed_host(raw)
    assert result.is_local is expected_local
    assert result.display_host == raw


@pytest.mark.parametrize(
    ("raw", "expected_local", "expected_display"),
    [
        ("127.0.0.1:port:extra", True, "127.0.0.1"),
        ("a:b:c:d", False, "a:b:c:d"),
    ],
)
def test_hostile_multicolon_inputs_pin_locality(
    raw: str, expected_local: bool, expected_display: str
) -> None:
    """Issue #353 item 1 fallout, pinned deliberately: a loopback host with
    multi-colon non-numeric-port garbage falls through and classifies by
    its host (local, remainder dropped -- consistent with
    `localhost:s3cret`), while an all-hex multi-colon value stays a
    plausible whole-value IPv6 host (non-local)."""
    result = classify_embed_host(raw)
    assert result.is_local is expected_local
    assert result.display_host == expected_display


# --- issue #353 item 2: balanced empty bracket pair ----------------------------


@pytest.mark.parametrize("raw", ["[]", "[]:11434"])
def test_balanced_empty_bracket_pair_is_nonlocal(raw: str) -> None:
    """Issue #353 item 2: `[]`/`[]:11434` compute an empty host, but that
    emptiness came from an EXPLICIT bracket pair, not from an unset value --
    it is not the local default. Classify non-local, display the hostport."""
    result = classify_embed_host(raw)
    assert result.is_local is False
    assert result.display_host == raw


# --- issue #353 item 3: userinfo-only degenerate values ------------------------


@pytest.mark.parametrize("raw", ["@", "@@@", "http://user@"])
def test_userinfo_only_value_is_nonlocal_with_placeholder(raw: str) -> None:
    """Issue #353 item 3: a value that reduces to an empty host only AFTER
    userinfo redaction (`@`, `@@@`, `http://user@`) is malformed, not the
    local default -- non-local, displayed as the safe placeholder (never an
    empty string, never the userinfo)."""
    result = classify_embed_host(raw)
    assert result.is_local is False
    assert result.display_host == "<unparseable>"
    assert "user" not in result.display_host


def test_lone_colon_stays_local_default() -> None:
    """Issue #353 item 3, the preserved side: a lone `:` (empty host, empty
    port, NO `@`) stays the local default exactly like `:11434`."""
    result = classify_embed_host(":")
    assert result.is_local is True


# --- issue #353 item 5: `@` in the path, judged by authority shape --------------


@pytest.mark.parametrize(
    "raw",
    [
        "http://localhost:11434/v1@x",
        "localhost:11434/v1@x",
        "http://[::1]:11434/v1@x",
    ],
)
def test_path_at_with_clean_local_authority_is_local(raw: str) -> None:
    """Issue #353 item 5: when the `@` appears only AFTER the first
    separator and the AUTHORITY parses as a clean host[:numeric-port], the
    `@` belongs to the path -- classify by the authority normally. A local
    authority stays local (and silent); the path fragment (`x`) must never
    be the display."""
    result = classify_embed_host(raw)
    assert result.is_local is True


def test_path_at_with_clean_nonlocal_authority_names_the_authority() -> None:
    """Issue #353 item 5: `remote.example/x@localhost` has a clean non-local
    authority, so the display names the AUTHORITY -- never `localhost`, which
    is a path fragment nobody configured as the host."""
    result = classify_embed_host("remote.example/x@localhost")
    assert result.is_local is False
    assert result.display_host == "remote.example"


def test_suspicious_authority_with_empty_at_remainder_uses_placeholder() -> None:
    """Issue #353 item 5, the degenerate corner: `user:s3cret/x@` has a
    credential-shaped authority (non-numeric port) so it takes the
    redact-against-full-remainder path -- but that remainder strips to
    empty, so the display is the placeholder, never an empty string."""
    result = classify_embed_host("user:s3cret/x@")
    assert result.is_local is False
    assert result.display_host == "<unparseable>"
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
