"""The surviving-concept scrub inside `_FILE_INFO_CALLBACK_SNIPPET` (#1329).

Like `test_scrub_snippet_parity.py`, this execs the DEPLOYED snippet's own
source -- the nested `_scrub_concept` function and its helpers, extracted
verbatim from the static constant -- since the real thing only runs inside
`git-filter-repo`. It pins the YAML and markdown shapes a hand-edited bundle
can hold that `dump_frontmatter` itself never emits (indented items, quoted
values, flow lists, `type` before `target`).
"""

import textwrap
from collections.abc import Callable

import pytest

from openkos.vcs.git import _FILE_INFO_CALLBACK_SNIPPET

_START_MARKER = "_bullet_markers = "
_END_MARKER = "\ncontents = value.get_contents_by_identifier"

Scrub = Callable[[bytes, set[bytes], list[str], bool], bytes]


def _extract() -> Scrub:
    start = _FILE_INFO_CALLBACK_SNIPPET.index(_START_MARKER)
    end = _FILE_INFO_CALLBACK_SNIPPET.index(_END_MARKER)
    body = _FILE_INFO_CALLBACK_SNIPPET[start:end] + "\nreturn _scrub_concept\n"
    factory_src = "def _factory():\n" + textwrap.indent(body, "    ")
    namespace: dict[str, object] = {"re": __import__("re")}
    exec(factory_src, namespace)  # noqa: S102 -- test-only, static source, no user input
    factory = namespace["_factory"]
    assert callable(factory)
    return factory()  # type: ignore[no-any-return]


_SCRUB = _extract()
_IDS = {b"concepts/bee"}
_TITLE = "Bee Keeping Guild"


def _doc(*frontmatter: str, body: str = "\n# Ant\n\nText.\n") -> bytes:
    return ("---\n" + "".join(f"{x}\n" for x in frontmatter) + "---\n" + body).encode()


def _run(
    document: bytes,
    *,
    titles: list[str] | None = None,
    relations: bool = False,
) -> bytes:
    return _SCRUB(document, _IDS, [_TITLE] if titles is None else titles, relations)


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        # Unindented list, the shape `dump_frontmatter` emits.
        (
            ["relations:", "- target: concepts/bee", "  type: depends_on", "title: A"],
            ["title: A"],
        ),
        # Indented list.
        (["relations:", "  - target: concepts/bee", "    type: depends_on"], []),
        # `type` written before `target`, target quoted.
        (["relations:", "- type: depends_on", "  target: 'concepts/bee'"], []),
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
def test_relations_scrub_drops_only_the_purged_target(
    frontmatter: list[str], expected: list[str]
) -> None:
    assert _run(_doc(*frontmatter), relations=True) == _doc(*expected)


def test_relations_are_left_alone_unless_asked() -> None:
    document = _doc("relations:", "- target: concepts/bee", "  type: depends_on")
    assert _run(document, relations=False) is document


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        # Block list, with the generated `sources` projection kept in step.
        (
            [
                "provenance:",
                "- concepts/bee",
                "- sources/notes",
                "sources:",
                "- id: concepts/bee",
                "  resource: /concepts/bee.md",
                "- id: sources/notes",
                "  resource: /sources/notes.md",
            ],
            [
                "provenance:",
                "- sources/notes",
                "sources:",
                "- id: sources/notes",
                "  resource: /sources/notes.md",
            ],
        ),
        # Entry spellings that normalize to the id: leading slash, `.md`, quotes.
        (
            ["provenance:", "- '/concepts/bee.md'", "- sources/notes"],
            ["provenance:", "- sources/notes"],
        ),
        # Flow list.
        (
            ["provenance: [concepts/bee, sources/notes]"],
            ["provenance: [sources/notes]"],
        ),
        # Whole provenance is the purged id: both keys go.
        (
            [
                "title: A",
                "provenance:",
                "- concepts/bee",
                "sources:",
                "- id: concepts/bee",
                "  resource: /concepts/bee.md",
            ],
            ["title: A"],
        ),
        (["provenance: [concepts/bee]", "title: A"], ["title: A"]),
        # A different concept's id sharing the prefix survives.
        (
            ["provenance:", "- concepts/bee-keeper"],
            ["provenance:", "- concepts/bee-keeper"],
        ),
    ],
)
def test_provenance_and_sources_lose_the_purged_entry(
    frontmatter: list[str], expected: list[str]
) -> None:
    assert _run(_doc(*frontmatter)) == _doc(*expected)


def _body_of(body: str, **kwargs: object) -> str:
    out = _run(_doc("title: A", body=body), **kwargs)  # type: ignore[arg-type]
    return out.decode().split("---\n", 2)[2]


def test_a_bullet_that_opens_with_a_link_to_the_id_is_dropped_whole() -> None:
    body = f"\n- [{_TITLE}](/concepts/bee.md) -- rival\n- [Cat](/concepts/cat.md)\n"
    assert _body_of(body) == "\n- [Cat](/concepts/cat.md)\n"


def test_link_text_is_dropped_only_when_it_is_the_title() -> None:
    body = (
        f"\nSee [{_TITLE}](/concepts/bee.md) and [the guild](/concepts/bee.md) "
        "and [Cat](/concepts/cat.md).\n"
    )
    assert _body_of(body) == "\nSee  and the guild and [Cat](/concepts/cat.md).\n"


def test_a_title_that_is_not_scrubbed_keeps_its_link_text() -> None:
    body = "\nSee [Bee](/concepts/bee.md).\n"
    assert _body_of(body, titles=[]) == "\nSee Bee.\n"


def test_bare_ids_and_titles_become_purged_on_boundaries_only() -> None:
    body = (
        f"\nThe {_TITLE} (concepts/bee) is not concepts/bee-keeper, "
        f"nor the {_TITLE}s or x{_TITLE}, nor a Bee Keeping guild.\n"
    )
    assert _body_of(body) == (
        "\nThe [purged] ([purged]) is not concepts/bee-keeper, "
        f"nor the {_TITLE}s or x{_TITLE}, nor a Bee Keeping guild.\n"
    )


def test_non_ascii_titles_respect_unicode_word_boundaries() -> None:
    assert _body_of(
        "\nVisita a Gremio Colmena Ñandú hoy; Gremio Colmena Ñandúes no.\n",
        titles=["Gremio Colmena Ñandú"],
    ) == ("\nVisita a [purged] hoy; Gremio Colmena Ñandúes no.\n")


def test_a_document_without_a_mention_comes_back_untouched() -> None:
    plain = _doc("title: A", "provenance:", "- sources/notes")
    assert _run(plain, relations=True) is plain
    bare = b"# Ant\n\nNothing here.\n"
    assert _run(bare) is bare
