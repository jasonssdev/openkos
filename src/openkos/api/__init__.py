"""The OpenKOS Python API: a versioned, in-process facade over the engine.

This is the one declared public Python surface. Read these statements before
relying on it:

1. The surface is in-process and synchronous. Nothing here starts a thread, an
   event loop or a server; a caller that needs concurrency runs these functions
   on its own worker thread.
2. The surface is owner-local, as the CLI is: its caller is the workspace
   owner, and it applies no sensitivity disclosure gate. Results may carry
   stored content, including from confidential concepts. An adapter that
   discloses to anyone else must apply the local API's gate itself (ADR-0058).
3. Judging is the only operation that sends content anywhere, and only to the
   backend the workspace configures. The only other process the surface runs is
   the local git auto-commit of a decision it applies, which never pushes.
4. Cancellation is cooperative: a stop is observed between chunks, so it waits
   for the chunk in flight. A model call is never preempted.
5. Applying a decision does not reconcile merged concept bodies with a model
   and does not refresh the derived stores; `status` reports stale indexes and a
   maintenance pass or a reindex refreshes them.
6. `API_VERSION`, not the package version, is the compatibility promise. Within
   one value changes are additive; a breaking change raises it. A committed
   snapshot of every public name pins this in CI.

Only names listed in `__all__` are public; the underscore modules of this
package and everything under `openkos.application` are not.
"""

from typing import Final

from openkos.api._decisions import (
    Decision,
    DecisionKind,
    Evaluation,
    IdentityDetail,
    RelationDetail,
    TierDetail,
    list_decisions,
)
from openkos.api._reads import (
    ActivityEntry,
    HealthCounts,
    JobSummary,
    KindCounts,
    PendingReport,
    PendingRow,
    WorkspaceStatus,
    pending_report,
    status,
)
from openkos.api._workspace import Workspace, WorkspaceNotFound, WorkspaceUnreadable
from openkos.application.curate_decisions import Action, WithheldAction
from openkos.application.judging import JudgingRefused
from openkos.application.pending_queue_report import QueueUnavailableError
from openkos.application.queue_refresh import QueueRefreshRefused
from openkos.application.write_gate import (
    DriftDetected,
    Refused,
    WriteFailed,
    WriteRefused,
)
from openkos.errors import OpenKosError
from openkos.llm.base import (
    BackendError,
    BackendGenerationCapped,
    BackendModelNotFound,
    BackendUnavailable,
)
from openkos.lock import WorkspaceBusyError, WorkspaceLockUnavailableError

API_VERSION: Final = 1
"""The compatibility version of this surface, independent of the package version."""

__all__ = [
    "API_VERSION",
    "Action",
    "ActivityEntry",
    "BackendError",
    "BackendGenerationCapped",
    "BackendModelNotFound",
    "BackendUnavailable",
    "Decision",
    "DecisionKind",
    "DriftDetected",
    "Evaluation",
    "HealthCounts",
    "IdentityDetail",
    "JobSummary",
    "JudgingRefused",
    "KindCounts",
    "OpenKosError",
    "PendingReport",
    "PendingRow",
    "QueueRefreshRefused",
    "QueueUnavailableError",
    "Refused",
    "RelationDetail",
    "TierDetail",
    "WithheldAction",
    "Workspace",
    "WorkspaceBusyError",
    "WorkspaceLockUnavailableError",
    "WorkspaceNotFound",
    "WorkspaceStatus",
    "WorkspaceUnreadable",
    "WriteFailed",
    "WriteRefused",
    "list_decisions",
    "pending_report",
    "status",
]
