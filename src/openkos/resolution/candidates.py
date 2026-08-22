"""Read-only, whole-bundle entity-resolution candidate generation.

`find_candidates` walks a bundle via `okf._iter_docs` (D2, the SAME
enumerate/skip pattern as `state/fts.py`/`graph/sqlite_graph.py`),
collects non-Source concept documents (`_eligible_keyed_docs`), and
proposes candidate GROUPS via three deterministic, stdlib-only tiers:
HIGH (an exact shared `normalize.normalize_key`, bucketed ACROSS all
declared OKF types -- issue #437), ACRONYM (`similarity.
acronym_expansion_match`), and LOW (a `similarity.is_near_match`,
excluding any pair already HIGH or ACRONYM). ACRONYM and LOW stay
per-type, with two exemptions: HIGH is not partitioned by type at all
(#437), and a pair one document's own `type_alternative` bridges is
admitted to the ACRONYM/LOW comparison the partition would otherwise have
prevented (#804, `_bridged_cross_type_pairs`). Output is ephemeral -- frozen dataclasses only, never a persisted OKF type or
`bundle`/`state` file -- and this module never writes a byte of the
bundle.

Cross-type exact-title bucketing (#437): two documents of DIFFERENT
declared OKF types whose titles normalize identically now form ONE HIGH
candidate group, carrying both types via `CandidateGroup.member_types`
(index-aligned with `member_ids`) and a joined display label on
`okf_type` (e.g. `"Concept+Entity"`, sorted and `+`-joined -- see
`_type_label`). This is a deliberate, narrow exemption to the ACRONYM/LOW
per-type rule: HIGH's zero-cost bucket-by-key shape extends naturally
across types with no pairwise cost, while ACRONYM/LOW's pairwise passes
stay scoped per type for cost-profile reasons.

Bridged cross-type pairs (#804): two documents describing one thing under
two names AND two types fall through every tier by construction -- HIGH
needs an exact shared key, and the partition hides them from the fuzzy
tiers that exist for exactly this case. Where the extractor wrote down the
uncertainty that justifies comparing them (`type_alternative` naming the
other's declared type), the pair is compared. The comparison is unchanged;
only its membership widens.

`find_exact_title_groups` (issue #216) is the same pass with the
ACRONYM/LOW tiers left out: it returns exactly the `Tier.HIGH` groups
`find_candidates` would return, in the same order, without ever running
the O(n^2) pairwise `near_match_score` pass. It exists for `status`, which
counted HIGH groups and threw every LOW group away. Both entry points
share `_eligible_keyed_docs` and `_high_candidate_groups`, so they cannot
drift.

status-aware-retrieval (MVP-3 gap #8 · S1, Phase 3): unless the caller
passes `include_deprecated=True`, `find_candidates` computes the shared
`openkos.lifecycle.deprecated_concept_ids(bundle_dir)` predicate ONCE per
call and excludes any deprecated/superseded concept id from
`_iter_eligible`'s output BEFORE HIGH/ACRONYM/LOW pairing, so no candidate
group ever contains a deprecated concept. `include_deprecated=True` skips
the predicate walk entirely (no `_iter_docs` pass), restoring today's
status-blind behavior byte-for-byte (design R1's zero-cost escape path).
"""

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from itertools import combinations
from pathlib import Path
from typing import Final

from openkos import lifecycle
from openkos.model import okf

from .normalize import normalize_key
from .similarity import acronym_expansion_match, near_match_score

_TIER_ORDER: dict["Tier", int] = {}
"""Populated after `Tier` is defined -- HIGH sorts before LOW within a type
partition (module-level ordering table, keeps `find_candidates`'s sort key
a simple lookup)."""


