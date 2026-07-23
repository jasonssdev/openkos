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

from openkos import lifecycle
from openkos.model import okf

from .normalize import normalize_key
from .similarity import near_match_score

_TIER_ORDER: dict["Tier", int] = {}
"""Populated after `Tier` is defined -- HIGH sorts before LOW within a type
partition (module-level ordering table, keeps `find_candidates`'s sort key
a simple lookup)."""


class Tier(Enum):
    """Candidate confidence tier."""

    HIGH = "high"
    """Exact shared normalized key -- see `normalize.normalize_key`."""
    LOW = "low"
    """Near-match per `similarity.is_near_match`, not already HIGH for the
    same pair."""


_TIER_ORDER[Tier.HIGH] = 0
_TIER_ORDER[Tier.LOW] = 1


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
    """`Tier.HIGH` or `Tier.LOW`."""
    trigger: str
    """HIGH: the shared normalized key. LOW: the near-match score
    (`near_match_score`) formatted to 3 decimal places."""


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
        concept_id = scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
        eligible.append((concept_id, str(okf_type), title))
    return eligible


def _high_groups_for_type(
    keyed: list[tuple[str, str]],
) -> tuple[list[tuple[str, ...]], set[frozenset[str]]]:
    """Group `(concept_id, normalized_key)` pairs by exact key.

    Returns the HIGH member-id tuples (each with >= 2 members, sorted) and
    the set of every unordered pair already covered by a HIGH group --
    the latter is what excludes a pair from the LOW pass (HIGH/LOW
    disjoint, per pair).
    """
    by_key: dict[str, list[str]] = defaultdict(list)
    for concept_id, key in keyed:
        by_key[key].append(concept_id)

    high_groups: list[tuple[str, ...]] = []
    high_pairs: set[frozenset[str]] = set()
    for key in sorted(by_key):
        members = sorted(by_key[key])
        if len(members) < 2:
            continue
        high_groups.append(tuple(members))
        for pair in combinations(members, 2):
            high_pairs.add(frozenset(pair))
    return high_groups, high_pairs


def find_candidates(
    bundle_dir: Path, *, include_deprecated: bool = False
) -> list[CandidateGroup]:
    """Scan `bundle_dir` and return every candidate group, read-only.

    Never writes a byte of the bundle and creates no persisted state.
    Given an unchanged bundle, repeated calls return the SAME candidate
    set in the SAME stable order: grouped by `okf_type` ascending, HIGH
    groups before LOW within each type, ties broken by ascending
    `member_ids` (i.e. by concept_id). An empty or single-document bundle
    (per type) yields no candidates and never raises.

    Unless `include_deprecated=True`, the shared
    `lifecycle.deprecated_concept_ids(bundle_dir)` predicate is computed
    ONCE and any deprecated/superseded concept id is excluded from
    `_iter_eligible`'s output BEFORE HIGH/LOW pairing (status-aware-
    retrieval, Phase 3) -- a deprecated concept never joins a candidate
    group, but its live groupmates still pair normally with each other.
    `include_deprecated=True` skips the predicate walk entirely, restoring
    today's status-blind behavior byte-for-byte.
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

    groups: list[CandidateGroup] = []
    for okf_type in sorted(by_type):
        docs = sorted(by_type[okf_type], key=lambda doc: doc[0])
        keyed = [(concept_id, normalize_key(title)) for concept_id, title in docs]
        key_by_id = dict(keyed)

        high_groups, high_pairs = _high_groups_for_type(keyed)
        for member_ids in high_groups:
            groups.append(
                CandidateGroup(
                    okf_type=okf_type,
                    member_ids=member_ids,
                    tier=Tier.HIGH,
                    trigger=key_by_id[member_ids[0]],
                )
            )

        for (id_a, key_a), (id_b, key_b) in combinations(keyed, 2):
            pair = frozenset((id_a, id_b))
            if pair in high_pairs:
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

    groups.sort(key=lambda g: (g.okf_type, _TIER_ORDER[g.tier], g.member_ids))
    return groups
