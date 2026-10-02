"""Byte-identity oracle for every production prompt (#1277, ADR-0043).

Each row is the sha256[:16] of the text the model actually receives from a
prompt constant: the rendered value, after any splice. The hashes were
recorded from the code before prompts moved into files, so this test proves
that moving them changed no byte. A deliberate prompt edit updates its row
here and in `test_prompt_registry.py`, and must carry its measurement.
"""

import hashlib
import importlib

import pytest

# (module, attribute) -> sha256(text.encode())[:16]
RENDERED_HASHES: dict[tuple[str, str], str] = {
    ("openkos.extraction.concept", "_SYSTEM_PROMPT"): "6514c14bc12ec1d8",
    ("openkos.extraction.concept", "_REASK_SYSTEM_PROMPT"): "abd2691d6f73732b",
    (
        "openkos.extraction.concept",
        "_PARTICIPANT_CAPTURE_SYSTEM_PROMPT",
    ): "be7e5009a40ede2d",
    ("openkos.extraction.concept", "TRANSCRIPT_SUBJECTS_CLAUSE"): "3002ba68dd2f1b1e",
    ("openkos.extraction.concept", "_LANGUAGE_ANCHOR"): "165db5afd66cf667",
    ("openkos.extraction.judge", "_JUDGE_SYSTEM_PROMPT"): "6bb30c750944f78e",
    ("openkos.resolution.adjudication", "_SYSTEM_PROMPT"): "aaed5c3e06569c83",
    ("openkos.resolution.contradiction", "_SYSTEM_PROMPT"): "bb3180dfc98729c6",
    ("openkos.resolution.edge_typing", "_SYSTEM_PROMPT"): "5166fdeda941bbbb",
    ("openkos.resolution.volatility_typing", "_SYSTEM_PROMPT"): "f978cfef96b5fdf3",
    ("openkos.resolution.reconciliation", "_SYSTEM_PROMPT"): "03864f8da0c22f9b",
    ("openkos.resolution.decision_subject", "_SUBJECT_SYSTEM_PROMPT"): (
        "b105cbe9f130b62d"
    ),
    ("openkos.resolution.decision_revision", "_JUDGE_SYSTEM_PROMPT"): (
        "d8238af6410ac31d"
    ),
    ("openkos.retrieval.answer", "_SYSTEM_PROMPT"): "65e8c9000da5f64f",
    ("openkos.retrieval.answer", "_SUFFICIENCY_PROMPT"): "3e2b44fcf3e8477f",
    ("openkos.llm.prompting", "RATIONALE_LANGUAGE_TEMPLATE"): "9e23dd811331f0c1",
}

# These two key a persisted cache; their values must never move silently.
CACHE_KEYS: dict[tuple[str, str], str] = {
    ("openkos.resolution.decision_subject", "SUBJECT_PROMPT_VERSION"): (
        "b105cbe9f130b62d"
    ),
    ("openkos.resolution.decision_revision", "JUDGE_PROMPT_VERSION"): (
        "d8238af6410ac31d"
    ),
}


@pytest.mark.parametrize(("module", "attr"), sorted(RENDERED_HASHES))
def test_rendered_prompt_bytes_are_unchanged(module: str, attr: str) -> None:
    text = getattr(importlib.import_module(module), attr)
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]

    assert digest == RENDERED_HASHES[module, attr]


@pytest.mark.parametrize(("module", "attr"), sorted(CACHE_KEYS))
def test_cache_keying_versions_are_unchanged(module: str, attr: str) -> None:
    assert getattr(importlib.import_module(module), attr) == CACHE_KEYS[module, attr]
