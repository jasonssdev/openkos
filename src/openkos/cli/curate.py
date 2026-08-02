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

`_STAGES` carries all five entries starting in slice 1 (design D2): the
three not yet implemented (Structure, Metadata, Contradictions) carry
`live=False` and are skipped WITHOUT probing and WITHOUT prompting, but
still appear in the five-entry end-of-run summary, labeled "not yet
available in this version" (spec: Slice Boundary). This is deliberately NOT
a two-entry `_STAGES` proven only by test-only fakes: the tuple and every
descriptor field are frozen in slice 1, so slice 2 only flips `live` and
fills in `probe`/`run` on the three existing entries -- no framework change.

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
from pathlib import Path
from typing import Literal

import typer

from openkos import config
from openkos.cli import next_action as next_action_module
from openkos.cli import observability
from openkos.llm.base import LLMBackend
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.resolution import find_candidates
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    Verdict,
    adjudicate_candidates,
)
from openkos.resolution.candidates import CandidateGroup

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


@dataclass(frozen=True)
class StageOutcome:
    """Everything one stage's run left behind, for the end-of-run summary
    (design's Interfaces). `status` is the fixed six-way vocabulary every
    stage outcome collapses to; `notice` is the human-readable line
    `render_summary` prints for this stage."""

    status: Literal["applied", "declined", "empty", "unavailable", "failed", "not-live"]
    applied: int = 0
    skipped: int = 0
    notice: str | None = None


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
    ollama_client: LLMBackend | None = field(default=None, init=False)
    ollama_unavailable_notice: str | None = field(default=None, init=False)


