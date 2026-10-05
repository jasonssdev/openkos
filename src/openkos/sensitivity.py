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

`merged_content_blocked` (surface-merged-body-contradictions, issue #409) is a
THIRD, deliberate gate -- not an accidental duplicate of the two above. It
closes a gap neither `should_block` nor `sensitive_concept_ids` can see: both
read only the CURRENT on-disk `sensitivity` value, but a merge survivor's
`MergeLedgerEntry.absorbed_snapshot` embeds a full historical document body
that may have been written at a HIGHER sensitivity than the survivor's
current value now reads. The induction "current sensitivity dominates every
absorbed body, because `combine_sensitivity` only ever raises it" holds
across merges but breaks across `set-sensitivity`, which can deliberately
LOWER a concept's sensitivity (shipped ADR-0008 behavior, permitted on an
interactive confirm or with `--allow-downgrade`). A survivor that absorbed a
`confidential` document and was later downgraded to `public` still has that
confidential body sitting in its ledger; gating on the current value alone
would ship it to the LLM. `merged_content_blocked` closes this by ranking
the max of the survivor's current sensitivity AND the entry's own frozen
`sensitivity_before`/`sensitivity_after` -- per ledger entry, since a later
downgrade can lower the current value below what one specific historical
entry established. It composes with `include_confidential`/`local_exemption`
identically to `should_block`/`sensitive_concept_ids`. This is a sibling
gate, not a fourth accidental copy of the same authority: it exists because
the OTHER two gates are structurally incapable of inspecting `merged_from`.

`blocks_disclosure` and `disclosable_concept_ids` (mcp-read-surface, #1010,
ADR-0028) gate a SECOND, deliberately separate boundary: disclosure to a
non-LLM consumer (an MCP client), not an `llm.chat` send. Every predicate
above protects LLM egress and offers `include_confidential`/
`local_exemption` hatches; neither has an honest meaning here, because a
tool result usually leaves this process toward a remote model provider the
server cannot observe, and there is no per-request human to grant
`include_confidential`, and a stdio peer's locality says nothing about
where it forwards what it received. `blocks_disclosure` therefore takes
exactly one policy input -- the consuming surface's launch-time opt-in,
`expose_confidential` -- and reuses `blocks_llm_send`'s identical
fail-closed rank unchanged. `disclosable_concept_ids` is an ALLOWED set,
the mirror image of `sensitive_concept_ids`'s BLOCKED set: a blocked set
fails open for any id the walk never reached (a dangling reference, a file
created after the walk), while an allowed set withholds all of them by
construction, because it is built only from ids the walk actually saw.

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
from dataclasses import dataclass
from enum import StrEnum
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


