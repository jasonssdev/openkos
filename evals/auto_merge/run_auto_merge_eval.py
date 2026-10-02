"""Measures whether a confidence threshold exists under which the
adjudicator's `SAME` verdicts on the owner's eligible class (#1054, ADR-0034)
never auto-merge two different things.

**This is the STOP-gate harness, not the feature.** `curate --auto-merge`
does not exist yet and is built only if this harness's pre-registered rule
(`design.md` "Decision rule") PASSes -- see `openspec/changes/
auto-merge-safe-class/design.md` D1. The rule is frozen in that file, in the
planning commit, before any live run; this module implements it exactly,
never invents a variant.

**The eligible class** (design D3): a `CandidateGroup` of exactly two
members, sharing one declared OKF `type`, with `lifecycle.cross_type_concern`
`None`, whose plan is not guardrail-refused for stacked, unreconciled body
content. `auto_merge_fixtures.py` carries ten labelled-pair classes -- five
expected `different` (the negatives a false auto-merge would delete a
concept over, including the owner's named hard negative, `week-apart`: "two
different meetings a week apart") and five expected `same` (the positives
the mode exists to clear) -- and `--self-test` asserts every one of them is
structurally eligible, never adjudicated by hand.

**The signal** (design D2) is `AdjudicatedCandidate.confidence` of the FINAL
verdict, for trials whose final verdict is `SAME`. The auto-merge decision
at threshold `t` is `verdict == SAME and confidence >= t`.

**Calibration and confirmation are separate arms** (design D4): the
threshold is chosen from ONE 15-run invocation and judged on a SEPARATE
15-run invocation, same fixture, same model, production sampling (no seed
or temperature pinned) and production client settings
(`config.DEFAULT_CONTEXT_WINDOW`, `config.DEFAULT_MAX_GENERATION_TOKENS`).
Mixing the two arms, or re-using one arm's runs as if they were the other's,
makes the eventual verdict circular -- a threshold "just above the worst
negative" scored on the SAME data it was chosen from would pass the
zero-false-merge bar by construction.

Usage:

    python evals/auto_merge/run_auto_merge_eval.py --self-test
    python evals/auto_merge/run_auto_merge_eval.py --arm calibration --runs 15
    python evals/auto_merge/run_auto_merge_eval.py --arm confirmation --runs 15
    python evals/auto_merge/run_auto_merge_eval.py --decide \\
        results/runs-calibration-<stamp>-<model>.json \\
        results/runs-confirmation-<stamp>-<model>.json

`--decide` is pure and offline: it reads two stored `runs-*.json` files (in
`calibration confirmation` order) and writes the verdict file. It never
calls a model and never re-runs anything -- re-running an arm is always a
separate `--arm` invocation, on purpose (design D4): the threshold and its
judgment must come from bytes already committed to disk, never from a value
this process just happened to hold in memory.

Live runs need Ollama serving `qwen3:8b` locally. Budget: roughly 22 pairs
x 15 runs x ~19s, about 1.7 hours per arm (design.md "Harness shape").
Results JSON never contains a private-corpus document: `auto_merge_fixtures`
is committed, invented content only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, cast

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# APPENDED, not inserted at zero, mirroring `run_adjudication_eval.py`'s own
# discipline: an insert at zero would put the evals root AHEAD of this
# harness's own directory, so a module added at the root could shadow a
# same-named one beside this file.
sys.path.append(str(REPO_ROOT / "evals"))

from auto_merge_fixtures import (  # noqa: E402
    NEGATIVE_PROBES,
    PAIRS,
    POSITIVE_PROBES,
    PROBES,
    FixtureDoc,
    LabelledPair,
    documents,
)
from harness_report import arm_identity_line  # noqa: E402
from harness_stamp import build_stamp  # noqa: E402

from openkos import config as config_mod  # noqa: E402
from openkos.application import lifecycle as lifecycle_mod  # noqa: E402
from openkos.bundle import bundle as bundle_mod  # noqa: E402
from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.ollama import OllamaClient, OllamaError  # noqa: E402
from openkos.resolution import adjudication as adjudication_mod  # noqa: E402
from openkos.resolution import candidates as candidates_mod  # noqa: E402

DEFAULT_MODEL: Final[str] = "qwen3:8b"
DEFAULT_RUNS: Final[int] = 15
"""15, not fewer: #765 measured a single arm swinging 0.25 against ITSELF
across 5 runs on this repo's own harnesses -- a shorter arm here would
report noise as a threshold."""

_MISSING: Final[str] = "<missing>"
"""Recorded for a labelled pair whose group produced no result in a run: a
`find_candidates` change, a mid-loop `OllamaError` that cut the batch short,
or a group that never formed. Its own verdict token, never folded into
`uncertain` -- R0 treats it as an unmeasured trial (`INVALID`, re-run),
which `uncertain` would not be."""

_NEGATIVE_TRIAL_FLOOR: Final[int] = 180
"""R1: 12 negative pairs (the design's stated minimum) x 15 runs. A
zero-false-merge result means nothing without the cases that could have
failed it."""

_SCHEMA: Final[str] = "openkos.eval.auto_merge/v1"


# --------------------------------------------------------------------------- #
# bundle materialization
# --------------------------------------------------------------------------- #


def _materialize_bundle(bundle_dir: pathlib.Path, docs: tuple[FixtureDoc, ...]) -> None:
    """Write every fixture document as a minimal OKF concept file.
    `sensitivity: private` is not decoration: `sensitive_concept_ids` fails
    CLOSED on an absent value, and a `provenance:` list is required for
    `lifecycle.cross_source_same_pair` to be computable at all (design D3) --
    same reason as the adjudication harness's own materializer."""
    for doc in docs:
        path = bundle_dir / f"{doc.concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            f"type: {doc.okf_type}",
            f"title: {doc.title}",
            "sensitivity: private",
        ]
        if doc.provenance:
            lines.append("provenance:")
            lines.extend(f"  - {entry}" for entry in doc.provenance)
        lines += [
            "---",
            f"# {doc.title}",
            "",
            doc.body,
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fixture_digest(bundle_dir: pathlib.Path) -> str:
    """`sha256:<hex>` over every materialized file's relative path and
    bytes, in sorted-path order -- an arm's identity guard (R0): two
    materializations of the same fixture set must hash identically, and a
    stored run's digest must match its sibling arm's before `--decide` ever
    trusts the pair."""
    hasher = hashlib.sha256()
    for path in sorted(bundle_dir.rglob("*.md")):
        hasher.update(path.relative_to(bundle_dir).as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
    return f"sha256:{hasher.hexdigest()}"


def _materialize_workspace(root: pathlib.Path) -> config_mod.WorkspaceLayout:
    """A full, minimal OKF workspace at `root` -- `bundle/index.md` and
    `bundle/log.md` (`bundle.create`), `openkos.yaml` (`config.write_config`,
    packaged defaults), then every fixture document. Needed only for the
    self-test's D3 structural check, which calls `prepare_one_merge` --
    that function reads `openkos.yaml` (`config.read_config`) and both
    reserved files (`fsio.snapshot_read`), none of which a live `--arm` run
    needs (design.md "Harness shape": a live run calls only
    `find_candidates` + `adjudicate_candidates` against a bare `bundle_dir`,
    the same shape `evals/adjudication/` already uses). No git: `init`'s own
    git bootstrap is best-effort and orthogonal to what `prepare_one_merge`
    reads."""
    layout = config_mod.WorkspaceLayout(root)
    bundle_mod.create(layout.bundle_dir, datetime.now(UTC).date())
    config_mod.write_config(root)
    _materialize_bundle(layout.bundle_dir, documents())
    return layout


def _pair_key(labelled: LabelledPair) -> frozenset[str]:
    """The identity a labelled pair is matched to a real `CandidateGroup`
    by -- a `frozenset`, matching `evals/adjudication/`'s own reasoning: the
    grouping is a SET relation, and an order-carrying key would silently
    stop matching if a third fixture ever joined a title bucket."""
    return frozenset((labelled.left.concept_id, labelled.right.concept_id))


def _pair_id(labelled: LabelledPair) -> str:
    """The stable, sorted, `+`-joined string identity a stored trial JSON
    row is keyed by -- readable, and independent of dict/set iteration
    order across a Python version or process."""
    return "+".join(sorted(_pair_key(labelled)))


_PAIR_ID_BY_KEY: Final[dict[frozenset[str], str]] = {
    _pair_key(pair): _pair_id(pair) for pair in PAIRS
}


# --------------------------------------------------------------------------- #
# one live run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Observed:
    """One labelled pair's outcome in one live run -- everything a stored
    `runs-*.json` trial row carries (design.md "Results file format")."""

    verdict: str
    confidence: float
    rationale: str
    tier: str
    cross_source: bool


def _run_once(
    bundle_dir: pathlib.Path, client: OllamaClient
) -> dict[frozenset[str], _Observed]:
    """One full candidate-then-adjudicate pass over the REAL production
    path -- `find_candidates` then `adjudicate_candidates`, no
    `findings.db`, so no verdict is ever served from cache. Groups are
    DISCOVERED, never hand-built, mirroring the adjudication harness's own
    reasoning: a hand-built `CandidateGroup` would pin the tier and keep
    scoring after a `find_candidates` change stopped producing the pair at
    all."""
    groups = candidates_mod.find_candidates(bundle_dir)
    batch = adjudication_mod.adjudicate_candidates(
        groups, bundle_dir=bundle_dir, llm=client
    )
    observed: dict[frozenset[str], _Observed] = {}
    for result in batch.results:
        member_ids = tuple(result.candidate.member_ids)
        key = frozenset(member_ids)
        cross_source = (
            lifecycle_mod.cross_source_same_pair(bundle_dir, member_ids)
            if len(member_ids) == 2
            else False
        )
        observed[key] = _Observed(
            verdict=result.verdict.value,
            confidence=result.confidence,
            rationale=result.rationale,
            tier=result.candidate.tier.value,
            cross_source=cross_source,
        )
    return observed


# --------------------------------------------------------------------------- #
# decide() -- the pre-registered rule, `design.md` "Decision rule"
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TrialRecord:
    """One stored trial, the unit `decide()` reasons over -- carries only
    the fields the rule reads; `tier`/`cross_source` ride along for the
    secondary (cross-source-excluded) population and offline rescoring
    (design D2/D3), never for the primary bars."""

    pair_id: str
    probe: str
    expected: str
    verdict: str
    confidence: float
    tier: str = "low"
    cross_source: bool = False


@dataclass(frozen=True)
class ArmData:
    """One arm's stored runs, parsed from (or built in place of) a
    `runs-*.json` file."""

    arm: str
    model: str
    fixture_digest: str
    runs: tuple[tuple[TrialRecord, ...], ...]

    @property
    def trials(self) -> tuple[TrialRecord, ...]:
        return tuple(trial for run in self.runs for trial in run)


@dataclass(frozen=True)
class Decision:
    """`decide()`'s full output: the verdict, every failed bar (or every
    `INVALID` reason), and the threshold bookkeeping the verdict file must
    disclose."""

    verdict: str
    """`PASS`, `FAIL`, or `INVALID`."""
    reasons: tuple[str, ...]
    b: float
    t_star: float | None
    non_binding: bool
    r1_negative_trials: int
    r2_false_merges: int
    r4_retention: float
    r5_stability: float


def _is_auto_merge(trial: TrialRecord, t_star: float) -> bool:
    return trial.verdict == "same" and trial.confidence >= t_star


def decide(calibration: ArmData, confirmation: ArmData) -> Decision:
    """The pre-registered rule, verbatim from `design.md` "Decision rule" --
    frozen there before any live run and never edited after (`tasks.md`:
    "No task may edit it after task 2.2 starts"). `INVALID` means the
    measurement did not happen (re-run only the invalid arm); `FAIL` means
    it happened and the class does not qualify; `PASS` unlocks Phase 3.
    """
    invalid: list[str] = []
    if calibration.fixture_digest != confirmation.fixture_digest:
        invalid.append(
            "fixture digest differs between arms "
            f"({calibration.fixture_digest!r} != {confirmation.fixture_digest!r})"
        )
    if calibration.model != confirmation.model:
        invalid.append(
            f"model differs between arms ({calibration.model!r} != "
            f"{confirmation.model!r})"
        )
    for arm_name, arm in (("calibration", calibration), ("confirmation", confirmation)):
        missing = [t.pair_id for t in arm.trials if t.verdict == _MISSING]
        if missing:
            invalid.append(
                f"{arm_name} arm has {len(missing)} <missing> trial(s), e.g. "
                f"{missing[0]!r}"
            )
    if invalid:
        return Decision(
            verdict="INVALID",
            reasons=tuple(invalid),
            b=float("nan"),
            t_star=None,
            non_binding=False,
            r1_negative_trials=0,
            r2_false_merges=0,
            r4_retention=0.0,
            r5_stability=0.0,
        )

    # Step 1 -- threshold, from the calibration arm only.
    cal_trials = calibration.trials
    negative_same = [
        t.confidence
        for t in cal_trials
        if t.expected == "different" and t.verdict == "same"
    ]
    b = max(negative_same) if negative_same else float("-inf")
    non_binding = b == float("-inf")
    positive_same_above_b = sorted(
        t.confidence
        for t in cal_trials
        if t.expected == "same" and t.verdict == "same" and t.confidence > b
    )
    if not positive_same_above_b:
        return Decision(
            verdict="FAIL",
            reasons=(
                "no separator: no calibration positive exceeds the highest negative",
            ),
            b=b,
            t_star=None,
            non_binding=non_binding,
            r1_negative_trials=0,
            r2_false_merges=0,
            r4_retention=0.0,
            r5_stability=0.0,
        )
    t_star = positive_same_above_b[0]

    # Step 2 -- bars, on the confirmation arm with t* fixed.
    conf_trials = confirmation.trials
    negative_conf = [t for t in conf_trials if t.expected == "different"]
    positive_conf = [t for t in conf_trials if t.expected == "same"]

    failures: list[str] = []

    r1_count = len(negative_conf)
    if r1_count < _NEGATIVE_TRIAL_FLOOR:
        failures.append(
            f"R1 exposure: {r1_count} negative trial(s) reached the gate, "
            f"below the floor of {_NEGATIVE_TRIAL_FLOOR}"
        )

    false_merges = [t for t in negative_conf if _is_auto_merge(t, t_star)]
    if false_merges:
        failures.append(
            f"R2 false auto-merges: {len(false_merges)} negative trial(s) "
            f"auto-merged at t*={t_star:.4f} (e.g. {false_merges[0].pair_id!r})"
        )

    week_apart_merged = [
        t
        for t in cal_trials + conf_trials
        if t.probe == "week-apart" and _is_auto_merge(t, t_star)
    ]
    if week_apart_merged:
        failures.append(
            f"R3 owner's named negative: {len(week_apart_merged)} week-apart "
            f"trial(s) auto-merged at t*={t_star:.4f} "
            f"(e.g. {week_apart_merged[0].pair_id!r})"
        )

    retention = 0.0
    if positive_conf:
        merged = sum(1 for t in positive_conf if _is_auto_merge(t, t_star))
        retention = merged / len(positive_conf)
    if retention < 0.50:
        failures.append(f"R4 retention: {retention:.4f} < 0.50")

    per_pair: dict[str, list[bool]] = defaultdict(list)
    for run in confirmation.runs:
        for trial in run:
            if trial.expected == "same":
                per_pair[trial.pair_id].append(_is_auto_merge(trial, t_star))
    stabilities = [
        max(sum(decisions), len(decisions) - sum(decisions)) / len(decisions)
        for decisions in per_pair.values()
        if decisions
    ]
    stability = statistics.fmean(stabilities) if stabilities else 0.0
    if stability < 0.80:
        failures.append(f"R5 stability: {stability:.4f} < 0.80")

    return Decision(
        verdict="PASS" if not failures else "FAIL",
        reasons=tuple(failures),
        b=b,
        t_star=t_star,
        non_binding=non_binding,
        r1_negative_trials=r1_count,
        r2_false_merges=len(false_merges),
        r4_retention=retention,
        r5_stability=stability,
    )


def _secondary_decide(calibration: ArmData, confirmation: ArmData) -> Decision:
    """Every bar again on the cross-source-EXCLUDED subpopulation (design
    D3: "reported ... as a secondary, non-deciding result"). Filters both
    arms to `cross_source is False` and reruns the identical rule -- never
    used to gate `PASS`/`FAIL`, only to disclose whether tightening
    eligibility to same-source pairs would change the picture."""

    def _filter(arm: ArmData) -> ArmData:
        return ArmData(
            arm=arm.arm,
            model=arm.model,
            fixture_digest=arm.fixture_digest,
            runs=tuple(tuple(t for t in run if not t.cross_source) for run in arm.runs),
        )

    return decide(_filter(calibration), _filter(confirmation))


# --------------------------------------------------------------------------- #
# self-test -- no model, no network
# --------------------------------------------------------------------------- #


def _check_list(failures: list[str], label: str, got: object, want: object) -> None:
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def _assert_fixture_shape(failures: list[str]) -> None:
    docs = documents()
    by_probe: dict[str, list[LabelledPair]] = defaultdict(list)
    for pair in PAIRS:
        by_probe[pair.probe].append(pair)

    _check_list(
        failures,
        "every probe in PROBES is populated",
        sorted({p.probe for p in PAIRS}),
        sorted(PROBES),
    )
    negative_pairs = [p for p in PAIRS if p.expected == "different"]
    positive_pairs = [p for p in PAIRS if p.expected == "same"]
    _check_list(
        failures,
        "at least 12 negative pairs across at least 5 classes",
        (len(negative_pairs) >= 12 and len({p.probe for p in negative_pairs}) >= 5),
        True,
    )
    _check_list(
        failures,
        "at least 10 positive pairs across at least 4 classes",
        (len(positive_pairs) >= 10 and len({p.probe for p in positive_pairs}) >= 4),
        True,
    )
    _check_list(
        failures,
        "week-apart carries at least 3 pairs",
        len(by_probe["week-apart"]) >= 3,
        True,
    )
    _check_list(
        failures,
        "asym-recurrence carries at least 3 pairs",
        len(by_probe["asym-recurrence"]) >= 3,
        True,
    )
    _check_list(
        failures,
        "reingest-dup carries at least 3 pairs",
        len(by_probe["reingest-dup"]) >= 3,
        True,
    )
    _check_list(
        failures,
        "every negative probe is labelled 'different'",
        sorted({p.expected for probe in NEGATIVE_PROBES for p in by_probe[probe]}),
        ["different"],
    )
    _check_list(
        failures,
        "every positive probe is labelled 'same'",
        sorted({p.expected for probe in POSITIVE_PROBES for p in by_probe[probe]}),
        ["same"],
    )
    _check_list(
        failures,
        "grupo-calidad-datos (#869) is present in asym-recurrence",
        any(
            "grupo-calidad-datos" in doc.concept_id
            for pair in by_probe["asym-recurrence"]
            for doc in (pair.left, pair.right)
        ),
        True,
    )
    for doc in docs:
        if not doc.okf_type:
            failures.append(f"{doc.concept_id}: empty OKF type")
        if not doc.title:
            failures.append(f"{doc.concept_id}: empty title")
        if not doc.provenance:
            failures.append(f"{doc.concept_id}: empty provenance -- not computable")


def _assert_digest_stable_and_d3(failures: list[str], stack: ExitStack) -> None:
    """Materializes the fixture twice into separate temp directories and
    asserts identical digests -- a harness whose own fixture is
    non-deterministic could never anchor R0's cross-arm digest check.
    Then runs the D3 structural-eligibility assertion against the first
    workspace, so that check does not pay for a third materialization.
    Both temp directories are registered on `stack` and cleaned up when the
    self-test returns."""
    first_root = (
        pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory())) / "workspace-a"
    )
    second_root = (
        pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory())) / "workspace-b"
    )
    first_layout = _materialize_workspace(first_root)
    second_layout = _materialize_workspace(second_root)
    first_digest = _fixture_digest(first_layout.bundle_dir)
    second_digest = _fixture_digest(second_layout.bundle_dir)
    _check_list(
        failures,
        "fixture digest is identical across two materializations",
        first_digest,
        second_digest,
    )
    _assert_d3_structural_eligibility(failures, first_root)


