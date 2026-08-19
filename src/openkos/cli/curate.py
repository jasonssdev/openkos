"""`openkos curate`'s stage engine: one ordered session over the five kinds
of pending human judgment -- identity, structure, metadata, sensitivity,
contradictions -- in the one order ADR-0005/ADR-0011 already fixes (issue
#266).

This module owns the whole engine: the `Stage` descriptor shape, `_STAGES`
(all five entries, frozen in slice 1 -- design D2), the cost gate (`gate`,
generalizing #134's spend-consent-before-model-call pattern), the sequencer
(`run_curate`), and the end-of-run summary (`render_summary`). `cli/main.py`
gains only the thin Typer command: workspace gate, config read, context
build, one call into `run_curate`, and an echo loop over `render_summary` --
the same `cli/next_action.py` shape (module owns the ordered engine,
`main.py` stays thin).

ONE deliberate inversion from `next_action.py`: `next` memoizes its signals
in `_BundleSignals` because every tier there reads the SAME pre-run
snapshot. `curate` MUST NOT memoize anything across stages -- Identity may
auto-commit a merge, and Structure/Metadata/Contradictions (slice 2) then
have to see the POST-merge bundle, not a stale pre-run view (design D4,
proposal D4). Every stage therefore derives its own queue from scratch when
the loop reaches it, by calling its own `probe` fresh, every run.

`_STAGES` has carried all five entries since slice 1 (design D2), and as of
slice 2 every entry is `live=True` with a real `probe`/`run` pair -- the
slice-1 state (Structure, Metadata, Contradictions at `live=False`, skipped
without probing and labeled "not yet available in this version") is history,
kept only as the record of HOW the tuple stayed frozen: slice 2 flipped
`live` and filled in `probe`/`run` on the three existing entries with no
framework change (spec: Slice Boundary). The `live` field itself remains,
as the descriptor contract a future stage would ship through the same way.

Imports here mirror `next_action.py`'s own precedent (design D1): this
module is the CLI-layer composition root, so it imports `resolution`,
`config`, and `observability` directly, exactly as `next_action.py` already
does. The one thing this module never imports at module scope is
`cli.main` itself -- `main.py` imports THIS module to register the `curate`
command, so importing `main` back at module scope here would be circular.
Identity's `run` needs a handful of `main.py`-private helpers
(`_prepare_one_merge`, `_commit_one_merge`, `_echo_n_gt2_skip`,
`_reject_drifted_targets`, `_merge_drift_targets`) that already exist there
for `merge`/`adjudicate --apply`; those are imported LAZILY, inside the
function bodies that need them, which is safe because by the time any
`curate` invocation actually runs, both modules have finished loading.
"""

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import typer

from openkos import config, lint, sensitivity
from openkos.cli import next_action as next_action_module
from openkos.cli import observability
from openkos.graph.base import Edge
from openkos.graph.sqlite_graph import build_graph
from openkos.llm.base import LLMBackend
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.model import okf
from openkos.model.relations import ASYMMETRIC_RELATION_TYPES
from openkos.resolution import candidate_group_truncation_notice, find_candidates_report
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    Verdict,
    adjudicate_candidates,
)
from openkos.resolution.candidates import CandidateGroup
from openkos.resolution.contradiction import (
    CandidatePlan,
    ContradictionVerdict,
    _CandidateSpec,
    contradiction_truncation_notice,
    find_contradictions,
    is_high_confidence_contradiction,
    plan_candidates,
)
from openkos.resolution.edge_typing import (
    LEAST_SPECIFIC_RELATION_TYPE,
    EdgeSuggestion,
    candidate_edges,
    candidate_truncation_notice,
    suggest_edge_types,
)
from openkos.resolution.volatility_typing import TierSuggestion, suggest_volatility
from openkos.state import derived, findings
from openkos.state.vectorstore import content_hash

_DOCTOR_HINT = " Or run `openkos doctor` to diagnose the environment."
"""Byte-identical to `cli/main.py`'s own `_DOCTOR_HINT` (mirrors, not
imports, to avoid a module-scope import of `main.py` -- see the module
docstring)."""


@dataclass(frozen=True)
class StageProbe:
    """The cheap, LLM-free result of one stage's `probe`: the queue plus
    the LLM-call count `cost_line` reports (design's Interfaces).

    `unavailable` means the queue itself could not be derived (e.g.
    Preconditions' missing/empty `vectors.db`) -- a user-facing message, set
    only when the stage cannot even attempt to read its own state.
    `empty_message` means the queue WAS derived and is empty -- a distinct
    condition from `unavailable`, since "nothing pending" and "could not
    check" need different notices."""

    items: tuple[object, ...] = ()
    llm_calls: int = 0
    unavailable: str | None = None
    empty_message: str | None = None
    notice: str | None = None
    """A one-line, non-blocking advisory `gate()` echoes to stderr
    immediately before `cost_line` (design D4, #378 slice 2) -- Structure's
    probe sets this to the candidate-edge cap truncation notice when pass 3
    truncated its output; `None` (the default) prints nothing extra."""


@dataclass(frozen=True)
class StageOutcome:
    """Everything one stage's run left behind, for the end-of-run summary
    (design's Interfaces). `status` is the fixed six-way vocabulary every
    stage outcome collapses to; `notice` is the human-readable REMAINDER of
    the line `render_summary` prints for this stage -- it must NOT name its
    own stage, because `render_summary` owns the `"{stage}: "` prefix and
    would otherwise print it twice (issue #504)."""

    status: Literal["applied", "declined", "empty", "unavailable", "failed", "not-live"]
    applied: int = 0
    skipped: int = 0
    notice: str | None = None
    skipped_items: tuple[str, ...] = ()
    """Identities of the items the OPERATOR declined at this stage's
    per-item `[y/N]` prompt (issue #398) -- a strict subset of what
    `skipped` counts, which keeps its pre-#398 meaning and still counts
    every skip branch (no-valid-suggestion, N>2 groups, prepared-None)
    exactly as before. `render_summary` prints one indented line per entry,
    so the operator knows exactly which items to revisit rather than only
    how many there were. Defaults empty so every pre-existing construction
    site stays valid."""


@dataclass(frozen=True)
class Stage:
    """One stage descriptor (design D2). `writes` is a CAPABILITY field, not
    a convenience flag -- it is what lets `run_curate`'s non-TTY policy (D3)
    be decided once, by the framework, rather than restated in every
    stage's own `run`. `halts_run` is true for Preconditions only.
    `live=False` (Structure/Metadata/Contradictions in slice 1) means the
    stage is skipped WITHOUT ever calling `probe`."""

    name: str
    noun: str
    probe: "Callable[[CurateContext], StageProbe]"
    run: "Callable[[CurateContext, StageProbe], StageOutcome]"
    needs_llm: bool = True
    writes: bool = True
    unattended_hint: str | None = None
    halts_run: bool = False
    live: bool = True
    auto_acceptable: bool = False
    """Whether `--accept` (or `review: false`) may turn this stage's
    per-item walk into an accept-all (issue #385).

    Defaults to `False` so the capability is opt-in per stage rather than
    inherited by anything that happens to write. Identity keeps the default
    ON PURPOSE and this is the ONE place that decision lives: a merge
    absorbs one concept into another and DELETES the absorbed file, so no
    flag and no config knob may apply one unreviewed. Making it a
    descriptor field rather than a name check scattered through the CLI is
    what keeps a future writing stage from silently inheriting accept-all
    just by being added to `_STAGES`."""

    accept_caveat: str = ""
    """What `--accept <this stage>` still asks per item, or `""` when it
    holds nothing back (issue #702, gap 1).

    Part of the offer, not a footnote. Structure's bulk path covers only the
    symmetric-scope types -- asymmetric ones and `related_to` keep asking,
    because the engine marks those directions `model-suggested, unverified`
    and cannot vouch for them (#624/#508). A hint that promised to remove
    every prompt would be selling something the flag does not do, and the
    operator would find out mid-run.

    Lives on the descriptor beside `auto_acceptable` for the same reason
    that field does: the two answer "may this stage be bulk-accepted" and
    "what survives if it is", and a future stage that sets the first without
    the second should have to think about it here rather than inherit a
    silence that happens to be wrong."""

    task: str | None = None
    """Which measured task this stage's LLM calls belong to (issue #515), or
    `None` for a stage that makes none.

    Keyed by TASK rather than by stage NAME on purpose: `Structure` and the
    standalone `suggest-relations` verb both run `suggest_edge_types`, and
    naming the task is what routes them to the same `models:` entry. A
    stage-name key would let the two drift onto different models -- the
    drift #385's design already prevents by routing both through one write
    core.

    A `needs_llm` stage that leaves this `None` silently keeps the global
    model; `test_every_llm_stage_declares_the_task_it_spends_on` is what
    turns that into a failing test rather than a quiet wrong-model run."""


