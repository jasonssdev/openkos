"""The ONE whole-text prompt bound: plan a char budget against the window
the backend will actually enforce, and excerpt what does not fit.

Ollama does not raise on an oversized prompt. llama.cpp keeps a few head
tokens plus the LAST half of the window -- measured during #866 on a 54K-char
transcript as `truncating input prompt: limit=6146 prompt=16091 keep=4` -- so
the system prompt and most of the content are silently cut, the model answers
a decapitated prompt, and nothing anywhere reports it. Every seam that sends
a whole document in one prompt therefore has to plan its own size.

#866 closed that on the INGEST path and #882 found the identical defect on
the RETRIEVAL path, where it was worse: `query --save` filed the retrieved
citations as provenance verbatim, so a document the model never read became
a false provenance claim on disk. Rather than copy the arithmetic into a
second module -- the two-renderer drift #883 had just closed elsewhere in
this codebase -- both paths call in here.

A config-free leaf, exactly like the chunking constants it grew out of: pure
string/integer arithmetic over its own arguments, no I/O and no imports from
the rest of the package. The two window/ceiling defaults mirror
`config.DEFAULT_CONTEXT_WINDOW` and `config.DEFAULT_MAX_GENERATION_TOKENS`
as literals for that reason; a backend that advertises its own pinned values
overrides them.
"""

from collections.abc import Sequence
from typing import Final

PLANNING_CONTEXT_WINDOW: Final = 12_288
"""Planning window (tokens) used when the backend advertises none.

Mirrors `config.DEFAULT_CONTEXT_WINDOW`. Planning conservatively is strictly
safer than not planning: a smaller-than-necessary excerpt costs recall,
while an unplanned prompt is decapitated server-side with no notice."""

REPLY_RESERVE: Final = 8_192
"""Generation room (tokens) held back when the backend advertises no
ceiling of its own.

Mirrors `config.DEFAULT_MAX_GENERATION_TOKENS`. `num_ctx` bounds prompt AND
completion together, so a budget that spent the whole window on the prompt
would leave the reply to be cut off instead -- trading a silent prompt
truncation for a silent reply truncation."""

TOKENS_PER_CHAR: Final = 0.40
"""Chars-to-tokens planning ratio.

Deliberately pessimistic. #882 measured 3.55 chars/token on real Spanish
prose (110,358 chars -> 31,128 tokens on `qwen3:8b`), i.e. ~0.28
tokens/char; planning at 0.40 leaves headroom for denser material rather
than budgeting to the exact edge of a window that truncates silently when
the estimate is wrong."""

ELISION_MARKER: Final = "\n[... source elided to fit the context window ...]\n"
"""Rendered between non-adjacent windows of an excerpted text, so the model
is told text is missing rather than left to read two spliced fragments as
continuous prose."""


def planning_window(llm: object) -> int:
    """The context window to plan against: the backend's pinned one when it
    advertises a usable value, else `PLANNING_CONTEXT_WINDOW`.

    Duck-typed rather than typed against a Protocol: every structural fake
    in the test suite, and any third-party `LLMBackend`, is a backend
    without the attribute, and all of them must still get a bounded prompt.

    `True` is rejected explicitly. `bool` is a subclass of `int` in Python,
    so an `isinstance(window, int)` check alone would plan a ONE-token
    window and excerpt everything to nothing -- the same shape
    `config.read_config` rejects for the same reason.

    Never raises. The attribute read is the one frame that can throw (a
    property on a custom backend), and it degrades to the default. #866
    closed both halves of this in one review cycle: unguarded, a throwing
    backend aborted the pipeline; guarded around the whole body, it silently
    skipped the bound and sent the full oversized prompt. Guarding exactly
    the read closes both."""
    try:
        window = getattr(llm, "context_window", None)
    except Exception:
        return PLANNING_CONTEXT_WINDOW
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        return PLANNING_CONTEXT_WINDOW
    return window


