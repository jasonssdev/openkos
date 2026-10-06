"""Link rewrite into an import namespace (okf-import, slice 2): the shared
target resolver, the pointer-site scanner, `rewrite_links_into_namespace` and
its proof, `namespace_link_violations`.

The engine has several independent link readers and they disagree (lint,
the graph, `bundle/links.py`, the index). The rewrite anchors on the `](`
and `]:` delimiters, and the proof re-reads the OUTPUT the way each reader
does; `test_link_recognizer_inventory.py` runs every real engine reader over
the corpus defined here.
"""

import pytest

from openkos.bundle import links

NS = "acme"
PREFIX = "imports/acme"

# --- resolve_link_target (2.1) -----------------------------------------------


def _resolve(target: str, file_id: str = "concepts/a") -> links.LinkTarget:
    return links.resolve_link_target(target, file_id=file_id)


@pytest.mark.parametrize(
    ("target", "kind"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("<>", "empty"),
        ("#section", "anchor"),
        ("<#section>", "anchor"),
        ("https://example.org/x.md", "external"),
        ("mailto:a@b.c", "external"),
        ("urn:isbn:123", "external"),
        ("%68ttp://example.org/x.md", "external"),
    ],
)
def test_non_path_kinds(target: str, kind: str) -> None:
    resolved = _resolve(target)
    assert resolved.kind == kind
    assert resolved.path == ""
    assert resolved.escaped is False


def test_absolute_target_is_a_path_from_the_root() -> None:
    resolved = _resolve("/people/maria.md")
    assert resolved == links.LinkTarget(
        kind="path", path="people/maria.md", suffix="", escaped=False, absolute=True
    )


def test_relative_target_resolves_against_the_document_directory() -> None:
    resolved = _resolve("../people/maria.md", "concepts/deep/a")
    assert resolved.path == "concepts/people/maria.md"
    assert resolved.absolute is False
    assert resolved.escaped is False
    # triangulate: a root document has no directory to climb through
    assert _resolve("maria.md", "a").path == "maria.md"


def test_relative_target_that_climbs_above_the_root_is_clamped() -> None:
    resolved = _resolve("../../outside.md", "concepts/a")
    assert resolved.path == "outside.md"
    assert resolved.escaped is True
    # triangulate: staying exactly at the root is not an escape
    inside = _resolve("../x.md", "concepts/a")
    assert inside.path == "x.md"
    assert inside.escaped is False


def test_absolute_target_that_climbs_above_the_root_is_clamped() -> None:
    resolved = _resolve("/../x.md")
    assert resolved.path == "x.md"
    assert resolved.escaped is True
    assert resolved.absolute is True


def test_network_path_target_reads_as_the_engine_reads_it() -> None:
    # every engine reader strips ALL leading slashes
    resolved = _resolve("//concepts/x.md")
    assert resolved.path == "concepts/x.md"
    assert resolved.absolute is True
    assert resolved.escaped is False


def test_dot_segments_are_removed_per_rfc_3986() -> None:
    assert _resolve("/a/./b/../c.md").path == "a/c.md"
    assert _resolve("./b.md", "concepts/a").path == "concepts/b.md"
    assert _resolve("/a/b/../../../c.md").escaped is True


def test_percent_escapes_are_decoded_before_resolution() -> None:
    assert _resolve("/concepts%2Fx.md").path == "concepts/x.md"
    escaped = _resolve("%2e%2e/%2e%2e/x.md", "concepts/a")
    assert escaped.escaped is True
    assert escaped.path == "x.md"
    # a decoded leading slash makes a relative-looking target absolute
    assert _resolve("%2Fetc/x.md").absolute is True


def test_fragment_and_query_are_kept() -> None:
    fragment = _resolve("/a.md#frag")
    assert fragment.path == "a.md"
    assert fragment.suffix == "#frag"
    query = _resolve("/a.md?v=1#frag")
    # the engines never split a query: it stays part of the last segment
    assert query.path == "a.md?v=1"
    assert query.suffix == "#frag"


def test_angle_brackets_are_stripped() -> None:
    assert _resolve("</people/maria.md>").path == "people/maria.md"
    assert _resolve("</my doc.md>").path == "my doc.md"


