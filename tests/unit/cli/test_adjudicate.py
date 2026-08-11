"""Unit tests for the `adjudicate` CLI command: read-only LLM precision
layer over slice-1 `find_candidates` output.

`adjudicate` is a FOURTH read command, mirroring `query`'s wiring exactly:
`config.require_workspace` gate -> `config.read_config` -> a real
`OllamaClient(model=cfg.model)` built from the workspace's configured model
-> `resolution.find_candidates` -> `resolution.adjudication.adjudicate_candidates`.
It is read-only: no writes, no `--auto`, no confirmation gate, distinct from
the reserved `resolve`/`merge` verbs (slice 3). `--same-only` is a
DISPLAY-only filter -- the library always receives every candidate group
regardless of the flag.

Every test that needs a specific adjudication OUTCOME patches
`openkos.cli.main.adjudicate_candidates` directly (mirrors how `test_query.py`
patches `openkos.cli.main.answer`) -- zero network, zero real Ollama
process. Only the good-life-demo integration proof patches
`openkos.cli.main.OllamaClient` itself with a fake backend, so the test
never depends on whether the real example bundle happens to contain
candidates.
"""

import json
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.bundle import ledger as bundle_ledger
from openkos.cli import main
from openkos.cli.main import app
from openkos.llm.base import Message
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.model import okf
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
)
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from openkos.vcs import git as vcs_git
from tests.unit.cli.conftest import changed_paths, disable_local_exemption
from tests.unit.cli.conftest import snapshot_with_mtime as _snapshot
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _break_os_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `okf._walk_errors` to report exactly one directory-scan error,
    deterministically -- mirrors `tests/unit/model/test_okf.py`'s onerror
    monkeypatch pattern, without relying on real `chmod` bits."""
    original_walk = os.walk
    walk_error = OSError(13, "Permission denied", "locked")

    def fake_walk(
        top: str | os.PathLike[str],
        topdown: bool = True,
        onerror: Callable[[OSError], object] | None = None,
        followlinks: bool = False,
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        if onerror is not None:
            onerror(walk_error)
        yield from original_walk(top, topdown, onerror, followlinks)

    monkeypatch.setattr(os, "walk", fake_walk)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _write_doc(path: Path, *, doc_type: str = "Concept", title: str = "Stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n# {title}\n",
        encoding="utf-8",
    )


def _adjudicated(
    group: CandidateGroup,
    *,
    verdict: Verdict = Verdict.SAME,
    confidence: float = 0.9,
    rationale: str = "stub rationale",
) -> AdjudicatedCandidate:
    return AdjudicatedCandidate(
        candidate=group, verdict=verdict, confidence=confidence, rationale=rationale
    )


def test_format_verdict_tally_empty_returns_empty_string() -> None:
    """`_format_verdict_tally(0, 0, 0)` returns `""` -- signals "no line to
    print" (spec: Reusable Verdict-Tally Formatting Helper, zero counts)."""
    assert main._format_verdict_tally(0, 0, 0) == ""


def test_format_verdict_tally_same_and_different_only() -> None:
    """Zero UNCERTAIN omits the segment entirely (spec: Zero UNCERTAIN
    omits the segment)."""
    assert main._format_verdict_tally(2, 1, 0) == "adjudicated 3: 2 SAME, 1 DIFFERENT"


def test_format_verdict_tally_with_uncertain() -> None:
    """A nonzero UNCERTAIN count appends its segment (spec: Mixed results
    with UNCERTAIN present)."""
    assert (
        main._format_verdict_tally(2, 1, 1)
        == "adjudicated 4: 2 SAME, 1 DIFFERENT, 1 UNCERTAIN"
    )


def test_format_verdict_tally_all_same() -> None:
    """All-SAME results render `0 DIFFERENT` and no UNCERTAIN segment
    (spec: All-SAME results)."""
    assert main._format_verdict_tally(3, 0, 0) == "adjudicated 3: 3 SAME, 0 DIFFERENT"


def test_format_verdict_tally_all_different() -> None:
    """All-DIFFERENT results render `0 SAME` and no UNCERTAIN segment
    (spec: All-DIFFERENT results)."""
    assert main._format_verdict_tally(0, 3, 0) == "adjudicated 3: 0 SAME, 3 DIFFERENT"