@dataclass
class CurateContext:
    """Everything one `curate` invocation threads through every stage.

    Deliberately NOT frozen (unlike `Stage`/`StageProbe`/`StageOutcome`):
    `ollama_client`, `ollama_clients` and `ollama_unavailable_notices` are
    run-scoped mutable state the sequencer fills in as the run progresses
    (design D7's lazy client and short-circuit flag, re-keyed per model by
    #515) -- there is exactly one `CurateContext` per run, built once by the
    `curate` command and threaded by reference, never copied."""

    root: Path
    layout: config.WorkspaceLayout
    cfg: config.Config
    auto: bool = False
    include_confidential: bool = False
    include_deprecated: bool = False
    local_exemption: bool = False
    """Whether a `confidential` concept may be included in this run's
    `llm.chat` payloads because the backend is verifiably this machine
    (issue #240), resolved ONCE by the `curate` command via
    `cli.main._resolve_local_exemption` and threaded to every stage.

    Resolved by the COMMAND rather than lazily alongside `ollama_client`
    because the stage PROBES need it: `_structure_probe`,
    `_concept_type_names` and `_contradiction_plan` all apply the
    sensitivity filter to compute their cost-gate counts, and they run
    before any client is built. A probe that filtered on different terms
    than the run would preview a cost the run does not pay.

    Defaults to `False` so a `CurateContext` built without it -- a test
    fixture, a future caller -- fails closed exactly like every other seam
    in this change."""
    accepted_stages: frozenset[str] = frozenset()
    """Names of the stages whose per-item walk runs as an accept-all this
    run (issue #385), resolved ONCE by `resolve_accepted_stages` from
    `--accept` and `cfg.review`.

    Empty by default, so a context built without it prompts for everything
    -- the pre-#385 behavior. Membership is only ever populated from
    `auto_acceptable` stages, which is why no code downstream re-checks
    whether Identity slipped in: it structurally cannot."""
    no_reconcile: bool = False
    """Whether Identity's merges skip the #645 merged-body reconciliation
    pass (issue #688) -- `curate --no-reconcile`, the same opt-out lever
    `merge` takes, rather than a second differently-named one.

    Defaults to `False` (reconciliation ON) because #645's ruling is
    opt-OUT, and because that is the direction a forgotten thread-through
    should fail: reconciling a merge that did not need it costs one model
    call, while stacking one that did produces the stapled document this
    issue was filed about."""
    ollama_client: LLMBackend | None = field(default=None, init=False)
    """The client for the stage currently running -- reassigned by the
    sequencer before each `needs_llm` stage's `run`.

    Was a build-once, run-scoped client before per-task models (#515). It
    stays a single attribute because every stage `run` reads exactly this
    one, and reshaping four call sites to look up their own client would
    have moved the model decision INTO the stages, where a stage could
    disagree with the model its own cost gate had just disclosed."""
    ollama_clients: dict[str, LLMBackend] = field(default_factory=dict, init=False)
    """Every client built this run, keyed by MODEL TAG (issue #515).

    Keyed by model rather than by stage so the stages that resolve the same
    tag -- every stage, in a workspace with no `models:` override -- share
    one client. Without that sharing, keying availability per model would
    multiply connections by stage count instead of by distinct model."""
    ollama_unavailable_notices: dict[str, str] = field(default_factory=dict, init=False)
    """Which MODELS proved unreachable this run, keyed by tag (#515).

    Replaces a single run-scoped `ollama_unavailable_notice`. That flag
    encoded a claim that stopped being true once stages could run on
    different models: Structure failing for want of `gemma2:27b` says
    nothing about whether Metadata's model is reachable, and skipping
    Metadata on that basis would refuse work that would have succeeded.

    The deliberate cost, stated rather than discovered: against a genuinely
    dead server, a run now pays one failed connection per DISTINCT model
    instead of one per run. Those failures are fast (a refused connection,
    not a timeout), and the alternative is silently skipping stages that had
    nothing to do with the failure."""
    partial_progress: StageOutcome | None = field(default=None, init=False)
    """What a WRITING stage had already applied when an availability failure
    forced it to re-raise instead of return (issue #468 item 4).

    The `OllamaUnavailable`/`OllamaModelNotFound` arms of the partial-batch
    split (#441) stay raise-shaped so the sequencer keeps its run-scoped
    skip of later `needs_llm` stages -- but a raise carries no return value,
    so the `applied`/`skipped` the stage had just counted died with it and
    the sequencer's handler built its outcome with the default `applied=0`.
    A concept could be absorbed and DELETED, Ollama could then go down, and
    the summary would report only the dead server.

    A stage stashes the outcome it WOULD have returned here immediately
    before re-raising; `_availability_outcome` consumes and clears it. Only
    the three writing stages set it -- Contradictions is read-only, so it
    has no destructive work to disclose."""


def cost_line(stage: Stage, probe: StageProbe) -> str:
    """The pinned cost-gate literal (design D3): `{n} {noun}(s) -> {n} LLM
    call(s)`. For Structure's `untyped edge` noun, this is byte-identical
    to the PREFIX of `suggest-relations`' existing line (`main.py:7587`):
    that line reads `f"{total} untyped edge(s) -> {total} LLM call(s), one
    per edge (this can take a while). Pass --auto to skip this prompt."` --
    `cost_line` pins the shared `"{n} {noun}(s) -> {n} LLM call(s)"` prefix
    only, never the standalone verb's trailing "one per edge..."/"Pass
    --auto..." clause, which is specific to that verb's own confirm prompt,
    not `curate`'s `gate()`. `probe.llm_calls` -- not `len(probe.items)` --
    is the one number both halves report: the two coincide for every stage
    this design has pinned so far, but keeping `llm_calls` authoritative is
    what lets a future stage (design's Open Questions: Metadata's exact
    cost unit) report a call count that is NOT a 1:1 count of its queue
    without this helper needing to change."""
    n = probe.llm_calls
    return f"{n} {stage.noun}(s) -> {n} LLM call(s)"


_ACCEPT_HINT_THRESHOLD = 4
"""Queue length at or above which `gate` names `--accept` (issue #702).

Four, not one: a hint printed on every run is a hint that stops being read,
and below four the flag costs more to learn about than it saves. Four is
also comfortably under both stages measured in the 2026-08-14 session that
filed the issue -- Structure at 7 edges and Metadata at 6 concept types --
so the case the issue is actually about clears it without the threshold
having been fitted to those two numbers."""


def accept_notice(stage: Stage, probe: StageProbe, ctx: CurateContext) -> str | None:
    """The one-line `--accept` hint, or `None` when it would be noise
    (issue #702, gap 1).

    `curate`'s wall time is dominated by the operator, not the model: the
    measured session spent 90s of inference inside 310s, and the remaining
    ~220s was answering per-item prompts. The mechanism that fixes that
    already exists and is well designed -- it was simply never mentioned
    while the questions were being asked, so the users who most need it are
    exactly the ones who have not read the full `--help`.

    Four conditions, each of which would make the line wrong rather than
    merely unhelpful:

    - `auto_acceptable` -- Identity is excluded from bulk by construction,
      since its merges DELETE a concept. Offering `--accept identity` would
      advertise a flag that is refused, and advertise it hardest on the one
      stage where accepting unseen does the most damage.
    - not already accepted -- an operator running `--accept structure` is
      told nothing; the hint exists to make an unknown mechanism
      discoverable, not to congratulate someone using it.
    - a queue of at least `_ACCEPT_HINT_THRESHOLD`.
    - a stage that actually WRITES -- a read-only stage asks no per-item
      write questions, so there is nothing for the flag to save there.

    The count is `len(probe.items)`, deliberately not `probe.llm_calls`:
    this line is about QUESTIONS, and `cost_line` right above it is already
    the authority on calls. The two coincide for every stage shipped today
    and would diverge for a stage whose cost unit is not its queue -- at
    which point reporting calls here would misstate what the flag saves.
    """
    if not stage.auto_acceptable or not stage.writes:
        return None
    if stage.name in ctx.accepted_stages:
        return None
    count = len(probe.items)
    if count < _ACCEPT_HINT_THRESHOLD:
        return None
    caveat = f"; {stage.accept_caveat}" if stage.accept_caveat else ""
    return (
        f"openkos curate: {stage.name}: {count} {stage.noun}(s) to review "
        f"one by one. Pass `--accept {stage.name.lower()}` to accept them in "
        f"bulk{caveat}."
    )


def stage_model(ctx: CurateContext, stage: Stage) -> str:
    """The model tag `stage` resolves to this run (issue #515).

    One function so the cost gate, the client cache, the availability map,
    and the `ollama pull` remediation cannot disagree about which model a
    stage is talking to. A gate that disclosed one model while the run
    contacted another would be worse than no disclosure at all."""
    return config.resolve_task_model(ctx.cfg, stage.task)


def model_notice(ctx: CurateContext, stage: Stage) -> str | None:
    """The gate's model-disclosure line, or `None` when there is nothing to
    disclose (issue #515).

    Returns a line ONLY when the stage resolves a model other than the
    global `model:`. Two reasons it is a separate line rather than a suffix
    on `cost_line`, and conditional rather than unconditional:

    - The `curate-command` spec pins the below-cap cost line byte-identical
      ("Below-Cap Cost-Line Output Is Byte-Identical To Pre-Change
      Behavior"). A suffix would rewrite that pinned literal for every
      workspace, including the ones that never opted into `models:`.
    - The disclosure gap #515 identified only EXISTS when a stage runs on
      something other than the default: `74 untyped edge(s) -> 74 LLM
      call(s)` is ~1.6 minutes on `qwen3:8b` and 8-14 on `gemma2:27b`,
      the spread depending on document size (the prompt carries both
      bodies in full, so the input scales while the short JSON reply does
      not -- 6.8 s/edge measured on the harness's ~145-char fixtures,
      11.5 on a bundle averaging ~1.2 KB). When the two models agree, the
      line already describes what is being consented to.

    So a workspace with no `models:` sees today's output byte for byte, and
    one that opted in is told which model its consent is buying."""
    model = stage_model(ctx, stage)
    if model == ctx.cfg.model:
        return None
    return f"openkos curate: {stage.name}: this stage runs on '{model}'."


