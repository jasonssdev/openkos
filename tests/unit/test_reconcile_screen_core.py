"""`evals/reconcile_screen/`: the deterministic half of the merged-body
reconciliation screen (#1563) -- the fixture contract, the scoring of one merged
body, the model-free baselines, the fabrication check, the paired bootstrap, the
vetoes, the forecast and the blind labelling sheet.

These are the parts a paid run rests on and a model cannot exercise, so they are
executed here rather than trusted. Both modules are loaded straight from their
paths, like `edge_screen_core` in `test_edge_typing_screen_core.py`.
"""

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DIR = _REPO_ROOT / "evals" / "reconcile_screen"


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _DIR / filename)
    assert spec is not None, filename
    assert spec.loader is not None, filename
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load("reconcile_core", "reconcile_core.py")
adj = _load("reconcile_blind_adjudication", "reconcile_blind_adjudication.py")


def _claim(
    claim_id: str = "k-01",
    kind: str = "a_only",
    anchors: tuple[str, ...] = ("alpha",),
    once: str | None = None,
    alt: str | None = None,
    adjudication: dict[str, str] | None = None,
    retired: str | None = None,
) -> Any:
    return core.Claim(
        id=claim_id,
        kind=kind,
        claim="a neutral statement",
        anchors=anchors,
        once=once,
        alt_label=alt,
        adjudication=adjudication,
        retired=retired,
    )


def _case(
    claims: tuple[Any, ...],
    a: str = "alpha one. beta one.",
    b: str = "beta two. gamma two.",
) -> Any:
    return core.Case(
        id="c1",
        title="A title",
        survivor=(core.Piece(source={}, text=a),),
        absorbed=(core.Piece(source={}, text=b),),
        claims=claims,
    )


# --- the real fixture ----------------------------------------------------------


def test_the_fixture_loads_and_is_coherent() -> None:
    cases = core.load_fixture()
    assert core.integrity_problems(cases) == []
    assert len(cases) >= 12
    assert sum(len(c.claims) for c in cases) >= 100


def test_every_piece_is_still_a_verbatim_quotation() -> None:
    assert core.provenance_problems(core.load_fixture()) == []


def test_a_piece_that_drifted_from_its_source_is_reported() -> None:
    cases = core.load_fixture()
    piece = cases[0].survivor[0]
    drifted = core.Piece(
        source=piece.source, text=piece.text + " and a sentence nobody wrote"
    )
    mutated = [
        core.Case(
            id=cases[0].id,
            title=cases[0].title,
            survivor=(drifted, *cases[0].survivor[1:]),
            absorbed=cases[0].absorbed,
            claims=cases[0].claims,
        ),
        *cases[1:],
    ]
    problems = core.provenance_problems(mutated)
    assert len(problems) == 1
    assert cases[0].id in problems[0]


# --- the fixture contract --------------------------------------------------------


def test_integrity_accepts_a_well_formed_case() -> None:
    case = _case(
        (
            _claim("k-01", "a_only", ("alpha",)),
            _claim("k-02", "b_only", ("gamma",)),
            _claim("k-03", "both", ("beta",), once="beta"),
        )
    )
    assert core.integrity_problems([case]) == []


@pytest.mark.parametrize(
    ("claims", "needle"),
    [
        ((_claim("k-01", "a_only", ("absent",)),), "not in the survivor"),
        ((_claim("k-01", "a_only", ("beta",)),), "also in the absorbed"),
        ((_claim("k-01", "b_only", ("alpha",)),), "not in the absorbed"),
        ((_claim("k-01", "b_only", ("beta",)),), "also in the survivor"),
        ((_claim("k-01", "both", ("alpha",), once="alpha"),), "not in the absorbed"),
        ((_claim("k-01", "both", ("beta",), once=None),), "needs a `once`"),
        ((_claim("k-01", "a_only", ("alpha",), once="alpha"),), "only a `both` claim"),
        (
            (
                _claim("k-01", "both", ("beta",), once="beta"),
                _claim("k-01", "a_only", ("alpha",)),
            ),
            "duplicate",
        ),
        ((_claim("k-01", "a_only", ("A title",)),), "title"),
        ((_claim("k-01", "a_only", ()),), "no anchors"),
        ((_claim("k-01", "oops", ("alpha",)),), "unknown kind"),
    ],
)
def test_integrity_names_each_way_a_claim_can_be_unscoreable(
    claims: tuple[Any, ...], needle: str
) -> None:
    problems = core.integrity_problems([_case(claims)])
    assert any(needle in p for p in problems), problems