def test_adjudicate_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, `adjudicate` refuses (exit 1), prints the shared
    `require_workspace` reason under an `adjudicate`-specific prefix, and
    never calls `adjudicate_candidates()` (spec: mirrors `query`/`lint`)."""
    monkeypatch.chdir(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos adjudicate: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_adjudicate_malformed_config_maps_to_exit_one_before_calling_adjudicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `openkos.yaml` (Phase-A `read_config` guard, mirrors
    `query`) is caught, printed as a friendly stderr message, exits 1 with no
    raw traceback, and `adjudicate_candidates()` is never reached."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith(
        "openkos adjudicate: failed while reading the workspace -- "
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_adjudicate_fresh_bundle_reports_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly initialized, empty bundle has zero candidate groups, so the
    real `adjudicate_candidates` never calls `llm.chat` -- a real
    `OllamaClient` is safe to construct here. Prints a clear "no candidates"
    line and exits 0."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "No candidates found." in result.stdout


def test_adjudicate_never_writes_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file under the workspace is created, modified, or deleted on any
    run -- byte contents AND `st_mtime_ns` both unchanged (spec: Verb renders
    verdicts with zero writes)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Ada Lovelace")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="ada lovelace")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["adjudicate"], catch_exceptions=True)

    # Regardless of exit code (a real Ollama may or may not be reachable in
    # this environment), the workspace bytes/mtimes must be identical --
    # `adjudicate` never writes, whether it succeeds or degrades.
    assert _snapshot(tmp_path) == before
    if result.exit_code == 0:
        assert "openkos adjudicate: workspace at" in result.stdout


def test_adjudicate_renders_grouped_verdict_and_rationale_without_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with one real HIGH candidate group renders its type, tier,
    trigger, and members (duplicates' render style) PLUS the group's verdict
    and rationale, and exits 0 (spec: Verb renders verdicts with zero
    writes). The confidence number is NOT displayed: a local model returns a
    flat, uncalibrated value, and a fake-precise "0.87" invites unwarranted
    trust (issue #138)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Café Society")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="cafe   society")
    captured: dict[str, object] = {}

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        captured["candidates"] = candidates
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    candidates[0],
                    verdict=Verdict.SAME,
                    confidence=0.87,
                    rationale="Same concept, different casing",
                )
            ]
        )

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "HIGH" in result.stdout
    assert "Concept" in result.stdout
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout
    assert "SAME" in result.stdout
    assert "0.87" not in result.stdout
    assert "confidence" not in result.stdout.lower()
    assert "Same concept, different casing" in result.stdout
    candidates = captured["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 1


def test_adjudicate_prints_leading_verdict_tally_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first stdout line for mixed SAME/DIFFERENT results with zero
    UNCERTAIN is the verdict tally, and the UNCERTAIN segment is absent
    (spec: Leading Verdict Tally Line Over Full Results, no UNCERTAIN)."""
    _init_workspace(tmp_path, monkeypatch)
    same_group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    same_group, verdict=Verdict.SAME, rationale="same rationale"
                ),
                _adjudicated(
                    same_group, verdict=Verdict.SAME, rationale="same rationale 2"
                ),
                _adjudicated(
                    different_group,
                    verdict=Verdict.DIFFERENT,
                    rationale="diff rationale",
                ),
            ]
        )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [same_group, different_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    tally = "adjudicated 3: 2 SAME, 1 DIFFERENT"
    assert tally in result.stdout
    assert "UNCERTAIN" not in result.stdout
    lines = result.stdout.splitlines()
    assert lines.index(tally) < next(
        i for i, line in enumerate(lines) if line.startswith("[HIGH]")
    )


def test_adjudicate_prints_uncertain_segment_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verdict tally line appends `, z UNCERTAIN` when the results
    include at least one UNCERTAIN verdict (spec: Mixed results with
    UNCERTAIN present)."""
    _init_workspace(tmp_path, monkeypatch)
    same_group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )
    uncertain_group = CandidateGroup(
        okf_type="Person", member_ids=("e", "f"), tier=Tier.LOW, trigger="stub-unc"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [same_group, different_group, uncertain_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    same_group, verdict=Verdict.SAME, rationale="same rationale"
                ),
                _adjudicated(
                    same_group, verdict=Verdict.SAME, rationale="same rationale 2"
                ),
                _adjudicated(
                    different_group,
                    verdict=Verdict.DIFFERENT,
                    rationale="diff rationale",
                ),
                _adjudicated(
                    uncertain_group,
                    verdict=Verdict.UNCERTAIN,
                    rationale="unc rationale",
                ),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "adjudicated 4: 2 SAME, 1 DIFFERENT, 1 UNCERTAIN" in result.stdout


def test_adjudicate_tally_counts_full_results_under_same_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--same-only` filters the printed detail but the tally still counts
    the FULL `results` set (spec: `--same-only` filters display, not the
    tally count)."""
    _init_workspace(tmp_path, monkeypatch)
    same_group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )
    uncertain_group = CandidateGroup(
        okf_type="Person", member_ids=("e", "f"), tier=Tier.LOW, trigger="stub-unc"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [same_group, different_group, uncertain_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    same_group, verdict=Verdict.SAME, rationale="same rationale"
                ),
                _adjudicated(
                    different_group,
                    verdict=Verdict.DIFFERENT,
                    rationale="diff rationale",
                ),
                _adjudicated(
                    uncertain_group,
                    verdict=Verdict.UNCERTAIN,
                    rationale="unc rationale",
                ),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--same-only"])

    assert result.exit_code == 0
    assert "adjudicated 3: 1 SAME, 1 DIFFERENT, 1 UNCERTAIN" in result.stdout
    assert "diff rationale" not in result.stdout
    assert "unc rationale" not in result.stdout


def test_adjudicate_prints_legend_once_before_the_results_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legend line explaining the verdict/confidence/rationale columns
    appears exactly once, before the first result's detail lines (spec:
    One-Time Verdict-Column Legend Line)."""
    _init_workspace(tmp_path, monkeypatch)
    group_a = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-a"
    )
    group_b = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-b"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group_a, group_b]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(group_a, verdict=Verdict.SAME, rationale="rationale a"),
                _adjudicated(
                    group_b, verdict=Verdict.DIFFERENT, rationale="rationale b"
                ),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    legend = (
        "Legend: [tier] type -- trigger, then verdict and rationale. "
        "The tier is the MATCH METHOD, not a strength ranking: "
        "HIGH = exact normalized key, LOW = near-match similarity score."
    )
    assert result.stdout.count(legend) == 1
    lines = result.stdout.splitlines()
    legend_idx = lines.index(legend)
    first_detail_idx = next(
        i for i, line in enumerate(lines) if line.startswith("[HIGH]")
    )
    assert legend_idx < first_detail_idx


def test_adjudicate_prints_next_hint_as_the_last_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last stdout line when at least one result is displayed is the
    `Next:` merge hint (spec: Trailing Next-Action Hint)."""
    _init_workspace(tmp_path, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[_adjudicated(group, verdict=Verdict.SAME, rationale="rationale")]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    lines = [line for line in result.stdout.splitlines() if line]
    assert lines[-1] == "Next: openkos merge <survivor> <absorbed>"


def test_adjudicate_no_results_suppresses_tally_legend_and_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `if not results` empty guard suppresses tally, legend, and
    `Next:` entirely (spec: Empty And Same-Only-Empty States Stay
    Single-Line, no candidates)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "No candidates found." in result.stdout
    assert "adjudicated" not in result.stdout
    assert "Legend:" not in result.stdout
    assert "Next:" not in result.stdout


def test_adjudicate_same_only_empty_suppresses_tally_legend_and_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `if not displayed` empty guard (`--same-only` filters every
    result out) also suppresses tally, legend, and `Next:` entirely (spec:
    Empty And Same-Only-Empty States Stay Single-Line, same-only empty)."""
    _init_workspace(tmp_path, monkeypatch)
    different_group = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [different_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    different_group,
                    verdict=Verdict.DIFFERENT,
                    rationale="diff rationale",
                )
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--same-only"])

    assert result.exit_code == 0
    assert "No SAME-verdict candidates to display" in result.stdout
    assert "adjudicated" not in result.stdout
    assert "Legend:" not in result.stdout
    assert "Next:" not in result.stdout


def test_adjudicate_same_only_hides_non_same_verdicts_from_output_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--same-only` hides DIFFERENT/UNCERTAIN groups from the printed
    report, but `adjudicate_candidates` still receives every group -- the
    filter never touches the library call (spec: `--same-only` is a
    display-only filter)."""
    _init_workspace(tmp_path, monkeypatch)
    same_group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )
    uncertain_group = CandidateGroup(
        okf_type="Person", member_ids=("e", "f"), tier=Tier.LOW, trigger="stub-unc"
    )
    fake_groups = [same_group, different_group, uncertain_group]
    captured: dict[str, object] = {}

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = fake_groups
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        captured["candidates"] = candidates
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    same_group, verdict=Verdict.SAME, rationale="same rationale"
                ),
                _adjudicated(
                    different_group,
                    verdict=Verdict.DIFFERENT,
                    rationale="diff rationale",
                ),
                _adjudicated(
                    uncertain_group,
                    verdict=Verdict.UNCERTAIN,
                    rationale="unc rationale",
                ),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--same-only"])

    assert result.exit_code == 0
    assert "same rationale" in result.stdout
    assert "diff rationale" not in result.stdout
    assert "unc rationale" not in result.stdout
    # The library call itself still received every group -- the flag is
    # display-only and never filters the data layer.
    candidates = captured["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 3


def test_adjudicate_same_only_with_no_same_verdicts_prints_empty_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--same-only` filtering every group out (no SAME verdicts at all)
    still exits 0 and prints a clear notice instead of silently rendering
    nothing -- the `displayed` list empty-after-filter branch."""
    _init_workspace(tmp_path, monkeypatch)
    different_group = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [different_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    different_group,
                    verdict=Verdict.DIFFERENT,
                    rationale="diff rationale",
                )
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--same-only"])

    assert result.exit_code == 0
    assert "diff rationale" not in result.stdout
    assert "No SAME-verdict candidates to display" in result.stdout


def test_adjudicate_builds_ollama_client_from_configured_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate` builds the `OllamaClient` from the model configured in
    `openkos.yaml`, not a hardcoded value (spec: mirrors `query`'s wiring)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def _recording_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        captured["kwargs"] = kwargs
        return AdjudicationBatch(results=[])

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _recording_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    llm = kwargs["llm"]
    assert isinstance(llm, OllamaClient)
    assert llm._model == configured_model
    assert kwargs["bundle_dir"] == tmp_path / "bundle"


def test_adjudicate_ollama_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate_candidates()` raising `OllamaUnavailable` is caught,
    printed as a friendly stderr message with `ollama serve` remediation,
    exits 1, and writes nothing (spec: Degrade-On-No-Model)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    def _raise_unavailable(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        raise OllamaUnavailable("Ollama not reachable at http://localhost:11434")

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _raise_unavailable)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos adjudicate: failed -- ")
    assert "Ollama not reachable" in result.stderr
    assert "ollama serve" in result.stderr
    assert result.stderr.rstrip("\n").endswith(
        "Or run `openkos doctor` to diagnose the environment."
    )
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_adjudicate_model_not_found_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate_candidates()` raising `OllamaModelNotFound` is caught,
    printed with the CONFIGURED model tag and `ollama pull <model>`
    remediation, exits 1, and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    before = _snapshot(tmp_path)

    def _raise_model_not_found(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        raise OllamaModelNotFound("Model not found (404): {}")

    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates", _raise_model_not_found
    )

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos adjudicate: failed -- ")
    assert "is not installed" in result.stderr
    assert f"ollama pull {configured_model}" in result.stderr
    assert "openkos doctor" not in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_adjudicate_generic_ollama_error_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate_candidates()` raising a plain `OllamaError` (neither
    `OllamaUnavailable` nor `OllamaModelNotFound`) is caught, printed as the
    generic friendly message with no cause-specific remediation, exits 1,
    and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    def _raise_generic(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        raise OllamaError("boom")

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _raise_generic)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == "openkos adjudicate: failed -- boom.\n"
    assert "ollama serve" not in result.stderr
    assert "ollama pull" not in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_adjudicate_specific_ollama_subclasses_do_not_fall_through_to_generic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both `OllamaUnavailable` and `OllamaModelNotFound` must reach their
    OWN handler, not the generic `OllamaError` fallback -- the direct RED
    test for the 3-tier handler ordering, mirrors `query`'s equivalent."""
    _init_workspace(tmp_path, monkeypatch)
    unavailable_message = "Ollama not reachable at http://localhost:11434"

    def _raise_unavailable(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        raise OllamaUnavailable(unavailable_message)

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _raise_unavailable)
    result = runner.invoke(app, ["adjudicate"])
    assert result.stderr != f"openkos adjudicate: failed -- {unavailable_message}.\n"
    assert "ollama serve" in result.stderr

    def _raise_model_not_found(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        raise OllamaModelNotFound("Model not found (404): {}")

    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates", _raise_model_not_found
    )
    result = runner.invoke(app, ["adjudicate"])
    assert "Model not found (404)" not in result.stderr
    assert "ollama pull" in result.stderr


# ---------------------------------------------------------------------------
# issue #441: a partial batch keeps its completed verdicts
# ---------------------------------------------------------------------------


def _two_group_partial_batch(
    monkeypatch: pytest.MonkeyPatch, failure: OllamaError
) -> CandidateGroup:
    """Wire `find_candidates_report` to two groups and
    `adjudicate_candidates` to a batch whose first group completed SAME and
    whose second raised `failure` -- the #441 mid-batch shape."""
    group_done = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-done"
    )
    group_failed = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-failed"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        return CandidateGroupReport(
            groups=(group_done, group_failed), produced=2, retained=2
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(group_done, verdict=Verdict.SAME, rationale="kept work")
            ],
            failure=failure,
            failed_index=2,
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)
    return group_done


def test_adjudicate_partial_batch_reports_completed_verdicts_then_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-batch `OllamaError` no longer discards completed verdicts
    (#441): the report renders them exactly as a complete run over that list
    would, THEN one stderr line reports the failure with completed-of-total
    counts, and the exit code stays the OllamaError-family 1."""
    _init_workspace(tmp_path, monkeypatch)
    _two_group_partial_batch(monkeypatch, OllamaError("boom"))

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "adjudicated 1: 1 SAME" in result.stdout
    assert "kept work" in result.stdout
    assert result.stderr == (
        "openkos adjudicate: failed after adjudicating 1 of 2 candidate "
        "group(s) -- boom.\n"
    )
    assert "Traceback" not in result.stderr


def test_adjudicate_partial_batch_unavailable_keeps_remediation_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `OllamaUnavailable` batch failure keeps its cause-specific
    remediation (`ollama serve` + doctor hint), gains the completed-of-total
    counts, and still exits 1 (#441)."""
    _init_workspace(tmp_path, monkeypatch)
    _two_group_partial_batch(
        monkeypatch, OllamaUnavailable("Ollama not reachable at http://localhost:11434")
    )

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 1
    assert "1 of 2" in result.stderr
    assert "ollama serve" in result.stderr
    assert result.stderr.rstrip("\n").endswith(
        "Or run `openkos doctor` to diagnose the environment."
    )
    assert "kept work" in result.stdout


def test_adjudicate_json_partial_batch_emits_completed_verdicts_then_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--json` over a partial batch serializes the completed verdicts
    BEFORE the failure epilogue (#441): stdout parses to exactly the one
    adjudicated group, stderr carries the single completed-of-total line,
    and the exit code stays the OllamaError-family 1.

    The envelope declares the truncation IN BAND (#468 item 5): `partial`
    is true and `adjudicated` falls short of `total`, so a redirected
    `> out.json` still carries the fact that work is missing after the exit
    code is gone.

    This is the RETURNED-batch path, and it is what distinguishes a partial
    payload from the pre-#441 empty stdout:
    `test_adjudicate_json_ollama_unavailable_still_errors_on_stderr_with_no_json`
    covers the RAISE path, where nothing was adjudicated and stdout stays
    empty."""
    _init_workspace(tmp_path, monkeypatch)
    _two_group_partial_batch(monkeypatch, OllamaError("boom"))

    result = runner.invoke(app, ["adjudicate", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "partial": True,
        "adjudicated": 1,
        "total": 2,
        "results": [
            {
                "member_ids": ["a", "b"],
                "okf_type": "Concept",
                "tier": "HIGH",
                "verdict": "SAME",
                "rationale": "kept work",
            }
        ],
    }
    assert result.stderr == (
        "openkos adjudicate: failed after adjudicating 1 of 2 candidate "
        "group(s) -- boom.\n"
    )
    assert "Traceback" not in result.stderr


def test_adjudicate_apply_partial_batch_walks_completed_pairs_before_failing(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--apply` on a partial batch still walks every completed SAME pair
    through the interactive merge (the work was already paid for), and only
    THEN reports the batch failure on stderr and exits 1 (#441)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[_adjudicated(group, verdict=Verdict.SAME, rationale="same")],
            failure=OllamaError("boom"),
            failed_index=2,
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(
            groups=(group, _group_cd()), produced=2, retained=2
        ),
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\n")

    assert result.exit_code == 1
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert "applied 1" in result.stdout
    assert "1 of 2" in result.stderr
    assert "boom" in result.stderr


def _group_cd() -> CandidateGroup:
    """The never-adjudicated second group of the #441 partial-batch shape."""
    return CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/c", "concepts/d"),
        tier=Tier.LOW,
        trigger="stub-failed",
    )


# ---------------------------------------------------------------------------
# issue #468 item 4: `--apply-same` counts only the COMPLETED groups
# ---------------------------------------------------------------------------


def test_adjudicate_apply_same_partial_batch_confirms_completed_count_only(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--apply-same` over a partial batch previews, counts and merges only
    the COMPLETED groups: two groups were queued, one adjudicated SAME, and
    the gate asks for -- and accepts -- `1`, not `2`. The merge really
    commits, and only afterwards does the batch failure surface on stderr
    and set the exit code to 1 despite that successful write.

    This pins CURRENT behavior for issue #468 item 4: at confirm time the
    operator is told `Total: 1` and nothing else, so nothing in the gate
    discloses that a second group was queued and failed. Whether that
    disclosure should be added is still an open decision."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Concept D")
    _seed_commit(
        tmp_path,
        [
            "bundle/concepts/a.md",
            "bundle/concepts/b.md",
            "bundle/concepts/c.md",
            "bundle/concepts/d.md",
        ],
    )
    group_done = _two_member_group(("concepts/a", "concepts/b"), trigger="stub-done")
    group_failed = _two_member_group(
        ("concepts/c", "concepts/d"), trigger="stub-failed", tier=Tier.LOW
    )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[_adjudicated(group_done, verdict=Verdict.SAME, rationale="same")],
            failure=OllamaError("boom"),
            failed_index=2,
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(
            groups=(group_done, group_failed), produced=2, retained=2
        ),
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)
    before_commits = _commit_count(tmp_path)

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--confirm-count", "1"])

    assert result.exit_code == 1
    # The count printed and required is the COMPLETED eligible count, never
    # the queued 2 -- and `--confirm-count 1` therefore proceeds.
    assert "Total: 1" in result.stdout
    assert "Total: 2" not in result.stdout
    assert "aborted -- confirmation count" not in result.stderr
    assert "applied 1 of 1 previewed" in result.stdout
    # The write really happened, commit and all, before the failure line.
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "d.md").exists()
    assert _commit_count(tmp_path) == before_commits + 1
    assert "failed after adjudicating 1 of 2 candidate group(s)" in result.stderr


def test_adjudicate_no_auto_flag_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate` is read-only: no `--auto` or confirmation flag exists,
    unlike `ingest`/`forget` (spec: Read-Only `adjudicate` CLI verb)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["adjudicate", "--auto"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


# ---------------------------------------------------------------------------
# `--include-deprecated` (status-aware-retrieval Phase 4)
# ---------------------------------------------------------------------------


def test_adjudicate_include_deprecated_flag_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-deprecated` is forwarded unchanged as
    `find_candidates(..., include_deprecated=True)` (`adjudicate` uses
    candidates, per the locked scope decision; spec: `--include-deprecated`
    Escape Flag)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_find_candidates(
        bundle_dir: Path, **kwargs: object
    ) -> CandidateGroupReport:
        captured["kwargs"] = kwargs
        return CandidateGroupReport()

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _recording_find_candidates
    )

    result = runner.invoke(app, ["adjudicate", "--include-deprecated"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_deprecated"] is True


def test_adjudicate_omitted_include_deprecated_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--include-deprecated` forwards the safe default
    `include_deprecated=False` (spec: Deprecated Concepts Excluded By
    Default)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_find_candidates(
        bundle_dir: Path, **kwargs: object
    ) -> CandidateGroupReport:
        captured["kwargs"] = kwargs
        return CandidateGroupReport()

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _recording_find_candidates
    )

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_deprecated"] is False


def test_adjudicate_default_excludes_a_deprecated_candidate_group_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real (unmocked) `find_candidates` excludes a deprecated group member
    by default, so the only candidate group is dropped before
    `adjudicate_candidates` ever sees it -- `adjudicate` renders "No
    candidates found." (mirrors
    `test_duplicates_default_excludes_a_deprecated_group_member`)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: STOICISM\nstatus: deprecated\n---\n# STOICISM\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "No candidates found." in result.stdout


def test_adjudicate_include_deprecated_restores_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-deprecated` restores the deprecated group member, so real
    (unmocked) `find_candidates` returns the HIGH group and `adjudicate`
    renders it -- `adjudicate_candidates` is mocked here ONLY to avoid a
    real Ollama call, mirroring how other tests in this file mock it."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: STOICISM\nstatus: deprecated\n---\n# STOICISM\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def _recording_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        captured["candidates"] = candidates
        return AdjudicationBatch(
            results=[
                _adjudicated(group, verdict=Verdict.SAME, rationale="restored group")
                for group in candidates
            ]
        )

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _recording_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--include-deprecated"])

    assert result.exit_code == 0
    assert "restored group" in result.stdout
    candidates = captured["candidates"]
    assert isinstance(candidates, list)
    assert len(candidates) == 1


# ---------------------------------------------------------------------------
# `--include-confidential` (sensitivity-fail-closed-filter S3a)
# ---------------------------------------------------------------------------


def test_adjudicate_include_confidential_flag_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` is forwarded unchanged as
    `adjudicate_candidates(..., include_confidential=True)` -- `adjudicate`
    threads the flag into `adjudicate_candidates` (member-level filtering),
    not `find_candidates` (spec: `--include-confidential` Escape Flag)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="STOICISM")
    captured: dict[str, object] = {}

    def _recording_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        captured["kwargs"] = kwargs
        return AdjudicationBatch(results=[_adjudicated(group) for group in candidates])

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _recording_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--include-confidential"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_confidential"] is True


def test_adjudicate_omitted_include_confidential_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--include-confidential` forwards the safe default
    `include_confidential=False` (spec: Confidential Excluded By Default)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="STOICISM")
    captured: dict[str, object] = {}

    def _recording_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        captured["kwargs"] = kwargs
        return AdjudicationBatch(results=[_adjudicated(group) for group in candidates])

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _recording_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_confidential"] is False


# ---------------------------------------------------------------------------
# `adjudicate --apply` (interactive merge path, issue #137 Slice 2b-ii)
# ---------------------------------------------------------------------------


def _init_apply_workspace(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_init_workspace` plus a real, isolated git identity (mirrors
    `test_main_autocommit.py::_init_workspace`) -- `--apply` auto-commits
    each accepted merge, so tests exercising the accept path need an actual
    git identity, never the host machine's real `~/.gitconfig`."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _commit_count(root: Path) -> int:
    result = vcs_git._run(["git", "log", "--format=%H"], cwd=root)
    return len([line for line in result.stdout.splitlines() if line])


def _last_commit_subject(root: Path) -> str:
    result = vcs_git._run(["git", "log", "-1", "--format=%s"], cwd=root)
    return result.stdout.strip()


def _seed_commit(root: Path, rel_paths: list[str], message: str = "seed") -> None:
    """Pre-track paths via `commit_paths` -- test setup only, mirrors
    `test_main_autocommit.py::_seed_commit`: `git add -- <deleted-untracked-
    path>` fails, so a merge's absorbed-file removal must already be
    tracked before `_autocommit` can stage its deletion."""
    vcs_git.commit_paths(root, rel_paths, message)


def test_adjudicate_apply_and_json_rejected_with_exit_code_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate --apply --json` is rejected before any work: a clear
    stderr message, exit code 2, and `adjudicate_candidates` is never called
    (spec: `--apply` Rejects `--json`)."""
    _init_workspace(tmp_path, monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["adjudicate", "--apply", "--json"])

    assert result.exit_code == 2
    assert "No such option" not in result.stderr
    assert "openkos adjudicate" in result.stderr
    assert "--apply" in result.stderr
    assert "--json" in result.stderr
    assert calls == []


def test_adjudicate_apply_offers_a_same_two_member_group(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SAME-verdict group with exactly 2 members is prompted for merge
    (spec: SAME 2-member group is offered)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[_adjudicated(group, verdict=Verdict.SAME, rationale="same")]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="n\n")

    assert "Merge concepts/b into concepts/a? [y/N]" in result.stdout


def test_adjudicate_apply_never_prompts_different_or_uncertain_groups(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DIFFERENT and UNCERTAIN groups are never offered for merge (spec:
    DIFFERENT group is never offered)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    different_group = CandidateGroup(
        okf_type="Concept", member_ids=("x", "y"), tier=Tier.LOW, trigger="stub-diff"
    )
    uncertain_group = CandidateGroup(
        okf_type="Concept", member_ids=("p", "q"), tier=Tier.LOW, trigger="stub-unc"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [different_group, uncertain_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    different_group, verdict=Verdict.DIFFERENT, rationale="diff"
                ),
                _adjudicated(
                    uncertain_group, verdict=Verdict.UNCERTAIN, rationale="unc"
                ),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"])

    assert "[y/N/skip]" not in result.stdout
    assert result.exit_code == 0


def test_adjudicate_apply_same_group_with_three_members_is_skipped_not_prompted(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SAME-verdict group with 3 members prints `skipped (N>2, merge
    manually)` and is never prompted (spec: SAME group with >2 members is
    skipped, not prompted)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b", "c"), tier=Tier.HIGH, trigger="stub"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[_adjudicated(group, verdict=Verdict.SAME, rationale="same")]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"])

    assert "skipped (N>2, merge manually)" in result.stdout
    assert "run in order (each reversible via unmerge):" in result.stdout
    assert "openkos merge a b" in result.stdout
    assert "openkos merge a c" in result.stdout
    assert "[y/N/skip]" not in result.stdout
    assert result.exit_code == 0


def test_adjudicate_apply_n_gt2_skip_prints_pairwise_merge_commands_in_order(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The N>2 skip line is followed by the EXACT pairwise merge commands --
    survivor is the first member (member_ids are sorted ascending, matching
    the 2-member `survivor_id, absorbed_id = group.member_ids` convention),
    one `openkos merge <survivor> <absorbed>` line per remaining member, in
    member order -- so the operator can run the manual merges without
    reconstructing them (issue #191). Counters and the summary line stay
    byte-identical to the pre-#191 output."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b", "concepts/c"),
        tier=Tier.HIGH,
        trigger="stub",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[_adjudicated(group, verdict=Verdict.SAME, rationale="same")]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"])

    assert result.exit_code == 0
    skip_at = result.stdout.index("skipped (N>2, merge manually)")
    guidance_at = result.stdout.index("run in order (each reversible via unmerge):")
    first_at = result.stdout.index("openkos merge concepts/a concepts/b")
    second_at = result.stdout.index("openkos merge concepts/a concepts/c")
    assert skip_at < guidance_at < first_at < second_at
    # Exactly the two pairwise commands -- never a survivor-less extra pair.
    assert result.stdout.count("openkos merge ") == 2
    assert "openkos merge concepts/b concepts/c" not in result.stdout
    # Summary counters are unchanged by the new command lines.
    assert "applied 0, skipped 1" in result.stdout
    assert "N>2: 1" in result.stdout


def test_adjudicate_apply_cross_type_n_gt2_group_is_skipped_with_joined_label(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 3-member CROSS-TYPE exact-title group (Concept + Decision +
    Entity sharing one normalized title, #437's cross-type bucketing) is
    found by the REAL `find_candidates_report` -- not a hand-built stub --
    and `--apply` routes it through `_echo_n_gt2_skip` (issue #480): the
    skip line renders the group's sorted, `+`-joined `okf_type` label,
    the pairwise merge guidance follows, and the group is never merged --
    zero writes (bundle bytes AND mtimes unchanged) and zero commits.
    Only `adjudicate_candidates` is patched, with a recorder returning
    SAME for exactly the groups it receives, per the module docstring's
    convention -- so the captured input doubles as the proof that
    `find_candidates` produced the cross-type group."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    for sub, doc_type in [
        ("concepts", "Concept"),
        ("entities", "Entity"),
        ("decisions", "Decision"),
    ]:
        _write_doc(
            tmp_path / "bundle" / sub / "stoicism.md",
            doc_type=doc_type,
            title="Stoicism",
        )
    captured: list[CandidateGroup] = []

    def _recording_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        captured.extend(candidates)
        return AdjudicationBatch(
            results=[
                _adjudicated(group, verdict=Verdict.SAME, rationale="same")
                for group in candidates
            ]
        )

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _recording_adjudicate)
    before = _snapshot(tmp_path / "bundle")
    commits_before = _commit_count(tmp_path)

    result = runner.invoke(app, ["adjudicate", "--apply"])

    assert result.exit_code == 0
    # The real `find_candidates` found the cross-type triple as ONE group.
    assert [(group.okf_type, group.member_ids) for group in captured] == [
        (
            "Concept+Decision+Entity",
            ("concepts/stoicism", "decisions/stoicism", "entities/stoicism"),
        )
    ]
    # `_echo_n_gt2_skip` renders the joined label on the skip line, then
    # the pairwise guidance, and never prompts.
    assert (
        "[Concept+Decision+Entity] ('concepts/stoicism', 'decisions/stoicism', "
        "'entities/stoicism'): skipped (N>2, merge manually)" in result.stdout
    )
    assert "run in order (each reversible via unmerge):" in result.stdout
    assert "openkos merge concepts/stoicism decisions/stoicism" in result.stdout
    assert "openkos merge concepts/stoicism entities/stoicism" in result.stdout
    assert "[y/N]" not in result.stdout
    assert "applied 0, skipped 1 (N>2: 1" in result.stdout
    # Never merged: zero writes, zero commits.
    assert _snapshot(tmp_path / "bundle") == before
    assert _commit_count(tmp_path) == commits_before


def test_adjudicate_apply_overlapping_groups_second_reports_already_merged(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two overlapping SAME 2-member groups sharing a member: the first is
    accepted (`y`), the second prints `skipped (member unresolved --
    already merged or missing)` and the run does not crash (spec: Later
    group referencing an already-absorbed member is skipped)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    group1 = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub1",
    )
    group2 = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/b", "concepts/c"),
        tier=Tier.HIGH,
        trigger="stub2",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group1, group2]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(group1, verdict=Verdict.SAME, rationale="same1"),
                _adjudicated(group2, verdict=Verdict.SAME, rationale="same2"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\n")

    assert result.exit_code == 0
    assert "skipped (member unresolved -- already merged or missing)" in result.stdout
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "c.md").exists()


def _seed_one_same_group(
    tmp_path: Path,
) -> tuple[
    CandidateGroup,
    Callable[..., CandidateGroupReport],
    Callable[..., AdjudicationBatch],
]:
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[_adjudicated(group, verdict=Verdict.SAME, rationale="same")]
        )

    return group, _fake_find_candidates, _fake_adjudicate


def test_adjudicate_apply_preview_precedes_the_exact_prompt_text(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `prepare_merge` preview is printed before the exact prompt text
    (spec: Preview precedes the exact prompt text)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="n\n")

    prompt_line = "Merge concepts/b into concepts/a? [y/N]"
    assert prompt_line in result.stdout
    lines = result.stdout.splitlines()
    prompt_idx = next(i for i, line in enumerate(lines) if prompt_line in line)
    assert prompt_idx > 0
    preview_text = "\n".join(lines[:prompt_idx])
    assert "concepts/b" in preview_text
    assert "concepts/a" in preview_text


def test_adjudicate_apply_accepts_merge_updates_filesystem_and_ledger(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`input="y\\n"` applies the merge: survivor file updated, absorbed file
    removed, index/log updated, `merged_from` ledger entry written (spec:
    `y` applies the merge / Applied merge updates filesystem and ledger)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\n")

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    survivor_text = (tmp_path / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )
    assert "merged_from" not in survivor_text
    entries = bundle_ledger.read_entries("concepts/a", tmp_path / "bundle")
    assert len(entries) == 1
    assert entries[0].absorbed_id == "concepts/b"
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "concepts/b" not in index_text


def test_adjudicate_apply_merge_unions_provenance_from_both_sources(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for #379 criterion 1: two `Concept` documents that
    share an exact normalized title (the HIGH tier's trigger), each with a
    single-element `provenance:` naming a DIFFERENT source, flow end to end
    through the real (unmocked) `find_candidates` -> `adjudicate_candidates`
    -> merge chain -- only the `LLMBackend` is a stub returning `SAME`, per
    the module docstring's convention (never a real Ollama call). The
    assertion that matters is the UNION: the surviving document's
    `provenance` must cite BOTH source files, not merely that a merge
    happened (a test asserting only the latter would pass while criterion 1
    silently regressed)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeOllamaClient)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "a.md").write_text(
        "---\n"
        "type: Concept\n"
        "title: Stoicism\n"
        "sensitivity: private\n"
        "provenance:\n"
        "  - sources/notes-on-the-enchiridion\n"
        "---\n"
        "# Stoicism\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\n"
        "type: Concept\n"
        "title: Stoicism\n"
        "sensitivity: private\n"
        "provenance:\n"
        "  - sources/call-with-maria\n"
        "---\n"
        "# Stoicism\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\n")

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    survivor_text = (tmp_path / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )
    metadata, _ = okf.load_frontmatter(survivor_text)
    assert metadata["provenance"] == [
        "sources/notes-on-the-enchiridion",
        "sources/call-with-maria",
    ]


@pytest.mark.parametrize("declining_input", ["\n", "n\n"])
def test_adjudicate_apply_declining_inputs_do_not_merge_and_continue(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    declining_input: str,
) -> None:
    """Empty input (the documented `N` default) and `n` decline the merge,
    write nothing, and the run continues (issue #483, the #398 contract:
    only `n`/`no`/Enter decline -- `skip` is no longer a token)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input=declining_input)

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "a.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert "declined" in result.stdout


@pytest.mark.parametrize("unrecognized", ["t", "skip"])
def test_adjudicate_apply_unrecognized_answer_reprompts_then_applies(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    unrecognized: str,
) -> None:
    """An unrecognized answer (`t` -- #398's typo evidence -- or the
    formerly advertised `skip`) is re-asked with a notice naming the
    accepted tokens, never silently counted as a decline; the subsequent
    `y` applies the merge (issue #483, the #398 contract)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input=f"{unrecognized}\ny\n")

    assert result.exit_code == 0
    assert (
        f"Unrecognized answer '{unrecognized}' -- expected y or n "
        "(Enter = N). Asking again." in result.stdout
    )
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert "applied 1" in result.stdout


def test_adjudicate_apply_piped_reprompt_shifts_every_later_answer_by_one(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PINS current multi-group piped-stdin semantics, and DOCUMENTS them
    as the scripting hazard of issue #489: an unrecognized answer makes
    `curate._confirm`'s re-prompt consume the NEXT stdin line, so EVERY
    later piped answer shifts by one position. Two SAME 2-member groups
    with piped input `t\\nn\\ny\\n`: the first group's `t` is unrecognized
    and re-prompted, the re-prompt consumes the `n` a scripter would have
    aimed at the SECOND group (first group declined), and the trailing
    `y` lands on the second group (applied). A script that pipes
    one-answer-per-group input therefore silently mis-targets every
    answer after a single typo -- which is exactly why `--apply-same
    --confirm-count <N>` is the documented unattended path (#489); this
    test changes no behavior."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Concept D")
    first_group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub1",
    )
    second_group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/c", "concepts/d"),
        tier=Tier.HIGH,
        trigger="stub2",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [first_group, second_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(first_group, verdict=Verdict.SAME, rationale="r1"),
                _adjudicated(second_group, verdict=Verdict.SAME, rationale="r2"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="t\nn\ny\n")

    assert result.exit_code == 0
    assert (
        "Unrecognized answer 't' -- expected y or n (Enter = N). Asking again."
        in result.stdout
    )
    # Line 2 (`n`) answered the FIRST group's re-prompt, not the second
    # group: the first pair is declined and stays on disk.
    assert (tmp_path / "bundle" / "concepts" / "a.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert "  declined: concepts/b -> concepts/a" in result.stdout
    # Line 3 (`y`) shifted onto the SECOND group and applied it.
    assert not (tmp_path / "bundle" / "concepts" / "d.md").exists()
    assert "applied 1" in result.stdout
    assert "  declined: concepts/d -> concepts/c" not in result.stdout


def test_adjudicate_apply_prompt_advertises_y_n_without_skip(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt advertises exactly the two implemented outcomes -- `[y/N]`
    -- and the walk's output never advertises a `skip` token anywhere
    (issue #483: the prompt shape #398 removed from curate)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="n\n")

    assert result.exit_code == 0
    assert "Merge concepts/b into concepts/a? [y/N]" in result.stdout
    assert "[y/N/skip]" not in result.stdout
    assert "/skip" not in result.stdout


def test_adjudicate_apply_names_declined_merges_in_the_summary(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each operator-declined merge is named `absorbed -> survivor` after
    the summary line (issue #483, mirroring #398's decline listing), so a
    typo-free decline set is revisitable without re-running the walk."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Concept D")
    applied_group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub1",
    )
    declined_group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/c", "concepts/d"),
        tier=Tier.HIGH,
        trigger="stub2",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [applied_group, declined_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(applied_group, verdict=Verdict.SAME, rationale="r1"),
                _adjudicated(declined_group, verdict=Verdict.SAME, rationale="r2"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\nn\n")

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    summary_idx = next(
        i for i, line in enumerate(lines) if "openkos adjudicate --apply:" in line
    )
    assert "  declined: concepts/d -> concepts/c" in lines[summary_idx + 1 :]
    # The applied pair is never listed as declined.
    assert "  declined: concepts/b -> concepts/a" not in result.stdout


def test_adjudicate_apply_all_applied_names_no_declined_merges(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run where every offered merge was accepted prints no per-item
    `declined:` naming line -- the listing appears only when the operator
    actually declined something (issue #483)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\n")

    assert result.exit_code == 0
    assert "applied 1" in result.stdout
    assert "  declined:" not in result.stdout


def test_adjudicate_apply_two_accepted_merges_produce_two_separate_commits(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two accepted merges auto-commit independently -- two separate
    commits, not one commit for the whole run (spec: Two applied merges
    produce two commits)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Concept D")
    _seed_commit(
        tmp_path,
        [
            "bundle/concepts/a.md",
            "bundle/concepts/b.md",
            "bundle/concepts/c.md",
            "bundle/concepts/d.md",
        ],
    )
    group1 = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub1",
    )
    group2 = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/c", "concepts/d"),
        tier=Tier.HIGH,
        trigger="stub2",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group1, group2]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(group1, verdict=Verdict.SAME, rationale="same1"),
                _adjudicated(group2, verdict=Verdict.SAME, rationale="same2"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)
    before_commits = _commit_count(tmp_path)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\ny\n")

    assert result.exit_code == 0
    assert _commit_count(tmp_path) == before_commits + 2
    assert "concepts/d" in _last_commit_subject(tmp_path)


def test_adjudicate_apply_then_unmerge_restores_the_absorbed_member(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applying a real merge via `adjudicate --apply`, then running
    `unmerge` against the survivor, restores the absorbed member (spec:
    Applied merge is unmerge-reversible)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\n")
    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()

    unmerge_result = runner.invoke(
        app, ["unmerge", "concepts/a", "concepts/b", "--auto"]
    )

    assert unmerge_result.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()


def test_adjudicate_apply_mid_run_merge_core_failure_stops_the_run(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `merge_core` failure for the first accepted merge stops the run
    before the second group, exits non-zero with a clear stderr message, and
    the run does not crash with a raw traceback (spec: `merge_core` failure
    halts remaining groups)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Concept D")
    group1 = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub1",
    )
    group2 = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/c", "concepts/d"),
        tier=Tier.HIGH,
        trigger="stub2",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group1, group2]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(group1, verdict=Verdict.SAME, rationale="same1"),
                _adjudicated(group2, verdict=Verdict.SAME, rationale="same2"),
            ]
        )

    def _raise_merge_core(*args: object, **kwargs: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)
    monkeypatch.setattr("openkos.cli.main.merge_core", _raise_merge_core)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\ny\n")

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert result.stderr.strip() != ""
    assert (tmp_path / "bundle" / "concepts" / "c.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "d.md").exists()


def test_adjudicate_apply_prepare_merge_failure_stops_the_run(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `prepare_merge` (Phase A, read-only) failure halts the run before any
    prompt or write, exits non-zero with a clear stderr message and no raw
    traceback, and destroys nothing -- both concept files remain (spec:
    mid-run failure stops remaining groups; `prepare_merge` writes nothing so a
    failure there cannot corrupt on-disk state)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    group1 = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub1",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group1]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[_adjudicated(group1, verdict=Verdict.SAME, rationale="same1")]
        )

    def _raise_prepare_merge(*args: object, **kwargs: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)
    monkeypatch.setattr("openkos.cli.main.prepare_merge", _raise_prepare_merge)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\n")

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert result.stderr.strip() != ""
    assert (tmp_path / "bundle" / "concepts" / "a.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()


# ---------------------------------------------------------------------------
# `adjudicate --apply` TOCTOU drift guard (issue #346)
# ---------------------------------------------------------------------------


def test_adjudicate_apply_toctou_drift_exits_three_nothing_written(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An edit landing on the survivor file INSIDE the post-confirm window
    -- after `_prepare_one_merge`'s snapshot, while the `[y/N]` prompt
    waited -- is refused, not clobbered (issue #346, the #306/#313/#319
    drift-guard contract): exit 3, `refusing to write --` on stderr naming
    the actual command, the hand-edit intact, and nothing else on disk
    touched.

    The TOCTOU window under test opens AFTER `_prepare_one_merge`'s
    snapshot and closes at `_merge_drift_targets` -- in the `--apply` walk
    that window is exactly the per-pair `[y/N]` prompt (`curate._confirm`
    since #483, still built on `typer.prompt`), so the concurrent edit is
    injected from a `typer.prompt` stub that edits the survivor and then
    accepts (mirrors `test_curate.py`'s Identity TOCTOU test, the
    established pattern for this window)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    survivor_path = tmp_path / "bundle" / "concepts" / "a.md"
    concurrent = "hand-edited while the prompt waited\n"
    before = _snapshot(tmp_path)

    def _prompt_edits_then_accepts(*args: object, **kwargs: object) -> str:
        survivor_path.write_text(concurrent, encoding="utf-8")
        return "y"

    monkeypatch.setattr("typer.prompt", _prompt_edits_then_accepts)

    result = runner.invoke(app, ["adjudicate", "--apply"])

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "openkos adjudicate --apply: refusing to write --" in result.stderr
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/concepts/a.md")}
    assert survivor_path.read_text(encoding="utf-8") == concurrent


def test_adjudicate_apply_clean_accepted_pair_still_merges_after_drift_guard(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression pin for issue #346: the drift guard is a NO-OP on a clean
    accepted pair -- when nothing edits the targets during the prompt, the
    guarded walk merges exactly as before (exit 0, absorbed file removed,
    one applied in the summary), so adding the guard changed only the
    drifted case."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\n")

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert "applied 1" in result.stdout


def test_adjudicate_apply_summary_reflects_applied_and_skipped_counts(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mixed run (one applied, one N>2 skip, one declined) prints
    `applied 1, skipped 2` with the breakdown of skip reasons (spec:
    Summary reflects applied and skipped counts)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Concept D")
    applied_group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub1",
    )
    n_gt2_group = CandidateGroup(
        okf_type="Concept", member_ids=("x", "y", "z"), tier=Tier.HIGH, trigger="stub2"
    )
    declined_group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/c", "concepts/d"),
        tier=Tier.HIGH,
        trigger="stub3",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [applied_group, n_gt2_group, declined_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(applied_group, verdict=Verdict.SAME, rationale="r1"),
                _adjudicated(n_gt2_group, verdict=Verdict.SAME, rationale="r2"),
                _adjudicated(declined_group, verdict=Verdict.SAME, rationale="r3"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\nn\n")

    assert result.exit_code == 0
    assert "applied 1, skipped 2" in result.stdout
    assert "N>2: 1" in result.stdout
    assert "already-merged: 0" in result.stdout
    assert "declined: 1" in result.stdout


def test_adjudicate_apply_no_eligible_groups_prints_nothing_to_apply(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No SAME 2-member groups exist: a clear "nothing to apply" message,
    exit 0, and no filesystem writes (spec: No eligible groups, nothing
    applied)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    different_group = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [different_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    different_group, verdict=Verdict.DIFFERENT, rationale="diff"
                )
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["adjudicate", "--apply"])

    assert result.exit_code == 0
    assert "nothing to apply" in result.stdout.lower()
    assert _snapshot(tmp_path) == before


def test_adjudicate_apply_same_only_is_a_no_op_composition(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`adjudicate --apply --same-only` produces identical eligibility,
    prompts, and outcomes as `adjudicate --apply` alone (spec: `--apply
    --same-only` behaves like `--apply`)."""
    plain_root = tmp_path_factory.mktemp("apply-plain")
    _init_apply_workspace(plain_root, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(plain_root)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    plain_result = runner.invoke(app, ["adjudicate", "--apply"], input="n\n")

    same_only_root = tmp_path_factory.mktemp("apply-same-only")
    _init_apply_workspace(same_only_root, tmp_path_factory, monkeypatch)
    _, fake_find2, fake_adjudicate2 = _seed_one_same_group(same_only_root)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find2)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate2)

    same_only_result = runner.invoke(
        app, ["adjudicate", "--apply", "--same-only"], input="n\n"
    )

    def _strip_workspace_line(stdout: str) -> str:
        return "\n".join(
            line for line in stdout.splitlines() if "workspace at" not in line
        )

    assert plain_result.exit_code == same_only_result.exit_code == 0
    assert _strip_workspace_line(plain_result.stdout) == _strip_workspace_line(
        same_only_result.stdout
    )


# ---------------------------------------------------------------------------
# `adjudicate --apply-same` (guarded batch merge, issue #137 closing slice)
# ---------------------------------------------------------------------------


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke`
    call (mirrors `test_forget.py::_simulate_tty`)."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def test_adjudicate_apply_same_and_apply_rejected_with_exit_code_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate --apply-same --apply` is rejected before any work: a
    clear stderr message, exit code 2, and `adjudicate_candidates` is never
    called (spec: `--apply-same` Mutual Exclusion With `--apply` And
    `--json`)."""
    _init_workspace(tmp_path, monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--apply"])

    assert result.exit_code == 2
    assert "No such option" not in result.stderr
    assert "openkos adjudicate" in result.stderr
    assert "--apply-same" in result.stderr
    assert calls == []


def test_adjudicate_apply_same_and_json_rejected_with_exit_code_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate --apply-same --json` is rejected before any work: a
    clear stderr message, exit code 2, and `adjudicate_candidates` is never
    called (spec: `--apply-same` Mutual Exclusion With `--apply` And
    `--json`)."""
    _init_workspace(tmp_path, monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--json"])

    assert result.exit_code == 2
    assert "No such option" not in result.stderr
    assert "openkos adjudicate" in result.stderr
    assert "--apply-same" in result.stderr
    assert calls == []


def _two_member_group(
    ids: tuple[str, str], *, trigger: str = "stub", tier: Tier = Tier.HIGH
) -> CandidateGroup:
    return CandidateGroup(
        okf_type="Concept", member_ids=ids, tier=tier, trigger=trigger
    )


def test_adjudicate_apply_same_eligibility_filters_to_same_two_member_groups(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only SAME 2-member groups are previewed: SAME >2-member is skipped
    and reported, DIFFERENT/UNCERTAIN never appear in the batch (spec:
    `--apply-same` Eligibility Filter)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    same_pair = _two_member_group(("concepts/a", "concepts/b"), trigger="stub-same")
    same_triple = CandidateGroup(
        okf_type="Concept", member_ids=("x", "y", "z"), tier=Tier.HIGH, trigger="stub3"
    )
    different_group = _two_member_group(("p", "q"), trigger="stub-diff")
    uncertain_group = _two_member_group(("r", "s"), trigger="stub-unc")

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [same_pair, same_triple, different_group, uncertain_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(same_pair, verdict=Verdict.SAME, rationale="same"),
                _adjudicated(
                    same_triple, verdict=Verdict.SAME, rationale="same-triple"
                ),
                _adjudicated(
                    different_group, verdict=Verdict.DIFFERENT, rationale="diff"
                ),
                _adjudicated(
                    uncertain_group, verdict=Verdict.UNCERTAIN, rationale="unc"
                ),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--confirm-count", "1"])

    assert result.exit_code == 0
    assert "concepts/b" in result.stdout
    assert "concepts/a" in result.stdout
    assert "skipped (N>2, merge manually)" in result.stdout
    assert "run in order (each reversible via unmerge):" in result.stdout
    assert "openkos merge x y" in result.stdout
    assert "openkos merge x z" in result.stdout
    assert "Total: 1" in result.stdout


def test_adjudicate_apply_same_n_gt2_skip_prints_pairwise_merge_commands_in_order(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--apply-same`'s N>2 skip prints the SAME pairwise merge commands as
    `--apply` -- shared helper, no divergent strings: survivor is the first
    (sorted-ascending) member, one `openkos merge <survivor> <absorbed>`
    line per remaining member, in member order (issue #191). Counters and
    the zero-eligible summary stay byte-identical to the pre-#191 output."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b", "concepts/c"),
        tier=Tier.HIGH,
        trigger="stub",
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[_adjudicated(group, verdict=Verdict.SAME, rationale="same")]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply-same"])

    assert result.exit_code == 0
    skip_at = result.stdout.index("skipped (N>2, merge manually)")
    guidance_at = result.stdout.index("run in order (each reversible via unmerge):")
    first_at = result.stdout.index("openkos merge concepts/a concepts/b")
    second_at = result.stdout.index("openkos merge concepts/a concepts/c")
    assert skip_at < guidance_at < first_at < second_at
    assert result.stdout.count("openkos merge ") == 2
    assert "openkos merge concepts/b concepts/c" not in result.stdout
    # Zero eligible 2-member groups: the short-circuit summary still counts
    # the N>2 skip exactly as before.
    assert "applied 0, skipped 1 (N>2: 1, already-merged: 0)" in result.stdout


def test_adjudicate_apply_same_aggregate_preview_precedes_gate_and_writes(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full aggregate preview (every eligible pair plus the total
    count) is printed BEFORE the confirmation gate and before any write
    (spec: Aggregate Preview Before Any Write)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Concept D")
    group1 = _two_member_group(("concepts/a", "concepts/b"), trigger="stub1")
    group2 = _two_member_group(("concepts/c", "concepts/d"), trigger="stub2")

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group1, group2]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(group1, verdict=Verdict.SAME, rationale="r1"),
                _adjudicated(group2, verdict=Verdict.SAME, rationale="r2"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--confirm-count", "0"])

    assert result.exit_code == 1
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout
    assert "concepts/c" in result.stdout
    assert "concepts/d" in result.stdout
    assert "Total: 2" in result.stdout
    total_idx = result.stdout.index("Total: 2")
    assert result.stdout.index("concepts/b") < total_idx
    assert result.stdout.index("concepts/d") < total_idx
    assert _snapshot(tmp_path) == before


def test_adjudicate_apply_same_confirm_count_exact_applies_all(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--confirm-count <exact>` proceeds without any interactive prompt and
    applies every eligible pair (spec: `--confirm-count <exact>`
    proceeds)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Concept D")
    _seed_commit(
        tmp_path,
        [
            "bundle/concepts/a.md",
            "bundle/concepts/b.md",
            "bundle/concepts/c.md",
            "bundle/concepts/d.md",
        ],
    )
    group1 = _two_member_group(("concepts/a", "concepts/b"), trigger="stub1")
    group2 = _two_member_group(("concepts/c", "concepts/d"), trigger="stub2")

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group1, group2]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(group1, verdict=Verdict.SAME, rationale="r1"),
                _adjudicated(group2, verdict=Verdict.SAME, rationale="r2"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)
    before_commits = _commit_count(tmp_path)

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--confirm-count", "2"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert not (tmp_path / "bundle" / "concepts" / "d.md").exists()
    assert _commit_count(tmp_path) == before_commits + 2
    assert "applied 2 of 2 previewed" in result.stdout


@pytest.mark.parametrize("wrong_count", ["0", "2", "", "yes"])
def test_adjudicate_apply_same_confirm_count_mismatch_aborts_with_zero_writes(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    wrong_count: str,
) -> None:
    """`--confirm-count` wrong/empty/non-numeric aborts the run with ZERO
    writes and a byte-identical workspace (spec: `--confirm-count
    <wrong/empty/non-numeric>` aborts with zero writes)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["adjudicate", "--apply-same", "--confirm-count", wrong_count]
    )

    assert result.exit_code == 1
    assert _snapshot(tmp_path) == before


def test_adjudicate_apply_same_tty_prompt_exact_count_applies(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a TTY without `--confirm-count`, the full preview is printed then
    the operator is prompted for the exact eligible count; a correct typed
    count applies the merge (spec: TTY prompt with exact count typed
    proceeds)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _simulate_tty(monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply-same"], input="1\n")

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()


@pytest.mark.parametrize("wrong_input", ["\n", "0\n", "2\n", "yes\n"])
def test_adjudicate_apply_same_tty_prompt_wrong_input_aborts_with_zero_writes(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    wrong_input: str,
) -> None:
    """On a TTY, empty/wrong/non-numeric typed input aborts the run with
    ZERO writes and a byte-identical workspace (spec: TTY prompt with empty
    input aborts with zero writes; TTY prompt with wrong or non-numeric
    input aborts with zero writes)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _simulate_tty(monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["adjudicate", "--apply-same"], input=wrong_input)

    assert result.exit_code == 1
    assert _snapshot(tmp_path) == before


def test_adjudicate_apply_same_non_tty_without_confirm_count_refuses(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-TTY invocation without `--confirm-count` refuses with exit code
    1 and ZERO writes (spec: Non-TTY without `--confirm-count`
    refuses)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["adjudicate", "--apply-same"])

    assert result.exit_code == 1
    assert "TTY" in result.stderr
    assert _snapshot(tmp_path) == before


def test_adjudicate_apply_same_mid_batch_merge_core_failure_keeps_prior_commit(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `merge_core` failure for the 2nd of 3 accepted pairs stops the run,
    KEEPS the 1st pair's commit intact, and NEVER attempts the 3rd pair
    (spec: Mid-batch failure stops but keeps prior commits). On the
    failure path it also echoes a partial summary -- how many merges were
    applied before the failure, out of how many previewed -- so the
    operator can drive recovery (N sequential `unmerge`) without having to
    reconstruct that count themselves (4R resilience fix)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    for stem in ("a", "b", "c", "d", "e", "f"):
        _write_doc(
            tmp_path / "bundle" / "concepts" / f"{stem}.md", title=f"Concept {stem}"
        )
    group1 = _two_member_group(("concepts/a", "concepts/b"), trigger="stub1")
    group2 = _two_member_group(("concepts/c", "concepts/d"), trigger="stub2")
    group3 = _two_member_group(("concepts/e", "concepts/f"), trigger="stub3")

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group1, group2, group3]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(group1, verdict=Verdict.SAME, rationale="r1"),
                _adjudicated(group2, verdict=Verdict.SAME, rationale="r2"),
                _adjudicated(group3, verdict=Verdict.SAME, rationale="r3"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    original_merge_core = main.merge_core
    call_count = {"n": 0}

    def _flaky_merge_core(*args: object, **kwargs: object) -> object:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("disk full")
        return original_merge_core(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("openkos.cli.main.merge_core", _flaky_merge_core)

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--confirm-count", "3"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "d.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "e.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "f.md").exists()
    assert "applied 1 of 3 previewed before this failure" in result.stderr
    assert "remaining pairs were not attempted" in result.stderr


def test_adjudicate_apply_same_toctou_drift_exits_three_with_partial_summary(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An edit landing on the survivor file INSIDE the batch's unguarded
    window -- after Pass 2's re-prepare captured the pair's baseline, before
    `_commit_one_merge` writes it -- is refused, not clobbered (issue #346,
    the #306/#313/#319 drift-guard contract): exit 3, `refusing to write --`
    on stderr naming the actual command, the mid-batch partial summary
    (applied so far of total, remainder never attempted, applied merges
    reversible via `unmerge`) echoed before the run ends, the hand-edit
    intact, and nothing else on disk touched.

    The injection point differs from the `--apply` test on purpose: an edit
    landing at the typed-count prompt is RE-READ by Pass 2's re-prepare and
    recomputed over, so the prompt is not this walk's unguarded window --
    the re-prepare-to-write gap inside the batch loop is. The concurrent
    edit is therefore injected from a `prepare_merge` wrapper on its second
    call (Pass 1 previews on call 1, Pass 2 re-prepares on call 2), which
    lands the edit strictly after the baseline capture and strictly before
    the write."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = _seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)

    survivor_path = tmp_path / "bundle" / "concepts" / "a.md"
    concurrent = "hand-edited between re-prepare and write\n"
    before = _snapshot(tmp_path)

    original_prepare_merge = main.prepare_merge
    call_count = {"n": 0}

    def _prepare_then_concurrent_edit(*args: object, **kwargs: object) -> object:
        prepared = original_prepare_merge(*args, **kwargs)  # type: ignore[arg-type]
        call_count["n"] += 1
        if call_count["n"] == 2:
            survivor_path.write_text(concurrent, encoding="utf-8")
        return prepared

    monkeypatch.setattr("openkos.cli.main.prepare_merge", _prepare_then_concurrent_edit)

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--confirm-count", "1"])

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "openkos adjudicate --apply-same: refusing to write --" in result.stderr
    assert "applied 0 of 1 previewed" in result.stderr
    assert "remaining pairs were not attempted" in result.stderr
    assert "reversible via `unmerge`" in result.stderr
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/concepts/a.md")}
    assert survivor_path.read_text(encoding="utf-8") == concurrent


def test_adjudicate_apply_same_chained_shared_member_skips_second_pair(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two eligible SAME pairs sharing a member: the first is applied, the
    second is skipped without crashing, and the summary reports applied <
    previewed (spec: Stale-Id Guard Across Batch)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    group1 = _two_member_group(("concepts/a", "concepts/b"), trigger="stub1")
    group2 = _two_member_group(("concepts/b", "concepts/c"), trigger="stub2")

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group1, group2]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(group1, verdict=Verdict.SAME, rationale="r1"),
                _adjudicated(group2, verdict=Verdict.SAME, rationale="r2"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--confirm-count", "2"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "c.md").exists()
    assert "skipped (member unresolved -- already merged or missing)" in result.stdout
    assert "applied 1 of 2 previewed" in result.stdout


def test_adjudicate_apply_same_batch_round_trips_via_sequential_unmerge(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch of N applied merges round-trips via N sequential LIFO
    `unmerge` calls per survivor chain, restoring byte parity (spec:
    Reversibility Via Sequential Unmerge)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="Concept C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="Concept D")
    group1 = _two_member_group(("concepts/a", "concepts/b"), trigger="stub1")
    group2 = _two_member_group(("concepts/c", "concepts/d"), trigger="stub2")

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group1, group2]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(group1, verdict=Verdict.SAME, rationale="r1"),
                _adjudicated(group2, verdict=Verdict.SAME, rationale="r2"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--confirm-count", "2"])
    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert not (tmp_path / "bundle" / "concepts" / "d.md").exists()

    unmerge1 = runner.invoke(app, ["unmerge", "concepts/c", "concepts/d", "--auto"])
    unmerge2 = runner.invoke(app, ["unmerge", "concepts/a", "concepts/b", "--auto"])

    assert unmerge1.exit_code == 0
    assert unmerge2.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "d.md").exists()


def test_adjudicate_apply_same_zero_eligible_non_tty_exits_zero_nothing_to_apply(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero eligible SAME 2-member groups, non-TTY, no `--confirm-count`:
    the run must exit 0 with a "nothing to apply" message and zero writes
    -- NOT the non-TTY refusal, which only applies when there IS something
    to confirm (4R reliability fix: zero-eligible spurious failure)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    different_group = _two_member_group(("c", "d"), trigger="stub-diff")

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [different_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    different_group, verdict=Verdict.DIFFERENT, rationale="diff"
                )
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["adjudicate", "--apply-same"])

    assert result.exit_code == 0
    assert "nothing to apply" in result.stdout.lower()
    assert "not a TTY" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_adjudicate_apply_same_zero_eligible_tty_exits_zero_without_prompt(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero eligible SAME 2-member groups on a TTY: exit 0, no interactive
    prompt is ever shown (4R reliability fix: zero-eligible spurious
    failure)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _simulate_tty(monkeypatch)
    different_group = _two_member_group(("c", "d"), trigger="stub-diff")

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [different_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    different_group, verdict=Verdict.DIFFERENT, rationale="diff"
                )
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    # No `input=` supplied: if the code ever reached `typer.prompt`, this
    # invocation would hang/fail reading past EOF -- the absence of a
    # crash together with exit_code == 0 is itself proof no prompt fired.
    result = runner.invoke(app, ["adjudicate", "--apply-same"])

    assert result.exit_code == 0
    assert "nothing to apply" in result.stdout.lower()
    assert "Type the eligible count" not in result.stdout


def test_adjudicate_apply_same_total_matches_resolvable_preview_count(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `Total:` line, the preview lines actually displayed, and the
    count the confirm gate accepts are all the RESOLVABLE-at-preview-time
    count -- not the raw structural eligible-group count. A group that is
    already unresolvable when the preview is built (e.g. a bogus/missing
    member id, not a mid-batch chained absorption) is silently excluded
    from all three, rather than inflating `Total` past what is actually
    offered (4R resilience+readability fix: preview/Total consistency)."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    resolvable_group = _two_member_group(("concepts/a", "concepts/b"), trigger="stub1")
    unresolvable_group = _two_member_group(
        ("concepts/ghost1", "concepts/ghost2"), trigger="stub2"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [resolvable_group, unresolvable_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(resolvable_group, verdict=Verdict.SAME, rationale="r1"),
                _adjudicated(unresolvable_group, verdict=Verdict.SAME, rationale="r2"),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--apply-same", "--confirm-count", "1"])

    assert result.exit_code == 0
    assert result.stdout.count("merge concepts/b into concepts/a") == 1
    assert "ghost1" not in result.stdout
    assert "Total: 1" in result.stdout
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()


def test_adjudicate_command_name_is_not_resolve() -> None:
    """`adjudicate` is registered under its own name -- `resolve` remains
    unimplemented (spec: distinct from the reserved `resolve` verb).
    `merge` is now implemented (entity-resolution-merge slice 3, U4) and is
    deliberately NOT asserted absent here anymore."""
    callback_names = {
        command.callback.__name__
        for command in app.registered_commands
        if command.callback is not None
    }
    assert "adjudicate" in callback_names
    assert "resolve" not in callback_names
    assert "merge" in callback_names

    # Typer's own dispatcher independently confirms `resolve` is an unknown
    # subcommand (exit 2, "No such command"), not merely absent from the
    # callback-name set above.
    assert runner.invoke(app, ["resolve"]).exit_code == 2


# --- integration proof (real bundle: examples/good-life-demo) ---------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOOD_LIFE_ROOT = _REPO_ROOT / "examples" / "good-life-demo"


class _FakeOllamaClient:
    """Structural `LLMBackend` stand-in substituted for the real
    `OllamaClient` the verb constructs internally -- so the integration proof
    never depends on whether the real example bundle happens to contain
    candidate groups, and never touches a real Ollama process."""

    locality = LOCAL_BACKEND_LOCALITY
    """Stands in for `OllamaClient.locality` (issue #240): the CLI reads it
    for the embedding-host advisory and the confidential local exemption,
    and a fake without it raises `AttributeError` inside a fail-open
    handler -- a fixture gap that would read as a degrade."""

    def __init__(self, *, model: str, **kwargs: object) -> None:
        self.model = model

    def chat(self, messages: list[Message]) -> str:
        return '{"verdict": "same", "confidence": 0.5, "rationale": "fake reply"}'


def test_adjudicate_over_good_life_demo_is_read_only_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running the `adjudicate` verb against the real
    `examples/good-life-demo` workspace, with a FAKE injected `LLMBackend`
    substituted for `OllamaClient`, exits 0, writes nothing under the
    bundle (bytes AND mtimes unchanged), and renders a coherent report --
    the Unit 2 CLI-level integration proof (tasks.md 6.2)."""
    assert _GOOD_LIFE_ROOT.is_dir(), f"missing example workspace: {_GOOD_LIFE_ROOT}"
    monkeypatch.chdir(_GOOD_LIFE_ROOT)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeOllamaClient)
    bundle_dir = _GOOD_LIFE_ROOT / "bundle"
    before = _snapshot(bundle_dir)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "openkos adjudicate: workspace at" in result.stdout
    assert _snapshot(bundle_dir) == before


# ---------------------------------------------------------------------------
# directory-walk-observability follow-up: walk-incompleteness signal
# ---------------------------------------------------------------------------


def test_adjudicate_warns_stderr_on_incomplete_walk_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete directory walk (`okf._walk_errors` non-empty) prints a
    self-explaining warning to STDERR and the command still exits 0 -- WARN,
    not refuse (spec: Incomplete walk warns and still exits 0). A freshly
    initialized, empty bundle has zero candidates, so the real
    `adjudicate_candidates` never calls `llm.chat` -- a real `OllamaClient`
    is safe to construct here.

    The default workspace grants the confidential local exemption (#240),
    which is the OTHER hatch that suppresses the FILTER-SCOPED half of this
    signal -- see `test_adjudicate_local_exemption_keeps_the_general_advisory`
    below. This test is about the run where NEITHER hatch is set, so it opts
    out of the exemption through the same workspace switch a user would use,
    and both advisories are then true at once.

    Asserted through text unique to each: since #356 both lines open with
    `bundle scan was incomplete`, so that prefix no longer tells them
    apart."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" in result.stderr


def test_adjudicate_no_warning_on_clean_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully readable bundle produces NEITHER advisory (spec: Clean bundle
    produces no warning) -- the walk coming back empty is the only thing
    that silences the #356 one, which no hatch suppresses."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" not in result.stderr
    assert "confidential-content filter" not in result.stderr


# ---------------------------------------------------------------------------
# `adjudicate --json` CLI wiring (issue #137 Slice 2a)
# ---------------------------------------------------------------------------


def test_adjudicate_json_flag_emits_clean_json_and_suppresses_human_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate --json` with mixed verdicts exits 0, its stdout parses
    cleanly as JSON in `results` order, and NONE of the human-only output
    substrings leak through (spec: Machine-Readable `--json` Output Mode,
    `--json` Fully Suppresses Human Output)."""
    _init_workspace(tmp_path, monkeypatch)
    same_group = CandidateGroup(
        okf_type="person", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="org", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [same_group, different_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    same_group, verdict=Verdict.SAME, rationale="same rationale"
                ),
                _adjudicated(
                    different_group,
                    verdict=Verdict.DIFFERENT,
                    rationale="diff rationale",
                ),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "partial": False,
        "adjudicated": 2,
        "total": 2,
        "results": [
            {
                "member_ids": ["a", "b"],
                "okf_type": "person",
                "tier": "HIGH",
                "verdict": "SAME",
                "rationale": "same rationale",
            },
            {
                "member_ids": ["c", "d"],
                "okf_type": "org",
                "tier": "LOW",
                "verdict": "DIFFERENT",
                "rationale": "diff rationale",
            },
        ],
    }
    # The human tally is `adjudicated N: x SAME, ...` (main.py:1134). Guard
    # on that SHAPE, not on the bare word: since #468 item 5 the envelope
    # legitimately carries an `"adjudicated"` KEY, so the old
    # `"adjudicated " not in stdout` check now passes only by the accident
    # of `json.dumps` emitting `"adjudicated": 2` with no space before the
    # colon -- a guard that survives by punctuation is not a guard.
    assert re.search(r"^adjudicated \d+:", result.stdout, re.MULTILINE) is None
    assert "Legend:" not in result.stdout
    assert "Next: openkos merge" not in result.stdout


def test_adjudicate_json_same_only_composability_filters_to_same_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate --json --same-only` with mixed verdicts emits ONLY the
    `"verdict": "SAME"` objects (spec: `--same-only` Composes With
    `--json`). Verification RED: this flows through
    `_adjudication_payload(same_only=same_only)` wired in Task 2, so it is
    expected to already pass -- confirming existing coverage, not a new
    failure."""
    _init_workspace(tmp_path, monkeypatch)
    same_group = CandidateGroup(
        okf_type="person", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="org", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )
    uncertain_group = CandidateGroup(
        okf_type="org", member_ids=("e", "f"), tier=Tier.LOW, trigger="stub-unc"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [same_group, different_group, uncertain_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    same_group, verdict=Verdict.SAME, rationale="same rationale"
                ),
                _adjudicated(
                    different_group,
                    verdict=Verdict.DIFFERENT,
                    rationale="diff rationale",
                ),
                _adjudicated(
                    uncertain_group,
                    verdict=Verdict.UNCERTAIN,
                    rationale="unc rationale",
                ),
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--json", "--same-only"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload["results"]) == 1
    assert payload["results"][0]["verdict"] == "SAME"
    assert payload["results"][0]["rationale"] == "same rationale"
    # The display filter dropped two of the three results; the RUN still
    # adjudicated all three and completed (#468 item 5) -- `--same-only`
    # must never masquerade as a truncated batch.
    assert payload["adjudicated"] == 3
    assert payload["total"] == 3
    assert payload["partial"] is False


def test_adjudicate_json_no_candidates_emits_empty_results_not_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate --json` on a fresh, empty bundle (zero candidate groups)
    emits an envelope with an empty `results`, bypassing the "No candidates
    found." guard entirely (spec: Empty State Emits Valid Empty `results`
    Under `--json`, no candidates). Nothing was queued, so the run is
    complete, not partial."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["adjudicate", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "partial": False,
        "adjudicated": 0,
        "total": 0,
        "results": [],
    }
    assert "No candidates found." not in result.stdout


def test_adjudicate_json_same_only_all_filtered_out_emits_empty_results_not_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate --json --same-only` where every result is DIFFERENT emits
    an empty `results`, bypassing the "No SAME-verdict candidates to
    display" guard entirely (spec: Empty State Emits Valid Empty `results`
    Under `--json`, `--same-only` filters all results out).

    `results` is empty but `adjudicated` is 1: the model answered for the
    one group queued, the DISPLAY filter removed it (#468 item 5)."""
    _init_workspace(tmp_path, monkeypatch)
    different_group = CandidateGroup(
        okf_type="org", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [different_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(
                    different_group,
                    verdict=Verdict.DIFFERENT,
                    rationale="diff rationale",
                )
            ]
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate", "--json", "--same-only"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "partial": False,
        "adjudicated": 1,
        "total": 1,
        "results": [],
    }
    assert "No SAME-verdict candidates to display" not in result.stdout


def test_adjudicate_json_ollama_unavailable_still_errors_on_stderr_with_no_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adjudicate --json` with Ollama unreachable still writes the existing
    unavailability message to stderr, exits 1, and stdout has no partial
    JSON payload -- the `--json` branch sits after all three Ollama error
    handlers, so error paths are unaffected (spec: Error Paths Unaffected By
    `--json`)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_unavailable(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        raise OllamaUnavailable("Ollama not reachable at http://localhost:11434")

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _raise_unavailable)

    result = runner.invoke(app, ["adjudicate", "--json"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "Ollama not reachable" in result.stderr
    assert "ollama serve" in result.stderr
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# `_adjudication_payload` (`adjudicate --json`, issue #137 Slice 2a)
# ---------------------------------------------------------------------------


def test_adjudication_payload_empty_results_returns_empty_results_list() -> None:
    """`_adjudication_payload([], ...)` returns the envelope with an EMPTY
    `results` list -- proves the builder handles the no-results case without
    any group to map, and that the empty state is still self-describing
    rather than a bare `[]` (spec: Empty State Emits Valid Empty `results`
    Under `--json`)."""
    assert main._adjudication_payload([], same_only=False, total=0, partial=False) == {
        "partial": False,
        "adjudicated": 0,
        "total": 0,
        "results": [],
    }


def test_adjudication_payload_complete_run_marks_partial_false_with_equal_counts() -> (
    None
):
    """A complete run over two groups reports `partial: False` and
    `adjudicated == total` -- the envelope a consumer reads to know the
    payload is whole (spec: Machine-Readable `--json` Output Mode)."""
    same_group = CandidateGroup(
        okf_type="person", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="org", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )
    results = [
        _adjudicated(same_group, verdict=Verdict.SAME, rationale="same rationale"),
        _adjudicated(
            different_group, verdict=Verdict.DIFFERENT, rationale="diff rationale"
        ),
    ]

    payload = main._adjudication_payload(
        results, same_only=False, total=2, partial=False
    )

    assert payload["partial"] is False
    assert payload["adjudicated"] == 2
    assert payload["total"] == 2


def test_adjudication_payload_partial_run_marks_partial_true_with_short_count() -> None:
    """A partial batch reports `partial: True` and `adjudicated < total` --
    THE point of issue #468 item 5: `openkos adjudicate --json > out.json`
    used to write a truncated array indistinguishable from a complete run,
    recoverable only from an exit code the redirect threw away."""
    group = CandidateGroup(
        okf_type="person", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    results = [_adjudicated(group, verdict=Verdict.SAME, rationale="kept work")]

    payload = main._adjudication_payload(
        results, same_only=False, total=2, partial=True
    )

    assert payload["partial"] is True
    assert payload["adjudicated"] == 1
    assert payload["total"] == 2
    assert payload["results"] == [
        {
            "member_ids": ["a", "b"],
            "okf_type": "person",
            "tier": "HIGH",
            "verdict": "SAME",
            "rationale": "kept work",
        }
    ]


def test_adjudication_payload_same_only_does_not_shrink_adjudicated_count() -> None:
    """`same_only=True` filters `results` but MUST NOT touch `adjudicated`:
    the counters describe the RUN (what the model was asked and answered),
    `results` is a display view of it. Conflating them would report a
    complete run as partial merely because the operator filtered the
    output (spec: `--same-only` Composes With `--json`)."""
    same_group = CandidateGroup(
        okf_type="person", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="org", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )
    results = [
        _adjudicated(same_group, verdict=Verdict.SAME, rationale="same rationale"),
        _adjudicated(
            different_group, verdict=Verdict.DIFFERENT, rationale="diff rationale"
        ),
    ]

    payload = main._adjudication_payload(
        results, same_only=True, total=2, partial=False
    )

    assert payload["adjudicated"] == 2
    assert payload["total"] == 2
    assert payload["partial"] is False
    assert len(payload["results"]) == 1


def test_adjudication_payload_single_same_result_exact_field_set() -> None:
    """A single SAME result maps to exactly the locked field set, with
    `tier` rendered UPPERCASE via `.name` (NOT lowercase `.value`) and no
    `confidence` key present (spec: Machine-Readable `--json` Output Mode,
    Exact field set, no confidence)."""
    group = CandidateGroup(
        okf_type="person",
        member_ids=("concept-a", "concept-b"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    result = _adjudicated(
        group,
        verdict=Verdict.SAME,
        rationale="Same individual; identical canonical name and role.",
    )

    payload = main._adjudication_payload(
        [result], same_only=False, total=1, partial=False
    )

    assert payload["results"] == [
        {
            "member_ids": ["concept-a", "concept-b"],
            "okf_type": "person",
            "tier": "HIGH",
            "verdict": "SAME",
            "rationale": "Same individual; identical canonical name and role.",
        }
    ]
    assert "confidence" not in payload["results"][0]


def test_adjudication_payload_mixed_verdicts_preserves_order_and_renders_low_tier() -> (
    None
):
    """Mixed SAME/DIFFERENT/UNCERTAIN results preserve `results` order and
    each object's `tier`/`verdict` reflect their own group/result -- proves
    no re-derivation or re-sorting happens (spec: Deterministic,
    Pretty-Printed JSON, Stable ordering)."""
    same_group = CandidateGroup(
        okf_type="person", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="org", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )
    uncertain_group = CandidateGroup(
        okf_type="org", member_ids=("e", "f"), tier=Tier.LOW, trigger="stub-unc"
    )
    results = [
        _adjudicated(same_group, verdict=Verdict.SAME, rationale="same rationale"),
        _adjudicated(
            different_group, verdict=Verdict.DIFFERENT, rationale="diff rationale"
        ),
        _adjudicated(
            uncertain_group, verdict=Verdict.UNCERTAIN, rationale="unc rationale"
        ),
    ]

    payload = main._adjudication_payload(
        results, same_only=False, total=3, partial=False
    )

    assert payload["results"] == [
        {
            "member_ids": ["a", "b"],
            "okf_type": "person",
            "tier": "HIGH",
            "verdict": "SAME",
            "rationale": "same rationale",
        },
        {
            "member_ids": ["c", "d"],
            "okf_type": "org",
            "tier": "LOW",
            "verdict": "DIFFERENT",
            "rationale": "diff rationale",
        },
        {
            "member_ids": ["e", "f"],
            "okf_type": "org",
            "tier": "LOW",
            "verdict": "UNCERTAIN",
            "rationale": "unc rationale",
        },
    ]


def test_adjudication_payload_cross_type_group_has_no_member_types_key() -> None:
    """entity-resolution-adjudication delta scenario "`--json` payload keys
    are unchanged for a cross-type group": a cross-type group's parsed
    object has EXACTLY the existing keys (`member_ids`, `okf_type`, `tier`,
    `verdict`, `rationale`) -- no `member_types` key is ever added, even
    though the underlying `CandidateGroup` carries one."""
    group = CandidateGroup(
        okf_type="Concept+Entity",
        member_ids=("concepts/a", "entities/b"),
        tier=Tier.HIGH,
        trigger="stoicism",
        member_types=("Concept", "Entity"),
    )
    result = _adjudicated(
        group, verdict=Verdict.DIFFERENT, rationale="different entities"
    )

    payload = main._adjudication_payload(
        [result], same_only=False, total=1, partial=False
    )

    assert payload["results"] == [
        {
            "member_ids": ["concepts/a", "entities/b"],
            "okf_type": "Concept+Entity",
            "tier": "HIGH",
            "verdict": "DIFFERENT",
            "rationale": "different entities",
        }
    ]
    assert set(payload["results"][0].keys()) == {
        "member_ids",
        "okf_type",
        "tier",
        "verdict",
        "rationale",
    }


def test_adjudication_payload_same_only_filters_to_same_verdicts() -> None:
    """`same_only=True` keeps only `Verdict.SAME` entries, dropping
    DIFFERENT/UNCERTAIN from the returned list (spec: `--same-only` Composes
    With `--json`)."""
    same_group = CandidateGroup(
        okf_type="person", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-same"
    )
    different_group = CandidateGroup(
        okf_type="org", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-diff"
    )
    results = [
        _adjudicated(same_group, verdict=Verdict.SAME, rationale="same rationale"),
        _adjudicated(
            different_group, verdict=Verdict.DIFFERENT, rationale="diff rationale"
        ),
    ]

    payload = main._adjudication_payload(
        results, same_only=True, total=2, partial=False
    )

    assert len(payload["results"]) == 1
    assert payload["results"][0]["verdict"] == "SAME"
    assert payload["results"][0]["rationale"] == "same rationale"


def test_adjudicate_include_confidential_keeps_the_general_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` suppresses the FILTER-SCOPED advisory only:
    the filter is deliberately off, so its message would be a claim about a
    filter that never ran. The #356 incomplete-inputs advisory still prints,
    and the command still exits 0 -- `find_candidates` walks the same
    bundle, so a duplicate pair with one member in the unreadable subtree is
    never even proposed.

    The default workspace ALSO grants the confidential local exemption
    (#240), which independently suppresses the same filter-scoped message.
    Without opting out of that here, this test would keep passing even if
    `--include-confidential` were silently dropped from the
    `warn_if_walk_incomplete` call site, so it disables the exemption to
    make the FLAG the thing that discriminates."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["adjudicate", "--include-confidential"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" not in result.stderr


def test_adjudicate_local_exemption_keeps_the_general_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confidential local exemption (#240) suppresses the FILTER-SCOPED
    advisory the SAME way `--include-confidential` does, and leaves the #356
    incomplete-inputs advisory printing.

    This is the STOCK workspace -- default local backend, exemption active
    -- which is exactly the path that emitted nothing at all before #356:
    an unreadable subtree removed its documents from candidate generation,
    and a clean "no candidates" report exited 0 over a bundle that was never
    fully read."""
    _init_workspace(tmp_path, monkeypatch)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" not in result.stderr


# ---------------------------------------------------------------------------
# Issue #190: TTY-gated per-group progress wiring
# ---------------------------------------------------------------------------


def test_adjudicate_wires_tty_gated_progress_callback_into_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a TTY, `adjudicate` passes `observability.progress_callback`'s
    hook into `adjudicate_candidates` as `on_progress`; each invocation
    renders `openkos adjudicate: adjudicating group <i>/<n> - <elapsed>...`
    on STDERR while STDOUT keeps the clean report (issue #190, in-place +
    elapsed since #383/#384, mirroring `suggest-relations`' #134
    wiring)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        on_progress = kwargs["on_progress"]
        assert callable(on_progress)
        results = [_adjudicated(group, verdict=Verdict.DIFFERENT, rationale="diff")]
        for index, result in enumerate(results, start=1):
            on_progress(index, len(results), result)
        return AdjudicationBatch(results=results)

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "openkos adjudicate: adjudicating group 1/1 - " in result.stderr
    assert "adjudicating group" not in result.stdout


def test_adjudicate_passes_no_progress_hook_when_stderr_is_not_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a TTY (`CliRunner`'s default), the factory returns `None`
    and `adjudicate_candidates` receives `on_progress=None` -- piped output
    stays byte-clean (issue #190)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        captured["on_progress"] = kwargs["on_progress"]
        return AdjudicationBatch(results=[])

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert captured["on_progress"] is None


# ---------------------------------------------------------------------------
# curate-call-budget: `adjudicate` discloses `find_candidates_report`
# truncation (entity-resolution delta: Truncation Is Never Silent)
# ---------------------------------------------------------------------------


def test_adjudicate_over_cap_bundle_emits_the_truncation_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `find_candidates_report` reports `produced > retained`,
    `adjudicate` echoes `candidate_group_truncation_notice` to stderr
    before the LLM pass begins."""
    _init_workspace(tmp_path, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )
    report = CandidateGroupReport(groups=(group,) * 50, produced=80, retained=50)
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", lambda *a, **k: report
    )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(c, verdict=Verdict.SAME, rationale="same")
                for c in candidates
            ]
        )

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "50 of 80 candidate group(s) shown (cap reached)" in result.stderr


def test_adjudicate_below_cap_bundle_emits_no_truncation_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `produced == retained` (the cap does not bind), `adjudicate`
    emits no truncation notice on stderr."""
    _init_workspace(tmp_path, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )
    report = CandidateGroupReport(groups=(group,) * 6, produced=6, retained=6)
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", lambda *a, **k: report
    )

    def _fake_adjudicate(
        candidates: list[CandidateGroup], **kwargs: object
    ) -> AdjudicationBatch:
        return AdjudicationBatch(
            results=[
                _adjudicated(c, verdict=Verdict.SAME, rationale="same")
                for c in candidates
            ]
        )

    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", _fake_adjudicate)

    result = runner.invoke(app, ["adjudicate"])

    assert result.exit_code == 0
    assert "cap reached" not in result.stderr
