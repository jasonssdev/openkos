"""The `duplicates` use case, as an application service (ADR-0018, issue
#1168; the findings slice after `ingest_service` and `reindex_service`).

Three operations over the workspace at an explicit `root`, each returning a
typed outcome and raising typed `DuplicatesRefused` subclasses -- none prompts,
renders, reads the current directory, commits, or raises `typer.Exit`. The CLI
`duplicates` verb is one adapter over them; MVP 4's scheduled maintenance is
meant to be another.

* `report_duplicates` -- the cross-source candidate report, with every group a
  human ruled distinct (#797) already removed and counted;
* `record_identity_ruling` -- the `--keep-distinct` / `--reopen` writes: an
  identity ruling persisted under `bundle/.state/decisions/**`, never a
  concept file, never `.openkos/findings.db`;
* `list_kept_distinct` -- the `--kept-distinct` listing.

What it does NOT own, and how it reaches each through a parameter instead (the
layering invariant forbids importing `openkos.cli`, `typer`, `rich` or
`openkos.vcs` here):

* every word the user reads -- the caller renders the returned data, and the
  notes the `bundle` readers/writers produce arrive through an `on_warning`
  callback the caller decides how to show;
* the auto-commit of a ruling -- the outcome names the workspace-relative path
  and the caller stages it, because git is an adapter concern;
* the whole-bundle candidate pass -- `find_candidates_report` is a parameter,
  so it stays a substitutable seam.

Nothing here is an opinion about whether two concepts ARE the same entity: a
ruling records the human's answer and the report honours it, which is the
"human curates, engine maintains" principle in its smallest form.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from openkos import config
from openkos.application import lifecycle as application_lifecycle
from openkos.application import pending as application_pending
from openkos.bundle import decisions as bundle_decisions
from openkos.resolution.candidates import (
    CandidateGroup,
    CandidateGroupReport,
    candidate_group_truncation_notice,
)

_VERB = "duplicates"

Warn = Callable[[str], None]
"""Receives one note a `bundle` reader/writer produced; the caller decides how
it is shown."""


# -- Typed refusals ---------------------------------------------------------


class DuplicatesRefused(Exception):
    """Base of every refusal the service raises. `message` is the complete,
    user-facing text (the wording the CLI has always printed), so an adapter
    renders it verbatim and maps the TYPE to an exit code."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotAWorkspace(DuplicatesRefused):
    """`root` is not an OpenKOS workspace."""


class InvalidMembers(DuplicatesRefused):
    """The operator-supplied member ids cannot name an identity ruling: one is
    unsafe as a concept id, or fewer than two distinct ids remain."""


# -- Outcomes ---------------------------------------------------------------


@dataclass(frozen=True)
class DuplicatesReport:
    """One report run: the groups still offered, how many the human's rulings
    hid, and the cap's truncation notice when it bound."""

    groups: tuple[CandidateGroup, ...]
    suppressed: int
    truncation_notice: str | None


@dataclass(frozen=True)
class IdentityRuling:
    """A ruling that was written: its canonical members and the
    workspace-relative path of the sidecar, for the caller's auto-commit."""

    members: tuple[str, ...]
    rel_path: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_workspace(root: Path) -> config.WorkspaceLayout:
    reason = config.require_workspace(root)
    if reason is not None:
        raise NotAWorkspace(f"openkos {_VERB}: refusing to run -- {reason}.")
    return config.WorkspaceLayout(root)


# -- Operations -------------------------------------------------------------


def validated_identity_members(
    raw_members: Sequence[str], flag: str
) -> tuple[str, ...]:
    """Canonicalize operator-supplied member ids for an identity ruling
    (#797): deduped and sorted, so either typing order reaches the same
    record and the same owning sidecar.

    Every id goes through `canonicalize_concept_id` FIRST -- the documented
    path-safety gate `forget`/`merge` apply. These ids reach
    `bundle_decisions.decisions_path_for`, which joins them onto the decisions
    root, so an absolute id or a `..` segment would otherwise write a sidecar
    OUTSIDE the bundle. It also collapses `./` and repeated slashes, so two
    spellings of one id cannot key two rulings. Existence is NOT required: a
    ruling may name a concept absent right now, and demanding a live file would
    make the human's answer depend on the machine's state.

    Refuses below two DISTINCT members. A one-member "group" is not a duplicate
    ruling at all, and a repeated id silently collapsing to one would write a
    record no candidate group can ever match -- a ruling that appears to have
    been recorded and suppresses nothing is worse than a refusal."""
    canonical: set[str] = set()
    for member in raw_members:
        stripped = member.strip()
        if not stripped:
            continue
        try:
            canonical.add(application_lifecycle.canonicalize_concept_id(stripped))
        except ValueError as exc:
            raise InvalidMembers(
                f"openkos {_VERB}: refusing to run -- {flag} {exc}."
            ) from exc
    members = tuple(sorted(canonical))
    if len(members) < 2:
        raise InvalidMembers(
            f"openkos {_VERB}: refusing to run -- {flag} needs at least two "
            "distinct concept ids; repeat the flag once per member."
        )
    return members


