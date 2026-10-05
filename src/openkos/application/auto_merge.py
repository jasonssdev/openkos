"""Structural Identity auto-merge (#1298, ADR-0049): the one class of
Identity duplicates `openkos curate --auto-merge` may merge without a
per-item answer, and the measured evidence that admits it.

This module holds decision logic only, so it is testable without the CLI and
has no `typer`. It never imports `openkos.cli`, a concrete `openkos.llm.*`
client, or `yaml` (the application layering guard keeps that true); side
effects (the lock, the commit) arrive as injected callables.

The class is admitted by a pre-registered measurement
(`evals/auto_merge/PREREGISTRATION-1298.md`, verdict
`evals/auto_merge/results/auto-merge-verdict-1298-20261005T181724Z-gemma4-26b-a4b.md`).
Every constant below is a fixed engine constant, never a setting: nothing in
`openkos.yaml`, the environment or a flag changes one (ADR-0034 decision 3).
Changing any of them requires re-running the harness.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from openkos import config, fsio, sensitivity
from openkos.application import lifecycle
from openkos.application.ingest import ATTACH_EXCLUDED_TYPES
from openkos.application.lock_wait import CommitSection
from openkos.application.merge_service import merge_commit_paths
from openkos.bundle import ledger as bundle_ledger
from openkos.llm.base import BackendError, InstalledModel
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict, rubric_digest
from openkos.resolution.candidates import CandidateGroup, Tier
from openkos.resolution.normalize import is_suffix_family
from openkos.state import adjudications as adjudications_store

MEASURED_MODEL: Final = "gemma4:26b-a4b"
"""The adjudication model the class was measured with: the `model` of both
committed structural run files and their `stamp.model.name`."""

MEASURED_MODEL_DIGEST: Final = (
    "001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c"
)
"""That model's full content digest: `stamp.model.digest` in both
`runs-structural-calibration-20261005T164453Z-gemma4-26b-a4b.json` and
`runs-structural-confirmation-20261005T170308Z-gemma4-26b-a4b.json`. A tag
can be re-pointed at different weights; the digest cannot."""

T_STAR: Final = 0.90
"""The confidence threshold `t*`: the verdict file's `t*: 0.9000`, derived by
the frozen #1054 rule on the in-class calibration arm."""

MEASURED_AT_COMMIT: Final = "63b5f551541f7e0e98c939331898f747e23f38da"
"""The tree the measurement ran on: `stamp.harness.commit` (`dirty: false`)
in both arms."""

MEASURED_PROMPT_SHA256_16: Final = "aaed5c3e06569c83"
"""The adjudication system prompt's hash: `stamp.prompts[adjudication/system]
.sha256_16` in both arms."""

MEASURED_RUBRIC_DIGEST: Final = (
    "sha256:72d7c7cb794a6ee81478d3c51cfaf88c9067f390bcc15d62759f299885ad3483"
)
"""`rubric_digest()` evaluated on the measured tree (`63b5f551`). It covers
the prompt AND the deterministic withdrawal rule's data, because the measured
verdicts passed through that rule; the stamp's 16-hex prompt hash alone would
leave a marker edit looking eligible. Verified equal at the measured commit
and at the commit that introduced this constant."""

MEASURED_CONTEXT_WINDOW: Final = 12288
"""`settings.context_window` of both run files."""

MEASURED_MAX_GENERATION_TOKENS: Final = 8192
"""`settings.max_generation_tokens` of both run files."""


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


def _short(digest: str) -> str:
    """A digest as its first 12 hex characters, whatever prefix it carries."""
    return digest.removeprefix("sha256:")[:12]