class Tier(Enum):
    """Candidate confidence tier."""

    HIGH = "high"
    """Exact shared normalized key -- see `normalize.normalize_key`."""
    ACRONYM = "acronym"
    """One title's token IS the initials of a word run in the other, per
    `similarity.acronym_expansion_match` -- `Google ADK` against `ADK (Agent
    Development Kit)` (#397). Always exactly a pair."""
    LOW = "low"
    """Near-match per `similarity.is_near_match`, not already HIGH or
    ACRONYM for the same pair."""


_TIER_ORDER[Tier.HIGH] = 0
_TIER_ORDER[Tier.ACRONYM] = 1
_TIER_ORDER[Tier.LOW] = 2


def _type_label(member_types: tuple[str, ...]) -> str:
    """The `okf_type` display label for a set of member types (design D2):
    the DISTINCT types in `member_types`, sorted ascending and joined with
    `+`. A same-type group's `member_types` holds exactly one distinct
    value, so this returns that bare type unchanged (e.g. `"Concept"`); a
    cross-type group's distinct values join deterministically regardless of
    which member's type appears first (e.g. `("Entity", "Concept")` and
    `("Concept", "Entity")` both produce `"Concept+Entity"`). This label is
    EPHEMERAL and DISPLAY-ONLY -- it is never a persisted OKF `type` and
    never parsed back into individual types by any consumer."""
    return "+".join(sorted(set(member_types)))


@dataclass(frozen=True)
class CandidateGroup:
    """One candidate group: OKF objects that MIGHT be the same real-world
    entity. Ephemeral -- never a persisted OKF type or `bundle`/`state`
    file. For the ACRONYM and LOW tiers every member shares one OKF type
    (strict per-type blocking); a HIGH group MAY span more than one
    declared OKF type (#437's cross-type exact-title bucketing)."""

    okf_type: str
    """The display type: the shared OKF `type` for a same-type group, or
    the sorted, `+`-joined distinct types for a cross-type HIGH group (see
    `_type_label`) -- an ephemeral display label only, never a persisted
    OKF type."""
    member_ids: tuple[str, ...]
    """The involved concept_ids -- sorted ascending, at least 2, unique.
    A HIGH group may have more than 2 members (all sharing one exact
    normalized key); a LOW group is always exactly a pair."""
    tier: Tier
    """`Tier.HIGH`, `Tier.ACRONYM`, or `Tier.LOW`."""
    trigger: str
    """HIGH: the shared normalized key. ACRONYM: the matched acronym itself.
    LOW: the near-match score (`near_match_score`) formatted to 3 decimal
    places."""
    member_types: tuple[str, ...] = ()
    """The declared OKF type of each member, index-aligned with
    `member_ids` (`member_types[i]` is `member_ids[i]`'s type). Defaults,
    via `__post_init__`, to `(okf_type,) * len(member_ids)` when omitted --
    every existing same-type construction site remains valid unchanged, and
    the field is never empty. Explicitly passed only for a cross-type HIGH
    group, where it MUST be index-aligned with `member_ids` (`ValueError` on
    a length mismatch)."""

    def __post_init__(self) -> None:
        if not self.member_types:
            object.__setattr__(
                self, "member_types", (self.okf_type,) * len(self.member_ids)
            )
        elif len(self.member_types) != len(self.member_ids):
            raise ValueError(
                "member_types must be index-aligned with member_ids: got "
                f"{len(self.member_types)} member_types for "
                f"{len(self.member_ids)} member_ids"
            )


_MAX_CANDIDATE_GROUPS: Final[int] = 50
"""Hard ceiling on candidate groups one `find_candidates`/`find_candidates_
report` call may return, applied to the FULL cross-type group set before
either entry point returns (curate-call-budget, entity-resolution delta:
Bounded Candidate-Group Output Per Call). Matches
`sqlite_graph._MAX_CANDIDATE_EDGES` (`graph/sqlite_graph.py:241`) -- the
house idiom this cap extends to the one stage that never received it. This
is a SAFETY RAIL against a pathological corpus, NOT a per-session curation
budget: it must rarely bind on a representative corpus, and MUST NOT be
retuned into an iterative-curation mechanism. Truncation is NEVER silent --
see `CandidateGroupReport`/`candidate_group_truncation_notice`."""


