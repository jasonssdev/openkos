"""The typed parse-failure contract of `okf.load_frontmatter`.

`model/okf.py` is the only module that knows what a frontmatter parse
failure looks like; callers catch `okf.FrontmatterError` instead of a blanket
`Exception`.
"""

import pytest

from openkos.model import okf


def test_frontmatter_error_is_a_value_error() -> None:
    """Sites that already caught `ValueError` keep working unchanged."""
    assert issubclass(okf.FrontmatterError, ValueError)


@pytest.mark.parametrize(
    "text",
    [
        "---\na: [\n---\nbody",  # malformed YAML (ParserError)
        "---\na: 1\n\x00\n---\nbody",  # reader error
        "---\n!!python/object:os.system x\n---\nbody",  # constructor error
        "---\nd: 2020-99-99\n---\nbody",  # library ValueError on a bad date
    ],
)
def test_load_frontmatter_raises_typed_error(text: str) -> None:
    with pytest.raises(okf.FrontmatterError):
        okf.load_frontmatter(text)


def test_load_frontmatter_chains_the_parser_error() -> None:
    with pytest.raises(okf.FrontmatterError) as excinfo:
        okf.load_frontmatter("---\na: [\n---\nbody")
    assert excinfo.value.__cause__ is not None


@pytest.mark.parametrize("text", ["---\n- a\n- b\n---\nbody", "---\nfoo\n---\nbody"])
def test_non_mapping_frontmatter_yields_empty_metadata(text: str) -> None:
    """The library drops a non-mapping block; that semantic is unchanged."""
    metadata, body = okf.load_frontmatter(text)
    assert metadata == {}
    assert body == "body"


def test_unterminated_block_still_returns_empty_metadata() -> None:
    metadata, _ = okf.load_frontmatter("---\na: 1\nb")
    assert metadata == {}


def test_try_load_frontmatter_returns_none_on_failure() -> None:
    assert okf.try_load_frontmatter("---\na: [\n---\nbody") is None


def test_try_load_frontmatter_returns_the_parse_on_success() -> None:
    assert okf.try_load_frontmatter("---\na: 1\n---\nbody") == ({"a": 1}, "body")


def test_parse_frontmatter_fragment_raises_typed_error() -> None:
    with pytest.raises(okf.FrontmatterError):
        okf.parse_frontmatter_fragment("title: [\n")
    assert okf.parse_frontmatter_fragment("title: x\n") == {"title": "x"}


def test_rewrite_okf_version_flips_only_the_version_line() -> None:
    text = "---\ntype: Index\nokf_version: '0.1'\nz: 'keep'\n---\n# body\n"
    out = okf.rewrite_okf_version(text, label="t")
    assert out == "---\ntype: Index\nokf_version: '0.2'\nz: 'keep'\n---\n# body\n"
    assert okf.rewrite_okf_version(out, label="t") == out


def test_rewrite_okf_version_refuses_without_the_field() -> None:
    with pytest.raises(ValueError, match="t: .*no okf_version"):
        okf.rewrite_okf_version("---\ntype: Index\n---\nb", label="t")


def test_rewrite_okf_version_refuses_without_a_block() -> None:
    with pytest.raises(ValueError, match="t: missing or malformed"):
        okf.rewrite_okf_version("no block", label="t")


def test_okf_version_is_current() -> None:
    assert okf.okf_version_is_current({"okf_version": okf.OKF_VERSION})
    assert not okf.okf_version_is_current({"okf_version": "0.1"})
    assert not okf.okf_version_is_current({})
