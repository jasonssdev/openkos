"""Merged-body reconciliation (#645): one `llm.chat` call that rewrites a
merge's stacked body -- the survivor's text plus the absorbed text appended
under a `## Merged content (...)` heading -- as a single coherent document.

Opt-out by ruling: `merge` runs this by default when the stacked share is
at or above the CLI's threshold, disclosed in the plan before consent, and
FALLS BACK to the stacked body on decline, refusal, or any model failure.
The asymmetry that shapes every guard here: a kept stacked body is merely
the disclosed pre-#645 behavior, while a bad rewrite that replaced real
content would be silent data loss. So every doubtful reply returns `None`
and the caller keeps the stack.

Mirrors the sibling `resolution/` modules' seam discipline: the LLM backend
is injected, this module never reads config or the filesystem, and the
prompt is assembled from exactly the caller-provided pieces."""

from __future__ import annotations

import re
from typing import Final

from openkos.llm.base import LLMBackend, Message

_MIN_LENGTH_RATIO: Final = 0.5
"""The reconciled body must be at least half as long as the LONGER input
body. Reconciliation legitimately condenses duplication -- two overlapping
documents can honestly shrink toward the size of one -- but a reply below
half the longer side lost content rather than duplication, and content
loss is the failure the fallback exists to prevent."""

_STACKED_HEADING_MARKER: Final = "## Merged content ("
"""A reply still carrying the stacked heading re-emitted the shape being
fixed instead of reconciling it -- refused."""

_SYSTEM_PROMPT: Final = (
    "You are merging two overlapping notes about the same subject into ONE "
    "coherent document. Both notes are correct; they overlap and phrase the "
    "same content in two voices. Write a single markdown body that covers "
    "every distinct claim from both notes exactly once, in one voice. Keep "
    "concrete details; do not summarize away substance; do not add new "
    "claims. Output ONLY the merged document body in markdown -- no YAML "
    "frontmatter, no headings about merging, no commentary before or after."
)


_HEADING_RE: Final = re.compile(r"^(?P<hashes>#{1,6})(?P<rest>\s.*)?$")
"""One ATX heading line, captured as its level and its remainder. No leading
whitespace is tolerated: `_pin_leading_heading` rewrites structure, and an
indented `#` is either a code block or continuation text."""

_FENCE_RE: Final = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})")
"""A fenced code block's delimiter. A `#` line inside a fence is CONTENT the
document is showing, not structure -- shifting it would edit the sample."""


def _heading_levels(lines: list[str]) -> list[tuple[int, int, str]]:
    """`(index, level, rest)` for every ATX heading line OUTSIDE a fenced
    block, in document order, where `rest` is everything after the hashes.

    `rest` is carried out of the ONE match rather than re-derived at the
    rewrite site: re-matching there would need an impossible-branch guard
    for a `None` this function has already ruled out.

    Fences are tracked by their opening marker so a `~~~` block is not
    closed by a stray ``` and vice versa, and a closing run must be at
    least as long as the opening one -- CommonMark's rule, and the reason a
    ```` ```` ```` block can quote a ``` fence without ending early."""
    out: list[tuple[int, int, str]] = []
    fence: str | None = None
    for index, line in enumerate(lines):
        marker = _FENCE_RE.match(line)
        if fence is not None:
            closing = marker.group("fence") if marker is not None else ""
            if closing[:1] == fence[:1] and len(closing) >= len(fence):
                fence = None
            continue
        if marker is not None:
            fence = marker.group("fence")
            continue
        heading = _HEADING_RE.match(line)
        if heading is not None:
            out.append(
                (index, len(heading.group("hashes")), heading.group("rest") or "")
            )
    return out