def upgrade_notice(ctx: CurateContext, stage: Stage) -> str | None:
    """The gate's optional-upgrade line (#650), or `None` when there is
    nothing to recommend.

    Returns a line ONLY when the stage's task has a
    `config.RECOMMENDED_TASK_MODELS` entry AND the workspace never keyed
    that task in `models:`. Keying it -- to any value, including an
    explicit null -- is the operator's decision, and a gate that nags past
    a decision teaches people to stop reading gates. With the #650 default
    inversion this is the surface where the old packaged default used to
    be disclosed, so the recommendation lives exactly where the operator
    is consenting to the spend it would change."""
    task = stage.task
    if task is None:
        return None
    recommended = config.RECOMMENDED_TASK_MODELS.get(task)
    if recommended is None:
        return None
    if task in ctx.cfg.models:
        return None
    model = stage_model(ctx, stage)
    if model == recommended:
        return None
    return (
        f"openkos curate: {stage.name}: runs on '{model}'; the measured "
        f"optional upgrade is '{recommended}' -- set "
        f"`models: {{{task}: {recommended}}}` in openkos.yaml "
        "(a 15.6 GB pull; see docs/cli.md)."
    )


def parse_accepted_stages(accept: str | None) -> frozenset[str] | None:
    """Validate the raw `--accept` value and return the named stage set, or
    `None` when the flag was not passed at all (issue #385).

    PURE vocabulary: this never reads the workspace or the config, so it
    can run BEFORE `require_workspace` and report a typo as itself rather
    than as a missing workspace -- the same ordering `list`'s `TYPE` and
    `set-volatility`'s tier already use.

    Names are matched case-insensitively against `_STAGES`. Two refusals,
    both exit 2 before any work:

    - a name that is not a stage at all, because a typo must never read as
      "accepted";
    - a stage that exists but is not `auto_acceptable` -- today only
      Identity, whose merges delete a concept. The message names the
      acceptable stages rather than only rejecting, since the operator's
      next move is to pick one of them.

    `None` is distinct from `frozenset()`: the first means "not specified,
    fall back to `review`", the second means "specified as nothing", which
    an explicit `--accept ''` legitimately produces."""
    if accept is None:
        return None

    acceptable = {stage.name.lower() for stage in _STAGES if stage.auto_acceptable}
    known = {stage.name.lower(): stage.name for stage in _STAGES}
    requested = [item.strip().lower() for item in accept.split(",") if item.strip()]
    offered = ", ".join(sorted(acceptable))
    for name in requested:
        if name not in known:
            typer.echo(
                f"openkos curate: --accept: unknown stage '{name}' -- "
                f"expected one of: {offered}.",
                err=True,
            )
            raise typer.Exit(code=2)
        if name not in acceptable:
            typer.echo(
                f"openkos curate: --accept: '{name}' cannot be accepted in "
                "bulk -- it applies destructive changes that must be "
                f"reviewed one at a time. Acceptable stages: {offered}.",
                err=True,
            )
            raise typer.Exit(code=2)
    return frozenset(known[name] for name in requested)


def resolve_accepted_stages(
    explicit: frozenset[str] | None, *, review: bool
) -> frozenset[str]:
    """Fold `parse_accepted_stages`' result together with `openkos.yaml`'s
    `review` knob into the set `CurateContext` carries (issue #385).

    `review: false` has meant "do not confirm before saving" in 16 places
    in `main.py` since long before `curate` existed; #385 is the issue that
    finally makes this verb read it. It accepts every auto-acceptable stage
    -- and ONLY those, so a knob set months ago for the standalone verbs
    can never become retroactive authorization to delete a concept today.

    An EXPLICIT `--accept` wins and names the exact set rather than
    widening it: a flag that could only ever add would leave an operator
    running with `review: false` no way to re-review a single stage without
    editing the config file mid-session."""
    if explicit is not None:
        return explicit
    if review:
        return frozenset()
    return frozenset(stage.name for stage in _STAGES if stage.auto_acceptable)


def _accepts(ctx: CurateContext, stage_name: str) -> bool:
    """Whether `stage_name`'s per-item walk runs as an accept-all (#385).

    The one read of `ctx.accepted_stages`, so the three walks never grow
    their own membership logic."""
    return stage_name in ctx.accepted_stages


def _confirm_item(
    ctx: CurateContext,
    stage_name: str,
    prompt_text: str,
    *,
    acceptable_in_bulk: bool = True,
) -> bool:
    """`_confirm`, unless this stage was accepted in bulk for the run, in
    which case the answer is yes and no prompt is printed (issue #385).

    `acceptable_in_bulk=False` exempts ONE item from that acceptance
    (issue #508): the stage is still accepted, but this particular
    suggestion asserts nothing specific, so it is worth the operator's
    glance even in a run that opted out of the rest. On a TTY it falls
    back to the prompt; on a pipe there is no channel to ask on, so it is
    SKIPPED rather than prompted -- reaching `typer.prompt` with no
    terminal would kill the walk mid-run, which is the same failure the
    Identity non-TTY guard exists to prevent.

    Identity calls `_confirm` DIRECTLY rather than routing through here:
    that keeps the merge walk structurally incapable of being skipped, so
    a future edit to the acceptance rules cannot reach it by accident."""
    if _accepts(ctx, stage_name):
        if acceptable_in_bulk:
            return True
        if not sys.stdin.isatty():
            return False
    return _confirm(prompt_text)


def _partial_progress(
    applied: int, skipped: int, declined: Sequence[str]
) -> StageOutcome:
    """The outcome a WRITING stage stashes on `ctx.partial_progress` just
    before re-raising an availability failure (issue #468 item 4).

    `status` is `"unavailable"` because that is what the sequencer's handler
    goes on to report; the payload that matters is the counts, which the
    raise would otherwise discard. `notice` is a COMPLETE sentence, appended
    after the handler's remediation text so the summary line reads as one
    continuous statement."""
    return StageOutcome(
        status="unavailable",
        applied=applied,
        skipped=skipped,
        notice=f"Already applied {applied}, skipped {skipped} before the failure.",
        skipped_items=tuple(declined),
    )


def _availability_outcome(ctx: CurateContext, notice: str) -> StageOutcome:
    """The sequencer's `unavailable` outcome, folding in whatever a partial
    WRITING stage had already applied before it re-raised (#468 item 4).

    Consumes `ctx.partial_progress`, so a later stage's own availability
    notice can never inherit an earlier stage's counts. A stage that raised
    before applying anything leaves it `None` and the notice stays exactly
    as it was before this change."""
    progress = ctx.partial_progress
    ctx.partial_progress = None
    if progress is None:
        return StageOutcome(status="unavailable", notice=notice)
    return StageOutcome(
        status="unavailable",
        applied=progress.applied,
        skipped=progress.skipped,
        notice=f"{notice} {progress.notice}",
        skipped_items=progress.skipped_items,
    )


def gate(stage: Stage, probe: StageProbe, ctx: CurateContext) -> bool:
    """The one spend-consent gate every LLM-costing stage shares (design
    D3, generalizing #134). Prints `cost_line` to stderr, THEN decides:

    - `ctx.auto`: accepted outright -- `--auto` consents to model SPEND,
      never to a per-item write (rule 1, enforced separately by
      `run_curate`'s `writes`x non-TTY policy below, never here).
    - a TTY, no `--auto`: `typer.confirm` asks and returns its answer.
    - non-TTY, no `--auto`: declines unconditionally, with no exception for
      a read-only stage (spec: Non-TTY without --auto declines every
      LLM-costing stage) -- there is no consent channel at all here.

    `probe.notice` is deliberately NOT printed here: `run_curate` echoes it
    the moment the probe returns, so it survives the branches that never
    reach this gate (#378).

    The model-disclosure line (#515) IS printed here, unlike `probe.notice`,
    and for the mirror-image reason: it describes what this consent is
    buying, so it belongs on the branches that ASK. A stage that never
    reaches the gate spends nothing, and has no model spend to disclose."""
    typer.echo(cost_line(stage, probe), err=True)
    notice = model_notice(ctx, stage)
    if notice is not None:
        typer.echo(notice, err=True)
    upgrade = upgrade_notice(ctx, stage)
    if upgrade is not None:
        typer.echo(upgrade, err=True)
    # #702 gap 1: printed HERE, with the other disclosures, because this is
    # the moment it becomes actionable -- the queue is known, the stage has
    # not started, and the operator is being asked to consent to the run
    # that contains the prompts. Naming it after the walk has begun would be
    # a hint about work already done.
    accept = accept_notice(stage, probe, ctx)
    if accept is not None:
        typer.echo(accept, err=True)
    if ctx.auto:
        return True
    if sys.stdin.isatty():
        return typer.confirm("Proceed?")
    typer.echo(
        f"openkos curate: {stage.name}: refusing without confirmation -- "
        "stdin is not a TTY; re-run with --auto.",
        err=True,
    )
    return False