def static_ineligibility(cfg: config.Config) -> tuple[str, ...]:
    """Every failing run-eligibility fact that needs no I/O, in a fixed
    order: the resolved adjudication model, `context_window`,
    `max_generation_tokens`, the sampling pins, and the rubric identity.

    `chat_client` forwards each setting verbatim, so the config value is the
    value the adjudication call would be sent with. An explicit key equal to
    the measured value is therefore eligible; `None` or another value is not.
    The measurement ran unpinned, so a pinned `temperature` or `seed` moves
    the verdict distribution `T_STAR` was fitted on and is ineligible too.
    """
    reasons: list[str] = []
    tag = config.resolve_task_model(cfg, "adjudication")
    if tag != MEASURED_MODEL:
        reasons.append(
            f"adjudication model is {tag!r}, the measured model is {MEASURED_MODEL!r}"
        )
    if cfg.context_window != MEASURED_CONTEXT_WINDOW:
        reasons.append(
            f"context_window is {cfg.context_window}, "
            f"the measured value is {MEASURED_CONTEXT_WINDOW}"
        )
    if cfg.max_generation_tokens != MEASURED_MAX_GENERATION_TOKENS:
        reasons.append(
            f"max_generation_tokens is {cfg.max_generation_tokens}, "
            f"the measured value is {MEASURED_MAX_GENERATION_TOKENS}"
        )
    if cfg.temperature is not None:
        reasons.append(
            f"temperature is pinned to {cfg.temperature}, the measured run pinned none"
        )
    if cfg.seed is not None:
        reasons.append(f"seed is pinned to {cfg.seed}, the measured run pinned none")
    observed_rubric = rubric_digest()
    if observed_rubric != MEASURED_RUBRIC_DIGEST:
        reasons.append(
            f"rubric identity is sha256:{_short(observed_rubric)}, "
            f"the measured identity is sha256:{_short(MEASURED_RUBRIC_DIGEST)}"
        )
    return tuple(reasons)


def model_digest_ineligibility(
    list_models: Callable[[], list[InstalledModel]], tag: str
) -> str | None:
    """Why the installed `tag` is not the measured model, or `None` when its
    digest equals `MEASURED_MODEL_DIGEST` exactly.

    One backend listing, no model call. A listing failure, an unlisted tag, a
    listed tag without a digest (an `openai-compatible` backend always) and a
    different digest are each their own reason; digests are shown as their
    first 12 hex characters."""
    try:
        installed = list_models()
    except BackendError as exc:
        return f"could not list installed models ({exc})"
    entry = next((m for m in installed if m.tag == tag), None)
    if entry is None:
        return f"model {tag!r} is not listed by the backend"
    if entry.digest is None:
        return f"the backend reports no digest for {tag!r} (digest unknown)"
    if entry.digest != MEASURED_MODEL_DIGEST:
        return (
            f"installed digest {_short(entry.digest)} differs from the measured "
            f"digest {_short(MEASURED_MODEL_DIGEST)}"
        )
    return None


@dataclass(frozen=True)
class RunEligibility:
    """Whether the structural class may act this run, with every reason it may
    not. Eligible if and only if there is no reason."""

    reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.reasons


def run_eligibility(
    cfg: config.Config, list_models: Callable[[], list[InstalledModel]]
) -> RunEligibility:
    """Static checks first, the digest last. The backend is listed (one read)
    only when every static check passed, so a run that is already ineligible
    pays no network read."""
    static = static_ineligibility(cfg)
    if static:
        return RunEligibility(static)
    reason = model_digest_ineligibility(
        list_models, config.resolve_task_model(cfg, "adjudication")
    )
    return RunEligibility(() if reason is None else (reason,))


def _group_key(group: CandidateGroup) -> str:
    return adjudications_store.group_key_for(group.member_ids)


def judging_partition(
    groups: Sequence[CandidateGroup],
    served_by_key: Mapping[str, AdjudicatedCandidate],
    to_judge: Sequence[CandidateGroup],
    *,
    auto_merge: bool,
    interactive: bool,
    statically_eligible: bool,
) -> tuple[dict[str, AdjudicatedCandidate], list[CandidateGroup]]:
    """Which groups are judged fresh this run, and which keep a served verdict.

    The auto pass acts only on a verdict this run's model produced, never on a
    cached or queued one (neither records the model that produced it). So when
    `auto_merge` is on and the run is statically eligible, every in-class group
    is judged fresh whatever the store holds.

    * flag off, or not statically eligible: the inputs, unchanged (today's serve).
    * interactive: each in-class group leaves `served_by_key` and joins
      `to_judge` (once); every other group is untouched.
    * non-interactive: `to_judge` is exactly the in-class groups and nothing is
      served, so a group outside the class is neither judged nor walked, as a
      non-TTY Identity stage that spends nothing never did.

    The probe and the run both call this, so the cost line prices what the run
    pays."""
    if not auto_merge or not statically_eligible:
        return dict(served_by_key), list(to_judge)
    in_class = [group for group in groups if in_structural_class(group)]
    if not interactive:
        return {}, in_class
    in_class_keys = {_group_key(group) for group in in_class}
    served = {k: v for k, v in served_by_key.items() if k not in in_class_keys}
    judging = list(to_judge)
    queued = {_group_key(group) for group in judging}
    judging.extend(group for group in in_class if _group_key(group) not in queued)
    return served, judging