def _pin_leading_heading(body: str, survivor_title: str) -> str:
    """Rewrite `body`'s leading heading to `survivor_title` at level 1,
    promoting the whole heading tree by the same delta (issues #695, #904).

    Both input notes carry their own `# ` heading, so the model is free to
    head the reconciled document with either -- in the reported case it took
    the ABSORBED note's. The merged frontmatter `title:`, meanwhile, is
    always the survivor's (the survivor-wins scalar rule in
    `okf.build_merged_document`), and the two then disagree permanently: the
    frontmatter title is what `index.md`, `status`, `list` and citations
    render, while the heading is what a human -- or an OKF consumer with no
    OpenKOS awareness -- reads as the document's name.

    #904's secondary finding is that the pin used to require a LEADING `# `
    exactly, so a reply that shifted the tree down one escaped it entirely:
    the merged document came out with `## <Title>` and `### Related` where
    every other document in the bundle uses `# <Title>` and `## Related`,
    AND kept the absorbed note's title. Nothing downstream catches either --
    `graph/sqlite_graph.py` resolves by markdown link syntax rather than
    heading level, and `lint` has no heading-shape category -- so it passed
    every gate silently.

    The shift is ONE delta applied to every heading, never a per-heading
    normalisation: a document demoted by two keeps `H1 > H2 > H3` intact,
    just re-based at 1. Rewriting only the leading heading would leave
    `# Title` directly above `### Related`, a hierarchy worse than the
    demoted one it replaced because the gap is no longer explainable.

    Deterministic rather than prompt-asked, matching the discipline the
    language gates (#618/#630) settled on: the survivor's title is a fact
    already in hand here, so asking the model to echo it would trade that
    fact for a probability.

    Fails CLOSED, returning the body untouched, on every shape it cannot
    rewrite safely:

    - a title that is blank or carries a newline/carriage return, which
      cannot be spliced as one heading line (such a title cannot reach here
      through the ordinary write paths -- `bundle.index` rejects newlines --
      so this is a guard, not a code path with a known producer);
    - a body that opens with prose, since this pins a heading that exists
      and never INVENTS one, so a legitimately heading-less body keeps its
      shape and the frontmatter title still names the document;
    - a leading heading that is not the SHALLOWEST in the document, because
      shifting a tree whose top is not at the top would collapse two
      distinct levels into one, and a corrupted hierarchy is worse than a
      demoted one.

    A `#` line inside a fenced code block is content the document is
    showing, and is left exactly as written."""
    if not survivor_title.strip() or "\n" in survivor_title or "\r" in survivor_title:
        return body
    stripped = body.lstrip("\n")
    if not stripped:
        return body
    leading_blanks = body[: len(body) - len(stripped)]
    lines = stripped.split("\n")
    headings = _heading_levels(lines)
    if not headings or headings[0][0] != 0:
        # The pin acts on a document that OPENS with its own name; a heading
        # further down is sectioning inside a prose lead-in.
        return body
    leading_level = headings[0][1]
    if any(level < leading_level for _index, level, _rest in headings):
        return body
    delta = leading_level - 1
    lines[0] = f"# {survivor_title}"
    if delta:
        for index, level, rest in headings[1:]:
            lines[index] = "#" * (level - delta) + rest
    return leading_blanks + "\n".join(lines)


def _unwrap_fence(reply: str) -> str:
    """Unwrap a reply the model wrapped in one whole-message code fence."""
    lines = reply.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return reply


def reconcile_merged_body(
    *,
    survivor_title: str,
    survivor_body: str,
    absorbed_body: str,
    llm: LLMBackend,
) -> str | None:
    """Rewrite the two bodies as one document via `llm`, or `None` on any
    fail-closed refusal (the caller keeps the stacked body):

    - an empty or whitespace-only reply;
    - a reply echoing YAML frontmatter (`---` first line) -- the prompt
      asked for a body, and splicing frontmatter into a body corrupts the
      document;
    - a reply still carrying the `## Merged content (` stacked heading;
    - a reply shorter than `_MIN_LENGTH_RATIO` of the longer input body
      (content loss, not deduplication).

    `OllamaError`-family exceptions from `llm.chat` propagate -- the CLI
    caller owns the transport-failure fallback, mirroring how the other
    `resolution/` seams report availability at the verb layer."""
    messages: list[Message] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"SUBJECT: {survivor_title}\n\n"
                f"NOTE A:\n{survivor_body}\n\n"
                f"NOTE B:\n{absorbed_body}"
            ),
        },
    ]
    reply = llm.chat(messages)
    text = _unwrap_fence(reply.strip())
    if not text:
        return None
    if text.startswith("---"):
        return None
    if _STACKED_HEADING_MARKER in text:
        return None
    floor = _MIN_LENGTH_RATIO * max(len(survivor_body), len(absorbed_body))
    if len(text) < floor:
        return None
    # #695: pin AFTER every refusal gate, so the length floor still scores
    # the model's own reply rather than one this function just edited.
    return _pin_leading_heading(text, survivor_title)
