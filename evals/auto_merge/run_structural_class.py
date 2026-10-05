"""The structural auto-merge class (#1298): predicate, exposure, and the
pre-registered decision rules -- model-free.

`PREREGISTRATION-1298.md` (beside this file) is the contract; this module
encodes the parts of it that need no model, so they are checked before any
live run:

- `in_structural_class(group)` -- the class, built only from existing
  production code: `Tier.HIGH` (same normalized key), two members, one OKF
  type outside `application.ingest.ATTACH_EXCLUDED_TYPES`, and a base/`-N`
  family by `resolution.normalize.is_suffix_family`.
- exposure, as `n of TOTAL` labelled pairs per fixture: the #1054 fixture and
  `evals/adjudication`'s fixture hold none of the class, which is why
  `structural_fixtures.py` exists.
- `decide_structural` -- the #1054 rule (`run_auto_merge_eval.decide`, frozen,
  called unchanged) on the in-class population only, plus bar S2 (the named
  negative) and the latency bar L1.
- `decide_recommended` -- the separate bars for Identity "accept recommended".

The live arms are NOT here: they call a model. They are a task in the
pre-registration ("What must be built before the run").

    uv run python evals/auto_merge/run_structural_class.py --self-test
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Final

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import run_auto_merge_eval as base  # noqa: E402
from auto_merge_fixtures import PAIRS, FixtureDoc, LabelledPair, documents  # noqa: E402
from structural_fixtures import (  # noqa: E402
    NAMED_NEGATIVE_PROBE,
    STRUCTURAL_NEGATIVE_PROBES,
    STRUCTURAL_PAIRS,
    STRUCTURAL_POSITIVE_PROBES,
)

from openkos.application.ingest import ATTACH_EXCLUDED_TYPES  # noqa: E402
from openkos.resolution import candidates as candidates_mod  # noqa: E402
from openkos.resolution.candidates import CandidateGroup, Tier  # noqa: E402
from openkos.resolution.normalize import is_suffix_family  # noqa: E402

LATENCY_FACTOR: Final[float] = 3.0
"""L1: the candidate arm's median per-run wall-clock is at most this multiple
of the `qwen3:8b` reference arm's, same fixture, same session -- the identity
budget #1269 pre-registered ("<= 3x")."""

RECOMMENDED_FALSE_RATE_MAX: Final[float] = 0.01
RECOMMENDED_PERSISTENCE_MAX: Final[int] = 1
RECOMMENDED_RECALL_MIN: Final[float] = 0.90


# --------------------------------------------------------------------------- #
# the class
# --------------------------------------------------------------------------- #


def in_structural_class(group: CandidateGroup) -> bool:
    """Whether `group` is in the #1298 class. Structural only: no verdict, no
    confidence, no file read. `cross_type_concern` and the stacked-body
    guardrail stay separate production checks (ADR-0034's eligibility), which
    the fixture self-test asserts for every labelled pair."""
    if group.tier is not Tier.HIGH or len(group.member_ids) != 2:
        return False
    types = set(group.member_types)
    if len(types) != 1 or types & ATTACH_EXCLUDED_TYPES:
        return False
    first, second = group.member_ids
    return is_suffix_family(first, second) or is_suffix_family(second, first)


@dataclass(frozen=True)
class Exposure:
    """How many labelled pairs of one fixture fall in the class."""

    fixture: str
    in_class: int
    total: int
    in_class_negative: int
    total_negative: int

    def line(self) -> str:
        return (
            f"{self.fixture}: {self.in_class} of {self.total} pairs in class "
            f"({self.in_class_negative} of {self.total_negative} negatives)"
        )


def exposure(
    fixture: str,
    pairs: Sequence[LabelledPair],
    groups: Iterable[CandidateGroup],
) -> Exposure:
    """Count `pairs` whose discovered group is in the class. A pair whose two
    ids land in a larger group is matched by subset, and that group (more than
    two members) is out of class -- the honest reading of a pair the
    production walk would never see as a pair."""
    group_list = list(groups)
    in_class = 0
    in_class_negative = 0
    for pair in pairs:
        ids = {pair.left.concept_id, pair.right.concept_id}
        group = next((g for g in group_list if ids <= set(g.member_ids)), None)
        if group is not None and in_structural_class(group):
            in_class += 1
            if pair.expected == "different":
                in_class_negative += 1
    return Exposure(
        fixture=fixture,
        in_class=in_class,
        total=len(pairs),
        in_class_negative=in_class_negative,
        total_negative=sum(1 for p in pairs if p.expected == "different"),
    )