def _confirm(prompt_text: str) -> bool:
    """The one per-item write-consent prompt all three interactive walks --
    Identity, Structure, Metadata -- share (issue #398): loops on
    `typer.prompt(prompt_text, default="N", show_default=False)` until the
    answer is recognized. `y`/`yes` (case/whitespace-insensitive) accepts;
    `n`/`no` or empty input declines -- pressing Enter reaches this loop as
    the prompt's own `"N"` default, so the documented decline default is
    preserved. Any OTHER answer (`t`, `a`, `si`, `1` -- #398's typo
    evidence) echoes a one-line notice naming the accepted tokens and asks
    again, instead of being silently counted as a decline. The advertised
    contract is `[y/N]`: the formerly advertised `skip` token never had
    behavior distinct from a decline, so it is gone from all three call
    sites' prompt strings rather than given one here."""
    while True:
        answer = typer.prompt(prompt_text, default="N", show_default=False)
        token = answer.strip().lower()
        if token in {"y", "yes"}:
            return True
        if token in {"", "n", "no"}:
            return False
        typer.echo(
            f"Unrecognized answer '{answer.strip()}' -- expected y or n "
            "(Enter = N). Asking again."
        )


def _preconditions_probe(ctx: CurateContext) -> StageProbe:
    """Reuses `_open_proximity_or_degrade` (main.py) for the SAME
    missing-or-empty `vectors.db` check `suggest-relations`/`contradictions`
    already make, and `next_action._tier_missing_vector_index`'s own wording
    for the consequence -- byte-compatible messaging, one source of truth
    (design D4/#266 task 1.17). Never returns items: Preconditions is a
    binary gate, not a queue, so it is resolved entirely via the
    `unavailable`/empty-queue branches in `run_curate`, never via `gate`."""
    from openkos.cli import main as cli_main

    source = cli_main._open_proximity_or_degrade(ctx.layout.vectors_db_path)
    if source is None:
        signals = next_action_module._BundleSignals(ctx.layout)
        action = next_action_module._tier_missing_vector_index(signals)
        reason = (
            action.reason
            if action is not None
            else (
                "Dense retrieval and candidate edges are unavailable -- the "
                "vector index is missing or empty."
            )
        )
        return StageProbe(unavailable=f"{reason} Run `openkos reindex`.")
    source.close()
    return StageProbe()


def _preconditions_run(ctx: CurateContext, probe: StageProbe) -> StageOutcome:
    """Structurally unreachable through `run_curate`: `_preconditions_probe`
    never returns items, so the sequencer always resolves Preconditions via
    the `unavailable`/empty branches, before `gate` or `run` is ever
    reached. Kept only to satisfy `Stage`'s required `run` field and for
    direct unit coverage of the (never-exercised) fallback."""
    return StageOutcome(status="empty")


def _identity_probe(ctx: CurateContext) -> StageProbe:
    """`resolution.find_candidates_report` (design D1/D4) -- one LLM call
    per RETAINED candidate group, since `adjudicate_candidates` issues
    exactly one `llm.chat` per group (`resolution/adjudication.py`).
    `_MAX_CANDIDATE_GROUPS` bounds `probe.llm_calls` transitively
    (entity-resolution delta: Bounded Candidate-Group Output Per Call);
    `probe.notice` carries `candidate_group_truncation_notice`'s "N of M
    ... shown (cap reached)" disclosure when `report.produced >
    report.retained`, and is `None` (unchanged wiring) otherwise
    (curate-command delta: Identity Cost Line Discloses Truncation,
    Below-Cap Cost-Line Output Is Byte-Identical To Pre-Change Behavior)."""
    report = find_candidates_report(
        ctx.layout.bundle_dir, include_deprecated=ctx.include_deprecated
    )
    return StageProbe(
        items=tuple(report.groups),
        llm_calls=report.retained,
        empty_message="No candidate groups found." if not report.groups else None,
        notice=candidate_group_truncation_notice(report),
    )


def _identity_run(ctx: CurateContext, probe: StageProbe) -> StageOutcome:
    """`adjudicate_candidates` then, per SAME 2-member group, the exact
    `_prepare_one_merge` / preview / `[y/N]` / `_reject_drifted_targets`
    / `_commit_one_merge` walk `adjudicate --apply` already performs (design
    D4/D6) -- reused verbatim rather than re-implemented, so the two write
    paths can never drift apart. N>2 groups are never auto-merged
    (`_echo_n_gt2_skip` prints the exact pairwise `openkos merge` commands,
    spec: N>2 group prints pairwise commands, never auto-merges).

    A drift refusal (`_reject_drifted_targets`) raises `typer.Exit(code=3)`
    and is deliberately let propagate all the way out of `run_curate` --
    unlike an `OllamaError`, drift is terminal for the WHOLE RUN (design D6):
    it proves the workspace is racing, so every later stage's plan would be
    computed from a state already disproved. A mid-run `(OSError, ValueError)`
    write failure stops the loop immediately too, mirroring
    `_run_adjudicate_apply`'s own documented behavior -- prior commits stay
    intact and reversible via `unmerge`.

    A PARTIAL `AdjudicationBatch` (#441) keeps its completed verdicts: the
    walk below runs over `batch.results` exactly as over a complete run --
    the merge cores need no model, so a dead server cannot invalidate
    verdicts already paid for. Only then is the failure surfaced, split by
    class: `OllamaUnavailable`/`OllamaModelNotFound` are RE-RAISED so the
    sequencer's existing handlers keep their run-scoped skip (later
    `needs_llm` stages must not ask the operator to spend against a server
    this stage just proved dead/misconfigured), while the rest of the
    `OllamaError` family returns a `failed` outcome with completed-of-total
    counts, leaving later stages to run -- the same fails-only-this-stage
    scope the sequencer's generic handler already pins."""
    from openkos.cli import main as cli_main

    layout = ctx.layout
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    # `StageProbe.items` is deliberately `tuple[object, ...]` (one probe
    # shape for all five stages); Identity's own probe only ever queues
    # `CandidateGroup`s, so this narrowing filter is an identity function
    # at runtime -- it exists for the type system, not to drop items. The
    # client is the sequencer's `needs_llm` invariant: it is always built
    # before a `needs_llm` stage's `run` is invoked.
    groups = [item for item in probe.items if isinstance(item, CandidateGroup)]
    llm = ctx.ollama_client
    if llm is None:  # pragma: no cover -- sequencer invariant (needs_llm)
        raise RuntimeError("Identity stage requires an LLM client")
    batch = adjudicate_candidates(
        groups,
        bundle_dir=layout.bundle_dir,
        llm=llm,
        include_confidential=ctx.include_confidential,
        local_exemption=ctx.local_exemption,
        on_progress=observability.progress_callback("curate", "adjudicating group"),
    )
    results: Sequence[AdjudicatedCandidate] = batch.results

    applied = 0
    skipped = 0
    declined: list[str] = []
    for result in results:
        group = result.candidate
        if result.verdict is not Verdict.SAME:
            continue
        if len(group.member_ids) != 2:
            if len(group.member_ids) > 2:
                cli_main._echo_n_gt2_skip(layout.bundle_dir, group)
                skipped += 1
            continue

        # #776: the pair is ordered ONCE (richer body survives), displayed,
        # and pinned through the prepare -- never re-derived from live
        # bodies between the preview and the write.
        survivor_id, absorbed_id, survivor_criterion = cli_main._ordered_merge_pair(
            layout.bundle_dir, group.member_ids
        )
        try:
            prepared = cli_main._prepare_one_merge(
                ctx.root,
                layout,
                index_path,
                log_path,
                group,
                ordered_pair=(survivor_id, absorbed_id),
            )
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos curate: Identity: failed while merging "
                f"{absorbed_id} into {survivor_id} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        if prepared is None:
            skipped += 1
            continue

        typer.echo(
            cli_main._format_merge_preview_line(prepared, no_reconcile=ctx.no_reconcile)
        )
        # #776: the survivor rule is deterministic and STATED, and the
        # risky cross-source class is named BEFORE the prompt -- the same
        # two disclosures `adjudicate --apply` renders, via the same
        # helpers and the same shared note constant, so the recommended
        # path is never the less-informed one.
        typer.echo(f"  survivor: {prepared.survivor_canonical} ({survivor_criterion})")
        if cli_main._cross_source_same_pair(layout.bundle_dir, group.member_ids):
            typer.echo(cli_main._CROSS_SOURCE_WALK_NOTE)
        if not _confirm(
            f"Merge {prepared.absorbed_canonical} into "
            f"{prepared.survivor_canonical}? [y/N]"
        ):
            skipped += 1
            declined.append(
                f"{prepared.absorbed_canonical} -> {prepared.survivor_canonical}"
            )
            continue

        # #688: the reconciliation runs AFTER this item's consent (the
        # preview disclosed it) and BEFORE the drift re-check, so the model
        # call sits inside the window the guard re-validates -- the exact
        # ordering `merge` uses, via the exact same helper.
        prepared = cli_main._apply_reconciliation(
            ctx.root, prepared, no_reconcile=ctx.no_reconcile, verb="curate"
        )

        absorbed_path = layout.bundle_dir / f"{prepared.absorbed_canonical}.md"
        cli_main._reject_drifted_targets(
            layout,
            cli_main._merge_drift_targets(layout, prepared),
            "curate",
            deletes=frozenset({absorbed_path}),
        )

        try:
            cli_main._commit_one_merge(ctx.root, layout, index_path, log_path, prepared)
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos curate: Identity: failed while merging "
                f"{prepared.absorbed_canonical} into "
                f"{prepared.survivor_canonical} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        applied += 1

    if isinstance(batch.failure, OllamaUnavailable | OllamaModelNotFound):
        # Availability failures stay raise-shaped so the sequencer's handler
        # keeps its run-scoped skip of later `needs_llm` stages; the walk
        # above already ran, so the completed verdicts survive (#441).
        # The raise carries no return value, so hand the counts to the
        # sequencer through `ctx` -- merges here DELETE a concept, and a
        # notice about a dead server must not be the only record of that
        # (#468 item 4).
        ctx.partial_progress = _partial_progress(applied, skipped, declined)
        raise batch.failure
    if batch.failure is not None:
        return StageOutcome(
            status="failed",
            applied=applied,
            skipped=skipped,
            notice=(
                f"failed -- {batch.failure} (adjudicated "
                f"{len(batch.results)} of {len(groups)} candidate group(s); "
                f"applied {applied}, skipped {skipped})."
            ),
            skipped_items=tuple(declined),
        )

    status: Literal["applied", "empty"] = "applied" if applied or skipped else "empty"
    return StageOutcome(
        status=status,
        applied=applied,
        skipped=skipped,
        notice=f"applied {applied}, skipped {skipped}.",
        skipped_items=tuple(declined),
    )


