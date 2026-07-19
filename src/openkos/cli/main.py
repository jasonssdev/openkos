"""Typer application object exposed as the `openkos` console script."""

import re
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import typer

from openkos import config, fsio
from openkos import lint as lint_check
from openkos.bundle import bundle
from openkos.bundle import index as bundle_index
from openkos.bundle import log as bundle_log
from openkos.llm.ollama import OllamaClient, OllamaError
from openkos.model import okf
from openkos.retrieval.answer import answer
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
    """Copy `src` into `raw/` and generate one OKF Source concept (null compiler, D5).

    This is the MVP 1 "null compiler": no LLM extraction happens here --
    exactly one `Source` concept is generated per invocation, with an
    honest description stating the source was imported and not yet
    compiled or extracted.

    Phase A (pure, no writes) validates and builds the entire result in
    memory, in order: `src` must be an existing, readable file, or this
    refuses; the current directory must already be a workspace (both
    `bundle/index.md` and `bundle/log.md` present), or this refuses; the
    destination name and concept slug are derived ONLY from `src`'s
    basename (`Path(src).name`/`.stem` -- directory components, including
    traversal segments like `../../evil.txt`, are always stripped, so the
    raw copy and concept document can never land outside `raw/` or
    `bundle/sources/`); if `raw/<name>` or `bundle/sources/<slug>.md`
    already exists, this refuses rather than overwriting either; otherwise
    `read_config` resolves `default_sensitivity`, the Source concept and
    the new `index.md`/`log.md` bytes are computed in memory, and a preview
    of the proposed changes is printed.

    Confirm gate, checked in order: `--auto` skips the prompt outright;
    otherwise config `review: false` skips the prompt the same way;
    otherwise, if stdin is a TTY, `typer.confirm` asks and aborts (exit 1)
    on decline; otherwise (non-TTY, `review: true`, no `--auto`) this
    refuses to write (exit 1) rather than defaulting silently, telling the
    user to re-run with `--auto` -- this intentionally diverges from
    `init`'s silent-on-non-TTY behavior, because `ingest` honors "review
    before save".

    Phase B (after confirm) writes, in order: `bundle/sources/` (created if
    absent), the raw copy (`copy_exclusive`, create-only), the concept
    document (`write_exclusive`, create-only), then `index.md` and `log.md`
    (`write_atomic`, catalog LAST -- so the catalog never points at a file
    that does not yet exist, mirroring `init`'s marker-last ordering, D3).
    Every one of these writes is itself create-only or atomic, so none is
    ever left half-written -- but Phase B as a whole is NOT transactional:
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

        if raw_dest.exists() or concept_path.exists():
            existing = (
                f"raw/{name}" if raw_dest.exists() else f"bundle/sources/{slug}.md"
            )
            typer.echo(
                f"openkos ingest: refusing to ingest -- '{existing}' already "
                "exists -- this source may already be ingested, or a "
                "previous run crashed mid-write; inspect and remove it "
                "before retrying.",
                err=True,
            )
            raise typer.Exit(code=1)
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
        index_text = index_path.read_text(encoding="utf-8")
        log_text = log_path.read_text(encoding="utf-8")
        new_index_text = bundle_index.insert_source_entry(
            index_text, title=title, slug=slug, description=description
        )
        new_log_text = bundle_log.insert_log_entry(
            log_text,
            now.astimezone().date(),
            f"**Ingest**: Imported [{title}](/sources/{slug}.md) from `{resource}`.",
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos ingest: failed while preparing the ingest -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos ingest: proposed changes:")
    typer.echo(f"  + raw/{name}")
    typer.echo(f"  + bundle/sources/{slug}.md")
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
        fsio.copy_exclusive(src, raw_dest)
        fsio.write_exclusive(concept_path, concept_content)
        fsio.write_atomic(index_path, new_index_text)
        fsio.write_atomic(log_path, new_log_text)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos ingest: failed while writing the ingest -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

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

    The answer is grounded in the concepts retrieved from the bundle and is
    printed first; when at least one concept was cited, a `Citations:`
    section follows, one `  → {concept_id} ({title})` line per citation, in
    the order they were used. When nothing in the bundle matches, a single
    no-match line is printed and the command still exits 0 -- "no answer
    found" is a valid result, not an error.

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
    except (FtsUnavailable, OllamaError) as exc:
        typer.echo(f"openkos query: failed -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(result.answer)
    if result.citations:
        typer.echo()
        typer.echo("Citations:")
        for citation in result.citations:
            typer.echo(f"  → {citation.concept_id} ({citation.title})")