# --- pointer-site scanner (2.4) ------------------------------------------------


def _destinations(body: str) -> list[tuple[str, str]]:
    sites = links.pointer_sites(body)
    for site in sites:
        assert body[site.start : site.end] == site.destination
    return [(site.kind, site.destination) for site in sites]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("[a](/x.md)", [("inline", "/x.md")]),
        ("![alt](/x.md)", [("inline", "/x.md")]),
        ("[a](<my doc.md>)", [("inline", "<my doc.md>")]),
        ('[a](/x.md "Title")', [("inline", "/x.md")]),
        ("[a](/x.md 'Title')", [("inline", "/x.md")]),
        ("[a](/x(1).md)", [("inline", "/x(1).md")]),
        ("[a](  /x.md  )", [("inline", "/x.md")]),
        ("[a](\n/x.md)", [("inline", "/x.md")]),
        ("[a](\t/x.md#frag)", [("inline", "/x.md#frag")]),
        ("[multi\nline](/x.md)", [("inline", "/x.md")]),
        ("[a](/x.md) and [b](y.md)", [("inline", "/x.md"), ("inline", "y.md")]),
        ("[ref]: /x.md", [("definition", "/x.md")]),
        ('[ref]: /x.md "Title"', [("definition", "/x.md")]),
        ("[ref]:\n  /x.md", [("definition", "/x.md")]),
        ("[ref]: <a b.md>", [("definition", "<a b.md>")]),
    ],
)
def test_every_pointer_site_is_found_and_its_destination_read(
    body: str, expected: list[tuple[str, str]]
) -> None:
    assert _destinations(body) == expected


def test_the_scan_has_no_fence_mask_and_no_line_split() -> None:
    # fenced and inline code are pointer sites too: lint reads them unmasked
    assert _destinations("```\n[a](/x.md)\n```\n") == [("inline", "/x.md")]
    assert _destinations("`[a](/x.md)`") == [("inline", "/x.md")]


def test_an_empty_destination_is_not_a_site() -> None:
    assert _destinations("[a]() and [b](  )") == []
    # triangulate: the same text with a destination IS a site
    assert _destinations("[a](/x.md)") == [("inline", "/x.md")]


def test_the_destination_stops_at_the_closing_parenthesis_not_before() -> None:
    body = "(see [a](/x(1)(2).md) there)"
    assert _destinations(body) == [("inline", "/x(1)(2).md")]


def test_a_backslash_escaped_parenthesis_does_not_end_the_destination() -> None:
    assert _destinations(r"[a](/x\).md)") == [("inline", r"/x\).md")]


def test_an_unclosed_angle_destination_is_read_as_a_plain_run() -> None:
    assert _destinations("[a](<x.md)") == [("inline", "<x.md")]


def test_sites_come_back_in_document_order() -> None:
    body = "[a](/1.md)\n[r]: /2.md\n[b](/3.md)"
    assert [dest for _, dest in _destinations(body)] == ["/1.md", "/2.md", "/3.md"]


# --- rewrite corpus (2.5) ------------------------------------------------------

# Each form carries one pointer site, `{t}` being the destination.
FORMS: list[tuple[str, str]] = [
    ("inline", "See [X]({t}) for more.\n"),
    ("image", "![alt text]({t})\n"),
    ("angle", "A [X](<{t}>) link.\n"),
    ("titled", 'A [X]({t} "A title") link.\n'),
    ("single-quote-title", "A [X]({t} 'A title') link.\n"),
    ("reference", "Read [X][ref].\n\n[ref]: {t}\n"),
    ("reference-titled", 'Read [X][ref].\n\n[ref]: {t} "A title"\n'),
    ("multi-line-label", "A [multi\nline label]({t}) link.\n"),
    ("fenced-code", "```\n[X]({t})\n```\n"),
    ("inline-code", "Write `[X]({t})` to link.\n"),
]

