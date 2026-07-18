"""Typer application object exposed as the `openkos` console script."""

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

from openkos import config, fsio
from openkos.bundle import bundle
from openkos.bundle import index as bundle_index
from openkos.bundle import log as bundle_log
from openkos.model import okf

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

        if not index_path.is_file() or not log_path.is_file():
            typer.echo(
                "openkos ingest: refusing to ingest -- no OpenKOS workspace "
                "found in this directory (run 'openkos init' first).",
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
    description = (
        f"Raw source imported from '{src}' as {resource}; not yet compiled "
        "or extracted into concepts."
    )

    try:
        cfg = config.read_config(root)
        concept_content = okf.build_source_concept(
            title=title,
            description=description,
            resource=resource,
            tags=[],
            timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            sensitivity=cfg.default_sensitivity,
            provenance=[resource],
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