def _structure_probe(ctx: CurateContext) -> StageProbe:
    """`build_graph(..., candidates=source)` + `candidate_edges` (design
    D4): the SAME pre-flight surface `suggest-relations` counts to bound
    cost before committing to `suggest_edge_types`'s one-`llm.chat`-per-edge
    run (issue #134) -- no LLM call, graph-projection-reuse (#196) closes
    the proximity source as soon as `build_graph` consumes it."""
    from openkos.cli import main as cli_main

    source = cli_main._open_proximity_or_degrade(ctx.layout.vectors_db_path)
    try:
        graph = build_graph(ctx.layout.bundle_dir, candidates=source)
    finally:
        if source is not None:
            source.close()

    with graph as store:
        edges = candidate_edges(
            ctx.layout.bundle_dir,
            include_confidential=ctx.include_confidential,
            local_exemption=ctx.local_exemption,
            store=store,
        )
        # #378 slice 2 (post-review correction): pass 3's candidate-edge cap
        # truncation, never silent -- read here, INSIDE the `with` block,
        # since `store` closes below. `store.candidate_report.produced`/
        # `.retained` are RAW, unfiltered by pass 3 itself;
        # `candidate_truncation_notice` re-derives both from
        # `report.pairs` through the SAME `sensitivity
        # .sensitive_concept_ids` walk `candidate_edges` above just ran, so
        # this notice never discloses a pre-cap volume that includes a
        # confidential endpoint the `edges` queue above already excluded.
        notice = candidate_truncation_notice(
            store.candidate_report,
            ctx.layout.bundle_dir,
            include_confidential=ctx.include_confidential,
            local_exemption=ctx.local_exemption,
        )

    return StageProbe(
        items=tuple(edges),
        llm_calls=len(edges),
        empty_message="No untyped edges found." if not edges else None,
        notice=notice,
    )


def _structure_run(ctx: CurateContext, probe: StageProbe) -> StageOutcome:
    """`suggest_edge_types` with `on_progress` (design D4), then a per-item
    `[y/N]` walk writing every accepted suggestion through the
    extracted `relate` core (`prepare_relate`/`relate_core`, design D5) --
    the exact same write path standalone `relate` uses, so the two can
    never drift apart. A degraded suggestion (`suggested_type=None`) is
    reported and skipped without a prompt (spec: Structure Stage Writes
    Through The Relate Core).

    A PARTIAL `EdgeSuggestionBatch` (#441) keeps its completed suggestions:
    the walk below runs over `batch.results` exactly as over a complete run
    -- the relate core needs no model, so a dead server cannot invalidate
    suggestions already paid for. Only then is the failure surfaced, split
    by class: `OllamaUnavailable`/`OllamaModelNotFound` are RE-RAISED so
    the sequencer's existing handlers keep their run-scoped skip (later
    `needs_llm` stages must not ask the operator to spend against a server
    this stage just proved dead/misconfigured), while the rest of the
    `OllamaError` family returns a `failed` outcome, leaving later stages
    to run -- the same fails-only-this-stage scope the sequencer's generic
    handler already pins. Unlike Identity's failed notice, this one also
    carries the walk's applied/skipped counts (issue #468 follow-up 4): the
    walk WRITES accepted relations through `relate_core` before the failure
    surfaces, so a notice reporting only how much was suggested would hide
    how much was written."""
    from openkos.cli import main as cli_main

    edges = [item for item in probe.items if isinstance(item, Edge)]
    llm = ctx.ollama_client
    if llm is None:  # pragma: no cover -- sequencer invariant (needs_llm)
        raise RuntimeError("Structure stage requires an LLM client")

    if _accepts(ctx, "Structure"):
        # #513: `evals/edge_typing/` measures this suggester emitting a
        # SPECIFIC type correctly about 60% of the time, so roughly two in
        # five bulk-written types are wrong by the rubric. The operator
        # asked for bulk acceptance and keeps it; what they do not keep is
        # going in blind. Since #624 the bulk path covers only the
        # symmetric-scope types -- asymmetric ones and `related_to` always
        # ask per item -- so the advisory names that split. Once per run,
        # on stderr, so a piped summary stays clean.
        typer.echo(
            "openkos curate: Structure: symmetric suggested relation types "
            "are applied without review -- accuracy is measured, not "
            "assumed (see evals/edge_typing/ and issue #513). Asymmetric "
            "types and related_to still ask per item (issue #624). Re-run "
            "without `--accept structure` to decide each one.",
            err=True,
        )

    batch = suggest_edge_types(
        edges,
        bundle_dir=ctx.layout.bundle_dir,
        llm=llm,
        include_confidential=ctx.include_confidential,
        local_exemption=ctx.local_exemption,
        on_progress=observability.progress_callback("curate", "untyped edge"),
    )
    suggestions: Sequence[EdgeSuggestion] = batch.results

    layout = ctx.layout
    log_path = layout.bundle_dir / "log.md"
    now = datetime.now(UTC)
    applied = 0
    skipped = 0
    declined: list[str] = []

    for suggestion in suggestions:
        edge = suggestion.edge
        if suggestion.suggested_type is None:
            typer.echo(f"[?] {edge.source_id} -> {edge.target_id}")
            typer.echo("  note: no valid type suggested")
            skipped += 1
            continue

        typer.echo(
            f"[{suggestion.suggested_type}] {edge.source_id} -> {edge.target_id}"
        )
        typer.echo(f"  rationale: {suggestion.rationale}")
        # #624: an asymmetric suggestion carries no direction evidence --
        # #613 measured both models answering the SAME asymmetric type with
        # SOURCE/TARGET swapped on nearly every edge -- so its consent line
        # says so, and it is never bulk-acceptable.
        asymmetric = suggestion.suggested_type in ASYMMETRIC_RELATION_TYPES
        direction_caveat = (
            " (direction model-suggested, unverified)" if asymmetric else ""
        )
        if not _confirm_item(
            ctx,
            "Structure",
            f"Relate {edge.source_id} -> {edge.target_id} "
            f"[{suggestion.suggested_type}]{direction_caveat}? [y/N]",
            # #508: the least-specific type asserts nothing beyond the
            # untyped link that already existed, so it still reaches a
            # human even in a bulk-accepted run; #624 extends the same
            # routing to every asymmetric type, whose direction is
            # unverified. See `edge_typing.LEAST_SPECIFIC_RELATION_TYPE`
            # and `relations.ASYMMETRIC_RELATION_TYPES`.
            acceptable_in_bulk=(
                suggestion.suggested_type != LEAST_SPECIFIC_RELATION_TYPE
                and not asymmetric
            ),
        ):
            skipped += 1
            declined.append(
                f"{edge.source_id} -> {edge.target_id} [{suggestion.suggested_type}]"
            )
            continue

        source_path = okf.concept_path_for(edge.source_id, layout.bundle_dir)
        try:
            prepared = cli_main.prepare_relate(
                source_path,
                log_path,
                edge.source_id,
                edge.target_id,
                suggestion.suggested_type,
                ctx.root,
                now=now,
            )
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos curate: Structure: failed while relating "
                f"{edge.source_id} -> {edge.target_id} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc

        cli_main._reject_drifted_targets(
            layout,
            {source_path: prepared.source_bytes, log_path: prepared.log_bytes},
            "curate",
        )

        try:
            cli_main.relate_core(source_path, log_path, prepared)
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos curate: Structure: failed while relating "
                f"{edge.source_id} -> {edge.target_id} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc

        cli_main._autocommit(
            ctx.root,
            [f"bundle/{edge.source_id}.md", "bundle/log.md"],
            f"openkos: relate {edge.source_id} -> {edge.target_id} "
            f"({suggestion.suggested_type})",
        )
        applied += 1

    if isinstance(batch.failure, OllamaUnavailable | OllamaModelNotFound):
        # Availability failures stay raise-shaped so the sequencer's handler
        # keeps its run-scoped skip of later `needs_llm` stages; the walk
        # above already ran, so the accepted writes survive (#441). The
        # raise carries no return value, so hand the counts to the
        # sequencer through `ctx` (#468 item 4).
        ctx.partial_progress = _partial_progress(applied, skipped, declined)
        raise batch.failure
    if batch.failure is not None:
        return StageOutcome(
            status="failed",
            applied=applied,
            skipped=skipped,
            notice=(
                f"failed -- {batch.failure} (suggested "
                f"{len(batch.results)} of {len(edges)} untyped edge(s); "
                f"applied {applied}, skipped {skipped})."
            ),
            skipped_items=tuple(declined),
        )

    status: Literal["applied", "empty"] = "applied" if applied or skipped else "empty"
    return StageOutcome(
        status=status,
        applied=applied,
        skipped=skipped,
        notice=f"applied {applied}, skipped {skipped}.",
        skipped_items=tuple(declined),
    )


