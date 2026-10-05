"""The structural Identity auto-merge seam (#1298, ADR-0049): the class
predicate, the measured constants, and run eligibility.

Every guard below is tested from an all-valid baseline with exactly one
deviation, so mutating that one guard alone turns exactly its own test red.
No test reaches an LLM or a network: `list_models` is always a stub.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import functools
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from openkos import config, fsio, lock, sensitivity
from openkos.application import auto_merge, lifecycle, unmerge_service
from openkos.application import pending as application_pending
from openkos.bundle import ledger as bundle_ledger
from openkos.llm.base import BackendError, InstalledModel
from openkos.llm.prompts import prompt_hash
from openkos.resolution import adjudication
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict
from openkos.resolution.candidates import CandidateGroup, Tier
from openkos.state import adjudications as adjudications_store
from openkos.vcs import git as vcs_git
from tests.unit.application.curation_support import (
    CommitRecorder,
    make_workspace,
    tree,
    write_concept,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RESULTS = _REPO_ROOT / "evals" / "auto_merge" / "results"
_EVAL = _REPO_ROOT / "evals" / "auto_merge" / "run_structural_class.py"


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


# --------------------------------------------------------------------------- #
# the class predicate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "group",
    [
        pytest.param(_group(("concepts/x", "concepts/x-2")), id="base/-2 Concept"),
        pytest.param(
            _group(("projects/x", "projects/x-13"), "Project"), id="base/-13 Project"
        ),
        pytest.param(
            _group(("concepts/x-2", "concepts/x")), id="suffixed id listed first"
        ),
    ],
)
def test_in_class_admits_a_base_and_dash_n_pair_in_either_order(
    group: CandidateGroup,
) -> None:
    assert auto_merge.in_structural_class(group) is True


@pytest.mark.parametrize(
    "group",
    [
        pytest.param(
            _group(("concepts/x", "concepts/x-2"), tier=Tier.LOW), id="LOW tier"
        ),
        pytest.param(
            _group(("concepts/x", "concepts/x-2"), tier=Tier.ACRONYM),
            id="ACRONYM tier",
        ),
        pytest.param(
            _group(("concepts/x", "concepts/x-2", "concepts/x-3")), id="three members"
        ),
        pytest.param(_group(("events/x", "events/x-2"), "Event"), id="Event"),
        pytest.param(_group(("people/x", "people/x-2"), "Person"), id="Person"),
        pytest.param(
            _group(
                ("concepts/x", "concepts/x-2"),
                "Concept+Entity",
                member_types=("Concept", "Entity"),
            ),
            id="cross-type",
        ),
        pytest.param(_group(("concepts/x-a", "concepts/x-b")), id="-a/-b"),
        pytest.param(_group(("concepts/x-1", "concepts/x-2")), id="-1/-2 siblings"),
        pytest.param(_group(("concepts/x", "concepts/x-2a")), id="non-digit suffix"),
        pytest.param(_group(("concepts/x", "entities/x-2")), id="other directory"),
    ],
)
def test_in_class_each_disqualifier_excludes_on_its_own(group: CandidateGroup) -> None:
    assert auto_merge.in_structural_class(group) is False


def test_in_class_reads_no_file_and_does_not_raise(tmp_path: Path) -> None:
    """Spec "The predicate reads no verdict and no file": the member files do
    not exist anywhere, and the predicate still answers."""
    group = _group(("concepts/ghost", "concepts/ghost-2"))
    assert not (tmp_path / "concepts" / "ghost.md").exists()
    assert auto_merge.in_structural_class(group) is True


def _load_eval(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import the harness by path. It must be registered in `sys.modules`
    while it executes (its dataclasses resolve their annotations through it),
    and the registration is undone with the test."""
    name = "_run_structural_class"
    spec = importlib.util.spec_from_file_location(name, _EVAL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.syspath_prepend(str(_EVAL.parent))
    spec.loader.exec_module(module)
    return module


def test_the_eval_harness_uses_the_production_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec "The eval harness uses the production predicate": one predicate,
    no second copy. Identity AND source are both checked, so re-defining it in
    the eval fails whichever way it is spelled."""
    module = _load_eval(monkeypatch)
    assert module.in_structural_class is auto_merge.in_structural_class
    tree = ast.parse(_EVAL.read_text(encoding="utf-8"))
    defined = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "in_structural_class"
    ]
    assert defined == []


# --------------------------------------------------------------------------- #
# measured constants
# --------------------------------------------------------------------------- #

_CALIBRATION = (
    _RESULTS / "runs-structural-calibration-20261005T164453Z-gemma4-26b-a4b.json"
)
_CONFIRMATION = (
    _RESULTS / "runs-structural-confirmation-20261005T170308Z-gemma4-26b-a4b.json"
)
_VERDICT = _RESULTS / "auto-merge-verdict-1298-20261005T181724Z-gemma4-26b-a4b.md"


@pytest.mark.parametrize("path", [_CALIBRATION, _CONFIRMATION], ids=["cal", "conf"])
def test_constants_match_the_committed_stamp(path: Path) -> None:
    """Each constant equals its evidence, in BOTH measured arms, so a
    constant can never drift from the run it cites. Reads committed JSON
    only; no model."""
    run = json.loads(path.read_text(encoding="utf-8"))
    stamp = run["stamp"]
    prompts = {p["id"]: p["sha256_16"] for p in stamp["prompts"]}

    assert auto_merge.MEASURED_MODEL == run["model"] == stamp["model"]["name"]
    assert stamp["model"]["digest"] == auto_merge.MEASURED_MODEL_DIGEST
    assert stamp["harness"]["commit"] == auto_merge.MEASURED_AT_COMMIT
    assert stamp["harness"]["dirty"] is False
    assert prompts["adjudication/system"] == auto_merge.MEASURED_PROMPT_SHA256_16
    assert run["settings"]["context_window"] == auto_merge.MEASURED_CONTEXT_WINDOW
    assert (
        run["settings"]["max_generation_tokens"]
        == auto_merge.MEASURED_MAX_GENERATION_TOKENS
    )


def test_t_star_matches_the_verdict_file() -> None:
    assert "`t*`: 0.9000" in _VERDICT.read_text(encoding="utf-8")
    assert auto_merge.T_STAR == 0.90


_REUSE_NOTE = (
    "A rubric change invalidates the measurement behind ADR-0049: re-run the "
    "pre-registered harness (evals/auto_merge) and only then update the "
    "measured constants."
)


def test_the_measured_rubric_is_the_current_rubric() -> None:
    assert adjudication.rubric_digest() == auto_merge.MEASURED_RUBRIC_DIGEST, (
        _REUSE_NOTE
    )


def test_the_measured_prompt_hash_is_the_current_prompt_hash() -> None:
    assert (
        prompt_hash(adjudication._SYSTEM_PROMPT) == auto_merge.MEASURED_PROMPT_SHA256_16
    ), _REUSE_NOTE


# --------------------------------------------------------------------------- #
# static run eligibility
# --------------------------------------------------------------------------- #


def _baseline_cfg(tmp_path: Path, **overrides: Any) -> config.Config:
    """An all-valid `Config`: the measured model for adjudication, the
    measured window and generation cap, nothing sampled."""
    config.write_config(tmp_path)
    cfg = config.read_config(tmp_path)
    cfg = dataclasses.replace(
        cfg,
        backend="ollama",
        model="qwen3:8b",
        models={"adjudication": auto_merge.MEASURED_MODEL},
        context_window=auto_merge.MEASURED_CONTEXT_WINDOW,
        max_generation_tokens=auto_merge.MEASURED_MAX_GENERATION_TOKENS,
        temperature=None,
        seed=None,
    )
    return dataclasses.replace(cfg, **overrides) if overrides else cfg


def test_the_baseline_has_no_static_reason(tmp_path: Path) -> None:
    assert auto_merge.static_ineligibility(_baseline_cfg(tmp_path)) == ()


def test_a_different_adjudication_model_is_ineligible(tmp_path: Path) -> None:
    cfg = _baseline_cfg(tmp_path, models={"adjudication": "qwen3:8b"})
    (reason,) = auto_merge.static_ineligibility(cfg)
    assert "gemma4:26b-a4b" in reason
    assert "qwen3:8b" in reason
    assert "model" in reason


def test_an_adjudication_opt_out_falls_back_to_the_global_model(
    tmp_path: Path,
) -> None:
    """`models: {adjudication: null}` declines the packaged default and lands
    on `cfg.model` -- the resolver `chat_client` uses -- so it is ineligible
    when the global model is not the measured one."""
    cfg = _baseline_cfg(tmp_path, models={"adjudication": None}, model="llama3:8b")
    (reason,) = auto_merge.static_ineligibility(cfg)
    assert "llama3:8b" in reason


def test_the_global_model_alone_does_not_change_an_eligible_task_model(
    tmp_path: Path,
) -> None:
    cfg = _baseline_cfg(tmp_path, model="anything:1b")
    assert auto_merge.static_ineligibility(cfg) == ()


def test_an_unset_context_window_is_ineligible(tmp_path: Path) -> None:
    cfg = _baseline_cfg(tmp_path, context_window=None)
    (reason,) = auto_merge.static_ineligibility(cfg)
    assert "context_window" in reason
    assert "12288" in reason
    assert "None" in reason


def test_another_context_window_is_ineligible(tmp_path: Path) -> None:
    cfg = _baseline_cfg(tmp_path, context_window=16384)
    (reason,) = auto_merge.static_ineligibility(cfg)
    assert "context_window" in reason
    assert "12288" in reason
    assert "16384" in reason


def test_a_max_generation_tokens_other_than_measured_is_ineligible(
    tmp_path: Path,
) -> None:
    cfg = _baseline_cfg(tmp_path, max_generation_tokens=4096)
    (reason,) = auto_merge.static_ineligibility(cfg)
    assert "max_generation_tokens" in reason
    assert "8192" in reason
    assert "4096" in reason


def test_a_pinned_temperature_is_ineligible(tmp_path: Path) -> None:
    cfg = _baseline_cfg(tmp_path, temperature=0.0)
    (reason,) = auto_merge.static_ineligibility(cfg)
    assert "temperature" in reason
    assert "0.0" in reason


def test_a_pinned_seed_is_ineligible(tmp_path: Path) -> None:
    cfg = _baseline_cfg(tmp_path, seed=7)
    (reason,) = auto_merge.static_ineligibility(cfg)
    assert "seed" in reason
    assert "7" in reason


def test_a_different_rubric_digest_is_ineligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hits: list[int] = []

    def other() -> str:
        hits.append(1)
        return "sha256:" + "0" * 64

    monkeypatch.setattr(auto_merge, "rubric_digest", other)
    (reason,) = auto_merge.static_ineligibility(_baseline_cfg(tmp_path))
    assert hits, "the patch must be read at the exact import site"
    assert "rubric" in reason
    assert "sha256:" + "0" * 12 in reason


def test_explicit_keys_equal_to_the_measured_values_stay_eligible(
    tmp_path: Path,
) -> None:
    """An explicit key that spells the measured value is the same fact as the
    default; only None or another value deviates."""
    cfg = _baseline_cfg(
        tmp_path, context_window=12288, max_generation_tokens=8192, temperature=None
    )
    assert auto_merge.static_ineligibility(cfg) == ()


def test_every_failed_static_check_is_named_in_order(tmp_path: Path) -> None:
    cfg = _baseline_cfg(
        tmp_path,
        models={"adjudication": "qwen3:8b"},
        context_window=16384,
        seed=3,
    )
    reasons = auto_merge.static_ineligibility(cfg)
    assert len(reasons) == 3
    assert "model" in reasons[0]
    assert "context_window" in reasons[1]
    assert "seed" in reasons[2]


# --------------------------------------------------------------------------- #
# the model digest and the whole-run eligibility
# --------------------------------------------------------------------------- #

_GOOD = InstalledModel(
    tag=auto_merge.MEASURED_MODEL,
    family="gemma",
    digest=auto_merge.MEASURED_MODEL_DIGEST,
)


class _ListSpy:
    def __init__(self, models: list[InstalledModel] | Exception) -> None:
        self._models = models
        self.calls = 0

    def __call__(self) -> list[InstalledModel]:
        self.calls += 1
        if isinstance(self._models, Exception):
            raise self._models
        return self._models


def _digest_reason(models: list[InstalledModel] | Exception) -> str | None:
    return auto_merge.model_digest_ineligibility(
        _ListSpy(models), auto_merge.MEASURED_MODEL
    )


def test_a_listed_model_with_the_measured_digest_has_no_reason() -> None:
    assert _digest_reason([InstalledModel("other:1", None, "ab" * 32), _GOOD]) is None


def test_a_listing_failure_is_a_reason() -> None:
    reason = _digest_reason(BackendError("down"))
    assert reason is not None
    assert "could not list installed models" in reason


def test_an_unlisted_tag_is_a_reason() -> None:
    reason = _digest_reason([InstalledModel("other:1", None, "ab" * 32)])
    assert reason is not None
    assert "not listed" in reason


def test_a_listed_tag_with_no_digest_is_a_reason() -> None:
    reason = _digest_reason([InstalledModel(auto_merge.MEASURED_MODEL, "gemma", None)])
    assert reason is not None
    assert "digest" in reason
    assert "unknown" in reason


def test_a_different_digest_is_a_reason_shown_as_twelve_hex_chars() -> None:
    other = "ab" * 32
    reason = _digest_reason([InstalledModel(auto_merge.MEASURED_MODEL, "gemma", other)])
    assert reason is not None
    assert other[:12] in reason
    assert other not in reason
    assert auto_merge.MEASURED_MODEL_DIGEST[:12] in reason


@pytest.mark.parametrize(
    "observed",
    [
        pytest.param(auto_merge.MEASURED_MODEL_DIGEST[:12], id="truncated"),
        pytest.param(auto_merge.MEASURED_MODEL_DIGEST + "00", id="extended"),
    ],
)
def test_a_digest_that_only_shares_a_prefix_does_not_match(observed: str) -> None:
    """Exact equality, in both directions: neither a truncated digest nor one
    that merely starts with the measured one is the measured model."""
    reason = _digest_reason(
        [InstalledModel(auto_merge.MEASURED_MODEL, "gemma", observed)]
    )
    assert reason is not None
    assert "differs" in reason


def test_the_lookup_is_by_exact_tag_not_prefix() -> None:
    sibling = InstalledModel(
        auto_merge.MEASURED_MODEL + "-q4", "gemma", auto_merge.MEASURED_MODEL_DIGEST
    )
    reason = _digest_reason([sibling])
    assert reason is not None
    assert "not listed" in reason


def test_the_baseline_run_is_eligible(tmp_path: Path) -> None:
    spy = _ListSpy([_GOOD])
    outcome = auto_merge.run_eligibility(_baseline_cfg(tmp_path), spy)
    assert outcome.eligible is True
    assert outcome.reasons == ()
    assert spy.calls == 1


def test_a_static_reason_means_the_listing_is_never_called(tmp_path: Path) -> None:
    spy = _ListSpy([_GOOD])
    cfg = _baseline_cfg(tmp_path, context_window=16384)
    outcome = auto_merge.run_eligibility(cfg, spy)
    assert outcome.eligible is False
    assert len(outcome.reasons) == 1
    assert "context_window" in outcome.reasons[0]
    assert spy.calls == 0


def test_a_digest_reason_makes_the_run_ineligible(tmp_path: Path) -> None:
    spy = _ListSpy([InstalledModel(auto_merge.MEASURED_MODEL, "gemma", "ab" * 32)])
    outcome = auto_merge.run_eligibility(_baseline_cfg(tmp_path), spy)
    assert outcome.eligible is False
    assert len(outcome.reasons) == 1
    assert ("ab" * 6) in outcome.reasons[0]
    assert spy.calls == 1


def test_run_eligibility_checks_the_resolved_adjudication_tag(
    tmp_path: Path,
) -> None:
    """The digest is looked up for the model the adjudication call would use
    (the resolver's answer), not for the global `cfg.model`."""
    seen: list[str] = []

    def listing() -> list[InstalledModel]:
        seen.append("listed")
        return [_GOOD]

    cfg = _baseline_cfg(tmp_path, model="global:1b")
    assert auto_merge.run_eligibility(cfg, listing).eligible is True
    assert seen == ["listed"]


# --------------------------------------------------------------------------- #
# the stacked-body guardrail predicate (extracted for the pass, task 2.9)
# --------------------------------------------------------------------------- #


def _family(base: str = "concepts/foo") -> CandidateGroup:
    """A base/-2 HIGH group, ids already in the candidate generator's order."""
    return _group((base, f"{base}-2"))


def _verdict(
    group: CandidateGroup,
    verdict: Verdict = Verdict.SAME,
    confidence: float = 0.95,
) -> AdjudicatedCandidate:
    return AdjudicatedCandidate(
        candidate=group, verdict=verdict, confidence=confidence, rationale="r"
    )


def _prepare(root: Path, group: CandidateGroup) -> lifecycle.PreparedMerge:
    layout = config.WorkspaceLayout(root)
    survivor, absorbed, _ = lifecycle.ordered_merge_pair(
        layout.bundle_dir, group.member_ids
    )
    prepared = lifecycle.prepare_one_merge(
        root,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        group,
        ordered_pair=(survivor, absorbed),
    )
    assert prepared is not None
    return prepared


@pytest.fixture
def family_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, "concepts/foo", title="Foo", body="Foo body.")
    write_concept(root, "concepts/foo-2", title="Foo", body="Foo copy body.")
    return root


@pytest.mark.parametrize(
    ("absorbed_chars", "refused"),
    [(80, True), (79, False)],
    ids=["at the 0.8 guardrail", "just under it"],
)
def test_stacked_body_refused_follows_the_guardrail_threshold(
    family_workspace: Path, absorbed_chars: int, refused: bool
) -> None:
    prepared = dataclasses.replace(
        _prepare(family_workspace, _family()),
        stacked_body=lifecycle.StackedBodyReport(
            absorbed_chars=absorbed_chars, merged_chars=100
        ),
    )
    assert lifecycle.stacked_body_refused(prepared) is refused


def test_stacked_body_refused_is_false_without_a_stacked_body(
    family_workspace: Path,
) -> None:
    prepared = dataclasses.replace(
        _prepare(family_workspace, _family()), stacked_body=None
    )
    assert lifecycle.stacked_body_refused(prepared) is False


def test_apply_same_preview_uses_the_shared_guardrail_predicate(
    family_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One guardrail predicate, not two: the batch preview calls the very
    function the auto-merge pass calls (spy at the exact call site)."""
    seen: list[lifecycle.PreparedMerge] = []
    real = lifecycle.stacked_body_refused

    def spy(prepared: lifecycle.PreparedMerge) -> bool:
        seen.append(prepared)
        return real(prepared)

    monkeypatch.setattr(lifecycle, "stacked_body_refused", spy)
    layout = config.WorkspaceLayout(family_workspace)
    preview = lifecycle.preview_apply_same(
        family_workspace,
        layout,
        layout.bundle_dir / "index.md",
        layout.bundle_dir / "log.md",
        [_verdict(_family())],
    )
    assert len(seen) == 1
    assert len(preview.previewed) == 1


# --------------------------------------------------------------------------- #
# the judging partition (fresh verdicts only, task 2.1)
# --------------------------------------------------------------------------- #

_A = _group(("concepts/a", "concepts/a-2"))  # in class, served from the cache
_B = _group(("concepts/b", "concepts/b-2"))  # in class, needs judging anyway
_C = _group(("concepts/p", "concepts/q"), tier=Tier.LOW)  # not in class, served
_D = _group(("concepts/d", "concepts/d-2"))  # in class, served from a queue row
_E = _group(("concepts/r", "concepts/s"), tier=Tier.LOW)  # not in class, unserved


def _key(group: CandidateGroup) -> str:
    return adjudications_store.group_key_for(group.member_ids)


def _partition(**overrides: Any) -> tuple[dict[str, AdjudicatedCandidate], list[Any]]:
    """The eligible-interactive baseline with at most one deviation."""
    kwargs: dict[str, Any] = {
        "auto_merge": True,
        "interactive": True,
        "statically_eligible": True,
    }
    kwargs.update(overrides)
    served = {_key(g): _verdict(g, confidence=0.99) for g in (_A, _C, _D)}
    return auto_merge.judging_partition(
        [_A, _B, _C, _D, _E], served, [_B, _E], **kwargs
    )


def test_without_the_flag_the_partition_is_todays_serve() -> None:
    served, to_judge = _partition(auto_merge=False)
    assert set(served) == {_key(_A), _key(_C), _key(_D)}
    assert to_judge == [_B, _E]


def test_a_statically_ineligible_run_keeps_todays_serve() -> None:
    served, to_judge = _partition(statically_eligible=False)
    assert set(served) == {_key(_A), _key(_C), _key(_D)}
    assert to_judge == [_B, _E]


def test_an_eligible_interactive_run_rejudges_every_in_class_group() -> None:
    served, to_judge = _partition()
    # cached (A) and queued (D) in-class rows are no longer served; the
    # non-class served group is untouched
    assert set(served) == {_key(_C)}
    # judging order: what was already queued for judging, then the formerly
    # served in-class groups in candidate order
    assert to_judge == [_B, _E, _A, _D]


def test_an_in_class_group_already_to_judge_is_not_judged_twice() -> None:
    _served, to_judge = _partition()
    assert to_judge.count(_B) == 1


def test_an_eligible_non_interactive_run_judges_only_the_in_class_groups() -> None:
    served, to_judge = _partition(interactive=False)
    assert to_judge == [_A, _B, _D]
    # non-class groups are neither judged (E) nor served (C)
    assert served == {}


# --------------------------------------------------------------------------- #
# the strict confidential / LLM-blocked predicate (task 2.4)
# --------------------------------------------------------------------------- #


def _blocked_bundle(tmp_path: Path, deviant: str) -> Path:
    """A bundle holding a private and a public concept plus ONE deviant."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True)

    def write(name: str, text: str) -> Path:
        path = bundle_dir / "concepts" / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def doc(sensitivity_line: str) -> str:
        return f"---\ntype: Concept\ntitle: T\n{sensitivity_line}---\n\nBody.\n"

    write("priv", doc("sensitivity: private\n"))
    write("pub", doc("sensitivity: public\n"))
    if deviant == "confidential":
        write("dev", doc("sensitivity: confidential\n"))
    elif deviant == "absent":
        write("dev", doc(""))
    elif deviant == "blank":
        write("dev", doc("sensitivity: ''\n"))
    elif deviant == "unparseable":
        write("dev", "---\ntitle: [unclosed\nsensitivity: private\n---\n\nBody.\n")
    elif deviant == "unreadable":
        path = write("dev", doc("sensitivity: private\n"))
        path.chmod(0)
    return bundle_dir


def test_private_and_public_members_are_not_blocked(tmp_path: Path) -> None:
    bundle_dir = _blocked_bundle(tmp_path, "none")
    assert auto_merge.strict_blocked_members(bundle_dir) == frozenset()


@pytest.mark.parametrize(
    "deviant", ["confidential", "absent", "blank", "unparseable", "unreadable"]
)
def test_each_unsafe_member_is_blocked_on_its_own(tmp_path: Path, deviant: str) -> None:
    if deviant == "unreadable" and os.geteuid() == 0:
        pytest.skip("root reads a mode-0 file")
    bundle_dir = _blocked_bundle(tmp_path, deviant)
    try:
        assert auto_merge.strict_blocked_members(bundle_dir) == {"concepts/dev"}
    finally:
        deviant_path = bundle_dir / "concepts" / "dev.md"
        if deviant_path.exists():
            deviant_path.chmod(0o644)


def test_the_blocked_set_is_computed_with_both_escape_hatches_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run's `include_confidential` and `local_exemption` never widen what
    the auto pass may merge: the call ignores both by passing both off."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    real = sensitivity.sensitive_concept_ids

    def spy(*args: Any, **kwargs: Any) -> frozenset[str]:
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(sensitivity, "sensitive_concept_ids", spy)
    bundle_dir = _blocked_bundle(tmp_path, "confidential")
    assert auto_merge.strict_blocked_members(bundle_dir) == {"concepts/dev"}
    assert calls == [
        (
            (bundle_dir,),
            {"include_confidential": False, "local_exemption": False},
        )
    ]


# --------------------------------------------------------------------------- #
# the per-group planner, gates 1-6 (task 2.6)
# --------------------------------------------------------------------------- #

_CHANGED = "changed since it was judged"
_NOT_FRESH = "no fresh verdict this run"
_BLOCKED = "a member is confidential or cannot be sent to the model"
_SURVIVOR_TAKEN = "survivor already merged this run"


def _plan(
    results: list[AdjudicatedCandidate], **overrides: Any
) -> auto_merge.AutoMergePlan:
    """The all-pass baseline with at most one deviation: every result is fresh,
    nothing is blocked, no cross-type concern, the real survivor rule."""
    kwargs: dict[str, Any] = {
        "fresh_keys": frozenset(_key(r.candidate) for r in results),
        "blocked": frozenset(),
        "cross_type_concern": lambda pair: None,
        "ordered_pair": functools.partial(lifecycle.ordered_merge_pair, Path("unused")),
    }
    kwargs.update(overrides)
    return auto_merge.plan_auto_merges(results, **kwargs)


def _only(plan: auto_merge.AutoMergePlan) -> auto_merge.AutoMergeSkip:
    assert plan.planned == ()
    assert len(plan.skipped) == 1
    return plan.skipped[0]


def test_the_all_pass_baseline_plans_one_merge_with_the_base_as_survivor() -> None:
    result = _verdict(_family())
    plan = _plan([result])
    assert plan.planned == (
        auto_merge.PlannedAutoMerge(result, "concepts/foo", "concepts/foo-2"),
    )
    assert plan.skipped == ()


def test_the_base_survives_whatever_order_the_group_lists_its_members() -> None:
    result = _verdict(_group(("concepts/foo-2", "concepts/foo")))
    (planned,) = _plan([result]).planned
    assert (planned.survivor, planned.absorbed) == ("concepts/foo", "concepts/foo-2")


def test_gate_1_a_served_verdict_is_never_acted_on() -> None:
    result = _verdict(_family(), confidence=1.0)
    skip = _only(_plan([result], fresh_keys=frozenset()))
    assert skip == auto_merge.AutoMergeSkip(_family().member_ids, _NOT_FRESH)


@pytest.mark.parametrize("verdict", [Verdict.DIFFERENT, Verdict.UNCERTAIN])
def test_gate_2_only_a_same_verdict_is_considered(verdict: Verdict) -> None:
    plan = _plan([_verdict(_family(), verdict, 0.99)])
    assert plan.planned == ()
    assert plan.skipped == ()


def test_gate_3_the_threshold_is_inclusive() -> None:
    plan = _plan([_verdict(_family(), confidence=0.90)])
    assert len(plan.planned) == 1


@pytest.mark.parametrize("confidence", [0.8999, 0.85])
def test_gate_3_below_the_threshold_is_skipped_with_both_numbers(
    confidence: float,
) -> None:
    skip = _only(_plan([_verdict(_family(), confidence=confidence)]))
    assert skip.reason == f"confidence {confidence} below 0.90"


def test_gate_3_uses_the_measured_constant_not_a_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auto_merge, "T_STAR", 0.5)
    assert len(_plan([_verdict(_family(), confidence=0.6)]).planned) == 1
    skip = _only(_plan([_verdict(_family(), confidence=0.4)]))
    assert skip.reason == "confidence 0.4 below 0.50"


@pytest.mark.parametrize(
    "blocked_member", ["concepts/foo", "concepts/foo-2"], ids=["survivor", "absorbed"]
)
def test_gate_4_a_blocked_member_is_skipped(blocked_member: str) -> None:
    skip = _only(_plan([_verdict(_family())], blocked=frozenset({blocked_member})))
    assert skip == auto_merge.AutoMergeSkip(_family().member_ids, _BLOCKED)


def test_gate_5_a_cross_type_concern_is_skipped_with_its_text() -> None:
    seen: list[tuple[str, str]] = []

    def concern(pair: tuple[str, str]) -> str | None:
        seen.append(pair)
        return "members declare different OKF types (Concept / Person)"

    skip = _only(_plan([_verdict(_family())], cross_type_concern=concern))
    assert skip.reason == "members declare different OKF types (Concept / Person)"
    assert seen == [("concepts/foo", "concepts/foo-2")]


def test_gate_6_one_merge_per_survivor_in_candidate_order() -> None:
    first = _verdict(_group(("concepts/foo", "concepts/foo-2")))
    second = _verdict(_group(("concepts/foo", "concepts/foo-3")))
    plan = _plan([first, second])
    assert [(p.survivor, p.absorbed) for p in plan.planned] == [
        ("concepts/foo", "concepts/foo-2")
    ]
    assert plan.skipped == (
        auto_merge.AutoMergeSkip(second.candidate.member_ids, _SURVIVOR_TAKEN),
    )


def test_gate_6_a_skipped_group_does_not_claim_its_survivor() -> None:
    low = _verdict(_group(("concepts/foo", "concepts/foo-2")), confidence=0.5)
    later = _verdict(_group(("concepts/foo", "concepts/foo-3")))
    plan = _plan([low, later])
    assert [p.absorbed for p in plan.planned] == ["concepts/foo-3"]
    assert [s.reason for s in plan.skipped] == ["confidence 0.5 below 0.90"]


@pytest.mark.parametrize(
    "group",
    [
        pytest.param(_group(("concepts/x", "concepts/x-2", "concepts/x-3")), id="3"),
        pytest.param(_group(("concepts/p", "concepts/q"), tier=Tier.LOW), id="low"),
        pytest.param(_group(("concepts/m", "concepts/n")), id="not a suffix family"),
    ],
)
def test_a_group_outside_the_class_is_neither_planned_nor_skipped(
    group: CandidateGroup,
) -> None:
    plan = _plan([_verdict(group, confidence=0.99)])
    assert plan.planned == ()
    assert plan.skipped == ()


def test_the_first_failing_gate_is_the_reported_reason() -> None:
    # gates 1 and 3 both fail: not fresh, and below the threshold
    both = _plan([_verdict(_family(), confidence=0.5)], fresh_keys=frozenset())
    assert _only(both).reason == _NOT_FRESH
    # gates 3 and 4 both fail
    low_and_blocked = _plan(
        [_verdict(_family(), confidence=0.5)], blocked=frozenset({"concepts/foo"})
    )
    assert _only(low_and_blocked).reason == "confidence 0.5 below 0.90"
    # gates 4 and 5 both fail
    blocked_and_cross = _plan(
        [_verdict(_family())],
        blocked=frozenset({"concepts/foo"}),
        cross_type_concern=lambda pair: "types differ",
    )
    assert _only(blocked_and_cross).reason == _BLOCKED


# --------------------------------------------------------------------------- #
# the commit phase: one lock, one commit (tasks 2.11-2.13)
# --------------------------------------------------------------------------- #


def _git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _pin_git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CI runner configures no identity; `GIT_CONFIG_COUNT` is what
    `git config` reads back (`GIT_AUTHOR_*` is not)."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "openkos tests")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "tests@openkos.invalid")


def _pass_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bases: Sequence[str] = ("foo", "bar", "baz"),
) -> Path:
    """A real workspace in a real git repo holding one base/-2 pair per base,
    everything committed so the pass's deletions and rewrites are tracked."""
    _pin_git_identity(monkeypatch)
    root = make_workspace(tmp_path, monkeypatch)
    for base in bases:
        write_concept(root, f"concepts/{base}", title=base, body=f"{base} body.")
        write_concept(root, f"concepts/{base}-2", title=base, body=f"{base} copy.")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


