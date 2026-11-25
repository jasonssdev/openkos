"""The opt-in `evidence_quote` setting: the concept prompt asks for a verbatim
quote only when it is on, and the prompt it then sends is exactly the one the
pre-registered A/B measured (`evals/concept_model_screen/`)."""

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from openkos import config
from openkos.application import ingest as application_ingest
from openkos.extraction import concept as concept_mod
from openkos.extraction import evidence_quote
from openkos.llm.base import Message

REGISTERED_SHA256 = "9dbb82be4ede1522fa83cd7544677735edb374c52ce65dda838aad1d4230e58b"

_TITLE = "Notes"
_TEXT = "The team chose the regular chip because it is cheaper to buy in bulk.\n"
_CONCEPT = (
    '[{"type": "Concept", "title": "Chip choice", "description": "About chips.",'
    ' "body": "The team chose the regular chip."}]'
)


class _RecordingLLM:
    """Answers every call with one concept and records each system prompt."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.systems.append(messages[0]["content"])
        return _CONCEPT


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_default_is_off() -> None:
    assert config.DEFAULT_EVIDENCE_QUOTE is False


def test_the_default_prompt_is_the_shipped_prompt_unchanged() -> None:
    default = concept_mod._build_messages(_TEXT, _TITLE)[0]["content"]
    explicit_off = concept_mod._build_messages(_TEXT, _TITLE, evidence_quote=False)[0][
        "content"
    ]
    assert default == concept_mod._SYSTEM_PROMPT
    assert explicit_off == concept_mod._SYSTEM_PROMPT
    assert evidence_quote.INSTRUCTION not in default
    assert '"evidence"' not in default


def test_the_prompt_with_the_setting_on_is_the_registered_treatment() -> None:
    treated = concept_mod._build_messages(_TEXT, _TITLE, evidence_quote=True)[0][
        "content"
    ]
    assert _sha(treated) == REGISTERED_SHA256
    assert evidence_quote.INSTRUCTION in treated


def test_only_the_system_message_differs() -> None:
    off = concept_mod._build_messages(_TEXT, _TITLE)
    on = concept_mod._build_messages(_TEXT, _TITLE, evidence_quote=True)
    assert off[1] == on[1]
    assert off[0]["content"] != on[0]["content"]


def test_a_drifted_prompt_is_refused_rather_than_spliced_elsewhere() -> None:
    with pytest.raises(ValueError, match="drifted"):
        evidence_quote.treat("a prompt with no anchors")


def test_the_union_extractor_sends_the_treatment_only_when_on() -> None:
    off, on = _RecordingLLM(), _RecordingLLM()
    concept_mod.extract_concept_union(_TEXT, source_title=_TITLE, llm=off)
    concept_mod.extract_concept_union(
        _TEXT, source_title=_TITLE, llm=on, evidence_quote=True
    )
    assert concept_mod._SYSTEM_PROMPT in off.systems
    assert evidence_quote.INSTRUCTION not in "".join(off.systems)
    mains = [s for s in on.systems if s.startswith(concept_mod._SYSTEM_PROMPT[:40])]
    assert mains
    assert all(_sha(s) == REGISTERED_SHA256 for s in mains)


def test_the_legacy_extractor_sends_the_treatment_only_when_on() -> None:
    off, on = _RecordingLLM(), _RecordingLLM()
    concept_mod.extract_concept(_TEXT, source_title=_TITLE, llm=off)
    concept_mod.extract_concept(_TEXT, source_title=_TITLE, llm=on, evidence_quote=True)
    assert evidence_quote.INSTRUCTION not in "".join(off.systems)
    assert evidence_quote.INSTRUCTION in on.systems[0]


def test_every_other_prompt_is_unchanged_by_the_setting() -> None:
    """The re-ask, decision pass and participant capture never carry the
    instruction, with the setting on or off."""
    meeting = (
        "Alice: Which chip should we use for the board?\n"
        "Bob: The regular chip is cheaper, so let us use it.\n"
    ) * 6
    off, on = _RecordingLLM(), _RecordingLLM()
    concept_mod.extract_concept_union(
        meeting, source_title="Weekly meeting", llm=off, transcript_decisions=True
    )
    concept_mod.extract_concept_union(
        meeting,
        source_title="Weekly meeting",
        llm=on,
        transcript_decisions=True,
        evidence_quote=True,
    )
    off_systems, on_systems = set(off.systems), set(on.systems)
    treated = {s for s in on_systems if evidence_quote.INSTRUCTION in s}
    assert {_sha(s) for s in treated} == {REGISTERED_SHA256}
    assert on_systems - treated == off_systems - {concept_mod._SYSTEM_PROMPT}


def test_the_chunked_path_sends_the_treatment_in_every_window() -> None:
    long_text = "\n".join(
        f"Line {i}: the team discussed the regular chip and its bulk price."
        for i in range(2000)
    )
    llm = _RecordingLLM()
    concept_mod.extract_concept(
        long_text, source_title=_TITLE, llm=llm, concurrent=False
    )
    assert len(llm.systems) > 1
    assert evidence_quote.INSTRUCTION not in "".join(llm.systems)

    llm_on = _RecordingLLM()
    concept_mod.extract_concept(
        long_text, source_title=_TITLE, llm=llm_on, evidence_quote=True
    )
    mains = [s for s in llm_on.systems if evidence_quote.INSTRUCTION in s]
    assert len(mains) > 1
    assert {_sha(s) for s in mains} == {REGISTERED_SHA256}


def test_the_config_key_reads_validates_and_defaults_off(tmp_path: Path) -> None:
    (tmp_path / "openkos.yaml").write_text("model: gemma3\n", encoding="utf-8")
    assert config.read_config(tmp_path).evidence_quote is False
    (tmp_path / "openkos.yaml").write_text("evidence_quote: null\n", encoding="utf-8")
    assert config.read_config(tmp_path).evidence_quote is False
    (tmp_path / "openkos.yaml").write_text("evidence_quote: true\n", encoding="utf-8")
    assert config.read_config(tmp_path).evidence_quote is True


@pytest.mark.parametrize("body", ["1", '"yes"', "[true]"])
def test_a_non_boolean_evidence_quote_is_refused(tmp_path: Path, body: str) -> None:
    (tmp_path / "openkos.yaml").write_text(
        f"evidence_quote: {body}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="'evidence_quote' must be a boolean"):
        config.read_config(tmp_path)


def _extractor(seen: list[dict[str, Any]]) -> Any:
    def _fake(*args: Any, **kwargs: Any) -> concept_mod.ExtractionOutcome:
        seen.append(kwargs)
        return concept_mod.ExtractionOutcome(
            objects=[],
            report=concept_mod.ExtractionReport(
                produced=0, retained=0, chunks=1, runs=1
            ),
        )

    return _fake


@pytest.mark.parametrize("union_judge", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
def test_the_key_reaches_whichever_extractor_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    union_judge: bool,
    enabled: bool,
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(application_ingest, "extract_concept_union", _extractor(seen))
    monkeypatch.setattr(application_ingest, "extract_concept", _extractor(seen))
    (tmp_path / "openkos.yaml").write_text(
        f"model: x\nevidence_quote: {str(enabled).lower()}\n", encoding="utf-8"
    )
    application_ingest.stage_derived_objects(
        raw_content="Some notes.",
        source_title="Notes",
        source_slug="b",
        workspace_floor="private",
        stamp_sensitivity="public",
        timestamp="2026-02-01T00:00:00Z",
        bundle_dir=tmp_path / "bundle",
        llm=_RecordingLLM(),
        cfg=config.read_config(tmp_path),
        union_judge=union_judge,
    )
    assert [kw["evidence_quote"] for kw in seen] == [enabled]