def in_class_pair_ids(
    pairs: Sequence[LabelledPair] = STRUCTURAL_PAIRS,
) -> frozenset[str]:
    return frozenset(base._pair_id(pair) for pair in pairs)


# --------------------------------------------------------------------------- #
# decision rules
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StructuralDecision:
    verdict: str
    """`PASS`, `FAIL`, or `INVALID` -- `PASS` only when the #1054 rule passes
    on the in-class population AND S2 and L1 hold."""
    reasons: tuple[str, ...]
    core: base.Decision
    """The frozen #1054 rule's own output on the in-class population."""
    s2_named_negative_merges: int
    l1_ratio: float | None


def _filter(arm: base.ArmData, pair_ids: frozenset[str]) -> base.ArmData:
    return base.ArmData(
        arm=arm.arm,
        model=arm.model,
        fixture_digest=arm.fixture_digest,
        runs=tuple(tuple(t for t in run if t.pair_id in pair_ids) for run in arm.runs),
    )


def latency_ratio(
    candidate_run_seconds: Sequence[float], reference_run_seconds: Sequence[float]
) -> float:
    """Median per-run wall-clock of the candidate over the reference's."""
    return statistics.median(candidate_run_seconds) / statistics.median(
        reference_run_seconds
    )


def decide_structural(
    calibration: base.ArmData,
    confirmation: base.ArmData,
    *,
    pair_ids: frozenset[str],
    latency: float | None,
) -> StructuralDecision:
    """The pre-registered rule for the class (PREREGISTRATION-1298.md,
    "Decision rule"). Step 1 and R0-R5 are `base.decide`, unchanged, on the
    trials whose pair is in `pair_ids`. R3 (`week-apart`) has no exposure in
    that population and is replaced by S2 on the class's own named negative.
    `latency` is `latency_ratio(...)`, or `None` when the reference arm was
    not run, which is a FAIL (L1 unmeasured is never a pass)."""
    core = base.decide(_filter(calibration, pair_ids), _filter(confirmation, pair_ids))
    if core.verdict == "INVALID":
        return StructuralDecision("INVALID", core.reasons, core, 0, latency)
    reasons = list(core.reasons)
    s2 = 0
    if core.t_star is not None:
        t_star = core.t_star
        s2 = sum(
            1
            for arm in (calibration, confirmation)
            for t in arm.trials
            if t.pair_id in pair_ids
            and t.probe == NAMED_NEGATIVE_PROBE
            and base._is_auto_merge(t, t_star)
        )
        if s2:
            reasons.append(
                f"S2 named negative: {s2} {NAMED_NEGATIVE_PROBE} trial(s) "
                f"auto-merged at t*={t_star:.4f}"
            )
    if latency is None:
        reasons.append("L1 latency: reference arm not measured")
    elif latency > LATENCY_FACTOR:
        reasons.append(f"L1 latency: {latency:.2f}x > {LATENCY_FACTOR:.1f}x")
    return StructuralDecision(
        "PASS" if not reasons else "FAIL", tuple(reasons), core, s2, latency
    )


@dataclass(frozen=True)
class RecommendedDecision:
    verdict: str
    reasons: tuple[str, ...]
    false_rate: float
    worst_negative_pair: int
    recall: float


