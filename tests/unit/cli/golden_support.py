"""Shared plumbing for the byte-identity characterization goldens of the
findings verbs (issue #1168): normalise the workspace path and compare a run
against the golden recorded on the tree BEFORE the verb moved into its
application service.
"""

import json
import re
from pathlib import Path
from typing import Any

ROOT_PLACEHOLDER = "<ROOT>"
SHA_PLACEHOLDER = "<SHA>"

_COMMIT_SHA = re.compile(r"committed as [0-9a-f]{7,40}")
_REVERT_SHA = re.compile(r"`git revert [0-9a-f]{7,40}`")


def normalise(text: str, root: Path) -> str:
    """Replace the (tmp) workspace path so a golden holds on any machine."""
    return text.replace(str(root.resolve()), ROOT_PLACEHOLDER).replace(
        str(root), ROOT_PLACEHOLDER
    )


def normalise_shas(text: str) -> str:
    """Replace the commit id in every `committed as <hex>` disclosure, and in
    the `git revert <hex>` hint the same sentence carries, so a golden holds
    across runs (a sha depends on the clock and the tmp path)."""
    text = _COMMIT_SHA.sub(f"committed as {SHA_PLACEHOLDER}", text)
    return _REVERT_SHA.sub(f"`git revert {SHA_PLACEHOLDER}`", text)


class Goldens:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, dict[str, Any]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    def check(self, scenario: str, actual: dict[str, Any]) -> None:
        expected = self._data[scenario]
        for key in expected:
            assert actual[key] == expected[key], f"{scenario}: {key}"
        assert set(actual) == set(expected), scenario