def _fail_closed_rank(value: object) -> int:
    """Rank a raw sensitivity `value` fail-closed, treating a missing
    (`None`) or blank/whitespace-only string as `"confidential"` rather than
    `okf._rank`'s own absent-value default of `"private"` -- the SAME
    fail-closed override `blocks_llm_send` already applies before ever
    delegating to `okf._rank` (see that function's docstring for why a
    security-relevant signal that is simply missing must fail toward the
    MOST restrictive level, not the merge-combine default). A present,
    non-blank value is delegated to `okf._rank` unchanged, which itself
    already fails closed on an unrecognized string or non-string value."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return okf.SENSITIVITY_ORDER.index("confidential")
    return okf._rank(value)


def merged_content_blocked(
    current_sensitivity: object,
    entry: okf.MergeLedgerEntry,
    *,
    threshold: str = "confidential",
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> bool:
    """Ledger-aware fail-closed gate for ONE intra-document (merged-body)
    contradiction candidate (surface-merged-body-contradictions, Correction
    1): `True` when `entry`'s absorbed body must NOT reach an `llm.chat`
    payload.

    Ranks (fail-closed, via `_fail_closed_rank`) the MAX of three values and
    compares it against `threshold` (default `"confidential"`, `okf._rank`
    semantics):

    1. `current_sensitivity` -- the survivor's CURRENT on-disk sensitivity,
       covering anything established by a merge or a raise that happened
       AFTER this entry;
    2. `entry.sensitivity_before` -- the survivor's own level immediately
       before THIS specific merge, frozen in the ledger and never mutated by
       a later `set-sensitivity` downgrade;
    3. `entry.sensitivity_after` -- `plan_merge` sets this to
       `combine_sensitivity(survivor, absorbed)` at merge time, so it
       already dominates the absorbed document's own original sensitivity
       by construction -- no need to reparse `entry.absorbed_snapshot`'s
       frontmatter.

    This MUST be called once PER LEDGER ENTRY, never once per survivor: a
    later downgrade can have lowered `current_sensitivity` below what one
    specific historical entry established, and that entry's own frozen
    values are an independent floor a caller must not skip by checking only
    the survivor's current value or only its most recent entry.

    `entry.sensitivity_before` uses `""` as the sentinel for "survivor had
    no `sensitivity` key at merge time" (`MergeLedgerEntry`'s docstring);
    `_fail_closed_rank` treats it identically to a missing value -- the most
    restrictive rank, never `okf._rank`'s absent-default of private.

    `include_confidential`/`local_exemption` compose identically to
    `should_block`/`sensitive_concept_ids`'s own contract: either hatch
    short-circuits to `False` (never blocked) IMMEDIATELY, before any
    ledger field is inspected. No new escape-hatch semantics."""
    if include_confidential or local_exemption:
        return False
    threshold_rank = okf._rank(threshold)
    combined_rank = max(
        _fail_closed_rank(current_sensitivity),
        _fail_closed_rank(entry.sensitivity_before),
        _fail_closed_rank(entry.sensitivity_after),
    )
    return combined_rank >= threshold_rank


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
        cid = okf.concept_id_for(scan.path, bundle_dir)
        if scan.read_error is not None or scan.parse_error is not None:
            blocked.add(cid)  # unreadable/unparseable -> fail closed
            continue
        raw = (scan.metadata or {}).get("sensitivity")
        if blocks_llm_send(raw, threshold=threshold):
            blocked.add(cid)
    return frozenset(blocked)


def blocks_disclosure(value: object, *, expose_confidential: bool = False) -> bool:
    """Return `True` when a raw `sensitivity` `value` must NOT be disclosed
    to a non-LLM consumer (an MCP client) -- the disclosure boundary's
    per-value check (mcp-read-surface, #1010, ADR-0028).

    Delegates to `blocks_llm_send` unchanged, so absent, blank,
    whitespace-only, unrecognized and non-string values all block, exactly
    like an explicit `confidential` value -- the SAME fail-closed rank the
    LLM-egress boundary uses, applied at a second boundary.

    `expose_confidential` is the ONE policy input this predicate accepts: the
    consuming surface's launch-time opt-in (e.g. `openkos mcp
    --expose-confidential`). When `True`, this always returns `False`
    (never blocked) -- there is deliberately no `include_confidential` or
    `local_exemption` parameter, because neither escape hatch has an honest
    meaning for disclosure to a program rather than a human-directed
    `llm.chat` send (see the module docstring)."""
    if expose_confidential:
        return False
    return blocks_llm_send(value)


def disclosable_concept_ids(
    bundle_dir: Path, *, expose_confidential: bool = False
) -> frozenset[str]:
    """Compute the ALLOWED set of concept ids that MAY be disclosed to a
    non-LLM consumer, in one `okf._iter_docs` walk, never raising
    (mcp-read-surface, #1010, ADR-0028).

    This is the mirror image of `sensitive_concept_ids`'s BLOCKED set, and
    the direction is deliberate, not stylistic: a blocked set fails OPEN for
    any id the walk never reached (a dangling provenance target, a file
    created after the walk, a subtree the walk could not list), because an
    id absent from a blocked set reads as "not blocked". An allowed set
    withholds every such id by construction, because it is built only from
    ids the walk actually saw and could verify.

    A document that failed to read or parse
    (`scan.read_error`/`scan.parse_error` set) is excluded unless
    `expose_confidential` is `True` -- its value cannot be verified, so it
    ranks confidential, exactly like `blocks_llm_send`'s own fail-closed
    ranking. Any other document is included when
    `not blocks_disclosure(meta.get("sensitivity"), expose_confidential=
    expose_confidential)`.

    Under `expose_confidential=True`, every WALKED id is included -- even
    the unreadable and unparseable ones, because the policy then discloses
    every rank. A dangling id is still absent even then: there is no
    document behind it for the walk to have included in the first place,
    so `expose_confidential` has nothing to disclose (ADR-0028's recorded
    consequence)."""
    allowed: set[str] = set()
    for scan in okf._iter_docs(bundle_dir):
        cid = okf.concept_id_for(scan.path, bundle_dir)
        if scan.read_error is not None or scan.parse_error is not None:
            if expose_confidential:
                allowed.add(cid)
            continue
        raw = (scan.metadata or {}).get("sensitivity")
        if not blocks_disclosure(raw, expose_confidential=expose_confidential):
            allowed.add(cid)
    return frozenset(allowed)


class ExportReason(StrEnum):
    """Why `export_boundary` withheld a concept (okf-export, #1301).

    Decided in this precedence order -- the first that applies wins -- so a
    document is never admitted by falling through to a later check."""

    UNREADABLE = "unreadable"
    """The document could not be read or parsed, is a symlink, or carries no
    non-empty `type`: it is not a conformant concept to export."""
    INCOMPLETE = "incomplete-ingest"
    """It carries `okf.INGEST_PENDING_KEY`: its compilation never finished."""
    UNLABELLED = "unlabelled"
    """Its `sensitivity` is absent, blank, non-string or unrecognized."""
    CONFIDENTIAL = "confidential"
    """`confidential` never leaves the device, under any flag."""
    PRIVATE = "private"
    """`private`, and the run was not given `--include-private`."""
    BELOW_SOURCE = "below-source"
    """Its own label ranks below one of its provenance ancestors', and the run
    was not given `--allow-below-source` (ADR-0048)."""


@dataclass(frozen=True)
class ExportBoundary:
    """One `export_boundary` result.

    `allowed` is an ALLOWED set: only ids the call was given and admitted.
    `withheld` maps every other id it was given to its reason. `below_source`
    lists, sorted, every id the below-source rule decided -- withheld without
    `allow_below_source`, admitted with it -- so a preview can name each."""

    allowed: frozenset[str]
    withheld: Mapping[str, ExportReason]
    below_source: tuple[str, ...]


_CONFIDENTIAL_RANK = okf.SENSITIVITY_ORDER.index("confidential")
_PRIVATE_RANK = okf.SENSITIVITY_ORDER.index("private")
_PUBLIC_RANK = okf.SENSITIVITY_ORDER.index("public")


def _export_label_rank(value: object) -> int | None:
    """The rank of a canonical label, or `None` for a doubtful one.

    Stricter than `okf._rank` on purpose: that ranks an absent label
    `private` (ADR-0003's combine floor), the wrong answer at a boundary --
    a missing label is a doubtful signal and must not leave."""
    if isinstance(value, str) and value.strip() in okf.SENSITIVITY_ORDER:
        return okf.SENSITIVITY_ORDER.index(value.strip())
    return None


def _provenance_parents(metadata: Mapping[str, object]) -> frozenset[str] | None:
    """The concept ids `metadata`'s `provenance` names, or `None` when the
    field is present but malformed (a non-list, or any entry that is not a
    non-empty string). Entries under `raw/` are workspace paths, not concept
    ids, and are skipped; a leading `/` and a trailing `.md` are removed."""
    raw = metadata.get("provenance")
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        return None
    parents: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            return None
        if entry.startswith("raw/"):
            continue
        parents.add(entry.removeprefix("/").removesuffix(".md"))
    return frozenset(parents)


def _ancestor_ceiling(
    concept_id: str, docs: Mapping[str, Mapping[str, object] | None]
) -> int | None:
    """The highest fail-closed rank among `concept_id`'s transitive provenance
    ancestors that are documents in `docs`, `-1` when it has none, or `None`
    when its own or any ancestor's `provenance` is malformed.

    An ancestor that is unreadable (`None`) or carries a doubtful label ranks
    `confidential`. An id naming no document in `docs` contributes nothing."""
    ceiling = -1
    seen: set[str] = {concept_id}
    frontier = [concept_id]
    while frontier:
        meta = docs.get(frontier.pop())
        if meta is None:
            continue
        parents = _provenance_parents(meta)
        if parents is None:
            return None
        for parent in parents:
            if parent in seen or parent not in docs:
                continue
            seen.add(parent)
            parent_meta = docs[parent]
            rank = (
                None
                if parent_meta is None
                else _export_label_rank(parent_meta.get("sensitivity"))
            )
            ceiling = max(ceiling, _CONFIDENTIAL_RANK if rank is None else rank)
            frontier.append(parent)
    return ceiling


def export_boundary(
    docs: Mapping[str, Mapping[str, object] | None],
    *,
    include_private: bool,
    allow_below_source: bool,
) -> ExportBoundary:
    """Decide which concept ids may leave the device in an `openkos export`
    (okf-export, #1301; ADR-0048): the export boundary's predicate, a third
    boundary beside LLM egress and MCP disclosure (ADR-0028).

    `docs` maps every concept id of ONE walk of the bundle to its parsed
    frontmatter, or `None` when the document could not be read or parsed.
    The function is pure over that map, so the caller's drift check compares
    exactly the bytes this function judged.

    It returns an ALLOWED set: an id is admitted only when its document is
    readable with a non-empty `type`, is not mid-ingest, and carries a
    canonical label that is `public`, or `private` with `include_private`.
    `confidential` and every doubtful label are withheld under every flag;
    there is deliberately no parameter that admits `confidential` (the
    knowledge object model: "excluded from exports and sharing").

    An otherwise-admitted object whose own rank sits below the highest rank
    among its provenance ancestors is withheld as `BELOW_SOURCE` unless
    `allow_below_source`: a person may have lowered it on purpose
    (ADR-0008), or a machine label may be stale, and the flag is how the
    person says which. A malformed `provenance` cannot prove the object is
    not below its sources, so it counts as below them."""
    ceiling = _PRIVATE_RANK if include_private else _PUBLIC_RANK
    allowed: set[str] = set()
    withheld: dict[str, ExportReason] = {}
    below_source: list[str] = []
    for concept_id, meta in docs.items():
        doc_type = None if meta is None else meta.get("type")
        if meta is None or not isinstance(doc_type, str) or not doc_type.strip():
            withheld[concept_id] = ExportReason.UNREADABLE
            continue
        if meta.get(okf.INGEST_PENDING_KEY):
            withheld[concept_id] = ExportReason.INCOMPLETE
            continue
        rank = _export_label_rank(meta.get("sensitivity"))
        if rank is None:
            withheld[concept_id] = ExportReason.UNLABELLED
            continue
        if rank >= _CONFIDENTIAL_RANK:
            withheld[concept_id] = ExportReason.CONFIDENTIAL
            continue
        if rank > ceiling:
            withheld[concept_id] = ExportReason.PRIVATE
            continue
        ancestors = _ancestor_ceiling(concept_id, docs)
        if ancestors is None or rank < ancestors:
            below_source.append(concept_id)
            if not allow_below_source:
                withheld[concept_id] = ExportReason.BELOW_SOURCE
                continue
        allowed.add(concept_id)
    return ExportBoundary(
        allowed=frozenset(allowed),
        withheld=withheld,
        below_source=tuple(sorted(below_source)),
    )
