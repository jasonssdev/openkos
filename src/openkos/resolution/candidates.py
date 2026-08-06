"""Read-only, whole-bundle entity-resolution candidate generation.

`find_candidates` walks a bundle via `okf._iter_docs` (D2, the SAME
enumerate/skip pattern as `state/fts.py`/`graph/sqlite_graph.py`),
partitions non-Source concept documents by their EXACT declared OKF
`type`, and proposes candidate GROUPS within each partition via two
deterministic, stdlib-only tiers: HIGH (an exact shared
`normalize.normalize_key`) and LOW (a `similarity.is_near_match`,
excluding any pair already HIGH). Output is ephemeral -- frozen
dataclasses only, never a persisted OKF type or `bundle`/`state` file --
and this module never writes a byte of the bundle.

`find_exact_title_groups` (issue #216) is the same pass with the LOW tier
left out: it returns exactly the `Tier.HIGH` groups `find_candidates` would
return, in the same order, without ever running the O(n^2) pairwise
`near_match_score` pass. It exists for `status`, which counted HIGH groups
and threw every LOW group away. Both entry points share
`_keyed_docs_by_type` and `_high_candidate_groups`, so they cannot drift.

status-aware-retrieval (MVP-3 gap #8 · S1, Phase 3): unless the caller
passes `include_deprecated=True`, `find_candidates` computes the shared
`openkos.lifecycle.deprecated_concept_ids(bundle_dir)` predicate ONCE per
call and excludes any deprecated/superseded concept id from
`_iter_eligible`'s output BEFORE HIGH/LOW pairing, so no candidate group
ever contains a deprecated concept. `include_deprecated=True` skips the
predicate walk entirely (no `_iter_docs` pass), restoring today's
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


@dataclass(frozen=True)
class CandidateGroup:
    """One candidate group: same-type OKF objects that MIGHT be the same
    real-world entity. Ephemeral -- never a persisted OKF type or
    `bundle`/`state` file."""

    okf_type: str
    """The exact OKF `type` shared by every member."""
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


def _iter_eligible(bundle_dir: Path) -> list[tuple[str, str, str]]:
    """Return `(concept_id, okf_type, title)` for every eligible document.

    Mirrors `_iter_docs`'s skip-and-continue degrade contract: a read
    error or parse error excludes the document from consideration, never
    raising (spec: Degrade, Not Crash). A document with a missing/empty
    `type`, `type == "Source"`, or a blank/non-string `title` is also
    excluded (design: "Reading the bundle"). `concept_id` is the
    bundle-relative path with the `.md` suffix removed -- the same
    identity `state/fts.py` uses.
    """
    eligible: list[tuple[str, str, str]] = []
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
        concept_id = okf.concept_id_for(scan.path, bundle_dir)
        eligible.append((concept_id, str(okf_type), title))
    return eligible


def _high_groups_for_type(
    keyed: list[tuple[str, str]],
) -> list[tuple[str, ...]]:
    """Group `(concept_id, normalized_key)` pairs by exact key.

    Returns the HIGH member-id tuples (each with >= 2 members, sorted), in
    ascending normalized-key order. Bucketing plus one sort of the keys --
    no pairwise work here, so the HIGH-only entry point never pays for any
    (see `_pairs_covered_by_high_groups`, which only `find_candidates`
    calls).
    """
    by_key: dict[str, list[str]] = defaultdict(list)
    for concept_id, key in keyed:
        by_key[key].append(concept_id)

    high_groups: list[tuple[str, ...]] = []
    for key in sorted(by_key):
        members = sorted(by_key[key])
        if len(members) < 2:
            continue
        high_groups.append(tuple(members))
    return high_groups


def _keyed_docs_by_type(
    bundle_dir: Path, *, include_deprecated: bool
) -> list[tuple[str, list[tuple[str, str]]]]:
    """The shared prelude of BOTH public entry points, in one place.

    Walks the bundle via `_iter_eligible`, applies the
    `lifecycle.deprecated_concept_ids` exclusion unless
    `include_deprecated=True` (in which case that predicate walk is skipped
    entirely), partitions what survives by exact `okf_type`, and returns
    `(okf_type, keyed)` pairs in ASCENDING `okf_type` order, where `keyed`
    is `(concept_id, normalize_key(title))` sorted by ascending
    `concept_id`. Callers pick up exactly where `_high_groups_for_type`
    takes over, so `find_candidates` and `find_exact_title_groups` cannot
    drift on eligibility, deprecation, partitioning, or normalization
    (#216).
    """
    eligible = _iter_eligible(bundle_dir)
    if not include_deprecated:
        deprecated = lifecycle.deprecated_concept_ids(bundle_dir)
        eligible = [
            (concept_id, okf_type, title)
            for concept_id, okf_type, title in eligible
            if concept_id not in deprecated
        ]

    by_type: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for concept_id, okf_type, title in eligible:
        by_type[okf_type].append((concept_id, title))

    keyed_by_type: list[tuple[str, list[tuple[str, str]]]] = []
    for okf_type in sorted(by_type):
        docs = sorted(by_type[okf_type], key=lambda doc: doc[0])
        keyed_by_type.append(
            (
                okf_type,
                [(concept_id, normalize_key(title)) for concept_id, title in docs],
            )
        )
    return keyed_by_type


def _high_candidate_groups(
    okf_type: str, keyed: list[tuple[str, str]]
) -> list[CandidateGroup]:
    """Build one type partition's HIGH `CandidateGroup`s from `keyed`.

    Returns them in `_high_groups_for_type`'s key-sorted order -- callers own
    the final ordering. Shared by both public entry points so a HIGH group's
    `okf_type`/`member_ids`/`trigger` are constructed in exactly ONE place
    (#216).
    """
    key_by_id = dict(keyed)
    return [
        CandidateGroup(
            okf_type=okf_type,
            member_ids=member_ids,
            tier=Tier.HIGH,
            trigger=key_by_id[member_ids[0]],
        )
        for member_ids in _high_groups_for_type(keyed)
    ]


def _pairs_covered_by_high_groups(
    high_groups: list[CandidateGroup],
) -> set[frozenset[str]]:
    """Every unordered concept-id pair already covered by a HIGH group.

    This is exactly what excludes a pair from the LOW pass (HIGH/LOW
    disjoint, per pair), and it is fully derivable from the HIGH groups, so
    ONLY `find_candidates` calls it. Keeping it out of
    `_high_groups_for_type` keeps the O(m^2)-in-cluster-size pair build off
    `find_exact_title_groups`'s path, which never runs a LOW pass and so has
    nothing to exclude.
    """
    return {
        frozenset(pair)
        for group in high_groups
        for pair in combinations(group.member_ids, 2)
    }


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
    groups: list[CandidateGroup] = []
    for okf_type, keyed in _keyed_docs_by_type(
        bundle_dir, include_deprecated=include_deprecated
    ):
        high_groups = _high_candidate_groups(okf_type, keyed)
        groups.extend(high_groups)
        high_pairs = _pairs_covered_by_high_groups(high_groups)

        for (id_a, key_a), (id_b, key_b) in combinations(keyed, 2):
            pair = frozenset((id_a, id_b))
            if pair in high_pairs:
                continue
            # ACRONYM is evaluated BEFORE the near-match rule so a pair
            # qualifying under both is emitted once, under the stronger of
            # the two (#397). Every group costs one adjudication call
            # (#382), so double-reporting one pair would buy nothing and
            # charge twice.
            acronym = acronym_expansion_match(key_a, key_b)
            if acronym is not None:
                groups.append(
                    CandidateGroup(
                        okf_type=okf_type,
                        member_ids=tuple(sorted((id_a, id_b))),
                        tier=Tier.ACRONYM,
                        trigger=acronym,
                    )
                )
                continue
            score = near_match_score(key_a, key_b)
            if score is None:
                continue
            groups.append(
                CandidateGroup(
                    okf_type=okf_type,
                    member_ids=tuple(sorted((id_a, id_b))),
                    tier=Tier.LOW,
                    trigger=f"{score:.3f}",
                )
            )

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
    `_keyed_docs_by_type` and `_high_candidate_groups`, then apply the SAME
    final sort key, and within one type HIGH groups have disjoint member
    sets -- so `(okf_type, member_ids)` is a strict total order over them
    and no tie is left for the sort to break arbitrarily. The verbatim
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
    the type partition's documents by their normalized key, plus one sort of
    those keys. No pairwise work of any kind -- not `near_match_score`, and
    not the already-HIGH pair set either, which is quadratic in the size of a
    single exact-title cluster and which only the LOW pass needs (see
    `_pairs_covered_by_high_groups`, called by `find_candidates` alone).

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
    groups: list[CandidateGroup] = []
    for okf_type, keyed in _keyed_docs_by_type(
        bundle_dir, include_deprecated=include_deprecated
    ):
        groups.extend(_high_candidate_groups(okf_type, keyed))

    groups.sort(key=lambda g: (g.okf_type, _TIER_ORDER[g.tier], g.member_ids))
    return groups