def test_a_once_literal_that_repeats_inside_a_note_is_unscoreable() -> None:
    case = _case(
        (_claim("k-01", "both", ("beta",), once="beta"),),
        a="beta here. beta again.",
        b="beta there.",
    )
    assert any("exactly once" in p for p in core.integrity_problems([case]))


def test_retired_claims_are_skipped_by_integrity_and_scoring() -> None:
    gone = _claim(
        "k-02", "a_only", ("absent",), retired="no literal survives the relabel"
    )
    case = _case((_claim("k-01", "a_only", ("alpha",)), gone))
    assert core.integrity_problems([case]) == []
    assert core.case_score(case, "alpha", which="strict") == 1.0


# --- scoring one merged body -------------------------------------------------------


def test_one_sided_claim_is_kept_iff_every_anchor_survives() -> None:
    claim = _claim(kind="a_only", anchors=("alpha", "one"))
    assert core.score_claim(claim, "ALPHA   is  one") == (True, True)
    assert core.score_claim(claim, "alpha only") == (False, False)
    assert core.score_claim(claim, "") == (False, False)


def test_a_shared_claim_must_be_stated_exactly_once() -> None:
    claim = _claim(kind="both", anchors=("beta",), once="beta")
    assert core.score_claim(claim, "beta") == (True, True)
    assert core.score_claim(claim, "beta then beta") == (False, False)
    assert core.score_claim(claim, "nothing") == (False, False)


def test_lenient_accepts_a_flagged_claim_that_is_kept_in_any_number() -> None:
    claim = _claim(kind="both", anchors=("beta",), once="beta", alt="a_only")
    assert core.score_claim(claim, "beta beta") == (False, True)
    plain = _claim(kind="both", anchors=("beta",), once="beta")
    assert core.score_claim(plain, "beta beta") == (False, False)
    assert core.score_claim(claim, "nothing") == (False, False)


def test_scoring_against_another_kind_is_possible_for_the_sensitivity_analysis() -> (
    None
):
    claim = _claim(
        kind="both",
        anchors=("beta",),
        once="beta",
        alt="a_only",
        adjudication={
            "first": "a_only",
            "second": "both",
            "resolved": "both",
            "reason": "r",
        },
    )
    assert claim.first_kind == "a_only"
    assert core.score_claim(claim, "beta beta") == (False, True)
    assert core.score_claim(claim, "beta beta", kind=claim.first_kind)[0] is True


def test_case_score_is_the_share_of_claims_kept() -> None:
    case = _case(
        (
            _claim("k-01", "a_only", ("alpha",)),
            _claim("k-02", "b_only", ("gamma",)),
            _claim("k-03", "both", ("beta",), once="beta"),
            _claim("k-04", "b_only", ("gamma two",)),
        )
    )
    assert core.case_score(case, "alpha gamma two beta") == pytest.approx(1.0)
    assert core.case_score(case, "alpha beta beta") == pytest.approx(1 / 4)
    assert core.case_score(case, "") == 0.0


# --- the model-free baselines ------------------------------------------------------


def test_the_stack_is_what_production_keeps_when_a_reply_is_refused() -> None:
    case = _case((_claim(),))
    stack = core.stack_text(case)
    assert case.survivor_body in stack
    assert case.absorbed_body in stack
    assert stack.index(case.survivor_body) < stack.index(case.absorbed_body)
    assert "## Merged content (" in stack


