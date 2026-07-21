"""Workspace root: `openkos.yaml`, `AGENTS.md`, and the four refusal conditions.

A workspace is `openkos.yaml`, `AGENTS.md`, `raw/`, and `bundle/` at some
root directory (docs/architecture.md:141-154). `bundle/` is not a workspace
on its own -- `raw/` sits outside it by design, so the workspace root is
where the engine's own files live, not the OKF bundle root.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import NamedTuple

import yaml

from openkos import fsio

DEFAULT_MODEL = "qwen3:8b"
"""The packaged default Ollama model tag, offered when no `--model` is given."""

DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
"""The packaged default Ollama embedding model tag. Default-only for this
slice (hybrid-retrieval Slice 1): not written to `openkos.yaml.template`,
resolved solely via `read_config`'s `is not None` fallback, distinct from
the chat `DEFAULT_MODEL`."""

DEFAULT_REVIEW = True
"""Packaged default for `review`: show a preview and confirm before saving."""

DEFAULT_SENSITIVITY = "private"
"""Packaged default for `default_sensitivity`, matching `openkos.yaml.template`."""

DEFAULT_FRESHNESS_WINDOW = "7d"
"""Packaged default for `freshness_window`, matching `openkos.yaml.template`.

Raw passthrough only -- the `"7d"`/`"2w"` duration grammar is parsed by
`lint.parse_window`, not here (policy stays out of `config`)."""

_MODEL_TOKEN_RE = re.compile(r"[A-Za-z0-9._:/-]+")


def validate_model(tag: str) -> str:
    """Trim `tag` and reject any value unsafe to substitute into `openkos.yaml`.

    The assembled line `model: <VALUE>  # comment` must remain a valid
    single-line YAML plain scalar, so this validates via an ALLOWLIST rather
    than blocking individually known-bad characters: every character of the
    trimmed value must be a letter, digit, `.`, `_`, `:`, `/`, or `-`. Within
    that allowlist, a trailing colon (`qwen3:`) would still corrupt the line
    into `model: qwen3:  # ...`, whose `: ` is invalid YAML (a colon read as
    a mapping separator), and a leading colon or leading `-` would likewise
    be misread (an empty key, or a YAML block-sequence entry), so those three
    positions are rejected on top of the character allowlist. A colon in the
    middle stays allowed: Ollama's `name:tag` convention (`qwen3:8b`,
    `mistral:7b`) and the default `qwen3:8b` both rely on it.
    """
    trimmed = tag.strip()
    if not trimmed:
        raise ValueError("model must not be blank")
    if not _MODEL_TOKEN_RE.fullmatch(trimmed):
        raise ValueError(
            "model must not contain characters other than letters, digits, "
            "'.', '_', ':', '/', or '-'"
        )
    if trimmed.startswith(":") or trimmed.endswith(":"):
        raise ValueError("model must not start or end with ':'")
    if trimmed.startswith("-"):
        raise ValueError("model must not start with '-'")
    return trimmed


@dataclass(frozen=True)
class WorkspaceLayout:
    """The four paths init reads and writes at a workspace root, plus the
    engine's own cache paths (`openkos_dir`/`vectors_db_path`).

    The cache paths are PURE path derivation, like every property here --
    resolving them creates nothing on disk. Unlike the four init paths
    above, `openkos_dir`/`vectors_db_path` are never written by `init`
    (embedding-vector-store, Slice 2a): they are engine-cache paths a
    consumer (e.g. `state.vectorstore.open_vector_store`) creates lazily on
    first open, not part of a freshly initialized workspace's file set.
    """

    root: Path

    @property
    def config_path(self) -> Path:
        """`openkos.yaml`: the workspace marker (Q7.6) and layout declaration."""
        return self.root / "openkos.yaml"

    @property
    def agents_path(self) -> Path:
        """`AGENTS.md`: the engine's operating manual for this workspace."""
        return self.root / "AGENTS.md"

    @property
    def raw_dir(self) -> Path:
        """`raw/`: immutable sources, outside the OKF bundle."""
        return self.root / "raw"

    @property
    def bundle_dir(self) -> Path:
        """`bundle/`: the OKF bundle root."""
        return self.root / "bundle"

    @property
    def openkos_dir(self) -> Path:
        """`.openkos/`: the engine's own on-disk cache directory (e.g. the
        vector store). NOT created by `init` -- a consumer creates it lazily
        on first open."""
        return self.root / ".openkos"

    @property
    def vectors_db_path(self) -> Path:
        """`.openkos/vectors.db`: the sqlite-vec vector store database."""
        return self.openkos_dir / "vectors.db"

    @property
    def fts_db_path(self) -> Path:
        """`.openkos/fts.db`: the persisted FTS5 derived index (Slice 5).

        Mirrors `vectors_db_path`'s pure-derivation contract: written ONLY
        by `state.reindex.reindex`, lazily -- this property never creates
        anything on disk by itself."""
        return self.openkos_dir / "fts.db"