def decide_recommended(
    calibration: base.ArmData,
    confirmation: base.ArmData,
    *,
    pair_ids: frozenset[str],
) -> RecommendedDecision:
    """Identity "accept recommended" (PREREGISTRATION-1298.md, A0-A4). The
    recommended set is the in-class groups the judge calls `same`, at any
    confidence: a person still confirms the list, so no threshold is fitted.
    Both arms are pooled (no threshold is chosen, so nothing is circular)."""
    invalid = [
        f"{arm.arm} arm has <missing> trial(s)"
        for arm in (calibration, confirmation)
        if any(t.verdict == base._MISSING for t in arm.trials)
    ]
    if calibration.fixture_digest != confirmation.fixture_digest:
        invalid.append("fixture digest differs between arms")
    if calibration.model != confirmation.model:
        invalid.append("model differs between arms")
    if invalid:
        return RecommendedDecision("INVALID", tuple(invalid), 0.0, 0, 0.0)

    trials = [
        t
        for arm in (calibration, confirmation)
        for t in arm.trials
        if t.pair_id in pair_ids
    ]
    negatives = [t for t in trials if t.expected == "different"]
    positives = [t for t in trials if t.expected == "same"]
    reasons: list[str] = []

    named = sum(
        1 for t in negatives if t.probe == NAMED_NEGATIVE_PROBE and t.verdict == "same"
    )
    if named:
        reasons.append(
            f"A1 named negative: {named} {NAMED_NEGATIVE_PROBE} trial(s) recommended"
        )

    false = sum(1 for t in negatives if t.verdict == "same")
    false_rate = false / len(negatives) if negatives else 1.0
    if not negatives or false_rate > RECOMMENDED_FALSE_RATE_MAX:
        reasons.append(
            f"A2 false recommendations: {false} of {len(negatives)} negative trials"
        )

    per_pair: dict[str, int] = defaultdict(int)
    for t in negatives:
        if t.verdict == "same":
            per_pair[t.pair_id] += 1
    worst = max(per_pair.values(), default=0)
    if worst > RECOMMENDED_PERSISTENCE_MAX:
        reasons.append(f"A3 persistence: one negative pair recommended {worst} times")

    recall = (
        sum(1 for t in positives if t.verdict == "same") / len(positives)
        if positives
        else 0.0
    )
    if recall < RECOMMENDED_RECALL_MIN:
        reasons.append(f"A4 recall: {recall:.4f} < {RECOMMENDED_RECALL_MIN:.2f}")

    return RecommendedDecision(
        "PASS" if not reasons else "FAIL", tuple(reasons), false_rate, worst, recall
    )


# --------------------------------------------------------------------------- #
# self-test -- no model, no network
# --------------------------------------------------------------------------- #


def _group(
    member_ids: tuple[str, ...],
    okf_type: str = "Concept",
    *,
    tier: Tier = Tier.HIGH,
    member_types: tuple[str, ...] = (),
) -> CandidateGroup:
    return CandidateGroup(
        okf_type=okf_type,
        member_ids=member_ids,
        tier=tier,
        trigger="key",
        member_types=member_types,
    )


def _self_test_predicate(failures: list[str]) -> None:
    """Each criterion shown able to exclude a group on its own."""
    cases: list[tuple[str, CandidateGroup, bool]] = [
        ("base/-2 Concept", _group(("concepts/x", "concepts/x-2")), True),
        ("base/-13 Project", _group(("projects/x", "projects/x-13"), "Project"), True),
        ("suffixed id listed first", _group(("concepts/x-2", "concepts/x")), True),
        ("LOW tier", _group(("concepts/x", "concepts/x-2"), tier=Tier.LOW), False),
        (
            "ACRONYM tier",
            _group(("concepts/x", "concepts/x-2"), tier=Tier.ACRONYM),
            False,
        ),
        (
            "three members",
            _group(("concepts/x", "concepts/x-2", "concepts/x-3")),
            False,
        ),
        ("Event", _group(("events/x", "events/x-2"), "Event"), False),
        ("Person", _group(("people/x", "people/x-2"), "Person"), False),
        (
            "cross-type",
            _group(
                ("concepts/x", "concepts/x-2"),
                "Concept+Entity",
                member_types=("Concept", "Entity"),
            ),
            False,
        ),
        ("-a/-b, not a suffix family", _group(("concepts/x-a", "concepts/x-b")), False),
        ("-1/-2 siblings", _group(("concepts/x-1", "concepts/x-2")), False),
        ("non-digit suffix", _group(("concepts/x", "concepts/x-2a")), False),
        ("other directory", _group(("concepts/x", "entities/x-2")), False),
    ]
    for label, group, want in cases:
        base._check_list(
            failures, f"predicate: {label}", in_structural_class(group), want
        )


