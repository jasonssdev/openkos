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


def test_a_leading_heading_is_pinned_to_the_survivor_title() -> None:
    """Issue #695: the model is free to head the reconciled document with
    whichever of the two notes it found more natural -- in the reported case
    the ABSORBED note's title -- while the merged frontmatter `title:` keeps
    the SURVIVOR's (the survivor-wins scalar rule in
    `okf.build_merged_document`). The two then disagree permanently: the
    frontmatter title is what `index.md`, `status`, `list` and citations
    render, and the heading is what a human reads on opening the file.

    Pinned deterministically rather than asked for in the prompt: the
    survivor's title is already known here, so trusting the model to echo
    it would trade a fact for a probability."""
    reply = (
        "# Reunión de Evaluación de Decisión AFG\n\n"
        "The Model Context Protocol (MCP) is an open protocol that connects "
        "language models to external tools and data sources through servers. "
        "Each server exposes tools over a standard transport."
    )

    out = _reconcile(reply)

    assert out is not None
    assert out.startswith("# Model Context Protocol\n"), out[:80]
    assert "Reunión de Evaluación de Decisión AFG" not in out
    assert "Each server exposes tools over a standard transport." in out


def test_a_body_with_no_leading_heading_is_left_alone() -> None:
    """The pin rewrites a heading that is already there; it never INVENTS
    one. A reconciled body that legitimately opens with prose keeps its
    shape -- the frontmatter title still names the document."""
    reply = (
        "The Model Context Protocol (MCP) is an open protocol that connects "
        "language models to external tools and data sources through servers."
    )

    out = _reconcile(reply)

    assert out == reply


def test_only_the_leading_heading_is_pinned() -> None:
    """A later `# ` heading inside the body is the model's own sectioning,
    not the document's name -- rewriting it would corrupt real structure."""
    reply = (
        "# Wrong Title\n\n"
        "The Model Context Protocol is an open protocol connecting language "
        "models to external tools and data sources through servers.\n\n"
        "# Transport\n\n"
        "Each server exposes tools over a standard transport, so one client "
        "works with any conformant server."
    )

    out = _reconcile(reply)

    assert out is not None
    assert out.startswith("# Model Context Protocol\n")
    assert "# Transport" in out
    assert "Wrong Title" not in out


def test_a_title_that_cannot_be_spliced_safely_leaves_the_body_alone() -> None:
    """The pin fails CLOSED on a title it cannot write as one heading line.
    Keeping the model's own heading is strictly better than corrupting the
    document with a multi-line one, and such a title cannot reach here
    through the ordinary write paths anyway (`bundle.index` rejects
    newlines in titles) -- this is a guard, not a live code path."""
    reply = (
        "# Model Heading\n\n"
        "The Model Context Protocol is an open protocol connecting language "
        "models to external tools and data sources through servers."
    )
    for bad_title in ("", "   ", "Two\nLines", "Carriage\rReturn"):
        out = reconciliation.reconcile_merged_body(
            survivor_title=bad_title,
            survivor_body=_SURVIVOR,
            absorbed_body=_ABSORBED,
            llm=_FakeLLM(reply),
        )
        assert out == reply, bad_title


# --- issue #904, secondary: the reconciled document came out demoted -------


def test_a_demoted_leading_heading_is_promoted_and_pinned() -> None:
    """#904's secondary finding. The reconciled document came out with
    `## <Title>` and `### Related` while every other document in the bundle
    uses `# <Title>` and `## Related`.

    Worse than cosmetic: the #695 pin only fired on a body opening with
    `# `, so a demoted reply kept the ABSORBED note's title as well as the
    wrong level -- the exact disagreement #695 exists to remove, silently
    reintroduced whenever the model shifted the tree down one.

    Nothing catches it downstream: `sqlite_graph` resolves by markdown link
    syntax rather than heading level, and `lint` has no heading-shape
    category, so it passes every gate."""
    reply = (
        "## Reunión de Evaluación de Decisión AFG\n\n"
        "The Model Context Protocol (MCP) is an open protocol that connects "
        "language models to external tools and data sources through servers.\n\n"
        "### Related\n\n"
        "- [Transport](transport.md)\n"
    )

    out = _reconcile(reply)

    assert out is not None
    assert out.startswith("# Model Context Protocol\n"), out[:80]
    assert "## Related" in out
    assert "### Related" not in out
    assert "Reunión de Evaluación de Decisión AFG" not in out


def test_promotion_shifts_the_whole_tree_by_the_same_delta() -> None:
    """The shift is one delta applied to every heading, not a per-heading
    normalisation: a document demoted by two keeps `H1 > H2 > H3` intact,
    just re-based at 1. Rewriting only the leading heading would leave
    `# Title` above `### Related` -- a hierarchy worse than the demoted one
    it replaced, because the gap is no longer explainable."""
    reply = (
        "### Wrong Title\n\n"
        "The Model Context Protocol is an open protocol connecting language "
        "models to external tools and data sources through servers.\n\n"
        "#### Transport\n\n"
        "Each server exposes tools over a standard transport.\n\n"
        "##### Details\n\n"
        "One client works with any conformant server.\n"
    )

    out = _reconcile(reply)

    assert out is not None
    assert out.startswith("# Model Context Protocol\n")
    assert "\n## Transport\n" in out
    assert "\n### Details\n" in out
    assert "####" not in out


