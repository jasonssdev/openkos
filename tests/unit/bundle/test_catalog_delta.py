"""The catalog/log delta primitives the V5 merge ledger is built on (#758).

`bundle.index.removed_entry_restores` / `restore_entries` and
`bundle.log.remove_inserted_entry` replace the whole-file `index_before`/
`log_before` snapshots. They are pure text-in/text-out, so their contract is
provable here without a bundle: exact round-trip when nothing else moved,
SURVIVAL of unrelated work when something did, and a fail-closed refusal
when the catalog drifted past recognition.
"""

import pytest

from openkos.bundle import index as bundle_index
from openkos.bundle import log as bundle_log
from openkos.model import okf

_SURVIVOR = "* [Survivor](/concepts/survivor.md) - Survivor description.\n"
_ABSORBED = "* [Absorbed](/concepts/absorbed.md) - Absorbed description.\n"
_LATER = "* [Later](/concepts/later.md) - Filed after the merge.\n"


def _index(*bullets: str) -> str:
    return okf.dump_frontmatter(
        {"okf_version": okf.OKF_VERSION}, "# Concepts\n\n" + "".join(bullets)
    )


def test_round_trip_is_byte_exact_when_nothing_else_changed() -> None:
    """remove -> restore reproduces the catalog byte for byte."""
    before = _index(_SURVIVOR, _ABSORBED)
    restores = bundle_index.removed_entry_restores(before, "concepts/absorbed")
    after, removed = bundle_index.remove_index_entry(before, "concepts/absorbed")
    assert removed == 1

    restored, inserted = bundle_index.restore_entries(after, restores)

    assert inserted == 1
    assert restored == before


def test_restore_preserves_a_bullet_added_after_the_removal() -> None:
    """The surgical property: work that landed in between SURVIVES.

    This is the whole reason the delta exists. The snapshot shape it
    replaced would have written `before` back over the top and taken
    `_LATER` with it.
    """
    before = _index(_SURVIVOR, _ABSORBED)
    restores = bundle_index.removed_entry_restores(before, "concepts/absorbed")
    after, _ = bundle_index.remove_index_entry(before, "concepts/absorbed")
    drifted = after.replace(_SURVIVOR, _SURVIVOR + _LATER)

    restored, _ = bundle_index.restore_entries(drifted, restores)

    assert _LATER in restored
    assert _ABSORBED in restored


def test_restore_targets_the_recorded_duplicate_anchor() -> None:
    """A catalog may hold byte-identical bullets, and the restore must land
    under the RECORDED one -- the reason `preceded_by_occurrence` exists.

    `remove_index_entry`'s own contract calls a repeated bullet "a duplicate
    catalog entry" and handles it rather than refusing, so this is a state
    the reversal has to survive. With a content-only anchor both candidates
    look identical and the bullet silently lands under the first, which
    reproduces the catalog in the wrong ORDER while every "is it present?"
    assertion still passes.
    """
    duplicate = "* [Dup](/concepts/dup.md) - Same bullet twice.\n"
    before = _index(duplicate, _SURVIVOR, duplicate, _ABSORBED)

    restores = bundle_index.removed_entry_restores(before, "concepts/absorbed")
    assert restores[0].preceded_by == duplicate
    assert restores[0].preceded_by_occurrence == 1

    after, _ = bundle_index.remove_index_entry(before, "concepts/absorbed")
    restored, _ = bundle_index.restore_entries(after, restores)

    assert restored == before


def test_restore_of_a_first_line_bullet_uses_the_empty_anchor() -> None:
    """A bullet with no preceding line records `""` and goes back on top.

    Built without `dump_frontmatter`, whose body always opens with a blank
    line -- under it the anchor is that blank line, which is correct but
    exercises the ordinary path rather than this branch.
    """
    before = "---\nokf_version: '0.1'\n---\n" + _ABSORBED
    restores = bundle_index.removed_entry_restores(before, "concepts/absorbed")
    assert restores[0].preceded_by == ""

    after, _ = bundle_index.remove_index_entry(before, "concepts/absorbed")
    restored, _ = bundle_index.restore_entries(after, restores)

    assert restored == before


def test_restore_is_idempotent() -> None:
    """Re-running a restore whose line is already present changes nothing --
    `unmerge` writes several files in sequence and must be safe to re-run."""
    before = _index(_SURVIVOR, _ABSORBED)
    restores = bundle_index.removed_entry_restores(before, "concepts/absorbed")
    after, _ = bundle_index.remove_index_entry(before, "concepts/absorbed")

    once, first_count = bundle_index.restore_entries(after, restores)
    twice, second_count = bundle_index.restore_entries(once, restores)

    assert first_count == 1
    assert second_count == 0
    assert twice == once


def test_restore_refuses_when_the_anchor_is_gone() -> None:
    """A catalog drifted past recognition refuses rather than guessing."""
    before = _index(_SURVIVOR, _ABSORBED)
    restores = bundle_index.removed_entry_restores(before, "concepts/absorbed")
    after, _ = bundle_index.remove_index_entry(before, "concepts/absorbed")
    without_anchor = after.replace(_SURVIVOR, "")

    with pytest.raises(ValueError, match="cannot restore"):
        bundle_index.restore_entries(without_anchor, restores)