def _assert_fixture_shape(failures: list[str]) -> None:
    by_probe: dict[str, list[LabelledPair]] = defaultdict(list)
    for pair in STRUCTURAL_PAIRS:
        by_probe[pair.probe].append(pair)
    base._check_list(
        failures,
        "every structural probe is populated, no other",
        sorted(by_probe),
        sorted(STRUCTURAL_NEGATIVE_PROBES + STRUCTURAL_POSITIVE_PROBES),
    )
    negatives = [p for p in STRUCTURAL_PAIRS if p.expected == "different"]
    positives = [p for p in STRUCTURAL_PAIRS if p.expected == "same"]
    base._check_list(
        failures, "at least 12 in-class negative pairs", len(negatives) >= 12, True
    )
    base._check_list(
        failures, "at least 10 in-class positive pairs", len(positives) >= 10, True
    )
    base._check_list(
        failures,
        "every negative probe has at least 2 pairs",
        all(len(by_probe[p]) >= 2 for p in STRUCTURAL_NEGATIVE_PROBES),
        True,
    )
    base._check_list(
        failures,
        "the named negative has at least 3 pairs",
        len(by_probe[NAMED_NEGATIVE_PROBE]) >= 3,
        True,
    )
    base._check_list(
        failures,
        "negatives span at least 4 OKF types",
        len({p.left.okf_type for p in negatives}) >= 4,
        True,
    )
    for probe in STRUCTURAL_NEGATIVE_PROBES:
        base._check_list(
            failures,
            f"{probe} is labelled different",
            {p.expected for p in by_probe[probe]},
            {"different"},
        )
    for probe in STRUCTURAL_POSITIVE_PROBES:
        base._check_list(
            failures,
            f"{probe} is labelled same",
            {p.expected for p in by_probe[probe]},
            {"same"},
        )


def _adjudication_docs_and_pairs() -> tuple[
    tuple[FixtureDoc, ...], tuple[LabelledPair, ...]
]:
    """`evals/adjudication`'s pairs in this harness's shapes, for exposure."""
    from adjudication_fixtures import PAIRS as ADJ_PAIRS

    docs: dict[str, FixtureDoc] = {}
    pairs: list[LabelledPair] = []
    for pair in ADJ_PAIRS:
        converted = []
        for doc in (pair.left, pair.right):
            fixture_doc = FixtureDoc(
                concept_id=doc.concept_id,
                okf_type=doc.okf_type,
                title=doc.title,
                body=doc.body,
                provenance=(f"raw/{doc.concept_id.replace('/', '-')}-source.md",),
            )
            docs.setdefault(doc.concept_id, fixture_doc)
            converted.append(fixture_doc)
        pairs.append(
            LabelledPair(
                converted[0], converted[1], pair.probe, pair.expected, pair.note
            )
        )
    return tuple(docs.values()), tuple(pairs)


def _self_test_fixture(failures: list[str], stack: ExitStack) -> list[Exposure]:
    """The structural fixture materializes into its OWN bundle, where every
    pair forms exactly one D3-eligible 2-member group and is in the class;
    the two existing fixtures, each in its own bundle, have none in it.

    One bundle per fixture, not one combined: `find_candidates` keeps at most
    `_MAX_CANDIDATE_GROUPS` (50) groups, and the 54 pairs of both fixtures
    together would truncate the LOW-tier ones. A live run therefore
    materializes the fixtures separately, and the margin is asserted here."""
    roots = [
        pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory())) / name
        for name in ("a", "b", "legacy", "adjudication")
    ]
    structural_docs = documents(STRUCTURAL_PAIRS)
    layout_a = base._materialize_workspace(roots[0], structural_docs)
    layout_b = base._materialize_workspace(roots[1], structural_docs)
    base._check_list(
        failures,
        "structural fixture digest is stable across two materializations",
        base._fixture_digest(layout_a.bundle_dir),
        base._fixture_digest(layout_b.bundle_dir),
    )
    base._assert_d3_structural_eligibility(failures, roots[0], STRUCTURAL_PAIRS)

    groups = candidates_mod.find_candidates(layout_a.bundle_dir)
    base._check_list(
        failures,
        "structural bundle stays under the candidate-group cap",
        len(groups) < candidates_mod._MAX_CANDIDATE_GROUPS,
        True,
    )
    structural = exposure("structural_fixtures (#1298)", STRUCTURAL_PAIRS, groups)
    base._check_list(
        failures,
        "every structural pair is in the class",
        structural.in_class,
        len(STRUCTURAL_PAIRS),
    )

    legacy_layout = base._materialize_workspace(roots[2])
    legacy = exposure(
        "auto_merge_fixtures (#1054)",
        PAIRS,
        candidates_mod.find_candidates(legacy_layout.bundle_dir),
    )
    base._check_list(failures, "no #1054 pair is in the class", legacy.in_class, 0)

    adj_docs, adj_pairs = _adjudication_docs_and_pairs()
    adj_layout = base._materialize_workspace(roots[3], adj_docs)
    adjudication = exposure(
        "adjudication_fixtures",
        adj_pairs,
        candidates_mod.find_candidates(adj_layout.bundle_dir),
    )
    base._check_list(
        failures, "no adjudication pair is in the class", adjudication.in_class, 0
    )
    return [structural, legacy, adjudication]


