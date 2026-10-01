"""The single-source ingest use case, as an application service (ADR-0018,
issue #1138; the orchestration half of #918's ingest bounded context).

`ingest_source` ingests ONE source into the workspace at an explicit
`root`: Phase A (validate, stage, compose the whole result in memory), the
confirmation question, the drift guard, Phase B (the writes), the
auto-commit, and the post-commit derived-index step. It returns a typed
`IngestOutcome` and raises typed `IngestRefused` subclasses -- it never
prompts, never renders, never reads the current directory, never calls
`sys.stdin.isatty()`, and never raises `typer.Exit`. The CLI `ingest` verb
(single file and batch alike) is one adapter over it; MVP 4's folder watch
is meant to be another.

What it does NOT own, and how it reaches each through a parameter instead
(the layering invariant forbids importing `openkos.cli`, `typer`, `rich`
or `openkos.vcs` here):

* the confirmation question -- a `confirm` callback answering
  `"proceed"`, `"declined"` or `"unavailable"`; the service owns only WHEN
  the gate applies (`not policy.skip_confirmation and cfg.review`), and with
  no callback a required confirmation is `"unavailable"`, never a silent
  yes;
* every word the user reads -- an `IngestObserver` receives typed data (the
  preview, the staged objects, the import summary) and the few advisory
  lines whose wording is the service's own;
* the concrete effects -- `IngestPorts` carries the chat-client factory, the
  auto-commit, the post-commit embed step, the snapshot read and the clock,
  so each stays a substitutable seam and the adapter's own implementations
  (which print warnings and touch git) stay adapter-side.

Why a module of its own rather than more of `application/ingest.py`: that
module is the pure staging and plan-composition core (no I/O of its own,
1.5k lines); this one is the orchestration that sequences it with effects.
Keeping them apart lets the core stay effect-free.

Phase B is not transactional (see `ingest_source`); the `ingest_pending`
marker on the Source (#1136) is what makes an interrupted run completable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from openkos import config, fsio
from openkos.application import catalog_delta, lock_wait, queue_resolution
from openkos.application import drift as application_drift
from openkos.application import ingest as application_ingest
from openkos.bundle import source_titles
from openkos.extraction.concept import FAN_OUT_CONCURRENCY, fans_out
from openkos.llm.base import BackendError, LLMBackend, is_timeout_failure
from openkos.model import okf
from openkos.state.vectorstore import content_hash

ConfirmationAnswer = Literal["proceed", "declined", "unavailable"]
"""What a `confirm` callback answers. `"unavailable"` means the question
could not be asked (no TTY, no human): the service refuses rather than
choosing for the caller."""

PhaseHook = Callable[[str], None]


# -- Typed refusals ---------------------------------------------------------


class IngestRefused(Exception):
    """Base of every refusal the service raises. `message` is the complete,
    user-facing refusal text (the wording the CLI has always printed), so
    an adapter renders it verbatim and maps the TYPE to an exit code. Not an
    `OSError`/`ValueError`: the service's own `except (OSError, ValueError)`
    blocks must never swallow one of these."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SourceNotReadable(IngestRefused):
    """`src` does not exist or is not a file."""


class NotAWorkspace(IngestRefused):
    """`root` is not an OpenKOS workspace."""


class RawImmutabilityRefused(IngestRefused):
    """The raw copy already exists under this name with different bytes.
    `source_id` is the Source concept id the refused file maps to, so a caller
    that queues the refusal needs no second look at the workspace."""

    def __init__(self, message: str, *, source_id: str | None = None) -> None:
        super().__init__(message)
        self.source_id = source_id


class InconsistentWorkspace(IngestRefused):
    """The Source concept exists but its raw copy is missing."""


class SymlinkedDestination(IngestRefused):
    """A destination path passes through a symlinked segment below the
    workspace root (#1126); nothing was written."""


class SourceCheckFailed(IngestRefused):
    """Checking the source or the workspace raised `OSError`/`ValueError`."""


class PreparationFailed(IngestRefused):
    """Phase A (reading, staging, composing) raised `OSError`/`ValueError`."""


class WriteFailed(IngestRefused):
    """Phase B raised `OSError`/`ValueError` partway through; whatever
    already landed stays in place (Phase B is not transactional)."""


class DriftDetected(IngestRefused):
    """A target changed, vanished, or left the workspace after Phase A read
    it; nothing was written. The one refusal a caller may retry."""


class ConfirmationDeclined(IngestRefused):
    """The confirmation question was answered no; nothing was written."""

    def __init__(self) -> None:
        super().__init__("declined")


class ConfirmationUnavailable(IngestRefused):
    """A confirmation was required and could not be asked (or there is no
    one to ask); nothing was written."""

    def __init__(self) -> None:
        super().__init__("confirmation required but unavailable")


# -- Inputs -----------------------------------------------------------------


@dataclass(frozen=True)
class IngestPolicy:
    """The caller's choices for one ingest."""

    include_confidential: bool = False
    re_extract: bool = False
    event_date: date | None = None
    skip_confirmation: bool = False
    """`--auto`: the confirmation question is not asked. The drift guard
    still runs -- skipping the prompt does not skip the window it stood in."""


