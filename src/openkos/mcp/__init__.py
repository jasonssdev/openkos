"""The MCP adapter: a hand-rolled, stdio-only server for protocol revision
2025-11-25 (ADR-0027).

This is the only package in the repository that imports `asyncio`
(ADR-0021 D2, `tests/unit/mcp/test_layering.py`). It calls synchronous
`application/` services on worker threads and never reaches below them for
knowledge-model facts. `openkos.cli` imports it lazily, only inside the
`mcp` verb, so `asyncio` stays off every other command's start-up path.
"""
