"""Imported concepts stay out of entity resolution's automatic paths
(okf-import, design D8).

An imported concept (Concept ID under `imports/`) is never an attach-at-ingest
target and never in the #1298 structural class, so it is neither auto-merged
nor offered by accept-recommended. It stays visible to `duplicates`,
`adjudicate` and `merge`, and in a human merge the local concept survives it.
"""

from __future__ import annotations

import functools
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import auto_merge, lifecycle
from openkos.application import ingest as application_ingest
from openkos.application.ingest import AttachTarget
from openkos.bundle import imports as bundle_imports
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict
from openkos.resolution.candidates import CandidateGroup, Tier
from openkos.state import adjudications as adjudications_store
from tests.unit.application.curation_support import make_workspace, write_concept

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVAL_DIR = _REPO_ROOT / "evals" / "auto_merge"

LOCAL = "concepts/skill"
IMPORTED = "imports/acme/concepts/skill"


def _write(bundle: Path, concept_id: str, frontmatter: str, body: str = "x") -> None:
    path = bundle / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")


def _lookup(bundle: Path) -> application_ingest.AttachLookup:
    return application_ingest.build_attach_lookup(
        bundle, read=lambda concept_id: AttachTarget(concept_id, "")
    )


def _group(*member_ids: str, okf_type: str = "Concept") -> CandidateGroup:
    return CandidateGroup(
        okf_type=okf_type, member_ids=member_ids, tier=Tier.HIGH, trigger="key"
    )


def _same(group: CandidateGroup) -> AdjudicatedCandidate:
    return AdjudicatedCandidate(
        candidate=group, verdict=Verdict.SAME, confidence=0.99, rationale="r"
    )


# --------------------------------------------------------------------------- #
# attach-at-ingest targets
# --------------------------------------------------------------------------- #


def test_an_imported_concept_is_never_an_attach_target(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, IMPORTED, "type: Concept\ntitle: Skill")

    assert _lookup(bundle).find("Concept", "Skill") == ()


def test_a_local_twin_is_still_an_attach_target_beside_an_imported_one(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, IMPORTED, "type: Concept\ntitle: Skill")
    _write(bundle, LOCAL, "type: Concept\ntitle: Skill")

    assert _lookup(bundle).find("Concept", "Skill") == (LOCAL,)


def test_a_local_concept_whose_path_merely_mentions_imports_is_a_target(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, "concepts/imports/skill", "type: Concept\ntitle: Skill")

    assert _lookup(bundle).find("Concept", "Skill") == ("concepts/imports/skill",)


def test_person_and_event_keep_their_exclusion_and_the_set_is_not_widened(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, "imports/acme/people/ana", "type: Person\ntitle: Ana")
    _write(bundle, "people/ana", "type: Person\ntitle: Ana")

    lookup = _lookup(bundle)

    assert lookup.find("Person", "Ana") == ()
    assert frozenset({"Event", "Person"}) == application_ingest.ATTACH_EXCLUDED_TYPES


# --------------------------------------------------------------------------- #
# the structural class
# --------------------------------------------------------------------------- #

_IMPORTED_BASE = "imports/acme/concepts/x"


@pytest.mark.parametrize(
    "members",
    [
        pytest.param((_IMPORTED_BASE, f"{_IMPORTED_BASE}-2"), id="both imported"),
        pytest.param((_IMPORTED_BASE, "imports/acme/concepts/x-2"), id="base id"),
        pytest.param((f"{_IMPORTED_BASE}-2", _IMPORTED_BASE), id="suffixed first"),
    ],
)
def test_an_imported_pair_is_out_of_the_class(members: tuple[str, ...]) -> None:
    assert auto_merge.in_structural_class(_group(*members)) is False


@pytest.mark.parametrize(
    "members",
    [
        pytest.param(("imports/acme/concepts/x", "imports/acme/concepts/x-2")),
        pytest.param(("imports/acme/concepts/x-2", "imports/acme/concepts/x")),
    ],
)
def test_an_identical_local_shape_stays_in_the_class(members: tuple[str, ...]) -> None:
    local = tuple(m.removeprefix("imports/acme/") for m in members)
    assert auto_merge.in_structural_class(_group(*local)) is True


