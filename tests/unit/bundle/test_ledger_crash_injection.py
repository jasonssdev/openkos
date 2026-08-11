"""Crash-injection integration tests for the ledger's two-phase write
(task 2.3; design Decision 1): `fsio.write_atomic`/`Path.replace` are
monkeypatched to raise at each boundary of the S1 (write pending) -> V
(write survivor) -> S2 (commit pending) sequence, then `ledger.recover`
is run and its verdict is checked against the truth table.

`fsio.write_atomic` is itself atomic (temp file + rename, `fsio.py:39-69`),
so an interruption strictly BEFORE its internal rename leaves no trace on
disk at all -- this is what lets "crash during S1" and "crash during V"
be simulated by simply raising before the corresponding write ever lands.
"""

from pathlib import Path

import pytest

from openkos import fsio
from openkos.bundle import ledger
from openkos.model import okf


def _entry() -> okf.MergeLedgerEntry:
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id="concepts/absorbed",
        absorbed_snapshot="absorbed text",
        survivor_before="survivor before",
        index_before="index before",
        log_before="log before",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )


def _run_two_phase_write(
    bundle_dir: Path, *, survivor_text: str, crash_after: str | None
) -> None:
    """Replays S1 -> V -> S2 for `concepts/survivor`, raising immediately
    AFTER the named step lands (`crash_after=None` runs to completion).
    Mirrors the sequence `merge_core` will drive in task 2.6, but exercised
    directly against `ledger.py` + `fsio` so this test does not depend on
    the CLI wiring that lands later in this same PR."""
    entry = _entry()
    expected_sha256 = ledger.survivor_sha256(survivor_text)

    ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[entry],
        expected_survivor_sha256=expected_sha256,
    )
    if crash_after == "S1":
        raise RuntimeError("simulated crash after S1")

    survivor_path = bundle_dir / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True, exist_ok=True)
    fsio.write_atomic(survivor_path, survivor_text)
    if crash_after == "V":
        raise RuntimeError("simulated crash after V")

    ledger.commit_pending("concepts/survivor", bundle_dir)
    if crash_after == "S2":
        raise RuntimeError("simulated crash after S2")


def test_crash_before_s1_lands_leaves_no_pending_and_recovers_as_none(
    tmp_path: Path,
) -> None:
    """`fsio.write_atomic`'s internal write raises before its rename, so
    S1 never lands at all: no `.pending` marker exists, and `recover`
    reports the already-consistent `"none"` verdict."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    def _boom(path: Path, content: str) -> None:
        raise OSError("simulated disk failure before S1's rename")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fsio, "write_atomic", _boom)
        with pytest.raises(OSError, match="simulated disk failure"):
            ledger.write_pending(
                "concepts/survivor",
                bundle_dir,
                survivor_id="concepts/survivor",
                entries=[_entry()],
                expected_survivor_sha256="a" * 64,
            )

    assert not ledger.pending_path_for("concepts/survivor", bundle_dir).exists()
    assert ledger.recover("concepts/survivor", bundle_dir) == "none"


def test_crash_after_s1_before_v_recovers_as_roll_back(tmp_path: Path) -> None:
    """S1 landed (`.pending` exists), but V never ran -- the survivor is
    absent, so `recover` rolls back (truth table row 3)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    with pytest.raises(RuntimeError, match="simulated crash after S1"):
        _run_two_phase_write(
            bundle_dir,
            survivor_text="---\ntype: Concept\n---\nMerged body.\n",
            crash_after="S1",
        )

    assert ledger.pending_path_for("concepts/survivor", bundle_dir).exists()

    verdict = ledger.recover("concepts/survivor", bundle_dir)

    assert verdict == "roll-back"
    assert not ledger.pending_path_for("concepts/survivor", bundle_dir).exists()
    assert not ledger.ledger_path_for("concepts/survivor", bundle_dir).exists()


def test_crash_after_v_before_s2_recovers_as_roll_forward(tmp_path: Path) -> None:
    """V landed (survivor written, hash-matched by `.pending`), but S2 (the
    commit rename) never ran -- `recover` promotes the pending container
    (truth table row 2)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    survivor_text = "---\ntype: Concept\n---\nMerged body.\n"

    with pytest.raises(RuntimeError, match="simulated crash after V"):
        _run_two_phase_write(bundle_dir, survivor_text=survivor_text, crash_after="V")

    assert ledger.pending_path_for("concepts/survivor", bundle_dir).exists()
    assert not ledger.ledger_path_for("concepts/survivor", bundle_dir).exists()

    verdict = ledger.recover("concepts/survivor", bundle_dir)

    assert verdict == "roll-forward"
    assert not ledger.pending_path_for("concepts/survivor", bundle_dir).exists()
    assert ledger.read_entries("concepts/survivor", bundle_dir) == [_entry()]


def test_crash_after_s2_leaves_a_consistent_committed_ledger(
    tmp_path: Path,
) -> None:
    """S2 (the commit rename) already landed before the simulated crash --
    `.pending` is gone, the committed sidecar exists, and `recover` reports
    the already-consistent `"none"` verdict (D, the absorbed-file removal,
    is orthogonal to ledger recovery)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    survivor_text = "---\ntype: Concept\n---\nMerged body.\n"

    with pytest.raises(RuntimeError, match="simulated crash after S2"):
        _run_two_phase_write(bundle_dir, survivor_text=survivor_text, crash_after="S2")

    assert not ledger.pending_path_for("concepts/survivor", bundle_dir).exists()
    assert ledger.ledger_path_for("concepts/survivor", bundle_dir).exists()

    verdict = ledger.recover("concepts/survivor", bundle_dir)

    assert verdict == "none"
    assert ledger.read_entries("concepts/survivor", bundle_dir) == [_entry()]


def test_crash_after_s1_with_a_hand_edited_survivor_recovers_as_roll_back(
    tmp_path: Path,
) -> None:
    """Residual, accepted case from the design: a crash between V and S2
    followed by a hand-edit of the survivor before recovery makes the hash
    mismatch and rolls back a merge that actually landed. Documented, not
    defended against -- this test pins that documented behavior rather
    than a silent regression of it."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    survivor_text = "---\ntype: Concept\n---\nMerged body.\n"

    with pytest.raises(RuntimeError, match="simulated crash after V"):
        _run_two_phase_write(bundle_dir, survivor_text=survivor_text, crash_after="V")

    survivor_path = bundle_dir / "concepts" / "survivor.md"
    survivor_path.write_text(survivor_text + "Hand-edited line.\n", encoding="utf-8")

    verdict = ledger.recover("concepts/survivor", bundle_dir)

    assert verdict == "roll-back"
