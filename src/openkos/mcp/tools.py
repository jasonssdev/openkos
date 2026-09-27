"""The tool registry, hand-written schemas, and the argument validator
(design Decisions 2 and 15, ADR-0027).

`execute` is the ONE composition (design Decision 2): validate arguments,
run the tool's service call, take a fresh disclosure snapshot, read the
consistency warnings, then let `mcp.gate` build the final disclosure-safe
payload. Slice 5 registered `get`; slice 6 added `navigate`; slice 7 added
`pending`; this slice adds `query`, the only tool that calls
`ctx.make_llm`/`ctx.make_embedder`/`ctx.local_exemption_for`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, cast

from openkos import config
from openkos.application import concept_read, list_service, next_action
from openkos.application import consistency as application_consistency
from openkos.application import query as query_service
from openkos.llm.base import Embedder, LLMBackend
from openkos.mcp import gate

ProgressSink = Callable[[str, int, int], None]
"""`(phase, completed, total)`, called from the worker thread running a
tool's `run` (design Decision 13). Only `query` uses this, from slice 9
onward; every other tool's `run` receives `None`."""


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool's `run` needs besides its own arguments.

    `make_llm`/`make_embedder`/`local_exemption_for` are injected factories
    (ADR-0018 D1: `application/*` binds no concrete LLM backend). `query`,
    the only tool that calls them, is not registered until slice 9 -- this
    slice's `serve()` supplies factories that are never actually called.
    """

    layout: config.WorkspaceLayout
    expose_confidential: bool
    make_llm: Callable[[config.Config], LLMBackend]
    make_embedder: Callable[[config.Config], Embedder]
    local_exemption_for: Callable[[LLMBackend, config.Config], bool]


class WorkspaceReadError(OSError):
    """Wraps a `ValueError` a tool's `run()` raises while reading workspace
    configuration (design Decision 12): `config.read_config` -- called by
    `query`'s `run()` to resolve `cfg.model`/`cfg.embedding_model`/
    `cfg.sufficiency_check`/`cfg.revision_history` -- raises `ValueError`
    for a malformed `openkos.yaml` (an `OSError` already covers an
    unreadable one). `execute` (below) is what performs this wrapping, so
    that BOTH failure modes fall through the SAME `read_failed` row in
    `server.py`'s `_TOOL_ERROR_TABLE`: subclassing `OSError` means no new
    row is needed there. A caller must retry either mode identically --
    fix the workspace, then call again -- and neither the wrapped
    `ValueError`'s message nor this one ever crosses the boundary, since
    the table always substitutes its own fixed string, never `str(exc)`."""


@dataclass(frozen=True)
class Tool:
    """One registered MCP tool: a service call (`run`) and the disclosure
    function that turns its raw result into a serializable payload
    (`disclose`)."""

    name: str
    title: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    run: Callable[[Mapping[str, object], ToolContext, ProgressSink | None], object]
    disclose: Callable[[object, gate.Snapshot], dict[str, object]]
    stale_reads: tuple[str, ...] = ()
    emits_progress: bool = False


def _get_run(
    arguments: Mapping[str, object],
    ctx: ToolContext,
    progress: ProgressSink | None,
) -> gate.GetRaw:
    """`get`'s service call (design Decisions 2 and 4): read the target
    concept, then walk its provenance ancestors -- BOTH before the
    disclosure snapshot is taken, so an object raised to confidential
    mid-read is still caught by the gate, never by this function."""
    concept_id = cast(
        str, arguments["concept_id"]
    )  # `inputSchema` already enforced this
    record = concept_read.read_concept(ctx.layout, concept_id)
    sources = list_service.list_provenance_sources(ctx.layout, record.concept_id)
    return gate.GetRaw(target_id=record.concept_id, record=record, sources=sources)


_GET_INPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {"concept_id": {"type": "string", "minLength": 1}},
    "required": ["concept_id"],
    "additionalProperties": False,
}

_GET_OUTPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "concept": {"type": ["object", "null"]},
        "withheld": {"type": "integer", "minimum": 0},
        "warnings": {"type": "array"},
        "not_run": {"type": "array"},
        "error": {"type": "object"},
    },
    "required": ["withheld", "warnings", "not_run"],
}

_GET_TOOL: Final = Tool(
    name="get",
    title="Get Concept",
    description="Read one concept: its curated fields, filtered relations, "
    "filtered provenance, and source ancestors.",
    input_schema=_GET_INPUT_SCHEMA,
    output_schema=_GET_OUTPUT_SCHEMA,
    run=_get_run,
    disclose=gate.disclose_get,
)


def _navigate_run(
    arguments: Mapping[str, object],
    ctx: ToolContext,
    progress: ProgressSink | None,
) -> concept_read.Neighborhood:
    """`navigate`'s service call (design Decision 5): read every neighbor
    `build_graph`'s projection holds for the target, in both directions --
    unfiltered; `gate.disclose_navigate` is what decides what may be
    disclosed."""
    concept_id = cast(
        str, arguments["concept_id"]
    )  # `inputSchema` already enforced this
    return concept_read.concept_neighbors(ctx.layout, concept_id)


_NAVIGATE_INPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {"concept_id": {"type": "string", "minLength": 1}},
    "required": ["concept_id"],
    "additionalProperties": False,
}

_NAVIGATE_OUTPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "concept_id": {"type": ["string", "null"]},
        "neighbors": {"type": "array"},
        "withheld": {"type": "integer", "minimum": 0},
        "warnings": {"type": "array"},
        "not_run": {"type": "array"},
        "error": {"type": "object"},
    },
    "required": ["withheld", "warnings", "not_run"],
}

_NAVIGATE_TOOL: Final = Tool(
    name="navigate",
    title="Navigate Concept Graph",
    description="Read one concept's neighbors from the graph projection, "
    "both outbound and inbound, typed and untyped.",
    input_schema=_NAVIGATE_INPUT_SCHEMA,
    output_schema=_NAVIGATE_OUTPUT_SCHEMA,
    run=_navigate_run,
    disclose=gate.disclose_navigate,
)


def _pending_run(
    arguments: Mapping[str, object],
    ctx: ToolContext,
    progress: ProgressSink | None,
) -> next_action.NextResult:
    """`pending`'s service call (design Decision 6): `next_action.
    next_action` unmodified -- no arguments to pass, per Decision 15's
    empty `inputSchema`; `gate.disclose_pending` is what decides what may
    be disclosed."""
    return next_action.next_action(ctx.layout)


_PENDING_INPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_PENDING_OUTPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "action": {"type": ["object", "null"]},
        "declinations": {"type": "array"},
        "skipped_documents": {"type": "integer", "minimum": 0},
        "withheld": {"type": "integer", "minimum": 0},
        "warnings": {"type": "array"},
        "not_run": {"type": "array"},
        "error": {"type": "object"},
    },
    "required": ["withheld", "warnings", "not_run"],
}

_PENDING_TOOL: Final = Tool(
    name="pending",
    title="Pending Work",
    description="Read the single ranked recommendation, plus the findings "
    "seen and declined or skipped along the way.",
    input_schema=_PENDING_INPUT_SCHEMA,
    output_schema=_PENDING_OUTPUT_SCHEMA,
    run=_pending_run,
    disclose=gate.disclose_pending,
)


def _query_run(
    arguments: Mapping[str, object],
    ctx: ToolContext,
    progress: ProgressSink | None,
) -> query_service.QueryOutcome:
    """`query`'s service call (design Decision 9): resolve this workspace's
    configuration, build the chat and embedding backends through the
    injected factories, resolve `local_exemption` ONLY when the launch
    opt-in (`ctx.expose_confidential`) is on, and call `run_query` with
    `include_confidential` ALWAYS `False` -- a confidential object reaching
    the LLM still requires BOTH the launch opt-in AND the resolved
    local-exemption gate (`sensitivity.should_block`'s disjunction is what
    actually releases the block once `local_exemption` is `True`; this
    function never bypasses that by passing `include_confidential=True`
    instead). `progress` is threaded straight through to `answer()`,
    unmodified. `limit` defaults to `5` -- the CLI's own default -- when
    omitted."""
    question = cast(str, arguments["question"])  # inputSchema already enforced this
    limit = cast(int, arguments.get("limit", 5))
    cfg = config.read_config(ctx.layout.root)
    llm = ctx.make_llm(cfg)
    embedder = ctx.make_embedder(cfg)
    local_exemption = (
        ctx.local_exemption_for(llm, cfg) if ctx.expose_confidential else False
    )
    return query_service.run_query(
        question,
        layout=ctx.layout,
        cfg=cfg,
        llm=llm,
        embedder=embedder,
        limit=limit,
        include_deprecated=False,
        include_confidential=False,
        local_exemption=local_exemption,
        progress=progress,
    )


_QUERY_INPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["question"],
    "additionalProperties": False,
}

_QUERY_OUTPUT_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "answer_withheld": {"type": "boolean"},
        "citations": {"type": "array"},
        "llm_invoked": {"type": "boolean"},
        "no_match_cause": {"type": "string"},
        "attribution": {"type": "string"},
        "counts": {"type": "object"},
        "degraded": {"type": "object"},
        "excerpted_titles": {"type": "array"},
        "omitted_titles": {"type": "array"},
        "history_truncated_titles": {"type": "array"},
        "skipped_documents": {"type": "integer", "minimum": 0},
        "withheld": {"type": "integer", "minimum": 0},
        "warnings": {"type": "array"},
        "not_run": {"type": "array"},
        "error": {"type": "object"},
    },
    "required": ["withheld", "warnings", "not_run"],
}

_QUERY_TOOL: Final = Tool(
    name="query",
    title="Query",
    description="Answer a question from this workspace's bundle, citing "
    "the concepts used.",
    input_schema=_QUERY_INPUT_SCHEMA,
    output_schema=_QUERY_OUTPUT_SCHEMA,
    run=_query_run,
    disclose=gate.disclose_query,
    stale_reads=("fts",),
    emits_progress=True,
)

REGISTRY: Final[Mapping[str, Tool]] = {
    "query": _QUERY_TOOL,
    "get": _GET_TOOL,
    "navigate": _NAVIGATE_TOOL,
    "pending": _PENDING_TOOL,
}


SUPPORTED_SCHEMA_KEYWORDS: Final = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minLength",
        "minimum",
        "description",
        "default",
    }
)
"""Decision 15's declared keyword set. A test walks every registered
`inputSchema`/`outputSchema` and fails on any keyword outside this set, so
a keyword the validator silently ignores can never pass unnoticed (fail
open)."""

_TYPE_PREDICATES: Final[Mapping[str, Callable[[object], bool]]] = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    # `bool` is an `int` subclass in Python; an `integer`-typed property
    # must reject `True`/`False`, or a caller could smuggle a boolean past
    # a numeric check.
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def _type_names(type_spec: object) -> list[str]:
    if isinstance(type_spec, str):
        return [type_spec]
    if isinstance(type_spec, list):
        return [name for name in type_spec if isinstance(name, str)]
    return []


def _matches_type(value: object, type_spec: object) -> bool:
    names = _type_names(type_spec)

    def _no_match(_value: object) -> bool:
        return False

    return any(_TYPE_PREDICATES.get(name, _no_match)(value) for name in names)


def _validate(schema: Mapping[str, object], value: object, *, name: str) -> str | None:
    """Validate `value` (named `name` in any error message) against
    `schema`. Returns `None` when valid, or an error message naming `name`
    -- never `value` itself, which is caller-supplied and may be
    sensitive."""
    type_spec = schema.get("type")
    if type_spec is not None and not _matches_type(value, type_spec):
        return f"{name} has the wrong type"

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            return f"{name} is shorter than the minimum length"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            return f"{name} is below the minimum"

    if isinstance(value, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for index, item in enumerate(value):
                error = _validate(items_schema, item, name=f"{name}[{index}]")
                if error is not None:
                    return error

    if isinstance(value, dict):
        properties = schema.get("properties")
        property_schemas: Mapping[str, object] = (
            properties if isinstance(properties, dict) else {}
        )
        for property_name, property_schema in property_schemas.items():
            if property_name in value and isinstance(property_schema, dict):
                error = _validate(
                    property_schema, value[property_name], name=property_name
                )
                if error is not None:
                    return error

        required = schema.get("required")
        if isinstance(required, list):
            for required_name in required:
                if isinstance(required_name, str) and required_name not in value:
                    return f"missing required property: {required_name}"

        if schema.get("additionalProperties") is False:
            for property_name in value:
                if property_name not in property_schemas:
                    return f"unexpected property: {property_name}"

    return None


def validate_arguments(schema: Mapping[str, object], arguments: object) -> str | None:
    """Validate `arguments` against a tool's `inputSchema`.

    Returns `None` when valid, or an error message naming the offending
    property -- never the value that failed validation, since that value is
    caller-supplied and MAY itself be sensitive.
    """
    return _validate(schema, arguments, name="arguments")


_INVALID_ARGUMENTS_CODE: Final = "invalid_arguments"


def execute(
    tool: Tool,
    arguments: Mapping[str, object],
    ctx: ToolContext,
    progress: ProgressSink | None,
) -> tuple[bool, dict[str, object]]:
    """Validate `arguments`, then dispatch to `tool`.

    Returns `(is_error, structured_content)`. An `inputSchema` violation is
    a *tool execution error* (`invalid_arguments`), never a JSON-RPC
    *protocol error* -- design's "Invalid Tool Arguments" requirement,
    confirmed against the 2025-11-25 tools page's Error Handling section
    ("Orchestrator verification").

    The success path is design Decision 2's one composition: `run`, THEN
    take a fresh disclosure snapshot and read the consistency warnings, so
    both reflect the bundle's state at (or after) the read rather than
    before it -- an object raised to confidential while `run` was reading
    is still caught. A raised exception (e.g. `ConceptNotFound`) propagates
    unchanged; `mcp/server.py` maps it to a tool error or `-32603`. The one
    exception: a `ValueError` `run` raises (today, only `query`'s
    `config.read_config` call) is wrapped into `WorkspaceReadError` here,
    so it funnels through the SAME `read_failed` tool-error row an ordinary
    unreadable-file `OSError` already uses (design Decision 12).
    """
    error = validate_arguments(tool.input_schema, arguments)
    if error is not None:
        return True, {
            "error": {
                "code": _INVALID_ARGUMENTS_CODE,
                "retryable": False,
                "message": error,
            },
            "withheld": 0,
            "warnings": [],
            "not_run": [],
        }

    try:
        raw = tool.run(arguments, ctx, progress)
    except ValueError as exc:
        raise WorkspaceReadError(str(exc)) from exc
    snapshot = gate.take_snapshot(
        ctx.layout.bundle_dir, expose_confidential=ctx.expose_confidential
    )
    consistency = application_consistency.read_consistency(
        ctx.layout, stale_reads=tool.stale_reads
    )
    return False, gate.finish(tool.disclose(raw, snapshot), consistency)