def apply_identity_decision(
    layout: config.WorkspaceLayout,
    member_ids: tuple[str, ...],
    *,
    target_state: bundle_decisions.DecisionState,
    on_warning: Warn | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> str:
    """Write (or update in place) the single identity ruling for `member_ids`
    to `target_state` (#797), returning the workspace-relative path for the
    caller's auto-commit list.

    Never opens `.openkos/findings.db`. A keep-distinct ruling must be writable
    with NO adjudication row behind it: the human may be overruling a verdict
    the model has not produced yet, or one that was recomputed away. Requiring
    a matching row would make the human's answer depend on the machine's.

    Members are sorted before keying AND before choosing the owning sidecar, so
    an operator typing the ids in either order reaches the same record."""
    members = tuple(sorted(member_ids))
    key = bundle_decisions.identity_decision_key_for(members)
    owner_id = members[0]
    existing = bundle_decisions.read_identity_decisions(
        owner_id, layout.bundle_dir, on_warning=on_warning
    )
    records = [record for record in existing if record.decision_key != key]
    records.append(
        bundle_decisions.IdentityDecisionRecord(
            decision_key=key,
            member_ids=members,
            state=target_state,
            decided_at=clock().isoformat(),
        )
    )
    path = bundle_decisions.write_identity_decisions(
        owner_id, layout.bundle_dir, records=records
    )
    return f"bundle/{path.relative_to(layout.bundle_dir).as_posix()}"


def record_identity_ruling(
    root: Path,
    raw_members: Sequence[str],
    *,
    flag: str,
    target_state: bundle_decisions.DecisionState,
    on_warning: Warn | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> IdentityRuling:
    """Validate `raw_members` and persist the ruling (`"declined"` for
    `--keep-distinct`, `"open"` for `--reopen`).

    Short-circuits before any bundle walk by construction: this function never
    computes candidates, so a ruling is recordable on a workspace whose
    candidate set is expensive to compute."""
    layout = _require_workspace(root)
    members = validated_identity_members(raw_members, flag)
    rel_path = apply_identity_decision(
        layout,
        members,
        target_state=target_state,
        on_warning=on_warning,
        clock=clock,
    )
    return IdentityRuling(members=members, rel_path=rel_path)


def list_kept_distinct(
    root: Path, *, on_warning: Warn | None = None
) -> tuple[bundle_decisions.IdentityDecisionRecord, ...]:
    """Every group a human ruled distinct, ordered by decision key, so the
    ruling is visible and reversible rather than an invisible suppression.

    Reads the WALKED sidecar path, never one rebuilt from a sidecar's own
    `concept_id` frontmatter field -- the same traversal guard the contradiction
    `--declined` listing applies."""
    layout = _require_workspace(root)
    records: list[bundle_decisions.IdentityDecisionRecord] = []
    for decisions_path in bundle_decisions.iter_decisions(layout.bundle_dir):
        records.extend(
            record
            for record in bundle_decisions.read_identity_decisions_at(
                decisions_path, on_warning=on_warning
            )
            if record.state == "declined"
        )
    return tuple(sorted(records, key=lambda item: item.decision_key))


def report_duplicates(
    root: Path,
    *,
    include_deprecated: bool,
    find_candidates_report: Callable[..., CandidateGroupReport],
    on_warning: Warn | None = None,
) -> DuplicatesReport:
    """The read-only candidate report for the workspace at `root`.

    The suppression of ruled-distinct groups runs AFTER the walk, never inside
    it: the cap and its truncation notice describe what the corpus PRODUCED,
    and filtering before them would let a ruled-distinct group silently consume
    a cap slot's worth of accounting. `on_warning` is consulted once per group,
    so a caller that wants each distinct note said once passes a de-duplicating
    callback."""
    layout = _require_workspace(root)
    report = find_candidates_report(
        layout.bundle_dir, include_deprecated=include_deprecated
    )
    groups = tuple(
        group
        for group in report.groups
        if not application_pending.is_group_kept_distinct(
            layout, group.member_ids, on_warning=on_warning
        )
    )
    return DuplicatesReport(
        groups=groups,
        suppressed=len(report.groups) - len(groups),
        truncation_notice=candidate_group_truncation_notice(report),
    )
