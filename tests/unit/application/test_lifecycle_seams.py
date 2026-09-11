"""Structural seam guards for the lifecycle application service (issue
#918, design D2 and "The injection seam, and how a silent no-op is
caught").

Two independent guarantees live here, deliberately kept apart from the
behavioral tests in `test_lifecycle.py`:

* D2 -- neither `ConfirmationRequest` variant declares a `granted`,
  `force`, or `override` field anywhere in the union, so a hard refusal
  gate can never be represented as "a request that was granted" by
  construction. A later field addition that would quietly re-open this is
  caught here, by name, rather than by re-reading the diff.
* The mechanical no-op guarantee (design, "The injection seam"): once
  `prepare_merge`/`merge_core` relocate into `application.lifecycle`,
  `openkos.cli.main` must no longer carry them as module attributes at
  all -- a stale `monkeypatch.setattr("openkos.cli.main.prepare_merge",
  ...)` must raise `AttributeError` under pytest's default `raising=True`,
  not silently no-op. Task 6.2 (Slice S2b) extends this to
  `_execute_single_unmerge`: S2a left it as a thin wrapper, so its
  deletion is what completes unmerge's Phase A/B split.
"""

import dataclasses
import re
from pathlib import Path

from openkos.application import consent as consent_service
from openkos.application import lifecycle as application_lifecycle
from openkos.cli import main as cli_main

_FORBIDDEN_FIELD_NAMES = {"granted", "force", "override"}


def test_confirmation_request_variants_carry_no_grant_field() -> None:
    """D2: neither variant of `ConfirmationRequest` may ever carry a
    `granted`, `force`, or `override` field -- the request is the
    question; the adapter's answer is a value it holds separately."""
    for cls in (
        consent_service.BooleanConfirmation,
        consent_service.TypedChallengeConfirmation,
    ):
        field_names = {f.name for f in dataclasses.fields(cls)}
        offending = field_names & _FORBIDDEN_FIELD_NAMES
        assert not offending, (
            f"{cls.__name__} must not declare {offending} (D2) -- a hard "
            "refusal gate must never be representable as a granted request"
        )


def test_prepare_merge_and_merge_core_no_longer_live_on_cli_main() -> None:
    """The mechanical no-op guarantee (design: "The injection seam, and how
    a silent no-op is caught"): `prepare_merge`/`merge_core` relocated into
    `application.lifecycle` and are deliberately NEVER aliased back, so
    `openkos.cli.main` must not carry them as module attributes at all. A
    stale `monkeypatch.setattr("openkos.cli.main.prepare_merge"/
    "merge_core", ...)` then raises `AttributeError` under pytest's default
    `raising=True`, instead of silently patching a name nothing reads."""
    assert not hasattr(cli_main, "prepare_merge")
    assert not hasattr(cli_main, "merge_core")


def test_execute_single_unmerge_no_longer_lives_on_cli_main() -> None:
    """Task 6.2 (Slice S2b): S2a left `_execute_single_unmerge` as a thin
    wrapper around Phase A/preview/gate/guard, still calling out to
    `application.lifecycle.unmerge_core` for the write only. S2b's
    `prepare_unmerge` completes the split, so this name must be deleted
    from `cli/main.py` entirely -- `unmerge` calls
    `application_lifecycle.prepare_unmerge`/`unmerge_core` directly,
    exactly as `merge` calls `prepare_merge`/`merge_core` (design's Slice
    Plan, "unmerge matches merge's public prepare/core pair")."""
    assert not hasattr(cli_main, "_execute_single_unmerge")


def test_purge_confirm_phrase_and_decisions_history_targets_no_longer_live_on_cli_main() -> (
    None
):
    """S4 (issue #918): `purge_confirm_phrase` (was `_purge_confirm_phrase`)
    and `_decisions_history_targets` relocated into `application.lifecycle`
    and are never aliased back, so `openkos.cli.main` must not carry either
    as a module attribute -- a stale
    `monkeypatch.setattr("openkos.cli.main._purge_confirm_phrase", ...)`
    then raises `AttributeError` under pytest's default `raising=True`."""
    assert not hasattr(cli_main, "_purge_confirm_phrase")
    assert not hasattr(cli_main, "_decisions_history_targets")
    assert not hasattr(cli_main, "_purge_dropped_store_notice")
    assert not hasattr(cli_main, "_purge_residual_store_notice")


