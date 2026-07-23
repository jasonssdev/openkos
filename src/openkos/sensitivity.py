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

`blocks_llm_send` is the ONE fail-closed authority both `sensitive_concept_ids`
(per-bundle walk) and every single-value gate outside a walk (the `ingest`
extract floor gate in `cli/main.py`, and `retrieval/answer.py`'s per-doc
re-check at assemble time) delegate a raw `sensitivity` value to -- introduced
by the correction batch (post-4R-review, FIX 1) after a CONFIRMED fail-open:
`cli/main.py` used to call `okf._rank` directly on `cfg.default_sensitivity`,
so a blank/whitespace `default_sensitivity: ""` in config silently resolved to
`"private"` (via `okf._rank`'s own absent/blank-is-private fallback) and never
tripped the confidential floor gate. `blocks_llm_send` fixes this by treating
absent (`None`) or blank/whitespace-only as blocked BEFORE ever reaching
`okf._rank`, exactly like `sensitive_concept_ids` already did inline; a
present, non-blank value is the only thing ever delegated to `okf._rank`,
which itself already fails closed on an unrecognized string or a non-string
value by ranking it as `"confidential"`.
"""

from pathlib import Path

from openkos.model import okf


def blocks_llm_send(value: object, *, threshold: str = "confidential") -> bool:
    """Return `True` when a raw `sensitivity` `value` must block an
    `llm.chat` send, fail-closed (correction batch, post-4R-review FIX 1).

    `value` is blocked when it is `None`, a blank/whitespace-only string, or
    (once present and non-blank) ranks at or above `threshold` (default
    `"confidential"`) per `okf._rank`. Absent/blank is NEVER delegated to
    `okf._rank` alone: `okf._rank(None)` and `okf._rank("")` both resolve to
    `"private"` (rank 1) -- a fine default for `combine_sensitivity`'s merge
    floor combine, but the WRONG answer here, because a security-relevant
    signal that is simply missing must fail closed (confidential), not
    private. This is the ONE shared authority every fail-closed sensitivity
    gate in this codebase delegates to -- see the module docstring."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    return okf._rank(value) >= okf._rank(threshold)


def sensitive_concept_ids(
    bundle_dir: Path, *, threshold: str = "confidential"
) -> frozenset[str]:
    """Compute the set of concept ids whose effective sensitivity is at or
    above `threshold` (default `"confidential"`) in one `okf._iter_docs`
    walk, never raising.

    A concept id is included when: its document failed to read or parse
    (`scan.read_error`/`scan.parse_error` set); or its `sensitivity`
    frontmatter value -- absent, blank/whitespace, or present -- is blocked
    per `blocks_llm_send` (the shared fail-closed authority; see the module
    docstring)."""
    blocked: set[str] = set()
    for scan in okf._iter_docs(bundle_dir):
        cid = scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
        if scan.read_error is not None or scan.parse_error is not None:
            blocked.add(cid)  # unreadable/unparseable -> fail closed
            continue
        raw = (scan.metadata or {}).get("sensitivity")
        if blocks_llm_send(raw, threshold=threshold):
            blocked.add(cid)
    return frozenset(blocked)