class _Section:
    """A `CommitSection` that counts entries and knows whether it is held."""

    def __init__(self) -> None:
        self.entered = 0
        self.held = False

    @contextlib.contextmanager
    def __call__(self) -> Iterator[None]:
        self.entered += 1
        self.held = True
        try:
            yield
        finally:
            self.held = False


class _Commits:
    """The real `commit_paths`, recording every call and whether the section
    was held while it ran."""

    def __init__(self, section: _Section) -> None:
        self.section = section
        self.calls: list[tuple[list[str], str]] = []
        self.held: list[bool] = []

    def __call__(self, root: Path, paths: Sequence[str], message: str) -> str | None:
        self.calls.append((list(paths), message))
        self.held.append(self.section.held)
        try:
            return vcs_git.commit_paths(root, paths, message)
        except (vcs_git.GitError, OSError):
            return None


@dataclasses.dataclass
class _Run:
    root: Path
    layout: config.WorkspaceLayout
    section: _Section
    commits: _Commits
    results: list[AdjudicatedCandidate]
    judged: dict[str, str | None]

    def apply(
        self,
        plan: auto_merge.AutoMergePlan | None = None,
        digest_of: Callable[[str], str | None] | None = None,
    ) -> Any:
        if digest_of is None:
            digest_of = application_pending.current_finding_digest(
                self.layout.bundle_dir
            )
        return auto_merge.apply_auto_merges(
            self.root,
            self.layout,
            plan if plan is not None else _plan(self.results),
            commit_section=self.section,
            autocommit=self.commits,
            judged_digests=self.judged,
            digest_of=digest_of,
        )