def test_918_slice5_helpers_no_longer_live_on_cli_main() -> None:
    """Issue #955: `_canonicalize_concept_id`/`_resolve_concept_path`/
    `_merge_drift_targets`/`_member_body_length`/`_ordered_merge_pair`/
    `_cross_source_same_pair`/`_cross_type_concern`/`_prepare_one_merge`/
    `_reconcile_planned` relocated into `application.lifecycle` by issue
    #918 Slice 5, but were briefly aliased back onto `openkos.cli.main`
    under their original private names -- exactly the "injection seam"
    hazard `test_prepare_merge_and_merge_core_no_longer_live_on_cli_main`
    above already guards against for `prepare_merge`/`merge_core`.

    The hazard here was measured, not hypothetical:
    `application.lifecycle.preview_apply_same` calls `ordered_merge_pair`,
    `cross_source_same_pair`, `cross_type_concern`, `prepare_one_merge`,
    and `resolve_concept_path` by module-local name, and
    `ordered_merge_pair` calls `member_body_length` the same way. A
    `monkeypatch.setattr("openkos.cli.main._prepare_one_merge", ...)`
    patches only `cli.main`'s call sites -- it is a silent no-op for that
    service-internal walk, so the patched test and the real code path
    diverge while the test still reports green. Deleting the aliases
    turns that same stale patch into an `AttributeError` under pytest's
    default `raising=True`, instead of a silent divergence.

    What this guard does NOT observe: whether every former in-module call
    site was rewritten. A surviving reference to a deleted alias in a
    rarely executed branch of `cli/main.py` raises `NameError` only when
    that branch runs -- it is not a failure here. The call-site sweep is
    proved by the suite that exercises those branches, not by this
    assertion."""
    for name in (
        "_canonicalize_concept_id",
        "_resolve_concept_path",
        "_merge_drift_targets",
        "_member_body_length",
        "_ordered_merge_pair",
        "_cross_source_same_pair",
        "_cross_type_concern",
        "_prepare_one_merge",
        "_reconcile_planned",
    ):
        assert not hasattr(cli_main, name)


def test_forget_plan_gate_one_counts_never_become_confirmation_request_fields() -> None:
    """D2/R3 (task 8.2): `ForgetPlan.surviving_refs`/`unverifiable_refs` are
    Gate 1's hard-refusal inputs -- `forget`'s inbound-reference guard,
    bypassed only by `--force`, never by any answer to a confirmation. They
    must stay structurally unreachable from `ConfirmationRequest.matches()`:
    neither `ConfirmationRequest` variant declares either name as a field
    (so a renderer that matched on `kind` could never read them off a
    `ConfirmationRequest`), and `forget`'s own `confirmation` is a
    `BooleanConfirmation`, the variant with no `matches()` method at all --
    Gate 1's counts have no method that could ever consult them in the
    first place."""
    forget_plan_fields = {
        f.name for f in dataclasses.fields(application_lifecycle.ForgetPlan)
    }
    assert {"surviving_refs", "unverifiable_refs"} <= forget_plan_fields

    for cls in (
        consent_service.BooleanConfirmation,
        consent_service.TypedChallengeConfirmation,
    ):
        confirmation_fields = {f.name for f in dataclasses.fields(cls)}
        offending = confirmation_fields & {"surviving_refs", "unverifiable_refs"}
        assert not offending, (
            f"{cls.__name__} must not declare {offending} -- Gate 1 must stay "
            "outside the ConfirmationRequest union (D2/R3)"
        )
    assert not hasattr(consent_service.BooleanConfirmation, "matches")


def test_batch_apply_preview_confirmation_carries_no_grant_field() -> None:
    """D2 (task 12.4, Slice S5): `BatchApplyPreview.confirmation` is a real
    `TypedChallengeConfirmation` -- the SAME union member every other typed
    gate uses, not a bespoke field of its own that could smuggle back a
    `granted`/`force`/`override` shape."""
    confirmation = consent_service.TypedChallengeConfirmation(
        prompt="Type the eligible count (0) to proceed",
        expected="0",
        supplying_flag="--confirm-count",
        non_tty_refusal="refusing to apply -- stdin is not a TTY.",
        mismatch_abort="aborted -- confirmation count did not match exactly.",
        match_mode="strip-then-exact",
    )
    preview = application_lifecycle.BatchApplyPreview(
        skips=(), items=(), previewed=(), confirmation=confirmation
    )

    assert isinstance(preview.confirmation, consent_service.TypedChallengeConfirmation)
    field_names = {f.name for f in dataclasses.fields(type(preview.confirmation))}
    assert not field_names & _FORBIDDEN_FIELD_NAMES


def test_adjudicate_apply_same_repoint_count_unchanged_beyond_s1() -> None:
    """Task 12.4: `preview_apply_same`/`prepare_one_merge`/
    `ordered_merge_pair`/`reconcile_planned` introduce NO new
    `"openkos.cli.main.X"` patch target naming a RELOCATED symbol in
    `test_adjudicate.py`.

    Asserts the SET of patched names, not their count. A count is the wrong
    predicate here: it moves whenever a legitimate new test patches one of
    the untouched discovery-half collaborators, and it would equally stay
    put if one relocated name were swapped for another. What the design's
    "The injection seam" section actually promises is that only the four
    S1 sites (`merge_core` x2, `prepare_merge` x2) ever left, and that the
    surviving targets are exactly the names this change never moved
    (design C3)."""
    test_file = (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "unit"
        / "cli"
        / "test_adjudicate.py"
    )
    text = test_file.read_text(encoding="utf-8")
    patched = set(re.findall(r'"openkos\.cli\.main\.([A-Za-z_]+)"', text))
    assert patched == {
        "adjudicate_candidates",
        "find_candidates_report",
        "_reconcile_merged_survivor",
        "OllamaClient",
    }
    # And every symbol this change relocated is gone from that set.
    for relocated in ("prepare_merge", "merge_core", "prepare_one_merge"):
        assert relocated not in patched