def test_the_stack_keeps_every_one_sided_claim_and_repeats_every_shared_one() -> None:
    case = _case(
        (
            _claim("k-01", "a_only", ("alpha",)),
            _claim("k-02", "b_only", ("gamma",)),
            _claim("k-03", "both", ("beta",), once="beta"),
        )
    )
    scores = core.baseline_claim_scores([case])["stack"]
    assert scores == {"k-01": True, "k-02": True, "k-03": False}


def test_a_one_note_baseline_loses_the_other_notes_claims() -> None:
    case = _case(
        (
            _claim("k-01", "a_only", ("alpha",)),
            _claim("k-02", "b_only", ("gamma",)),
            _claim("k-03", "both", ("beta",), once="beta"),
        )
    )
    result = core.baseline_claim_scores([case])
    assert result["survivor_only"] == {"k-01": True, "k-02": False, "k-03": True}
    assert result["absorbed_only"] == {"k-01": False, "k-02": True, "k-03": True}


def test_sentence_union_drops_a_restated_sentence_and_keeps_a_new_one() -> None:
    survivor = "The cloud is optional and never required for the system."
    absorbed = "The cloud is never required for the system. Keys live in a keychain."
    merged = core.sentence_union(survivor, absorbed)
    assert merged.count("cloud") == 1
    assert "keychain" in merged
    assert core.sentence_union(survivor, absorbed) == merged


def test_baselines_report_k_of_n_and_name_the_strongest() -> None:
    cases = core.load_fixture()
    summary = core.baseline_summary(cases)
    n = sum(1 for c in cases for cl in c.claims if not cl.retired)
    for name, row in summary["baselines"].items():
        assert 0 <= row["claims_kept"] <= n, name
        assert row["n_claims"] == n
    best = summary["strongest"]
    assert summary["baselines"][best]["mean_case_score"] == max(
        r["mean_case_score"] for r in summary["baselines"].values()
    )


def test_the_fixtures_shape_and_floors_are_pinned() -> None:
    """The numbers the README and the pre-registration quote. They are facts about
    the committed fixture; a change to it (or to a baseline) must change them
    here, on purpose, and in the documents."""
    cases = core.load_fixture()
    assert len(cases) == 15
    assert core.class_balance(cases) == {"a_only": 42, "b_only": 46, "both": 33}
    summary = core.baseline_summary(cases)
    assert {k: v["claims_kept"] for k, v in summary["baselines"].items()} == {
        "stack": 88,
        "survivor_only": 75,
        "absorbed_only": 79,
        "longer_only": 81,
        "sentence_union": 94,
    }
    assert summary["strongest"] == "sentence_union"
    assert core.reference_baseline(cases)[0] == "sentence_union"
    assert summary["cases_no_baseline_perfect"] == 13
    assert summary["claims_strongest_wrong"] == 27
    assert summary["claims_all_baselines_wrong"] == 0


def test_the_fixture_discriminates_between_a_baseline_and_a_perfect_merge() -> None:
    cases = core.load_fixture()
    summary = core.baseline_summary(cases)
    # a model-free predictor must leave room: cases where no baseline is perfect
    assert summary["cases_no_baseline_perfect"] >= 10
    # and claims the strongest baseline gets wrong, so a model can win something
    assert summary["claims_strongest_wrong"] >= 25


# --- fabrication -------------------------------------------------------------------


def test_a_digit_token_that_neither_note_contains_is_novel() -> None:
    case = _case((_claim(),), a="Python 3.14 only.", b="Uses uv.")
    assert core.novel_tokens("Python 3.14 only and 3.12 too", case) == ["3.12"]
    assert core.novel_tokens("Python 3.14 only. Uses uv.", case) == []


def test_a_code_span_that_neither_note_contains_is_novel() -> None:
    case = _case((_claim(),), a="Call `alpha()`.", b="And `beta()`.")
    assert core.novel_tokens("Call `alpha()` then `gamma()`", case) == ["`gamma()`"]


