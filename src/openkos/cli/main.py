"""Typer application object exposed as the `openkos` console script."""

import re
import shutil
import sqlite3
import sys
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

import typer

from openkos import config, fsio
from openkos import lint as lint_check
from openkos.bundle import bundle
from openkos.bundle import index as bundle_index
from openkos.bundle import links as bundle_links
from openkos.bundle import log as bundle_log
from openkos.bundle import merge as bundle_merge
from openkos.bundle import provenance as bundle_provenance
from openkos.bundle import references as bundle_references
from openkos.bundle import relations as bundle_relations
from openkos.cli import observability
from openkos.extraction.concept import extract_concept
from openkos.graph import sqlite_graph
from openkos.graph.base import GraphStore
from openkos.llm.base import LLMBackend
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
    model_tag_matches,
)
from openkos.model import okf
from openkos.model.relations import validate_relation_type
from openkos.model.types import CLASSIFIABLE_TYPES as _CLASSIFIABLE_TYPES
from openkos.model.types import TYPE_TO_LINK_DIR as _TYPE_TO_LINK_DIR
from openkos.model.types import TYPE_TO_SECTION as _TYPE_TO_SECTION
from openkos.resolution import find_candidates
from openkos.resolution.adjudication import Verdict, adjudicate_candidates
from openkos.resolution.candidates import Tier
from openkos.resolution.contradiction import (
    find_contradictions,
    is_high_confidence_contradiction,
)
from openkos.resolution.edge_typing import suggest_relations
from openkos.resolution.volatility_typing import suggest_volatility
from openkos.retrieval.answer import NO_MATCH, Citation, NoMatchCause, answer
from openkos.sensitivity import blocks_llm_send
from openkos.state import derived, fts
from openkos.state import reindex as reindex_module
from openkos.state.fts import FtsUnavailable
from openkos.state.vectorstore import (
    VectorStoreDB,
    VecUnavailable,
    open_vector_store,
    probe_vec_loadable,
)
from openkos.vcs import git as vcs_git

app = typer.Typer()

# doctor and init's Ollama preflight are both fast interactive diagnostics:
# use a short timeout so a hung/firewalled host fails quickly instead of
# blocking on OllamaClient's DEFAULT_TIMEOUT.
_PREFLIGHT_TIMEOUT = 5.0

# Shared remediation clause appended to the OllamaUnavailable handlers of
# query, adjudicate, and suggest-relations -- kept as a single constant so
# the three verbs cannot drift from each other in wording.
_DOCTOR_HINT = " Or run `openkos doctor` to diagnose the environment."

# Uniform lock-contention message for `reindex`'s two error ladders
# (vectors/fts and graph) -- a single source of truth so a locked
# vectors.db/fts.db/graph.db always reads identically regardless of which
# store hit the lock (reindex-lock-handling, decision 5).
_LOCK_CONTENTION_MSG = (
    "openkos reindex: failed -- another process is holding the workspace "
    "lock (a concurrent reindex?); wait for it to finish, then try again."
)


@app.callback()
def callback() -> None:
    """openkos: local-first engine that compiles text into a portable knowledge base."""


def _resolve_model(flag: str | None) -> str:
    """Resolve the model tag to write, precedence flag > TTY prompt > default.

    `flag` (already the raw `--model` value, or `None` if not given) wins
    outright -- no prompt is shown even on a TTY. Otherwise, if stdin is a
    TTY, `typer.prompt` offers `config.DEFAULT_MODEL` and the user may
    accept it or type a different tag. If stdin is not a TTY (e.g. piped,
    or a non-interactive CI run), no prompt is shown and the default is
    used silently. Every path runs through `config.validate_model`, which
    raises `ValueError` for a blank or unsafe value -- callers must catch
    it before any file is written.
    """
    if flag is not None:
        return config.validate_model(flag)
    if sys.stdin.isatty():
        return config.validate_model(
            typer.prompt("Model", default=config.DEFAULT_MODEL)
        )
    return config.DEFAULT_MODEL


