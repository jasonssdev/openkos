"""Pointers into withheld objects in bodies and reserved files (okf-export,
#1301, ADR-0048): `links.withhold_links`, `index.filter_index_for_export`
and `log.render_export_log`."""

from datetime import date
from pathlib import Path

import pytest

from openkos.bundle import index as bundle_index
from openkos.bundle import links as bundle_links
from openkos.bundle import log as bundle_log
from openkos.model import okf

_EXPORTED = frozenset({"concepts/epicureanism", "sources/notes"})


def _withhold(body: str, file_id: str = "concepts/epicureanism") -> str:
    return bundle_links.withhold_links(body, file_id=file_id, exported=_EXPORTED)


# --- withhold_links -----------------------------------------------------------


def test_the_related_line_into_a_confidential_concept() -> None:
    body = "- [Stoicism](/concepts/stoicism.md) — contrasted with; both agree\n"
    assert _withhold(body) == "- [withheld] — contrasted with; both agree\n"


@pytest.mark.parametrize(
    "target",
    [
        "/concepts/stoicism.md",
        "./stoicism.md",
        "stoicism.md",
        "../concepts/stoicism.md",
        "/concepts/stoicism.md#apatheia",
        "</concepts/stoicism.md>",
        '/concepts/stoicism.md "Stoicism"',
        "/concepts/sto%69cism.md",
    ],
)
def test_every_inline_form_into_a_withheld_concept_is_removed(target: str) -> None:
    body = f"See [Stoicism]({target}) here.\n"
    assert _withhold(body) == "See [withheld] here.\n"


def test_an_image_into_a_withheld_document_is_removed() -> None:
    assert _withhold("![Stoicism](/concepts/stoicism.md)\n") == "[withheld]\n"


@pytest.mark.parametrize(
    "link",
    [
        "[Epicureanism](/concepts/epicureanism.md)",
        "[notes](../sources/notes.md#top)",
        "[SEP](https://plato.stanford.edu/entries/stoicism/)",
        "[mail](mailto:a@b.c)",
        "[here](#section)",
        "[index](/index.md)",
        "[picture](/images/a.png)",
        "[outside](../../../elsewhere.md)",
        # Percent-encoding is decoded before the id is compared, so an
        # encoded link to an EXPORTED concept is not withheld by accident.
        "[Epicureanism](/concepts/epicur%65anism.md)",
    ],
)
def test_links_that_are_not_into_a_withheld_concept_are_kept(link: str) -> None:
    body = f"Text {link} text.\n"
    assert _withhold(body) is body


def test_a_reference_definition_and_its_uses_are_removed() -> None:
    body = (
        "Read [the Stoics][sto] and [Sto] and [sto][] and [Epicurus][epi].\n"
        "\n"
        "[sto]: /concepts/stoicism.md\n"
        "[epi]: /concepts/epicureanism.md\n"
    )
    assert _withhold(body) == (
        "Read [withheld] and [withheld] and [withheld] and [Epicurus][epi].\n"
        "\n"
        "[epi]: /concepts/epicureanism.md\n"
    )


def test_a_footnote_is_not_a_reference_link() -> None:
    body = "A claim.[^1]\n\n[^1]: see /concepts/stoicism.md\n"
    assert _withhold(body) is body


def test_fenced_code_is_left_for_the_leak_check() -> None:
    body = "```\n[Stoicism](/concepts/stoicism.md)\n```\n"
    assert _withhold(body) is body


def test_prose_outside_links_is_kept() -> None:
    body = "Stoicism is mentioned here by name, outside any link.\n"
    assert _withhold(body) is body


def test_merge_link_scanner_is_unchanged() -> None:
    # `withhold_links` must not change what `merge`/`forget` match.
    files = {
        "a.md": "[x](/b.md) [y](./b.md)\n",
    }
    rewrites = bundle_links.find_inbound_link_rewrites(
        files, absorbed_id="b", survivor_id="c"
    )
    assert [r.file for r in rewrites] == ["a.md"]


def test_good_life_demo_inbound_scan_is_unchanged() -> None:
    bundle = Path(__file__).resolve().parents[3] / "examples/good-life-demo/bundle"
    files = {
        p.relative_to(bundle).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(bundle.rglob("*.md"))
    }
    rewrites = bundle_links.find_inbound_link_rewrites(
        files, absorbed_id="concepts/stoicism", survivor_id="concepts/x"
    )
    assert sorted({r.file for r in rewrites}) == [
        "concepts/epicureanism.md",
        "decisions/frame-the-essay-on-the-dichotomy-of-control.md",
        "index.md",
        "log.md",
        "people/maria-salazar.md",
        "sources/call-with-maria-2026-07-14.md",
        "sources/notes-on-the-enchiridion-2026-07-05.md",
    ]


# --- filter_index_for_export ---------------------------------------------------

_INDEX = (
    "---\nokf_version: '0.2'\n---\n"
    "\n"
    "# Concepts\n"
    "\n"
    "* [Stoicism](/concepts/stoicism.md) - Virtue is the only good.\n"
    "* [Epicureanism](/concepts/epicureanism.md) - The good is pleasure.\n"
    "\n"
    "# People\n"
    "\n"
    "* [Maria](/people/maria.md) - A friend.\n"
    "\n"
    "# Sources\n"
    "\n"
    "* [Notes](/sources/notes.md) - Reading notes; see [Maria](/people/maria.md).\n"
)


def test_withheld_entries_and_emptied_sections_are_dropped() -> None:
    result = bundle_index.filter_index_for_export(_INDEX, _EXPORTED)
    assert result == (
        "---\nokf_version: '0.2'\n---\n"
        "\n"
        "# Concepts\n"
        "\n"
        "* [Epicureanism](/concepts/epicureanism.md) - The good is pleasure.\n"
    )


def test_the_filtered_index_is_conformant(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text(
        bundle_index.filter_index_for_export(_INDEX, _EXPORTED), encoding="utf-8"
    )
    assert okf.check_conformance(tmp_path) == []


def test_a_fully_exported_index_keeps_every_entry() -> None:
    exported = _EXPORTED | {"concepts/stoicism", "people/maria"}
    result = bundle_index.filter_index_for_export(_INDEX, exported)
    assert "Stoicism" in result
    assert "# People" in result
    assert result.count("* [") == 4


def test_prose_links_in_the_index_are_withheld_too() -> None:
    text = "---\nokf_version: '0.2'\n---\n\nIntro, see [Maria](/people/maria.md).\n"
    result = bundle_index.filter_index_for_export(text, _EXPORTED)
    assert "maria" not in result
    assert "[withheld]" in result


# --- render_export_log ----------------------------------------------------------


def test_the_export_log_has_one_entry_naming_no_object(tmp_path: Path) -> None:
    text = bundle_log.render_export_log(date(2026, 10, 5))
    assert text == (
        "# Directory Update Log\n"
        "\n"
        "## 2026-10-05\n"
        "\n"
        "* **Export**: Exported this bundle from an OpenKOS workspace.\n"
    )
    (tmp_path / "log.md").write_text(text, encoding="utf-8")
    assert okf.check_conformance(tmp_path) == []
