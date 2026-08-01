"""Typer application object exposed as the `openkos` console script."""

import json
import re
import shutil
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path, PurePosixPath
from typing import Literal

import typer
from rich.console import Console

from openkos import config, fsio, source_title
from openkos import lint as lint_check
from openkos.bundle import bundle, listing, source_titles
from openkos.bundle import index as bundle_index
from openkos.bundle import links as bundle_links
from openkos.bundle import log as bundle_log
from openkos.bundle import merge as bundle_merge
from openkos.bundle import provenance as bundle_provenance
from openkos.bundle import references as bundle_references
from openkos.bundle import relations as bundle_relations
from openkos.cli import next_action as next_action_module
from openkos.cli import observability
from openkos.extraction.concept import extract_concept
from openkos.graph import proximity, sqlite_graph
from openkos.graph.base import GraphStore
from openkos.graph.sqlite_graph import build_graph
from openkos.graph.summary import graph_edge_summary
from openkos.llm.base import Embedder, LLMBackend
from openkos.llm.ollama import (
    InstalledModel,
    OllamaClient,
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
    is_embedding_model,
    model_tag_matches,
)
from openkos.model import okf, types
from openkos.model.relations import validate_relation_type
from openkos.model.types import CLASSIFIABLE_TYPES as _CLASSIFIABLE_TYPES
from openkos.model.types import TYPE_TO_LINK_DIR as _TYPE_TO_LINK_DIR
from openkos.model.types import TYPE_TO_SECTION as _TYPE_TO_SECTION
from openkos.resolution import find_candidates, find_exact_title_groups
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    Verdict,
    adjudicate_candidates,
)
from openkos.resolution.candidates import CandidateGroup, Tier
from openkos.resolution.contradiction import (
    find_contradictions,
    is_high_confidence_contradiction,
)
from openkos.resolution.edge_typing import (
    EdgeSuggestion,
    candidate_edges,
    suggest_edge_types,
)
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
    vector_store_is_empty,
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

_MAX_PICKER_ATTEMPTS = 3
"""Bounded reprompt count for `_pick_chat_model`'s numeric-choice loop --
an invalid answer reprompts up to this many times before the picker gives
up and silently falls back to `config.DEFAULT_MODEL`, so a non-interactive
or misbehaving stdin can never hang `init` forever (design D3)."""

# Uniform lock-contention message for `reindex`'s two error ladders
# (vectors/fts and graph) -- a single source of truth so a locked
# vectors.db/fts.db/graph.db always reads identically regardless of which
# store hit the lock (reindex-lock-handling, decision 5).
_LOCK_CONTENTION_MSG = (
    "openkos reindex: failed -- another process is holding the workspace "
    "lock (a concurrent reindex?); wait for it to finish, then try again."
)


def _version_line() -> str:
    """The single `openkos {version}` line emitted by both `--version` and
    `doctor`'s banner, read from installed distribution metadata (never from a
    constant, so it cannot drift from the built artifact). `PackageNotFoundError`
    -- realistically only a raw `sys.path` run with no install step -- degrades to
    `openkos unknown`: `unknown` cannot be misread as a released build the way
    `0.0.0-dev` would. Staleness (a bumped `pyproject.toml` without `uv sync`) is
    an explicit NON-GOAL: this reports what is installed, not what is checked out."""
    try:
        return f"openkos {_pkg_version('openkos')}"
    except PackageNotFoundError:
        return "openkos unknown"


def _version_callback(value: bool) -> None:
    """Eager `--version` handler: print and exit 0 before Typer resolves any
    subcommand, so the flag works standalone and outside a workspace."""
    if value:
        typer.echo(_version_line())
        raise typer.Exit(code=0)


@app.callback()
def callback(
    # `version` is never read in the body and looks dead, but it is load-bearing:
    # Typer derives the `--version` option from this signature, so deleting the
    # parameter deletes the flag. All the work happens in `_version_callback`,
    # which fires eagerly during parsing. Ruff's `ARG` rules are not enabled, so
    # nothing but this comment protects it from a well-meaning cleanup.
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the installed openkos version and exit.",
        is_eager=True,
        callback=_version_callback,
    ),
) -> None:
    """openkos: local-first engine that compiles text into a portable knowledge base."""


def _probe_installed_models() -> list[InstalledModel]:
    """Single reachability probe shared by BOTH the chat-model and the
    embedding-model pickers (spec: Graceful Degradation Of The Embedding
    Picker -- MUST reuse the chat picker's existing probe call, MUST NOT
    issue a second, separate reachability request). Wrapped in a broad
    `except Exception` so an unreachable Ollama server (or any other probe
    failure) never blocks `init`; returns an empty list on any failure,
    letting each picker fall back to its own default resolution
    independently."""
    try:
        probe = OllamaClient(model=config.DEFAULT_MODEL, timeout=_PREFLIGHT_TIMEOUT)
        return probe.list_models()
    except Exception:
        return []


def _resolve_model(flag: str | None, installed: list[InstalledModel]) -> str:
    """Resolve the model tag to write, precedence flag > interactive picker > default.

    `flag` (already the raw `--model` value, or `None` if not given) wins
    outright -- no prompt or picker is shown even on a TTY. Otherwise, if
    stdin is a TTY, `_pick_chat_model` offers a numbered list of `installed`
    chat models (falling back to a typed prompt if `installed` is empty or
    no chat model is among it). If stdin is not a TTY (e.g. piped, or a
    non-interactive CI run), neither prompt nor picker is shown and the
    default is used silently. Every path runs through `config.validate_model`,
    which raises `ValueError` for a blank or unsafe value -- callers must
    catch it before any file is written.

    `installed` is the single shared reachability probe's result (see
    `_probe_installed_models`), passed in rather than probed here again.
    """
    if flag is not None:
        return config.validate_model(flag)
    if sys.stdin.isatty():
        return _pick_chat_model(installed)
    return config.DEFAULT_MODEL


def _is_selectable_model_tag(tag: str) -> bool:
    """`True` iff `tag` would pass `config.validate_model` -- used to drop a
    server-reported tag the picker could otherwise list but that would
    hard-fail `init` the moment the user selected it."""
    try:
        config.validate_model(tag)
        return True
    except ValueError:
        return False


def _pick_chat_model(installed: list[InstalledModel]) -> str:
    """Interactive numbered picker over Ollama's installed chat models.

    `installed` comes from the shared `_probe_installed_models` probe (run
    strictly before any workspace write, Phase A) -- this function issues no
    reachability request of its own. An empty `installed` list (probe
    failed, or Ollama reported nothing) falls back to the pre-picker typed
    prompt below (spec: Graceful Degradation). Embedding models (per
    `is_embedding_model`) are excluded from the candidate list; if that
    leaves zero chat models, the picker falls back the same way.
    `config.DEFAULT_MODEL` is always listed first, marked "(recommended)"
    -- prepended if the probe didn't report it installed.

    `typer.prompt("Model", default=str(<recommended index>))` reads the
    choice: pressing Enter re-supplies that default, so it resolves to the
    recommended tag with no special-casing needed. An in-range digit picks
    that list entry; anything else reprompts, up to `_MAX_PICKER_ATTEMPTS`
    times, rather than silently accepting garbage or hanging forever on a
    misbehaving/non-interactive stdin -- after which it falls back to
    `config.DEFAULT_MODEL`. The final choice is still validated by
    `config.validate_model`, same as every other `_resolve_model` path.
    """
    candidates = [
        m.tag
        for m in installed
        if not is_embedding_model(m) and _is_selectable_model_tag(m.tag)
    ]

    if not candidates:
        return config.validate_model(
            typer.prompt("Model", default=config.DEFAULT_MODEL)
        )

    if config.DEFAULT_MODEL in candidates:
        candidates.remove(config.DEFAULT_MODEL)
    candidates.insert(0, config.DEFAULT_MODEL)

    typer.echo("Installed chat models:")
    for index, tag in enumerate(candidates, start=1):
        suffix = " (recommended)" if tag == config.DEFAULT_MODEL else ""
        typer.echo(f"  {index}) {tag}{suffix}")

    for _ in range(_MAX_PICKER_ATTEMPTS):
        choice = typer.prompt("Model", default="1")
        if (
            choice.isascii()
            and choice.isdigit()
            and 1 <= int(choice) <= len(candidates)
        ):
            return config.validate_model(candidates[int(choice) - 1])
        typer.echo(
            f"openkos init: '{choice}' isn't a valid choice -- enter a "
            f"number from 1 to {len(candidates)}, or press Enter for the "
            "recommended model.",
            err=True,
        )

    return config.validate_model(config.DEFAULT_MODEL)


def _canonical_allowlist_spelling(tag: str) -> str:
    """Return the `EMBEDDING_MODEL_ALLOWLIST` spelling `tag` names, or `tag`.

    One source of truth for D3 normalization, shared by the flag path and the
    picker so the two entry points can never disagree about whether a value
    is on the allowlist. A tag matching nothing is returned unchanged -- D6's
    off-allowlist escape hatch stays verbatim, never coerced."""
    for allowed in config.EMBEDDING_MODEL_ALLOWLIST:
        if model_tag_matches(allowed, [tag]):
            return allowed
    return tag


def _resolve_embedding_model(flag: str | None, installed: list[InstalledModel]) -> str:
    """Resolve the embedding model tag to write, precedence flag >
    interactive picker over the vetted allowlist > `DEFAULT_EMBEDDING_MODEL`.

    `flag` wins outright -- validated for YAML-safety via
    `config.validate_embedding_model` but NOT gated on
    `config.EMBEDDING_MODEL_ALLOWLIST` membership (D6): an off-allowlist
    value is still written, with a non-fatal stderr warning (see
    `init`). Otherwise, if stdin is a TTY, `_pick_embedding_model` offers a
    numbered list of `installed` models that are ALSO on the allowlist
    (falling back to the silent default if none qualify). If stdin is not a
    TTY, no prompt or picker is shown and the default is used silently.

    `installed` is the single shared reachability probe's result (see
    `_probe_installed_models`) -- this resolver never issues its own
    reachability request.

    A flag value that MATCHES an allowlist entry under `model_tag_matches`
    normalization resolves to the allowlist spelling, exactly as the picker
    does (D3): `--embedding-model bge-m3:latest` names the vetted model, so
    writing the raw server-style tag would make `cfg.embedding_model` differ
    from `DEFAULT_EMBEDDING_MODEL` and trip the model-tag re-embed gate into
    a full corpus re-embed for a no-op change. This is canonicalizing one
    model's spelling, NOT the silent coercion to the default that D6
    forbids -- an off-allowlist value still matches nothing here and is
    written verbatim.
    """
    if flag is not None:
        validated = config.validate_embedding_model(flag)
        return _canonical_allowlist_spelling(validated)
    if sys.stdin.isatty():
        return _pick_embedding_model(installed)
    return config.DEFAULT_EMBEDDING_MODEL


def _pick_embedding_model(installed: list[InstalledModel]) -> str:
    """Interactive numbered picker over the vetted embedding-model allowlist.

    Candidates are filtered on `config.EMBEDDING_MODEL_ALLOWLIST` ALONE --
    deliberately NOT `is_embedding_model(m) and allowlisted` (D2): `bge-m3`
    has no `embed` substring in its tag, so `_EMBEDDING_TAG_MARKER` never
    fires for it, and it classifies as an embedding model only via
    `family == "bert"`. An installed entry reporting no `details.family`
    (`InstalledModel(tag="bge-m3", family=None)`) would silently drop the
    recommended default from its own picker if the heuristic classifier
    were stacked on top of the allowlist -- the allowlist is stronger
    evidence on its own and must gate alone.

    Matching uses `ollama.model_tag_matches` (D3): a server-reported
    `bge-m3:latest` still matches the allowlisted `bge-m3` entry via
    Ollama's `:latest` normalization, and the ALLOWLIST spelling -- never
    the raw server tag -- is what gets listed/written, so `cfg.
    embedding_model` never diverges from `DEFAULT_EMBEDDING_MODEL` for a
    no-op selection.

    Graceful degradation (spec: Graceful Degradation Of The Embedding
    Picker) differs from the chat picker: an empty `installed` list or zero
    allowlisted candidates falls back SILENTLY to `DEFAULT_EMBEDDING_MODEL`
    -- no prompt of any kind, unlike `_pick_chat_model`'s typed-prompt
    fallback -- since a fresh workspace has nothing to lose by a silent
    default and the spec explicitly forbids a second reachability request
    just to ask a question the user never opted into.
    """
    installed_tags = [m.tag for m in installed]
    candidates = [
        allowed
        for allowed in config.EMBEDDING_MODEL_ALLOWLIST
        if model_tag_matches(allowed, installed_tags)
    ]

    if not candidates:
        return config.DEFAULT_EMBEDDING_MODEL

    if config.DEFAULT_EMBEDDING_MODEL in candidates:
        candidates.remove(config.DEFAULT_EMBEDDING_MODEL)
    candidates.insert(0, config.DEFAULT_EMBEDDING_MODEL)

    typer.echo("Installed embedding models:")
    for index, tag in enumerate(candidates, start=1):
        suffix = " (recommended)" if tag == config.DEFAULT_EMBEDDING_MODEL else ""
        typer.echo(f"  {index}) {tag}{suffix}")

    for _ in range(_MAX_PICKER_ATTEMPTS):
        choice = typer.prompt("Embedding model", default="1")
        if (
            choice.isascii()
            and choice.isdigit()
            and 1 <= int(choice) <= len(candidates)
        ):
            return config.validate_embedding_model(candidates[int(choice) - 1])
        typer.echo(
            f"openkos init: '{choice}' isn't a valid choice -- enter a "
            f"number from 1 to {len(candidates)}, or press Enter for the "
            "recommended embedding model.",
            err=True,
        )

    return config.validate_embedding_model(config.DEFAULT_EMBEDDING_MODEL)


def _commit_has_confidential(root: Path, paths: Sequence[str]) -> bool:
    """`True` iff any of the given (workspace-relative, POSIX) `paths` is a
    concept file whose frontmatter `sensitivity` equals the canonical top
    rank (`okf.SENSITIVITY_ORDER[-1]`, `"confidential"`) -- design: "Confidential
    detection reads frontmatter, not `blocks_llm_send`". `blocks_llm_send`
    is a FAIL-CLOSED gate that also treats a missing/blank/unreadable
    `sensitivity` as confidential; this predicate is transparency, not a
    security gate, so it looks for an EXPLICIT `confidential` value only --
    a source with no `sensitivity` field must never trigger a false
    "confidential committed" alarm.

    `bundle/index.md`/`bundle/log.md` (catalog files, never concept
    frontmatter), any path under `raw/` (source copies, not concept
    documents), and any path missing on disk (a staged deletion has
    nothing to read) are all skipped without raising."""
    reserved = {"bundle/index.md", "bundle/log.md"}
    for rel_path in paths:
        if rel_path in reserved or rel_path.startswith("raw/"):
            continue
        file_path = root / rel_path
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
            metadata, _ = okf.load_frontmatter(text)
        except (OSError, ValueError):
            continue
        if str(metadata.get("sensitivity", "")).strip() == okf.SENSITIVITY_ORDER[-1]:
            return True
    return False


def _autocommit(root: Path, paths: Sequence[str], message: str) -> None:
    """Best-effort, non-fatal auto-commit after a mutating verb's Phase B
    (git-lifecycle Slice 2), structurally cloned from `init`'s own
    best-effort git-setup block below. Every one of `ingest`/`forget`/
    `relate`/`merge`/`unmerge`/`reconcile` calls this exactly once, on the
    success path, strictly AFTER its own confirm gate and Phase-B writes
    have already landed on disk -- so no failure mode here ever changes
    the caller's exit code or leaves a canonical write unfinished; the
    worst outcome is a stderr WARNING pointing at `git status`.

    `paths` MUST be workspace-relative, POSIX paths; staging always goes
    through `commit_paths`' scoped `git add -- <paths>` (never `-A`/`-a`),
    so a pre-existing unrelated dirty file elsewhere in the workspace is
    never swept into this commit."""
    repo = vcs_git.repo_root(root)
    if repo is None:
        typer.echo(
            "openkos: WARNING -- not a git repository; skipped auto-commit "
            "(writes are on disk).",
            err=True,
        )
        return
    if not vcs_git.has_git_identity(root):
        typer.echo(
            "openkos: WARNING -- git identity unset; skipped auto-commit "
            "(writes are on disk).",
            err=True,
        )
        return
    try:
        vcs_git.commit_paths(root, paths, message)
    except (vcs_git.GitError, OSError) as exc:
        typer.echo(
            f"openkos: WARNING -- auto-commit did not complete ({exc}); "
            "run `git status` to inspect.",
            err=True,
        )
        return
    if _commit_has_confidential(root, paths):
        typer.echo(
            "openkos: NOTICE -- this commit includes content marked "
            "'sensitivity: confidential'. openkos commits to LOCAL git "
            "only and never pushes to a remote.",
            err=True,
        )