def _prepare_run(root: Path, bases: Sequence[str] = ("foo", "bar", "baz")) -> _Run:
    layout = config.WorkspaceLayout(root)
    results = [_verdict(_family(f"concepts/{base}")) for base in bases]
    digest_of = application_pending.current_finding_digest(layout.bundle_dir)
    judged = {m: digest_of(m) for r in results for m in r.candidate.member_ids}
    section = _Section()
    return _Run(root, layout, section, _Commits(section), results, judged)


def _sidecar(root: Path, base: str) -> str:
    layout = config.WorkspaceLayout(root)
    return (
        bundle_ledger.ledger_path_for(f"concepts/{base}", layout.bundle_dir)
        .relative_to(root)
        .as_posix()
    )


def _committed(root: Path) -> list[str]:
    return _git(root, "show", "--name-only", "--format=", "HEAD").split()


def test_three_merges_share_one_lock_and_one_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch)
    run = _prepare_run(root)
    before = _git(root, "rev-list", "--count", "HEAD")

    outcome = run.apply()

    assert run.section.entered == 1
    assert [(r.survivor, r.absorbed) for r in outcome.applied] == [
        ("concepts/foo", "concepts/foo-2"),
        ("concepts/bar", "concepts/bar-2"),
        ("concepts/baz", "concepts/baz-2"),
    ]
    assert len(run.commits.calls) == 1
    assert run.commits.held == [True]
    paths, message = run.commits.calls[0]
    expected = [
        "bundle/index.md",
        "bundle/log.md",
        *(
            p
            for base in ("foo", "bar", "baz")
            for p in (
                f"bundle/concepts/{base}.md",
                f"bundle/concepts/{base}-2.md",
                _sidecar(root, base),
            )
        ),
    ]
    assert paths == expected
    assert message == (
        "openkos: auto-merge 3 pair(s) in the measured identity class\n\n"
        "merge concepts/foo-2 into concepts/foo\n"
        "merge concepts/bar-2 into concepts/bar\n"
        "merge concepts/baz-2 into concepts/baz"
    )
    assert outcome.sha is not None
    assert int(_git(root, "rev-list", "--count", "HEAD")) == int(before) + 1
    assert sorted(_committed(root)) == sorted(expected)


