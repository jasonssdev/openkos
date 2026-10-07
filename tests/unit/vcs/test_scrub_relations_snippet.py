"""The `relations:` scrub inside `_FILE_INFO_CALLBACK_SNIPPET` (#1329).

Like `test_scrub_snippet_parity.py`, this execs the DEPLOYED snippet's own
source -- the nested `_scrub_relations` function, extracted verbatim from the
static constant -- since the real thing only runs inside `git-filter-repo`.
It pins the YAML shapes a hand-edited bundle can hold that `dump_frontmatter`
itself never emits (indented items, quoted targets, `type` before `target`).
"""

import textwrap
from collections.abc import Callable

import pytest

from openkos.vcs.git import _FILE_INFO_CALLBACK_SNIPPET

_START_MARKER = "_target_re = re.compile"
_END_MARKER = "\ncontents = value.get_contents_by_identifier"


def _extract() -> Callable[[bytes, set[bytes]], bytes]:
    start = _FILE_INFO_CALLBACK_SNIPPET.index(_START_MARKER)
    end = _FILE_INFO_CALLBACK_SNIPPET.index(_END_MARKER)
    body = _FILE_INFO_CALLBACK_SNIPPET[start:end] + "\nreturn _scrub_relations\n"
    factory_src = "def _factory():\n" + textwrap.indent(body, "    ")
    namespace: dict[str, object] = {"re": __import__("re")}
    exec(factory_src, namespace)  # noqa: S102 -- test-only, static source, no user input
    factory = namespace["_factory"]
    assert callable(factory)
    return factory()  # type: ignore[no-any-return]


_SCRUB = _extract()
_IDS = {b"concepts/bee"}


def _doc(*frontmatter_lines: str, body: str = "# Ant\n\nSee concepts/bee.\n") -> bytes:
    return (
        "---\n" + "".join(f"{x}\n" for x in frontmatter_lines) + "---\n\n" + body
    ).encode()


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        # Unindented list, the shape `dump_frontmatter` emits.
        (
            ["relations:", "- target: concepts/bee", "  type: depends_on", "title: A"],
            ["title: A"],
        ),
        # Indented list.
        (
            ["relations:", "  - target: concepts/bee", "    type: depends_on"],
            [],
        ),
        # `type` written before `target`, target quoted.
        (
            ["relations:", "- type: depends_on", "  target: 'concepts/bee'"],
            [],
        ),
        # Only the matching item goes; its neighbours and the key stay.
        (
            [
                "relations:",
                "- target: concepts/ant",
                "  type: relates_to",
                "- target: concepts/bee",
                "  type: depends_on",
                "- target: concepts/cat",
                "  type: relates_to",
            ],
            [
                "relations:",
                "- target: concepts/ant",
                "  type: relates_to",
                "- target: concepts/cat",
                "  type: relates_to",
            ],
        ),
        # A longer id sharing the prefix is a different concept.
        (
            ["relations:", "- target: concepts/bee-keeper", "  type: depends_on"],
            ["relations:", "- target: concepts/bee-keeper", "  type: depends_on"],
        ),
    ],
)
def test_scrub_relations_drops_only_the_purged_target(
    frontmatter: list[str], expected: list[str]
) -> None:
    assert _SCRUB(_doc(*frontmatter), _IDS) == _doc(*expected)


def test_scrub_relations_never_touches_the_body_or_a_document_without_it() -> None:
    plain = _doc("title: A", "type: Concept")
    assert _SCRUB(plain, _IDS) is plain
    # No frontmatter at all: returned as-is, body mention included.
    bare = b"# Ant\n\nSee concepts/bee.\n"
    assert _SCRUB(bare, _IDS) is bare
    # An unterminated block is not guessed at.
    unterminated = b"---\nrelations:\n- target: concepts/bee\n"
    assert _SCRUB(unterminated, _IDS) is unterminated