def reply_reserve(llm: object) -> int:
    """Generation room to hold back: the backend's pinned
    `max_generation_tokens` when it advertises a usable value, else
    `REPLY_RESERVE`.

    Same duck-typed, never-raises, `bool`-rejecting contract as
    `planning_window` above, and for the same reasons. Reading the real
    ceiling matters because a caller that pinned a small one should get the
    window back as prompt room rather than having it reserved for a reply
    that cannot use it."""
    try:
        reserve = getattr(llm, "max_generation_tokens", None)
    except Exception:
        return REPLY_RESERVE
    if isinstance(reserve, bool) or not isinstance(reserve, int) or reserve <= 0:
        return REPLY_RESERVE
    return reserve


def budget_chars(
    *, planning: int, generation_reserve_tokens: int, overhead_chars: int
) -> int:
    """Chars available for the bounded text: the planning window minus the
    reply reserve, converted to chars, minus the prompt's own fixed
    overhead (system rules, labels, the question -- they spend the same
    window).

    Floors at `0`: a small pinned window under a large overhead sends an
    EMPTY text portion, which keeps the call's instructions intact. A
    negative budget would be read by a slice as an offset from the end and
    silently return the WRONG text rather than none."""
    budget = (
        int((planning - generation_reserve_tokens) / TOKENS_PER_CHAR) - overhead_chars
    )
    return max(budget, 0)