def test_each_merge_keeps_its_own_log_bullet_and_a_stacked_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch)
    run = _prepare_run(root)

    run.apply()

    log = (root / "bundle" / "log.md").read_text(encoding="utf-8")
    bullets = [line for line in log.splitlines() if "concepts/foo-2" in line]
    assert len(bullets) == 1
    for base in ("foo", "bar", "baz"):
        assert len([ln for ln in log.splitlines() if f"concepts/{base}-2" in ln]) == 1
        survivor = (root / "bundle" / "concepts" / f"{base}.md").read_text("utf-8")
        assert f"## Merged content (concepts/{base}-2)" in survivor
        assert f"{base} copy." in survivor
        assert not (root / "bundle" / "concepts" / f"{base}-2.md").exists()


def test_the_pass_never_asks_for_a_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No reconciliation seam is reachable from the pass: `reconcile_planned`
    (the single decision for the #645 model pass) is never consulted."""

    def boom(*args: object, **kwargs: object) -> bool:
        raise AssertionError("the auto-merge pass consulted the reconciliation")

    monkeypatch.setattr(lifecycle, "reconcile_planned", boom)
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    outcome = _prepare_run(root, ("foo",)).apply()
    assert len(outcome.applied) == 1


def test_a_record_carries_the_hash_unmerge_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo", "bar"))
    outcome = _prepare_run(root, ("foo", "bar")).apply()
    assert len(outcome.applied) == 2
    for record in outcome.applied:
        written = (root / "bundle" / f"{record.survivor}.md").read_text("utf-8")
        assert record.survivor_after_sha256 == bundle_ledger.survivor_sha256(written)
        assert record.confidence == 0.95


def test_zero_planned_merges_make_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    run = _prepare_run(root, ("foo",))
    outcome = run.apply(auto_merge.AutoMergePlan((), ()))
    assert run.commits.calls == []
    assert outcome == auto_merge.AutoMergeOutcome((), (), None, None)


def test_a_user_staged_file_is_not_swept_into_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    (root / "notes.txt").write_text("mine\n", encoding="utf-8")
    _git(root, "add", "notes.txt")
    run = _prepare_run(root, ("foo",))

    run.apply()

    assert "notes.txt" not in _committed(root)
    assert _git(root, "diff", "--cached", "--name-only").split() == ["notes.txt"]


def test_the_absorbed_deletion_is_part_of_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    _prepare_run(root, ("foo",)).apply()
    deleted = _git(root, "show", "--diff-filter=D", "--name-only", "--format=", "HEAD")
    assert deleted.split() == ["bundle/concepts/foo-2.md"]


def test_a_degraded_autocommit_leaves_the_merges_standing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    run = _prepare_run(root, ("foo",))
    degraded: list[str] = []

    def no_commit(root: Path, paths: Sequence[str], message: str) -> str | None:
        degraded.append(message)
        return None

    digest_of = application_pending.current_finding_digest(run.layout.bundle_dir)
    outcome = auto_merge.apply_auto_merges(
        root,
        run.layout,
        _plan(run.results),
        commit_section=run.section,
        autocommit=no_commit,
        judged_digests=run.judged,
        digest_of=digest_of,
    )
    assert len(degraded) == 1
    assert outcome.sha is None
    assert len(outcome.applied) == 1
    assert not (root / "bundle" / "concepts" / "foo-2.md").exists()


# gates 7-9, each the only failing fact ------------------------------------- #


def test_gate_7_a_member_edited_after_judging_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    run = _prepare_run(root, ("foo",))
    edited = root / "bundle" / "concepts" / "foo-2.md"
    edited.write_text(edited.read_text("utf-8") + "\nAdded after judging.\n", "utf-8")
    before = tree(root)

    outcome = run.apply()

    assert outcome.applied == ()
    assert outcome.skipped == (
        auto_merge.AutoMergeSkip(_family("concepts/foo").member_ids, _CHANGED),
    )
    assert tree(root) == before
    assert run.commits.calls == []


def test_gate_7_a_member_whose_content_cannot_be_pinned_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No digest at judging time AND none now are equal as values, but there is
    no evidence the judged bytes are the current bytes: fail closed."""
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    run = _prepare_run(root, ("foo",))
    run.judged = {member: None for member in run.judged}

    outcome = run.apply(digest_of=lambda member: None)

    assert [s.reason for s in outcome.skipped] == [_CHANGED]
    assert outcome.applied == ()


def test_gate_7_a_member_that_was_never_pinned_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    run = _prepare_run(root, ("foo",))
    del run.judged["concepts/foo"]

    outcome = run.apply()

    assert [s.reason for s in outcome.skipped] == [_CHANGED]
    assert outcome.applied == ()


def test_gate_7_an_edit_to_one_group_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo", "bar"))
    run = _prepare_run(root, ("foo", "bar"))
    edited = root / "bundle" / "concepts" / "foo.md"
    edited.write_text(edited.read_text("utf-8") + "\nEdited.\n", "utf-8")

    outcome = run.apply()

    assert [r.survivor for r in outcome.applied] == ["concepts/bar"]
    assert [s.member_ids for s in outcome.skipped] == [
        _family("concepts/foo").member_ids
    ]
    assert (root / "bundle" / "concepts" / "foo-2.md").exists()
    assert not (root / "bundle" / "concepts" / "bar-2.md").exists()
    assert len(run.commits.calls) == 1
    assert run.commits.calls[0][1].startswith(
        "openkos: auto-merge 1 pair(s) in the measured identity class"
    )


def test_gate_8_a_member_that_no_longer_resolves_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    run = _prepare_run(root, ("foo",))
    seen: list[bool] = []

    def unresolved(*args: object, **kwargs: object) -> None:
        seen.append(run.section.held)

    monkeypatch.setattr(lifecycle, "prepare_one_merge", unresolved)
    before = tree(root)

    outcome = run.apply()

    assert seen == [True]
    assert [s.reason for s in outcome.skipped] == ["a member no longer resolves"]
    assert tree(root) == before


def test_gate_8_a_plan_that_cannot_be_built_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    run = _prepare_run(root, ("foo",))
    seen: list[bool] = []

    def broken(*args: object, **kwargs: object) -> None:
        seen.append(True)
        raise ValueError("unreadable frontmatter")

    monkeypatch.setattr(lifecycle, "prepare_one_merge", broken)

    outcome = run.apply()

    assert seen == [True]
    assert [s.reason for s in outcome.skipped] == [
        "could not be prepared (unreadable frontmatter)"
    ]
    assert outcome.failure is None


def test_gate_9_a_plan_crossing_the_stacked_body_guardrail_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("bar",))
    write_concept(root, "concepts/foo", title="foo", body="Short.")
    write_concept(root, "concepts/foo-2", title="foo", body="A long body. " * 80)
    run = _prepare_run(root, ("foo",))
    before = tree(root)

    outcome = run.apply()

    assert outcome.applied == ()
    assert len(outcome.skipped) == 1
    assert outcome.skipped[0].reason.startswith("stacked-body guardrail")
    assert tree(root) == before
    assert run.commits.calls == []


def test_the_guardrail_check_is_the_shared_predicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo",))
    run = _prepare_run(root, ("foo",))
    seen: list[str] = []
    real = lifecycle.stacked_body_refused

    def spy(prepared: lifecycle.PreparedMerge) -> bool:
        seen.append(prepared.absorbed_canonical)
        return real(prepared)

    monkeypatch.setattr(lifecycle, "stacked_body_refused", spy)
    run.apply()
    assert seen == ["concepts/foo-2"]


def test_each_merge_is_re_planned_against_the_bundle_the_previous_one_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo", "bar"))
    # `other` links to BOTH absorbed members, so merge 1 rewrites a file merge 2
    # also reads
    write_concept(
        root,
        "concepts/other",
        title="other",
        body="See [a](/concepts/foo-2.md) and [b](/concepts/bar-2.md).",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "other")
    run = _prepare_run(root, ("foo", "bar"))

    # A plan built up front goes stale by construction: once merge 1 lands, the
    # baseline bytes merge 2 would be guarded by no longer match the disk.
    first = _prepare(root, run.results[0].candidate)
    second = _prepare(root, run.results[1].candidate)
    lifecycle.merge_core(
        run.layout.bundle_dir,
        run.layout.bundle_dir / "index.md",
        run.layout.bundle_dir / "log.md",
        first,
    )
    stale = [
        path
        for path, data in lifecycle.merge_drift_targets(run.layout, second).items()
        if not path.exists() or path.read_bytes() != data
    ]
    assert stale, "the up-front plan should have read-drifted"
    _git(root, "reset", "-q", "--hard")
    _git(root, "clean", "-q", "-fd")

    outcome = run.apply()

    assert [r.survivor for r in outcome.applied] == ["concepts/foo", "concepts/bar"]
    assert outcome.skipped == ()
    other = (root / "bundle" / "concepts" / "other.md").read_text(encoding="utf-8")
    assert "foo-2" not in other
    assert "bar-2" not in other
    assert "/concepts/foo.md" in other
    assert "/concepts/bar.md" in other


# mid-run failure and the restore (task 2.16) -------------------------------- #


class _Breaker:
    """`merge_core` that fails on its `fail_on`-th call after a partial write,
    and is the real one every other time."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        root: Path,
        *,
        fail_on: int,
        error: Exception,
        stage: str = "pending",
    ) -> None:
        self.root = root
        self.fail_on = fail_on
        self.error = error
        self.stage = stage
        self.calls: list[str] = []
        self.before_failure: dict[str, bytes] = {}
        self.failed = False
        self._real = lifecycle.merge_core
        monkeypatch.setattr(lifecycle, "merge_core", self)

    def __call__(
        self,
        bundle_dir: Path,
        index_path: Path,
        log_path: Path,
        prepared: lifecycle.PreparedMerge,
    ) -> lifecycle.MergeResult:
        self.calls.append(prepared.survivor_canonical)
        if len(self.calls) != self.fail_on:
            return self._real(bundle_dir, index_path, log_path, prepared)
        self.before_failure = tree(self.root)
        survivor = prepared.survivor_canonical
        fsio.write_atomic(index_path, "torn index\n")
        (bundle_dir / f"{survivor}.md").write_text("torn survivor\n", "utf-8")
        bundle_ledger.write_pending(
            survivor,
            bundle_dir,
            survivor_id=survivor,
            entries=prepared.plan.ledger_entries,
            expected_survivor_sha256="0" * 64,
        )
        if self.stage == "committed":
            bundle_ledger.commit_pending(survivor, bundle_dir)
        if self.stage == "unreadable":
            (bundle_dir / f"{survivor}.md").chmod(0)
        fsio.remove_file(bundle_dir / f"{prepared.absorbed_canonical}.md")
        self.failed = True
        raise self.error