def test_the_class_exclusion_checks_each_member_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    members = ("concepts/x", "concepts/x-2")
    only_first = lambda concept_id: concept_id == members[0]  # noqa: E731
    monkeypatch.setattr(bundle_imports, "is_imported_concept", only_first)
    assert auto_merge.in_structural_class(_group(*members)) is False
    only_second = lambda concept_id: concept_id == members[1]  # noqa: E731
    monkeypatch.setattr(bundle_imports, "is_imported_concept", only_second)
    assert auto_merge.in_structural_class(_group(*members)) is False


# --------------------------------------------------------------------------- #
# one predicate feeds every consumer
# --------------------------------------------------------------------------- #

_LOCAL_PAIR = _group("concepts/y", "concepts/y-2")
_IMPORTED_PAIR = _group(_IMPORTED_BASE, f"{_IMPORTED_BASE}-2")


def _results() -> list[AdjudicatedCandidate]:
    return [_same(_IMPORTED_PAIR), _same(_LOCAL_PAIR)]


def _kwargs(results: list[AdjudicatedCandidate]) -> dict[str, Any]:
    return {
        "fresh_keys": frozenset(
            adjudications_store.group_key_for(r.candidate.member_ids) for r in results
        ),
        "blocked": frozenset(),
        "cross_type_concern": lambda pair: None,
        "ordered_pair": functools.partial(lifecycle.ordered_merge_pair, Path("unused")),
    }


def _members(plan: auto_merge.AutoMergePlan) -> list[tuple[str, ...]]:
    return [p.result.candidate.member_ids for p in plan.planned]


def test_the_automatic_pass_and_the_offer_plan_only_the_local_pair() -> None:
    results = _results()
    kwargs = _kwargs(results)
    planned = auto_merge.plan_auto_merges(results, **kwargs)
    offered = auto_merge.recommended(results, excluded_survivors=frozenset(), **kwargs)

    assert _members(planned) == [_LOCAL_PAIR.member_ids]
    assert _members(offered) == [_LOCAL_PAIR.member_ids]


def test_patching_the_one_class_predicate_admits_the_imported_pair_everywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits: list[tuple[str, ...]] = []

    def always(group: CandidateGroup) -> bool:
        hits.append(group.member_ids)
        return True

    monkeypatch.setattr(auto_merge, "in_structural_class", always)
    results = _results()
    kwargs = _kwargs(results)

    planned = auto_merge.plan_auto_merges(results, **kwargs)
    offered = auto_merge.recommended(results, excluded_survivors=frozenset(), **kwargs)

    assert _IMPORTED_PAIR.member_ids in _members(planned)
    assert _IMPORTED_PAIR.member_ids in _members(offered)
    assert hits.count(_IMPORTED_PAIR.member_ids) == 2


def test_patching_the_imported_predicate_excludes_a_local_id_at_both_sites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    _write(bundle, LOCAL, "type: Concept\ntitle: Skill")
    monkeypatch.setattr(bundle_imports, "is_imported_concept", lambda _id: True)

    assert _lookup(bundle).find("Concept", "Skill") == ()
    assert auto_merge.in_structural_class(_LOCAL_PAIR) is False


# --------------------------------------------------------------------------- #
# the survivor rule
# --------------------------------------------------------------------------- #


def _bundle_with(tmp_path: Path, docs: dict[str, str]) -> Path:
    bundle = tmp_path / "bundle"
    for concept_id, body in docs.items():
        _write(bundle, concept_id, "type: Concept\ntitle: T", body)
    return bundle


@pytest.mark.parametrize("flip_ids", [False, True], ids=["ids as is", "ids flipped"])
@pytest.mark.parametrize("flip_args", [False, True], ids=["args", "args flipped"])
def test_the_local_member_survives_the_richer_imported_one(
    tmp_path: Path, flip_ids: bool, flip_args: bool
) -> None:
    local, imported = ("zz/local", "imports/acme/a/imp")
    if flip_ids:
        local, imported = ("a/local", "imports/acme/z/imp")
    bundle = _bundle_with(tmp_path, {local: "short", imported: "much longer body " * 9})
    members = (imported, local) if flip_args else (local, imported)

    survivor, absorbed, criterion = lifecycle.ordered_merge_pair(bundle, members)

    assert (survivor, absorbed) == (local, imported)
    assert criterion == "local over imported"


