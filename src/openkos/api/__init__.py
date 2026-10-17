"""The OpenKOS Python API: a versioned, in-process facade over the engine.

This is the one declared public Python surface. Read these statements before
relying on it:

1. The surface is in-process and synchronous. Nothing here starts a thread, an
   event loop or a server; a caller that needs concurrency runs these functions
   on its own worker thread.
2. The surface is owner-local, as the CLI is: its caller is the workspace
   owner, and it applies no sensitivity disclosure gate to any result except
   the answer `ask` returns, which follows the query disclosure rule of the
   MCP `query` tool and `query --json` (ADR-0068). Every other result may
   carry stored content, including from confidential concepts. An adapter
   that discloses anything else to anyone else must apply the local API's
   gate itself (ADR-0058).
3. Judging and asking are the only operations that send content anywhere, and
   only to the backends the workspace configures. Setup readiness and the
   model pull talk to the configured runtime too, but send no content: they
   read its version and model list and ask it to fetch a model the workspace
   configures. Reading and changing the model settings reach nothing: they
   read and rewrite `openkos.yaml` only, and never write a key. The only other
   process the surface runs is the local git auto-commit of a decision, an
   unmerge, an undo or a settings change it applies, which never pushes.
4. Cancellation is cooperative: a stop is observed between chunks, so it waits
   for the chunk in flight. A model call is never preempted.
5. Applying a decision, an unmerge or an undo does not reconcile merged concept
   bodies with a model and does not refresh the derived stores; `status` reports stale
   indexes and a maintenance pass or a reindex refreshes them.
6. `API_VERSION`, not the package version, is the compatibility promise. Within
   one value changes are additive; a breaking change raises it. A committed
   snapshot of every public name pins this in CI.

Only names listed in `__all__` are public; the underscore modules of this
package and everything under `openkos.application` are not.
"""

from typing import Final as _Final