def _assert_d3_structural_eligibility(failures: list[str], root: pathlib.Path) -> None:
    """Design D3: every labelled pair, on the materialized bundle, forms
    exactly one 2-member `find_candidates` group, both members declare one
    OKF type, `cross_type_concern` is `None`, and the pinned
    `prepare_one_merge` plan is not guardrail-refused for stacked body
    content. An ineligible pair here is a FIXTURE defect, caught in CI,
    never a silent exclusion from the measured population."""
    layout = config_mod.WorkspaceLayout(root)
    groups = candidates_mod.find_candidates(layout.bundle_dir)
    found = {frozenset(g.member_ids): g for g in groups}
    wanted = {_pair_key(pair) for pair in PAIRS}

    missing = sorted(
        f"{pair.probe}:{_pair_id(pair)}"
        for pair in PAIRS
        if _pair_key(pair) not in found
    )
    extra = sorted(
        _PAIR_ID_BY_KEY.get(key, "+".join(sorted(key)))
        for key in found
        if key not in wanted
    )
    _check_list(
        failures, "no labelled pair is missing from find_candidates", missing, []
    )
    _check_list(failures, "find_candidates produces no unlabelled group", extra, [])
    _check_list(
        failures, "one group per labelled pair, none duplicated", len(found), len(PAIRS)
    )

    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    for pair in PAIRS:
        group = found.get(_pair_key(pair))
        if group is None:
            continue
        if len(group.member_ids) != 2:
            failures.append(
                f"{_pair_id(pair)}: group has {len(group.member_ids)} members, want 2"
            )
            continue
        concern = lifecycle_mod.cross_type_concern(layout.bundle_dir, group.member_ids)
        if concern is not None:
            failures.append(
                f"{_pair_id(pair)}: cross_type_concern is not None -- {concern}"
            )
            continue
        ordered_pair = lifecycle_mod.ordered_merge_pair(
            layout.bundle_dir, group.member_ids
        )[:2]
        try:
            prepared = lifecycle_mod.prepare_one_merge(
                root, layout, index_path, log_path, group, ordered_pair=ordered_pair
            )
        except (OSError, ValueError) as exc:
            failures.append(f"{_pair_id(pair)}: prepare_one_merge raised {exc!r}")
            continue
        if prepared is None:
            failures.append(f"{_pair_id(pair)}: prepare_one_merge returned None")
            continue
        if (
            prepared.stacked_body is not None
            and prepared.stacked_body.exceeds_guardrail
        ):
            failures.append(
                f"{_pair_id(pair)}: stacked body exceeds the guardrail "
                f"(share={prepared.stacked_body.share:.2f})"
            )


