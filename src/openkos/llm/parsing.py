"""Shared fail-closed JSON extraction for LLM chat replies.

This module is a leaf: stdlib `json`/`re`/`typing` only, no import of
`openkos.config` or any other `openkos` module -- same constraint as
`openkos.llm.base`.

Every LLM-facing seam that expects a JSON reply (adjudication, edge typing,
volatility typing, contradiction detection, and concept extraction) used to
carry its own byte-identical module-local copy of this parsing logic,
justified at the time by a "no cross-import of `_`-prefixed symbols" design
note. This module resolves that by exposing PUBLIC `extract_json_object`/
`extract_json_items` functions instead of private ones, so every call site
can import and share one implementation without violating that note's
intent (only cross-imports of `_`-prefixed, module-private symbols were
disallowed).
"""

import json
import re
from typing import Any


def _strip_code_fence(raw: str) -> str | None:
    """Parse step: strip a surrounding ``` or ```json fence, if present."""
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    return match.group(1).strip() if match else None


def _first_brace_block(raw: str) -> str | None:
    """Parse step: return the first `{...}` block found anywhere in `raw`,
    if any (recovers a JSON object embedded in surrounding prose)."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return match.group(0) if match else None


def _first_bracket_block(raw: str) -> str | None:
    """Parse step: return the first `[...]` block found anywhere in `raw`,
    if any (recovers a JSON array embedded in surrounding prose)."""
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    return match.group(0) if match else None


def extract_json_object(raw: object) -> dict[str, Any] | None:
    """3-step fail-closed JSON extraction of a single object: raw
    `json.loads`, then a fenced code block stripped, then the first
    `{...}` block. The first candidate that parses to a `dict` is used.
    `None` if none of the three steps yields a dict, or if `raw` is not a
    string (fail-closed: a backend that violates the `-> str` contract must
    not crash the parser)."""
    if not isinstance(raw, str):
        return None
    for candidate in (raw, _strip_code_fence(raw), _first_brace_block(raw)):
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def extract_json_items(raw: object) -> list[dict[str, Any]]:
    """4-step fail-closed JSON extraction, generalized to a list of candidate
    objects (design D2): raw `json.loads`, then a fenced code block stripped,
    then the first `[...]` block, then the first `{...}` block. The first
    candidate that parses is used: a JSON array keeps only its dict elements
    (non-dict elements, e.g. stray numbers, are dropped without failing the
    whole reply); a lone top-level JSON object (wrong shape -- not
    array-wrapped) is RECOVERED as a one-item list rather than failing
    closed, since a local LLM routinely emits a lone object for a
    single-object source and that is valid content on a shape technicality,
    not invalid data. `[]` if none of the four steps yields a list or
    object, or if `raw` is not a string (fail-closed: a backend that
    violates the `-> str` contract must not crash the parser)."""
    if not isinstance(raw, str):
        return []
    for candidate in (
        raw,
        _strip_code_fence(raw),
        _first_bracket_block(raw),
        _first_brace_block(raw),
    ):
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    return []