@dataclass(frozen=True)
class CandidateGroupReport:
    """The `_MAX_CANDIDATE_GROUPS` truncation report (curate-call-budget,
    design D1/D2). `produced` is the FULL cross-type group count BEFORE the
    cap is applied; `retained` is the count actually returned, `== len
    (groups)`. `groups` is the retained slice in the module's existing
    canonical output order (`okf_type` ascending, then tier, then
    `member_ids` ascending) -- NEVER in rank order, which governs only which
    groups survive the cap (see `_cap_rank_key`). All three default to the
    empty/zero below-cap-equivalent shape.

    Deliberately a NEW type rather than `graph.sqlite_graph.CandidateReport`
    reused (design D2): that type's `pairs` field exists to satisfy a
    sensitivity re-derivation duty (`sqlite_graph.py:264-270`) this report
    has no equivalent of -- today's Identity cost line already prints the
    raw `len(groups)` unfiltered by sensitivity, so disclosing `produced`
    here reveals no aggregate the pre-change line did not already reveal."""

    groups: tuple[CandidateGroup, ...] = ()
    produced: int = 0
    retained: int = 0


def _cap_rank_key(group: CandidateGroup) -> tuple[int, float, str, tuple[str, ...]]:
    """The total order `find_candidates_report` ranks the FULL cross-type
    group set by before truncating (entity-resolution delta: Deterministic
    Ranking For Truncation): tier priority first (`_TIER_ORDER`, GLOBAL
    across the whole set), then -- LOW only -- `near_match_score` descending,
    then the SAME `(okf_type, member_ids)` ascending tie-break
    `find_candidates` already establishes as its final sort key.

    The tier branch on `score` is MANDATORY, not cosmetic: only a LOW
    `trigger` is the `near_match_score` formatted to 3 decimals
    (`candidates.py:282`); a HIGH `trigger` is a normalized key and an
    ACRONYM `trigger` is the matched acronym string, so an unconditional
    `float(group.trigger)` would raise `ValueError` on either. `0.0` is an
    inert placeholder for HIGH/ACRONYM: `_TIER_ORDER[group.tier]` alone
    already separates every HIGH/ACRONYM group from every LOW group, so the
    placeholder never participates in an actual tie-break."""
    score = float(group.trigger) if group.tier is Tier.LOW else 0.0
    return (_TIER_ORDER[group.tier], -score, group.okf_type, group.member_ids)


def candidate_group_truncation_notice(report: CandidateGroupReport) -> str | None:
    """The Identity/`duplicates`/`adjudicate` truncation notice (design D3),
    `None` unless `report.produced > report.retained`. Wording matches
    `edge_typing.candidate_truncation_notice` (`edge_typing.py:589`)
    byte-for-byte apart from the noun -- direct precedent for the SAME
    "cap reached" shape, a different resource."""
    if report.produced <= report.retained:
        return None
    return (
        f"{report.retained} of {report.produced} candidate group(s) shown (cap reached)"
    )