def chunk_lines(text: str, target: int) -> list[str]:
    """Pack LINES into windows of at most `target` chars, never splitting
    inside a line (a truncated utterance is not extractable content).

    Lines, not paragraphs: the material this exists for -- speaker-labelled
    transcripts -- has no blank lines at all, which is exactly how the first
    chunking probe silently failed to chunk (#454). A single line longer
    than `target` becomes its own oversized window, whole.

    `target` is a required argument, never a signature default: a default
    expression is evaluated once at definition time, which made the ingest
    constant unpatchable and let an eval arm label itself 8 KB while packing
    4 KB windows (#699). Callers resolve their own constant at call time and
    pass it in.

    Lossless by construction: `"\\n".join(chunk_lines(text, n)) == text`."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        if current and size + len(line) + 1 > target:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def bounded_text(
    text: str,
    *,
    budget: int,
    windows: Sequence[str],
    marker: str = ELISION_MARKER,
) -> tuple[str, bool]:
    """`text` if it fits `budget`, else a deterministic even-coverage
    excerpt of `windows` that does -- and whether it was excerpted.

    A fitting text is returned BYTE-IDENTICAL with the flag down: the bound
    must not change model input on any prompt that already fits.

    The excerpt maximizes window count: evenly spaced windows -- including
    the first and the last whenever at least two fit -- joined with `marker`
    across gaps and plain newlines when adjacent, at the largest count that
    fits. Even coverage rather than a head or tail cut, because the
    server-side truncation this replaces is itself precisely a one-ended
    view, so reproducing it would buy nothing.

    When not even two windows fit, the first window is hard-truncated to the
    budget as the last resort -- empty when the overhead swallowed the
    budget entirely. There is deliberately no floor ABOVE the budget: a
    floor that sends more than fits re-creates the overflow this module
    exists to close.

    The window search is O(len(windows)^2) in integer additions. Measured on
    this path at an 800-char window target: 69 windows (a 55,000-char
    source, the size that motivated #882) costs 0.3 ms, 625 windows costs
    27 ms, and 2,500 windows -- a 2,000,000-char document -- costs 434 ms.
    Left quadratic deliberately: the cut is not strictly monotonic in window
    count, because whether two picked windows are adjacent changes the join
    cost, so a binary search over counts could return a smaller excerpt than
    one that fits."""
    if len(text) <= budget:
        return text, False
    if not windows:
        return "", True

    total_windows = len(windows)
    for count in range(total_windows - 1, 1, -1):
        picked = sorted(
            {round(i * (total_windows - 1) / (count - 1)) for i in range(count)}
        )
        # Sized arithmetically first; the excerpt string is built ONCE, for
        # the single count that fits. The search stays O(total_windows^2) in
        # integer additions, but building every candidate excerpt STRING
        # made it quadratic in CHARS -- the cost this ordering removes from
        # the synchronous path.
        size = sum(len(windows[index]) for index in picked)
        for position, index in enumerate(picked[1:], start=1):
            adjacent = index == picked[position - 1] + 1
            size += 1 if adjacent else len(marker)
        if size > budget:
            continue
        parts: list[str] = []
        previous: int | None = None
        for index in picked:
            if previous is not None:
                parts.append(marker if index > previous + 1 else "\n")
            parts.append(windows[index])
            previous = index
        return "".join(parts), True
    return windows[0][:budget], True


def _remainder_order(sizes: Sequence[int], open_indexes: list[int]) -> list[int]:
    """`open_indexes` ordered largest-need-first, ties by lowest index.

    The order the chars an integer division cannot split are handed out in.
    Keyed on SIZE rather than position so `fair_shares` stays
    order-independent: handing them out by position made the same multiset
    of documents allow different amounts once re-ranked, which its own
    docstring promised could not happen."""
    return sorted(open_indexes, key=lambda index: (-sizes[index], index))


def fair_shares(sizes: Sequence[int], *, budget: int) -> list[int]:
    """How many chars each of `sizes` may spend of a SHARED `budget`, in
    input order (#882).

    The ingest bound sizes one text against the whole window; retrieval
    sizes SEVERAL competing blocks against one window, which the single-text
    bound cannot express. An equal split alone would waste the window: a
    5-char block sitting on a 100-char share while a 500-char block is cut
    to 100 is the same lost recall the bound exists to limit.

    Water-filling, therefore: every block is offered an equal share of what
    is left, blocks that need less than their share take only what they need
    and release the remainder, and the pass repeats over the blocks still
    capped until nothing more can be released.

    Deterministic AND order-independent: the result depends on the multiset
    of sizes, not on which block was fused first, so a re-ranking cannot
    change what each block is allowed. That property is why the leftover
    chars an integer division cannot split go to the LARGEST needy blocks
    rather than the lowest indexes. Lowest-index-first looks equivalent and
    is not: measured on sizes `[7, 9, 100]` at a budget of 11 it returned
    `[4, 4, 3]`, while the same multiset fused in the reverse order returned
    `[3, 4, 4]` -- the same documents, re-ranked, allowed different amounts.
    Ties between equal sizes fall back to the lowest index, which cannot
    break the property: equal-size blocks are interchangeable, so the
    multiset of shares is unchanged either way.

    Never returns more than `budget` in total, and never more than a block's
    own size (a block is not padded to its share)."""
    if not sizes:
        return []
    shares = [0] * len(sizes)
    settled = [False] * len(sizes)
    remaining = max(budget, 0)
    while True:
        open_indexes = [i for i, done in enumerate(settled) if not done]
        if not open_indexes or remaining <= 0:
            break
        share = remaining // len(open_indexes)
        if share <= 0:
            # Fewer chars left than open blocks: give one char each to the
            # blocks that still need one, lowest index first, rather than
            # dropping the remainder entirely. Deterministic, and it keeps
            # `sum(shares) <= budget` exact. A char can go unspent when
            # fewer blocks need one than there are chars left; that residue
            # is at most one per block and buys nothing worth a second pass.
            #
            # SKIPPING blocks that need nothing is not a detail: handing a
            # zero-length body one char both breaks this function's own
            # never-more-than-its-own-size contract AND starves a later
            # block that had a use for it -- `fair_shares([0, 100],
            # budget=1)` returned `[1, 0]`, spending the only available
            # char on the empty block. An empty body is ordinary (a concept
            # that is all frontmatter), so this is reachable, not
            # theoretical.
            for index in _remainder_order(sizes, open_indexes):
                if remaining <= 0:
                    break
                if sizes[index] <= shares[index]:
                    continue
                shares[index] += 1
                remaining -= 1
            break
        released = False
        for index in open_indexes:
            if sizes[index] <= share:
                shares[index] = sizes[index]
                settled[index] = True
                remaining -= sizes[index]
                released = True
        if not released:
            for index in open_indexes:
                shares[index] = share
                remaining -= share
            for index in _remainder_order(sizes, open_indexes)[:remaining]:
                shares[index] += 1
            break
    return shares
