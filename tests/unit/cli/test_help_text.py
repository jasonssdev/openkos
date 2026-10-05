"""`--help` is user-facing text: it states behavior timelessly (AGENTS.md).

History lives in the CHANGELOG and the issue tracker, so a help page that cites
an issue number, or names an internal function, tells the reader something
they cannot act on and that goes stale on the next refactor.
"""

import re
from collections.abc import Iterator

import typer.main
from typer.testing import CliRunner

from openkos.cli.main import app

runner = CliRunner()

_ISSUE_NUMBER = re.compile(r"#\d+")
_INTERNAL_SYMBOL = re.compile(r"\b(?:bundle|openkos)\.[a-z_]+\.[a-z_]+\b")


def _command_paths(
    command: object, path: tuple[str, ...] = ()
) -> Iterator[tuple[str, ...]]:
    yield path
    for name, sub in getattr(command, "commands", {}).items():
        yield from _command_paths(sub, (*path, name))


def _help_pages() -> dict[str, str]:
    root = typer.main.get_command(app)
    pages: dict[str, str] = {}
    for path in _command_paths(root):
        result = runner.invoke(
            app, [*path, "--help"], env={"COLUMNS": "200", "NO_COLOR": "1"}
        )
        assert result.exit_code == 0, path
        pages[" ".join(path) or "openkos"] = " ".join(result.stdout.split())
    return pages


def test_no_help_page_cites_an_issue_number() -> None:
    offenders = {
        name: _ISSUE_NUMBER.findall(page)
        for name, page in _help_pages().items()
        if _ISSUE_NUMBER.search(page)
    }

    assert offenders == {}


def test_no_help_page_names_an_internal_symbol() -> None:
    offenders = {
        name: _INTERNAL_SYMBOL.findall(page)
        for name, page in _help_pages().items()
        if _INTERNAL_SYMBOL.search(page)
    }

    assert offenders == {}


def test_daemon_help_does_not_claim_it_never_changes_the_knowledge_base() -> None:
    """With `unattended.inbox` set the daemon imports Sources and writes their
    concepts, so "never changes your knowledge base" is false."""
    page = _help_pages()["daemon"]

    assert "Never changes your knowledge base" not in page
    assert "unattended.inbox" in page
