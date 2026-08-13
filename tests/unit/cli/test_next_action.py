"""Unit tests for `openkos.cli.next_action`'s open-contradiction tier
(pending-work design, Decision 6): the last tier in `_TIERS`, wired directly
to `.openkos/findings.db` and `bundle/.state/decisions/**` -- the two stores
Slice A/B1/B2 already ship, joined at read time by `decision_key_for`.

Follows `test_next.py`'s pattern: `_init_workspace` builds a bare workspace
via the real `init` command, `seed_vectors_db` marks embeddings present so
tier 1 never fires, and every fixture writes a persisted finding directly
via `state.findings.record_findings`/`bundle.decisions.write_decisions` --
no CLI writer is exercised here, mirroring Slice B1's own test posture."""

from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.bundle import decisions as bundle_decisions
from openkos.cli import next_action
from openkos.cli.main import app
from openkos.state import derived, findings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fts_index_present_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """#553 ranked a missing-FTS-index tier above the contradiction tier
    under test here; these fixtures never build an `fts.db`, so the signal
    reports present by default -- the same convention `seed_vectors_db`
    already applies to tier 1 (see `test_next.py`'s fixture of the same
    name)."""
    monkeypatch.setattr("openkos.cli.next_action.fts_index_present", lambda _path: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _record_finding(
    tmp_path: Path,
    *,
    pair_ids: tuple[str, str] = ("concepts/alpha", "concepts/beta"),
    merged_absorbed_id: str | None = None,
    verdict: str = "contradicts",
    confidence: float = 0.91,
    input_digests: tuple[findings.InputDigest, ...] = (),
) -> None:
    """Persist one finding directly via `state.findings.record_findings`
    (no CLI writer needed -- mirrors Slice A/B1's own test posture).

    `verdict` defaults to the lowercase `Verdict.CONTRADICTS.value` shape
    curate's `_persist_findings` actually writes (`verdict.verdict.value`)
    -- NOT the uppercase display rendering -- because #639's verdict filter
    compares against the persisted enum value, so a helper writing
    `"CONTRADICTS"` would silently test a shape the store never contains."""
    layout = config.WorkspaceLayout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=pair_ids,
                    merged_absorbed_id=merged_absorbed_id,
                    verdict=verdict,
                    confidence=confidence,
                    rationale="Stub rationale.",
                    input_digests=input_digests,
                )
            ],
        )
    finally:
        conn.close()


def _decline(
    tmp_path: Path,
    *,
    pair_ids: tuple[str, str],
    merged_absorbed_id: str | None = None,
) -> None:
    """Write a `declined` decision record directly via
    `bundle.decisions.write_decisions` (no CLI writer needed)."""
    from datetime import UTC, datetime

    bundle_dir = tmp_path / "bundle"
    key = bundle_decisions.decision_key_for(pair_ids, merged_absorbed_id)
    bundle_decisions.write_decisions(
        pair_ids[0],
        bundle_dir,
        records=[
            bundle_decisions.DecisionRecord(
                decision_key=key,
                pair_ids=pair_ids,
                merged_absorbed_id=merged_absorbed_id,
                state="declined",
                decided_at=datetime.now(UTC).isoformat(),
            )
        ],
    )


def _write_concept(path: Path, *, title: str, body: str = "Body.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Concept\ntitle: {title}\n---\n{body}\n", encoding="utf-8"
    )