@pytest.mark.parametrize("error", [OSError("disk full"), ValueError("bad plan")])
@pytest.mark.parametrize("stage", ["pending", "committed", "unreadable"])
def test_a_mid_run_failure_restores_that_merge_and_commits_the_landed_ones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    stage: str,
) -> None:
    if stage == "unreadable" and os.geteuid() == 0:
        pytest.skip("root reads a mode-0 file")
    root = _pass_workspace(tmp_path, monkeypatch)
    run = _prepare_run(root)
    breaker = _Breaker(monkeypatch, root, fail_on=2, error=error, stage=stage)

    outcome = run.apply()

    assert breaker.failed, "the patched merge_core was never reached"
    # merge 3 is never attempted, the tree is exactly what merge 1 left
    assert breaker.calls == ["concepts/foo", "concepts/bar"]
    assert tree(root) == breaker.before_failure
    assert [r.survivor for r in outcome.applied] == ["concepts/foo"]
    failure = outcome.failure
    assert failure is not None
    assert (failure.survivor, failure.absorbed) == ("concepts/bar", "concepts/bar-2")
    assert str(error) in failure.error
    assert failure.restored is True
    assert failure.unrestored_paths == ()
    # one commit, holding merge 1 only
    assert len(run.commits.calls) == 1
    assert run.commits.calls[0][1].startswith(
        "openkos: auto-merge 1 pair(s) in the measured identity class"
    )
    committed = _committed(root)
    assert "bundle/concepts/foo-2.md" in committed
    assert not any("bar" in path or "baz" in path for path in committed)
    assert outcome.sha is not None


