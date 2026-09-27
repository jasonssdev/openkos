"""Fixture helpers for the canary enumeration guard (design Decision 16,
must-have): a confidential canary object plus a public referrer, a needle
list, `find_canary_leaks`, `GUARD_MATRIX` scaffolding, `run_matrix`, and
the deliberately violating test-only `leaky_probe` tool.

This slice's fixture covers exactly what `test_enumeration_guard.py`'s two
permanent-assertion tests need -- the guard's own self-test (it can catch
a violation) and the positive control that proves the needles are
genuinely reachable. Decision 16 also names a pending merge-ledger marker,
a findings row, an FTS index, and a fake `LLMBackend`; those are added by
the slice whose real tool first needs them (5's `get`, 7's `pending`, 9's
`query`), so this module is EXTENDED there, never rebuilt.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from openkos.mcp import server as mcp_server
from openkos.mcp import tools as mcp_tools
from openkos.mcp import transport

CANARY_ID: Final = "concepts/zq-canary-7f3a"
CANARY_TITLE: Final = "Zq Canary Title 7f3a"
CANARY_BODY_MARKER: Final = "ZQ-CANARY-BODY-7F3A"
CANARY_SOURCE_ID: Final = "sources/zq-canary-src-7f3a"
PUBLIC_ID: Final = "concepts/pub"
BROKEN_ID: Final = "concepts/zq-broken-7f3a"

_NEEDLE_SOURCES: Final[tuple[str, ...]] = (
    CANARY_ID,
    CANARY_ID.rsplit("/", 1)[-1],
    CANARY_SOURCE_ID,
    CANARY_SOURCE_ID.rsplit("/", 1)[-1],
    CANARY_TITLE,
    CANARY_BODY_MARKER,
)
NEEDLES: Final[tuple[str, ...]] = tuple(
    {variant for needle in _NEEDLE_SOURCES for variant in (needle, needle.casefold())}
)
"""Every canary id, the slugs without their directory, the title, and the
body marker, each also case-folded (design Decision 16's "Needles")."""


def build_canary_bundle(root: Path) -> Path:
    """Write the fixture's `bundle/` under `root` (design Decision 16's
    "Fixture workspace") and return `root / "bundle"`."""
    bundle_dir = root / "bundle"
    concepts = bundle_dir / "concepts"
    sources = bundle_dir / "sources"
    concepts.mkdir(parents=True, exist_ok=True)
    sources.mkdir(parents=True, exist_ok=True)

    (concepts / "zq-canary-7f3a.md").write_text(
        "---\n"
        "type: Concept\n"
        f"title: {CANARY_TITLE}\n"
        "sensitivity: confidential\n"
        "relations:\n"
        "  - target: concepts/pub\n"
        "    type: related_to\n"
        "---\n"
        f"{CANARY_BODY_MARKER} -- a confidential canary body.\n",
        encoding="utf-8",
    )
    (sources / "zq-canary-src-7f3a.md").write_text(
        "---\n"
        "type: Source\n"
        "title: Zq Canary Source 7f3a\n"
        "sensitivity: confidential\n"
        "extraction_status: failed\n"
        "---\n"
        "A confidential canary Source.\n",
        encoding="utf-8",
    )
    (concepts / "pub.md").write_text(
        "---\n"
        "type: Concept\n"
        "title: Public Referrer\n"
        "sensitivity: public\n"
        "relations:\n"
        "  - target: concepts/zq-canary-7f3a\n"
        "    type: related_to\n"
        "provenance:\n"
        "  - concepts/zq-canary-7f3a\n"
        "  - sources/zq-canary-src-7f3a\n"
        "---\n"
        "An ordinary public concept. Its own prose never names a canary "
        "(P1: a disclosable object's text is shown as written).\n",
        encoding="utf-8",
    )
    (concepts / "zq-broken-7f3a.md").write_bytes(
        b"---\ntype: Concept\ntitle: Broken\n---\n\xff\xfe invalid body\n"
    )
    return bundle_dir


def find_canary_leaks(lines: Sequence[bytes]) -> list[str]:
    """Scan every raw line the server wrote for a canary needle.

    `structuredContent` and the `text` content block are both plain,
    un-escaped JSON (`transport.encode_message`/the server's own
    `_json_dumps` both pass `ensure_ascii=False`), so one substring scan
    over each DECODED raw line covers both -- unlike a check that inspects
    only a parsed `structuredContent` field, which would miss a needle
    sitting only inside the `text` block's own JSON string. Returns the
    needles found, in the order first seen; non-empty means a leak.
    """
    found: list[str] = []
    for raw_line in lines:
        if not raw_line.strip():
            continue
        text = raw_line.decode("utf-8", errors="replace")
        for needle in NEEDLES:
            if needle in text and needle not in found:
                found.append(needle)
    return found


@dataclass(frozen=True)
class Call:
    """One `tools/call` request the guard sends against a registered tool
    (design Decision 16's `GUARD_MATRIX` rows)."""

    arguments: Mapping[str, object]
    label: str = ""


GUARD_MATRIX: dict[str, list[Call]] = {
    "get": [
        Call(arguments={"concept_id": PUBLIC_ID}, label="disclosable"),
        Call(arguments={"concept_id": CANARY_ID}, label="canary_concept"),
        Call(arguments={"concept_id": CANARY_SOURCE_ID}, label="canary_source"),
        Call(arguments={"concept_id": "concepts/does-not-exist-7f3a"}, label="missing"),
        Call(arguments={}, label="invalid_arguments"),
    ],
    "navigate": [
        Call(arguments={"concept_id": PUBLIC_ID}, label="disclosable"),
        Call(arguments={"concept_id": CANARY_ID}, label="canary_concept"),
        Call(arguments={"concept_id": CANARY_SOURCE_ID}, label="canary_source"),
        Call(arguments={"concept_id": "concepts/does-not-exist-7f3a"}, label="missing"),
        Call(arguments={}, label="invalid_arguments"),
    ],
    "pending": [
        Call(arguments={}, label="disclosable"),
        Call(arguments={"unexpected": True}, label="invalid_arguments"),
    ],
}
"""`query`'s row joins in slice 9. `pending` takes no arguments (Decision
15's empty `inputSchema`), so its own row varies the CALL rather than a
per-id target: a bare call, and one with an unexpected property (its own
`additionalProperties: false` refusal)."""


async def _run_matrix_async(
    registry: Mapping[str, mcp_tools.Tool],
    matrix: Mapping[str, list[Call]],
    ctx: mcp_tools.ToolContext,
) -> list[bytes]:
    buffer = io.BytesIO()
    server = mcp_server.Server(registry, ctx, transport.MessageWriter(buffer))

    await server.handle_raw(
        transport.encode_message(
            {
                "jsonrpc": "2.0",
                "id": "__canary_init__",
                "method": "initialize",
                "params": {"protocolVersion": mcp_server.PROTOCOL_VERSION},
            }
        )
    )

    request_id = 0
    for name, calls in matrix.items():
        for call in calls:
            request_id += 1
            await server.handle_raw(
                transport.encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": dict(call.arguments)},
                    }
                )
            )
            # Bounded wait for THIS call to finish (its task removed from
            # the server's own in-flight map) before sending the next one,
            # so every response is fully written in call order -- mirrors
            # `tests/unit/mcp/test_server.py`'s own polling convention.
            for _ in range(2000):
                if not server._inflight:
                    break
                await asyncio.sleep(0.005)
            else:
                raise TimeoutError(
                    f"tool call {name!r} (request {request_id}) never completed"
                )

    return buffer.getvalue().splitlines()


