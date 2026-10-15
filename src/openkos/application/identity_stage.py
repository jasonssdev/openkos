"""The identity advisor stage, split into a compute half and a commit half.

`identity_report` is the LLM-free walk: the candidate report with the groups the
model already judged DIFFERENT dropped. `enqueue_identity_report` is the commit
half: it carries the latest servable stored verdict of each group into the
pending-work queue. The unattended daemon calls both in order; a caller that
wants to refresh the queue without a model reuses them (#1391).

Neither half builds a client or calls a model. The candidate walk is reached
through the `find_candidates_report` argument and the duplicates report through
its module attribute, so a test that patches either still intercepts.
"""

import dataclasses
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from openkos import config
from openkos.application import (
    duplicates_service,
    identity_adjudication,
    queue_producers,
)
from openkos.application import pending as application_pending
from openkos.application.lock_wait import CommitSection
from openkos.resolution.candidates import CandidateGroupReport


def identity_report(
    root: Path,
    layout: config.WorkspaceLayout,
    *,
    find_candidates_report: Callable[..., CandidateGroupReport],
) -> duplicates_service.DuplicatesReport:
    """LLM-free: `report_duplicates`, then drop judged-different groups (#1226).

    A group the model judged DIFFERENT (verdict still servable) is settled;
    leaving it out lets a complete pass retire its open row."""
    report = duplicates_service.report_duplicates(
        root,
        include_deprecated=False,
        find_candidates_report=find_candidates_report,
    )
    judged_different = application_pending.judged_different_groups(layout)
    return dataclasses.replace(
        report,
        groups=tuple(
            group
            for group in report.groups
            if tuple(sorted(group.member_ids)) not in judged_different
        ),
    )


def enqueue_identity_report(
    conn: sqlite3.Connection,
    layout: config.WorkspaceLayout,
    report: duplicates_service.DuplicatesReport,
    *,
    commit_section: CommitSection,
    clock: Callable[[], datetime] | None = None,
) -> queue_producers.ProducerResult:
    """`servable_stored_verdicts`, then `queue_producers.enqueue_identity`.

    The latest servable stored verdict (rubric, members and digests checked) is
    carried in, so a pass never replaces a judged row with an unjudged one.
    Store-only: no client is built and no model is called."""
    return queue_producers.enqueue_identity(
        conn,
        report,
        current_digest=application_pending.current_finding_digest(layout.bundle_dir),
        bundle_dir=layout.bundle_dir,
        commit_section=commit_section,
        adjudicated=identity_adjudication.servable_stored_verdicts(
            layout, report.groups
        ),
        clock=clock,
    )