# (row id, foreign id of the carrying document, destination in, destination
# out, whether the rewrite clamped a `..` above the foreign root)
ROWS: list[tuple[str, str, str, str, bool]] = [
    ("abs", "a", "/concepts/x.md", "/imports/acme/concepts/x.md", False),
    ("abs-no-ext", "a", "/concepts/x", "/imports/acme/concepts/x", False),
    ("abs-fragment", "a", "/concepts/x.md#f", "/imports/acme/concepts/x.md#f", False),
    ("abs-query", "a", "/concepts/x.md?v=1", "/imports/acme/concepts/x.md?v=1", False),
    ("abs-network", "a", "//concepts/x.md", "/imports/acme/concepts/x.md", False),
    ("abs-dotdot", "a", "/concepts/../x.md", "/imports/acme/x.md", False),
    ("abs-dot", "a", "/concepts/./x.md", "/imports/acme/concepts/x.md", False),
    ("abs-encoded", "a", "/concepts%2Fx.md", "/imports/acme/concepts/x.md", False),
    ("abs-above-root", "a", "/../x.md", "/imports/acme/x.md", True),
    ("abs-root", "a", "/", "/imports/acme/", False),
    (
        "abs-nested",
        "concepts/deep/b",
        "/concepts/deep/b.md",
        "/imports/acme/concepts/deep/b.md",
        False,
    ),
    ("rel-inside", "a", "b.md", "b.md", False),
    ("rel-dot", "a", "./b.md", "./b.md", False),
    ("rel-encoded-inside", "a", "sub%2Fb.md", "sub%2Fb.md", False),
    ("rel-escape-root-doc", "a", "../outside.md", "/imports/acme/outside.md", True),
    ("rel-root", "a", "..", "/imports/acme/", True),
    ("nested-up-one", "concepts/deep/b", "../x.md", "../x.md", False),
    ("nested-up-to-root", "concepts/deep/b", "../../x.md", "../../x.md", False),
    ("nested-escape", "concepts/deep/b", "../../../x.md", "/imports/acme/x.md", True),
    ("nested-sibling", "concepts/deep/b", "sibling.md", "sibling.md", False),
    (
        "nested-encoded-escape",
        "concepts/deep/b",
        "%2e%2e/%2e%2e/%2e%2e/x.md",
        "/imports/acme/x.md",
        True,
    ),
    ("external", "a", "https://example.org/x.md", "https://example.org/x.md", False),
    ("mailto", "a", "mailto:a@b.c", "mailto:a@b.c", False),
    ("anchor", "a", "#sec", "#sec", False),
]


class Case:
    def __init__(
        self, form: str, template: str, row: tuple[str, str, str, str, bool]
    ) -> None:
        row_id, self.foreign_id, target_in, target_out, clamped = row
        self.id = f"{form}/{row_id}"
        self.body_in = template.format(t=target_in)
        self.body_out = template.format(t=target_out)
        self.rewritten = int(target_in != target_out)
        self.clamped = int(clamped)


CORPUS: list[Case] = [
    Case(form, template, row) for form, template in FORMS for row in ROWS
]


@pytest.mark.parametrize("case", CORPUS, ids=[case.id for case in CORPUS])
def test_the_corpus_is_rewritten_to_the_exact_bytes(case: Case) -> None:
    result = links.rewrite_links_into_namespace(
        case.body_in, foreign_id=case.foreign_id, prefix=PREFIX
    )
    assert result.text == case.body_out
    assert result.links_rewritten == case.rewritten
    assert result.links_clamped == case.clamped


def test_the_corpus_exercises_every_outcome() -> None:
    # the product is not vacuous: rewrites, untouched links and clamps all occur
    assert sum(case.rewritten for case in CORPUS) > 100
    assert sum(1 - case.rewritten for case in CORPUS) > 50
    assert sum(case.clamped for case in CORPUS) > 30


