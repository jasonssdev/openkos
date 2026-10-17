"""The combined-pages read of `openkos.application.unmerge_service`
(`review-undo` change, design D1): every existing page whose merge ledger
records a merge, newest combine first, each with its merge history.

Every merge is made through `merge_service`, the same write the CLI makes, so
the ledgers read here are real ones.
"""

from pathlib import Path

import pytest

from openkos.application import unmerge_service as svc
from openkos.bundle import ledger as bundle_ledger
from tests.unit.application.curation_support import make_workspace, tree, write_concept
from tests.unit.application.test_unmerge_api_service import _merge


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    for name in ("alpha", "bravo", "charlie", "delta", "echo"):
        write_concept(root, f"concepts/{name}", title=name.title())
    return root


def _summary(root: Path) -> list[tuple[str, list[str]]]:
    return [
        (page.concept_id, [record.absorbed_id for record in page.merges])
        for page in svc.merged_concepts(root)
    ]


def test_every_combined_page_is_listed_newest_combine_first(root: Path) -> None:
    _merge(root, "concepts/charlie", "concepts/delta")
    _merge(root, "concepts/alpha", "concepts/bravo")
    before = tree(root)

    assert _summary(root) == [
        ("concepts/alpha", ["concepts/bravo"]),
        ("concepts/charlie", ["concepts/delta"]),
    ]
    assert tree(root) == before


def test_a_page_is_placed_by_its_newest_combine_not_its_first(root: Path) -> None:
    _merge(root, "concepts/alpha", "concepts/bravo")
    _merge(root, "concepts/charlie", "concepts/delta")
    _merge(root, "concepts/alpha", "concepts/echo")

    assert _summary(root) == [
        ("concepts/alpha", ["concepts/echo", "concepts/bravo"]),
        ("concepts/charlie", ["concepts/delta"]),
    ]


def test_each_page_carries_the_history_the_per_page_read_returns(root: Path) -> None:
    _merge(root, "concepts/alpha", "concepts/bravo")
    _merge(root, "concepts/alpha", "concepts/charlie")

    (page,) = svc.merged_concepts(root)

    assert page.concept_id == "concepts/alpha"
    assert page.merges == svc.merge_history(root, "concepts/alpha")


def test_a_page_absorbed_after_its_own_combine_is_left_out(root: Path) -> None:
    _merge(root, "concepts/bravo", "concepts/charlie")
    _merge(root, "concepts/alpha", "concepts/bravo")

    assert bundle_ledger.ledger_path_for("concepts/bravo", root / "bundle").is_file()
    assert _summary(root) == [("concepts/alpha", ["concepts/bravo"])]


def test_an_unreadable_ledger_is_skipped(root: Path) -> None:
    _merge(root, "concepts/alpha", "concepts/bravo")
    _merge(root, "concepts/charlie", "concepts/delta")
    broken = bundle_ledger.ledger_path_for("concepts/charlie", root / "bundle")
    broken.write_text("---\nmerged_from: [unterminated\n---\n", encoding="utf-8")

    assert _summary(root) == [("concepts/alpha", ["concepts/bravo"])]


def test_a_ledger_with_no_entries_is_left_out(root: Path) -> None:
    _merge(root, "concepts/alpha", "concepts/bravo")
    empty = bundle_ledger.ledger_path_for("concepts/charlie", root / "bundle")
    empty.write_text(
        f"---\nschema: {bundle_ledger.LEDGER_SIDECAR_SCHEMA}\n"
        "survivor_id: concepts/charlie\nmerged_from: []\n---\n",
        encoding="utf-8",
    )

    assert _summary(root) == [("concepts/alpha", ["concepts/bravo"])]


def test_a_workspace_with_no_combine_lists_nothing(root: Path) -> None:
    assert svc.merged_concepts(root) == ()


def test_a_tie_on_the_newest_combine_is_ordered_by_concept_id() -> None:
    newer = svc.MergeHistoryEntry("z", "y", None, "2026-10-16T10:00:00+00:00")
    tied = svc.MergeHistoryEntry("a", "b", None, "2026-10-16T12:00:00+02:00")
    unparsed = svc.MergeHistoryEntry("m", "n", None, "not a time")
    naive = svc.MergeHistoryEntry("k", "j", None, "2026-10-16T09:00:00")

    assert [
        page.concept_id
        for page in svc.order_merged(
            [
                svc.MergedConcept("m", (unparsed,)),
                svc.MergedConcept("k", (naive,)),
                svc.MergedConcept("z", (newer,)),
                svc.MergedConcept("a", (tied,)),
            ]
        )
    ] == ["a", "z", "k", "m"]
