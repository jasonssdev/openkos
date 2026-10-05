"""The structural Identity auto-merge seam (#1298, ADR-0049): the class
predicate, the measured constants, and run eligibility.

Every guard below is tested from an all-valid baseline with exactly one
deviation, so mutating that one guard alone turns exactly its own test red.
No test reaches an LLM or a network: `list_models` is always a stub.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import auto_merge
from openkos.llm.base import BackendError, InstalledModel
from openkos.llm.prompts import prompt_hash
from openkos.resolution import adjudication
from openkos.resolution.candidates import CandidateGroup, Tier

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