def test_two_adjacent_duplicates_round_trip_in_order() -> None:
    """A duplicated catalog entry removes BOTH lines, and both go back --
    the second anchored on the first, satisfied by restoring in order."""
    before = _index(_SURVIVOR, _ABSORBED, _ABSORBED)
    restores = bundle_index.removed_entry_restores(before, "concepts/absorbed")
    assert len(restores) == 2

    after, removed = bundle_index.remove_index_entry(before, "concepts/absorbed")
    assert removed == 2
    restored, inserted = bundle_index.restore_entries(after, restores)

    assert inserted == 2
    assert restored == before


# --- log.md ---------------------------------------------------------------

_ENTRY = "**Merge**: Merged [concepts/absorbed](/concepts/absorbed.md) into x."
_DAY = "2026-08-17"


def _log(*sections: str) -> str:
    return "# Directory Update Log\n" + "".join(sections)


def test_log_round_trip_removes_the_section_the_insert_created() -> None:
    """Inserting into a log with no section for today creates one; reversing
    must take it away again, or byte-parity breaks on an untouched log."""
    from datetime import date

    before = _log("\n## 2026-08-16\n\n* Older entry.\n")
    after = bundle_log.insert_log_entry(before, date.fromisoformat(_DAY), _ENTRY)
    assert f"## {_DAY}" in after

    restored, removed = bundle_log.remove_inserted_entry(after, _ENTRY)

    assert removed == 1
    assert restored == before


def test_log_keeps_a_section_that_still_holds_other_entries() -> None:
    """Only an EMPTIED section goes; one holding later work stays put."""
    from datetime import date

    before = _log("\n## 2026-08-16\n\n* Older entry.\n")
    after = bundle_log.insert_log_entry(before, date.fromisoformat(_DAY), _ENTRY)
    after = bundle_log.insert_log_entry(
        after, date.fromisoformat(_DAY), "**Ingest**: Something later."
    )

    restored, removed = bundle_log.remove_inserted_entry(after, _ENTRY)

    assert removed == 1
    assert f"## {_DAY}" in restored
    assert "**Ingest**: Something later." in restored
    assert _ENTRY not in restored


def test_log_removal_takes_the_newest_of_several_identical_entries() -> None:
    """Identical merge lines can coexist (merge, unmerge, merge again). The
    TOPMOST is removed, because the log is newest-first and `unmerge` only
    reverses the LIFO tail -- the two orderings agree."""
    older = f"\n## 2026-08-15\n\n* {_ENTRY}\n"
    newer = f"\n## {_DAY}\n\n* {_ENTRY}\n"
    text = _log(newer, older)

    restored, removed = bundle_log.remove_inserted_entry(text, _ENTRY)

    assert removed == 1
    assert f"## {_DAY}" not in restored, "the newest section should have gone"
    assert "## 2026-08-15" in restored, "the older entry must be left alone"


def test_log_removal_is_idempotent() -> None:
    """An already-reversed entry is a no-op, not a refusal: `unmerge` must be
    safe to re-run after dying midway."""
    text = _log("\n## 2026-08-16\n\n* Older entry.\n")

    restored, removed = bundle_log.remove_inserted_entry(text, _ENTRY)

    assert removed == 0
    assert restored == text


# --- review corrections ----------------------------------------------------


def test_log_removal_keeps_non_bullet_content_in_the_emptied_section() -> None:
    """Emptying a dated section must not take unrelated CONTENT with it.

    The first cut pruned a section whenever no line began with the literal
    `"* "`, so an operator's prose note under that date -- or an entry
    written with the `-` marker `index.py`'s own `_BULLET_MARKERS` treats as
    legitimate -- was destroyed by the very reversal that advertises
    "everything else is left alone". Found by the resilience lens.
    """
    from datetime import date

    before = _log(f"\n## {_DAY}\n\n- Operator note kept by hand.\n")
    after = bundle_log.insert_log_entry(before, date.fromisoformat(_DAY), _ENTRY)

    restored, removed = bundle_log.remove_inserted_entry(after, _ENTRY)

    assert removed == 1
    assert "- Operator note kept by hand." in restored, (
        "reversing the merge line destroyed unrelated content in its section"
    )
    assert restored == before


def test_restore_anchor_survives_a_new_section_above_it() -> None:
    """A bullet whose anchor is the blank line under its section header goes
    back correctly after an unrelated section is catalogued above it.

    The first cut recorded the anchor's occurrence index over the WHOLE body,
    and a section's first bullet is always preceded by a blank line. Adding
    any section above shifts how many blank lines precede it, so the absolute
    index pointed at a different blank line and the bullet was restored in
    the wrong place -- byte-parity broken by an ordinary `ingest`. Found by
    the reliability lens.
    """
    before = _index(_ABSORBED, _SURVIVOR)
    restores = bundle_index.removed_entry_restores(before, "concepts/absorbed")
    after, _ = bundle_index.remove_index_entry(before, "concepts/absorbed")

    # An unrelated ingest catalogues a new section ABOVE the concepts one.
    grown = after.replace(
        "# Concepts\n",
        "# People\n\n* [Someone](/people/someone.md) - A person.\n\n# Concepts\n",
    )

    restored, _ = bundle_index.restore_entries(grown, restores)

    assert restored == grown.replace(_SURVIVOR, _ABSORBED + _SURVIVOR), (
        "the bullet was restored under the wrong blank line"
    )