def test_a_leading_heading_deeper_than_a_later_one_is_left_alone() -> None:
    """Fails CLOSED when the leading heading is NOT the shallowest: shifting
    a tree whose top is not at the top cannot be done without collapsing
    two distinct levels into one, and a corrupted hierarchy is worse than a
    demoted one. The frontmatter title still names the document."""
    reply = (
        "## Wrong Title\n\n"
        "The Model Context Protocol is an open protocol connecting language "
        "models to external tools and data sources through servers.\n\n"
        "# Transport\n\n"
        "Each server exposes tools over a standard transport.\n"
    )

    out = _reconcile(reply)

    assert out == reply.strip()


def test_an_already_top_level_heading_is_unchanged_by_promotion() -> None:
    """Delta zero is a no-op: every reply that worked before #904 takes a
    byte-identical path, so this widening cannot move a document that was
    already shaped correctly."""
    reply = (
        "# Wrong Title\n\n"
        "The Model Context Protocol is an open protocol connecting language "
        "models to external tools and data sources through servers.\n\n"
        "## Related\n\n"
        "- [Transport](transport.md)\n"
    )

    out = _reconcile(reply)

    assert out is not None
    assert out.startswith("# Model Context Protocol\n")
    assert "\n## Related\n" in out


def test_a_hash_inside_a_fenced_block_is_not_promoted() -> None:
    """A `#` line inside a fenced code block is content, not structure.
    Shifting it would edit the sample the document is showing -- and the
    fence's own opening line proves the block is deliberate."""
    reply = (
        "## Wrong Title\n\n"
        "The Model Context Protocol connects language models to external "
        "tools and data sources through servers.\n\n"
        "```sh\n"
        "### not a heading, a shell comment\n"
        "```\n\n"
        "### Related\n\n"
        "- [Transport](transport.md)\n"
    )

    out = _reconcile(reply)

    assert out is not None
    assert out.startswith("# Model Context Protocol\n")
    assert "### not a heading, a shell comment" in out
    assert "\n## Related\n" in out


def test_a_short_closing_run_does_not_end_a_longer_fence() -> None:
    """CommonMark: a closing run must be at least as long as the opening
    one, which is how a four-backtick block quotes a three-backtick fence
    without ending early. Comparing only the marker CHARACTER would end the
    block at the inner fence and promote the `###` line after it -- editing
    the sample the document is showing."""
    reply = (
        "## Wrong Title\n\n"
        "The Model Context Protocol connects language models to external "
        "tools and data sources through servers.\n\n"
        "````md\n"
        "```sh\n"
        "### still inside the outer fence\n"
        "```\n"
        "````\n\n"
        "### Related\n\n"
        "- [Transport](transport.md)\n"
    )

    out = _reconcile(reply)

    assert out is not None
    assert out.startswith("# Model Context Protocol\n")
    assert "### still inside the outer fence" in out
    assert "\n## Related\n" in out


def test_an_info_string_line_does_not_close_a_fence() -> None:
    """CommonMark: a closing fence carries nothing but whitespace after the
    run. A ```python line inside an open block is the block SHOWING an
    opening fence -- treating it as the close would end the block early and
    promote the `###` line after it, editing the sample (#911's R3 review
    finding, fixed in lockstep with `extraction/concept.py`'s copy)."""
    reply = (
        "## Wrong Title\n\n"
        "The Model Context Protocol connects language models to external "
        "tools and data sources through servers.\n\n"
        "```md\n"
        "```python\n"
        "### still inside the fence\n"
        "```\n\n"
        "### Related\n\n"
        "- [Transport](transport.md)\n"
    )

    out = _reconcile(reply)

    assert out is not None
    assert out.startswith("# Model Context Protocol\n")
    assert "### still inside the fence" in out
    assert "\n## Related\n" in out


def test_a_sibling_heading_at_the_leading_level_is_promoted_with_it() -> None:
    """A heading at the leading heading's OWN level is a sibling, not a
    descendant, and the delta reaches it too: the rule is one shift for the
    whole tree, so relative structure is preserved rather than flattened.

    Two level-1 headings is not a new state this introduces -- at delta
    zero `test_only_the_leading_heading_is_pinned` already pins a later
    `# ` heading being kept untouched. Promotion cannot be the thing that
    forbids what delta zero already allows."""
    reply = (
        "## Wrong Title\n\n"
        "The Model Context Protocol connects language models to external "
        "tools and data sources through servers.\n\n"
        "## Transport\n\n"
        "Each server exposes tools over a standard transport.\n\n"
        "### Details\n\n"
        "One client works with any conformant server.\n"
    )

    out = _reconcile(reply)

    assert out is not None
    assert out.startswith("# Model Context Protocol\n")
    assert "\n# Transport\n" in out
    assert "\n## Details\n" in out