class RefusalCondition(NamedTuple):
    """One reason `init` might refuse to write, with its workspace classification."""

    marks_workspace: bool
    reason: str


def _refusal_conditions(root: Path) -> Iterator[RefusalCondition]:
    """The ONE place that defines every reason `init` might refuse to write at `root`.

    Yields `RefusalCondition(marks_workspace, reason)` in priority order --
    the first item is the reason `refusal_reason` reports. `marks_workspace`
    is `True` for the four conditions the spec names as "already a
    workspace" (existing `openkos.yaml`, existing `AGENTS.md`, non-empty
    `raw/`, non-empty `bundle/`) and `False` for the two that answer a
    different question: `raw` or `bundle` already exists as a plain file, or
    as a symlink. Neither is a workspace, yet init still cannot write there.
    For the plain-file case, `Path.mkdir` would raise `FileExistsError` --
    an `OSError` Phase B (`cli/main.py`) DOES catch, so nothing goes
    uncaught, but without this pre-flight condition the failure would only
    surface as the generic "failed while creating the workspace" message,
    and only after any earlier Phase-B writes had already landed. For the
    symlink case, `Path.mkdir`/`open("x")` would follow the link, letting
    init write through it into whatever directory or file the symlink
    targets -- potentially outside the workspace root entirely -- instead
    of refusing outright. `is_workspace` and `refusal_reason` both read this
    generator, so extending what counts as either question changes both at
    once instead of the two silently drifting apart.
    """
    layout = WorkspaceLayout(root)
    if layout.config_path.exists():
        yield RefusalCondition(
            True, f"'{layout.config_path.name}' already exists in this directory"
        )
    if layout.agents_path.exists():
        yield RefusalCondition(
            True, f"'{layout.agents_path.name}' already exists in this directory"
        )
    for path in (layout.raw_dir, layout.bundle_dir):
        if path.is_symlink():
            yield RefusalCondition(False, f"'{path.name}' is a symlink")
        elif path.exists() and not path.is_dir():
            yield RefusalCondition(
                False, f"'{path.name}' exists and is not a directory"
            )
        elif path == layout.bundle_dir and _non_empty_dir(path):
            yield RefusalCondition(
                True,
                f"'{path.name}/' already exists and is not empty; a previous init "
                "may have crashed mid-write -- inspect and remove it before retrying",
            )
        elif _non_empty_dir(path):
            yield RefusalCondition(
                True, f"'{path.name}/' already exists and is not empty"
            )


def is_workspace(root: Path) -> bool:
    """True if `root` already looks like an initialized (or partially seeded) workspace.

    Checks the four refusal conditions the spec names: an existing
    `openkos.yaml`, an existing `AGENTS.md`, or a non-empty `raw/` or
    `bundle/`. A directory holding unrelated files but none of these is NOT
    a workspace -- init may adopt it.
    """
    return any(marks_workspace for marks_workspace, _ in _refusal_conditions(root))


def refusal_reason(root: Path) -> str | None:
    """Return why `init` must refuse to write at `root`, or `None` if it may proceed."""
    return next((reason for _, reason in _refusal_conditions(root)), None)


_NO_WORKSPACE_REASON = (
    "no OpenKOS workspace found in this directory (run 'openkos init' first)"
)


_UNREADABLE_WORKSPACE_REASON_PREFIX = "OpenKOS workspace files at"
"""Prefix for the distinct permission-denied reason `require_workspace`
returns -- kept as a separate constant from `_NO_WORKSPACE_REASON` because
the two cases are NOT the same: this one means the workspace exists but
could not be inspected, not that it is missing."""


def require_workspace(root: Path) -> str | None:
    """Return `None` if `root` already holds an initialized workspace, else
    the exact refusal reason string every read-only command shares (D1).

    `None` means both `bundle/index.md` and `bundle/log.md` are `is_file()`
    at `root` -- the same check `ingest` performed inline before this
    extraction. `is_file()` only swallows `ENOENT`/`ENOTDIR`/`EBADF`/`ELOOP`
    into `False`; it RE-RAISES any other `OSError`, notably
    `PermissionError` on a bundle directory or file this process cannot
    stat. That case is caught here and reported as a DISTINCT reason (never
    `_NO_WORKSPACE_REASON`, since the workspace demonstrably exists -- it
    just could not be read) so callers never see a raw traceback. Callers
    (`ingest`, `status`) format their own command-specific prefix around
    this reason; `config` stays free of `typer` (layering).
    """
    layout = WorkspaceLayout(root)
    index_path = layout.bundle_dir / "index.md"
    log_path = layout.bundle_dir / "log.md"
    try:
        both_files_present = index_path.is_file() and log_path.is_file()
    except OSError as exc:
        return (
            f"{_UNREADABLE_WORKSPACE_REASON_PREFIX} '{layout.bundle_dir}' "
            f"could not be read ({exc})"
        )
    if not both_files_present:
        return _NO_WORKSPACE_REASON
    return None


