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
from openkos.model import types

DEFAULT_MODEL = "qwen3:8b"
"""The packaged default Ollama model tag, offered when no `--model` is given."""

DEFAULT_EMBEDDING_MODEL = "bge-m3"
"""The packaged default Ollama embedding model tag (ADR-0006: reliability-
first -- `bge-m3` proved measurably more resilient to transient embed
failures than the previous `qwen3-embedding:0.6b` default). Written into
`openkos.yaml.template` via its own placeholder (`write_config`), and used
as `read_config`'s `is not None` fallback for an omitted or explicit-null
`embedding_model:` key, distinct from the chat `DEFAULT_MODEL`."""

EMBEDDING_MODEL_ALLOWLIST: tuple[str, ...] = (DEFAULT_EMBEDDING_MODEL,)
"""Vetted embedding model tags known to produce 1024-float vectors,
satisfying the `EMBED_DIM` contract (ADR-0006). Default first (=
recommended). This is policy data, not transport/classification, so it
lives here rather than `llm/ollama.py` (D1). Honesty rule: an entry is
added only after a measured 1024-dim embed -- never a guess. Gates ONLY
the interactive embedding-model picker's candidate list; it never gates
`--embedding-model` or a hand-written `openkos.yaml` value (see
`validate_embedding_model`, which checks YAML-safety alone)."""

DEFAULT_REVIEW = True
"""Packaged default for `review`: show a preview and confirm before saving."""

DEFAULT_SENSITIVITY = "private"
"""Packaged default for `default_sensitivity`, matching `openkos.yaml.template`."""

DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION = True
"""Packaged default for `confidential_local_exemption` (issue #240): a
`confidential` concept MAY be sent to an `llm.chat` backend that is
verifiably this machine, without `--include-confidential`.

`True` because local-first is the design: on a stock install Ollama runs on
loopback, nothing leaves the machine, and `sensitivity` -- which governs
EGRESS -- has nothing to protect against. Shipping this `False` would leave
every stock workspace in the state #240 opens by describing: confidential
objects silently absent from every retrieval and resolution pass, and users
trained to pass `--include-confidential` habitually, which disables the one
gate that would matter the day the backend is NOT local.

Setting it `false` restores the pre-#240 blanket behavior (`confidential`
means "no LLM ever, local or not"). It is deliberately a WORKSPACE-level key
rather than a per-command flag: a security policy that depends on
remembering to type a flag on every invocation is not a policy. It never
weakens the fail-closed posture on its own -- the exemption also requires
`OllamaClient.locality.is_local`, so an unknown or unparseable host is still
treated as remote regardless of this value."""

DEFAULT_FRESHNESS_WINDOW = "7d"
"""Packaged default for `freshness_window`, matching `openkos.yaml.template`.

Raw passthrough only -- the `"7d"`/`"2w"` duration grammar is parsed by
`lint.parse_window`, not here (policy stays out of `config`)."""

DEFAULT_CHAT_TIMEOUT = 600.0
"""Packaged default for `chat_timeout`, in seconds, matching
`openkos.yaml.template` and `llm.ollama.DEFAULT_TIMEOUT` (issue #405).

Raised from the 120s that shipped before. 120s was measured too low for the
documents this product targets: across a sweep of 9 sources x 4 sampling
arms x 5 runs, 8 calls timed out, every one of them on a 6-17 KB real
document, while none of the 700-800 B demo fixtures ever did.

This deadline is NOT a lever against runaway generation. The same
measurement found a fixture that timed out 5 of 5 at 120s and 5 of 5 again
at 300s under greedy decoding -- a model that never terminates is not
rescued by a longer wait, and raising this value further would only make the
failure slower to observe. That failure mode belongs to the prompt's
anti-enumeration instruction (#404), not here."""

DEFAULT_VOLATILITY_WINDOWS: dict[str, str] = {"slow": "90d", "volatile": "7d"}
"""Packaged per-tier default windows (freshness-lint-v1, design: "Per-tier
windows (CONCRETE, FINAL)"): `slow` = 90d, `volatile` = 7d -- continuity
with today's global default for fast-moving types. `static` has no window
value (never flagged), so it is never a key of this map. Raw passthrough
only, like `DEFAULT_FRESHNESS_WINDOW` -- the duration grammar and the
tier-resolution precedence are `lint.resolve_windows`'s job, not
`config`'s."""