def _iter_eligible(bundle_dir: Path) -> list[tuple[str, str, str, str | None]]:
    """Return `(concept_id, okf_type, title, type_alternative)` for every
    eligible document.

    Mirrors `_iter_docs`'s skip-and-continue degrade contract: a read
    error or parse error excludes the document from consideration, never
    raising (spec: Degrade, Not Crash). A document with a missing/empty
    `type`, `type == "Source"`, or a blank/non-string `title` is also
    excluded (design: "Reading the bundle"). `concept_id` is the
    bundle-relative path with the `.md` suffix removed -- the same
    identity `state/fts.py` uses.

    `type_alternative` is the type the extractor recorded as its runner-up
    (#804), or `None`. It is advisory in exactly the way the rest of this
    walk is: a non-string value normalizes to `None` rather than raising,
    because a hand-edited bundle must not be able to crash a read-only
    scan, and surrounding whitespace is stripped so a quoted `" Entity "`
    names the same type `Entity` does. A value left blank by that strip is
    `None` too. Whether the recorded type is USABLE is not decided here --
    `_bridged_cross_type_pairs` owns that.
    """
    eligible: list[tuple[str, str, str, str | None]] = []
    for scan in okf._iter_docs(bundle_dir):
        if scan.read_error is not None or scan.parse_error is not None:
            continue
        metadata = scan.metadata or {}
        okf_type = metadata.get("type")
        if not okf_type or okf_type == "Source":
            continue
        title = metadata.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        alternative = metadata.get(okf.TYPE_ALTERNATIVE_KEY)
        alternative = (
            alternative.strip() if isinstance(alternative, str) else None
        ) or None
        concept_id = okf.concept_id_for(scan.path, bundle_dir)
        eligible.append((concept_id, str(okf_type), title, alternative))
    return eligible


def _eligible_keyed_docs(
    bundle_dir: Path, *, include_deprecated: bool
) -> list[tuple[str, str, str, str | None]]:
    """The shared, FLAT I/O prelude of BOTH public entry points (design D1).

    Walks the bundle via `_iter_eligible`, applies the
    `lifecycle.deprecated_concept_ids` exclusion unless
    `include_deprecated=True` (in which case that predicate walk is skipped
    entirely), and returns `(concept_id, okf_type, normalized_key)` triples
    for every survivor, in NO particular order -- `_keyed_docs_by_type` and
    `_high_candidate_groups` each impose their own ordering downstream. This
    is the ONLY function in the module that touches the filesystem for
    candidate generation; `_keyed_docs_by_type` (the per-type partition) and
    `_high_candidate_groups` (the cross-type HIGH bucketing) are both pure
    functions over this flat list, so `find_candidates` and
    `find_exact_title_groups` cannot drift on eligibility, deprecation, or
    normalization (#216, #437).
    """
    eligible = _iter_eligible(bundle_dir)
    if not include_deprecated:
        deprecated = lifecycle.deprecated_concept_ids(bundle_dir)
        eligible = [doc for doc in eligible if doc[0] not in deprecated]
    return [
        (concept_id, okf_type, normalize_key(title), alternative)
        for concept_id, okf_type, title, alternative in eligible
    ]


def _keyed_docs_by_type(
    keyed: list[tuple[str, str, str, str | None]],
) -> list[tuple[str, list[tuple[str, str]]]]:
    """PURE partition of the flat `_eligible_keyed_docs` output by exact
    `okf_type` (design D1) -- the SAME output shape the pre-#437 shared
    prelude produced, now split out as its own pure step. Returns
    `(okf_type, keyed)` pairs in ASCENDING `okf_type` order, where `keyed`
    is `(concept_id, normalized_key)` sorted by ascending `concept_id`. Feeds
    the ACRONYM/LOW passes in `find_candidates_report`, which stay strictly
    per-type; the HIGH tier no longer goes through this partition at all
    (see `_high_candidate_groups`, which buckets the FLAT list instead).
    """
    by_type: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for concept_id, okf_type, key, _ in keyed:
        by_type[okf_type].append((concept_id, key))

    keyed_by_type: list[tuple[str, list[tuple[str, str]]]] = []
    for okf_type in sorted(by_type):
        docs = sorted(by_type[okf_type], key=lambda doc: doc[0])
        keyed_by_type.append((okf_type, docs))
    return keyed_by_type