def _non_empty_dir(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _read_template(filename: str) -> str:
    """Read a packaged template from `src/openkos/templates/` (D4).

    Uses `importlib.resources`, never `__file__` or a relative path, so this
    works identically from an editable install and from an installed wheel.
    """
    return (resources.files("openkos") / "templates" / filename).read_text(
        encoding="utf-8"
    )


def write_agents(root: Path) -> None:
    """Write a byte-identical copy of the packaged `AGENTS.md` template at `root`.

    Exclusive-create mode ("x"): a colliding file raises `FileExistsError`
    instead of being overwritten (D2), matching `bundle.create`'s guarantee.
    """
    content = _read_template("agents.md.template")
    layout = WorkspaceLayout(root)
    fsio.write_exclusive(layout.agents_path, content)


_MODEL_PLACEHOLDER = "__OPENKOS_MODEL__"


def write_config(root: Path, model: str = DEFAULT_MODEL) -> None:
    """Write the packaged `openkos.yaml` template at `root`, with `model`
    substituted for the template's single `__OPENKOS_MODEL__` placeholder.

    `model` is the ONLY user-selectable field (D5): every other line is
    byte-identical to the packaged template regardless of the chosen model.
    The directory itself remains the single source of truth for the
    workspace's identity, so nothing in `openkos.yaml` is derived from
    `root`. Substitution is a single constrained `str.replace` on a known
    placeholder token -- never a YAML dumper or serializer -- so it cannot
    reformat, reorder, or fold any other line the way a round-trip through a
    YAML library could. `validate_model` runs first and raises `ValueError`
    before any file is written if `model` is blank or contains whitespace, a
    quote, or `#`. Exclusive-create mode ("x") never overwrites an existing
    file (D2).
    """
    validated_model = validate_model(model)
    template = _read_template("openkos.yaml.template")
    if template.count(_MODEL_PLACEHOLDER) != 1:
        raise ValueError(
            f"expected exactly one {_MODEL_PLACEHOLDER!r} placeholder in the "
            "packaged template"
        )
    content = template.replace(_MODEL_PLACEHOLDER, validated_model)
    layout = WorkspaceLayout(root)
    fsio.write_exclusive(layout.config_path, content)


@dataclass(frozen=True)
class Config:
    """The subset of `openkos.yaml` the engine reads back at runtime.

    Fields absent from the file fall back to the same packaged defaults
    `openkos.yaml.template` ships (D3): `DEFAULT_MODEL`, `DEFAULT_REVIEW`,
    `DEFAULT_SENSITIVITY`, `DEFAULT_FRESHNESS_WINDOW`. `embedding_model` is
    default-only (`DEFAULT_EMBEDDING_MODEL`): it is not part of
    `openkos.yaml.template`, but a user may hand-add the key to override it.
    """

    model: str
    review: bool
    default_sensitivity: str
    freshness_window: str
    embedding_model: str


def read_config(root: Path) -> Config:
    """Parse `openkos.yaml` at `root` and return its `model`/`review`/
    `default_sensitivity` fields, falling back to packaged defaults for any
    field the file omits OR sets to an explicit YAML null (D3).

    Uses `yaml.safe_load` -- never a loader that can construct arbitrary
    Python objects from untrusted YAML. A `yaml.YAMLError` (malformed YAML)
    or a root that parses but is not a mapping both raise `ValueError`, so
    callers can catch alongside `OSError` (a missing or unreadable file)
    with a single `except (OSError, ValueError)`, matching `init`'s
    convention. `raw.get(key, DEFAULT)` alone only falls back when `key` is
    ABSENT -- a key present with an explicit `key: null` (or bare `key:`)
    parses to `None`, which would otherwise violate `Config`'s typed fields.
    Each field is therefore checked `is not None` before falling back, not
    truthiness: `review: false` is a real value (`False is not None`), so it
    must survive untouched and never get coerced to `DEFAULT_REVIEW`.
    """
    layout = WorkspaceLayout(root)
    text = layout.config_path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{layout.config_path.name}: invalid YAML -- {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(
            f"{layout.config_path.name}: expected a mapping at the document root"
        )
    model = raw.get("model")
    review = raw.get("review")
    default_sensitivity = raw.get("default_sensitivity")
    freshness_window = raw.get("freshness_window")
    embedding_model = raw.get("embedding_model")
    return Config(
        model=model if model is not None else DEFAULT_MODEL,
        review=review if review is not None else DEFAULT_REVIEW,
        default_sensitivity=(
            default_sensitivity
            if default_sensitivity is not None
            else DEFAULT_SENSITIVITY
        ),
        freshness_window=(
            freshness_window
            if freshness_window is not None
            else DEFAULT_FRESHNESS_WINDOW
        ),
        embedding_model=(
            embedding_model if embedding_model is not None else DEFAULT_EMBEDDING_MODEL
        ),
    )
