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
from collections.abc import Iterator
from typing import Any


def _strip_code_fence(raw: str) -> str | None:
    """Parse step: strip a surrounding ``` or ```json fence, if present."""
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    return match.group(1).strip() if match else None


def _embedded_json_values(raw: str) -> Iterator[Any]:
    """Every JSON value that starts at a `[` or `{` in `raw`, in order.

    Replaces the greedy-regex recovery for the embedded case. `\\[.*\\]` with
    DOTALL spans from the FIRST `[` anywhere in the reply to the LAST `]`,
    so any bracketed prose ahead of the payload -- `SUBJECTS: [a, b, c]`,
    a citation, a stray list -- made the block invalid JSON; and the brace
    step failed identically, spanning the first `{` to the last `}` across
    every object. The two together turned a perfectly good three-object
    reply into `[]`. Not a truncated extraction: a destroyed one, and
    downstream indistinguishable from the model answering `[]` (#524).

    `raw_decode` parses ONE value starting at a given offset and stops at
    its end, so each candidate is bounded by its own syntax rather than by
    the last delimiter in the string."""
    decoder = json.JSONDecoder()
    index = 0
    while index < len(raw):
        if raw[index] not in "[{":
            index += 1
            continue
        try:
            value, end = decoder.raw_decode(raw, index)
        except ValueError:
            index += 1
            continue
        yield value
        # Resume AFTER the value just parsed, never inside it: scanning
        # every brace would report `{"a": {"b": 1}}` as two values and make
        # a nested object look like a back-to-back pair.
        index = end


def extract_json_object(raw: object) -> dict[str, Any] | None:
    """3-step fail-closed JSON extraction of a single object: raw
    `json.loads`, then a fenced code block stripped, then a positional scan
    for the first embedded JSON object (`_embedded_json_values`). `None` if
    none of the three steps yields a dict, or if `raw` is not a string
    (fail-closed: a backend that violates the `-> str` contract must not
    crash the parser)."""
    if not isinstance(raw, str):
        return None
    fenced = _strip_code_fence(raw)
    for candidate in (raw, fenced):
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    for value in _embedded_json_values(fenced if fenced is not None else raw):
        if isinstance(value, dict):
            return value
    return None


def extract_json_items(raw: object) -> list[dict[str, Any]]:
    """3-step fail-closed JSON extraction, generalized to a list of candidate
    objects (design D2): raw `json.loads`, then a fenced code block stripped,
    then a positional scan over embedded JSON values
    (`_embedded_json_values`). The first candidate that yields at least one
    dict is used: a JSON array keeps only its dict elements
    (non-dict elements, e.g. stray numbers, are dropped without failing the
    whole reply); a lone top-level JSON object (wrong shape -- not
    array-wrapped) is RECOVERED as a one-item list rather than failing
    closed, since a local LLM routinely emits a lone object for a
    single-object source and that is valid content on a shape technicality,
    not invalid data. `[]` if no step yields a dict or a list containing
    one, or if `raw` is not a string (fail-closed: a backend that
    violates the `-> str` contract must not crash the parser)."""
    if not isinstance(raw, str):
        return []
    fenced = _strip_code_fence(raw)
    for candidate in (raw, fenced):
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
    # Embedded scan. A candidate that yields NO dicts is not an answer, so
    # the scan continues past it: `["a", "b"]` ahead of the payload parses
    # cleanly, and returning its empty dict subset would be a silent
    # zero-object extraction with no error raised anywhere.
    values = list(_embedded_json_values(fenced if fenced is not None else raw))
    for position, value in enumerate(values):
        if isinstance(value, list):
            items = [item for item in value if isinstance(item, dict)]
            if items:
                return items
        elif isinstance(value, dict):
            # D2 recovers a LONE bare object. Two or more back-to-back bare
            # objects stay fail-closed, and that outcome outlives the greedy
            # regex that used to produce it by accident: returning the first
            # of several would be silent truncation reported as success,
            # which is worse than a visible zero.
            following = any(isinstance(later, dict) for later in values[position + 1 :])
            return [] if following else [value]
    return []
