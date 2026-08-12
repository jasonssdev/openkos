"""Typer application object exposed as the `openkos` console script."""

import glob
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path, PurePosixPath
from typing import Final, Literal, TypedDict

import typer
from rich.console import Console

from openkos import config, fsio, source_title
from openkos import lint as lint_check
from openkos.bundle import bundle, listing, source_titles
from openkos.bundle import decisions as bundle_decisions
from openkos.bundle import index as bundle_index
from openkos.bundle import ledger as bundle_ledger
from openkos.bundle import links as bundle_links
from openkos.bundle import log as bundle_log
from openkos.bundle import merge as bundle_merge
from openkos.bundle import provenance as bundle_provenance
from openkos.bundle import references as bundle_references
from openkos.bundle import relations as bundle_relations
from openkos.cli import curate as curate_module
from openkos.cli import next_action as next_action_module
from openkos.cli import observability
from openkos.extraction.concept import (
    ExtractionReport,
    extract_concept,
    extract_concept_union,
)
from openkos.graph import proximity, sqlite_graph
from openkos.graph.base import GraphStore
from openkos.graph.sqlite_graph import build_graph
from openkos.graph.summary import graph_edge_summary
from openkos.llm.base import LLMBackend
from openkos.llm.ollama import (
    BackendHostLocality,
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
from openkos.resolution import find_candidates_report, find_exact_title_groups
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
    adjudicate_candidates,
)
from openkos.resolution.candidates import (
    CandidateGroup,
    Tier,
    candidate_group_truncation_notice,
)
from openkos.resolution.contradiction import (
    ContradictionBatch,
    contradiction_truncation_notice,
    find_contradictions,
    is_high_confidence_contradiction,
    plan_candidates,
)
from openkos.resolution.edge_typing import (
    EdgeSuggestion,
    EdgeSuggestionBatch,
    candidate_edges,
    candidate_truncation_notice,
    suggest_edge_types,
)
from openkos.resolution.volatility_typing import (
    TierSuggestionBatch,
    suggest_volatility,
)
from openkos.retrieval.answer import NO_MATCH, Citation, NoMatchCause, answer
from openkos.sensitivity import blocks_llm_send
from openkos.state import derived, fts
from openkos.state import reindex as reindex_module
from openkos.state.derived import stale_derived_stores
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

# Every command sets `help=` and `rich_help_panel=` (#389).
#
# `help=` exists so Typer publishes THAT text instead of the raw `__doc__`.
# The docstrings keep their design, spec and issue references for
# maintainers; the published surface stays free of them. This is not only
# about `--help`: MCP tool descriptions are usually derived from the same
# source, and text that does not help a person will not help an agent.
#
# Panels group by WHAT THE READER IS TRYING TO DO -- not by write-ness, not
# by cost. "Get started" is the first hour. "Explore" asks the bundle
# questions. "Curate" decides things about its contents. "Maintain" tends
# the machinery behind it. "Remove" deletes. Use that rule when adding a
# command rather than matching the nearest-looking neighbour; before #389
# the listing was declaration order, which put `purge` -- irreversible and
# rare -- fourth, and `query`, the value moment, near the bottom.

# doctor and init's Ollama preflight are both fast interactive diagnostics:
# use a short timeout so a hung/firewalled host fails quickly instead of
# blocking on OllamaClient's DEFAULT_TIMEOUT.
_PREFLIGHT_TIMEOUT = 5.0


def _chat_client(cfg: config.Config, *, task: str | None = None) -> OllamaClient:
    """Build the CHAT client for a workspace, honoring its `chat_timeout`.

    Every chat verb goes through here (issue #405). Constructing
    `OllamaClient(model=cfg.model)` inline instead reads fine and silently
    ignores the workspace's `chat_timeout`, falling back to the transport
    default -- a defect no type checker or ordinary test can see, across the
    eight-odd verbs that make chat calls. One seam means a workspace that
    raises its deadline raises it for `ingest`, `curate`, `query`,
    `adjudicate`, `suggest-relations`, and `contradictions` alike, rather
    than for whichever verbs someone remembered.

    Deliberately NOT used for the two other client kinds: embedding clients
    (`model=cfg.embedding_model`) keep the transport default, and the
    liveness probes keep `_PREFLIGHT_TIMEOUT`, which answers "is anything
    listening" and must stay short -- a 600s probe would hang the CLI on a
    firewalled host instead of failing fast.

    `curate.py` builds its own client from `ctx.cfg` rather than calling
    this: `main.py` already imports `curate`, so the dependency cannot run
    the other way. `tests/unit/cli/test_chat_timeout_wiring.py` pins both
    sites so the pair cannot drift.

    Also honors `cfg.max_generation_tokens` (issue #422): the safety rail
    on how much a single chat call may GENERATE, distinct from
    `chat_timeout`'s bound on how long the client WAITS.

    `task` (issue #515) names which measured task this client is for, so
    `config.resolve_task_model` can honor a `models:` override. Omitting it
    keeps `cfg.model` -- and the two callers that omit it do so
    deliberately, not by oversight:

    - `query` synthesizes an answer, which is NOT one of the five keys in
      `TASK_MODEL_KEYS`. It has no harness, so #508's rule ("a per-task
      default must be justified on a fixture") forbids inventing a key for
      it here.
    - `curate`'s `_resolve_local_exemption` probe asks the client for its
      `locality`, a property of the HOST it would connect to. That answer
      is identical whichever model tag the client carries, so resolving a
      task model for it would imply a per-task locality that does not
      exist.

    The per-task tag changes WHICH model runs and nothing else: both safety
    rails still apply, which matters most precisely for the large models
    #516's sweep favors at edge typing.
    """
    return OllamaClient(
        model=config.resolve_task_model(cfg, task),
        timeout=cfg.chat_timeout,
        max_generation_tokens=cfg.max_generation_tokens,
    )


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


def _snapshot_read(path: Path) -> tuple[bytes, str]:
    """Read `path` exactly ONCE; return the raw bytes (for
    `_reject_drifted_targets`) and the decoded text (for the parsers), both
    derived from the SAME observation (issue #318).

    Every drift-guarded verb needs both forms of every write target: the
    parsers want decoded text to compute the plan from, and the guard wants
    raw bytes to compare against at write time. Obtaining them as TWO reads
    -- `read_text` for the plan, then `read_bytes` for the guard -- left a
    window between them that defeated the guard entirely: a writer landing
    in it became the guard's own baseline, so the comparison found no
    drift and Phase B wrote the plan derived from the EARLIER text,
    silently reverting the edit the guard exists to catch (and
    `_autocommit` then committed the revert). One read, both forms derived
    from it, is the only shape with no such window; callers must never
    split this back into separate `read_text`/`read_bytes` calls.

    The newline translation below reproduces `read_text`'s
    universal-newline behavior EXACTLY -- `\\r\\n` first, then any lone
    `\\r`, both to `\\n` -- because a bare `bytes.decode()` is not the text
    the parsers were written against: a CRLF-at-rest file would hand them
    `\\r`-bearing lines that `read_text` never produced, changing parser
    behavior for a file nobody touched. The RAW bytes are returned
    untranslated, which is what lets the guard still see a CRLF-only
    rewrite as the drift it is."""
    data = path.read_bytes()
    text = data.decode("utf-8")
    return data, text.replace("\r\n", "\n").replace("\r", "\n")


def _reject_torn_ledger_write(
    bundle_dir: Path, survivor_canonical: str, verb: str
) -> None:
    """Refuse (exit 1, writes nothing) when a `.pending` intent marker
    already exists for `survivor_canonical`'s ledger sidecar (design
    Decision 5, Check A -- a torn two-phase write from a prior crashed
    `merge`). `merge`/`unmerge` both call this in Phase A, before any
    write, and with NO `--force` override: unlike the doctor-flagged
    (post-merge-mutation) refusal, a torn `.pending` is mechanically
    exact and trivially repairable (`bundle_ledger.recover`), and forcing
    past it would commit a known-inconsistent ledger on top of an
    unresolved crash artifact."""
    pending_path = bundle_ledger.pending_path_for(survivor_canonical, bundle_dir)
    if not pending_path.is_file():
        return
    typer.echo(
        f"openkos {verb}: refusing to {verb} -- {survivor_canonical!r}'s ledger "
        "has a torn write pending (a prior merge crashed mid-commit). Run "
        "`openkos doctor` to inspect it; this refusal has no --force override "
        "because the marker is trivially repairable and forcing past it would "
        "commit a known-inconsistent ledger.",
        err=True,
    )
    raise typer.Exit(code=1)


def _reject_flagged_ledger_write(
    root: Path, bundle_dir: Path, survivor_canonical: str, force: bool
) -> None:
    """Refuse (exit 1, writes nothing) when `survivor_canonical`'s ledger
    sidecar is flagged by doctor's Check B (post-merge mutation,
    `bundle_ledger.scan_nesting_violations`) -- UNLESS `--force` is passed
    (spec: "`merge` Refuses On A Doctor-Flagged Ledger, With `--force`").

    `merge` calls this in Phase A, before any write. `--force` bypasses
    ONLY this refusal -- it is orthogonal to the confirm-gate precedence
    (`--auto`/`review: false`/TTY prompt) that governs the write itself,
    mirroring `forget --force`'s independence from `--auto`. Unlike
    `_reject_torn_ledger_write` (Check A, mechanically exact and trivially
    repairable), Check B's corruption is not always repairable, so this
    refusal has an escape hatch for an operator who has already confirmed
    it is safe to proceed."""
    if force:
        return
    violations = bundle_ledger.scan_nesting_violations(bundle_dir)
    if not any(survivor_id == survivor_canonical for survivor_id, _ in violations):
        return
    if vcs_git.repo_root(root) is not None and vcs_git.has_reset_point(root):
        reset_remedy = "run `git reset --hard <first-merge>~1` then `openkos reindex`"
    else:
        reset_remedy = (
            "no git reset point is available in this workspace (no "
            "repository, no configured git identity, or no commit "
            "history) -- there is no remedy that restores reversibility "
            "for the affected merge(s)"
        )
    typer.echo(
        f"openkos merge: refusing to merge -- {survivor_canonical!r}'s ledger "
        "is flagged by the merge-ledger-integrity check (post-merge "
        "mutation). If the ledger is merely unmigrated (still embedded in "
        "the survivor's own frontmatter, not corrupted), run `openkos "
        f"repair`; if corrupted, {reset_remedy} -- reversibility of merges "
        "made before this fix is not guaranteed. Re-run with --force to "
        "bypass this refusal.",
        err=True,
    )
    raise typer.Exit(code=1)


def _sweep_ledger_sidecars_for_ids(
    bundle_dir: Path, purge_ids: Iterable[str]
) -> list[Path]:
    """Privacy sweep of `bundle/.state/ledger/` for `purge_ids` membership
    (forget-command spec: "Deletion Sweep Includes Ledger Storage";
    privacy-purge spec: "Whole-History Expunge Covers The Ledger Sidecar
    Store" -- shared by `forget`'s and `purge`'s own Phase B, so the sweep
    is written exactly once):

    - Each purge-set member's OWN ledger sidecar (if it is/was itself a
      merge survivor) is deleted OUTRIGHT -- the member's own merge history
      is no longer meaningful once the member itself is gone.
    - Every OTHER live sidecar has any entry whose `absorbed_id` is in
      `purge_ids` dropped (`ledger.write_entries` with the remaining
      entries, or removed entirely when none remain) -- so a purge-set
      member's pre-merge body does not survive merely because it was
      absorbed into a DIFFERENT survivor that is not itself being
      forgotten/purged.

    Returns every ledger path touched (deleted, or rewritten), bundle-dir-
    relative-capable via the caller, so it can be folded into the SAME
    Phase B write/`_autocommit` the concept-file deletion already uses --
    never a second, independent write pass.

    A sidecar whose `survivor_id` field is missing or non-string is skipped
    defensively rather than guessed at -- this sweep only ever REMOVES
    content, so a malformed sidecar it cannot safely identify is left for
    `doctor` to flag, not silently rewritten under an invented id."""
    purge_ids_set = set(purge_ids)
    touched: list[Path] = []
    deleted: set[Path] = set()
    for member in sorted(purge_ids_set):
        own_path = bundle_ledger.ledger_path_for(member, bundle_dir)
        if own_path.is_file():
            fsio.remove_file(own_path)
            touched.append(own_path)
            deleted.add(own_path)
    for ledger_path in bundle_ledger.iter_ledgers(bundle_dir):
        if ledger_path in deleted:
            continue
        metadata, _ = okf.load_frontmatter(ledger_path.read_text(encoding="utf-8"))
        survivor_id = metadata.get("survivor_id")
        if not isinstance(survivor_id, str) or not survivor_id:
            continue
        entries = okf.decode_merged_from(metadata)
        remaining = [e for e in entries if e.absorbed_id not in purge_ids_set]
        if len(remaining) == len(entries):
            continue
        bundle_ledger.write_entries(
            survivor_id, bundle_dir, survivor_id=survivor_id, entries=remaining
        )
        touched.append(ledger_path)
    return touched


def _decisions_history_targets(bundle_dir: Path, purge_ids: Iterable[str]) -> list[str]:
    """Every `bundle/.state/decisions/**` path -- own OR foreign -- that
    references a purge-set member, for inclusion in `purge`'s
    `expunge_targets` list IN THE SAME `git filter-repo` pass as the
    concept's own file expunge (privacy-purge spec: "Whole-History
    Expunge Covers The Pending-Work Decision Subtree", pending-work design
    Decision 5).

    A record "references" `purge_ids` when its `pair_ids` (either
    element) OR its `merged_absorbed_id` names a purge-set member.

    Unlike the merge-ledger sidecar's history coverage (own sidecar ONLY,
    the `bundle_ledger.ledger_path_for` loop above) -- which leaves a
    FOREIGN sidecar's historical `absorbed_id` entries as a documented gap
    -- this covers foreign decisions sidecars too. `expunge_paths`' own
    `--file-info-callback` content-scrub is wired ONLY for
    `bundle/index.md`/`bundle/log.md`, not for `bundle/.state/**`, so a
    whole-file history removal is the only way to guarantee no historical
    blob of ANY decisions path retains a purged id, which the spec
    requires. `_sweep_decisions_for_ids` is the LIVE-tree counterpart that
    reconstructs a foreign file's surviving (unrelated) records afterwards,
    in the SAME Phase B pass.

    Returned as bundle-relative POSIX strings (`bundle/.state/decisions/
    **`), matching the shape every other `expunge_targets` entry already
    uses -- callers append these directly, no further conversion needed."""
    purge_ids_set = set(purge_ids)
    targets: list[str] = []
    for decisions_path in bundle_decisions.iter_decisions(bundle_dir):
        metadata, _ = okf.load_frontmatter(decisions_path.read_text(encoding="utf-8"))
        concept_id = metadata.get("concept_id")
        if not isinstance(concept_id, str) or not concept_id:
            continue
        records = bundle_decisions.read_decisions(concept_id, bundle_dir)
        references_purge_set = any(
            record.pair_ids[0] in purge_ids_set
            or record.pair_ids[1] in purge_ids_set
            or record.merged_absorbed_id in purge_ids_set
            for record in records
        )
        if references_purge_set:
            targets.append(
                f"bundle/{decisions_path.relative_to(bundle_dir).as_posix()}"
            )
    return targets


def _sweep_decisions_for_ids(bundle_dir: Path, purge_ids: Iterable[str]) -> list[Path]:
    """Privacy sweep of `bundle/.state/decisions/` for `purge_ids`
    membership (privacy-purge spec: "Whole-History Expunge Covers The
    Pending-Work Decision Subtree"; forget-command spec: "Forget Sweeps
    Live Decision Entries Referencing The Purge Set" -- shared by
    `forget`'s and `purge`'s own Phase B, mirroring
    `_sweep_ledger_sidecars_for_ids`'s two-branch shape exactly, one
    primitive written once):

    - Each purge-set member's OWN decisions sidecar
      (`bundle.decisions.decisions_path_for(member, bundle_dir)`) is
      deleted OUTRIGHT -- once the concept itself is gone, decisions keyed
      on it (`pair_ids[0] == member`) are meaningless.
    - Every OTHER live decisions sidecar has any record whose `pair_ids`
      or `merged_absorbed_id` names a purge-set member dropped
      (`bundle.decisions.write_decisions` with the remaining records, or
      the file removed entirely when none remain) -- so a purge-set
      member's participation in a contradiction decision does not survive
      merely because the record lives under a DIFFERENT (live) concept's
      sidecar.

    Returns every decisions path touched (deleted, or rewritten),
    bundle-dir-relative-capable via the caller, so it can be folded into
    the SAME Phase B write/`_autocommit` the concept-file deletion already
    uses -- never a second, independent write pass.

    For `purge`, `_decisions_history_targets` ALSO puts every one of these
    same paths into `expunge_targets` before the `git filter-repo` pass
    (a stronger guarantee than the ledger sweep's own-file-only history
    coverage, per the privacy-purge spec delta); this function is the
    LIVE-tree half of that coverage, and it is the ENTIRE sweep for
    `forget`, which performs no history rewrite at all.

    A sidecar whose `concept_id` field is missing or non-string is skipped
    defensively rather than guessed at, matching
    `_sweep_ledger_sidecars_for_ids`'s own defensive posture."""
    purge_ids_set = set(purge_ids)
    touched: list[Path] = []
    deleted: set[Path] = set()
    for member in sorted(purge_ids_set):
        own_path = bundle_decisions.decisions_path_for(member, bundle_dir)
        if own_path.is_file():
            fsio.remove_file(own_path)
            touched.append(own_path)
            deleted.add(own_path)
    for decisions_path in bundle_decisions.iter_decisions(bundle_dir):
        if decisions_path in deleted:
            continue
        metadata, _ = okf.load_frontmatter(decisions_path.read_text(encoding="utf-8"))
        concept_id = metadata.get("concept_id")
        if not isinstance(concept_id, str) or not concept_id:
            continue
        records = bundle_decisions.read_decisions(concept_id, bundle_dir)
        remaining = [
            record
            for record in records
            if record.pair_ids[0] not in purge_ids_set
            and record.pair_ids[1] not in purge_ids_set
            and record.merged_absorbed_id not in purge_ids_set
        ]
        if len(remaining) == len(records):
            continue
        bundle_decisions.write_decisions(concept_id, bundle_dir, records=remaining)
        touched.append(decisions_path)
    return touched


def _reject_drifted_targets(
    layout: config.WorkspaceLayout,
    expected: Mapping[Path, bytes],
    verb: str,
    *,
    deletes: AbstractSet[Path] = frozenset(),
    remedy: str | None = None,
) -> None:
    """Refuse the whole run (exit 3, nothing written, nothing deleted) when
    any target this run intends to WRITE or UNLINK changed on disk after
    the plan was computed from it (issues #306, #313, #319, #329).

    Every caller shares one shape: Phase A reads a snapshot, computes the
    ENTIRE plan from it -- each document's new bytes, plus the new
    `log.md` text in all of them, the new `index.md` text in the title
    backfill, and for the delete verbs WHICH files to unlink -- and only
    then prompts. Nothing re-read anything at write time, so an edit
    landing while the prompt waited was overwritten IN FULL by
    `fsio.write_atomic` -- or, on a delete target, destroyed outright by
    `fsio.remove_file`, which is strictly worse since nothing survives to
    recover from -- with no error, no signal that a newer version existed,
    and an `_autocommit` that then committed the result. Every caller
    therefore invokes this strictly AFTER its confirm gate and strictly
    BEFORE its first write -- and unconditionally, outside the gate's own
    `if`, because `--auto` and `review: false` skip the prompt but not the
    window: nothing pauses for a human there, which makes those runs the
    likeliest to race a second writer, not the least.

    Every mutating verb now calls this (#313's rollout is complete). A
    reader arriving from one call site does not need the roster --
    `grep _reject_drifted_targets` is exact and never goes stale -- only
    the assurance that every caller satisfies the same contract below. An
    enumeration here would be a second place to forget to update, which is
    how a previous version of this paragraph came to claim three callers
    while five existed.

    `expected` maps the ABSOLUTE `Path` a verb will actually hand to
    `fsio.write_atomic` (or delete) to the RAW BYTES that path held when
    Phase A read it, and the comparison is bytes-to-bytes. `Path` keys, not
    workspace-relative strings, are the point (issue #325): a string key is
    a SECOND construction of the target's identity, rebuilt by
    interpolation at each call site, and it protects the write only while
    the two constructions happen to agree -- a drive-anchored concept-id or
    an absolute `rel` out of an unmerge ledger made them diverge, and the
    guard then validated a path the verb was not going to write. Passing
    the one `Path` object both phases share leaves nothing to diverge.
    The workspace-relative POSIX spelling still appears in the refusal
    message, derived here via `relative_to(layout.root)`; a key that
    escapes the workspace entirely (`relative_to` raises) is drift BY
    DEFINITION, named by its raw path and refused before any byte of it is
    read -- an out-of-tree target is one the operator was never shown, so
    even byte-identical content must fail closed, explicitly rather than
    by accident of an unreadable join as before.

    Both sides MUST come from the same single observation: every caller
    obtains its snapshot bytes from the `_snapshot_read` call whose decoded
    text fed the plan, which is precisely what closes the #318 window (two
    reads made an edit landing between them the guard's own baseline).
    That helper returning BYTES for this guard alongside the text is not
    convenience but correctness, in both directions: comparing decoded
    text to decoded text misses a CRLF-only rewrite landing during the
    prompt, because universal-newline translation makes it equal its own
    LF snapshot, while `fsio.write_atomic` (opening with `newline=""`)
    then writes the LF plan over it; comparing raw bytes to a translated
    snapshot is worse, because a file that was ALREADY CRLF at rest,
    untouched by anyone, compares unequal on every run, so the verb
    refuses forever with a message naming a cause that never happened and
    a re-run that cannot clear it. Bytes on both sides is the only pairing
    with neither failure.

    Drift is not one situation but THREE, and the refusal reports them
    separately because each demands a different next step (#319; flattening
    them into one "changed on disk" sentence sent operators in circles):

    - CHANGED: the bytes differ. The benign bucket -- a plain re-run
      recomputes the plan over the current state and succeeds, so the
      default advice is exactly that re-run.
    - VANISHED: the read raised `OSError` (deleted, or unreadable).
      Re-creating or overwriting a file whose current state the operator
      can no longer be shown is the same silent revert, so it still
      refuses -- and a vanished DELETE target is not the run's own intent
      honored early (#329): the run promised to unlink exactly the bytes
      the operator previewed, and a path someone ELSE removed no longer
      supports that claim any more than a changed one does. A plain re-run
      reads the same missing path and -- for the delete verbs -- fails in
      Phase A before any prompt, so the advice must say the path has to be
      restored first, and ONLY that: the old "or confirm the deletion is
      intended" clause was a dead end (R4 wave 4), since no re-run reaches
      a confirmation while the path stays missing. Advising a bare re-run
      here was the #319 loop.
    - OUT-OF-TREE: the key escapes the workspace (`relative_to` raises).
      Nothing "changed" and nothing "vanished" -- the same inputs produce
      this refusal on EVERY run, deterministically, so a re-run cannot
      clear it and the message says so (this is also the wave-2 R4 fix:
      the flattened sentence blamed an edit that never happened).

    `deletes` names the subset of `expected`'s keys the verb will UNLINK
    rather than write -- `forget`'s purge set, `purge`'s root-plus-cascade,
    `merge`'s absorbed file. The distinction is reporting, not detection:
    every bucket applies to both kinds, but "refusing to write" on a path
    the verb was about to DESTROY understates what the operator just
    avoided, so each path is labeled a "write target" or a "delete target"
    by what Phase B would actually have done to it, and the fail-closed
    footer extends to "nothing was deleted" exactly when the plan had a
    delete half to fail closed on. The function's NAME stays
    target-kind-neutral on purpose (#329): with `deletes` in the signature
    and both kinds named in the message, "drifted targets" already covers
    writes and unlinks alike, and a rename would churn every call site for
    no contract gain.

    `remedy` replaces the DEFAULT advice -- the changed bucket's re-run
    sentence -- when a verb's re-run is NOT a safe recovery: `unmerge`
    (#328), whose re-run would overwrite the very edit the guard just
    protected. Replacement is scoped to that one sentence, not wholesale
    (R3+R4 wave 5): the vanished and out-of-tree sentences are advisory
    FACTS about the refusal, not recovery advice a verb can substitute --
    a vanished target still has to be restored before anything proceeds,
    and an out-of-tree refusal is still deterministic -- so each is
    appended after whatever remedy is in effect whenever its bucket is
    non-empty. Under wholesale replacement, unmerge's copy-your-edit
    remedy talked about copying an edit that, for a vanished target, does
    not exist, and silently dropped the restore-first instruction.

    Exit code 3, and only here (#319): a drift refusal is the ONE failure a
    script may safely retry -- nothing was written, and when the message
    carries the re-run advice a retry genuinely recovers -- while every
    other failure keeps exit 1 and stays not-obviously-retryable. Scripts
    can now branch on `$? -eq 3` instead of parsing stderr; fail-closed
    semantics are unchanged (still non-zero, still before the first
    write). The retry contract is "safe WHEN the message says so": a
    vanished or out-of-tree refusal also exits 3, and its message is what
    tells the script's operator that a bare retry will not clear it.

    Refusal is whole-run, never per-path, because the plan is a unit. The
    title backfill's new `index.md` already encodes a relabel for every
    staged Source and its new `log.md` already names them, so skipping one
    drifted document would leave `index.md` asserting a relabel that never
    happened; `reconcile` shows the same thing on a smaller plan, where
    honouring one side of a symmetric pair while skipping the other leaves
    the two concepts disagreeing about their own resolution -- the one state
    its refuse-on-conflict gate exists to prevent. Neither case is special:
    every caller computes its plan as a whole from one snapshot, so a
    partial application asserts something that snapshot no longer supports.
    Recomputing after the prompt merely re-opens the same window.
    Refusing before the first write is the only fail-closed option, and it
    costs the operator one cheap re-run over fresh state.
    """
    changed: dict[bool, list[str]] = {False: [], True: []}
    vanished: dict[bool, list[str]] = {False: [], True: []}
    out_of_tree: dict[bool, list[str]] = {False: [], True: []}
    for path in expected:
        is_delete = path in deletes
        try:
            rel_path = path.relative_to(layout.root).as_posix()
        except ValueError:
            # Out-of-tree target: no workspace-relative spelling exists, so
            # the raw path is the entry, and no read is attempted -- see the
            # docstring for why matching bytes must not rescue it (#325).
            out_of_tree[is_delete].append(str(path))
            continue
        try:
            current = path.read_bytes()
        except OSError:
            vanished[is_delete].append(rel_path)
            continue
        if current != expected[path]:
            changed[is_delete].append(rel_path)
    if (
        not any(changed.values())
        and not any(vanished.values())
        and not any(out_of_tree.values())
    ):
        return

    # One clause per non-empty (bucket, kind) pair, bucket-major, writes
    # before deletes -- so every path is named under the verb's ACTUAL
    # intent for it and under the ACTUAL observation that refused it.
    clauses: list[str] = []
    bucket_specs = [
        (changed, "changed on disk after this run computed its plan"),
        (vanished, "vanished from disk (deleted or unreadable)"),
        (out_of_tree, "resolve outside the workspace"),
    ]
    for bucket, cause in bucket_specs:
        for is_delete in (False, True):
            paths = sorted(bucket[is_delete])
            if not paths:
                continue
            kind = "delete target(s)" if is_delete else "write target(s)"
            clauses.append(f"{len(paths)} {kind} {cause}: {', '.join(paths)}")

    # Deliberately NOT Phase B's "No path was written." sentence: that one
    # reports a write that already began, this one reports a run that never
    # started writing, and #234 pinned that two messages a bug report might
    # quote must never read alike. The delete half appears exactly when the
    # plan HAD a delete half (`deletes` non-empty) -- claiming "nothing was
    # deleted" for a verb that deletes nothing would be noise.
    footer = (
        "Nothing was written, nothing was deleted."
        if deletes
        else "Nothing was written."
    )

    # A custom `remedy` replaces only the DEFAULT re-run advice (the
    # changed bucket's); the vanished/out-of-tree sentences are advisory
    # facts about the refusal itself and follow whichever remedy is in
    # effect, each scoped to its own bucket ("the vanished target(s)",
    # "the out-of-tree refusal") so a mixed refusal reads as a checklist,
    # not a contradiction -- see the docstring (R3+R4 wave 5).
    advice: list[str] = []
    if remedy is not None:
        advice.append(remedy)
    elif any(changed.values()):
        advice.append("Re-run to recompute over the current bundle.")
    if any(vanished.values()):
        advice.append(
            "A plain re-run will refuse again on the vanished target(s): "
            "restore them first."
        )
    if any(out_of_tree.values()):
        advice.append(
            "The out-of-tree refusal is deterministic -- the same inputs "
            "reproduce it on every run, and a re-run cannot clear it."
        )
    remedy = " ".join(advice)

    typer.echo(
        f"openkos {verb}: refusing to write -- {'; '.join(clauses)}. {footer} {remedy}",
        err=True,
    )
    # Exit 3 is the drift-refusal contract (#319): the one failure code a
    # script may treat as retryable when the message says so. Everything
    # else in this module exits 1.
    raise typer.Exit(code=3)