def test_a_failure_on_the_first_merge_commits_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch)
    run = _prepare_run(root)
    breaker = _Breaker(monkeypatch, root, fail_on=1, error=OSError("disk full"))

    outcome = run.apply()

    assert breaker.failed
    assert breaker.calls == ["concepts/foo"]
    assert tree(root) == breaker.before_failure
    assert outcome.applied == ()
    assert outcome.sha is None
    assert run.commits.calls == []
    assert outcome.failure is not None
    assert outcome.failure.restored is True


def test_a_failing_restore_means_no_commit_and_names_every_modified_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch)
    run = _prepare_run(root)
    breaker = _Breaker(monkeypatch, root, fail_on=2, error=OSError("disk full"))
    real_write = fsio.write_atomic
    restore_attempts: list[Path] = []

    def write_atomic(path: Path, content: str) -> None:
        if breaker.failed:
            restore_attempts.append(path)
            raise OSError("read-only filesystem")
        real_write(path, content)

    monkeypatch.setattr(fsio, "write_atomic", write_atomic)

    outcome = run.apply()

    assert breaker.failed
    assert restore_attempts, "the restore never wrote"
    assert run.commits.calls == []
    assert outcome.sha is None
    # the merge that landed is reported, uncommitted
    assert [r.survivor for r in outcome.applied] == ["concepts/foo"]
    failure = outcome.failure
    assert failure is not None
    assert failure.restored is False
    assert set(failure.unrestored_paths) == {
        "bundle/index.md",
        "bundle/concepts/bar.md",
        "bundle/concepts/bar-2.md",
    }


