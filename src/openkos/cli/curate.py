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
    contradiction_truncation_notice,
    find_contradictions,
    is_high_confidence_contradiction,
    plan_candidates,
)
from openkos.resolution.edge_typing import (
    EdgeSuggestion,
    candidate_edges,
    candidate_truncation_notice,
    suggest_edge_types,
)
from openkos.resolution.volatility_typing import TierSuggestion, suggest_volatility

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


@dataclass
class CurateContext:
    """Everything one `curate` invocation threads through every stage.

    Deliberately NOT frozen (unlike `Stage`/`StageProbe`/`StageOutcome`):
    `ollama_client` and `ollama_unavailable_notice` are run-scoped mutable
    state the sequencer fills in as the run progresses (design D7's lazy
    client and short-circuit flag) -- there is exactly one `CurateContext`
    per run, built once by the `curate` command and threaded by reference,
    never copied."""

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
    ollama_client: LLMBackend | None = field(default=None, init=False)
    ollama_unavailable_notice: str | None = field(default=None, init=False)


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
    reach this gate (#378)."""
    typer.echo(cost_line(stage, probe), err=True)
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
                cli_main._echo_n_gt2_skip(group)
                skipped += 1
            continue

        survivor_id, absorbed_id = group.member_ids
        try:
            prepared = cli_main._prepare_one_merge(
                ctx.root, layout, index_path, log_path, group
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

        typer.echo(cli_main._format_merge_preview_line(prepared))
        if not _confirm(
            f"Merge {prepared.absorbed_canonical} into "
            f"{prepared.survivor_canonical}? [y/N]"
        ):
            skipped += 1
            declined.append(
                f"{prepared.absorbed_canonical} -> {prepared.survivor_canonical}"
            )
            continue

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
        raise batch.failure
    if batch.failure is not None:
        return StageOutcome(
            status="failed",
            applied=applied,
            skipped=skipped,
            notice=(
                f"failed -- {batch.failure} (adjudicated "
                f"{len(batch.results)} of {len(groups)} candidate group(s))."
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
        if not _confirm(
            f"Relate {edge.source_id} -> {edge.target_id} "
            f"[{suggestion.suggested_type}]? [y/N]"
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
        # above already ran, so the accepted writes survive (#441).
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
        if not _confirm(f"Set {result.type_name} -> {result.suggested_tier}? [y/N]"):
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
        # above already ran, so the accepted writes survive (#441).
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

    high_confidence: list[ContradictionVerdict] = [
        v for v in verdicts if is_high_confidence_contradiction(v)
    ]
    for verdict in high_confidence:
        source_id, target_id = verdict.pair_ids
        typer.echo(
            f"[{verdict.verdict.value.upper()}] {source_id} <-> {target_id} "
            f"(confidence: {verdict.confidence:.2f})"
        )
        for claim in verdict.conflicting_claims:
            typer.echo(f"  - {claim}")
        typer.echo(f"  rationale: {verdict.rationale}")

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

    status: Literal["applied", "empty"] = "applied" if high_confidence else "empty"
    notice = (
        f"{len(high_confidence)} high-confidence contradiction(s) found."
        if high_confidence
        else "no high-confidence contradictions found."
    )
    return StageOutcome(status=status, applied=len(high_confidence), notice=notice)


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
    ),
    Stage(
        name="Contradictions",
        noun="pair",
        probe=_contradictions_probe,
        run=_contradictions_run,
        needs_llm=True,
        writes=False,
        live=True,
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

        if stage.needs_llm and ctx.ollama_unavailable_notice is not None:
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

        if stage.writes and not sys.stdin.isatty():
            # D3 rule 2: `--auto` consents to model spend, never to a
            # per-item write -- reached only when `gate` accepted via
            # `ctx.auto` on a non-TTY, since a TTY decline/non-TTY-no-auto
            # already short-circuited above.
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

        if stage.needs_llm and ctx.ollama_client is None:
            ctx.ollama_client = OllamaClient(
                model=ctx.cfg.model,
                timeout=ctx.cfg.chat_timeout,
                max_generation_tokens=ctx.cfg.max_generation_tokens,
            )

        try:
            outcome = stage.run(ctx, probe)
        except OllamaUnavailable as exc:
            notice = (
                f"unavailable -- {exc}. Start it with "
                f"`ollama serve`, then try again.{_DOCTOR_HINT}"
            )
            ctx.ollama_unavailable_notice = notice
            outcome = StageOutcome(status="unavailable", notice=notice)
        except OllamaModelNotFound:
            notice = (
                f"unavailable -- model '{ctx.cfg.model}' is not "
                f"installed. Pull it with `ollama pull {ctx.cfg.model}`, then "
                "try again."
            )
            ctx.ollama_unavailable_notice = notice
            outcome = StageOutcome(status="unavailable", notice=notice)
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