_MODEL_TOKEN_RE = re.compile(r"[A-Za-z0-9._:/-]+")

_YAML_RESERVED_WORDS = frozenset({"yes", "no", "true", "false", "on", "off", "null"})
"""PyYAML's default `SafeLoader` resolver treats these exact tokens (any
casing) as `bool`/`None`, not a plain string -- an unquoted `model: yes` in
`openkos.yaml` therefore reads back as `model=True`, not `model="yes"`
(issue #128, defect #2). Rejected as a whole-token match only: a reserved
word appearing as a substring of a longer tag (`yesmodel`, `on-prem`) is
unaffected by this resolver behavior and stays valid."""


def _validate_model_token(tag: str, field: str) -> str:
    """Trim `tag` and reject any value unsafe to substitute into `openkos.yaml`.

    Shared body for `validate_model` and `validate_embedding_model` (D5):
    one source of truth for YAML-scalar safety, parameterized only by
    `field` for the raised messages. The assembled line
    `<field>: <VALUE>  # comment` must remain a valid single-line YAML plain
    scalar, so this validates via an ALLOWLIST rather than blocking
    individually known-bad characters: every character of the trimmed value
    must be a letter, digit, `.`, `_`, `:`, `/`, or `-`. Within that
    allowlist, a trailing colon (`qwen3:`) would still corrupt the line into
    `<field>: qwen3:  # ...`, whose `: ` is invalid YAML (a colon read as a
    mapping separator), and a leading colon or leading `-` would likewise be
    misread (an empty key, or a YAML block-sequence entry), so those three
    positions are rejected on top of the character allowlist. A colon in the
    middle stays allowed: Ollama's `name:tag` convention (`qwen3:8b`,
    `mistral:7b`) and the defaults `qwen3:8b`/`bge-m3` both rely on it.

    Checks safety only -- never allowlist membership. `validate_model` has
    no allowlist; `validate_embedding_model` deliberately does not gate on
    `EMBEDDING_MODEL_ALLOWLIST` here (D6) -- that gate applies to the
    interactive picker's candidates only.
    """
    trimmed = tag.strip()
    if not trimmed:
        raise ValueError(f"{field} must not be blank")
    if trimmed.lower() in _YAML_RESERVED_WORDS:
        raise ValueError(
            f"{field} must not be a YAML reserved word "
            "(yes/no/true/false/on/off/null) -- it would not round-trip as "
            "the literal string"
        )
    if not _MODEL_TOKEN_RE.fullmatch(trimmed):
        raise ValueError(
            f"{field} must not contain characters other than letters, digits, "
            "'.', '_', ':', '/', or '-'"
        )
    if trimmed.startswith(":") or trimmed.endswith(":"):
        raise ValueError(f"{field} must not start or end with ':'")
    if trimmed.startswith("-"):
        raise ValueError(f"{field} must not start with '-'")
    return trimmed


def validate_model(tag: str) -> str:
    """Trim `tag` and reject any value unsafe to substitute into `openkos.yaml`.

    See `_validate_model_token` for the full safety-check rationale.
    """
    return _validate_model_token(tag, "model")


def validate_embedding_model(tag: str) -> str:
    """Trim `tag` and reject any value unsafe to substitute into
    `openkos.yaml`'s `embedding_model:` line.

    Applies the SAME YAML-safety and reserved-word rejection as
    `validate_model` (see `_validate_model_token`), independent of
    `EMBEDDING_MODEL_ALLOWLIST` membership (D6): an off-allowlist value
    passed via `--embedding-model` must still pass this check and be
    written, never silently coerced to the default.
    """
    return _validate_model_token(tag, "embedding_model")


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

    @property
    def graph_db_path(self) -> Path:
        """`.openkos/graph.db`: the persisted graph projection (Slice 5, PR2).

        Mirrors `fts_db_path`'s pure-derivation contract: written ONLY by
        `openkos.graph.sqlite_graph.reindex_graph`, lazily -- this property
        never creates anything on disk by itself."""
        return self.openkos_dir / "graph.db"


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
_EMBEDDING_MODEL_PLACEHOLDER = "__OPENKOS_EMBEDDING_MODEL__"