class _BusySection:
    """A `CommitSection` whose lock is held by another process."""

    def __call__(self) -> _BusySection:
        return self

    def __enter__(self) -> None:
        raise lock.WorkspaceBusyError(lock.BUSY_REASON)

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_a_busy_workspace_stops_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch)
    run = _prepare_run(root)
    before = tree(root)

    def unreachable(*args: object, **kwargs: object) -> None:
        raise AssertionError("work started without the lock")

    monkeypatch.setattr(lifecycle, "prepare_one_merge", unreachable)
    digest_of = application_pending.current_finding_digest(run.layout.bundle_dir)

    with pytest.raises(lock.WorkspaceBusyError):
        auto_merge.apply_auto_merges(
            root,
            run.layout,
            _plan(run.results),
            commit_section=_BusySection(),
            autocommit=run.commits,
            judged_digests=run.judged,
            digest_of=digest_of,
        )

    assert tree(root) == before
    assert run.commits.calls == []


# unmerge parity with the real unmerge core (task 2.18) ---------------------- #


def _concept_bytes(root: Path, concept_id: str) -> bytes | None:
    path = root / "bundle" / f"{concept_id}.md"
    return path.read_bytes() if path.exists() else None


def _merge_bullet(root: Path, absorbed: str) -> str:
    lines = (root / "bundle" / "log.md").read_text("utf-8").splitlines()
    (bullet,) = [line for line in lines if absorbed in line]
    return bullet