@app.command()
def init(
    model: str | None = typer.Option(
        None,
        "--model",
        help="Ollama model tag to write into openkos.yaml. "
        "Prompted on a TTY, defaults to qwen3:8b otherwise.",
    ),
) -> None:
    """Create a fresh OKF workspace in the current directory.

    Refuses (exit 1) without writing anything if the current directory
    cannot become a workspace, per the conditions `config.refusal_reason`
    checks (existing `openkos.yaml`, existing `AGENTS.md`, `raw/` or
    `bundle/` non-empty, or `raw/` or `bundle/` existing as a plain file or
    a symlink), OR if the resolved model (see `_resolve_model`: `--model`
    flag > TTY prompt > default `qwen3:8b`) is blank or contains
    whitespace, a quote, or `#`.
    The refusal reason is printed to stderr so the user knows which
    condition triggered it. This is Phase A (D1): a pure read plus model
    resolution/validation, evaluated in full before any write is attempted.

    Phase A itself can fail to even read the directory (e.g. a pre-existing
    `raw/` or `bundle/` with no read permission) -- that is neither a
    refusal (no workspace was found; the check itself errored) nor a
    write failure (Phase B never started), so it gets its own message.

    Phase B (D1) then writes, in order: `raw/`, the bundle (`index.md` then
    `log.md`), `AGENTS.md`, and `openkos.yaml` LAST (D3) -- the marker is
    written only once every other artifact already exists, so a crash
    mid-init never leaves a directory falsely claiming workspace status.
    `raw/` gets the filesystem's default directory permissions; no `chmod`
    is applied (spec: Default raw/ Permissions). Any write failure
    (permissions, disk full, a collision winning the Phase A -> B race) is
    caught and reported on stderr rather than surfacing a raw traceback.
    """
    root = Path.cwd()
    try:
        reason = config.refusal_reason(root)
    except OSError as exc:
        typer.echo(
            f"openkos init: failed while checking the workspace -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc
    if reason is not None:
        typer.echo(f"openkos init: refusing to initialize -- {reason}.", err=True)
        raise typer.Exit(code=1)

    try:
        resolved_model = _resolve_model(model)
    except ValueError as exc:
        typer.echo(f"openkos init: refusing to initialize -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    layout = config.WorkspaceLayout(root)
    try:
        layout.raw_dir.mkdir(parents=True, exist_ok=True)
        bundle.create(layout.bundle_dir, datetime.now().astimezone().date())
        config.write_agents(root)
        config.write_config(root, model=resolved_model)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos init: failed while creating the workspace -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos init: created workspace in {root} "
        f"({layout.raw_dir.name}/, {layout.bundle_dir.name}/index.md, "
        f"{layout.bundle_dir.name}/log.md, {layout.agents_path.name}, "
        f"{layout.config_path.name})."
    )
    typer.echo("Next: run `openkos ingest <path>` to import your first source.")

    # Non-fatal Ollama preflight (D2): purely observational, runs strictly
    # after the workspace already exists. `except Exception` (not
    # `BaseException`) deliberately catches OllamaUnavailable/
    # OllamaModelNotFound/OllamaError AND any unexpected probe error while
    # still letting Ctrl-C/SystemExit propagate; nothing here ever raises
    # `typer.Exit` or pulls a model/spawns a server -- init's exit code
    # stays 0 on every outcome, and the file-writer guarantee above is
    # unaffected either way.
    try:
        probe = OllamaClient(model=resolved_model, timeout=_PREFLIGHT_TIMEOUT)
        ready = model_tag_matches(resolved_model, probe.list_models())
    except Exception:
        ready = False
    if not ready:
        typer.echo(
            "openkos init: note -- Ollama isn't ready for model "
            f"'{resolved_model}' yet. Run `openkos doctor` to diagnose "
            "(ingest and query need it; the workspace was still created).",
            err=True,
        )


def _plural(n: int) -> str:
    """Return `""` for `n == 1`, else `"s"` -- English plural suffix helper
    shared by the `query` command's stderr rendering."""
    return "" if n == 1 else "s"


_SLUG_SANITIZE_RE = re.compile(r"[^a-z0-9]+")
_TITLE_SEPARATOR_RE = re.compile(r"[-_]+")


def _slugify(stem: str) -> str:
    """Sanitize a filename stem into a slug: lowercase, `[^a-z0-9]+` -> `-`, trimmed.

    Callers always pass `Path(src).stem` -- the basename's stem, already
    stripped of every directory component by `Path.name`/`Path.stem` -- so a
    traversal segment in the original `<path>` argument can never leak into
    the returned slug (path containment). When the stem is already safe
    (only lowercase letters/digits/hyphens) it is returned unchanged, so
    `raw/notes.md` and `bundle/sources/notes.md` line up.
    """
    return _SLUG_SANITIZE_RE.sub("-", stem.lower()).strip("-")


def _titleize(stem: str) -> str:
    """Turn a filename stem into a human-readable title: `-`/`_` -> spaces."""
    return _TITLE_SEPARATOR_RE.sub(" ", stem).strip()


# `_TYPE_TO_LINK_DIR`/`_TYPE_TO_SECTION` are now derived from
# `openkos.model.types.REGISTRY` -- see that module for the single source of
# truth. `extraction.ExtractionResult.type` -> catalog section / bundle
# subdirectory (design: Path/Catalog).


@dataclass(frozen=True)
class _DerivedPlan:
    """One validated derived object staged for Phase B write -- one entry
    per item in the list `_stage_derived_objects` returns. The list itself,
    not this dataclass, carries the zero-to-N cardinality (design: bounded
    multi-object contract, D4); `[]` means every candidate was declined,
    dropped, or skipped, and `ingest` degrades to Source-only for this
    batch."""

    doc_type: str
    section: str
    link_dir: str
    slug: str
    title: str
    description: str
    path: Path
    content: str


def _stage_derived_objects(
    *,
    raw_content: str | None,
    source_title: str,
    source_slug: str,
    sensitivity: str,
    timestamp: str,
    bundle_dir: Path,
    llm: LLMBackend,
    include_confidential: bool = False,
) -> list[_DerivedPlan]:
    """Attempt LLM extraction of zero or more distinct derived objects from
    the source's decoded text, and stage each validated candidate for Phase
    B (`ingest` owns slug/path derivation and per-candidate drop wording;
    the extraction leaf stays config-free, per design's Technical Approach).

    This function IS Phase A in full: every check below runs strictly
    BEFORE any write, and the returned list is the COMPLETE, already-deduped
    write set -- Phase B (in `ingest`) does nothing but `mkdir` +
    `write_exclusive` per plan, with no existence check, slug work, or
    dedup left there (design D5 pinned ordering), so a failure partway
    through Phase B never leaves a partially-reconciled state.

    Returns `[]` -- always a Source-only degrade for this batch, never a
    raised error -- when: `raw_content` is `None` or blank (a
    binary/undecodable or empty source has no text to extract from, so the
    LLM is never called); `llm.chat` raises any `OllamaError`-family
    exception (caught HERE, per design's "Degrade seam" --
    `extraction/concept.py` lets it propagate unswallowed); or
    `extract_concept` itself returns `[]` (`[]`, never `None`, is
    `extract_concept`'s contract -- design D4 -- meaning either nothing was
    worth extracting, or every candidate failed ITS OWN fail-closed
    validation; this layer does not distinguish the two).

    Each item in a non-empty `extract_concept` result is then staged
    independently, in reply order, per design's pinned Phase A sequence:
    (1) derive a slug from the title -- an empty slug (a title made only of
    characters `_slugify` strips) skips just that candidate; (2) an
    in-batch collision guard -- a slug already claimed by an EARLIER
    candidate in this SAME reply keeps the first and drops the later one
    (spec: In-Batch Slug-Collision Guard); (3) `derived_path.exists()` -- a
    slug already on disk for ANY source (this source's own prior
    extraction, a hand-authored file, or a genuine cross-source slug
    collision) skips this candidate, leaving the existing file untouched.
    This REPLACES the old provenance-keyed `_source_has_derived_object`
    all-or-nothing gate with PER-SLUG reconciliation (design D5): a
    re-ingest now calls the LLM again and can insert a genuinely NEW object
    even when an older one for the same source already exists, at the
    accepted cost that a nondeterministic LLM title can slugify differently
    across re-ingests and produce a duplicate object. (4) `okf.build_concept`
    -- untrusted LLM fields that slipped past `extract_concept`'s own
    validation (e.g. an embedded newline) can still fail `build_concept`'s
    stricter single-line gate (`ValueError`), which skips just that
    candidate.

    Every one of these four main.py-visible drops (empty slug, collision,
    exists, build failure) is reported to stderr, per candidate (design D4
    drop transparency); a candidate dropped inside `extract_concept`'s own
    validation stays silent there, unchanged from today.

    sensitivity-fail-closed-filter (S3b): unless `include_confidential` is
    `True`, `extract` gates on the WORKSPACE floor rather than a per-doc
    value (a raw source has no per-doc `sensitivity` yet, unlike the other
    five `llm.chat` seams): when `sensitivity.blocks_llm_send(sensitivity)`
    -- i.e. the workspace's `default_sensitivity` floor is confidential (or
    absent/blank, correction batch post-4R-review FIX 1) -- this returns `[]`
    WITHOUT calling `extract_concept` at all, so `llm.chat` is never invoked,
    and emits the same Source-only degrade message shape as the
    blank-content case above. `include_confidential=True` bypasses this gate
    entirely. This delegates to the SAME shared `blocks_llm_send` authority
    `sensitivity.sensitive_concept_ids` uses per-doc, rather than calling
    `okf._rank` directly on `sensitivity` -- a bare `okf._rank` call would
    wrongly resolve a blank/whitespace `default_sensitivity: ""` to
    `"private"` (never tripping this gate), because `okf._rank(None)`/
    `okf._rank("")` both fall back to `"private"` for the unrelated
    `combine_sensitivity` merge-floor use case, not this fail-closed one.
    """
    if raw_content is None or not raw_content.strip():
        typer.echo(
            "openkos ingest: source has no extractable text; keeping the Source only.",
            err=True,
        )
        return []

    if not include_confidential and blocks_llm_send(sensitivity):
        typer.echo(
            "openkos ingest: workspace default_sensitivity floor is confidential; "
            "skipping concept extraction, keeping the Source only.",
            err=True,
        )
        return []

    try:
        extractions = extract_concept(raw_content, source_title=source_title, llm=llm)
    except OllamaError as exc:
        typer.echo(
            f"openkos ingest: concept extraction skipped -- {exc}; "
            "keeping the Source only.",
            err=True,
        )
        return []

    if not extractions:
        typer.echo(
            "openkos ingest: no concept extracted from this source; "
            "keeping the Source only.",
            err=True,
        )
        return []

    plans: list[_DerivedPlan] = []
    seen_slugs: set[str] = set()
    for extraction in extractions:
        derived_slug = _slugify(extraction.title)
        if not derived_slug:
            typer.echo(
                "openkos ingest: extracted title could not be turned into a "
                "slug; skipping this candidate.",
                err=True,
            )
            continue

        if derived_slug in seen_slugs:
            typer.echo(
                f"openkos ingest: duplicate slug '{derived_slug}' within "
                "this extraction batch; keeping the first, skipping this "
                "candidate.",
                err=True,
            )
            continue

        link_dir = _TYPE_TO_LINK_DIR[extraction.type]
        section = _TYPE_TO_SECTION[extraction.type]
        derived_path = bundle_dir / link_dir / f"{derived_slug}.md"
        if derived_path.exists():
            # Create-only reconciliation (design D5): leave the existing
            # derived object -- and its original catalog/log entries --
            # untouched rather than overwriting a possibly hand-edited file.
            typer.echo(
                f"openkos ingest: '{derived_slug}' already exists; skipping "
                "this candidate (create-only).",
                err=True,
            )
            continue

        try:
            content = okf.build_concept(
                type=extraction.type,
                title=extraction.title,
                description=extraction.description,
                body=extraction.body,
                provenance=[f"sources/{source_slug}"],
                sensitivity=sensitivity,
                timestamp=timestamp,
            )
        except ValueError as exc:
            typer.echo(
                f"openkos ingest: extracted content failed validation -- {exc}; "
                "skipping this candidate.",
                err=True,
            )
            continue

        seen_slugs.add(derived_slug)
        plans.append(
            _DerivedPlan(
                doc_type=extraction.type,
                section=section,
                link_dir=link_dir,
                slug=derived_slug,
                title=extraction.title,
                description=extraction.description,
                path=derived_path,
                content=content,
            )
        )

    return plans


@app.command()
def ingest(
    src: Path = typer.Argument(
        ..., help="Path to the raw source file to copy into the workspace."
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help=(
            "Bypass the workspace default_sensitivity floor gate on concept "
            "extraction (excluded by default)."
        ),
    ),
) -> None:
    """Copy `src` into `raw/`, generate one OKF Source concept, and attempt
    LLM extraction of zero or more distinct derived objects, up to
    `extraction.concept._MAX_OBJECTS_PER_SOURCE` (multi-object-extraction,
    PR 2; D5).

    Beyond the MVP-1 "null compiler" (exactly one `Source` concept per
    invocation, with an honest description stating the source was imported),
    this now also attempts ONE LLM-driven extraction step -- one call to
    `extract_concept` per ingest, which itself returns a bounded LIST: an
    injected `OllamaClient` classifies the source's decoded text against the
    full classifiable vocabulary (`openkos.model.types.CLASSIFIABLE_TYPES`)
    and proposes zero, one, or several distinct objects. Any candidate that
    fails validation, collides with an earlier candidate's slug in the same
    batch, or already exists on disk is dropped individually, never the
    whole batch; if the LLM call itself fails or nothing survives at all,
    this degrades to the exact same Source-only result MVP-1 always
    produced, with a short note on stderr and exit 0. A successful
    extraction ADDS zero or more additional, create-only derived documents
    -- one file per validated, staged candidate, under `bundle/concepts/`,
    `bundle/entities/`, `bundle/people/`, `bundle/organizations/`,
    `bundle/places/`, `bundle/events/`, `bundle/procedures/`,
    `bundle/decisions/`, or `bundle/projects/` -- alongside the Source,
    never replacing it. See `_stage_derived_objects` for the full staging,
    degrade, and reconciliation matrix.

    Phase A (pure, no writes) validates and builds the entire result in
    memory, in order: `src` must be an existing, readable file, or this
    refuses; the current directory must already be a workspace (both
    `bundle/index.md` and `bundle/log.md` present), or this refuses; the
    destination name and concept slug are derived ONLY from `src`'s
    basename (`Path(src).name`/`.stem` -- directory components, including
    traversal segments like `../../evil.txt`, are always stripped, so the
    raw copy and concept document can never land outside `raw/` or
    `bundle/sources/`). When `raw/<name>` already exists, `src`'s bytes are
    compared against it (full-byte, before any write): identical bytes make
    this an idempotent re-ingest -- `raw/<name>` is reused untouched and only
    the Source concept plus `index.md`/`log.md` are regenerated, regardless
    of whether the concept already exists (closes the `forget`-then-`ingest`
    trap) -- while differing bytes refuse (raw sources are immutable). When
    `raw/<name>` is absent but `bundle/sources/<slug>.md` exists, this
    refuses as an inconsistent workspace (no raw bytes to compare against).
    Otherwise `read_config` resolves `default_sensitivity`, the Source
    concept is computed in memory, extraction is attempted (always, even
    under `--auto` -- only the confirmation PROMPT is skipped), the derived
    objects (zero or more -- `_stage_derived_objects`' already-reconciled,
    deduped result) are staged, the new `index.md`/`log.md` bytes are
    computed to cover the Source and every staged derived object, and a
    preview of the proposed changes -- listing the Source and every staged
    derived object -- is printed.

    Unless `--include-confidential` is passed, extraction gates on the
    WORKSPACE `default_sensitivity` floor (sensitivity-fail-closed-filter,
    S3b): when the floor is `confidential`, `_stage_derived_objects` returns
    `[]` WITHOUT calling `extract_concept`/`llm.chat` at all, and this
    ingest degrades to a Source-only result -- a raw source has no per-doc
    `sensitivity` value of its own yet, so this is the one `llm.chat` seam
    gated on the workspace floor rather than a per-concept predicate.

    Confirm gate, checked in order: `--auto` skips the prompt outright;
    otherwise config `review: false` skips the prompt the same way;
    otherwise, if stdin is a TTY, `typer.confirm` asks and aborts (exit 1)
    on decline; otherwise (non-TTY, `review: true`, no `--auto`) this
    refuses to write (exit 1) rather than defaulting silently, telling the
    user to re-run with `--auto` -- this intentionally diverges from
    `init`'s silent-on-non-TTY behavior, because `ingest` honors "review
    before save".

    Phase B (after confirm) writes, in order: `bundle/sources/` (created if
    absent), the raw copy (`copy_exclusive`, create-only) and the concept
    document (`write_exclusive`, create-only) on a fresh ingest -- or, on a
    byte-identical re-ingest (D2), the raw copy step is SKIPPED entirely and
    the concept is written via non-exclusive `write_atomic` instead, since it
    may already exist -- then, for EACH staged derived object in staging
    order (zero or more; `_stage_derived_objects` already computed and
    deduped the full write set in Phase A, so this loop does nothing but
    `mkdir` + `write_exclusive`, with no existence check or dedup left
    here, design D5), its own directory (`bundle/concepts/`,
    `bundle/entities/`, `bundle/people/`, `bundle/organizations/`,
    `bundle/places/`, `bundle/events/`, `bundle/procedures/`,
    `bundle/decisions/`, or `bundle/projects/`, created if absent) and its
    document (`write_exclusive`, create-only -- always, regardless of
    whether the Source itself was fresh or regenerated) -- then `index.md`
    and `log.md` (`write_atomic`, catalog LAST -- so the catalog never
    points at a file that does not yet exist, mirroring `init`'s
    marker-last ordering, D3), extended to cover each staged derived
    object's own bullet/log entry, in staging order. Every one of these
    writes is itself create-only or atomic, so none is ever left
    half-written -- but Phase B as a whole is NOT transactional:
    there is no rollback across the sequence (`init`'s D3 "no cleanup
    path" position, retreated to here after an attempt at real rollback
    proved it could not be made truly atomic across independent filesystem
    writes). A failure partway through leaves whatever already landed in
    place -- e.g. a raw copy or concept document written but not yet
    reflected in `index.md`/`log.md` -- a detectable, recoverable partial
    result, never silent corruption (content is always written before the
    catalog, so the catalog never references a file that does not exist).
    Because the OKF bundle is version-controlled, recovery is `git status`
    to see the partial result and `git checkout`/`git clean` to restore --
    not a manual unlink. Any failure -- Phase A or Phase B -- is caught and
    reported on stderr (exit 1), not a raw traceback; `except (OSError,
    ValueError)`, matching `init`'s convention.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        if not src.is_file():
            typer.echo(
                f"openkos ingest: refusing to ingest -- '{src}' does not exist "
                "or is not a readable file.",
                err=True,
            )
            raise typer.Exit(code=1)

        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos ingest: refusing to ingest -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)

        name = src.name
        slug = _slugify(src.stem)
        if not slug:
            raise ValueError(f"cannot derive a concept name from '{src}'")
        raw_dest = layout.raw_dir / name
        sources_dir = layout.bundle_dir / "sources"
        concept_path = sources_dir / f"{slug}.md"

        regenerate = False
        if raw_dest.exists():
            if src.read_bytes() != raw_dest.read_bytes():
                # differing source under an immutable raw copy -> refuse (D4)
                typer.echo(
                    f"openkos ingest: refusing to ingest -- '{src}' differs from "
                    f"the existing 'raw/{name}' copy; raw sources are "
                    "immutable. Ingest under a different name, or inspect the "
                    "existing copy.",
                    err=True,
                )
                raise typer.Exit(code=1)
            regenerate = True  # identical bytes -> idempotent re-ingest (D1)
        elif concept_path.exists():
            # raw absent + concept present -> inconsistent workspace (D5)
            typer.echo(
                f"openkos ingest: refusing to ingest -- 'bundle/sources/{slug}.md' "
                f"exists but its raw source 'raw/{name}' is missing; the "
                "workspace is inconsistent, inspect it before retrying.",
                err=True,
            )
            raise typer.Exit(code=1)
        # else: raw absent + concept absent -> fresh (regenerate stays False)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos ingest: failed while checking the source or workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    now = datetime.now(UTC)
    title = _titleize(src.stem)
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
        if raw_content is None:
            description = (
                f"Raw source imported from '{src}' as {resource}; "
                "binary/non-text content could not be embedded, not yet "
                "extracted into concepts."
            )
        else:
            description = (
                f"Raw source imported from '{src}' as {resource}; full text "
                "embedded verbatim below, not yet extracted into concepts."
            )
        cfg = config.read_config(root)
        concept_content = okf.build_source_concept(
            title=title,
            description=description,
            resource=resource,
            tags=[],
            timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            sensitivity=cfg.default_sensitivity,
            provenance=[resource],
            raw_content=raw_content,
        )
        # Extraction runs AFTER the Source concept is built, BEFORE the
        # preview (design: Technical Approach) -- always attempted, even
        # under `--auto`; only the confirm PROMPT is skipped by `--auto`.
        # `derived_plans` is the FULL, already-reconciled Phase A write set
        # (design D5 pinned ordering) -- zero or more entries, in reply
        # order.
        derived_plans = _stage_derived_objects(
            raw_content=raw_content,
            source_title=title,
            source_slug=slug,
            sensitivity=cfg.default_sensitivity,
            timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            bundle_dir=layout.bundle_dir,
            llm=OllamaClient(model=cfg.model),
            include_confidential=include_confidential,
        )
        index_text = index_path.read_text(encoding="utf-8")
        log_text = log_path.read_text(encoding="utf-8")
        if regenerate:
            # D3: dedup before insert -- a no-forget re-ingest already has
            # the bullet, so a bare insert would duplicate it; a post-forget
            # re-ingest has zero matches, leaving index_text unchanged.
            index_text, _ = bundle_index.remove_index_entry(
                index_text, f"sources/{slug}"
            )
            log_line = (
                f"**Re-ingest**: Regenerated [{title}](/sources/{slug}.md) from "
                f"existing `{resource}` (identical source, raw copy reused)."
            )
        else:
            log_line = (
                f"**Ingest**: Imported [{title}](/sources/{slug}.md) from `{resource}`."
            )
        new_index_text = bundle_index.insert_source_entry(
            index_text, title=title, slug=slug, description=description
        )
        new_log_text = bundle_log.insert_log_entry(
            log_text, now.astimezone().date(), log_line
        )
        # Extends the SAME index/log diff (design: one confirm gate, one
        # preview) rather than a second read-modify-write round trip per
        # derived object; loops `derived_plans` in staging order (design:
        # ingest() call-site loop reshape).
        for plan in derived_plans:
            new_index_text = bundle_index.insert_index_entry(
                new_index_text,
                section=plan.section,
                link_dir=plan.link_dir,
                title=plan.title,
                slug=plan.slug,
                description=plan.description,
            )
            new_log_text = bundle_log.insert_log_entry(
                new_log_text,
                now.astimezone().date(),
                f"**Ingest**: Extracted [{plan.title}]"
                f"(/{plan.link_dir}/{plan.slug}.md) ({plan.doc_type}) "
                f"from [{title}](/sources/{slug}.md).",
            )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos ingest: failed while preparing the ingest -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    if regenerate:
        typer.echo(
            "openkos ingest: proposed changes (re-ingest -- identical source "
            "already present):"
        )
        typer.echo(f"  ~ raw/{name} (existing copy reused -- not rewritten)")
        typer.echo(f"  ~ bundle/sources/{slug}.md (regenerated)")
        for plan in derived_plans:
            typer.echo(f"  + bundle/{plan.link_dir}/{plan.slug}.md")
        typer.echo(f"  ~ {index_path.name} (Source entry refreshed)")
        typer.echo(f"  ~ {log_path.name} (new dated entry)")
    else:
        typer.echo("openkos ingest: proposed changes:")
        typer.echo(f"  + raw/{name}")
        typer.echo(f"  + bundle/sources/{slug}.md")
        for plan in derived_plans:
            typer.echo(f"  + bundle/{plan.link_dir}/{plan.slug}.md")
        typer.echo(f"  ~ {index_path.name} (new Source entry)")
        typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos ingest: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        sources_dir.mkdir(parents=True, exist_ok=True)
        if regenerate:
            # D2: raw copy SKIPPED -- raw/<name> is reused, never rewritten;
            # write_atomic (not write_exclusive) since the concept may
            # already exist (no-forget case) or be absent (post-forget case).
            fsio.write_atomic(concept_path, concept_content)
        else:
            fsio.copy_exclusive(src, raw_dest)
            fsio.write_exclusive(concept_path, concept_content)
        # Phase B write loop (design D5): `derived_plans` is the COMPLETE,
        # already-deduped write set computed by `_stage_derived_objects` in
        # Phase A -- no existence check, slug work, or dedup happens here,
        # only `mkdir` + create-only write, per plan, in staging order.
        for plan in derived_plans:
            plan.path.parent.mkdir(parents=True, exist_ok=True)
            fsio.write_exclusive(plan.path, plan.content)
        fsio.write_atomic(index_path, new_index_text)
        fsio.write_atomic(log_path, new_log_text)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos ingest: failed while writing the ingest -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    imported_paths = [f"raw/{name}", f"bundle/sources/{slug}.md"]
    imported_paths.extend(
        f"bundle/{plan.link_dir}/{plan.slug}.md" for plan in derived_plans
    )
    typer.echo(
        f"openkos ingest: imported '{src}' -> {', '.join(imported_paths)} "
        f"({index_path.name}, {log_path.name} updated)."
    )


def _canonicalize_concept_id(concept_id: str) -> str:
    """Canonicalize `concept_id` to its bundle-relative form, applying every
    path-safety check `_resolve_concept_path` applies EXCEPT existence:
    rejects an absolute id (a leading `/`), any `..` path segment, and a
    reserved basename (`index`/`log`, `okf.RESERVED_FILENAMES`, matched
    CASE-INSENSITIVELY so a case-insensitive filesystem -- macOS/Windows
    default -- cannot be tricked into targeting the real `index.md`/
    `log.md`) -- but does NOT require (or refuse) that `<canonical_id>.md`
    currently exists on disk.

    Shared by `_resolve_concept_path` (which adds the existence check
    needed for a target that must already be there) and `unmerge`'s
    `absorbed_id`, whose file is EXPECTED to be absent -- it was removed by
    the very merge this command reverses -- until Phase B recreates it.
    """
    if concept_id.startswith("/"):
        raise ValueError(f"'{concept_id}' must be a relative concept-id, not absolute")
    posix_id = PurePosixPath(concept_id.removesuffix(".md"))
    if ".." in posix_id.parts:
        raise ValueError(f"'{concept_id}' must not contain '..' segments")
    canonical_id = "/".join(posix_id.parts)
    if not canonical_id:
        raise ValueError(f"'{concept_id}' is not a valid concept-id")
    reserved = {name.lower() for name in okf.RESERVED_FILENAMES}
    if f"{posix_id.name}.md".lower() in reserved:
        raise ValueError(f"'{concept_id}' is a reserved filename")
    return canonical_id


def _resolve_concept_path(bundle_dir: Path, concept_id: str) -> tuple[Path, str]:
    """Resolve `concept_id` to `(concept_file, canonical_id)` under
    `bundle_dir`, or raise `ValueError` (`forget`'s Phase A path-safety gate,
    mirroring `ingest`'s basename-derived containment).

    The `concept_id` is canonicalized ONCE, via `_canonicalize_concept_id` --
    a redundant `.md` suffix is stripped and `PurePosixPath` collapses `.`
    and repeated-slash segments -- and that single `canonical_id` is used
    for BOTH the filesystem path and the caller's `index.md` match, so a
    leading `./` (or a `.md` suffix) can never delete a concept file while
    leaving its catalog bullet dangling.

    On top of `_canonicalize_concept_id`'s path-safety checks (all
    security-relevant and MUST run before any filesystem read tied to
    `concept_id`, threat matrix: path-traversal deletion), this also
    refuses (`ValueError`) if the resolved `<canonical_id>.md` file does
    not exist -- a nonexistent concept-id is a clear error, never a silent
    no-op (spec: Nonexistent Concept Refusal).
    """
    canonical_id = _canonicalize_concept_id(concept_id)
    concept_path = bundle_dir / f"{canonical_id}.md"
    if not concept_path.is_file():
        raise ValueError(f"concept '{concept_id}' does not exist")
    return concept_path, canonical_id


_ForgetScope = Literal["self", "source"]


@app.command()
def forget(
    concept_id: str = typer.Argument(
        ..., help="Bundle-relative concept id (path minus '.md') to remove."
    ),
    scope: _ForgetScope = typer.Option(
        "self",
        "--scope",
        help=(
            "'self' (default) removes only <concept_id>, byte-identical to "
            "a single-concept forget (S2a). 'source' expands the purge set "
            "to <concept_id> plus every concept whose ENTIRE `provenance` "
            "resolves back to it -- the orphan-after-delete closure "
            "computed by `bundle.provenance.find_provenance_descendants`; "
            "a concept with ANY surviving provenance entry outside the "
            "purge set is preserved untouched."
        ),
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Proceed even when inbound references (markdown links or typed "
            "relations) -- or unverifiable referrers whose frontmatter "
            "could not be parsed but that may reference a purge-set "
            "member -- were detected; they are left dangling, never "
            "retargeted. Independent of --auto -- it never skips the "
            "confirmation prompt."
        ),
    ),
) -> None:
    """Delete a concept file and remove its `index.md` catalog entry: the
    mirror-image of `ingest` (MVP-1 simplified delete, decision #717),
    reference-aware (MVP-3 gap #8 S2a) and, since `--scope source`,
    cascade-aware over a concept's provenance descendants (MVP-3 gap #8
    S2b).

    Phase A (pure, no writes) validates and builds the entire result in
    memory, in order: the current directory must already be a workspace
    (the same `config.require_workspace` gate `ingest`/`status`/`lint`
    share), or this refuses; `concept_id` (the ROOT of the purge set) is
    resolved via `_resolve_concept_path`, which rejects an absolute id, any
    `..` segment, a reserved basename (`index`/`log`), or a nonexistent
    concept file -- all as `ValueError`, all refusing BEFORE any read tied
    to `concept_id`, and BEFORE any descendant resolution (threat matrix:
    path-traversal deletion; spec: "Path safety runs before descendant
    resolution"). Descendant ids, by construction, are never user input --
    they are drawn only from real `other_files` keys discovered under
    `bundle_dir`.

    Once path-safety clears, Phase A reads the root's own text and takes
    ONE whole-bundle snapshot of every other `*.md` file (mirroring
    `merge`'s `other_files` construction: reserved filenames and the
    root's own file excluded). This SAME snapshot feeds every step below,
    for both scopes -- no extra bundle scan (design: Technical Approach).

    The PURGE SET is then resolved (design decision 6, unified Phase-A
    data path): `--scope self` (default) collapses it to `{concept_id}`,
    reproducing S2a byte-for-byte; `--scope source` expands it via
    `bundle_provenance.find_provenance_descendants` -- a concept C (C !=
    root) joins iff its `provenance` is NON-EMPTY and a SUBSET of the
    purge set, iterated to a fixed point (spec: "Provenance Descendant
    Resolution"; the non-empty guard is the critical over-deletion
    barrier).

    For EVERY purge-set member, Phase A collects: (1) outbound
    `supersedes` edges targeting a concept OUTSIDE the purge set --
    resurrection disclosures, each naming the target AND the causing
    member (spec: "Resurrection Interaction Disclosure"); (2) inbound
    references via `bundle_references.find_inbound_references` (S2a's own
    scanner, called once per member over the SAME snapshot), from which
    any reference whose REFERRER is itself a purge-set member is dropped
    -- the set-difference gate (design decision 2): an intra-set backlink
    (e.g. a cascade child's `## Related` link back to its Source) is
    expected and must never block, while an EXTERNAL reference or
    unverifiable referrer still does. `unverifiable` referrers -- files
    whose frontmatter could not be parsed at all but whose raw text
    mentions a purge-set member's id, fail-CLOSED per S2a -- are deduped
    by `referrer_id` across members, so one malformed file mentioning
    several member ids surfaces once, not once per member.

    `index.md` is rewritten via `bundle_index.remove_index_entry`, once
    per purge-set member (a pure text transform; call order does not
    affect the result). `log.md` gets one TOMBSTONE-marked entry per
    member (`**Tombstone** (HH:MM:SSZ): Removed [<title>](/<id>.md)
    (id: <id>).`), all sharing the same timestamp (spec: "Log Entry on
    Forget" -- N lines for a cascade, exactly one for `self`).

    The preview prints every purge-set id as `- bundle/<id>.md`, the
    catalog/log edit lines, one `!`/`?` line per surviving EXTERNAL
    reference, one `~` line per resurrection disclosure, and -- for
    `--scope source` only -- a trailing count line (spec: "Full-Set
    Preview and Count Confirmation"). `--scope self`'s preview and every
    downstream string is UNCHANGED from S2a (byte-identity, design
    decision 6): the member-suffix on reference lines and the count line
    are both scope-conditional and never appear for `self`.

    TWO ORTHOGONAL gates follow, in order (spec: "`--force` Is Orthogonal
    to the Confirm Gate"): gate 1 refuses (exit 1, no write) iff a
    surviving verified reference OR unverifiable referrer was detected AND
    `--force` was not passed -- `--force` bypasses ONLY this refusal,
    never retargeting/rewriting the dangling references it leaves behind.
    Gate 2 is the confirm gate, identical precedence to `ingest`: `--auto`
    skips the prompt outright; otherwise config `review: false` skips it
    the same way; otherwise, on a TTY, `typer.confirm` asks (stating the
    delete COUNT for `--scope source`; S2a's verbatim text for `self`) and
    aborts (exit 1) on decline; otherwise (non-TTY, no `--auto`) this
    refuses to write (exit 1). `--force` does NOT auto-confirm gate 2 --
    the two gates stay fully orthogonal for both scopes.

    Phase B (after both gates) writes `index.md` then `log.md`
    (`write_atomic`, catalog FIRST, covering every purge-set member) and
    deletes each member's concept file (`fsio.remove_file`) LAST, in
    deterministic `sorted(purge_ids)` order (design decision 5) -- so
    `index.md`/`log.md` never reference a file that does not exist. This
    is NOT transactional as a whole: a failure partway through the N
    unlinks leaves a benign, git-recoverable partial result -- the catalog
    already fully updated, one or more concept files possibly still
    present as orphans -- never silent corruption. Any failure, Phase A or
    Phase B, is caught and reported on stderr (exit 1), not a raw
    traceback; `except (OSError, ValueError)`, matching `ingest`'s
    convention.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos forget: refusing to forget -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)

        # Path-safety on the ROOT id runs FIRST, before any descendant
        # resolution (spec: "Path safety runs before descendant
        # resolution") -- descendant ids are disk-discovered later, never
        # user input.
        concept_path, canonical_id = _resolve_concept_path(
            layout.bundle_dir, concept_id
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos forget: refusing to forget -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    now = datetime.now(UTC)

    try:
        cfg = config.read_config(root)
        index_text = index_path.read_text(encoding="utf-8")
        log_text = log_path.read_text(encoding="utf-8")
        concept_text = concept_path.read_text(encoding="utf-8")

        # One whole-bundle snapshot, read ONCE, mirroring `merge`'s
        # `other_files` construction (~L1330-1337): every other `*.md`
        # file, reserved filenames and the ROOT's own file excluded. This
        # single snapshot feeds descendant resolution, inbound detection,
        # resurrection, and per-member titles/tombstones -- no extra
        # bundle scan, for either scope (design: Technical Approach).
        other_files: dict[str, str] = {}
        for path in sorted(layout.bundle_dir.rglob("*.md")):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            if path == concept_path:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            other_files[rel] = path.read_text(encoding="utf-8")

        # Unified Phase-A data path (design decision 6): `--scope self`
        # collapses to a single-member purge set and reproduces every
        # downstream computation identically to S2a; `--scope source`
        # expands it via the pure orphan-closure helper. Resolution runs
        # strictly after path-safety/existence (above) and before
        # detection/preview (spec: "Provenance Descendant Resolution").
        purge_ids: list[str] = (
            bundle_provenance.find_provenance_descendants(
                other_files, root_ids={canonical_id}
            )
            if scope == "source"
            else [canonical_id]
        )
        purge_ids_set = set(purge_ids)

        # Per-member text + parsed frontmatter. Every non-root member id in
        # `purge_ids` came out of `find_provenance_descendants`, itself
        # derived only from real `other_files` keys (disk-discovered, never
        # user input) -- so this dict lookup can never escape `bundle_dir`.
        member_texts: dict[str, str] = {canonical_id: concept_text}
        for member in purge_ids:
            if member != canonical_id:
                member_texts[member] = other_files[f"{member}.md"]
        member_metadata: dict[str, dict[str, object]] = {
            member: okf.load_frontmatter(text)[0]
            for member, text in member_texts.items()
        }

        # Outbound `supersedes` disclosure (spec: "Resurrection Interaction
        # Disclosure"), per PURGE-SET MEMBER: a target OUTSIDE the purge
        # set re-enters retrieval once the whole set is gone. The
        # `target not in purge_ids_set` guard also covers S2a's defensive
        # self-`supersedes` exclusion for the `self` scope (no known CLI
        # path can construct one).
        #
        # Tuple convention: the purge-set MEMBER (the "tag" identifying
        # which purge-set concept caused the disclosure) is ALWAYS field 0,
        # matching `all_refs` below (`(member, ref)`) -- a future edit
        # copying one unpacking idiom onto the other stays safe. Sort order
        # is preserved as "primarily by target" (the original tuple order)
        # via an explicit key, so output is unchanged.
        resurrection_pairs = sorted(
            {
                (member, relation.target)
                for member in purge_ids
                for relation in okf.decode_relations(member_metadata[member])
                if relation.type == "supersedes"
                and relation.target not in purge_ids_set
            },
            key=lambda pair: (pair[1], pair[0]),
        )

        # Set-difference inbound-reference detection (design decision 2):
        # `find_inbound_references` -- S2a's own scanner, unmodified -- is
        # called once PER purge-set member over the SAME whole-bundle
        # snapshot; any referrer whose id is ITSELF a purge-set member is
        # dropped (an intra-set backlink, e.g. a cascade child's
        # `## Related` link back to its Source, is expected and must never
        # block). `unverifiable` referrers are deduped by `referrer_id`
        # across members -- a single malformed file mentioning several
        # member ids must surface once, not once per member.
        #
        # Tuple convention: the purge-set MEMBER is field 0, `ref` is
        # field 1, matching `resurrection_pairs` above (member also field
        # 0) -- keep both tuple shapes member-first so a future edit can
        # never silently swap fields by copying one unpacking idiom onto
        # the other.
        all_refs: list[tuple[str, bundle_references.InboundReference]] = []
        seen_unverifiable: set[str] = set()
        for member in purge_ids:
            for ref in bundle_references.find_inbound_references(
                other_files, target_id=member
            ):
                if ref.referrer_id in purge_ids_set:
                    continue
                if ref.kind == "unverifiable":
                    if ref.referrer_id in seen_unverifiable:
                        continue
                    seen_unverifiable.add(ref.referrer_id)
                all_refs.append((member, ref))
        verified_refs = [ref for _, ref in all_refs if ref.kind != "unverifiable"]
        unverifiable_refs = [ref for _, ref in all_refs if ref.kind == "unverifiable"]

        # `index.md` bullet removal for every purge-set member (a pure
        # text transform -- call order has no effect on the final result).
        new_index_text = index_text
        total_removed = 0
        for member in purge_ids:
            new_index_text, removed_i = bundle_index.remove_index_entry(
                new_index_text, member
            )
            total_removed += removed_i

        # `log.md` tombstones, one per member, all sharing `tombstone_time`
        # (a single `now`). Built in REVERSED sorted order so the LAST
        # prepend (the smallest id) ends up at the very top -- a
        # deterministic ascending top-to-bottom order matching the sorted
        # delete order below.
        tombstone_time = now.strftime("%H:%M:%SZ")
        new_log_text = log_text
        for member in reversed(purge_ids):
            raw_title = member_metadata[member].get("title")
            title = (
                raw_title
                if isinstance(raw_title, str) and raw_title.strip()
                else member
            )
            new_log_text = bundle_log.insert_log_entry(
                new_log_text,
                now.astimezone().date(),
                f"**Tombstone** ({tombstone_time}): Removed [{title}]"
                f"(/{member}.md) (id: {member}).",
            )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos forget: failed while preparing the forget -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos forget: proposed changes:")
    if total_removed >= 1:
        typer.echo(f"  ~ {index_path.name} (remove entry)")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")
    for member in purge_ids:
        typer.echo(f"  - bundle/{member}.md")
    for member, ref in all_refs:
        if ref.kind == "link":
            line = f"  ! bundle/{ref.referrer_id}.md (inbound link)"
        elif ref.kind == "relation":
            line = (
                f"  ! bundle/{ref.referrer_id}.md "
                f"(inbound relation: {ref.relation_type})"
            )
        else:
            line = (
                f"  ? bundle/{ref.referrer_id}.md "
                f"(unverifiable: could not parse; may reference {member})"
            )
        if scope == "source" and ref.kind != "unverifiable":
            line += f" -> {member}"
        typer.echo(line)
    for member, target in resurrection_pairs:
        typer.echo(
            f"  ~ bundle/{target}.md (re-enters retrieval: no longer "
            f"superseded by {member})"
        )
    if scope == "source":
        typer.echo(f"  Total: {len(purge_ids)} concept(s) to delete.")

    # Gate 1 (spec: "Refuse Forget When Inbound References Exist, Unless
    # --force"): refuses iff a surviving (external, set-difference-
    # filtered) verified reference OR unverifiable referrer was detected
    # AND --force was not passed -- fully independent of gate 2 below
    # (spec: "--force Is Orthogonal to the Confirm Gate"). `target_desc`
    # is scope-conditional ONLY in wording; for `self` it reproduces S2a's
    # exact `'<canonical_id>'` phrasing byte-for-byte.
    if (verified_refs or unverifiable_refs) and not force:
        messages: list[str] = []
        target_desc = (
            f"the {len(purge_ids)}-concept purge set rooted at '{canonical_id}'"
            if scope == "source"
            else f"'{canonical_id}'"
        )
        if verified_refs:
            messages.append(
                f"{len(verified_refs)} inbound reference(s) to {target_desc} found"
            )
        if unverifiable_refs:
            messages.append(
                f"could not verify {len(unverifiable_refs)} referrer(s) "
                f"that may reference {target_desc}"
            )
        typer.echo(
            "openkos forget: refusing to forget -- "
            + "; ".join(messages)
            + "; re-run with --force to proceed (references will be left "
            "dangling).",
            err=True,
        )
        raise typer.Exit(code=1)

    # Gate 2: the confirm gate, untouched by --force. `--scope source`
    # names the delete COUNT in its own prompt text (spec: "`--force`
    # does not auto-confirm the count"); `--scope self` keeps S2a's
    # verbatim prompt (byte-identity, design decision 6).
    if not auto and cfg.review:
        if sys.stdin.isatty():
            if scope == "source":
                typer.confirm(f"Delete {len(purge_ids)} concepts?", abort=True)
            else:
                typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos forget: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    unlinked_count = 0
    try:
        fsio.write_atomic(index_path, new_index_text)
        fsio.write_atomic(log_path, new_log_text)
        # N-delete, LAST, in deterministic sorted order (design decision 5)
        # -- the catalog already reflects every removal before any unlink,
        # so a failure partway through leaves a benign, git-recoverable
        # partial result, never a dangling catalog entry.
        for member in sorted(purge_ids):
            fsio.remove_file(layout.bundle_dir / f"{member}.md")
            unlinked_count += 1
    except (OSError, ValueError) as exc:
        message = f"openkos forget: failed while writing the forget -- {exc}."
        # K-of-N observability on a mid-cascade unlink failure (`--scope
        # source`): only enrich when there is more than one purge-set
        # member to report on, so the `self`/single-member message stays
        # byte-identical to S2a.
        if len(purge_ids) > 1:
            remaining = len(purge_ids) - unlinked_count
            message += (
                f" removed {unlinked_count} of {len(purge_ids)} concept(s) "
                f"before failing; {remaining} remain (recover with git or "
                "'openkos lint')."
            )
        typer.echo(message, err=True)
        raise typer.Exit(code=1) from exc

    if scope == "source":
        deleted_paths = ", ".join(f"bundle/{member}.md" for member in purge_ids)
        typer.echo(
            f"openkos forget: removed {len(purge_ids)} concept(s) "
            f"({deleted_paths}) ({index_path.name}, {log_path.name} updated)."
        )
    else:
        typer.echo(
            f"openkos forget: removed 'bundle/{canonical_id}.md' "
            f"({index_path.name}, {log_path.name} updated)."
        )


_PurgeScope = Literal["self", "source"]


def _purge_confirm_phrase(
    canonical_id: str, purge_ids: list[str], scope: _PurgeScope
) -> str:
    """The exact typed confirmation phrase `purge` requires before Phase B:
    `purge <canonical_id>` for `--scope self`, `purge <canonical_id> (<N>
    concepts)` for `--scope source` -- names the delete COUNT so an operator
    cannot type the self-scope phrase by habit and unknowingly confirm a
    larger cascade (design: Typed Confirmation)."""
    if scope == "source":
        return f"purge {canonical_id} ({len(purge_ids)} concepts)"
    return f"purge {canonical_id}"


def _purge_clean_live_index(
    layout: config.WorkspaceLayout, purge_ids: list[str]
) -> None:
    """After the (already irreversible) history rewrite has succeeded,
    remove the LIVE `index.md` catalog bullet for EVERY purge-set member --
    reusing `forget`'s own `bundle_index.remove_index_entry` +
    `fsio.write_atomic` write path.

    Without this, the live catalog would keep a bullet pointing at a
    concept whose file no longer exists in ANY commit -- a broken catalog
    entry, and the purged id/title staying visible in the LIVE workspace
    (not merely history).

    This runs as an ordinary working-tree edit AFTER `git filter-repo` has
    already committed the rewritten history and checked out the new HEAD --
    there is no dirty-tree rail left to satisfy at this point (Phase B has
    already begun; spec: Irreversibility -- No Rollback After Rewrite
    Begins), so this is simply the next write in the same irreversible
    operation, not a new gated action.

    A failure here is reported but does NOT fail the (already-succeeded)
    purge -- the erasure already happened; a stale catalog bullet left
    behind by a failed write is a correctness issue to fix with
    `openkos lint`, not a data-leak one."""
    index_path = layout.bundle_dir / "index.md"
    try:
        index_text = index_path.read_text(encoding="utf-8")
        new_index_text = index_text
        for member in purge_ids:
            new_index_text, _ = bundle_index.remove_index_entry(new_index_text, member)
        if new_index_text != index_text:
            fsio.write_atomic(index_path, new_index_text)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos purge: warning -- failed to clean the live index.md "
            f"catalog: {exc}. Run 'openkos lint' to detect/fix a dangling "
            "bullet.",
            err=True,
        )


def _purge_clean_live_log(layout: config.WorkspaceLayout, purge_ids: list[str]) -> None:
    """After the (already irreversible) history rewrite has succeeded,
    remove any LIVE `log.md` `forget` tombstone entry for EVERY purge-set
    member -- mirroring `_purge_clean_live_index` exactly, but via
    `bundle_log.remove_log_entry`.

    Without this, a concept that was `forget`-ed before being `purge`-d
    would leave its tombstone visible in the LIVE `log.md` even though the
    concept itself, and now (Slice 2) every HISTORICAL mention of it in
    `index.md`/`log.md`, is gone.

    A failure here is reported but does NOT fail the (already-succeeded)
    purge, matching `_purge_clean_live_index`'s same non-fatal contract."""
    log_path = layout.bundle_dir / "log.md"
    try:
        log_text = log_path.read_text(encoding="utf-8")
        new_log_text = log_text
        for member in purge_ids:
            new_log_text, _ = bundle_log.remove_log_entry(new_log_text, member)
        if new_log_text != log_text:
            fsio.write_atomic(log_path, new_log_text)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos purge: warning -- failed to clean the live log.md "
            f"tombstone(s): {exc}. Run 'openkos lint' to detect/fix a "
            "dangling entry.",
            err=True,
        )


def _purge_rebuild_indexes(layout: config.WorkspaceLayout) -> None:
    """Phase B's index cleanup (spec: Index Cleanup Is Delete-And-Rebuild, No
    Tombstone): physically DELETE `.openkos/{fts,vectors,graph}.db` --
    row-level `DELETE` would leave SQLite freelist-recoverable pages, which
    defeats the point of an erasure -- then best-effort rebuild FTS + graph
    ONLY (never the full `state.reindex.reindex`, which hard-depends on a
    running Ollama embedder `purge` must never require). `vectors.db` is
    deliberately left deleted for the next `openkos reindex` to lazily
    re-embed.

    A rebuild failure here is reported but MUST NOT fail the (already
    irreversible, already-succeeded) purge -- the DELETE above is the
    security-critical erasure; the rebuild is a best-effort convenience over
    the survivors (design: Index cleanup decision)."""
    for db_path in (
        layout.fts_db_path,
        layout.vectors_db_path,
        layout.graph_db_path,
    ):
        try:
            db_path.unlink(missing_ok=True)
        except OSError as exc:
            typer.echo(
                f"openkos purge: warning -- failed to delete '{db_path.name}': "
                f"{exc}. Run `openkos reindex` to rebuild derived indexes.",
                err=True,
            )

    try:
        reindex_module._reindex_fts(layout.bundle_dir, layout.fts_db_path, force=True)
    except (OSError, sqlite3.Error, FtsUnavailable) as exc:
        typer.echo(
            f"openkos purge: warning -- failed to rebuild fts.db: {exc}. "
            "Run `openkos reindex` to restore search.",
            err=True,
        )

    try:
        sqlite_graph.reindex_graph(layout.bundle_dir, layout.graph_db_path, force=True)
    except (OSError, sqlite3.Error) as exc:
        typer.echo(
            f"openkos purge: warning -- failed to rebuild graph.db: {exc}. "
            "Run `openkos reindex` to restore search.",
            err=True,
        )


@app.command()
def purge(
    concept_id: str = typer.Argument(
        ..., help="Bundle-relative concept id (path minus '.md') to purge."
    ),
    scope: _PurgeScope = typer.Option(
        "self",
        "--scope",
        help=(
            "'self' (default) purges only <concept_id>. 'source' expands the "
            "purge set to <concept_id> plus every concept whose ENTIRE "
            "`provenance` resolves back to it -- the SAME orphan-after-delete "
            "closure `forget --scope source` uses "
            "(`bundle.provenance.find_provenance_descendants`)."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Proceed even when inbound references (markdown links or typed "
            "relations) -- or unverifiable referrers -- to a purge-set "
            "member were detected; they are left dangling. Bypasses ONLY "
            "the reference-aware rail -- every other rail (git-root, clean "
            "tree, no published commits, typed confirmation) still runs."
        ),
    ),
    confirm_phrase: str | None = typer.Option(
        None,
        "--confirm-phrase",
        help=(
            "The exact typed confirmation phrase (see the printed preview), "
            "for non-interactive/test use. On a TTY, omitting this prompts "
            "interactively instead. There is NO --auto bypass for this "
            "phrase -- purge is irreversible."
        ),
    ),
) -> None:
    """Irreversibly whole-file-expunge a concept's source `raw/<name>` and
    bundle file from ALL git history via `git-filter-repo`, ALSO content-
    scrubbing every historical `bundle/index.md`/`bundle/log.md` blob of the
    purge-set member(s) -- the true-erasure counterpart to `forget`,
    completing right-to-be-forgotten (Slice 1 whole-file expunge + Slice 2
    history content-scrub).

    Phase A (pure, no writes) is IDENTICAL to `forget`'s: `require_workspace`
    gate, `_resolve_concept_path` path-safety on the root id, the purge-set
    resolution (`--scope self|source` via
    `bundle_provenance.find_provenance_descendants`), and the SAME reference-
    aware inbound-reference detection. On top of that, for every purge-set
    member this also resolves its raw source path from a Source's
    `resource: raw/<name>` frontmatter (a derived concept, with no
    `resource`, contributes only its own `bundle/<id>.md`; a Source whose
    `resource` is absent or fails validation -- must start with `raw/`, no
    `..` segment, resolve under `raw/` -- is WARNED about, not refused, and
    simply contributes no raw path).

    Six fail-closed safety rails run, in this EXACT order, ALL before any
    write: (1) reference-aware refusal (unless `--force`) -- reused from
    `forget`'s own gate; (2) `git`/`git-filter-repo` availability; (3) the
    workspace root must BE a git repository root (`vcs.repo_root`); (4) the
    working tree must be clean (`vcs.is_clean`); (5) the local repo must
    have NO commits already published on any remote (`vcs.has_published_commits`
    -- history rewriting cannot retroactively change what a remote already
    has); (6) a TYPED CONFIRMATION PHRASE, printed alongside the preview,
    must match EXACTLY (never a bare `y`/`yes`) -- there is no `--auto`
    bypass for this rail, since purge is irreversible. The first failing
    rail refuses immediately (exit 1, nothing written); no later rail is
    evaluated.

    Phase B (the point of no return, reached only once all six rails pass):
    `vcs.expunge_paths` rewrites every purge-set member's `raw/<name>` and
    `bundle/<id>.md` out of ALL git history and the working tree, and, in
    the SAME pass, content-scrubs every historical `index.md`/`log.md` blob
    of the purge-set member(s)' catalog bullet, log entries, and any prior
    `forget` tombstone (Slice 2), then finalizes (reflog expire + gc). A
    `GitFinalizeError` (the rewrite SUCCEEDED but finalize failed) is
    surfaced distinctly, and live-index/live-log cleanup still runs -- the
    rewrite already happened and cannot be undone. Index cleanup then
    deletes `.openkos/{fts,vectors,graph}.db` and best-effort rebuilds FTS +
    graph (never `vectors.db`, and never through the Ollama-dependent full
    `reindex()`) -- a rebuild failure is reported but does NOT fail the
    already-irreversible purge. After a successful purge, the purged id/
    title no longer appears anywhere in `index.md` or `log.md`, live or
    historical -- no residual warning is printed.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos purge: refusing to purge -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)

        # Path-safety on the ROOT id runs FIRST, before any descendant
        # resolution -- identical to `forget` (threat matrix: path-traversal
        # deletion).
        concept_path, canonical_id = _resolve_concept_path(
            layout.bundle_dir, concept_id
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos purge: refusing to purge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    try:
        concept_text = concept_path.read_text(encoding="utf-8")

        # Same whole-bundle snapshot construction as `forget` (~L1006-1019).
        other_files: dict[str, str] = {}
        for path in sorted(layout.bundle_dir.rglob("*.md")):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            if path == concept_path:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            other_files[rel] = path.read_text(encoding="utf-8")

        purge_ids: list[str] = (
            bundle_provenance.find_provenance_descendants(
                other_files, root_ids={canonical_id}
            )
            if scope == "source"
            else [canonical_id]
        )
        purge_ids_set = set(purge_ids)

        member_texts: dict[str, str] = {canonical_id: concept_text}
        for member in purge_ids:
            if member != canonical_id:
                member_texts[member] = other_files[f"{member}.md"]
        member_metadata: dict[str, dict[str, object]] = {
            member: okf.load_frontmatter(text)[0]
            for member, text in member_texts.items()
        }

        # Reference-aware detection (rail 1's data), identical set-difference
        # gate to `forget`'s.
        all_refs: list[tuple[str, bundle_references.InboundReference]] = []
        seen_unverifiable: set[str] = set()
        for member in purge_ids:
            for ref in bundle_references.find_inbound_references(
                other_files, target_id=member
            ):
                if ref.referrer_id in purge_ids_set:
                    continue
                if ref.kind == "unverifiable":
                    if ref.referrer_id in seen_unverifiable:
                        continue
                    seen_unverifiable.add(ref.referrer_id)
                all_refs.append((member, ref))
        verified_refs = [ref for _, ref in all_refs if ref.kind != "unverifiable"]
        unverifiable_refs = [ref for _, ref in all_refs if ref.kind == "unverifiable"]

        # Raw-path resolution (design: "Raw-path resolution"): a Source's
        # `resource` is validated (must start with `raw/`, no `..`, resolve
        # under `layout.raw_dir`) -- an absent or malformed `resource` is
        # WARNED about, never refused, and simply contributes no raw path
        # (this Source's own `bundle/<id>.md` is still targeted).
        expunge_targets: list[str] = []
        resource_warnings: list[str] = []
        raw_dir_resolved = layout.raw_dir.resolve()
        for member in sorted(purge_ids):
            resource = member_metadata[member].get("resource")
            if isinstance(resource, str) and resource:
                posix_resource = PurePosixPath(resource)
                valid = (
                    resource.startswith("raw/")
                    and not resource.startswith("/")
                    and ".." not in posix_resource.parts
                )
                if valid:
                    try:
                        (root / resource).resolve().relative_to(raw_dir_resolved)
                    except ValueError:
                        valid = False
                if valid:
                    expunge_targets.append(resource)
                else:
                    resource_warnings.append(
                        f"'{member}': resource frontmatter {resource!r} is "
                        "absent/malformed -- skipping its raw-path expunge "
                        "(its bundle file is still targeted)"
                    )
            expunge_targets.append(f"bundle/{member}.md")
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos purge: failed while preparing the purge -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    # Preview: every path targeted for expunge, any raw-path resolution
    # warnings, and the cascade count (source scope only) -- all printed
    # before rail 1. Slice 2 removes the (now-obsolete) mandatory
    # residual-leak warning: the history content-scrub below means no
    # residual is left to warn about.
    typer.echo("openkos purge: proposed IRREVERSIBLE history rewrite:")
    for target in expunge_targets:
        typer.echo(f"  - {target}")
    for warning in resource_warnings:
        # Stream-consistent with the rest of the pre-confirmation preview
        # (stdout, not stderr) -- an operator capturing only stdout must
        # not silently lose a malformed-resource warning printed here.
        typer.echo(f"  ! {warning}")
    if scope == "source":
        typer.echo(f"  Total: {len(purge_ids)} concept(s) to purge.")
    typer.echo()

    # Rail 1: reference-aware refusal, unless --force (spec req 2, rail 1).
    if (verified_refs or unverifiable_refs) and not force:
        messages: list[str] = []
        target_desc = (
            f"the {len(purge_ids)}-concept purge set rooted at '{canonical_id}'"
            if scope == "source"
            else f"'{canonical_id}'"
        )
        if verified_refs:
            messages.append(
                f"{len(verified_refs)} inbound reference(s) to {target_desc} found"
            )
        if unverifiable_refs:
            messages.append(
                f"could not verify {len(unverifiable_refs)} referrer(s) "
                f"that may reference {target_desc}"
            )
        typer.echo(
            "openkos purge: refusing to purge -- "
            + "; ".join(messages)
            + "; re-run with --force to proceed (references will be left "
            "dangling).",
            err=True,
        )
        raise typer.Exit(code=1)

    # Rail 2: git/git-filter-repo availability (spec req 2, rail 2 in this
    # implementation's ordering -- cheap, deterministic, no repo assumption).
    if not vcs_git.git_available():
        typer.echo(
            "openkos purge: refusing to purge -- git is not available on "
            "PATH. Install git (e.g. https://git-scm.com/downloads, or "
            "`brew install git`), then try again.",
            err=True,
        )
        raise typer.Exit(code=1)
    if not vcs_git.filter_repo_available():
        typer.echo(
            "openkos purge: refusing to purge -- git-filter-repo is not "
            "available. Install it (e.g. `pip install git-filter-repo`, or "
            "`brew install git-filter-repo`), then try again.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Rail 3: the workspace root MUST be a git repository root (threat
    # matrix: git repository selection) -- always run in cwd, never
    # `git -C <userpath>`.
    try:
        found_root = vcs_git.repo_root(root)
    except vcs_git.GitError as exc:
        typer.echo(f"openkos purge: refusing to purge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc
    if found_root is None:
        typer.echo(
            "openkos purge: refusing to purge -- the workspace is not "
            "inside a git repository.",
            err=True,
        )
        raise typer.Exit(code=1)
    if found_root != root.resolve():
        typer.echo(
            "openkos purge: refusing to purge -- the workspace root is not "
            "the git repository root (a nested or ancestor repo cannot be "
            "safely rewritten).",
            err=True,
        )
        raise typer.Exit(code=1)

    # Rail 4: the working tree must be clean.
    try:
        clean = vcs_git.is_clean(root)
    except vcs_git.GitError as exc:
        typer.echo(f"openkos purge: refusing to purge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc
    if not clean:
        typer.echo(
            "openkos purge: refusing to purge -- the working tree has "
            "uncommitted changes; commit or stash them, then try again.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Rail 5: no commits already published on any remote -- history
    # rewriting cannot retroactively change what a remote already has.
    try:
        published = vcs_git.has_published_commits(root)
    except vcs_git.GitError as exc:
        typer.echo(f"openkos purge: refusing to purge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc
    if published:
        typer.echo(
            "openkos purge: refusing to purge -- commits are already "
            "present on a remote; purge cannot rewrite published history.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Rail 6: the typed confirmation phrase, EXACT match only -- no --auto
    # bypass (irreversible). `--confirm-phrase` serves both non-interactive
    # use and tests; on a TTY without it, `typer.prompt` asks interactively.
    expected_phrase = _purge_confirm_phrase(canonical_id, purge_ids, scope)
    if confirm_phrase is not None:
        typed_phrase = confirm_phrase
    elif sys.stdin.isatty():
        typed_phrase = typer.prompt(f"Type '{expected_phrase}' to proceed")
    else:
        typer.echo(
            "openkos purge: refusing to purge -- stdin is not a TTY; "
            "re-run with --confirm-phrase.",
            err=True,
        )
        raise typer.Exit(code=1)
    if typed_phrase != expected_phrase:
        typer.echo(
            "openkos purge: aborted -- confirmation phrase did not match "
            "exactly; nothing was written.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Phase B: the point of no return. No rail evaluation, no abort path,
    # from here on (spec: Irreversibility -- No Rollback After Rewrite
    # Begins). `expunge_paths` itself is silent and can run for a while on
    # a large history -- print an explicit "do not interrupt" line FIRST,
    # so an operator who sees no output does not mistake it for a hang and
    # Ctrl-C into the catastrophic mid-rewrite state.
    typer.echo(
        "openkos purge: beginning the irreversible history rewrite now -- "
        "do not interrupt.",
        err=True,
    )
    try:
        vcs_git.expunge_paths(root, expunge_targets, scrub_identities=purge_ids)
    except vcs_git.GitFinalizeError as exc:
        typer.echo(
            f"openkos purge: the history rewrite SUCCEEDED, but finalize "
            f"failed -- {exc}",
            err=True,
        )
        _purge_clean_live_index(layout, purge_ids)
        _purge_clean_live_log(layout, purge_ids)
        _purge_rebuild_indexes(layout)
        raise typer.Exit(code=1) from exc
    except vcs_git.GitError as exc:
        typer.echo(
            f"openkos purge: failed -- the history rewrite did not complete -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    _purge_clean_live_index(layout, purge_ids)
    _purge_clean_live_log(layout, purge_ids)
    _purge_rebuild_indexes(layout)

    if scope == "source":
        typer.echo(
            f"openkos purge: permanently expunged {len(purge_ids)} "
            "concept(s) from ALL git history."
        )
    else:
        typer.echo(
            f"openkos purge: permanently expunged 'bundle/{canonical_id}.md' "
            "from ALL git history."
        )


@app.command()
def relate(
    source_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') to add the relation to.",
    ),
    rel: str = typer.Argument(
        ..., help="Relation type, e.g. 'references', 'depends_on'."
    ),
    target_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') the relation points to.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Write one deterministic typed edge -- `{target: target_id, type: rel}`
    -- into `source_id`'s `relations:` frontmatter (no LLM this slice, spec:
    "`relate` CLI Verb Writes A Typed Relation").

    Phase A (pure, no writes) mirrors `forget`'s gate shape: the current
    directory must already be a workspace (the same `config.require_workspace`
    gate every other write verb shares), or this refuses; `source_id` and
    `target_id` are EACH resolved via the same `_resolve_concept_path`
    `forget`/`merge` use -- rejecting an absolute id, any `..` segment, a
    reserved basename, or a nonexistent concept file, all as `ValueError`,
    all before any read (fail-closed existence on BOTH ends, spec: "Target
    Containment Consistent With Existing Verbs"). The two ids MUST resolve
    to DISTINCT concept files, else this refuses too, mirroring `merge`'s
    same-id guard. `rel` is validated via
    `model.relations.validate_relation_type`: rejected (no write) if empty
    or whitespace-only; accepted -- with an advisory note on stderr -- if it
    is not one of the seeded defaults (spec: "Seeded-But-Extensible Relation
    Vocabulary").

    The rest of Phase A builds the entire result in memory: `source_id`'s
    frontmatter is parsed (`okf.load_frontmatter`), its existing
    `relations:` decoded (`okf.decode_relations`), and the new
    `{target: target_id, type: rel}` edge appended UNLESS an identical
    `(target, type)` pair is already present -- in which case the existing
    list is kept as-is, so a repeated `relate` call is idempotent (spec:
    duplicate edge is not written twice). The full list is then
    re-encoded (`okf.encode_relations`, sorted, deterministic) and the
    source document re-rendered via `okf.dump_frontmatter`. A `log.md`
    entry is built in memory via `bundle_log.insert_log_entry` (a plain
    `**Relate**` line; no `index.md` entry -- a relation is an edit to an
    EXISTING catalog entry, not a new one, design decision 3).

    The preview printed before the confirm gate shows the source file, the
    relation being added, and the `relations:` entry count before/after.

    Confirm gate, identical precedence and mechanism to `forget`/`ingest`/
    `merge`: `--auto` skips the prompt outright; otherwise config
    `review: false` skips it the same way; otherwise, on a TTY,
    `typer.confirm` asks and aborts (exit 1) on decline; otherwise
    (non-TTY, no `--auto`) this refuses to write (exit 1), telling the user
    to re-run with `--auto`. Declining or refusing leaves the bundle
    completely untouched -- Phase A never writes anything.

    Phase B (after confirm) writes the source concept file
    (`fsio.write_atomic`, since it already exists) then `log.md`
    (`fsio.write_atomic`) -- content before the audit trail, mirroring
    `ingest`'s content-then-catalog ordering. Not transactional as a whole,
    matching every other write verb's documented limitation: a failure
    partway through is a benign, git-recoverable partial result, never
    silent corruption. Any failure, Phase A or Phase B, is caught and
    reported on stderr (exit 1), not a raw traceback.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos relate: refusing to relate -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)

        source_path, source_canonical = _resolve_concept_path(
            layout.bundle_dir, source_id
        )
        _, target_canonical = _resolve_concept_path(layout.bundle_dir, target_id)
        if source_canonical == target_canonical:
            raise ValueError(
                "source and target concept-ids must be distinct, both "
                f"resolved to {source_canonical!r}"
            )
        rel_type = validate_relation_type(rel)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos relate: refusing to relate -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    now = datetime.now(UTC)

    try:
        cfg = config.read_config(root)
        source_text = source_path.read_text(encoding="utf-8")
        log_text = log_path.read_text(encoding="utf-8")

        metadata, body = okf.load_frontmatter(source_text)
        existing_relations = okf.decode_relations(metadata)
        new_relation = okf.Relation(target=target_canonical, type=rel_type)
        already_present = any(
            relation.target == new_relation.target
            and relation.type == new_relation.type
            for relation in existing_relations
        )
        updated_relations = (
            existing_relations
            if already_present
            else [*existing_relations, new_relation]
        )
        metadata[okf.RELATIONS_KEY] = okf.encode_relations(updated_relations)
        new_source_text = okf.dump_frontmatter(metadata, body)

        if already_present:
            log_line = (
                f"**Relate**: [{source_canonical}](/{source_canonical}.md) already "
                f"has a {rel_type!r} relation to "
                f"[{target_canonical}](/{target_canonical}.md); no change."
            )
        else:
            log_line = (
                f"**Relate**: Added a {rel_type!r} relation from "
                f"[{source_canonical}](/{source_canonical}.md) to "
                f"[{target_canonical}](/{target_canonical}.md)."
            )
        new_log_text = bundle_log.insert_log_entry(
            log_text, now.astimezone().date(), log_line
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos relate: failed while preparing the relate -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos relate: proposed changes:")
    if already_present:
        preview_line = (
            f"  ~ bundle/{source_canonical}.md (relations: "
            f"{len(existing_relations)} -> {len(updated_relations)} entries; "
            f"unchanged: {{target: {target_canonical}, type: {rel_type}}} "
            "already present)"
        )
    else:
        preview_line = (
            f"  ~ bundle/{source_canonical}.md (relations: "
            f"{len(existing_relations)} -> {len(updated_relations)} entries; "
            f"+{{target: {target_canonical}, type: {rel_type}}})"
        )
    typer.echo(preview_line)
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos relate: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        fsio.write_atomic(source_path, new_source_text)
        fsio.write_atomic(log_path, new_log_text)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos relate: failed while writing the relate -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos relate: added a {rel_type!r} relation from "
        f"'bundle/{source_canonical}.md' to 'bundle/{target_canonical}.md' "
        f"({log_path.name} updated)."
    )


def _apply_link_rewrite_idempotently(
    text: str, *, file: str, rewrites: list[okf.LinkRewrite]
) -> str:
    """Apply `file`'s recorded inbound-link rewrites to `text`, but treat a
    file that ALREADY shows every rewrite's `new_link` at its recorded
    `offset` as a clean no-op -- returns `text` unchanged instead of
    raising. This is the idempotency guard `merge`'s retry story needs: a
    prior partial Phase-B attempt may have already migrated some OTHER
    file before failing on a later one, and re-running `merge` must not
    error out on a file that is already correctly rewritten.

    Delegates to `bundle_links.apply_link_rewrites` (the SAME bounded,
    offset-exact primitive U3 defined) for the normal not-yet-rewritten
    case, so the bounded-rewrite guarantee is never weakened -- this
    wrapper only adds the already-applied short-circuit."""
    file_rewrites = [rw for rw in rewrites if rw.file == file]
    if file_rewrites and all(
        text[rw.offset : rw.offset + len(rw.new_link)] == rw.new_link
        for rw in file_rewrites
    ):
        return text
    return bundle_links.apply_link_rewrites(text, file=file, rewrites=rewrites)


def _reverse_link_rewrite_idempotently(
    text: str, *, file: str, rewrites: list[okf.LinkRewrite]
) -> str:
    """Reverse `file`'s recorded inbound-link rewrites in `text`, but treat
    a file that ALREADY shows every rewrite's `old_link` at its recorded
    `offset` as a clean no-op -- returns `text` unchanged instead of
    raising. This is the reverse analog of `_apply_link_rewrite_idempotently`,
    closing the same half-completed-write retry trap for `unmerge`'s Phase
    B: each rewritten file is written atomically in one call covering ALL
    of that file's recorded rewrites at once, so on a retry a file is
    either fully reversed already (this short-circuit) or not reversed at
    all (delegates to the real primitive below, unchanged).

    Delegates to `bundle_links.reverse_link_rewrites` (the SAME bounded,
    offset-exact primitive U3 defined) for the normal not-yet-reversed
    case, so the fail-closed drift contract is never weakened: a file that
    matches NEITHER the fully-reversed nor the not-yet-reversed state still
    raises `ValueError` via that primitive (spec: Unmerge Achieves
    Round-Trip Parity's idempotence/safety contract)."""
    file_rewrites = [rw for rw in rewrites if rw.file == file]
    if file_rewrites and all(
        text[rw.offset : rw.offset + len(rw.old_link)] == rw.old_link
        for rw in file_rewrites
    ):
        return text
    return bundle_links.reverse_link_rewrites(text, file=file, rewrites=rewrites)


def _expected_post_merge_index_and_log(
    entry: okf.MergeLedgerEntry, *, survivor_id: str, absorbed_id: str
) -> tuple[str, str]:
    """Reconstruct what `index.md`/`log.md` looked like immediately AFTER
    the merge `entry` records, by replaying the SAME deterministic
    transforms `merge` itself applied to `entry.index_before`/
    `entry.log_before` -- `bundle_index.remove_index_entry` and the exact
    `**Merge**` log line, dated from `entry.merged_at`.

    This lets `unmerge`'s Phase A tell the difference between "index.md/
    log.md look exactly like the merge left them" and "something ELSE
    (another `ingest`/`forget`/unrelated `merge`) touched them since" --
    `unmerge` unconditionally overwrites both with the PRE-merge snapshot
    regardless, but the caller uses this to decide whether to surface a
    warning about that discard (principle #3: reviewable, not silent)."""
    expected_index, _ = bundle_index.remove_index_entry(entry.index_before, absorbed_id)
    merge_date = datetime.fromisoformat(entry.merged_at).astimezone().date()
    expected_log = bundle_log.insert_log_entry(
        entry.log_before,
        merge_date,
        f"**Merge**: Merged [{absorbed_id}](/{absorbed_id}.md) "
        f"into [{survivor_id}](/{survivor_id}.md).",
    )
    return expected_index, expected_log


@app.command()
def merge(
    survivor_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') that survives the merge.",
    ),
    absorbed_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') absorbed into the survivor.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Fuse two distinct concept-ids into one: the first DESTRUCTIVE
    entity-resolution write (spec: Merge Fuses Two Distinct Concept-IDs).

    Phase A (pure, no writes) mirrors `forget`'s gate shape exactly: the
    current directory must already be a workspace (the same
    `config.require_workspace` gate `ingest`/`forget`/`status` share), or
    this refuses; both `survivor_id`/`absorbed_id` are resolved via the
    same `_resolve_concept_path` `forget` uses -- rejecting an absolute id,
    any `..` segment, a reserved basename, or a nonexistent concept file,
    all as `ValueError`, all before any read. The two ids MUST resolve to
    DISTINCT concept files, else this refuses too (spec: Same-id or unknown
    id rejected) -- checked right after resolution, before any bundle file
    beyond the two concepts themselves is even read.

    The rest of Phase A builds the entire result in memory:
    `bundle.merge.plan_merge` (U2) computes the merged survivor document --
    body appended (never overwritten), scalar conflicts survivor-wins, list
    fields unioned deduped order-preserving, freshness/timestamp taken from
    whichever side is strictly more recent, and `sensitivity` RECOMPUTED via
    `combine_sensitivity` (never copied, high-water-mark) -- plus the full
    `merged_from` ledger entry (ADR-0002) capturing the pre-merge snapshot
    set `unmerge` needs for round-trip parity.
    `bundle.links.find_inbound_link_rewrites` (U3) then scans every OTHER
    bundle concept file (never the survivor or absorbed file themselves,
    and never `index.md`/`log.md`) for a markdown link resolving to
    `absorbed_id`, recording the rewrite each needs to instead point at
    `survivor_id`; a link inside a fenced code block is never matched.
    `index.md`'s bullet for `absorbed_id` is dropped via the same
    `bundle_index.remove_index_entry` `forget` uses (zero matches is drift,
    not an error); a `log.md` entry describing the merge is built via
    `bundle_log.insert_log_entry`.

    `bundle.merge.plan_merge` moves any outbound `relations:` the absorbed
    object bears onto the survivor -- retargeted, self-loops dropped,
    collisions deduped (spec: Reversible Typed-Relation Rewiring; ADR-0005)
    -- `merge` never refuses or blocks on typed relations.
    `bundle.relations.find_inbound_relation_rewrites` (D3) scans the SAME
    `other_files` whole-bundle snapshot `find_inbound_link_rewrites` already
    captured -- taken BEFORE any write, so both scans see identical
    pre-merge bytes -- for a bundle file OTHER than the survivor/absorbed
    pair whose OWN `relations:` targets `absorbed_id`, recording the
    whole-file snapshot `unmerge` needs to reverse it later (design D1/D3).

    The preview printed before the confirm gate surfaces exactly what a
    reviewer needs to approve a DESTRUCTIVE, hard-to-undo-by-hand write:
    the recomputed sensitivity outcome (`before -> after`), any dropped
    self-loop or deduped collision from the OUTBOUND merge (design D2/
    "Preview"), every OTHER file whose inbound link OR inbound relation
    will be rewritten, the catalog/log updates, the merged survivor file,
    and the absorbed file that will be removed.

    Confirm gate, identical precedence and mechanism to `forget`/`ingest`:
    `--auto` skips the prompt outright; otherwise config `review: false`
    skips it the same way; otherwise, on a TTY, `typer.confirm` asks and
    aborts (exit 1) on decline; otherwise (non-TTY, no `--auto`) this
    refuses to write (exit 1), telling the user to re-run with `--auto`.
    Declining or refusing leaves the bundle completely untouched -- Phase A
    never writes anything.

    Phase B (after confirm) writes, in order: `index.md` then `log.md`
    (`write_atomic`, catalog FIRST, mirroring `forget`'s ordering
    invariant), then applies EVERY OTHER file's inbound-link rewrite AND/OR
    inbound-relation retarget -- a file present in BOTH touches disjoint
    regions (body link vs. frontmatter `relations:`), so applying both to
    the same in-memory text is safe (design D5) -- then the merged
    survivor file (carrying the `merged_from` ledger, now including
    `relation_rewrites`), and finally removes the absorbed file LAST. The
    survivor/ledger is deliberately committed only AFTER every rewrite has
    succeeded: if a rewrite fails partway through, the survivor has NO
    ledger entry yet, so a clean re-run of this same command is never
    refused by `plan_merge`'s "already merged" guard, and the absorbed file
    -- untouched until the very last step -- is still there to retry
    against. Rewriting a file that some earlier, partial attempt already
    migrated to `survivor_id` is a no-op skip, not a failure, so a re-run
    after a partial rewrite failure completes cleanly. Not transactional
    as a whole, matching `forget`'s documented limitation: a failure
    partway through is a benign, git-recoverable partial result, never
    silent corruption. Any failure, Phase A or Phase B, is caught and
    reported on stderr (exit 1), not a raw traceback.

    Residual recovery note: a failure while rewriting inbound links
    (before the survivor/ledger is written) leaves no trace, so a plain
    re-run of `merge` completes it. A failure at or after the
    survivor/ledger write (including a failed absorbed-file removal) has
    already committed the `merged_from` entry, so a re-run is refused by
    `_reject_already_merged`; that narrow window is recoverable only via
    `git` or `unmerge`, same as `forget`'s own non-transactional
    limitation.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos merge: refusing to merge -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)

        survivor_path, survivor_canonical = _resolve_concept_path(
            layout.bundle_dir, survivor_id
        )
        absorbed_path, absorbed_canonical = _resolve_concept_path(
            layout.bundle_dir, absorbed_id
        )
        if survivor_canonical == absorbed_canonical:
            raise ValueError(
                "survivor and absorbed concept-ids must be distinct, both "
                f"resolved to {survivor_canonical!r}"
            )
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos merge: refusing to merge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    now = datetime.now(UTC)

    try:
        cfg = config.read_config(root)
        survivor_text = survivor_path.read_text(encoding="utf-8")
        absorbed_text = absorbed_path.read_text(encoding="utf-8")
        index_text = index_path.read_text(encoding="utf-8")
        log_text = log_path.read_text(encoding="utf-8")

        other_files: dict[str, str] = {}
        for path in sorted(layout.bundle_dir.rglob("*.md")):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            if path in (survivor_path, absorbed_path):
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            other_files[rel] = path.read_text(encoding="utf-8")

        link_rewrites = bundle_links.find_inbound_link_rewrites(
            other_files,
            absorbed_id=absorbed_canonical,
            survivor_id=survivor_canonical,
        )
        # Same `other_files` whole-bundle snapshot, captured ONCE above
        # BEFORE any write -- both scans see identical pre-merge bytes
        # (design D3).
        relation_rewrites = bundle_relations.find_inbound_relation_rewrites(
            other_files,
            absorbed_id=absorbed_canonical,
            survivor_id=survivor_canonical,
        )

        plan = bundle_merge.plan_merge(
            survivor_id=survivor_canonical,
            absorbed_id=absorbed_canonical,
            survivor_text=survivor_text,
            absorbed_text=absorbed_text,
            index_text=index_text,
            log_text=log_text,
            merged_at=now.isoformat(),
            link_rewrites=link_rewrites,
            relation_rewrites=relation_rewrites,
        )

        # The OUTBOUND merge_relations report (dropped self-loops, deduped
        # collisions) for the preview below: recomputed here from the SAME
        # survivor/absorbed metadata `plan_merge` -> `build_merged_document`
        # already used internally, since neither is exposed on `MergePlan`
        # (design: "preview report comes from merge_relations return").
        # Pure and deterministic -- calling it a second time is cheap and
        # never diverges from what `plan.merged_survivor` actually carries.
        survivor_metadata, _ = okf.load_frontmatter(survivor_text)
        absorbed_metadata, _ = okf.load_frontmatter(absorbed_text)
        _, dropped_self_loops, deduped_collisions = okf.merge_relations(
            okf.decode_relations(survivor_metadata),
            okf.decode_relations(absorbed_metadata),
            survivor_id=survivor_canonical,
            absorbed_id=absorbed_canonical,
        )

        new_index_text, removed = bundle_index.remove_index_entry(
            index_text, absorbed_canonical
        )
        new_log_text = bundle_log.insert_log_entry(
            log_text,
            now.astimezone().date(),
            f"**Merge**: Merged [{absorbed_canonical}](/{absorbed_canonical}.md) "
            f"into [{survivor_canonical}](/{survivor_canonical}.md).",
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos merge: failed while preparing the merge -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    rewritten_files = sorted({rewrite.file for rewrite in link_rewrites})
    relation_rewritten_files = sorted({rewrite.file for rewrite in relation_rewrites})
    touched_files = sorted(set(rewritten_files) | set(relation_rewritten_files))
    sensitivity_before = plan.ledger_entry.sensitivity_before or "(none)"
    sensitivity_after = plan.ledger_entry.sensitivity_after

    typer.echo("openkos merge: proposed changes:")
    typer.echo(f"  ~ sensitivity: {sensitivity_before} -> {sensitivity_after}")
    for relation in dropped_self_loops:
        typer.echo(f"  - drop self-loop: {relation.target} ({relation.type})")
    for relation in deduped_collisions:
        typer.echo(f"  ~ dedupe collision: {relation.target} ({relation.type})")
    for rel in rewritten_files:
        typer.echo(f"  ~ bundle/{rel} (rewrite inbound link(s) to survivor)")
    for rel in relation_rewritten_files:
        typer.echo(f"  ~ bundle/{rel} (retarget relation to survivor)")
    if removed >= 1:
        typer.echo(f"  ~ {index_path.name} (remove entry)")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")
    typer.echo(f"  ~ bundle/{survivor_canonical}.md (merged content)")
    typer.echo(f"  - bundle/{absorbed_canonical}.md")

    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos merge: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        fsio.write_atomic(index_path, new_index_text)
        fsio.write_atomic(log_path, new_log_text)

        # All inbound-link rewrites AND inbound-relation retargets are
        # computed BEFORE any of them (or the survivor/ledger) is written: a
        # compute-time failure on any one file thus leaves every other file
        # untouched, so a re-run's fresh Phase-A rescan sees every still-
        # absorbed-linked/related file exactly as it was and rewrites it
        # from scratch -- no file is left silently half-migrated by this
        # step. A file present in BOTH `rewritten_files` and
        # `relation_rewritten_files` gets both transforms applied to the
        # SAME in-memory text -- safe, since they touch disjoint regions
        # (body link vs. frontmatter `relations:`, design D5).
        rewritten_texts = {
            rel: bundle_relations.apply_relation_rewrites(
                _apply_link_rewrite_idempotently(
                    other_files[rel], file=rel, rewrites=link_rewrites
                ),
                file=rel,
                survivor_id=survivor_canonical,
                absorbed_id=absorbed_canonical,
                rewrites=relation_rewrites,
            )
            for rel in touched_files
        }
        for rel in touched_files:
            fsio.write_atomic(layout.bundle_dir / rel, rewritten_texts[rel])

        # The merged survivor (with its `merged_from` ledger) is committed
        # LAST among the writes, only once every rewrite above has
        # succeeded -- see this command's docstring for why that ordering
        # is what makes a mid-rewrite failure cleanly retryable.
        fsio.write_atomic(survivor_path, plan.merged_survivor)
        fsio.remove_file(absorbed_path)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos merge: failed while writing the merge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos merge: merged 'bundle/{absorbed_canonical}.md' into "
        f"'bundle/{survivor_canonical}.md' "
        f"({index_path.name}, {log_path.name} updated)."
    )


@app.command()
def unmerge(
    survivor_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') that survived a prior merge.",
    ),
    absorbed_id: str = typer.Argument(
        ...,
        help=(
            "Concept id expected to be the LIFO-tail absorbed_id of "
            "survivor's merged_from ledger."
        ),
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Reverse the most recent `merge` on `survivor_id`, restoring both
    concept files to byte parity with their pre-merge state (spec: Unmerge
    Achieves Round-Trip Parity) -- the reversal `merged_from` (ADR-0002)
    exists to make possible.

    `unmerge <survivor-id> <absorbed-id>` is two-arg and LIFO-ENFORCED: it
    targets ONLY the most-recent unreversed `merged_from` entry (the LIFO
    tail). `absorbed_id` MUST equal that tail entry's `absorbed_id`, else
    this refuses with a clean error and no write -- reversing a non-tail
    entry is unsafe, since a later merge's snapshots/rewrites may nest on
    top of an earlier one's (spec scenario: Absorbed-id is not the LIFO
    tail).

    Phase A (pure, no writes) mirrors `merge`'s gate shape: the current
    directory must already be a workspace (the same `config.require_workspace`
    gate every other verb shares), or this refuses; `survivor_id` is
    resolved via `_resolve_concept_path` (rejecting an absolute id, any
    `..` segment, a reserved basename, or a nonexistent concept file);
    `absorbed_id` is canonicalized via `_canonicalize_concept_id` ONLY --
    the SAME path-safety checks minus the existence check, since the
    absorbed file is EXPECTED to be absent (removed by the merge being
    reversed) until Phase B recreates it. `bundle.merge.plan_unmerge` (U2)
    then reads the survivor's `merged_from` ledger and computes the entire
    restoration in memory: the restored survivor (`survivor_before`,
    stripping this entry while retaining any earlier ones), the restored
    absorbed document (`absorbed_snapshot`), and the restored `index.md`/
    `log.md` (`index_before`/`log_before`). If a file already exists at the
    absorbed concept's path (drift since the merge), this refuses before
    any write (threat matrix: Unmerge restore collision). Every recorded
    inbound-link rewrite is then read from disk and reversed in memory via
    `bundle.links.reverse_link_rewrites` (U3) -- bounded to the exact
    recorded `{file, old_link, new_link, offset}` occurrence, never a
    blind replace-all -- which fails closed (`ValueError`) if a target file
    drifted since the merge (threat matrix: Link-file drift before unmerge).

    Every recorded `relation_rewrites` entry (design D1/D3; `[]` for a
    pre-slice-2a v1 ledger entry) is read from disk and reversed via
    `bundle.relations.reverse_relation_rewrites` -- an ABSOLUTE whole-file
    overwrite of the recorded pre-merge snapshot, never offset math (design
    D4's overlapping-LIFO proof relies on this exact property) -- but
    DRIFT-AWARE and FAIL-CLOSED, symmetric with the link path: the file's
    CURRENT on-disk text is compared against what THIS merge deterministically
    wrote there (recomputed by re-applying the retarget to the recorded
    pre-merge snapshot), and a mismatch (a legitimate edit landed on that
    file after the merge and before this `unmerge`) raises `ValueError`
    rather than silently clobbering that edit with the stale snapshot
    (CRITICAL fix, review correction batch). A file present in BOTH
    `link_rewrites` and `relation_rewrites` (design D5) has its inbound-link
    reversal SKIPPED entirely: the relation snapshot already restores that
    file's full bytes -- link included -- so also attempting
    `reverse_link_rewrites` on it would either corrupt the already-restored
    text or fail closed on a now-nonexistent `new_link` occurrence.

    The preview printed before the confirm gate surfaces every file this
    DESTRUCTIVE-in-reverse write will touch: each reversed inbound link,
    each restored relation snapshot, the catalog/log restoration, the
    restored survivor, and the recreated absorbed file.

    Confirm gate, identical precedence and mechanism to `merge`/`forget`:
    `--auto` skips the prompt outright; otherwise config `review: false`
    skips it the same way; otherwise, on a TTY, `typer.confirm` asks and
    aborts (exit 1) on decline; otherwise (non-TTY, no `--auto`) this
    refuses to write (exit 1), telling the user to re-run with `--auto`.
    Declining or refusing leaves the bundle completely untouched -- Phase A
    never writes anything.

    Phase B (after confirm) writes, in this order: `index.md` then
    `log.md` restored to their EXACT pre-merge bytes (`index_before`/
    `log_before`) first; then every reversed inbound-link file; then the
    recreated absorbed file (`absorbed_snapshot`); then the restored
    survivor (`survivor_before`, which drops this ledger entry while
    keeping any earlier ones intact) -- mirroring `merge`'s own ordering
    reasoning (the least-recoverable-if-lost artifacts land first, most
    easily git-recoverable last); and FINALLY, only once every restore
    above has landed, `log.md` is written a SECOND time with one
    `**Unmerge**` audit line appended on top of the just-restored
    `log_before` -- so the append-only audit trail net-grows by exactly
    one line documenting the round trip, even though every other file
    returns to its pre-merge bytes exactly. Not transactional as a whole,
    matching `merge`/`forget`'s documented limitation: a failure partway
    through is a benign, git-recoverable partial result, never silent
    corruption. Any failure, Phase A or Phase B, is caught and reported on
    stderr (exit 1), not a raw traceback.

    Limitation: `unmerge` restores `index.md`/`log.md` to their EXACT
    pre-merge snapshot (`index_before`/`log_before`), not a merge of that
    snapshot with whatever is on disk now. If another command (`ingest`,
    `forget`, or an unrelated `merge`) touched the catalog/log after this
    merge, that content is discarded when `unmerge` runs -- Phase A detects
    this drift and prints a warning in the preview before the confirm gate,
    but does not refuse; round-trip parity assumes a prompt unmerge.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos unmerge: refusing to unmerge -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)

        survivor_path, survivor_canonical = _resolve_concept_path(
            layout.bundle_dir, survivor_id
        )
        absorbed_canonical = _canonicalize_concept_id(absorbed_id)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos unmerge: refusing to unmerge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    now = datetime.now(UTC)

    try:
        cfg = config.read_config(root)
        survivor_text = survivor_path.read_text(encoding="utf-8")

        plan = bundle_merge.plan_unmerge(
            survivor_id=survivor_canonical,
            absorbed_id=absorbed_canonical,
            survivor_text=survivor_text,
        )

        absorbed_path = layout.bundle_dir / f"{absorbed_canonical}.md"
        if absorbed_path.exists():
            raise ValueError(
                f"cannot restore 'bundle/{absorbed_canonical}.md' -- a file "
                "already exists at that path"
            )

        current_index_text = index_path.read_text(encoding="utf-8")
        current_log_text = log_path.read_text(encoding="utf-8")
        expected_index_text, expected_log_text = _expected_post_merge_index_and_log(
            plan.entry,
            survivor_id=survivor_canonical,
            absorbed_id=absorbed_canonical,
        )
        catalog_log_drifted = (
            current_index_text != expected_index_text
            or current_log_text != expected_log_text
        )

        # D5: a file present in BOTH `link_rewrites` and `relation_rewrites`
        # is reversed EXCLUSIVELY via its `relation_rewrites` whole-file
        # snapshot below -- excluded here so `reverse_link_rewrites` is
        # never attempted on it (see this command's docstring).
        relation_rewrite_files = sorted(
            {rewrite.file for rewrite in plan.relation_rewrites}
        )
        rewritten_files = sorted(
            {rewrite.file for rewrite in plan.link_rewrites}
            - set(relation_rewrite_files)
        )
        other_texts = {
            rel: (layout.bundle_dir / rel).read_text(encoding="utf-8")
            for rel in rewritten_files
        }
        reversed_texts = {
            rel: _reverse_link_rewrite_idempotently(
                other_texts[rel], file=rel, rewrites=plan.link_rewrites
            )
            for rel in rewritten_files
        }
        # Whole-file absolute restore, never offset math (design D1/D3/D4) --
        # but DRIFT-AWARE and FAIL-CLOSED (CRITICAL fix, review correction
        # batch), symmetric with the link path above: each file's CURRENT
        # on-disk text is read and compared against what this merge
        # deterministically wrote there. A mismatch (a legitimate edit
        # landed on that file after the merge) raises `ValueError` here,
        # caught by this same try/except -- refusing the whole unmerge
        # before any write, rather than clobbering the edit with the stale
        # snapshot.
        relation_texts = {
            rel: (layout.bundle_dir / rel).read_text(encoding="utf-8")
            for rel in relation_rewrite_files
        }
        relation_reversed_texts = {
            rel: bundle_relations.reverse_relation_rewrites(
                relation_texts[rel],
                file=rel,
                survivor_id=survivor_canonical,
                absorbed_id=absorbed_canonical,
                rewrites=plan.relation_rewrites,
                link_rewrites=plan.link_rewrites,
            )
            for rel in relation_rewrite_files
        }

        new_log_text = bundle_log.insert_log_entry(
            plan.restored_log,
            now.astimezone().date(),
            f"**Unmerge**: Restored [{absorbed_canonical}](/{absorbed_canonical}.md) "
            f"from [{survivor_canonical}](/{survivor_canonical}.md).",
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos unmerge: failed while preparing the unmerge -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos unmerge: proposed changes:")
    for rel in rewritten_files:
        typer.echo(f"  ~ bundle/{rel} (reverse inbound link rewrite)")
    for rel in relation_rewrite_files:
        typer.echo(f"  ~ bundle/{rel} (restore pre-merge relations snapshot)")
    typer.echo(f"  ~ {index_path.name} (restore pre-merge contents)")
    typer.echo(
        f"  ~ {log_path.name} (restore pre-merge contents, append unmerge entry)"
    )
    typer.echo(f"  ~ bundle/{survivor_canonical}.md (restore pre-merge contents)")
    typer.echo(f"  + bundle/{absorbed_canonical}.md (restore)")
    if catalog_log_drifted:
        typer.echo(
            "Warning: index.md/log.md changed since the merge; unmerge "
            "restores the pre-merge snapshot and will discard those changes."
        )

    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos unmerge: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        # `index.md`/`log.md` are restored to their EXACT pre-merge bytes
        # FIRST -- if anything below fails, a retry (or manual inspection)
        # finds the catalog/log already back to a consistent pre-merge
        # state, which is idempotent to re-write on a retry.
        fsio.write_atomic(index_path, plan.restored_index)
        fsio.write_atomic(log_path, plan.restored_log)

        for rel in rewritten_files:
            fsio.write_atomic(layout.bundle_dir / rel, reversed_texts[rel])
        for rel in relation_rewrite_files:
            fsio.write_atomic(layout.bundle_dir / rel, relation_reversed_texts[rel])

        # The absorbed file is recreated BEFORE the survivor is restored:
        # the survivor's `merged_from` ledger entry (the only record of
        # `absorbed_snapshot`) is deliberately kept intact on disk until
        # the absorbed file it describes has actually landed, so a failure
        # between these two steps never loses the absorbed content --
        # it is still recoverable from the (not-yet-overwritten) survivor.
        fsio.write_atomic(absorbed_path, plan.restored_absorbed)
        fsio.write_atomic(survivor_path, plan.restored_survivor)

        # Only once every restore above has succeeded is `log.md` written a
        # SECOND time, with the `**Unmerge**` audit line appended on top of
        # the just-restored `log_before` -- the append-only trail net-grows
        # by exactly this one line.
        fsio.write_atomic(log_path, new_log_text)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos unmerge: failed while writing the unmerge -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos unmerge: restored 'bundle/{absorbed_canonical}.md' from "
        f"'bundle/{survivor_canonical}.md' "
        f"({index_path.name}, {log_path.name} updated)."
    )


_RECONCILE_ANCHOR_TEMPLATE = "<!-- okos:reconcile target={target} role={role} -->"
"""Hidden HTML-comment anchor keyed on the counterpart concept-id (design:
Interfaces / Contracts). `reconcile`'s idempotency check
(`_reconcile_anchor_present`) matches on `target=<id>` alone, ignoring
`role` and the note's heading level, so ANY prior anchor for that
counterpart -- however it got there -- suppresses a re-append."""

_RECONCILE_ANCHOR_RE = re.compile(r"<!-- okos:reconcile target=(\S+) role=(\w+) -->")


def _reconcile_anchor_present(body: str, counterpart_id: str) -> bool:
    """Return whether `body` already carries a `## Reconciliation` anchor
    referencing `counterpart_id` (any role) -- `reconcile`'s idempotency
    gate: a repeated call for the same pair never re-appends a duplicate
    note (spec: Idempotent Re-run)."""
    return any(
        match.group(1) == counterpart_id
        for match in _RECONCILE_ANCHOR_RE.finditer(body)
    )


_ReconcileRole = Literal["reconciled", "supersedes", "superseded"]


def _reconcile_sentence(
    role: _ReconcileRole, counterpart_id: str, date_str: str
) -> str:
    """One human-readable sentence for a `## Reconciliation` note, per
    `role` (design: Interfaces / Contracts) -- `reconciled` (symmetric,
    both coexist), `supersedes` (this concept wins), or `superseded`
    (label-only, no status change). `role` is a closed `Literal`, and any
    other value raises defensively (rather than silently falling through to
    the "superseded" sentence) so a typo can never mislabel a note."""
    link = f"[{counterpart_id}](/{counterpart_id}.md)"
    if role == "reconciled":
        return f"Reconciled with {link} on {date_str} (both coexist)."
    if role == "supersedes":
        return f"Supersedes {link} as of {date_str} (this concept wins)."
    if role == "superseded":
        return f"Superseded by {link} as of {date_str} (label-only, no status change)."
    raise ValueError(f"unexpected reconciliation role {role!r}")


def _reconciliation_note(
    *, counterpart_id: str, role: _ReconcileRole, date_str: str
) -> str:
    """Build one full `## Reconciliation` body note: an h2 heading (chosen
    over `#` to avoid a second top-level heading alongside the concept's own
    title, design note), the hidden anchor keyed on `counterpart_id`, and
    one sentence linking to the counterpart."""
    anchor = _RECONCILE_ANCHOR_TEMPLATE.format(target=counterpart_id, role=role)
    sentence = _reconcile_sentence(role, counterpart_id, date_str)
    return f"## Reconciliation\n{anchor}\n{sentence}\n"


def _append_reconciliation_note(body: str, note: str) -> str:
    """Append `note` to `body` as a new trailing section, additive-only --
    never overwrites existing content (mirrors
    `okf.build_merged_document`'s body-append separator math)."""
    new_body = body.rstrip("\n") + "\n\n" + note
    if not new_body.endswith("\n"):
        new_body += "\n"
    return new_body


def _add_relation_if_absent(
    relations: list[okf.Relation], new_relation: okf.Relation
) -> tuple[list[okf.Relation], bool]:
    """Append `new_relation` to `relations` unless an identical
    `(target, type)` pair is already present, mirroring `relate`'s
    idempotent dedup (task 2.3). Returns the possibly-extended list and
    whether an entry was actually added."""
    already_present = any(
        relation.target == new_relation.target and relation.type == new_relation.type
        for relation in relations
    )
    if already_present:
        return relations, False
    return [*relations, new_relation], True


def _existing_reconciliation_state(
    *,
    relations_a: list[okf.Relation],
    relations_b: list[okf.Relation],
    canonical_a: str,
    canonical_b: str,
) -> tuple[Literal["none", "symmetric", "directional"], str | None]:
    """Classify the pair's EXISTING reconciliation state from
    already-loaded (pre-mutation) relations, gathering both `supersedes`
    directions and the symmetric `reconciled_with` edge between `{a, b}` --
    the CRITICAL refuse-on-conflict gate (fix: a mode-switch re-run must
    never add a second, contradictory reconciliation resolution). Returns
    `("none", None)` when the pair carries no prior reconciliation,
    `("symmetric", None)` when a `reconciled_with` edge already links them,
    or `("directional", winner)` when a `supersedes` edge already points
    winner -> loser."""
    a_supersedes_b = any(
        relation.target == canonical_b and relation.type == "supersedes"
        for relation in relations_a
    )
    b_supersedes_a = any(
        relation.target == canonical_a and relation.type == "supersedes"
        for relation in relations_b
    )
    if a_supersedes_b:
        return "directional", canonical_a
    if b_supersedes_a:
        return "directional", canonical_b

    symmetric = any(
        relation.target == canonical_b and relation.type == "reconciled_with"
        for relation in relations_a
    ) or any(
        relation.target == canonical_a and relation.type == "reconciled_with"
        for relation in relations_b
    )
    if symmetric:
        return "symmetric", None
    return "none", None


def _reconciliation_state_description(
    mode: Literal["none", "symmetric", "directional"], winner: str | None
) -> str:
    """Human-readable description of an existing reconciliation state, for
    the refuse-on-conflict error message."""
    if mode == "directional":
        return f"a directional reconciliation ({winner!r} supersedes its counterpart)"
    return "a symmetric reconciliation ('reconciled_with')"


@app.command()
def reconcile(
    id_a: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') of one concept in the pair.",
    ),
    id_b: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') of the other concept in the pair.",
    ),
    winner: str | None = typer.Option(
        None,
        "--winner",
        help=(
            "Concept id (must resolve to id_a or id_b) that supersedes its "
            "counterpart. Omit for a symmetric 'reconciled_with' reconciliation."
        ),
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Record a human's resolution of a contradiction between two concepts:
    the first WRITE verb of the freshness-lint-v1 arc (spec: Reconcile
    Command Specification). No LLM in the write path -- `id_a`/`id_b`/
    `--winner` are plain concept-id arguments; this never invokes
    contradiction detection.

    Phase A (pure, no writes) mirrors `relate`'s gate shape: the current
    directory must already be a workspace (the same `config.require_workspace`
    gate every other write verb shares), or this refuses; `id_a` and `id_b`
    are EACH resolved via the same `_resolve_concept_path` `forget`/`relate`/
    `merge` use -- rejecting an absolute id, any `..` segment, a reserved
    basename, or a nonexistent concept file, all as `ValueError`, all before
    any read. The two ids MUST resolve to DISTINCT concept files, else this
    refuses too (self-pair rejected). If `--winner <id>` is given, it is
    ALSO resolved via `_resolve_concept_path` and its canonical id MUST
    equal EXACTLY one of the two pair members -- else this refuses (no
    write, spec: "--winner gamma (not in pair {alpha,beta})"); the other
    pair member becomes the loser.

    Before building any new edge, `_existing_reconciliation_state` gathers
    the pair's EXISTING reconciliation edges (any `reconciled_with` or
    `supersedes` already linking `id_a`/`id_b`, in either direction) and
    classifies them as `"none"`, `"symmetric"`, or `"directional"` (with a
    winner). This is compared to what THIS invocation requests: if the pair
    carries NO prior reconciliation, this proceeds as a fresh write; if the
    prior state matches the request EXACTLY (same mode, same winner for
    `--winner`), this proceeds to the ordinary idempotent no-op path below;
    if the prior state DIFFERS (a mode switch, e.g. symmetric then
    `--winner`, or an opposite `--winner`), this REFUSES here (`ValueError`,
    exit 1, ZERO writes) rather than adding a second, contradictory
    resolution -- a pair can carry AT MOST ONE reconciliation resolution
    written by `reconcile` (CRITICAL fix: a mode-switch re-run used to dedup
    the new edge only on `(target, type)`, so a DIFFERENT edge type was
    added alongside the stale one, while the anchor-gated note below matches
    on `target` alone and is blind to `role`, so it silently kept describing
    the earlier resolution -- frontmatter and body note went out of sync
    with no way to repair it on a later run).

    The rest of Phase A builds the entire result in memory. With no
    `--winner`, a SYMMETRIC `reconciled_with` edge is added to BOTH
    concepts (each targeting the other, design: "Symmetric edge = one
    outbound edge per side"); with `--winner`, a single DIRECTIONAL
    `supersedes` edge is added on the winner's document only, pointing at
    the loser -- no `superseded_by` back-edge; `supersedes` is LABEL-ONLY,
    this verb never writes `status` or any deprecation field (spec:
    Additive-Only, No Status/Lifecycle Write). Either edge shape dedups on
    `(target, type)` (`_add_relation_if_absent`), mirroring `relate`'s
    idempotency -- safe now that the refuse-on-conflict gate above has
    already ruled out a mode switch reaching this point. Each side then gets
    a `## Reconciliation` body note appended -- unless a hidden `<!--
    okos:reconcile target=<counterpart> ... -->` anchor for that counterpart
    is already present (`_reconcile_anchor_present`), in which case the note
    is skipped (idempotent re-run, spec: Idempotent Re-run). All writes are
    additive: existing body content and relations are preserved verbatim,
    never overwritten. A `log.md` entry is built via
    `bundle_log.insert_log_entry`, in one of three shapes: symmetric-new,
    winner-new, or no-change (when nothing on either side actually changed
    -- a clean re-run).

    Confirm gate, identical precedence and mechanism to `relate`/`merge`/
    `forget`: `--auto` skips the prompt outright; otherwise config
    `review: false` skips it the same way; otherwise, on a TTY,
    `typer.confirm` asks and aborts (exit 1) on decline; otherwise
    (non-TTY, no `--auto`) this refuses to write (exit 1), telling the user
    to re-run with `--auto`. Declining or refusing leaves the bundle
    completely untouched -- Phase A never writes anything.

    Phase B (after confirm) writes, in order: `id_a`'s document, then
    `id_b`'s document (both `fsio.write_atomic`, since both already exist),
    then `log.md` -- content before the audit trail, mirroring every other
    write verb's ordering. Not transactional as a whole, matching every
    other write verb's documented limitation: a failure partway through is
    a benign, git-recoverable partial result -- and, since every write here
    is additive, a re-run safely completes whatever landed without
    duplicating it (idempotency above). Any failure, Phase A or Phase B, is
    caught and reported on stderr (exit 1), not a raw traceback.

    Reversibility is git-undo only: no ledger, no `unreconcile` (design:
    "Reversibility = git-undo only -- NO ledger, NO unreconcile"), unlike
    `merge`/`unmerge`'s `merged_from` ledger, which exists only because
    `merge` is lossy; `reconcile` never deletes or overwrites content, so a
    ledger here would be over-engineering.

    Threat matrix: N/A -- no routing, shell, subprocess, VCS/PR automation,
    or process-integration boundary. Write safety is the confirm-gate +
    atomic writes + additive/git-undo, same as every prior write verb.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos reconcile: refusing to reconcile -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)

        path_a, canonical_a = _resolve_concept_path(layout.bundle_dir, id_a)
        path_b, canonical_b = _resolve_concept_path(layout.bundle_dir, id_b)
        if canonical_a == canonical_b:
            raise ValueError(
                f"id_a and id_b must be distinct, both resolved to {canonical_a!r}"
            )

        winner_canonical: str | None = None
        loser_canonical: str | None = None
        if winner is not None:
            _, winner_resolved = _resolve_concept_path(layout.bundle_dir, winner)
            if winner_resolved == canonical_a:
                winner_canonical, loser_canonical = canonical_a, canonical_b
            elif winner_resolved == canonical_b:
                winner_canonical, loser_canonical = canonical_b, canonical_a
            else:
                raise ValueError(
                    f"--winner {winner!r} must resolve to one of the pair "
                    f"({canonical_a!r}, {canonical_b!r}), got {winner_resolved!r}"
                )
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos reconcile: refusing to reconcile -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    now = datetime.now(UTC)
    today = now.astimezone().date()
    date_str = today.isoformat()

    try:
        cfg = config.read_config(root)
        text_a = path_a.read_text(encoding="utf-8")
        text_b = path_b.read_text(encoding="utf-8")
        log_text = log_path.read_text(encoding="utf-8")

        metadata_a, body_a = okf.load_frontmatter(text_a)
        metadata_b, body_b = okf.load_frontmatter(text_b)
        relations_a = okf.decode_relations(metadata_a)
        relations_b = okf.decode_relations(metadata_b)

        # CRITICAL refuse-on-conflict gate (before ANY edge is computed or
        # written): a pair may carry AT MOST ONE reconciliation resolution
        # written by `reconcile`. Compare the pair's EXISTING state to the
        # one requested by THIS invocation -- an unrelated (`"none"`) prior
        # state proceeds as a fresh write, an IDENTICAL prior state falls
        # through to the ordinary idempotent no-op path below, but a
        # DIFFERENT prior state (mode switch, or opposite `--winner`) is
        # refused here, with zero writes -- this is what prevents a 2nd
        # `supersedes` edge from coexisting with a stale `reconciled_with`
        # edge (or a 2nd, opposite-direction `supersedes` edge), and
        # prevents the `## Reconciliation` note from going stale relative
        # to frontmatter (the note-append gate below is anchor-keyed on
        # `target` alone and blind to `role`, so it cannot itself repair a
        # mismatched note on a later run).
        existing_mode, existing_winner = _existing_reconciliation_state(
            relations_a=relations_a,
            relations_b=relations_b,
            canonical_a=canonical_a,
            canonical_b=canonical_b,
        )
        requested_mode: Literal["symmetric", "directional"] = (
            "directional" if winner_canonical is not None else "symmetric"
        )
        if existing_mode != "none" and (
            existing_mode != requested_mode or existing_winner != winner_canonical
        ):
            description = _reconciliation_state_description(
                existing_mode, existing_winner
            )
            raise ValueError(
                f"concepts {canonical_a!r} and {canonical_b!r} are already "
                f"reconciled as {description}; reconcile will not overwrite "
                "an existing resolution. To change it, edit the concepts "
                "manually or revert with git, then re-run"
            )

        edge_added_a = False
        edge_added_b = False
        role_a: _ReconcileRole
        role_b: _ReconcileRole
        if winner_canonical is None:
            relations_a, edge_added_a = _add_relation_if_absent(
                relations_a, okf.Relation(target=canonical_b, type="reconciled_with")
            )
            relations_b, edge_added_b = _add_relation_if_absent(
                relations_b, okf.Relation(target=canonical_a, type="reconciled_with")
            )
            role_a, role_b = "reconciled", "reconciled"
        elif winner_canonical == canonical_a:
            relations_a, edge_added_a = _add_relation_if_absent(
                relations_a, okf.Relation(target=canonical_b, type="supersedes")
            )
            role_a, role_b = "supersedes", "superseded"
        else:
            relations_b, edge_added_b = _add_relation_if_absent(
                relations_b, okf.Relation(target=canonical_a, type="supersedes")
            )
            role_a, role_b = "superseded", "supersedes"

        note_added_a = False
        if not _reconcile_anchor_present(body_a, canonical_b):
            body_a = _append_reconciliation_note(
                body_a,
                _reconciliation_note(
                    counterpart_id=canonical_b, role=role_a, date_str=date_str
                ),
            )
            note_added_a = True

        note_added_b = False
        if not _reconcile_anchor_present(body_b, canonical_a):
            body_b = _append_reconciliation_note(
                body_b,
                _reconciliation_note(
                    counterpart_id=canonical_a, role=role_b, date_str=date_str
                ),
            )
            note_added_b = True

        metadata_a[okf.RELATIONS_KEY] = okf.encode_relations(relations_a)
        metadata_b[okf.RELATIONS_KEY] = okf.encode_relations(relations_b)
        new_text_a = okf.dump_frontmatter(metadata_a, body_a)
        new_text_b = okf.dump_frontmatter(metadata_b, body_b)

        changed = edge_added_a or edge_added_b or note_added_a or note_added_b
        if not changed:
            log_line = (
                f"**Reconcile**: [{canonical_a}](/{canonical_a}.md) and "
                f"[{canonical_b}](/{canonical_b}.md) are already reconciled; "
                "no change."
            )
        elif winner_canonical is None:
            log_line = (
                "**Reconcile**: Recorded a symmetric 'reconciled_with' "
                f"between [{canonical_a}](/{canonical_a}.md) and "
                f"[{canonical_b}](/{canonical_b}.md)."
            )
        else:
            log_line = (
                f"**Reconcile**: [{winner_canonical}](/{winner_canonical}.md) "
                f"supersedes [{loser_canonical}](/{loser_canonical}.md) "
                "(recorded 'supersedes')."
            )
        new_log_text = bundle_log.insert_log_entry(log_text, today, log_line)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos reconcile: failed while preparing the reconcile -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos reconcile: proposed changes:")
    typer.echo(
        f"  ~ bundle/{canonical_a}.md (relation "
        f"{'added' if edge_added_a else 'unchanged'}; note "
        f"{'appended' if note_added_a else 'already present'})"
    )
    typer.echo(
        f"  ~ bundle/{canonical_b}.md (relation "
        f"{'added' if edge_added_b else 'unchanged'}; note "
        f"{'appended' if note_added_b else 'already present'})"
    )
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos reconcile: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        fsio.write_atomic(path_a, new_text_a)
        fsio.write_atomic(path_b, new_text_b)
        fsio.write_atomic(log_path, new_log_text)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos reconcile: failed while writing the reconcile -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if winner_canonical is None:
        typer.echo(
            "openkos reconcile: recorded a symmetric reconciliation between "
            f"'bundle/{canonical_a}.md' and 'bundle/{canonical_b}.md' "
            f"({log_path.name} updated)."
        )
    else:
        typer.echo(
            f"openkos reconcile: recorded '{winner_canonical}' as superseding "
            f"'{loser_canonical}' ({log_path.name} updated)."
        )


RECENT_ACTIVITY_LIMIT = 5
"""How many `log.md` bullets `status` shows under "Recent activity" (D4).

Display policy, not parsing policy -- `bundle/log.py::read_recent_entries`
stays free of this constant and takes it as a parameter instead."""


@app.command()
def status() -> None:
    """Report what the bundle currently contains: read-only, Phase-A only.

    Refuses (exit 1) via the shared `config.require_workspace` gate (D1) if
    the current directory is not an initialized workspace -- the SAME check
    `ingest` uses -- printing the reason to stderr with no raw traceback.
    This is the ONLY non-zero exit path.

    On a workspace, sequences three reads and renders their result as plain
    text via `typer.echo`, always exiting 0: `okf.survey_bundle` scans
    `bundle/**/*.md` ONCE for source/concept counts and §9 findings (D2) --
    counts always reflect the disk scan, never `index.md` alone, so catalog
    drift after an interrupted `ingest` is still visible; `log.md` is read
    and passed through `bundle_log.read_recent_entries` for the most recent
    `RECENT_ACTIVITY_LIMIT` entries, newest-first -- an unreadable or
    malformed `log.md` degrades to a notice (`except (OSError, ValueError)`)
    rather than failing the whole command (D5), because recent activity is
    the one nice-to-have `status` exists to show, not the counts or the
    conformance findings. `survey_bundle`'s findings (missing/unparseable
    frontmatter, unreadable files) are informational: their presence never
    changes the exit code (spec: Needs-Attention via §9 Conformance).

    No file under the workspace is ever created, modified, or deleted, and
    no `--json` or other structured output mode is offered (spec: Read-Only
    and Human-Readable Only).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos status: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    survey = okf.survey_bundle(layout.bundle_dir)

    try:
        log_text = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
        recent_entries = bundle_log.read_recent_entries(log_text, RECENT_ACTIVITY_LIMIT)
    except (OSError, ValueError):
        recent_entries = None

    typer.echo(f"openkos status: workspace at {root}")
    typer.echo()
    typer.echo("Bundle contents:")
    typer.echo(f"  Sources:  {survey.sources}")
    typer.echo(f"  Concepts: {survey.concepts}")
    typer.echo()
    typer.echo("Recent activity:")
    if recent_entries is None:
        typer.echo("  Recent activity unavailable — log.md could not be read/parsed.")
    elif not recent_entries:
        typer.echo("  No activity recorded yet.")
    else:
        for entry in recent_entries:
            typer.echo(f"  {entry.date}  {entry.text}")
    typer.echo()
    typer.echo("Needs attention:")
    if not survey.findings:
        typer.echo("  Nothing needs attention.")
    else:
        for finding in survey.findings:
            typer.echo(f"  {finding}")


@app.command()
def lint() -> None:
    """Health-check the bundle for stale stamps and orphan pages: read-only, Phase-A only.

    The SECOND read command, mirroring `status`'s shape exactly: no Phase B,
    no confirm gate, no `--auto`. Refuses (exit 1) via the shared
    `config.require_workspace` gate (D1) if the current directory is not an
    initialized workspace -- the SAME check `ingest`/`status` use -- printing
    the reason to stderr with no raw traceback. A permission-denied
    `bundle/index.md` that passes `require_workspace`'s `is_file()` check but
    fails to `read_text()` is the only OTHER non-zero path: caught here and
    reported the same way, never left to raise a raw traceback.

    On a workspace, the flow is: `read_config(root)`'s `freshness_window`
    and `volatility_windows` are resolved together via
    `lint.resolve_windows` (freshness-lint-v1, Q4) into one
    `lint.VolatilityWindows` -- an invalid/zero/negative/non-mapping value,
    for any tier, never raises; it falls back to the packaged default and
    prints a fallback-notice line instead. `today` is computed ONCE via
    `datetime.now(UTC).date()` and injected into `lint.check_stale_stamps`
    (the clock is never read inside `lint.py` itself, keeping every scan
    deterministic and testable). `lint.collect_docs` reuses `okf._iter_docs`
    for the single walk, returning `(docs, skip_notices)` so a skipped
    file never silently shrinks the scan; `lint.check_stale_stamps` scans
    inline `(as of YYYY-MM-DD)` body stamps (never the `freshness` field),
    resolving each doc's own stale window via `lint.window_for_doc`'s
    per-concept-override -> per-type-default -> global-fallback precedence
    (a `static`-tier doc, by override or type default, is never flagged);
    `lint.check_orphans` scans markdown links from `index.md` and every
    doc body (never `log.md` -- see its docstring for why).

    The volatility-window and skip notices feed one `lint.LintReport`, rendered
    under two sections, `Stale stamps:` and `Orphan pages:`, each with its
    own empty-state line when there is nothing to report. Every
    successful read exits 0, whether the bundle is clean or
    has findings (spec: Non-Gating Exit Contract) -- `lint` is NOT a CI
    gate in MVP-1. No file under the workspace is ever created, modified,
    or deleted, and no `--json` or other structured output mode is offered
    (spec: Read-Only and Human-Readable Only).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos lint: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
        index_text = (layout.bundle_dir / "index.md").read_text(encoding="utf-8")
        docs, skip_notices = lint_check.collect_docs(layout.bundle_dir)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos lint: failed while reading the workspace -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    windows, window_notices = lint_check.resolve_windows(cfg)
    today = datetime.now(UTC).date()
    stale = lint_check.check_stale_stamps(docs, today=today, windows=windows)
    orphans = lint_check.check_orphans(docs, index_text=index_text)
    notices = window_notices + skip_notices
    report = lint_check.LintReport(stale=stale, orphans=orphans, notices=notices)

    typer.echo(f"openkos lint: workspace at {root}")
    for notice_line in report.notices:
        typer.echo(notice_line)
    typer.echo()
    typer.echo("Stale stamps:")
    if not report.stale:
        typer.echo("  No stale stamps.")
    else:
        for finding in report.stale:
            typer.echo(f"  {finding.path}: {finding.detail}")
    typer.echo()
    typer.echo("Orphan pages:")
    if not report.orphans:
        typer.echo("  No orphan pages.")
    else:
        for finding in report.orphans:
            typer.echo(f"  {finding.path}: {finding.detail}")


@app.command()
def duplicates(
    include_deprecated: bool = typer.Option(
        False,
        "--include-deprecated",
        help="Include deprecated and superseded concepts (excluded by default).",
    ),
) -> None:
    """Report cross-source candidate duplicates: read-only, Phase-A only.

    A THIRD read command, mirroring `status`/`lint`'s shape exactly: no
    Phase B, no confirm gate, no `--auto`. Refuses (exit 1) via the shared
    `config.require_workspace` gate (D1) if the current directory is not an
    initialized workspace -- the SAME check `status`/`lint` use -- printing
    the reason to stderr with no raw traceback.

    On a workspace, `resolution.find_candidates` performs one read-only,
    whole-bundle pass and returns candidate groups: same-type OKF objects
    that MIGHT be the same real-world entity, at a HIGH (exact normalized
    title) or LOW (near-match) confidence tier. This is a REPORT ONLY --
    `duplicates` never merges, deletes, or otherwise adjudicates a
    candidate; that is reserved for a later, explicitly-named `resolve`/
    `merge` verb (spec: Read-Only CLI Candidate Report Verb).

    Output is grouped by OKF `type`, then by tier, mirroring
    `find_candidates`'s own stable ordering: each group renders its type,
    tier, member concept_ids, and the trigger (the shared normalized key
    for HIGH, the similarity score for LOW). An empty result renders a
    clear "No candidates found." line instead of an empty section. Every
    successful read exits 0, whether or not any candidates are found (spec:
    No candidates still exits 0). No file under the workspace is ever
    created, modified, or deleted, and no `--json` or other structured
    output mode is offered (spec: Read-Only and Human-Readable Only).

    Unless `--include-deprecated` is passed, deprecated/superseded concepts
    (status-aware-retrieval) are excluded from every candidate group --
    `duplicates` shares `adjudicate`'s `find_candidates` call and, per the
    locked scope decision, gets the SAME `--include-deprecated` flag for
    consistency.
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos duplicates: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    groups = find_candidates(layout.bundle_dir, include_deprecated=include_deprecated)

    typer.echo(f"openkos duplicates: workspace at {root}")
    typer.echo()
    if not groups:
        typer.echo("No candidates found.")
        return

    for group in groups:
        tier_label = "HIGH" if group.tier is Tier.HIGH else "LOW"
        typer.echo(f"[{tier_label}] {group.okf_type} -- {group.trigger}")
        for member_id in group.member_ids:
            typer.echo(f"  - {member_id}")
        typer.echo()


@app.command()
def adjudicate(
    same_only: bool = typer.Option(
        False,
        "--same-only",
        help="Only show SAME-verdict groups in the printed report.",
    ),
    include_deprecated: bool = typer.Option(
        False,
        "--include-deprecated",
        help="Include deprecated and superseded concepts (excluded by default).",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
) -> None:
    """LLM-adjudicate cross-source candidate duplicates: read-only, like `query`.

    A FOURTH read command, mirroring `query`'s wiring exactly: the shared
    `config.require_workspace` gate (D1), then a Phase-A `read_config` guard
    (`except (OSError, ValueError)`, lint parity), then a real
    `OllamaClient(model=cfg.model)` is built and injected -- as the
    `LLMBackend` -- into `resolution.find_candidates` followed by
    `resolution.adjudication.adjudicate_candidates`. Distinct from the
    reserved `resolve`/`merge` verbs (slice 3): `adjudicate` never merges,
    writes, or decides -- it only prints a verdict for human review. No
    `--auto`, no confirmation gate, no `--json` or other structured mode.

    Output mirrors `duplicates`'s grouped render (type, tier, trigger,
    members) with each group's verdict and rationale appended. The parsed
    confidence is intentionally NOT rendered (issue #138): a local model
    returns a flat, uncalibrated value, so a two-decimal number would imply
    a precision it does not have.
    `--same-only` is a DISPLAY-only filter: it hides non-`SAME` verdicts from
    the printed report, but `adjudicate_candidates` always receives -- and
    returns -- every candidate group regardless of the flag; the library
    itself never filters.

    A no-model/no-Ollama run degrades via the SAME 3-tier ORDERED handler
    `query` uses -- `OllamaUnavailable`, then `OllamaModelNotFound`, then the
    generic `OllamaError` fallback -- each with its own actionable stderr
    message, exit 1, and zero writes.

    Unless `--include-deprecated` is passed, deprecated/superseded concepts
    (status-aware-retrieval) are excluded from the `find_candidates` call
    that feeds `adjudicate_candidates` -- `adjudicate` uses candidates, so it
    threads the flag into `find_candidates`, not into `adjudicate_candidates`
    itself.

    Unless `--include-confidential` is passed, confidential concepts
    (sensitivity-fail-closed-filter) are excluded at the MEMBER level, inside
    `adjudicate_candidates` itself -- distinct from the deprecated axis above,
    a confidential member is dropped from a group's `member_ids` before its
    content is ever read, rather than dropping the whole group upstream.

    No file under the workspace is ever created, modified, or deleted (spec:
    Verb renders verdicts with zero writes).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos adjudicate: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos adjudicate: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    candidates = find_candidates(
        layout.bundle_dir, include_deprecated=include_deprecated
    )
    llm = OllamaClient(model=cfg.model)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir, include_confidential=include_confidential
    )
    try:
        results = adjudicate_candidates(
            candidates,
            bundle_dir=layout.bundle_dir,
            llm=llm,
            include_confidential=include_confidential,
        )
    except OllamaUnavailable as exc:
        typer.echo(
            f"openkos adjudicate: failed -- {exc}. Start it with `ollama serve`, "
            f"then try again.{_DOCTOR_HINT}",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except OllamaModelNotFound as exc:
        typer.echo(
            f"openkos adjudicate: failed -- model '{cfg.model}' is not "
            f"installed. Pull it with `ollama pull {cfg.model}`, then try "
            "again.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    # The two specific handlers above MUST precede this generic handler:
    # both `OllamaUnavailable` and `OllamaModelNotFound` subclass
    # `OllamaError`, so reordering would silently funnel them into this
    # fallback and lose their actionable remediation messages (mirrors
    # `query`'s ordering).
    except OllamaError as exc:
        typer.echo(f"openkos adjudicate: failed -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"openkos adjudicate: workspace at {root}")
    typer.echo()
    if not results:
        typer.echo("No candidates found.")
        return

    displayed = [
        result for result in results if not same_only or result.verdict is Verdict.SAME
    ]
    if not displayed:
        typer.echo("No SAME-verdict candidates to display (--same-only).")
        return

    for result in displayed:
        group = result.candidate
        tier_label = "HIGH" if group.tier is Tier.HIGH else "LOW"
        typer.echo(f"[{tier_label}] {group.okf_type} -- {group.trigger}")
        for member_id in group.member_ids:
            typer.echo(f"  - {member_id}")
        # Confidence is intentionally NOT shown: a local model returns a
        # flat, uncalibrated value (issue #138), so a fake-precise two-decimal
        # number would invite trust it has not earned. The value is still
        # parsed and kept on `AdjudicatedCandidate` for future thresholding.
        typer.echo(f"  verdict: {result.verdict.value.upper()}")
        typer.echo(f"  rationale: {result.rationale}")
        typer.echo()


@app.command("suggest-relations")
def suggest_relations_cmd(
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
) -> None:
    """LLM-suggest a relation `type` for every existing UNTYPED body-link
    edge: read-only, like `adjudicate`.

    A FIFTH read command, mirroring `adjudicate`'s wiring exactly: the
    shared `config.require_workspace` gate (D1), then a Phase-A
    `read_config` guard (`except (OSError, ValueError)`, lint parity), then
    a real `OllamaClient(model=cfg.model)` is built and injected -- as the
    `LLMBackend` -- into `resolution.edge_typing.suggest_relations`, which
    OWNS the internal `openkos.graph` read (design D2/D6, "No CLI Surface"):
    this module imports ONLY from `openkos.resolution.edge_typing`, never
    `openkos.graph` directly.

    `suggest-relations` never writes, merges, or decides -- it only prints a
    suggested `type` + rationale per untyped edge for human review, plus a
    closing hint pointing at the existing `relate` verb, the ONLY write path
    for an accepted suggestion (spec: Human-In-The-Loop Write Path
    Unchanged). No `--auto`, no confirmation gate, no `--json` or other
    structured mode.

    A degraded suggestion (`suggested_type=None` -- a malformed LLM reply,
    or a suggested type that failed `validate_relation_type`) renders as
    `[?]` plus a `note: no valid type suggested` line, never as if it were a
    valid suggestion (spec: Invalid suggested type is not surfaced as
    valid). Already-typed edges never appear at all -- `suggest_relations`
    filters them out before this command ever sees them (spec: Already-typed
    edges are excluded from suggestions).

    A no-model/no-Ollama run degrades via the SAME 3-tier ORDERED handler
    `adjudicate`/`query` use -- `OllamaUnavailable`, then
    `OllamaModelNotFound`, then the generic `OllamaError` fallback -- each
    with its own actionable stderr message, exit 1, and zero writes.

    Unless `--include-confidential` is passed, an untyped edge with a
    confidential endpoint (sensitivity-fail-closed-filter) is excluded from
    candidates -- dropped by `suggest_relations` before `llm.chat` is ever
    called for it.

    No file under the workspace is ever created, modified, or deleted
    (spec: Verb performs zero writes).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos suggest-relations: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos suggest-relations: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    llm = OllamaClient(model=cfg.model)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir, include_confidential=include_confidential
    )
    try:
        results = suggest_relations(
            layout.bundle_dir, llm=llm, include_confidential=include_confidential
        )
    except OllamaUnavailable as exc:
        typer.echo(
            f"openkos suggest-relations: failed -- {exc}. Start it with "
            f"`ollama serve`, then try again.{_DOCTOR_HINT}",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except OllamaModelNotFound as exc:
        typer.echo(
            f"openkos suggest-relations: failed -- model '{cfg.model}' is "
            f"not installed. Pull it with `ollama pull {cfg.model}`, then "
            "try again.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    # The two specific handlers above MUST precede this generic handler:
    # both `OllamaUnavailable` and `OllamaModelNotFound` subclass
    # `OllamaError`, so reordering would silently funnel them into this
    # fallback and lose their actionable remediation messages (mirrors
    # `adjudicate`'s ordering).
    except OllamaError as exc:
        typer.echo(f"openkos suggest-relations: failed -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"openkos suggest-relations: workspace at {root}")
    typer.echo()
    if not results:
        typer.echo("No untyped relations found.")
        return

    for result in results:
        edge = result.edge
        if result.suggested_type is None:
            typer.echo(f"[?] {edge.source_id} -> {edge.target_id}")
            typer.echo("  note: no valid type suggested")
        else:
            typer.echo(
                f"[{result.suggested_type}] {edge.source_id} -> {edge.target_id}"
            )
            typer.echo(f"  rationale: {result.rationale}")
        typer.echo()

    typer.echo("Next: openkos relate <source> <type> <target>")


@app.command("suggest-volatility")
def suggest_volatility_cmd(
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
) -> None:
    """LLM-suggest a volatility `tier` for every concept TYPE present in the
    bundle: read-only, like `suggest-relations`.

    A SIXTH read command, mirroring `suggest-relations`'s wiring exactly:
    the shared `config.require_workspace` gate (D1), then a Phase-A
    `read_config` guard (`except (OSError, ValueError)`, lint parity), then
    a real `OllamaClient(model=cfg.model)` is built and injected -- as the
    `LLMBackend` -- into `resolution.volatility_typing.suggest_volatility`,
    the config-free leaf that owns the internal bundle read (via
    `lint.collect_docs`).

    `suggest-volatility` never writes, merges, or decides -- it only prints
    a suggested `tier` + rationale per concept type present for human
    review, plus a closing hint pointing at hand-editing `type_tiers:` in
    `openkos.yaml` -- there is no dedicated write-path verb for this one
    (unlike `suggest-relations` -> `relate`). No `--auto`, no confirmation
    gate, no `--json` or other structured mode.

    A degraded suggestion (`suggested_tier=None` -- a malformed LLM reply,
    or a suggested tier that is not a member of `types.VOLATILITY_TIERS`)
    renders as `[?]` plus a `note: no valid tier suggested` line, never as
    if it were a valid suggestion (spec: Fail-Closed Per-Type Suggestion
    Parsing). One other type's degraded reply never stops the run -- every
    other type present is still reported.

    A no-model/no-Ollama run degrades via the SAME 3-tier ORDERED handler
    `suggest-relations`/`adjudicate`/`query` use -- `OllamaUnavailable`,
    then `OllamaModelNotFound`, then the generic `OllamaError` fallback --
    each with its own actionable stderr message, exit 1, and zero writes.

    Unless `--include-confidential` is passed, a confidential concept
    (sensitivity-fail-closed-filter) is excluded from sampling for its type
    -- dropped by `suggest_volatility` before its body is ever shown to the
    LLM. A type whose docs are all confidential yields no suggestion for
    that type at all.

    No file under the workspace is ever created, modified, or deleted
    (spec: Verb performs zero writes).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(
            f"openkos suggest-volatility: refusing to run -- {reason}.", err=True
        )
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos suggest-volatility: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    llm = OllamaClient(model=cfg.model)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir, include_confidential=include_confidential
    )
    try:
        results = suggest_volatility(
            layout.bundle_dir, llm=llm, include_confidential=include_confidential
        )
    except OllamaUnavailable as exc:
        typer.echo(
            f"openkos suggest-volatility: failed -- {exc}. Start it with "
            f"`ollama serve`, then try again.{_DOCTOR_HINT}",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except OllamaModelNotFound as exc:
        typer.echo(
            f"openkos suggest-volatility: failed -- model '{cfg.model}' is "
            f"not installed. Pull it with `ollama pull {cfg.model}`, then "
            "try again.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    # The two specific handlers above MUST precede this generic handler:
    # both `OllamaUnavailable` and `OllamaModelNotFound` subclass
    # `OllamaError`, so reordering would silently funnel them into this
    # fallback and lose their actionable remediation messages (mirrors
    # `suggest-relations`'s ordering).
    except OllamaError as exc:
        typer.echo(f"openkos suggest-volatility: failed -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"openkos suggest-volatility: workspace at {root}")
    typer.echo()
    if not results:
        typer.echo("No concept types found.")
        return

    for result in results:
        if result.suggested_tier is None:
            typer.echo(f"[?] {result.type_name}")
            typer.echo("  note: no valid tier suggested")
        else:
            typer.echo(f"[{result.suggested_tier}] {result.type_name}")
            typer.echo(f"  rationale: {result.rationale}")
        typer.echo()

    typer.echo("Next: edit type_tiers in openkos.yaml")


@app.command()
def contradictions(
    show_all: bool = typer.Option(
        False,
        "--all",
        help="Show every verdict (CONTRADICTS, CONSISTENT, UNCERTAIN) "
        "regardless of confidence.",
    ),
    include_deprecated: bool = typer.Option(
        False,
        "--include-deprecated",
        help="Include deprecated and superseded concepts (excluded by default).",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
) -> None:
    """LLM-detect contradictions between already-related concepts: read-only,
    like `adjudicate`/`suggest-relations`/`suggest-volatility`.

    A SEVENTH read command, mirroring `suggest-relations`'s wiring exactly:
    the shared `config.require_workspace` gate (D1), then a Phase-A
    `read_config` guard (`except (OSError, ValueError)`, lint parity), then
    a real `OllamaClient(model=cfg.model)` is built and injected -- as the
    `LLMBackend` -- into `resolution.contradiction.find_contradictions`,
    which OWNS the internal `openkos.graph` read (design D2/D6, "No CLI
    Surface"): this module imports ONLY from `openkos.resolution.
    contradiction`, never `openkos.graph` directly.

    `contradictions` never writes, merges, or reconciles -- it only prints a
    verdict, confidence, rationale, and cited conflicting claims per
    candidate pair for human review. No `--auto`, no confirmation gate, no
    `--json` or other structured mode.

    By DEFAULT only high-confidence `CONTRADICTS` verdicts are shown
    (`is_high_confidence_contradiction`); `CONSISTENT` and `UNCERTAIN`, and
    low-confidence `CONTRADICTS`, are hidden (spec: Default view hides
    CONSISTENT/UNCERTAIN). `--all` is a DISPLAY-only filter: it reveals
    every verdict regardless of type or confidence, but
    `find_contradictions` always judges every candidate pair either way
    (spec: `--all` Reveals Every Verdict).

    A candidate set truncated by the engine leaf's pair cap is reported as
    an explicit "N of M pairs shown (cap reached)" line -- never silent
    (spec: Pair Cap With Explicit Truncation Notice). A bundle with zero
    candidate pairs prints a clear "No candidate pairs found." line and
    exits 0 without ever calling `llm.chat` (spec: Empty Graph Yields Clear
    Message, No Crash).

    A no-model/no-Ollama run degrades via the SAME 3-tier ORDERED handler
    `suggest-relations`/`adjudicate`/`query` use -- `OllamaUnavailable`,
    then `OllamaModelNotFound`, then the generic `OllamaError` fallback --
    each with its own actionable stderr message, exit 1, and zero writes.

    Unless `--include-deprecated` is passed, deprecated/superseded concepts
    (status-aware-retrieval) never appear in a candidate pair -- dropped by
    `find_contradictions` before any pair is judged, so the LLM is never
    invoked on them.

    Unless `--include-confidential` is passed, confidential concepts
    (sensitivity-fail-closed-filter) likewise never appear in a candidate
    pair, dropped by `find_contradictions` the same way.

    No file under the workspace is ever created, modified, or deleted
    (spec: Read-Only `contradictions` CLI Verb).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos contradictions: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos contradictions: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    llm = OllamaClient(model=cfg.model)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir, include_confidential=include_confidential
    )
    try:
        verdicts, total_pairs = find_contradictions(
            layout.bundle_dir,
            llm=llm,
            include_deprecated=include_deprecated,
            include_confidential=include_confidential,
        )
    except OllamaUnavailable as exc:
        typer.echo(
            f"openkos contradictions: failed -- {exc}. Start it with "
            f"`ollama serve`, then try again.{_DOCTOR_HINT}",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except OllamaModelNotFound as exc:
        typer.echo(
            f"openkos contradictions: failed -- model '{cfg.model}' is not "
            f"installed. Pull it with `ollama pull {cfg.model}`, then try "
            "again.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    # The two specific handlers above MUST precede this generic handler:
    # both `OllamaUnavailable` and `OllamaModelNotFound` subclass
    # `OllamaError`, so reordering would silently funnel them into this
    # fallback and lose their actionable remediation messages (mirrors
    # `suggest-relations`'s ordering).
    except OllamaError as exc:
        typer.echo(f"openkos contradictions: failed -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"openkos contradictions: workspace at {root}")
    typer.echo()
    if not verdicts:
        typer.echo("No candidate pairs found.")
        return

    if total_pairs > len(verdicts):
        typer.echo(f"{len(verdicts)} of {total_pairs} pairs shown (cap reached)")
        typer.echo()

    displayed = (
        verdicts
        if show_all
        else [v for v in verdicts if is_high_confidence_contradiction(v)]
    )
    if not displayed:
        typer.echo("No high-confidence contradictions found.")
        return

    for result in displayed:
        source_id, target_id = result.pair_ids
        typer.echo(
            f"[{result.verdict.value.upper()}] {source_id} <-> {target_id} "
            f"(confidence: {result.confidence:.2f})"
        )
        for claim in result.conflicting_claims:
            typer.echo(f"  - {claim}")
        typer.echo(f"  rationale: {result.rationale}")
        typer.echo()


def _no_match_message(cause: NoMatchCause, fts_hit_count: int) -> str:
    """Map `AnswerResult.no_match_cause` to an actionable STDOUT message,
    distinguishing the three causes `query` must not conflate: nothing
    matched, matches existed but were unreadable, or no question was asked.

    Only the three real no-match causes are expected here; the caller guards
    against `"none"`. An unhandled cause raises rather than silently falling
    through to a misleading message, so a future `NoMatchCause` value fails
    loudly instead of rendering the wrong text."""
    if cause == "zero_hits":
        return (
            f"{NO_MATCH} Try different wording, or run `openkos status` "
            "to see what the bundle contains."
        )
    if cause == "all_unreadable":
        return (
            f"Found {fts_hit_count} matching concept{_plural(fts_hit_count)}, "
            "but none could be read from the compiled bundle — it may be "
            "corrupted. Run `openkos lint` to check bundle health."
        )
    if cause == "empty_query":
        return (
            "No question was provided. Pass a question to answer, e.g. "
            'openkos query "what is stoicism?".'
        )
    raise ValueError(f"unexpected no_match_cause: {cause!r}")


def _open_vector_store_or_degrade(
    path: Path,
) -> tuple[AbstractContextManager["VectorStoreDB | None"], bool]:
    """Existence-gated store open for `query`'s read-only dense seam.

    `query` never CREATES `vectors.db` -- `open_vector_store` (which lazily
    creates `.openkos/vectors.db` on a successful open) is only called when
    `path` already exists on disk. Returns a context manager yielding either
    an open `VectorStoreDB` or `None`, plus whether the CLI itself detected
    the store as unavailable this call (absent, `VecUnavailable` at open,
    or a raw `sqlite3.Error` -- e.g. a corrupt/locked EXISTING `vectors.db`
    raising `DatabaseError`/`OperationalError` from `open_vector_store`'s
    CREATE TABLE step, which is not mapped to `VecUnavailable`) -- distinct
    from `AnswerResult.dense_degraded`, which is set INSIDE `answer()` for a
    read-path failure at query time. The caller's reindex hint fires on
    either signal."""
    if not path.exists():
        return nullcontext(None), True
    try:
        return open_vector_store(path), False
    except (VecUnavailable, sqlite3.Error):
        return nullcontext(None), True


def _open_fts_or_degrade(
    path: Path,
) -> tuple[AbstractContextManager["fts.FtsIndex | None"], bool]:
    """Existence-gated, read-only handle open for `query`'s persisted FTS
    seam (Slice 5, PR3).

    Same INTENT and RETURN SHAPE as `_open_vector_store_or_degrade` --
    `(context_manager, bool)`, degrading to `(nullcontext(None), True)` on
    absence or failure -- but NOT structurally identical (review finding
    R2: the two are related, not "mirrored exactly"). Two deliberate
    differences: (1) `_open_vector_store_or_degrade` checks `path.exists()`
    explicitly, because `open_vector_store` does not existence-gate itself;
    this function has NO explicit existence check of its own, because
    `fts.open_fts_index_readonly` is ALREADY existence-gated internally and
    returns `None` for an absent path on its own. (2) `_open_vector_store_or_degrade`
    catches `(VecUnavailable, sqlite3.Error)`; this function catches ONLY
    `sqlite3.Error`, since FTS has no typed "unavailable" exception analogous
    to `VecUnavailable` (plain `CREATE`/`SELECT`, no extension-load step to
    fail). The caller's reindex hint fires on either signal (absent or
    caught error); `answer()` itself only ever sees "handle or `None`" (the
    exception-vs-degrade boundary lives entirely at this call site, never
    inside `answer()`)."""
    try:
        handle = fts.open_fts_index_readonly(path)
    except sqlite3.Error:
        return nullcontext(None), True
    if handle is None:
        return nullcontext(None), True
    return handle, False


def _open_graph_or_degrade(
    path: Path,
) -> tuple[AbstractContextManager["GraphStore | None"], bool]:
    """Existence-gated, read-only handle open for `query`'s persisted graph
    seam (Slice 5, PR3).

    Structurally IDENTICAL to `_open_fts_or_degrade` (same two deliberate
    differences from `_open_vector_store_or_degrade` documented there: no
    explicit `path.exists()` guard here either, since
    `sqlite_graph.open_graph_store_readonly` is already existence-gated
    internally; catches only `sqlite3.Error`, since the graph store has no
    typed "unavailable" exception either). Returns a context manager
    yielding either an open `SqliteGraphStore` (satisfying `GraphStore`
    structurally) or `None`, plus whether the CLI itself detected the store
    as unavailable this call (absent, or a raw `sqlite3.Error` from
    `open_graph_store_readonly`'s open-time validating read against a
    corrupt/invalid EXISTING `graph.db`)."""
    try:
        handle = sqlite_graph.open_graph_store_readonly(path)
    except sqlite3.Error:
        return nullcontext(None), True
    if handle is None:
        return nullcontext(None), True
    return handle, False


@dataclass(frozen=True)
class _FiledAnswerPlan:
    """One validated `query --save` filing staged for Phase B write --
    mirrors `_DerivedPlan`'s shape (design: "`_stage_filed_answer` helper
    (not inline)")."""

    link_dir: str
    section: str
    slug: str
    title: str
    description: str
    path: Path
    content: str
    sensitivity: str


def _stage_filed_answer(
    *,
    question: str,
    answer_text: str,
    citations: list[Citation],
    bundle_dir: Path,
    default_sensitivity: str,
    timestamp: str,
    title: str | None = None,
    description: str | None = None,
    doc_type: str = "Concept",
) -> _FiledAnswerPlan:
    """Stage a `query --save` filing of `answer_text` as a new derived OKF
    concept -- a pure, in-memory Phase A step mirroring
    `_stage_derived_objects`'s staging shape: every refusal below raises
    `ValueError`, caught once at the `query` call site; nothing is written
    here -- Phase B (in `query`) does the actual `mkdir` + `write_exclusive`.

    Refuses when `citations` is empty (design: "Refuse `--save` when zero
    citations") -- `build_concept` requires non-empty provenance, and a
    sourceless "derived" concept is not a real derived node. `title`/
    `description` default to `question` when not overridden; `doc_type`
    defaults to `"Concept"`. `doc_type` MUST be a member of the classifiable
    vocabulary, else `ValueError` (same gate `build_concept` enforces,
    checked here first so the bundle subdirectory can be resolved safely).
    `slug = _slugify(title)`; an empty slug, or a slug that collides with an
    existing file at the target path, both refuse (design: "Slug collision
    handling (mirror ingest)").

    Sensitivity is the high-water-mark (`okf.combine_sensitivity`) folded
    over each cited concept's RE-READ frontmatter, seeded at
    `default_sensitivity`; an unreadable OR unparseable cited concept folds
    the running floor to `"confidential"` -- the most-restrictive level,
    NOT skipped (fail-closed: "cannot verify sensitivity -> confidential",
    the same stance as `okf._rank` / `sensitivity.blocks_llm_send`).
    Skipping would under-classify: a cited concept surfaced under
    `--include-confidential` that becomes unreadable at save time could
    otherwise leave a filed answer -- which may have synthesized
    confidential content -- classified below `confidential`, a future-leak
    vector.
    """
    if not citations:
        raise ValueError(
            "nothing to file -- the answer cited no concepts; --save records "
            "provenance from citations"
        )
    if doc_type not in _CLASSIFIABLE_TYPES:
        raise ValueError(
            f"type must be one of {sorted(_CLASSIFIABLE_TYPES)}, got {doc_type!r}"
        )

    resolved_title = question if title is None else title
    resolved_description = question if description is None else description

    slug = _slugify(resolved_title)
    if not slug:
        raise ValueError(
            f"cannot derive a filename from title {resolved_title!r}; pass --title"
        )

    link_dir = _TYPE_TO_LINK_DIR[doc_type]
    section = _TYPE_TO_SECTION[doc_type]
    path = bundle_dir / link_dir / f"{slug}.md"
    if path.exists():
        raise ValueError(
            f"a concept already exists at bundle/{link_dir}/{slug}.md; use "
            "--title to file under a different name, or forget the existing one"
        )

    sensitivity = default_sensitivity
    for citation in citations:
        try:
            text = (bundle_dir / f"{citation.concept_id}.md").read_text(
                encoding="utf-8"
            )
            metadata, _ = okf.load_frontmatter(text)
        except Exception:  # broad: any read/parse failure
            # fails CLOSED to "confidential" (cannot verify -> most
            # restrictive), mirroring `_assemble_context`'s broad
            # `except Exception` in retrieval/answer.py.
            sensitivity = okf.combine_sensitivity(sensitivity, "confidential")
            continue
        sensitivity = okf.combine_sensitivity(sensitivity, metadata.get("sensitivity"))

    content = okf.build_concept(
        type=doc_type,
        title=resolved_title,
        description=resolved_description,
        body=answer_text,
        provenance=[citation.concept_id for citation in citations],
        sensitivity=sensitivity,
        timestamp=timestamp,
        related_note="concept cited to produce this answer",
    )

    return _FiledAnswerPlan(
        link_dir=link_dir,
        section=section,
        slug=slug,
        title=resolved_title,
        description=resolved_description,
        path=path,
        content=content,
        sensitivity=sensitivity,
    )


@app.command()
def query(
    question: str = typer.Argument(
        ..., help="Natural-language question to answer from the bundle."
    ),
    limit: int = typer.Option(
        5, "--limit", help="Max concepts to retrieve as context."
    ),
    include_deprecated: bool = typer.Option(
        False,
        "--include-deprecated",
        help="Include deprecated and superseded concepts (excluded by default).",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
    save: bool = typer.Option(
        False,
        "--save",
        help=(
            "File the cited answer back as a new derived concept (opt-in; "
            "off by default keeps query read-only)."
        ),
    ),
    title: str | None = typer.Option(
        None, "--title", help="Title for the filed concept (default: the question)."
    ),
    description: str | None = typer.Option(
        None,
        "--description",
        help="Description for the filed concept (default: the question).",
    ),
    save_type: str = typer.Option(
        "Concept", "--type", help="Type for the filed concept (default: Concept)."
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="With --save, skip the confirmation prompt and write immediately.",
    ),
) -> None:
    """Answer a natural-language question from the compiled bundle, with citations.

    Read-only, like `status` and `lint`: no writes, no confirmation prompt,
    no `--auto`. Must be run inside an initialized workspace; outside one it
    refuses (exit 1) with a short reason on stderr. Retrieval fuses THREE
    lists: lexical (FTS5) hits, dense (`vectors.db`) hits, and a second-stage
    seeded personalized-PageRank graph pool -- all three now read PERSISTED,
    read-only on-disk indexes under `.openkos/` (`fts.db`, `vectors.db`,
    `graph.db`) that `reindex` maintains, rather than rebuilding anything
    in-process per call (Slice 5, PR3). `query` never WRITES to any of the
    three derived stores -- an absent or unavailable/corrupt store degrades
    cleanly (FTS/graph fall back to the remaining lists; dense falls back to
    FTS-only), never creating or repairing one; only `reindex` writes.

    Every completed run (successful answer or no-match) prints a one-line
    `retrieval:` summary to STDERR reporting the raw FTS hit count, the raw
    dense hit count, the raw graph hit count, the fused count, whether the
    LLM was invoked, and how many sources were cited -- so a silent
    short-circuit (e.g. zero hits, so the LLM never ran) is always visible,
    even though STDOUT stays pipe-clean. When any derived index is absent or
    unavailable/corrupt (FTS, dense, or graph), an additional stderr line
    hints at running `openkos reindex` to enable full retrieval -- `query`
    itself never recomputes or compares the bundle's manifest hash to reach
    this decision; staleness detection is `reindex`'s exclusive job (D2).
    When graph retrieval degraded (absent/unopenable index, no seeds, or a
    PageRank failure), a separate stderr note says so -- graph retrieval
    never affects the FTS/dense outcome. When the FTS index build skipped
    any unreadable/unparseable files (at the LAST `reindex` run), an
    `index:` skip-notice block follows the summary on stderr, worded as a
    whole-bundle build diagnostic -- it never implies the skipped files were
    candidates for THIS query's match.

    On a successful answer, STDOUT carries exactly the answer text, then
    (only when at least one concept was cited) a blank line, `Citations:`,
    and one `  → {concept_id} ({title})` line per citation, in the order
    they were used -- unchanged from prior behavior. When nothing in the
    bundle matches, STDOUT instead carries a cause-specific message (zero
    hits, hits found but all unreadable, or an empty/whitespace question)
    and the command still exits 0 -- "no answer found" is a valid result,
    not an error.

    Use `--limit` to cap how many concepts are retrieved as context
    (default 5). Answering needs a local Ollama server running the model
    configured in `openkos.yaml`. A workspace/config problem, an unreachable
    Ollama, or an unusable search index is reported on stderr with no
    traceback and exits 1.

    Unless `--include-deprecated` is passed, deprecated/superseded concepts
    (status-aware-retrieval) are excluded from every retrieval channel
    (lexical, dense, graph) BEFORE fusion -- the `retrieval:` stderr summary
    and every count in it (FTS/dense/graph/fused/cited) already report the
    POST-filter values, since filtering happens inside `answer()` before
    those counts are captured.

    Unless `--include-confidential` is passed, confidential concepts
    (sensitivity-fail-closed-filter) are likewise excluded from every
    retrieval channel before fusion, exactly like a deprecated concept.
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos query: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos query: failed while reading the workspace -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    llm = OllamaClient(model=cfg.model)
    embedder = OllamaClient(model=cfg.embedding_model)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir, include_confidential=include_confidential
    )
    vector_store_cm, store_was_unavailable = _open_vector_store_or_degrade(
        layout.vectors_db_path
    )
    fts_index_cm, fts_was_unavailable = _open_fts_or_degrade(layout.fts_db_path)
    graph_index_cm, graph_was_unavailable = _open_graph_or_degrade(layout.graph_db_path)
    with (
        vector_store_cm as vector_store,
        fts_index_cm as fts_index,
        graph_index_cm as graph_index,
    ):
        try:
            result = answer(
                question,
                bundle_dir=layout.bundle_dir,
                llm=llm,
                embedder=embedder,
                vector_store=vector_store,
                fts_index=fts_index,
                graph_index=graph_index,
                limit=limit,
                include_deprecated=include_deprecated,
                include_confidential=include_confidential,
            )
        except OllamaUnavailable as exc:
            typer.echo(
                f"openkos query: failed -- {exc}. Start it with `ollama serve`, "
                f"then try again.{_DOCTOR_HINT}",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        except OllamaModelNotFound as exc:
            # Names the REAL failing model from the exception text -- `query`
            # now builds TWO Ollama-backed seams (chat `llm` + `embedder`), so
            # a hardcoded `cfg.model` would be wrong whenever the embedding
            # model is the one that actually 404'd.
            typer.echo(
                f"openkos query: failed -- {exc}. Pull it with "
                "`ollama pull <model>`, then try again.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        # The two specific handlers above MUST precede this generic tuple:
        # both `OllamaUnavailable` and `OllamaModelNotFound` subclass
        # `OllamaError`, so reordering would silently funnel them into this
        # fallback and lose their actionable remediation messages.
        except (FtsUnavailable, OllamaError) as exc:
            typer.echo(f"openkos query: failed -- {exc}.", err=True)
            raise typer.Exit(code=1) from exc

    cited_count = len(result.citations)
    llm_status = "invoked" if result.llm_invoked else "skipped"
    typer.echo(
        f"retrieval: {result.fts_hit_count} FTS + {result.dense_hit_count} "
        f"dense + {result.graph_hit_count} graph → {result.fused_count} "
        f"fused → LLM {llm_status} → {cited_count} cited",
        err=True,
    )
    if (
        store_was_unavailable
        or fts_was_unavailable
        or graph_was_unavailable
        or result.dense_degraded
    ):
        typer.echo(
            "hint: one or more derived indexes are unavailable this run -- "
            "run `openkos reindex` to enable full retrieval.",
            err=True,
        )
    if result.graph_degraded:
        typer.echo(
            "note: graph retrieval degraded for this run -- falling back to "
            "FTS+dense fusion only.",
            err=True,
        )
    if result.skip_notices:
        typer.echo(
            f"index: {len(result.skip_notices)} "
            f"doc{_plural(len(result.skip_notices))} skipped while building "
            "the search index (whole-bundle, not this query's hits):",
            err=True,
        )
        for notice in result.skip_notices:
            typer.echo(f"  {notice}", err=True)

    if result.no_match_cause != "none":
        typer.echo(_no_match_message(result.no_match_cause, result.fts_hit_count))
        return

    typer.echo(result.answer)
    if result.citations:
        typer.echo()
        typer.echo("Citations:")
        for citation in result.citations:
            typer.echo(f"  → {citation.concept_id} ({citation.title})")

    if not save:
        return

    try:
        plan = _stage_filed_answer(
            question=question,
            answer_text=result.answer,
            citations=result.citations,
            bundle_dir=layout.bundle_dir,
            default_sensitivity=cfg.default_sensitivity,
            timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            title=title,
            description=description,
            doc_type=save_type,
        )
    except ValueError as exc:
        typer.echo(f"openkos query: refusing to save -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    save_index_path = layout.bundle_dir / "index.md"
    save_log_path = layout.bundle_dir / "log.md"
    now = datetime.now(UTC)
    try:
        index_text = save_index_path.read_text(encoding="utf-8")
        log_text = save_log_path.read_text(encoding="utf-8")
        new_index_text = bundle_index.insert_index_entry(
            index_text,
            section=plan.section,
            link_dir=plan.link_dir,
            title=plan.title,
            slug=plan.slug,
            description=plan.description,
        )
        new_log_text = bundle_log.insert_log_entry(
            log_text,
            now.astimezone().date(),
            f"**Filed answer**: [{plan.title}](/{plan.link_dir}/{plan.slug}.md) "
            "from query.",
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos query: failed while preparing the save -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos query: proposed changes (--save):")
    typer.echo(f"  + bundle/{plan.link_dir}/{plan.slug}.md")
    typer.echo(f"  ~ {save_index_path.name} (new entry)")
    typer.echo(f"  ~ {save_log_path.name} (new dated entry)")

    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos query: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        fsio.write_exclusive(plan.path, plan.content)
        fsio.write_atomic(save_index_path, new_index_text)
        fsio.write_atomic(save_log_path, new_log_text)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos query: failed while saving the answer -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos query: filed answer as bundle/{plan.link_dir}/{plan.slug}.md "
        f"({save_index_path.name}, {save_log_path.name} updated). Run "
        "`openkos reindex` to make it searchable."
    )


@app.command()
def reindex(
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-embed every discovered doc, ignoring the content-hash cache.",
    ),
) -> None:
    """Backfill `.openkos/vectors.db`, `.openkos/fts.db`, and
    `.openkos/graph.db` from the compiled bundle -- the sole writer of every
    derived store's data (spec: reindex-command).

    Read-only over the bundle, write-only to the three `.openkos/*.db`
    derived stores: no bundle file is ever touched, no confirmation prompt,
    no `--auto`, mirroring `query`'s D1 gate shape (bare `require_workspace`,
    no Phase B). Must run inside an initialized workspace; outside one it
    refuses (exit 1) with a short reason on stderr (spec: Run outside a
    workspace refuses).

    Thin wiring only (spec: CLI Verb Is Thin Wiring): `require_workspace` →
    `read_config` → `open_vector_store(vectors_db_path)` →
    `state.reindex.reindex(bundle_dir, db, embedder, force=force,
    fts_db_path=..., model_tag=cfg.embedding_model)` →
    `sqlite_graph.reindex_graph(bundle_dir, graph_db_path, force=force)` →
    print a summary of embedded/cache-hit/pruned/skipped counts and exit 0.
    The vector/FTS orchestrator (`state/reindex.py`) owns the bundle walk,
    the `content_hash` cache gate, the prune pass, the FTS manifest gate,
    AND the embedding-model tag gate (MVP-2 follow-up #5: a stored tag
    absent or different from `cfg.embedding_model` forces one full
    re-embed, independent of `--force`; `ReindexReport.model_reembedded`
    surfaces this as a dedicated summary line naming the old and new model,
    plus a follow-up line when some docs could not be re-embedded this run
    -- review correction, CRITICAL + WARNING findings); the graph gate
    (`openkos.graph.sqlite_graph.reindex_graph`) is called SEPARATELY
    rather than from inside `state/reindex.py`, because `state/reindex.py`
    is canonical-layer code and must not import `openkos.graph` (derived
    layer) -- this command is the entry-layer seam that ties both together
    so a single invocation still writes all three stores. This command
    owns none of the gate/rebuild logic itself.

    Embeds through a local Ollama server running the model configured as
    `embedding_model` in `openkos.yaml` (default `bge-m3`, ADR-0006).
    An unreachable Ollama, a missing embedding model, or an unusable
    `sqlite-vec` extension is reported on stderr with no raw traceback and
    exits 1 -- the SAME ordered ladder `query` uses (`OllamaUnavailable` →
    `OllamaModelNotFound` → a generic `(VecUnavailable, OllamaError)`
    fallback), with `VecUnavailable` substituted for `FtsUnavailable` (spec:
    Error Ladder Mirrors query). A concurrent process holding a write lock
    on `vectors.db`/`fts.db`/`graph.db` past `busy_timeout` (e.g. a
    concurrent `reindex`) is ALSO caught -- at store open, `upsert_many`/the
    end-of-run `commit`, or a store's `BEGIN IMMEDIATE` -- and reported with
    the SAME uniform retry message across all three stores, discriminated
    from any other operational failure by `state.derived.is_lock_contention`
    (errorcode, never message text), never by a raw traceback
    (reindex-lock-handling). Never alters `query`'s own behavior or
    `retrieval/answer.py` (spec: No Retrieval Consumer Introduced).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos reindex: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos reindex: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    embedder = OllamaClient(model=cfg.embedding_model)
    try:
        with open_vector_store(layout.vectors_db_path) as db:
            # Captured BEFORE the call so the summary below can name the OLD
            # tag even though `reindex()` may have already overwritten it in
            # `vectors.db` by the time we get `report` back (review
            # correction, WARNING finding: model-tag force observability).
            previous_model_tag = db.read_model_tag()
            report = reindex_module.reindex(
                layout.bundle_dir,
                db,
                embedder,
                force=force,
                fts_db_path=layout.fts_db_path,
                model_tag=cfg.embedding_model,
            )
    except OllamaUnavailable as exc:
        typer.echo(
            f"openkos reindex: failed -- {exc}. Start it with `ollama serve`, "
            f"then try again.{_DOCTOR_HINT}",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except OllamaModelNotFound as exc:
        typer.echo(
            "openkos reindex: failed -- embedding model "
            f"'{cfg.embedding_model}' is not installed. Pull it with "
            f"`ollama pull {cfg.embedding_model}`, then try again.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    # A lock-contention OperationalError (a concurrent process holding
    # vectors.db/fts.db's write lock past busy_timeout) can be raised at
    # ANY write surface inside the `with open_vector_store(...)` block
    # above -- store open, `upsert_many`/the end-of-run `commit`, or FTS's
    # `BEGIN IMMEDIATE` (propagated unchanged by `state/fts.py`'s errorcode
    # discrimination) -- so this clause wraps the ENTIRE try, catching all
    # three. Placed BEFORE the generic `(VecUnavailable, FtsUnavailable,
    # OllamaError)` tuple below (reindex-lock-handling, decision 2): a
    # non-lock `OperationalError` is deliberately RE-RAISED, not swallowed
    # into a generic clean exit -- this stays strictly additive, matching
    # this catch's ONLY documented job (lock contention), and preserves
    # whatever pre-existing (uncaught) behavior a different operational
    # failure already had.
    except sqlite3.OperationalError as exc:
        if derived.is_lock_contention(exc):
            typer.echo(_LOCK_CONTENTION_MSG, err=True)
            raise typer.Exit(code=1) from exc
        raise
    # The two specific handlers above MUST precede this generic tuple, same
    # ordering rationale as `query`'s ladder: both `OllamaUnavailable` and
    # `OllamaModelNotFound` subclass `OllamaError`. `FtsUnavailable` joins
    # `VecUnavailable` here (Slice 5 review correction, Finding A): reindex
    # now reaches the FTS write path (`state.reindex._reindex_fts` ->
    # `fts.write_fts_index`), which raises `FtsUnavailable` exactly like
    # `query`'s FTS read path already does -- this mirrors `query`'s own
    # `(FtsUnavailable, OllamaError)` ladder instead of leaving it as a raw,
    # uncaught traceback.
    except (VecUnavailable, FtsUnavailable, OllamaError) as exc:
        typer.echo(f"openkos reindex: failed -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    # The vectors.db/fts.db summary is printed HERE, BEFORE the graph write
    # attempt below -- not after it, as an earlier revision did (review
    # finding R4). `report` already reflects durably-committed work at this
    # point (`state.reindex.reindex` returned successfully); if the graph
    # write below then fails, the user must still see what DID happen
    # (embedded/cache-hit/pruned/skipped counts, and the `prune_skipped`
    # follow-up notice) rather than losing that signal behind the graph
    # error -- printing it first guarantees it always reaches the user,
    # regardless of what happens next.
    typer.echo(
        f"openkos reindex: {report.embedded} embedded, {report.cache_hits} "
        f"cache-hit{_plural(report.cache_hits)}, {report.pruned} pruned, "
        f"{report.skipped} skipped, {report.embed_failed} embed-failed."
    )
    if report.prune_skipped:
        typer.echo(
            "openkos reindex: prune pass was skipped this run -- a "
            "directory-scan error made part of the bundle unreadable, so no "
            "concept was pruned even if some appeared absent (review "
            "carry-over, fold-in #3)."
        )
    # Model-tag force observability (review correction, WARNING finding):
    # a model-tag mismatch triggers an operationally heavy full re-embed
    # that is otherwise indistinguishable from an ordinary large content
    # change -- name the old and new tag explicitly. The wording must stay
    # ACCURATE to whether the re-embed actually covered every doc this run
    # (round-2 review correction, WARNING finding): claiming "re-embedded
    # all vectors" while ALSO reporting docs that could not be re-embedded
    # is self-contradictory, so the complete (`skipped == 0 AND
    # embed_failed == 0`) and incomplete (`skipped > 0 OR embed_failed >
    # 0`) cases get distinct, non-overlapping wording instead of one
    # unconditional line plus a caveat. The success branch's gate MUST
    # mirror `state.reindex`'s tag-persist gate exactly (`skipped == 0 AND
    # embed_failed == 0`) -- reindex-embedding-resilience widened the
    # tag-persist gate to also withhold on `embed_failed > 0`, so a
    # `skipped == 0`-only success check here would print a false success
    # while the tag was actually withheld (review correction, CRITICAL
    # finding).
    incomplete_count = report.skipped + report.embed_failed
    if report.model_reembedded and incomplete_count == 0:
        typer.echo(
            "openkos reindex: re-embedded all vectors -- embedding model "
            f"changed ({previous_model_tag or 'unset'} -> "
            f"{cfg.embedding_model})."
        )
    elif report.model_reembedded:
        typer.echo(
            f"openkos reindex: embedding model changed ({previous_model_tag or 'unset'} "
            f"-> {cfg.embedding_model}); re-embedding all vectors -- INCOMPLETE: "
            f"{incomplete_count} doc{_plural(incomplete_count)} could not be "
            "re-embedded, will retry next run."
        )
    # Actionable re-run notice (reindex-embedding-resilience): keys ONLY on
    # `embed_failed` -- transient embed-EOF skips (retry budget exhausted at
    # the OllamaClient layer) are self-healing, unlike the permanent
    # `skipped` diagnostics above (unreadable/parse/decode failures a re-run
    # will NOT fix). Deliberately NEVER keys on `skipped` alone, so the two
    # skip kinds stay distinct on stderr, matching `ReindexReport.skipped`
    # vs `embed_failed`'s separation. This only reaches an exit-0 run: the
    # fatal ladder above (`OllamaUnavailable`/`OllamaModelNotFound`) exits 1
    # before the summary is ever printed.
    if report.embed_failed > 0:
        typer.echo(
            "openkos reindex: INCOMPLETE -- "
            f"{report.embed_failed} doc{_plural(report.embed_failed)} could "
            "not be embedded (transient failure). Run `openkos reindex` "
            "again to complete it.",
            err=True,
        )

    # graph.db is written by a SEPARATE call, not by `state.reindex.reindex`
    # itself: `state/reindex.py` is canonical-layer code and must not import
    # `openkos.graph` (derived layer, docs/architecture.md); this entry-layer
    # command is the seam that ties both together so a single `openkos
    # reindex` invocation still writes all three derived stores (Slice 5,
    # PR2; reindex-command: Reindex writes all three derived stores in one
    # run). This call has its OWN try/except, deliberately separate from the
    # vectors/FTS ladder above: `sqlite_graph.reindex_graph` raises no typed
    # "unavailable" exception (plain `CREATE TABLE`, no extension dependency
    # like `fts5`/`sqlite-vec`) -- its only failure mode is a bare
    # `sqlite3.Error` (permission/IO/corrupt `graph.db`), which the vectors/FTS
    # ladder above was never scoped to catch (PR3 carry-over fix, Engram bug
    # #1470: the graph reindex ladder gap -- a graph-write failure after
    # vectors.db/fts.db already succeeded used to crash with a raw traceback
    # instead of the documented clean exit 1). Deliberately narrow: catches
    # ONLY this call's `sqlite3.Error`. A locked `graph.db` (lock contention,
    # discriminated by `is_lock_contention`) gets the SAME uniform
    # `_LOCK_CONTENTION_MSG` ladder 1 uses for vectors.db/fts.db, reusing
    # this broad `except sqlite3.Error` rather than a separate narrower
    # clause -- a non-lock `sqlite3.Error` keeps its existing, graph-specific
    # message unchanged (reindex-lock-handling; this closes the gap this
    # comment used to flag as deferred).
    try:
        sqlite_graph.reindex_graph(layout.bundle_dir, layout.graph_db_path, force=force)
    except sqlite3.Error as exc:
        if isinstance(exc, sqlite3.OperationalError) and derived.is_lock_contention(
            exc
        ):
            typer.echo(_LOCK_CONTENTION_MSG, err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(
            f"openkos reindex: failed while writing the graph index -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc


@dataclass(frozen=True)
class CheckResult:
    """One `doctor` check's outcome (D5): accumulated, never raised, so a
    failure never short-circuits the checks that follow it."""

    label: str
    status: Literal["pass", "fail", "skip"]
    critical: bool
    remediation: str | None = None
    detail: str | None = None


def _render_check(r: CheckResult) -> None:
    """Print one `CheckResult` as `[PASS]`/`[FAIL]`/`[SKIP] <label>`, with an
    optional ` — <detail>` suffix and, only under a `[FAIL]`, an indented
    `  -> <remediation>` line naming the user's own next command."""
    tag = {"pass": "[PASS]", "fail": "[FAIL]", "skip": "[SKIP]"}[r.status]
    line = f"{tag} {r.label}"
    if r.detail:
        line += f" — {r.detail}"
    typer.echo(line)
    if r.status == "fail" and r.remediation:
        typer.echo(f"  -> {r.remediation}")


@app.command()
def doctor() -> None:
    """Read-only environment health scan: fixed checks against the local
    workspace and local Ollama, printed as `[PASS]`/`[FAIL]`/`[SKIP]` lines
    with actionable remediation, usable even before `openkos init`.

    Deliberately NEW control-flow shape versus `status`/`lint`/`query`:
    instead of exiting on the first failure, this runs ALL nine checks,
    appends each to a `list[CheckResult]`, renders every line
    unconditionally, then exits ONCE (`code=1`) if any CRITICAL check
    failed (spec: Doctor Runs And Prints All Applicable Checks). Remediation
    TEXT lives only here; `llm/` stays config-free (D1).

    Checks, in order: (1) workspace-initialized -- informational, via the
    shared `config.require_workspace` gate; (2) config-valid -- critical,
    workspace-only, `[SKIP]` outside a workspace; (3) Ollama-reachable --
    critical, always, via `OllamaClient.list_models()`; (4) model-installed
    -- critical, always, via `model_tag_matches`; `[SKIP]` (never `[FAIL]`)
    when Ollama is unreachable, since the two share one root cause (D6);
    (5) embedding-model-installed -- informational, always, via the SAME
    already-fetched `installed` list and `model_tag_matches`; `[SKIP]`
    (never `[FAIL]`) when Ollama is unreachable, for the same D6 reason --
    Slice 1 does not wire embeddings into any consumed feature yet, so a
    failure here must not flip the exit code; (6) bundle-readable --
    informational, workspace-only, `[SKIP]` outside a workspace; (7)
    vector-extension-loadable -- informational, always, via
    `state.vectorstore.probe_vec_loadable()` against a throwaway `:memory:`
    connection; UNLIKE (5), this check has NO `[SKIP]` branch -- it depends
    on neither workspace state nor Ollama reachability, so it shares no root
    cause with any other check (embedding-vector-store, Slice 2a; the
    scaffolding this checks has no consumed feature yet either); (8)
    git-available -- informational, always, via `vcs.git.git_available()`;
    (9) git-filter-repo-available -- informational, always, via
    `vcs.git.filter_repo_available()`. Checks (8)/(9) exist for the
    not-yet-wired `purge` verb (privacy-purge Slice 1, PR2): like (7), they
    have no `[SKIP]` branch -- they depend on neither workspace state nor
    Ollama. Outside a workspace, checks (3)/(4)/(5)/(7)/(8)/(9) still run
    against `config.DEFAULT_MODEL`/`config.DEFAULT_EMBEDDING_MODEL` and
    (3)/(4) still determine the exit code (spec: Doctor Works Outside An
    Initialized Workspace).

    Never creates, modifies, or deletes any file, and never runs a
    remediation command itself (spec: Doctor Is Read-Only).
    """
    root = Path.cwd()
    results: list[CheckResult] = []

    # 1. workspace-initialized (informational)
    workspace_reason = config.require_workspace(root)
    in_workspace = workspace_reason is None
    results.append(
        CheckResult(
            "Workspace initialized",
            "pass" if in_workspace else "fail",
            critical=False,
            remediation=None if in_workspace else "openkos init",
            detail=None if in_workspace else workspace_reason,
        )
    )

    # 2. config-valid (critical, workspace-only; SKIP outside)
    cfg: config.Config | None = None
    if in_workspace:
        try:
            cfg = config.read_config(root)
            results.append(
                CheckResult(
                    "Config valid", "pass", critical=True, detail=f"model {cfg.model}"
                )
            )
        except (OSError, ValueError) as exc:
            results.append(
                CheckResult(
                    "Config valid",
                    "fail",
                    critical=True,
                    remediation="fix openkos.yaml",
                    detail=str(exc),
                )
            )
    else:
        results.append(CheckResult("Config valid", "skip", critical=True))

    model = cfg.model if cfg is not None else config.DEFAULT_MODEL
    embedding_model = (
        cfg.embedding_model if cfg is not None else config.DEFAULT_EMBEDDING_MODEL
    )

    # 3. Ollama-reachable (critical, always)
    reachable = False
    installed: list[str] = []
    client = OllamaClient(model=model, timeout=_PREFLIGHT_TIMEOUT)
    try:
        installed = client.list_models()
        reachable = True
        results.append(
            CheckResult(
                "Ollama reachable",
                "pass",
                critical=True,
                detail=f"{len(installed)} models",
            )
        )
    except OllamaUnavailable as exc:
        if shutil.which("ollama") is None:
            remediation = (
                "no `ollama` binary found on PATH -- install from "
                "https://ollama.com, or if Ollama is already installed "
                "(e.g. the macOS app) start it with `ollama serve`"
            )
        else:
            remediation = "ollama serve"
        results.append(
            CheckResult(
                "Ollama reachable",
                "fail",
                critical=True,
                remediation=remediation,
                detail=str(exc),
            )
        )
    except OllamaError as exc:  # non-transport server error
        results.append(
            CheckResult("Ollama reachable", "fail", critical=True, detail=str(exc))
        )

    # 4. model-installed (critical, always; SKIP-blocked if unreachable, D6)
    label = f"Model '{model}' installed"
    if not reachable:
        results.append(
            CheckResult(
                label, "skip", critical=True, detail="blocked: Ollama unreachable"
            )
        )
    elif model_tag_matches(model, installed):
        results.append(CheckResult(label, "pass", critical=True))
    else:
        results.append(
            CheckResult(
                label, "fail", critical=True, remediation=f"ollama pull {model}"
            )
        )

    # 5. embedding-model-installed (informational, always; SKIP-blocked if
    # unreachable, same D6 rationale as model-installed -- one root cause,
    # never double-reported). Reuses the already-fetched `installed` list,
    # constructs no additional `OllamaClient`.
    embedding_label = f"Embedding model '{embedding_model}' installed"
    if not reachable:
        results.append(
            CheckResult(
                embedding_label,
                "skip",
                critical=False,
                detail="blocked: Ollama unreachable",
            )
        )
    elif model_tag_matches(embedding_model, installed):
        results.append(CheckResult(embedding_label, "pass", critical=False))
    else:
        results.append(
            CheckResult(
                embedding_label,
                "fail",
                critical=False,
                remediation=f"ollama pull {embedding_model}",
            )
        )

    # 6. bundle-readable (informational, workspace-only; SKIP outside)
    if in_workspace:
        survey = okf.survey_bundle(config.WorkspaceLayout(root).bundle_dir)
        if not survey.findings:
            results.append(
                CheckResult(
                    "Bundle readable",
                    "pass",
                    critical=False,
                    detail=f"{survey.sources} sources, {survey.concepts} concepts",
                )
            )
        else:
            results.append(
                CheckResult(
                    "Bundle readable",
                    "fail",
                    critical=False,
                    detail=f"{len(survey.findings)} issue(s)",
                )
            )
    else:
        results.append(CheckResult("Bundle readable", "skip", critical=False))

    # 7. vector-extension-loadable (informational, always; NO SKIP branch --
    # unlike embedding-model-installed, this shares no root cause with any
    # other check: it depends only on the local Python/SQLite build, never
    # on workspace state or Ollama reachability). Probes a throwaway
    # `:memory:` connection -- creates no files (D: Doctor Is Read-Only).
    if probe_vec_loadable():
        results.append(CheckResult("Vector extension loadable", "pass", critical=False))
    else:
        results.append(
            CheckResult(
                "Vector extension loadable",
                "fail",
                critical=False,
                remediation=(
                    "run openkos with an extension-capable Python interpreter "
                    "(e.g. a uv-managed interpreter) that supports SQLite "
                    "extension loading"
                ),
            )
        )

    # 8. git-available (informational, always; NO SKIP branch -- shares no
    # root cause with any other check; exists for the not-yet-wired `purge`
    # verb, privacy-purge Slice 1 PR2)
    if vcs_git.git_available():
        results.append(CheckResult("git available", "pass", critical=False))
    else:
        results.append(
            CheckResult(
                "git available",
                "fail",
                critical=False,
                remediation=(
                    "install git (e.g. https://git-scm.com/downloads, or "
                    "`brew install git`)"
                ),
            )
        )

    # 9. git-filter-repo-available (informational, always; NO SKIP branch)
    if vcs_git.filter_repo_available():
        results.append(CheckResult("git-filter-repo available", "pass", critical=False))
    else:
        results.append(
            CheckResult(
                "git-filter-repo available",
                "fail",
                critical=False,
                remediation=(
                    "install git-filter-repo (e.g. `pip install git-filter-repo`, "
                    "or `brew install git-filter-repo`)"
                ),
            )
        )

    typer.echo(f"openkos doctor: checking environment at {root}")
    typer.echo()
    for r in results:
        _render_check(r)

    if any(r.status == "fail" and r.critical for r in results):
        raise typer.Exit(code=1)