_PLACEHOLDERS = (_MODEL_PLACEHOLDER, _EMBEDDING_MODEL_PLACEHOLDER)
"""The declared placeholder set -- the ONE place a new one is added (#210).

Both the substitution regex below and `write_config`'s per-placeholder count
guards are derived from this tuple, because the set used to be written out by
hand in three places and omitting any one of them failed SILENTLY: the count
guard only inspects the raw template, so it still passed; `re.sub` simply
never matched; and `openkos.yaml` was written with a literal
`__OPENKOS_SOMETHING__` in it. That file is still valid YAML, so `read_config`
parsed it and handed the caller a placeholder string as a real value.

One hand-written site remains by necessity -- `write_config`'s `substitutions`
mapping, which binds each token to a runtime argument this module-level tuple
cannot know. Omitting a token there is LOUD: the derived regex matches it and
the substitution callback raises `KeyError`."""

_PLACEHOLDER_RE = re.compile("|".join(re.escape(p) for p in _PLACEHOLDERS))
"""Matches any declared placeholder, so `write_config` substitutes in ONE pass.

Sequential `str.replace` calls would not be order-independent: the character
allowlist in `_MODEL_TOKEN_RE` admits `_`, so a validated `model` value may
itself equal the embedding placeholder token, and a later pass would overwrite
the value the earlier pass just wrote. A single pass never re-examines
substituted text."""

_TEMPLATE_PLACEHOLDER_RE = re.compile(r"__OPENKOS_[A-Z0-9_]*?__")
"""Any placeholder-SHAPED token, declared or not.

The body is LAZY. Greedy would run straight through a second token's
`__OPENKOS_` prefix -- every character of it is inside this same class --
and backtrack only to the final `__`, so two placeholders written back to
back would scan as ONE merged match belonging to no declaration, and the
check below would reject a template in which both are correctly declared and
each appears exactly once. Lazy stops at the first closing delimiter, which
is what a delimiter-bounded token means.

Closes the one hole deriving `_PLACEHOLDER_RE` cannot: a token added to the
packaged template that no constant declares. Every declaration-driven check
still passes in that case -- there is nothing to be inconsistent WITH -- and
the token is written through verbatim.

Applied to the TEMPLATE before substitution, never to the finished content
after it. A survivor scan of the output would be simpler and would be wrong:
`__OPENKOS_EMBEDDING_MODEL__` is a legal `model` value (the allowlist admits
`_`), and two tests pin that it round-trips intact, so an output scan would
refuse to write a config that is entirely correct."""