def cost_line(stage: Stage, probe: StageProbe) -> str:
    """The pinned cost-gate literal (design D3): `{n} {noun}(s) -> {n} LLM
    call(s)`, byte-compatible with `suggest-relations`' existing line
    (`main.py:7436`) for Structure's `untyped edge` noun. `probe.llm_calls`
    -- not `len(probe.items)` -- is the one number both halves report: the
    two coincide for every stage this design has pinned so far, but keeping
    `llm_calls` authoritative is what lets a future stage (design's Open
    Questions: Metadata's exact cost unit) report a call count that is NOT
    a 1:1 count of its queue without this helper needing to change."""
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
      LLM-costing stage) -- there is no consent channel at all here."""
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
    """`resolution.find_candidates` (design D4) -- one LLM call per
    candidate group, since `adjudicate_candidates` issues exactly one
    `llm.chat` per group (`resolution/adjudication.py`)."""
    groups = find_candidates(
        ctx.layout.bundle_dir, include_deprecated=ctx.include_deprecated
    )
    return StageProbe(
        items=tuple(groups),
        llm_calls=len(groups),
        empty_message="No candidate groups found." if not groups else None,
    )


def _identity_run(ctx: CurateContext, probe: StageProbe) -> StageOutcome:
    """`adjudicate_candidates` then, per SAME 2-member group, the exact
    `_prepare_one_merge` / preview / `[y/N/skip]` / `_reject_drifted_targets`
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
    intact and reversible via `unmerge`."""
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
    results: Sequence[AdjudicatedCandidate] = adjudicate_candidates(
        groups,
        bundle_dir=layout.bundle_dir,
        llm=llm,
        include_confidential=ctx.include_confidential,
        on_progress=observability.progress_callback("curate", "adjudicating group"),
    )

    applied = 0
    skipped = 0
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
        answer = typer.prompt(
            f"Merge {prepared.absorbed_canonical} into "
            f"{prepared.survivor_canonical}? [y/N/skip]",
            default="N",
            show_default=False,
        )
        if answer.strip().lower() not in {"y", "yes"}:
            skipped += 1
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

    status: Literal["applied", "empty"] = "applied" if applied or skipped else "empty"
    return StageOutcome(
        status=status,
        applied=applied,
        skipped=skipped,
        notice=f"Identity: applied {applied}, skipped {skipped}.",
    )


def _not_live_probe(stage_name: str) -> "Callable[[CurateContext], StageProbe]":
    """Build a defensive placeholder `probe` for a `live=False` slice-1
    descriptor: raises if ever called, proving the sequencer's `not
    stage.live` short-circuit is what keeps it from running -- never a live
    stage silently doing nothing (design D2)."""

    def _probe(ctx: CurateContext) -> StageProbe:
        raise AssertionError(
            f"{stage_name}: probe must not be called while live=False (slice 1)"
        )

    return _probe


def _not_live_run(
    stage_name: str,
) -> "Callable[[CurateContext, StageProbe], StageOutcome]":
    def _run(ctx: CurateContext, probe: StageProbe) -> StageOutcome:
        raise AssertionError(
            f"{stage_name}: run must not be called while live=False (slice 1)"
        )

    return _run


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
        probe=_not_live_probe("Structure"),
        run=_not_live_run("Structure"),
        needs_llm=True,
        writes=True,
        unattended_hint="openkos relate <source> <type> <target>",
        live=False,
    ),
    Stage(
        name="Metadata",
        noun="concept type",
        probe=_not_live_probe("Metadata"),
        run=_not_live_run("Metadata"),
        needs_llm=True,
        writes=True,
        unattended_hint="openkos set-volatility <concept> <tier>",
        live=False,
    ),
    Stage(
        name="Contradictions",
        noun="pair",
        probe=_not_live_probe("Contradictions"),
        run=_not_live_run("Contradictions"),
        needs_llm=True,
        writes=False,
        live=False,
    ),
)
"""D1 order, all five entries declared at runtime in slice 1 (design D2):
Preconditions, Identity, Structure, Metadata, Contradictions. The tuple and
every descriptor field are frozen here -- slice 2 only flips `live` and
fills in `probe`/`run` on the last three; nothing about this tuple's shape
changes."""

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
            outcomes.append(
                StageOutcome(
                    status="not-live", notice=f"{stage.name}: {_NOT_LIVE_NOTICE}"
                )
            )
            continue

        if halted:
            outcomes.append(
                StageOutcome(
                    status="empty",
                    notice=(
                        f"{stage.name}: not attempted -- Preconditions halted this run."
                    ),
                )
            )
            continue

        observability.stage_notice("curate", f"{stage.name}: checking...")
        probe = stage.probe(ctx)

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
                    notice=(
                        f"{stage.name}: skipped -- Ollama unavailable (see above)."
                    ),
                )
            )
            continue

        accepted = gate(stage, probe, ctx)
        if not accepted:
            outcomes.append(
                StageOutcome(
                    status="declined",
                    notice=f"{stage.name}: declined -- no LLM calls made.",
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
                        f"{stage.name}: non-interactive write consent "
                        f"unavailable -- run `{stage.unattended_hint}` instead."
                    ),
                )
            )
            continue

        if stage.needs_llm and ctx.ollama_client is None:
            ctx.ollama_client = OllamaClient(model=ctx.cfg.model)

        try:
            outcome = stage.run(ctx, probe)
        except OllamaUnavailable as exc:
            notice = (
                f"{stage.name}: unavailable -- {exc}. Start it with "
                f"`ollama serve`, then try again.{_DOCTOR_HINT}"
            )
            ctx.ollama_unavailable_notice = notice
            outcome = StageOutcome(status="unavailable", notice=notice)
        except OllamaModelNotFound:
            notice = (
                f"{stage.name}: unavailable -- model '{ctx.cfg.model}' is not "
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
            outcome = StageOutcome(
                status="failed", notice=f"{stage.name}: failed -- {exc}."
            )

        outcomes.append(outcome)

    return outcomes


def render_summary(outcomes: Sequence[StageOutcome]) -> list[str]:
    """One line per `_STAGES` entry, in order -- ALWAYS exactly `len(_STAGES)`
    lines, even when nothing was eligible in any stage (spec: Summary line
    names every stage outcome)."""
    return [
        f"{stage.name}: {outcome.notice or outcome.status}"
        for stage, outcome in zip(_STAGES, outcomes, strict=True)
    ]