def _refuse_symlinked_destinations(root: Path, destinations: Sequence[Path]) -> None:
    """Refuse when any destination path passes through a symlinked segment
    below the workspace root (#1126), with the shared D1-shaped reason
    `require_workspace` uses. `write_exclusive` opens with mode `x`, which
    follows a symlinked PARENT, so a linked directory would carry source text
    out of the workspace. Runs before any write, so a refusal leaves the
    workspace exactly as it was found."""
    for destination in destinations:
        reason = config.symlink_boundary_reason(destination, root)
        if reason is not None:
            raise SymlinkedDestination(
                f"openkos ingest: refusing to ingest -- {reason}."
            )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class IngestPorts:
    """The effects the service sequences but does not own."""

    chat_client: Callable[[config.Config], LLMBackend]
    """Builds the extraction backend for `cfg` (the adapter's own factory)."""

    autocommit: Callable[[Path, Sequence[str], str], object]
    """Best-effort commit of the listed workspace-relative paths; must never
    raise for a git failure (it degrades to a warning)."""

    after_commit: Callable[[config.WorkspaceLayout, config.Config], None]
    """The post-commit derived-index step; runs only after the commit so a
    failing embedder degrades to a notice instead of stranding uncommitted
    writes (#183)."""

    snapshot_read: Callable[[Path], tuple[bytes, str]] = fsio.snapshot_read
    """ONE observation of a file: its bytes (the drift baseline) and decoded
    text (the plan's input), taken together (#318)."""

    clock: Callable[[], datetime] = _utc_now

    commit_section: lock_wait.CommitSection = nullcontext
    """Entered around the COMMIT phase -- re-validation, the write burst and the
    auto-commit -- and nowhere else (ADR-0036). The CLI hands one that takes the
    workspace lock under its `--wait` policy, the unattended runner one with its
    own policy; the default holds nothing, for a caller that needs no lock.
    Extraction and the confirmation question run before it is entered, so a slow
    model never holds the lock."""


# -- Outputs ----------------------------------------------------------------


@dataclass(frozen=True)
class IngestPreview:
    """Everything the adapter needs to show the proposed changes, before
    anything is written. Data, not text: the adapter owns the wording."""

    regenerate: bool
    """A byte-identical re-ingest (the raw copy is reused untouched)."""
    name: str
    """The raw copy's basename under `raw/`."""
    slug: str
    resolved_sensitivity: str
    sensitivity_clause: str
    """Why the level is what it is; meaningful only when `regenerate`."""
    title_clause: str
    """`"; title changed from ... to ..."` or `""`; only when `regenerate`."""
    event_date: application_ingest.EventDateResolution
    derived: tuple[application_ingest.DerivedPlan, ...]
    adopted: tuple[application_ingest.AdoptedObject, ...]
    """Objects an interrupted earlier run wrote, now being catalogued."""
    source_only_rewrite: bool
    """#773's convergence path: a Source rewrite with no extraction."""
    frontmatter_key_count: int | None
    """Set only when a Source-only rewrite recorded incoming frontmatter."""
    tags_added: tuple[str, ...]
    """Only populated on a Source-only rewrite."""
    sensitivity_raised: bool
    """Only true on a Source-only rewrite that raised the Source's level."""
    index_name: str
    log_name: str


@dataclass(frozen=True)
class ImportedSummary:
    """What a finished ingest wrote, for the closing lines."""

    source: Path
    imported_paths: tuple[str, ...]
    index_name: str
    log_name: str
    type_counts: dict[str, int]


@dataclass(frozen=True)
class IngestOutcome:
    """What one ingest did, for a caller's own reporting and for the batch
    tally (#267). A refusal never constructs one: it is raised instead."""

    regenerated: bool
    """`True` on a byte-identical re-ingest, `False` on a fresh ingest."""

    extraction_degraded: bool
    """`True` exactly when staging returned a `skip_reason` on a run that
    actually extracted (the Source-only degrade taxonomy `docs/cli.md`
    documents)."""

    derived_count: int = 0
    """How many derived objects this run staged (#566's denominator)."""

    extraction_skipped: bool = False
    """`True` exactly when #773's convergence short-circuit fired: nothing
    was sent to a model. Always `False` on a fresh ingest, on
    `--re-extract`, and on the retryable-debt paths."""

    alternative_pairs: tuple[tuple[str, str], ...] = ()
    """One `(type, type_alternative)` per staged object whose classification
    the model reported as torn (#401); callers aggregate them into ONE
    summary line per run (#566)."""

    type_floor_pairs: tuple[tuple[str, str], ...] = ()
    """One `(type, resolved_level)` per staged object whose sensitivity floor
    was raised by its type (#669); aggregated like `alternative_pairs`."""

    extraction_notice: tuple[okf.ExtractionNotice, ...] = ()
    """Every `extraction_notice` token the Source CARRIES once the run has
    finished (#805, item 1) -- what is on disk when the run ends, which on
    #773's short-circuit is read back from the untouched Source rather than
    stamped by this run."""


@dataclass(frozen=True)
class IngestWritten(IngestOutcome):
    """The run wrote (and committed) a Source and its derived objects."""


@dataclass(frozen=True)
class IngestUnchanged(IngestOutcome):
    """#773's convergence short-circuit: a byte-identical re-ingest of a
    Source whose extraction already ran to its intended conclusion. Nothing
    was written and no model was contacted; this is the early return that
    used to end the CLI command, expressed as a value."""


