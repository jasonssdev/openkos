"""Shared CLI-test defaults.

`ingest` builds a real `OllamaClient` for `_embed_after_ingest` (#183), so
EVERY test that invokes `ingest` -- including the many that use it only as a
fixture to get a populated workspace -- now reaches for the network unless
the seam is patched.

Two things go wrong without a default. The obvious one is speed: an
unreachable backend costs a connection attempt plus the client's retry
budget per ingest, which measured at roughly 3-5 seconds per affected test
and nearly tripled a full offline suite run. The subtle one is worse -- a
test that asserts on embedding-dependent output silently becomes an
assertion about whether the developer happens to have Ollama running, green
locally and red in CI. That exact failure shipped in this change and was
caught by review, not by the suite.

So the default is offline. Modules needing specific behavior override it:
`test_ingest.py` has its own autouse fixture, and individual tests patch the
same seam directly -- both take precedence, since module-level and
function-level fixtures resolve after this one.

One consequence worth naming rather than discovering later: `test_ingest.py`
overrides this with `_FakeLLM`, which serves `chat()` but NOT `embed()`. Its
tests therefore reach `_embed_after_ingest`, raise `AttributeError` inside
`reindex`, and degrade through the broad guard to a stderr notice with an
unchanged exit code. That is the fail-open contract working, not a fixture
gap -- those tests assert on extraction, not embedding, and the ones that do
care use `_EmbeddingLLM`. It does mean their stderr carries an extra
embedding notice, so a strict full-stderr equality assertion added there
would fail for a reason that has nothing to do with what it is testing.
"""

from collections.abc import Sequence

import pytest

from openkos.llm.base import EMBED_DIM
from openkos.llm.ollama import OllamaClient


class _OfflineOllama(OllamaClient):
    """A real `OllamaClient` with only its two network methods stubbed.

    SUBCLASSES rather than replaces, because several tests legitimately
    assert the CLI constructs a genuine `OllamaClient` from the configured
    model (`isinstance(..., OllamaClient)` plus host/model resolution). A
    structural stand-in would break that real contract; this keeps every
    constructor behavior intact and removes only the network.

    Declines concept extraction and returns a fixed unit vector --
    deliberately uninteresting, so no test accidentally depends on these
    values."""

    def chat(self, messages: Sequence[object]) -> str:
        return '{"extract": false}'

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * (EMBED_DIM - 1) for _ in texts]


@pytest.fixture(autouse=True)
def _offline_ollama_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the CLI's Ollama seam so no CLI test reaches the network by
    accident. Same constructor signature, so call sites are unaffected."""
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _OfflineOllama)