def _trial(
    pair_id: str,
    probe: str,
    expected: str,
    verdict: str,
    confidence: float,
    *,
    cross_source: bool = False,
) -> TrialRecord:
    return TrialRecord(
        pair_id=pair_id,
        probe=probe,
        expected=expected,
        verdict=verdict,
        confidence=confidence,
        cross_source=cross_source,
    )


def _filler_negatives(n: int, *, prefix: str = "filler-neg") -> list[TrialRecord]:
    """`n` safely-DIFFERENT negative trials -- never `same`, so they can
    never contribute to `B`, R2, or R3. Used to satisfy R1's exposure floor
    in a synthetic arm without perturbing the bar under test."""
    return [
        _trial(f"{prefix}-{i}", "recurrence", "different", "different", 0.10)
        for i in range(n)
    ]


def _healthy_calibration(*, week_apart_confidence: float = 0.40) -> ArmData:
    """One calibration run with a real (non -inf) `B` and a `t*` strictly
    above it: `recurrence` sets `B=0.60`; `reingest-dup` sets `t*=0.92`. A
    harmless `week-apart` trial rides along under `B` -- it can never be
    the one that sets `B` above `t*`, by the rule's own invariant (`t* > B`
    always), so a calibration-side R3 violation is unreachable by
    construction; see the module docstring's R3 note."""
    run = [
        _trial("neg-recurrence-b", "recurrence", "different", "same", 0.60),
        _trial(
            "neg-week-apart-safe",
            "week-apart",
            "different",
            "same",
            week_apart_confidence,
        ),
        _trial("pos-reingest-t", "reingest-dup", "same", "same", 0.92),
    ]
    return ArmData(
        arm="calibration",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:fixture",
        runs=(tuple(run),),
    )