def run_matrix(
    registry: Mapping[str, mcp_tools.Tool],
    matrix: Mapping[str, list[Call]],
    ctx: mcp_tools.ToolContext,
) -> list[bytes]:
    """Drive the FULL server path (design Decision 16: "JSON-RPC in, bytes
    out", so error mapping and serialization are covered too) for every
    `(tool, call)` pair in `matrix`: one real `initialize` handshake
    followed by one `tools/call` request per pair, dispatched through a
    real `Server`. Returns every raw line the server wrote."""
    return asyncio.run(_run_matrix_async(registry, matrix, ctx))


def _leaky_probe_run(
    arguments: Mapping[str, object],
    ctx: mcp_tools.ToolContext,
    progress: mcp_tools.ProgressSink | None,
) -> object:
    return None


def _leaky_probe_disclose(raw: object, snapshot: object) -> dict[str, object]:
    """Deliberately violates the disclosure boundary: always returns the
    canary's title, regardless of `snapshot` (design Decision 16,
    assertion 3) -- this is what proves the guard is CAPABLE of catching a
    leak, before any real tool is trusted against it."""
    return {"leak": CANARY_TITLE, "withheld": 0, "warnings": [], "not_run": []}


LEAKY_PROBE: Final = mcp_tools.Tool(
    name="leaky_probe",
    title="Leaky Probe",
    description="Test-only tool that deliberately leaks the canary (guard self-test).",
    input_schema={"type": "object"},
    output_schema={"type": "object"},
    run=_leaky_probe_run,
    disclose=_leaky_probe_disclose,
)
