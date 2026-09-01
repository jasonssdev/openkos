"""Unit tests for `_slugify` -- the function that turns a filename stem or
an LLM-extracted title into a slug.

The slug IS the identity, not a filename detail: an object's Concept ID is
its path within the bundle with `.md` removed (OKF §2,
`docs/knowledge-object-model.md`), so what this function keeps and what it
throws away decides which objects exist at all. Issue #414: the old
`[^a-z0-9]+` sanitiser collapsed every non-ASCII character to a hyphen, so
`Diseño de Módulos` became `dise-o-de-m-dulos` and a title in any non-Latin
script (`知识图谱`, `Общая теория`, `こんにちは`) slugified to the empty
string -- which makes `_stage_derived_objects` skip the candidate, silently
discarding EVERY correctly extracted object from such a source.

These tests pin the four properties the Unicode-safe replacement must hold
simultaneously: ASCII byte-for-byte backward compatibility, Unicode letter/
digit preservation, NFC stability across filesystems, and path containment.
"""

import re
import unicodedata

import pytest

from openkos.cli.main import _slugify

# The pre-#414 implementation, kept verbatim as the backward-compatibility
# oracle. Every pure-ASCII input MUST slugify identically under the new
# implementation, or existing bundles silently rename themselves.
_LEGACY_SLUG_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


def _legacy_slugify(stem: str) -> str:
    return _LEGACY_SLUG_SANITIZE_RE.sub("-", stem.lower()).strip("-")


ASCII_INPUTS = [
    "notes",
    "My Notes",
    "my-notes",
    "My_Notes",
    "2024 Q1 Review",
    "Stoicism",
    "a  b",
    "--leading-and-trailing--",
    "MiXeD CaSe 123",
    "dot.separated.name",
    "already-a-slug-42",
    "!!!",
    "+++",
    "???",
    "",
    "   ",
    "under_score",
    "tabs\tand\nnewlines",
    "slash/in/name",
    "..",
    "a",
    "1",
]


@pytest.mark.parametrize("stem", ASCII_INPUTS)
def test_ascii_input_matches_the_legacy_implementation(stem: str) -> None:
    """Backward compatibility is non-negotiable: for any pure-ASCII input the
    new `_slugify` returns EXACTLY what the old `[^a-z0-9]+` regex returned.
    A slug is an identity, so a changed slug is a renamed object -- existing
    bundles must never silently rename."""
    assert stem.isascii()
    assert _slugify(stem) == _legacy_slugify(stem)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Diseño de Módulos", "diseño-de-módulos"),
        ("Generación automática de la Skill", "generación-automática-de-la-skill"),
        ("Café", "café"),
        ("Ærøskøbing", "ærøskøbing"),
        ("Straße", "straße"),
    ],
)
def test_accented_latin_titles_keep_their_letters(title: str, expected: str) -> None:
    """Accented Latin letters survive instead of collapsing to hyphens
    (#414: `Diseño de Módulos` -> `dise-o-de-m-dulos`)."""
    assert _slugify(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("知识图谱", "知识图谱"),
        ("Общая теория", "общая-теория"),
        ("こんにちは", "こんにちは"),
        ("지식 그래프", "지식-그래프"),
        ("Θεωρία", "θεωρία"),
        ("المعرفة", "المعرفة"),
        ("ידע", "ידע"),
    ],
)
def test_non_latin_scripts_produce_a_non_empty_slug(title: str, expected: str) -> None:
    """The #414 headline bug: a title in a non-Latin script slugified to the
    empty string, so `_stage_derived_objects` dropped every object extracted
    from that source. Transliteration is deliberately NOT used -- a
    romanization is a choice, not a fact -- so the script is preserved."""
    assert _slugify(title) == expected


def test_devanagari_combining_marks_survive() -> None:
    """Indic vowel signs and the virama are combining marks (Unicode
    categories `Mc`/`Mn`), not `str.isalnum()` characters. Dropping them
    would corrupt the word rather than merely transliterate it, so they are
    kept as part of their grapheme."""
    assert _slugify("हिन्दी") == "हिन्दी"


