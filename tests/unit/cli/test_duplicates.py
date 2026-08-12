"""Unit tests for the `duplicates` CLI command: read-only candidate report.

`duplicates` is a THIRD read command, mirroring `status`/`lint`'s shape
exactly: no Phase B, no confirm gate, no `--auto`. It composes
`config.require_workspace` (exit gate) and `resolution.find_candidates`
(the whole-bundle candidate pass), then renders one report via
`typer.echo`. Exit 0 on every successful read -- whether or not any
candidates are found -- and it writes nothing (spec: Read-Only CLI
Candidate Report Verb).
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli import main
from openkos.cli.main import app
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from tests.unit.cli.conftest import snapshot_with_mtime as _snapshot

runner = CliRunner()


def test_format_group_tally_empty_returns_empty_string() -> None:
    """`_format_group_tally(0, 0)` returns `""` -- signals "no line to
    print" (spec: Reusable Group-Tally Formatting Helper, zero counts)."""
    assert main._format_group_tally(0, 0, 0) == ""


def test_format_group_tally_single_high() -> None:
    """A single HIGH-tier group renders singular `"group"` wording (spec:
    Leading Candidate-Group Tally Line, single group scenario)."""
    assert (
        main._format_group_tally(1, 0, 0)
        == "1 candidate group (1 exact, 0 acronym, 0 near)"
    )


def test_format_group_tally_single_low() -> None:
    """A single LOW-tier group renders singular `"group"` wording."""
    assert (
        main._format_group_tally(0, 0, 1)
        == "1 candidate group (0 exact, 0 acronym, 1 near)"
    )


def test_format_group_tally_mixed_plural() -> None:
    """Mixed HIGH/LOW counts pluralize and sum correctly (spec: Multiple
    mixed exact/near groups)."""
    assert (
        main._format_group_tally(2, 0, 3)
        == "5 candidate groups (2 exact, 0 acronym, 3 near)"
    )


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


def test_duplicates_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace exits non-zero, with
    the shared `require_workspace` reason under a `duplicates`-specific
    prefix, and no raw traceback (mirrors `status`/`lint`)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos duplicates: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr


def test_duplicates_fresh_bundle_reports_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly initialized, empty bundle prints a clear "no candidates"
    line and exits 0 (spec: No candidates still exits 0)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "No candidates found." in result.stdout


def test_disambiguated_pair_forms_candidate_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two different sources whose extraction both yield the same title
    produce `<slug>.md` and `<slug>-2.md` (post-disambiguation, #131) --
    `duplicates` reports them as one candidate group, contrasting the
    PRIOR behavior where a foreign-source collision was silently dropped
    and `duplicates` found nothing (spec: Disambiguated pair forms a
    candidate group). Resolution logic itself (`find_candidates`) is
    unmodified by #131 -- this is a fixture-level regression check, not a
    new grouping code path."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(
        tmp_path / "bundle" / "concepts" / "stoic-practice.md", title="Stoic Practice"
    )
    _write_doc(
        tmp_path / "bundle" / "concepts" / "stoic-practice-2.md",
        title="Stoic Practice",
    )

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "No candidates found." not in result.stdout
    assert "HIGH" in result.stdout
    assert "concepts/stoic-practice" in result.stdout
    assert "concepts/stoic-practice-2" in result.stdout


def test_duplicates_reports_a_high_tier_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with two exact-normalized-key titles renders a HIGH group,
    its members, and its trigger, and still exits 0 (spec: Candidates
    reference concept_ids, type, and match reason)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Café Society")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="cafe   society")

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "HIGH" in result.stdout
    assert "Concept" in result.stdout
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout
    assert "cafe society" in result.stdout


def test_duplicates_reports_a_low_tier_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with two near-match (not identical) titles renders a LOW
    group with its similarity trigger (spec: Highly similar non-identical
    titles form a LOW candidate)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Stoic Philosophy")

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "LOW" in result.stdout
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout


