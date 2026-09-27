"""Unit tests for `mcp/tools.py`: the registry skeleton and the argument
validator (design Decisions 2, 15, ADR-0027).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from openkos import config
from openkos.llm.base import BackendHostLocality, Embedder, LLMBackend, Message
from openkos.mcp import tools
from openkos.state import fts

_UNECHOED_VALUE = "sk-secret-token-should-never-be-echoed"


def _never_called_llm(cfg: config.Config) -> LLMBackend:
    raise AssertionError("this test's tool never calls make_llm")


def _never_called_embedder(cfg: config.Config) -> Embedder:
    raise AssertionError("this test's tool never calls make_embedder")


def _never_called_local_exemption(client: LLMBackend, cfg: config.Config) -> bool:
    raise AssertionError("this test's tool never calls local_exemption_for")


def _ctx(*, expose_confidential: bool = False) -> tools.ToolContext:
    return tools.ToolContext(
        layout=config.WorkspaceLayout(root=Path("/nonexistent")),
        expose_confidential=expose_confidential,
        make_llm=_never_called_llm,
        make_embedder=_never_called_embedder,
        local_exemption_for=_never_called_local_exemption,
    )


def _tool(
    name: str = "echo",
    *,
    input_schema: Mapping[str, object] | None = None,
) -> tools.Tool:
    return tools.Tool(
        name=name,
        title=name.title(),
        description=f"Test-only tool {name}.",
        input_schema=input_schema if input_schema is not None else {"type": "object"},
        output_schema={"type": "object"},
        run=lambda arguments, ctx, progress: arguments,
        disclose=lambda raw, snapshot: {
            "withheld": 0,
            "warnings": [],
            "not_run": [],
            "echo": raw,
        },
    )


@pytest.mark.parametrize(
    ("schema", "arguments", "bad_property"),
    [
        # `required`: a missing required property.
        (
            {
                "type": "object",
                "required": ["concept_id"],
                "properties": {"concept_id": {"type": "string"}},
            },
            {},
            "concept_id",
        ),
        # `additionalProperties: false` rejects an extra property.
        (
            {
                "type": "object",
                "properties": {"concept_id": {"type": "string"}},
                "additionalProperties": False,
            },
            {"concept_id": "x", "extra": _UNECHOED_VALUE},
            "extra",
        ),
        # `minLength` violated.
        (
            {
                "type": "object",
                "properties": {"question": {"type": "string", "minLength": 1}},
            },
            {"question": ""},
            "question",
        ),
        # `minimum` violated.
        (
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1}},
            },
            {"limit": 0},
            "limit",
        ),
        # An `integer`-typed property given `True` -- `bool` is an `int`
        # subclass in Python, and this must still be rejected.
        (
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1}},
            },
            {"limit": True},
            "limit",
        ),
        # `items` validates array entries.
        (
            {
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
            },
            {"tags": ["ok", 3]},
            "tags[1]",
        ),
    ],
)
def test_validate_arguments_all_keyword_shapes(
    schema: Mapping[str, object], arguments: Mapping[str, object], bad_property: str
) -> None:
    """Kills `isinstance(x, int)` without excluding `bool` (the `True`
    case), and any rejection message that echoes the failing value."""
    error = tools.validate_arguments(schema, arguments)

    assert error is not None
    assert bad_property in error
    assert _UNECHOED_VALUE not in error


def test_validate_arguments_type_union_accepts_either() -> None:
    schema: Mapping[str, object] = {
        "type": "object",
        "properties": {"value": {"type": ["string", "null"]}},
    }

    assert tools.validate_arguments(schema, {"value": "x"}) is None
    assert tools.validate_arguments(schema, {"value": None}) is None


def test_validate_arguments_valid_arguments_pass() -> None:
    schema: Mapping[str, object] = {
        "type": "object",
        "required": ["concept_id"],
        "properties": {"concept_id": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }

    assert tools.validate_arguments(schema, {"concept_id": "pub"}) is None


def _schema_keywords(schema: object) -> list[str]:
    keywords: list[str] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            keywords.append(key)
            if key == "properties" and isinstance(value, dict):
                for sub_schema in value.values():
                    keywords.extend(_schema_keywords(sub_schema))
            elif key == "items":
                keywords.extend(_schema_keywords(value))
    return keywords


def test_every_schema_uses_only_supported_keywords() -> None:
    """Every registered `inputSchema`/`outputSchema` (the empty `REGISTRY`
    at this slice) uses only `SUPPORTED_SCHEMA_KEYWORDS`; a deliberately
    out-of-set keyword (`pattern`, on a test-only tool) IS caught. Kills
    removing a keyword from `SUPPORTED_SCHEMA_KEYWORDS` without removing
    its enforcement -- that would silently stop validating it."""
    registry = dict(tools.REGISTRY)
    registry["leaky_probe"] = _tool(
        name="leaky_probe",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "string", "pattern": "^a"}},
        },
    )

    offending = [
        (name, keyword)
        for name, tool in registry.items()
        for schema in (tool.input_schema, tool.output_schema)
        for keyword in _schema_keywords(schema)
        if keyword not in tools.SUPPORTED_SCHEMA_KEYWORDS
    ]

    assert offending == [("leaky_probe", "pattern")]


def test_invalid_arguments_is_a_tool_error_not_dash32602() -> None:
    """A schema violation is a tool execution error (`isError`,
    `invalid_arguments`), not routed through JSON-RPC's `-32602` -- design's
    "Invalid Tool Arguments" requirement, confirmed against the 2025-11-25
    tools page. Kills mapping schema-validation failures to `-32602`."""
    tool = _tool(
        input_schema={
            "type": "object",
            "required": ["concept_id"],
            "properties": {"concept_id": {"type": "string", "minLength": 1}},
        }
    )

    is_error, structured_content = tools.execute(tool, {}, _ctx(), None)

    assert is_error is True
    error = structured_content["error"]
    assert isinstance(error, dict)
    assert error["code"] == "invalid_arguments"
    assert error["message"] is not None


def test_execute_success_path_calls_run_then_disclose() -> None:
    """`execute` on valid arguments calls `run`, then `disclose` -- proving
    GREEN is real, not a trivial pass on the error path alone."""
    tool = _tool(input_schema={"type": "object"})

    is_error, structured_content = tools.execute(tool, {"a": 1}, _ctx(), None)

    assert is_error is False
    assert structured_content["echo"] == {"a": 1}
    assert structured_content["withheld"] == 0


def test_execute_wraps_config_value_error_as_workspace_read_error() -> None:
    """A `ValueError` a tool's `run()` raises (today, only `query`'s
    `config.read_config` call, for a malformed `openkos.yaml`) is wrapped
    into `WorkspaceReadError`, an `OSError` subclass -- so it funnels
    through the SAME `read_failed` row an ordinary unreadable-file
    `OSError` already uses (design Decision 12), rather than propagating
    as a bare `ValueError` that no row in `server.py`'s `_TOOL_ERROR_TABLE`
    would match."""

    def _raise_value_error(
        arguments: Mapping[str, object], ctx: tools.ToolContext, progress: object
    ) -> object:
        raise ValueError("malformed openkos.yaml")

    tool = _tool()
    raising_tool = tools.Tool(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        input_schema=tool.input_schema,
        output_schema=tool.output_schema,
        run=_raise_value_error,
        disclose=tool.disclose,
    )

    with pytest.raises(tools.WorkspaceReadError) as exc_info:
        tools.execute(raising_tool, {}, _ctx(), None)
    assert isinstance(exc_info.value, OSError)


# ---------------------------------------------------------------------------
# 9.1: the query tool's LLM-egress conjunction (design Decision 9)
# ---------------------------------------------------------------------------

_MARKER = "SECRET-MARKER-9F3A"


class _RecordingLLM:
    """A structural `LLMBackend` with a configurable `.locality`, recording
    every `chat` call's full prompt text so a test can assert on what
    actually reached it."""

    def __init__(self, *, is_local: bool) -> None:
        self._locality = BackendHostLocality(
            is_local=is_local,
            display_host="localhost" if is_local else "remote.example",
        )
        self.calls: list[str] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append("\n".join(message["content"] for message in messages))
        return "the reply"

    @property
    def locality(self) -> BackendHostLocality:
        return self._locality


class _NeverCalledEmbedder:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError(
            "this fixture has no vectors.db, so dense retrieval never embeds"
        )


def _build_query_fixture(root: Path) -> config.WorkspaceLayout:
    """A public and a confidential concept, both matching the same
    question -- so a hit survives (and `llm.chat` is reached) even when the
    confidential one is excluded, letting the test tell "excluded" apart
    from "nothing matched at all"."""
    bundle = root / "bundle"
    concepts = bundle / "concepts"
    concepts.mkdir(parents=True)
    (bundle / "index.md").write_text("# Index\n", encoding="utf-8")
    (bundle / "log.md").write_text("# Log\n", encoding="utf-8")
    (concepts / "open.md").write_text(
        "---\ntype: Concept\ntitle: Open\nsensitivity: public\n---\n"
        "This concept explains a marker value in general terms.\n",
        encoding="utf-8",
    )
    (concepts / "secret.md").write_text(
        "---\ntype: Concept\ntitle: Secret\nsensitivity: confidential\n---\n"
        f"This concept explains a marker value. The value is {_MARKER}.\n",
        encoding="utf-8",
    )
    (root / "openkos.yaml").write_text("sufficiency_check: false\n", encoding="utf-8")
    layout = config.WorkspaceLayout(root=root)
    fts.write_fts_index(layout.fts_db_path, layout.bundle_dir)
    return layout


def _query_ctx(
    layout: config.WorkspaceLayout, llm: LLMBackend, *, expose_confidential: bool
) -> tools.ToolContext:
    def _local_exemption_for(client: LLMBackend, cfg: config.Config) -> bool:
        assert isinstance(client, _RecordingLLM)
        return client.locality.is_local and cfg.confidential_local_exemption

    return tools.ToolContext(
        layout=layout,
        expose_confidential=expose_confidential,
        make_llm=lambda cfg: llm,
        make_embedder=lambda cfg: _NeverCalledEmbedder(),
        local_exemption_for=_local_exemption_for,
    )


def test_query_llm_egress_conjunction(tmp_path: Path) -> None:
    """Covers "The opt-in off sends both LLM-egress gates closed", "The
    opt-in on still requires the local-exemption gate", and "A remote
    backend never receives confidential content via query". Kills passing
    `include_confidential=ctx.expose_confidential` directly, bypassing the
    `local_exemption` conjunction design Decision 9 requires."""
    layout = _build_query_fixture(tmp_path)
    query_tool = tools.REGISTRY["query"]

    off_llm = _RecordingLLM(is_local=True)
    query_tool.run(
        {"question": "marker value"},
        _query_ctx(layout, off_llm, expose_confidential=False),
        None,
    )
    assert off_llm.calls
    assert _MARKER not in off_llm.calls[0]

    local_on_llm = _RecordingLLM(is_local=True)
    query_tool.run(
        {"question": "marker value"},
        _query_ctx(layout, local_on_llm, expose_confidential=True),
        None,
    )
    assert local_on_llm.calls
    assert _MARKER in local_on_llm.calls[0]

    remote_on_llm = _RecordingLLM(is_local=False)
    query_tool.run(
        {"question": "marker value"},
        _query_ctx(layout, remote_on_llm, expose_confidential=True),
        None,
    )
    assert remote_on_llm.calls
    assert _MARKER not in remote_on_llm.calls[0]
