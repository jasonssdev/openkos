"""Near-match clustering (#1701): the LOW-tier pairs of one subject become one
group-shaped candidate per connected component, bounded, never across types.

The before/after counts below are model-free: they count candidate groups, which
is exactly the number of identity questions the review queue is asked.
"""

from itertools import combinations
from pathlib import Path

import pytest

from openkos.resolution import candidates as candidates_mod
from openkos.resolution.candidates import CandidateGroup, Tier, find_candidates_report
from tests.unit.resolution.test_candidates import _write_doc

# A recurring subject: a hub and three near-duplicates. Four of the six possible
# pairs are not all candidates ("Usability" vs "New Television" is not), so the
# component is connected by chaining, not by a clique.
_REMOTE = (
    "Design of a Remote Control",
    "Remote Control Design and Usability",
    "New Television Remote Control Design",
    "Remote Control Design",
)
_BUDGET = (
    "Quarterly Budget Planning",
    "Budget Planning for Quarter",
    "Quarterly Budget Planning Process",
)
_STOIC = ("Stoicism", "Stoic Philosophy")


def _seed(bundle_dir: Path, titles: tuple[str, ...], prefix: str) -> None:
    for index, title in enumerate(titles):
        _write_doc(bundle_dir / "concepts" / f"{prefix}{index}.md", title=title)


def _recurring_bundle(tmp_path: Path) -> Path:
    bundle_dir = tmp_path / "bundle"
    _seed(bundle_dir, _REMOTE, "remote")
    _seed(bundle_dir, _BUDGET, "budget")
    _seed(bundle_dir, _STOIC, "stoic")
    return bundle_dir


def _former_pairs(bundle_dir: Path) -> set[frozenset[str]]:
    """Every pair the pairwise pass asks about today, recomputed independently."""
    eligible = candidates_mod._eligible_keyed_docs(bundle_dir, include_deprecated=False)
    pairs: set[frozenset[str]] = set()
    for _type, keyed in candidates_mod._keyed_docs_by_type(eligible):
        for (id_a, key_a), (id_b, key_b) in combinations(keyed, 2):
            if candidates_mod._pair_group((id_a, key_a, _type), (id_b, key_b, _type)):
                pairs.add(frozenset((id_a, id_b)))
    return pairs


def test_recurring_subjects_ask_one_question_each_instead_of_nine_pairs(
    tmp_path: Path,
) -> None:
    bundle_dir = _recurring_bundle(tmp_path)

    before = _former_pairs(bundle_dir)
    groups = find_candidates_report(bundle_dir).groups

    assert len(before) == 9  # 5 remote-control + 3 budget + 1 stoic pair
    assert len(groups) == 3
    assert sorted(len(g.member_ids) for g in groups) == [2, 3, 4]
    assert all(g.tier is Tier.LOW for g in groups)


def test_every_former_pair_stays_reachable_inside_one_group(tmp_path: Path) -> None:
    bundle_dir = _recurring_bundle(tmp_path)

    groups = find_candidates_report(bundle_dir).groups

    for pair in _former_pairs(bundle_dir):
        assert any(pair <= set(g.member_ids) for g in groups), pair


def test_a_component_of_two_stays_a_pair(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _seed(bundle_dir, _STOIC, "stoic")

    [group] = find_candidates_report(bundle_dir).groups

    assert group.member_ids == ("concepts/stoic0", "concepts/stoic1")


def test_a_cluster_is_one_sorted_same_type_low_group_with_its_weakest_score(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    _seed(bundle_dir, _BUDGET, "budget")

    [group] = find_candidates_report(bundle_dir).groups

    assert group == CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/budget0", "concepts/budget1", "concepts/budget2"),
        tier=Tier.LOW,
        trigger="0.875",
    )


def test_a_cluster_never_spans_types(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    _seed(bundle_dir, _BUDGET[:2], "budget")
    _write_doc(
        bundle_dir / "concepts" / "budget2.md", title=_BUDGET[2], doc_type="Entity"
    )

    groups = find_candidates_report(bundle_dir).groups

    assert [g.member_ids for g in groups] == [("concepts/budget0", "concepts/budget1")]


def test_a_component_above_the_bound_falls_back_to_its_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "bundle"
    _seed(bundle_dir, _REMOTE, "remote")
    monkeypatch.setattr(candidates_mod, "_MAX_CLUSTER_MEMBERS", 3)

    groups = find_candidates_report(bundle_dir).groups

    assert len(groups) == 5
    assert all(len(g.member_ids) == 2 for g in groups)


def test_a_component_at_the_bound_still_clusters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "bundle"
    _seed(bundle_dir, _REMOTE, "remote")
    monkeypatch.setattr(candidates_mod, "_MAX_CLUSTER_MEMBERS", 4)

    [group] = find_candidates_report(bundle_dir).groups

    assert len(group.member_ids) == 4


def test_the_bound_is_eight_members() -> None:
    assert candidates_mod._MAX_CLUSTER_MEMBERS == 8


def test_a_component_holding_an_exact_title_pair_keeps_its_pairs(
    tmp_path: Path,
) -> None:
    """An exact-title pair is already asked as a HIGH group; folding it into a
    cluster would ask it twice, so the component stays as pairs."""
    bundle_dir = tmp_path / "bundle"
    _write_doc(bundle_dir / "concepts" / "a.md", title="Design of a Remote Control")
    _write_doc(bundle_dir / "concepts" / "b.md", title="Design of a Remote Control")
    _write_doc(bundle_dir / "concepts" / "c.md", title="Remote Control Design")

    groups = find_candidates_report(bundle_dir).groups

    assert sorted(g.tier.value for g in groups) == ["high", "low", "low"]
    assert all(len(g.member_ids) == 2 or g.tier is Tier.HIGH for g in groups)


def test_clustering_is_deterministic(tmp_path: Path) -> None:
    bundle_dir = _recurring_bundle(tmp_path)

    assert find_candidates_report(bundle_dir) == find_candidates_report(bundle_dir)