def _high_candidate_groups(
    keyed: list[tuple[str, str, str, str | None]],
) -> list[CandidateGroup]:
    """Build HIGH `CandidateGroup`s from the FLAT `(concept_id, okf_type,
    normalized_key)` list, bucketed by exact key ACROSS ALL declared OKF
    types (#437, design D1) -- one bucket-then-sort pass, no pairwise work
    of any kind, so this stays free for the HIGH-only entry point exactly
    as before the cross-type change.

    Two or more documents sharing one normalized key form a single group
    regardless of type: a same-type cluster gets `okf_type` equal to that
    shared type (`_type_label` on one distinct value is a no-op); a
    cross-type cluster gets the sorted, `+`-joined display label (e.g.
    `"Concept+Entity"`) and `member_types` index-aligned with `member_ids`.
    Returned in ascending normalized-key order -- callers (`find_candidates_
    report`/`find_exact_title_groups`) own the final `(okf_type,
    member_ids)` output ordering; this function's own order is an
    implementation detail, not part of either entry point's contract.
    """
    by_key: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for concept_id, okf_type, key, _ in keyed:
        by_key[key].append((concept_id, okf_type))

    groups: list[CandidateGroup] = []
    for key in sorted(by_key):
        members = sorted(by_key[key], key=lambda member: member[0])
        if len(members) < 2:
            continue
        member_ids = tuple(member[0] for member in members)
        member_types = tuple(member[1] for member in members)
        groups.append(
            CandidateGroup(
                okf_type=_type_label(member_types),
                member_ids=member_ids,
                tier=Tier.HIGH,
                trigger=key,
                member_types=member_types,
            )
        )
    return groups


def _pairs_covered_by_high_groups(
    high_groups: list[CandidateGroup],
) -> set[frozenset[str]]:
    """Every unordered concept-id pair already covered by a HIGH group.

    This is exactly what excludes a pair from the ACRONYM/LOW passes
    (HIGH/ACRONYM/LOW disjoint, per pair), and it is fully derivable from the
    HIGH groups, so ONLY `find_candidates_report` calls it, ONCE, over the
    GLOBAL cross-type HIGH set (design D1) -- every per-type ACRONYM/LOW loop
    reuses that single result. A cross-type HIGH pair never appears in a
    same-type ACRONYM/LOW loop's `combinations(keyed, 2)` anyway (each such
    loop is already scoped to one `okf_type`), so this global set is a
    superset for any one type and the same-type exclusion it existed for
    stays byte-for-byte unchanged. Keeping this build out of
    `_high_candidate_groups` keeps the O(m^2)-in-cluster-size pair build off
    `find_exact_title_groups`'s path, which never runs an ACRONYM/LOW pass
    and so has nothing to exclude.
    """
    return {
        frozenset(pair)
        for group in high_groups
        for pair in combinations(group.member_ids, 2)
    }


def _pair_group(
    left: tuple[str, str, str],
    right: tuple[str, str, str],
) -> CandidateGroup | None:
    """The ACRONYM-then-LOW verdict for one `(concept_id, key, okf_type)`
    pair, or `None` when neither tier fires.

    ACRONYM is evaluated BEFORE the near-match rule so a pair qualifying
    under both is emitted once, under the stronger of the two (#397). Every
    group costs one adjudication call (#382), so double-reporting one pair
    would buy nothing and charge twice.

    Shared by the per-type pass and the bridged cross-type pass (#804) so
    the two cannot drift in tier order, trigger format, or member ordering.
    A same-type pair's `_type_label` is that bare type and its
    `member_types` is what `__post_init__` would have defaulted to, so the
    per-type pass emits exactly what it emitted before this helper existed.
    """
    (id_a, key_a, type_a), (id_b, key_b, type_b) = left, right
    ordered = sorted(((id_a, type_a), (id_b, type_b)), key=lambda doc: doc[0])
    member_ids = tuple(doc[0] for doc in ordered)
    member_types = tuple(doc[1] for doc in ordered)
    acronym = acronym_expansion_match(key_a, key_b)
    if acronym is not None:
        return CandidateGroup(
            okf_type=_type_label(member_types),
            member_ids=member_ids,
            tier=Tier.ACRONYM,
            trigger=acronym,
            member_types=member_types,
        )
    score = near_match_score(key_a, key_b)
    if score is None:
        return None
    return CandidateGroup(
        okf_type=_type_label(member_types),
        member_ids=member_ids,
        tier=Tier.LOW,
        trigger=f"{score:.3f}",
        member_types=member_types,
    )


