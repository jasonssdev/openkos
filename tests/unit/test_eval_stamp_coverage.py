"""Every harness that calls a chat model stamps what it measured (#1277).

A stored result that names only its arm and a timestamp cannot be tied to the
prompt text, the harness commit or the model weights it measured. #1269 put
that identity in `evals/harness_stamp.py` and #1277 wires it into every
harness; this test is what keeps a NEW harness from quietly skipping it, the
way `evals/run_self_tests.py` discovers rather than lists.

THE RULE. A file under `evals/` that constructs a model client or posts to a
chat endpoint must call `build_stamp(` or `write_stamp_sidecar(`, or appear in
`EXEMPT` with the reason it needs no stamp. The check is source-level on
purpose: the runners insert into `sys.path` and import bare sibling modules at
module scope, so they cannot be imported into the unit suite (the same reason
`test_harness_report.py` loads its module from a path).

Each exemption is itself checked, so it cannot rot: it must name a file that
exists, that still matches the model-call pattern, and that still carries no
stamp (a stamped file left in `EXEMPT` would hide the day the stamp is lost).
"""

import re
from pathlib import Path

import pytest

_EVALS = Path(__file__).resolve().parents[2] / "evals"

_MODEL_CALL = re.compile(r"OllamaClient\(|/api/chat|/api/generate|/api/embed|\.chat\(")
_STAMP_CALL = re.compile(r"\b(?:build_stamp|write_stamp_sidecar)\(")

EXEMPT: dict[str, str] = {
    "bakeoff/bakeoff_ollama.py": "reads Ollama's model list and load state; no results",
    "bakeoff/run_bakeoff.py": "the driver: it reads and checks the children's stamps",
    "decision_extraction/scripts/run_type_coverage.py": "prints to stdout; stores no result",
    "extraction_collapse/run_collapse_probe.py": "prints to stdout; stores no result",
    "ingest_concurrency/probe_fanout_share.py": "prints to stdout; stores no result",
    "ingest_concurrency/probe_server_capacity.py": "raw server timing with a fixed user prompt and no system prompt; stores no result",
    "insight_scan_bound/run_insight_scan_bound_probe.py": "embedding model only, zero chat calls",
    "proximity_threshold/run_proximity_threshold_probe.py": "embedding model only, zero chat calls",
    "query_grounding/run_query_grounding_probe.py": "embedding model only, zero chat calls",
    "query_identity/run_query_identity_probe.py": "embedding model only, zero chat calls",
    "retrieval_stability/run_retrieval_stability_probe.py": "retrieval only: the chat LLM is a stub",
    "section_coverage/run_embedding_coverage_probe.py": "embedding model only, zero chat calls",
}


def _calls_a_model(source: str) -> bool:
    return _MODEL_CALL.search(source) is not None


def _stamps(source: str) -> bool:
    return _STAMP_CALL.search(source) is not None


def unstamped_model_callers(root: Path, exempt: dict[str, str]) -> list[str]:
    """Paths (relative to `root`) that call a model, carry no stamp and are
    not exempt."""
    missing: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == "harness_stamp.py" or rel in exempt:
            continue
        source = path.read_text(encoding="utf-8")
        if _calls_a_model(source) and not _stamps(source):
            missing.append(rel)
    return missing


def test_every_model_calling_harness_stamps_its_results() -> None:
    assert unstamped_model_callers(_EVALS, EXEMPT) == [], (
        "these harnesses call a model but never build a stamp: wire "
        "`harness_stamp.build_stamp` (or `write_stamp_sidecar`) into the "
        "result they store, or add them to EXEMPT with the reason"
    )


@pytest.mark.parametrize("rel", sorted(EXEMPT))
def test_each_exemption_is_still_true(rel: str) -> None:
    path = _EVALS / rel
    assert path.is_file(), f"{rel} no longer exists: drop it from EXEMPT"
    source = path.read_text(encoding="utf-8")
    assert _calls_a_model(source), f"{rel} no longer calls a model: drop the exemption"
    assert not _stamps(source), f"{rel} stamps now: drop the exemption"


def test_the_scan_fails_on_a_harness_that_drops_its_stamp(tmp_path: Path) -> None:
    """The scan must be able to come out AGAINST a harness: one that calls a
    model and writes results with no stamp is reported, the same file with the
    stamp is not."""
    bare = "client = OllamaClient(model='m')\npath.write_text(json.dumps(rows))\n"
    stamped = bare.replace("json.dumps(rows)", "json.dumps({'stamp': build_stamp()})")
    (tmp_path / "bare.py").write_text(bare, encoding="utf-8")
    (tmp_path / "stamped.py").write_text(stamped, encoding="utf-8")
    (tmp_path / "sidecar.py").write_text(
        bare + "write_stamp_sidecar(path, stamp)\n", encoding="utf-8"
    )
    (tmp_path / "pure.py").write_text("print('no model here')\n", encoding="utf-8")

    assert unstamped_model_callers(tmp_path, {}) == ["bare.py"]
    assert unstamped_model_callers(tmp_path, {"bare.py": "why"}) == []
