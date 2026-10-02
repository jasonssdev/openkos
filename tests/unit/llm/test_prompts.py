"""The prompt registry and loader (#1277, ADR-0043).

`PINNED` is the one table of prompt id -> content hash. Editing a prompt
file fails `test_every_prompt_file_matches_its_pin` until the row is updated
on purpose, which is the point: a prompt change is never silent, and the
pull request that updates the pin is the one that must carry its A/B
measurement. A templated prompt's hash is of the file as stored, with its
`{{placeholders}}` unfilled; the text the model receives after filling is
pinned separately in `tests/unit/test_prompt_byte_identity.py`.
"""

from pathlib import Path

import pytest

from openkos.llm import prompts

PINNED: dict[str, str] = {
    "adjudication/system": "aaed5c3e06569c83",
    "answer/sufficiency": "afc4644b27500c2c",
    "answer/system": "9cbf11e8ac9f8220",
    "contradiction/system": "bb3180dfc98729c6",
    "decision_revision/judge": "d8238af6410ac31d",
    "decision_subject/system": "b105cbe9f130b62d",
    "edge_typing/system": "accabf7c5bcafb6b",
    "extraction/judge": "6bb30c750944f78e",
    "extraction/language_anchor": "165db5afd66cf667",
    "extraction/participant_capture": "be7e5009a40ede2d",
    "extraction/reask": "abd2691d6f73732b",
    "extraction/system": "68ea44d4370b2135",
    "extraction/transcript_subjects_clause": "3002ba68dd2f1b1e",
    "rationale/language_template": "9e23dd811331f0c1",
    "reconciliation/system": "03864f8da0c22f9b",
    "volatility_typing/system": "f978cfef96b5fdf3",
}

_PROMPTS_DIR = Path(prompts.__file__).resolve().parents[1] / "prompts"


def _ids_on_disk() -> set[str]:
    return {
        path.relative_to(_PROMPTS_DIR).with_suffix("").as_posix()
        for path in _PROMPTS_DIR.rglob("*")
        if path.is_file()
    }


def test_the_registry_and_the_folder_list_the_same_prompts() -> None:
    assert _ids_on_disk() == set(PINNED)


@pytest.mark.parametrize("prompt_id", sorted(PINNED))
def test_every_prompt_file_matches_its_pin(prompt_id: str) -> None:
    assert prompts.raw_prompt_hash(prompt_id) == PINNED[prompt_id]


@pytest.mark.parametrize("prompt_id", sorted(PINNED))
def test_a_prompt_file_has_no_carriage_return(prompt_id: str) -> None:
    path = _PROMPTS_DIR / f"{prompt_id}{prompts.PROMPT_SUFFIX}"

    assert b"\r" not in path.read_bytes()


def test_the_loaded_text_is_the_file_bytes_with_nothing_stripped() -> None:
    path = _PROMPTS_DIR / "extraction/transcript_subjects_clause.md"
    raw = path.read_bytes().decode("utf-8")

    assert raw.endswith("\n\n")
    assert prompts.load_prompt("extraction/transcript_subjects_clause") == raw


def test_placeholders_are_filled_exactly_once() -> None:
    text = prompts.load_prompt("answer/sufficiency", sufficiency_none="NONE")

    assert "{{" not in text
    assert "single word NONE." in text


def test_an_unfilled_placeholder_is_refused() -> None:
    with pytest.raises(ValueError, match="sufficiency_none"):
        prompts.load_prompt("answer/sufficiency")


def test_an_unused_argument_is_refused() -> None:
    with pytest.raises(ValueError, match="do not match"):
        prompts.load_prompt("contradiction/system", stray="x")


@pytest.mark.parametrize("bad", ["", "system", "../x/y", "a/b/c", "A/b", "a/b.md"])
def test_a_malformed_id_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="malformed prompt id"):
        prompts.load_prompt(bad)


def test_an_unknown_id_is_a_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        prompts.load_prompt("nope/missing")


def test_prompt_hash_is_the_sixteen_hex_prefix_of_sha256() -> None:
    assert prompts.prompt_hash("abc") == "ba7816bf8f01cfea"
