"""`config.clear_type_tier` (`review-undo` change, design D6): removing one
type's `type_tiers` entry by the same comment-safe text surgery
`set_type_tier` uses, so every other byte of `openkos.yaml` is kept."""

import pytest

from openkos import config

_CONFIG = (
    "backend: ollama\n"
    "# How quickly facts go out of date, per type.\n"
    "type_tiers:\n"
    "  Person: slow  # people move\n"
    "  # Places rarely change.\n"
    "  Place: static\n"
    "review: true\n"
)


def test_the_entry_is_removed_and_every_other_line_kept() -> None:
    assert config.clear_type_tier(_CONFIG, "Person") == (
        "backend: ollama\n"
        "# How quickly facts go out of date, per type.\n"
        "type_tiers:\n"
        "  # Places rarely change.\n"
        "  Place: static\n"
        "review: true\n"
    )


def test_the_last_entry_leaves_the_header() -> None:
    text = "type_tiers:\n  Person: slow\n"

    cleared = config.clear_type_tier(text, "Person")

    assert cleared == "type_tiers:\n"
    assert config.type_tier_entry(cleared, "Person") is None
    assert config.set_type_tier(cleared, "Person", "slow") == text


def test_a_type_with_no_entry_is_unchanged() -> None:
    assert config.clear_type_tier(_CONFIG, "Event") == _CONFIG


def test_no_block_is_unchanged() -> None:
    assert config.clear_type_tier("backend: ollama\n", "Person") == "backend: ollama\n"


def test_a_crlf_line_is_removed_whole() -> None:
    text = "type_tiers:\r\n  Person: slow\r\n  Place: static\r\n"

    assert (
        config.clear_type_tier(text, "Person") == "type_tiers:\r\n  Place: static\r\n"
    )


@pytest.mark.parametrize(
    "text",
    [
        "type_tiers: {Person: slow}\n",
        "type_tiers: slow\n",
        "type_tiers:\n  Person: slow\ntype_tiers:\n  Place: static\n",
        "type_tiers:\n\tPerson: slow\n",
        "type_tiers:\n  Person: slow &a\n",
    ],
)
def test_a_shape_set_type_tier_cannot_edit_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match=r"openkos\.yaml"):
        config.clear_type_tier(text, "Person")


def test_an_unknown_type_is_refused() -> None:
    with pytest.raises(ValueError, match="not a known concept type"):
        config.clear_type_tier(_CONFIG, "Spaceship")