@pytest.mark.parametrize(
    ("body", "expected", "rewritten", "clamped"),
    [
        # a spaced target, inside and outside
        ("[X](<my doc.md>)", "[X](<my doc.md>)", 0, 0),
        ("[X](</my doc.md>)", "[X](</imports/acme/my doc.md>)", 1, 0),
        ("[X](</a b/../c d.md>)", "[X](</imports/acme/c%20d.md>)", 1, 0),
        ("[ref]: <a b.md>", "[ref]: <a b.md>", 0, 0),
        ("[ref]: </a b.md>", "[ref]: </imports/acme/a b.md>", 1, 0),
        # balanced parentheses survive byte for byte on the insertion path
        ("[X](/a(1).md)", "[X](/imports/acme/a(1).md)", 1, 0),
        # a decoded parenthesis is re-quoted so no reader can end the target early
        ("[X](/a%28b.md)", "[X](/imports/acme/a%28b.md)", 1, 0),
        # several links, only some pointing in
        (
            "[a](/x.md) [b](y.md) [c](https://e.org) [d](../../z.md)",
            "[a](/imports/acme/x.md) [b](y.md) [c](https://e.org)"
            " [d](/imports/acme/z.md)",
            2,
            1,
        ),
        # the title and the text around a link are untouched
        (
            'Before **bold** [a](/x.md "Keep  this") after.\n',
            'Before **bold** [a](/imports/acme/x.md "Keep  this") after.\n',
            1,
            0,
        ),
        # a definition whose destination is on the next line
        ("[ref]:\n  /concepts/x.md\n", "[ref]:\n  /imports/acme/concepts/x.md\n", 1, 0),
        # nothing to rewrite returns the same text
        ("Plain prose with no links.\n", "Plain prose with no links.\n", 0, 0),
    ],
)
def test_rewrite_details(
    body: str, expected: str, rewritten: int, clamped: int
) -> None:
    result = links.rewrite_links_into_namespace(body, foreign_id="a", prefix=PREFIX)
    assert result.text == expected
    assert result.links_rewritten == rewritten
    assert result.links_clamped == clamped


def test_raw_html_and_wiki_links_are_not_pointer_sites() -> None:
    body = (
        '<a href="/concepts/x.md">x</a> <img src="/images/a.png">\n'
        "[[wiki link]] and [[wiki|/concepts/x.md]]\n"
    )
    result = links.rewrite_links_into_namespace(body, foreign_id="a", prefix=PREFIX)
    assert result.text == body
    assert result.links_rewritten == 0
    assert result.html_links is True
    # triangulate: a body with no bundle-absolute attribute reports none
    plain = links.rewrite_links_into_namespace(
        '<a href="https://e.org/x">x</a> [[wiki]]\n', foreign_id="a", prefix=PREFIX
    )
    assert plain.html_links is False


def test_a_link_that_is_not_a_pointer_site_is_not_counted() -> None:
    result = links.rewrite_links_into_namespace(
        "A footnote.[^1]\n\n[^1]: see the notes\n", foreign_id="a", prefix=PREFIX
    )
    assert result.links_rewritten == 0


def test_a_site_nested_inside_a_rewritten_destination_is_left_to_the_proof() -> None:
    body = "[a](/x](/concepts/q.md)"
    result = links.rewrite_links_into_namespace(body, foreign_id="a", prefix=PREFIX)
    assert result.text == "[a](/imports/acme/x](/concepts/q.md)"
    # the inner `](/concepts/q.md` is still a site of the output, and it
    # still resolves outside the namespace: the proof refuses it
    assert links.namespace_link_violations(
        result.text, concept_id="imports/acme/a", prefix=PREFIX
    )


# --- the proof: namespace_link_violations (2.7) --------------------------------

LOCAL_ID = "imports/acme/concepts/a"


def _violations(body: str, concept_id: str = LOCAL_ID) -> list[str]:
    return links.namespace_link_violations(body, concept_id=concept_id, prefix=PREFIX)


def test_a_clean_output_has_no_violation() -> None:
    body = (
        "[a](/imports/acme/concepts/x.md) [b](sibling.md) [c](../y.md#f)\n"
        "[d](https://example.org/x.md) [e](#sec) [f](mailto:a@b.c)\n"
        '[g](/imports/acme/x.md "title") ![i](/imports/acme/i.png)\n'
        "[ref]: /imports/acme/concepts/x.md\n"
        "```\n[h](/imports/acme/z.md)\n```\n"
    )
    assert len(links.pointer_sites(body)) >= 9  # the scan ran over real sites
    assert _violations(body) == []


