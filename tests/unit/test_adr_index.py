"""The ADR index must agree with the ADR files (#920).

`openspec/config.yaml`'s archive rule says an accepted ADR's status is flipped
"in BOTH places: the frontmatter status field and the **Status:** line in the
body", and its row in `docs/adr/README.md` updated to match. That is three
places kept in sync by hand, and the 2026-09-03 documentation truth sweep found
two had already drifted: ADR-0012 was `Accepted` in its file and `Proposed` in
the index, and ADR-0013 carried ADR-0017's partial supersession in the index but
not in its own status.

Prose cannot enforce a three-way sync. This test can.
"""

import re
from pathlib import Path

import pytest

_ADR_DIR = Path(__file__).resolve().parents[2] / "docs" / "adr"
_INDEX = _ADR_DIR / "README.md"


def _adr_files() -> list[Path]:
    return sorted(_ADR_DIR.glob("0[0-9][0-9][0-9]-*.md"))


def _frontmatter_status(text: str) -> str:
    match = re.search(r"^status:\s*(.+?)\s*$", text, re.MULTILINE)
    assert match is not None, "every ADR carries a frontmatter status"
    return match.group(1)


def _body_status(text: str) -> str:
    match = re.search(r"^- \*\*Status:\*\*\s*(.+?)\s*$", text, re.MULTILINE)
    assert match is not None, "every ADR carries a body **Status:** line"
    return match.group(1)


def _index_rows() -> dict[str, str]:
    """`{number: status cell}` for every row of the index table."""
    rows: dict[str, str] = {}
    for line in _INDEX.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*\[(\d{4})\]\([^)]+\)\s*\|([^|]*)\|([^|]*)\|", line)
        if match:
            rows[match.group(1)] = match.group(3).strip()
    return rows


def test_there_is_at_least_one_adr() -> None:
    """Guards every other test here from passing vacuously over an empty glob."""
    assert len(_adr_files()) >= 18


@pytest.mark.parametrize("adr", _adr_files(), ids=lambda p: p.name[:4])
def test_frontmatter_and_body_status_agree(adr: Path) -> None:
    """The two in-file copies must say the same thing."""
    text = adr.read_text(encoding="utf-8")
    front = _frontmatter_status(text)
    body = _body_status(text)

    # The body may carry a trailing clarifying clause the frontmatter omits
    # (ADR-0002: "Superseded in part by ADR-0013 -- the storage clause only"),
    # so the body must START with the frontmatter status rather than equal it.
    assert body.startswith(front), (
        f"{adr.name}: frontmatter says {front!r}, body says {body!r}"
    )


def test_every_adr_has_an_index_row() -> None:
    """A new ADR that never reaches the index is invisible to a reader who
    starts, as intended, from the index."""
    numbered = {adr.name[:4] for adr in _adr_files()}

    assert numbered - set(_index_rows()) == set()


def test_no_index_row_names_a_missing_adr() -> None:
    """And the reverse: the index must not promise a file that is not there."""
    numbered = {adr.name[:4] for adr in _adr_files()}

    assert set(_index_rows()) - numbered == set()


@pytest.mark.parametrize("adr", _adr_files(), ids=lambda p: p.name[:4])
def test_index_status_matches_the_file(adr: Path) -> None:
    """The third copy. This is the pair that had actually drifted."""
    number = adr.name[:4]
    listed = _index_rows()[number]
    front = _frontmatter_status(adr.read_text(encoding="utf-8"))

    assert listed == front, (
        f"ADR-{number}: index says {listed!r}, the file says {front!r}"
    )
