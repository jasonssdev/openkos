"""Unit tests for `model/okf.py`'s deprecated-status export projection
(`deprecated-status-export`, issue #1075, Phase 1).

`project_deprecation_export` is the ONE pure function every writer, `lint`,
and `repair` call to decide whether a concept's frontmatter `status` should
carry the computed supersession -- see the spec's "One Deterministic
Projection Decides Every Export Write" table. This file is the whole test
surface for that table, its idempotence, and the never-read-back rule on
`declares_deprecated`.
"""

from openkos.model import okf

_MARKER_KEY = okf.STATUS_DERIVED_FROM_KEY


def _meta(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {"type": "Concept", "title": "Stub"}
    base.update(fields)
    return base


# -- has_valid_export_marker ---------------------------------------------


def test_has_valid_export_marker_true_for_deprecated_plus_marker() -> None:
    """spec: 'A valid marker'."""
    meta = _meta(status="deprecated", **{_MARKER_KEY: "supersedes"})

    assert okf.has_valid_export_marker(meta) is True


def test_has_valid_export_marker_false_beside_non_deprecated_status() -> None:
    """spec: 'A marker beside a non-deprecated status is invalid'."""
    meta = _meta(status="stable", **{_MARKER_KEY: "supersedes"})

    assert okf.has_valid_export_marker(meta) is False


def test_has_valid_export_marker_false_for_unknown_marker_value() -> None:
    """spec: 'A marker with an unknown value is invalid'."""
    meta = _meta(status="deprecated", **{_MARKER_KEY: "manual"})

    assert okf.has_valid_export_marker(meta) is False


def test_has_valid_export_marker_false_when_absent() -> None:
    meta = _meta(status="deprecated")

    assert okf.has_valid_export_marker(meta) is False


# -- project_deprecation_export: EXPORT -----------------------------------


def test_export_from_absent_status() -> None:
    """spec: 'A superseded stable concept is exported' (absent variant)."""
    meta = _meta()

    decision = okf.project_deprecation_export(meta, superseded=True)

    assert decision.outcome is okf.ExportOutcome.EXPORT
    assert decision.metadata["status"] == "deprecated"
    assert decision.metadata[_MARKER_KEY] == "supersedes"
    assert decision.metadata["title"] == "Stub"
    assert decision.blocked_value is None


def test_export_from_stable_status() -> None:
    """spec: 'A superseded stable concept is exported'."""
    meta = _meta(status="stable")

    decision = okf.project_deprecation_export(meta, superseded=True)

    assert decision.outcome is okf.ExportOutcome.EXPORT
    assert decision.metadata["status"] == "deprecated"
    assert decision.metadata[_MARKER_KEY] == "supersedes"


def test_export_from_legacy_active_status() -> None:
    meta = _meta(status="active")

    decision = okf.project_deprecation_export(meta, superseded=True)

    assert decision.outcome is okf.ExportOutcome.EXPORT
    assert decision.metadata["status"] == "deprecated"
    assert decision.metadata[_MARKER_KEY] == "supersedes"


# -- project_deprecation_export: UNCHANGED for superseded + deprecated ----


def test_unchanged_for_superseded_deprecated_with_valid_marker() -> None:
    meta = _meta(status="deprecated", **{_MARKER_KEY: "supersedes"})

    decision = okf.project_deprecation_export(meta, superseded=True)

    assert decision.outcome is okf.ExportOutcome.UNCHANGED
    assert decision.metadata == meta


def test_unchanged_for_superseded_deprecated_without_marker() -> None:
    """A human-authored `deprecated` on a concept that also happens to be
    superseded is left alone -- the human value wins."""
    meta = _meta(status="deprecated")

    decision = okf.project_deprecation_export(meta, superseded=True)

    assert decision.outcome is okf.ExportOutcome.UNCHANGED
    assert decision.metadata == meta


# -- project_deprecation_export: BLOCKED ----------------------------------


def test_blocked_for_draft() -> None:
    """spec: 'A human-authored draft is never overwritten'."""
    meta = _meta(status="draft")

    decision = okf.project_deprecation_export(meta, superseded=True)

    assert decision.outcome is okf.ExportOutcome.BLOCKED
    assert decision.metadata == meta
    assert decision.blocked_value == "draft"


def test_blocked_for_unknown_value() -> None:
    meta = _meta(status="pending-review")

    decision = okf.project_deprecation_export(meta, superseded=True)

    assert decision.outcome is okf.ExportOutcome.BLOCKED
    assert decision.metadata == meta
    assert decision.blocked_value == "pending-review"


# -- project_deprecation_export: WITHDRAW ---------------------------------


def test_withdraw_when_edge_is_gone() -> None:
    """spec: 'An export whose edge is gone is withdrawn'."""
    meta = _meta(status="deprecated", **{_MARKER_KEY: "supersedes"})

    decision = okf.project_deprecation_export(meta, superseded=False)

    assert decision.outcome is okf.ExportOutcome.WITHDRAW
    assert decision.metadata["status"] == "stable"
    assert _MARKER_KEY not in decision.metadata
    assert decision.blocked_value is None


# -- project_deprecation_export: UNCHANGED for unmarked live deprecated ---


def test_unchanged_for_unmarked_live_deprecated() -> None:
    """spec: 'A human-authored deprecation is never withdrawn'."""
    meta = _meta(status="deprecated")

    decision = okf.project_deprecation_export(meta, superseded=False)

    assert decision.outcome is okf.ExportOutcome.UNCHANGED
    assert decision.metadata == meta


def test_unchanged_for_live_stable_no_marker() -> None:
    meta = _meta(status="stable")

    decision = okf.project_deprecation_export(meta, superseded=False)

    assert decision.outcome is okf.ExportOutcome.UNCHANGED
    assert decision.metadata == meta


def test_unchanged_for_live_absent_status() -> None:
    meta = _meta()

    decision = okf.project_deprecation_export(meta, superseded=False)

    assert decision.outcome is okf.ExportOutcome.UNCHANGED
    assert decision.metadata == meta


# -- project_deprecation_export: DROP-MARKER ------------------------------


def test_drop_marker_for_invalid_marker_beside_draft() -> None:
    """spec: 'An invalid marker on a live concept is dropped alone'."""
    meta = _meta(status="draft", **{_MARKER_KEY: "supersedes"})

    decision = okf.project_deprecation_export(meta, superseded=False)

    assert decision.outcome is okf.ExportOutcome.DROP_MARKER
    assert decision.metadata["status"] == "draft"
    assert _MARKER_KEY not in decision.metadata


def test_drop_marker_for_unknown_marker_value_beside_deprecated() -> None:
    """A `status: deprecated` with an invalid marker VALUE (not `supersedes`)
    is not a valid export, so it is human-authored -- the marker is dropped,
    the human's `deprecated` stays (spec: 'A marker with an unknown value is
    invalid', combined with 'A human-authored deprecation is never
    withdrawn')."""
    meta = _meta(status="deprecated", **{_MARKER_KEY: "manual"})

    decision = okf.project_deprecation_export(meta, superseded=False)

    assert decision.outcome is okf.ExportOutcome.DROP_MARKER
    assert decision.metadata["status"] == "deprecated"
    assert _MARKER_KEY not in decision.metadata


# -- idempotence sweep -----------------------------------------------------

_ROWS: list[dict[str, object]] = [
    _meta(),
    _meta(status="stable"),
    _meta(status="active"),
    _meta(status="deprecated", **{_MARKER_KEY: "supersedes"}),
    _meta(status="deprecated"),
    _meta(status="draft"),
    _meta(status="pending-review"),
    _meta(status="draft", **{_MARKER_KEY: "supersedes"}),
    _meta(status="deprecated", **{_MARKER_KEY: "manual"}),
]


def test_idempotence_sweep_over_every_row_and_both_superseded_values() -> None:
    """spec: 'The projection is idempotent'. Re-projecting a projection's own
    result with the SAME `superseded` value that produced it always yields
    `UNCHANGED` or `BLOCKED`."""
    for row in _ROWS:
        for superseded in (True, False):
            first = okf.project_deprecation_export(row, superseded=superseded)
            second = okf.project_deprecation_export(
                first.metadata, superseded=superseded
            )
            assert second.outcome in (
                okf.ExportOutcome.UNCHANGED,
                okf.ExportOutcome.BLOCKED,
            ), (row, superseded, first.outcome, second.outcome)


def test_idempotence_sweep_preserves_every_other_key_and_body_byte_for_byte() -> None:
    for row in _ROWS:
        row_with_extra = dict(row)
        row_with_extra["tags"] = ["a", "b"]
        row_with_extra["sensitivity"] = "private"
        for superseded in (True, False):
            decision = okf.project_deprecation_export(
                row_with_extra, superseded=superseded
            )
            for key, value in row_with_extra.items():
                if key in ("status", _MARKER_KEY):
                    continue
                assert decision.metadata[key] == value


# -- apply_deprecation_export ----------------------------------------------


def test_apply_deprecation_export_writes_the_projected_frontmatter() -> None:
    text = okf.dump_frontmatter(_meta(status="stable"), "Body text.\n")

    decision, new_text = okf.apply_deprecation_export(text, superseded=True)

    assert decision.outcome is okf.ExportOutcome.EXPORT
    metadata, body = okf.load_frontmatter(new_text)
    assert metadata["status"] == "deprecated"
    assert metadata[_MARKER_KEY] == "supersedes"
    assert body == "Body text."


def test_apply_deprecation_export_unchanged_returns_the_same_text_object() -> None:
    """spec/design: `UNCHANGED`/`BLOCKED` return the SAME input text object
    (no re-serialization), mirroring `migrate_document`'s `Unchanged` rule."""
    text = okf.dump_frontmatter(_meta(status="stable"), "Body text.\n")

    decision, new_text = okf.apply_deprecation_export(text, superseded=False)

    assert decision.outcome is okf.ExportOutcome.UNCHANGED
    assert new_text is text


def test_apply_deprecation_export_blocked_returns_the_same_text_object() -> None:
    text = okf.dump_frontmatter(_meta(status="draft"), "Body text.\n")

    decision, new_text = okf.apply_deprecation_export(text, superseded=True)

    assert decision.outcome is okf.ExportOutcome.BLOCKED
    assert new_text is text


# -- declares_deprecated: never reads back its own export -------------------


def test_declares_deprecated_false_for_valid_export_marker() -> None:
    """spec: 'The Engine Never Reads Its Own Export'."""
    meta = _meta(status="deprecated", **{_MARKER_KEY: "supersedes"})

    assert okf.declares_deprecated(meta) is False


def test_declares_deprecated_true_for_unmarked_deprecated() -> None:
    meta = _meta(status="deprecated")

    assert okf.declares_deprecated(meta) is True


def test_declares_deprecated_true_for_deprecated_with_invalid_marker_value() -> None:
    meta = _meta(status="deprecated", **{_MARKER_KEY: "manual"})

    assert okf.declares_deprecated(meta) is True