def test_a_missing_local_member_still_survives_an_imported_one(
    tmp_path: Path,
) -> None:
    bundle = _bundle_with(tmp_path, {"imports/acme/a/imp": "body"})
    survivor, _absorbed, criterion = lifecycle.ordered_merge_pair(
        bundle, ("a/local", "imports/acme/a/imp")
    )
    assert survivor == "a/local"
    assert criterion == "local over imported"


def test_the_family_rule_still_outranks_local_over_imported(tmp_path: Path) -> None:
    bundle = _bundle_with(
        tmp_path, {"imports/acme/c/x": "b", "imports/acme/c/x-2": "b" * 50}
    )
    assert lifecycle.ordered_merge_pair(
        bundle, ("imports/acme/c/x-2", "imports/acme/c/x")
    ) == ("imports/acme/c/x", "imports/acme/c/x-2", lifecycle._SUFFIX_FAMILY_CRITERION)


@pytest.mark.parametrize(
    "ids",
    [
        pytest.param(("imports/acme/a/p", "imports/acme/a/q"), id="both imported"),
        pytest.param(("a/p", "a/q"), id="both local"),
    ],
)
def test_same_origin_pairs_use_the_existing_rules(
    tmp_path: Path, ids: tuple[str, str]
) -> None:
    bundle = _bundle_with(tmp_path, {ids[0]: "short", ids[1]: "much longer body " * 4})
    assert lifecycle.ordered_merge_pair(bundle, ids) == (
        ids[1],
        ids[0],
        "richer body",
    )


def test_an_unreadable_member_still_ranks_below_a_readable_one(
    tmp_path: Path,
) -> None:
    bundle = _bundle_with(tmp_path, {"a/p": "body"})
    assert lifecycle.ordered_merge_pair(bundle, ("a/ghost", "a/p"))[2] == "richer body"


def test_a_merge_leaves_a_local_survivor_that_is_not_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    layout = config.WorkspaceLayout(root)
    local = write_concept(root, "concepts/skill", title="Skill", body="Local.")
    assert local.exists()
    imported = layout.bundle_dir / f"{IMPORTED}.md"
    imported.parent.mkdir(parents=True)
    imported.write_text(
        "---\ntype: Concept\ntitle: Skill\nsensitivity: confidential\nprovenance:\n- imports/acme--bundle\n"
        "imported:\n  from: acme\n---\n\n# Skill\n\n" + "Imported body. " * 20 + "\n",
        encoding="utf-8",
    )
    group = _group(LOCAL, IMPORTED)
    survivor, absorbed, criterion = lifecycle.ordered_merge_pair(
        layout.bundle_dir, group.member_ids
    )
    assert (survivor, absorbed, criterion) == (LOCAL, IMPORTED, "local over imported")

    prepared = lifecycle.prepare_one_merge(
        root,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        group,
        ordered_pair=(survivor, absorbed),
    )
    assert prepared is not None
    lifecycle.merge_core(
        layout.bundle_dir,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        prepared,
    )

    assert not imported.exists()
    text = local.read_text(encoding="utf-8")
    assert "imports/acme--bundle" in text
    assert "imported:" not in text
    assert "sensitivity: confidential" in text
    assert bundle_imports.is_imported_concept(LOCAL) is False


# --------------------------------------------------------------------------- #
# the measured population
# --------------------------------------------------------------------------- #


def _load_eval(monkeypatch: pytest.MonkeyPatch) -> Any:
    name = "_run_structural_class_for_import_pin"
    path = _EVAL_DIR / "run_structural_class.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.syspath_prepend(str(_EVAL_DIR))
    spec.loader.exec_module(module)
    return module


def test_the_measured_population_cannot_silently_shift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The harness still classifies with the production predicate, and no
    fixture document of either harness carries an `imports/` Concept ID."""
    module = _load_eval(monkeypatch)
    assert module.in_structural_class is auto_merge.in_structural_class
    ids = [doc.concept_id for doc in module.documents()]
    ids += [
        doc.concept_id
        for pair in module.STRUCTURAL_PAIRS
        for doc in (pair.left, pair.right)
    ]
    assert ids
    assert not [i for i in ids if bundle_imports.is_imported_concept(i)]
