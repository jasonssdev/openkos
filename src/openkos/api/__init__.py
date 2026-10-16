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

from typing import Final as _Final

from openkos.api._decisions import (
    ApplyOutcome,
    Decision,
    DecisionKind,
    DecisionPreview,
    Evaluation,
    IdentityDetail,
    KeepDistinctPlan,
    MergePlan,
    MergeStep,
    RelatePlan,
    RelationDetail,
    TierDetail,
    TierPlan,
    apply_decision,
    list_decisions,
    preview_decision,
)
from openkos.api._judging import (
    ChatClient,
    ChatClientFactory,
    RunResult,
    quote_judging,
    run_judging,
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
from openkos.api._refresh import KindRefresh, QueueRefresh, refresh_decision_queue
from openkos.api._workspace import Workspace, WorkspaceNotFound, WorkspaceUnreadable
from openkos.application.curate_decisions import (
    Action,
    Applied,
    DecisionVersion,
    GroupSelection,
    NotApplied,
    Stale,
    WithheldAction,
)
from openkos.application.judging import (
    ChangedField,
    JudgingKind,
    JudgingObserver,
    JudgingOutcome,
    JudgingProgress,
    JudgingQuote,
    JudgingRefused,
    QuoteBinding,
    QuoteNotice,
    QuoteStale,
    QuoteToken,
    RunRefused,
    WorkItem,
)
from openkos.application.pending_queue_report import QueueUnavailableError
from openkos.application.queue_refresh import QueueRefreshRefused
from openkos.application.runtime import StopToken
from openkos.application.write_gate import (
    DriftDetected,
    MergeGroupInterrupted,
    Refused,
    WriteFailed,
    WriteRefused,
)
from openkos.errors import OpenKosError
from openkos.llm.base import (
    BackendError,
    BackendGenerationCapped,
    BackendHostLocality,
    BackendModelNotFound,
    BackendUnavailable,
    LLMBackend,
    Message,
)
from openkos.lock import WorkspaceBusyError, WorkspaceLockUnavailableError

API_VERSION: _Final = 1
"""The compatibility version of this surface, independent of the package version."""

__all__ = [
    "API_VERSION",
    "Action",
    "ActivityEntry",
    "Applied",
    "ApplyOutcome",
    "BackendError",
    "BackendGenerationCapped",
    "BackendHostLocality",
    "BackendModelNotFound",
    "BackendUnavailable",
    "ChangedField",
    "ChatClient",
    "ChatClientFactory",
    "Decision",
    "DecisionKind",
    "DecisionPreview",
    "DecisionVersion",
    "DriftDetected",
    "Evaluation",
    "GroupSelection",
    "HealthCounts",
    "IdentityDetail",
    "JobSummary",
    "JudgingKind",
    "JudgingObserver",
    "JudgingOutcome",
    "JudgingProgress",
    "JudgingQuote",
    "JudgingRefused",
    "KeepDistinctPlan",
    "KindCounts",
    "KindRefresh",
    "LLMBackend",
    "MergeGroupInterrupted",
    "MergePlan",
    "MergeStep",
    "Message",
    "NotApplied",
    "OpenKosError",
    "PendingReport",
    "PendingRow",
    "QueueRefresh",
    "QueueRefreshRefused",
    "QueueUnavailableError",
    "QuoteBinding",
    "QuoteNotice",
    "QuoteStale",
    "QuoteToken",
    "Refused",
    "RelatePlan",
    "RelationDetail",
    "RunRefused",
    "RunResult",
    "Stale",
    "StopToken",
    "TierDetail",
    "TierPlan",
    "WithheldAction",
    "WorkItem",
    "Workspace",
    "WorkspaceBusyError",
    "WorkspaceLockUnavailableError",
    "WorkspaceNotFound",
    "WorkspaceStatus",
    "WorkspaceUnreadable",
    "WriteFailed",
    "WriteRefused",
    "apply_decision",
    "list_decisions",
    "pending_report",
    "preview_decision",
    "quote_judging",
    "refresh_decision_queue",
    "run_judging",
    "status",
]
