"""Unit tests for the pinned rationale language (#812) across BOTH curate
rationale prompts: `resolution.edge_typing` (curate's Structure stage) and
`resolution.volatility_typing` (curate's Metadata stage).

One file for both on purpose. The defect #812 reports is a table the
operator reads top to bottom whose rows arrive in whichever language each
type's documents happened to dominate, and it is produced by two separate
suggesters that mirror each other line for line. A per-module test pair
would let one of them be fixed and the other quietly left behind -- the
same table would still mix languages, and both files would be green. Every
assertion below therefore runs against both prompts under one parametrize.

The byte-identity tests are the load-bearing ones. This repo does not adopt
prompt changes on a common path without measurement
(`extraction.concept._LANGUAGE_ANCHOR`'s docstring, and #459's measured
regression), so the UNSET default must assemble a prompt byte-identical to
the pre-#812 one -- not merely equivalent. `_SYSTEM_PROMPT` is that
pre-#812 text: nothing outside these tests appends to it, so comparing the
assembled system turn against the constant verbatim is the byte-identity
claim, and it fails the moment the language clause becomes unconditional.

Zero network: `_build_messages` is pure, and the two seam tests use the
reply-queue fake `LLMBackend` the sibling suite already uses.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from openkos.graph.base import Edge
from openkos.llm.base import Message
from openkos.resolution import edge_typing as edge_typing_mod
from openkos.resolution import volatility_typing as volatility_typing_mod

_PINNED = "Spanish"
"""One arbitrary pinned language, used everywhere below.

Free-form by design (`config.Config.rationale_language`), so the tests name
a language the engine has no enum for -- pinning the value the operator
typed, rather than a token the engine recognises."""


class _FakeLLM:
    """A structural `LLMBackend`: records every `chat` call, returns queued
    replies in call order. Mirrors `_FakeLLM` in
    `tests/unit/resolution/test_edge_typing.py`."""

    def __init__(self, replies: Sequence[str] = ()) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self._replies.pop(0)


def _edge_messages(language: str | None) -> list[Message]:
    """Structure's 2-message prompt over one fixed edge."""
    return edge_typing_mod._build_messages(
        Edge(source_id="concepts/a", target_id="concepts/b"),
        ("Alpha", "Alpha body."),
        ("Beta", "Beta body."),
        rationale_language=language,
    )


def _volatility_messages(language: str | None) -> list[Message]:
    """Metadata's 2-message prompt over one fixed type."""
    return volatility_typing_mod._build_messages(
        "Person",
        "slow",
        ["Person body."],
        rationale_language=language,
    )


_BOTH_PROMPTS = pytest.mark.parametrize(
    ("build", "system_prompt"),
    [
        pytest.param(
            _edge_messages, edge_typing_mod._SYSTEM_PROMPT, id="structure-edge-typing"
        ),
        pytest.param(
            _volatility_messages,
            volatility_typing_mod._SYSTEM_PROMPT,
            id="metadata-volatility-typing",
        ),
    ],
)


@_BOTH_PROMPTS
def test_unset_language_assembles_a_byte_identical_prompt(
    build: Callable[[str | None], list[Message]], system_prompt: str
) -> None:
    """The default (no pinned language) sends the pre-#812 bytes.

    This is the whole reason #812 is a config key and not a prompt edit: a
    longer prompt has already been measured in this project to LOSE its A/B
    (recall down, decay up, runaway generations), and
    `_LANGUAGE_ANCHOR`'s docstring records that a language instruction must
    not reach a common path unmeasured. Every existing workspace is
    therefore owed a prompt that did not change at all -- so the assembled
    system turn must be `_SYSTEM_PROMPT` verbatim, not a re-rendered
    equivalent."""
    messages = build(None)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == system_prompt


@_BOTH_PROMPTS
def test_unset_language_is_the_signature_default(
    build: Callable[[str | None], list[Message]], system_prompt: str
) -> None:
    """Omitting the parameter entirely is the same as passing `None`.

    Guards the other half of the byte-identity promise: a library or eval
    caller that never heard of #812 must not have to opt out of it."""
    if build is _edge_messages:
        omitted = edge_typing_mod._build_messages(
            Edge(source_id="concepts/a", target_id="concepts/b"),
            ("Alpha", "Alpha body."),
            ("Beta", "Beta body."),
        )
    else:
        omitted = volatility_typing_mod._build_messages(
            "Person", "slow", ["Person body."]
        )

    assert omitted == build(None)


@_BOTH_PROMPTS
def test_pinned_language_is_named_in_the_system_turn(
    build: Callable[[str | None], list[Message]], system_prompt: str
) -> None:
    """A pinned language reaches the model, and names itself.

    Asserted against the APPENDED slice, not the whole turn: both prompts
    already contain the word "rationale", so a whole-turn membership check
    would pass without the clause existing. The pinned value is
    interpolated verbatim rather than mapped through a table --
    `rationale_language` is free-form (see its `Config` docstring), so what
    the operator typed is what the model is told."""
    content = build(_PINNED)[0]["content"]
    appended = content[len(system_prompt) :]

    assert _PINNED in appended
    assert "rationale" in appended


@_BOTH_PROMPTS
def test_pinned_language_only_appends_to_the_system_turn(
    build: Callable[[str | None], list[Message]], system_prompt: str
) -> None:
    """Pinning adds a clause; it never rewrites the rubric above it.

    Additive-only keeps the two arms of a measurement comparable: the
    pinned arm differs from the baseline by exactly the appended clause, so
    a quality movement cannot be blamed on an unrelated re-wording."""
    content = build(_PINNED)[0]["content"]

    assert content.startswith(system_prompt)
    assert len(content) > len(system_prompt)


@_BOTH_PROMPTS
def test_pinned_language_leaves_the_user_turn_untouched(
    build: Callable[[str | None], list[Message]], system_prompt: str
) -> None:
    """The user turn carries the concept bodies and nothing about language.

    #812's cause is that the bodies pick the language by default; the fix
    belongs in the system half, where the tool speaks, not in the half the
    documents own."""
    assert build(_PINNED)[1] == build(None)[1]


def test_suggest_edge_types_threads_the_language_to_the_prompt(
    tmp_path: Path,
) -> None:
    """The Structure entry point actually sends it -- not merely accepts it.

    A parameter that is threaded but never passed is the "computed but
    never read" defect this repo has already paid for twice (#690), and the
    only way to see it is to read the bytes that left the seam."""
    bundle_dir = tmp_path
    llm = _FakeLLM(['{"type": "related_to", "rationale": "x"}'])

    edge_typing_mod.suggest_edge_types(
        [Edge(source_id="concepts/a", target_id="concepts/b")],
        bundle_dir=bundle_dir,
        llm=llm,
        rationale_language=_PINNED,
    )

    assert len(llm.calls) == 1
    assert _PINNED in llm.calls[0][0]["content"]


def test_suggest_volatility_threads_the_language_to_the_prompt(
    tmp_path: Path,
) -> None:
    """The Metadata entry point actually sends it -- same #690 reasoning."""
    bundle_dir = tmp_path
    (bundle_dir / "person.md").write_text(
        "---\ntype: Person\ntitle: Ana\nsensitivity: private\n---\n\nBody.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM(['{"tier": "slow", "rationale": "x"}'])

    volatility_typing_mod.suggest_volatility(
        bundle_dir,
        llm=llm,
        rationale_language=_PINNED,
    )

    assert len(llm.calls) == 1
    assert _PINNED in llm.calls[0][0]["content"]