def _confirmation_pass() -> ArmData:
    """15 runs, each 12 safely-different negatives (one of them
    `week-apart`, safely under `t*`) plus 4 positive pairs all merging
    consistently -- exposure 180, zero false merges, retention 1.0,
    stability 1.0. Everything a `PASS` needs."""
    runs: list[tuple[TrialRecord, ...]] = []
    for run_index in range(15):
        row = [
            _trial(
                f"neg-{i}-run{run_index}", "recurrence", "different", "different", 0.10
            )
            for i in range(11)
        ] + [
            _trial(
                f"week-apart-run{run_index}",
                "week-apart",
                "different",
                "different",
                0.10,
            )
        ]
        row += [
            _trial(f"pos-{i}", "reingest-dup", "same", "same", 0.97) for i in range(4)
        ]
        runs.append(tuple(row))
    return ArmData(
        arm="confirmation",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:fixture",
        runs=tuple(runs),
    )


def _self_test_decide(failures: list[str]) -> None:
    """`decide()` on synthetic arms, one case per possible outcome
    (`tasks.md` 1.6) -- every bar is shown able to fail, mirroring
    `evals/adjudication/`'s own "an unexercised safety net is
    indistinguishable from a deleted one" discipline."""
    healthy_cal = _healthy_calibration()
    healthy_conf = _confirmation_pass()

    # PASS.
    pass_decision = decide(healthy_cal, healthy_conf)
    _check_list(failures, "PASS case: verdict", pass_decision.verdict, "PASS")
    _check_list(failures, "PASS case: reasons empty", pass_decision.reasons, ())
    _check_list(failures, "PASS case: t* is 0.92", pass_decision.t_star, 0.92)
    _check_list(failures, "PASS case: B is 0.60", pass_decision.b, 0.60)
    _check_list(
        failures, "PASS case: non_binding is False", pass_decision.non_binding, False
    )

    # FAIL (no separator): the highest bad is at or above every good --
    # design.md's own "prior expectation" shape (B=0.95, nothing exceeds it).
    no_separator_cal = ArmData(
        arm="calibration",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:fixture",
        runs=(
            (
                _trial("neg-hard", "asym-recurrence", "different", "same", 0.95),
                # Exactly EQUAL to the negative, not merely below it: this is
                # the strict-inequality boundary ("s > B", design.md Step 1)
                # -- a `>` -> `>=` mutation here would wrongly accept 0.95 as
                # its own separator (task 1.8's mutation-proof case).
                _trial("pos-soft", "reingest-dup", "same", "same", 0.95),
            ),
        ),
    )
    no_separator = decide(no_separator_cal, healthy_conf)
    _check_list(failures, "FAIL(no separator): verdict", no_separator.verdict, "FAIL")
    _check_list(
        failures,
        "FAIL(no separator): names the reason",
        any("no separator" in r for r in no_separator.reasons),
        True,
    )
    _check_list(failures, "FAIL(no separator): t* is None", no_separator.t_star, None)

    # FAIL R2: one confirmation negative reaches t*.
    r2_conf = ArmData(
        arm="confirmation",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:fixture",
        runs=(
            (
                *_filler_negatives(180 - 1, prefix="r2-filler"),
                _trial("r2-false-merge", "asym-recurrence", "different", "same", 0.99),
                *[
                    _trial(f"r2-pos-{i}", "reingest-dup", "same", "same", 0.97)
                    for i in range(4)
                ],
            ),
        ),
    )
    r2_fail = decide(healthy_cal, r2_conf)
    _check_list(failures, "FAIL R2: verdict", r2_fail.verdict, "FAIL")
    _check_list(
        failures,
        "FAIL R2: names R2",
        any(r.startswith("R2") for r in r2_fail.reasons),
        True,
    )

    # FAIL R3: a week-apart trial reaches t* -- necessarily placed in the
    # CONFIRMATION arm. A calibration-side week-apart violation is
    # mathematically excluded by Step 1's own invariant: B is the max over
    # ALL calibration negative same-trials (week-apart included), and
    # t* is required to be strictly greater than B, so every calibration
    # negative trial's confidence -- week-apart's own included -- is by
    # construction below t*. R3's cross-arm check is still real: it is
    # exactly what catches a lucky CONFIRMATION run.
    r3_conf = ArmData(
        arm="confirmation",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:fixture",
        runs=(
            (
                *_filler_negatives(180 - 1, prefix="r3-filler"),
                _trial("r3-week-apart-merged", "week-apart", "different", "same", 0.99),
                *[
                    _trial(f"r3-pos-{i}", "reingest-dup", "same", "same", 0.97)
                    for i in range(4)
                ],
            ),
        ),
    )
    r3_fail = decide(healthy_cal, r3_conf)
    _check_list(failures, "FAIL R3: verdict", r3_fail.verdict, "FAIL")
    _check_list(
        failures,
        "FAIL R3: names R3",
        any(r.startswith("R3") for r in r3_fail.reasons),
        True,
    )

    # FAIL R4: retention exactly 0.49 (49 of 100 positive trials merge).
    r4_conf_runs = (
        tuple(_filler_negatives(180, prefix="r4-filler"))
        + tuple(
            _trial(f"r4-pos-merge-{i}", "reingest-dup", "same", "same", 0.97)
            for i in range(49)
        )
        + tuple(
            _trial(f"r4-pos-hold-{i}", "reingest-dup", "same", "different", 0.10)
            for i in range(51)
        ),
    )
    r4_conf = ArmData(
        arm="confirmation",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:fixture",
        runs=r4_conf_runs,
    )
    r4_fail = decide(healthy_cal, r4_conf)
    _check_list(failures, "FAIL R4: verdict", r4_fail.verdict, "FAIL")
    _check_list(
        failures, "FAIL R4: retention is 0.49", round(r4_fail.r4_retention, 2), 0.49
    )
    _check_list(
        failures,
        "FAIL R4: names R4",
        any(r.startswith("R4") for r in r4_fail.reasons),
        True,
    )

    # FAIL R5: one positive pair merges in 79 of 100 runs (modal share 0.79).
    r5_runs: list[tuple[TrialRecord, ...]] = []
    r5_runs.extend(
        (_trial("r5-pair", "reingest-dup", "same", "same", 0.97),) for _ in range(79)
    )
    r5_runs.extend(
        (_trial("r5-pair", "reingest-dup", "same", "different", 0.10),)
        for _ in range(21)
    )
    r5_runs_with_exposure = [
        run + tuple(_filler_negatives(180, prefix=f"r5-filler-{index}"))
        for index, run in enumerate(r5_runs)
    ]
    r5_conf = ArmData(
        arm="confirmation",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:fixture",
        runs=tuple(r5_runs_with_exposure),
    )
    r5_fail = decide(healthy_cal, r5_conf)
    _check_list(failures, "FAIL R5: verdict", r5_fail.verdict, "FAIL")
    _check_list(
        failures, "FAIL R5: stability is 0.79", round(r5_fail.r5_stability, 2), 0.79
    )
    _check_list(
        failures,
        "FAIL R5: names R5",
        any(r.startswith("R5") for r in r5_fail.reasons),
        True,
    )

    # INVALID: one <missing> trial.
    missing_conf = ArmData(
        arm="confirmation",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:fixture",
        runs=((_trial("missing-one", "reingest-dup", "same", _MISSING, 0.0),),),
    )
    missing_decision = decide(healthy_cal, missing_conf)
    _check_list(
        failures, "INVALID(<missing>): verdict", missing_decision.verdict, "INVALID"
    )

    # INVALID: mismatched fixture digest.
    mismatched_conf = ArmData(
        arm="confirmation",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:different",
        runs=healthy_conf.runs,
    )
    digest_decision = decide(healthy_cal, mismatched_conf)
    _check_list(
        failures,
        "INVALID(digest mismatch): verdict",
        digest_decision.verdict,
        "INVALID",
    )

    # B = -inf labels t* non-binding: no calibration negative ever reaches
    # `same`, so B stays -inf, and the bars alone decide.
    non_binding_cal = ArmData(
        arm="calibration",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:fixture",
        runs=(
            (
                _trial("neg-never-same", "recurrence", "different", "different", 0.10),
                _trial("pos-any", "reingest-dup", "same", "same", 0.55),
            ),
        ),
    )
    non_binding_conf = ArmData(
        arm="confirmation",
        model=DEFAULT_MODEL,
        fixture_digest="sha256:fixture",
        runs=tuple(
            (
                *_filler_negatives(180, prefix=f"nb-filler-{i}"),
                _trial("pos-any", "reingest-dup", "same", "same", 0.90),
            )
            for i in range(1)
        ),
    )
    non_binding_decision = decide(non_binding_cal, non_binding_conf)
    _check_list(
        failures, "non-binding: B is -inf", non_binding_decision.b, float("-inf")
    )
    _check_list(
        failures,
        "non-binding: non_binding is True",
        non_binding_decision.non_binding,
        True,
    )
    _check_list(
        failures, "non-binding: t* is still computed", non_binding_decision.t_star, 0.55
    )