def test_open_contradiction_is_ranked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """One open, non-stale, non-declined contradiction finding, with every
    higher-ranked tier clean, is what `next` recommends acting on
    (pending-work spec, Scenario "An open contradiction is ranked")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _record_finding(tmp_path)

    result = runner.invoke(app, ["next"])

    assert result.exit_code == 0
    assert "openkos contradictions" in result.stdout


def test_stale_or_declined_only_yields_none_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A bundle whose only persisted findings are stale or declined MUST
    yield `action is None`, and the rendered output MUST still carry
    `_STATUS_POINTER` and MUST NOT assert the bundle is clean (pending-work
    spec, Scenario "An unranked finding does not become a false
    all-clear"; design Decision 6 -- the honesty guard)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _write_concept(tmp_path / "bundle" / "concepts" / "gamma.md", title="Gamma")
    _write_concept(tmp_path / "bundle" / "concepts" / "delta.md", title="Delta")

    # Stale: stored digest deliberately does not match "alpha"'s current bytes.
    _record_finding(
        tmp_path,
        pair_ids=("concepts/alpha", "concepts/beta"),
        input_digests=(findings.InputDigest("concepts/alpha", "0" * 64),),
    )
    # Declined: an explicit operator decision hides it.
    _record_finding(tmp_path, pair_ids=("concepts/gamma", "concepts/delta"))
    _decline(tmp_path, pair_ids=("concepts/gamma", "concepts/delta"))

    result = next_action.next_action(config.WorkspaceLayout(tmp_path))

    assert result.action is None

    result_cli = runner.invoke(app, ["next"])
    assert result_cli.exit_code == 0
    assert next_action._STATUS_POINTER in result_cli.stdout
    assert "openkos contradictions" not in result_cli.stdout
    assert "clean" not in result_cli.stdout.lower()


def test_consistent_finding_is_not_ranked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A persisted `consistent` verdict -- curate records EVERY judged
    pair, not only contradictions -- is not open contradiction work, so the
    tier MUST NOT fire on it even at high confidence (#639: before the
    verdict filter, `next` told operators to review pairs already judged
    consistent, and nothing could clear the recommendation)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _record_finding(tmp_path, verdict="consistent", confidence=0.95)

    result = next_action.next_action(config.WorkspaceLayout(tmp_path))

    assert result.action is None

    result_cli = runner.invoke(app, ["next"])
    assert result_cli.exit_code == 0
    assert "openkos contradictions" not in result_cli.stdout


def test_low_confidence_contradiction_is_not_ranked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A `contradicts` verdict below `is_high_confidence_finding`'s 0.7
    threshold is not surfaced by `contradictions` or offered by
    `reconcile --from-findings`, so `next` ranking it would recommend work
    no other command shows (#639: same shared predicate, same cutoff)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _record_finding(tmp_path, verdict="contradicts", confidence=0.5)

    result = next_action.next_action(config.WorkspaceLayout(tmp_path))

    assert result.action is None


def test_contradiction_reason_line_names_a_contradiction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A high-confidence `contradicts` finding still fires the tier, and
    the reason line reads "an open contradiction finding" -- fixed wording,
    not the raw verdict value, because after #639's filter the verdict here
    is always `contradicts` and interpolating it would print the lowercase
    enum value ("an open contradicts finding")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _record_finding(tmp_path, verdict="contradicts", confidence=0.9)

    result_cli = runner.invoke(app, ["next"])

    assert result_cli.exit_code == 0
    assert "openkos contradictions" in result_cli.stdout
    assert (
        "an open contradiction finding is pending review (confidence: 0.90)"
        in result_cli.stdout
    )


def test_mixed_verdicts_rank_only_the_contradiction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """With a `consistent` finding (0.95) and a `contradicts` finding (0.9)
    both persisted, the tier fires ON THE CONTRADICTION: the consistent
    pair is filtered out even though it sorts first and carries the higher
    confidence (#639)."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _write_concept(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_concept(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")
    _write_concept(tmp_path / "bundle" / "concepts" / "gamma.md", title="Gamma")
    _write_concept(tmp_path / "bundle" / "concepts" / "delta.md", title="Delta")
    _record_finding(
        tmp_path,
        pair_ids=("concepts/alpha", "concepts/beta"),
        verdict="consistent",
        confidence=0.95,
    )
    _record_finding(
        tmp_path,
        pair_ids=("concepts/gamma", "concepts/delta"),
        verdict="contradicts",
        confidence=0.9,
    )

    result_cli = runner.invoke(app, ["next"])

    assert result_cli.exit_code == 0
    assert "concepts/gamma <-> concepts/delta" in result_cli.stdout
    assert "concepts/alpha" not in result_cli.stdout