def _require_member_baseline(
    verb: str, other_bytes: Mapping[str, bytes], member: str
) -> bytes:
    """A purge-set member's Phase-A snapshot bytes, or a clean exit-3
    refusal when the scan somehow produced none.

    DEFENSIVE-ONLY, deliberately: today `purge_ids` and `other_bytes` are
    built from the SAME bundle scan, and Phase A's own `member_texts`
    lookup would have crashed on the missing key long before the guard
    mapping is built -- so this branch cannot be reached end-to-end, and no
    integration test contorts the suite to pretend it can; the helper's
    unit tests pin the behavior directly instead. It exists because both
    callers used to index `other_bytes[f"{member}.md"]` bare, which a
    future refactor computing `purge_ids` from anything other than the
    scanned files would turn into a `KeyError` traceback in the middle of
    the post-confirm gate. A member with no same-observation baseline
    (#318) cannot be validated against drift, so the fail-closed answer is
    the guard's own shape: refuse the whole run (exit 3), name the member,
    write nothing. `_reject_drifted_targets`' contract is untouched --
    this refusal fires while its mapping is being BUILT, before the guard
    ever sees it.
    """
    baseline = other_bytes.get(f"{member}.md")
    if baseline is None:
        typer.echo(
            f"openkos {verb}: refusing to write -- 'bundle/{member}.md' is "
            "in the delete plan but has no Phase-A snapshot to validate "
            "against, so post-confirm drift on it cannot be ruled out. "
            "Nothing was written. Re-run to recompute over the current "
            "bundle.",
            err=True,
        )
        raise typer.Exit(code=3)
    return baseline