class IngestObserver:
    """Receives what a run has to say while it runs. Every method is a
    no-op, so an unattended caller passes nothing and gets silence; an
    adapter overrides the ones it renders."""

    def notice(self, message: str) -> None:
        """One advisory line (the CLI sends it to stderr)."""

    def extraction_starting(self) -> None:
        """The single long model wait is about to begin."""

    def extraction_progress(self) -> AbstractContextManager[PhaseHook | None]:
        """A context wrapping the extraction call; yields a phase-label hook
        for the extractor, or `None` for no hook."""
        return nullcontext(None)

    def staged(self, staged: application_ingest.StagedDerivedObjects) -> None:
        """Staging finished: drops, degrade notes and report summary to show."""

    def preview(self, preview: IngestPreview) -> None:
        """The proposed changes, shown before the confirmation question."""

    def imported(self, summary: ImportedSummary) -> None:
        """Phase B finished, before the commit."""


ConfirmCallback = Callable[[IngestPreview], ConfirmationAnswer]


# -- The use case -----------------------------------------------------------


@dataclass(frozen=True)
class _Prepared:
    """Phase A's complete result: every byte Phase B will write, computed in
    memory from one observation of each input."""

    layout: config.WorkspaceLayout
    cfg: config.Config
    src: Path
    name: str
    slug: str
    regenerate: bool
    raw_dest: Path
    sources_dir: Path
    concept_path: Path
    index_path: Path
    log_path: Path
    concept_content: str
    new_index_text: str
    new_log_text: str
    index_snapshot: bytes
    log_snapshot: bytes
    """The bytes `new_index_text`/`new_log_text` were composed from. When both
    still match at the commit phase the finished text is written as planned;
    otherwise the catalog is re-composed from the current bytes."""
    recompose_catalog: Callable[[str, str], application_ingest.CatalogUpdate]
    """The staged catalog delta: `(index_text, log_text)` -> the catalog the plan
    owns, applied to those bytes. Pure, so the commit phase can re-apply it to
    whatever `index.md` and `log.md` hold by then instead of refusing."""
    guarded_targets: dict[Path, bytes]
    """Targets the plan rewrites in place: a change refuses the run."""
    read_dependencies: dict[Path, bytes]
    """Files whose bytes decided a sensitivity level or a provenance claim in
    the plan but which the plan does not write. A change refuses the run."""
    created_targets: tuple[Path, ...]
    derived_plans: tuple[application_ingest.DerivedPlan, ...]
    adopted: tuple[application_ingest.AdoptedObject, ...]
    two_step: bool
    rewrites_existing_concept: bool
    """The concept existed at Phase A and is in the drift guard's mapping."""
    preview: IngestPreview
    outcome: IngestOutcome


def ingest_source(
    root: Path,
    src: Path,
    policy: IngestPolicy,
    *,
    ports: IngestPorts,
    observer: IngestObserver | None = None,
    confirm: ConfirmCallback | None = None,
) -> IngestOutcome:
    """Copy `src` into `root`'s `raw/`, generate one OKF Source concept, and
    attempt LLM extraction of zero or more derived objects.

    Phase A (`_prepare`, no writes) validates and builds the entire result
    in memory: `src` must be an existing file and `root` a workspace; the
    destination is resolved against the whole collision family under
    `raw/` (#552) and is always a bare basename, so the raw copy and the
    concept can never land outside `raw/` or `bundle/sources/`. On a
    byte-identical re-ingest the raw copy is reused untouched and only the
    Source, `index.md` and `log.md` are regenerated; differing bytes against
    a MATCHED copy refuse (`RawImmutabilityRefused`); a concept with no raw
    copy refuses as an inconsistent workspace. Extraction is always
    attempted (even under `skip_confirmation`), gated on the workspace
    sensitivity floor unless `include_confidential`; a backend failure or an
    empty result degrades to a Source-only run, never a refusal. #773's
    convergence short-circuit returns `IngestUnchanged` before any model
    contact when a re-ingest has nothing to redo.

    The confirmation gate follows the preview: when `skip_confirmation` is
    false and the workspace config says `review: true`, `confirm` is asked
    (`ConfirmationDeclined` on no, `ConfirmationUnavailable` when it could
    not be asked or no callback was given).

    Past the gate -- and on runs that skip it -- the drift guard re-reads
    `index.md`, `log.md` and a pre-existing concept and raises
    `DriftDetected` (nothing written) if any changed or vanished since Phase
    A read it (#306, #313, #319). The create-only writes are excluded on
    purpose: `copy_exclusive`/`write_exclusive` already fail closed on a
    concurrent create, and a file that did not exist at Phase A has no
    snapshot to compare. The two mechanisms tile the whole space (#322).

    Phase B writes, in order: the raw copy and the Source (create-only, or
    atomic when it existed at Phase A and is guarded), each derived object
    (create-only), then `index.md` and `log.md` (atomic, catalog last so it
    never points at a file that does not exist yet). It is NOT
    transactional -- a failure partway leaves what already landed, a
    detectable partial result (`WriteFailed`). #1136: the Source, the one
    file the convergence gate reads, is written FIRST carrying
    `ingest_pending` and rewritten WITHOUT it as the LAST write, so a run
    killed in between is never treated as converged and the next run
    completes it. After the writes: the import summary, the auto-commit, and
    the post-commit derived-index step, in that order."""
    obs = observer if observer is not None else IngestObserver()
    prepared = _prepare(root, src, policy, ports, obs)
    if isinstance(prepared, IngestUnchanged):
        return prepared

    obs.preview(prepared.preview)

    if not policy.skip_confirmation and prepared.cfg.review:
        answer: ConfirmationAnswer = (
            confirm(prepared.preview) if confirm is not None else "unavailable"
        )
        if answer == "declined":
            raise ConfirmationDeclined()
        if answer != "proceed":
            raise ConfirmationUnavailable()

    # The commit phase (ADR-0036): everything above ran without the workspace
    # lock; from here to the end of the auto-commit the lock is held. Nothing in
    # it calls a model or waits on a human.
    with ports.commit_section():
        # Issue #313: every byte below was computed from a pre-prompt read, so
        # re-validate each target now -- after the gate, before the first write.
        _revalidate(prepared)
        new_index_text, new_log_text = _recompose_catalog(prepared, ports)

        _write(prepared, new_index_text, new_log_text)
        _resolve_watch_refusals(prepared)

        imported_paths = [
            f"raw/{prepared.name}",
            f"bundle/sources/{prepared.slug}.md",
        ]
        imported_paths.extend(
            f"bundle/{plan.link_dir}/{plan.slug}.md" for plan in prepared.derived_plans
        )
        # Adopted objects were written by the interrupted run and are still
        # uncommitted; they belong in this run's commit (#1136).
        committed_paths = [
            *imported_paths,
            *(f"bundle/{obj.link_dir}/{obj.slug}.md" for obj in prepared.adopted),
        ]
        type_counts: dict[str, int] = {}
        for plan in prepared.derived_plans:
            type_counts[plan.doc_type] = type_counts.get(plan.doc_type, 0) + 1
        obs.imported(
            ImportedSummary(
                source=src,
                imported_paths=tuple(imported_paths),
                index_name=prepared.index_path.name,
                log_name=prepared.log_path.name,
                type_counts=type_counts,
            )
        )

        ports.autocommit(
            root,
            [*committed_paths, "bundle/index.md", "bundle/log.md"],
            f"openkos: ingest {prepared.name} (+{len(prepared.derived_plans)} concepts)",
        )

    # AFTER the commit, never before: the ingest is durable by this point,
    # so a failing embedder degrades to a notice instead of stranding
    # written-but-uncommitted files (#183). Outside the commit section too: the
    # embedding calls hold no lock, and the adapter re-takes it briefly for the
    # vector upsert.
    ports.after_commit(prepared.layout, prepared.cfg)

    return prepared.outcome