def write_config(
    root: Path,
    model: str = DEFAULT_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> None:
    """Write the packaged `openkos.yaml` template at `root`, with `model`
    and `embedding_model` substituted for the template's two independent
    placeholders (`__OPENKOS_MODEL__`, `__OPENKOS_EMBEDDING_MODEL__`).

    `model` and `embedding_model` are the ONLY user-selectable fields: every
    other line is byte-identical to the packaged template regardless of the
    chosen model(s). The directory itself remains the single source of
    truth for the workspace's identity, so nothing in `openkos.yaml` is
    derived from `root`. Substitution is ONE constrained pass over the two
    known placeholder tokens (`_PLACEHOLDER_RE`) -- never a YAML dumper or
    serializer -- so it cannot reformat, reorder, or fold any other line the
    way a round-trip through a YAML library could, and it never re-examines
    text it just substituted (see `_PLACEHOLDER_RE`). `validate_model` and
    `validate_embedding_model` each run first and raise `ValueError` before
    any file is written if their value is blank or contains whitespace, a
    quote, or `#`; `embedding_model` is validated for YAML-safety only,
    independent of `EMBEDDING_MODEL_ALLOWLIST` membership (D6).

    Two template checks run before any substitution, both derived from
    `_PLACEHOLDERS` rather than written per token (#210). First, the template
    may contain no placeholder-shaped token that nothing declares. Second,
    each declared placeholder must be matched exactly once -- counted by
    running `_PLACEHOLDER_RE` over the template, the SAME alternation that
    then performs the substitution, so the guard measures what the operation
    will actually do rather than what a substring search reports.

    Together they mean adding a placeholder is a single edit to
    `_PLACEHOLDERS` plus its template line and its `substitutions` entry,
    with every way of half-applying it raising rather than writing a literal
    token into `openkos.yaml`.

    Exclusive-create mode ("x") never overwrites an existing file (D2).
    """
    validated_model = validate_model(model)
    validated_embedding_model = validate_embedding_model(embedding_model)
    template = _read_template("openkos.yaml.template")
    undeclared = sorted(
        set(_TEMPLATE_PLACEHOLDER_RE.findall(template)) - set(_PLACEHOLDERS)
    )
    if undeclared:
        raise ValueError(
            "packaged template contains undeclared placeholder(s): "
            + ", ".join(repr(token) for token in undeclared)
        )
    # Counted with the SAME alternation that performs the substitution, never
    # with `str.count`. The two disagree: `str.count` counts OVERLAPPING
    # substrings, while `re.sub` consumes NON-OVERLAPPING. Two declared tokens
    # sharing one underscore pair (`__OPENKOS_MODEL__OPENKOS_EMBEDDING_MODEL__`)
    # are therefore each "present once" to `str.count` while substitution can
    # only ever consume the first -- writing the second through as literal
    # text, valid YAML, no error. A guard that does not measure what the
    # operation will actually do is not a guard.
    matched = _PLACEHOLDER_RE.findall(template)
    mismatched = [
        (placeholder, matched.count(placeholder))
        for placeholder in _PLACEHOLDERS
        if matched.count(placeholder) != 1
    ]
    if mismatched:
        raise ValueError(
            "packaged template placeholder mismatch: "
            + "; ".join(
                f"{placeholder!r} is matched {count} time(s) by the "
                "substitution scan, expected exactly one"
                for placeholder, count in mismatched
            )
        )
    substitutions = {
        _MODEL_PLACEHOLDER: validated_model,
        _EMBEDDING_MODEL_PLACEHOLDER: validated_embedding_model,
    }
    content = _PLACEHOLDER_RE.sub(lambda m: substitutions[m.group(0)], template)
    layout = WorkspaceLayout(root)
    fsio.write_exclusive(layout.config_path, content)


@dataclass(frozen=True)
class Config:
    """The subset of `openkos.yaml` the engine reads back at runtime.

    Fields absent from the file fall back to the same packaged defaults
    `openkos.yaml.template` ships (D3): `DEFAULT_MODEL`, `DEFAULT_REVIEW`,
    `DEFAULT_SENSITIVITY`, `DEFAULT_FRESHNESS_WINDOW`,
    `DEFAULT_EMBEDDING_MODEL`, and `DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION`. `embedding_model` IS part of
    `openkos.yaml.template`, written via its own placeholder by
    `write_config`; this fallback still applies when reading a
    pre-existing or hand-edited `openkos.yaml` that omits the key or sets
    it to an explicit YAML null.
    """

    model: str
    review: bool
    default_sensitivity: str
    freshness_window: str
    embedding_model: str
    chat_timeout: float
    """Seconds an `llm.chat` request may take before the transport gives up
    (issue #405), defaulting to `DEFAULT_CHAT_TIMEOUT` when the key is absent
    or explicitly null. Validated as a positive number and coerced to
    `float` at read time, so every consumer sees one type. Governs the CHAT
    seams only -- embedding calls keep `OllamaClient`'s own default, and the
    liveness probes keep `_PREFLIGHT_TIMEOUT`, which answers a different
    question (is anything listening) and must stay short."""
    confidential_local_exemption: bool
    """Whether a `confidential` concept may be included in an `llm.chat`
    payload when the backend host is verifiably this machine (issue #240),
    defaulting to `DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION` when the key is
    absent or explicitly null. This is only ONE of the two terms: the CLI
    ANDs it with the client's own `locality.is_local`, so `true` never
    grants an exemption for a host that could not be proven local."""
    volatility_windows: dict[str, str]
    """Raw `volatility_windows` passthrough (freshness-lint-v1): `{}` when
    absent or explicitly null, matching every other field's `is not None`
    fallback. A present map passes through verbatim -- duration-grammar
    parsing and the `static`/`slow`/`volatile` precedence stay in
    `lint.resolve_windows`, not here."""
    type_tiers: dict[str, str]
    """Raw `type_tiers` passthrough (freshness-suggest-windows,
    `concept-volatility` spec): `{}` when absent or explicitly null,
    mirroring `volatility_windows`'s `is not None` fallback. A present map
    passes through verbatim -- unknown-type/invalid-tier validation and the
    override step in the `volatility`/`type_tiers`/registry-default/fallback
    precedence stay in `lint.window_for_doc`, not here."""


def read_config(root: Path) -> Config:
    """Parse `openkos.yaml` at `root` and return its `model`/`review`/
    `default_sensitivity` fields, falling back to packaged defaults for any
    field the file omits OR sets to an explicit YAML null (D3).

    Uses `yaml.safe_load` -- never a loader that can construct arbitrary
    Python objects from untrusted YAML. A `yaml.YAMLError` (malformed YAML),
    a `TypeError` (some PyYAML constructor code paths raise this directly
    rather than a `YAMLError`, e.g. for a mapping with an unhashable complex
    key), or a root that parses but is not a mapping all raise `ValueError`,
    so callers can catch alongside `OSError` (a missing or unreadable file)
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
    except (yaml.YAMLError, TypeError) as exc:
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
    chat_timeout = raw.get("chat_timeout")
    confidential_local_exemption = raw.get("confidential_local_exemption")
    volatility_windows = raw.get("volatility_windows")
    type_tiers = raw.get("type_tiers")
    if model is not None and not isinstance(model, str):
        raise ValueError(
            f"{layout.config_path.name}: 'model' must be a string, got "
            f"{type(model).__name__}"
        )
    if embedding_model is not None and not isinstance(embedding_model, str):
        raise ValueError(
            f"{layout.config_path.name}: 'embedding_model' must be a string, got "
            f"{type(embedding_model).__name__}"
        )
    if chat_timeout is not None and (
        isinstance(chat_timeout, bool)
        or not isinstance(chat_timeout, int | float)
        or chat_timeout <= 0
    ):
        # `bool` is excluded FIRST and explicitly, because it subclasses
        # `int`: without that term `chat_timeout: true` would resolve to a
        # one-second deadline, so every chat call would fail instantly and
        # look like a dead backend rather than a bad config value. This is
        # the same int-as-bool hazard `confidential_local_exemption` guards
        # below, pointed the other way.
        #
        # Non-positive values are refused for a related reason: `urllib`
        # treats them as an immediate expiry, so `chat_timeout: 0` would
        # disable the LLM entirely while reading like a deliberate setting.
        raise ValueError(
            f"{layout.config_path.name}: 'chat_timeout' must be a positive "
            f"number of seconds, got {chat_timeout!r}"
        )
    if confidential_local_exemption is not None and not isinstance(
        confidential_local_exemption, bool
    ):
        # Validated, never coerced (issue #240), mirroring how `model` and
        # `embedding_model` validate their own type. This key decides whether
        # `confidential` content may reach an LLM at all, so a value the user
        # meant as something else -- `maybe`, `1`, a nested mapping -- must
        # fail loudly instead of being evaluated for truthiness and silently
        # enabling the exemption. `isinstance(x, bool)` is deliberately
        # narrower than a truthiness test: YAML resolves `1` to `int`, and
        # accepting int-as-bool would make `: 0` and `: false` agree only by
        # coincidence of Python's numeric tower.
        raise ValueError(
            f"{layout.config_path.name}: 'confidential_local_exemption' must be "
            f"a boolean, got {type(confidential_local_exemption).__name__}"
        )
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
        # Coerced, not merely passed through: `chat_timeout: 900` is a YAML
        # int and the field is typed `float`, so the boundary normalizes it
        # once instead of leaving every consumer to handle both.
        chat_timeout=(
            float(chat_timeout) if chat_timeout is not None else DEFAULT_CHAT_TIMEOUT
        ),
        confidential_local_exemption=(
            confidential_local_exemption
            if confidential_local_exemption is not None
            else DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION
        ),
        volatility_windows=(
            volatility_windows if volatility_windows is not None else {}
        ),
        type_tiers=(type_tiers if type_tiers is not None else {}),
    )


_TYPE_TIERS_HEADER_PREFIX = "type_tiers:"
"""Column-0-only header prefix `set_type_tier` looks for. A leading `#`
never matches this (string `startswith` on the RAW line, no lstrip), so the
shipped-template fully-commented `# type_tiers:` line is correctly treated
as case (c) -- absent -- not as an editable header."""

_TYPE_TIERS_ENTRY_RE = re.compile(
    r"^(?P<indent>[ \t]+)(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?P<sep>[ \t]*)"
    r"(?P<val>\S+)(?P<rest>.*)$"
)
"""One `type_tiers:` block entry: `{indent}{Key}:{sep}{val}{rest}`, where
`rest` is everything after the value to end-of-line (a trailing comment or
trailing whitespace), captured so case (a)'s rewrite can preserve it
byte-for-byte."""


def _split_line_ending(line: str) -> tuple[str, str]:
    """Split `line` (from `str.splitlines(keepends=True)`) into its content
    and its original line terminator (`"\\n"`, `"\\r\\n"`, or `""` for a
    final line with none), so a rewritten line can keep the exact same
    terminator it had before."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


@dataclass(frozen=True)
class _TypeTierEntry:
    """One parsed `type_tiers:` block entry, plus its position in the file."""

    line_index: int
    indent: str
    key: str
    sep: str
    val: str
    rest: str


def _validate_type_tier_vocab(concept_type: str, tier: str) -> None:
    valid_types = {ot.name for ot in types.REGISTRY}
    if concept_type not in valid_types:
        raise ValueError(
            f"{concept_type!r} is not a known concept type "
            f"(expected one of {sorted(valid_types)})"
        )
    if tier not in types.VOLATILITY_TIERS:
        raise ValueError(
            f"{tier!r} is not a valid volatility tier "
            f"(expected one of {sorted(types.VOLATILITY_TIERS)})"
        )


def _append_fresh_type_tiers_block(yaml_text: str, concept_type: str, tier: str) -> str:
    """Case (c): no uncommented `type_tiers:` header exists (absent, or the
    shipped-template fully-commented state) -- append a brand-new block at
    EOF after ensuring exactly one trailing newline; the rest of the file is
    untouched."""
    if not yaml_text:
        base = ""
    elif yaml_text.endswith("\n"):
        base = yaml_text.rstrip("\n") + "\n"
    else:
        base = yaml_text + "\n"
    return f"{base}type_tiers:\n  {concept_type}: {tier}\n"


def _parse_type_tiers_block(lines: list[str], header_idx: int) -> list[_TypeTierEntry]:
    """Parse the `type_tiers:` block body starting right after `header_idx`.

    The body is every following line that is blank, a comment (at any
    indent, including column 0), or a real `{indent}Key: val` entry; it ends
    at the first column-0 line that is neither blank nor a comment (the next
    top-level key). Raises `ValueError` on a tab-indented entry or a line
    inside the block that is indented but does not parse as an entry
    (fail-closed on any un-editable shape)."""
    entries: list[_TypeTierEntry] = []
    idx = header_idx + 1
    while idx < len(lines):
        content, _ = _split_line_ending(lines[idx])
        stripped = content.strip()
        if stripped == "" or stripped.startswith("#"):
            idx += 1
            continue
        if not content[:1].isspace():
            break
        match = _TYPE_TIERS_ENTRY_RE.match(content)
        if match is None:
            raise ValueError(
                "openkos.yaml: unrecognized line inside the 'type_tiers:' "
                f"block (line {idx + 1})"
            )
        indent = match.group("indent")
        if "\t" in indent:
            raise ValueError(
                "openkos.yaml: tab-indented 'type_tiers:' entry is not supported"
            )
        rest = match.group("rest")
        rest_stripped = rest.strip()
        if rest_stripped and not rest_stripped.startswith("#"):
            # The value token is `\S+`, so anything non-comment after it (a
            # second token, a YAML anchor tail like `&a slow`, a spilled quoted
            # value) would be blindly re-appended by the case (a) rewrite and
            # silently corrupt the value. Refuse rather than guess -- only a
            # bare tier and an optional trailing comment are editable.
            raise ValueError(
                "openkos.yaml: unsupported value in the 'type_tiers:' entry "
                f"(line {idx + 1}); only a bare tier and an optional trailing "
                "comment are supported"
            )
        entries.append(
            _TypeTierEntry(
                line_index=idx,
                indent=indent,
                key=match.group("key"),
                sep=match.group("sep"),
                val=match.group("val"),
                rest=rest,
            )
        )
        idx += 1

    if entries:
        first_indent = entries[0].indent
        if any(entry.indent != first_indent for entry in entries):
            raise ValueError(
                "openkos.yaml: inconsistent indentation inside the 'type_tiers:' block"
            )
        key_counts: dict[str, int] = {}
        for entry in entries:
            key_counts[entry.key] = key_counts.get(entry.key, 0) + 1
        if any(count > 1 for count in key_counts.values()):
            raise ValueError(
                "openkos.yaml: duplicate entry inside the 'type_tiers:' block"
            )
    return entries


def set_type_tier(yaml_text: str, concept_type: str, tier: str) -> str:
    """Return `yaml_text` with `type_tiers[concept_type] = tier` set via
    comment-safe text surgery -- never a YAML round-trip, so every other
    line, including comments, stays byte-identical (freshness-suggest-
    windows / `set-volatility` write verb, #140).

    Three edit cases, all load-bearing (design: "Text-Surgery Algorithm"):
    (a) the block already has an entry for `concept_type` -- only that
    line's value is rewritten, indent/separator/trailing comment preserved;
    (b) the block exists but has no entry for `concept_type` -- a new
    `{indent}{concept_type}: {tier}` line is inserted right after the last
    real entry, using the block's own canonical indent (or a fixed 2-space
    indent if the block is empty, i.e. header-only); (c) no uncommented
    `type_tiers:` header exists at all (absent, or the shipped-template
    fully-commented state) -- a fresh block is appended at EOF.

    Raises `ValueError` -- and returns nothing, leaving the caller to not
    write -- on `concept_type`/`tier` outside the known vocabulary (defense-
    in-depth; the CLI validates first) or on any `type_tiers:` shape this
    cannot confidently edit: an inline flow-mapping (`type_tiers: {...}`),
    more than one `type_tiers:` header key, a non-mapping scalar value, a
    tab-indented block, inconsistent entry indentation, or a duplicate entry
    key. Idempotent: if the entry already equals `tier`, the rewritten line
    is identical to the original, so the returned text is byte-identical to
    `yaml_text` (defense-in-depth -- the CLI already short-circuits before
    calling this).
    """
    _validate_type_tier_vocab(concept_type, tier)

    lines = yaml_text.splitlines(keepends=True)
    header_indices = [
        i
        for i, line in enumerate(lines)
        if _split_line_ending(line)[0].startswith(_TYPE_TIERS_HEADER_PREFIX)
    ]

    if len(header_indices) > 1:
        raise ValueError("openkos.yaml: multiple 'type_tiers:' keys found")

    if not header_indices:
        return _append_fresh_type_tiers_block(yaml_text, concept_type, tier)

    header_idx = header_indices[0]
    header_content, _ = _split_line_ending(lines[header_idx])
    trailing = header_content[len(_TYPE_TIERS_HEADER_PREFIX) :]
    trailing_stripped = trailing.strip()
    if trailing_stripped and not trailing_stripped.startswith("#"):
        if trailing_stripped.startswith("{"):
            raise ValueError(
                "openkos.yaml: inline flow-mapping 'type_tiers: {...}' is not supported"
            )
        raise ValueError(
            "openkos.yaml: 'type_tiers:' has a non-mapping value "
            f"({trailing_stripped!r})"
        )

    entries = _parse_type_tiers_block(lines, header_idx)
    existing = next((entry for entry in entries if entry.key == concept_type), None)
    new_lines = list(lines)

    if existing is not None:
        _, terminator = _split_line_ending(lines[existing.line_index])
        new_content = (
            f"{existing.indent}{existing.key}:{existing.sep}{tier}{existing.rest}"
        )
        new_lines[existing.line_index] = new_content + terminator
        return "".join(new_lines)

    if entries:
        insert_indent = entries[0].indent
        insert_at = entries[-1].line_index + 1
    else:
        insert_indent = "  "
        insert_at = header_idx + 1

    new_lines.insert(insert_at, f"{insert_indent}{concept_type}: {tier}\n")
    return "".join(new_lines)