from openkos.api._ask import Answer, AnswerCitation, AskProgress, OmittedConcept, ask
from openkos.api._decisions import (
    ApplyOutcome,
    ContradictionDetail,
    Decision,
    DecisionKind,
    DecisionPreview,
    DeclinePlan,
    Evaluation,
    FindingPreview,
    IdentityDetail,
    KeepDistinctPlan,
    MergePlan,
    MergeStep,
    ReconcilePlan,
    RefusedFileDetail,
    RelatePlan,
    RelationDetail,
    RevisionDetail,
    TierDetail,
    TierPlan,
    apply_decision,
    list_decisions,
    preview_decision,
    preview_finding,
)
from openkos.api._judging import (
    ChatClient,
    ChatClientFactory,
    RunResult,
    quote_judging,
    run_judging,
)
from openkos.api._pages import Page, PageSource, read_page
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
from openkos.api._settings import (
    apply_model_settings,
    model_settings,
    preview_model_settings,
)
from openkos.api._setup import pull_model, setup_readiness
from openkos.api._undo import (
    KeptDistinct,
    NotUndoable,
    Reopened,
    ReopenPreview,
    ReopenVersion,
    TierChanged,
    TierChangePreview,
    TierChangeVersion,
    UndoStale,
    Unrelated,
    UnrelatePreview,
    UnrelateVersion,
    VolatilityTier,
    apply_reopen,
    apply_tier_change,
    apply_unrelate,
    kept_distinct,
    preview_reopen,
    preview_tier_change,
    preview_unrelate,
)
from openkos.api._unmerge import (
    MergedConcept,
    MergeRecord,
    NotUnmergeable,
    Unmerged,
    UnmergePreview,
    UnmergeStale,
    UnmergeStep,
    UnmergeVersion,
    apply_unmerge,
    merge_history,
    merged_concepts,
    preview_unmerge,
)
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
from openkos.application.model_settings import (
    ModelBackend,
    ModelSettings,
    ModelSettingsChange,
    ModelSettingsChanged,
    ModelSettingsPreview,
    ModelSettingsRefused,
    ModelSettingsStale,
    ModelSettingsVersion,
    SettingChange,
)
from openkos.application.pending_queue_report import QueueUnavailableError
from openkos.application.queue_refresh import QueueRefreshRefused
from openkos.application.runtime import StopToken
from openkos.application.setup import (
    MemoryReadiness,
    ModelReadiness,
    PullObserver,
    PullOutcome,
    PullPhase,
    PullProgress,
    PullRefused,
    RuntimeReadiness,
    SetupReadiness,
)
from openkos.application.write_gate import (
    DriftDetected,
    MergeGroupInterrupted,
    Refused,
    UnmergeInterrupted,
    WriteFailed,
    WriteRefused,
)
from openkos.errors import OpenKosError
from openkos.llm.base import (
    BackendEmbeddingDimensionMismatch,
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
    "Answer",
    "AnswerCitation",
    "Applied",
    "ApplyOutcome",
    "AskProgress",
    "BackendEmbeddingDimensionMismatch",
    "BackendError",
    "BackendGenerationCapped",
    "BackendHostLocality",
    "BackendModelNotFound",
    "BackendUnavailable",
    "ChangedField",
    "ChatClient",
    "ChatClientFactory",
    "ContradictionDetail",
    "Decision",
    "DecisionKind",
    "DecisionPreview",
    "DecisionVersion",
    "DeclinePlan",
    "DriftDetected",
    "Evaluation",
    "FindingPreview",
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
    "KeptDistinct",
    "KindCounts",
    "KindRefresh",
    "LLMBackend",
    "MemoryReadiness",
    "MergeGroupInterrupted",
    "MergePlan",
    "MergeRecord",
    "MergeStep",
    "MergedConcept",
    "Message",
    "ModelBackend",
    "ModelReadiness",
    "ModelSettings",
    "ModelSettingsChange",
    "ModelSettingsChanged",
    "ModelSettingsPreview",
    "ModelSettingsRefused",
    "ModelSettingsStale",
    "ModelSettingsVersion",
    "NotApplied",
    "NotUndoable",
    "NotUnmergeable",
    "OmittedConcept",
    "OpenKosError",
    "Page",
    "PageSource",
    "PendingReport",
    "PendingRow",
    "PullObserver",
    "PullOutcome",
    "PullPhase",
    "PullProgress",
    "PullRefused",
    "QueueRefresh",
    "QueueRefreshRefused",
    "QueueUnavailableError",
    "QuoteBinding",
    "QuoteNotice",
    "QuoteStale",
    "QuoteToken",
    "ReconcilePlan",
    "Refused",
    "RefusedFileDetail",
    "RelatePlan",
    "RelationDetail",
    "ReopenPreview",
    "ReopenVersion",
    "Reopened",
    "RevisionDetail",
    "RunRefused",
    "RunResult",
    "RuntimeReadiness",
    "SettingChange",
    "SetupReadiness",
    "Stale",
    "StopToken",
    "TierChangePreview",
    "TierChangeVersion",
    "TierChanged",
    "TierDetail",
    "TierPlan",
    "UndoStale",
    "UnmergeInterrupted",
    "UnmergePreview",
    "UnmergeStale",
    "UnmergeStep",
    "UnmergeVersion",
    "Unmerged",
    "UnrelatePreview",
    "UnrelateVersion",
    "Unrelated",
    "VolatilityTier",
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
    "apply_model_settings",
    "apply_reopen",
    "apply_tier_change",
    "apply_unmerge",
    "apply_unrelate",
    "ask",
    "kept_distinct",
    "list_decisions",
    "merge_history",
    "merged_concepts",
    "model_settings",
    "pending_report",
    "preview_decision",
    "preview_finding",
    "preview_model_settings",
    "preview_reopen",
    "preview_tier_change",
    "preview_unmerge",
    "preview_unrelate",
    "pull_model",
    "quote_judging",
    "read_page",
    "refresh_decision_queue",
    "run_judging",
    "setup_readiness",
    "status",
]