@app.command()
def init(
    model: str | None = typer.Option(
        None,
        "--model",
        help="Ollama model tag to write into openkos.yaml. "
        "Prompted on a TTY, defaults to qwen3:8b otherwise.",
    ),
    embedding_model: str | None = typer.Option(
        None,
        "--embedding-model",
        help="Ollama embedding model tag to write into openkos.yaml. "
        "Prompted on a TTY over the vetted allowlist, defaults to bge-m3 "
        "otherwise. A value off the allowlist is still accepted, with a "
        "warning.",
    ),
) -> None:
    """Create a fresh OKF workspace in the current directory.

    Refuses (exit 1) without writing anything if the current directory
    cannot become a workspace, per the conditions `config.refusal_reason`
    checks (existing `openkos.yaml`, existing `AGENTS.md`, `raw/` or
    `bundle/` non-empty, or `raw/` or `bundle/` existing as a plain file or
    a symlink), OR if the resolved model (see `_resolve_model`: `--model`
    flag > TTY prompt > default `qwen3:8b`) or the resolved embedding model
    (see `_resolve_embedding_model`: `--embedding-model` flag > TTY picker
    over the vetted allowlist > default `bge-m3`) is blank or contains
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

    # Single shared reachability probe (spec: Graceful Degradation Of The
    # Embedding Picker -- MUST reuse the chat picker's existing probe call,
    # MUST NOT issue a second, separate reachability request). Only needed
    # when at least one of the two pickers might actually run: a TTY with at
    # least one of the two flags unset. Skipping it otherwise avoids an
    # unnecessary network call when both flags are given, or neither picker
    # can ever be shown (non-TTY).
    installed_models: list[InstalledModel] = []
    if sys.stdin.isatty() and (model is None or embedding_model is None):
        installed_models = _probe_installed_models()

    try:
        resolved_model = _resolve_model(model, installed_models)
        resolved_embedding_model = _resolve_embedding_model(
            embedding_model, installed_models
        )
    except ValueError as exc:
        typer.echo(f"openkos init: refusing to initialize -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    if (
        embedding_model is not None
        and resolved_embedding_model not in config.EMBEDDING_MODEL_ALLOWLIST
    ):
        typer.echo(
            f"openkos init: WARNING -- '{resolved_embedding_model}' is not "
            "on the vetted embedding-model allowlist; writing it anyway.",
            err=True,
        )

    layout = config.WorkspaceLayout(root)
    try:
        layout.raw_dir.mkdir(parents=True, exist_ok=True)
        bundle.create(layout.bundle_dir, datetime.now().astimezone().date())
        config.write_agents(root)
        config.write_config(
            root, model=resolved_model, embedding_model=resolved_embedding_model
        )
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

    # Best-effort git setup (Slice 1, git-lifecycle): runs strictly AFTER
    # Phase B's last write (`openkos.yaml`, just above), so any git failure
    # happens only once the workspace is already valid -- mirroring the
    # Ollama preflight's non-fatal shape below. `git init` only runs when
    # `repo_root` reports `cwd` is not already inside a git working tree
    # (never nests a repo inside a parent one); an existing `.gitignore` is
    # never overwritten; the initial commit stages ONLY the paths `init`
    # itself just created (never `-A`/`-a`, so unrelated dirty content in a
    # host repo is never swept in) and is skipped entirely -- with a stderr
    # WARNING, no fallback bot identity -- when git identity is unset. Any
    # `GitError`/`GitUnavailable`/`OSError` here is caught and reported as a
    # non-fatal stderr WARNING; `init`'s exit code and the workspace-write
    # guarantee above are unaffected either way.
    try:
        repo = vcs_git.repo_root(root)
        if repo is None:
            vcs_git.init_repo(root)

        gitignore_path = root / ".gitignore"
        wrote_gitignore = False
        if not gitignore_path.exists():
            gitignore_path.write_text(vcs_git._GITIGNORE_TEMPLATE, encoding="utf-8")
            wrote_gitignore = True

        git_paths = [
            layout.config_path.name,
            layout.agents_path.name,
            layout.raw_dir.name,
            layout.bundle_dir.name,
        ]
        if wrote_gitignore:
            git_paths.append(gitignore_path.name)

        if vcs_git.has_git_identity(root):
            vcs_git.commit_paths(
                root, git_paths, "chore(openkos): initialize workspace"
            )
        else:
            typer.echo(
                "openkos init: WARNING -- git identity unset; skipped the "
                "initial commit (the workspace and .gitignore are still "
                "created).",
                err=True,
            )
    except (vcs_git.GitError, OSError) as exc:
        # Honest for ALL failure modes: a repo/.gitignore may already have
        # been created and files staged before this error hit, so "skipped"
        # would be misleading here. Actionable: points at `git status` to
        # inspect and finish setup manually.
        typer.echo(
            f"openkos init: WARNING -- git setup did not complete cleanly ({exc}). "
            "The workspace itself is still valid; run `git status` in it to "
            "inspect and finish git setup manually if needed.",
            err=True,
        )

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
        ready = model_tag_matches(resolved_model, [m.tag for m in probe.list_models()])
    except Exception:
        ready = False
    if not ready:
        typer.echo(
            "openkos init: note -- Ollama isn't ready for model "
            f"'{resolved_model}' yet. Run `openkos doctor` to diagnose "
            "(ingest and query need it; the workspace was still created).",
            err=True,
        )

    # Sticky re-embed warning (spec: Sticky Re-Embed Warning On Every
    # Successful Init): printed UNCONDITIONALLY on every successful init,
    # regardless of TTY/non-TTY or which embedding model was resolved --
    # never conditioned on a prior corpus existing, since a fresh workspace
    # has nothing to re-embed yet. Worded about FUTURE cost only: this
    # workspace has never re-embedded anything, so it must never claim a
    # re-embed already happened.
    #
    # It also names only causes that can affect THIS workspace. An earlier
    # revision blamed "a future init of a different workspace", which cannot
    # force a re-embed here and read as a non-sequitur to anyone who did not
    # already know the model-tag gate is per-workspace.
    typer.echo(
        f"openkos init: note -- the embedding model ('{resolved_embedding_model}') "
        "is sticky: editing it in this workspace's openkos.yaml later forces "
        "a full corpus re-embed the next time `openkos reindex` runs.",
        err=True,
    )


def _plural(n: int) -> str:
    """Return `""` for `n == 1`, else `"s"` -- English plural suffix helper
    shared by the `query` command's stderr rendering."""
    return "" if n == 1 else "s"


def _format_type_tally(counts: dict[str, int]) -> str:
    """Render a per-type derived-object tally line from a `type -> count`
    dict, decoupled from any `ingest`-specific internals so other commands
    MAY reuse it (spec: Reusable Type-Tally Formatting Helper).

    Returns `""` for an empty (or all-zero) dict, signaling "no line to
    print" to the caller. Otherwise returns `extracted {N} objects -- {c}
    {Type}, ...`, ordered by canonical `_TYPE_TO_SECTION` registry order
    (NOT insertion order), so identical input always renders the same
    string."""
    total = sum(counts.values())
    if total == 0:
        return ""
    order = {t: i for i, t in enumerate(_TYPE_TO_SECTION)}
    parts = ", ".join(
        f"{counts[t]} {t}" for t in sorted(counts, key=lambda t: order[t])
    )
    return f"extracted {total} object{_plural(total)} — {parts}"


def _format_group_tally(high: int, low: int) -> str:
    """Render the leading candidate-group tally line from per-tier counts,
    decoupled from `CandidateGroup` internals so callers pass primitive
    counts (spec: Reusable Group-Tally Formatting Helper).

    Returns `""` for all-zero counts, signaling "no line to print" to the
    caller. Otherwise returns `N candidate group(s) (X exact, Y near)`."""
    total = high + low
    if total == 0:
        return ""
    return f"{total} candidate group{_plural(total)} ({high} exact, {low} near)"


def _format_verdict_tally(same: int, different: int, uncertain: int) -> str:
    """Render the leading adjudication verdict-tally line from per-verdict
    counts (spec: Reusable Verdict-Tally Formatting Helper).

    Returns `""` for all-zero counts, signaling "no line to print" to the
    caller. Otherwise returns `adjudicated N: x SAME, y DIFFERENT`, with a
    `, z UNCERTAIN` segment appended only when `uncertain > 0`."""
    total = same + different + uncertain
    if total == 0:
        return ""
    parts = f"{same} SAME, {different} DIFFERENT"
    if uncertain > 0:
        parts += f", {uncertain} UNCERTAIN"
    return f"adjudicated {total}: {parts}"


def _adjudication_payload(
    results: Sequence[AdjudicatedCandidate], *, same_only: bool
) -> list[dict[str, object]]:
    """Build the pure, I/O-free `adjudicate --json` payload from `results`,
    preserving `results` order and omitting `confidence` and any
    survivor/absorbed field (spec: Machine-Readable `--json` Output Mode).

    `tier` MUST be rendered via `.name` (uppercase `"HIGH"`/`"LOW"`), NOT
    `.value` (lowercase) -- mirrors the human path's ternary but sourced
    directly from the enum member's name. `verdict` mirrors the human path's
    `.value.upper()` rendering. `same_only=True` keeps only `Verdict.SAME`
    entries, the same predicate the human `--same-only` display filter uses."""
    return [
        {
            "member_ids": list(result.candidate.member_ids),
            "okf_type": result.candidate.okf_type,
            "tier": result.candidate.tier.name,
            "verdict": result.verdict.value.upper(),
            "rationale": result.rationale,
        }
        for result in results
        if not same_only or result.verdict is Verdict.SAME
    ]


def _prepare_one_merge(
    root: Path,
    layout: config.WorkspaceLayout,
    index_path: Path,
    log_path: Path,
    group: CandidateGroup,
) -> "PreparedMerge | None":
    """Resolve both member ids of one SAME 2-member `group` and build the
    pure `PreparedMerge` preview, extracted verbatim from
    `_run_adjudicate_apply`'s former per-pair body (issue #137 closing
    slice, Phase 1 refactor) so the interactive `--apply` walk and the
    `--apply-same` batch share exactly one apply-one-pair unit. Returns
    `None` when either member id fails to resolve
    (`_resolve_concept_path` raises `ValueError`) -- this covers BOTH a
    member already absorbed by an earlier merge in this same run AND a
    genuinely missing/invalid concept id; the two causes are
    indistinguishable from here, so the caller must not assert either one
    as the sole reason. Otherwise raises `OSError`/`ValueError` straight
    from `prepare_merge`, unchanged."""
    survivor_id, absorbed_id = group.member_ids
    try:
        survivor_path, survivor_canonical = _resolve_concept_path(
            layout.bundle_dir, survivor_id
        )
        absorbed_path, absorbed_canonical = _resolve_concept_path(
            layout.bundle_dir, absorbed_id
        )
    except ValueError:
        return None

    now = datetime.now(UTC)
    return prepare_merge(
        layout.bundle_dir,
        index_path,
        log_path,
        survivor_path,
        absorbed_path,
        survivor_canonical,
        absorbed_canonical,
        root,
        now=now,
    )


def _format_merge_preview_line(prepared: "PreparedMerge") -> str:
    """The "merge X into Y (...)" preview line for one prepared merge,
    extracted verbatim from the former inline body (issue #137 closing
    slice, Phase 1 refactor)."""
    return (
        f"  merge {prepared.absorbed_canonical} into {prepared.survivor_canonical} "
        f"(sensitivity {prepared.sensitivity_before}->"
        f"{prepared.sensitivity_after}, {len(prepared.touched_files)} "
        f"rewrite(s), removes bundle/{prepared.absorbed_canonical}.md)"
    )


def _commit_one_merge(
    root: Path,
    layout: config.WorkspaceLayout,
    index_path: Path,
    log_path: Path,
    prepared: "PreparedMerge",
) -> None:
    """`merge_core` + `_autocommit` for one prepared merge, extracted
    verbatim from the former inline body (issue #137 closing slice, Phase 1
    refactor). Raises `OSError`/`ValueError` straight from `merge_core`,
    unchanged -- callers decide how to report and whether to stop."""
    merge_result = merge_core(layout.bundle_dir, index_path, log_path, prepared)
    _autocommit(
        root,
        [
            "bundle/index.md",
            "bundle/log.md",
            *(f"bundle/{rel}" for rel in merge_result.touched_files),
            f"bundle/{prepared.survivor_canonical}.md",
            f"bundle/{prepared.absorbed_canonical}.md",
        ],
        f"openkos: merge {prepared.absorbed_canonical} into "
        f"{prepared.survivor_canonical}",
    )


def _run_adjudicate_apply(
    root: Path,
    layout: config.WorkspaceLayout,
    index_path: Path,
    log_path: Path,
    results: Sequence[AdjudicatedCandidate],
) -> None:
    """The interactive `adjudicate --apply` merge walk (issue #137 Slice
    2b-ii, design D2-D9): per SAME 2-member group (D3), re-verify both
    member ids still exist (D4, since an earlier merge this same run may
    have absorbed a later group's member), preview what `prepare_merge`
    would fuse (D5), prompt `[y/N/skip]` (D6), and on `y` execute
    `merge_core` + `_autocommit` (D7) -- reusing every 2b-i building block
    verbatim. A mid-run write failure (D8) stops the loop immediately;
    prior per-merge commits remain intact and reversible via `unmerge`. A
    final summary line (D9) always prints, even when nothing is eligible."""
    applied = 0
    skipped_n_gt2 = 0
    skipped_already_merged = 0
    skipped_declined = 0

    for result in results:
        group = result.candidate
        if result.verdict is not Verdict.SAME:
            continue
        if len(group.member_ids) != 2:
            if len(group.member_ids) > 2:
                typer.echo(
                    f"[{group.okf_type}] {group.member_ids}: "
                    f"skipped (N>2, merge manually)"
                )
                skipped_n_gt2 += 1
            continue

        survivor_id, absorbed_id = group.member_ids

        try:
            prepared = _prepare_one_merge(root, layout, index_path, log_path, group)
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos adjudicate --apply: failed while merging "
                f"{absorbed_id} into {survivor_id} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        if prepared is None:
            typer.echo(
                f"{survivor_id} / {absorbed_id}: skipped (member unresolved "
                "-- already merged or missing)"
            )
            skipped_already_merged += 1
            continue

        typer.echo(_format_merge_preview_line(prepared))
        answer = typer.prompt(
            f"Merge {prepared.absorbed_canonical} into "
            f"{prepared.survivor_canonical}? [y/N/skip]",
            default="N",
            show_default=False,
        )
        if answer.strip().lower() not in {"y", "yes"}:
            skipped_declined += 1
            continue

        try:
            _commit_one_merge(root, layout, index_path, log_path, prepared)
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos adjudicate --apply: failed while merging "
                f"{prepared.absorbed_canonical} into "
                f"{prepared.survivor_canonical} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        applied += 1

    skipped_total = skipped_n_gt2 + skipped_already_merged + skipped_declined
    prefix = "nothing to apply -- " if applied == 0 and skipped_total == 0 else ""
    typer.echo(
        f"openkos adjudicate --apply: {prefix}applied {applied}, skipped "
        f"{skipped_total} (N>2: {skipped_n_gt2}, "
        f"already-merged: {skipped_already_merged}, declined: {skipped_declined})"
    )