def strict_blocked_members(bundle_dir: Path) -> frozenset[str]:
    """Every concept id that is confidential or cannot be sent to a model,
    under the strictest reading: an unreadable, unparseable, absent or blank
    `sensitivity` blocks, exactly like `confidential`.

    Both escape hatches the run's chat seams honour (`include_confidential`,
    `local_exemption`) are deliberately passed OFF: a human opting into
    confidential content for a model call is not consent to merge it without
    a per-item answer. One walk, shared by the auto pass and the
    accept-recommended selector so they use one predicate."""
    return sensitivity.sensitive_concept_ids(
        bundle_dir, include_confidential=False, local_exemption=False
    )


@dataclass(frozen=True)
class PlannedAutoMerge:
    """One group that passed every per-group gate: merge `absorbed` into
    `survivor`."""

    result: AdjudicatedCandidate
    survivor: str
    absorbed: str


@dataclass(frozen=True)
class AutoMergeSkip:
    """An in-class SAME group the pass did not merge, and the first gate that
    refused it."""

    member_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class AutoMergePlan:
    planned: tuple[PlannedAutoMerge, ...]
    skipped: tuple[AutoMergeSkip, ...]


def plan_auto_merges(
    results: Sequence[AdjudicatedCandidate],
    *,
    fresh_keys: frozenset[str],
    blocked: frozenset[str],
    cross_type_concern: Callable[[tuple[str, str]], str | None],
    ordered_pair: Callable[[tuple[str, ...]], tuple[str, str, str]],
) -> AutoMergePlan:
    """The per-group gates, pure over injected facts: which in-class groups
    merge this run, and why each of the others does not.

    A group outside the class never enters the pass (it is not "skipped"), and
    neither does a verdict other than SAME (it is simply not a merge). Each
    remaining group must pass, in order: a fresh verdict from this run's
    batch; `confidence >= T_STAR`; no confidential or LLM-blocked member; no
    cross-type concern; and a survivor no earlier group of this run already
    claimed. The first failing gate is the reported reason. Gates that need
    the bundle as the previous merge left it (the judged-content check, the
    re-plan, the stacked-body guardrail) run later, under the lock."""
    planned: list[PlannedAutoMerge] = []
    skipped: list[AutoMergeSkip] = []
    claimed: set[str] = set()
    for result in results:
        group = result.candidate
        if not in_structural_class(group) or result.verdict is not Verdict.SAME:
            continue
        reason = _first_refusal(
            result,
            fresh_keys=fresh_keys,
            blocked=blocked,
            cross_type_concern=cross_type_concern,
            ordered_pair=ordered_pair,
            claimed=claimed,
            min_confidence=T_STAR,
        )
        if isinstance(reason, str):
            skipped.append(AutoMergeSkip(group.member_ids, reason))
            continue
        survivor, absorbed = reason
        claimed.add(survivor)
        planned.append(PlannedAutoMerge(result, survivor, absorbed))
    return AutoMergePlan(tuple(planned), tuple(skipped))


def _first_refusal(
    result: AdjudicatedCandidate,
    *,
    fresh_keys: frozenset[str],
    blocked: frozenset[str],
    cross_type_concern: Callable[[tuple[str, str]], str | None],
    ordered_pair: Callable[[tuple[str, ...]], tuple[str, str, str]],
    claimed: set[str],
    min_confidence: float | None,
) -> str | tuple[str, str]:
    """The reason the first failing gate gives, or `(survivor, absorbed)` when
    every per-group gate passes. `min_confidence=None` drops the confidence
    gate: a person confirms those groups, so the threshold that licenses
    acting without one does not apply."""
    group = result.candidate
    if _group_key(group) not in fresh_keys:
        return "no fresh verdict this run"
    if min_confidence is not None and result.confidence < min_confidence:
        return f"confidence {result.confidence} below {min_confidence:.2f}"
    survivor, absorbed, _criterion = ordered_pair(group.member_ids)
    if blocked.intersection((survivor, absorbed)):
        return "a member is confidential or cannot be sent to the model"
    concern = cross_type_concern((survivor, absorbed))
    if concern is not None:
        return concern
    if survivor in claimed:
        return "survivor already merged this run"
    return survivor, absorbed