def _autocommit(root: Path, paths: Sequence[str], message: str) -> None:
    """Best-effort, non-fatal auto-commit after a mutating verb's Phase B
    (git-lifecycle Slice 2), structurally cloned from `init`'s own
    best-effort git-setup block below. Every mutating verb calls this
    exactly once, on the success path -- `grep _autocommit(` is exact and
    never goes stale, where the enumeration this sentence replaces had
    quietly stopped at six callers while thirteen existed -- strictly
    AFTER its own confirm gate and Phase-B writes
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


@app.command(
    help=(
        "Create a new OpenKOS workspace in the current directory, with its "
        "bundle layout, config file and local index stores."
    ),
    rich_help_panel="Get started",
)
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

    # State the stickiness BEFORE the embedding picker runs (#389), not as a
    # postscript once the answer is already on disk. This note used to land
    # after the choice AND below the call to action, which is after both
    # moments it exists to inform. The concrete note naming the resolved tag
    # still prints later; this one reaches the reader while they are deciding.
    stickiness_stated_at_the_picker = sys.stdin.isatty() and embedding_model is None
    if stickiness_stated_at_the_picker:
        typer.echo(
            "openkos init: note -- the embedding model you pick here is "
            "sticky: changing it in this workspace later forces a full "
            "corpus re-embed on the next `openkos reindex`.",
            err=True,
        )

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
    # ONE stickiness message per run (review finding on this change). Moving
    # the warning earlier is worthless if the reader then meets the same
    # sentence again a few lines down: when the picker already carried the
    # explanation, this line only confirms which tag it applies to.
    if stickiness_stated_at_the_picker:
        typer.echo(
            f"openkos init: the sticky embedding model is "
            f"'{resolved_embedding_model}'.",
            err=True,
        )
    else:
        typer.echo(
            f"openkos init: note -- the embedding model "
            f"('{resolved_embedding_model}') is sticky: editing it in this "
            "workspace's openkos.yaml later forces a full corpus re-embed the "
            "next time `openkos reindex` runs.",
            err=True,
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


def _format_group_tally(high: int, acronym: int, low: int) -> str:
    """Render the leading candidate-group tally line from per-tier counts,
    decoupled from `CandidateGroup` internals so callers pass primitive
    counts (spec: Reusable Group-Tally Formatting Helper).

    Returns `""` for all-zero counts, signaling "no line to print" to the
    caller. Otherwise returns
    `N candidate group(s) (X exact, Y acronym, Z near)`.

    ACRONYM is counted separately rather than folded into `near` (#397
    follow-up): it is a distinct match METHOD, and reporting a
    deterministic initials match as a fuzzy similarity score misdescribes
    the evidence a reader is about to adjudicate."""
    total = high + acronym + low
    if total == 0:
        return ""
    return (
        f"{total} candidate group{_plural(total)} "
        f"({high} exact, {acronym} acronym, {low} near)"
    )


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


class AdjudicationPayload(TypedDict):
    """The `adjudicate --json` envelope (issue #468 item 5).

    `results` was the WHOLE payload until #468: a bare JSON array. A partial
    batch (#441) emits the completed verdicts and reports the failure on
    stderr with exit 1, which means `openkos adjudicate --json > out.json`
    wrote a valid-looking but truncated array whose incompleteness lived
    ONLY in an exit code the redirect discarded. These three counters put
    that fact in the file itself.

    `adjudicated` and `total` describe the RUN -- groups the model answered
    for, and groups queued -- so they are deliberately NOT affected by
    `--same-only`, which filters `results` alone. Conflating them would
    report a complete run as truncated merely because the operator asked
    for a narrower view."""

    partial: bool
    adjudicated: int
    total: int
    results: list[dict[str, object]]


def _adjudication_payload(
    results: Sequence[AdjudicatedCandidate],
    *,
    same_only: bool,
    total: int,
    partial: bool,
) -> AdjudicationPayload:
    """Build the pure, I/O-free `adjudicate --json` payload from `results`,
    preserving `results` order and omitting `confidence` and any
    survivor/absorbed field (spec: Machine-Readable `--json` Output Mode).

    `tier` MUST be rendered via `.name` (uppercase `"HIGH"`/`"LOW"`), NOT
    `.value` (lowercase) -- mirrors the human path's ternary but sourced
    directly from the enum member's name. `verdict` mirrors the human path's
    `.value.upper()` rendering. `same_only=True` keeps only `Verdict.SAME`
    entries, the same predicate the human `--same-only` display filter uses.

    `total` is the count of candidate groups QUEUED and `partial` comes from
    `batch.failure is not None` -- both are the caller's to supply, because
    neither is recoverable from `results` alone: a batch that failed on its
    very first group and one that completed a single-group run produce the
    same `results` list (issue #468 item 5)."""
    return {
        "partial": partial,
        "adjudicated": len(results),
        "total": total,
        "results": [
            {
                "member_ids": list(result.candidate.member_ids),
                "okf_type": result.candidate.okf_type,
                "tier": result.candidate.tier.name,
                "verdict": result.verdict.value.upper(),
                "rationale": result.rationale,
            }
            for result in results
            if not same_only or result.verdict is Verdict.SAME
        ],
    }


def _render_adjudicate_report(
    root: Path, results: Sequence[AdjudicatedCandidate], *, same_only: bool
) -> None:
    """The human `adjudicate` report over `results`, byte-identical to the
    pre-#441 inline body -- extracted so the partial-batch failure epilogue
    can run AFTER every output mode instead of fighting this path's early
    returns."""
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
        tier_label = group.tier.name
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


def _echo_adjudicate_batch_failure(
    batch: AdjudicationBatch, *, total: int, model: str
) -> None:
    """One stderr line for a partial `AdjudicationBatch` (#441): the same
    3-tier cause-specific wording the raise-path handlers use, prefixed with
    how much paid-for work survived. The `isinstance` dispatch mirrors the
    handlers' ORDER for the same reason they are ordered: both specific
    classes subclass `OllamaError`, so the generic branch must come last or
    their actionable remediation is lost."""
    failure = batch.failure
    context = (
        f"openkos adjudicate: failed after adjudicating {len(batch.results)} "
        f"of {total} candidate group(s)"
    )
    if isinstance(failure, OllamaUnavailable):
        typer.echo(
            f"{context} -- {failure}. Start it with `ollama serve`, then try "
            f"again.{_DOCTOR_HINT}",
            err=True,
        )
    elif isinstance(failure, OllamaModelNotFound):
        typer.echo(
            f"{context} -- model '{model}' is not installed. Pull it with "
            f"`ollama pull {model}`, then try again.",
            err=True,
        )
    else:
        typer.echo(f"{context} -- {failure}.", err=True)


def _echo_suggest_relations_batch_failure(
    batch: EdgeSuggestionBatch, *, total: int, model: str
) -> None:
    """One stderr line for a partial `EdgeSuggestionBatch` (#441): the same
    3-tier cause-specific wording the raise-path handlers use, prefixed with
    how much paid-for work survived (mirrors
    `_echo_adjudicate_batch_failure`). The `isinstance` dispatch mirrors the
    handlers' ORDER for the same reason they are ordered: both specific
    classes subclass `OllamaError`, so the generic branch must come last or
    their actionable remediation is lost."""
    failure = batch.failure
    context = (
        f"openkos suggest-relations: failed after suggesting "
        f"{len(batch.results)} of {total} untyped edge(s)"
    )
    if isinstance(failure, OllamaUnavailable):
        typer.echo(
            f"{context} -- {failure}. Start it with `ollama serve`, then try "
            f"again.{_DOCTOR_HINT}",
            err=True,
        )
    elif isinstance(failure, OllamaModelNotFound):
        typer.echo(
            f"{context} -- model '{model}' is not installed. Pull it with "
            f"`ollama pull {model}`, then try again.",
            err=True,
        )
    else:
        typer.echo(f"{context} -- {failure}.", err=True)


def _echo_contradictions_batch_failure(
    batch: ContradictionBatch, *, total: int, model: str
) -> None:
    """One stderr line for a partial `ContradictionBatch` (#441): the same
    3-tier cause-specific wording the raise-path handlers use, prefixed with
    how much paid-for work survived (mirrors
    `_echo_adjudicate_batch_failure`). `total` is `plan.llm_calls` -- the
    judged-candidate budget the verb already holds, so the count needs no
    second planning pass. The `isinstance` dispatch mirrors the handlers'
    ORDER for the same reason they are ordered: both specific classes
    subclass `OllamaError`, so the generic branch must come last or their
    actionable remediation is lost."""
    failure = batch.failure
    context = (
        f"openkos contradictions: failed after judging {len(batch.results)} "
        f"of {total} candidate(s)"
    )
    if isinstance(failure, OllamaUnavailable):
        typer.echo(
            f"{context} -- {failure}. Start it with `ollama serve`, then try "
            f"again.{_DOCTOR_HINT}",
            err=True,
        )
    elif isinstance(failure, OllamaModelNotFound):
        typer.echo(
            f"{context} -- model '{model}' is not installed. Pull it with "
            f"`ollama pull {model}`, then try again.",
            err=True,
        )
    else:
        typer.echo(f"{context} -- {failure}.", err=True)


def _echo_suggest_volatility_batch_failure(
    batch: TierSuggestionBatch, *, model: str
) -> None:
    """One stderr line for a partial `TierSuggestionBatch` (#441): the same
    3-tier cause-specific wording the raise-path handlers use, prefixed with
    how much paid-for work survived (mirrors
    `_echo_adjudicate_batch_failure`). Unlike its three siblings, the count
    has no of-total: `suggest_volatility` derives its type queue INSIDE the
    leaf, so the verb holds no pre-flight total and fabricating one would
    cost a second full bundle walk for an error line. The `isinstance`
    dispatch mirrors the handlers' ORDER for the same reason they are
    ordered: both specific classes subclass `OllamaError`, so the generic
    branch must come last or their actionable remediation is lost."""
    failure = batch.failure
    context = (
        f"openkos suggest-volatility: failed after suggesting "
        f"{len(batch.results)} concept type(s)"
    )
    if isinstance(failure, OllamaUnavailable):
        typer.echo(
            f"{context} -- {failure}. Start it with `ollama serve`, then try "
            f"again.{_DOCTOR_HINT}",
            err=True,
        )
    elif isinstance(failure, OllamaModelNotFound):
        typer.echo(
            f"{context} -- model '{model}' is not installed. Pull it with "
            f"`ollama pull {model}`, then try again.",
            err=True,
        )
    else:
        typer.echo(f"{context} -- {failure}.", err=True)


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
    slice, Phase 1 refactor). Gains an optional stacked-body clause (issue
    #409, report half) only when `prepared.stacked_body` is non-`None` --
    a merge that stacks nothing says nothing extra here either."""
    stacked_note = ""
    if prepared.stacked_body is not None:
        stacked_note = (
            f", stacks {prepared.stacked_body.absorbed_chars} unreconciled "
            f"body char(s) ({prepared.stacked_body.share:.0%} of merged body)"
        )
    return (
        f"  merge {prepared.absorbed_canonical} into {prepared.survivor_canonical} "
        f"(sensitivity {prepared.sensitivity_before}->"
        f"{prepared.sensitivity_after}, {len(prepared.touched_files)} "
        f"rewrite(s), removes bundle/{prepared.absorbed_canonical}.md"
        f"{stacked_note})"
    )


def _echo_n_gt2_skip(group: "CandidateGroup") -> None:
    """The SAME-verdict N>2 skip report shared by `_run_adjudicate_apply`
    and `_run_adjudicate_apply_same` (issue #191) -- ONE helper so the two
    walks can never drift apart again.

    Keeps the pre-#191 skip line byte-identical, then prints the exact
    pairwise merge commands the operator would otherwise have to
    reconstruct by hand: the survivor is `group.member_ids[0]` (member ids
    are sorted ascending, so this matches the existing 2-member convention
    `survivor_id, absorbed_id = group.member_ids`), and each remaining
    member is absorbed into it with one `openkos merge <survivor>
    <absorbed>` line, in member order. Sequential pairwise merges into one
    survivor are safe to run in order because each individual merge is
    reversible via `unmerge` -- a mistake at step k never strands steps
    1..k-1 (issue #191). Print-only: counters and summary lines stay with
    the callers, byte-identical to the pre-#191 output."""
    typer.echo(f"[{group.okf_type}] {group.member_ids}: skipped (N>2, merge manually)")
    survivor_id = group.member_ids[0]
    typer.echo("  run in order (each reversible via unmerge):")
    for absorbed_id in group.member_ids[1:]:
        typer.echo(f"    openkos merge {survivor_id} {absorbed_id}")


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
            merge_result.ledger_sidecar_path,
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
    would fuse (D5), prompt `[y/N]` (D6, reshaped by issue #483), and on
    `y` execute `merge_core` + `_autocommit` (D7) -- reusing every 2b-i
    building block verbatim. A mid-run write failure (D8) stops the loop
    immediately; prior per-merge commits remain intact and reversible via
    `unmerge`. A final summary line (D9) always prints, even when nothing
    is eligible, followed by one `  declined: absorbed -> survivor` line
    per operator-declined merge (issue #483, mirroring #398's decline
    listing) so a typo-free decline set is revisitable.

    The prompt itself is `curate._confirm` -- the SAME validating helper
    curate's Identity stage routes this same merge decision through since
    PR #482 (issue #483, closing the #398 gap here): `y`/`yes` accepts,
    `n`/`no`/Enter declines, and any other answer re-prompts with a notice
    naming the accepted tokens instead of being silently counted as a
    decline. Reusing the private helper across the module boundary is
    deliberate, like `_type_label` in #479 -- one prompt contract, one
    source of truth (`main` already imports `curate_module` at module
    scope; the docstring warning in curate.py is about the OPPOSITE
    direction).

    Between the accepted `y` and the write sits the same TOCTOU window
    every drift-guarded verb closes (the #306/#313/#319 arc): every byte
    `_commit_one_merge` writes was computed by `_prepare_one_merge` BEFORE
    the `[y/N]` prompt, so an edit landing on any target while the
    prompt waited -- likeliest on the survivor, worst on the absorbed
    file, which is UNLINKED rather than overwritten -- would be silently
    destroyed. Each accepted pair therefore hands `_merge_drift_targets`'
    baseline mapping to `_reject_drifted_targets` strictly after its
    prompt and strictly before its write (issue #346, closing the gap
    `merge` and `curate`'s Identity stage already closed): drift refuses
    with exit 3, nothing is written for that pair, and the run ends --
    prior per-pair commits remain intact and reversible via `unmerge`,
    exactly like the D8 failure path."""
    applied = 0
    skipped_n_gt2 = 0
    skipped_already_merged = 0
    declined: list[str] = []

    for result in results:
        group = result.candidate
        if result.verdict is not Verdict.SAME:
            continue
        if len(group.member_ids) != 2:
            if len(group.member_ids) > 2:
                _echo_n_gt2_skip(group)
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
        # Issue #483: `curate._confirm` is the one validating per-item
        # write-consent prompt (#398 contract) -- private-helper reuse
        # across the boundary is deliberate, as with `_type_label` (#479).
        if not curate_module._confirm(
            f"Merge {prepared.absorbed_canonical} into "
            f"{prepared.survivor_canonical}? [y/N]"
        ):
            declined.append(
                f"{prepared.absorbed_canonical} -> {prepared.survivor_canonical}"
            )
            continue

        # Issue #346: every byte `_commit_one_merge` writes below was
        # computed before the prompt, so re-validate each target now --
        # after the accepted `y`, before the first write. The absorbed
        # file rides in `deletes=` because it is UNLINKED, not overwritten
        # (#329), mirroring `merge`'s own call site.
        absorbed_path = layout.bundle_dir / f"{prepared.absorbed_canonical}.md"
        _reject_drifted_targets(
            layout,
            _merge_drift_targets(layout, prepared),
            "adjudicate --apply",
            deletes=frozenset({absorbed_path}),
        )

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

    skipped_total = skipped_n_gt2 + skipped_already_merged + len(declined)
    prefix = "nothing to apply -- " if applied == 0 and skipped_total == 0 else ""
    typer.echo(
        f"openkos adjudicate --apply: {prefix}applied {applied}, skipped "
        f"{skipped_total} (N>2: {skipped_n_gt2}, "
        f"already-merged: {skipped_already_merged}, declined: {len(declined)})"
    )
    for item in declined:
        typer.echo(f"  declined: {item}")


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
    Semantics; 4R fix: mid-batch failure hides the applied count).

    Pass 2's re-prepare narrows the batch's TOCTOU window but does not
    close it (the #306/#313/#319 arc): an edit landing during the confirm
    gate or an earlier pair's commit IS re-read and recomputed over, but
    every byte `_commit_one_merge` writes for pair k was still captured by
    that pair's re-prepare BEFORE the write, so an edit landing in the
    re-prepare-to-write gap would be silently destroyed -- likeliest on
    the survivor, worst on the absorbed file, which is UNLINKED rather
    than overwritten. Each pair therefore hands `_merge_drift_targets`'
    baseline mapping to `_reject_drifted_targets` strictly between its
    re-prepare and its write (issue #346, closing the gap `merge` and
    `curate`'s Identity stage already closed). A drift refusal on pair k
    aborts the batch exactly like a mid-batch write failure: the partial
    summary (applied so far of total, remainder never attempted, applied
    merges committed and reversible via `unmerge`) is echoed before the
    guard's exit 3 propagates, so the refusal keeps the same recovery
    affordance the failure path already has."""
    eligible_groups: list[CandidateGroup] = []
    skipped_n_gt2 = 0
    for result in results:
        if result.verdict is not Verdict.SAME:
            continue
        group = result.candidate
        if len(group.member_ids) == 2:
            eligible_groups.append(group)
        elif len(group.member_ids) > 2:
            _echo_n_gt2_skip(group)
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

        # Issue #346: every byte `_commit_one_merge` writes below was
        # captured by this pair's re-prepare above, so re-validate each
        # target now -- after the baseline capture, before the first
        # write. The absorbed file rides in `deletes=` because it is
        # UNLINKED, not overwritten (#329), mirroring `merge`'s own call
        # site. The guard's exit 3 is re-raised unchanged; the wrapper
        # exists only to echo the same partial summary the mid-batch
        # failure paths echo, so a drift abort leaves the operator the
        # same recovery affordance.
        absorbed_path = layout.bundle_dir / f"{prepared.absorbed_canonical}.md"
        try:
            _reject_drifted_targets(
                layout,
                _merge_drift_targets(layout, prepared),
                "adjudicate --apply-same",
                deletes=frozenset({absorbed_path}),
            )
        except typer.Exit:
            typer.echo(
                "openkos adjudicate --apply-same: stopped after drift "
                f"refusal -- applied {applied} of {total} previewed before "
                "this refusal; the remaining pairs were not attempted. "
                "Applied merges remain committed and reversible via "
                "`unmerge`.",
                err=True,
            )
            raise

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


_SLUG_COLLAPSE_RE = re.compile(r"-+")


def _is_slug_char(char: str) -> bool:
    """Whether `_slugify` keeps `char` verbatim.

    The whitelist is Unicode letters and digits (`str.isalnum()`, i.e.
    categories `L*`/`N*`) plus combining marks (`M*`). Marks are included
    because in Indic, Hebrew and Arabic scripts a vowel sign or virama is
    part of its grapheme, not decoration: dropping `ि`/`्` from `हिन्दी`
    would corrupt the word rather than merely spell it differently.
    """
    return char.isalnum() or unicodedata.category(char).startswith("M")


def _slugify(stem: str) -> str:
    """Sanitize a filename stem OR a title into a slug: NFC, lowercase,
    Unicode letters/digits/marks kept, every other run -> `-`, trimmed.

    THE SLUG IS THE IDENTITY, not a filename detail: an object's Concept ID
    is its path within the bundle with `.md` removed (OKF §2,
    `docs/knowledge-object-model.md`), and there is no separate `id` field.
    So what this function throws away decides which objects exist at all.

    **Unicode-safe (issue #414).** The original `[^a-z0-9]+` sanitiser
    collapsed every non-ASCII character to a hyphen, which mangled accented
    Latin (`Diseño de Módulos` -> `dise-o-de-m-dulos`) and slugified a title
    in any non-Latin script (`知识图谱`, `Общая теория`, `こんにちは`) to the
    EMPTY string -- and an empty slug makes `_stage_derived_objects` skip
    the candidate, so every correctly extracted object from such a source
    was silently discarded. The whitelist is now Unicode-aware
    (`_is_slug_char`), so those titles keep their own script.

    **Transliteration was considered and rejected.** Romanising `知识图谱`
    to `zhi-shi-tu-pu` is a *choice* (which romanisation? whose tone
    marks?), not a fact about the title, and it would need a dependency to
    make that choice for the user. OpenKOS preserves representations rather
    than deciding them, so the slug carries the author's script.

    **NFC is normalised explicitly, in and out.** macOS filesystems
    normalise to NFD; without `unicodedata.normalize("NFC", ...)` the same
    title would produce different bytes -- and therefore a different Concept
    ID -- on different platforms. Identity has to be stable across
    filesystems, so both the input and the lowercased result are folded to
    NFC (lowercasing can itself denormalise, e.g. `İ` -> `i` + U+0307).

    **Path containment holds by construction**, which matters more now that
    the docstring's old guarantee ("callers always pass `Path(src).stem`")
    no longer holds -- `_stage_derived_objects` (LLM-extracted titles) and
    `_stage_filed_answer` (`query --save` titles) both pass unconstrained
    text. Nothing but a Unicode letter, digit, mark or `-` can reach the
    output, and no Unicode alphanumeric is a path separator on any supported
    platform, so `/`, `\\`, `.`, `..`, `:`, a null byte, a control character
    and the Windows-reserved set are all unreachable: `../../etc/passwd`
    slugifies to `etc-passwd`.

    **Backward compatible for ASCII, exactly.** For any pure-ASCII input the
    result is byte-for-byte what the old `[^a-z0-9]+` regex returned
    (`notes` -> `notes`, `My Notes` -> `my-notes`), because ASCII
    `str.isalnum()` is precisely `[a-zA-Z0-9]`. Existing bundles never
    silently rename.

    May still return `""` -- a title of only punctuation or emoji has no
    letters or digits to keep. That is a supported outcome, not an error:
    every caller (`ingest`, `_stage_derived_objects`, `_stage_filed_answer`)
    has its own empty-slug branch.
    """
    normalized = unicodedata.normalize("NFC", stem)
    sanitized = "".join(
        char.lower() if _is_slug_char(char) else "-" for char in normalized
    )
    return unicodedata.normalize(
        "NFC", _SLUG_COLLAPSE_RE.sub("-", sanitized).strip("-")
    )


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
    mechanics; #131).

    Both sides are NFC-normalized before matching (#414). `_slugify` emits
    NFC, but HFS+ (and some SMB mounts) rewrite a filename to NFD on write
    while APFS preserves whatever spelling it is handed, so `glob` can
    legitimately return the NFD stem of a file created under the NFC slug.
    Matching the raw stems would miss it -- while `derived_path.exists()`,
    which is normalization-INSENSITIVE on macOS, still reports True. The
    caller would then read an EMPTY family, misread the slug as belonging to
    a foreign source, and disambiguate to `<slug>-2` on every re-ingest
    until `write_exclusive` raised `FileExistsError`. Unreachable while
    slugs were ASCII (ASCII has no NFD form); reachable now that they carry
    accents.
    """
    if not link_dir.is_dir():
        return []
    base = unicodedata.normalize("NFC", base_slug)
    pattern = re.compile(rf"^{re.escape(base)}(?:-(\d+))?$")
    members: list[tuple[int, Path]] = []
    for path in link_dir.glob("*.md"):
        match = pattern.match(unicodedata.normalize("NFC", path.stem))
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


def _read_source_sensitivity(concept_path: Path, text: str) -> object:
    """Raw `sensitivity` from an EXISTING Source concept, unranked.

    Returns the raw frontmatter value (possibly missing, blank, non-string)
    for `okf.combine_sensitivity` to rank fail-closed per ADR-0003.
    Raises `ValueError` when the frontmatter cannot be parsed -- a
    re-ingest MUST NOT degrade an unreadable classification to the config
    default (design: "Where the on-disk read happens, and how it fails").
    This deliberately diverges from `_family_owns_source`'s
    degrade-and-continue pattern (`:1130`), which is a best-effort scan;
    this is a security field.

    Takes the already-decoded `text` rather than reading `concept_path`
    itself (#318): the caller snapshots the file exactly once via
    `_snapshot_read`, and this helper parsing that same observation is
    what keeps `_reject_drifted_targets`' baseline and the resolved
    sensitivity describing one on-disk state. `concept_path` is retained
    purely to name the file in the error. The read-failure translation
    that used to live here moved to the call site, next to the one read
    that can now fail."""
    try:
        metadata, _ = okf.load_frontmatter(text)
    except Exception as exc:
        # `frontmatter.loads` raises `yaml.YAMLError` on malformed YAML,
        # which is neither `OSError` nor `ValueError` -- translate rather
        # than degrade (design gotcha).
        raise ValueError(
            f"refusing to ingest -- '{concept_path}' frontmatter could not "
            "be parsed to resolve the sensitivity from its snapshot -- the "
            f"single read that also feeds the title parse and the drift "
            f"baseline: {exc}"
        ) from exc
    return metadata.get("sensitivity")


def _read_source_title(concept_path: Path, text: str) -> object:
    """Raw `title` from an EXISTING Source concept, read so a re-ingest's
    preview can name a title change instead of overwriting it silently
    (review finding on issue #248's content-derived title: the regenerate
    preview never mentioned that re-ingest recomputes and overwrites a
    pre-existing Source's title). Mirrors `_read_source_sensitivity`'s
    shape exactly -- same single-observation `text` parameter, same
    `load_frontmatter` call, same fail-closed `ValueError` on an
    unparseable file -- but for `title` rather than `sensitivity`. This
    does NOT make `title` sticky: the caller uses the return value only to
    decide what the preview SAYS, never to decide what gets WRITTEN --
    re-ingest still rebuilds `title` from content every run, unaffected by
    this read."""
    try:
        metadata, _ = okf.load_frontmatter(text)
    except Exception as exc:
        # `frontmatter.loads` raises `yaml.YAMLError` on malformed YAML,
        # which is neither `OSError` nor `ValueError` -- translate rather
        # than degrade (design gotcha), matching `_read_source_sensitivity`.
        raise ValueError(
            f"refusing to ingest -- '{concept_path}' frontmatter could not "
            f"be parsed to resolve its existing title: {exc}"
        ) from exc
    return metadata.get("title")


def _raw_collision_family(raw_dir: Path, name: str) -> list[Path]:
    """Every file in `raw_dir` belonging to `name`'s collision family --
    `<stem><ext>` itself and every `<stem>-N<ext>` (N a positive integer) --
    sorted ascending by `N`, the bare name first (#552).

    The raw-layer sibling of `_collision_family`, deliberately a separate
    function rather than a generalization of it. That one globs `*.md` and
    matches on the STEM alone, which is right for a bundle link dir where
    every file is a `.md` document and the stem IS the identity. `raw/`
    holds arbitrary user files, so the extension is part of the name and
    must be matched exactly: `notes.txt` and `notes.md` are two different
    basenames that never collided in `raw/` and must not start now.

    Anchored regex on the stem, never a glob, for `_collision_family`'s
    reason: an unrelated `<stem>-draft<ext>` must not join the family. Both
    sides NFC-normalized for the same macOS reason (#414) -- HFS+ rewrites a
    filename to NFD on write, so the on-disk spelling of a name openkos
    created in NFC can legitimately come back decomposed, and matching raw
    bytes would read an EMPTY family and disambiguate forever.
    """
    if not raw_dir.is_dir():
        return []
    named = PurePosixPath(name)
    base = unicodedata.normalize("NFC", named.stem)
    normalized_ext = unicodedata.normalize("NFC", named.suffix)
    pattern = re.compile(rf"^{re.escape(base)}(?:-(\d+))?$")
    members: list[tuple[int, Path]] = []
    for path in raw_dir.iterdir():
        if not path.is_file():
            continue
        member = PurePosixPath(path.name)
        if unicodedata.normalize("NFC", member.suffix) != normalized_ext:
            continue
        match = pattern.match(unicodedata.normalize("NFC", member.stem))
        if match is None:
            continue
        members.append((int(match.group(1)) if match.group(1) else 0, path))
    members.sort(key=lambda item: item[0])
    return [path for _, path in members]


def _first_free_raw_name(family: list[Path], name: str) -> str:
    """First free `<stem>-N<ext>` (N from 2) not already on disk in
    `family` -- `_first_free_disambiguated_slug`'s raw-layer sibling (#552),
    same ascending deterministic scan and the same NFC comparison."""
    named = PurePosixPath(name)
    stem, ext = named.stem, named.suffix
    taken = {unicodedata.normalize("NFC", path.name) for path in family}
    n = 2
    while unicodedata.normalize("NFC", f"{stem}-{n}{ext}") in taken:
        n += 1
    return f"{stem}-{n}{ext}"


def _raw_member_origin_key(bundle_dir: Path, member: Path) -> str | None:
    """The `origin_key` recorded by the Source owning raw file `member`, or
    `None` when it has none or cannot be read (#552).

    `None` means "unknown origin", never "no match": a Source written before
    `origin_key` existed, or one whose document is unreadable/malformed. The
    caller degrades that case to the byte comparison, which is exactly
    today's predicate -- so an unreadable neighbour can never escalate into
    a refusal or a spurious new copy. Mirrors `_family_owns_source`'s
    per-member parse tolerance.
    """
    slug = _slugify(Path(member.name).stem)
    if not slug:
        return None
    concept_path = okf.concept_path_for(f"sources/{slug}", bundle_dir)
    try:
        metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    except Exception:  # broad: malformed frontmatter degrades to unknown
        return None
    value = metadata.get(okf.ORIGIN_KEY_KEY)
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class _RawDestination:
    """Where this ingest's raw copy goes, and how it got there (#552)."""

    name: str
    regenerate: bool
    """Whether an existing raw copy was matched -- an idempotent re-ingest."""
    disambiguated_from: str | None
    """The basename that was already taken, `None` when none was."""


def _resolve_raw_destination(
    src: Path, layout: config.WorkspaceLayout, origin_key: str
) -> _RawDestination:
    """Resolve `src` to its `raw/` destination, disambiguating a basename
    already held by a DIFFERENT file (#552).

    `raw/` is a flat namespace derived from `Path(src).name` -- the
    path-traversal defence, which is correct and is not weakened here: every
    name this returns is still a bare basename under `raw/`. What changes is
    that a taken basename no longer forces one of two bad outcomes (refuse a
    legitimate file, or silently absorb it into the incumbent's Source).

    Identity is the ORIGIN, never the content. Two empty `__init__.py` files
    from two packages are two sources; the byte check passed on them, which
    is precisely how one silently inherited the other's provenance.

    The matrix, in family order:

    - a member whose recorded `origin_key` EQUALS `origin_key` is the same
      file -> re-ingest it (the caller still applies raw immutability to its
      bytes);
    - a member with a recorded but DIFFERENT `origin_key` is a different
      file -> keep scanning;
    - a member with NO recorded origin is a pre-#552 Source. Match it on
      identical bytes, which is exactly today's predicate, so a legacy
      workspace stays idempotent and backfills its key on this run.

    Nothing matched -> the first free `<stem>-N<ext>`.

    **Immutability is scoped to a file we KNOW is the same one.** That is
    the whole of the change, stated at its sharpest: a legacy member with
    differing bytes cannot be PROVEN to be the candidate, and #552 asks for
    disambiguation "when the basename exists with different content".
    Refusing there is the harm it was filed for -- real content turned away.
    No existing byte is ever rewritten either way, so the immutability
    guarantee itself is untouched; only the set of files it claims to cover
    is now the set it can actually identify.
    """
    family = _raw_collision_family(layout.raw_dir, src.name)
    if not family:
        return _RawDestination(src.name, False, None)
    src_bytes = src.read_bytes()
    for member in family:
        member_origin = _raw_member_origin_key(layout.bundle_dir, member)
        if member_origin is not None:
            if member_origin == origin_key:
                return _RawDestination(member.name, True, None)
            continue
        if member.read_bytes() == src_bytes:
            return _RawDestination(member.name, True, None)
    return _RawDestination(_first_free_raw_name(family, src.name), False, src.name)


def _first_free_disambiguated_slug(
    family: list[Path], base_slug: str, reserved: set[str]
) -> str:
    """First free `<base_slug>-N` (N starting at 2) that is neither already
    on disk (a stem present in `family`) nor already claimed by an earlier
    candidate in THIS batch (`reserved`) -- deterministic, ascending scan
    (design: Collision loop mechanics -- batch-local `seen_slugs` guard;
    #131).

    On-disk stems are NFC-normalized before the comparison, for the same
    reason `_collision_family` normalizes (#414): an NFD `<base>-2.md` must
    still count as taken, or this would hand back a name that already
    exists."""
    taken = {unicodedata.normalize("NFC", path.stem) for path in family} | reserved
    n = 2
    while f"{base_slug}-{n}" in taken:
        n += 1
    return f"{base_slug}-{n}"


_CAP_NOTICE_TITLE_LIMIT = 3
"""How many discarded titles the cap notice names before counting the rest.
A source that proposed 61 objects would otherwise dump 56 titles into the
terminal -- name enough to judge whether the loss mattered, then count."""


def _judge_failure_notice(report: ExtractionReport) -> str | None:
    """Render the union+judge failure-degrade notice (#456/#456), or `None`
    when the judge succeeded or was never invoked.

    Distinct wording from `_extraction_cap_notice` (a numeric cap firing)
    and from `_judge_selection_notice` (a successful judge selection) --
    spec: "a `_judge_failure_notice` distinct from `_judge_selection_notice`/
    `_extraction_cap_notice`". Fires on BOTH degrade statuses that keep the
    full (backstop-capped) merged union instead of filtering it:
    `"failed"` (the judge call raised, returned an empty reply, or an
    unparseable/wrong-shape reply) and `"empty"` (#456 gate finding: a
    valid-shaped reply whose admitted set was empty). Each status renders
    distinct wording so the two degrade causes stay tellable apart in the
    terminal."""
    if report.judge_status == "failed":
        reason = "judge selection unavailable"
    elif report.judge_status == "empty":
        reason = "judge selection admitted zero objects"
    else:
        return None
    return (
        f"{reason}; kept the full merged extraction "
        f"union ({report.retained} object(s)) unfiltered"
    )


def _pre_judge_ceiling_notice(report: ExtractionReport) -> str | None:
    """Render the pre-judge ceiling drop notice, or `None` when the
    24-candidate ceiling (`concept._MAX_JUDGE_CANDIDATES`) cut nothing.

    Distinct wording from `_extraction_cap_notice` (the FINAL backstop cap
    firing on what survived selection) and from both judge notices (what the
    judge did or failed to do): these candidates were cut BEFORE the judge
    ever saw them, so they were never judged, dropped, or cap-discarded --
    they simply never reached the judge."""
    if report.pre_judge_dropped <= 0:
        return None
    return (
        "merged extraction union exceeded the 24-candidate pre-judge "
        f"ceiling; {report.pre_judge_dropped} merged candidate(s) never "
        "reached the judge"
    )


def _reask_notice(report: ExtractionReport) -> str | None:
    """Render the bounded sole-twin re-ask notice (#584), or `None` when no
    re-ask was spent -- which is the common case.

    An extra model call is a cost the user pays, so it is reported rather
    than hidden, exactly like the pre-judge ceiling reports candidates the
    judge never saw. Distinct wording from every other notice here: nothing
    was dropped, judged, or capped -- a call was ADDED, and what it found
    was added with it.

    Both outcomes are surfaced, including "found nothing further": that is
    the answer the re-ask prompt names as correct for a genuinely
    single-subject source, and a spent call that changed nothing is still a
    spent call."""
    if report.reask_runs <= 0:
        return None
    if not report.reask_added_titles:
        return (
            "extraction returned one object restating the source title; "
            "1 extra re-ask call found nothing further"
        )
    shown = report.reask_added_titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(report.reask_added_titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return (
        "extraction returned one object restating the source title; "
        f"1 extra re-ask call added {len(report.reask_added_titles)} "
        f"object(s): {listed}"
    )


def _sole_object_notice(report: ExtractionReport) -> str | None:
    """Render the honest-degrade notice for a source whose SOLE derived
    object restates it (#585), or `None` -- the common case.

    Distinct from `_reask_notice`, and the two deliberately co-occur on the
    run that matters. That one reports a COST (an extra call was spent);
    this one reports an OUTCOME (what the bundle ended up storing). A
    re-ask that found nothing prints both, and neither is redundant: the
    user paid for a second question, and the answer was that there is
    genuinely nothing else here.

    It also fires without any re-ask at all -- on the union path where the
    judge reduced the set, or wherever `sole_object_restates_source`
    survived -- so it must not be folded into the re-ask notice's branch.

    Wording states BOTH halves of #585's chosen criterion, because a line
    that named only the problem would read as a warning about something the
    tool failed to do. Keeping the object is the decision, not a fallback:
    a genuinely single-subject source is indistinguishable from this defect
    by title alone, so dropping would emit `[]` for real content."""
    if not report.sole_object_restates_source:
        return None
    return (
        "the only derived object restates this source; keeping it and "
        "marking the Source (extraction_notice: "
        f"{okf.EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES})"
    )


def _judge_selection_notice(report: ExtractionReport) -> str | None:
    """Render the union+judge SUCCESSFUL-selection notice (#456), naming
    what the judge dropped, or `None` when the judge kept everything, was
    never invoked, or failed (handled by `_judge_failure_notice` instead).

    Mirrors `_extraction_cap_notice`'s shape (a count plus a bounded list of
    named titles) for the judge's OWN drop, distinct from the FINAL numeric
    cap: a judge-dropped title is never also a cap-discarded title, since
    `report.discarded_titles` is built from what SURVIVED judge selection
    (see `extract_concept_union`'s docstring)."""
    if report.judge_status != "ok" or not report.judged_out_titles:
        return None
    shown = report.judged_out_titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(report.judged_out_titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return f"judge dropped {len(report.judged_out_titles)} candidate(s): {listed}"


def _extraction_cap_notice(report: ExtractionReport) -> str | None:
    """Render the `_MAX_OBJECTS_PER_SOURCE` truncation notice, or `None` when
    the cap did not fire (#404).

    Mirrors `resolution.edge_typing.candidate_truncation_notice` -- the same
    `"{retained} of {produced} ... (cap reached)"` shape #378 established for
    candidate edges, so the two truncations in this product read alike -- and
    extends it with the discarded titles, because the measurement behind #404
    showed a bare count cannot tell a reader whether the cap cost them a real
    subject or trimmed a decayed tail of near-duplicates.

    Returns `None` on the healthy path rather than an empty string, so the
    caller renders on truncation alone with no special-casing, and an
    advisory never fires when there is nothing to advise.
    """
    if report.produced <= report.retained:
        return None
    shown = report.discarded_titles[:_CAP_NOTICE_TITLE_LIMIT]
    remainder = len(report.discarded_titles) - len(shown)
    listed = ", ".join(shown)
    if remainder > 0:
        listed = f"{listed} (+{remainder} more)"
    return (
        f"{report.retained} of {report.produced} extracted object(s) kept "
        f"(cap reached); discarded: {listed}"
    )


def _stale_index_names(
    layout: config.WorkspaceLayout, *, reads: tuple[str, ...]
) -> tuple[str, ...]:
    """The manifest-gated derived stores whose contents predate the bundle,
    named for a user-facing advisory (#381) -- shared by `query` and
    `status` (and mirrored by `next`'s `_BundleSignals.stale_indexes`) so
    all three agree on what "stale" means and on the wording of the names
    they print.

    `reads` (#436) declares which of the checked stores THIS caller's
    answer actually depends on, and the advisory names only that
    intersection. `query` stopped reading `graph.db` in #434, so graph
    staleness cannot degrade its answer and warning about it there was a
    true statement about the workspace attached to the wrong claim
    ("this answer may be degraded"). `status` and `next` describe the
    workspace itself, so they keep declaring both stores. Keeping the one
    shared helper -- with the caller's declaration as a parameter rather
    than a fork -- is the point: what "stale" means still lives in exactly
    one place. A name in `reads` outside the checked set is ignored.

    Only `fts.db` and `graph.db` are checkable, because only those two are
    gated by a whole-bundle `manifest_hash`. `vectors.db` is maintained
    per-document (`reindex` compares each doc's own `content_hash`), so an
    edited bundle leaves it PARTIALLY current rather than wholesale stale --
    which is exactly the asymmetry #381's evidence recorded, where dense
    retrieval still returned 9 hits while FTS and graph returned 0.

    Never raises: a failing advisory must not be what breaks the command it
    advises, so any error degrades to "nothing to report" rather than
    propagating. The cost is one bundle walk (~4ms over 29 docs), and
    `stale_derived_stores` skips even that when no declared store is on
    disk.
    """
    known = (("fts", layout.fts_db_path), ("graph", layout.graph_db_path))
    stores = tuple((name, path) for name, path in known if name in reads)
    if not stores:
        return ()
    try:
        return stale_derived_stores(layout.bundle_dir, stores)
    except Exception:  # broad: an advisory never breaks its own command
        return ()


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
    union_judge: bool = False,
) -> tuple[
    list[_DerivedPlan], okf.ExtractionStatus | None, okf.ExtractionNotice | None
]:
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

    Returns a `(plans, skip_reason, notice)` triple (issue #187, design:
    `_stage_derived_objects` return shape; extended by #585). `skip_reason`
    carries WHY this batch produced zero derived objects, for the caller to
    stamp onto the Source's `extraction_status` frontmatter key -- `None` on
    the healthy path (`plans` non-empty).

    `notice` (issue #585) is the mirror-image disclosure: `skip_reason`
    fires when extraction produced NOTHING, `notice` when it produced
    exactly one object that restates the source and so adds nothing the
    Source did not already say. The two are mutually exclusive by
    construction -- zero objects and exactly one object -- and travel as
    separate values rather than one field precisely because they answer
    different questions and are read by different consumers
    (`lint.check_unextracted` reads only the first).

    `notice` is derived from `ExtractionReport.sole_object_restates_source`,
    which describes what EXTRACTION produced, not what THIS run wrote. It is
    therefore returned even when the sole object is then dropped below as
    already-on-disk: on a re-ingest the object IS in the bundle, put there
    by the earlier run, and the Source's disclosure must not blink off
    merely because this run had nothing new to write. Returns `([], "no-extractable-text")` -- always
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

    `union_judge` (design D9, #456) selects which extraction orchestrator
    runs: `False` (this kwarg's own default -- a REQUIRED keyword, not
    defaulted from `config`, so every existing direct call site keeps
    exercising the untouched single-run path as a regression guard) calls
    `extraction.concept.extract_concept` exactly once; `True` calls
    `extract_concept_union`, which runs extraction twice (or once per chunk)
    and adds a selector-judge pass. The CLI's own `ingest` call site is the
    ONE place that injects `cfg.union_judge` explicitly, so the product-ON
    default lives in `config.DEFAULT_UNION_JUDGE` alone.
    """
    if raw_content is None or not raw_content.strip():
        typer.echo(
            "openkos ingest: source has no extractable text; keeping the Source only.",
            err=True,
        )
        return [], "no-extractable-text", None

    if not include_confidential and blocks_llm_send(workspace_floor):
        typer.echo(
            "openkos ingest: workspace default_sensitivity floor is confidential; "
            "skipping concept extraction, keeping the Source only. The Source "
            "is still added to the embedding index so search and candidate "
            "relations keep working -- the sensitivity floor governs "
            "`llm.chat`, not embeddings.",
            err=True,
        )
        return [], "blocked-by-sensitivity", None

    extractor = extract_concept_union if union_judge else extract_concept
    try:
        with Console(stderr=True).status("openkos ingest: extracting concepts…"):
            outcome = extractor(raw_content, source_title=source_title, llm=llm)
    except OllamaError as exc:
        typer.echo(
            f"openkos ingest: concept extraction skipped -- {exc}; "
            "keeping the Source only.",
            err=True,
        )
        return [], "failed", None

    extractions = outcome.objects
    # #584: the re-ask fires before the judge ever runs (it feeds the merged
    # candidate list), so its notice renders ahead of every other one --
    # the notices read in the order the pipeline produced them.
    reask_notice = _reask_notice(outcome.report)
    if reask_notice is not None:
        typer.echo(f"openkos ingest: {reask_notice}", err=True)

    # The pre-judge ceiling fires FIRST of all: it cut candidates before
    # the judge ever saw them, so it renders ahead of what the judge did.
    ceiling_notice = _pre_judge_ceiling_notice(outcome.report)
    if ceiling_notice is not None:
        typer.echo(f"openkos ingest: {ceiling_notice}", err=True)

    # #456: a judge notice fires next, distinct from the #404 cap notice --
    # `judge_status` is "skipped" on the single-run path, so both helpers
    # are no-ops there without needing an `if union_judge` guard here.
    judge_notice = _judge_failure_notice(outcome.report) or _judge_selection_notice(
        outcome.report
    )
    if judge_notice is not None:
        typer.echo(f"openkos ingest: {judge_notice}", err=True)

    # #404: the cap was the ONE drop in this function that said nothing --
    # empty slug, in-batch collision, existing file and failed build all
    # report per candidate below. A source proposing 20 objects and one
    # proposing 5 were indistinguishable in the output, since only the
    # truncated list ever reached this layer.
    cap_notice = _extraction_cap_notice(outcome.report)
    if cap_notice is not None:
        typer.echo(f"openkos ingest: {cap_notice}", err=True)

    # #585 renders LAST of the extraction notices: every other one reports
    # a step of the pipeline, and this one reports what the pipeline ended
    # up with. Read off the report rather than re-derived from `extractions`
    # here -- the predicate lives in `extraction/concept.py` beside the
    # re-ask trigger it shares, and a second spelling in this layer is
    # exactly the drift that helper exists to prevent.
    extraction_notice: okf.ExtractionNotice | None = (
        okf.EXTRACTION_NOTICE_SOLE_OBJECT_RESTATES
        if outcome.report.sole_object_restates_source
        else None
    )
    sole_object_notice = _sole_object_notice(outcome.report)
    if sole_object_notice is not None:
        typer.echo(f"openkos ingest: {sole_object_notice}", err=True)

    if not extractions:
        typer.echo(
            "openkos ingest: no concept extracted from this source; "
            "keeping the Source only.",
            err=True,
        )
        return [], "no-concepts-found", None

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
                type_alternative=extraction.type_alternative,
            )
        except ValueError as exc:
            typer.echo(
                f"openkos ingest: extracted content failed validation -- {exc}; "
                "skipping this candidate.",
                err=True,
            )
            continue

        if extraction.type_alternative is not None:
            # #401: the type is not cosmetic -- it decides the bundle
            # subdirectory, the `index.md` catalog section, and the default
            # volatility tier (`model/types.py` gives Event the `static`
            # tier and Project the `volatile` one). When the model reports
            # it was torn, saying so puts this choice on the same footing as
            # every other consequential call this function makes (empty
            # slug, in-batch collision, existing file, disambiguation,
            # failed build, the #404 cap) -- all of which report per
            # candidate. Recording the alternative does NOT resolve the
            # ambiguity; a genuinely ambiguous subject stays ambiguous. It
            # stops a coin flip from being filed as a settled fact.
            typer.echo(
                f"openkos ingest: '{extraction.title}' classified as "
                f"{extraction.type}, but the model also weighed "
                f"{extraction.type_alternative}; recorded as "
                f"{okf.TYPE_ALTERNATIVE_KEY} on the document.",
                err=True,
            )

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

    return plans, None, extraction_notice


def _resolve_local_exemption(client: OllamaClient, cfg: config.Config) -> bool:
    """The ONE place the confidential local exemption is decided (issue
    #240): `True` only when `client`'s own resolved host is verifiably this
    machine AND the workspace has not opted out.

    Both terms are required and neither is inferable from the other. The
    workspace key alone is a POLICY (`confidential_local_exemption`, default
    `true`); the client's `locality.is_local` is a verified FACT about the
    host this command's `llm.chat` will actually reach. An exemption granted
    on policy alone would rest on an assumption, which is precisely what
    #240 refuses.

    `client`, not `os.environ["OLLAMA_HOST"]`, because the two answer
    different questions: the env read ignores an explicit `host=` argument,
    so a client aimed at a remote host while `OLLAMA_HOST` happens to be
    loopback would be granted an exemption for a send that leaves the
    machine. Reading the client that will do the sending closes that by
    construction.

    Fails closed on every axis and never raises: `classify_backend_host`
    degrades an unparseable or unrecognized host to non-local, so unknown
    locality is treated as remote. The returned boolean is threaded into the
    five `llm.chat` seams as `local_exemption=`; what it MEANS there is
    `sensitivity.should_block`'s contract, never re-derived at a call
    site."""
    return client.locality.is_local and cfg.confidential_local_exemption


def _warn_if_nonlocal_embed_host(command: str, locality: BackendHostLocality) -> None:
    """One stderr advisory when the embedding host is not literally this
    machine (issue #199): document text and embedding vectors are about to
    be POSTed to it, and a user who exported `OLLAMA_HOST` for some other
    tool may not realize openkos inherits it.

    ADVISORY only, by contract: never blocks, never raises, never changes
    an exit code -- `classify_backend_host` is a pure literal check (no DNS,
    no network) that degrades unparseable values to "warn" instead of
    raising, and its `display_host` is userinfo-redacted on every path, so
    a credentialed value can never leak a password here (the withdrawn
    #183-PR3 predecessor's two CRITICALs). An unset/empty `OLLAMA_HOST`
    means Ollama's own local default: silent. Over-warning is the accepted
    failure direction; staying silent about data leaving the machine is
    not.

    `locality` is now PASSED IN rather than computed from `os.environ` here
    (issue #240): it comes from the embedding client's own
    `OllamaClient.locality`, i.e. the host the embed will actually POST to.
    The env read this replaced ignored an explicit `host=` argument, so it
    could stay silent about a client demonstrably sending off-machine. The
    advisory's own contract is untouched -- only the source of the fact it
    reports changed, and it is now the same authority the confidential local
    exemption reads, so the two can never disagree about one host."""
    if locality.is_local:
        return
    typer.echo(
        f"openkos {command}: note -- embedding host '{locality.display_host}' "
        "is not this machine (OLLAMA_HOST); document text and embedding "
        "vectors will leave this machine.",
        err=True,
    )


def _embed_after_ingest(
    layout: config.WorkspaceLayout,
    embedder: OllamaClient,
    *,
    model_tag: str,
    warn_nonlocal_host: bool = True,
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
    for a full-text rebuild it did not ask for.

    `warn_nonlocal_host=False` suppresses ONLY the non-local embedding-host
    advisory: `_ingest_batch` emits that advisory itself, once per batch
    invocation (the batch cost-gate precedent), so its per-file runs must
    not repeat it N times (issue #353, item 4). Everything else here --
    the embed, its fail-open degrade notices -- is unchanged either way."""
    # BEFORE the embed attempt, so the notice lands even when the embed
    # itself then degrades: the advisory is about where the data is headed,
    # not about whether it arrived (#199).
    if warn_nonlocal_host:
        _warn_if_nonlocal_embed_host("ingest", embedder.locality)
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


@dataclass(frozen=True)
class _SingleIngestOutcome:
    """What one `_ingest_single` run did, for `_ingest_batch`'s per-file
    outcome lines and aggregate tally (issue #267): `regenerated` is the
    run's own D1 flag (`True` on a byte-identical re-ingest, `False` on a
    fresh ingest), and `extraction_degraded` is `True` exactly when
    `_stage_derived_objects` returned a non-`None` `skip_reason` -- the
    same Source-only degrade taxonomy `docs/cli.md` documents
    (no-extractable-text / blocked-by-sensitivity / failed /
    no-concepts-found). A refusal never constructs this: `_ingest_single`
    raises `typer.Exit` before its single `return`."""

    regenerated: bool
    extraction_degraded: bool


_GLOB_MAGIC_CHARS = frozenset("*?[")
"""Glob magic characters (the character class `glob.has_magic` matches): a
`src` that is neither an existing file nor a directory but contains one of
these is treated as a quoted glob pattern, not a missing file (issue #267)."""


def _expand_batch_sources(src: Path) -> list[Path] | None:
    """Route `ingest`'s `src` argument (issue #267): return `None` when it
    must take the existing single-file path unchanged, or the SORTED list
    of matched files for the batch path.

    `None` covers two cases: an existing plain FILE (single-file behavior
    stays byte-identical -- checked FIRST, so even a filename containing a
    glob magic character keeps today's behavior when the file exists), and
    a nonexistent, magic-free path (which keeps today's exact "does not
    exist or is not a readable file" refusal).

    A DIRECTORY matches every readable file (`os.access(..., R_OK)`)
    directly inside it -- non-recursive, so subdirectories (`.git/`, nested
    workspaces, anything) are never walked into; recursion is available
    only via an explicit `**` glob. A path containing a glob magic
    character (`*`, `?`, `[`) is expanded with `glob.glob(...,
    recursive=True)` relative to the cwd, keeping only matched FILES (a
    bare `**` also matches directories; those are dropped).

    Both expansions sort by the path STRING (`key=str`), never filesystem
    order, so `log.md` entries and the per-file commits are reproducible
    across machines (settled decision 4). May raise `OSError` (an
    unreadable directory); the `ingest` command maps that to its usual
    stderr refusal."""
    if src.is_file():
        return None
    if src.is_dir():
        return sorted(
            (
                entry
                for entry in src.iterdir()
                if entry.is_file() and os.access(entry, os.R_OK)
            ),
            key=str,
        )
    if any(char in str(src) for char in _GLOB_MAGIC_CHARS):
        # `glob.glob`, deliberately not `Path.glob` (PTH207): pathlib
        # refuses an absolute pattern outright (`NotImplementedError`) and
        # rebases every relative match onto its base directory, where
        # `glob.glob` accepts both forms and echoes each match in the
        # pattern's OWN relative/absolute shape -- which is exactly what the
        # batch report and its outcome lines print back to the user.
        return sorted(
            (
                match_path
                for match in glob.glob(str(src), recursive=True)  # noqa: PTH207
                if (match_path := Path(match)).is_file()
            ),
            key=str,
        )
    return None


def _refuse_basename_collisions(matches: Sequence[Path]) -> None:
    """Batch Phase A (issue #267, settled decision 1): the destination name
    and slug derive ONLY from each file's basename -- the single-file
    path-traversal defense (`Path(src).name`), deliberately not weakened
    here -- so two matched files sharing a basename would fight over the
    same `raw/<name>`: the second would refuse against (or silently
    re-ingest) the first's freshly written copy. Detect this BEFORE ANY
    write and refuse the WHOLE run (exit 1), naming every colliding path;
    nothing is written."""
    by_name: dict[str, list[Path]] = {}
    for path in matches:
        by_name.setdefault(path.name, []).append(path)
    collisions = {name: group for name, group in by_name.items() if len(group) > 1}
    if not collisions:
        return
    for name, group in collisions.items():
        paths = " and ".join(f"'{path}'" for path in group)
        typer.echo(
            f"openkos ingest: refusing the whole batch -- basename collision: "
            f"{paths} would all land as 'raw/{name}' (destination names "
            "derive only from the basename); nothing was written. Rename "
            "one, or ingest them separately.",
            err=True,
        )
    raise typer.Exit(code=1)


def _ingest_batch(
    src: Path, matches: list[Path], *, auto: bool, include_confidential: bool
) -> None:
    """Drive every file in `matches` (already expanded and sorted by
    `_expand_batch_sources`) through the EXISTING single-file pipeline
    (`_ingest_single`) -- reuse, never reimplementation: the per-file
    ingestion, its per-ingest auto-commit (settled decision 3: PER-FILE
    commit granularity, so an interrupted run leaves a committed,
    consistent workspace and a re-run is idempotent for completed files),
    and its post-commit embedding all run unchanged, once per file, with
    `--include-confidential` forwarded unchanged (issue #267).

    Batch Phase A, before ANY write: an empty match set refuses (exit 1);
    the workspace check runs once up front (the same
    `config.require_workspace` refusal each per-file run would hit, but
    surfaced BEFORE the cost gate can prompt); then
    `_refuse_basename_collisions` refuses the whole run on a basename
    collision (settled decision 1).

    Cost gate (settled decision 4, the #134 pattern): before any LLM
    contact, `{n} file(s) -> {n} LLM call(s)` is printed to stderr and ONE
    up-front confirmation is asked, with the single-file gate's exact
    precedence mirrored -- `--auto` skips it, config `review: false` skips
    it the same way, a TTY asks (`abort=True`: decline exits 1, nothing
    written), and non-TTY stdin without `--auto` refuses to write. That
    single batch-level consent covers every file: each per-file run is
    invoked with the prompt suppressed the way `--auto` suppresses it
    today (`auto=True`), never 40 per-file prompts.

    The non-local embedding-host advisory (#199) follows the same
    once-per-run consolidation (issue #353, item 4): it is emitted here,
    once, up front (before the loop), and every per-file run is invoked
    with `warn_nonlocal_embed_host=False` so `_embed_after_ingest` does
    not repeat it N times. Same wording, same stderr, still advisory-only;
    a local (or unset) host stays silent exactly as before.

    Per-file failure isolation (settled decision 2): a per-file refusal
    (`typer.Exit` from `_ingest_single`, its reason already on stderr --
    including a drift refusal's exit 3) SKIPS that file and CONTINUES; a
    per-file extraction failure stays non-fatal exactly as today
    (Source-only degrade, stderr note) and is only TALLIED here. Progress
    is `i/N` on stderr via the TTY-gated `observability.progress_callback`
    (issue #190) -- silent when piped. The run ends with per-file outcome
    lines plus an aggregate summary on stdout -- outcome lines FIRST, the
    summary as the batch's last word (issue #349).

    Exit ladder (issue #349): 0 when every file succeeded (idempotent
    re-ingests count as success); 3 when EVERY skip was the per-file
    pipeline's drift refusal (exit 3, #319) -- nothing those files would
    have written was written, so the batch inherits the single-file retry
    guarantee a script relies on; 1 when ANY skip was a hard refusal --
    a plain re-run would refuse again, so the batch must not advertise
    retryability it cannot deliver (#234: distinct causes must not read
    alike)."""
    root = Path.cwd()
    if not matches:
        typer.echo(
            f"openkos ingest: refusing to ingest -- no files matched '{src}'; "
            "nothing was written.",
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
    _refuse_basename_collisions(matches)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos ingest: failed while preparing the batch -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    total = len(matches)
    if not auto and cfg.review:
        typer.echo(
            f"{total} file(s) -> {total} LLM call(s), one extraction per file "
            "(this can take a while). Pass --auto to skip this prompt.",
            err=True,
        )
        if sys.stdin.isatty():
            typer.confirm("Proceed?", abort=True)
        else:
            typer.echo(
                "openkos ingest: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    # ONE advisory for the whole batch (the cost-gate precedent), before
    # the loop: each per-file run below suppresses its own copy
    # (issue #353, item 4). The client is built here ONLY to read the host
    # it resolved (issue #240) -- construction performs no I/O, and every
    # per-file run builds its own identical client for the actual embed.
    _warn_if_nonlocal_embed_host(
        "ingest", OllamaClient(model=cfg.embedding_model).locality
    )

    progress = observability.progress_callback("ingest", "ingesting file")
    outcome_lines: list[str] = []
    ingested_count = 0
    reingested_count = 0
    degraded_count = 0
    skipped_count = 0
    hard_skip_count = 0
    for index, path in enumerate(matches, start=1):
        if progress is not None:
            progress(index, total, path)
        try:
            outcome = _ingest_single(
                path,
                auto=True,
                include_confidential=include_confidential,
                warn_nonlocal_embed_host=False,
            )
        except typer.Exit as exc:
            # The per-file pipeline already printed its own refusal reason
            # to stderr (unchanged single-file wording); this line only
            # records the skip in the batch report and moves on. Whether
            # the skip was a drift refusal (exit 3, the ONE retryable
            # failure, #319) or a hard refusal feeds the exit ladder below.
            skipped_count += 1
            if exc.exit_code != 3:
                hard_skip_count += 1
            outcome_lines.append(
                f"  ! {path} -- skipped (refused with exit code "
                f"{exc.exit_code}; its reason is on stderr above)"
            )
            continue
        if outcome.regenerated:
            reingested_count += 1
            marker, label = "~", "re-ingested"
        else:
            ingested_count += 1
            marker, label = "+", "ingested"
        suffix = ""
        if outcome.extraction_degraded:
            degraded_count += 1
            suffix = " (extraction degraded -- Source only; see stderr)"
        outcome_lines.append(f"  {marker} {path} -- {label}{suffix}")

    # Per-file outcome lines FIRST, the aggregate summary as the batch's
    # last word -- the order the docstrings and docs/cli.md promise
    # (issue #349).
    for line in outcome_lines:
        typer.echo(line)
    typer.echo(
        f"openkos ingest: batch summary -- {total} file(s): "
        f"{ingested_count} ingested, {reingested_count} re-ingested, "
        f"{skipped_count} skipped, {degraded_count} extraction-degraded."
    )
    if skipped_count:
        # Exit ladder (issue #349): every skip a drift refusal -> exit 3,
        # inheriting the single-file retry contract (#319); any hard
        # refusal in the mix -> exit 1, because a plain re-run would
        # refuse again.
        raise typer.Exit(code=1 if hard_skip_count else 3)


@app.command(
    help=(
        "Ingest a file, a directory, or a glob's matches into the bundle: "
        "extracts concepts, writes them as documents, and updates the "
        "catalog."
    ),
    rich_help_panel="Get started",
)
def ingest(
    src: Path = typer.Argument(
        ...,
        help=(
            "Path to a raw source file, a directory of source files, or a "
            "quoted glob pattern to copy into the workspace."
        ),
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
    """Ingest one source file, a whole directory, or a glob's matches into
    the workspace in a single invocation (issue #267).

    A plain existing FILE keeps the exact single-file behavior documented
    on `_ingest_single` -- copy into `raw/`, one OKF Source concept,
    bounded LLM extraction, one confirm gate, per-ingest auto-commit. A
    DIRECTORY ingests every readable file directly inside it
    (non-recursive; subdirectories are never walked into). A quoted GLOB
    (detected by its magic characters `*`, `?`, `[`; expanded relative to
    the cwd, recursion only via an explicit `**`) ingests every matched
    file. Matched files are SORTED by path string -- never filesystem
    order -- so `log.md` and the per-file commits are reproducible across
    machines.

    The batch drives each matched file through the SAME single-file
    pipeline, in order, each with its own per-ingest auto-commit
    (an interrupted run leaves every completed file committed; re-running
    is idempotent for them). Before any write: a basename collision
    between two matched files (destination names derive only from the
    basename -- the path-traversal defense) refuses the WHOLE run, exit 1,
    naming both paths. Before any LLM contact: one up-front cost gate
    prints `{n} file(s) -> {n} LLM call(s)` and asks ONCE -- that single
    consent covers every file, so the per-file prompt is suppressed the
    way `--auto` suppresses it today; `--auto` (or config `review: false`)
    skips the gate, and non-TTY stdin without `--auto` refuses to write,
    mirroring the single-file convention. A per-file refusal skips that
    file (reason on stderr) and CONTINUES; per-file outcome lines plus an
    aggregate summary (ingested / re-ingested / skipped /
    extraction-degraded) close the run, in that order. The batch exits 0
    when every file succeeded (re-ingests count as success), 3 when every
    skip was a drift refusal (the retryable failure, exit 3 per file),
    and 1 when any skip was a hard refusal (issue #349). An empty
    directory or a glob matching nothing refuses (exit 1, nothing
    written). See `_ingest_batch` for the full batch contract.
    """
    try:
        matches = _expand_batch_sources(src)
    except OSError as exc:
        typer.echo(
            f"openkos ingest: failed while expanding '{src}' -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    if matches is None:
        _ingest_single(src, auto=auto, include_confidential=include_confidential)
        return
    _ingest_batch(src, matches, auto=auto, include_confidential=include_confidential)


def _ingest_single(
    src: Path,
    *,
    auto: bool,
    include_confidential: bool,
    warn_nonlocal_embed_host: bool = True,
) -> _SingleIngestOutcome:
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

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads `index.md`, `log.md`, and the
    existing concept on a re-ingest, and refuses the WHOLE run (exit 3,
    nothing written) if any changed or vanished since Phase A read it
    (issues #306, #313, #319). The create-only writes below are excluded
    deliberately: `copy_exclusive`/`write_exclusive` already fail closed on
    a concurrent create, and a fresh ingest has no snapshot of a concept
    that did not exist. The two mechanisms tile the whole space by
    construction (#322): every target that EXISTED at Phase A is in the
    guard's mapping, and every target that did NOT exist -- including the
    concept on a post-`forget` regenerate -- is written create-only, so a
    file created at any write target during the prompt window is always
    refused, never silently overwritten.

    Phase B (after confirm) writes, in order: `bundle/sources/` (created if
    absent), the raw copy (`copy_exclusive`, create-only) and the concept
    document (`write_exclusive`, create-only) on a fresh ingest -- or, on a
    byte-identical re-ingest (D2), the raw copy step is SKIPPED entirely and
    the concept is written via non-exclusive `write_atomic` ONLY when it
    existed at Phase A (the drift guard holds its snapshot); a post-`forget`
    regenerate, whose concept was absent at Phase A, writes it create-only
    (`write_exclusive`) like a fresh ingest, since the guard has no bytes to
    defend it with (#322) -- then, for EACH staged derived object in staging
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

    This function IS the `ingest` command's original single-file body,
    extracted verbatim for issue #267 so `_ingest_batch` can reuse it
    unchanged, once per matched file -- the batch path wraps this, never
    modifies it. It returns a `_SingleIngestOutcome` (fresh vs re-ingest,
    and whether extraction degraded to Source-only) purely for the batch
    summary; the `ingest` command itself ignores the return value, so
    single-file behavior stays byte-identical. Every refusal still raises
    `typer.Exit` exactly as before -- the batch catches it to skip that
    file and continue.
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

        # #552: the destination is resolved against the whole collision
        # FAMILY under `raw/`, not against the bare basename alone -- a name
        # already held by a different file no longer refuses this one or
        # absorbs it into the incumbent's Source. Still a bare basename, so
        # the path-traversal containment is unchanged.
        origin_key = okf.origin_key_for(src)
        destination = _resolve_raw_destination(src, layout, origin_key)
        name = destination.name
        slug = _slugify(Path(name).stem)
        if not slug:
            raise ValueError(f"cannot derive a concept name from '{src}'")
        raw_dest = layout.raw_dir / name
        sources_dir = layout.bundle_dir / "sources"
        concept_path = sources_dir / f"{slug}.md"

        if destination.disambiguated_from is not None:
            # A destination the user did not name is never chosen silently.
            # Printed BEFORE the checks below so it frames any refusal that
            # follows, rather than being swallowed by the exit.
            typer.echo(
                f"openkos ingest: 'raw/{destination.disambiguated_from}' is "
                f"already held by a different source; copying this one to "
                f"'raw/{name}' instead.",
                err=True,
            )

        regenerate = destination.regenerate
        if regenerate:
            if src.read_bytes() != raw_dest.read_bytes():
                # Same file, changed bytes -> refuse (D4). Reachable now
                # only when the destination was MATCHED (by recorded origin,
                # or by a legacy member's identical bytes), so immutability
                # speaks about a file this run could identify -- never about
                # an unrelated neighbour that merely shared a basename.
                typer.echo(
                    f"openkos ingest: refusing to ingest -- '{src}' differs from "
                    f"the existing 'raw/{name}' copy; raw sources are "
                    "immutable. Ingest under a different name, or inspect the "
                    "existing copy.",
                    err=True,
                )
                raise typer.Exit(code=1)
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
            # ONE observation of the concept file, taken HERE and not with
            # `index.md`/`log.md` further down (#313 review, R4 CRITICAL;
            # single-read shape per #318). Between this point and there sits
            # `_stage_derived_objects`' `llm.chat` round trip -- an
            # unbounded network call. Snapshotting after it would make an
            # edit landing during extraction the guard's OWN baseline: the
            # comparison would find no drift and `write_atomic` would then
            # write back the document built from this text, reverting it.
            # That revert is a sensitivity DOWNGRADE, since
            # `resolved_sensitivity` is the high-water mark computed from
            # `on_disk_sensitivity`. Both parses below and the guard's
            # bytes derive from this single read, so there is no second
            # read for an edit to slip between.
            try:
                concept_snapshot: bytes | None
                concept_snapshot, concept_text = _snapshot_read(concept_path)
            except (OSError, UnicodeDecodeError) as exc:
                raise ValueError(
                    f"refusing to ingest -- '{concept_path}' could not be "
                    "read to snapshot its current contents (sensitivity, "
                    f"title, and drift baseline): {exc}"
                ) from exc
            on_disk_sensitivity = _read_source_sensitivity(concept_path, concept_text)
            resolved_sensitivity = okf.combine_sensitivity(
                on_disk_sensitivity, cfg.default_sensitivity
            )
            # Parsed BEFORE `title` is (re)computed below, purely so the
            # preview can NAME a retitle -- never to make `title` sticky
            # (review finding: re-ingest silently overwrote a pre-existing
            # Source's title with no mention in the preview). `title`
            # itself is still rebuilt from content every run, exactly as
            # before this read existed.
            on_disk_title = _read_source_title(concept_path, concept_text)
        else:
            on_disk_sensitivity = None
            resolved_sensitivity = cfg.default_sensitivity
            on_disk_title = None
            concept_snapshot = None

        def _build_source_document(
            extraction_status: okf.ExtractionStatus | None,
            extraction_notice: okf.ExtractionNotice | None = None,
        ) -> str:
            """Bound immediately before the first build (design: "The
            ordering conflict"). Builds the Source document from-scratch
            from this run's local inputs -- called once with `None`
            before staging (`:1717` today), and, ONLY when staging produces
            a `skip_reason` or a `notice`, called a SECOND time with it so
            the key is stamped onto freshly built content, never merged onto
            on-disk frontmatter. The healthy path calls this exactly once,
            so its output stays byte-identical to before these parameters
            existed.

            Both markers are rebuilt from scratch for THIS run alone, which
            is what makes them self-clearing (#187's anti-merge rule,
            inherited unchanged by #585): a re-ingest whose extraction now
            finds a second subject rebuilds without the notice, so a stale
            marker can never outlive the condition it described."""
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
                extraction_notice=extraction_notice,
                origin_key=origin_key,
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
        # ONE TTY-gated stage notice before the single long extraction call
        # (issue #190) -- `ingest` has no per-item loop to hook, so
        # `stage_notice` is the single-call sibling of `progress_callback`.
        # Printed even when the confidential-floor short-circuit inside
        # `_stage_derived_objects` skips the LLM: harmless on a TTY, and the
        # skip itself is reported right after.
        observability.stage_notice(
            "ingest", "extracting derived objects (waiting on the LLM)..."
        )
        derived_plans, skip_reason, extraction_notice = _stage_derived_objects(
            raw_content=raw_content,
            source_title=title,
            source_slug=slug,
            workspace_floor=cfg.default_sensitivity,
            stamp_sensitivity=source_sensitivity,
            timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            bundle_dir=layout.bundle_dir,
            llm=_chat_client(cfg, task="extraction"),
            include_confidential=include_confidential,
            union_judge=cfg.union_judge,
        )
        if skip_reason is not None or extraction_notice is not None:
            # Re-render from scratch with whichever marker was discovered
            # stamped in (design: "The ordering conflict", conditional
            # re-render) -- never patch the already-built bytes, and never
            # read either key off disk (unlike `sensitivity` above): both
            # are always recomputed fresh for THIS run alone.
            #
            # `skip_reason` and `extraction_notice` are mutually exclusive
            # by construction (zero objects vs exactly one), so this passes
            # both rather than branching: a hypothetical future pairing
            # would then reach disk and be visible, instead of one marker
            # silently winning here.
            concept_content = _build_source_document(skip_reason, extraction_notice)
        # One `_snapshot_read` observation per target: the decoded text
        # feeds the parsers below, the raw bytes feed
        # `_reject_drifted_targets` (issues #306, #313, #318).
        index_bytes, index_text = _snapshot_read(index_path)
        log_bytes, log_text = _snapshot_read(log_path)
        guarded_targets: dict[Path, bytes] = {
            index_path: index_bytes,
            log_path: log_bytes,
        }
        if concept_snapshot is not None:
            guarded_targets[concept_path] = concept_snapshot
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

    # Issue #313: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    _reject_drifted_targets(layout, guarded_targets, "ingest")

    try:
        sources_dir.mkdir(parents=True, exist_ok=True)
        if regenerate:
            # D2: raw copy SKIPPED -- raw/<name> is reused, never rewritten.
            # The concept's writer is chosen by the SAME `had_prior_source`
            # condition that gated its guard entry above, so the two
            # mechanisms are visibly complementary (#322): a concept that
            # EXISTED at Phase A has a snapshot in `guarded_targets` and is
            # written with `write_atomic` (create-only would ALWAYS fail
            # there); a concept ABSENT at Phase A (post-`forget`) left the
            # guard nothing to compare, so `write_exclusive` fails closed
            # on a file created during the prompt window instead of
            # silently overwriting it -- the same create-only protection,
            # surfacing the same `FileExistsError` through the same error
            # path, as the fresh-ingest branch below.
            if had_prior_source:
                fsio.write_atomic(concept_path, concept_content)
            else:
                fsio.write_exclusive(concept_path, concept_content)
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
        warn_nonlocal_host=warn_nonlocal_embed_host,
    )

    return _SingleIngestOutcome(
        regenerated=regenerate,
        extraction_degraded=skip_reason is not None,
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
    # `okf.concept_path_for`, not `bundle_dir / f"{canonical_id}.md"` (#430):
    # ids derived from a walked path are NFC-normalized, while the name on disk
    # may be decomposed -- a bundle authored on HFS+ and cloned onto a
    # byte-exact filesystem carries NFD filenames openkos never wrote. The
    # path-safety canonicalization above still runs FIRST and is untouched;
    # this only decides which SPELLING of an already-safe id is on disk, and
    # the `is_file` refusal below is still the one place absence is decided.
    concept_path = okf.concept_path_for(canonical_id, bundle_dir)
    if not concept_path.is_file():
        raise ValueError(f"concept '{concept_id}' does not exist")
    return concept_path, canonical_id


_ForgetScope = Literal["self", "source"]


@app.command(
    help=(
        "Delete a concept and its catalog entry, leaving the source "
        "material it came from in place."
    ),
    rich_help_panel="Remove",
)
def forget(
    concept_id: str = typer.Argument(
        ..., help="Bundle-relative concept id (path minus '.md') to remove."
    ),
    scope: _ForgetScope = typer.Option(
        "self",
        "--scope",
        help=(
            "'self' (default) removes only <concept_id>, byte-identical to "
            "a single-concept forget. 'source' expands the purge set "
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

    Past both gates -- and on the runs that skip gate 2, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every path this run intends to touch
    and refuses the WHOLE run (exit 3, nothing written, nothing unlinked)
    if any changed or vanished since Phase A read it (issues #306, #313,
    #319).

    That set includes the DELETE targets, not just `index.md` and `log.md`.
    The why lives in ONE place -- the comment on the
    `_reject_drifted_targets` call in the body (#320) -- in short: an edit
    landing on a purge-set member during the prompt would be destroyed
    outright rather than overwritten, and that member-side protection is
    all the guard delivers; referrer-side drift is out of its reach.

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
        # One `_snapshot_read` observation per target (issues #306, #313,
        # #318): each path is read exactly once, at the moment its decoded
        # text feeds the plan, and the guard's bytes come from that same
        # read -- there is no second read for an edit to slip between,
        # which is the window that let one land ahead of the guard's own
        # baseline in `ingest` (#313 review, R4).
        index_bytes, index_text = _snapshot_read(index_path)
        log_bytes, log_text = _snapshot_read(log_path)
        concept_bytes, concept_text = _snapshot_read(concept_path)

        # One whole-bundle snapshot, read ONCE, mirroring `merge`'s
        # `other_files` construction (~L1330-1337): every other `*.md`
        # file, reserved filenames and the ROOT's own file excluded. This
        # single snapshot feeds descendant resolution, inbound detection,
        # resurrection, and per-member titles/tombstones -- no extra
        # bundle scan, for either scope (design: Technical Approach).
        #
        # `other_bytes` shadows it for the guard -- and ONLY on `--scope
        # source` (#326). Which of these files the run will DELETE is not
        # known until `purge_ids` resolves below, so for that scope the
        # bytes come out of the same `_snapshot_read` observation as the
        # text, rather than re-read per member afterwards: a second read
        # would leave every file the #318 window. On the default `self`
        # scope the guard's member comprehension is empty by construction
        # (`purge_ids` is statically `[canonical_id]`), so not one byte
        # would ever be consulted -- retaining the whole bundle's raw bytes
        # there doubled Phase A's peak memory for nothing. The scope is
        # known before the scan starts, so gating retention on it opens no
        # new drift window: every file is still a single-read observation.
        other_files: dict[str, str] = {}
        other_bytes: dict[str, bytes] = {}
        for path in sorted(layout.bundle_dir.rglob("*.md")):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            if path == concept_path:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            raw, other_files[rel] = _snapshot_read(path)
            if scope == "source":
                other_bytes[rel] = raw

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

    # Issue #313: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    #
    # The DELETE targets are in here too, not just the two `write_atomic`
    # ones -- and this comment is the ONE copy of the why (#320: three
    # copies of this rationale each over-claimed). `forget` picks its purge
    # set from the Phase-A bundle snapshot and then unlinks those exact
    # paths, so an edit landing during the prompt is destroyed outright --
    # strictly worse than being overwritten, since nothing survives to
    # recover from -- and a `provenance:` edit on a member is drift in that
    # member's own claim to purge-set membership. Either alone justifies
    # guarding the delete targets. It is also ALL the guard delivers: it
    # re-reads only this mapping's paths, so an inbound reference gained
    # during the prompt -- which lives in a REFERRER file, by construction
    # outside the purge set, since the gate above drops intra-set referrers
    # -- is not caught, and neither is a brand-new `.md` file created
    # during the prompt: additive drift has no baseline here.
    _reject_drifted_targets(
        layout,
        {
            index_path: index_bytes,
            log_path: log_bytes,
            concept_path: concept_bytes,
            **{
                # Defensive fail-closed lookup (see `_require_member_baseline`):
                # today the key exists by construction, but a missing baseline
                # must refuse cleanly, never `KeyError` mid-gate.
                layout.bundle_dir / f"{member}.md": _require_member_baseline(
                    "forget", other_bytes, member
                )
                for member in purge_ids
                if member != canonical_id
            },
        },
        "forget",
        # #319: the purge-set members are UNLINKED below, not written --
        # `deletes` is what makes the refusal say so. Built the same way the
        # unlink loop builds its paths (`bundle_dir / f"{member}.md"`, with
        # `concept_path` standing in for the canonical root), so the labels
        # track Phase B by construction.
        deletes=frozenset(
            {concept_path}
            | {
                layout.bundle_dir / f"{member}.md"
                for member in purge_ids
                if member != canonical_id
            }
        ),
    )

    unlinked_count = 0
    ledger_touched: list[Path] = []
    decisions_touched: list[Path] = []
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
        # Merge-ledger sidecar privacy sweep (forget-command spec:
        # "Deletion Sweep Includes Ledger Storage"), same Phase B write:
        # a purge-set member's content must not survive `forget` merely
        # because it was previously absorbed into (or is the survivor of)
        # a merge.
        ledger_touched = _sweep_ledger_sidecars_for_ids(layout.bundle_dir, purge_ids)
        # Pending-work decision sweep (forget-command spec: "Forget Sweeps
        # Live Decision Entries Referencing The Purge Set"), same Phase B
        # write: a purge-set member's contradiction decision must not
        # survive `forget` merely because the record lives under a
        # different (live) concept's sidecar. `forget` performs no history
        # rewrite, so this call IS the entire sweep for it (unlike
        # `purge`, which also puts these paths into `expunge_targets`).
        decisions_touched = _sweep_decisions_for_ids(layout.bundle_dir, purge_ids)
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
            *(
                f"bundle/{p.relative_to(layout.bundle_dir).as_posix()}"
                for p in (*ledger_touched, *decisions_touched)
            ),
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
    Tombstone): physically DELETE `.openkos/{fts,vectors,graph,findings}.db`
    -- row-level `DELETE` would leave SQLite freelist-recoverable pages,
    which defeats the point of an erasure -- then best-effort rebuild FTS +
    graph ONLY (never the full `state.reindex.reindex`, which hard-depends
    on a running Ollama embedder `purge` must never require). `vectors.db`
    and `findings.db` are BOTH deliberately left deleted, never rebuilt
    in-line: `vectors.db` for the next `openkos reindex` to lazily
    re-embed, and `findings.db` because regenerating a contradiction
    finding costs LLM calls (pending-work design Decision 1's rebuild-
    posture table -- `findings.db` shares `vectors.db`'s posture, not
    `fts.db`'s).

    A rebuild failure here is reported but MUST NOT fail the (already
    irreversible, already-succeeded) purge -- the DELETE above is the
    security-critical erasure; the rebuild is a best-effort convenience over
    the survivors (design: Index cleanup decision)."""
    for db_path in (
        layout.fts_db_path,
        layout.vectors_db_path,
        layout.graph_db_path,
        layout.findings_db_path,
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


@app.command(
    help=(
        "Irreversibly expunge a concept AND the source material behind it. "
        "There is no undo."
    ),
    rich_help_panel="Remove",
)
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

    Past rail 6 -- reached without pausing when `--confirm-phrase` is
    given, so the check is unconditional -- `_reject_drifted_targets`
    re-reads `index.md`, `log.md`, and every purge-set member's bundle
    file, and refuses the WHOLE run (exit 3, nothing written, no history
    rewritten) if any changed or vanished since Phase A read it (issues
    #313, #319, #321). Rail 4 pinned the tree clean BEFORE the typed-phrase
    prompt -- the widest prompt window of any verb -- so without this an
    edit landing while the operator typed the phrase would be destroyed by
    the checkout of rewritten history, unrecoverably. The `raw/<name>`
    targets are deliberately not in the mapping: Phase A never reads their
    content, so they have no same-observation baseline (#318) and remain
    covered by rail 4 alone.

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

    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"

    try:
        # One `_snapshot_read` observation per target (issues #313, #318,
        # #321): each path is read exactly once, at the moment its decoded
        # text feeds the plan, and the guard's bytes come from that same
        # read. `index.md`/`log.md` are not plan inputs here -- their
        # post-rewrite cleanup re-reads them fresh -- but they ARE what
        # `git filter-repo`'s checkout clobbers, so their baselines are
        # captured with the same single-observation discipline.
        index_bytes, _ = _snapshot_read(index_path)
        log_bytes, _ = _snapshot_read(log_path)
        concept_bytes, concept_text = _snapshot_read(concept_path)

        # Same whole-bundle snapshot construction as `forget` (~L1006-1019).
        # `other_bytes` shadows it for the guard: which of these files the
        # run will EXPUNGE is not known until `purge_ids` resolves below, so
        # the bytes come out of the same `_snapshot_read` observation as the
        # text rather than re-read per member afterwards -- a second read
        # would leave every file the #318 window. Only the purge-set
        # entries are ever consulted; the rest are dropped.
        other_files: dict[str, str] = {}
        other_bytes: dict[str, bytes] = {}
        for path in sorted(layout.bundle_dir.rglob("*.md")):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            if path == concept_path:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            other_bytes[rel], other_files[rel] = _snapshot_read(path)

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
        # Whole-History Expunge Covers The Ledger Sidecar Store
        # (privacy-purge spec, task 3.4): every purge-set member's OWN
        # `bundle/.state/ledger/` sidecar (i.e. it is/was itself a merge
        # survivor) is expunged in this SAME `git filter-repo` pass -- no
        # second invocation. An absorbed-but-not-itself-a-survivor member
        # has no sidecar of its own; its historical body may still live as
        # an `absorbed_snapshot` fragment inside a DIFFERENT survivor's
        # sidecar, which stays a documented gap (see design's threat
        # matrix note) rather than a whole-file expunge target here.
        for member in sorted(purge_ids):
            member_sidecar = bundle_ledger.ledger_path_for(member, layout.bundle_dir)
            if member_sidecar.is_file():
                expunge_targets.append(
                    f"bundle/{member_sidecar.relative_to(layout.bundle_dir).as_posix()}"
                )
        # Whole-History Expunge Covers The Pending-Work Decision Subtree
        # (privacy-purge spec, B1.4): every `bundle/.state/decisions/**`
        # sidecar -- own OR foreign -- that references a purge-set member
        # is expunged in this SAME `git filter-repo` pass. Unlike the
        # ledger sidecar loop above, this covers FOREIGN sidecars too
        # (`_decisions_history_targets`'s own docstring explains why).
        expunge_targets.extend(_decisions_history_targets(layout.bundle_dir, purge_ids))
        # Threat matrix ("Shell / subprocess"): concept ids are user-
        # controlled, and a decisions path derived from one could contain
        # `==>` (git-filter-repo's rename delimiter) or another rejected
        # sequence -- validate the WHOLE `expunge_targets` list here, in
        # Phase A, so a malformed path refuses cleanly (this except
        # clause) rather than raising an uncaught `ValueError` from deep
        # inside `vcs_git.expunge_paths` after the point of no return.
        # `vcs_git.expunge_paths` re-validates this same list itself
        # (defense in depth, never trusted to be skipped), so this call
        # can never desync from what the real rewrite enforces.
        vcs_git._validate_rel_paths(expunge_targets)
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

    # Issue #321: rail 4 pinned the tree clean BEFORE the typed-phrase
    # prompt, and typing a whole sentence makes this the WIDEST prompt
    # window of any verb -- so an edit landing during it is invisible to
    # every rail, and `git filter-repo`'s history rewrite plus checkout
    # would destroy it outright, unrecoverably. Re-validate each target
    # now -- after the phrase gate (which `--confirm-phrase` reaches
    # without pausing, hence unconditionally), before the first write.
    #
    # The DELETE targets are in here alongside `index.md`/`log.md`: the
    # purge set is a claim about the Phase-A bundle, and a member edited
    # while the prompt waited is a state the operator was never shown.
    # The `raw/<name>` expunge targets are NOT: Phase A never reads their
    # content (they may legitimately be absent from the live tree), so
    # there is no same-observation baseline to compare -- they stay under
    # rail 4's clean-tree protection alone.
    _reject_drifted_targets(
        layout,
        {
            index_path: index_bytes,
            log_path: log_bytes,
            concept_path: concept_bytes,
            **{
                # Defensive fail-closed lookup (see `_require_member_baseline`):
                # today the key exists by construction, but a missing baseline
                # must refuse cleanly, never `KeyError` mid-gate.
                layout.bundle_dir / f"{member}.md": _require_member_baseline(
                    "purge", other_bytes, member
                )
                for member in purge_ids
                if member != canonical_id
            },
        },
        "purge",
        # #319: the root concept and every cascade member are expunged --
        # DELETE targets, and the refusal must name them as such. Only
        # `index.md`/`log.md` are writes here.
        deletes=frozenset(
            {concept_path}
            | {
                layout.bundle_dir / f"{member}.md"
                for member in purge_ids
                if member != canonical_id
            }
        ),
    )

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
        _sweep_ledger_sidecars_for_ids(layout.bundle_dir, purge_ids)
        _sweep_decisions_for_ids(layout.bundle_dir, purge_ids)
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
    # Whole-History Expunge Covers The Ledger Sidecar Store
    # (privacy-purge spec): each purge-set member's OWN sidecar was already
    # removed from the working tree by `expunge_paths`' filter-repo checkout
    # (it was in `expunge_targets` above); this is the LIVE-tree half --
    # dropping any OTHER survivor's sidecar entry whose `absorbed_id` is a
    # purge-set member, reusing the exact same primitive `forget`'s Phase B
    # calls, so the sweep is written exactly once.
    ledger_touched = _sweep_ledger_sidecars_for_ids(layout.bundle_dir, purge_ids)
    # Whole-History Expunge Covers The Pending-Work Decision Subtree
    # (privacy-purge spec): each purge-set member's OWN decisions sidecar,
    # and every FOREIGN sidecar referencing it, was already removed from
    # the working tree by `expunge_paths`' filter-repo checkout (both were
    # in `expunge_targets` above, via `_decisions_history_targets`); this
    # is the LIVE-tree half -- reconstructing any foreign sidecar's
    # surviving (unrelated) records, reusing the exact same primitive
    # `forget`'s Phase B calls, so the sweep is written exactly once.
    decisions_touched = _sweep_decisions_for_ids(layout.bundle_dir, purge_ids)
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
    commit_paths_rel = [
        "bundle/index.md",
        "bundle/log.md",
        *(
            f"bundle/{p.relative_to(layout.bundle_dir).as_posix()}"
            for p in (*ledger_touched, *decisions_touched)
        ),
    ]
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


@app.command(
    help=(
        "Write one typed relation between two concepts, exactly as given. "
        "No inference, no model call."
    ),
    rich_help_panel="Curate",
)
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

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every path this run intends to write
    (the source concept and `log.md`) and refuses the WHOLE run (exit 3,
    nothing written) if either changed or vanished since Phase A read it
    (issues #306, #313, #319). Any run, prompted or not, can therefore reach this
    point and still end without writing.

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
        prepared = prepare_relate(
            source_path,
            log_path,
            source_canonical,
            target_canonical,
            rel_type,
            root,
            now=now,
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos relate: failed while preparing the relate -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo("openkos relate: proposed changes:")
    if prepared.already_present:
        preview_line = (
            f"  ~ bundle/{prepared.source_canonical}.md (relations: "
            f"{prepared.existing_relations_count} -> "
            f"{prepared.updated_relations_count} entries; "
            f"unchanged: {{target: {prepared.target_canonical}, "
            f"type: {prepared.rel_type}}} already present)"
        )
    else:
        preview_line = (
            f"  ~ bundle/{prepared.source_canonical}.md (relations: "
            f"{prepared.existing_relations_count} -> "
            f"{prepared.updated_relations_count} entries; "
            f"+{{target: {prepared.target_canonical}, type: {prepared.rel_type}}})"
        )
    typer.echo(preview_line)
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if not auto and prepared.review:
        if sys.stdin.isatty():
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos relate: refusing to write without confirmation -- "
                "stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    # Issue #313: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    _reject_drifted_targets(
        layout,
        {
            source_path: prepared.source_bytes,
            log_path: prepared.log_bytes,
        },
        "relate",
    )

    try:
        relate_core(source_path, log_path, prepared)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos relate: failed while writing the relate -- {exc}.", err=True
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos relate: added a {prepared.rel_type!r} relation from "
        f"'bundle/{prepared.source_canonical}.md' to "
        f"'bundle/{prepared.target_canonical}.md' ({log_path.name} updated)."
    )

    _autocommit(
        root,
        [f"bundle/{prepared.source_canonical}.md", "bundle/log.md"],
        f"openkos: relate {prepared.source_canonical} -> "
        f"{prepared.target_canonical} ({prepared.rel_type})",
    )


@app.command(
    "set-sensitivity",
    help=(
        "Set one concept's sensitivity level directly, without a sweep or a "
        "model call. Scope is exactly the named concept and never its "
        "siblings -- except that raising a Source's level also raises every "
        "concept derived from it, and only ever upward."
    ),
    rich_help_panel="Curate",
)
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

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every path this run intends to write
    (the target concept, each staged descendant, and `log.md`) and refuses
    the WHOLE run (exit 3, nothing written) if any changed or vanished since
    Phase A read it (issues #306, #313, #319).

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
        # One `_snapshot_read` observation: the decoded text feeds the
        # parsers below, the raw bytes feed `_reject_drifted_targets`
        # (issues #306, #318).
        concept_bytes, concept_text = _snapshot_read(concept_path)
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
    bundle_snapshot: dict[str, str] = {}
    bundle_bytes: dict[str, bytes] = {}
    if metadata.get("type") == "Source" and direction == "raise":
        try:
            for path in sorted(layout.bundle_dir.rglob("*.md")):
                if path.name in okf.RESERVED_FILENAMES:
                    continue
                if path == concept_path:
                    continue
                rel = path.relative_to(layout.bundle_dir).as_posix()
                bundle_bytes[rel], bundle_snapshot[rel] = _snapshot_read(path)

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
        log_bytes, log_text = _snapshot_read(log_path)
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

    # Issue #306: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    _reject_drifted_targets(
        layout,
        {
            **{
                layout.bundle_dir / f"{descendant_raise.concept_id}.md": bundle_bytes[
                    f"{descendant_raise.concept_id}.md"
                ]
                for descendant_raise in descendant_raises
            },
            concept_path: concept_bytes,
            log_path: log_bytes,
        },
        "set-sensitivity",
    )

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


@app.command(
    "backfill-sensitivity",
    help=(
        "Raise sensitivity across the whole bundle where a concept sits "
        "below the level its source requires. Never lowers one."
    ),
    rich_help_panel="Maintain",
)
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

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every staged descendant plus `log.md`
    and refuses the WHOLE run (exit 3, nothing written) if any changed or
    vanished since Phase A read it (issues #306, #313, #319).

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
        bundle_bytes: dict[str, bytes] = {}
        for path in sorted(layout.bundle_dir.rglob("*.md")):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            bundle_bytes[rel], bundle_snapshot[rel] = _snapshot_read(path)

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
        log_bytes, log_text = _snapshot_read(log_path)
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

    # Issue #306: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    _reject_drifted_targets(
        layout,
        {
            **{
                layout.bundle_dir / f"{descendant_raise.concept_id}.md": bundle_bytes[
                    f"{descendant_raise.concept_id}.md"
                ]
                for descendant_raise in descendant_raises
            },
            log_path: log_bytes,
        },
        "backfill-sensitivity",
    )

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


@app.command(
    "normalize-names",
    help=(
        "Rename on-disk files and directories whose names are not in "
        "normalized Unicode form, so tools compare them consistently."
    ),
    rich_help_panel="Maintain",
)
def normalize_names_cmd(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Skip the confirmation prompt and write immediately (unattended).",
    ),
) -> None:
    """Rename every on-disk name (file OR directory) under `bundle_dir`
    that is not NFC to its NFC form -- the dedicated mutating verb that
    remediates what `lint`'s `non-nfc-name` finding only reports (issue
    #474 part 2). Structural twin of `backfill-sensitivity`
    (design D6): Phase A snapshot -> preview -> confirm gate -> drift
    re-check -> Phase B writes -> one `log.md` entry -> one `_autocommit`.

    Phase A obtains its candidate set from `lint_check.scan_non_nfc_entries`
    -- the SAME scan `openkos lint`'s `non-nfc-name` finding uses (design
    D1) -- plus `lint_check.scan_stranded_rename_temps`, whose result is
    printed as a stderr WARNING per stranded entry and never touched
    (design D3: a temp can be stranded by a hard kill between
    `fsio.rename_two_step`'s two hops or by a double fault in its guard
    or hop-2 branch where the suppressed restore also fails, PR #492 --
    its post-rename verification branch strands no temp, issue #495 --
    and auto-deleting or
    auto-renaming it would be data loss or a guess). Every candidate is
    classified as a planned rename or a non-fatal skip (collision: an
    NFC-spelled sibling already exists; symlink: never followed;
    vanished: the entry's parent listing became unreadable between the
    scan and classification) -- design D4/D5 -- then
    sorted `(-depth, rel_posix)` so a child renames before its ancestor.
    An empty or all-skip plan prints an explicit no-op line, writes
    nothing, and exits 0 -- which is also the idempotency property (a
    second run over an already-normalized bundle plans zero renames).

    The preview lists every planned rename (raw -> NFC target) and skip
    (with its reason) in apply order; a decomposed directory previews as
    ONE entry noting its subtree moves with it, never one line per
    descendant (design D4). The confirm gate mirrors
    `backfill_sensitivity_cmd`'s exact precedence: `--auto` skips it;
    otherwise config `review: false` skips it; otherwise a TTY prompts via
    `typer.confirm` and aborts (exit 1) on decline; otherwise (non-TTY, no
    `--auto`) this refuses to write.

    Immediately before Phase B, a PURPOSE-BUILT drift re-check
    re-validates every planned rename against current on-disk state
    (design D4): each entry's `raw_name` must still be present
    byte-exactly, its `nfc_name` must still be absent byte-exactly, and it
    must not have become a symlink. Any failure demotes that entry to a
    reported skip, never a crash. `log.md` alone goes through the
    existing `_reject_drifted_targets` (exit 3), matching every other
    mutating verb. If the drift re-check empties the plan, the run writes
    nothing, appends no log entry, and creates no commit.

    Phase B applies `fsio.rename_two_step` per entry in apply order, then
    appends exactly one dated `log.md` entry summarizing the run (design
    D6's bounded line: counts always; the renamed pairs are listed inline
    only when the total entry count is <= 5, so the line stays bounded
    and single -- `insert_log_entry` rejects newlines), then issues
    exactly one `_autocommit` scoped to every renamed entry's OLD and NEW
    path plus `log.md` (design D7) -- staging scope, not resulting diff:
    on a git configuration where the old and new spellings were already
    recorded identically (e.g. macOS `core.precomposeunicode=true`,
    design.md S1 Q7/Q8), staging both paths can legitimately produce no
    diff, and `log.md`'s entry keeps the commit non-empty regardless.
    `_autocommit` stays best-effort and non-fatal (not a repo, no git
    identity, or any `GitError` -- including the untracked-old-path case,
    Key Decisions Recorded a) -> stderr WARNING, exit code unchanged,
    renames already applied stay on disk. There is no cross-file rollback:
    a mid-Phase-B failure names every entry already landed by its OLD
    path only -- final paths are resolved strictly after the whole batch
    (review R3-001), so they do not exist yet at failure time --
    mirroring `backfill_sensitivity_cmd`'s
    design D9 pattern; any failure is caught (`OSError`/`ValueError`) and
    reported on stderr (exit 1), never a raw traceback.

    `index.md` is never touched (no concept id, `relations:` target,
    `provenance:` reference, or file body content changes -- design D6);
    no reindex is triggered (design D7).
    """
    root = Path.cwd()
    layout = config.WorkspaceLayout(root)
    log_path = layout.bundle_dir / "log.md"

    try:
        workspace_reason = config.require_workspace(root)
        if workspace_reason is not None:
            raise ValueError(workspace_reason)
        cfg = config.read_config(root)
        entries = lint_check.scan_non_nfc_entries(layout.bundle_dir)
        stranded_temps = lint_check.scan_stranded_rename_temps(layout.bundle_dir)
    except (OSError, ValueError) as exc:
        typer.echo(f"openkos normalize-names: refusing to run -- {exc}.", err=True)
        raise typer.Exit(code=1) from exc

    for stranded in stranded_temps:
        rel = stranded.relative_to(root).as_posix()
        typer.echo(
            f"openkos normalize-names: WARNING -- {rel!r} looks like a "
            "rename left stranded by an interrupted run (temp prefix "
            f"{fsio.RENAME_TEMP_PREFIX!r}); its original spelling is not "
            "recoverable from the temp name, so it is left untouched -- "
            "rename it by hand once you know what it should be.",
            err=True,
        )

    planned: list[lint_check.NonNfcEntry] = []
    skips: list[tuple[lint_check.NonNfcEntry, str, str]] = []
    for entry in entries:
        if entry.is_symlink:
            skips.append((entry, "symlink", "symlink"))
            continue
        try:
            sibling_listing = os.listdir(entry.path.parent)  # noqa: PTH208
        except OSError:
            skips.append((entry, "vanished", "vanished"))
            continue
        if entry.nfc_name in sibling_listing:
            skips.append((entry, "collision", f"{entry.nfc_name!r} already exists"))
            continue
        planned.append(entry)
    planned.sort(key=lambda entry: (-entry.depth, entry.rel_posix))

    if not planned:
        if skips:
            skip_kind_counts = Counter(kind for _entry, kind, _reason in skips)
            skip_detail = ", ".join(
                f"{kind}: {count}" for kind, count in sorted(skip_kind_counts.items())
            )
            typer.echo(
                "openkos normalize-names: nothing to normalize -- every "
                f"on-disk name under {layout.bundle_dir.name}/ is already "
                f"NFC ({len(skips)} skipped -- {skip_detail})."
            )
        else:
            typer.echo(
                "openkos normalize-names: nothing to normalize -- every "
                f"on-disk name under {layout.bundle_dir.name}/ is already NFC."
            )
        return

    confirm_enabled = not auto and cfg.review
    prompt_will_run = confirm_enabled and sys.stdin.isatty()

    typer.echo(
        f"openkos normalize-names: proposed renames ({len(planned)}, "
        f"deepest first), {len(skips)} skipped:"
    )
    for entry in planned:
        suffix = (
            "  (directory -- its whole subtree moves with it)" if entry.is_dir else ""
        )
        typer.echo(f"  ~ {entry.rel_posix!r} -> {entry.nfc_name!r}{suffix}")
    for entry, _kind, reason in skips:
        typer.echo(f"  ! {entry.rel_posix!r} -- skipped: {reason}")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

    if confirm_enabled:
        if prompt_will_run:
            typer.confirm("Proceed with these changes?", abort=True)
        else:
            typer.echo(
                "openkos normalize-names: refusing to write without "
                "confirmation -- stdin is not a TTY; re-run with --auto.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        log_bytes, log_text = _snapshot_read(log_path)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos normalize-names: failed while reading {log_path.name} -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    # Issue #306-style guard: `log.md` is re-validated against its
    # pre-prompt snapshot immediately before the first write.
    _reject_drifted_targets(layout, {log_path: log_bytes}, "normalize-names")

    # Purpose-built drift re-check (design D4), immediately before Phase
    # B: nothing here rewrites file BYTES, so `_reject_drifted_targets`'
    # bytes-comparison contract does not apply to the rename targets
    # themselves. Each planned entry is re-validated against current
    # on-disk state; any failure demotes it to a reported skip, never a
    # crash.
    final_renames: list[lint_check.NonNfcEntry] = []
    drift_skips: list[tuple[lint_check.NonNfcEntry, str, str]] = []
    for entry in planned:
        try:
            current_listing = os.listdir(entry.path.parent)  # noqa: PTH208
        except OSError:
            drift_skips.append((entry, "vanished", "vanished"))
            continue
        if entry.raw_name not in current_listing:
            drift_skips.append((entry, "vanished", "vanished"))
            continue
        if entry.nfc_name in current_listing:
            drift_skips.append(
                (entry, "collision", f"{entry.nfc_name!r} already exists")
            )
            continue
        try:
            drifted_to_symlink = entry.path.is_symlink()
        except OSError:
            drifted_to_symlink = False
        if drifted_to_symlink:
            drift_skips.append((entry, "symlink", "symlink"))
            continue
        final_renames.append(entry)

    if not final_renames:
        typer.echo(
            "openkos normalize-names: every planned rename drifted away "
            "before it could be applied -- nothing was written, no log "
            "entry was appended, and no commit was created."
        )
        return

    all_skips = skips + drift_skips
    pairs = ", ".join(
        f"{entry.rel_posix!r} -> {entry.nfc_name!r}" for entry in final_renames
    )
    skip_kind_counts = Counter(kind for _entry, kind, _reason in all_skips)
    skip_detail = ", ".join(
        f"{kind}: {count}" for kind, count in sorted(skip_kind_counts.items())
    )
    # Design D6's bounded log line: counts always; the renamed pairs are
    # listed inline only for a small batch (<= 5 total entries), so the
    # line stays single (`insert_log_entry` rejects newlines) and never
    # grows unbounded with the batch size (Key Decisions Recorded, b).
    total_entries = len(final_renames) + len(all_skips)
    if total_entries <= 5:
        log_line = (
            f"**Normalize-names**: Renamed {len(final_renames)} on-disk "
            f"name(s) to NFC: {pairs}. Skipped {len(all_skips)}"
            + (f" ({skip_detail})" if all_skips else "")
            + "."
        )
    else:
        log_line = (
            f"**Normalize-names**: Renamed {len(final_renames)} on-disk "
            f"name(s) to NFC. Skipped {len(all_skips)}"
            + (f" ({skip_detail})" if all_skips else "")
            + "."
        )
    try:
        new_log_text = bundle_log.insert_log_entry(
            log_text, datetime.now(UTC).astimezone().date(), log_line
        )
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos normalize-names: failed while preparing the "
            f"normalize-names -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    landed: list[str] = []
    applied_raw_rels: list[str] = []
    try:
        for entry in final_renames:
            # `entry.path` is the RAW spelling captured at Phase A/scan
            # time; `entry.rel_posix` is already NFC-normalized (design
            # D1), so it names the entry's NEW path, never its old one --
            # using it for `old_rel` would stage the wrong pathspec.
            old_rel = entry.path.relative_to(root).as_posix()
            fsio.rename_two_step(entry.path, entry.nfc_name)
            landed.append(old_rel)
            applied_raw_rels.append(old_rel)
        # New paths are resolved only AFTER the whole batch: the path
        # `rename_two_step` returns names the entry under its ancestors'
        # spellings AT RENAME TIME, and deepest-first means a later
        # ancestor rename carries the entry along, so that momentary
        # spelling goes stale before `_autocommit` ever sees it (review
        # R3-001). The final spelling normalizes exactly the segments
        # whose own rename APPLIED -- never a blanket NFC over the whole
        # path, because an ancestor skipped at drift time (collision)
        # keeps its raw spelling, and its NFC twin names the COLLIDING
        # sibling, not this entry.
        applied = set(applied_raw_rels)

        def _final_rel(raw_rel: str) -> str:
            parts = raw_rel.split("/")
            return "/".join(
                unicodedata.normalize("NFC", part)
                if "/".join(parts[: index + 1]) in applied
                else part
                for index, part in enumerate(parts)
            )

        # Strictly AFTER the write (issue #495): `landed` doubles as the
        # failure report, which promises OLD paths only, and as
        # `_autocommit`'s staging scope, which needs the final spellings
        # too. Extending before the write let a failure AT the write
        # report both spellings for the same entry.
        fsio.write_atomic(log_path, new_log_text)
        landed.extend(_final_rel(raw_rel) for raw_rel in applied_raw_rels)
        landed.append("bundle/log.md")
    except (OSError, ValueError) as exc:
        landed_suffix = (
            f"Already landed (left renamed, not rolled back): {', '.join(landed)}."
            if landed
            else "No path was written."
        )
        typer.echo(
            f"openkos normalize-names: failed while writing the "
            f"normalize-names -- {exc}. {landed_suffix}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos normalize-names: renamed {len(final_renames)} on-disk "
        f"name(s) ({log_path.name} updated): {pairs}."
    )

    _autocommit(root, landed, "openkos: normalize-names")


@app.command(
    "backfill-source-titles",
    help=(
        "Re-derive the title of every source-type concept from its own "
        "content, across the whole bundle."
    ),
    rich_help_panel="Maintain",
)
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
    printed -- closed by the `index.md`/`log.md` aggregate disclosure with
    the real relabel count (#308) -- then the confirm gate mirrors
    `backfill_sensitivity_cmd`'s precedence exactly: `--auto` /
    `review: false` / TTY `typer.confirm` / non-TTY refuse.

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads `index.md`, every staged Source, and
    `log.md`, and refuses the WHOLE run (exit 3, nothing written) if any
    changed or vanished since Phase A read it (issues #306, #313, #319).

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
        bundle_bytes: dict[str, bytes] = {}
        for path in sorted(layout.bundle_dir.rglob("*.md")):
            if path.name in okf.RESERVED_FILENAMES:
                continue
            rel = path.relative_to(layout.bundle_dir).as_posix()
            bundle_bytes[rel], bundle_snapshot[rel] = _snapshot_read(path)

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
        # Both originals come out of one `_snapshot_read` observation each:
        # the decoded text is what the rewrites are computed from, and the
        # raw bytes are what `_reject_drifted_targets` compares against
        # below (issues #306, #318).
        index_bytes, index_text = _snapshot_read(index_path)
        new_index_text = index_text
        relabeled_total = 0
        uncataloged: list[str] = []
        for retitle in backfill.staged:
            # The count is BOUND, not discarded (#308): zero matches means
            # this staged Source has no catalog bullet at all, so the
            # `index.md` write below is byte-identical for it -- claiming
            # "catalog updated" would be a lie the operator cannot see.
            new_index_text, relabel_count = bundle_index.relabel_index_entry(
                new_index_text, retitle.concept_id, retitle.new_title
            )
            relabeled_total += relabel_count
            if relabel_count == 0:
                uncataloged.append(retitle.concept_id)

        retitled = ", ".join(
            f"'bundle/{retitle.concept_id}.md' -> {retitle.new_title!r}"
            for retitle in backfill.staged
        )
        log_line = (
            f"**Backfill-source-titles**: Re-derived {len(backfill.staged)} "
            f"Source title(s) from their raw content: {retitled}."
        )
        log_bytes, log_text = _snapshot_read(log_path)
        new_log_text = bundle_log.insert_log_entry(
            log_text,
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
    # Deliberately NOT a `(warned: ...)` line (#308): those carry the closed
    # reason vocabulary of Sources the sweep will NOT touch, while these
    # Sources ARE retitled -- only their catalog bullet is missing, so the
    # relabel changes nothing for them.
    for concept_id in uncataloged:
        typer.echo(f"  ! bundle/{concept_id}.md (no index.md catalog entry to relabel)")
    # Aggregate disclosure (#308): the buckets above name the Sources, but
    # `index.md` and `log.md` are write targets of this run too -- every
    # sibling verb says so before its confirm gate, and the count keeps the
    # `index.md` line honest when some staged Source has no bullet.
    typer.echo(f"  ~ {index_path.name} ({relabeled_total} catalog label(s) relabeled)")
    typer.echo(f"  ~ {log_path.name} (new dated entry)")

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

    # Issue #306: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    _reject_drifted_targets(
        layout,
        {
            index_path: index_bytes,
            **{
                layout.bundle_dir / f"{retitle.concept_id}.md": bundle_bytes[
                    f"{retitle.concept_id}.md"
                ]
                for retitle in backfill.staged
            },
            log_path: log_bytes,
        },
        "backfill-source-titles",
    )

    landed: list[str] = []
    try:
        # `index.md` FIRST, then each staged Source, then `log.md` --
        # the OPPOSITE of `backfill-sensitivity`'s items-then-aggregate
        # order (design D6). The classifier keys on a Source document's own
        # `title`; once a document is written, a mid-sweep failure before
        # `index.md` lands would leave its bullet unrevisitable on re-run.
        # Index-first is the order a re-run repairs -- but only for the
        # index-versus-Source pair (#307): the `log.md` stage sits OUTSIDE
        # that guarantee, because once every Source is written, a re-run
        # classifies them all as curated and cannot re-stage the lost
        # entry (hence the dedicated failure report below). Do NOT "fix"
        # this order to match `backfill-sensitivity`.
        fsio.write_atomic(index_path, new_index_text)
        landed.append("bundle/index.md")
        for retitle in backfill.staged:
            source_path = f"bundle/{retitle.concept_id}.md"
            fsio.write_atomic(
                layout.bundle_dir / f"{retitle.concept_id}.md", retitle.content
            )
            landed.append(source_path)
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

    try:
        fsio.write_atomic(log_path, new_log_text)
        landed.append("bundle/log.md")
    except (OSError, ValueError) as exc:
        # #307: a failure HERE is not one more mid-sweep hole -- it is the
        # one the sweep cannot repair. Every retitle landed, so a re-run
        # classifies each Source as curated and short-circuits "nothing was
        # staged": silently clean, no log entry, no commit. The message
        # therefore states exactly what landed, that the dated entry is
        # gone for good, and that git holds the only record -- worded
        # apart from the mid-sweep message above per #234, because a bug
        # report quoting either must identify its phase unambiguously.
        typer.echo(
            f"openkos backfill-source-titles: failed while writing the "
            f"sweep's log entry -- {exc}. Landed and left in place: "
            f"{', '.join(landed)}. The dated log.md entry for this sweep "
            f"was NOT written and a re-run will NOT recreate it -- every "
            f"retitled Source now classifies as curated, so a re-run "
            f"reports nothing staged. Nothing was committed: inspect the "
            f"partial result with `git diff` and commit it manually.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos backfill-source-titles: retitled {len(backfill.staged)} "
        f"Source(s) ({index_path.name}: {relabeled_total} catalog label(s) "
        f"relabeled, {log_path.name} updated): {retitled}."
    )

    _autocommit(root, landed, "openkos: backfill-source-titles")


@app.command(
    "set-volatility",
    help=(
        "Set the freshness window for one kind of concept, recorded in the "
        "workspace config."
    ),
    rich_help_panel="Curate",
)
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

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads `openkos.yaml` and refuses (exit 3,
    nothing written) if it changed or vanished since the plan was rendered
    from it (issues #313, #319, #335).

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
        # `read_config` above parsed a separate read, but the plan is
        # computed from `prepare_set_volatility`'s own `_snapshot_read`, so
        # the guard's baseline sits beside it (issues #313, #318, #335).
        prepared = prepare_set_volatility(layout.config_path, concept_type, tier)
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

    # Issue #335: `new_config_text` is the ENTIRE file, rendered from a
    # pre-prompt read, so an edit landing while the prompt waited --
    # possibly a safety setting like `review:` or `default_sensitivity:` --
    # would be silently reverted by the whole-file write below. Re-validate
    # the one target now -- after the gate, before the write.
    _reject_drifted_targets(
        layout, {layout.config_path: prepared.config_bytes}, "set-volatility"
    )

    try:
        set_volatility_core(layout.config_path, prepared)
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
class StackedBodyReport:
    """Body-stacking signal for one merge (issue #409, report half):
    `okf.build_merged_document` unconditionally appends the absorbed body
    under a `## Merged content (<absorbed-id>)` heading without comparing
    it against the survivor's body -- this report says the merge DID that,
    since nothing else does.

    A bare "bodies were stacked" boolean would fire on essentially every
    merge (an absorbed body is normally non-empty) and add pure noise, so
    the signal instead carries magnitude: `absorbed_chars` is how much
    unreconciled content the absorbed side contributed, and `share` is
    what fraction of the resulting merged body that now is -- a stacked
    sentence and a stacked essay are materially different things to flag
    for a human. `PreparedMerge.stacked_body` is `None`, printed as
    nothing, when the absorbed body carries no reconcilable content
    (empty or whitespace-only) -- matching the same "print nothing on the
    empty case" discipline `dropped_self_loops` / `deduped_collisions`
    already follow, rather than reporting a report about nothing.

    This does NOT detect disagreement between the two bodies -- that is
    the intra-document contradiction-detection half of #409, a separate,
    larger change. This is purely "the merge stacked N chars of
    unreconciled content"."""

    absorbed_chars: int
    merged_chars: int

    @property
    def share(self) -> float:
        """Fraction of the merged body's chars contributed by the absorbed
        side, unreconciled. `merged_chars` is never zero when this report
        exists (a non-empty absorbed body was appended to the merged
        body), so this never divides by zero."""
        return self.absorbed_chars / self.merged_chars


@dataclass(frozen=True)
class PreparedMerge:
    """Pure Phase-A result of `prepare_merge`: everything `merge`'s preview,
    confirm gate, and `merge_core` need, built in memory without writing
    anything (design: merge-core Extraction, Slice 2b-i). `review` carries
    `cfg.review`, consumed only by the command's confirm gate -- `prepare_merge`
    itself never prompts.

    The `*_bytes` fields are the drift guard's baselines (issue #334): the
    raw bytes each write/delete target held at the SAME `_snapshot_read`
    observation whose decoded text fed the plan (#318), which the command
    hands to `_reject_drifted_targets` after its confirm gate.
    `touched_bytes` is scoped to `touched_files` -- the rest of the
    whole-bundle scan feeds rewrite detection only and is never written, so
    it never becomes a guard target."""

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
    stacked_body: StackedBodyReport | None
    sensitivity_before: str
    sensitivity_after: str
    review: bool
    now: datetime
    index_bytes: bytes
    log_bytes: bytes
    survivor_bytes: bytes
    absorbed_bytes: bytes
    touched_bytes: dict[str, bytes]


@dataclass(frozen=True)
class MergeResult:
    """Pure Phase-B result of `merge_core`: what got written, for the
    command's success echo and `_autocommit` path list. `merge_core` itself
    performs NO VCS side effect (design decision: `_autocommit` stays in the
    command). `ledger_sidecar_path` (durable-derived-state slice 1a) is the
    workspace-relative `bundle/.state/ledger/**` path callers MUST add to
    their own `_autocommit` path list, or the ledger silently never enters
    git (threat matrix, design's "portability rationale")."""

    survivor_canonical: str
    absorbed_canonical: str
    touched_files: list[str]
    committed_paths: list[str]
    ledger_sidecar_path: str


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
    nothing to disk.

    Every plan-feeding read goes through `_snapshot_read`, capturing the
    raw bytes BESIDE the decoded text -- one observation per target, never
    a second batched read (issue #318) -- so the returned `PreparedMerge`
    can carry the drift guard's baselines for the command to check after
    its confirm gate (issue #334)."""
    cfg = config.read_config(root)
    # One `_snapshot_read` observation per target (issues #313, #318, #334):
    # each path is read exactly once, at the moment its decoded text feeds
    # the plan, and the guard's bytes come from that same read -- there is
    # no second read for an edit to slip between.
    survivor_bytes, survivor_text = _snapshot_read(survivor_path)
    absorbed_bytes, absorbed_text = _snapshot_read(absorbed_path)
    index_bytes, index_text = _snapshot_read(index_path)
    log_bytes, log_text = _snapshot_read(log_path)

    # `other_bytes` shadows the whole-bundle snapshot for the guard. Which
    # of these files the run will WRITE is not known until the three rewrite
    # scans below resolve `touched_files`, so the bytes come out of the same
    # `_snapshot_read` observation as the text rather than re-read per
    # touched file afterwards -- a second read would leave every file the
    # #318 window. Only the touched entries reach the guard mapping; the
    # rest feed rewrite DETECTION only and are never written, so they are
    # not guard targets (mirrors `unmerge`'s scoping).
    other_files: dict[str, str] = {}
    other_bytes: dict[str, bytes] = {}
    for path in sorted(bundle_dir.rglob("*.md")):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        if path in (survivor_path, absorbed_path):
            continue
        rel = path.relative_to(bundle_dir).as_posix()
        other_bytes[rel], other_files[rel] = _snapshot_read(path)

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

    # Durable-derived-state slice 1a: the survivor's existing ledger entries
    # now live in a sidecar (`bundle/ledger.py`), never the survivor's own
    # frontmatter -- `plan_merge` no longer decodes them from `survivor_text`.
    existing_entries = bundle_ledger.read_entries(survivor_canonical, bundle_dir)

    plan = bundle_merge.plan_merge(
        survivor_id=survivor_canonical,
        absorbed_id=absorbed_canonical,
        survivor_text=survivor_text,
        absorbed_text=absorbed_text,
        index_text=index_text,
        log_text=log_text,
        merged_at=now.isoformat(),
        existing_entries=existing_entries,
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
    absorbed_metadata, absorbed_body = okf.load_frontmatter(absorbed_text)
    _, dropped_self_loops, deduped_collisions = okf.merge_relations(
        okf.decode_relations(survivor_metadata),
        okf.decode_relations(absorbed_metadata),
        survivor_id=survivor_canonical,
        absorbed_id=absorbed_canonical,
    )

    # Body-stacking report (issue #409, report half): `build_merged_document`
    # stays pure and returns bytes only (design decision -- see
    # `StackedBodyReport`'s docstring), so this recomputes the signal from
    # `plan.merged_survivor` -- the SAME bytes `merge_core` will write --
    # the same way the outbound relations report above is recomputed rather
    # than threaded through `MergePlan`. `None` when there is nothing to
    # report: an empty/whitespace-only absorbed body contributes no
    # unreconciled content, so the "print nothing on empty" discipline
    # applies here exactly as it does to `dropped_self_loops` /
    # `deduped_collisions`.
    stripped_absorbed_body = absorbed_body.strip()
    stacked_body: StackedBodyReport | None = None
    if stripped_absorbed_body:
        _, merged_body = okf.load_frontmatter(plan.merged_survivor)
        stacked_body = StackedBodyReport(
            absorbed_chars=len(stripped_absorbed_body),
            merged_chars=len(merged_body),
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
    touched_bytes = {rel: other_bytes[rel] for rel in touched_files}
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
        stacked_body=stacked_body,
        sensitivity_before=sensitivity_before,
        sensitivity_after=sensitivity_after,
        review=cfg.review,
        now=now,
        index_bytes=index_bytes,
        log_bytes=log_bytes,
        survivor_bytes=survivor_bytes,
        absorbed_bytes=absorbed_bytes,
        touched_bytes=touched_bytes,
    )


def merge_core(
    bundle_dir: Path,
    index_path: Path,
    log_path: Path,
    prepared: PreparedMerge,
) -> MergeResult:
    """Phase B (after confirm): ordered writes -- `index.md` then `log.md`,
    every touched file's rewrite, then the ledger sidecar's two-phase write
    around the merged survivor -- S1 (`bundle_ledger.write_pending`), V (the
    merged survivor, unchanged call site), S2
    (`bundle_ledger.commit_pending`) -- and finally removes the absorbed
    file (D) (durable-derived-state slice 1a, design Decision 1; extracted
    verbatim from `merge`'s former inline body, `main.py:2559-2596`, design:
    merge-core Extraction, Slice 2b-i). Non-interactive; raises
    `OSError`/`ValueError`. Performs NO VCS side effect -- `_autocommit`
    stays the command's responsibility."""
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

    # The merged survivor is committed only once every rewrite above has
    # succeeded -- see `merge`'s docstring for why that ordering is what
    # makes a mid-rewrite failure cleanly retryable. The ledger sidecar's
    # two-phase write wraps that write (design Decision 1): S1 binds
    # `expected_survivor_sha256` to the EXACT bytes V is about to write,
    # so `recover` can tell "V landed, only S2 (the commit rename) was
    # torn" from "V never landed" purely from on-disk state.
    survivor_path = bundle_dir / f"{survivor_canonical}.md"
    absorbed_path = bundle_dir / f"{absorbed_canonical}.md"
    expected_survivor_sha256 = bundle_ledger.survivor_sha256(
        prepared.plan.merged_survivor
    )
    bundle_ledger.write_pending(
        survivor_canonical,
        bundle_dir,
        survivor_id=survivor_canonical,
        entries=prepared.plan.ledger_entries,
        expected_survivor_sha256=expected_survivor_sha256,
    )  # S1
    fsio.write_atomic(survivor_path, prepared.plan.merged_survivor)  # V
    bundle_ledger.commit_pending(survivor_canonical, bundle_dir)  # S2
    fsio.remove_file(absorbed_path)  # D

    sidecar_rel = (
        bundle_ledger.ledger_path_for(survivor_canonical, bundle_dir)
        .relative_to(bundle_dir)
        .as_posix()
    )

    ledger_sidecar_path = f"bundle/{sidecar_rel}"

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
            ledger_sidecar_path,
        ],
        ledger_sidecar_path=ledger_sidecar_path,
    )


def _merge_drift_targets(
    layout: config.WorkspaceLayout, prepared: PreparedMerge
) -> dict[Path, bytes]:
    """Build the drift-guard baseline mapping (issue #334) a prepared merge
    needs for `_reject_drifted_targets` -- extracted from `merge`'s former
    inline dict literal at its own call site (design D6, issue #266) so
    `curate`'s Identity stage can call `_reject_drifted_targets` with the
    EXACT same guard mapping `merge` uses, rather than reconstructing it
    and risking the two drifting apart. `merge` itself now calls this too,
    so there is exactly one place this mapping is built.

    The absorbed file's baseline is included alongside every OVERWRITE
    target: it is the one path `merge_core`/`curate`'s Identity stage
    UNLINKS, not overwrites -- the caller is responsible for passing it in
    `deletes=` to `_reject_drifted_targets` so the refusal message reports
    it as a delete target, not a write target (#329)."""
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    survivor_path = layout.bundle_dir / f"{prepared.survivor_canonical}.md"
    absorbed_path = layout.bundle_dir / f"{prepared.absorbed_canonical}.md"
    return {
        index_path: prepared.index_bytes,
        log_path: prepared.log_bytes,
        **{
            layout.bundle_dir / rel: data
            for rel, data in prepared.touched_bytes.items()
        },
        survivor_path: prepared.survivor_bytes,
        absorbed_path: prepared.absorbed_bytes,
    }


@dataclass(frozen=True)
class PreparedRelate:
    """Pure Phase-A result of `prepare_relate`: everything `relate`'s
    preview, confirm gate, and `relate_core` need, built in memory without
    writing anything (design: `curate` change, D5 -- the Structure stage's
    write seam, issue #266).

    `source_bytes`/`log_bytes` are the drift guard's baselines (issues
    #306, #313, #318): the raw bytes each write target held at the SAME
    `_snapshot_read` observation whose decoded text fed the plan, which the
    command hands to `_reject_drifted_targets` after its confirm gate --
    mirroring `PreparedMerge`'s snapshot-bytes shape."""

    source_canonical: str
    target_canonical: str
    rel_type: str
    new_source_text: str
    new_log_text: str
    already_present: bool
    existing_relations_count: int
    updated_relations_count: int
    review: bool
    source_bytes: bytes
    log_bytes: bytes


def prepare_relate(
    source_path: Path,
    log_path: Path,
    source_canonical: str,
    target_canonical: str,
    rel_type: str,
    root: Path,
    *,
    now: datetime,
) -> PreparedRelate:
    """Phase A (pure, no writes): read config + the two texts, compute the
    updated `relations:` list and the `log.md` entry -- extracted verbatim
    from `relate`'s former inline body (`main.py:3715-3753` pre-extraction,
    design D5). Non-interactive; raises `OSError`/`ValueError` on bad
    input. Writes nothing to disk.

    One `_snapshot_read` observation per target (issues #306, #313, #318):
    the decoded text feeds the plan, the raw bytes feed the drift guard's
    baseline the returned `PreparedRelate` carries for the command to check
    after its confirm gate."""
    cfg = config.read_config(root)
    # One `_snapshot_read` observation per target: the decoded text feeds
    # the parsers below, the raw bytes feed `_reject_drifted_targets`
    # (issues #306, #313, #318).
    source_bytes, source_text = _snapshot_read(source_path)
    log_bytes, log_text = _snapshot_read(log_path)

    metadata, body = okf.load_frontmatter(source_text)
    existing_relations = okf.decode_relations(metadata)
    new_relation = okf.Relation(target=target_canonical, type=rel_type)
    already_present = any(
        relation.target == new_relation.target and relation.type == new_relation.type
        for relation in existing_relations
    )
    updated_relations = (
        existing_relations if already_present else [*existing_relations, new_relation]
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

    return PreparedRelate(
        source_canonical=source_canonical,
        target_canonical=target_canonical,
        rel_type=rel_type,
        new_source_text=new_source_text,
        new_log_text=new_log_text,
        already_present=already_present,
        existing_relations_count=len(existing_relations),
        updated_relations_count=len(updated_relations),
        review=cfg.review,
        source_bytes=source_bytes,
        log_bytes=log_bytes,
    )


def relate_core(source_path: Path, log_path: Path, prepared: PreparedRelate) -> None:
    """Phase B (after confirm): write the source concept file then
    `log.md` -- extracted verbatim from `relate`'s former inline body
    (`main.py:3800-3801` pre-extraction, design D5). Non-interactive;
    raises `OSError`/`ValueError`. Performs NO VCS side effect --
    `_autocommit` stays the caller's responsibility."""
    fsio.write_atomic(source_path, prepared.new_source_text)
    fsio.write_atomic(log_path, prepared.new_log_text)


@dataclass(frozen=True)
class PreparedSetVolatility:
    """Pure Phase-A result of `prepare_set_volatility`: everything
    `set-volatility`'s preview, confirm gate, and `set_volatility_core`
    need, built in memory without writing anything (design: `curate`
    change, D5 -- the Metadata stage's write seam, issue #266).

    `config_bytes` is the drift guard's baseline (issues #313, #318, #335):
    the raw bytes `openkos.yaml` held at the SAME `_snapshot_read`
    observation whose decoded text fed `new_config_text`, which the
    command hands to `_reject_drifted_targets` after its confirm gate --
    mirroring `PreparedMerge`'s snapshot-bytes shape."""

    concept_type: str
    tier: str
    new_config_text: str
    config_bytes: bytes


def prepare_set_volatility(
    config_path: Path, concept_type: str, tier: str
) -> PreparedSetVolatility:
    """Phase A (pure, no writes): read `openkos.yaml` and run
    `config.set_type_tier`'s text-surgery core -- extracted verbatim from
    `set-volatility`'s former inline body (`main.py:4769-4776`
    pre-extraction, design D5). Non-interactive; raises `OSError`/
    `ValueError` on an un-editable existing shape. Writes nothing to disk.

    One `_snapshot_read` observation (issues #313, #318, #335): the
    decoded text is what `set_type_tier` derives the whole new file from,
    and the raw bytes are the guard's baseline for that same state."""
    config_bytes, config_text = _snapshot_read(config_path)
    new_config_text = config.set_type_tier(config_text, concept_type, tier)
    return PreparedSetVolatility(
        concept_type=concept_type,
        tier=tier,
        new_config_text=new_config_text,
        config_bytes=config_bytes,
    )


def set_volatility_core(config_path: Path, prepared: PreparedSetVolatility) -> None:
    """Phase B (after confirm): write `openkos.yaml` -- extracted verbatim
    from `set-volatility`'s former inline body (`main.py:4805`
    pre-extraction, design D5). Non-interactive; raises `OSError`/
    `ValueError`. Performs NO VCS side effect -- `_autocommit` stays the
    caller's responsibility."""
    fsio.write_atomic(config_path, prepared.new_config_text)


@app.command(
    help=(
        "Fuse two concepts into one, keeping a ledger entry that makes the "
        "merge reversible with unmerge."
    ),
    rich_help_panel="Curate",
)
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
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Bypass the doctor-flagged ledger-integrity refusal (Check B, "
            "post-merge mutation) for the survivor's sidecar. Independent "
            "of --auto -- it never skips the confirmation prompt."
        ),
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

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every path this run intends to touch
    and refuses the WHOLE run (exit 3, nothing written, nothing removed) if
    any changed or vanished since Phase A read it (issues #313, #319,
    #334).

    That set is `index.md`, `log.md`, every touched third-party file, the
    survivor, AND the absorbed file. The absorbed file is the worst case:
    it is deleted, not overwritten, so an edit landing on it during the
    prompt would be destroyed outright with nothing left to recover from.
    Files the whole-bundle scan read but the plan will neither write nor
    delete are not guard targets -- they feed rewrite detection only.

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

    _reject_torn_ledger_write(layout.bundle_dir, survivor_canonical, "merge")
    _reject_flagged_ledger_write(root, layout.bundle_dir, survivor_canonical, force)

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
    if prepared.stacked_body is not None:
        typer.echo(
            f"  + stack absorbed body: {prepared.stacked_body.absorbed_chars} "
            f"unreconciled char(s) ({prepared.stacked_body.share:.0%} of "
            "merged body -- bodies were appended, not reconciled)"
        )
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

    # Issue #334: every byte `merge_core` writes below was computed from a
    # pre-prompt read, so re-validate each target now -- after the gate,
    # before the first write.
    #
    # The ABSORBED file is in here too, not just the write targets: it is
    # UNLINKED, so an edit landing on it during the prompt would be
    # destroyed outright -- strictly worse than being overwritten, since
    # nothing survives to recover from. The keys are built from the same
    # `bundle_dir`/resolution both phases share (#325): `survivor_path`/
    # `absorbed_path` are `_resolve_concept_path`'s `bundle_dir /
    # f"{canonical}.md"`, the exact construction `merge_core` writes and
    # removes.
    _reject_drifted_targets(
        layout,
        _merge_drift_targets(layout, prepared),
        "merge",
        # #319: the absorbed file is the one path `merge_core` UNLINKS;
        # everything else in the mapping is overwritten.
        deletes=frozenset({absorbed_path}),
    )

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
            result.ledger_sidecar_path,
        ],
        f"openkos: merge {absorbed_canonical} into {survivor_canonical}",
    )


@app.command(
    help=(
        "Reverse the most recent merge on a concept, restoring both "
        "documents to their pre-merge state."
    ),
    rich_help_panel="Curate",
)
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

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads `index.md`, `log.md`, the survivor
    and every rewritten third-party file, and refuses the WHOLE run (exit 3,
    nothing written) if any changed or vanished since Phase A read it
    (issues #306, #313, #319). The refusal carries a CUSTOM remedy (#328)
    because `unmerge` is the one guarded verb whose re-run is not a safe
    recovery: nothing is recomputed from the current state, so a re-run
    restores the pre-merge snapshots over `index.md`/`log.md`/the survivor
    -- overwriting the protected edit -- and keeps refusing on an edited
    rewrite file until the edit is reverted. The message therefore tells
    the operator to copy the edit somewhere safe first, and never advises
    the plain re-run that would discard it.

    What that adds differs per target, and only one group was already
    protected. The link/relation/provenance rewrite files DO have a
    pre-prompt fail-closed check below, so for them the guard narrows a
    timing window. `index.md`/`log.md` have only the warn-and-continue
    `catalog_log_drifted` notice (see Limitation), and the survivor has no
    pre-prompt drift check at all -- for those three the guard is the FIRST
    thing that refuses, and only for drift landing inside the prompt
    window. Drift that arrives a moment earlier is still discarded.

    The recreated absorbed file is the one write the guard cannot cover:
    Phase A refuses outright if it already exists, so there are no bytes to
    compare against. Its protection is the write itself being create-only
    (`fsio.write_exclusive`, #323): a file created at that path between
    Phase A's existence check and Phase B's write raises `FileExistsError`
    instead of being clobbered, making the Phase-A promise hold at write
    time.

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
    corruption. That now includes a file created at the absorbed path
    during the prompt window (#323): its create-only write errors
    mid-Phase-B instead of silently winning, leaving the catalog/log
    restored, every reversed inbound-link/relation/provenance rewrite file
    already restored too (they land before the absorbed write in the order
    above), the created file intact, and the survivor -- ledger and all,
    so the absorbed content stays recoverable -- untouched. Any failure,
    Phase A or Phase B, is caught and reported on stderr (exit 1), not a
    raw traceback.

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

    _reject_torn_ledger_write(layout.bundle_dir, survivor_canonical, "unmerge")

    now = datetime.now(UTC)

    try:
        cfg = config.read_config(root)
        # One `_snapshot_read` observation (issues #306, #313, #318): the
        # raw bytes are the drift guard's baseline for the survivor.
        # Durable-derived-state slice 1a: `plan_unmerge` no longer needs the
        # DECODED text at all -- the ledger entries live in a sidecar
        # (`bundle/ledger.py`), never the survivor's own frontmatter, and
        # `restored_survivor` comes straight from the tail entry's
        # `survivor_before`, not from parsing this file.
        survivor_bytes, _survivor_text = _snapshot_read(survivor_path)
        existing_entries = bundle_ledger.read_entries(
            survivor_canonical, layout.bundle_dir
        )
        plan = bundle_merge.plan_unmerge(
            survivor_id=survivor_canonical,
            absorbed_id=absorbed_canonical,
            entries=existing_entries,
        )

        absorbed_path = layout.bundle_dir / f"{absorbed_canonical}.md"
        if absorbed_path.exists():
            raise ValueError(
                f"cannot restore 'bundle/{absorbed_canonical}.md' -- a file "
                "already exists at that path"
            )

        index_bytes, current_index_text = _snapshot_read(index_path)
        log_bytes, current_log_text = _snapshot_read(log_path)
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
        # Accumulated across all three partitions below, each file's bytes
        # coming out of the same `_snapshot_read` observation as the text
        # its reversal is computed from (issues #306, #313, #318).
        rewrite_bytes: dict[str, bytes] = {}
        provenance_texts: dict[str, str] = {}
        for rel in provenance_rewrite_files:
            rewrite_bytes[rel], provenance_texts[rel] = _snapshot_read(
                layout.bundle_dir / rel
            )
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
        other_texts: dict[str, str] = {}
        for rel in rewritten_files:
            rewrite_bytes[rel], other_texts[rel] = _snapshot_read(
                layout.bundle_dir / rel
            )
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
        relation_texts: dict[str, str] = {}
        for rel in relation_rewrite_files:
            rewrite_bytes[rel], relation_texts[rel] = _snapshot_read(
                layout.bundle_dir / rel
            )
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

    # Issue #313: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    #
    # `absorbed_path` is absent by necessity, not oversight -- see the
    # docstring. The guard's `Mapping[Path, bytes]` cannot express "expected
    # absent", so its window is closed by the write itself being create-only
    # (`fsio.write_exclusive` below, #323), not by an entry here.
    _reject_drifted_targets(
        layout,
        {
            index_path: index_bytes,
            log_path: log_bytes,
            survivor_path: survivor_bytes,
            **{layout.bundle_dir / rel: data for rel, data in rewrite_bytes.items()},
        },
        "unmerge",
        # #328: the guard's default advice -- "re-run to recompute" -- is
        # actively destructive here. `unmerge` does not recompute anything
        # from the current state: `index.md`/`log.md`/the survivor are
        # restored to their PRE-MERGE snapshots, so a re-run overwrites the
        # very edit this refusal just protected; and an edited rewrite file
        # keeps failing `reverse_link_rewrites`' own drift check until the
        # edit is reverted. The remedy must describe that asymmetry and put
        # "save your edit first" ahead of any re-run.
        remedy=(
            "Copy your edit somewhere safe before re-running: a re-run "
            "restores the pre-merge snapshots over index.md, log.md, and "
            "the survivor (overwriting the edit), and keeps refusing on an "
            "edited rewrite file until that edit is reverted."
        ),
    )

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
        # the ledger sidecar entry (the only record of `absorbed_snapshot`,
        # durable-derived-state slice 1a) is deliberately kept intact on
        # disk until the absorbed file it describes has actually landed, so
        # a failure between these two steps never loses the absorbed
        # content -- it is still recoverable from the sidecar.
        #
        # Create-only (#323): Phase A promised that "a file already exists
        # at that path" refuses the unmerge, but that existence check
        # cannot see a file created during the prompt window, and the drift
        # guard cannot either (no Phase-A bytes to compare). `write_exclusive`
        # makes the promise hold at write time: a concurrent create raises
        # `FileExistsError` here -- caught by this try's `except OSError`
        # arm and reported like any other Phase-B write failure, never a
        # traceback -- leaving the git-recoverable partial state documented
        # above instead of silently discarding the created file.
        fsio.write_exclusive(absorbed_path, plan.restored_absorbed)
        fsio.write_atomic(survivor_path, plan.restored_survivor)

        # Only once every restore above has succeeded is `log.md` written a
        # SECOND time, with the `**Unmerge**` audit line appended on top of
        # the just-restored `log_before` -- the append-only trail net-grows
        # by exactly this one line.
        fsio.write_atomic(log_path, new_log_text)

        # The ledger sidecar's tail entry is popped LAST of all (task 2.7):
        # every restore above is idempotent to re-write on a retry, so
        # popping the tail only after they have all landed makes a partial
        # failure here safely re-runnable -- a re-run recomputes the exact
        # same restores and pops the exact same (still-present) tail entry.
        bundle_ledger.write_entries(
            survivor_canonical,
            layout.bundle_dir,
            survivor_id=survivor_canonical,
            entries=plan.remaining_entries,
        )
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

    ledger_sidecar_rel = (
        bundle_ledger.ledger_path_for(survivor_canonical, layout.bundle_dir)
        .relative_to(layout.bundle_dir)
        .as_posix()
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
            f"bundle/{ledger_sidecar_rel}",
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


@app.command(
    help=(
        "Record how you resolved a contradiction between two concepts, so "
        "the decision is kept rather than repeated."
    ),
    rich_help_panel="Curate",
)
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
    refuses too (self-pair rejected) -- checked BOTH as canonical strings
    (the literal duplicate, clearer message) and as `samefile` device+inode
    identity (#324), since a case-insensitive filesystem or a symlink can
    make two differing ids denote one file, which the byte-comparing drift
    guard cannot detect. If `--winner <id>` is given, it is
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

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads every path this run intends to write
    (both concept documents and `log.md`) and refuses the WHOLE run (exit 3,
    nothing written) if any changed or vanished since Phase A read it
    (issues #306, #313, #319). Distinct from the partial result the next paragraph
    describes: nothing is written at all, so there is nothing to complete on
    re-run.

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
        # Distinct STRINGS are not distinct FILES (#324): on a
        # case-insensitive filesystem (macOS default) `foo` and `Foo` are
        # two canonical ids for ONE file -- and a symlink aliases one under
        # any name on any filesystem. The drift guard cannot catch this
        # either: both keys snapshot the same identical bytes (no drift),
        # and Phase B's second `write_atomic` over the same inode then
        # silently discards the first document's edge and note. `samefile`
        # compares device+inode -- after `_resolve_concept_path` proved
        # both exist, so error precedence is preserved -- and is naturally
        # False for genuinely distinct files on case-sensitive hosts. The
        # string check above stays: it is cheap and gives the literal
        # self-pair its clearer message.
        if path_a.samefile(path_b):
            raise ValueError(
                f"id_a and id_b must be distinct, {canonical_a!r} and "
                f"{canonical_b!r} resolve to the same file on this filesystem"
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
        # One `_snapshot_read` observation per target: the decoded text
        # feeds the parsers below, the raw bytes feed
        # `_reject_drifted_targets` (issues #306, #313, #318).
        bytes_a, text_a = _snapshot_read(path_a)
        bytes_b, text_b = _snapshot_read(path_b)
        log_bytes, log_text = _snapshot_read(log_path)

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

    # Issue #313: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    # Whole-run refusal is what keeps the pair from ending up disagreeing
    # about its own resolution.
    _reject_drifted_targets(
        layout,
        {
            path_a: bytes_a,
            path_b: bytes_b,
            log_path: log_bytes,
        },
        "reconcile",
    )

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
        # Name the STATUS the loser will carry, not only the act (#389).
        # This verb said "recorded as superseding" while `list` shows
        # `deprecated` in its STATUS column, so the operator met two words
        # for the action they had just performed and its effect, with
        # nothing connecting them.
        typer.echo(
            f"openkos reconcile: recorded '{winner_canonical}' as superseding "
            f"'{loser_canonical}'; '{loser_canonical}' now lists as "
            f"deprecated ({log_path.name} updated)."
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


@app.command(
    help=(
        "Report what the bundle contains right now: counts by type, recent "
        "activity, and anything needing attention."
    ),
    rich_help_panel="Explore",
)
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
    `build_graph`'s walk behind the untyped-edge needs-attention line and
    the empty-graph notice (#387).
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
    # issue #257: reuses this SAME in-memory `docs` list again -- no
    # fourth `collect_docs()` call (the structural no-fifth-walk guard).
    dangling_provenance = lint_check.check_dangling_provenance(docs)
    # issue #421: and again -- an engine-owned `derived_from` no
    # `provenance:` entry backs. Pure and deterministic: `status` calls no
    # model for it, and the SAME `docs` list still serves every check.
    unbacked_provenance = lint_check.check_unbacked_provenance(docs)
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
    needs_attention.extend(
        f"{finding.concept_id}: [{finding.kind}] {finding.detail}"
        for finding in dangling_provenance
    )
    needs_attention.extend(
        f"{finding.concept_id}: [{finding.kind}] {finding.detail}"
        for finding in unbacked_provenance
    )
    # #186: pending duplicate groups are ACTIONABLE -- name `duplicates` as
    # the next step. Exact-title matches only; near-match (LOW) is a
    # deliberate high-recall review queue, not an alert (similarity.py).
    # #216: hence `find_exact_title_groups`, not `find_candidates` -- it
    # returns the identical HIGH groups in the identical order, but skips the
    # O(n^2) pairwise `near_match_score` pass whose LOW groups this line
    # discarded. `duplicates`/`adjudicate` still call `find_candidates_report`
    # (curate-call-budget): they use both tiers.
    exact_title_groups = len(find_exact_title_groups(layout.bundle_dir))
    if exact_title_groups:
        needs_attention.append(
            f"{exact_title_groups} candidate group{_plural(exact_title_groups)} with "
            "identical titles — run `openkos duplicates` to review."
        )
    # issue #183 (Slice 0): a missing/empty `vectors.db` is genuinely
    # ACTIONABLE (spec: "Needs-Attention Surfaces Missing Vector Index"), so
    # it belongs in `needs_attention` itself -- unlike the empty-graph line
    # below, which stays purely INFORMATIONAL. #386: gated on the bundle
    # holding at least one eligible document (the SAME `docs` list every
    # check above reuses -- no new walk): reindexing a bundle with nothing
    # to index is meaningless, and `next` owns naming the real first step
    # (`openkos ingest`) in that state.
    vectors_missing = vector_store_is_empty(layout.vectors_db_path)
    if vectors_missing and docs:
        needs_attention.append(
            "Dense retrieval and candidate edges unavailable — run "
            "`openkos reindex` (vectors.db missing)."
        )
    # #381: an index older than the bundle is ACTIONABLE in exactly the way
    # the missing-`vectors.db` line above is -- it names the command that
    # fixes it -- so it belongs here rather than among the informational
    # lines. Absence is deliberately NOT reported as staleness (see
    # `_stale_index_names`): a freshly `init`ed workspace has no derived
    # store at all, and recommending a refresh of indexes that were never
    # built is the same defect #386 reports against `next`. `status`
    # describes the workspace, not one answer, so it declares BOTH
    # manifest-gated stores (#436) -- unlike `query`, which reads only fts.
    stale_indexes = _stale_index_names(layout, reads=("fts", "graph"))
    if stale_indexes:
        needs_attention.append(
            f"Derived indexes are stale ({', '.join(stale_indexes)}) — run "
            "`openkos reindex` to refresh retrieval."
        )
    # #387: an UNTYPED concept-to-concept edge is pending curation work, so
    # it earns a needs-attention line that says how many and names the verb
    # that types them (`openkos curate`). A fully-typed edge count is a
    # graph-density metric with no action, which is exactly what this
    # section must not carry -- and `status` has no informational section
    # for derived-graph metrics ("Bundle contents" is pinned to the disk
    # scan), so the fully-typed count is dropped rather than moved.
    # `graph_edge_summary` is read-only over the graph projection, built
    # once (#195) and skipped when `vectors_missing`, exactly as before.
    edge_summary: tuple[int, int] | None = None
    if not vectors_missing:
        with build_graph(layout.bundle_dir) as store:
            edge_summary = graph_edge_summary(layout.bundle_dir, store=store)
        total, typed = edge_summary
        untyped = total - typed
        if untyped:
            needs_attention.append(
                f"{untyped} of {total} concept-to-concept edge(s) untyped — "
                "run `openkos curate` to type them."
            )
    if not needs_attention:
        typer.echo("  Nothing needs attention.")
    else:
        for line in needs_attention:
            typer.echo(f"  {line}")
    # The empty-graph notice stays a separate, purely INFORMATIONAL line
    # (spec: "or an adjacent informational line") -- never appended to
    # `needs_attention`, so a healthy workspace still prints "Nothing needs
    # attention." above.
    if edge_summary is not None and edge_summary[0] == 0:
        typer.echo("  No concept relationships yet.")


@app.command(
    "next",
    help=(
        "Print the single command worth running next, chosen from the "
        "bundle's current state. Read-only and deterministic."
    ),
    rich_help_panel="Get started",
)
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


@app.command(
    "list",
    help=(
        "List bundle objects with their id, type, sensitivity and lifecycle "
        "status. Optionally filtered to one type."
    ),
    rich_help_panel="Explore",
)
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

    Rows are `ID  TYPE  SENSITIVITY  STATUS  TITLE`, `ljust`-aligned over
    the header labels and the rows actually shown (post-filter,
    post-truncation) -- the same pattern `status`'s bundle-contents
    section uses (its `label_width` block over `_bundle_content_lines`,
    design D6; cited by symbol, not by line number, because the line
    range this docstring used to name had drifted into an unrelated
    function). `TYPE` sits beside
    `ID` because both answer "what am I looking at" (#399): without it two
    objects of different kinds with the same title print identically, and
    the only discriminator is the directory prefix buried inside the id.
    It is rendered from `listing.LINK_DIR_TO_TYPE_NAME` over the
    structurally derived `link_dir`, never by re-reading a document's
    `type` field, and falls back to `(unknown)` for an object living
    outside the registry's directories. A title is rendered
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
    (spec: Read-Only, No Structured Output; deferred, not banned -- the
    deferral was recorded in the list-verb work, #184, and no issue tracks
    it yet).
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

    type_names = {
        row.concept_id: listing.LINK_DIR_TO_TYPE_NAME.get(row.link_dir, "(unknown)")
        for row in shown
    }

    id_w = max(len("ID"), *(len(row.concept_id) for row in shown))
    type_w = max(len("TYPE"), *(len(name) for name in type_names.values()))
    sens_w = max(len("SENSITIVITY"), *(len(row.sensitivity) for row in shown))
    stat_w = max(len("STATUS"), *(len(row.status) for row in shown))

    typer.echo(
        f"{'ID'.ljust(id_w)}  {'TYPE'.ljust(type_w)}  "
        f"{'SENSITIVITY'.ljust(sens_w)}  {'STATUS'.ljust(stat_w)}  TITLE"
    )
    for row in shown:
        title = row.title or ("(unreadable)" if not row.readable else "(untitled)")
        typer.echo(
            f"{row.concept_id.ljust(id_w)}  {type_names[row.concept_id].ljust(type_w)}  "
            f"{row.sensitivity.ljust(sens_w)}  "
            f"{row.status.ljust(stat_w)}  {title}"
        )

    if not all_objects and total > len(shown):
        typer.echo(f"Showing {len(shown)} of {total} — use --all to see the rest.")


@app.command(
    help=(
        "Health-check the bundle's contents: stale stamps, orphan pages, "
        "malformed names and other findings you may want to act on."
    ),
    rich_help_panel="Explore",
)
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
    `Dangling provenance:` (issue #257:
    `lint_check.check_dangling_provenance`, reusing this SAME `docs` list --
    no new walk), `Unextracted sources:` (issue #187:
    `lint_check.check_unextracted`, reusing this SAME `docs` list -- no new
    walk), `Below-source sensitivity:`, `Multi-source uncovered:`
    (issue #231, PR2: `lint_check.check_below_source_sensitivity`, reusing
    this SAME `docs` list too -- design D3's no-fifth-walk guard), and
    `Unbacked provenance:` (issue #421:
    `lint_check.check_unbacked_provenance`, this SAME `docs` list again --
    an engine-owned `relations:` type, `derived_from`, naming a target the
    document's own `provenance:` never records), and `Non-NFC names:`
    (issue #474: `lint_check.check_non_nfc_names` -- a NAMES-ONLY
    incremental `rglob` walk over `layout.bundle_dir` that never opens a
    file, which is why it is NOT a violation of design D3's no-fifth-walk
    guard: that guard protects the read+parse walk, and this one must see
    what `collect_docs` cannot -- a decomposed name on a directory, a
    non-`.md` file, or an unreadable doc. Rendered via `finding.path`,
    never `.concept_id`, because the finding names an on-disk entry, not
    a concept object. `lint` stays read-only and never renames; the
    detail points at `openkos normalize-names`, the dedicated verb that
    does (#474 part 2)), each with its own empty-state line when there is
    nothing to report. Every
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
    # issue #257: reuses this SAME `docs` list again -- no new bundle walk
    # (the structural no-fifth-walk guard holds).
    dangling_provenance = lint_check.check_dangling_provenance(docs)
    # issue #421: and again -- pure, deterministic, no LLM, no clock.
    unbacked_provenance = lint_check.check_unbacked_provenance(docs)
    # issue #474: a names-only walk, never the docs list -- collect_docs
    # cannot see a decomposed directory, non-`.md` file, or unreadable doc.
    non_nfc = lint_check.check_non_nfc_names(layout.bundle_dir)
    # task 3.6: a names-only walk over `bundle/.state/` alone, never the
    # `docs` list -- `collect_docs`/`_iter_docs` never descends there.
    state_dir_markdown = lint_check.check_state_dir_contains_no_markdown(
        layout.bundle_dir
    )
    notices = window_notices + skip_notices
    report = lint_check.LintReport(
        stale=stale,
        orphans=orphans,
        dangling=dangling,
        unextracted=unextracted,
        below_source=below_source,
        multi_source_uncovered=multi_source_uncovered,
        dangling_provenance=dangling_provenance,
        unbacked_provenance=unbacked_provenance,
        non_nfc=non_nfc,
        state_dir_markdown=state_dir_markdown,
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
    typer.echo("Dangling provenance:")
    if not report.dangling_provenance:
        typer.echo("  No dangling provenance findings.")
    else:
        for finding in report.dangling_provenance:
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
    typer.echo()
    typer.echo("Unbacked provenance:")
    if not report.unbacked_provenance:
        typer.echo("  No unbacked provenance claims.")
    else:
        for finding in report.unbacked_provenance:
            typer.echo(f"  {finding.concept_id}: {finding.detail}")
    typer.echo()
    typer.echo("Non-NFC names:")
    if not report.non_nfc:
        typer.echo("  No non-NFC on-disk names.")
    else:
        # #474: `finding.path`, never `.concept_id` -- this kind names an
        # on-disk entry (possibly a directory or non-`.md` file), not a
        # concept object, so the path is the honest spelling.
        for finding in report.non_nfc:
            typer.echo(f"  {finding.path}: {finding.detail}")
    typer.echo()
    typer.echo("State-dir markdown:")
    if not report.state_dir_markdown:
        typer.echo("  No `.md` files under bundle/.state/.")
    else:
        for finding in report.state_dir_markdown:
            typer.echo(f"  {finding.path}: {finding.detail}")


@app.command(
    help=(
        "Report concepts from different sources that look like duplicates, "
        "without judging or changing anything."
    ),
    rich_help_panel="Explore",
)
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

    On a workspace, `resolution.find_candidates_report` performs one
    read-only, whole-bundle pass and returns a `CandidateGroupReport` of
    candidate groups: same-type OKF objects that MIGHT be the same
    real-world entity, tiered by HOW they matched -- HIGH (an exact
    normalized title) or LOW (a near-match). The tier records the match
    METHOD, never a strength ranking, which is why a LOW group can carry
    a similarity score of 1.000 without contradiction (issue #192). This is
    a REPORT ONLY -- `duplicates` never merges, deletes, or otherwise
    adjudicates a candidate; it points at the SHIPPED `merge` verb
    (`cli/main.py:3957`) through its trailing hint instead (spec: Read-Only
    CLI Candidate Report Verb).

    `find_candidates_report` bounds its returned groups to
    `_MAX_CANDIDATE_GROUPS` (curate-call-budget); it does NOT return every
    group a pathological corpus would otherwise produce. WHEN the cap
    binds, `duplicates` echoes `candidate_group_truncation_notice` to
    stderr before rendering the (bounded) report -- never silently.

    Output is grouped by OKF `type`, then by tier, mirroring
    `find_candidates_report`'s own stable ordering: each group renders its
    type, tier, member concept_ids, and the trigger (the shared normalized
    key for HIGH, the similarity score for LOW). An empty result renders a
    clear "No candidates found." line instead of an empty section. Every
    successful read exits 0, whether or not any candidates are found (spec:
    No candidates still exits 0). No file under the workspace is ever
    created, modified, or deleted, and no `--json` or other structured
    output mode is offered (spec: Read-Only and Human-Readable Only).

    Unless `--include-deprecated` is passed, deprecated/superseded concepts
    (status-aware-retrieval) are excluded from every candidate group --
    `duplicates` shares `adjudicate`'s `find_candidates_report` call and,
    per the locked scope decision, gets the SAME `--include-deprecated`
    flag for consistency.
    """
    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos duplicates: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    report = find_candidates_report(
        layout.bundle_dir, include_deprecated=include_deprecated
    )
    groups = list(report.groups)
    notice = candidate_group_truncation_notice(report)
    if notice is not None:
        typer.echo(notice, err=True)

    typer.echo(f"openkos duplicates: workspace at {root}")
    typer.echo()
    if not groups:
        typer.echo("No candidates found.")
        return

    high_count = sum(1 for group in groups if group.tier is Tier.HIGH)
    acronym_count = sum(1 for group in groups if group.tier is Tier.ACRONYM)
    low_count = len(groups) - high_count - acronym_count
    typer.echo(_format_group_tally(high_count, acronym_count, low_count))
    typer.echo(
        "Legend: [tier] type -- trigger. The tier is the MATCH METHOD, "
        "not a strength ranking: HIGH = exact normalized key, "
        "ACRONYM = one title's token is the initials of a word run in the "
        "other, LOW = near-match similarity score."
    )
    for group in groups:
        tier_label = group.tier.name
        typer.echo(f"[{tier_label}] {group.okf_type} -- {group.trigger}")
        for member_id in group.member_ids:
            typer.echo(f"  - {member_id}")
        typer.echo()
    typer.echo("Next: openkos merge <survivor> <absorbed>")


@app.command(
    help=(
        "Report which candidate duplicates the model judges to be the same "
        "concept, with its reasoning. Read-only unless you pass an apply flag."
    ),
    rich_help_panel="Curate",
)
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
    `LLMBackend` -- into `resolution.find_candidates_report` followed by
    `resolution.adjudication.adjudicate_candidates`. Invoked WITHOUT
    `--apply`/`--apply-same` it never merges, writes, or decides -- it only
    prints a verdict for human review and points at the SHIPPED `merge` verb
    (`cli/main.py:3957`) through its own `Next:` hint, exactly as
    `duplicates` does. Those two flags are the only paths that write, and
    both are gated on an explicit confirmation; see their paragraphs below.

    `find_candidates_report` bounds its returned groups to
    `_MAX_CANDIDATE_GROUPS` (curate-call-budget); it does NOT hand
    `adjudicate_candidates` every group a pathological corpus would
    otherwise produce. WHEN the cap binds, `adjudicate` echoes
    `candidate_group_truncation_notice` to stderr before the LLM pass
    begins -- never silently.

    `--json` emits the adjudication results as a single pretty-printed JSON
    array on stdout and fully suppresses all human output (tally, legend,
    per-group detail, `Next:` hint, and both empty-state messages). It emits
    every verdict by default; passing `--same-only` filters the array to
    `SAME` entries, mirroring the human display filter. On a partial batch
    (#441) the array holds the completed verdicts and the run still exits 1
    after the stderr failure line below -- a machine consumer that ignores
    the exit code reads valid, paid-for verdicts, never a fabricated
    complete run.

    Output mirrors `duplicates`'s grouped render (type, tier, trigger,
    members) with each group's verdict and rationale appended. The parsed
    confidence is intentionally NOT rendered (issue #138): a local model
    returns a flat, uncalibrated value, so a two-decimal number would imply
    a precision it does not have.
    `--same-only` is a DISPLAY-only filter: it hides non-`SAME` verdicts from
    the printed report, but `adjudicate_candidates` always receives -- and
    returns -- every candidate group regardless of the flag; the library
    itself never filters.

    A no-model/no-Ollama failure comes back INSIDE the returned
    `AdjudicationBatch` (#441) and maps onto the SAME 3-tier ORDERED wording
    `query` uses -- `OllamaUnavailable`, then `OllamaModelNotFound`, then
    the generic `OllamaError` fallback -- each with its own actionable
    stderr message and exit 1. The completed verdicts are NEVER discarded:
    every output mode (report, `--json`, `--apply`, `--apply-same`) first
    processes `batch.results` exactly as a complete run over that list,
    THEN one stderr line reports the failure with completed-of-total counts
    and the run exits 1. The raise-path handler ladder is retained around
    the call itself for an injected backend that raises outside `llm.chat`'s
    guarded seam -- same wording, no counts, zero writes.

    Unless `--include-deprecated` is passed, deprecated/superseded concepts
    (status-aware-retrieval) are excluded from the `find_candidates_report`
    call that feeds `adjudicate_candidates` -- `adjudicate` uses candidates,
    so it threads the flag into `find_candidates_report`, not into
    `adjudicate_candidates` itself.

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

    report = find_candidates_report(
        layout.bundle_dir, include_deprecated=include_deprecated
    )
    candidates = list(report.groups)
    notice = candidate_group_truncation_notice(report)
    if notice is not None:
        typer.echo(notice, err=True)
    llm = _chat_client(cfg, task="adjudication")
    local_exemption = _resolve_local_exemption(llm, cfg)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    try:
        batch = adjudicate_candidates(
            candidates,
            bundle_dir=layout.bundle_dir,
            llm=llm,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
            # TTY-gated per-group progress on stderr; `None` (silent) when
            # output is piped (issue #190, mirrors `suggest-relations`' #134
            # per-edge line).
            on_progress=observability.progress_callback(
                "adjudicate", "adjudicating group"
            ),
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

    results = batch.results
    if json_output:
        typer.echo(
            json.dumps(
                _adjudication_payload(
                    results,
                    same_only=same_only,
                    total=len(candidates),
                    partial=batch.failure is not None,
                ),
                indent=2,
            )
        )
    elif apply:
        _run_adjudicate_apply(root, layout, index_path, log_path, results)
    elif apply_same:
        _run_adjudicate_apply_same(
            root, layout, index_path, log_path, results, confirm_count=confirm_count
        )
    else:
        _render_adjudicate_report(root, results, same_only=same_only)

    if batch.failure is not None:
        # Partial batch (#441): every output mode above already processed the
        # completed verdicts exactly as a complete run over that list -- the
        # paid-for work is never discarded -- so all that remains is the one
        # stderr failure line and the OllamaError-family exit code.
        _echo_adjudicate_batch_failure(batch, total=len(candidates), model=cfg.model)
        raise typer.Exit(code=1) from batch.failure


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


@app.command(
    "suggest-relations",
    help=(
        "Suggest a type for every untyped link between concepts, for you to "
        "review before anything is written."
    ),
    rich_help_panel="Curate",
)
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
    builds a real `OllamaClient(model=cfg.model)` BEFORE the candidate
    count is known -- construction performs no I/O, it only resolves and
    stores the host -- because the confidential local exemption (#240)
    must be resolved from that SAME client before `candidate_edges` (the
    pre-flight sensitivity filter) runs; the cost gate on the candidate
    count still happens first, and only a confirmed run reaches
    `suggest_edge_types`, which the resolved client is then injected into.

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

    A no-model/no-Ollama failure comes back INSIDE the returned
    `EdgeSuggestionBatch` (#441) and maps onto the SAME 3-tier ORDERED
    wording `adjudicate`/`query` use -- `OllamaUnavailable`, then
    `OllamaModelNotFound`, then the generic `OllamaError` fallback -- each
    with its own actionable stderr message and exit 1. The completed
    suggestions are NEVER discarded: the report first renders
    `batch.results` exactly as a complete run over that list, THEN one
    stderr line reports the failure with completed-of-total counts and the
    run exits 1. The raise-path handler ladder is retained around the call
    itself for an injected backend that raises outside `llm.chat`'s guarded
    seam -- same wording, no counts, zero writes either way.

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

    # Built here rather than just before the run: `candidate_edges` below
    # already filters on sensitivity, so the exemption must be resolved from
    # the SAME client the later `suggest_edge_types` will send through
    # (issue #240). Construction performs no I/O -- it only resolves and
    # stores the host -- so nothing is contacted by moving it up.
    llm = _chat_client(cfg, task="edge_typing")
    local_exemption = _resolve_local_exemption(llm, cfg)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
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
            local_exemption=local_exemption,
            store=store,
        )

        typer.echo(f"openkos suggest-relations: workspace at {root}")
        typer.echo()
        # #378 slice 2 (post-review correction): pass 3's candidate-edge cap
        # truncation, never silent -- but restricted to what THIS caller may
        # see. Read here, INSIDE the `with` block, since `store` closes
        # below.
        #
        # `store.candidate_report.produced`/`.retained` are RAW counts: pass
        # 3 has no sensitivity awareness, so they can include pairs with a
        # confidential endpoint that `candidate_edges` above already
        # excluded from `edges`. Printing them directly would disclose a
        # pre-cap volume the edge list below deliberately withholds --
        # `candidate_truncation_notice` re-derives both counts from
        # `report.pairs` through the SAME `sensitivity.sensitive_concept_ids`
        # walk `candidate_edges` just ran, so this line and `total` below
        # agree on what a caller without `--include-confidential` may see.
        notice = candidate_truncation_notice(
            store.candidate_report,
            layout.bundle_dir,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        )
        if notice is not None:
            typer.echo(notice)
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

    def _on_progress(index: int, count: int, suggestion: EdgeSuggestion) -> None:
        """Per-edge progress line to stderr (keeps stdout the clean report)."""
        edge = suggestion.edge
        label = suggestion.suggested_type or "?"
        typer.echo(
            f"  [{index}/{count}] {edge.source_id} -> {edge.target_id}  [{label}]",
            err=True,
        )

    try:
        batch = suggest_edge_types(
            edges,
            bundle_dir=layout.bundle_dir,
            llm=llm,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
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

    for result in batch.results:
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

    if batch.failure is not None:
        # Partial batch (#441): the report above already rendered the
        # completed suggestions exactly as a complete run over that list --
        # the paid-for work is never discarded -- so all that remains is the
        # one stderr failure line and the OllamaError-family exit code.
        _echo_suggest_relations_batch_failure(batch, total=total, model=cfg.model)
        raise typer.Exit(code=1) from batch.failure


@app.command(
    "suggest-volatility",
    help=(
        "Suggest how quickly each kind of concept goes stale, so freshness "
        "checks use a sensible window per type. Advisory; writes nothing on "
        "its own."
    ),
    rich_help_panel="Curate",
)
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

    A no-model/no-Ollama failure comes back INSIDE the returned
    `TierSuggestionBatch` (#441) and maps onto the SAME 3-tier ORDERED
    wording `suggest-relations`/`adjudicate`/`query` use --
    `OllamaUnavailable`, then `OllamaModelNotFound`, then the generic
    `OllamaError` fallback -- each with its own actionable stderr message
    and exit 1. The completed suggestions are NEVER discarded: the report
    first renders `batch.results` exactly as a complete run over that list,
    THEN one stderr line reports the failure with the completed count (no
    of-total -- see `_echo_suggest_volatility_batch_failure` for why this
    verb cannot state one) and the run exits 1. The raise-path handler
    ladder is retained around the call itself for an injected backend that
    raises outside `llm.chat`'s guarded seam -- same wording, no counts,
    zero writes either way.

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

    llm = _chat_client(cfg, task="volatility_typing")
    local_exemption = _resolve_local_exemption(llm, cfg)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    try:
        batch = suggest_volatility(
            layout.bundle_dir,
            llm=llm,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
            # TTY-gated per-type progress on stderr; `None` (silent) when
            # output is piped (issue #190, mirrors `suggest-relations`' #134
            # per-edge line).
            on_progress=observability.progress_callback(
                "suggest-volatility", "suggesting type"
            ),
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

    results = batch.results
    typer.echo(f"openkos suggest-volatility: workspace at {root}")
    typer.echo()
    if not results and batch.failure is None:
        # Guarded on a clean run only (#441): a first-type failure also
        # carries zero results, and "No concept types found." would then
        # claim an empty bundle the failure, not the walk, produced.
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

    if batch.failure is not None:
        # Partial batch (#441): the report above already rendered the
        # completed suggestions exactly as a complete run over that list --
        # the paid-for work is never discarded -- so all that remains is the
        # one stderr failure line and the OllamaError-family exit code.
        _echo_suggest_volatility_batch_failure(batch, model=cfg.model)
        raise typer.Exit(code=1) from batch.failure


@app.command(
    help=(
        "Report concepts whose content disagrees, using the model to judge "
        "already-related pairs. Advisory only; writes nothing."
    ),
    rich_help_panel="Explore",
)
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

    A no-model/no-Ollama failure comes back INSIDE the returned
    `ContradictionBatch` (#441) and maps onto the SAME 3-tier ORDERED
    wording `suggest-relations`/`adjudicate`/`query` use --
    `OllamaUnavailable`, then `OllamaModelNotFound`, then the generic
    `OllamaError` fallback -- each with its own actionable stderr message
    and exit 1. The completed verdicts are NEVER discarded: the report
    first renders `batch.results` exactly as a complete run over that list
    (the `--all`/high-confidence display filter included), THEN one stderr
    line reports the failure with completed-of-total counts -- the total is
    `plan.llm_calls`, the same number the truncation notice describes --
    and the run exits 1. The raise-path handler ladder is retained around
    the call itself for an injected backend that raises outside `llm.chat`'s
    guarded seam -- same wording, no counts, zero writes either way.

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

    llm = _chat_client(cfg, task="contradiction")
    local_exemption = _resolve_local_exemption(llm, cfg)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
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
        # Built here, not inside `find_contradictions`, because the cap-reached
        # line below has to name WHICH KIND was truncated (#444) -- and passing
        # it in means that line describes the exact list that was judged.
        plan = plan_candidates(
            layout.bundle_dir,
            store=store,
            include_deprecated=include_deprecated,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        )
        try:
            batch, _total_pairs = find_contradictions(
                layout.bundle_dir,
                llm=llm,
                include_deprecated=include_deprecated,
                include_confidential=include_confidential,
                local_exemption=local_exemption,
                store=store,
                plan=plan,
                # TTY-gated per-pair progress on stderr; `None` (silent)
                # when output is piped (issue #190, mirrors
                # `suggest-relations`' #134 per-edge line).
                on_progress=observability.progress_callback(
                    "contradictions", "checking pair"
                ),
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

        verdicts = batch.results
        typer.echo(f"openkos contradictions: workspace at {root}")
        typer.echo()
        # #378 slice 2 (post-review correction): pass 3's candidate-edge cap
        # truncation, never silent -- distinct from
        # `contradiction_truncation_notice(plan)` below, which reports the
        # contradiction-engine's OWN candidate cap. Read here, INSIDE the
        # `with` block, since `store` closes below.
        #
        # The two lines count genuinely different things -- the plan's totals
        # come from `plan_candidates`, which is deprecation-filtered, this
        # one from pass 3's own seeding -- but BOTH must now respect the
        # sensitivity-fail-closed-filter before being printed:
        # `candidate_truncation_notice` re-derives its counts from
        # `store.candidate_report.pairs` (RAW, unfiltered by pass 3 itself)
        # through `sensitivity.sensitive_concept_ids`, so this line never
        # discloses a pre-cap volume that includes a confidential endpoint
        # `find_contradictions` above already excluded from its own results.
        notice = candidate_truncation_notice(
            store.candidate_report,
            layout.bundle_dir,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        )
        if notice is not None:
            typer.echo(notice)
            typer.echo()
        if not verdicts and batch.failure is None:
            # Guarded on a clean run only (#441): a first-candidate failure
            # also carries zero verdicts, and the zero-candidates state
            # message would then claim an empty graph the failure, not the
            # projection, produced.
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

    truncation = contradiction_truncation_notice(plan)
    if truncation is not None:
        typer.echo(truncation)
        typer.echo()

    displayed = (
        verdicts
        if show_all
        else [v for v in verdicts if is_high_confidence_contradiction(v)]
    )
    if not displayed:
        # No early return (#441): the partial-batch failure epilogue below
        # must run after every display path, exactly as in `adjudicate`.
        typer.echo("No high-confidence contradictions found.")

    for result in displayed:
        # surface-merged-body-contradictions (#409): `merged_absorbed_id` is
        # the SOLE discriminator between a typed-edge verdict and an
        # intra-document (merged-body) verdict -- NEVER `pair_ids` shape
        # (`ContradictionVerdict.merged_absorbed_id`'s docstring warning). A
        # `(x, x)` `pair_ids` is separately reachable today via an ordinary
        # typed self-loop (#411), so branching on pair equality here would
        # conflate the two.
        if result.merged_absorbed_id is not None:
            survivor_id, _ = result.pair_ids
            typer.echo(
                f"[{result.verdict.value.upper()}] {survivor_id} "
                f"(merged content, absorbed {result.merged_absorbed_id}) "
                f"(confidence: {result.confidence:.2f})"
            )
            # Name the verb that resolves the condition (#445, same shape as
            # #386's advisory ladder). A pair verdict needs no pointer: the
            # operator can open both files. A merged-content verdict has ONE
            # node, and the disagreeing second body lives in the ledger where
            # no ordinary read will surface it -- `unmerge` is the only verb
            # that separates them, and it takes exactly these two ids.
            #
            # The LIFO qualifier is not decoration (review finding on this
            # change): `resolution.contradiction` raises ONE candidate PER
            # `merged_from` entry, not just the newest, while
            # `bundle.merge.plan_unmerge` refuses any `absorbed_id` that is
            # not the ledger's tail. On a survivor with two unreversed
            # merges, the older verdict's command therefore REFUSES -- so the
            # line states the precondition instead of promising success.
            typer.echo(
                f"  next: openkos unmerge {survivor_id} {result.merged_absorbed_id}"
                " (LIFO-enforced: refuses unless this is the survivor's "
                "most recent unreversed merge)"
            )
        else:
            source_id, target_id = result.pair_ids
            typer.echo(
                f"[{result.verdict.value.upper()}] {source_id} <-> {target_id} "
                f"(confidence: {result.confidence:.2f})"
            )
        for claim in result.conflicting_claims:
            typer.echo(f"  - {claim}")
        typer.echo(f"  rationale: {result.rationale}")
        typer.echo()

    if batch.failure is not None:
        # Partial batch (#441): the report above already rendered the
        # completed verdicts exactly as a complete run over that list -- the
        # paid-for work is never discarded -- so all that remains is the one
        # stderr failure line and the OllamaError-family exit code.
        _echo_contradictions_batch_failure(batch, total=plan.llm_calls, model=cfg.model)
        raise typer.Exit(code=1) from batch.failure


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
            # `okf.concept_path_for`, not `bundle_dir / f"{id}.md"` (#473):
            # citation ids come out of `okf.concept_id_for` and are NFC, while
            # the name on disk may be decomposed on a byte-exact filesystem.
            # A direct read of the NFC spelling misses a file that exists,
            # falls into the fail-closed `except` below, and folds a READABLE
            # citation's sensitivity to `confidential` -- fail-closed is for
            # documents that cannot be verified, not for a spelling mismatch
            # the rest of the pipeline already tolerates.
            text = okf.concept_path_for(citation.concept_id, bundle_dir).read_text(
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


@app.command(
    help=(
        "Answer a natural-language question from the bundle, with citations "
        "back to the documents the answer came from."
    ),
    rich_help_panel="Explore",
)
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

    Read-only WITHOUT `--save`, like `status` and `lint`: no writes, no
    confirmation prompt, no `--auto`. `--save` is the sole exception and
    brings all three with it (see below). Must be run inside an initialized
    workspace; outside one it refuses (exit 1) with a short reason on
    stderr. Retrieval fuses TWO lists: lexical (FTS5) hits and dense
    (`vectors.db`) hits -- both read PERSISTED, read-only on-disk indexes
    under `.openkos/` (`fts.db`, `vectors.db`) that `reindex` maintains,
    rather than rebuilding anything in-process per call (Slice 5, PR3).
    `query` never WRITES to either derived store -- an absent or
    unavailable/corrupt store degrades cleanly (FTS falls back to dense-only;
    dense falls back to FTS-only), never creating or repairing one; only
    `reindex` writes.

    There was a THIRD list until issue #434: a second-stage seeded
    personalized-PageRank pool read from `.openkos/graph.db`. It is gone
    from retrieval because centrality is not relevance -- it repeatedly
    seated the corpus's most central concept at the cost of a real hit, once
    evicting the very document that answered the question. `graph.db` is
    still built by `reindex` and still backs contradiction candidates;
    `query` simply no longer opens it.

    Every completed run (successful answer or no-match) prints a one-line
    `retrieval:` summary to STDERR reporting the raw FTS hit count, the raw
    dense hit count, the fused count, whether the LLM was invoked, and how
    many sources were cited -- so a silent short-circuit (e.g. zero hits, so
    the LLM never ran) is always visible, even though STDOUT stays
    pipe-clean. When either derived index is absent or unavailable/corrupt
    (FTS or dense), an additional stderr line hints at running
    `openkos reindex` to enable full retrieval -- `query` itself never
    recomputes or compares the bundle's manifest hash to reach this
    decision; staleness detection is `reindex`'s exclusive job (D2). When
    the FTS index build skipped any unreadable/unparseable files (at the
    LAST `reindex` run), an `index:` skip-notice block follows the summary
    on stderr, worded as a whole-bundle build diagnostic -- it never implies
    the skipped files were candidates for THIS query's match.

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
    (status-aware-retrieval) are excluded from both retrieval channels
    (lexical, dense) BEFORE fusion -- the `retrieval:` stderr summary and
    every count in it (FTS/dense/fused/cited) already report the POST-filter
    values, since filtering happens inside `answer()` before those counts
    are captured.

    Unless `--include-confidential` is passed, confidential concepts
    (sensitivity-fail-closed-filter) are likewise excluded from every
    retrieval channel before fusion, exactly like a deprecated concept.

    `--save` files the just-printed cited answer as a new concept, and is
    the only writing path here. It previews the three paths it would touch,
    then gates on the usual precedence: `--auto` skips the prompt outright;
    otherwise config `review: false` skips it the same way; otherwise a TTY
    prompts via `typer.confirm`; otherwise it refuses (exit 1).

    Past that gate -- and on the runs that skip it, since `--auto` and
    `review: false` skip the prompt but not the window it stood in --
    `_reject_drifted_targets` re-reads `index.md` and `log.md` and refuses
    the WHOLE run (exit 3, nothing written) if either changed or vanished
    since Phase A read it (issues #306, #313, #319). The answer document
    itself is written create-only (`fsio.write_exclusive`), which already
    fails closed if something appeared at that path meanwhile, so it needs
    no entry in the guard and has no Phase-A bytes to give one.

    Phase B is NOT transactional, matching `ingest`/`set-sensitivity`'s
    documented limitation (#331): three writes -- the answer document, then
    `index.md`, then `log.md` -- with no rollback across the sequence.
    Content-before-catalog ordering is why a partial result is benign: the
    catalog never references a missing file; the worst partial state is an
    uncataloged answer document (or a filed-and-indexed one missing only
    its `log.md` line). A mid-sequence failure names exactly the paths that
    already landed ("Already written (left partially filed, not rolled
    back): ..."), or "No path was written." when the first write failed --
    so the operator knows which state they are in without diffing. On
    success the three paths are auto-committed like every other mutating
    verb (workspace-autocommit; the previous exclusion had no documented
    rationale, #331); a PARTIAL result is not captured in a commit, since
    `_autocommit` runs only on the success path -- recover a partial with
    `git status`/`git checkout` as with any sibling's mid-write failure.
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

    llm = _chat_client(cfg)
    embedder = OllamaClient(model=cfg.embedding_model)
    _warn_if_nonlocal_embed_host("query", embedder.locality)
    # The CHAT client decides the exemption, not the embedder: the
    # confidential concept bodies travel in the `llm.chat` payload (#240).
    local_exemption = _resolve_local_exemption(llm, cfg)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    vector_store_cm, store_was_unavailable = _open_vector_store_or_degrade(
        layout.vectors_db_path
    )
    fts_index_cm, fts_was_unavailable = _open_fts_or_degrade(layout.fts_db_path)
    with (
        vector_store_cm as vector_store,
        fts_index_cm as fts_index,
    ):
        # #381: named BEFORE the LLM call, not after it -- the user is told
        # their answer is suspect while they are still waiting for it,
        # rather than after having read it and trusted it. This is the CLI
        # seam that already owns the open-failure-to-`None` decision, so the
        # D2 binding contract holds: `answer()` below still never computes
        # or compares a manifest hash of its own. #436: `query` declares
        # only `fts` -- it stopped reading `graph.db` in #434, so graph
        # staleness cannot degrade THIS answer and must not be blamed here
        # (`status`/`next` still report it as workspace state).
        stale_stores = _stale_index_names(layout, reads=("fts",))
        if stale_stores:
            typer.echo(
                f"warning: derived indexes are stale ({', '.join(stale_stores)}) "
                "-- this answer may be degraded; run `openkos reindex`.",
                err=True,
            )
        # ONE TTY-gated stage notice before the single long retrieval+answer
        # call (issue #190) -- `query`'s `llm.chat` runs inside `answer()`,
        # so this CLI seam is where the wait becomes visible; `stage_notice`
        # is the single-call sibling of `progress_callback`.
        observability.stage_notice("query", "answering (waiting on the LLM)...")
        try:
            result = answer(
                question,
                bundle_dir=layout.bundle_dir,
                llm=llm,
                embedder=embedder,
                vector_store=vector_store,
                fts_index=fts_index,
                limit=limit,
                include_deprecated=include_deprecated,
                include_confidential=include_confidential,
                local_exemption=local_exemption,
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
    # Two retrieval terms, because there are two retrieval channels. The
    # summary used to carry a third, `<n> graph-added` from
    # `graph_contributed_count` -- how many reserved tail slots the seeded
    # personalized-PageRank channel filled with concepts FTS and dense never
    # found. Issue #434 removed the channel: measured over 10 questions the
    # slot it claimed was 7 times harmful, 3 times neutral and never
    # beneficial, because PageRank centrality is a property of the corpus,
    # not of the question. There is no term to print because there is no
    # third list, and no graph-degrade note below for the same reason.
    typer.echo(
        f"retrieval: {result.fts_hit_count} FTS + {result.dense_hit_count} "
        f"dense → "
        f"{result.fused_count} fused → LLM {llm_status} → {cited_count} cited",
        err=True,
    )
    if store_was_unavailable or fts_was_unavailable or result.dense_degraded:
        typer.echo(
            "hint: one or more derived indexes are unavailable this run -- "
            "run `openkos reindex` to enable full retrieval.",
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
        # One `_snapshot_read` observation per target: the decoded text
        # feeds the parsers below, the raw bytes feed
        # `_reject_drifted_targets` (issues #306, #313, #318).
        index_bytes, index_text = _snapshot_read(save_index_path)
        log_bytes, log_text = _snapshot_read(save_log_path)
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

    # Issue #313: every byte below was computed from a pre-prompt read, so
    # re-validate each target now -- after the gate, before the first write.
    # `plan.path` is absent by necessity, not oversight -- see the docstring.
    _reject_drifted_targets(
        layout,
        {save_index_path: index_bytes, save_log_path: log_bytes},
        "query",
    )

    answer_rel = f"bundle/{plan.link_dir}/{plan.slug}.md"
    landed: list[str] = []
    try:
        # Write order: answer document BEFORE `index.md` BEFORE `log.md`
        # (content before catalog, mirroring `ingest`'s D3): a mid-sequence
        # failure can leave an uncataloged file on disk, never a catalog
        # entry pointing at a file that does not exist. There is no
        # cross-file rollback, matching every other mutating verb's
        # documented limitation. `landed` records each path only AFTER its
        # write returns, so a failure names exactly the paths already on
        # disk (#331, mirroring `set-sensitivity`'s D9 shape).
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        fsio.write_exclusive(plan.path, plan.content)
        landed.append(answer_rel)
        fsio.write_atomic(save_index_path, new_index_text)
        landed.append("bundle/index.md")
        fsio.write_atomic(save_log_path, new_log_text)
        landed.append("bundle/log.md")
    except (OSError, ValueError) as exc:
        # Distinct from the refusal phases above on purpose (#234): this is
        # reached only after the write phase began, so the answer document
        # may already be on disk while the catalog is not. "refusing" would
        # tell an operator nothing happened, which is exactly wrong here.
        landed_suffix = (
            f"Already written (left partially filed, not rolled back): "
            f"{', '.join(landed)}."
            if landed
            else "No path was written."
        )
        typer.echo(
            f"openkos query: failed while saving the answer -- {exc}. {landed_suffix}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"openkos query: filed answer as bundle/{plan.link_dir}/{plan.slug}.md "
        f"({save_index_path.name}, {save_log_path.name} updated). Run "
        "`openkos reindex` to make it searchable."
    )

    # #331: `query --save` was the ONE mutating path without the
    # workspace-autocommit safety net, for no documented reason -- the
    # Slice-2 exclusion list (workspace-autocommit spec: "Exclusions and
    # Unconditional Behavior") names `reindex` output, `init`, and
    # read-only verbs only, and `query --save` simply postdated the
    # planning that produced the six-verb roster. Same call shape as every
    # sibling: the exact Phase-B paths, workspace-relative POSIX, scoped
    # `git add -- <paths>`, best-effort and non-fatal.
    _autocommit(
        root,
        [answer_rel, "bundle/index.md", "bundle/log.md"],
        f"openkos: query --save {plan.link_dir}/{plan.slug}",
    )


@app.command(
    help=(
        "Rebuild the local search indexes from the bundle's documents, so "
        "query and duplicate detection see current content."
    ),
    rich_help_panel="Maintain",
)
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
    _warn_if_nonlocal_embed_host("reindex", embedder.locality)
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
                # TTY-gated per-doc embedding progress on stderr; `None`
                # (silent) when output is piped (issue #190, mirrors
                # `suggest-relations`' #134 per-edge line).
                on_progress=observability.progress_callback("reindex", "embedding doc"),
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


@app.command(
    help=(
        "Check this machine's environment: whether the model backend is "
        "reachable, correctly configured, and running where you expect."
    ),
    rich_help_panel="Maintain",
)
def doctor() -> None:
    """Read-only environment health scan: fixed checks against the local
    workspace and local Ollama, printed as `[PASS]`/`[FAIL]`/`[SKIP]` lines
    with actionable remediation, usable even before `openkos init`.

    Deliberately NEW control-flow shape versus `status`/`lint`/`query`:
    instead of exiting on the first failure, this runs ALL twelve checks,
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
    Ollama; (11) backend-host-locality -- informational, always, via the
    check-(3) client's own `OllamaClient.locality` (issue #240), reporting
    the REDACTED `display_host`, whether it is this machine, and whether the
    confidential local exemption is consequently active. It is `[PASS]` while
    Ollama is reachable and `[SKIP]` when it is not (#389) -- not because it
    cannot answer without the server, but because a green line printed
    directly beneath `[FAIL] Ollama reachable` reads as a contradiction to
    anyone scanning the column. It is NEVER `[FAIL]`: a non-local backend is
    a legitimate configuration, not a fault, so the status only reports
    whether the check was verified and the DETAIL carries the finding, on
    both branches. It can therefore never change the exit code.
    Outside a workspace, checks (3)/(4)/(5)/(8)/(9)/(10)/(11) still run
    against `config.DEFAULT_MODEL`/`config.DEFAULT_EMBEDDING_MODEL`/
    `config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION` and (3)/(4) still
    determine the exit code (spec: Doctor Works Outside An Initialized
    Workspace).

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

    # 5b. task-models-installed (informational, always; SKIP-blocked if
    # unreachable, same D6 one-root-cause rationale as checks 4 and 5).
    #
    # ONE check covering every per-task model rather than one check per task
    # (issue #513): the check COUNT stays fixed regardless of how many tasks
    # a workspace keys, which is what lets the doctor spec keep pinning a
    # total. Only models DIFFERING from the global tag are examined -- a task
    # resolving `cfg.model` is already covered by check 4, and reporting it
    # twice would double-count one root cause.
    #
    # Informational, never critical: a missing per-task model fails only the
    # stage that named it (#515 decision 2), so `ingest`, `query`, and
    # `adjudicate` all still work. Exiting 1 on a workspace that is fine for
    # every other verb would be a false alarm rather than a diagnosis.
    task_models = {
        task: config.resolve_task_model(cfg, task)
        for task in sorted(config.TASK_MODEL_KEYS)
        if cfg is not None
    }
    if cfg is None:
        task_models = {
            task: tag
            for task, tag in config.DEFAULT_TASK_MODELS.items()
            if isinstance(tag, str)
        }
    extra_models = {task: tag for task, tag in task_models.items() if tag != model}
    task_label = "Task models installed"
    if not extra_models:
        results.append(
            CheckResult(
                task_label,
                "pass",
                critical=False,
                detail="none configured beyond the global model",
            )
        )
    elif not reachable:
        results.append(
            CheckResult(
                task_label,
                "skip",
                critical=False,
                detail="blocked: Ollama unreachable",
            )
        )
    else:
        missing = {
            task: tag
            for task, tag in extra_models.items()
            if not model_tag_matches(tag, installed_tags)
        }
        if missing:
            named = ", ".join(f"{task} -> {tag}" for task, tag in missing.items())
            results.append(
                CheckResult(
                    task_label,
                    "fail",
                    critical=False,
                    detail=f"missing: {named}",
                    remediation=" && ".join(
                        f"ollama pull {tag}" for tag in dict.fromkeys(missing.values())
                    ),
                )
            )
        else:
            named = ", ".join(f"{task} -> {tag}" for task, tag in extra_models.items())
            results.append(
                CheckResult(task_label, "pass", critical=False, detail=named)
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

    # 11. backend-host-locality (informational, always; ALWAYS EMITTED).
    # Reuses the SAME `client` check 3 built, so what is reported is the
    # host `doctor` itself would have sent to, never a re-derivation. It
    # skips when Ollama is unreachable despite being ABLE to answer without
    # the server: locality is a literal-form check over the host the client
    # already resolved, so the skip is about how the line READS beside a
    # failure, not about an inability to answer.
    #
    # NEVER `fail` (issue #240): `[FAIL]` on a non-local backend would call a
    # legitimate configuration broken, and a failing status invites a future
    # reader to make this check critical, which would let an informational
    # report flip an exit code that scripts gate on.
    #
    # Both terms are named separately because they are distinct facts and a
    # user debugging "why is my confidential concept in the prompt" needs to
    # know WHICH one decided it. Outside a workspace the packaged
    # `confidential_local_exemption` default applies, mirroring how checks
    # 3-5 fall back to the packaged model tags.
    locality = client.locality
    exemption_enabled = (
        cfg.confidential_local_exemption
        if cfg is not None
        else config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION
    )
    where = "this machine" if locality.is_local else "not this machine"
    exemption_state = (
        "active" if (locality.is_local and exemption_enabled) else "inactive"
    )
    # The detail differs per branch so the configured host survives the skip:
    # only the claim that anything was VERIFIED goes away (#389).
    results.append(
        CheckResult(
            "Backend host locality",
            "pass" if reachable else "skip",
            critical=False,
            detail=(
                f"{where} ({locality.display_host}); confidential local "
                f"exemption {exemption_state}"
                if reachable
                else (
                    f"configured for {where} ({locality.display_host}); not "
                    "verified while Ollama is unreachable; confidential local "
                    f"exemption {exemption_state}"
                )
            ),
        )
    )

    # 12. merge-ledger-torn-writes (informational, workspace-only; SKIP
    # outside -- Check A, design Decision 5: mechanically exact, zero false
    # positives/negatives). A `.pending` marker means a two-phase write was
    # interrupted mid-flight; `doctor` PREVIEWS what `recover` would decide
    # (`bundle_ledger.scan_torn_writes`, read-only) but never repairs --
    # only the repair verb (1b) writes.
    if in_workspace:
        torn = bundle_ledger.scan_torn_writes(config.WorkspaceLayout(root).bundle_dir)
        if torn:
            results.append(
                CheckResult(
                    "Merge ledger torn writes",
                    "fail",
                    critical=False,
                    detail=f"{len(torn)} pending marker(s)",
                    remediation="openkos repair",
                )
            )
        else:
            results.append(
                CheckResult("Merge ledger torn writes", "pass", critical=False)
            )
    else:
        results.append(CheckResult("Merge ledger torn writes", "skip", critical=False))

    # 13. merge-ledger-entries-free-of-post-merge-mutation (informational,
    # workspace-only; SKIP outside -- Check B, design Decision 5: doctor-
    # command spec "Merge-Ledger Integrity Check"). Nested-prefix equality
    # over every committed sidecar (`bundle_ledger.scan_nesting_violations`,
    # read-only); a `[FAIL]` names BOTH remedies -- the repair verb (a
    # ledger merely unmigrated, not corrupted) and `git reset --hard
    # <first-merge>~1` + `openkos reindex` (a ledger the check judges
    # corrupted) -- and states pre-fix reversibility is not guaranteed.
    # The git-reset half is gated on `vcs_git.has_reset_point` (gap fix,
    # task 2.4/2.5): `_autocommit` is best-effort and silently no-ops with
    # no repo, no configured git identity, or any `GitError`/`OSError`, so
    # a workspace that never actually committed has no reset point at all
    # -- printing that remedy unconditionally would name a command that
    # cannot work.
    if in_workspace:
        bundle_dir = config.WorkspaceLayout(root).bundle_dir
        violations = bundle_ledger.scan_nesting_violations(bundle_dir)
        if violations:
            if vcs_git.repo_root(root) is not None and vcs_git.has_reset_point(root):
                reset_remedy = (
                    "run `git reset --hard <first-merge>~1` then `openkos reindex`"
                )
            else:
                reset_remedy = (
                    "no git reset point is available in this workspace (no "
                    "repository, no configured git identity, or no commit "
                    "history) -- there is no remedy that restores "
                    "reversibility for the affected merge(s)"
                )
            results.append(
                CheckResult(
                    "Merge ledger entries free of post-merge mutation",
                    "fail",
                    critical=False,
                    detail=f"{len(violations)} entr{'y' if len(violations) == 1 else 'ies'}",
                    remediation=(
                        "if a ledger is merely unmigrated (still embedded in "
                        "the survivor's own frontmatter, not corrupted), run "
                        f"`openkos repair`; if corrupted, {reset_remedy} -- "
                        "reversibility of merges made before this fix is not "
                        "guaranteed"
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    "Merge ledger entries free of post-merge mutation",
                    "pass",
                    critical=False,
                )
            )
    else:
        results.append(
            CheckResult(
                "Merge ledger entries free of post-merge mutation",
                "skip",
                critical=False,
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


@app.command(
    help=(
        "Migrate legacy, frontmatter-embedded merge ledgers into "
        "bundle/.state/ledger/, refusing on any sign of a torn write or "
        "cross-survivor pollution risk."
    ),
    rich_help_panel="Maintain",
)
def repair() -> None:
    """Read-write migration verb (durable-derived-state slice 1b): extracts
    every survivor's OWN frontmatter-embedded `merged_from` ledger (pre-
    relocation, unmigrated) into its `bundle/.state/ledger/` sidecar,
    VERBATIM, and strips the `merged_from` key from the survivor's own
    frontmatter -- nothing else about the survivor changes.

    TWO refusal gates, BOTH with NO override flag at all (unlike `merge`'s
    `--force`): migrating a corrupted ledger verbatim would convert a
    git-revertible bug into a permanent durable fact, so this verb is
    deliberately MORE conservative than `merge`/`unmerge`'s own refusals.

    Gate 1 (Check A, torn write): any `.pending` marker anywhere in the
    bundle refuses the WHOLE run -- `openkos doctor` names the affected
    survivor(s); `openkos repair` does not repair a torn write itself
    (`bundle_ledger.recover` does, on the NEXT `merge`/`unmerge` that
    touches that survivor).

    Gate 2 (cross-survivor-pollution gate, design Decision 5): refuses the
    WHOLE run whenever ANY survivor bundle-wide -- migrated OR unmigrated
    -- carries 2 or more entries, regardless of what Check B's per-ledger
    nested-prefix check would have found on its own. Deliberately coarser
    than Check B: a merge of X into Y can rewrite bytes inside a THIRD
    survivor Z's embedded snapshot (`merge_core`'s `other_files`,
    `cli/main.py:6542`), a corruption Check B cannot see at every index.

    Before writing, reports whether this run's own effect is undoable via
    `git reset --hard` (`vcs_git.has_reset_point`, the same gap-fix probe
    `doctor` uses): `_autocommit` is best-effort and silently no-ops with
    no repo, no configured git identity, or any `GitError`/`OSError`, so a
    workspace that never committed has no safety net for THIS run either.
    """
    root = Path.cwd()
    workspace_reason = config.require_workspace(root)
    if workspace_reason is not None:
        typer.echo(f"openkos repair: refusing to run -- {workspace_reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    bundle_dir = layout.bundle_dir

    torn = bundle_ledger.scan_torn_writes(bundle_dir)
    if torn:
        typer.echo(
            f"openkos repair: refusing to run -- {len(torn)} pending "
            "marker(s) found (a prior merge crashed mid-commit); this "
            "refusal has no override. Run `openkos doctor` to inspect, or "
            "`openkos merge`/`openkos unmerge` on the affected survivor to "
            "trigger recovery.",
            err=True,
        )
        raise typer.Exit(code=1)

    if bundle_ledger.bundle_wide_max_entries(bundle_dir) >= 2:
        typer.echo(
            "openkos repair: refusing to run -- at least one survivor in "
            "this bundle carries 2 or more merge-ledger entries. Migrating "
            "a possibly-corrupted ledger verbatim would convert a "
            "git-revertible bug into a permanent durable fact, so this "
            "refusal has NO override. Run `openkos doctor` to inspect; if "
            "it reports a corrupted ledger, its own remediation is the way "
            "forward, not this verb.",
            err=True,
        )
        raise typer.Exit(code=1)

    unmigrated = bundle_ledger.scan_unmigrated(bundle_dir)
    if not unmigrated:
        typer.echo(
            "openkos repair: nothing to migrate -- no unmigrated merge ledger found."
        )
        return

    if vcs_git.repo_root(root) is not None and vcs_git.has_reset_point(root):
        typer.echo(
            "openkos repair: this run's writes can be undone with `git "
            "reset --hard HEAD` before this run's own auto-commit lands, "
            "or `git reset --hard <commit-before-this-run>` after."
        )
    else:
        typer.echo(
            "openkos repair: WARNING -- no git reset point is available in "
            "this workspace (no repository, no configured git identity, or "
            "no commit history); this run's writes cannot be undone via "
            "git.",
            err=True,
        )

    touched: list[str] = []
    for concept_id, entries in unmigrated:
        bundle_ledger.write_entries(
            concept_id, bundle_dir, survivor_id=concept_id, entries=entries
        )
        sidecar_path = bundle_ledger.ledger_path_for(concept_id, bundle_dir)
        touched.append(f"bundle/{sidecar_path.relative_to(bundle_dir).as_posix()}")

        survivor_path = okf.concept_path_for(concept_id, bundle_dir)
        metadata, body = okf.load_frontmatter(survivor_path.read_text(encoding="utf-8"))
        metadata.pop(okf.MERGED_FROM_KEY, None)
        fsio.write_atomic(survivor_path, okf.dump_frontmatter(metadata, body))
        touched.append(f"bundle/{survivor_path.relative_to(bundle_dir).as_posix()}")

    typer.echo(
        f"openkos repair: migrated {len(unmigrated)} ledger"
        f"{'s' if len(unmigrated) != 1 else ''} to bundle/.state/ledger/."
    )

    _autocommit(
        root,
        touched,
        f"openkos: repair (migrate {len(unmigrated)} ledger(s) to "
        "bundle/.state/ledger/)",
    )


@app.command(
    help=(
        "Work through every pending decision in one guided session, in "
        "dependency order, so each answer informs the next."
    ),
    rich_help_panel="Curate",
)
def curate(
    auto: bool = typer.Option(
        False,
        "--auto",
        help=(
            "Auto-accept every stage's cost gate (model spend, never a "
            "per-item write consent -- see the write-consent note below)."
        ),
    ),
    include_confidential: bool = typer.Option(
        False,
        "--include-confidential",
        help="Include confidential concepts (excluded by default).",
    ),
    include_deprecated: bool = typer.Option(
        False,
        "--include-deprecated",
        help="Include deprecated and superseded concepts (excluded by default).",
    ),
    accept: str | None = typer.Option(
        None,
        "--accept",
        metavar="STAGES",
        help=(
            "Comma-separated stages whose per-item prompts are accepted in "
            "bulk (structure, metadata). Identity is never accepted in "
            "bulk: its merges delete a concept."
        ),
    ),
) -> None:
    """One dependency-ordered decision session over the five kinds of
    pending human judgment: Preconditions, Identity, Structure, Metadata,
    Contradictions (ADR-0005/ADR-0011 ordering; issue #266).

    A THIN command, mirroring `next`'s shape: the shared
    `config.require_workspace` gate, then `config.read_config`
    (`except (OSError, ValueError)`, the same lint parity every other verb
    keeps), then `observability.warn_if_walk_incomplete` exactly ONCE for
    the whole run (design D8) -- never per stage, since five identical
    incomplete-walk paragraphs in one session would be noise. The entire
    ranked engine -- `_STAGES`, the cost gate, the sequencer, and the
    end-of-run summary -- lives in `cli/curate.py`; this command builds one
    `CurateContext`, calls `run_curate`, and echoes `render_summary`
    verbatim.

    Preconditions probes `vectors.db` before Identity: missing or empty, it
    prints the starved-candidate-edges consequence plus an `openkos
    reindex` pointer and halts the ENTIRE run (exit 0, no later stage runs
    -- spec: Preconditions Stage Halts The Run). Every other stage's
    decline, empty queue, or `live=False` skip is scoped to that stage
    alone and never aborts the rest (spec: Stage Order Is A Product
    Invariant).

    Each LLM-costing stage prints its own cost line (`{n} {noun}(s) -> {n}
    LLM call(s)`) and asks for confirmation before contacting the model,
    unless `--auto` is passed; `--auto` consents to model SPEND only, NEVER
    to a per-item write. On a non-TTY run with `--auto`, a write stage
    (`writes: true`, e.g. Identity) declines its write walk and prints the
    corresponding standalone verb for unattended use instead
    (`openkos adjudicate --apply-same --confirm-count <n>`), while a
    read-only stage (Contradictions) runs and reports normally (spec:
    Per-Stage Cost Gate).

    Identity reuses the exact `find_candidates` / `adjudicate_candidates` /
    `_prepare_one_merge` / `_commit_one_merge` / `_reject_drifted_targets`
    building blocks `adjudicate --apply` already exercises (design D4/D6):
    an accepted SAME 2-member pair commits per-item, auto-committing before
    the next candidate; an N>2 group is never auto-merged -- the exact
    pairwise `openkos merge` commands are printed instead (spec: Identity
    Stage Reuses Merge Cores).

    `--include-confidential`/`--include-deprecated` are forwarded into
    every stage's underlying call, fail-closed by default (spec:
    Sensitivity Threading Is Fail-Closed).

    All five stages run fully as of slice 2 (design D10): Preconditions and
    Identity shipped in slice 1; Structure, Metadata, and Contradictions
    went `live=True` in slice 2 with real `probe`/`run` implementations, so
    each now states its cost and, once accepted, spends real model calls
    (spec: Slice Boundary). `curate` is not a CI gate: pending work never
    sets a non-zero exit. Exit codes: 0 normal (including every decline,
    empty queue, and the Preconditions halt), 1 on a workspace/config
    failure or a failed mid-walk write, 2 on a Typer usage error, 3 on a
    drift refusal (#319, propagated unchanged from
    `_reject_drifted_targets`)."""
    # `--accept`'s vocabulary is checked BEFORE the workspace gate, so a
    # typo is reported as itself rather than as a missing workspace --
    # `list`'s TYPE and `set-volatility`'s tier already refuse in this
    # order (issue #385).
    explicit_accept = curate_module.parse_accepted_stages(accept)

    root = Path.cwd()
    reason = config.require_workspace(root)
    if reason is not None:
        typer.echo(f"openkos curate: refusing to run -- {reason}.", err=True)
        raise typer.Exit(code=1)

    layout = config.WorkspaceLayout(root)
    try:
        cfg = config.read_config(root)
    except (OSError, ValueError) as exc:
        typer.echo(
            f"openkos curate: failed while reading the workspace -- {exc}.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    # Resolved from a client identical to the one `run_curate` builds lazily
    # for its `needs_llm` stages (same `cfg.model`, same host resolution),
    # because the stage PROBES apply the sensitivity filter before any
    # client exists -- see `CurateContext.local_exemption` (issue #240).
    # Constructing a client performs no I/O. Resolved BEFORE
    # `warn_if_walk_incomplete` (not just before `CurateContext`) so the
    # advisory can be told about this hatch too, the same way the other
    # five verbs already are.
    accepted_stages = curate_module.resolve_accepted_stages(
        explicit_accept, review=cfg.review
    )
    local_exemption = _resolve_local_exemption(_chat_client(cfg), cfg)
    observability.warn_if_walk_incomplete(
        layout.bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )

    ctx = curate_module.CurateContext(
        root=root,
        layout=layout,
        cfg=cfg,
        auto=auto,
        include_confidential=include_confidential,
        include_deprecated=include_deprecated,
        local_exemption=local_exemption,
        accepted_stages=accepted_stages,
    )
    outcomes = curate_module.run_curate(ctx)
    for line in curate_module.render_summary(outcomes):
        typer.echo(line)


PANEL_ORDER: Final[tuple[str, ...]] = (
    "Get started",
    "Explore",
    "Curate",
    "Maintain",
    "Remove",
)
"""The order help panels are printed in (#389).

Grouping alone did not fix the ordering half of that issue. Rich prints
panels in the order it FIRST meets a command belonging to each, which is
declaration order -- and `forget`/`purge` are declared early, so "Remove"
landed second and made the irreversible verbs MORE prominent than the flat
list did. This is the reading order instead: start, then ask, then decide,
then maintain, and only then delete.

Sorting the registry is what makes it explicit rather than a side effect of
where a function happens to sit in this file. The sort is stable, so order
WITHIN a panel is still declaration order."""


def _panel_rank(info: typer.models.CommandInfo) -> int:
    """Rank one command for the panel sort, failing LOUDLY and legibly.

    A bare `PANEL_ORDER.index(...)` raises at import time, which takes the
    whole CLI down -- every command, including `--help` -- for one typo, and
    the built-in message names neither the command nor the bad value
    (review finding on this change). Failing hard is still right: a
    misplaced command should not ship quietly. What was wrong was failing
    hard and mutely."""
    try:
        return PANEL_ORDER.index(info.rich_help_panel)
    except ValueError:
        name = info.name or (info.callback.__name__ if info.callback else "<unnamed>")
        raise RuntimeError(
            f"command {name!r} declares rich_help_panel="
            f"{info.rich_help_panel!r}, which is not one of {PANEL_ORDER}. "
            "Put the command in an existing panel, or add the new panel to "
            "PANEL_ORDER at the position it should be read in."
        ) from None


app.registered_commands.sort(key=_panel_rank)