def test_the_title_is_not_fabrication() -> None:
    case = core.Case(
        id="c",
        title="Release 2.0 notes",
        survivor=(core.Piece(source={}, text="x"),),
        absorbed=(core.Piece(source={}, text="y"),),
        claims=(_claim(),),
    )
    assert core.novel_tokens("# Release 2.0 notes", case) == []


# --- rows, the effective body and the summary -----------------------------------------


def _row(
    case_id: str,
    run: int,
    output: str | None,
    failure: str | None = None,
    latency: float = 1.0,
) -> dict[str, Any]:
    return {
        "case": case_id,
        "arm": "default",
        "run": run,
        "model": "m",
        "output": output,
        "failure": failure,
        "latency_s": latency,
    }


def test_a_failed_call_is_scored_as_the_stack_never_skipped() -> None:
    case = _case(
        (
            _claim("k-01", "a_only", ("alpha",)),
            _claim("k-03", "both", ("beta",), once="beta"),
        )
    )
    assert core.effective_text(case, _row("c1", 1, None, "backend")) == core.stack_text(
        case
    )
    assert core.effective_text(case, _row("c1", 1, "kept", None)) == "kept"


def test_summarize_reports_retention_dedupe_fabrication_and_failures() -> None:
    case = _case(
        (
            _claim("k-01", "a_only", ("alpha",)),
            _claim("k-02", "b_only", ("gamma",)),
            _claim("k-03", "both", ("beta",), once="beta"),
        )
    )
    rows = [
        _row("c1", 1, "alpha gamma beta"),
        _row("c1", 2, "alpha gamma beta beta 99", latency=3.0),
        _row("c1", 3, None, "refused", latency=2.0),
    ]
    s = core.summarize(rows, [case])
    assert s["n_calls"] == 3
    # run 1 = 1.0, run 2 = 2/3 (the doubled shared claim), run 3 = the stack = 2/3
    assert s["strict_accuracy"] == pytest.approx((1 + 2 / 3 + 2 / 3) / 3)
    assert s["failure_rate"] == pytest.approx(1 / 3)
    assert s["failures"] == {"refused": 1}
    assert s["fabrication_rate"] == pytest.approx(
        1 / 2
    )  # over calls that produced a body
    assert s["dedupe_rate"] == pytest.approx(
        1 / 2
    )  # shared claims kept once, over model bodies
    assert s["retention"] == pytest.approx(1.0)
    assert s["median_latency_s"] == 2.0
    assert s["stability"] == 0.0


def test_summarize_separates_the_first_authors_labels() -> None:
    claim = _claim(
        "k-03",
        "both",
        ("beta",),
        once="beta",
        adjudication={
            "first": "a_only",
            "second": "both",
            "resolved": "both",
            "reason": "r",
        },
    )
    case = _case((claim,))
    s = core.summarize([_row("c1", 1, "beta beta")], [case])
    assert s["strict_accuracy"] == 0.0
    assert s["strict_first_accuracy"] == 1.0


# --- comparison -----------------------------------------------------------------------


def test_paired_bootstrap_of_a_constant_gain_is_that_gain() -> None:
    cand = {f"c{i}": 0.9 for i in range(10)}
    ref = {f"c{i}": 0.6 for i in range(10)}
    gap = core.paired_bootstrap(cand, ref, resamples=500)
    assert gap["point"] == pytest.approx(0.3)
    assert gap["lo"] == pytest.approx(0.3)
    assert core.justified(gap)


def test_paired_bootstrap_refuses_mismatched_cases() -> None:
    with pytest.raises(ValueError, match="same cases"):
        core.paired_bootstrap({"a": 1.0}, {"b": 1.0})


def test_a_noisy_zero_gain_is_not_justified() -> None:
    cand = {f"c{i}": float(i % 2) for i in range(10)}
    ref = {f"c{i}": float((i + 1) % 2) for i in range(10)}
    assert not core.justified(core.paired_bootstrap(cand, ref, resamples=500))