def test_duplicates_prints_leading_tally_line_for_mixed_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The group tally line renders before any group's detail lines for a
    mixed HIGH/LOW fixture (spec: Leading Candidate-Group Tally Line,
    multiple mixed exact/near groups)."""
    _init_workspace(tmp_path, monkeypatch)
    high_group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub-high"
    )
    low_group = CandidateGroup(
        okf_type="Concept", member_ids=("c", "d"), tier=Tier.LOW, trigger="stub-low"
    )

    def _fake_find_candidates(
        bundle_dir: object, **kwargs: object
    ) -> CandidateGroupReport:
        groups = [high_group, low_group]
        return CandidateGroupReport(
            groups=tuple(groups), produced=len(groups), retained=len(groups)
        )

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    tally = "2 candidate groups (1 exact, 0 acronym, 1 near)"
    assert tally in result.stdout
    lines = result.stdout.splitlines()
    tally_idx = lines.index(tally)
    first_detail_idx = next(
        i for i, line in enumerate(lines) if line.startswith("[HIGH]")
    )
    assert tally_idx < first_detail_idx


def test_duplicates_prints_legend_once_before_the_group_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legend line explaining `[tier] type -- trigger` appears exactly
    once, before the first group's detail lines (spec: One-Time
    Trigger-Column Legend Line)."""
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

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    legend = (
        "Legend: [tier] type -- trigger. The tier is the MATCH METHOD, "
        "not a strength ranking: HIGH = exact normalized key, "
        "ACRONYM = one title's token is the initials of a word run in the "
        "other, LOW = weakest per-token match of the smaller title "
        "(1.000 = every token matched, NOT identical titles)."
    )
    assert result.stdout.count(legend) == 1
    lines = result.stdout.splitlines()
    legend_idx = lines.index(legend)
    first_detail_idx = next(
        i for i, line in enumerate(lines) if line.startswith("[HIGH]")
    )
    assert legend_idx < first_detail_idx


def test_duplicates_prints_next_hint_as_the_last_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last stdout line when at least one group is found is the
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

    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", _fake_find_candidates
    )

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    lines = [line for line in result.stdout.splitlines() if line]
    assert lines[-1] == "Next: openkos merge <survivor> <absorbed>"


def test_duplicates_zero_groups_suppresses_tally_legend_and_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zero-groups path prints only the existing "No candidates found."
    line -- no tally, legend, or `Next:` hint (spec: Empty State Stays
    Single-Line)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "No candidates found." in result.stdout
    assert "candidate group" not in result.stdout
    assert "Legend:" not in result.stdout
    assert "Next:" not in result.stdout


