"""Unit tests for `OpenAICompatibleClient.locality` (issue #1057 Phase 7,
tasks 7.5-7.7): wired to the ONE shared `classify_backend_host` authority
applied to `resolved_base_url` -- covers the Threat Matrix "Locality" row
(`localhost.evil.com`, `127.1`, unmatched `[::1`, userinfo, IPv6 expanded
loopback).
"""

import pytest

from openkos.llm.openai_compatible import OpenAICompatibleClient


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ],
)
def test_loopback_base_url_classifies_local(base_url: str) -> None:
    """A loopback `base_url` classifies local, matching `OllamaClient`'s
    classification for the equivalent host (spec: "Locality Uses The
    Shared Classifier", local half)."""
    client = OpenAICompatibleClient(model="qwen3", base_url=base_url)

    assert client.locality.is_local is True


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost.evil.com",
        "http://127.1",
        "http://[::1",
        "http://[0:0:0:0:0:0:0:1]",
        "http://example.com",
    ],
)
def test_nonlocal_or_unparseable_base_url_classifies_nonlocal(base_url: str) -> None:
    """An unparseable or remote `base_url` classifies non-local (spec:
    "An unparseable or remote host classifies non-local"; Threat Matrix
    "Locality" row): a lookalike domain (`localhost.evil.com`), a
    non-loopback IPv4 shortcut (`127.1`), an unmatched IPv6 bracket
    (`[::1`), the IPv6 EXPANDED-zeros loopback spelling (literal-form-only
    rule, NOT treated as local), and a plain remote host."""
    client = OpenAICompatibleClient(model="qwen3", base_url=base_url)

    assert client.locality.is_local is False


def test_locality_never_leaks_userinfo() -> None:
    """A `base_url` carrying userinfo never echoes it through
    `display_host` (issue #355's rule, applied to this backend)."""
    client = OpenAICompatibleClient(
        model="qwen3", base_url="http://user:s3cret@example.com:8080"
    )

    assert "s3cret" not in client.locality.display_host