def _bridged_cross_type_pairs(
    keyed: list[tuple[str, str, str, str | None]],
) -> list[tuple[tuple[str, str, str], tuple[str, str, str]]]:
    """PURE: the cross-type pairs `_keyed_docs_by_type` cannot reach, opened
    by a document's own recorded `type_alternative` (#804).

    ACRONYM and LOW compare within one exact type, so two documents
    describing one thing under two names AND two types fall through every
    tier by construction rather than by scoring -- HIGH needs an exact
    shared key, and the per-type partition hides the pair from the fuzzy
    tiers that exist for exactly this case.

    The extractor already wrote down the uncertainty that justifies the
    comparison: `type_alternative` is the type it nearly chose. When one
    document's runner-up names another document's declared type, the two are
    admitted to the SAME comparison the per-type pass would have run. What
    happens there is unchanged -- the same `acronym_expansion_match`, the
    same `near_match_score`, the same thresholds -- so this widens WHO is
    compared and never HOW.

    One direction is enough: both extractions being uncertain at once is
    luck, not stronger evidence. A pair recorded from both sides is still
    one bridge, and is yielded once. Order within each pair and across the
    list is deterministic (`concept_id` ascending), so a bundle's candidate
    set does not depend on filesystem walk order.

    An alternative naming the document's OWN type opens nothing. Downstream
    would absorb it -- those pairs are the per-type pass's own, already
    emitted or already declined -- so the skip buys no verdict, only the
    work of re-deciding them. It is enforced HERE, where it is observable,
    rather than in the walk, where two guards would drift.
    """
    by_type: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for concept_id, okf_type, key, _ in keyed:
        by_type[okf_type].append((concept_id, key, okf_type))

    bridged: dict[frozenset[str], tuple[tuple[str, str, str], ...]] = {}
    for concept_id, okf_type, key, alternative in sorted(keyed):
        if alternative is None or alternative == okf_type:
            continue
        for other in by_type.get(alternative, ()):
            if other[0] == concept_id:
                continue
            left = (concept_id, key, okf_type)
            # Keyed by the unordered pair, and each side builds the SAME
            # sorted tuple, so a bridge recorded from both directions
            # collapses to one entry rather than needing a guard.
            bridged[frozenset((concept_id, other[0]))] = tuple(
                sorted((left, other), key=lambda doc: doc[0])
            )
    return [
        (pair[0], pair[1]) for _, pair in sorted(bridged.items(), key=_bridge_order)
    ]


def _bridge_order(
    item: tuple[frozenset[str], tuple[tuple[str, str, str], ...]],
) -> tuple[str, ...]:
    """Sort key for `_bridged_cross_type_pairs`: the pair's member ids,
    ascending. A `frozenset` has no order of its own, so sorting on it
    directly would make the output depend on hash seeding."""
    return tuple(doc[0] for doc in item[1])