def test_duplicates_rejects_json_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--json` or other structured output mode is offered, matching
    `status`/`lint`'s Read-Only and Human-Readable Only contract."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["duplicates", "--json"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


def test_duplicates_never_writes_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file under the workspace is created, modified, or deleted on any
    run -- whether candidates are found or not (spec: Building candidates
    writes nothing)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Stoicism")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert _snapshot(tmp_path) == before


def test_duplicates_no_auto_flag_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`duplicates` is read-only: no `--auto` or confirmation flag exists,
    unlike `ingest`/`forget` (spec: read-only reporting verb, no
    confirmation gate)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["duplicates", "--auto"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


# ---------------------------------------------------------------------------
# `--include-deprecated` (status-aware-retrieval Phase 4)
# ---------------------------------------------------------------------------


def test_duplicates_include_deprecated_flag_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-deprecated` is forwarded unchanged as
    `find_candidates(..., include_deprecated=True)` (LOCKED decision:
    `duplicates` gets the SAME flag as `adjudicate`, both call
    `find_candidates`; spec: `--include-deprecated` Escape Flag)."""
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

    result = runner.invoke(app, ["duplicates", "--include-deprecated"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_deprecated"] is True


def test_duplicates_omitted_include_deprecated_defaults_to_false(
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

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_deprecated"] is False


def test_duplicates_include_deprecated_restores_a_deprecated_group_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-deprecated` restores the deprecated group member into
    `duplicates`'s report -- the counterpart to
    `test_duplicates_default_excludes_a_deprecated_group_member` above."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: STOICISM\nstatus: deprecated\n---\n# STOICISM\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["duplicates", "--include-deprecated"])

    assert result.exit_code == 0
    assert "HIGH" in result.stdout
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout


# --- integration proof (real bundle: examples/good-life-demo) ---------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOOD_LIFE_ROOT = _REPO_ROOT / "examples" / "good-life-demo"


def test_duplicates_default_excludes_a_deprecated_group_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`duplicates` calls `find_candidates(layout.bundle_dir)` with no flag
    (status-aware-retrieval Phase 3 CLI ripple: `find_candidates`'s new
    `include_deprecated: bool = False` default means `duplicates` now
    excludes deprecated/superseded concepts from its report too, even though
    the `--include-deprecated` CLI flag itself is not wired until Phase 4 --
    pinning the intended default-exclude side effect so it is not a silent
    surprise (tasks.md 4.3))."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="STOICISM")
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: STOICISM\nstatus: deprecated\n---\n# STOICISM\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "No candidates found." in result.stdout


def test_duplicates_over_good_life_demo_is_read_only_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running the `duplicates` verb against the real
    `examples/good-life-demo` workspace exits 0, writes nothing under the
    bundle, and renders a coherent report -- the Unit 2 CLI-level
    integration proof (tasks.md 5.1)."""
    assert _GOOD_LIFE_ROOT.is_dir(), f"missing example workspace: {_GOOD_LIFE_ROOT}"
    monkeypatch.chdir(_GOOD_LIFE_ROOT)
    bundle_dir = _GOOD_LIFE_ROOT / "bundle"
    before = _snapshot(bundle_dir)

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "openkos duplicates: workspace at" in result.stdout
    assert _snapshot(bundle_dir) == before


# --- ACRONYM tier rendering (#397 follow-up) --------------------------------


def test_format_group_tally_counts_acronym_separately() -> None:
    """The ACRONYM tier is a distinct match method, not a flavour of
    near-match: folding it into `near` told the reader a deterministic
    initials match was a fuzzy similarity score."""
    assert (
        main._format_group_tally(1, 2, 3)
        == "6 candidate groups (1 exact, 2 acronym, 3 near)"
    )


def test_format_group_tally_still_empty_for_all_zero() -> None:
    """Unchanged contract: no groups means no line to print."""
    assert main._format_group_tally(0, 0, 0) == ""


def test_format_group_tally_singular_acronym_only() -> None:
    assert (
        main._format_group_tally(0, 1, 0)
        == "1 candidate group (0 exact, 1 acronym, 0 near)"
    )


# ---------------------------------------------------------------------------
# curate-call-budget: `duplicates` discloses `find_candidates_report`
# truncation (entity-resolution delta: Truncation Is Never Silent)
# ---------------------------------------------------------------------------


def test_duplicates_over_cap_bundle_emits_the_truncation_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `find_candidates_report` reports `produced > retained`,
    `duplicates` echoes `candidate_group_truncation_notice` to stderr."""
    _init_workspace(tmp_path, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )
    report = CandidateGroupReport(groups=(group,) * 50, produced=80, retained=50)
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", lambda *a, **k: report
    )

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert result.stderr == "50 of 80 candidate group(s) shown (cap reached)\n"


def test_duplicates_below_cap_bundle_emits_no_truncation_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `produced == retained` (the cap does not bind), `duplicates`
    emits no truncation notice on stderr."""
    _init_workspace(tmp_path, monkeypatch)
    group = CandidateGroup(
        okf_type="Concept", member_ids=("a", "b"), tier=Tier.HIGH, trigger="stub"
    )
    report = CandidateGroupReport(groups=(group,) * 6, produced=6, retained=6)
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report", lambda *a, **k: report
    )

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert result.stderr == ""