def _display(layout: config.WorkspaceLayout, path: Path) -> str:
    try:
        return path.relative_to(layout.root).as_posix()
    except ValueError:
        return str(path)


def _describe_dependency_drift(
    layout: config.WorkspaceLayout, dependencies: Mapping[Path, bytes]
) -> str | None:
    """Refusal text when a file that decided the plan's sensitivity or
    provenance changed or vanished since Phase A read it, else `None`.

    Separate from `describe_drift` because these files are READ by the plan,
    never written: calling them "write targets" would misname what the operator
    has to look at."""
    changed: list[str] = []
    for path, expected in dependencies.items():
        try:
            current = path.read_bytes()
        except OSError:
            changed.append(f"{_display(layout, path)} (vanished)")
            continue
        if current != expected:
            changed.append(_display(layout, path))
    if not changed:
        return None
    return (
        "openkos ingest: refusing to write -- "
        f"{len(changed)} input(s) that decided this ingest's sensitivity or "
        f"provenance changed on disk after the plan was computed: "
        f"{', '.join(sorted(changed))}. Nothing was written. Re-run to "
        "recompute over the current state."
    )


def _describe_created_targets(
    layout: config.WorkspaceLayout, created: Sequence[Path]
) -> str | None:
    """Refusal text when a path the plan would CREATE exists by the commit
    phase. Create-only writes fail closed on their own, but only partway through
    the burst; checking first keeps the refusal whole-run, before any write."""
    existing: list[str] = []
    for path in created:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            pass  # unreadable is not provably absent: fail closed
        existing.append(_display(layout, path))
    if not existing:
        return None
    return (
        "openkos ingest: refusing to write -- "
        f"{len(existing)} path(s) this run would create now exist: "
        f"{', '.join(sorted(existing))}. Nothing was written. Re-run to "
        "recompute over the current bundle."
    )


def _revalidate(prepared: _Prepared) -> None:
    """The commit phase's first step: refuse, before the first write, when any
    input the plan's safety rests on changed since Phase A read it."""
    drift = application_drift.describe_drift(
        prepared.layout, prepared.guarded_targets, "ingest"
    )
    if drift is None:
        drift = _describe_dependency_drift(prepared.layout, prepared.read_dependencies)
    if drift is None:
        drift = _describe_created_targets(prepared.layout, prepared.created_targets)
    if drift is not None:
        raise DriftDetected(drift)


def _recompose_catalog(prepared: _Prepared, ports: IngestPorts) -> tuple[str, str]:
    """The `index.md` and `log.md` text to write.

    Every verb appends to both, so a change since Phase A is the ordinary case
    under a lock held only for the commit, not drift. When their bytes still
    match what the plan was composed from the finished text is used as planned;
    otherwise the staged catalog delta is re-applied to the current bytes, so a
    concurrent append is kept. Only a file that cannot be read, or whose
    current text cannot take the delta, refuses."""
    try:
        return catalog_delta.recompose_catalog(
            verb="ingest",
            index_path=prepared.index_path,
            log_path=prepared.log_path,
            index_baseline=prepared.index_snapshot,
            log_baseline=prepared.log_snapshot,
            planned=(prepared.new_index_text, prepared.new_log_text),
            delta=lambda index_text, log_text: _catalog_texts(
                prepared.recompose_catalog(index_text, log_text)
            ),
            read=ports.snapshot_read,
            subject="the ingest's",
        )
    except catalog_delta.CatalogRecomposeError as exc:
        raise DriftDetected(str(exc)) from exc


