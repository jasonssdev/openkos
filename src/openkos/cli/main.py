"""Typer application object exposed as the `openkos` console script."""

import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

import typer

from openkos import config, fsio
from openkos import lint as lint_check
from openkos.bundle import bundle
from openkos.bundle import index as bundle_index
from openkos.bundle import log as bundle_log
from openkos.extraction.concept import extract_concept
from openkos.llm.base import LLMBackend
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
    model_tag_matches,
)
from openkos.model import okf
from openkos.model.types import CLASSIFIABLE_LINK_DIRS
from openkos.model.types import TYPE_TO_LINK_DIR as _TYPE_TO_LINK_DIR
from openkos.model.types import TYPE_TO_SECTION as _TYPE_TO_SECTION
from openkos.retrieval.answer import NO_MATCH, NoMatchCause, answer
from openkos.state.fts import FtsUnavailable

app = typer.Typer()


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
    """A validated derived object staged for Phase B, or `None`
    (`_stage_derived_object`'s return) when extraction is skipped, declined,
    fails validation, or the LLM is unavailable -- every one of those cases
    degrades to Source-only, never a crash (design: Degrade seam)."""

    doc_type: str
    section: str
    link_dir: str
    slug: str
    title: str
    description: str
    path: Path
    content: str


def _source_has_derived_object(bundle_dir: Path, source_slug: str) -> bool:
    """True if any existing derived object of any of the four types already
    cites this source in its provenance -- so a re-ingest never extracts (or
    writes) a second derived object for the same source, independent of
    nondeterministic LLM titles.

    Idempotency in `_stage_derived_object` cannot be keyed off the derived
    document's own slug (`_slugify(extraction.title)`), because that title
    comes from the LLM and is not guaranteed deterministic across calls: a
    re-ingest that gets a slightly different title would slugify to a
    different path, `derived_path.exists()` would be `False`, and a SECOND
    derived document (plus its own catalog bullet and log entry) would be
    created for the same source. Provenance (`sources/<slug>`), by contrast,
    is engine-derived and deterministic, so it is the correct idempotency
    key.
    """
    source_ref = f"sources/{source_slug}"
    for link_dir in CLASSIFIABLE_LINK_DIRS:
        section_dir = bundle_dir / link_dir
        if not section_dir.is_dir():
            continue
        for path in sorted(section_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                metadata, _ = okf.load_frontmatter(text)
            except Exception:  # noqa: S112 -- broad: a concurrent edit can corrupt frontmatter
                continue
            provenance = metadata.get("provenance")
            if isinstance(provenance, list) and source_ref in provenance:
                return True
    return False


def _stage_derived_object(
    *,
    raw_content: str | None,
    source_title: str,
    source_slug: str,
    sensitivity: str,
    timestamp: str,
    bundle_dir: Path,
    llm: LLMBackend,
) -> _DerivedPlan | None:
    """Attempt LLM extraction of at most one derived object from the
    source's decoded text, and stage it for Phase B (`ingest` owns
    slug/path derivation and degrade-note wording; the extraction leaf stays
    config-free, per design's Technical Approach).

    Returns `None` -- always a Source-only degrade, never a raised error --
    when: `raw_content` is `None` or blank (a binary/undecodable or empty
    source has no text to extract from, so the LLM is never called); a
    derived object already cites this source's provenance (idempotent
    re-ingest, see `_source_has_derived_object` -- checked BEFORE calling the
    LLM, so a re-ingest never spends a network call it cannot use);
    `llm.chat` raises any `OllamaError`-family exception (caught HERE, per
    design's "Degrade seam" -- `extraction/concept.py` lets it propagate
    unswallowed); `extract_concept` itself returns `None` (declined, or
    failed its own fail-closed validation); the extracted title slugifies to
    an empty string (an LLM title made only of non-alphanumeric characters);
    or `okf.build_concept` raises `ValueError` (untrusted LLM fields that
    slipped past `extract_concept`'s own validation -- e.g. an embedded
    newline -- fail `build_concept`'s stricter single-line gate). Every one
    of these is reported to stderr with wording that distinguishes the
    `None`/declined case from the `OllamaError` case (design: Degrade
    wording), and `ingest` keeps exiting 0.

    A `None` return also covers the create-only idempotency case: when the
    derived object's target path already exists (a prior successful
    extraction for this same source), it is left untouched -- no re-write,
    no duplicate catalog/log entry -- exactly like a hand-edited file. This
    same-slug guard is a SECONDARY safety net only, guarding against two
    DIFFERENT sources colliding on the same slug; the PRIMARY idempotency
    guard is `_source_has_derived_object`, above, which is provenance-keyed
    and therefore immune to a nondeterministic LLM title changing the slug
    on a re-ingest of the SAME source.
    """
    if raw_content is None or not raw_content.strip():
        typer.echo(
            "openkos ingest: source has no extractable text; keeping the Source only.",
            err=True,
        )
        return None

    if _source_has_derived_object(bundle_dir, source_slug):
        return None

    try:
        extraction = extract_concept(raw_content, source_title=source_title, llm=llm)
    except OllamaError as exc:
        typer.echo(
            f"openkos ingest: concept extraction skipped -- {exc}; "
            "keeping the Source only.",
            err=True,
        )
        return None

    if extraction is None:
        typer.echo(
            "openkos ingest: no concept extracted from this source; "
            "keeping the Source only.",
            err=True,
        )
        return None

    derived_slug = _slugify(extraction.title)
    if not derived_slug:
        typer.echo(
            "openkos ingest: extracted title could not be turned into a "
            "slug; keeping the Source only.",
            err=True,
        )
        return None

    link_dir = _TYPE_TO_LINK_DIR[extraction.type]
    section = _TYPE_TO_SECTION[extraction.type]
    derived_path = bundle_dir / link_dir / f"{derived_slug}.md"
    if derived_path.exists():
        # Create-only idempotency (design: Idempotency/collision): leave the
        # existing derived object -- and its original catalog/log entries --
        # untouched rather than overwriting a possibly hand-edited file.
        return None

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
            "keeping the Source only.",
            err=True,
        )
        return None

    return _DerivedPlan(
        doc_type=extraction.type,
        section=section,
        link_dir=link_dir,
        slug=derived_slug,
        title=extraction.title,
        description=extraction.description,
        path=derived_path,
        content=content,
    )


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
) -> None:
    """Copy `src` into `raw/`, generate one OKF Source concept, and attempt
    LLM extraction of at most one derived object (MVP-2 vertical slice; D5).

    Beyond the MVP-1 "null compiler" (exactly one `Source` concept per
    invocation, with an honest description stating the source was imported),
    this now also attempts ONE LLM-driven extraction step: an injected
    `OllamaClient` classifies the source's decoded text as `{Concept,
    Entity, Person, Organization}` or declines. Any failure of that step --
    declined extraction, fail-closed validation, or the LLM being
    unavailable -- degrades to the exact same Source-only result MVP-1
    always produced, with a short note on stderr and exit 0; a successful
    extraction ADDS a second, create-only derived document
    (`bundle/concepts/<slug>.md`, `bundle/entities/<slug>.md`,
    `bundle/people/<slug>.md`, or `bundle/organizations/<slug>.md`)
    alongside the Source, never replacing it. See `_stage_derived_object`
    for the full degrade matrix.

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
    object (if any) is staged, the new `index.md`/`log.md` bytes are
    computed to cover BOTH objects, and a preview of the proposed changes --
    listing both the Source and the derived object, when one was staged --
    is printed.

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
    may already exist -- then, if a derived object was staged, its own
    directory (`bundle/concepts/`, `bundle/entities/`, `bundle/people/`, or
    `bundle/organizations/`, created if absent) and its document
    (`write_exclusive`, create-only -- always,
    regardless of whether the Source itself was fresh or regenerated) --
    then `index.md` and `log.md` (`write_atomic`, catalog LAST -- so the
    catalog never points at a file that does not yet exist, mirroring
    `init`'s marker-last ordering, D3), extended to cover the derived
    object's own bullet/log entry when one was staged. Every one of these
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
        derived = _stage_derived_object(
            raw_content=raw_content,
            source_title=title,
            source_slug=slug,
            sensitivity=cfg.default_sensitivity,
            timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            bundle_dir=layout.bundle_dir,
            llm=OllamaClient(model=cfg.model),
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
        if derived is not None:
            # Extends the SAME index/log diff (design: one confirm gate, one
            # preview) rather than a second read-modify-write round trip.
            new_index_text = bundle_index.insert_index_entry(
                new_index_text,
                section=derived.section,
                link_dir=derived.link_dir,
                title=derived.title,
                slug=derived.slug,
                description=derived.description,
            )
            new_log_text = bundle_log.insert_log_entry(
                new_log_text,
                now.astimezone().date(),
                f"**Ingest**: Extracted [{derived.title}]"
                f"(/{derived.link_dir}/{derived.slug}.md) ({derived.doc_type}) "
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
        if derived is not None:
            typer.echo(f"  + bundle/{derived.link_dir}/{derived.slug}.md")
        typer.echo(f"  ~ {index_path.name} (Source entry refreshed)")
        typer.echo(f"  ~ {log_path.name} (new dated entry)")
    else:
        typer.echo("openkos ingest: proposed changes:")
        typer.echo(f"  + raw/{name}")
        typer.echo(f"  + bundle/sources/{slug}.md")
        if derived is not None:
            typer.echo(f"  + bundle/{derived.link_dir}/{derived.slug}.md")
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
        if derived is not None:
            # Create-only, like the Source's own fresh write -- ALWAYS
            # (even on a Source `regenerate`), since a staged `derived` here
            # already proved its target path does not exist yet.
            derived.path.parent.mkdir(parents=True, exist_ok=True)
            fsio.write_exclusive(derived.path, derived.content)
        fsio.write_atomic(index_path, new_index_text)
        fsio.write_atomic(log_path, new_log_text)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos ingest: failed while writing the ingest -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    if derived is not None:
        typer.echo(
            f"openkos ingest: imported '{src}' -> raw/{name}, "
            f"bundle/sources/{slug}.md, bundle/{derived.link_dir}/{derived.slug}.md "
            f"({index_path.name}, {log_path.name} updated)."
        )
    else:
        typer.echo(
            f"openkos ingest: imported '{src}' -> raw/{name}, "
            f"bundle/sources/{slug}.md ({index_path.name}, {log_path.name} updated)."
        )


def _resolve_concept_path(bundle_dir: Path, concept_id: str) -> tuple[Path, str]:
    """Resolve `concept_id` to `(concept_file, canonical_id)` under
    `bundle_dir`, or raise `ValueError` (`forget`'s Phase A path-safety gate,
    mirroring `ingest`'s basename-derived containment).

    The `concept_id` is canonicalized ONCE -- a redundant `.md` suffix is
    stripped and `PurePosixPath` collapses `.` and repeated-slash segments --
    and that single `canonical_id` is used for BOTH the filesystem path and
    the caller's `index.md` match, so a leading `./` (or a `.md` suffix) can
    never delete a concept file while leaving its catalog bullet dangling.

    Rejects an absolute `concept_id` (a leading `/`), any `..` path segment,
    and a reserved basename (`index`/`log`, `okf.RESERVED_FILENAMES`, matched
    CASE-INSENSITIVELY so a case-insensitive filesystem -- macOS/Windows
    default -- cannot be tricked into deleting the real `index.md`/`log.md`).
    Every one of these is a security-relevant path-traversal check and MUST
    run before any filesystem read tied to `concept_id` (threat matrix:
    path-traversal deletion). Finally refuses (also `ValueError`) if the
    resolved `<canonical_id>.md` file does not exist -- a nonexistent
    concept-id is a clear error, never a silent no-op (spec: Nonexistent
    Concept Refusal).
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
    concept_path = bundle_dir / f"{canonical_id}.md"
    if not concept_path.is_file():
        raise ValueError(f"concept '{concept_id}' does not exist")
    return concept_path, canonical_id


@app.command()
def forget(
    concept_id: str = typer.Argument(
        ..., help="Bundle-relative concept id (path minus '.md') to remove."
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Delete a concept file and remove its `index.md` catalog entry: the
    mirror-image of `ingest` (MVP-1 simplified delete, decision #717).

    Phase A (pure, no writes) validates and builds the entire result in
    memory, in order: the current directory must already be a workspace
    (the same `config.require_workspace` gate `ingest`/`status`/`lint`
    share), or this refuses; `concept_id` is resolved via
    `_resolve_concept_path`, which rejects an absolute id, any `..`
    segment, a reserved basename (`index`/`log`), or a nonexistent concept
    file -- all as `ValueError`, all refusing before any write. `index.md`
    is then searched, via `bundle_index.remove_index_entry`, for a bullet
    in ANY section (Sources, Concepts, People, Decisions) whose first
    markdown link resolves to `concept_id` -- matching is generic across
    sections, never Sources-only (#922). A `log.md` entry is built in
    memory via `bundle_log.insert_log_entry` (a plain `**Forget**` line,
    never a tombstone marker). A preview of the proposed changes is then
    printed: `~ index.md (remove entry)` only appears when at least one
    bullet matched (zero matches is drift, not an error -- the deletion
    still proceeds); `~ log.md (new dated entry)` and `- bundle/<concept_id>.md`
    always appear.

    Confirm gate, identical precedence and mechanism to `ingest`: `--auto`
    skips the prompt outright; otherwise config `review: false` skips it
    the same way; otherwise, on a TTY, `typer.confirm` asks and aborts
    (exit 1) on decline; otherwise (non-TTY, no `--auto`) this refuses to
    write (exit 1), telling the user to re-run with `--auto`.

    Phase B (after confirm) writes `index.md` then `log.md`
    (`write_atomic`, catalog FIRST) and deletes the concept file
    (`fsio.remove_file`) LAST -- the inverse of `ingest`'s content-then-
    catalog ordering, preserving the same invariant either way: `index.md`
    never references a file that does not exist. This is NOT transactional
    as a whole: a failure partway through (e.g. the unlink itself) leaves a
    benign, git-recoverable partial result -- the catalog already updated,
    the concept file possibly still present as an orphan -- never silent
    corruption. Any failure, Phase A or Phase B, is caught and reported on
    stderr (exit 1), not a raw traceback; `except (OSError, ValueError)`,
    matching `ingest`'s convention.

    Known limitation (deferred to MVP-2, per the proposal's non-goals):
    other concepts that still link to the forgotten one are left with a
    dangling inbound reference -- this is neither detected nor rewritten
    here.
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
        new_index_text, removed = bundle_index.remove_index_entry(
            index_text, canonical_id
        )
        new_log_text = bundle_log.insert_log_entry(
            log_text,
            now.astimezone().date(),
            f"**Forget**: Removed [{canonical_id}](/{canonical_id}.md).",
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos forget: failed while preparing the forget -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos forget: proposed changes:")
    if removed >= 1:
        typer.echo(f"  ~ {index_path.name} (remove entry)")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")
    typer.echo(f"  - bundle/{canonical_id}.md")

    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos forget: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        fsio.write_atomic(index_path, new_index_text)
        fsio.write_atomic(log_path, new_log_text)
        fsio.remove_file(concept_path)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos forget: failed while writing the forget -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos forget: removed 'bundle/{canonical_id}.md' "
        f"({index_path.name}, {log_path.name} updated)."
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

    On a workspace, the flow is: `read_config(root).freshness_window` is
    resolved via `lint.resolve_window` (Q4) -- an invalid/zero/negative
    value never raises, it falls back to the packaged default and prints a
    fallback-notice line instead. `today` is computed ONCE via
    `datetime.now(UTC).date()` and injected into `lint.check_stale_stamps`
    (the clock is never read inside `lint.py` itself, keeping every scan
    deterministic and testable). `lint.collect_docs` reuses `okf._iter_docs`
    for the single walk, returning `(docs, skip_notices)` so a skipped
    file never silently shrinks the scan; `lint.check_stale_stamps` scans
    inline `(as of YYYY-MM-DD)` body stamps (never the `freshness` field);
    `lint.check_orphans` scans markdown links from `index.md` and every
    doc body (never `log.md` -- see its docstring for why).

    The window and skip notices feed one `lint.LintReport`, rendered
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

    window, window_notice = lint_check.resolve_window(cfg.freshness_window)
    today = datetime.now(UTC).date()
    stale = lint_check.check_stale_stamps(docs, today=today, window=window)
    orphans = lint_check.check_orphans(docs, index_text=index_text)
    notices = ([window_notice] if window_notice is not None else []) + skip_notices
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


@app.command()
def query(
    question: str = typer.Argument(
        ..., help="Natural-language question to answer from the bundle."
    ),
    limit: int = typer.Option(
        5, "--limit", help="Max concepts to retrieve as context."
    ),
) -> None:
    """Answer a natural-language question from the compiled bundle, with citations.

    Read-only, like `status` and `lint`: no writes, no confirmation prompt,
    no `--auto`. Must be run inside an initialized workspace; outside one it
    refuses (exit 1) with a short reason on stderr.

    Every completed run (successful answer or no-match) prints a one-line
    `retrieval:` summary to STDERR reporting the raw FTS hit count, whether
    the LLM was invoked, and how many sources were cited -- so a silent
    short-circuit (e.g. zero hits, so the LLM never ran) is always visible,
    even though STDOUT stays pipe-clean. When the build skipped any
    unreadable/unparseable files, an `index:` skip-notice block follows the
    summary on stderr, worded as a whole-bundle build diagnostic -- it never
    implies the skipped files were candidates for THIS query's match.

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
    try:
        result = answer(question, bundle_dir=layout.bundle_dir, llm=llm, limit=limit)
    except OllamaUnavailable as exc:
        typer.echo(
            f"openkos query: failed -- {exc}. Start it with `ollama serve`, "
            "then try again.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except OllamaModelNotFound as exc:
        typer.echo(
            f"openkos query: failed -- model '{cfg.model}' is not installed. "
            f"Pull it with `ollama pull {cfg.model}`, then try again.",
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
        f"retrieval: {result.fts_hit_count} FTS hit{_plural(result.fts_hit_count)} "
        f"→ LLM {llm_status} → {cited_count} source{_plural(cited_count)} cited",
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
    instead of exiting on the first failure, this runs ALL five checks,
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
    (5) bundle-readable -- informational, workspace-only, `[SKIP]` outside a
    workspace. Outside a workspace, checks (3)/(4) still run against
    `config.DEFAULT_MODEL` and still determine the exit code (spec: Doctor
    Works Outside An Initialized Workspace).

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

    # 3. Ollama-reachable (critical, always)
    reachable = False
    installed: list[str] = []
    # doctor is a fast interactive diagnostic: use a short preflight timeout so a
    # hung/firewalled host fails quickly instead of blocking on DEFAULT_TIMEOUT.
    client = OllamaClient(model=model, timeout=5.0)
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
        results.append(
            CheckResult(
                "Ollama reachable",
                "fail",
                critical=True,
                remediation="ollama serve",
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

    # 5. bundle-readable (informational, workspace-only; SKIP outside)
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

    typer.echo(f"openkos doctor: checking environment at {root}")
    typer.echo()
    for r in results:
        _render_check(r)

    if any(r.status == "fail" and r.critical for r in results):
        raise typer.Exit(code=1)
