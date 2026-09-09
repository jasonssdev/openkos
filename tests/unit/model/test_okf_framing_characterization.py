"""Byte-identity characterization test for OKF on-disk frontmatter framing
(okf-codec-seam WU1, design D4.2), shaped after
`tests/unit/cli/test_ingest_characterization.py`.

The fixture was generated from `bundle.index._split_frontmatter_verbatim`
on the pre-move tree (design D4): each scenario records the exact `text`
fed in and the exact `(block, body)` it produced. Landing this pin BEFORE
WU2's move (proposal Success Criteria) is what makes it falsifiable against
a real divergence rather than a tautology -- see
`test_frontmatter_split_parity.py` for the cross-module half of the same
guarantee, and the apply-progress transcript (task 1.6) for the recorded
falsification.

Falsification (design D4: "a golden that cannot go red is a golden that
proves nothing") was performed manually during apply, NOT as a permanent
test here: `index.py`'s `_FRONTMATTER_RE` was mutated (dropping
`re.DOTALL`), `__pycache__` was purged, `uv run pytest` was run and
confirmed RED against this file, then the mutation was reverted with the
exact inverse edit and `__pycache__` purged again. See the apply-progress
record for the exact transcript.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from openkos.bundle import index as index_module

_GOLDENS_PATH = Path(__file__).parent / "fixtures" / "okf_framing_goldens.json"
_GOLDENS: dict[str, dict[str, Any]] = json.loads(
    _GOLDENS_PATH.read_text(encoding="utf-8")
)


@pytest.mark.parametrize("scenario", sorted(_GOLDENS))
def test_recorded_framing_matches_golden(scenario: str) -> None:
    """Re-running the split against the recorded `text` reproduces the
    recorded `(block, body)` exactly -- this is what makes the golden
    FALSIFIABLE against a real regex/slicing regression (design D4.2),
    unlike a fixture that only checks itself for internal consistency."""
    entry = _GOLDENS[scenario]

    block, body = index_module._split_frontmatter_verbatim(entry["text"])

    assert block == entry["block"]
    assert body == entry["body"]


@pytest.mark.parametrize("scenario", sorted(_GOLDENS))
def test_recorded_framing_satisfies_the_concatenation_invariant(
    scenario: str,
) -> None:
    """`block + body == text` for every scenario -- the framing invariant
    that is falsifiable independently of whether the recorded bytes
    themselves are correct (design D4.2): a JSON fixture truncated or
    mis-split during recording would fail this even if a hand transcription
    error happened to leave `block`/`body` matching each other."""
    entry = _GOLDENS[scenario]

    assert entry["block"] + entry["body"] == entry["text"]