def test_vetoes_trip_on_failures_latency_and_fabrication_and_on_missing_numbers() -> (
    None
):
    base = {"failure_rate": 0.0, "median_latency_s": 2.0, "fabrication_rate": 0.1}
    fine = {"failure_rate": 0.02, "median_latency_s": 3.9, "fabrication_rate": 0.2}
    assert core.vetoes(fine, base) == []
    assert core.vetoes({**fine, "failure_rate": 0.11}, base) == ["V1"]
    assert core.vetoes(
        {**fine, "failure_rate": 0.05}, {**base, "failure_rate": 0.0}
    ) == ["V1"]
    assert core.vetoes({**fine, "median_latency_s": 4.1}, base) == ["V2"]
    assert core.vetoes({**fine, "fabrication_rate": 0.21}, base) == ["V3"]
    assert core.vetoes({"failure_rate": 0.0}, base) == ["V2", "V3"]


def test_selection_needs_a_positive_lower_bound_and_no_veto() -> None:
    good = {"point": 0.1, "lo": 0.01, "hi": 0.2}
    assert core.selected(good, [])
    assert not core.selected(good, ["V2"])
    assert not core.selected({"point": 0.1, "lo": 0.0, "hi": 0.2}, [])
    assert not core.selected({"point": 0.0, "lo": 0.0, "hi": 0.2}, [])


def test_j_tolerance_grows_with_wins_and_is_pinned_at_the_edges() -> None:
    reference = {f"c{i}": 1.0 if i < 10 else 0.0 for i in range(20)}
    few = core.j_tolerance(reference, wins=1, resamples=400)
    many = core.j_tolerance(reference, wins=10, resamples=400)
    assert few <= many
    assert (
        core.j_tolerance(reference, wins=0, resamples=400) == -1
    )  # cannot clear J at all


def test_the_j_bar_on_the_real_reference_is_pinned() -> None:
    """The numbers `PREREGISTRATION-DRAFT.md` quotes for the J bar."""
    cases = core.load_fixture()
    name, reference = core.reference_baseline(cases)
    assert name == "sentence_union"
    assert core.j_tolerance(reference, wins=3) == -1
    assert core.j_tolerance(reference, wins=4) == 0
    assert core.j_tolerance(reference, wins=14) == 0
    uniform = {k: min(1.0, v + 0.05) for k, v in reference.items()}
    assert core.justified(core.paired_bootstrap(uniform, reference))


def test_a_constant_gain_is_capped_at_a_perfect_case_score() -> None:
    reference = {"a": 0.5, "b": 0.98, "c": 1.0}
    bound = core.constant_gain_bound(reference, gain=0.05, resamples=200)
    assert bound["point"] == pytest.approx((0.05 + 0.02 + 0.0) / 3)
    assert bound["lo"] <= bound["point"] <= bound["hi"]


def test_the_j_table_lists_wins_up_to_the_cases_below_perfect() -> None:
    reference = {f"c{i}": 1.0 if i < 10 else 0.5 for i in range(20)}
    rows = core.j_table_rows(reference, resamples=300)
    assert [r["wins"] for r in rows] == list(range(0, 11))
    assert rows[0]["tolerance"] == -1
    text = core.render_j_table("demo", reference, gains=[0.05], resamples=300)
    assert "reference: demo" in text
    assert "scores below 1.0 in 10 of 20 cases" in text
    assert "| 10 |" in text
    assert "+0.05" in text


# --- the forecast ---------------------------------------------------------------------


def test_forecast_multiplies_cases_runs_and_latency() -> None:
    plan = core.forecast(
        n_cases=15, runs=5, median_latency_s={"a": 10.0, "b": 20.0}, load_s=60.0
    )
    assert plan["calls_per_model"] == 75
    assert plan["calls"] == 150
    assert plan["seconds"] == {"a": 75 * 10.0 + 60.0, "b": 75 * 20.0 + 60.0}
    assert plan["total_seconds"] == pytest.approx(75 * 30.0 + 120.0)


# --- the class balance -----------------------------------------------------------------