def test_nfd_and_nfc_spellings_slugify_identically() -> None:
    """Identity must be stable across filesystems. macOS normalises to NFD,
    so without an explicit `unicodedata.normalize("NFC", ...)` the same title
    would yield different bytes -- and therefore a different Concept ID -- on
    different platforms."""
    nfc = unicodedata.normalize("NFC", "Diseño")
    nfd = unicodedata.normalize("NFD", "Diseño")

    assert nfc != nfd  # the two spellings really are different strings
    assert _slugify(nfd) == _slugify(nfc)


@pytest.mark.parametrize("title", ["Diseño", "Módulos", "café", "ﬁ"])
def test_the_returned_slug_is_nfc_normalized(title: str) -> None:
    """The returned slug is always in NFC, whatever the input spelling."""
    for form in ("NFC", "NFD"):
        slug = _slugify(unicodedata.normalize(form, title))
        assert slug == unicodedata.normalize("NFC", slug)


TRAVERSAL_INPUTS = [
    "../../etc/passwd",
    "..",
    "../..",
    "..\\..\\windows\\system32",
    "/absolute/path",
    "C:\\Users\\admin",
    "a/b/c",
    "a\\b\\c",
    ".hidden",
    "....",
    "~/.ssh/id_rsa",
    "nul",
    "con",
    "aux.txt",
    'a"b<c>d|e*f?g:h',
    "null\x00byte",
    "bell\x07and\x1bescape",
    "\u202eright-to-left-override",
    "\ufeffzero-width",
    "nbsp\u00a0separated",
    "fullwidth\uff0fsolidus",
    "fraction\u2044slash",
    "division\u2215slash",
    "\u2028line\u2029separators",
    "../../../知识图谱",
    "Diseño/../../etc",
]

FORBIDDEN_IN_A_SLUG = frozenset('/\\.\x00:*?"<>|\r\n\t')


@pytest.mark.parametrize("title", TRAVERSAL_INPUTS)
def test_no_path_separator_or_traversal_survives(title: str) -> None:
    """Path containment: the returned slug is joined straight onto a bundle
    directory, so no `/`, `\\`, `.`, `..`, drive letter separator, null byte,
    control character or Windows-reserved character may EVER appear in it,
    regardless of input."""
    slug = _slugify(title)

    assert not (FORBIDDEN_IN_A_SLUG & set(slug))
    assert ".." not in slug
    assert not any(char.isspace() for char in slug)
    assert all(
        char.isprintable() or unicodedata.category(char).startswith("M")
        for char in slug
    )


@pytest.mark.parametrize("title", TRAVERSAL_INPUTS + ASCII_INPUTS)
def test_every_slug_character_is_alphanumeric_a_mark_or_a_hyphen(title: str) -> None:
    """The whitelist stated positively: containment holds by construction
    because nothing but Unicode letters/digits, combining marks and the
    hyphen can ever reach the output."""
    for char in _slugify(title):
        assert (
            char == "-" or char.isalnum() or unicodedata.category(char).startswith("M")
        ), f"unexpected character {char!r} in slug for {title!r}"


@pytest.mark.parametrize(
    "title",
    ["!!!", "???", "+++", "", "   ", "---", "🎉🎉", "…", "«»", "\x00", "./."],
)
def test_titles_with_no_letters_or_digits_still_slugify_to_empty(title: str) -> None:
    """The empty-slug fallback path must keep working: a title made only of
    punctuation or emoji still yields `""`, which the callers
    (`_stage_derived_objects`, `ingest`, `application.query.stage_filed_answer`)
    handle explicitly. `_slugify` must not start raising instead."""
    assert _slugify(title) == ""


def test_hyphen_runs_collapse_across_mixed_ascii_and_unicode() -> None:
    """Runs of stripped characters collapse to a single hyphen and are
    trimmed at both ends, exactly as before -- the rule is unchanged, only
    the set of characters that survive it is wider."""
    assert _slugify("  ¿Qué?  --  Módulos!!  ") == "qué-módulos"