_IN: Final[frozenset[str]] = frozenset(
    {f"neg-{i}" for i in range(12)} | {"named-0", "pos-0", "pos-1", "pos-2"}
)


def _t(
    pair_id: str, probe: str, expected: str, verdict: str, conf: float
) -> base.TrialRecord:
    return base._trial(pair_id, probe, expected, verdict, conf)


def _arm(
    name: str, extra: Sequence[base.TrialRecord] = (), runs: int = 15
) -> base.ArmData:
    """`runs` runs of 12 safe in-class negatives, one safe named negative and
    three in-class positives merging at 0.97, plus `extra` in the first run."""
    out: list[tuple[base.TrialRecord, ...]] = []
    for index in range(runs):
        row = [
            _t(f"neg-{i}", "key-homonym", "different", "different", 0.1)
            for i in range(12)
        ]
        row.append(_t("named-0", NAMED_NEGATIVE_PROBE, "different", "different", 0.1))
        row += [
            _t(f"pos-{i}", "key-cross-source-dup", "same", "same", 0.97)
            for i in range(3)
        ]
        if index == 0:
            row += list(extra)
        out.append(tuple(row))
    return base.ArmData(name, "gemma4:26b-a4b", "sha256:f", tuple(out))


def _self_test_decide(failures: list[str]) -> None:
    cal_b = _t("neg-0", "key-homonym", "different", "same", 0.60)
    cal = _arm("calibration", [cal_b])
    ok = decide_structural(cal, _arm("confirmation"), pair_ids=_IN, latency=1.2)
    base._check_list(
        failures, "structural PASS", (ok.verdict, ok.reasons), ("PASS", ())
    )
    base._check_list(failures, "structural PASS t*", ok.core.t_star, 0.97)

    out_of_class = _t("events/x+events/x-2", "recurrence", "different", "same", 0.99)
    filtered = decide_structural(
        cal, _arm("confirmation", [out_of_class]), pair_ids=_IN, latency=1.2
    )
    base._check_list(
        failures,
        "an out-of-class false merge does not decide",
        filtered.verdict,
        "PASS",
    )

    in_class = _t("neg-3", "key-homonym", "different", "same", 0.99)
    r2 = decide_structural(
        cal, _arm("confirmation", [in_class]), pair_ids=_IN, latency=1.2
    )
    base._check_list(failures, "in-class false merge FAILs", r2.verdict, "FAIL")
    base._check_list(failures, "in-class false merge is R2", r2.core.r2_false_merges, 1)

    named = _t("named-0", NAMED_NEGATIVE_PROBE, "different", "same", 0.98)
    s2 = decide_structural(
        cal, _arm("confirmation", [named]), pair_ids=_IN, latency=1.2
    )
    base._check_list(failures, "named negative FAILs", s2.verdict, "FAIL")
    base._check_list(
        failures, "named negative counted by S2", s2.s2_named_negative_merges, 1
    )

    thin = frozenset(_IN - {f"neg-{i}" for i in range(6)})
    r1 = decide_structural(cal, _arm("confirmation"), pair_ids=thin, latency=1.2)
    base._check_list(
        failures,
        "thin in-class exposure FAILs R1",
        any(r.startswith("R1") for r in r1.reasons),
        True,
    )

    for label, value in (("slow", 3.5), ("unmeasured", None)):
        slow = decide_structural(cal, _arm("confirmation"), pair_ids=_IN, latency=value)
        base._check_list(failures, f"L1 {label} FAILs", slow.verdict, "FAIL")
    base._check_list(
        failures,
        "latency_ratio is median over median",
        latency_ratio([3, 9, 6], [2, 2, 4]),
        3.0,
    )

    missing = _t("pos-0", "key-cross-source-dup", "same", base._MISSING, 0.0)
    invalid = decide_structural(
        cal, _arm("confirmation", [missing]), pair_ids=_IN, latency=1.2
    )
    base._check_list(failures, "missing trial is INVALID", invalid.verdict, "INVALID")


