"""Canonical-layer fail-closed sensitivity predicate
(sensitivity-fail-closed-filter, MVP-3 gap #8 · S3).

`sensitive_concept_ids` is the ONE shared predicate every `llm.chat`-calling
seam (`query`, `contradictions`, `adjudicate`, `suggest-relations`,
`suggest-volatility`) filters against before sending concept content to an
LLM -- see `openspec/changes/sensitivity-fail-closed-filter/design.md`. It
imports only `openkos.model.okf` + stdlib, a package-root leaf like
`lifecycle.py`/`lint.py`/`config.py`: every consumer depends on it with no
cycle.

This is a DISTINCT, deliberately separate leaf from `lifecycle.py`, not an
extension of it: `lifecycle.deprecated_concept_ids` fails **safe** (skip on
doubt -- an unreadable/unparseable document contributes no status and is
silently ignored), whereas `sensitive_concept_ids` fails **closed** (block on
doubt -- an unreadable/unparseable document, or any doubtful signal at all,
is treated as the MOST restrictive sensitivity and excluded). Co-locating
two opposite fail-directions in one module would invite a future edit to
cross the invariant; `lifecycle.filter_hits` stays where it is and is reused
here verbatim (its `deprecated` parameter is just a `frozenset[str]` of
excluded ids, axis-agnostic).

A concept is blocked (would resolve to confidential-or-more-restrictive) iff:
its document could not be read or its frontmatter could not be parsed; its
`sensitivity` frontmatter field is absent; its `sensitivity` value is
blank/whitespace-only; or `okf._rank(raw) >= okf._rank(threshold)` for the
raw, present, non-blank value. This is STRICTER than delegating absent/blank
values to `okf._rank` alone: `okf._rank(None)` and `okf._rank("")` both
resolve to `"private"` (rank 1) -- a fine default for `combine_sensitivity`'s
merge floor combine, but the WRONG answer here, because a security-relevant
signal that is simply missing must fail closed (confidential), not
private. Only a PRESENT, non-blank raw value is ever delegated to `_rank`,
which itself already fails closed on an unrecognized string or a non-string
value by ranking it as `"confidential"`.
"""

from pathlib import Path

from openkos.model import okf


def sensitive_concept_ids(
    bundle_dir: Path, *, threshold: str = "confidential"
) -> frozenset[str]:
    """Compute the set of concept ids whose effective sensitivity is at or
    above `threshold` (default `"confidential"`) in one `okf._iter_docs`
    walk, never raising.

    A concept id is included when: its document failed to read or parse
    (`scan.read_error`/`scan.parse_error` set); its `sensitivity` frontmatter
    field is absent or blank/whitespace-only; or its present, non-blank raw
    value ranks at or above `threshold` per `okf._rank` (which itself fails
    closed toward confidential on an unrecognized or non-string value). See
    the module docstring for why absent/blank is NOT delegated to `_rank`
    directly."""
    floor = okf._rank(threshold)
    blocked: set[str] = set()
    for scan in okf._iter_docs(bundle_dir):
        cid = scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
        if scan.read_error is not None or scan.parse_error is not None:
            blocked.add(cid)  # unreadable/unparseable -> fail closed
            continue
        raw = (scan.metadata or {}).get("sensitivity")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            blocked.add(cid)  # absent/blank -> fail closed, never delegated to _rank
            continue
        if okf._rank(raw) >= floor:
            blocked.add(cid)
    return frozenset(blocked)