def test_class_balance_counts_the_resolved_kinds_without_the_retired() -> None:
    case = _case(
        (
            _claim("k-01", "a_only"),
            _claim("k-02", "b_only", ("gamma",)),
            _claim("k-03", "b_only", ("gamma two",)),
            _claim("k-04", "a_only", retired="r"),
        )
    )
    assert core.class_balance([case]) == {"a_only": 1, "b_only": 2, "both": 0}


# --- adjudication records ----------------------------------------------------------------


def test_an_adjudication_record_must_resolve_to_the_claims_kind() -> None:
    good: dict[str, Any] = {
        "id": "k-01",
        "kind": "both",
        "claim": "s",
        "anchors": ["beta"],
        "once": "beta",
        "alt_label": "a_only",
        "adjudication": {
            "first": "a_only",
            "second": "both",
            "resolved": "both",
            "reason": "r",
        },
    }
    assert core.parse_claim(good).first_kind == "a_only"
    record: dict[str, str] = dict(good["adjudication"])
    for broken in (
        {**good, "adjudication": {**record, "resolved": "a_only"}},
        {**good, "alt_label": None},
        {**good, "adjudication": {"first": "a_only"}},
    ):
        with pytest.raises(ValueError, match="adjudication"):
            core.parse_claim(broken)


def test_a_flag_and_a_kind_must_differ() -> None:
    with pytest.raises(ValueError, match="alt_label"):
        core.parse_claim(
            {
                "id": "k",
                "kind": "a_only",
                "claim": "s",
                "anchors": ["x"],
                "alt_label": "a_only",
            }
        )


# --- the blind sheet -----------------------------------------------------------------------


def test_opaque_ids_are_stable_unique_and_hide_the_fixture_ids() -> None:
    cases = core.load_fixture()
    ids = adj.opaque_ids(cases)
    assert len(set(ids.values())) == len(ids) == sum(len(c.claims) for c in cases)
    assert ids == adj.opaque_ids(cases)
    assert not any(k in v for k, v in ids.items())


def test_the_sheet_shows_every_claim_once_and_hides_every_label_bearing_field() -> None:
    cases = core.load_fixture()
    sheet = adj.build_sheet(cases)
    ids = adj.opaque_ids(cases)
    for opaque in ids.values():
        assert sheet.count(f"### Claim {opaque}") == 1
    for case in cases:
        for claim in case.claims:
            assert claim.claim in sheet
            assert claim.id not in sheet
            assert not any(a in {"a_only", "b_only", "both"} for a in claim.anchors)
    for word in ("a_only", "b_only", "alt_label", "anchors", "once"):
        assert word not in adj.sheet_chrome(sheet), word


def test_a_planted_leak_in_a_hidden_field_does_not_reach_the_sheet() -> None:
    cases = core.load_fixture()
    leak = "LEAKMARK-zq9"
    first = cases[0]
    planted = core.Claim(
        id=f"{leak}-both",
        kind="both",
        claim=first.claims[0].claim,
        anchors=first.claims[0].anchors,
        once=first.claims[0].once,
        alt_label="a_only",
        adjudication=None,
        retired=None,
    )
    mutated = [
        core.Case(
            id=f"{leak}-case",
            title=first.title,
            survivor=tuple(
                core.Piece(source={"kind": "file", "ref": f"{leak}.md"}, text=p.text)
                for p in first.survivor
            ),
            absorbed=first.absorbed,
            claims=(planted, *first.claims[1:]),
        ),
        *cases[1:],
    ]
    sheet = adj.build_sheet(mutated)
    assert leak not in sheet
    assert adj.opaque_ids(mutated)[f"{leak}-both"] in sheet


def test_claim_order_inside_a_case_does_not_follow_the_fixture_order() -> None:
    cases = core.load_fixture()
    sheet = adj.build_sheet(cases)
    ids = adj.opaque_ids(cases)
    moved = 0
    for case in cases:
        order = [ids[c.id] for c in case.claims]
        seen = [o for o in order if sheet.index(f"### Claim {o}") >= 0]
        positions = [sheet.index(f"### Claim {o}") for o in seen]
        moved += positions != sorted(positions)
    assert moved >= len(cases) // 2