def _concept_type_names(ctx: CurateContext) -> list[str]:
    """The cheap, LLM-free pre-flight count Metadata's `cost_line` needs
    (design D4): every distinct, non-blank `type` present among
    `lint.collect_docs`' readable/parseable docs, after the SAME
    sensitivity fail-closed filter (S3b) `suggest_volatility` itself
    applies -- mirroring `candidate_edges`' role for Structure. This is a
    genuine re-derivation, not a cached count: it re-walks the bundle every
    time `run_curate` reaches Metadata (design D4's no-memoization rule)."""
    blocked = sensitivity.sensitive_concept_ids(
        ctx.layout.bundle_dir,
        include_confidential=ctx.include_confidential,
        local_exemption=ctx.local_exemption,
    )
    docs, _skip_notices = lint.collect_docs(ctx.layout.bundle_dir)
    return sorted(
        {doc.type for doc in docs if doc.type and doc.identity not in blocked}
    )


def _sensitivity_gap_ids(bundle_dir: Path) -> frozenset[str]:
    """Concept ids with NO `sensitivity` frontmatter key at all (design D4:
    Metadata's report-only sensitivity gap) -- a strictly narrower set than
    `sensitivity.sensitive_concept_ids` (which also folds in blank/
    unrecognized/confidential values, since THAT predicate answers "does
    this block an LLM send", not "is this literally unset"). An unreadable
    or unparseable doc is skipped, never reported as a gap: this is a
    report, not a fail-closed send gate, so a doc this cannot even read
    contributes no signal either way."""
    gaps: set[str] = set()
    for scan in okf._iter_docs(bundle_dir):
        if scan.read_error is not None or scan.parse_error is not None:
            continue
        if (scan.metadata or {}).get("sensitivity") is None:
            cid = okf.concept_id_for(scan.path, bundle_dir)
            gaps.add(cid)
    return frozenset(gaps)


def _metadata_probe(ctx: CurateContext) -> StageProbe:
    """`lint.collect_docs` + `cfg.type_tiers` (design D4): the queue is
    every distinct concept TYPE `suggest_volatility` would sample -- one
    `llm.chat` call per type, never per concept (module docstring of
    `resolution.volatility_typing`).

    The sensitivity-gap notice rides the EMPTY branch too (review
    correction, R3-01 CRITICAL): `_concept_type_names` and
    `_sensitivity_gap_ids` key off the same fail-closed sensitivity set, so
    the one bundle shape the gap report exists to flag -- every
    under-specified doc excluded from the type list precisely BECAUSE its
    `sensitivity` is unset -- used to empty the probe, and `run_curate`'s
    empty-queue short-circuit then returned before `_metadata_run` (the
    only place the notice printed). Folding the notice into
    `empty_message` keeps the report reachable without touching the frozen
    sequencer: the empty branch already prints this message verbatim."""
    type_names = _concept_type_names(ctx)
    empty_message: str | None = None
    if not type_names:
        empty_message = "No concept types found."
        gaps = _sensitivity_gap_ids(ctx.layout.bundle_dir)
        if gaps:
            gap_list = ", ".join(sorted(gaps))
            empty_message += (
                f" Sensitivity unset on: {gap_list} -- set it with "
                "`openkos set-sensitivity`."
            )
    return StageProbe(
        items=tuple(type_names),
        llm_calls=len(type_names),
        empty_message=empty_message,
    )


def _metadata_run(ctx: CurateContext, probe: StageProbe) -> StageOutcome:
    """`suggest_volatility` with `on_progress` (design D4), then a per-type
    `[y/N]` walk writing every accepted tier through the extracted
    `set-volatility` core (`prepare_set_volatility`/`set_volatility_core`,
    design D5). Sensitivity gaps surfaced in the SAME pass (`
    _sensitivity_gap_ids`) are reported only, naming `openkos
    set-sensitivity`, and never written here (spec: Metadata Stage Writes
    Tiers, Reports Sensitivity).

    A PARTIAL `TierSuggestionBatch` (#441) keeps its completed suggestions:
    the walk below runs over `batch.results` exactly as over a complete run
    -- the set-volatility core needs no model, so a dead server cannot
    invalidate suggestions already paid for -- and the sensitivity-gap
    report still prints, since it reads the bundle, never the model. Only
    then is the failure surfaced, split by class:
    `OllamaUnavailable`/`OllamaModelNotFound` are RE-RAISED so the
    sequencer's existing handlers keep their run-scoped skip (later
    `needs_llm` stages must not ask the operator to spend against a server
    this stage just proved dead/misconfigured), while the rest of the
    `OllamaError` family returns a `failed` outcome, leaving later stages
    to run -- the same fails-only-this-stage scope the sequencer's generic
    handler already pins. Like Structure's failed notice (issue #468
    follow-up 4), this one carries the walk's applied/skipped counts
    alongside completed-of-total: the walk WRITES accepted tiers through
    `set_volatility_core` before the failure surfaces, so a notice
    reporting only how much was suggested would hide how much was written.
    The of-total names the probe's type queue -- the count the operator's
    cost gate consented to."""
    from openkos.cli import main as cli_main

    type_names = [item for item in probe.items if isinstance(item, str)]
    llm = ctx.ollama_client
    if llm is None:  # pragma: no cover -- sequencer invariant (needs_llm)
        raise RuntimeError("Metadata stage requires an LLM client")

    batch = suggest_volatility(
        ctx.layout.bundle_dir,
        llm=llm,
        include_confidential=ctx.include_confidential,
        local_exemption=ctx.local_exemption,
        on_progress=observability.progress_callback("curate", "concept type"),
    )
    results: Sequence[TierSuggestion] = batch.results

    applied = 0
    skipped = 0
    declined: list[str] = []
    for result in results:
        if result.suggested_tier is None:
            typer.echo(f"[?] {result.type_name}")
            typer.echo("  note: no valid tier suggested")
            skipped += 1
            continue

        typer.echo(f"[{result.suggested_tier}] {result.type_name}")
        typer.echo(f"  rationale: {result.rationale}")
        if not _confirm_item(
            ctx,
            "Metadata",
            f"Set {result.type_name} -> {result.suggested_tier}? [y/N]",
        ):
            skipped += 1
            declined.append(f"{result.type_name} -> {result.suggested_tier}")
            continue

        try:
            prepared = cli_main.prepare_set_volatility(
                ctx.layout.config_path, result.type_name, result.suggested_tier
            )
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos curate: Metadata: failed while setting volatility "
                f"for {result.type_name} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc

        cli_main._reject_drifted_targets(
            ctx.layout, {ctx.layout.config_path: prepared.config_bytes}, "curate"
        )

        try:
            cli_main.set_volatility_core(ctx.layout.config_path, prepared)
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos curate: Metadata: failed while setting volatility "
                f"for {result.type_name} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc

        cli_main._autocommit(
            ctx.root,
            ["openkos.yaml"],
            f"openkos: set-volatility {result.type_name} -> {result.suggested_tier}",
        )
        applied += 1

    for concept_id in sorted(_sensitivity_gap_ids(ctx.layout.bundle_dir)):
        typer.echo(
            f"Metadata: sensitivity gap -- {concept_id} has no sensitivity "
            f"set. Run `openkos set-sensitivity {concept_id} <level>`."
        )

    if isinstance(batch.failure, OllamaUnavailable | OllamaModelNotFound):
        # Availability failures stay raise-shaped so the sequencer's handler
        # keeps its run-scoped skip of later `needs_llm` stages; the walk
        # above already ran, so the accepted writes survive (#441). The
        # raise carries no return value, so hand the counts to the
        # sequencer through `ctx` (#468 item 4).
        ctx.partial_progress = _partial_progress(applied, skipped, declined)
        raise batch.failure
    if batch.failure is not None:
        return StageOutcome(
            status="failed",
            applied=applied,
            skipped=skipped,
            notice=(
                f"failed -- {batch.failure} (suggested "
                f"{len(batch.results)} of {len(type_names)} concept type(s); "
                f"applied {applied}, skipped {skipped})."
            ),
            skipped_items=tuple(declined),
        )

    status: Literal["applied", "empty"] = "applied" if applied or skipped else "empty"
    return StageOutcome(
        status=status,
        applied=applied,
        skipped=skipped,
        notice=f"applied {applied}, skipped {skipped}.",
        skipped_items=tuple(declined),
    )


def _contradiction_plan(ctx: CurateContext) -> CandidatePlan:
    """The cheap, LLM-free pre-flight plan Contradictions' `cost_line` needs
    (design D4): the EXACT candidate list `_contradictions_run`'s
    `find_contradictions` will judge, built by the same `plan_candidates`
    call it uses -- no `llm.chat` call.

    Issue #446: this used to count `_pairs_and_types` alone, i.e. typed-edge
    pairs only, while the run judged typed-edge PLUS merged-body candidates.
    The gate therefore understated the spend by the bundle's merge-ledger
    count, and `--auto` consented to a number smaller than what was paid.
    Sharing `plan_candidates` makes the two the same number by construction.

    The graph is still built fresh here rather than carried over to `run`
    (design D4's no-memoization rule is about STATE CARRIED BETWEEN STAGES,
    not a ban on this stage reading its own bundle twice in one pass)."""
    from openkos.cli import main as cli_main

    source = cli_main._open_proximity_or_degrade(ctx.layout.vectors_db_path)
    try:
        graph = build_graph(ctx.layout.bundle_dir, candidates=source)
    finally:
        if source is not None:
            source.close()

    with graph as store:
        return plan_candidates(
            ctx.layout.bundle_dir,
            store=store,
            include_deprecated=ctx.include_deprecated,
            include_confidential=ctx.include_confidential,
            local_exemption=ctx.local_exemption,
        )