def _catalog_texts(update: application_ingest.CatalogUpdate) -> tuple[str, str]:
    return update.new_index_text, update.new_log_text


def _prepare(
    root: Path,
    src: Path,
    policy: IngestPolicy,
    ports: IngestPorts,
    obs: IngestObserver,
) -> _Prepared | IngestUnchanged:
    """Phase A: no writes. Returns the complete plan, or `IngestUnchanged`
    when #773's convergence short-circuit applies."""
    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        if not src.is_file():
            raise SourceNotReadable(
                f"openkos ingest: refusing to ingest -- '{src}' does not exist "
                "or is not a readable file."
            )

        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise NotAWorkspace(
                f"openkos ingest: refusing to ingest -- {workspace_reason}."
            )

        # #552: the destination is resolved against the whole collision
        # FAMILY under `raw/`, not against the bare basename alone -- a name
        # already held by a different file no longer refuses this one or
        # absorbs it into the incumbent's Source. Still a bare basename, so
        # the path-traversal containment is unchanged.
        origin_key = okf.origin_key_for(src)
        destination = application_ingest.resolve_raw_destination(
            src, layout, origin_key
        )
        name = destination.name
        slug = source_titles.slugify(Path(name).stem)
        if not slug:
            raise ValueError(f"cannot derive a concept name from '{src}'")
        raw_dest = layout.raw_dir / name
        sources_dir = layout.bundle_dir / "sources"
        concept_path = sources_dir / f"{slug}.md"
        # Symlink boundary (#1126): refused here, before the extraction spends
        # a backend call and before anything is written.
        _refuse_symlinked_destinations(root, [raw_dest, concept_path])

        if destination.disambiguated_from is not None:
            # A destination the user did not name is never chosen silently.
            # Said BEFORE the checks below so it frames any refusal that
            # follows, rather than being swallowed by it.
            obs.notice(
                f"openkos ingest: 'raw/{destination.disambiguated_from}' is "
                f"already held by a different source; copying this one to "
                f"'raw/{name}' instead."
            )

        regenerate = destination.regenerate
        if regenerate:
            if src.read_bytes() != raw_dest.read_bytes():
                # Same file, changed bytes -> refuse (D4). Reachable only when
                # the destination was MATCHED (by recorded origin, or by a
                # legacy member's identical bytes), so immutability speaks
                # about a file this run could identify -- never about an
                # unrelated neighbour that merely shared a basename.
                raise RawImmutabilityRefused(
                    f"openkos ingest: refusing to ingest -- '{src}' differs from "
                    f"the existing 'raw/{name}' copy; raw sources are "
                    "immutable. Ingest under a different name, or inspect the "
                    "existing copy.",
                    source_id=f"sources/{slug}",
                )
        elif concept_path.exists():
            # raw absent + concept present -> inconsistent workspace (D5)
            raise InconsistentWorkspace(
                f"openkos ingest: refusing to ingest -- 'bundle/sources/{slug}.md' "
                f"exists but its raw source 'raw/{name}' is missing; the "
                "workspace is inconsistent, inspect it before retrying."
            )
        # else: raw absent + concept absent -> fresh (regenerate stays False)
    except (OSError, ValueError) as exc:
        raise SourceCheckFailed(
            f"openkos ingest: failed while checking the source or workspace -- {exc}."
        ) from exc

    now = ports.clock()
    resource = f"raw/{name}"

    try:
        try:
            raw_content: str | None = src.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # `UnicodeDecodeError` subclasses `ValueError`, so it MUST be
            # caught here first: the outer `except (OSError, ValueError)`
            # would otherwise swallow a binary/non-text source and fail the
            # whole ingest, instead of degrading to the binary-fallback body.
            raw_content = None
        # The workspace config decides the Source's sensitivity floor and every
        # derived object's: its bytes are a read dependency of the plan. Taken
        # BEFORE it is parsed, so an edit landing between the two reads makes
        # the commit phase refuse (fail closed) rather than adopt the parse.
        config_snapshot = layout.config_path.read_bytes()
        cfg = config.read_config(root)
        had_prior_source = regenerate and concept_path.exists()
        if had_prior_source:
            # ONE observation of the concept file, taken HERE and not with
            # `index.md`/`log.md` further down (#313 review, R4 CRITICAL;
            # single-read shape per #318). Between this point and there sits
            # `stage_derived_objects`'s `llm.chat` round trip -- an unbounded
            # network call. Snapshotting after it would make an edit landing
            # during extraction the guard's OWN baseline: the comparison would
            # find no drift and `write_atomic` would then write back the
            # document built from this text, reverting it. That revert is a
            # sensitivity DOWNGRADE, since `resolved_sensitivity` is the
            # high-water mark computed from `on_disk_sensitivity`. Both
            # parses inside `compose_source_document` below and the guard's
            # bytes derive from this single read, so there is no second read
            # for an edit to slip between.
            try:
                concept_snapshot: bytes | None
                concept_snapshot, concept_text = ports.snapshot_read(concept_path)
            except (OSError, UnicodeDecodeError) as exc:
                raise ValueError(
                    f"refusing to ingest -- '{concept_path}' could not be "
                    "read to snapshot its current contents (sensitivity, "
                    f"title, and drift baseline): {exc}"
                ) from exc
        else:
            concept_snapshot = None
            concept_text = None

        # `title`/`description` derivation (issue #248), the re-ingest
        # sensitivity high-water mark (issue #229) and the on-disk title
        # read-back all live in `compose_source_document` --
        # `concept_text is None` is exactly `had_prior_source` being `False`.
        source_plan = application_ingest.compose_source_document(
            raw_content=raw_content,
            source_stem=src.stem,
            # The raw copy's basename, never the absolute import path: the
            # description is committed, embedded and served over MCP, and the
            # original location is not knowledge about the source (#1129).
            source_display_path=Path(resource).name,
            # A SECOND path, deliberately. `source_display_path` names the RAW
            # source and feeds the Source document's description ("Raw source
            # imported from '<name>'"); the refusal messages must instead name
            # the SOURCE DOCUMENT, because that is the file whose frontmatter
            # failed to parse and the one the operator has to open. Collapsing
            # the two silently reworded the refusal (#918 Slice 3).
            source_document_display_path=str(concept_path),
            resource=resource,
            origin_key=destination.origin_key,
            concept_text=concept_text,
            cfg=cfg,
            timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            event_date_flag=policy.event_date,
            source_name=src.name,
        )
        title = source_plan.title
        if source_plan.event_date.stored_malformed:
            # design.md Decision 7's warning: said right after
            # `compose_source_document` returns, regardless of whether the run
            # below converges and leaves the file untouched -- "ignoring",
            # never "removed", is what stays true either way.
            obs.notice(
                "openkos ingest: ignoring the malformed event_date "
                f"{source_plan.event_date.stored_raw!r} in "
                f"'bundle/sources/{slug}.md' -- expected YYYY-MM-DD."
            )

        # #773: the convergence short-circuit, decided BEFORE any model
        # contact and before any write. A byte-identical re-ingest of a
        # source whose previous extraction ran to its intended conclusion has
        # nothing to redo: re-running it here is what unioned every set the
        # model ever produced for one unchanged document (17 objects from an
        # 81-line source), because create-only dedup can only catch
        # verbatim-reproduced slugs. Writing NOTHING -- not even a
        # regenerated Source -- is what makes the promised idempotence true,
        # and it keeps the prior markers alive on disk without violating the
        # never-read-back rule. `converged_reingest` owns the gate's policy
        # decisions plus `--re-extract`'s deliberate-redo override.
        converged = (
            application_ingest.converged_reingest(
                concept_text, re_extract=policy.re_extract
            )
            if had_prior_source and concept_text is not None
            else None
        )
        # design.md Decision 6/7: convergence skips ONLY when the resolved
        # `event_date` did not change AND the lifted state (preserve-source-
        # frontmatter, #1062) did not change either. A converged Source whose
        # date OR lifted state DID change falls through with `converged` still
        # set -- `stage_derived_objects(carried=converged)` short-circuits
        # before any LLM call, and `compose_catalog_update` rebuilds the Source
        # with the carried markers and the new date/frontmatter -- the
        # "Source-only rewrite". Both `event_date.changed` and `lift_changed`
        # are always `False` when `converged is None`, so this condition is a
        # strict narrowing of the pre-#1014c skip, never a widening of it.
        if (
            converged is not None
            and not source_plan.event_date.changed
            and not source_plan.lift_changed
        ):
            obs.notice(
                "openkos ingest: source unchanged and already "
                "extracted; skipping extraction -- existing derived "
                "objects preserved; pass --re-extract to run "
                "extraction again."
            )
            return IngestUnchanged(
                regenerated=True,
                extraction_degraded=False,
                extraction_skipped=True,
                # This run stamps nothing, but the Source it just left
                # untouched may STILL carry the prior run's disclosure --
                # reachably #585's `sole-object-restates-source`. The summary
                # term counts what a Source CARRIES when the run ends (#805),
                # so reporting nothing here would under-count it. This is NOT
                # a new frontmatter read-back: `converged_reingest` already
                # performed the one read this needs.
                extraction_notice=converged.carried_notices,
            )

        # Extraction runs AFTER the Source concept is built, BEFORE the
        # preview -- always attempted, even under `skip_confirmation`; only
        # the confirm PROMPT is skipped. Guarded by `converged is None`
        # (design.md Decision 6): a date-only rewrite extracts nothing, so the
        # stage wording would misdescribe the run.
        if converged is None:
            obs.extraction_starting()
        # The chat client is constructed BEFORE the progress context opens,
        # mirroring the pre-move evaluation order. Still constructed on the
        # date-only-rewrite path: harmless, since `stage_derived_objects(
        # carried=...)` never calls `llm.chat` on it (design.md Decision 6).
        extraction_llm = ports.chat_client(cfg)
        try:
            with obs.extraction_progress() as on_progress:
                staged = application_ingest.stage_derived_objects(
                    raw_content=raw_content,
                    source_title=title,
                    source_slug=slug,
                    workspace_floor=source_plan.source_sensitivity,
                    stamp_sensitivity=source_plan.source_sensitivity,
                    source_tags=source_plan.tags,
                    timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    bundle_dir=layout.bundle_dir,
                    llm=extraction_llm,
                    cfg=cfg,
                    include_confidential=policy.include_confidential,
                    union_judge=cfg.union_judge,
                    on_progress=on_progress,
                    carried=converged,
                )
        except BackendError as exc:
            obs.notice(
                f"openkos ingest: concept extraction skipped -- {exc}; "
                "keeping the Source only."
            )
            # #746: the engine knows both halves of this and used to say
            # neither. `concurrent_extraction` inflates PER-CALL wall time when
            # the server is not running requests in parallel -- each request's
            # own timeout keeps running while it queues -- so a deadline
            # failure on that path may be an artifact of the setting rather
            # than a backend problem.
            #
            # All three conditions are required, and the third is the one a
            # naive check gets wrong: with the flag on but a source below the
            # chunking threshold there are no windows to overlap, so
            # concurrency was never involved. Naming a timeout that is really a
            # refused connection would send the operator after a setting while
            # their server is not running.
            if (
                cfg.concurrent_extraction
                # Provably non-`None` here (a `BackendError` can only come from
                # the extractor call, which the service never reaches on
                # `None`/blank content); the explicit check is for mypy.
                and raw_content is not None
                and is_timeout_failure(exc)
                and fans_out(raw_content, source_title=title)
            ):
                obs.notice(
                    "openkos ingest: this run had concurrent_extraction on, "
                    "and the request ran out of time rather than failing "
                    "outright. Concurrent windows queue on a server started "
                    "without OLLAMA_NUM_PARALLEL, and each one's "
                    "chat_timeout keeps running while it waits -- so this "
                    "may be the setting, not the backend. Either raise "
                    "OLLAMA_NUM_PARALLEL to "
                    f"{FAN_OUT_CONCURRENCY} on the Ollama server, or set "
                    "concurrent_extraction: false in openkos.yaml."
                )
            staged = application_ingest.StagedDerivedObjects(
                plans=(),
                skip_reason="failed",
                notices=(),
                report=None,
                drops=(),
                lost_in_staging=0,
            )
        # Guarded like the stage notice (design.md Decision 6): a date-only
        # rewrite ran no extraction, so none of this render's wording
        # describes anything that actually happened this run.
        if converged is None:
            obs.staged(staged)
        derived_plans = staged.plans
        skip_reason = staged.skip_reason
        # Same boundary for the derived-object directories (`bundle/entities`,
        # ...), known only once staging has chosen each object's type.
        _refuse_symlinked_destinations(root, [plan.path for plan in derived_plans])
        extraction_notice = staged.notices
        # One snapshot observation per target: the decoded text feeds
        # `compose_catalog_update` below, the raw bytes feed the drift guard
        # (issues #306, #313, #318).
        index_bytes, index_text = ports.snapshot_read(index_path)
        log_bytes, log_text = ports.snapshot_read(log_path)
        # `index.md` and `log.md` are NOT guarded targets: every verb appends
        # to them, so the commit phase re-composes them from their current
        # bytes (`_recompose_catalog`) instead of refusing on a change.
        guarded_targets: dict[Path, bytes] = {}
        if concept_snapshot is not None:
            guarded_targets[concept_path] = concept_snapshot
        # `compose_catalog_update` owns the conditional Source re-render (never
        # patch the already-built bytes, never read either key off disk), the
        # dedup-before-insert Source bullet (D3), and the derived-plans
        # index/log loop including the durable disambiguation audit entry
        # (#131).
        # #1136: a prior Source still marked `ingest_pending` was left by an
        # interrupted run; objects that run wrote but never catalogued are
        # adopted into this run's index/log update (never rewritten).
        adopted: tuple[application_ingest.AdoptedObject, ...] = (
            application_ingest.find_uncatalogued_objects(
                layout.bundle_dir, slug, index_text
            )
            if converged is None
            and had_prior_source
            and application_ingest.prior_ingest_pending(concept_text)
            else ()
        )

        def recompose_catalog(
            current_index_text: str, current_log_text: str
        ) -> application_ingest.CatalogUpdate:
            return application_ingest.compose_catalog_update(
                source=source_plan,
                staged=staged,
                slug=slug,
                resource=resource,
                index_text=current_index_text,
                log_text=current_log_text,
                regenerate=regenerate,
                timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                entry_date=now.astimezone().date(),
                adopted=adopted,
            )

        catalog_update = recompose_catalog(index_text, log_text)
    except (OSError, ValueError) as exc:
        raise PreparationFailed(
            f"openkos ingest: failed while preparing the ingest -- {exc}."
        ) from exc

    sensitivity_clause = ""
    title_clause = ""
    if regenerate:
        # The resolved level is always named; the trailing clause
        # distinguishes the three re-ingest causes, selected with
        # `okf.sensitivity_direction(on_disk, cfg.default_sensitivity)`.
        # `had_prior_source` is `False` only for the post-forget case (no
        # prior Source to read), which reports "from the workspace default".
        if had_prior_source:
            direction = okf.sensitivity_direction(
                source_plan.on_disk_sensitivity, cfg.default_sensitivity
            )
            if direction == "lower":
                sensitivity_clause = "preserved from the existing Source"
            elif direction == "raise":
                sensitivity_clause = "raised by the workspace default"
            else:
                sensitivity_clause = "unchanged"
        else:
            sensitivity_clause = "from the workspace default"
        # Re-ingest recomputes `title` from content every run; only the
        # PREVIEW WORDING depends on the on-disk read. Name the change ONLY
        # when `on_disk_title` is known and actually differs from the freshly
        # derived `title` -- silence on the common path is deliberate.
        on_disk_title = source_plan.on_disk_title
        title_clause = (
            f"; title changed from {on_disk_title!r} to {title!r}"
            if on_disk_title is not None and on_disk_title != title
            else ""
        )

    # preserve-source-frontmatter (#1062), design.md Decision 7: each of these
    # is set only when its OWN specific delta fired on a Source-only rewrite
    # (`frontmatter_changed`/`tags_added`/`sensitivity_changed` are exposed
    # separately from the OR'd `lift_changed` for exactly this reason).
    source_only = converged is not None
    frontmatter_key_count: int | None = None
    if source_only and source_plan.frontmatter_changed:
        frontmatter = source_plan.source_frontmatter
        frontmatter_key_count = len(frontmatter) if frontmatter is not None else 0

    preview = IngestPreview(
        regenerate=regenerate,
        name=name,
        slug=slug,
        resolved_sensitivity=source_plan.resolved_sensitivity,
        sensitivity_clause=sensitivity_clause,
        title_clause=title_clause,
        event_date=source_plan.event_date,
        derived=tuple(derived_plans),
        adopted=adopted,
        source_only_rewrite=source_only,
        frontmatter_key_count=frontmatter_key_count,
        tags_added=tuple(source_plan.tags_added) if source_only else (),
        sensitivity_raised=source_only and source_plan.sensitivity_changed,
        index_name=index_path.name,
        log_name=log_path.name,
    )
    outcome = IngestWritten(
        regenerated=regenerate,
        # design.md Decision 6: a date-only rewrite (`converged is not None`)
        # carries the PRIOR run's `skip_reason` forward unread by any fresh
        # extraction -- that is not a fresh degrade, so it must not count as
        # one; `extraction_skipped` reports the carry instead.
        extraction_degraded=skip_reason is not None and converged is None,
        extraction_skipped=converged is not None,
        extraction_notice=extraction_notice,
        derived_count=len(derived_plans),
        alternative_pairs=tuple(
            (plan.doc_type, plan.type_alternative)
            for plan in derived_plans
            if plan.type_alternative is not None
        ),
        type_floor_pairs=tuple(
            (plan.doc_type, plan.sensitivity)
            for plan in derived_plans
            if plan.type_floor_raised
        ),
    )
    return _Prepared(
        layout=layout,
        cfg=cfg,
        src=src,
        name=name,
        slug=slug,
        regenerate=regenerate,
        raw_dest=raw_dest,
        sources_dir=sources_dir,
        concept_path=concept_path,
        index_path=index_path,
        log_path=log_path,
        concept_content=catalog_update.concept_content,
        new_index_text=catalog_update.new_index_text,
        new_log_text=catalog_update.new_log_text,
        index_snapshot=index_bytes,
        log_snapshot=log_bytes,
        recompose_catalog=recompose_catalog,
        guarded_targets=guarded_targets,
        read_dependencies={layout.config_path: config_snapshot},
        # Create-only writes have no snapshot to compare, so the commit phase
        # checks they are still absent: the raw copy on a fresh ingest, the
        # Source when it did not exist, and every staged derived object.
        created_targets=(
            *(() if regenerate else (raw_dest,)),
            *(() if had_prior_source else (concept_path,)),
            *(plan.path for plan in derived_plans),
        ),
        derived_plans=tuple(derived_plans),
        adopted=adopted,
        # A Source-only rewrite (`converged` set) extracts nothing and stays
        # one atomic write: the marker there would only force a needless
        # re-extract (#1136).
        two_step=converged is None,
        rewrites_existing_concept=had_prior_source,
        preview=preview,
        outcome=outcome,
    )