def _fill(sheet: str, pick: Any) -> str:
    out = []
    current = None
    for line in sheet.splitlines():
        if line.startswith("### Claim "):
            current = line.split()[-1]
        if line.startswith("Label:") and current is not None:
            line = f"Label: {pick(current)}"
        out.append(line)
    return "\n".join(out) + "\n"


def test_a_sheet_that_agrees_everywhere_scores_kappa_one() -> None:
    cases = core.load_fixture()
    ids = adj.opaque_ids(cases)
    token = {"a_only": "A", "b_only": "B", "both": "AB"}
    by_opaque = {ids[c.id]: token[c.first_kind] for case in cases for c in case.claims}
    result = adj.adjudicate(
        cases, adj.parse_sheet(_fill(adj.build_sheet(cases), by_opaque.__getitem__))
    )
    assert result.problems == []
    assert result.kappa == pytest.approx(1.0)
    assert result.disagreements == []


def test_a_disagreement_is_named_and_flags_the_first_authors_alternative() -> None:
    cases = core.load_fixture()
    ids = adj.opaque_ids(cases)
    token = {"a_only": "A", "b_only": "B", "both": "AB"}
    flagged = next(c for case in cases for c in case.claims if c.alt_label)
    by_opaque = {ids[c.id]: token[c.first_kind] for case in cases for c in case.claims}
    by_opaque[ids[flagged.id]] = token[flagged.alt_label]
    result = adj.adjudicate(
        cases, adj.parse_sheet(_fill(adj.build_sheet(cases), by_opaque.__getitem__))
    )
    assert [d.claim for d in result.disagreements] == [flagged.id]
    assert result.disagreements[0].is_alt
    assert result.kappa is not None
    assert result.kappa < 1.0


@pytest.mark.parametrize("bad", ["", "C", "A and B", "a_only"])
def test_an_unusable_label_rejects_the_whole_sheet(bad: str) -> None:
    cases = core.load_fixture()
    sheet = _fill(adj.build_sheet(cases), lambda _o: "A")
    first_id = next(iter(adj.opaque_ids(cases).values()))
    lines = sheet.splitlines()
    for i, line in enumerate(lines):
        if line == f"### Claim {first_id}":
            j = next(k for k in range(i, len(lines)) if lines[k].startswith("Label:"))
            lines[j] = f"Label: {bad}"
    result = adj.adjudicate(cases, adj.parse_sheet("\n".join(lines)))
    assert result.problems
    assert result.kappa is None


def test_a_missing_claim_rejects_the_sheet() -> None:
    cases = core.load_fixture()
    sheet = _fill(adj.build_sheet(cases), lambda _o: "A")
    first_id = next(iter(adj.opaque_ids(cases).values()))
    result = adj.adjudicate(
        cases, adj.parse_sheet(sheet.replace(f"### Claim {first_id}", "### Note"))
    )
    assert any(first_id in p for p in result.problems)


def test_quoted_note_text_cannot_be_read_as_structure() -> None:
    sheet = "### Claim abc123\n\n> ### Claim zzzzzz\n> Label: AB\n\nLabel: A\n"
    assert adj.parse_sheet(sheet) == {"abc123": "A"}


def test_the_sheet_refuses_to_be_written_inside_the_repository(tmp_path: Path) -> None:
    assert adj.outside_repo_problem(_REPO_ROOT / "evals" / "sheet.md") is not None
    assert adj.outside_repo_problem(tmp_path / "sheet.md") is None


def test_fixture_json_round_trips_through_the_loader(tmp_path: Path) -> None:
    raw = json.loads((_DIR / "fixture.json").read_text(encoding="utf-8"))
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(copy.deepcopy(raw)), encoding="utf-8")
    assert [c.id for c in core.load_fixture(path)] == [c["id"] for c in raw["cases"]]
