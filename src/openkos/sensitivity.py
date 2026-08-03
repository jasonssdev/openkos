"""Canonical-layer fail-closed sensitivity predicate
(sensitivity-fail-closed-filter, MVP-3 gap #8 · S3).

`should_block` (directory-walk-observability follow-up, correction batch
post-4R-review readability FIX 1 -- distinct from the unrelated
`blocks_llm_send` FIX 1 described below, which belongs to an earlier
sensitivity-fail-closed-filter correction) is the ONE shared fail-closed
AUTHORITY every send-time re-check delegates its DECISION to. Before this,
the identical inline expression `not include_confidential and
sensitivity.blocks_llm_send(metadata.get("sensitivity"))` was independently
copy-pasted at 5 call sites (`retrieval/answer.py`, `resolution/
{contradiction,edge_typing,adjudication,volatility_typing}.py`) -- a future
edit to the semantic (e.g. a new escape hatch, a different metadata key) had
to land identically in all 5 places, and a partial edit would silently
fail open at whichever site was missed. Centralizing the DECISION here
means each site still owns its own ACTION on block (skip/degrade/continue/
filter), but the boolean predicate itself has exactly one source of truth.

Issue #240 is the first real test of that centralization, and it held: the
`confidential` gate became conditional on the `llm.chat` backend being
verifiably this machine, and the resulting two-term disjunction
(`include_confidential or local_exemption`) landed in exactly TWO functions
here rather than in five call sites. Each site still passes its own
`local_exemption` boolean -- only the site knows which client it is about to
send through -- but no site restates what the booleans MEAN. The escape
hatch `sensitive_concept_ids` gained is the same one: its callers' guarding
`if not include_confidential:` moved inside, so the walk-skip stays free
without five copies of the condition.

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

from collections.abc import Mapping
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


def should_block(
    metadata: Mapping[str, object],
    *,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> bool:
    """Return `True` when a send-time re-check of `metadata` (a parsed
    document's frontmatter) must block that document from an `llm.chat`
    payload -- the single fail-closed authority every per-doc re-check in
    this codebase shares (directory-walk-observability follow-up, correction
    batch post-4R-review readability FIX 1; see the module docstring for the
    5-way duplication this replaces).

    `include_confidential` is the caller's own opt-in escape hatch (e.g. the
    CLI's `--include-confidential` flag): it is OPT-IN, never the default,
    because the whole point of this predicate is that a doc's sensitivity
    is verified independently of whatever a directory walk may have missed
    -- an escape hatch that defaulted to bypassing would defeat that
    guarantee for every caller that forgot to pass it explicitly. When
    `True`, this always returns `False` (never blocked), restoring
    byte-identical pre-filter behavior. Otherwise, delegates to
    `blocks_llm_send` against `metadata.get("sensitivity")` -- fail-closed
    on a missing/blank/unrecognized value, exactly as before
    centralization.

    `local_exemption` is the SECOND escape hatch (issue #240) and it is a
    different KIND of thing from the first: `include_confidential` is a
    human overriding the policy, while `local_exemption` is the caller
    asserting the policy has nothing to protect, because the `llm.chat`
    backend this send will actually reach is verifiably this machine and no
    content leaves it. `sensitivity` governs EGRESS; a local backend is not
    egress. The caller -- never this module -- owns proving that: it passes
    `client.locality.is_local and cfg.confidential_local_exemption`, so both
    the verified fact and the workspace's opt-out are folded in before the
    boolean arrives here.

    It defaults to `False`, and that default is load-bearing rather than
    stylistic. This predicate is reached from five independent seams; a
    `True` default would grant the exemption to every seam that had not yet
    been taught to compute locality, converting "forgot to thread a
    parameter" into "sent a confidential concept to a remote host". Forgetting
    the parameter must cost nothing worse than today's blanket blocking.

    The two hatches are a DISJUNCTION: either alone releases the block, so
    `--include-confidential` keeps working unchanged against a remote
    backend while a local backend needs no flag at all. The disjunction lives
    HERE and nowhere else, for the same reason the module docstring gives for
    centralizing the original predicate -- a copy of it at five call sites is
    five places a future edit can be half-applied, and the half that is
    missed fails OPEN."""
    if include_confidential or local_exemption:
        return False
    return blocks_llm_send(metadata.get("sensitivity"))


def sensitive_concept_ids(
    bundle_dir: Path,
    *,
    threshold: str = "confidential",
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> frozenset[str]:
    """Compute the set of concept ids whose effective sensitivity is at or
    above `threshold` (default `"confidential"`) in one `okf._iter_docs`
    walk, never raising.

    A concept id is included when: its document failed to read or parse
    (`scan.read_error`/`scan.parse_error` set); or its `sensitivity`
    frontmatter value -- absent, blank/whitespace, or present -- is blocked
    per `blocks_llm_send` (the shared fail-closed authority; see the module
    docstring).

    `include_confidential` and `local_exemption` are the same two escape
    hatches `should_block` documents, moved INTO this function by issue
    #240. They used to live at each call site as a guarding
    `if not include_confidential:` around the call; with a second hatch to
    consider, that shape would have put the same two-term disjunction in
    five places, which is exactly the duplication the module docstring
    exists to argue against. Both default to `False` for the same
    fail-closed reason `should_block` gives.

    Either hatch returns `frozenset()` IMMEDIATELY, before `okf._iter_docs`
    is touched, preserving the zero-cost bypass those call-site guards
    provided: a caller that will exclude nothing must not pay for a walk of
    the whole bundle to learn it."""
    if include_confidential or local_exemption:
        return frozenset()
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