def _contradictions_probe(ctx: CurateContext) -> StageProbe:
    """`build_graph` + `find_contradictions`'s own candidate planning (design
    D4), counted via `_contradiction_plan` with no `llm.chat` call --
    Contradictions runs LAST, so this never affects an earlier stage's queue.

    `llm_calls` is `plan.llm_calls`, the length of the SAME already-capped
    spec list the run will judge, so the cost line cannot understate the
    spend (issue #446).

    `notice` carries the per-kind cap-truncation line (issue #450), mirroring
    what `_structure_probe` already does with `candidate_truncation_notice`.
    `gate()` echoes it immediately before `cost_line`, so an operator learns
    that candidates were dropped BEFORE consenting to the spend -- reporting
    it only from `run` told them after the calls it describes had been paid
    for."""
    plan = _contradiction_plan(ctx)
    return StageProbe(
        items=plan.specs,
        llm_calls=plan.llm_calls,
        empty_message="No candidate pairs found." if not plan.specs else None,
        notice=contradiction_truncation_notice(plan),
    )


def finding_input_digests(
    bundle_dir: Path, spec: _CandidateSpec
) -> tuple[findings.InputDigest, ...]:
    """Per-input `(input_ref, sha256)` rows for one judged candidate
    (durable-pending-work design, Decision 2's input table):

    - **typed-edge** (`spec.merge_entry is None`): both concept files' raw
      bytes, plus the `relation_type` label string -- it is in the prompt
      (`resolution.contradiction._build_messages`), so re-labelling an edge
      must invalidate the verdict too. An unreadable concept file (a race
      with a later mutation) simply contributes no row for that input,
      mirroring `_load_doc`'s own degrade-not-crash posture -- this is a
      digest computation, not a re-judgment, and must never raise.
    - **merged-body** (`spec.merge_entry is not None`): the ledger entry's
      OWN `survivor_before`/`absorbed_snapshot` strings, digested in
      memory -- never a second file read, since those strings are the
      exact bytes the verdict was computed from."""
    if spec.merge_entry is not None:
        survivor_id, _ = spec.pair_ids
        entry = spec.merge_entry
        return (
            findings.InputDigest(
                f"{survivor_id}:survivor_before",
                content_hash(entry.survivor_before.encode("utf-8")),
            ),
            findings.InputDigest(
                f"{entry.absorbed_id}:absorbed_snapshot",
                content_hash(entry.absorbed_snapshot.encode("utf-8")),
            ),
        )

    digests: list[findings.InputDigest] = []
    for concept_id in spec.pair_ids:
        try:
            raw_bytes = okf.concept_path_for(concept_id, bundle_dir).read_bytes()
        except OSError:
            continue
        digests.append(findings.InputDigest(concept_id, content_hash(raw_bytes)))
    if spec.relation_type is not None:
        digests.append(
            findings.InputDigest(
                "relation_type", content_hash(spec.relation_type.encode("utf-8"))
            )
        )
    return tuple(digests)


def persist_findings(
    layout: config.WorkspaceLayout,
    plan: CandidatePlan,
    verdicts: Sequence[ContradictionVerdict],
) -> None:
    """Record every judged verdict to `.openkos/findings.db` (pending-work
    spec: "Contradiction Findings Are Persisted With Provenance"; curate-
    command spec delta: "Persisting a finding is not a bundle write" -- this
    writes ONLY under `.openkos/`, never `bundle/`).

    `plan.specs`/`verdicts` share the SAME deterministic order
    (`find_contradictions`'s own contract), so `zip` pairs each verdict with
    the candidate it was judged from without re-deriving anything; a
    partial batch (#441) yields fewer `verdicts` than `specs`, and `zip`
    stops there -- exactly the already-judged prefix, never a mismatch.
    An empty `verdicts` list opens no connection at all.

    Public since #653: the `contradictions` verb persists its own fresh
    verdicts through this exact write path, so the store the two verbs
    share can never diverge in shape or digest convention."""
    batch = [
        findings.Finding(
            pair_ids=verdict.pair_ids,
            merged_absorbed_id=verdict.merged_absorbed_id,
            verdict=verdict.verdict.value,
            confidence=verdict.confidence,
            rationale=verdict.rationale,
            input_digests=finding_input_digests(layout.bundle_dir, spec),
            conflicting_claims=verdict.conflicting_claims,
        )
        for spec, verdict in zip(plan.specs, verdicts, strict=False)
    ]
    if not batch:
        return
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        findings.record_findings(conn, batch)
    finally:
        conn.close()


def _contradictions_run(ctx: CurateContext, probe: StageProbe) -> StageOutcome:
    """`find_contradictions` with `on_progress` (design D4): report-only
    and terminal -- never proposes or performs a write (spec: Contradictions
    Stage Is Report-Only And Last).

    The plan is rebuilt here rather than carried from `probe` (design D4's
    no-memoization rule) and handed to `find_contradictions`, so the
    cap-reached line names WHICH KIND was truncated (#444) and describes the
    exact list that was judged.

    No `store` is passed alongside it, and no graph is built here beyond the
    one `_contradiction_plan` builds internally (issue #450):
    `find_contradictions` reads `store` ONLY inside its `if plan is None:`
    branch, so a second `build_graph` purely to supply that argument would be
    a full in-memory SQLite build over the bundle for no effect.

    A PARTIAL `ContradictionBatch` (#441) keeps its completed verdicts: the
    report below runs over `batch.results` exactly as over a complete run
    -- they are already-paid-for reports, and this stage never writes. Only
    then is the failure surfaced, split by class:
    `OllamaUnavailable`/`OllamaModelNotFound` are RE-RAISED so the
    sequencer's existing handlers keep their run-scoped skip wording
    uniform (this stage is last, so there is no later stage to protect --
    the split exists so all four batch stages fail in the same shapes),
    while the rest of the `OllamaError` family returns a `failed` outcome.
    The stage is READ-ONLY, so unlike Structure/Metadata the failed notice
    carries completed-of-total counts only, never applied/skipped write
    counts (issue #468 follow-up 4: write counts belong only where the walk
    writes). The of-total is `plan.llm_calls` -- the same number the cost
    gate stated and the truncation notice describes."""
    llm = ctx.ollama_client
    if llm is None:  # pragma: no cover -- sequencer invariant (needs_llm)
        raise RuntimeError("Contradictions stage requires an LLM client")

    plan = _contradiction_plan(ctx)

    batch, _total_pairs = find_contradictions(
        ctx.layout.bundle_dir,
        llm=llm,
        include_deprecated=ctx.include_deprecated,
        include_confidential=ctx.include_confidential,
        local_exemption=ctx.local_exemption,
        plan=plan,
        on_progress=observability.progress_callback("curate", "pair"),
    )
    verdicts: Sequence[ContradictionVerdict] = batch.results

    truncation = contradiction_truncation_notice(plan)
    if truncation is not None:
        typer.echo(f"Contradictions: {truncation}")

    from openkos.cli import main as cli_main

    high_confidence: list[ContradictionVerdict] = [
        v for v in verdicts if is_high_confidence_contradiction(v)
    ]
    # pending-work spec ("Declined Findings Are Hidden By Default"): a
    # verdict whose decision_key already carries a `declined` decision is
    # dropped from the DISPLAY list only -- it is still judged and still
    # persisted below, mirroring `main._run_contradictions`'s own `displayed`
    # filter over `_is_contradiction_declined`, so the two `curate`/
    # `contradictions` echo paths cannot drift apart on this rule.
    displayed = [
        v
        for v in high_confidence
        if not cli_main._is_contradiction_declined(
            ctx.layout, v.pair_ids, v.merged_absorbed_id
        )
    ]
    for verdict in displayed:
        source_id, target_id = verdict.pair_ids
        typer.echo(
            f"[{verdict.verdict.value.upper()}] {source_id} <-> {target_id} "
            f"(confidence: {verdict.confidence:.2f})"
        )
        for claim in verdict.conflicting_claims:
            typer.echo(f"  - {claim}")
        typer.echo(f"  rationale: {verdict.rationale}")

    persist_findings(ctx.layout, plan, verdicts)

    if isinstance(batch.failure, OllamaUnavailable | OllamaModelNotFound):
        # Availability failures stay raise-shaped so the sequencer's handler
        # keeps its run-scoped skip of later `needs_llm` stages; the report
        # above already ran, so the completed verdicts survive (#441).
        raise batch.failure
    if batch.failure is not None:
        return StageOutcome(
            status="failed",
            applied=len(high_confidence),
            notice=(
                f"failed -- {batch.failure} (judged "
                f"{len(batch.results)} of {plan.llm_calls} candidate(s))."
            ),
        )

    # The summary line is part of ordinary `curate` output too, so it counts
    # `displayed`, not `high_confidence`: a run whose sole high-confidence
    # verdict was already declined must read "no ... found", never disclose
    # a nonzero count for a finding this stage otherwise shows nothing about
    # (pending-work spec: "Declined Findings Are Hidden By Default").
    status: Literal["applied", "empty"] = "applied" if displayed else "empty"
    notice = (
        f"{len(displayed)} high-confidence contradiction(s) found."
        if displayed
        else "no high-confidence contradictions found."
    )
    return StageOutcome(status=status, applied=len(displayed), notice=notice)