def _self_test() -> int:
    failures: list[str] = []
    _assert_fixture_shape(failures)
    with ExitStack() as stack:
        _assert_digest_stable_and_d3(failures, stack)
    _self_test_decide(failures)

    if failures:
        print("self-test FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"self-test OK: {len(PAIRS)} labelled pairs across {len(PROBES)} probe "
        "classes materialize into exactly that many 2-member candidate groups, "
        "each structurally eligible for the auto-merge class (D3); the fixture "
        "digest is stable across two materializations; and decide() reaches "
        "PASS, FAIL (no separator), FAIL R2-R5, INVALID, and the B=-inf "
        "non-binding case on synthetic arms."
    )
    return 0


# --------------------------------------------------------------------------- #
# live arm + --decide
# --------------------------------------------------------------------------- #


def _trial_to_json(
    pair: LabelledPair, observed: _Observed | None, latency_s: float
) -> dict[str, object]:
    if observed is None:
        return {
            "pair_id": _pair_id(pair),
            "probe": pair.probe,
            "expected": pair.expected,
            "tier": "low",
            "cross_source": False,
            "verdict": _MISSING,
            "confidence": 0.0,
            "rationale": "",
            "latency_s": latency_s,
        }
    return {
        "pair_id": _pair_id(pair),
        "probe": pair.probe,
        "expected": pair.expected,
        "tier": observed.tier,
        "cross_source": observed.cross_source,
        "verdict": observed.verdict,
        "confidence": observed.confidence,
        "rationale": observed.rationale,
        "latency_s": latency_s,
    }