def _resolve_watch_refusals(prepared: _Prepared) -> None:
    """A raw copy landed: resolve the open `watch_refusal` rows that refused
    exactly these bytes (#1141). Skipped when the raw copy was reused, not
    written (`regenerate`): no new bytes landed."""
    if prepared.regenerate:
        return
    try:
        digest = content_hash(prepared.raw_dest.read_bytes())
    except OSError:
        return
    queue_resolution.resolve_watch_refusals(prepared.layout.root, raw_digest=digest)


def _write(prepared: _Prepared, new_index_text: str, new_log_text: str) -> None:
    """Phase B: the writes, in order (see `ingest_source`)."""
    first_content = (
        okf.mark_ingest_pending(prepared.concept_content)
        if prepared.two_step
        else prepared.concept_content
    )
    try:
        prepared.sources_dir.mkdir(parents=True, exist_ok=True)
        if prepared.regenerate:
            # D2: raw copy SKIPPED -- raw/<name> is reused, never rewritten.
            # The concept's writer is chosen by the SAME condition that gated
            # its guard entry in Phase A, so the two mechanisms are visibly
            # complementary (#322): a concept that EXISTED at Phase A has a
            # snapshot in `guarded_targets` and is written with
            # `write_atomic` (create-only would ALWAYS fail there); a concept
            # ABSENT at Phase A (post-`forget`) left the guard nothing to
            # compare, so `write_exclusive` fails closed on a file created
            # during the prompt window instead of silently overwriting it.
            if prepared.rewrites_existing_concept:
                fsio.write_atomic(prepared.concept_path, first_content)
            else:
                fsio.write_exclusive(prepared.concept_path, first_content)
        else:
            fsio.copy_exclusive(prepared.src, prepared.raw_dest)
            fsio.write_exclusive(prepared.concept_path, first_content)
        # Phase B write loop (design D5): `derived_plans` is the COMPLETE,
        # already-deduped write set computed in Phase A -- only `mkdir` +
        # create-only write, per plan, in staging order.
        for plan in prepared.derived_plans:
            plan.path.parent.mkdir(parents=True, exist_ok=True)
            fsio.write_exclusive(plan.path, plan.content)
        fsio.write_atomic(prepared.index_path, new_index_text)
        fsio.write_atomic(prepared.log_path, new_log_text)
        if prepared.two_step:
            fsio.write_atomic(prepared.concept_path, prepared.concept_content)
    except (OSError, ValueError) as exc:
        raise WriteFailed(
            f"openkos ingest: failed while writing the ingest -- {exc}."
        ) from exc