def _self_test_recommended(failures: list[str]) -> None:
    def verdict(extra: Sequence[base.TrialRecord]) -> RecommendedDecision:
        return decide_recommended(
            _arm("calibration"), _arm("confirmation", extra), pair_ids=_IN
        )

    base._check_list(failures, "recommended PASS", verdict([]).verdict, "PASS")
    outside = [
        _t("events/x+events/x-2", "recurrence", "different", "same", 0.9)
        for _ in range(5)
    ]
    base._check_list(
        failures,
        "an out-of-class false recommendation does not decide",
        verdict(outside).verdict,
        "PASS",
    )
    named = verdict([_t("named-0", NAMED_NEGATIVE_PROBE, "different", "same", 0.5)])
    base._check_list(failures, "recommended A1", [r[:2] for r in named.reasons], ["A1"])
    # 30 runs x 13 negatives = 390 negative trials; 4 single-run misses on
    # four different pairs is above 1% and below the persistence cap.
    spread = verdict(
        [_t(f"neg-{i}", "key-homonym", "different", "same", 0.5) for i in range(4)]
    )
    base._check_list(
        failures, "recommended A2", [r[:2] for r in spread.reasons], ["A2"]
    )
    persistent = decide_recommended(
        _arm("calibration", [_t("neg-1", "key-homonym", "different", "same", 0.5)]),
        _arm("confirmation", [_t("neg-1", "key-homonym", "different", "same", 0.5)]),
        pair_ids=_IN,
    )
    base._check_list(
        failures, "recommended A3", [r[:2] for r in persistent.reasons], ["A3"]
    )
    row = (
        *(
            _t(f"pos-{i}", "key-cross-source-dup", "same", "different", 0.5)
            for i in range(3)
        ),
        _t("neg-0", "key-homonym", "different", "different", 0.1),
    )
    low = decide_recommended(
        base.ArmData("calibration", "m", "sha256:f", (row,) * 15),
        base.ArmData("confirmation", "m", "sha256:f", (row,) * 15),
        pair_ids=_IN,
    )
    base._check_list(failures, "recommended A4", [r[:2] for r in low.reasons], ["A4"])
    missing = verdict([_t("pos-0", "key-cross-source-dup", "same", base._MISSING, 0.0)])
    base._check_list(failures, "recommended INVALID", missing.verdict, "INVALID")


def _self_test() -> int:
    failures: list[str] = []
    _self_test_predicate(failures)
    _assert_fixture_shape(failures)
    with ExitStack() as stack:
        exposures = _self_test_fixture(failures, stack)
    _self_test_decide(failures)
    _self_test_recommended(failures)
    if failures:
        print("self-test FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("self-test OK. Class exposure, n of TOTAL labelled pairs:")
    for item in exposures:
        print(f"  {item.line()}")
    print(
        "The structural fixture forms one D3-eligible 2-member group per pair; "
        "decide_structural reaches PASS, FAIL R1/R2/S2/L1 and INVALID, and an "
        "out-of-class false merge does not decide; decide_recommended reaches "
        "PASS, FAIL A1-A4 and INVALID."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the class predicate, the fixture's exposure and both decision rules, with no model",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    parser.error(
        "only --self-test exists until the live arms are built (see PREREGISTRATION-1298.md)"
    )


if __name__ == "__main__":
    raise SystemExit(main())