def recommended(
    results: Sequence[AdjudicatedCandidate],
    *,
    fresh_keys: frozenset[str],
    blocked: frozenset[str],
    cross_type_concern: Callable[[tuple[str, str]], str | None],
    ordered_pair: Callable[[tuple[str, ...]], tuple[str, str, str]],
    excluded_survivors: frozenset[str],
) -> AutoMergePlan:
    """The groups Identity's accept-recommended question may offer: the
    planner's gates with the confidence gate removed, because a person
    confirms the batch.

    In class, a fresh SAME verdict from this run's model, no blocked member,
    no cross-type concern, and one group per survivor (a survivor the
    automatic pass already merged counts as taken). A group that fails any
    gate is simply absent, so it keeps its per-item prompt; none is reported.
    Pass `blocked=frozenset()` to admit confidential members instead (the
    owner-revisable reading)."""
    planned: list[PlannedAutoMerge] = []
    claimed = set(excluded_survivors)
    for result in results:
        group = result.candidate
        if not in_structural_class(group) or result.verdict is not Verdict.SAME:
            continue
        verdict = _first_refusal(
            result,
            fresh_keys=fresh_keys,
            blocked=blocked,
            cross_type_concern=cross_type_concern,
            ordered_pair=ordered_pair,
            claimed=claimed,
            min_confidence=None,
        )
        if isinstance(verdict, str):
            continue
        survivor, absorbed = verdict
        claimed.add(survivor)
        planned.append(PlannedAutoMerge(result, survivor, absorbed))
    return AutoMergePlan(tuple(planned), ())


@dataclass(frozen=True)
class AutoMergeRecord:
    """One merge the pass wrote. `survivor_after_sha256` is the hash of the
    survivor exactly as written, the value `unmerge` compares against to tell
    whether the survivor was edited since (#1110)."""

    survivor: str
    absorbed: str
    confidence: float
    survivor_after_sha256: str


@dataclass(frozen=True)
class AutoMergeFailure:
    """The merge that raised mid-run, and whether the tree was put back."""

    survivor: str
    absorbed: str
    error: str
    restored: bool
    unrestored_paths: tuple[str, ...]


@dataclass(frozen=True)
class AutoMergeOutcome:
    applied: tuple[AutoMergeRecord, ...]
    skipped: tuple[AutoMergeSkip, ...]
    """Groups refused in the commit phase (judged-content check, an
    unresolvable member, the stacked-body guardrail). The planner's own
    skips are in `AutoMergePlan.skipped`."""
    sha: str | None
    failure: AutoMergeFailure | None


def apply_auto_merges(
    root: Path,
    layout: config.WorkspaceLayout,
    plan: AutoMergePlan,
    *,
    commit_section: CommitSection,
    autocommit: Callable[[Path, Sequence[str], str], str | None],
    judged_digests: Mapping[str, str | None],
    digest_of: Callable[[str], str | None],
) -> AutoMergeOutcome:
    """Write every planned merge under ONE commit phase, then commit once.

    The lock is held from the first re-validation to the autocommit, so no
    other `openkos` writer can take it between two merges and sweep this run's
    uncommitted `index.md`/`log.md` bullets into its own commit. Each merge is
    re-planned INSIDE the lock against the bundle the previous merge left
    (every merge changes what the next one reads, so a plan made up front
    would read-drift from the second merge on). The re-plan is model-free and
    prompt-free, the two things ADR-0036 keeps out of the lock.

    Per merge: the members' content must still be what was judged (gate 7);
    the pair must still resolve (gate 8); the stacked body must not cross the
    guardrail (gate 9). A refused merge is skipped and later ones go on. No
    reconciliation runs: the stacked body is what lands, so `unmerge`
    restores byte parity."""
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    applied: list[AutoMergeRecord] = []
    skipped: list[AutoMergeSkip] = []
    union: dict[str, None] = {}
    failure: AutoMergeFailure | None = None
    with commit_section():
        for planned in plan.planned:
            group = planned.result.candidate
            if any(
                judged_digests.get(member) is None
                or digest_of(member) != judged_digests.get(member)
                for member in group.member_ids
            ):
                skipped.append(AutoMergeSkip(group.member_ids, _CHANGED))
                continue
            try:
                prepared = lifecycle.prepare_one_merge(
                    root,
                    layout,
                    index_path,
                    log_path,
                    group,
                    ordered_pair=(planned.survivor, planned.absorbed),
                )
            except (OSError, ValueError) as exc:
                skipped.append(
                    AutoMergeSkip(group.member_ids, f"could not be prepared ({exc})")
                )
                continue
            if prepared is None:
                skipped.append(
                    AutoMergeSkip(group.member_ids, "a member no longer resolves")
                )
                continue
            if lifecycle.stacked_body_refused(prepared):
                skipped.append(
                    AutoMergeSkip(group.member_ids, guardrail_reason(prepared))
                )
                continue
            snapshot = _snapshot(layout, prepared)
            try:
                result = lifecycle.merge_core(
                    layout.bundle_dir, index_path, log_path, prepared
                )
            except (OSError, ValueError) as exc:
                unrestored = _restore(root, snapshot)
                failure = AutoMergeFailure(
                    planned.survivor,
                    planned.absorbed,
                    f"{type(exc).__name__}: {exc}",
                    restored=not unrestored,
                    unrestored_paths=unrestored,
                )
                break
            union.update(dict.fromkeys(merge_commit_paths(prepared, result)))
            applied.append(
                AutoMergeRecord(
                    planned.survivor,
                    planned.absorbed,
                    planned.result.confidence,
                    bundle_ledger.survivor_sha256(prepared.plan.merged_survivor),
                )
            )
        # A half-restored tree is never committed: a dirty tree the user can
        # see is better than a commit that records half a merge.
        sha = None
        if applied and (failure is None or failure.restored):
            sha = autocommit(root, list(union), _commit_message(applied))
    return AutoMergeOutcome(tuple(applied), tuple(skipped), sha, failure)