def find_candidates_report(
    bundle_dir: Path, *, include_deprecated: bool = False
) -> CandidateGroupReport:
    """Scan `bundle_dir` and return the `_MAX_CANDIDATE_GROUPS`-bounded
    candidate-group report, read-only (curate-call-budget, design D1).

    Builds the FULL cross-type group set exactly as `find_candidates` always
    has -- same walk, same HIGH/ACRONYM/LOW passes, same per-type ordering --
    then, if that set exceeds `_MAX_CANDIDATE_GROUPS`, ranks it with
    `_cap_rank_key` and slices to the cap BEFORE re-sorting the retained
    subset back into the module's canonical output order (entity-resolution
    delta: Deterministic Ranking For Truncation). Below the cap, the slice is
    a no-op: `report.groups` is byte-identical to today's unbounded
    `find_candidates` output, and `report.produced == report.retained`.

    Never writes a byte of the bundle and creates no persisted state. Given
    an unchanged bundle, repeated calls return the identical report
    (extends the existing determinism guarantee).

    Unless `include_deprecated=True`, the shared
    `lifecycle.deprecated_concept_ids(bundle_dir)` predicate is computed
    ONCE and any deprecated/superseded concept id is excluded from
    `_iter_eligible`'s output BEFORE HIGH/LOW pairing (status-aware-
    retrieval, Phase 3) -- a deprecated concept never joins a candidate
    group, but its live groupmates still pair normally with each other.
    `include_deprecated=True` skips the predicate walk entirely, restoring
    today's status-blind behavior byte-for-byte.

    `find_candidates` delegates to `list(find_candidates_report(...).groups)`
    -- the two cannot drift. A caller that needs only the `Tier.HIGH` groups
    should call `find_exact_title_groups` instead (issue #216): it is NEVER
    capped (see its own docstring) and never pays for the O(n^2)
    `near_match_score` pass below.
    """
    eligible = _eligible_keyed_docs(bundle_dir, include_deprecated=include_deprecated)
    high_groups = _high_candidate_groups(eligible)
    high_pairs = _pairs_covered_by_high_groups(high_groups)

    groups: list[CandidateGroup] = list(high_groups)
    seen: set[frozenset[str]] = set(high_pairs)
    for okf_type, keyed in _keyed_docs_by_type(eligible):
        for (id_a, key_a), (id_b, key_b) in combinations(keyed, 2):
            pair = frozenset((id_a, id_b))
            if pair in seen:
                continue
            group = _pair_group((id_a, key_a, okf_type), (id_b, key_b, okf_type))
            if group is None:
                continue
            seen.add(pair)
            groups.append(group)
    for left, right in _bridged_cross_type_pairs(eligible):
        pair = frozenset((left[0], right[0]))
        if pair in seen:
            continue
        group = _pair_group(left, right)
        if group is None:
            continue
        seen.add(pair)
        groups.append(group)

    produced = len(groups)
    retained_groups = sorted(groups, key=_cap_rank_key)[:_MAX_CANDIDATE_GROUPS]
    retained_groups.sort(key=lambda g: (g.okf_type, _TIER_ORDER[g.tier], g.member_ids))
    return CandidateGroupReport(
        groups=tuple(retained_groups),
        produced=produced,
        retained=len(retained_groups),
    )


def find_candidates(
    bundle_dir: Path, *, include_deprecated: bool = False
) -> list[CandidateGroup]:
    """Scan `bundle_dir` and return every RETAINED candidate group,
    read-only -- `list(find_candidates_report(bundle_dir,
    include_deprecated=include_deprecated).groups)` (curate-call-budget,
    design D1). Signature and return type are unchanged; a caller that also
    needs the pre-cap `produced` count should call `find_candidates_report`
    directly.

    Given an unchanged bundle, repeated calls return the SAME candidate
    set in the SAME stable order: grouped by `okf_type` ascending, HIGH
    groups before LOW within each type, ties broken by ascending
    `member_ids` (i.e. by concept_id). An empty or single-document bundle
    (per type) yields no candidates and never raises. The returned list is
    bounded to `_MAX_CANDIDATE_GROUPS`; below that ceiling this is
    byte-identical to the pre-cap behavior.

    A caller that needs only the `Tier.HIGH` groups should call
    `find_exact_title_groups` instead (issue #216): it returns the identical
    HIGH groups in the identical order without paying for the O(n^2)
    `near_match_score` pass, and is never capped.
    """
    return list(
        find_candidates_report(bundle_dir, include_deprecated=include_deprecated).groups
    )