@pytest.mark.parametrize("target", ["foo", "bar"])
def test_unmerge_undoes_one_auto_merge_and_leaves_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    other = "bar" if target == "foo" else "foo"
    root = _pass_workspace(tmp_path, monkeypatch, ("foo", "bar"))
    originals = {
        cid: _concept_bytes(root, cid)
        for cid in (
            f"concepts/{target}",
            f"concepts/{target}-2",
        )
    }
    run = _prepare_run(root, ("foo", "bar"))
    assert len(run.apply().applied) == 2
    other_survivor = _concept_bytes(root, f"concepts/{other}")
    target_bullet = _merge_bullet(root, f"concepts/{target}-2")
    other_bullet = _merge_bullet(root, f"concepts/{other}-2")

    unmerge_service.unmerge_concept(
        root,
        f"concepts/{target}",
        f"concepts/{target}-2",
        unmerge_service.UnmergePolicy(auto=True),
        ports=unmerge_service.UnmergePorts(autocommit=CommitRecorder()),
    )

    # byte parity for the pair that was undone
    for concept_id, data in originals.items():
        assert _concept_bytes(root, concept_id) == data
    # the other merge, its survivor and its bullet are untouched
    assert _concept_bytes(root, f"concepts/{other}") == other_survivor
    assert _concept_bytes(root, f"concepts/{other}-2") is None
    log_lines = (root / "bundle" / "log.md").read_text("utf-8").splitlines()
    assert target_bullet not in log_lines
    assert other_bullet in log_lines


# the survivor-edit check (task 2.20) ---------------------------------------- #


def _edit(root: Path, concept_id: str) -> None:
    path = root / "bundle" / f"{concept_id}.md"
    path.write_text(path.read_text("utf-8") + "\nEdited later.\n", "utf-8")


def test_survivors_edited_since_names_only_the_edited_survivors_in_record_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch)
    run = _prepare_run(root)
    records = run.apply().applied
    assert [r.survivor for r in records] == [
        "concepts/foo",
        "concepts/bar",
        "concepts/baz",
    ]
    assert auto_merge.survivors_edited_since(run.layout, records) == ()

    _edit(root, "concepts/baz")
    _edit(root, "concepts/foo")

    assert auto_merge.survivors_edited_since(run.layout, records) == (
        "concepts/foo",
        "concepts/baz",
    )


def test_a_missing_survivor_file_is_not_reported_as_edited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _pass_workspace(tmp_path, monkeypatch, ("foo", "bar"))
    run = _prepare_run(root, ("foo", "bar"))
    records = run.apply().applied
    (root / "bundle" / "concepts" / "foo.md").unlink()
    _edit(root, "concepts/bar")

    assert auto_merge.survivors_edited_since(run.layout, records) == ("concepts/bar",)


def test_no_records_means_nothing_edited(tmp_path: Path) -> None:
    layout = config.WorkspaceLayout(tmp_path)
    assert auto_merge.survivors_edited_since(layout, []) == ()


# --------------------------------------------------------------------------- #
# the accept-recommended selector (tasks 4.1-4.3)
# --------------------------------------------------------------------------- #


def _recommended(
    results: list[AdjudicatedCandidate], **overrides: Any
) -> auto_merge.AutoMergePlan:
    """The all-pass baseline with at most one deviation, as `_plan` builds it,
    plus an empty `excluded_survivors`."""
    kwargs: dict[str, Any] = {
        "fresh_keys": frozenset(_key(r.candidate) for r in results),
        "blocked": frozenset(),
        "cross_type_concern": lambda pair: None,
        "ordered_pair": functools.partial(lifecycle.ordered_merge_pair, Path("unused")),
        "excluded_survivors": frozenset(),
    }
    kwargs.update(overrides)
    return auto_merge.recommended(results, **kwargs)


def _pairs(plan: auto_merge.AutoMergePlan) -> list[tuple[str, str]]:
    return [(p.survivor, p.absorbed) for p in plan.planned]


def test_the_recommended_baseline_offers_one_merge_with_the_base_as_survivor() -> None:
    result = _verdict(_family())
    plan = _recommended([result])
    assert plan.planned == (
        auto_merge.PlannedAutoMerge(result, "concepts/foo", "concepts/foo-2"),
    )


def test_a_recommended_set_never_reports_skips() -> None:
    """A refused group simply keeps its per-item prompt; nothing prints a
    "not merged automatically" line for it."""
    plan = _recommended([_verdict(_family())], blocked=frozenset({"concepts/foo"}))
    assert plan.planned == ()
    assert plan.skipped == ()


@pytest.mark.parametrize("confidence", [0.60, 0.0, 0.8999])
def test_recommended_gate_3_is_removed_so_any_confidence_is_offered(
    confidence: float,
) -> None:
    plan = _recommended([_verdict(_family(), confidence=confidence)])
    assert _pairs(plan) == [("concepts/foo", "concepts/foo-2")]


def test_recommended_never_reads_t_star(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_merge, "T_STAR", 0.999)
    plan = _recommended([_verdict(_family(), confidence=0.60)])
    assert len(plan.planned) == 1


def test_a_verdict_not_judged_this_run_is_not_recommended() -> None:
    plan = _recommended([_verdict(_family(), confidence=1.0)], fresh_keys=frozenset())
    assert plan.planned == ()


@pytest.mark.parametrize("verdict", [Verdict.DIFFERENT, Verdict.UNCERTAIN])
def test_only_a_same_verdict_is_recommended(verdict: Verdict) -> None:
    assert _recommended([_verdict(_family(), verdict, 0.99)]).planned == ()


@pytest.mark.parametrize(
    "blocked_member", ["concepts/foo", "concepts/foo-2"], ids=["survivor", "absorbed"]
)
def test_a_blocked_member_is_not_recommended(blocked_member: str) -> None:
    plan = _recommended([_verdict(_family())], blocked=frozenset({blocked_member}))
    assert plan.planned == ()


def test_the_blocked_exclusion_is_one_argument_the_owner_can_revisit() -> None:
    """Q2: passing an empty `blocked` admits the same group, so reversing the
    conservative reading is a one-argument change."""
    result = _verdict(_family())
    blocked = frozenset({"concepts/foo-2"})
    assert _recommended([result], blocked=blocked).planned == ()
    assert len(_recommended([result], blocked=frozenset()).planned) == 1


def test_a_cross_type_concern_is_not_recommended() -> None:
    plan = _recommended(
        [_verdict(_family())], cross_type_concern=lambda pair: "types differ"
    )
    assert plan.planned == ()


def test_a_survivor_the_auto_pass_already_merged_is_not_recommended() -> None:
    result = _verdict(_group(("concepts/foo", "concepts/foo-3")))
    assert (
        _recommended([result], excluded_survivors=frozenset({"concepts/foo"})).planned
        == ()
    )
    assert (
        len(
            _recommended(
                [result], excluded_survivors=frozenset({"concepts/bar"})
            ).planned
        )
        == 1
    )


def test_two_groups_sharing_a_survivor_recommend_only_the_first() -> None:
    first = _verdict(_group(("concepts/foo", "concepts/foo-2")), confidence=0.6)
    second = _verdict(_group(("concepts/foo", "concepts/foo-3")), confidence=0.7)
    assert _pairs(_recommended([first, second])) == [("concepts/foo", "concepts/foo-2")]


@pytest.mark.parametrize(
    "group",
    [
        pytest.param(_group(("concepts/x", "concepts/x-2", "concepts/x-3")), id="3"),
        pytest.param(_group(("concepts/p", "concepts/q"), tier=Tier.LOW), id="low"),
        pytest.param(_group(("concepts/m", "concepts/n")), id="not a suffix family"),
        pytest.param(
            _group(
                ("people/ann", "people/ann-2"),
                "Person",
                member_types=("Person", "Person"),
            ),
            id="person",
        ),
    ],
)
def test_a_group_outside_the_class_is_not_recommended(group: CandidateGroup) -> None:
    assert _recommended([_verdict(group, confidence=0.99)]).planned == ()
