"""Unit tests for `resolution/reconciliation.py` (#645): the one-call
merged-body reconciliation pass. Fail-closed by construction: every refusal
returns `None` so the caller keeps the stacked body -- a bad rewrite that
replaced real content would be silent data loss, while a kept stacked body
is merely the disclosed pre-#645 behavior."""

from collections.abc import Sequence

from openkos.llm.base import Message
from openkos.resolution import reconciliation


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self.reply


_SURVIVOR = (
    "The Model Context Protocol is an open protocol connecting language "
    "models to external tools. It standardizes the integration between "
    "clients and servers."
)
_ABSORBED = (
    "MCP lets a model reach tools and data sources through servers. Each "
    "server exposes tools over a standard transport, and one client works "
    "with any conformant server."
)


def _reconcile(reply: str) -> str | None:
    return reconciliation.reconcile_merged_body(
        survivor_title="Model Context Protocol",
        survivor_body=_SURVIVOR,
        absorbed_body=_ABSORBED,
        llm=_FakeLLM(reply),
    )


def test_a_plausible_rewrite_is_returned_stripped() -> None:
    reply = (
        "\nThe Model Context Protocol (MCP) is an open protocol that "
        "connects language models to external tools and data sources "
        "through servers. Each server exposes tools over a standard "
        "transport, so one client works with any conformant server.\n\n"
    )
    assert _reconcile(reply) == reply.strip()


def test_a_fenced_reply_is_unwrapped() -> None:
    inner = (
        "The Model Context Protocol (MCP) is an open protocol that "
        "connects language models to external tools and data sources "
        "through servers. Each server exposes tools over a standard "
        "transport, so one client works with any conformant server."
    )
    assert _reconcile(f"```markdown\n{inner}\n```") == inner


def test_an_empty_or_blank_reply_is_refused() -> None:
    assert _reconcile("") is None
    assert _reconcile("   \n  ") is None


def test_a_dramatically_shorter_reply_is_refused() -> None:
    """A reply shorter than half the LONGER input lost content, not
    duplication -- the guard that keeps a lazy summary from silently
    replacing two real documents."""
    assert _reconcile("MCP is a protocol.") is None


def test_a_frontmatter_echo_is_refused() -> None:
    reply = (
        "---\ntype: Concept\ntitle: Model Context Protocol\n---\n\n"
        "The Model Context Protocol (MCP) is an open protocol that "
        "connects language models to external tools and data sources "
        "through servers, and one client works with any conformant server."
    )
    assert _reconcile(reply) is None


def test_a_reply_echoing_the_stacked_heading_is_refused() -> None:
    """A reply still carrying `## Merged content (...)` re-emitted the
    stacked shape instead of reconciling -- the exact thing being fixed."""
    reply = f"{_SURVIVOR}\n\n## Merged content (concepts/mcp)\n\n{_ABSORBED}"
    assert _reconcile(reply) is None


def test_the_prompt_carries_title_and_both_bodies() -> None:
    llm = _FakeLLM(
        "The Model Context Protocol (MCP) is an open protocol that "
        "connects language models to external tools and data sources "
        "through servers. Each server exposes tools over a standard "
        "transport, so one client works with any conformant server."
    )
    reconciliation.reconcile_merged_body(
        survivor_title="Model Context Protocol",
        survivor_body=_SURVIVOR,
        absorbed_body=_ABSORBED,
        llm=llm,
    )

    assert len(llm.calls) == 1
    user_content = llm.calls[0][1]["content"]
    assert "Model Context Protocol" in user_content
    assert _SURVIVOR in user_content
    assert _ABSORBED in user_content
