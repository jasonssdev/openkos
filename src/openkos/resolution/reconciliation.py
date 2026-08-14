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
    return text