def find_exact_title_groups(
    bundle_dir: Path, *, include_deprecated: bool = False
) -> list[CandidateGroup]:
    """Scan `bundle_dir` and return only its exact-title (`Tier.HIGH`)
    candidate groups, read-only -- the cheap entry point (issue #216).

    EQUIVALENCE (the contract, and the whole safety argument), AMENDED for
    `_MAX_CANDIDATE_GROUPS` (curate-call-budget): this function is NEVER
    capped -- it costs zero LLM calls and feeds `status`/`next_action`
    counts, which must stay truthful regardless of Identity's adjudication
    budget. WHILE THE CAP DOES NOT BIND, the result is exactly `[g for g in
    find_candidates(bundle_dir, include_deprecated=include_deprecated) if
    g.tier is Tier.HIGH]`, INCLUDING list order, holding verbatim. WHEN THE
    CAP BINDS, `find_candidates`'s retained HIGH set is always a PREFIX of
    this function's (uncapped) output, in the SAME order: HIGH ranks first,
    globally, in `_cap_rank_key`'s ordering, so no HIGH group is ever
    evicted in favour of an ACRONYM or LOW group competing for the same cap
    slots, and both functions apply the SAME `(okf_type, member_ids)`
    tie-break to the surviving HIGH groups. Both functions share
    `_eligible_keyed_docs` and `_high_candidate_groups`, then apply the SAME
    final sort key, and HIGH groups have disjoint member sets (a member
    joins at most one exact-key bucket) -- so `(okf_type, member_ids)` is a
    strict total order over them and no tie is left for the sort to break
    arbitrarily. The verbatim
    (below-cap) equivalence is pinned by
    `tests/unit/resolution/test_candidates.py::
    test_find_exact_title_groups_equals_the_high_slice_in_order`; the
    amended (above-cap) prefix relation is pinned by
    `test_high_slice_is_a_prefix_of_find_exact_title_groups`.

    WHAT THIS SAVES: the pairwise LOW pass. `find_candidates` runs
    `near_match_score` over `combinations(keyed, 2)` for every type -- an
    O(n^2) cost in concepts-per-type -- and `status` discarded every
    `Tier.LOW` group it paid for. This function never calls
    `near_match_score` at all.

    WHAT THIS DOES NOT SAVE: the bundle walks. Like `find_candidates`, this
    still performs `_iter_eligible`'s `okf._iter_docs` walk plus, under the
    default `include_deprecated=False`, `lifecycle.deprecated_concept_ids`'s
    own walk -- TWO walks, unchanged. Consolidating `status`'s repeated
    walks is issue #195's territory and explicitly out of scope here; do not
    read this function as a walk-count win.

    WHAT THIS COSTS, precisely: after the shared walks, one pass bucketing
    the FLAT eligible-document list by normalized key ACROSS all types
    (#437), plus one sort of those keys. No pairwise work of any kind -- not
    `near_match_score`, and not the already-HIGH pair set either, which is
    quadratic in the size of a single exact-title cluster and which only the
    ACRONYM/LOW passes need (see `_pairs_covered_by_high_groups`, called by
    `find_candidates_report` alone).

    WHY A SEPARATE FUNCTION rather than a `tier=` filter on
    `find_candidates`:

    - A parameter that silently adds the whole-type pairwise pass -- the
      quadratic-in-concepts-per-type cost this function avoids entirely -- is
      easy to miss at a call site. A distinct name makes the cheap path
      obviously cheap, at the point of call.
    - A `tier` parameter would invite `Tier.LOW`, which has no coherent
      cheap implementation: the LOW pass excludes any pair already covered
      by a HIGH group, so it cannot skip the HIGH pass anyway.

    `duplicates` and `adjudicate` deliberately keep calling
    `find_candidates`: they render and adjudicate both tiers, so the
    pairwise pass is work they actually use.
    """
    eligible = _eligible_keyed_docs(bundle_dir, include_deprecated=include_deprecated)
    groups = _high_candidate_groups(eligible)

    groups.sort(key=lambda g: (g.okf_type, _TIER_ORDER[g.tier], g.member_ids))
    return groups