_CHANGED: Final = "changed since it was judged"


def guardrail_reason(prepared: lifecycle.PreparedMerge) -> str:
    """Why a bulk consent (the pass, accept-recommended) must not cover
    `prepared`: its stacked body is mostly the absorbed document."""
    share = prepared.stacked_body.share if prepared.stacked_body is not None else 0.0
    return (
        f"stacked-body guardrail: the absorbed body would be {share:.0%} of the "
        "merged body"
    )


def _commit_message(applied: Sequence[AutoMergeRecord]) -> str:
    lines = "\n".join(f"merge {r.absorbed} into {r.survivor}" for r in applied)
    return (
        f"openkos: auto-merge {len(applied)} pair(s) in the measured identity "
        f"class\n\n{lines}"
    )


def _snapshot(
    layout: config.WorkspaceLayout, prepared: lifecycle.PreparedMerge
) -> dict[Path, bytes | None]:
    """The pre-merge bytes of every path `merge_core` writes or unlinks, or
    `None` for a path that does not exist yet.

    `merge_drift_targets` covers `index.md`, `log.md`, every touched file, the
    survivor and the absorbed file (which is unlinked). `merge_core` also
    writes the survivor's ledger sidecar and, on the way to it, its pending
    marker; neither is a drift target, so both are added here."""
    snapshot: dict[Path, bytes | None] = dict(
        lifecycle.merge_drift_targets(layout, prepared, include_catalog=True)
    )
    for path in (
        bundle_ledger.ledger_path_for(prepared.survivor_canonical, layout.bundle_dir),
        bundle_ledger.pending_path_for(prepared.survivor_canonical, layout.bundle_dir),
    ):
        try:
            snapshot[path] = path.read_bytes()
        except FileNotFoundError:
            snapshot[path] = None
    return snapshot


def _restore(root: Path, snapshot: Mapping[Path, bytes | None]) -> tuple[str, ...]:
    """Put every path the failed merge may have touched back to its snapshot.
    Returns the workspace-relative paths that are STILL modified (empty when
    the tree is exactly what it was)."""
    unrestored: list[str] = []
    for path, original in snapshot.items():
        try:
            current = path.read_bytes() if path.exists() else None
        except OSError:
            current = b"\0 unreadable"
        if current == original:
            continue
        try:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                fsio.write_atomic(path, original.decode("utf-8"))
        except (OSError, ValueError):
            unrestored.append(path.relative_to(root).as_posix())
    return tuple(unrestored)


def survivors_edited_since(
    layout: config.WorkspaceLayout, records: Sequence[AutoMergeRecord]
) -> tuple[str, ...]:
    """The survivors whose current bytes no longer match what their merge
    wrote, in record order: exactly the ones `unmerge` will refuse (#1110)
    unless run with `--discard-survivor-edits`.

    Read the way `unmerge` reads, so the two never disagree. A survivor whose
    file is gone or unreadable is not reported: there is nothing to warn
    about an edit that cannot be seen."""
    edited: list[str] = []
    for record in records:
        try:
            _data, text = fsio.snapshot_read(
                layout.bundle_dir / f"{record.survivor}.md"
            )
        except (OSError, ValueError):
            continue
        if bundle_ledger.survivor_sha256(text) != record.survivor_after_sha256:
            edited.append(record.survivor)
    return tuple(edited)