def _run_adjudicate_apply_same(
    root: Path,
    layout: config.WorkspaceLayout,
    index_path: Path,
    log_path: Path,
    results: Sequence[AdjudicatedCandidate],
    *,
    confirm_count: str | None,
) -> None:
    """The guarded batch `adjudicate --apply-same` merge (issue #137
    closing slice): Pass 1 builds ONE aggregate preview over every eligible
    SAME 2-member group (spec: Aggregate Preview Before Any Write), reusing
    the SAME `_prepare_one_merge`/`_format_merge_preview_line` building
    blocks the interactive `--apply` walk uses. `total` -- the number
    printed after the preview and required by the gate -- equals the
    number of preview lines ACTUALLY DISPLAYED (i.e. the eligible groups
    that still resolve right now), never the raw structural eligible-group
    count; a group that is already unresolvable when the preview is built
    (bogus/missing id, unrelated to this run) is silently excluded from
    the preview, the total, and Pass 2 (4R fix: preview/Total
    consistency). Zero eligible groups short-circuits with a "nothing to
    apply" summary and exit 0 -- BEFORE the confirm gate -- mirroring
    `_run_adjudicate_apply`'s own empty-state handling, so an empty batch
    never triggers the non-TTY refusal or forces typing "0" on a TTY (4R
    fix: zero-eligible spurious failure). The confirmation gate (spec:
    Typed-Count Confirmation Gate) then requires the operator to type that
    EXACT count, via `--confirm-count`, an interactive TTY prompt, or
    refuses outright on a non-TTY without the flag -- any mismatch aborts
    with ZERO writes. Pass 2 RE-RESOLVES and RE-PREPARES each previewed
    pair immediately before applying it (spec: Stale-Id Guard Across
    Batch), since an earlier merge in THIS SAME batch may already have
    absorbed a later pair's member; that legitimate case is still skipped,
    not crashed on, and still yields applied < previewed. Accepted merges
    commit sequentially via `_commit_one_merge`; a mid-batch failure stops
    the run but keeps every prior commit intact and reversible via
    `unmerge` -- and, before raising, echoes a partial summary (applied so
    far / previewed, and that the remainder was never attempted) so the
    operator can drive that recovery without reconstructing the count
    themselves (spec: Sequential Execution And Mid-Batch Failure
    Semantics; 4R fix: mid-batch failure hides the applied count)."""
    eligible_groups: list[CandidateGroup] = []
    skipped_n_gt2 = 0
    for result in results:
        if result.verdict is not Verdict.SAME:
            continue
        group = result.candidate
        if len(group.member_ids) == 2:
            eligible_groups.append(group)
        elif len(group.member_ids) > 2:
            typer.echo(
                f"[{group.okf_type}] {group.member_ids}: skipped (N>2, merge manually)"
            )
            skipped_n_gt2 += 1

    previewed_groups: list[CandidateGroup] = []
    for group in eligible_groups:
        survivor_id, absorbed_id = group.member_ids
        try:
            prepared = _prepare_one_merge(root, layout, index_path, log_path, group)
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos adjudicate --apply-same: failed while previewing "
                f"{absorbed_id} into {survivor_id} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        if prepared is None:
            continue
        previewed_groups.append(group)
        typer.echo(_format_merge_preview_line(prepared))
    total = len(previewed_groups)
    typer.echo(f"Total: {total}")

    if total == 0:
        skipped_total = skipped_n_gt2
        prefix = "nothing to apply -- " if skipped_total == 0 else ""
        typer.echo(
            f"openkos adjudicate --apply-same: {prefix}applied 0, skipped "
            f"{skipped_total} (N>2: {skipped_n_gt2}, already-merged: 0)"
        )
        return

    if confirm_count is not None:
        typed_count = confirm_count
    elif sys.stdin.isatty():
        typed_count = typer.prompt(f"Type the eligible count ({total}) to proceed")
    else:
        typer.echo(
            "openkos adjudicate --apply-same: refusing to apply -- stdin is "
            "not a TTY; re-run with --confirm-count.",
            err=True,
        )
        raise typer.Exit(code=1)

    if typed_count.strip() != str(total):
        typer.echo(
            "openkos adjudicate --apply-same: aborted -- confirmation count "
            "did not match exactly; nothing was written.",
            err=True,
        )
        raise typer.Exit(code=1)

    applied = 0
    skipped_already_merged = 0
    for group in previewed_groups:
        survivor_id, absorbed_id = group.member_ids
        try:
            prepared = _prepare_one_merge(root, layout, index_path, log_path, group)
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos adjudicate --apply-same: failed while merging "
                f"{absorbed_id} into {survivor_id} -- {exc}.",
                err=True,
            )
            typer.echo(
                "openkos adjudicate --apply-same: stopped after failure -- "
                f"applied {applied} of {total} previewed before this "
                "failure; the remaining pairs were not attempted. Applied "
                "merges remain committed and reversible via `unmerge`.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        if prepared is None:
            typer.echo(
                f"{survivor_id} / {absorbed_id}: skipped (member unresolved "
                "-- already merged or missing)"
            )
            skipped_already_merged += 1
            continue

        try:
            _commit_one_merge(root, layout, index_path, log_path, prepared)
        except (OSError, ValueError) as exc:
            typer.echo(
                "openkos adjudicate --apply-same: failed while merging "
                f"{prepared.absorbed_canonical} into "
                f"{prepared.survivor_canonical} -- {exc}.",
                err=True,
            )
            typer.echo(
                "openkos adjudicate --apply-same: stopped after failure -- "
                f"applied {applied} of {total} previewed before this "
                "failure; the remaining pairs were not attempted. Applied "
                "merges remain committed and reversible via `unmerge`.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        applied += 1

    skipped_total = skipped_n_gt2 + skipped_already_merged
    typer.echo(
        f"openkos adjudicate --apply-same: applied {applied} of {total} "
        f"previewed, skipped {skipped_total} (N>2: {skipped_n_gt2}, "
        f"already-merged: {skipped_already_merged})"
    )


_SLUG_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


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
    """Turn a filename stem into a human-readable title: `-`/`_` -> spaces.

    Delegates to `bundle.source_titles.titleize` (design D1), promoted
    there so `ingest` and `backfill-source-titles` share exactly ONE
    implementation.
    """
    return source_titles.titleize(stem)


# `_TYPE_TO_LINK_DIR`/`_TYPE_TO_SECTION` are now derived from
# `openkos.model.types.REGISTRY` -- see that module for the single source of
# truth. `extraction.ExtractionResult.type` -> catalog section / bundle
# subdirectory (design: Path/Catalog).


def _collision_family(link_dir: Path, base_slug: str) -> list[Path]:
    """Return every file in `link_dir` belonging to `base_slug`'s collision
    family -- `<base_slug>.md` itself and every `<base_slug>-N.md` (N a
    positive integer) -- sorted ascending by `N` (the bare base slug sorts
    first). Matched via a REGEX anchored on the full filename stem
    (`^{base}(-\\d+)?$`), NEVER a glob, so an unrelated sibling like
    `<base>-word.md` never joins the family (design: Collision loop
    mechanics; #131)."""
    if not link_dir.is_dir():
        return []
    pattern = re.compile(rf"^{re.escape(base_slug)}(?:-(\d+))?$")
    members: list[tuple[int, Path]] = []
    for path in link_dir.glob("*.md"):
        match = pattern.match(path.stem)
        if match is None:
            continue
        suffix_n = int(match.group(1)) if match.group(1) else 0
        members.append((suffix_n, path))
    members.sort(key=lambda item: item[0])
    return [path for _, path in members]


def _family_owns_source(family: list[Path], source_slug: str) -> bool:
    """`True` if ANY member of `family` already carries THIS ingest's
    `sources/<source_slug>` provenance key -- the sole idempotency
    guarantee that a re-ingest never spawns a new disambiguated slug,
    including for a `<slug>-N` this source previously won (design:
    Idempotency Predicate; #131). A member whose frontmatter fails to read
    or parse is skipped, never raised -- the scan degrades per member,
    mirroring `okf._iter_docs`'s broad parse-failure tolerance."""
    provenance_key = f"sources/{source_slug}"
    for path in family:
        try:
            text = path.read_text(encoding="utf-8")
            metadata, _ = okf.load_frontmatter(text)
        except (OSError, UnicodeDecodeError):
            continue
        except Exception:  # noqa: S112 -- broad: malformed frontmatter degrades, never crashes
            continue
        provenance = metadata.get("provenance")
        if isinstance(provenance, list) and provenance_key in provenance:
            return True
    return False


def _read_source_sensitivity(concept_path: Path) -> object:
    """Raw `sensitivity` from an EXISTING Source concept, unranked.

    Returns the raw frontmatter value (possibly missing, blank, non-string)
    for `okf.combine_sensitivity` to rank fail-closed per ADR-0003.
    Raises `ValueError` when the file cannot be read or its frontmatter
    cannot be parsed -- a re-ingest MUST NOT degrade an unreadable
    classification to the config default (design: "Where the on-disk read
    happens, and how it fails"). This deliberately diverges from
    `_family_owns_source`'s degrade-and-continue pattern (`:1130`), which
    is a best-effort scan; this is a security field."""
    try:
        text = concept_path.read_text(encoding="utf-8")
        metadata, _ = okf.load_frontmatter(text)
    except OSError as exc:
        raise ValueError(
            f"refusing to ingest -- '{concept_path}' could not be read to "
            f"resolve its existing sensitivity: {exc}"
        ) from exc
    except Exception as exc:
        # `frontmatter.loads` raises `yaml.YAMLError` on malformed YAML,
        # which is neither `OSError` nor `ValueError` -- translate rather
        # than degrade (design gotcha).
        raise ValueError(
            f"refusing to ingest -- '{concept_path}' frontmatter could not "
            f"be parsed to resolve its existing sensitivity: {exc}"
        ) from exc
    return metadata.get("sensitivity")


def _read_source_title(concept_path: Path) -> object:
    """Raw `title` from an EXISTING Source concept, read so a re-ingest's
    preview can name a title change instead of overwriting it silently
    (review finding on issue #248's content-derived title: the regenerate
    preview never mentioned that re-ingest recomputes and overwrites a
    pre-existing Source's title). Mirrors `_read_source_sensitivity`'s
    shape exactly -- same read, same `load_frontmatter` call, same
    fail-closed `ValueError` on an unreadable or unparseable file -- but
    for `title` rather than `sensitivity`. This does NOT make `title`
    sticky: the caller uses the return value only to decide what the
    preview SAYS, never to decide what gets WRITTEN -- re-ingest still
    rebuilds `title` from content every run, unaffected by this read."""
    try:
        text = concept_path.read_text(encoding="utf-8")
        metadata, _ = okf.load_frontmatter(text)
    except OSError as exc:
        raise ValueError(
            f"refusing to ingest -- '{concept_path}' could not be read to "
            f"resolve its existing title: {exc}"
        ) from exc
    except Exception as exc:
        # `frontmatter.loads` raises `yaml.YAMLError` on malformed YAML,
        # which is neither `OSError` nor `ValueError` -- translate rather
        # than degrade (design gotcha), matching `_read_source_sensitivity`.
        raise ValueError(
            f"refusing to ingest -- '{concept_path}' frontmatter could not "
            f"be parsed to resolve its existing title: {exc}"
        ) from exc
    return metadata.get("title")


def _first_free_disambiguated_slug(
    family: list[Path], base_slug: str, reserved: set[str]
) -> str:
    """First free `<base_slug>-N` (N starting at 2) that is neither already
    on disk (a stem present in `family`) nor already claimed by an earlier
    candidate in THIS batch (`reserved`) -- deterministic, ascending scan
    (design: Collision loop mechanics -- batch-local `seen_slugs` guard;
    #131)."""
    taken = {path.stem for path in family} | reserved
    n = 2
    while f"{base_slug}-{n}" in taken:
        n += 1
    return f"{base_slug}-{n}"


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
    disambiguated_from: str | None = None
    """The original, colliding slug this plan was disambiguated away from
    -- `None` for the ordinary (no-collision) case. Set only when a
    foreign-source collision redirected this candidate to `<slug>-N`
    (design: Disambiguation loop, #131); Phase B uses it to emit the one
    audit `insert_log_entry` call for a disambiguated write."""


def _stage_derived_objects(
    *,
    raw_content: str | None,
    source_title: str,
    source_slug: str,
    workspace_floor: str,
    stamp_sensitivity: str,
    timestamp: str,
    bundle_dir: Path,
    llm: LLMBackend,
    include_confidential: bool = False,
) -> tuple[list[_DerivedPlan], okf.ExtractionStatus | None]:
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

    Returns a `(plans, skip_reason)` tuple (issue #187, design:
    `_stage_derived_objects` return shape). `skip_reason` carries WHY this
    batch produced zero derived objects, for the caller to stamp onto the
    Source's `extraction_status` frontmatter key -- `None` on the healthy
    path (`plans` non-empty). Returns `([], "no-extractable-text")` -- always
    a Source-only degrade for this batch, never a raised error -- when
    `raw_content` is `None` or blank (a binary/undecodable or empty source
    has no text to extract from, so the LLM is never called); returns
    `([], "blocked-by-sensitivity")` when the workspace floor blocks the LLM
    send; returns `([], "failed")` when `llm.chat` raises any
    `OllamaError`-family exception (caught HERE, per design's "Degrade seam"
    -- `extraction/concept.py` lets it propagate unswallowed); returns
    `([], "no-concepts-found")` when `extract_concept` itself returns `[]`
    (`[]`, never `None`, is `extract_concept`'s contract -- design D4 --
    meaning either nothing was worth extracting, or every candidate failed
    ITS OWN fail-closed validation; this layer does not distinguish the
    two). `plans == [] and skip_reason is None` is also possible (every
    candidate dropped individually below) -- that state deliberately writes
    no `extraction_status` key (design: Sequence, "a real, deliberate
    state").

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
    `True`, `extract` gates on the WORKSPACE floor (`workspace_floor`,
    always `cfg.default_sensitivity`) rather than any per-doc value (a raw
    source has no per-doc `sensitivity` yet, unlike the other five
    `llm.chat` seams): when `sensitivity.blocks_llm_send(workspace_floor)`
    -- i.e. the workspace's `default_sensitivity` floor is confidential (or
    absent/blank, correction batch post-4R-review FIX 1) -- this returns `[]`
    WITHOUT calling `extract_concept` at all, so `llm.chat` is never invoked,
    and emits the same Source-only degrade message shape as the
    blank-content case above. `include_confidential=True` bypasses this gate
    entirely. This delegates to the SAME shared `blocks_llm_send` authority
    `sensitivity.sensitive_concept_ids` uses per-doc, rather than calling
    `okf._rank` directly on `workspace_floor` -- a bare `okf._rank` call would
    wrongly resolve a blank/whitespace `default_sensitivity: ""` to
    `"private"` (never tripping this gate), because `okf._rank(None)`/
    `okf._rank("")` both fall back to `"private"` for the unrelated
    `combine_sensitivity` merge-floor use case, not this fail-closed one.

    `stamp_sensitivity` -- the built Source document's OWN resolved
    `sensitivity` value, read back from its rendered frontmatter by the
    caller -- is the value every validated derived object is stamped with
    (`okf.build_concept` below), so a derived object provably inherits its
    Source's actual value rather than merely sharing the same config
    constant (design: "Read the Source document back, and split the two
    `sensitivity` roles"). This is deliberately a SEPARATE parameter from
    `workspace_floor`: the extraction gate above MUST keep reading the
    workspace floor (`sensitivity-aware-llm` Requirement 4, unchanged by
    this change), never the Source's own value, even when the two differ.
    """
    if raw_content is None or not raw_content.strip():
        typer.echo(
            "openkos ingest: source has no extractable text; keeping the Source only.",
            err=True,
        )
        return [], "no-extractable-text"

    if not include_confidential and blocks_llm_send(workspace_floor):
        typer.echo(
            "openkos ingest: workspace default_sensitivity floor is confidential; "
            "skipping concept extraction, keeping the Source only. The Source "
            "is still added to the embedding index so search and candidate "
            "relations keep working -- the sensitivity floor governs "
            "`llm.chat`, not embeddings.",
            err=True,
        )
        return [], "blocked-by-sensitivity"

    try:
        with Console(stderr=True).status("openkos ingest: extracting concepts…"):
            extractions = extract_concept(
                raw_content, source_title=source_title, llm=llm
            )
    except OllamaError as exc:
        typer.echo(
            f"openkos ingest: concept extraction skipped -- {exc}; "
            "keeping the Source only.",
            err=True,
        )
        return [], "failed"

    if not extractions:
        typer.echo(
            "openkos ingest: no concept extracted from this source; "
            "keeping the Source only.",
            err=True,
        )
        return [], "no-concepts-found"

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
        link_dir_path = bundle_dir / link_dir
        derived_path = link_dir_path / f"{derived_slug}.md"
        original_slug: str | None = None
        if derived_path.exists():
            # A slug already on disk. Distinguish WHO owns it (design:
            # Idempotency Predicate, #131): scan the whole `<slug>`/
            # `<slug>-N` collision family for THIS ingest's own provenance
            # key before deciding.
            family = _collision_family(link_dir_path, derived_slug)
            if _family_owns_source(family, source_slug):
                # Same-source collision, anywhere in the family (including a
                # `<slug>-N` this source previously won) -- create-only
                # no-op (design D5): leave every existing file untouched.
                typer.echo(
                    f"openkos ingest: '{derived_slug}' already exists; "
                    "skipping this candidate (create-only).",
                    err=True,
                )
                continue
            # Foreign-source collision -- disambiguate to the first free
            # numeric suffix rather than dropping the candidate.
            original_slug = derived_slug
            derived_slug = _first_free_disambiguated_slug(
                family, original_slug, seen_slugs
            )
            derived_path = link_dir_path / f"{derived_slug}.md"
            typer.echo(
                f"openkos ingest: '{original_slug}' already exists for a "
                f"different source; disambiguating this candidate to "
                f"'{derived_slug}'.",
                err=True,
            )

        try:
            content = okf.build_concept(
                type=extraction.type,
                title=extraction.title,
                description=extraction.description,
                body=extraction.body,
                provenance=[f"sources/{source_slug}"],
                sensitivity=stamp_sensitivity,
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
                disambiguated_from=original_slug,
            )
        )

    return plans, None


def _embed_after_ingest(
    layout: config.WorkspaceLayout, embedder: Embedder, *, model_tag: str
) -> None:
    """Embed the concepts `ingest` just wrote, so candidate edges are
    available in the SAME run (#183).

    Without this, `vectors.db` stays absent until a separate `openkos
    reindex`, so a user's first `suggest-relations` after ingesting always
    reports an empty graph -- the symptom issue #183 opens with.

    FAIL-OPEN, and deliberately so. Embeddings are an enhancement layered
    onto ingest; the Source and its concepts are already written and
    COMMITTED by the time this runs. Losing embeddings must never cost the
    user the ingest itself, so every ordinary exception from the embed
    itself degrades to one stderr notice and an unchanged exit code.

    Scope of that promise, stated precisely rather than overclaimed: it
    covers the embed. It does not cover a failure to WRITE the notice --
    a `BrokenPipeError` from a closed downstream pipe still propagates, as
    it does from every other `typer.echo` in this module. Making stderr
    writes unkillable is a repo-wide concern, not this function's.

    The `except Exception` is broad ON PURPOSE, mirroring
    `vectorstore.probe_vec_loadable`'s rationale: not just the three mapped
    `OllamaError` subclasses, but any exception a backend might raise that
    nobody anticipated. `KeyboardInterrupt` and `SystemExit` derive from
    `BaseException`, so a user's Ctrl-C still interrupts the command rather
    than being mistaken for a degraded embed.

    Reuses `state.reindex.reindex` rather than embedding here (design
    Decision D), and passes NO `fts_db_path`: the FTS index is `reindex`'s
    job, not ingest's, and rebuilding it here would make every ingest pay
    for a full-text rebuild it did not ask for."""
    try:
        with open_vector_store(layout.vectors_db_path) as db:
            report = reindex_module.reindex(
                layout.bundle_dir, db, embedder, model_tag=model_tag
            )
    except Exception as exc:
        typer.echo(
            f"openkos ingest: embeddings not updated -- {exc}; candidate "
            "relations unavailable until `openkos reindex` succeeds.",
            err=True,
        )
        return

    # An exception is not the only way embedding degrades. `reindex` treats
    # a generic `OllamaError` as a PER-DOC transient failure and folds it
    # into `embed_failed` instead of raising -- only `OllamaUnavailable` and
    # `OllamaModelNotFound` are fatal enough to propagate. Reporting solely
    # on exceptions would therefore let a run where nothing was embedded
    # look identical to a clean one, and the user would meet the silence
    # later, as an inexplicably empty `suggest-relations`.
    if report.embed_failed:
        typer.echo(
            f"openkos ingest: embeddings not updated for {report.embed_failed} "
            f"doc{_plural(report.embed_failed)}; candidate relations may be "
            "incomplete until `openkos reindex` succeeds.",
            err=True,
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
        # `title` is derived from the decoded content (issue #248): a
        # binary/undecodable source (`raw_content is None`) and a blank or
        # whitespace-only decoded source (`not raw_content.strip()`) MUST
        # NOT call the helper at all -- neither has any usable text to
        # derive a title from, and the spec requires derivation to not run
        # for either case, not merely to return `None` for them. Any other
        # `None` result (no usable candidate found in real content) falls
        # back to today's slug title, unchanged. This single assignment
        # feeds every downstream consumer -- frontmatter `title`, the
        # Source's own `# ` heading, `index.md`/`log.md`, and
        # `_stage_derived_objects`'s LLM prompt (design: "Call-site wiring
        # in `ingest`").
        derived_title = (
            None
            if raw_content is None or not raw_content.strip()
            else source_title.derive_source_title(raw_content)
        )
        title = derived_title if derived_title is not None else _titleize(src.stem)
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
        # Re-ingest must never lower a Source's sensitivity (issue #229):
        # when an existing Source is being regenerated, resolve as the
        # high-water mark of its on-disk value and the config default,
        # BEFORE the document is built, so the single resolved value flows
        # through both the bytes written to `concept_path` and the
        # `stamp_sensitivity` read back below (design: "Resolve before
        # build, not merge after build"). A concept-absent regenerate
        # (post-`forget`) resolves directly to `cfg.default_sensitivity` --
        # `None` must never reach `combine_sensitivity`, or a `public`
        # workspace would be wrongly raised to `private` (`okf._rank(None)`
        # floors at `private`).
        had_prior_source = regenerate and concept_path.exists()
        if had_prior_source:
            on_disk_sensitivity = _read_source_sensitivity(concept_path)
            resolved_sensitivity = okf.combine_sensitivity(
                on_disk_sensitivity, cfg.default_sensitivity
            )
            # Read BEFORE `title` is (re)computed below, purely so the
            # preview can NAME a retitle -- never to make `title` sticky
            # (review finding: re-ingest silently overwrote a pre-existing
            # Source's title with no mention in the preview). `title`
            # itself is still rebuilt from content every run, exactly as
            # before this read existed.
            on_disk_title = _read_source_title(concept_path)
        else:
            on_disk_sensitivity = None
            resolved_sensitivity = cfg.default_sensitivity
            on_disk_title = None

        def _build_source_document(
            extraction_status: okf.ExtractionStatus | None,
        ) -> str:
            """Bound immediately before the first build (design: "The
            ordering conflict"). Builds the Source document from-scratch
            from this run's local inputs -- called once with `None`
            before staging (`:1717` today), and, ONLY when staging produces
            a `skip_reason`, called a SECOND time with that reason so the
            key is stamped onto freshly built content, never merged onto
            on-disk frontmatter. The healthy path calls this exactly once,
            so its output stays byte-identical to before this parameter
            existed."""
            return okf.build_source_concept(
                title=title,
                description=description,
                resource=resource,
                tags=[],
                timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                sensitivity=resolved_sensitivity,
                provenance=[resource],
                raw_content=raw_content,
                extraction_status=extraction_status,
            )

        concept_content = _build_source_document(None)
        # Extraction runs AFTER the Source concept is built, BEFORE the
        # preview (design: Technical Approach) -- always attempted, even
        # under `--auto`; only the confirm PROMPT is skipped by `--auto`.
        # `derived_plans` is the FULL, already-reconciled Phase A write set
        # (design D5 pinned ordering) -- zero or more entries, in reply
        # order. `skip_reason` (issue #187) is `None` on the healthy path.
        source_metadata, _ = okf.load_frontmatter(concept_content)
        source_sensitivity = str(source_metadata["sensitivity"])
        derived_plans, skip_reason = _stage_derived_objects(
            raw_content=raw_content,
            source_title=title,
            source_slug=slug,
            workspace_floor=cfg.default_sensitivity,
            stamp_sensitivity=source_sensitivity,
            timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            bundle_dir=layout.bundle_dir,
            llm=OllamaClient(model=cfg.model),
            include_confidential=include_confidential,
        )
        if skip_reason is not None:
            # Re-render from scratch with the discovered reason stamped in
            # (design: "The ordering conflict", conditional re-render) --
            # never patch the already-built bytes, and never read
            # `extraction_status` off disk (unlike `sensitivity` above):
            # this key is always recomputed fresh for THIS run alone.
            concept_content = _build_source_document(skip_reason)
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
            if plan.disambiguated_from is not None:
                # Durable disambiguation audit (spec: Durable Disambiguation
                # Audit Log, #131) -- one extra `log.md` bullet via the SAME
                # `insert_log_entry` primitive, no new persisted ledger file.
                new_log_text = bundle_log.insert_log_entry(
                    new_log_text,
                    now.astimezone().date(),
                    f"**Disambiguation**: [{plan.title}]"
                    f"(/{plan.link_dir}/{plan.slug}.md) from source '{slug}' "
                    f"collided with '{plan.disambiguated_from}'; wrote "
                    f"distinct concept '{plan.slug}'.",
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
        # The resolved level is always named; the trailing clause
        # distinguishes the three re-ingest causes, selected with
        # `okf.sensitivity_direction(on_disk, cfg.default_sensitivity)`
        # (design: preview wording table). `had_prior_source` is `False`
        # only for the post-forget case (no prior Source to read), which
        # reports "from the workspace default" instead.
        if had_prior_source:
            direction = okf.sensitivity_direction(
                on_disk_sensitivity, cfg.default_sensitivity
            )
            if direction == "lower":
                sensitivity_clause = "preserved from the existing Source"
            elif direction == "raise":
                sensitivity_clause = "raised by the workspace default"
            else:
                sensitivity_clause = "unchanged"
        else:
            sensitivity_clause = "from the workspace default"
        # Review finding: re-ingest recomputes `title` from content every
        # run (unaffected by this on-disk read -- only the PREVIEW WORDING
        # depends on it) and previously overwrote a pre-existing Source's
        # title with no mention in the preview. Name the change ONLY when
        # `on_disk_title` is known (`had_prior_source`) and actually
        # differs from the freshly derived `title` -- silence on the
        # common (unchanged) path is deliberate, matching the sensitivity
        # clause's own restraint on its "unchanged" branch.
        title_clause = (
            f"; title changed from {on_disk_title!r} to {title!r}"
            if on_disk_title is not None and on_disk_title != title
            else ""
        )
        typer.echo(
            f"  ~ bundle/sources/{slug}.md (regenerated -- sensitivity "
            f"{resolved_sensitivity} {sensitivity_clause}{title_clause})"
        )
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
    if derived_plans:
        typer.echo(_format_type_tally(Counter(plan.doc_type for plan in derived_plans)))

    _autocommit(
        root,
        [*imported_paths, "bundle/index.md", "bundle/log.md"],
        f"openkos: ingest {name} (+{len(derived_plans)} concepts)",
    )

    # AFTER the commit, never before: the ingest is durable by this point,
    # so a failing embedder degrades to a notice instead of stranding
    # written-but-uncommitted files (#183).
    _embed_after_ingest(
        layout,
        OllamaClient(model=cfg.embedding_model),
        model_tag=cfg.embedding_model,
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

    forget_message = f"openkos: forget {canonical_id}"
    if len(purge_ids) > 1:
        forget_message += f" (+{len(purge_ids) - 1} descendants)"
    _autocommit(
        root,
        [
            "bundle/index.md",
            "bundle/log.md",
            *(f"bundle/{member}.md" for member in purge_ids),
        ],
        forget_message,
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

    # Post-rewrite live-tree auto-commit (design: "purge empty-diff guard",
    # load-bearing): `_purge_clean_live_*` frequently leaves `index.md`/
    # `log.md` byte-identical to filter-repo's own rewrite (a no-op), and
    # `_autocommit` -> `commit_paths` runs `git commit` UNCONDITIONALLY,
    # raising `GitError` on an empty diff -- so a scoped `paths_dirty` probe
    # gates the call, avoiding a spurious WARNING on the common clean-purge
    # path. If the probe itself raises `GitError` (e.g. a genuinely broken
    # repo), fall through and attempt `_autocommit` anyway -- its own
    # try/except keeps that non-fatal too, matching this whole step's
    # never-fail-the-already-irreversible-purge contract.
    commit_paths_rel = ["bundle/index.md", "bundle/log.md"]
    try:
        should_commit = vcs_git.paths_dirty(root, commit_paths_rel)
    except vcs_git.GitError:
        should_commit = True
    if should_commit:
        commit_message = f"openkos: purge {canonical_id}"
        if len(purge_ids) > 1:
            commit_message += f" (+{len(purge_ids) - 1})"
        _autocommit(root, commit_paths_rel, commit_message)

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
    # #142 (purge-transactional-cleanup): `_purge_rebuild_indexes` always
    # deletes `.openkos/vectors.db` and deliberately does NOT rebuild it
    # (design: "Index cleanup decision") -- warn every time so an operator
    # is never left assuming dense retrieval is still intact.
    typer.echo(
        "openkos purge: dense retrieval degraded (vectors.db dropped) — "
        "run `openkos reindex` to restore it."
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

    _autocommit(
        root,
        [f"bundle/{source_canonical}.md", "bundle/log.md"],
        f"openkos: relate {source_canonical} -> {target_canonical} ({rel_type})",
    )


@app.command("set-sensitivity")
def set_sensitivity_cmd(
    concept_id: str = typer.Argument(
        ...,
        help="Bundle-relative concept id (path minus '.md') to update.",
    ),
    level: str = typer.Argument(
        ...,
        help="New sensitivity level: one of 'public', 'private', 'confidential'.",
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
    allow_downgrade: bool = typer.Option(
        False,
        "--allow-downgrade",
        help=(
            "Permit a lowering assignment on a path where the confirm "
            "prompt does not run (--auto, or config review: false)."
        ),
    ),
) -> None:
    """Set exactly one existing concept's `sensitivity` field directly --
    the write layer of the sensitivity-config domain (write-verb #185).
    Touches `<concept-id>`'s own frontmatter, PLUS, when `<concept-id>`
    resolves to a Source-typed concept, raises (never lowers) the
    `sensitivity` of every provenance descendant found by
    `bundle.provenance.find_provenance_descendants`, each combined via
    `okf.combine_sensitivity` (ADR-0003, ADR-0009). No sibling concept and
    no non-Source target's derived concept is ever read for propagation or
    written.

    Vocabulary validation happens FIRST, before any read of the concept
    file or the workspace: `level` must exact-match one of
    `okf.SENSITIVITY_ORDER`. `config.require_workspace` and
    `config.read_config` run next, then `concept_id` is resolved via the
    same `_resolve_concept_path` `forget`/`relate` use -- rejecting an
    absolute id, any `..` segment, a reserved basename, or a nonexistent
    concept file, all as `ValueError`, all before any write.

    Idempotence is checked by EXACT equality against the raw, unstripped
    current `sensitivity` value: if it already equals `level`, this is a
    no-op -- a message is printed, exit 0, no write, no commit. A dirty
    value (missing, blank, or unrecognized) never short-circuits here, so
    it always reaches `okf.sensitivity_direction`'s fail-closed ranking.

    The downgrade gate runs next, BEFORE the preview: `okf
    .sensitivity_direction(current, level) == "lower"` is permitted
    whenever the confirm prompt will actually run (interactive TTY,
    `--auto` not passed, and config `review` not `false`). On every path
    where the prompt does NOT run -- `--auto`, or workspace config
    `review: false`, which silences the prompt for every verb -- a
    lowering additionally requires `--allow-downgrade`; without it this
    refuses in Phase A (exit 1, no write, no commit, no preview), naming
    the required flag on stderr (ADR-0008).

    The preview line shows the concept file, the direction (raising/
    lowering/normalizing), the raw current value (`!r`), and the new
    level. The confirm gate mirrors `relate`'s exact precedence: `--auto`
    skips it; otherwise config `review: false` skips it; otherwise a TTY
    prompts via `typer.confirm` and aborts on decline; otherwise
    (non-TTY, no `--auto`) this refuses to write.

    When `<concept-id>` resolves to a Source-typed concept (`metadata.get
    ("type") == "Source"`) AND the assignment itself raises (`direction ==
    "raise"`), Phase A additionally reads a whole-bundle snapshot, resolves
    its provenance descendants, and computes `okf.combine_sensitivity
    (descendant_current, level)` per descendant -- staging a write only
    when that is a strict raise over the descendant's current value. Every
    staged raise appears in the preview and the success message. A
    provenance reference that resolves to no file in the snapshot emits a
    stderr WARNING naming it and is excluded -- fail-closed, never lowered,
    never blocking the Source's own write.

    That WARNING is SCOPED to the invoked Source, not to the bundle (issue
    #232): only a concept reachable from `<concept-id>` through provenance
    is reported. The snapshot it reads stays whole-bundle -- an id existing
    anywhere in the bundle is resolvable, and narrowing the snapshot would
    invent warnings -- so only the reporting scope narrows. Without that
    scope, every OTHER Source's own raw `resource` entry (never a bundle
    id) produced a WARNING on every run. Reachability here is
    `bundle.provenance.provenance_reachable`'s non-empty-INTERSECTION
    relation, deliberately WIDER than the subset closure that gates the
    writes: a descendant citing both `<concept-id>` and a dangling id is
    excluded from that closure, and is precisely the case this WARNING
    exists for. A concept citing ONLY an unresolvable id is unreachable and
    therefore silent here -- and NOTHING in the toolchain reports that case
    today: `lint`'s dangling check scans `relations:` and body links only,
    never `provenance:`. `lint` is the INTENDED FUTURE owner of that
    bundle-wide detection; the work is tracked as issue #257 and is not part
    of this change.

    A non-Source target, or a Source assignment that is a lowering or a
    same-rank normalization, skips this scan entirely -- a downgrade must
    never cascade even when `combine_sensitivity` would compute a raise for
    some individual descendant sitting below the new (lower) level.

    A confirmed write re-renders the frontmatter (`okf.dump_frontmatter`,
    changing only `sensitivity`), appends a `log.md` entry (no
    `index.md` change -- editing an existing catalog entry, not a new
    one). Phase B writes in this order: every staged descendant raise,
    then the target concept, then `log.md` -- via `fsio.write_atomic` --
    then one `_autocommit` covering every changed path, with message
    `openkos: set-sensitivity <id> -> <level>`. There is no cross-file
    rollback (matching `relate`/`merge`): a mid-way failure leaves the
    bundle over-classified, never under-classified. Any failure, Phase A or
    Phase B, is caught (`OSError`/`ValueError`) and reported on stderr
    (exit 1), never a raw traceback.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    try:
        if level not in okf.SENSITIVITY_ORDER:
            raise ValueError(
                f"{level!r} is not a valid sensitivity level (expected one "
                f"of {sorted(okf.SENSITIVITY_ORDER)})"
            )

        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise ValueError(workspace_reason)
        cfg = config.read_config(root)

        concept_path, canonical_id = _resolve_concept_path(
            layout.bundle_dir, concept_id
        )
        concept_text = concept_path.read_text(encoding="utf-8")
        metadata, body = okf.load_frontmatter(concept_text)
        current = metadata.get("sensitivity")
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos set-sensitivity: refusing to set -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    if current == level:
        typer.echo(
            f"openkos set-sensitivity: {canonical_id!r} already has "
            f"sensitivity {level!r}; no change made."
        )
        return

    direction = okf.sensitivity_direction(current, level)
    # ADR-0008: lowering rides on the confirm prompt as its whole friction
    # budget, so the gate must key on whether a human is ACTUALLY asked --
    # not merely on whether review is enabled. `confirm_enabled` answers
    # "is review on for this run"; `prompt_will_run` additionally requires
    # an interactive stdin. Dropping the TTY term here would let a piped
    # `review: true` run skip the gate, print the preview, and then refuse
    # via the Phase-B ladder naming `--auto` -- a remedy that still
    # refuses. Both gates below read these two names; never re-spell either
    # predicate inline, or the security rule acquires a second copy that
    # can drift.
    confirm_enabled = not auto and cfg.review
    prompt_will_run = confirm_enabled and sys.stdin.isatty()

    if direction == "lower" and not prompt_will_run and not allow_downgrade:
        typer.echo(
            "openkos set-sensitivity: refusing to lower "
            f"{canonical_id} from {current!r} to {level} without "
            "confirmation -- no confirm prompt will run (--auto, config "
            "review: false, or a non-interactive stdin); re-run with "
            "--allow-downgrade.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Raise-only propagation to provenance descendants (design: "Set-time
    # propagation"; ADR-0009). Source detection is the OKF `type` field of
    # record, never a path convention -- a non-Source target skips this
    # whole-bundle scan entirely and behaves byte-identically to today.
    # Propagation ALSO requires the Source's own assignment to be a raise
    # (`direction`, computed above) so a downgrade never cascades, even
    # when `combine_sensitivity` would compute a raise for some individual
    # descendant below the new (lower) level.
    descendant_raises: list[okf.DescendantRaise] = []
    if metadata.get("type") == "Source" and direction == "raise":
        try:
            bundle_snapshot: dict[str, str] = {}
            for path in sorted(layout.bundle_dir.rglob("*.md")):
                if path.name in okf.RESERVED_FILENAMES:
                    continue
                if path == concept_path:
                    continue
                rel = path.relative_to(layout.bundle_dir).as_posix()
                bundle_snapshot[rel] = path.read_text(encoding="utf-8")

            # Unresolvable provenance (design: "Unresolvable provenance"):
            # `known_extra_ids={canonical_id}` paired with the
            # target-excluding `bundle_snapshot` above reproduces the exact
            # historical pairing (design D7) that keeps the target's own
            # `provenance` from ever being warned about. Each unresolvable
            # entry is reported on stderr; the citing concept is
            # fail-closed excluded -- `resolve_source_raises`'s own
            # non-empty-subset rule already keeps it out of the raises
            # below, so this is purely reporting, never an extra write
            # gate.
            #
            # `root_ids={canonical_id}` narrows that REPORTING to what is
            # reachable from the invoked Source (issue #232); the snapshot
            # itself stays whole-bundle, because narrowing it would make an
            # id that exists elsewhere look unresolvable. Every OTHER Source
            # cites its own raw `resource` (`provenance=[resource]`, built
            # above at the `ingest` call site), which never normalizes to a
            # bundle id, so an unscoped scan emitted one bogus WARNING per
            # unrelated Source on every run. See
            # `find_unresolvable_provenance`'s docstring for why that scope
            # is a reachability relation rather than the subset closure
            # gating the writes.
            for member_id, entry_id in bundle_provenance.find_unresolvable_provenance(
                bundle_snapshot,
                known_extra_ids={canonical_id},
                root_ids={canonical_id},
            ):
                typer.echo(
                    "openkos set-sensitivity: WARNING -- "
                    f"{member_id!r} cites unresolvable provenance "
                    f"{entry_id!r}; excluded from propagation.",
                    err=True,
                )

            descendant_raises = bundle_provenance.resolve_source_raises(
                bundle_snapshot, source_id=canonical_id, level=level
            )
        except (OSError, ValueError) as exc:
            typer.echo(
                f"openkos set-sensitivity: failed while resolving the "
                f"descendant closure of {canonical_id!r} -- {exc}.",
                err=True,
            )
            raise typer.Exit(code=1) from exc

    now = datetime.now(UTC)

    try:
        log_text = log_path.read_text(encoding="utf-8")
        metadata["sensitivity"] = level
        new_concept_text = okf.dump_frontmatter(metadata, body)
        log_line = (
            f"**Set-sensitivity**: Set [{canonical_id}](/{canonical_id}.md) "
            f"sensitivity to {level!r} (was {current!r})."
        )
        new_log_text = bundle_log.insert_log_entry(
            log_text, now.astimezone().date(), log_line
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos set-sensitivity: failed while preparing the "
            f"set-sensitivity -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    direction_word = {
        "raise": "raising",
        "lower": "lowering",
        "same": "normalizing",
    }[direction]
    typer.echo("openkos set-sensitivity: proposed changes:")
    typer.echo(
        f"  ~ bundle/{canonical_id}.md (sensitivity: {direction_word} "
        f"{current!r} -> {level})"
    )
    for descendant_raise in descendant_raises:
        typer.echo(
            f"  ~ bundle/{descendant_raise.concept_id}.md (sensitivity: "
            f"raising {descendant_raise.current!r} -> "
            f"{descendant_raise.new_level})"
        )
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if confirm_enabled:
        if prompt_will_run:
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            # A lowering reaches here only when `--allow-downgrade` was
            # passed -- without it the Phase-A gate already refused, naming
            # that flag. So this refusal is about the WRITE lacking
            # confirmation, not about the downgrade lacking authorization,
            # and `--auto` is the correct remedy to name.
            typer.echo(
                "openkos set-sensitivity: refusing to write without "
                "confirmation -- stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    landed: list[str] = []
    try:
        # Write order: descendants BEFORE the target concept BEFORE
        # `log.md` (design: "Descendants are written BEFORE the target
        # concept"). A mid-way failure then leaves the bundle
        # over-classified, never under-classified -- there is no
        # cross-file rollback, matching `relate`/`merge`. `landed` records
        # each path only AFTER its `write_atomic` call returns, so a
        # failure names exactly the paths already on disk (design D9,
        # issue #233).
        for descendant_raise in descendant_raises:
            descendant_path = f"bundle/{descendant_raise.concept_id}.md"
            fsio.write_atomic(
                layout.bundle_dir / f"{descendant_raise.concept_id}.md",
                descendant_raise.content,
            )
            landed.append(descendant_path)
        fsio.write_atomic(concept_path, new_concept_text)
        landed.append(f"bundle/{canonical_id}.md")
        fsio.write_atomic(log_path, new_log_text)
        landed.append("bundle/log.md")
    except (OSError, ValueError) as exc:
        # Distinct from the two phases above on purpose: this one is
        # reached only after the write phase began, so the concept file may
        # already be on disk while `log.md` is not. "refusing" would tell an
        # operator nothing happened, which is exactly wrong here.
        landed_suffix = (
            f"Already written (left over-classified, not rolled back): "
            f"{', '.join(landed)}."
            if landed
            else "No path was written."
        )
        typer.echo(
            f"openkos set-sensitivity: failed while writing the "
            f"set-sensitivity -- {exc}. {landed_suffix}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if descendant_raises:
        propagated = ", ".join(
            f"'bundle/{descendant_raise.concept_id}.md' -> {descendant_raise.new_level}"
            for descendant_raise in descendant_raises
        )
        typer.echo(
            f"openkos set-sensitivity: set 'bundle/{canonical_id}.md' "
            f"sensitivity to {level} ({log_path.name} updated). Also raised "
            f"{len(descendant_raises)} provenance descendant(s): "
            f"{propagated}."
        )
    else:
        typer.echo(
            f"openkos set-sensitivity: set 'bundle/{canonical_id}.md' "
            f"sensitivity to {level} ({log_path.name} updated). Only this "
            "concept was changed; no sibling or derived object was touched."
        )

    _autocommit(
        root,
        [
            f"bundle/{descendant_raise.concept_id}.md"
            for descendant_raise in descendant_raises
        ]
        + [f"bundle/{canonical_id}.md", "bundle/log.md"],
        f"openkos: set-sensitivity {canonical_id} -> {level}",
    )


@app.command("backfill-sensitivity")
def backfill_sensitivity_cmd(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Dedicated, raise-only, bundle-wide sweep that closes the sensitivity
    gap left by bundles or descendants created before Source-to-descendant
    propagation existed (issue #219/#231). Wires the pure
    `bundle.provenance.resolve_backfill_raises` sweep core (design D4/D5)
    into Typer's confirm-gate and write scaffold, mirroring
    `set_sensitivity_cmd`'s Phase A/Phase B shape exactly.

    Unlike `set-sensitivity`, this command takes no `<concept-id>`
    argument -- it treats every `type: Source` concept in the bundle as an
    independent closure root in a single pass. It is bundle-wide only;
    `set-sensitivity` already covers the single-Source case. There is no
    `--allow-downgrade` equivalent: the sweep is raise-only by construction
    and never lowers a descendant. There is no `--dry-run` flag either --
    the preview shown before confirmation, or declining the prompt, already
    serves as the dry run (spec Non-Goals).

    Phase A: `require_workspace` -> `read_config` -> one `sorted(rglob)`
    bundle snapshot (reserved filenames skipped) -> `resolve_backfill_raises`
    computes every merged-by-max raise across every Source (design D4/D5).
    When the result is empty, this prints an explicit "nothing to
    backfill" message, writes nothing, creates no commit, and exits 0 --
    idempotent by construction, since a second run over an already-swept
    bundle recomputes zero raises. Otherwise, one preview lists every
    staged `(concept_id, current -> new_level)` raise (sorted by
    `concept_id`, matching `resolve_backfill_raises`'s own order), then the
    confirm gate mirrors `set_sensitivity_cmd`'s exact precedence: `--auto`
    skips it; otherwise config `review: false` skips it; otherwise a TTY
    prompts via `typer.confirm` and aborts on decline; otherwise (non-TTY,
    no `--auto`) this refuses to write.

    Deliberately does NOT call `find_unresolvable_provenance` (design D8):
    every Source cites its raw `resource`, which never resolves to a bundle
    id, so a bundle-wide run would emit one WARNING per Source on every
    invocation, including the no-op path above. That signal is delivered by
    `lint`'s existing `dangling` finding, never this sweep.

    Phase B writes every merged raise (sorted by `concept_id`), then
    appends exactly one dated `log.md` entry summarizing the whole sweep,
    then issues exactly one `_autocommit` covering every changed path.
    There is no cross-file rollback (matching `set-sensitivity`/`relate`/
    `merge`): a mid-way failure leaves the bundle over-classified, never
    under-classified, and the failure message names every path already
    written before the failure (design D9, mirrors the #233 fix). Any
    failure, Phase A or Phase B, is caught (`OSError`/`ValueError`) and
    reported on stderr (exit 1), never a raw traceback.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise ValueError(workspace_reason)
        cfg = config.read_config(root)

        bundle_snapshot: dict[str, str] = {}
        for path in sorted(layout.bundle_dir.rglob("*.md")):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            bundle_snapshot[rel] = path.read_text(encoding="utf-8")

        descendant_raises = bundle_provenance.resolve_backfill_raises(bundle_snapshot)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos backfill-sensitivity: refusing to run -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    if not descendant_raises:
        typer.echo(
            "openkos backfill-sensitivity: nothing to backfill -- every "
            "provenance descendant already meets or exceeds its Source's "
            "sensitivity."
        )
        return

    confirm_enabled = not auto and cfg.review
    prompt_will_run = confirm_enabled and sys.stdin.isatty()

    now = datetime.now(UTC)
    try:
        log_text = log_path.read_text(encoding="utf-8")
        propagated = ", ".join(
            f"'bundle/{descendant_raise.concept_id}.md' -> {descendant_raise.new_level}"
            for descendant_raise in descendant_raises
        )
        log_line = (
            f"**Backfill-sensitivity**: Raised {len(descendant_raises)} "
            f"provenance descendant(s) to match their Source's sensitivity: "
            f"{propagated}."
        )
        new_log_text = bundle_log.insert_log_entry(
            log_text, now.astimezone().date(), log_line
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos backfill-sensitivity: failed while preparing the "
            f"backfill-sensitivity -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos backfill-sensitivity: proposed changes:")
    for descendant_raise in descendant_raises:
        typer.echo(
            f"  ~ bundle/{descendant_raise.concept_id}.md (sensitivity: "
            f"raising {descendant_raise.current!r} -> "
            f"{descendant_raise.new_level})"
        )
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if confirm_enabled:
        if prompt_will_run:
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos backfill-sensitivity: refusing to write without "
                "confirmation -- stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    landed: list[str] = []
    try:
        # Write order: every staged descendant raise, then `log.md`
        # (design D4 Phase B). `landed` records each path only AFTER its
        # `write_atomic` call returns, so a failure names exactly the
        # paths already on disk (design D9, mirrors #233).
        for descendant_raise in descendant_raises:
            descendant_path = f"bundle/{descendant_raise.concept_id}.md"
            fsio.write_atomic(
                layout.bundle_dir / f"{descendant_raise.concept_id}.md",
                descendant_raise.content,
            )
            landed.append(descendant_path)
        fsio.write_atomic(log_path, new_log_text)
        landed.append("bundle/log.md")
    except (OSError, ValueError) as exc:
        landed_suffix = (
            f"Already written (left over-classified, not rolled back): "
            f"{', '.join(landed)}."
            if landed
            else "No path was written."
        )
        typer.echo(
            f"openkos backfill-sensitivity: failed while writing the "
            f"backfill-sensitivity -- {exc}. {landed_suffix}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    propagated = ", ".join(
        f"'bundle/{descendant_raise.concept_id}.md' -> {descendant_raise.new_level}"
        for descendant_raise in descendant_raises
    )
    typer.echo(
        f"openkos backfill-sensitivity: raised {len(descendant_raises)} "
        f"provenance descendant(s) ({log_path.name} updated): {propagated}."
    )

    _autocommit(
        root,
        [
            f"bundle/{descendant_raise.concept_id}.md"
            for descendant_raise in descendant_raises
        ]
        + ["bundle/log.md"],
        "openkos: backfill-sensitivity",
    )


@app.command("backfill-source-titles")
def backfill_source_titles_cmd(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Bundle-wide sweep that re-derives each `type: Source` concept's
    `title` from its immutable `raw/` bytes (design D2/D3), mirroring
    `backfill_sensitivity_cmd`'s Phase A/Phase B shape. No `<concept-id>`
    argument -- bundle-wide only.

    Phase A: snapshot -> `scan_source_titles` (candidates/skipped/warned
    from frontmatter alone) -> read `raw/<name>` per candidate into
    `raw_texts` (an absent key means unreadable, an explicit `None` means
    undecodable -- `UnicodeDecodeError` is caught before the outer
    `except (OSError, ValueError)`, `ingest`'s ordering, since it
    subclasses `ValueError`) -> `resolve_source_title_backfill`.

    Empty `staged` short-circuits: "nothing was staged", no write, no
    commit, exit 0. Otherwise a THREE-bucket preview (staged/skipped/
    warned, unlike `backfill-sensitivity`'s stage-or-nothing preview) is
    printed, then the confirm gate mirrors `backfill_sensitivity_cmd`'s
    precedence exactly: `--auto` / `review: false` / TTY `typer.confirm` /
    non-TTY refuse.

    Phase B writes `index.md` first, then each staged Source, then `log.md`,
    then one `_autocommit` (design D6); both write-bound texts are computed
    before the preview so a malformed `index.md` refuses before any write.
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise ValueError(workspace_reason)
        cfg = config.read_config(root)

        bundle_snapshot: dict[str, str] = {}
        for path in sorted(layout.bundle_dir.rglob("*.md")):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            bundle_snapshot[rel] = path.read_text(encoding="utf-8")

        scan = source_titles.scan_source_titles(bundle_snapshot)

        raw_texts: dict[str, str | None] = {}
        for candidate in scan.candidates:
            raw_path = layout.raw_dir / PurePosixPath(candidate.resource).name
            try:
                raw_texts[candidate.resource] = raw_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Subclasses `ValueError`: caught before `except (OSError,
                # ValueError)` below, or one binary raw file fails the sweep.
                raw_texts[candidate.resource] = None
            except OSError:
                pass  # absent key -> `raw-unreadable` (design D2)

        backfill = source_titles.resolve_source_title_backfill(scan, raw_texts)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos backfill-source-titles: refusing to run -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    if not backfill.staged:
        typer.echo(
            "openkos backfill-source-titles: nothing was staged -- no Source "
            "has a mechanical title with a differing re-derivation."
        )
        return

    # Both write-bound texts are computed HERE, before the preview, so a
    # malformed `index.md` refuses before any write rather than halfway
    # through Phase B (design D6).
    try:
        new_index_text = index_path.read_text(encoding="utf-8")
        for retitle in backfill.staged:
            new_index_text, _ = bundle_index.relabel_index_entry(
                new_index_text, retitle.concept_id, retitle.new_title
            )

        retitled = ", ".join(
            f"'bundle/{retitle.concept_id}.md' -> {retitle.new_title!r}"
            for retitle in backfill.staged
        )
        log_line = (
            f"**Backfill-source-titles**: Re-derived {len(backfill.staged)} "
            f"Source title(s) from their raw content: {retitled}."
        )
        new_log_text = bundle_log.insert_log_entry(
            log_path.read_text(encoding="utf-8"),
            datetime.now(UTC).astimezone().date(),
            log_line,
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos backfill-source-titles: failed while preparing the "
            f"backfill-source-titles -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos backfill-source-titles: proposed changes:")
    for retitle in backfill.staged:
        typer.echo(
            f"  ~ bundle/{retitle.concept_id}.md (title: "
            f"{retitle.current_title!r} -> {retitle.new_title!r})"
        )
    for skipped in backfill.skipped:
        typer.echo(f"  = bundle/{skipped.concept_id}.md (skipped: {skipped.reason})")
    for warned in backfill.warned:
        typer.echo(f"  ! bundle/{warned.concept_id}.md (warned: {warned.reason})")

    confirm_enabled = not auto and cfg.review
    prompt_will_run = confirm_enabled and sys.stdin.isatty()

    if confirm_enabled:
        if prompt_will_run:
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos backfill-source-titles: refusing to write without "
                "confirmation -- stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    landed: list[str] = []
    try:
        # `index.md` FIRST, then each staged Source, then `log.md` --
        # the OPPOSITE of `backfill-sensitivity`'s items-then-aggregate
        # order (design D6). The classifier keys on a Source document's own
        # `title`; once a document is written, a mid-sweep failure before
        # `index.md` lands would leave its bullet unrevisitable on re-run.
        # Index-first is the only order a re-run repairs -- do NOT "fix"
        # this to match `backfill-sensitivity`.
        fsio.write_atomic(index_path, new_index_text)
        landed.append("bundle/index.md")
        for retitle in backfill.staged:
            source_path = f"bundle/{retitle.concept_id}.md"
            fsio.write_atomic(
                layout.bundle_dir / f"{retitle.concept_id}.md", retitle.content
            )
            landed.append(source_path)
        fsio.write_atomic(log_path, new_log_text)
        landed.append("bundle/log.md")
    except (OSError, ValueError) as exc:
        landed_suffix = (
            f"Already written (left partially retitled, not rolled back): "
            f"{', '.join(landed)}."
            if landed
            else "No path was written."
        )
        typer.echo(
            f"openkos backfill-source-titles: failed while writing the "
            f"backfill -- {exc}. {landed_suffix}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos backfill-source-titles: retitled {len(backfill.staged)} "
        f"Source(s) ({index_path.name}, {log_path.name} updated): {retitled}."
    )

    _autocommit(root, landed, "openkos: backfill-source-titles")


@app.command("set-volatility")
def set_volatility_cmd(
    concept_type: str = typer.Argument(
        ..., help="Exact PascalCase REGISTRY type name, e.g. 'Person'."
    ),
    tier: str = typer.Argument(
        ..., help="Volatility tier: one of 'static', 'slow', 'volatile'."
    ),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Write `type_tiers[<ConceptType>] = <tier>` into `openkos.yaml` --
    the write half of `suggest-volatility`'s read-only recommendation
    (freshness-suggest-windows, write-verb #140).

    Vocabulary validation happens FIRST, before any read or write:
    `tier` must exact-match one of `types.VOLATILITY_TIERS`; `concept_type`
    must exact-match, case-sensitive, one of the 10 PascalCase `REGISTRY`
    type names (including `Source`, since `suggest-volatility` can suggest a
    tier for it even though it is not LLM-classifiable). Either failure
    refuses with a clear stderr message and non-zero exit, with zero
    read/write of the workspace.

    The shared `config.require_workspace` gate runs next, then
    `config.read_config` -- both `except (OSError, ValueError)`, matching
    every other write verb's convention. Idempotence is then checked against
    the PARSED `type_tiers` map (design: "Idempotence detected in CLI via
    parsed map, not the core"): if `concept_type` already maps to `tier`
    there, this is a no-op -- a message is printed, exit 0, and NEITHER
    `config.set_type_tier` NOR any write/commit happens. An explicit
    override equal to the type's REGISTRY default is NOT idempotent (it is
    not present in the parsed map), so it still proceeds as a real write.

    `openkos.yaml`'s raw text is read and passed to the pure
    `config.set_type_tier` text-surgery core (comment-safe, no YAML
    round-trip). Any un-editable existing shape (inline flow-mapping,
    multiple headers, non-mapping scalar, tab-indented block, inconsistent
    indent, duplicate entry) makes that core raise `ValueError`, caught here
    and reported as a refusal on stderr, exit 1, `openkos.yaml` left
    byte-identical -- the file is never touched on this path.

    A preview line `<ConceptType>: <old-or-default> -> <new>` is printed
    before the same confirm gate every other mutating verb shares (`--auto`
    skips it; otherwise config `review: false` skips it the same way;
    otherwise a TTY prompts via `typer.confirm` and aborts on decline;
    otherwise, non-TTY with no `--auto`, this refuses to write). Declining
    or refusing leaves `openkos.yaml` untouched.

    A confirmed write goes through `fsio.write_atomic`, then
    `_autocommit(root, ["openkos.yaml"], ...)` with message `openkos:
    set-volatility <ConceptType> -> <tier>`, mirroring every other mutating
    verb's commit-message convention (`openkos: <verb> ...`).
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)

    valid_types = {ot.name for ot in types.REGISTRY}
    if tier not in types.VOLATILITY_TIERS:
        typer.echo(
            f"openkos set-volatility: refusing to set -- {tier!r} is not a "
            f"valid tier (expected one of {sorted(types.VOLATILITY_TIERS)}).",
            err=True,
        )
        raise typer.Exit(code=1)
    if concept_type not in valid_types:
        typer.echo(
            f"openkos set-volatility: refusing to set -- {concept_type!r} is "
            f"not a known concept type (expected one of {sorted(valid_types)}).",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            typer.echo(
                f"openkos set-volatility: refusing to set -- {workspace_reason}.",
                err=True,
            )
            raise typer.Exit(code=1)
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos set-volatility: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    if cfg.type_tiers.get(concept_type) == tier:
        typer.echo(
            f"openkos set-volatility: {concept_type!r} already maps to "
            f"{tier!r}; no change made."
        )
        return

    old_tier = cfg.type_tiers.get(
        concept_type, types.TYPE_TO_DEFAULT_VOLATILITY[concept_type]
    )

    try:
        config_text = layout.config_path.read_text(encoding="utf-8")
        new_config_text = config.set_type_tier(config_text, concept_type, tier)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos set-volatility: refusing to set -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("openkos set-volatility: proposed changes:")
    typer.echo(f"  {concept_type}: {old_tier} -> {tier}")

    if not auto and cfg.review:
        if sys.stdin.isatty():
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos set-volatility: refusing to write without "
                "confirmation -- stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        fsio.write_atomic(layout.config_path, new_config_text)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos set-volatility: failed while writing -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos set-volatility: set {concept_type} -> {tier} in "
        f"{layout.config_path.name}."
    )

    _autocommit(
        root,
        ["openkos.yaml"],
        f"openkos: set-volatility {concept_type} -> {tier}",
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


@dataclass(frozen=True)
class PreparedMerge:
    """Pure Phase-A result of `prepare_merge`: everything `merge`'s preview,
    confirm gate, and `merge_core` need, built in memory without writing
    anything (design: merge-core Extraction, Slice 2b-i). `review` carries
    `cfg.review`, consumed only by the command's confirm gate -- `prepare_merge`
    itself never prompts."""

    survivor_canonical: str
    absorbed_canonical: str
    plan: "bundle_merge.MergePlan"
    new_index_text: str
    new_log_text: str
    other_files: dict[str, str]
    link_rewrites: list[okf.LinkRewrite]
    relation_rewrites: list[okf.RelationRewrite]
    provenance_rewrites: list[okf.ProvenanceRewrite]
    rewritten_files: list[str]
    relation_rewritten_files: list[str]
    provenance_rewritten_files: list[str]
    touched_files: list[str]
    removed: int
    dropped_self_loops: list[okf.Relation]
    deduped_collisions: list[okf.Relation]
    sensitivity_before: str
    sensitivity_after: str
    review: bool
    now: datetime


@dataclass(frozen=True)
class MergeResult:
    """Pure Phase-B result of `merge_core`: what got written, for the
    command's success echo and `_autocommit` path list. `merge_core` itself
    performs NO VCS side effect (design decision: `_autocommit` stays in the
    command)."""

    survivor_canonical: str
    absorbed_canonical: str
    touched_files: list[str]
    committed_paths: list[str]


def prepare_merge(
    bundle_dir: Path,
    index_path: Path,
    log_path: Path,
    survivor_path: Path,
    absorbed_path: Path,
    survivor_canonical: str,
    absorbed_canonical: str,
    root: Path,
    *,
    now: datetime,
) -> PreparedMerge:
    """Phase A (pure, no writes): read config + the four texts, scan for
    inbound link/relation rewrites, plan the merge, and recompute the
    preview data -- extracted verbatim from `merge`'s former inline body
    (`main.py:2453-2519`, design: merge-core Extraction, Slice 2b-i).
    Non-interactive; raises `OSError`/`ValueError` on bad input. Writes
    nothing to disk."""
    cfg = config.read_config(root)
    survivor_text = survivor_path.read_text(encoding="utf-8")
    absorbed_text = absorbed_path.read_text(encoding="utf-8")
    index_text = index_path.read_text(encoding="utf-8")
    log_text = log_path.read_text(encoding="utf-8")

    other_files: dict[str, str] = {}
    for path in sorted(bundle_dir.rglob("*.md")):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        if path in (survivor_path, absorbed_path):
            continue
        rel = path.relative_to(bundle_dir).as_posix()
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
    # Same `other_files` whole-bundle snapshot, captured ONCE above BEFORE
    # any write -- all three scans see identical pre-merge bytes (design:
    # rewrite-provenance-on-merge, "no additional bundle walk"). NOT gated
    # on the absorbed concept's `type` (spec: "A merge absorbing a
    # NON-Source concept also retargets third-party provenance").
    provenance_rewrites = bundle_provenance.find_inbound_provenance_rewrites(
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
        provenance_rewrites=provenance_rewrites,
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

    rewritten_files = sorted({rewrite.file for rewrite in link_rewrites})
    relation_rewritten_files = sorted({rewrite.file for rewrite in relation_rewrites})
    provenance_rewritten_files = sorted(
        {rewrite.file for rewrite in provenance_rewrites}
    )
    touched_files = sorted(
        set(rewritten_files)
        | set(relation_rewritten_files)
        | set(provenance_rewritten_files)
    )
    sensitivity_before = plan.ledger_entry.sensitivity_before or "(none)"
    sensitivity_after = plan.ledger_entry.sensitivity_after

    return PreparedMerge(
        survivor_canonical=survivor_canonical,
        absorbed_canonical=absorbed_canonical,
        plan=plan,
        new_index_text=new_index_text,
        new_log_text=new_log_text,
        other_files=other_files,
        link_rewrites=link_rewrites,
        relation_rewrites=relation_rewrites,
        provenance_rewrites=provenance_rewrites,
        rewritten_files=rewritten_files,
        relation_rewritten_files=relation_rewritten_files,
        provenance_rewritten_files=provenance_rewritten_files,
        touched_files=touched_files,
        removed=removed,
        dropped_self_loops=dropped_self_loops,
        deduped_collisions=deduped_collisions,
        sensitivity_before=sensitivity_before,
        sensitivity_after=sensitivity_after,
        review=cfg.review,
        now=now,
    )


def merge_core(
    bundle_dir: Path,
    index_path: Path,
    log_path: Path,
    prepared: PreparedMerge,
) -> MergeResult:
    """Phase B (after confirm): ordered writes -- `index.md` then `log.md`,
    every touched file's rewrite, the merged survivor (carrying the
    `merged_from` ledger) LAST among writes, then removes the absorbed file
    -- extracted verbatim from `merge`'s former inline body
    (`main.py:2559-2596`, design: merge-core Extraction, Slice 2b-i).
    Non-interactive; raises `OSError`/`ValueError`. Performs NO VCS side
    effect -- `_autocommit` stays the command's responsibility."""
    fsio.write_atomic(index_path, prepared.new_index_text)
    fsio.write_atomic(log_path, prepared.new_log_text)

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
    survivor_canonical = prepared.survivor_canonical
    absorbed_canonical = prepared.absorbed_canonical
    rewritten_texts = {
        rel: bundle_provenance.apply_provenance_rewrites(
            bundle_relations.apply_relation_rewrites(
                _apply_link_rewrite_idempotently(
                    prepared.other_files[rel],
                    file=rel,
                    rewrites=prepared.link_rewrites,
                ),
                file=rel,
                survivor_id=survivor_canonical,
                absorbed_id=absorbed_canonical,
                rewrites=prepared.relation_rewrites,
            ),
            file=rel,
            survivor_id=survivor_canonical,
            absorbed_id=absorbed_canonical,
            rewrites=prepared.provenance_rewrites,
        )
        for rel in prepared.touched_files
    }
    for rel in prepared.touched_files:
        fsio.write_atomic(bundle_dir / rel, rewritten_texts[rel])

    # The merged survivor (with its `merged_from` ledger) is committed
    # LAST among the writes, only once every rewrite above has
    # succeeded -- see `merge`'s docstring for why that ordering is what
    # makes a mid-rewrite failure cleanly retryable.
    survivor_path = bundle_dir / f"{survivor_canonical}.md"
    absorbed_path = bundle_dir / f"{absorbed_canonical}.md"
    fsio.write_atomic(survivor_path, prepared.plan.merged_survivor)
    fsio.remove_file(absorbed_path)

    return MergeResult(
        survivor_canonical=survivor_canonical,
        absorbed_canonical=absorbed_canonical,
        touched_files=prepared.touched_files,
        committed_paths=[
            "index.md",
            "log.md",
            *(f"bundle/{rel}" for rel in prepared.touched_files),
            f"bundle/{survivor_canonical}.md",
            f"bundle/{absorbed_canonical}.md",
        ],
    )


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
        prepared = prepare_merge(
            layout.bundle_dir,
            index_path,
            log_path,
            survivor_path,
            absorbed_path,
            survivor_canonical,
            absorbed_canonical,
            root,
            now=now,
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos merge: failed while preparing the merge -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos merge: proposed changes:")
    typer.echo(
        f"  ~ sensitivity: {prepared.sensitivity_before} -> {prepared.sensitivity_after}"
    )
    for relation in prepared.dropped_self_loops:
        typer.echo(f"  - drop self-loop: {relation.target} ({relation.type})")
    for relation in prepared.deduped_collisions:
        typer.echo(f"  ~ dedupe collision: {relation.target} ({relation.type})")
    for rel in prepared.rewritten_files:
        typer.echo(f"  ~ bundle/{rel} (rewrite inbound link(s) to survivor)")
    for rel in prepared.relation_rewritten_files:
        typer.echo(f"  ~ bundle/{rel} (retarget relation to survivor)")
    for rel in prepared.provenance_rewritten_files:
        typer.echo(f"  ~ bundle/{rel} (retarget provenance to survivor)")
    if prepared.removed >= 1:
        typer.echo(f"  ~ {index_path.name} (remove entry)")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")
    typer.echo(f"  ~ bundle/{survivor_canonical}.md (merged content)")
    typer.echo(f"  - bundle/{absorbed_canonical}.md")

    if not auto and prepared.review:
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
        result = merge_core(layout.bundle_dir, index_path, log_path, prepared)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos merge: failed while writing the merge -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos merge: merged 'bundle/{absorbed_canonical}.md' into "
        f"'bundle/{survivor_canonical}.md' "
        f"({index_path.name}, {log_path.name} updated)."
    )

    _autocommit(
        root,
        [
            "bundle/index.md",
            "bundle/log.md",
            *(f"bundle/{rel}" for rel in result.touched_files),
            f"bundle/{survivor_canonical}.md",
            f"bundle/{absorbed_canonical}.md",
        ],
        f"openkos: merge {absorbed_canonical} into {survivor_canonical}",
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

        # Precedence, generalized to three rewrite kinds (provenance >
        # relations > links): a file present in `provenance_rewrites` is
        # reversed EXCLUSIVELY via its provenance whole-file snapshot below
        # -- excluded from BOTH the relation and link partitions. D5's
        # original two-way rule still holds for the remaining files: a file
        # present in BOTH `link_rewrites` and `relation_rewrites` (and NOT
        # in `provenance_rewrites`) is reversed EXCLUSIVELY via its
        # `relation_rewrites` whole-file snapshot -- excluded here so
        # `reverse_link_rewrites` is never attempted on it (see this
        # command's docstring).
        provenance_rewrite_files = sorted(
            {rewrite.file for rewrite in plan.provenance_rewrites}
        )
        relation_rewrite_files = sorted(
            {rewrite.file for rewrite in plan.relation_rewrites}
            - set(provenance_rewrite_files)
        )
        rewritten_files = sorted(
            {rewrite.file for rewrite in plan.link_rewrites}
            - set(provenance_rewrite_files)
            - set(relation_rewrite_files)
        )
        provenance_texts = {
            rel: (layout.bundle_dir / rel).read_text(encoding="utf-8")
            for rel in provenance_rewrite_files
        }
        provenance_reversed_texts = {
            rel: bundle_provenance.reverse_provenance_rewrites(
                provenance_texts[rel],
                file=rel,
                survivor_id=survivor_canonical,
                absorbed_id=absorbed_canonical,
                rewrites=plan.provenance_rewrites,
                link_rewrites=plan.link_rewrites,
                relation_rewrites=plan.relation_rewrites,
            )
            for rel in provenance_rewrite_files
        }
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
    for rel in provenance_rewrite_files:
        typer.echo(f"  ~ bundle/{rel} (restore pre-merge provenance snapshot)")
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
        for rel in provenance_rewrite_files:
            fsio.write_atomic(layout.bundle_dir / rel, provenance_reversed_texts[rel])

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

    _autocommit(
        root,
        [
            "bundle/index.md",
            "bundle/log.md",
            *(f"bundle/{rel}" for rel in rewritten_files),
            *(f"bundle/{rel}" for rel in relation_rewrite_files),
            *(f"bundle/{rel}" for rel in provenance_rewrite_files),
            f"bundle/{absorbed_canonical}.md",
            f"bundle/{survivor_canonical}.md",
        ],
        f"openkos: unmerge {absorbed_canonical}",
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

    reconcile_message = (
        f"openkos: reconcile {canonical_a} <-> {canonical_b}"
        if winner_canonical is None
        else f"openkos: reconcile {winner_canonical} supersedes {loser_canonical}"
    )
    _autocommit(
        root,
        [f"bundle/{canonical_a}.md", f"bundle/{canonical_b}.md", "bundle/log.md"],
        reconcile_message,
    )


RECENT_ACTIVITY_LIMIT = 5
"""How many `log.md` bullets `status` shows under "Recent activity" (D4).

Display policy, not parsing policy -- `bundle/log.py::read_recent_entries`
stays free of this constant and takes it as a parameter instead."""


def _bundle_content_lines(survey: okf.BundleSurvey) -> list[tuple[str, int]]:
    """Build `status`'s per-type "Bundle contents" rows from a survey (#133).

    `Sources` first, then `Concepts` ALWAYS (even at 0, preserving the
    familiar summary), then every OTHER classifiable type that is actually
    present, in canonical `_TYPE_TO_SECTION` order using its plural section
    label -- so a Procedure, Decision, etc. gets its own line instead of
    being folded into "Concepts". Any non-Source raw `type` outside the
    classifiable vocabulary (e.g. a malformed lowercase `concept`) is
    surfaced last, sorted, labelled by its raw string, so nothing is hidden.
    """
    by_type = survey.by_type
    lines: list[tuple[str, int]] = [("Sources", survey.sources)]
    for type_name, section in _TYPE_TO_SECTION.items():
        count = by_type.get(type_name, 0)
        if type_name == "Concept" or count > 0:
            lines.append((section, count))
    known = set(_TYPE_TO_SECTION) | {"Source"}
    for type_name in sorted(by_type):
        if type_name not in known:
            lines.append((type_name, by_type[type_name]))
    return lines


@app.command()
def status() -> None:
    """Report what the bundle currently contains: read-only, Phase-A only.

    Refuses (exit 1) via the shared `config.require_workspace` gate (D1) if
    the current directory is not an initialized workspace -- the SAME check
    `ingest` uses -- printing the reason to stderr with no raw traceback.
    This is the ONLY non-zero exit path.

    On a workspace, sequences several reads and renders their result as
    plain text via `typer.echo`, always exiting 0. Note that these reads
    perform FIVE independent `bundle/**/*.md` walks, not one:
    `okf.survey_bundle` (source/concept counts and §9 findings, D2) --
    counts always reflect the disk scan, never `index.md` alone, so catalog
    drift after an interrupted `ingest` is still visible;
    `lint_check.collect_docs` (dangling-reference AND unextracted-source
    findings, #141/#187 -- both reuse this SAME `docs` list, no extra walk);
    `resolution.find_exact_title_groups` (exact-title candidate groups,
    #186), run UNCONDITIONALLY -- unlike the edge-count line below, it is
    never gated on `vectors_missing`, because it never touches embeddings --
    which is TWO walks by itself, not one: `_iter_eligible`, plus
    `lifecycle.deprecated_concept_ids` under the default
    `include_deprecated=False`; and -- only when `vectors.db` is non-empty --
    `build_graph`'s walk behind the informational edge-count line.
    Consolidating the remaining walks has no open owner: #195 already
    landed, and what it guaranteed is that `status` calls `build_graph`
    exactly once; #216 landed too, and what it removed was the O(n^2)
    pairwise `near_match_score` pass this line paid for and discarded -- NOT
    either of the two walks above, which are unchanged.
    `log.md` is read and passed through `bundle_log.read_recent_entries` for
    the most recent `RECENT_ACTIVITY_LIMIT` entries, newest-first -- an
    unreadable or malformed `log.md` degrades to a notice (`except (OSError,
    ValueError)`) rather than failing the whole command (D5), because recent
    activity is the one nice-to-have `status` exists to show, not the
    counts or the conformance findings. `survey_bundle`'s findings
    (missing/unparseable frontmatter, unreadable files) are informational:
    their presence never changes the exit code (spec: Needs-Attention via §9
    Conformance).

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
    content_lines = _bundle_content_lines(survey)
    label_width = max(len(f"{label}:") for label, _ in content_lines)
    for label, count in content_lines:
        typer.echo(f"  {(label + ':').ljust(label_width)} {count}")
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
    # #141: dangling-reference findings are knowledge-health (lint)
    # vocabulary, not OKF conformance -- `survey_bundle` never computes
    # them, so `status` calls `lint`'s own `collect_docs` +
    # `check_dangling_targets` directly and folds the rendered lines in
    # here, alongside §9 conformance findings and the #142 vector-index
    # check below. Still read-only, still exits 0.
    docs, _skip_notices = lint_check.collect_docs(layout.bundle_dir)
    dangling = lint_check.check_dangling_targets(docs)
    # issue #187: `unextracted` reuses this SAME in-memory `docs` list --
    # no second `collect_docs()` call, no new walk (status spec: "No new
    # bundle walk is introduced").
    unextracted = lint_check.check_unextracted(docs)
    # issue #231 (PR2): reuses this SAME in-memory `docs` list too -- no
    # third `collect_docs()` call (design D3's no-fifth-walk guard).
    sensitivity_findings = lint_check.check_below_source_sensitivity(docs)
    needs_attention: list[str] = [*survey.findings]
    needs_attention.extend(
        f"{finding.concept_id}: {finding.detail}" for finding in dangling
    )
    needs_attention.extend(
        f"{finding.concept_id}: {finding.detail}" for finding in unextracted
    )
    needs_attention.extend(
        f"{finding.concept_id}: [{finding.kind}] {finding.detail}"
        for finding in sensitivity_findings
    )
    # #186: pending duplicate groups are ACTIONABLE -- name `duplicates` as
    # the next step. Exact-title matches only; near-match (LOW) is a
    # deliberate high-recall review queue, not an alert (similarity.py).
    # #216: hence `find_exact_title_groups`, not `find_candidates` -- it
    # returns the identical HIGH groups in the identical order, but skips the
    # O(n^2) pairwise `near_match_score` pass whose LOW groups this line
    # discarded. `duplicates`/`adjudicate` still call `find_candidates`:
    # they use both tiers.
    exact_title_groups = len(find_exact_title_groups(layout.bundle_dir))
    if exact_title_groups:
        needs_attention.append(
            f"{exact_title_groups} candidate group{_plural(exact_title_groups)} with "
            "identical titles — run `openkos duplicates` to review."
        )
    # issue #183 (Slice 0): a missing/empty `vectors.db` is genuinely
    # ACTIONABLE (spec: "Needs-Attention Surfaces Missing Vector Index"), so
    # it belongs in `needs_attention` itself -- unlike the edge-count line
    # below, which stays purely INFORMATIONAL.
    vectors_missing = vector_store_is_empty(layout.vectors_db_path)
    if vectors_missing:
        needs_attention.append(
            "Dense retrieval and candidate edges unavailable — run "
            "`openkos reindex` (vectors.db missing)."
        )
    if not needs_attention:
        typer.echo("  Nothing needs attention.")
    else:
        for line in needs_attention:
            typer.echo(f"  {line}")
    # The edge-count summary stays a separate, purely INFORMATIONAL line
    # (spec: "or an adjacent informational line") -- never appended to
    # `needs_attention`, so a healthy workspace still prints "Nothing needs
    # attention." above; `graph_edge_summary` is read-only over the graph
    # projection, so this never changes the exit code either. Skipped when
    # `vectors_missing`, since it already starves any embedding-sourced
    # candidate edge and is covered by the line above.
    if not vectors_missing:
        with build_graph(layout.bundle_dir) as store:
            total, typed = graph_edge_summary(layout.bundle_dir, store=store)
        if total == 0:
            typer.echo("  No concept relationships yet.")
        else:
            typer.echo(f"  {total} concept-to-concept edge(s) ({typed} typed).")


@app.command("next")
def next_cmd() -> None:
    """Print the one command worth running next: read-only, deterministic.

    Refuses (exit 1) via the SAME shared `config.require_workspace` gate
    (D1) `status` uses if the current directory is not an initialized
    workspace, printing the reason to stderr with no raw traceback. This is
    the ONLY non-zero exit path -- every other workspace state, including a
    freshly initialized, empty bundle, exits 0.

    Delegates the whole ranked decision to
    `openkos.cli.next_action.next_action` (the ordered `_TIERS` tuple over
    a lazily-memoized `_BundleSignals` holder) and echoes
    `next_action.render_lines`'s output verbatim. `status`'s body is not
    read or touched here (design D2): every signal `next` reads comes from
    a function `status`/`lint` already ship, so no walk logic is
    duplicated. Named `next_cmd` internally only because `next` shadows a
    Python builtin -- the command itself is registered as `next`.

    No file under the workspace is ever created, modified, or deleted, no
    model backend is ever constructed, and no `--json` or other structured
    output mode is offered (spec: Read-Only and Human-Readable Only, No
    Model Backend Constructed).
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos next: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    result = next_action_module.next_action(layout)
    for line in next_action_module.render_lines(result):
        typer.echo(line)


@app.command("list")
def list_objects_cmd(
    concept_type: str | None = typer.Argument(
        None,
        help=(
            "Optional type filter: a canonical link_dir (e.g. 'people') or a "
            "REGISTRY.name alias (e.g. 'Person', case-sensitive). Omit to "
            "list every object."
        ),
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        help="Print at most this many rows (must be positive unless --all is given).",
    ),
    all_objects: bool = typer.Option(
        False,
        "--all",
        help="Print every matching row, ignoring --limit, with no truncation footer.",
    ),
) -> None:
    """List every bundle object's id, sensitivity, lifecycle status, and
    title -- the read-only discovery counterpart to the id-taking write
    verbs (`forget`, `relate`, `merge`, `unmerge`, `set-sensitivity`)
    (issue #184, `openspec/changes/discover-concept-ids/`).

    **Exit ladder, in this exact order (spec: Workspace Presence Check).**
    An unrecognized `TYPE` filter or an out-of-range `--limit` refuses
    (exit 1) BEFORE any workspace or disk access is attempted -- mirroring
    `set-volatility`'s vocabulary-then-workspace precedent
    (`cli/main.py` `set_volatility_cmd`). Only after both usage checks pass
    does `config.require_workspace` run; its failure is the only remaining
    non-zero path. Once past both refusals, no bundle content -- however
    malformed -- can make `list` fail.

    `TYPE` resolves via `listing.resolve_link_dir`: a canonical `link_dir`
    exact match first, then a case-sensitive `REGISTRY.name` alias. An
    unresolved value refuses with a message enumerating only canonical
    `link_dir` names, never the `REGISTRY.name` aliases (spec: Type Filter
    Vocabulary).

    **Exactly one bundle walk.** The single call to
    `listing.list_objects(layout.bundle_dir)` below is the ONLY
    disk-reading call this command makes -- filtering by resolved
    `link_dir` and slicing to the limit both happen on its in-memory
    result. `lifecycle.deprecated_concept_ids` is never called: status is
    already derived inside `listing.list_objects`'s own single pass
    (spec: Exactly One Bundle Walk; design D3).

    Rows are `ID  SENSITIVITY  STATUS  TITLE`, `ljust`-aligned over the
    header labels and the rows actually shown (post-filter,
    post-truncation) -- the same pattern `status`'s bundle-contents
    section uses (`cli/main.py:4989-4992`, design D6). A title is rendered
    `(unreadable)` when the underlying document failed to read/parse, or
    `(untitled)` when it read fine but declared no title -- two distinct
    markers for two distinct follow-ups. Deprecated and superseded objects
    are shown by default, marked via `STATUS`, with no flag to hide them
    (spec: Deprecated and Superseded Visibility).

    **Confidential titles print in full.** `sensitivity` is a column, not a
    gate: there is no redaction, no flag, and no omitted row based on
    sensitivity level -- output is byte-identical in shape regardless of
    it (spec: Confidential Titles Are Printed in Full). `sensitivity`
    governs what LEAVES the machine via `--include-confidential`
    (`sensitivity.py:78-99`), an LLM-send gate this command never touches
    -- `list` performs no LLM send at all.

    Default `--limit` is 50; a truncated result prints a footer reporting
    how many rows were shown out of the total match count. `--all` prints
    every matching row with no footer. `--limit 0` or any negative
    `--limit` is a usage refusal, not a bundle result (spec: Output
    Bounding). An empty bundle, or a filter matching nothing, prints a
    friendly empty-state line and exits 0 (spec: Empty Bundle and
    Unparseable Document Handling).

    Read-only: no file under the workspace is ever created, modified, or
    deleted, and no `--json` or other structured output mode is offered
    (spec: Read-Only, No Structured Output; deferred, not banned -- issue
    #240).
    """
    resolved_type: str | None = None
    if concept_type is not None:
        resolved_type = listing.resolve_link_dir(concept_type)
        if resolved_type is None:
            valid_link_dirs = sorted(
                ot.link_dir for ot in types.REGISTRY if ot.link_dir
            )
            typer.echo(
                f"openkos list: refusing to list -- {concept_type!r} is not a "
                f"known object type (expected one of {valid_link_dirs}).",
                err=True,
            )
            raise typer.Exit(code=1)

    if limit <= 0:
        typer.echo(
            f"openkos list: refusing to list -- --limit must be positive "
            f"(got {limit}); use --all to print every row instead.",
            err=True,
        )
        raise typer.Exit(code=1)

    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos list: refusing to list -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    rows = listing.list_objects(layout.bundle_dir)

    if resolved_type is not None:
        rows = [row for row in rows if row.link_dir == resolved_type]

    if not rows:
        typer.echo("No objects found.")
        return

    total = len(rows)
    shown = rows if all_objects else rows[:limit]

    id_w = max(len("ID"), *(len(row.concept_id) for row in shown))
    sens_w = max(len("SENSITIVITY"), *(len(row.sensitivity) for row in shown))
    stat_w = max(len("STATUS"), *(len(row.status) for row in shown))

    typer.echo(
        f"{'ID'.ljust(id_w)}  {'SENSITIVITY'.ljust(sens_w)}  {'STATUS'.ljust(stat_w)}  TITLE"
    )
    for row in shown:
        title = row.title or ("(unreadable)" if not row.readable else "(untitled)")
        typer.echo(
            f"{row.concept_id.ljust(id_w)}  {row.sensitivity.ljust(sens_w)}  "
            f"{row.status.ljust(stat_w)}  {title}"
        )

    if not all_objects and total > len(shown):
        typer.echo(f"Showing {len(shown)} of {total} — use --all to see the rest.")


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
    under `Stale stamps:`, `Orphan pages:`, `Dangling references:`,
    `Unextracted sources:` (issue #187: `lint_check.check_unextracted`,
    reusing this SAME `docs` list -- no new walk), `Below-source
    sensitivity:`, and `Multi-source uncovered:` (issue #231, PR2:
    `lint_check.check_below_source_sensitivity`, reusing this SAME `docs`
    list too -- design D3's no-fifth-walk guard), each with its own
    empty-state line when there is nothing to report. Every
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
    dangling = lint_check.check_dangling_targets(docs)
    unextracted = lint_check.check_unextracted(docs)
    # #231 (PR2): reuses this SAME `docs` list -- no new bundle walk
    # (design D3's no-fifth-walk guard).
    sensitivity_findings = lint_check.check_below_source_sensitivity(docs)
    below_source = [
        finding
        for finding in sensitivity_findings
        if finding.kind == "below-source-sensitivity"
    ]
    multi_source_uncovered = [
        finding
        for finding in sensitivity_findings
        if finding.kind == "multi-source-uncovered"
    ]
    notices = window_notices + skip_notices
    report = lint_check.LintReport(
        stale=stale,
        orphans=orphans,
        dangling=dangling,
        unextracted=unextracted,
        below_source=below_source,
        multi_source_uncovered=multi_source_uncovered,
        notices=notices,
    )

    typer.echo(f"openkos lint: workspace at {root}")
    for notice_line in report.notices:
        typer.echo(notice_line)
    typer.echo()
    typer.echo("Stale stamps:")
    if not report.stale:
        typer.echo("  No stale stamps.")
    else:
        for finding in report.stale:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Orphan pages:")
    if not report.orphans:
        typer.echo("  No orphan pages.")
    else:
        for finding in report.orphans:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Dangling references:")
    if not report.dangling:
        typer.echo("  No dangling references.")
    else:
        for finding in report.dangling:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Unextracted sources:")
    if not report.unextracted:
        typer.echo("  No unextracted sources.")
    else:
        for finding in report.unextracted:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Below-source sensitivity:")
    if not report.below_source:
        typer.echo("  No below-source sensitivity findings.")
    else:
        for finding in report.below_source:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Multi-source uncovered:")
    if not report.multi_source_uncovered:
        typer.echo("  No multi-source uncovered findings.")
    else:
        for finding in report.multi_source_uncovered:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")


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
    that MIGHT be the same real-world entity, tiered by HOW they matched --
    HIGH (an exact normalized title) or LOW (a near-match). The tier records
    the match METHOD, never a strength ranking, which is why a LOW group can carry
    a similarity score of 1.000 without contradiction (issue #192). This is
    a REPORT ONLY -- `duplicates` never merges, deletes, or otherwise
    adjudicates a candidate; it points at the SHIPPED `merge` verb
    (`cli/main.py:3957`) through its trailing hint instead (spec: Read-Only
    CLI Candidate Report Verb).

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

    high_count = sum(1 for group in groups if group.tier is Tier.HIGH)
    low_count = len(groups) - high_count
    typer.echo(_format_group_tally(high_count, low_count))
    typer.echo(
        "Legend: [tier] type -- trigger. The tier is the MATCH METHOD, "
        "not a strength ranking: HIGH = exact normalized key, "
        "LOW = near-match similarity score."
    )
    for group in groups:
        tier_label = "HIGH" if group.tier is Tier.HIGH else "LOW"
        typer.echo(f"[{tier_label}] {group.okf_type} -- {group.trigger}")
        for member_id in group.member_ids:
            typer.echo(f"  - {member_id}")
        typer.echo()
    typer.echo("Next: openkos merge <survivor> <absorbed>")


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
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit adjudication verdicts as JSON to stdout; suppress human output.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Interactively merge each SAME 2-member group after previewing it.",
    ),
    apply_same: bool = typer.Option(
        False,
        "--apply-same",
        help=(
            "Batch-merge every eligible SAME 2-member group after one "
            "guarded confirmation (see --confirm-count)."
        ),
    ),
    confirm_count: str | None = typer.Option(
        None,
        "--confirm-count",
        help=(
            "The exact eligible-merge count (see the printed preview), for "
            "non-interactive/test use with --apply-same. On a TTY, "
            "omitting this prompts interactively instead. There is NO "
            "bypass for this count -- it must match exactly."
        ),
    ),
) -> None:
    """LLM-adjudicate cross-source candidate duplicates: read-only by default.

    A FOURTH read command, mirroring `query`'s wiring exactly: the shared
    `config.require_workspace` gate (D1), then a Phase-A `read_config` guard
    (`except (OSError, ValueError)`, lint parity), then a real
    `OllamaClient(model=cfg.model)` is built and injected -- as the
    `LLMBackend` -- into `resolution.find_candidates` followed by
    `resolution.adjudication.adjudicate_candidates`. Invoked WITHOUT
    `--apply`/`--apply-same` it never merges, writes, or decides -- it only
    prints a verdict for human review and points at the SHIPPED `merge` verb
    (`cli/main.py:3957`) through its own `Next:` hint, exactly as
    `duplicates` does. Those two flags are the only paths that write, and
    both are gated on an explicit confirmation; see their paragraphs below.

    `--json` emits the adjudication results as a single pretty-printed JSON
    array on stdout and fully suppresses all human output (tally, legend,
    per-group detail, `Next:` hint, and both empty-state messages). It emits
    every verdict by default; passing `--same-only` filters the array to
    `SAME` entries, mirroring the human display filter. It short-circuits
    AFTER the Ollama error handlers below, so a degraded run still exits 1 on
    stderr with no JSON.

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

    Without `--apply`/`--apply-same`, no file under the workspace is ever
    created, modified, or deleted (spec: Verb renders verdicts with zero
    writes, whose scenario is scoped to the flagless invocation).

    `--apply` switches to an INTERACTIVE merge walk over the same
    adjudication results (issue #137 Slice 2b-ii): mutually exclusive with
    `--json` (interactive vs. machine-readable output is contradictory), so
    that combination is rejected up front, before any workspace gate or
    read, with exit code 2.

    `--apply-same` switches to a GUARDED BATCH merge of every eligible SAME
    2-member group (issue #137 closing slice): prints one aggregate
    preview and total count, then requires the operator to type that exact
    count (via `--confirm-count`, an interactive TTY prompt, or refuses on
    a non-TTY without the flag) before applying anything -- a mismatch
    aborts with zero writes. Mutually exclusive with both `--apply` and
    `--json`, rejected up front with exit code 2.
    """
    if apply and json_output:
        typer.echo(
            "openkos adjudicate: --apply and --json are mutually exclusive.",
            err=True,
        )
        raise typer.Exit(code=2)
    if apply_same and apply:
        typer.echo(
            "openkos adjudicate: --apply-same and --apply are mutually exclusive.",
            err=True,
        )
        raise typer.Exit(code=2)
    if apply_same and json_output:
        typer.echo(
            "openkos adjudicate: --apply-same and --json are mutually exclusive.",
            err=True,
        )
        raise typer.Exit(code=2)

    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos adjudicate: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
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

    if json_output:
        typer.echo(
            json.dumps(_adjudication_payload(results, same_only=same_only), indent=2)
        )
        return

    if apply:
        _run_adjudicate_apply(root, layout, index_path, log_path, results)
        return

    if apply_same:
        _run_adjudicate_apply_same(
            root, layout, index_path, log_path, results, confirm_count=confirm_count
        )
        return

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

    verdict_counts = Counter(result.verdict for result in results)
    typer.echo(
        _format_verdict_tally(
            verdict_counts[Verdict.SAME],
            verdict_counts[Verdict.DIFFERENT],
            verdict_counts[Verdict.UNCERTAIN],
        )
    )
    typer.echo(
        "Legend: [tier] type -- trigger, then verdict and rationale. "
        "The tier is the MATCH METHOD, not a strength ranking: "
        "HIGH = exact normalized key, LOW = near-match similarity score."
    )
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
    typer.echo("Next: openkos merge <survivor> <absorbed>")


_CANDIDATES_UNAVAILABLE_MESSAGE = (
    "Candidate relations unavailable — run `openkos reindex` (vectors.db missing)."
)
"""Slice 0 (issue #183) state-3 message, shared verbatim by
`suggest-relations` and `contradictions` -- design.md's Slice 0 table marks
both sites' state-3 message identical, and both key it on
`vector_store_is_empty(layout.vectors_db_path)` (absent OR empty; Slice 1's
`neighbors()`/`proximity.py` plumbing is out of scope for this check)."""


def _open_proximity_or_degrade(
    vectors_db_path: Path,
) -> proximity.VectorProximitySource | None:
    """Resolve the embedding-proximity candidate source for this run, or
    `None` when embeddings cannot serve one (#183).

    The ONE place any command decides whether candidate edges are available.
    Every caller then derives the user-facing "embeddings missing" state
    from `is None` rather than probing `vectors.db` a second time -- that
    probe used to live inside `_zero_edge_state_message`, on a path taken
    every run.

    Returns the source rather than a `(source, unavailable)` pair on
    purpose: `unavailable` IS `source is None`, and a tuple carrying the
    same fact twice invites the two halves to drift. `status`, which needs
    the state but never the source, keeps calling `vector_store_is_empty` --
    consistent by construction, because `open_proximity_source` is defined
    against that exact predicate.

    Never raises: `open_proximity_source` already absorbs an absent, empty,
    unreadable or extension-less store."""
    return proximity.open_proximity_source(vectors_db_path)


def _zero_edge_state_message(
    layout: config.WorkspaceLayout,
    *,
    store: GraphStore,
    use_typed_count: bool,
    none_survived: str,
    embeddings_missing: bool,
    all_excluded: str | None = None,
) -> str:
    """Select the Slice 0 (issue #183) three-state message for a
    zero-candidate outcome at `suggest-relations`/`contradictions`.

    `embeddings_missing` comes from the caller's `_open_proximity_or_degrade`
    result (`source is None`), never from a second probe of `vectors.db` --
    the seam already read it.

    `store` (graph-projection-reuse, issue #196): the SAME already-open
    `GraphStore` the caller built for its primary read -- REQUIRED, not
    optional, so a forgotten keyword fails a `TypeError` at import-time test
    collection rather than silently rebuilding the projection a second time.
    Both call sites now sit lexically inside their own `with build_graph(...)
    as store:` block, so this function never opens or closes a store itself.
    `layout` is retained for the state-3 early return's context even though
    its `bundle_dir` is not read again here.

    State 3 (embeddings absent OR empty) is checked FIRST: it also starves
    any embedding-sourced candidate edge, so it wins over whatever the
    typed/total edge count would otherwise say (design.md's Graceful
    Degradation table). Otherwise state 1 or state 2 (`none_survived`,
    formatted with `count=`) is picked from `graph_edge_summary`'s `(total,
    typed)` -- `use_typed_count` selects which of the two
    `suggest-relations` counts total edges while `contradictions` counts
    only typed ones, since a typed-but-excluded edge (e.g. `derived_from`)
    is still "nothing to contradict" but is NOT "nothing to type". State 1's
    wording also tracks `use_typed_count`: the typed-count mode (`contradictions`)
    says "no typed edges yet" (the graph may still have untyped
    concept-to-concept edges -- a DIFFERENT, non-contradictory claim from
    `status`'s total-count wording), while the total-count mode
    (`suggest-relations`) says "no concept relationships yet".

    `all_excluded` (total-count mode only) covers the case where the graph
    DOES still hold untyped rows but none of them survived the caller's
    filtering -- `_candidate_edges`'s PAIR-level exclusion (`relate` adds a
    typed `relations:` row without ever removing the original untyped
    body-link row, `edge_typing.py:116-138`) or `candidate_edges`'s
    confidentiality gate. `none_survived`'s "none are untyped" wording would
    be factually FALSE there, because `graph_edge_summary` counts raw rows
    with neither filter applied. It is formatted with `count=` and
    `untyped=`; omitting it keeps the plain `none_survived` wording."""
    if embeddings_missing:
        return _CANDIDATES_UNAVAILABLE_MESSAGE
    total, typed = graph_edge_summary(layout.bundle_dir, store=store)
    count = typed if use_typed_count else total
    if count == 0:
        if use_typed_count:
            return "The graph has no typed edges yet."
        return "No concept relationships in the graph yet."
    untyped = total - typed
    if all_excluded is not None and untyped > 0:
        return all_excluded.format(count=count, untyped=untyped)
    return none_survived.format(count=count)


@app.command("suggest-relations")
def suggest_relations_cmd(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation gate and type every untyped edge.",
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
) -> None:
    """LLM-suggest a relation `type` for every existing UNTYPED body-link
    edge: read-only, like `adjudicate`.

    A FIFTH read command, mirroring `adjudicate`'s wiring: the shared
    `config.require_workspace` gate (D1), then a Phase-A `read_config` guard
    (`except (OSError, ValueError)`, lint parity). It then counts the
    candidate edges via `resolution.edge_typing.candidate_edges`, which owns
    the candidate-narrowing logic. This command builds the graph projection
    ONCE per invocation via `graph.sqlite_graph.build_graph` and threads the
    open store into every reader it calls, including the zero-result
    `_zero_edge_state_message` path that used to trigger a second full build
    (#196). Holding an open `openkos.graph` store here is established
    practice (`query`, `reindex`); the live layering rule forbids only
    canonical-layer imports of `openkos.graph` and a `graph` CLI verb. It
    gates on the candidate count, and only then builds a real
    `OllamaClient(model=cfg.model)` and injects it into
    `suggest_edge_types`.

    Cost gate (issue #134): each untyped edge costs one LLM inference, run
    sequentially, so a large bundle can take many minutes with the model
    resident. Before contacting the model, the command prints the count
    (`N untyped edges -> N LLM calls`) and asks for confirmation; `--auto`
    skips the prompt. A per-edge progress line is written to stderr as the
    run proceeds. Declining the prompt exits 0 with nothing generated.

    `suggest-relations` never writes, merges, or decides -- it only prints a
    suggested `type` + rationale per untyped edge for human review, plus a
    closing hint pointing at the existing `relate` verb, the ONLY write path
    for an accepted suggestion (spec: Human-In-The-Loop Write Path
    Unchanged). The confirmation gate is read-only (it generates suggestions,
    never writes); there is no `--json` or other structured mode.

    A degraded suggestion (`suggested_type=None` -- a malformed LLM reply,
    or a suggested type that failed `validate_relation_type`) renders as
    `[?]` plus a `note: no valid type suggested` line, never as if it were a
    valid suggestion (spec: Invalid suggested type is not surfaced as
    valid). Already-typed edges never appear at all -- `candidate_edges`
    filters them out before this command ever sees them (spec: Already-typed
    edges are excluded from suggestions).

    A no-model/no-Ollama run degrades via the SAME 3-tier ORDERED handler
    `adjudicate`/`query` use -- `OllamaUnavailable`, then
    `OllamaModelNotFound`, then the generic `OllamaError` fallback -- each
    with its own actionable stderr message, exit 1, and zero writes.

    Unless `--include-confidential` is passed, an untyped edge with a
    confidential endpoint (sensitivity-fail-closed-filter) is excluded from
    candidates -- dropped by `candidate_edges` before `llm.chat` is ever
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

    observability.warn_if_walk_incomplete(
        layout.bundle_dir, include_confidential=include_confidential
    )

    # Count the candidate edges FIRST, with no LLM call, so the cost of the
    # one-inference-per-edge run can be previewed and gated before the model
    # is ever contacted (issue #134).
    #
    # graph-projection-reuse (#196): the proximity source is closed as early
    # as possible -- `build_graph` consumes it eagerly inside
    # `_populate_graph_tables`, so it is dead the instant `build_graph`
    # returns. The projection itself is built exactly ONCE per invocation
    # and threaded, via `store=`, into both `candidate_edges` and the
    # zero-result `_zero_edge_state_message` path (which used to trigger a
    # second full build).
    source = _open_proximity_or_degrade(layout.vectors_db_path)
    embeddings_missing = source is None
    try:
        graph = build_graph(layout.bundle_dir, candidates=source)
    finally:
        if source is not None:
            source.close()

    with graph as store:
        edges = candidate_edges(
            layout.bundle_dir,
            include_confidential=include_confidential,
            store=store,
        )

        typer.echo(f"openkos suggest-relations: workspace at {root}")
        typer.echo()
        total = len(edges)
        if total == 0:
            typer.echo(
                _zero_edge_state_message(
                    layout,
                    store=store,
                    use_typed_count=False,
                    embeddings_missing=embeddings_missing,
                    none_survived="{count} relation(s) exist; none are untyped.",
                    all_excluded=(
                        "{count} relation(s) exist; {untyped} untyped, but every "
                        "untyped pair is already typed elsewhere or filtered as "
                        "confidential -- nothing left to suggest."
                    ),
                )
            )
            return

    # Everything from here on runs OUTSIDE the `with` block: the store is
    # not needed once `edges` is materialized, so the minutes-long LLM run
    # and its progress loop stay out of the store's lifetime
    # (graph-projection-reuse design §4).
    if not auto:
        typer.echo(
            f"{total} untyped edge(s) -> {total} LLM call(s), one per edge "
            "(this can take a while). Pass --auto to skip this prompt.",
            err=True,
        )
        if not typer.confirm("Proceed?"):
            typer.echo("Aborted -- no suggestions generated.")
            return

    llm = OllamaClient(model=cfg.model)

    def _on_progress(index: int, count: int, suggestion: EdgeSuggestion) -> None:
        """Per-edge progress line to stderr (keeps stdout the clean report)."""
        edge = suggestion.edge
        label = suggestion.suggested_type or "?"
        typer.echo(
            f"  [{index}/{count}] {edge.source_id} -> {edge.target_id}  [{label}]",
            err=True,
        )

    try:
        results = suggest_edge_types(
            edges,
            bundle_dir=layout.bundle_dir,
            llm=llm,
            include_confidential=include_confidential,
            on_progress=_on_progress,
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
    review, plus a closing hint pointing at `openkos set-volatility
    <ConceptType> <tier>` (write-verb #140) to apply an accepted suggestion.
    No `--auto`, no confirmation gate, no `--json` or other structured mode.

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

    typer.echo("Next: openkos set-volatility <ConceptType> <tier>")


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
    which owns the candidate-narrowing logic. This command builds the graph
    projection ONCE per invocation via `graph.sqlite_graph.build_graph` and
    threads the open store into every reader it calls, including the
    zero-result `_zero_edge_state_message` path that used to trigger a
    second full build (#196). Holding an open `openkos.graph` store here is
    established practice (`query`, `reindex`); the live layering rule
    forbids only canonical-layer imports of `openkos.graph` and a `graph`
    CLI verb.

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
    # graph-projection-reuse (#196): source-then-build prologue, mirroring
    # `suggest_relations_cmd` -- the proximity source is closed as early as
    # possible, right after `build_graph` consumes it. Unlike
    # `suggest-relations`, the LLM loop runs INSIDE `find_contradictions`, so
    # the store stays open across it (design §4): splitting this block would
    # require two builds, which is exactly what #196 removes.
    source = _open_proximity_or_degrade(layout.vectors_db_path)
    embeddings_missing = source is None
    try:
        graph = build_graph(layout.bundle_dir, candidates=source)
    finally:
        if source is not None:
            source.close()

    with graph as store:
        try:
            verdicts, total_pairs = find_contradictions(
                layout.bundle_dir,
                llm=llm,
                include_deprecated=include_deprecated,
                include_confidential=include_confidential,
                store=store,
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
            typer.echo(
                _zero_edge_state_message(
                    layout,
                    store=store,
                    use_typed_count=True,
                    embeddings_missing=embeddings_missing,
                    none_survived=(
                        "{count} typed relation(s); none are contradiction candidates."
                    ),
                )
            )
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
        # `OllamaEmbeddingDimensionMismatch` is a PERMANENT, non-healing
        # misconfiguration (issue #209): the configured `embedding_model`
        # does not emit `EMBED_DIM`-dimensional vectors, so dense retrieval
        # is structurally impossible, not merely unhelpful this run --
        # `answer()` therefore propagates it instead of degrading to a
        # silent FTS-only answer at exit 0. Like `reindex`'s own branch, it
        # names a concrete remediation: restore the working
        # `embedding_model` value in `openkos.yaml`. It deliberately does
        # NOT point at `openkos reindex` -- reindex fails with this very
        # same error until the config is fixed, so that hint would be
        # actively misleading here. MUST NOT say "will retry next run"
        # (phrasing reserved for a transient `embed_failed` skip). Placed
        # BEFORE the generic tuple below for the same ordering reason as the
        # two handlers above: `OllamaEmbeddingDimensionMismatch` subclasses
        # `OllamaError`, so reordering would swallow it into the bare
        # message and lose this remediation.
        except OllamaEmbeddingDimensionMismatch as exc:
            typer.echo(
                f"openkos query: failed -- {exc} Restore the working "
                "'embedding_model' value in openkos.yaml, then try again.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        # The three specific handlers above MUST precede this generic tuple:
        # `OllamaUnavailable`, `OllamaModelNotFound`, and
        # `OllamaEmbeddingDimensionMismatch` all subclass `OllamaError`, so
        # reordering would silently funnel them into this fallback and lose
        # their actionable remediation messages.
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
    # `OllamaEmbeddingDimensionMismatch` is a PERMANENT, non-healing
    # misconfiguration -- unlike `OllamaUnavailable`/`OllamaModelNotFound`,
    # it names a concrete remediation: the configured `embedding_model` no
    # longer produces `EMBED_DIM`-dimensional vectors, so it must be
    # restored in `openkos.yaml`. Placed BEFORE the generic
    # `(VecUnavailable, FtsUnavailable, OllamaError)` tuple below --
    # `OllamaEmbeddingDimensionMismatch` subclasses `OllamaError`, so
    # reordering this branch after that tuple would silently swallow it
    # into the generic message (same ordering discipline as the two
    # handlers above). MUST NOT say "will retry next run" -- that phrasing
    # is reserved for a transient `embed_failed` skip, not a permanent
    # misconfiguration.
    except OllamaEmbeddingDimensionMismatch as exc:
        typer.echo(
            f"openkos reindex: failed -- {exc} Restore the working "
            "'embedding_model' value in openkos.yaml, then run `openkos "
            "reindex` again.",
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
        with_candidates = _open_proximity_or_degrade(layout.vectors_db_path)
        try:
            sqlite_graph.reindex_graph(
                layout.bundle_dir,
                layout.graph_db_path,
                force=force,
                candidates=with_candidates,
            )
        finally:
            if with_candidates is not None:
                with_candidates.close()
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
    instead of exiting on the first failure, this runs ALL ten checks,
    appends each to a `list[CheckResult]`, renders every line
    unconditionally, then exits ONCE (`code=1`) if any CRITICAL check
    failed (spec: Doctor Runs And Prints All Applicable Checks). Remediation
    TEXT lives only here; `llm/` stays config-free (D1).

    Output leads with an `openkos {version}` banner -- the same line
    `--version` prints (cli-version-flag, #181). It is informational only,
    never a `CheckResult`, and precedes both the header and the check lines,
    so it affects neither the check count nor the exit code.

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
    workspace-vector-index-present -- informational, workspace-only,
    `[SKIP]` outside a workspace, via `layout.vectors_db_path.exists()`
    (purge-transactional-cleanup #142) -- distinct from (8): this checks
    THIS workspace's own `.openkos/vectors.db` file, not a throwaway
    `:memory:` probe; a `[FAIL]` here always names `openkos reindex` as its
    remediation; (8) vector-extension-loadable -- informational, always, via
    `state.vectorstore.probe_vec_loadable()` against a throwaway `:memory:`
    connection; UNLIKE (5), this check has NO `[SKIP]` branch -- it depends
    on neither workspace state nor Ollama reachability, so it shares no root
    cause with any other check (embedding-vector-store, Slice 2a; the
    scaffolding this checks has no consumed feature yet either); (9)
    git-available -- informational, always, via `vcs.git.git_available()`;
    (10) git-filter-repo-available -- informational, always, via
    `vcs.git.filter_repo_available()`. Checks (9)/(10) exist for the
    not-yet-wired `purge` verb (privacy-purge Slice 1, PR2): like (8), they
    have no `[SKIP]` branch -- they depend on neither workspace state nor
    Ollama. Outside a workspace, checks (3)/(4)/(5)/(8)/(9)/(10) still run
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
    installed: list[InstalledModel] = []
    installed_tags: list[str] = []
    client = OllamaClient(model=model, timeout=_PREFLIGHT_TIMEOUT)
    try:
        installed = client.list_models()
        installed_tags = [m.tag for m in installed]
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
    elif model_tag_matches(model, installed_tags):
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
    elif model_tag_matches(embedding_model, installed_tags):
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

    # 7. workspace-vectors-present (informational, workspace-only; SKIP
    # outside -- mirrors check 6's workspace-only shape). Distinct from
    # vector-extension-loadable's throwaway `:memory:` probe
    # (`probe_vec_loadable()`, which
    # says nothing about a specific workspace's own index file): this
    # checks whether THIS workspace's `.openkos/vectors.db` exists on disk
    # (purge-transactional-cleanup #142). Staleness (mtime) is deliberately
    # out of scope -- absent-only.
    if in_workspace:
        if config.WorkspaceLayout(root).vectors_db_path.exists():
            results.append(
                CheckResult("Workspace vector index present", "pass", critical=False)
            )
        else:
            results.append(
                CheckResult(
                    "Workspace vector index present",
                    "fail",
                    critical=False,
                    remediation="openkos reindex",
                )
            )
    else:
        results.append(
            CheckResult("Workspace vector index present", "skip", critical=False)
        )

    # 8. vector-extension-loadable (informational, always; NO SKIP branch --
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

    # 9. git-available (informational, always; NO SKIP branch -- shares no
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

    # 10. git-filter-repo-available (informational, always; NO SKIP branch)
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

    # Leading version banner (cli-version-flag, #181): informational only, NOT
    # a CheckResult -- it is deliberately outside `results` so it can never
    # affect the check count or the exit code.
    typer.echo(_version_line())
    typer.echo(f"openkos doctor: checking environment at {root}")
    typer.echo()
    for r in results:
        _render_check(r)

    if any(r.status == "fail" and r.critical for r in results):
        raise typer.Exit(code=1)