def _run_arm(arm: str, runs_requested: int, model: str) -> int:
    client = OllamaClient(
        model=model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
    )
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607 -- `git` resolved from PATH, matching evals/ precedent
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()

    rows: list[dict[str, object]] = []
    completed_runs = 0
    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = pathlib.Path(tmp) / "bundle"
        bundle_dir.mkdir(parents=True)
        _materialize_bundle(bundle_dir, documents())
        fixture_digest = _fixture_digest(bundle_dir)
        for run_index in range(runs_requested):
            started = time.monotonic()
            try:
                observed = _run_once(bundle_dir, client)
            except OllamaError as exc:
                print(
                    f"  run {run_index + 1}/{runs_requested} FAILED ({exc}); "
                    f"scoring the {completed_runs} completed run(s)",
                    file=sys.stderr,
                )
                break
            latency = time.monotonic() - started
            trials = [
                _trial_to_json(pair, observed.get(_pair_key(pair)), latency)
                for pair in PAIRS
            ]
            rows.append({"run": run_index + 1, "trials": trials})
            completed_runs += 1
            print(f"  run {run_index + 1}/{runs_requested} done ({latency:.1f}s)")

    if completed_runs == 0:
        raise SystemExit("no run completed; nothing to score")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results_dir = pathlib.Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    slug = f"{arm}-{stamp}-{model.replace(':', '-')}"

    payload = {
        "schema": _SCHEMA,
        "arm": arm,
        "model": model,
        "git_sha": git_sha,
        "fixture_digest": fixture_digest,
        "settings": {
            "context_window": DEFAULT_CONTEXT_WINDOW,
            "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
        },
        "started_at": stamp,
        # #1269: commit, model digest and prompt identity. Per-run wall-clock
        # is already on every trial as `latency_s`.
        "stamp": build_stamp(
            model=model,
            prompts={"adjudication/system": adjudication_mod._SYSTEM_PROMPT},
        ),
        "runs": rows,
    }
    (results_dir / f"runs-{slug}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    report_lines = [
        f"# auto-merge eval -- arm `{arm}` (#1054)",
        "",
        f"_Generated: {stamp}_ · model `{model}` · **{completed_runs} runs**"
        f"{'' if completed_runs == runs_requested else f' of {runs_requested} requested'}"
        f" over {len(PAIRS)} labelled pairs.",
        "",
        arm_identity_line(
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
        ),
        "",
        f"Fixture digest: `{fixture_digest}` · git `{git_sha}`.",
        "Labels are CONSTRUCTED, not adjudicated -- see `auto_merge_fixtures.py`.",
        "",
        "| probe | expected | n | same | different | uncertain | missing |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    per_probe_verdicts: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for trial in cast("list[dict[str, object]]", row["trials"]):
            per_probe_verdicts[str(trial["probe"])].append(str(trial["verdict"]))
    expected_by_probe = {pair.probe: pair.expected for pair in PAIRS}
    for probe in PROBES:
        verdicts = per_probe_verdicts[probe]
        total = len(verdicts) or 1
        report_lines.append(
            f"| {probe} | `{expected_by_probe[probe]}` | "
            f"{len(verdicts)} | {verdicts.count('same') / total:.2f} | "
            f"{verdicts.count('different') / total:.2f} | "
            f"{verdicts.count('uncertain') / total:.2f} | {verdicts.count(_MISSING) / total:.2f} |"
        )
    report = "\n".join(report_lines) + "\n"
    (results_dir / f"auto-merge-{slug}.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


def _load_arm(path: pathlib.Path) -> ArmData:
    raw = json.loads(path.read_text(encoding="utf-8"))
    runs = tuple(
        tuple(
            TrialRecord(
                pair_id=str(trial["pair_id"]),
                probe=str(trial["probe"]),
                expected=str(trial["expected"]),
                verdict=str(trial["verdict"]),
                confidence=float(cast("float", trial["confidence"])),
                tier=str(trial.get("tier", "low")),
                cross_source=bool(trial.get("cross_source", False)),
            )
            for trial in cast("list[dict[str, object]]", run["trials"])
        )
        for run in cast("list[dict[str, object]]", raw["runs"])
    )
    return ArmData(
        arm=str(raw["arm"]),
        model=str(raw["model"]),
        fixture_digest=str(raw["fixture_digest"]),
        runs=runs,
    )


def _render_verdict(
    calibration_path: pathlib.Path,
    confirmation_path: pathlib.Path,
    decision: Decision,
    secondary: Decision,
) -> str:
    lines = [
        "# auto-merge eval -- verdict (#1054)",
        "",
        f"Calibration: `{calibration_path.name}`. Confirmation: `{confirmation_path.name}`.",
        "",
        f"**Verdict: {decision.verdict}**",
        "",
        f"`B` (highest calibration negative `same` confidence): "
        f"{'-inf' if decision.b == float('-inf') else f'{decision.b:.4f}'}"
        f"{' (non-binding -- no negative ever reached it)' if decision.non_binding else ''}.",
        f"`t*`: {decision.t_star if decision.t_star is not None else 'undefined'}.",
        "",
        "| bar | value |",
        "| --- | --- |",
        f"| R1 exposure (negative trials) | {decision.r1_negative_trials} |",
        f"| R2 false auto-merges | {decision.r2_false_merges} |",
        f"| R4 retention | {decision.r4_retention:.2f} |",
        f"| R5 stability | {decision.r5_stability:.2f} |",
        "",
    ]
    if decision.reasons:
        lines += [
            "## Failed bars" if decision.verdict == "FAIL" else "## INVALID reasons",
            "",
        ]
        lines += [f"- {reason}" for reason in decision.reasons]
        lines += [""]
    lines += [
        "## Secondary result -- cross-source-excluded population (never deciding)",
        "",
        f"Verdict under the same rule, excluding every cross-source trial: "
        f"**{secondary.verdict}**.",
        "",
        "## What a PASS does not establish",
        "",
        "Constructed labels are rubric-consistency, not agreement with a human "
        "on a real bundle; one model; de-identified analogues may be easier "
        "than real documents.",
        "",
    ]
    return "\n".join(lines)


def _run_decide(calibration_path: pathlib.Path, confirmation_path: pathlib.Path) -> int:
    calibration = _load_arm(calibration_path)
    confirmation = _load_arm(confirmation_path)
    decision = decide(calibration, confirmation)
    secondary = _secondary_decide(calibration, confirmation)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results_dir = pathlib.Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    report = _render_verdict(calibration_path, confirmation_path, decision, secondary)
    (
        results_dir
        / f"auto-merge-verdict-{stamp}-{calibration.model.replace(':', '-')}.md"
    ).write_text(report, encoding="utf-8")
    print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["calibration", "confirmation"])
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the fixture, D3 eligibility, and decide() with no model and no network",
    )
    parser.add_argument(
        "--decide",
        nargs=2,
        metavar=("CALIBRATION_JSON", "CONFIRMATION_JSON"),
        help="pure, offline: apply the pre-registered rule to two stored runs-*.json files",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.decide:
        return _run_decide(pathlib.Path(args.decide[0]), pathlib.Path(args.decide[1]))
    if args.arm is None:
        parser.error("--arm is required unless --self-test or --decide is given")
    return _run_arm(args.arm, args.runs, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