_STAGES: tuple[Stage, ...] = (
    Stage(
        name="Preconditions",
        noun="vector index check",
        probe=_preconditions_probe,
        run=_preconditions_run,
        needs_llm=False,
        writes=False,
        halts_run=True,
        live=True,
    ),
    Stage(
        name="Identity",
        noun="candidate group",
        probe=_identity_probe,
        run=_identity_run,
        needs_llm=True,
        writes=True,
        unattended_hint="openkos adjudicate --apply-same --confirm-count <n>",
        live=True,
        task="adjudication",
    ),
    Stage(
        name="Structure",
        noun="untyped edge",
        probe=_structure_probe,
        run=_structure_run,
        needs_llm=True,
        writes=True,
        unattended_hint="openkos relate <source> <type> <target>",
        live=True,
        auto_acceptable=True,
        accept_caveat=(
            "asymmetric types and related_to are still asked per item, "
            "since their direction is model-suggested and unverified"
        ),
        task="edge_typing",
    ),
    Stage(
        name="Metadata",
        noun="concept type",
        probe=_metadata_probe,
        run=_metadata_run,
        needs_llm=True,
        writes=True,
        unattended_hint="openkos set-volatility <concept> <tier>",
        live=True,
        auto_acceptable=True,
        task="volatility_typing",
    ),
    Stage(
        name="Contradictions",
        noun="pair",
        probe=_contradictions_probe,
        run=_contradictions_run,
        needs_llm=True,
        writes=False,
        live=True,
        task="contradiction",
    ),
)
"""D1 order, all five entries declared at runtime (design D2): Preconditions,
Identity, Structure, Metadata, Contradictions. All five are `live=True` as
of slice 2 -- the tuple's SHAPE stayed frozen from slice 1 through slice 2;
only `live` flipped and `probe`/`run` were filled in on the last three,
exactly as design D10 planned."""

_NOT_LIVE_NOTICE = "not yet available in this version"
"""Verbatim spec wording (Requirement: Slice Boundary) for a `live=False`
stage's summary line."""


def run_curate(ctx: CurateContext) -> list[StageOutcome]:
    """The whole sequencer (design's Data Flow): walk `_STAGES` in order,
    re-deriving every stage's queue fresh with no state carried between
    iterations (design D4 -- the deliberate inversion from `next_action`'s
    memoized `_BundleSignals`). Always returns exactly `len(_STAGES)`
    outcomes, one per stage, in order, regardless of how many stages were
    actually reached.

    A `live=False` stage is skipped WITHOUT ever calling `probe` (design
    D2). Once Preconditions halts the run (`halts_run=True` and its probe
    reports `unavailable`), every remaining LIVE stage is likewise skipped
    without probing -- re-deriving a queue over a bundle whose candidate
    edges are already known-starved would be work `curate` has no business
    doing (spec: Preconditions Stage Halts The Run)."""
    outcomes: list[StageOutcome] = []
    halted = False

    for stage in _STAGES:
        if not stage.live:
            outcomes.append(StageOutcome(status="not-live", notice=_NOT_LIVE_NOTICE))
            continue

        if halted:
            outcomes.append(
                StageOutcome(
                    status="empty",
                    notice="not attempted -- Preconditions halted this run.",
                )
            )
            continue

        observability.stage_notice("curate", f"{stage.name}: checking...")
        probe = stage.probe(ctx)

        # #378: echo the probe's advisory HERE, not inside `gate()`, because
        # three of the branches below return before `gate()` is ever reached
        # -- an unavailable probe, an empty queue, and a stage skipped
        # because Ollama already went down. The candidate-edge cap notice is
        # exactly the case that exposed this: a run whose candidates were
        # truncated but whose survivors were then all filtered out lands on
        # the empty-queue branch, and the reader would have been told
        # nothing. "Truncation is never silent" has to hold on every path,
        # not only the one that asks to spend.
        if probe.notice is not None:
            typer.echo(probe.notice, err=True)

        if probe.unavailable is not None:
            outcomes.append(
                StageOutcome(status="unavailable", notice=probe.unavailable)
            )
            if stage.halts_run:
                halted = True
            continue

        if not probe.items:
            outcomes.append(StageOutcome(status="empty", notice=probe.empty_message))
            continue

        # #515: keyed by THIS stage's model, not by the run. A stage is
        # skipped only when the model IT would contact already failed --
        # `gemma2:27b` being missing says nothing about the reachability of
        # the tag a later stage resolves. In a workspace with no `models:`
        # every stage resolves the same tag, so this is byte-for-byte the
        # pre-#515 run-scoped skip.
        model = stage_model(ctx, stage)
        if stage.needs_llm and model in ctx.ollama_unavailable_notices:
            outcomes.append(
                StageOutcome(
                    status="unavailable",
                    notice="skipped -- Ollama unavailable (see above).",
                )
            )
            continue

        accepted = gate(stage, probe, ctx)
        if not accepted:
            outcomes.append(
                StageOutcome(
                    status="declined",
                    notice="declined -- no LLM calls made.",
                )
            )
            continue

        if stage.writes and not sys.stdin.isatty() and not _accepts(ctx, stage.name):
            # D3 rule 2: `--auto` consents to model spend, never to a
            # per-item write -- reached only when `gate` accepted via
            # `ctx.auto` on a non-TTY, since a TTY decline/non-TTY-no-auto
            # already short-circuited above.
            #
            # `--accept` is the missing consent channel (#385), so an
            # accepted stage passes: naming it IS per-item write consent,
            # which is exactly what this refusal says is absent. That makes
            # `curate --auto --accept structure` parity with
            # `suggest-relations --auto`, which has always written
            # unattended. Identity can never reach this branch, since only
            # `auto_acceptable` stages ever enter `accepted_stages`.
            outcomes.append(
                StageOutcome(
                    status="declined",
                    notice=(
                        "non-interactive write consent unavailable -- run "
                        f"`{stage.unattended_hint}` instead."
                    ),
                )
            )
            continue

        if stage.needs_llm:
            cached = ctx.ollama_clients.get(model)
            if cached is None:
                # `model=config.resolve_task_model(...)` rather than the
                # `model` local computed above, though the two are equal by
                # construction: `test_chat_timeout_wiring.py` reads THIS
                # source to prove every chat client carries the workspace's
                # `chat_timeout` and `max_generation_tokens`, and it
                # recognizes a chat client by the shape of its `model=`
                # argument. A bare local name is indistinguishable there
                # from the liveness probes' own `model=` locals, which must
                # NOT be governed by those two settings -- so writing the
                # resolver call keeps that guard able to see this site.
                cached = OllamaClient(
                    model=config.resolve_task_model(ctx.cfg, stage.task),
                    timeout=ctx.cfg.chat_timeout,
                    max_generation_tokens=ctx.cfg.max_generation_tokens,
                    context_window=ctx.cfg.context_window,
                )
                ctx.ollama_clients[model] = cached
            ctx.ollama_client = cached

        try:
            outcome = stage.run(ctx, probe)
        except OllamaUnavailable as exc:
            notice = (
                f"unavailable -- {exc}. Start it with "
                f"`ollama serve`, then try again.{_DOCTOR_HINT}"
            )
            ctx.ollama_unavailable_notices[model] = notice
            outcome = _availability_outcome(ctx, notice)
        except OllamaModelNotFound:
            # Names the model THIS stage resolved, not `cfg.model` (#515).
            # A remediation naming the global default would send the
            # operator to pull a model that is already installed while the
            # one actually missing stays missing -- actively wrong guidance,
            # which is worse than none.
            notice = (
                f"unavailable -- model '{model}' is not "
                f"installed. Pull it with `ollama pull {model}`, then "
                "try again."
            )
            ctx.ollama_unavailable_notices[model] = notice
            outcome = _availability_outcome(ctx, notice)
        # The two specific handlers above MUST precede this generic one:
        # both subclass `OllamaError`, mirroring `adjudicate`'s ordering
        # discipline (main.py:7134-7141) -- a generic `OllamaError` fails
        # only THIS stage; no run-scoped flag is set, so a later stage still
        # tries its own call.
        except OllamaError as exc:
            outcome = StageOutcome(status="failed", notice=f"failed -- {exc}.")

        outcomes.append(outcome)

    return outcomes


def render_summary(outcomes: Sequence[StageOutcome]) -> list[str]:
    """One line per `_STAGES` entry, in order -- ALWAYS exactly `len(_STAGES)`
    stage lines, even when nothing was eligible in any stage (spec: Summary
    line names every stage outcome) -- plus, directly under a stage whose
    outcome carries `skipped_items`, one indented `  declined: {item}` line
    per operator-declined identity (issue #398), so the count-only stage
    line is never the only record of what was passed over.

    This is the SINGLE owner of the `"{stage}: "` prefix (issue #504): a
    `notice` that named its own stage rendered as `"Identity: Identity:
    ..."`, while the probe-derived notices that never did rendered
    correctly -- one summary block, two formats."""
    lines: list[str] = []
    for stage, outcome in zip(_STAGES, outcomes, strict=True):
        lines.append(f"{stage.name}: {outcome.notice or outcome.status}")
        lines.extend(f"  declined: {item}" for item in outcome.skipped_items)
    return lines
