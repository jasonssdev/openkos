"""PARITY test (Slice 2, first-class/explicit ask): proves the
`--file-info-callback` snippet's in-BYTES link-identity matcher and
`openkos.bundle.index._link_identity` resolve IDENTICAL identities for the
same inputs.

The snippet (`openkos.vcs.git._FILE_INFO_CALLBACK_SNIPPET`) runs only inside
`git-filter-repo`'s own subprocess, so this harness `exec`s just the
`_identity` nested function's SOURCE TEXT (extracted verbatim from the
static snippet constant, never re-typed) in an isolated namespace and calls
it directly -- proving the DEPLOYED snippet's own bytes, not a hand-copied
re-implementation, are what is under test.
"""

import textwrap
from collections.abc import Callable

import pytest

from openkos.bundle.index import _link_identity
from openkos.vcs.git import _FILE_INFO_CALLBACK_SNIPPET

_START_MARKER = "_scheme_re = re.compile"
_END_MARKER = "\n\ncontents = value.get_contents_by_identifier"


def _extract_identity_fn() -> Callable[[bytes], bytes | None]:
    """Extract the `_identity(target)` nested function's exact source text
    from the static snippet constant, and `exec` it as a standalone
    callable -- so this test exercises the SAME bytes `git filter-repo`
    would run, not a hand-copied duplicate."""
    start = _FILE_INFO_CALLBACK_SNIPPET.index(_START_MARKER)
    end = _FILE_INFO_CALLBACK_SNIPPET.index(_END_MARKER)
    assert start != -1
    assert end != -1
    body = _FILE_INFO_CALLBACK_SNIPPET[start:end] + "\nreturn _identity\n"

    factory_src = "def _factory():\n" + textwrap.indent(body, "    ")
    namespace: dict[str, object] = {"re": __import__("re")}
    exec(factory_src, namespace)  # noqa: S102 -- test-only, static source, no user input
    factory = namespace["_factory"]
    assert callable(factory)
    return factory()  # type: ignore[no-any-return]


_IDENTITY_FN = _extract_identity_fn()


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("concepts/foo", "concepts/foo"),
        ("/concepts/foo.md", "concepts/foo"),
        ("concepts/foo.md", "concepts/foo"),
        ("concepts/./foo.md", "concepts/foo"),
        ("concepts/bar/../foo.md", "concepts/foo"),
        ("../foo", None),
        ("http://example.com/foo", None),
        ("mailto:a@b.com", None),
        ('/concepts/foo.md "Foo Title"', "concepts/foo"),
        ("/concepts/foo.md#section", "concepts/foo"),
        ("", None),
    ],
)
def test_snippet_identity_matches_link_identity(
    target: str, expected: str | None
) -> None:
    """The snippet's bytes `_identity` and `bundle.index._link_identity`
    resolve to the SAME identity for the same parametrized input, across
    plain paths, leading `/`, `.md` suffix, `.`/`..` segments, external
    scheme URLs, quoted-title suffix, and `#fragment` suffix."""
    str_result = _link_identity(target)
    bytes_result = _IDENTITY_FN(target.encode("utf-8"))

    assert str_result == expected
    assert bytes_result == (expected.encode("utf-8") if expected is not None else None)


def test_snippet_identity_non_utf8_bytes_return_none() -> None:
    """A link target that is not valid UTF-8 -- which cannot even be
    represented as a Python `str` for `_link_identity` -- must resolve to
    `None` in the bytes-only snippet matcher, never a spurious match. This
    is a defensive case with no `_link_identity` equivalent to compare
    against."""
    assert _IDENTITY_FN(b"\xff\xfe/concepts/foo.md") is None
