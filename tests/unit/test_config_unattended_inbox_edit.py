"""`config.set_unattended_inbox`: comment-safe text surgery on the one
`unattended.inbox` line, and the typed reasons `unattended.inbox` is refused
for (ADR-0078)."""

from collections.abc import Callable
from pathlib import Path

import pytest

from openkos import config
from openkos.model import okf

BASE = "model: gemma3\nreview: true\n"


def _inbox(text: str) -> object:
    raw = okf.load_unique_key_yaml(text)
    assert isinstance(raw, dict)
    section = raw.get("unattended")
    return section.get("inbox") if isinstance(section, dict) else None


def test_an_absent_section_gets_a_new_one_at_the_end() -> None:
    out = config.set_unattended_inbox(BASE, "/Users/me/Inbox")

    assert out == BASE + 'unattended:\n  inbox: "/Users/me/Inbox"\n'


def test_a_missing_final_newline_is_added_before_the_section() -> None:
    out = config.set_unattended_inbox("model: gemma3", "/in")

    assert out == 'model: gemma3\nunattended:\n  inbox: "/in"\n'


def test_a_commented_template_section_is_not_a_live_section() -> None:
    text = BASE + "# unattended:\n#   inbox: ../inbox\n"

    out = config.set_unattended_inbox(text, "/in")

    assert out.startswith(text)
    assert _inbox(out) == "/in"


def test_an_existing_line_is_replaced_and_its_neighbours_stay() -> None:
    text = (
        BASE + "unattended:\n  quiet_seconds: 5\n  inbox: ../old  # mine\n"
        "  max_calls_per_pass: 3\nother: 1\n"
    )

    out = config.set_unattended_inbox(text, "/new")

    assert out == (
        BASE + 'unattended:\n  quiet_seconds: 5\n  inbox: "/new"\n'
        "  max_calls_per_pass: 3\nother: 1\n"
    )


def test_a_section_without_the_key_gets_it_inside_the_section() -> None:
    text = BASE + "unattended:\n  quiet_seconds: 5\nother: 1\n"

    out = config.set_unattended_inbox(text, "/in")

    assert out == BASE + 'unattended:\n  quiet_seconds: 5\n  inbox: "/in"\nother: 1\n'


def test_an_empty_flow_section_is_opened() -> None:
    out = config.set_unattended_inbox(BASE + "unattended: {}\n", "/in")

    assert out == BASE + 'unattended:\n  inbox: "/in"\n'


def test_removing_deletes_only_that_line() -> None:
    text = BASE + 'unattended:\n  quiet_seconds: 5\n  inbox: "/in"\nother: 1\n'

    out = config.set_unattended_inbox(text, None)

    assert out == BASE + "unattended:\n  quiet_seconds: 5\nother: 1\n"


def test_removing_what_is_absent_changes_nothing() -> None:
    assert config.set_unattended_inbox(BASE, None) == BASE


def test_a_path_YAML_would_misread_round_trips() -> None:
    for path in ("/a b/#c: d", '/q"uote', "/ünï/dir", "/back\\slash"):
        assert _inbox(config.set_unattended_inbox(BASE, path)) == path


def test_crlf_line_endings_are_kept() -> None:
    text = "model: gemma3\r\nunattended:\r\n  inbox: /old\r\n"

    out = config.set_unattended_inbox(text, "/new")

    assert out == 'model: gemma3\r\nunattended:\r\n  inbox: "/new"\r\n'


def test_two_inbox_lines_are_refused() -> None:
    text = BASE + "unattended:\n  inbox: /a\n  inbox: /b\n"

    with pytest.raises(ValueError, match="inbox"):
        config.set_unattended_inbox(text, "/c")


def test_a_non_block_section_is_refused() -> None:
    with pytest.raises(ValueError, match="unattended"):
        config.set_unattended_inbox(BASE + "unattended: [1]\n", "/c")


def test_the_live_lines_read_the_value_or_none() -> None:
    assert config.unattended_inbox_line(BASE) is None
    text = BASE + 'unattended:\n  inbox: "/x"  # c\n'
    assert config.unattended_inbox_line(text) == '"/x"  # c'


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda root: "   ", "invalid"),
        (lambda root: str(root / "raw"), "inside_workspace"),
        (lambda root: str(root / "bundle" / "x"), "inside_workspace"),
        (lambda root: str(root), "inside_workspace"),
        (lambda root: str(root.parent), "inside_workspace"),
        (lambda root: str(root.parent / "nope"), "not_a_directory"),
    ],
)
def test_a_refused_inbox_carries_its_reason(
    tmp_path: Path,
    make: Callable[[Path], str],
    reason: str,
) -> None:
    root = tmp_path / "ws"
    (root / "raw").mkdir(parents=True)
    (root / "bundle" / "x").mkdir(parents=True)
    (root / "openkos.yaml").write_text(
        f'model: gemma3\nunattended:\n  inbox: "{make(root)}"\n', encoding="utf-8"
    )

    with pytest.raises(config.InboxRejected) as caught:
        config.read_config(root)

    assert caught.value.reason == reason
    assert isinstance(caught.value, ValueError)


def test_a_section_ending_without_a_newline_is_closed_before_the_key() -> None:
    text = "model: gemma3\nunattended:\n  quiet_seconds: 5"

    out = config.set_unattended_inbox(text, "/in")

    assert out == 'model: gemma3\nunattended:\n  quiet_seconds: 5\n  inbox: "/in"\n'