def test_a_link_left_at_a_foreign_absolute_path_is_a_violation() -> None:
    violations = _violations("See [x](/concepts/x.md) here.\n")
    assert len(violations) == 1
    assert "`/concepts/x.md`" in violations[0]
    assert "resolves to `concepts/x.md`" in violations[0]


def test_a_relative_link_that_climbs_out_of_the_bundle_is_a_violation() -> None:
    violations = _violations("[x](../../../../x.md)")
    assert violations == [
        f"{LOCAL_ID}: `../../../../x.md` climbs out of the bundle, outside {PREFIX}/"
    ]


def test_a_relative_link_that_leaves_the_namespace_is_a_violation() -> None:
    violations = _violations("[x](../../../x.md)")
    assert len(violations) == 1
    assert "resolves to `x.md`" in violations[0]


def test_an_escaping_dot_dot_after_the_transform_is_a_violation() -> None:
    violations = _violations("[x](/imports/acme/../other/x.md)")
    assert len(violations) == 1
    assert "resolves to `imports/other/x.md`" in violations[0]


def test_a_reference_definition_is_proved_too() -> None:
    violations = _violations("[ref]: /concepts/x.md\n")
    assert len(violations) == 1
    assert "`/concepts/x.md`" in violations[0]


def test_a_link_in_fenced_code_is_proved_too() -> None:
    assert _violations("```\n[x](/concepts/x.md)\n```\n")


def test_a_label_spanning_lines_is_proved_too() -> None:
    assert _violations("[multi\nline](/concepts/x.md)")


# Each case below is a destination that leaves the namespace under exactly
# ONE reading an engine reader takes, so dropping that reading from the proof
# lets it through.


def test_only_the_first_closing_parenthesis_reading_sees_this_one() -> None:
    # lint and the index read everything up to the first `)`, whitespace
    # included; CommonMark and the graph stop at the first space
    body = "[x](/imports/acme/x.md ../../../y.md)"
    violations = _violations(body)
    assert len(violations) == 1
    assert "`/imports/acme/x.md ../../../y.md`" in violations[0]


def test_only_the_raw_reading_sees_this_one() -> None:
    # not decoded the reader sees `a%2Fb` as ONE segment and `..` pops it
    # and the namespace; decoded `a/b` is two segments and stays inside
    violations = _violations("[x](/imports/acme/a%2Fb/../../y.md)")
    assert len(violations) == 1
    assert "resolves to `imports/y.md`" in violations[0]


def test_only_the_decoded_reading_sees_this_one() -> None:
    # `_bundle_target_id` decodes `%2e%2e` to `..`; lint does not
    violations = _violations("[x](/imports/acme/%2e%2e/%2e%2e/y.md)")
    assert len(violations) == 1
    assert "resolves to `y.md`" in violations[0]


def test_only_the_angle_stripped_reading_sees_a_link_to_the_root() -> None:
    # `_bundle_target_id` strips `<>`; lint does not, and reads a file
    # named `<>` inside the namespace
    violations = _violations("[x](</>)")
    assert len(violations) == 1
    assert "`</>`" in violations[0]


def test_only_the_inline_link_reader_sees_an_angle_destination_with_a_newline() -> None:
    # CommonMark refuses a line ending inside `<...>`; `_INLINE_LINK_RE`
    # (`<[^>]*>`) reads straight through it
    body = "[a](<%2F%2e%2e<>%2e%2e../)"
    assert _violations(body)


def test_only_the_definition_reader_sees_an_angle_destination_with_an_inner_bracket() -> (
    None
):
    body = "[r]: </ x.md</imports/acme/t>\n"
    assert _violations(body)


def test_a_destination_after_two_line_endings_is_not_a_commonmark_link_but_is_refused() -> (
    None
):
    # lint's `[^)]+` still reads it, so the proof must too
    assert _violations("[a](\n\n/concepts/x.md)")


def test_a_destination_is_judged_in_the_frame_of_its_own_document() -> None:
    # `../x.md` is inside from a nested document and escapes from a shallow one
    nested = _violations("[x](../x.md)", "imports/acme/concepts/deep/a")
    assert nested == []
    shallow = _violations("[x](../../../x.md)", "imports/acme/a")
    assert shallow
