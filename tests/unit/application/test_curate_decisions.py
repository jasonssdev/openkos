"""Unit tests for `openkos.application.curate_decisions`, the curation
decision surface (the `curate-decisions-as-data` change, ADR-0053).

This module grows slice by slice. It holds the typed write failure, the
version token, the outcome types, the ports and observer, and the
keep-distinct and skip decisions. Every test drives the real function with
recording fakes for the effects the service does not own (the auto-commit and
the commit section), never with a mock of the function under test.
"""

import ast
import contextlib
import dataclasses
import hashlib
import inspect
import itertools
import json
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from openkos import config
from openkos.application import (
    catalog_delta,
    commit_phase,
    curate_decisions,
    curate_queue,
    write_gate,
)
from openkos.application import lifecycle as application_lifecycle
from openkos.application import pending as application_pending
from openkos.application.curate_decisions import (
    Action,
    Applied,
    BulkEligibility,
    ConfigCommitRefusal,
    DecisionItem,
    DecisionObserver,
    DecisionPolicy,
    DecisionPorts,
    DecisionVersion,
    MergeDecisionPreview,
    NotApplied,
    RelateDecisionPreview,
    Stale,
    TierDecisionPreview,
    WithheldAction,
    item_consent,
    spend_consent,
    write_walk_consent,
)
from openkos.bundle import bundle
from openkos.bundle import decisions as bundle_decisions
from openkos.graph.base import Edge
from openkos.model import okf
from openkos.resolution.adjudication import Verdict
from openkos.resolution.candidates import CandidateGroup, Tier
from openkos.resolution.edge_typing import EdgeSuggestion
from openkos.state import derived
from openkos.state import pending_queue as pq

# --------------------------------------------------------------------------- #
# WriteFailed
# --------------------------------------------------------------------------- #


def test_write_failed_is_a_refusal_carrying_structured_fields() -> None:
    failure = write_gate.WriteFailed("merge", ("concepts/a", "concepts/b"), "disk full")

    assert isinstance(failure, write_gate.Refused)
    assert isinstance(failure, write_gate.WriteRefused)
    assert failure.operation == "merge"
    assert failure.subjects == ("concepts/a", "concepts/b")
    assert failure.error == "disk full"


@pytest.mark.parametrize(
    ("operation", "subjects", "error"),
    [
        ("merge", ("concepts/a", "concepts/b"), "disk full"),
        ("relate", ("concepts/x",), "permission denied"),
        ("set_tier", ("Meeting",), "bad yaml"),
    ],
)
def test_write_failed_message_is_neutral_and_names_what_failed(
    operation: str, subjects: tuple[str, ...], error: str
) -> None:
    failure = write_gate.WriteFailed(operation, subjects, error)  # type: ignore[arg-type]

    assert operation in failure.message
    assert error in failure.message
    for subject in subjects:
        assert subject in failure.message
    assert not failure.message.startswith("openkos ")
    assert str(failure) == failure.message


def _convert(cause: OSError) -> None:
    try:
        raise cause
    except OSError as exc:
        raise write_gate.WriteFailed("merge", ("a",), str(exc)) from exc


def test_write_failed_chains_its_cause() -> None:
    cause = OSError("disk full")

    with pytest.raises(write_gate.WriteFailed) as caught:
        _convert(cause)

    assert caught.value.__cause__ is cause


def _guarded_by_a_service_except_clause() -> bool:
    """What a service's own `except (OSError, ValueError)` would do."""
    try:
        raise write_gate.WriteFailed("relate", ("a",), "boom")
    except OSError, ValueError:
        return True


def test_a_service_except_clause_for_os_and_value_errors_cannot_swallow_it() -> None:
    with pytest.raises(write_gate.WriteFailed):
        _guarded_by_a_service_except_clause()


# --------------------------------------------------------------------------- #
# DecisionVersion, outcomes, ports, observer
# --------------------------------------------------------------------------- #


def _version(
    *,
    decision_key: str = "identity:abc",
    payload_digest: str | None = "p1",
    content_digest: str = "c1",
) -> DecisionVersion:
    return DecisionVersion(
        decision_key=decision_key,
        payload_digest=payload_digest,
        content_digest=content_digest,
    )


def test_a_decision_version_is_frozen() -> None:
    version = _version()
    with pytest.raises(dataclasses.FrozenInstanceError):
        version.content_digest = "other"  # type: ignore[misc]


def test_the_token_is_deterministic_opaque_hex() -> None:
    token = _version().token()

    assert token == _version().token()
    assert len(token) == 64
    assert set(token) <= set("0123456789abcdef")
    assert "identity" not in token


@pytest.mark.parametrize(
    "changed",
    [
        {"decision_key": "identity:other"},
        {"payload_digest": "p2"},
        {"content_digest": "c2"},
    ],
)
def test_changing_any_component_changes_the_token(changed: dict[str, str]) -> None:
    assert _version(**changed).token() != _version().token()


def test_a_missing_payload_digest_is_allowed_and_distinct_from_an_empty_one() -> None:
    assert _version(payload_digest=None).token() != _version(payload_digest="").token()
    assert _version(payload_digest=None).payload_digest is None


def test_the_token_does_not_confuse_adjacent_components() -> None:
    """Moving a character across the key/payload boundary must not collide."""
    one = _version(decision_key="ab", payload_digest="c")
    other = _version(decision_key="a", payload_digest="bc")

    assert one.token() != other.token()


def test_the_action_values_are_the_wire_names() -> None:
    assert [action.value for action in Action] == [
        "merge",
        "keep_distinct",
        "skip",
        "relate",
        "relate_reversed",
        "set_tier",
        "merge_group",
        "decline",
        "reconcile",
        "supersede",
        "supersede_reversed",
        "revise",
        "revise_reversed",
    ]


def test_outcomes_are_frozen_with_structured_fields() -> None:
    applied = Applied("identity:k", Action.KEEP_DISTINCT, "abc1234")
    not_applied = NotApplied(Action.SKIP, "skipped")
    stale = Stale("identity:k", "content_changed")

    assert (applied.decision_key, applied.action, applied.commit_sha) == (
        "identity:k",
        Action.KEEP_DISTINCT,
        "abc1234",
    )
    assert not_applied.detail is None
    assert (not_applied.action, not_applied.reason) == (Action.SKIP, "skipped")
    assert (stale.decision_key, stale.reason) == ("identity:k", "content_changed")
    with pytest.raises(dataclasses.FrozenInstanceError):
        applied.commit_sha = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        not_applied.reason = "member_missing"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        stale.reason = "row_resolved"  # type: ignore[misc]


def test_ports_default_to_an_unlocked_section_and_a_silent_warning_sink(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ports = DecisionPorts(autocommit=lambda root, paths, message: None)

    ports.on_warning("ignored")

    assert ports.commit_section is commit_phase.unlocked_section
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_the_default_reconciliation_port_leaves_the_plan_as_previewed(
    tmp_path: Path,
) -> None:
    ports = DecisionPorts(autocommit=lambda root, paths, message: None)
    plan = object()

    assert ports.reconcile(tmp_path, plan) is plan  # type: ignore[arg-type]


def test_the_observer_has_exactly_the_three_in_section_events_and_no_effect(
    capsys: pytest.CaptureFixture[str],
) -> None:
    events = sorted(
        name
        for name, member in inspect.getmembers(DecisionObserver)
        if inspect.isfunction(member) and not name.startswith("_")
    )

    assert events == [
        "keep_distinct_unrecorded",
        "merge_member_missing",
        "relate_endpoint_missing",
    ]
    observer = DecisionObserver()
    observer.merge_member_missing("a", "b")
    observer.relate_endpoint_missing("a", "b")
    observer.keep_distinct_unrecorded(("a", "b"), "boom")
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_skip_writes_nothing_and_names_the_skip() -> None:
    outcome = curate_decisions.skip_decision("identity:k")

    assert outcome == NotApplied(Action.SKIP, "skipped")


# --------------------------------------------------------------------------- #
# Keep-distinct
# --------------------------------------------------------------------------- #


def _workspace(
    tmp_path: Path, *concept_ids: str
) -> tuple[Path, config.WorkspaceLayout]:
    root = tmp_path / "ws"
    (root / "bundle" / "concepts").mkdir(parents=True)
    (root / "bundle" / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "bundle" / "log.md").write_text("# Log\n", encoding="utf-8")
    (root / "openkos.yaml").write_text("model: llama3.1\n", encoding="utf-8")
    for concept_id in concept_ids:
        path = root / "bundle" / f"{concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\ntype: Thing\n---\n\nbody\n", encoding="utf-8")
    return root, config.WorkspaceLayout(root)


class _Recorder:
    """Records the order of every effect, including section entry and exit."""

    def __init__(self, *, sha: str | None = "abc1234") -> None:
        self.events: list[str] = []
        self.commits: list[tuple[Sequence[str], str]] = []
        self.warnings: list[str] = []
        self._sha = sha

    def autocommit(self, root: Path, paths: Sequence[str], message: str) -> str | None:
        self.events.append("autocommit")
        self.commits.append((tuple(paths), message))
        return self._sha

    @contextlib.contextmanager
    def section(self) -> Iterator[None]:
        self.events.append("enter")
        try:
            yield
        finally:
            self.events.append("exit")

    def ports(self) -> DecisionPorts:
        return DecisionPorts(
            autocommit=self.autocommit,
            commit_section=self.section,
            on_warning=self.warnings.append,
        )


class _Observer(DecisionObserver):
    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder
        self.unrecorded: list[tuple[tuple[str, ...], str]] = []

    def keep_distinct_unrecorded(self, member_ids: tuple[str, ...], error: str) -> None:
        self._recorder.events.append("unrecorded")
        self.unrecorded.append((member_ids, error))


def test_record_keep_distinct_writes_the_ruling_commits_and_returns_the_sha(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path, "concepts/a", "concepts/b")
    recorder = _Recorder()

    outcome = curate_decisions.record_keep_distinct(
        root, layout, ("concepts/a", "concepts/b"), ports=recorder.ports()
    )

    key = bundle_decisions.identity_decision_key_for(("concepts/a", "concepts/b"))
    assert outcome == Applied(f"identity:{key}", Action.KEEP_DISTINCT, "abc1234")
    (record,) = bundle_decisions.read_identity_decisions(
        "concepts/a", layout.bundle_dir
    )
    assert record.member_ids == ("concepts/a", "concepts/b")
    assert record.state == "declined"
    assert recorder.commits == [
        (
            ("bundle/.state/decisions/concepts/a.decisions.okf",),
            "openkos: keep distinct concepts/a/concepts/b",
        )
    ]


def test_record_keep_distinct_commit_subject_keeps_the_given_member_order(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path, "concepts/a", "concepts/b")
    recorder = _Recorder()

    curate_decisions.record_keep_distinct(
        root, layout, ("concepts/b", "concepts/a"), ports=recorder.ports()
    )

    assert recorder.commits[0][1] == "openkos: keep distinct concepts/b/concepts/a"


def test_a_degraded_autocommit_still_reports_the_ruling_as_applied(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path, "concepts/a", "concepts/b")
    recorder = _Recorder(sha=None)

    outcome = curate_decisions.record_keep_distinct(
        root, layout, ("concepts/a", "concepts/b"), ports=recorder.ports()
    )

    assert isinstance(outcome, Applied)
    assert outcome.commit_sha is None
    assert bundle_decisions.read_identity_decisions("concepts/a", layout.bundle_dir)


def test_a_write_error_is_returned_as_record_failed_with_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _workspace(tmp_path, "concepts/a", "concepts/b")
    recorder = _Recorder()

    def _boom(*args: object, **kwargs: object) -> str:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(
        "openkos.application.duplicates_service.apply_identity_decision", _boom
    )

    outcome = curate_decisions.record_keep_distinct(
        root, layout, ("concepts/a", "concepts/b"), ports=recorder.ports()
    )

    assert outcome == NotApplied(
        Action.KEEP_DISTINCT, "record_failed", "read-only filesystem"
    )
    assert recorder.commits == []


def test_a_value_error_from_the_commit_is_also_record_failed(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path, "concepts/a", "concepts/b")

    def _bad_commit(root: Path, paths: Sequence[str], message: str) -> str | None:
        raise ValueError("bad path")

    outcome = curate_decisions.record_keep_distinct(
        root,
        layout,
        ("concepts/a", "concepts/b"),
        ports=DecisionPorts(autocommit=_bad_commit),
    )

    assert outcome == NotApplied(Action.KEEP_DISTINCT, "record_failed", "bad path")


def test_the_ruling_writer_is_a_port_so_an_adapter_can_bind_it_late(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path, "concepts/a", "concepts/b")
    recorder = _Recorder()
    seen: list[tuple[str, ...]] = []

    def _writer(
        layout_: config.WorkspaceLayout,
        member_ids: tuple[str, ...],
        on_warning: object,
    ) -> str:
        seen.append(member_ids)
        return "bundle/.state/decisions/custom.okf"

    ports = DecisionPorts(
        autocommit=recorder.autocommit, record_identity_ruling=_writer
    )

    curate_decisions.record_keep_distinct(
        root, layout, ("concepts/a", "concepts/b"), ports=ports
    )

    assert seen == [("concepts/a", "concepts/b")]
    assert recorder.commits[0][0] == ("bundle/.state/decisions/custom.okf",)


def test_commit_keep_distinct_records_inside_one_commit_section(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path, "concepts/a", "concepts/b")
    recorder = _Recorder()
    observer = _Observer(recorder)

    outcome = curate_decisions.commit_keep_distinct(
        root,
        layout,
        ("concepts/a", "concepts/b"),
        ports=recorder.ports(),
        observer=observer,
    )

    assert isinstance(outcome, Applied)
    assert recorder.events == ["enter", "autocommit", "exit"]
    assert observer.unrecorded == []


def test_a_vanished_member_is_a_silent_skip_with_no_ruling_written(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path, "concepts/a")  # concepts/b is gone
    recorder = _Recorder()
    observer = _Observer(recorder)

    outcome = curate_decisions.commit_keep_distinct(
        root,
        layout,
        ("concepts/a", "concepts/b"),
        ports=recorder.ports(),
        observer=observer,
    )

    assert outcome == NotApplied(Action.KEEP_DISTINCT, "member_missing")
    assert recorder.events == ["enter", "exit"]
    assert observer.unrecorded == []
    assert (
        bundle_decisions.read_identity_decisions("concepts/a", layout.bundle_dir) == []
    )


def test_a_record_failure_notifies_the_observer_inside_the_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _workspace(tmp_path, "concepts/a", "concepts/b")
    recorder = _Recorder()
    observer = _Observer(recorder)

    def _boom(*args: object, **kwargs: object) -> str:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(
        "openkos.application.duplicates_service.apply_identity_decision", _boom
    )

    outcome = curate_decisions.commit_keep_distinct(
        root,
        layout,
        ("concepts/a", "concepts/b"),
        ports=recorder.ports(),
        observer=observer,
    )

    assert outcome == NotApplied(
        Action.KEEP_DISTINCT, "record_failed", "read-only filesystem"
    )
    assert observer.unrecorded == [
        (("concepts/a", "concepts/b"), "read-only filesystem")
    ]
    assert recorder.events == ["enter", "unrecorded", "exit"]


# --------------------------------------------------------------------------- #
# Identity merge: preview, version, commit
# --------------------------------------------------------------------------- #

_PAIR = ("concepts/a", "concepts/b")
_SURVIVOR, _ABSORBED = "concepts/a", "concepts/b"


def _concept(layout: config.WorkspaceLayout, concept_id: str, body: str) -> Path:
    path = layout.bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {"type": "Concept", "title": concept_id}, f"# {concept_id}\n\n{body}\n"
        ),
        encoding="utf-8",
    )
    return path


def _merge_workspace(tmp_path: Path) -> tuple[Path, config.WorkspaceLayout]:
    """Two mergeable concepts and a bystander document the plan's scan reads."""
    root = tmp_path / "ws"
    root.mkdir()
    config.write_config(root)
    layout = config.WorkspaceLayout(root)
    bundle.create(layout.bundle_dir, date(2026, 1, 1))
    # Short, so no reconciliation is planned unless a test forces one.
    _concept(layout, _SURVIVOR, "The survivor body.")
    _concept(layout, _ABSORBED, "The absorbed body.")
    _concept(layout, "concepts/c", "A bystander that mentions the pair.")
    return root, layout


def _group(ids: tuple[str, ...] = _PAIR) -> CandidateGroup:
    return CandidateGroup(
        okf_type="Concept", member_ids=ids, tier=Tier.HIGH, trigger="stub"
    )


def _merge_preview(
    root: Path,
    layout: config.WorkspaceLayout,
    *,
    verdict: Verdict | None = Verdict.SAME,
    reconcile: bool = False,
    no_reconcile: bool = False,
    ordered_pair: tuple[str, str] = (_SURVIVOR, _ABSORBED),
) -> MergeDecisionPreview | NotApplied:
    return curate_decisions.preview_merge(
        root,
        layout,
        _group(),
        verdict=verdict,
        ordered_pair=ordered_pair,
        no_reconcile=no_reconcile,
        reconcile=reconcile,
    )


def _must_preview(
    root: Path, layout: config.WorkspaceLayout, **kwargs: object
) -> MergeDecisionPreview:
    preview = _merge_preview(root, layout, **kwargs)  # type: ignore[arg-type]
    assert isinstance(preview, MergeDecisionPreview)
    return preview


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _edit(layout: config.WorkspaceLayout, concept_id: str) -> None:
    path = layout.bundle_dir / f"{concept_id}.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "\nEdited meanwhile.\n", encoding="utf-8"
    )


def test_a_merge_preview_carries_the_prepared_plan_and_its_disclosures(
    tmp_path: Path,
) -> None:
    root, layout = _merge_workspace(tmp_path)

    preview = _must_preview(root, layout)

    assert preview.prepared.survivor_canonical == _SURVIVOR
    assert preview.prepared.absorbed_canonical == _ABSORBED
    assert preview.member_ids == _PAIR
    assert preview.decision_key == pq.stored_decision_key(
        "identity", pq.identity_key(_PAIR)
    )
    assert preview.reconcile_planned is False
    assert isinstance(preview.cross_source_same_pair, bool)
    assert preview.cross_type_concern is None
    assert preview.survivor_criterion  # derived when the caller gives none


def test_a_given_survivor_criterion_is_carried_not_rederived(tmp_path: Path) -> None:
    root, layout = _merge_workspace(tmp_path)

    preview = curate_decisions.preview_merge(
        root,
        layout,
        _group(),
        verdict=Verdict.SAME,
        ordered_pair=(_SURVIVOR, _ABSORBED),
        survivor_criterion="the caller's criterion",
        no_reconcile=False,
        reconcile=False,
    )

    assert isinstance(preview, MergeDecisionPreview)
    assert preview.survivor_criterion == "the caller's criterion"


def test_the_merge_preview_discloses_a_planned_reconciliation_without_a_model(
    tmp_path: Path,
) -> None:
    root, layout = _merge_workspace(tmp_path)

    forced = _must_preview(root, layout, reconcile=True)
    suppressed = _must_preview(root, layout, reconcile=True, no_reconcile=True)

    assert forced.reconcile_planned is True
    assert suppressed.reconcile_planned is False
    assert forced.reconcile_planned == application_lifecycle.reconcile_planned(
        forced.prepared, no_reconcile=False, reconcile=True
    )


def test_a_merge_preview_writes_nothing(tmp_path: Path) -> None:
    root, layout = _merge_workspace(tmp_path)
    before = _snapshot(root)

    _must_preview(root, layout, reconcile=True)

    assert _snapshot(root) == before


@pytest.mark.parametrize(
    "verdict", [None, Verdict.DIFFERENT, Verdict.UNCERTAIN], ids=str
)
def test_a_merge_without_a_same_judgment_is_returned_not_mergeable(
    tmp_path: Path, verdict: Verdict | None
) -> None:
    root, layout = _merge_workspace(tmp_path)
    before = _snapshot(root)

    outcome = _merge_preview(root, layout, verdict=verdict)

    assert outcome == NotApplied(Action.MERGE, "not_mergeable")
    assert _snapshot(root) == before


def test_a_member_that_no_longer_resolves_is_returned_member_missing(
    tmp_path: Path,
) -> None:
    root, layout = _merge_workspace(tmp_path)
    (layout.bundle_dir / f"{_ABSORBED}.md").unlink()

    outcome = _merge_preview(root, layout)

    assert outcome == NotApplied(Action.MERGE, "member_missing")


def test_a_plan_that_cannot_be_built_is_a_typed_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _merge_workspace(tmp_path)
    cause = ValueError("unreadable member")

    def _boom(*args: object, **kwargs: object) -> object:
        raise cause

    monkeypatch.setattr(application_lifecycle, "prepare_one_merge", _boom)

    with pytest.raises(write_gate.WriteFailed) as caught:
        _merge_preview(root, layout)

    assert caught.value.operation == "merge"
    assert caught.value.subjects == (_SURVIVOR, _ABSORBED)
    assert caught.value.error == "unreadable member"
    assert caught.value.__cause__ is cause


def _content_digest(
    root: Path, layout: config.WorkspaceLayout, *, payload_digest: str | None = "p1"
) -> str:
    preview = _must_preview(root, layout)
    return curate_decisions.version_of(
        preview, layout=layout, payload_digest=payload_digest
    ).content_digest


def test_the_version_of_a_preview_is_deterministic_and_keyed_to_the_decision(
    tmp_path: Path,
) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)

    first = curate_decisions.version_of(preview, layout=layout, payload_digest="p1")
    again = curate_decisions.version_of(
        _must_preview(root, layout), layout=layout, payload_digest="p1"
    )

    assert first == again
    assert first.decision_key == preview.decision_key
    assert first.payload_digest == "p1"
    assert len(first.content_digest) == 64
    assert (
        curate_decisions.version_of(
            preview, layout=layout, payload_digest=None
        ).payload_digest
        is None
    )


@pytest.mark.parametrize(
    "edited",
    [_SURVIVOR, _ABSORBED, "concepts/c"],
    ids=["survivor", "absorbed", "bystander-the-scan-read"],
)
def test_editing_any_document_the_plan_rests_on_changes_the_content_digest(
    tmp_path: Path, edited: str
) -> None:
    """The bystander is the case `payload_digest` alone misses: the queue
    row's input digests cover the members, not every document a merge reads."""
    root, layout = _merge_workspace(tmp_path)
    before = _content_digest(root, layout)

    _edit(layout, edited)

    assert _content_digest(root, layout) != before


def test_a_document_appearing_in_the_bundle_changes_the_content_digest(
    tmp_path: Path,
) -> None:
    root, layout = _merge_workspace(tmp_path)
    before = _content_digest(root, layout)

    _concept(layout, "concepts/new", "Arrived while the human decided.")

    assert _content_digest(root, layout) != before


def test_the_names_of_the_documents_the_scan_could_see_are_part_of_the_digest(
    tmp_path: Path,
) -> None:
    """`present` carries the bytes of every readable document, so a name
    alone matters only for one that could not be read (or a write target):
    pin the names directly, with no bytes to carry the change."""
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)
    known = preview.prepared.read_dependencies

    def _digest_seeing(*names: str) -> str:
        dependencies = dataclasses.replace(
            known,
            documents=frozenset(layout.bundle_dir / f"{name}.md" for name in names),
        )
        replanned = dataclasses.replace(
            preview,
            prepared=dataclasses.replace(
                preview.prepared, read_dependencies=dependencies
            ),
        )
        return curate_decisions.version_of(
            replanned, layout=layout, payload_digest="p1"
        ).content_digest

    assert _digest_seeing("concepts/c") != _digest_seeing("concepts/c", "concepts/d")
    assert _digest_seeing("concepts/c") == _digest_seeing("concepts/c")


def test_a_path_outside_the_bundle_keeps_its_own_spelling_in_the_digest(
    tmp_path: Path,
) -> None:
    _root, layout = _merge_workspace(tmp_path)

    inside = layout.bundle_dir / "concepts" / "a.md"
    outside = layout.root / "openkos.yaml"

    assert curate_decisions._relative(layout, inside) == "concepts/a.md"
    assert curate_decisions._relative(layout, outside) == str(outside)


def test_the_content_digest_ignores_the_payload_digest_and_the_policy(
    tmp_path: Path,
) -> None:
    root, layout = _merge_workspace(tmp_path)
    plain = curate_decisions.version_of(
        _must_preview(root, layout), layout=layout, payload_digest="p1"
    )
    other = curate_decisions.version_of(
        _must_preview(root, layout, reconcile=True),
        layout=layout,
        payload_digest="p2",
    )

    assert plain.content_digest == other.content_digest
    assert plain.token() != other.token()


# -- commit ----------------------------------------------------------------- #


class _MergeRecorder(_Recorder):
    """Adds the reconcile port and a hook that fires as the section opens."""

    def __init__(
        self, *, sha: str | None = "abc1234", on_enter: Callable[[], None] | None = None
    ) -> None:
        super().__init__(sha=sha)
        self.reconciled: list[object] = []
        self._on_enter = on_enter

    def reconcile(
        self, root: Path, prepared: application_lifecycle.PreparedMerge
    ) -> application_lifecycle.PreparedMerge:
        self.events.append("reconcile")
        self.reconciled.append(prepared)
        return prepared

    @contextlib.contextmanager
    def section(self) -> Iterator[None]:
        self.events.append("enter")
        try:
            if self._on_enter is not None:
                self._on_enter()
            yield
        finally:
            self.events.append("exit")

    def ports(self) -> DecisionPorts:
        return dataclasses.replace(super().ports(), reconcile=self.reconcile)


class _MergeObserver(DecisionObserver):
    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder
        self.missing: list[tuple[str, str]] = []

    def merge_member_missing(self, survivor: str, absorbed: str) -> None:
        self._recorder.events.append("member_missing")
        self.missing.append((survivor, absorbed))


def _commit(
    root: Path,
    layout: config.WorkspaceLayout,
    preview: MergeDecisionPreview,
    recorder: _MergeRecorder,
    *,
    surface: str = "curate",
    expected: DecisionVersion | None = None,
) -> curate_decisions.ApplyOutcome:
    return curate_decisions.commit_merge_decision(
        root,
        layout,
        preview,
        ports=recorder.ports(),
        observer=_MergeObserver(recorder),
        surface=surface,
        expected=expected,
    )


def _absorbed_exists(layout: config.WorkspaceLayout) -> bool:
    return (layout.bundle_dir / f"{_ABSORBED}.md").exists()


def test_a_commit_merges_inside_one_section_and_commits_there(tmp_path: Path) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)
    recorder = _MergeRecorder()

    outcome = _commit(root, layout, preview, recorder)

    assert outcome == Applied(preview.decision_key, Action.MERGE, "abc1234")
    assert recorder.events == ["enter", "autocommit", "exit"]
    assert recorder.commits[0][1] == f"openkos: merge {_ABSORBED} into {_SURVIVOR}"
    assert not _absorbed_exists(layout)


def test_a_degraded_autocommit_still_applies_the_merge(tmp_path: Path) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)

    outcome = _commit(root, layout, preview, _MergeRecorder(sha=None))

    assert outcome == Applied(preview.decision_key, Action.MERGE, None)
    assert not _absorbed_exists(layout)


def test_a_cli_commit_compares_against_the_preview_baselines_and_reprepares_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("the CLI path must not re-preview")

    monkeypatch.setattr(application_lifecycle, "prepare_one_merge", _forbidden)

    outcome = _commit(root, layout, preview, _MergeRecorder())

    assert isinstance(outcome, Applied)


def test_reconciliation_runs_before_the_section_and_only_when_planned(
    tmp_path: Path,
) -> None:
    root, layout = _merge_workspace(tmp_path)
    planned = _must_preview(root, layout, reconcile=True)
    recorder = _MergeRecorder()

    _commit(root, layout, planned, recorder)

    assert recorder.events == ["reconcile", "enter", "autocommit", "exit"]
    assert recorder.reconciled == [planned.prepared]


def test_no_reconciliation_port_call_when_none_is_planned(tmp_path: Path) -> None:
    root, layout = _merge_workspace(tmp_path)
    recorder = _MergeRecorder()

    _commit(root, layout, _must_preview(root, layout), recorder)

    assert "reconcile" not in recorder.events
    assert recorder.reconciled == []


def test_the_reconciled_plan_is_the_one_that_is_written(tmp_path: Path) -> None:
    root, layout = _merge_workspace(tmp_path)
    planned = _must_preview(root, layout, reconcile=True)

    class _Rewriting(_MergeRecorder):
        def reconcile(
            self, root: Path, prepared: application_lifecycle.PreparedMerge
        ) -> application_lifecycle.PreparedMerge:
            return dataclasses.replace(
                prepared, absorbed_canonical=prepared.absorbed_canonical
            )

    seen: list[application_lifecycle.PreparedMerge] = []
    real = application_lifecycle.merge_core

    def _spy(*args: object, **kwargs: object) -> application_lifecycle.MergeResult:
        seen.append(args[3])  # type: ignore[arg-type]
        return real(*args, **kwargs)  # type: ignore[arg-type]

    recorder = _Rewriting()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(application_lifecycle, "merge_core", _spy)
        _commit(root, layout, planned, recorder)

    assert len(seen) == 1
    assert seen[0] is not planned.prepared  # the port's return value, not the preview's


def test_a_vanished_member_is_reported_inside_the_section_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)
    (layout.bundle_dir / f"{_ABSORBED}.md").unlink()
    before = _snapshot(root)
    recorder = _MergeRecorder()
    observer = _MergeObserver(recorder)

    outcome = curate_decisions.commit_merge_decision(
        root,
        layout,
        preview,
        ports=recorder.ports(),
        observer=observer,
        surface="curate",
    )

    assert outcome == NotApplied(Action.MERGE, "member_missing")
    assert observer.missing == [(_SURVIVOR, _ABSORBED)]
    assert recorder.events == ["enter", "member_missing", "exit"]
    assert _snapshot(root) == before


def test_drift_in_a_target_is_raised_naming_it_and_the_surface(tmp_path: Path) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)
    _edit(layout, _SURVIVOR)
    before = _snapshot(root)
    recorder = _MergeRecorder()

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit(root, layout, preview, recorder, surface="curate")

    assert _SURVIVOR in caught.value.message
    assert "openkos curate" in caught.value.message
    assert recorder.commits == []
    assert _snapshot(root) == before


def test_the_surface_label_is_passed_through_not_hardcoded(tmp_path: Path) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)
    _edit(layout, _SURVIVOR)

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit(root, layout, preview, _MergeRecorder(), surface="desktop")

    assert "openkos desktop" in caught.value.message
    assert "openkos curate" not in caught.value.message


def _refuse_recompose(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*args: object, **kwargs: object) -> object:
        raise catalog_delta.CatalogRecomposeError("the catalog moved")

    monkeypatch.setattr(application_lifecycle, "recompose_merge_catalog", _refuse)


def test_a_catalog_recompose_failure_becomes_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)
    _refuse_recompose(monkeypatch)
    recorder = _MergeRecorder()

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit(root, layout, preview, recorder)

    assert caught.value.message == "the catalog moved"
    assert isinstance(caught.value.__cause__, catalog_delta.CatalogRecomposeError)
    assert recorder.commits == []
    assert _absorbed_exists(layout)


def test_a_document_the_plan_only_read_drifting_is_raised(tmp_path: Path) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)
    _edit(layout, "concepts/c")
    recorder = _MergeRecorder()

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit(root, layout, preview, recorder)

    assert "concepts/c" in caught.value.message
    assert recorder.commits == []
    assert _absorbed_exists(layout)


def test_target_drift_is_reported_before_a_catalog_recompose_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Curate's order -- drift, then recompose, then read drift -- not
    `merge_concepts`' (drift, read drift, recompose)."""
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)
    _edit(layout, _SURVIVOR)
    _refuse_recompose(monkeypatch)

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit(root, layout, preview, _MergeRecorder())

    assert _SURVIVOR in caught.value.message
    assert caught.value.message != "the catalog moved"


def test_a_catalog_recompose_failure_is_reported_before_read_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)
    _edit(layout, "concepts/c")  # read drift only
    _refuse_recompose(monkeypatch)

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit(root, layout, preview, _MergeRecorder())

    assert caught.value.message == "the catalog moved"


@pytest.mark.parametrize("error", [OSError("disk full"), ValueError("bad plan")])
def test_a_failing_write_is_a_typed_write_failure_with_its_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    root, layout = _merge_workspace(tmp_path)
    preview = _must_preview(root, layout)

    def _boom(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(application_lifecycle, "merge_core", _boom)
    recorder = _MergeRecorder()

    with pytest.raises(write_gate.WriteFailed) as caught:
        _commit(root, layout, preview, recorder)

    assert caught.value.operation == "merge"
    assert caught.value.subjects == (_SURVIVOR, _ABSORBED)
    assert caught.value.error == str(error)
    assert caught.value.__cause__ is error
    assert recorder.commits == []
    assert recorder.events == ["enter", "exit"]  # the section is released


# -- the token path --------------------------------------------------------- #


def _identity_proposal(
    payload: str = "{}", digests: tuple[pq.InputDigest, ...] = ()
) -> pq.Proposal:
    return pq.Proposal(
        kind="identity",
        key_body=pq.identity_key(_PAIR),
        producer="duplicates/1",
        payload=payload,
        targets=_PAIR,
        input_digests=digests,
    )


@contextlib.contextmanager
def _unlocked() -> Iterator[None]:
    yield


def _enqueue(layout: config.WorkspaceLayout, proposal: pq.Proposal) -> None:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        pq.upsert_proposal(
            conn, proposal, commit_section=_unlocked, bundle_dir=layout.bundle_dir
        )
    finally:
        conn.close()


def _token(
    root: Path,
    layout: config.WorkspaceLayout,
    proposal: pq.Proposal,
    *,
    with_payload_digest: bool = True,
) -> DecisionVersion:
    return curate_decisions.version_of(
        _must_preview(root, layout),
        layout=layout,
        payload_digest=pq.payload_digest_for(proposal) if with_payload_digest else None,
    )


def _seeded(
    tmp_path: Path,
) -> tuple[Path, config.WorkspaceLayout, pq.Proposal, DecisionVersion]:
    root, layout = _merge_workspace(tmp_path)
    proposal = _identity_proposal(
        json.dumps({"member_ids": list(_PAIR)}), _digests(layout, *_PAIR)
    )
    _enqueue(layout, proposal)
    return root, layout, proposal, _token(root, layout, proposal)


def test_a_current_token_commits_and_resolves_the_row(tmp_path: Path) -> None:
    root, layout, proposal, version = _seeded(tmp_path)
    recorder = _MergeRecorder()

    outcome = _commit(
        root, layout, _must_preview(root, layout), recorder, expected=version
    )

    assert outcome == Applied(proposal.decision_key, Action.MERGE, "abc1234")
    assert not _absorbed_exists(layout)
    assert curate_queue.open_row_by_key(layout, proposal.decision_key) is None


def test_a_member_edited_after_the_token_is_stale_before_any_model_or_section(
    tmp_path: Path,
) -> None:
    root, layout, proposal, version = _seeded(tmp_path)
    preview = _must_preview(root, layout, reconcile=True)
    _edit(layout, _SURVIVOR)
    before = _snapshot(root)
    recorder = _MergeRecorder()

    outcome = _commit(root, layout, preview, recorder, expected=version)

    assert outcome == Stale(proposal.decision_key, "content_changed")
    assert recorder.events == []  # no reconcile call, no section, no commit
    assert _snapshot(root) == before


def test_a_row_resolved_elsewhere_is_stale_and_writes_nothing(tmp_path: Path) -> None:
    root, layout, proposal, version = _seeded(tmp_path)
    preview = _must_preview(root, layout)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        conn.execute(
            "UPDATE pending_items SET status = 'declined', resolution = 'declined'"
        )
        conn.commit()
    finally:
        conn.close()
    before = _snapshot(root)
    recorder = _MergeRecorder()

    outcome = _commit(root, layout, preview, recorder, expected=version)

    assert outcome == Stale(proposal.decision_key, "row_resolved")
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


def test_a_superseded_row_is_stale_and_writes_nothing(tmp_path: Path) -> None:
    root, layout, proposal, version = _seeded(tmp_path)
    preview = _must_preview(root, layout)
    _enqueue(
        layout,
        _identity_proposal(
            json.dumps({"member_ids": ["different"]}), _digests(layout, *_PAIR)
        ),
    )
    before = _snapshot(root)
    recorder = _MergeRecorder()

    outcome = _commit(root, layout, preview, recorder, expected=version)

    assert outcome == Stale(proposal.decision_key, "row_superseded")
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


def test_a_token_without_a_payload_digest_is_rejected_as_resolved(
    tmp_path: Path,
) -> None:
    root, layout, proposal, _ = _seeded(tmp_path)
    version = _token(root, layout, proposal, with_payload_digest=False)
    recorder = _MergeRecorder()

    outcome = _commit(
        root, layout, _must_preview(root, layout), recorder, expected=version
    )

    assert outcome == Stale(proposal.decision_key, "row_resolved")
    assert _absorbed_exists(layout)


def test_a_move_after_the_token_check_is_drift_never_stale(tmp_path: Path) -> None:
    root, layout, _proposal, version = _seeded(tmp_path)
    recorder = _MergeRecorder(on_enter=lambda: _edit(layout, _SURVIVOR))

    with pytest.raises(write_gate.DriftDetected):
        _commit(root, layout, _must_preview(root, layout), recorder, expected=version)

    assert recorder.commits == []
    assert _absorbed_exists(layout)


def test_a_different_policy_never_makes_a_token_stale(tmp_path: Path) -> None:
    root, layout, proposal, _ = _seeded(tmp_path)
    version = curate_decisions.version_of(
        _must_preview(root, layout, no_reconcile=True),
        layout=layout,
        payload_digest=pq.payload_digest_for(proposal),
    )
    recorder = _MergeRecorder()

    outcome = _commit(
        root,
        layout,
        _must_preview(root, layout, reconcile=True),
        recorder,
        expected=version,
    )

    assert isinstance(outcome, Applied)
    assert recorder.events[0] == "reconcile"


def test_a_member_gone_before_the_token_is_checked_is_not_applied(
    tmp_path: Path,
) -> None:
    root, layout, _proposal, version = _seeded(tmp_path)
    preview = _must_preview(root, layout)
    (layout.bundle_dir / f"{_ABSORBED}.md").unlink()
    recorder = _MergeRecorder()
    observer = _MergeObserver(recorder)

    outcome = curate_decisions.commit_merge_decision(
        root,
        layout,
        preview,
        ports=recorder.ports(),
        observer=observer,
        surface="curate",
        expected=version,
    )

    assert outcome == NotApplied(Action.MERGE, "member_missing")
    assert recorder.events == []  # outside any section, so nothing observed


# -- no judgment, no model -------------------------------------------------- #


def test_the_merge_half_names_no_model_client_and_no_judging() -> None:
    tree = ast.parse(inspect.getsource(curate_decisions))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    imported = {
        alias.name.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom | ast.Import)
        for alias in node.names
    }

    forbidden = {"LLMBackend", "adjudicate_candidates", "resolve_backend", "backends"}
    assert not forbidden & (names | imported)


def test_only_the_injected_port_may_reach_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the client modules poisoned, preview and commit still work; the
    reconcile port is the single seam, and the test fake is all that ran."""
    root, layout = _merge_workspace(tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")
    planned = _must_preview(root, layout, reconcile=True)
    recorder = _MergeRecorder()

    outcome = _commit(root, layout, planned, recorder)

    assert isinstance(outcome, Applied)
    assert recorder.reconciled == [planned.prepared]


# --------------------------------------------------------------------------- #
# Relate: preview and commit, forward and reversed
# --------------------------------------------------------------------------- #

_NOW = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)
_SRC, _TGT = "concepts/a", "concepts/b"


def _relate_workspace(tmp_path: Path) -> tuple[Path, config.WorkspaceLayout]:
    root = tmp_path / "ws"
    root.mkdir()
    config.write_config(root)
    layout = config.WorkspaceLayout(root)
    bundle.create(layout.bundle_dir, date(2026, 1, 1))
    _concept(layout, _SRC, "The source body.")
    _concept(layout, _TGT, "The target body.")
    return root, layout


def _suggestion(
    rel_type: str | None = "related_to",
    *,
    corrected: bool = False,
) -> EdgeSuggestion:
    return EdgeSuggestion(
        edge=Edge(source_id=_SRC, target_id=_TGT),
        suggested_type=rel_type,
        rationale="because",
        corrected_edge=Edge(source_id=_TGT, target_id=_SRC) if corrected else None,
    )


def _relate_preview(
    root: Path,
    layout: config.WorkspaceLayout,
    *,
    rel_type: str | None = "related_to",
    reverse: bool = False,
    corrected: bool = False,
    now: datetime = _NOW,
) -> RelateDecisionPreview | NotApplied:
    return curate_decisions.preview_relate(
        root,
        layout,
        _suggestion(rel_type, corrected=corrected),
        reverse=reverse,
        now=now,
    )


def _must_relate(
    root: Path, layout: config.WorkspaceLayout, **kwargs: object
) -> RelateDecisionPreview:
    preview = _relate_preview(root, layout, **kwargs)  # type: ignore[arg-type]
    assert isinstance(preview, RelateDecisionPreview)
    return preview


def test_a_relate_preview_carries_the_plan_and_the_decision_key(
    tmp_path: Path,
) -> None:
    root, layout = _relate_workspace(tmp_path)
    before = _snapshot(root)

    preview = _must_relate(root, layout)

    assert (preview.source_id, preview.target_id) == (_SRC, _TGT)
    assert preview.relation_type == "related_to"
    assert preview.decision_key == pq.stored_decision_key(
        "relation_type", pq.relation_type_key(_SRC, _TGT)
    )
    assert preview.prepared.source_canonical == _SRC
    assert preview.prepared.target_canonical == _TGT
    assert preview.prepared.updated_relations_count == 1
    assert _snapshot(root) == before  # a preview writes nothing


def test_a_reversed_relate_preview_swaps_the_endpoints_and_keeps_the_key(
    tmp_path: Path,
) -> None:
    root, layout = _relate_workspace(tmp_path)
    forward = _must_relate(root, layout)

    reversed_ = _must_relate(root, layout, reverse=True)

    assert (reversed_.source_id, reversed_.target_id) == (_TGT, _SRC)
    assert reversed_.prepared.source_canonical == _TGT
    assert reversed_.decision_key == forward.decision_key
    assert (
        reversed_.prepared.source_bytes
        == (layout.bundle_dir / f"{_TGT}.md").read_bytes()
    )


def test_a_direction_corrected_suggestion_previews_its_effective_edge(
    tmp_path: Path,
) -> None:
    root, layout = _relate_workspace(tmp_path)

    corrected = _must_relate(root, layout, corrected=True)
    both = _must_relate(root, layout, corrected=True, reverse=True)

    assert (corrected.source_id, corrected.target_id) == (_TGT, _SRC)
    # A reversal flips the effective edge, not the candidate one.
    assert (both.source_id, both.target_id) == (_SRC, _TGT)


@pytest.mark.parametrize("missing", [_SRC, _TGT])
@pytest.mark.parametrize("reverse", [False, True])
def test_an_endpoint_that_no_longer_resolves_is_returned_member_missing(
    tmp_path: Path, missing: str, reverse: bool
) -> None:
    root, layout = _relate_workspace(tmp_path)
    (layout.bundle_dir / f"{missing}.md").unlink()

    outcome = _relate_preview(root, layout, reverse=reverse)

    expected = Action.RELATE_REVERSED if reverse else Action.RELATE
    assert outcome == NotApplied(expected, "member_missing")


def test_a_suggestion_without_a_type_is_not_offered(tmp_path: Path) -> None:
    root, layout = _relate_workspace(tmp_path)

    assert _relate_preview(root, layout, rel_type=None) == NotApplied(
        Action.RELATE, "not_offered"
    )


def test_the_preview_date_comes_from_the_caller_not_a_clock(tmp_path: Path) -> None:
    root, layout = _relate_workspace(tmp_path)
    early = datetime(2020, 5, 6, 12, 0, tzinfo=UTC)

    old = _must_relate(root, layout, now=early)
    new = _must_relate(root, layout, now=_NOW)

    assert old.prepared.new_log_text != new.prepared.new_log_text
    assert early.astimezone().date().isoformat() in old.prepared.new_log_text
    assert _NOW.astimezone().date().isoformat() in new.prepared.new_log_text


def test_a_relate_plan_that_cannot_be_built_is_a_typed_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _relate_workspace(tmp_path)
    cause = ValueError("bad frontmatter")

    def _boom(*args: object, **kwargs: object) -> object:
        raise cause

    monkeypatch.setattr(application_lifecycle, "prepare_relate", _boom)

    with pytest.raises(write_gate.WriteFailed) as caught:
        _relate_preview(root, layout)

    assert caught.value.operation == "relate"
    assert caught.value.subjects == (_SRC, _TGT)
    assert caught.value.error == "bad frontmatter"
    assert caught.value.__cause__ is cause


def _relate_content_digest(
    layout: config.WorkspaceLayout, *pairs: tuple[str, bytes]
) -> str:
    lines = sorted(
        f"write\t{concept_id}.md\t{hashlib.sha256(data).hexdigest()}"
        for concept_id, data in pairs
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def test_the_relate_version_hashes_the_source_and_target_bytes(
    tmp_path: Path,
) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout, rel_type="supersedes")
    assert preview.prepared.target_bytes is not None

    version = curate_decisions.version_of(preview, layout=layout, payload_digest="p1")

    assert version.decision_key == preview.decision_key
    assert version.payload_digest == "p1"
    assert version.content_digest == _relate_content_digest(
        layout,
        (_SRC, preview.prepared.source_bytes),
        (_TGT, preview.prepared.target_bytes),
    )


def test_a_relate_without_a_target_write_hashes_the_source_alone(
    tmp_path: Path,
) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout)
    assert preview.prepared.target_bytes is None

    version = curate_decisions.version_of(preview, layout=layout, payload_digest=None)

    assert version.content_digest == _relate_content_digest(
        layout, (_SRC, preview.prepared.source_bytes)
    )


@pytest.mark.parametrize("edited", [_SRC, _TGT])
def test_editing_an_endpoint_changes_the_relate_content_digest(
    tmp_path: Path, edited: str
) -> None:
    root, layout = _relate_workspace(tmp_path)
    before = curate_decisions.version_of(
        _must_relate(root, layout, rel_type="supersedes"),
        layout=layout,
        payload_digest=None,
    )

    _edit(layout, edited)
    after = curate_decisions.version_of(
        _must_relate(root, layout, rel_type="supersedes"),
        layout=layout,
        payload_digest=None,
    )

    assert after.content_digest != before.content_digest


def test_the_log_and_the_date_are_not_part_of_the_relate_digest(
    tmp_path: Path,
) -> None:
    root, layout = _relate_workspace(tmp_path)
    first = curate_decisions.version_of(
        _must_relate(root, layout, now=datetime(2020, 1, 1, 12, tzinfo=UTC)),
        layout=layout,
        payload_digest=None,
    )
    log = layout.bundle_dir / "log.md"
    log.write_text(log.read_text(encoding="utf-8") + "\nanother verb\n")

    second = curate_decisions.version_of(
        _must_relate(root, layout, now=_NOW), layout=layout, payload_digest=None
    )

    assert second.content_digest == first.content_digest


def test_a_reversed_relate_has_its_own_content_digest(tmp_path: Path) -> None:
    root, layout = _relate_workspace(tmp_path)

    forward = curate_decisions.version_of(
        _must_relate(root, layout), layout=layout, payload_digest=None
    )
    backward = curate_decisions.version_of(
        _must_relate(root, layout, reverse=True), layout=layout, payload_digest=None
    )

    assert forward.decision_key == backward.decision_key
    assert forward.content_digest != backward.content_digest


# -- relate commit ---------------------------------------------------------- #


class _RelateRecorder(_Recorder):
    """A hook that fires as the section opens, to move the world under it."""

    def __init__(
        self, *, sha: str | None = "abc1234", on_enter: Callable[[], None] | None = None
    ) -> None:
        super().__init__(sha=sha)
        self._on_enter = on_enter

    @contextlib.contextmanager
    def section(self) -> Iterator[None]:
        self.events.append("enter")
        try:
            if self._on_enter is not None:
                self._on_enter()
            yield
        finally:
            self.events.append("exit")


class _RelateObserver(DecisionObserver):
    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder
        self.missing: list[tuple[str, str]] = []

    def relate_endpoint_missing(self, source: str, target: str) -> None:
        self._recorder.events.append("endpoint_missing")
        self.missing.append((source, target))


def _commit_relate(
    root: Path,
    layout: config.WorkspaceLayout,
    preview: RelateDecisionPreview,
    recorder: _RelateRecorder,
    *,
    surface: str = "curate",
    expected: DecisionVersion | None = None,
) -> curate_decisions.ApplyOutcome:
    return curate_decisions.commit_relate_decision(
        root,
        layout,
        preview,
        ports=recorder.ports(),
        observer=_RelateObserver(recorder),
        surface=surface,
        expected=expected,
    )


def _relations_of(layout: config.WorkspaceLayout, concept_id: str) -> list[object]:
    text = (layout.bundle_dir / f"{concept_id}.md").read_text(encoding="utf-8")
    return list(okf.decode_relations(okf.load_frontmatter(text)[0]))


def test_a_relate_commit_writes_the_edge_inside_one_section(tmp_path: Path) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout)
    recorder = _RelateRecorder()

    outcome = _commit_relate(root, layout, preview, recorder)

    assert outcome == Applied(preview.decision_key, Action.RELATE, "abc1234")
    assert recorder.events == ["enter", "autocommit", "exit"]
    assert recorder.commits == [
        (
            ("bundle/concepts/a.md", "bundle/log.md"),
            "openkos: relate concepts/a -> concepts/b (related_to)",
        )
    ]
    assert _relations_of(layout, _SRC) == [okf.Relation(target=_TGT, type="related_to")]
    assert "Added a 'related_to' relation" in (layout.bundle_dir / "log.md").read_text(
        encoding="utf-8"
    )


def test_a_supersedes_relate_commits_the_target_export_too(tmp_path: Path) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout, rel_type="supersedes")
    recorder = _RelateRecorder()

    outcome = _commit_relate(root, layout, preview, recorder)

    assert isinstance(outcome, Applied)
    assert recorder.commits == [
        (
            ("bundle/concepts/a.md", "bundle/concepts/b.md", "bundle/log.md"),
            "openkos: relate concepts/a -> concepts/b (supersedes)",
        )
    ]


def test_a_reversed_relate_writes_the_swapped_edge(tmp_path: Path) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout, reverse=True)
    recorder = _RelateRecorder()

    outcome = _commit_relate(root, layout, preview, recorder)

    assert outcome == Applied(preview.decision_key, Action.RELATE_REVERSED, "abc1234")
    assert (
        recorder.commits[0][1]
        == "openkos: relate concepts/b -> concepts/a (related_to)"
    )
    assert recorder.commits[0][0] == ("bundle/concepts/b.md", "bundle/log.md")
    assert _relations_of(layout, _TGT) == [okf.Relation(target=_SRC, type="related_to")]
    assert _relations_of(layout, _SRC) == []


def test_a_degraded_autocommit_still_applies_the_relation(tmp_path: Path) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout)

    outcome = _commit_relate(root, layout, preview, _RelateRecorder(sha=None))

    assert outcome == Applied(preview.decision_key, Action.RELATE, None)
    assert _relations_of(layout, _SRC) != []


def test_the_relate_write_goes_through_the_lifecycle_module_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout)
    calls: list[str] = []
    real = application_lifecycle.relate_core

    def _spy(*args: object, **kwargs: object) -> None:
        calls.append("relate_core")
        real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(application_lifecycle, "relate_core", _spy)

    _commit_relate(root, layout, preview, _RelateRecorder())

    assert calls == ["relate_core"]


def test_a_cli_relate_commit_reprepares_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout)

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("the CLI path must not re-preview")

    monkeypatch.setattr(application_lifecycle, "prepare_relate", _forbidden)

    assert isinstance(_commit_relate(root, layout, preview, _RelateRecorder()), Applied)


@pytest.mark.parametrize("gone", [_SRC, _TGT])
def test_an_endpoint_vanishing_is_reported_inside_the_section_and_writes_nothing(
    tmp_path: Path, gone: str
) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout)
    recorder = _RelateRecorder(
        on_enter=lambda: (layout.bundle_dir / f"{gone}.md").unlink()
    )
    observer = _RelateObserver(recorder)
    before = _snapshot(layout.bundle_dir)
    before.pop(layout.bundle_dir / f"{gone}.md")

    outcome = curate_decisions.commit_relate_decision(
        root,
        layout,
        preview,
        ports=recorder.ports(),
        observer=observer,
        surface="curate",
    )

    assert outcome == NotApplied(Action.RELATE, "member_missing")
    assert recorder.events == ["enter", "endpoint_missing", "exit"]
    assert observer.missing == [(_SRC, _TGT)]
    assert recorder.commits == []
    assert _snapshot(layout.bundle_dir) == before


@pytest.mark.parametrize("edited", [_SRC, _TGT])
def test_drift_in_an_endpoint_is_raised_naming_it_and_the_surface(
    tmp_path: Path, edited: str
) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout, rel_type="supersedes")
    _edit(layout, edited)
    recorder = _RelateRecorder()
    before = _snapshot(root)

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit_relate(root, layout, preview, recorder, surface="desktop")

    assert edited in caught.value.message
    assert "openkos desktop" in caught.value.message
    assert recorder.commits == []
    assert _snapshot(root) == before


def _refuse_log_recompose(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*args: object, **kwargs: object) -> object:
        raise catalog_delta.CatalogRecomposeError("the log moved")

    monkeypatch.setattr(application_lifecycle, "recompose_relate_log", _refuse)


def test_a_log_recompose_failure_becomes_drift_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout)
    _refuse_log_recompose(monkeypatch)
    recorder = _RelateRecorder()
    before = _snapshot(root)

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit_relate(root, layout, preview, recorder)

    assert caught.value.message == "the log moved"
    assert isinstance(caught.value.__cause__, catalog_delta.CatalogRecomposeError)
    assert recorder.commits == []
    assert _snapshot(root) == before


def test_the_log_recompose_is_told_which_surface_is_committing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout)
    verbs: list[object] = []
    real = application_lifecycle.recompose_relate_log

    def _spy(*args: object, **kwargs: object) -> object:
        verbs.append(kwargs.get("verb"))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(application_lifecycle, "recompose_relate_log", _spy)

    _commit_relate(root, layout, preview, _RelateRecorder(), surface="desktop")

    assert verbs == ["desktop"]


def test_endpoint_drift_is_reported_before_a_log_recompose_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout)
    _edit(layout, _SRC)
    _refuse_log_recompose(monkeypatch)

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit_relate(root, layout, preview, _RelateRecorder())

    assert _SRC in caught.value.message
    assert caught.value.message != "the log moved"


@pytest.mark.parametrize("error", [OSError("disk full"), ValueError("bad plan")])
def test_a_failing_relate_write_is_a_typed_write_failure_with_its_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    root, layout = _relate_workspace(tmp_path)
    preview = _must_relate(root, layout, reverse=True)

    def _boom(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(application_lifecycle, "relate_core", _boom)
    recorder = _RelateRecorder()

    with pytest.raises(write_gate.WriteFailed) as caught:
        _commit_relate(root, layout, preview, recorder)

    assert caught.value.operation == "relate"
    assert caught.value.subjects == (_TGT, _SRC)
    assert caught.value.error == str(error)
    assert caught.value.__cause__ is error
    assert recorder.commits == []
    assert recorder.events == ["enter", "exit"]


# -- the relate row resolution --------------------------------------------- #


def _relation_proposal(
    layout: config.WorkspaceLayout, source: str = _SRC, target: str = _TGT
) -> pq.Proposal:
    return pq.Proposal(
        kind="relation_type",
        key_body=pq.relation_type_key(_SRC, _TGT),
        producer="suggest-relations/1",
        payload=json.dumps(
            {
                "effective_source_id": source,
                "effective_target_id": target,
                "suggested_type": "related_to",
            }
        ),
        targets=(_SRC, _TGT),
        input_digests=_digests(layout, _SRC, _TGT),
    )


def _row_resolution(
    layout: config.WorkspaceLayout, proposal: pq.Proposal
) -> tuple[str, str]:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        status, resolution = conn.execute(
            "SELECT status, resolution FROM pending_items WHERE decision_key = ?",
            (proposal.decision_key,),
        ).fetchone()
        return status, resolution
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("reverse", "resolution"), [(False, "as_proposed"), (True, "modified")]
)
def test_a_relate_commit_resolves_its_row_as_proposed_or_modified(
    tmp_path: Path, reverse: bool, resolution: str
) -> None:
    root, layout = _relate_workspace(tmp_path)
    proposal = _relation_proposal(layout)
    _enqueue(layout, proposal)

    _commit_relate(
        root, layout, _must_relate(root, layout, reverse=reverse), _RelateRecorder()
    )

    assert _row_resolution(layout, proposal) == ("applied", resolution)


# -- the relate token path -------------------------------------------------- #


def _relate_token(
    root: Path,
    layout: config.WorkspaceLayout,
    proposal: pq.Proposal,
    *,
    with_payload_digest: bool = True,
    reverse: bool = False,
) -> DecisionVersion:
    return curate_decisions.version_of(
        _must_relate(root, layout, reverse=reverse),
        layout=layout,
        payload_digest=pq.payload_digest_for(proposal) if with_payload_digest else None,
    )


def _relate_seeded(
    tmp_path: Path,
) -> tuple[Path, config.WorkspaceLayout, pq.Proposal, DecisionVersion]:
    root, layout = _relate_workspace(tmp_path)
    proposal = _relation_proposal(layout)
    _enqueue(layout, proposal)
    return root, layout, proposal, _relate_token(root, layout, proposal)


def test_a_current_relate_token_commits_and_resolves_the_row(tmp_path: Path) -> None:
    root, layout, proposal, version = _relate_seeded(tmp_path)

    outcome = _commit_relate(
        root, layout, _must_relate(root, layout), _RelateRecorder(), expected=version
    )

    assert outcome == Applied(proposal.decision_key, Action.RELATE, "abc1234")
    assert curate_queue.open_row_by_key(layout, proposal.decision_key) is None
    assert _relations_of(layout, _SRC) != []


def test_a_reversed_relate_token_commits_the_reversed_edge(tmp_path: Path) -> None:
    root, layout = _relate_workspace(tmp_path)
    proposal = _relation_proposal(layout)
    _enqueue(layout, proposal)
    version = _relate_token(root, layout, proposal, reverse=True)

    outcome = _commit_relate(
        root,
        layout,
        _must_relate(root, layout, reverse=True),
        _RelateRecorder(),
        expected=version,
    )

    assert isinstance(outcome, Applied)
    assert outcome.action is Action.RELATE_REVERSED
    assert _relations_of(layout, _TGT) != []


def test_an_endpoint_edited_after_the_relate_token_is_stale_before_the_section(
    tmp_path: Path,
) -> None:
    root, layout, proposal, version = _relate_seeded(tmp_path)
    preview = _must_relate(root, layout)
    _edit(layout, _SRC)
    before = _snapshot(root)
    recorder = _RelateRecorder()

    outcome = _commit_relate(root, layout, preview, recorder, expected=version)

    assert outcome == Stale(proposal.decision_key, "content_changed")
    assert recorder.events == []
    assert _snapshot(root) == before


def test_a_relate_row_resolved_elsewhere_is_stale_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout, proposal, version = _relate_seeded(tmp_path)
    preview = _must_relate(root, layout)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        conn.execute(
            "UPDATE pending_items SET status = 'declined', resolution = 'declined'"
        )
        conn.commit()
    finally:
        conn.close()
    before = _snapshot(root)
    recorder = _RelateRecorder()

    outcome = _commit_relate(root, layout, preview, recorder, expected=version)

    assert outcome == Stale(proposal.decision_key, "row_resolved")
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


def test_a_superseded_relate_row_is_stale_and_writes_nothing(tmp_path: Path) -> None:
    root, layout, proposal, version = _relate_seeded(tmp_path)
    preview = _must_relate(root, layout)
    _enqueue(layout, _relation_proposal(layout, source=_TGT, target=_SRC))
    before = _snapshot(root)
    recorder = _RelateRecorder()

    outcome = _commit_relate(root, layout, preview, recorder, expected=version)

    assert outcome == Stale(proposal.decision_key, "row_superseded")
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


def test_a_relate_token_without_a_payload_digest_is_rejected_as_resolved(
    tmp_path: Path,
) -> None:
    root, layout, proposal, _ = _relate_seeded(tmp_path)
    version = _relate_token(root, layout, proposal, with_payload_digest=False)

    outcome = _commit_relate(
        root, layout, _must_relate(root, layout), _RelateRecorder(), expected=version
    )

    assert outcome == Stale(proposal.decision_key, "row_resolved")
    assert _relations_of(layout, _SRC) == []


def test_a_move_after_the_relate_token_check_is_drift_never_stale(
    tmp_path: Path,
) -> None:
    root, layout, _proposal, version = _relate_seeded(tmp_path)
    recorder = _RelateRecorder(on_enter=lambda: _edit(layout, _SRC))

    with pytest.raises(write_gate.DriftDetected):
        _commit_relate(
            root, layout, _must_relate(root, layout), recorder, expected=version
        )

    assert recorder.commits == []


def test_an_endpoint_gone_before_the_relate_token_is_checked_is_not_applied(
    tmp_path: Path,
) -> None:
    root, layout, _proposal, version = _relate_seeded(tmp_path)
    preview = _must_relate(root, layout)
    (layout.bundle_dir / f"{_TGT}.md").unlink()
    recorder = _RelateRecorder()

    outcome = _commit_relate(root, layout, preview, recorder, expected=version)

    assert outcome == NotApplied(Action.RELATE, "member_missing")
    assert recorder.events == []


# --------------------------------------------------------------------------- #
# Set tier: preview and commit
# --------------------------------------------------------------------------- #

_TYPE, _TIER = "Person", "volatile"


def _tier_workspace(tmp_path: Path) -> tuple[Path, config.WorkspaceLayout]:
    root = tmp_path / "ws"
    root.mkdir()
    config.write_config(root)
    return root, config.WorkspaceLayout(root)


def _must_tier(
    layout: config.WorkspaceLayout, type_name: str = _TYPE, tier: str = _TIER
) -> TierDecisionPreview:
    return curate_decisions.preview_set_tier(layout, type_name, tier)


def _config_text(layout: config.WorkspaceLayout) -> str:
    return layout.config_path.read_text(encoding="utf-8")


def _move_effective_tier(layout: config.WorkspaceLayout, tier: str = "static") -> None:
    """Change the tier the type is on, as another verb would."""
    layout.config_path.write_text(
        config.set_type_tier(_config_text(layout), _TYPE, tier), encoding="utf-8"
    )


def test_a_tier_preview_carries_the_plan_and_the_decision_key(tmp_path: Path) -> None:
    _root, layout = _tier_workspace(tmp_path)
    before = layout.config_path.read_bytes()

    preview = _must_tier(layout)

    assert preview.type_name == _TYPE
    assert preview.tier == _TIER
    assert preview.decision_key == pq.stored_decision_key(
        "volatility", pq.volatility_key(_TYPE)
    )
    assert preview.prepared.config_bytes == before
    assert preview.prepared.new_config_text == config.set_type_tier(
        before.decode("utf-8"), _TYPE, _TIER
    )


def test_a_tier_preview_writes_nothing(tmp_path: Path) -> None:
    root, layout = _tier_workspace(tmp_path)
    before = _snapshot(root)

    _must_tier(layout)

    assert _snapshot(root) == before


@pytest.mark.parametrize("error", [OSError, ValueError])
def test_a_tier_plan_that_cannot_be_built_is_a_typed_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    _root, layout = _tier_workspace(tmp_path)
    cause = error("cannot edit")

    def _boom(*args: object, **kwargs: object) -> object:
        raise cause

    monkeypatch.setattr(application_lifecycle, "prepare_set_volatility", _boom)

    with pytest.raises(write_gate.WriteFailed) as caught:
        _must_tier(layout)

    assert caught.value.operation == "set_tier"
    assert caught.value.subjects == (_TYPE,)
    assert caught.value.error == "cannot edit"
    assert caught.value.__cause__ is cause


def _tier_content_digest(layout: config.WorkspaceLayout, tier: str = _TIER) -> str:
    lines = sorted(
        [
            f"tier\t{_TYPE}\t{tier}",
            f"config\t{hashlib.sha256(layout.config_path.read_bytes()).hexdigest()}",
        ]
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def test_the_tier_version_hashes_the_proposed_tier_and_the_config_bytes(
    tmp_path: Path,
) -> None:
    _root, layout = _tier_workspace(tmp_path)
    preview = _must_tier(layout)

    version = curate_decisions.version_of(preview, layout=layout, payload_digest="p1")

    assert version.decision_key == preview.decision_key
    assert version.payload_digest == "p1"
    assert version.content_digest == _tier_content_digest(layout)


def test_the_tier_version_covers_the_proposed_tier(tmp_path: Path) -> None:
    _root, layout = _tier_workspace(tmp_path)

    slow = curate_decisions.version_of(
        _must_tier(layout, tier="slow"), layout=layout, payload_digest=None
    )
    fast = curate_decisions.version_of(
        _must_tier(layout, tier=_TIER), layout=layout, payload_digest=None
    )

    assert slow.content_digest != fast.content_digest


def test_the_tier_version_covers_the_currently_effective_tier(tmp_path: Path) -> None:
    _root, layout = _tier_workspace(tmp_path)
    before = curate_decisions.version_of(
        _must_tier(layout), layout=layout, payload_digest=None
    )

    _move_effective_tier(layout)
    after = curate_decisions.version_of(
        _must_tier(layout), layout=layout, payload_digest=None
    )

    assert before.content_digest != after.content_digest


class _TierRecorder(_Recorder):
    """Records the config-commit ports beside the section, in call order."""

    def __init__(
        self,
        *,
        sha: str | None = "abc1234",
        plan: object = None,
        refusal: str | None = None,
        on_enter: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(sha=sha)
        self.plan = plan
        self.refusal = refusal
        self.plan_calls: list[tuple[Path, str, str, str | None, bytes]] = []
        self.edit_commits: list[tuple[Path, str, object]] = []
        self._on_enter = on_enter

    def plan_config_commit(
        self,
        root: Path,
        verb: str,
        concept_type: str,
        tier: str | None,
        baseline: bytes,
    ) -> object:
        self.events.append("plan")
        self.plan_calls.append((root, verb, concept_type, tier, baseline))
        return None if self.refusal is None else ConfigCommitRefusal(self.refusal)

    def autocommit_config_edit(
        self, root: Path, message: str, plan: object = None
    ) -> str | None:
        self.events.append("commit_edit")
        self.edit_commits.append((root, message, plan))
        return self._sha

    @contextlib.contextmanager
    def section(self) -> Iterator[None]:
        self.events.append("enter")
        try:
            if self._on_enter is not None:
                self._on_enter()
            yield
        finally:
            self.events.append("exit")

    def ports(self) -> DecisionPorts:
        return dataclasses.replace(
            super().ports(),
            plan_config_commit=self.plan_config_commit,
            autocommit_config_edit=self.autocommit_config_edit,
        )


def _commit_tier(
    root: Path,
    layout: config.WorkspaceLayout,
    preview: TierDecisionPreview,
    recorder: _TierRecorder,
    *,
    surface: str = "curate",
    expected: DecisionVersion | None = None,
) -> curate_decisions.ApplyOutcome:
    return curate_decisions.commit_tier_decision(
        root,
        layout,
        preview,
        ports=recorder.ports(),
        surface=surface,
        expected=expected,
    )


def test_a_tier_commit_writes_the_config_and_commits_inside_one_section(
    tmp_path: Path,
) -> None:
    root, layout = _tier_workspace(tmp_path)
    preview = _must_tier(layout)
    recorder = _TierRecorder()

    outcome = _commit_tier(root, layout, preview, recorder)

    assert outcome == Applied(preview.decision_key, Action.SET_TIER, "abc1234")
    assert _config_text(layout) == preview.prepared.new_config_text
    assert recorder.events == ["enter", "plan", "commit_edit", "exit"]
    assert recorder.edit_commits == [
        (root, f"openkos: set-volatility {_TYPE} -> {_TIER}", None)
    ]


def test_the_config_plan_is_asked_for_the_surface_the_type_the_tier_and_the_baseline(
    tmp_path: Path,
) -> None:
    root, layout = _tier_workspace(tmp_path)
    preview = _must_tier(layout)
    recorder = _TierRecorder()

    _commit_tier(root, layout, preview, recorder, surface="curate")

    assert recorder.plan_calls == [
        (root, "curate", _TYPE, _TIER, preview.prepared.config_bytes)
    ]


def test_the_opaque_config_plan_reaches_the_commit_unchanged(tmp_path: Path) -> None:
    root, layout = _tier_workspace(tmp_path)
    plan = object()
    recorder = _TierRecorder(plan=plan)
    recorder.plan_config_commit = (  # type: ignore[method-assign]
        lambda *args: plan
    )

    _commit_tier(root, layout, _must_tier(layout), recorder)

    assert recorder.edit_commits[0][2] is plan


def test_a_degraded_tier_commit_still_applies_the_tier(tmp_path: Path) -> None:
    root, layout = _tier_workspace(tmp_path)
    preview = _must_tier(layout)

    outcome = _commit_tier(root, layout, preview, _TierRecorder(sha=None))

    assert outcome == Applied(preview.decision_key, Action.SET_TIER, None)
    assert _config_text(layout) == preview.prepared.new_config_text


def test_a_config_commit_refusal_is_raised_with_its_text_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout = _tier_workspace(tmp_path)
    preview = _must_tier(layout)
    before = _snapshot(root)
    recorder = _TierRecorder(refusal="openkos curate: refusing to write -- dirty.")

    with pytest.raises(write_gate.Refused) as caught:
        _commit_tier(root, layout, preview, recorder)

    assert not isinstance(caught.value, write_gate.WriteFailed)
    assert caught.value.message == "openkos curate: refusing to write -- dirty."
    assert _snapshot(root) == before
    assert recorder.edit_commits == []
    assert recorder.events == ["enter", "plan", "exit"]


def test_drift_in_the_config_is_raised_naming_the_surface_before_the_plan(
    tmp_path: Path,
) -> None:
    root, layout = _tier_workspace(tmp_path)
    preview = _must_tier(layout)
    _move_effective_tier(layout)
    before = _snapshot(root)
    recorder = _TierRecorder()

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit_tier(root, layout, preview, recorder, surface="curate")

    assert "openkos.yaml" in caught.value.message
    assert "curate" in caught.value.message
    assert _snapshot(root) == before
    assert recorder.events == ["enter", "exit"]  # the plan port was never asked


@pytest.mark.parametrize("error", [OSError("disk full"), ValueError("bad plan")])
def test_a_failing_tier_write_is_a_typed_write_failure_with_its_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    root, layout = _tier_workspace(tmp_path)
    preview = _must_tier(layout)

    def _boom(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(application_lifecycle, "set_volatility_core", _boom)
    recorder = _TierRecorder()

    with pytest.raises(write_gate.WriteFailed) as caught:
        _commit_tier(root, layout, preview, recorder)

    assert caught.value.operation == "set_tier"
    assert caught.value.subjects == (_TYPE,)
    assert caught.value.error == str(error)
    assert caught.value.__cause__ is error
    assert recorder.edit_commits == []
    assert recorder.events == ["enter", "plan", "exit"]


def test_the_tier_write_goes_through_the_lifecycle_module_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, layout = _tier_workspace(tmp_path)
    preview = _must_tier(layout)
    seen: list[Path] = []

    def _spy(config_path: Path, prepared: object) -> None:
        seen.append(config_path)

    monkeypatch.setattr(application_lifecycle, "set_volatility_core", _spy)

    _commit_tier(root, layout, preview, _TierRecorder())

    assert seen == [layout.config_path]


def test_ports_without_the_config_commit_ports_fall_back_to_a_plain_commit(
    tmp_path: Path,
) -> None:
    root, layout = _tier_workspace(tmp_path)
    preview = _must_tier(layout)
    recorder = _Recorder()

    outcome = curate_decisions.commit_tier_decision(
        root, layout, preview, ports=recorder.ports(), surface="curate"
    )

    assert outcome == Applied(preview.decision_key, Action.SET_TIER, "abc1234")
    assert recorder.commits == [
        (("openkos.yaml",), f"openkos: set-volatility {_TYPE} -> {_TIER}")
    ]


# -- the tier row resolution and token path --------------------------------- #


def _tier_proposal(tier: str = _TIER) -> pq.Proposal:
    return pq.Proposal(
        kind="volatility",
        key_body=pq.volatility_key(_TYPE),
        producer="suggest-volatility/1",
        payload=json.dumps(
            {
                "type_name": _TYPE,
                "current_default": "slow",
                "suggested_tier": tier,
                "rationale": "because",
            }
        ),
        targets=(),
    )


def _tier_seeded(
    tmp_path: Path,
) -> tuple[Path, config.WorkspaceLayout, pq.Proposal, DecisionVersion]:
    root, layout = _tier_workspace(tmp_path)
    proposal = _tier_proposal()
    _enqueue(layout, proposal)
    version = curate_decisions.version_of(
        _must_tier(layout),
        layout=layout,
        payload_digest=pq.payload_digest_for(proposal),
    )
    return root, layout, proposal, version


def test_a_tier_commit_resolves_its_row_as_proposed(tmp_path: Path) -> None:
    root, layout = _tier_workspace(tmp_path)
    proposal = _tier_proposal()
    _enqueue(layout, proposal)

    _commit_tier(root, layout, _must_tier(layout), _TierRecorder())

    assert _row_resolution(layout, proposal) == ("applied", "as_proposed")


def test_a_current_tier_token_commits_and_resolves_the_row(tmp_path: Path) -> None:
    root, layout, proposal, version = _tier_seeded(tmp_path)
    preview = _must_tier(layout)

    outcome = _commit_tier(root, layout, preview, _TierRecorder(), expected=version)

    assert outcome == Applied(proposal.decision_key, Action.SET_TIER, "abc1234")
    assert _config_text(layout) == preview.prepared.new_config_text
    assert curate_queue.open_row_by_key(layout, proposal.decision_key) is None


def test_the_effective_tier_changed_elsewhere_is_stale_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout, proposal, version = _tier_seeded(tmp_path)
    preview = _must_tier(layout)
    _move_effective_tier(layout)
    before = _snapshot(root)
    recorder = _TierRecorder()

    outcome = _commit_tier(root, layout, preview, recorder, expected=version)

    assert outcome == Stale(proposal.decision_key, "content_changed")
    assert recorder.events == []
    assert _snapshot(root) == before


def test_a_tier_row_resolved_elsewhere_is_stale_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout, proposal, version = _tier_seeded(tmp_path)
    preview = _must_tier(layout)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        conn.execute(
            "UPDATE pending_items SET status = 'declined', resolution = 'declined'"
        )
        conn.commit()
    finally:
        conn.close()
    before = _snapshot(root)
    recorder = _TierRecorder()

    outcome = _commit_tier(root, layout, preview, recorder, expected=version)

    assert outcome == Stale(proposal.decision_key, "row_resolved")
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


def test_a_superseded_tier_row_is_stale_and_writes_nothing(tmp_path: Path) -> None:
    root, layout, proposal, version = _tier_seeded(tmp_path)
    preview = _must_tier(layout)
    _enqueue(layout, _tier_proposal("fast"))
    before = _snapshot(root)
    recorder = _TierRecorder()

    outcome = _commit_tier(root, layout, preview, recorder, expected=version)

    assert outcome == Stale(proposal.decision_key, "row_superseded")
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


def test_a_tier_token_without_a_payload_digest_is_rejected_as_resolved(
    tmp_path: Path,
) -> None:
    root, layout, proposal, _ = _tier_seeded(tmp_path)
    version = curate_decisions.version_of(
        _must_tier(layout), layout=layout, payload_digest=None
    )
    before = _snapshot(root)

    outcome = _commit_tier(
        root, layout, _must_tier(layout), _TierRecorder(), expected=version
    )

    assert outcome == Stale(proposal.decision_key, "row_resolved")
    assert _snapshot(root) == before


def test_a_move_after_the_tier_token_check_is_drift_never_stale(
    tmp_path: Path,
) -> None:
    root, layout, _proposal, version = _tier_seeded(tmp_path)
    recorder = _TierRecorder(on_enter=lambda: _move_effective_tier(layout))

    with pytest.raises(write_gate.DriftDetected):
        _commit_tier(root, layout, _must_tier(layout), recorder, expected=version)

    assert recorder.edit_commits == []


# --------------------------------------------------------------------------- #
# The list: every open decision, as data
# --------------------------------------------------------------------------- #


def _typed_concept(
    layout: config.WorkspaceLayout, concept_id: str, okf_type: str
) -> None:
    path = layout.bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {"type": okf_type, "title": concept_id}, f"# {concept_id}\n\nBody.\n"
        ),
        encoding="utf-8",
    )


def _digests(
    layout: config.WorkspaceLayout, *concept_ids: str
) -> tuple[pq.InputDigest, ...]:
    """The input digests a producer records: each concept's current bytes."""
    current = application_pending.current_finding_digest(layout.bundle_dir)
    rows = [(concept_id, current(concept_id)) for concept_id in concept_ids]
    return tuple(pq.InputDigest(ref, digest) for ref, digest in rows if digest)


def _identity_row(
    layout: config.WorkspaceLayout,
    members: tuple[str, ...] = _PAIR,
    *,
    verdict: str | None = "same",
    confidence: float = 0.9,
    note: str = "",
) -> pq.Proposal:
    adjudication = (
        None
        if verdict is None
        else {"verdict": verdict, "confidence": confidence, "rationale": "r" + note}
    )
    return pq.Proposal(
        kind="identity",
        key_body=pq.identity_key(members),
        producer="duplicates/1",
        payload=json.dumps(
            {
                "member_ids": list(members),
                "member_types": ["Concept"] * len(members),
                "okf_type": "Concept",
                "tier": "high",
                "trigger": "stub",
                "adjudication": adjudication,
            }
        ),
        targets=members,
        input_digests=_digests(layout, *members),
    )


def _relation_row(
    layout: config.WorkspaceLayout,
    suggested_type: str = "related_to",
    *,
    source: str = _SRC,
    target: str = _TGT,
) -> pq.Proposal:
    return pq.Proposal(
        kind="relation_type",
        key_body=pq.relation_type_key(source, target),
        producer="suggest-relations/1",
        payload=json.dumps(
            {
                "source_id": source,
                "target_id": target,
                "effective_source_id": source,
                "effective_target_id": target,
                "suggested_type": suggested_type,
                "rationale": "because",
            }
        ),
        targets=(source, target),
        input_digests=_digests(layout, source, target),
    )


def _volatility_row(tier: str = "volatile", default: str = "slow") -> pq.Proposal:
    return pq.Proposal(
        kind="volatility",
        key_body=pq.volatility_key(_TYPE),
        producer="suggest-volatility/1",
        payload=json.dumps(
            {
                "type_name": _TYPE,
                "current_default": default,
                "suggested_tier": tier,
                "rationale": "because",
            }
        ),
        targets=(),
    )


def _contradiction_row() -> pq.Proposal:
    return pq.Proposal(
        kind="contradiction",
        key_body=pq.contradiction_key(_PAIR, None),
        producer="contradictions/1",
        payload=json.dumps(
            {"pair_ids": list(_PAIR), "confidence": 0.5, "rationale": "r"}
        ),
        targets=_PAIR,
    )


def _revision_row() -> pq.Proposal:
    return pq.Proposal(
        kind="revision",
        key_body=pq.revision_key(_PAIR),
        producer="revisions/1",
        payload=json.dumps({"pair_ids": list(_PAIR)}),
        targets=_PAIR,
    )


def _watch_row() -> pq.Proposal:
    return pq.Proposal(
        kind="watch_refusal",
        key_body=pq.watch_refusal_key("notes/big.md"),
        producer="watch/1",
        payload=json.dumps({"inbox_path": "notes/big.md", "reason": "too big"}),
        targets=(),
    )


def _list(layout: config.WorkspaceLayout) -> tuple[DecisionItem, ...]:
    return curate_decisions.list_open_decisions(layout)


def _only(layout: config.WorkspaceLayout) -> DecisionItem:
    items = _list(layout)
    assert len(items) == 1
    return items[0]


def test_every_kind_is_listed_in_kinds_order_then_queue_order(tmp_path: Path) -> None:
    _root, layout = _merge_workspace(tmp_path)
    _concept(layout, "concepts/d", "Another.")
    first = _identity_row(layout, _PAIR)
    second = _identity_row(layout, ("concepts/c", "concepts/d"))
    rows = [
        _watch_row(),
        _revision_row(),
        _contradiction_row(),
        _volatility_row(),
        _relation_row(layout),
        first,
        second,
    ]
    for row in rows:
        _enqueue(layout, row)

    items = _list(layout)

    assert [item.kind for item in items] == [
        "identity",
        "identity",
        "relation_type",
        "volatility",
        "contradiction",
        "revision",
        "watch_refusal",
    ]
    assert [item.decision_key for item in items[:2]] == [
        first.decision_key,
        second.decision_key,
    ]
    assert {item.kind for item in items} == set(pq.KINDS)


def test_an_item_carries_the_handle_the_payload_and_where_it_resolves(
    tmp_path: Path,
) -> None:
    _root, layout = _merge_workspace(tmp_path)
    row = _identity_row(layout)
    _enqueue(layout, row)

    item = _only(layout)

    assert item.decision_key == row.decision_key
    assert item.kind == "identity"
    assert item.targets == _PAIR
    assert item.fresh is True
    assert item.evaluation == "judged_same"
    assert item.payload == json.loads(row.payload)
    assert item.payload_digest == pq.payload_digest_for(row)
    assert item.resolves_via == "openkos adjudicate --apply"
    assert dataclasses.is_dataclass(item)
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.fresh = False  # type: ignore[misc]


def test_an_empty_queue_lists_nothing_and_creates_nothing(tmp_path: Path) -> None:
    root, layout = _merge_workspace(tmp_path)
    before = _snapshot(root)

    assert _list(layout) == ()

    assert _snapshot(root) == before
    assert not layout.findings_db_path.exists()


def test_listing_writes_nothing_prints_nothing_and_needs_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from openkos.application import backends

    root, layout = _merge_workspace(tmp_path)
    for row in (_identity_row(layout), _relation_row(layout), _volatility_row()):
        _enqueue(layout, row)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")

    def _no_client(*args: object, **kwargs: object) -> object:
        raise AssertionError("listing must not construct a model client")

    monkeypatch.setattr(backends, "chat_client", _no_client)
    before = _snapshot(root)
    capsys.readouterr()

    items = _list(layout)

    assert len(items) == 3
    assert _snapshot(root) == before
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# -- the action table: one test per row ------------------------------------- #


def _listed(layout: config.WorkspaceLayout, row: pq.Proposal) -> DecisionItem:
    _enqueue(layout, row)
    return _only(layout)


def test_an_identity_judged_same_offers_merge_keep_distinct_and_skip(
    tmp_path: Path,
) -> None:
    _root, layout = _merge_workspace(tmp_path)

    item = _listed(layout, _identity_row(layout))

    assert item.actions == (Action.MERGE, Action.KEEP_DISTINCT, Action.SKIP)
    assert item.withheld == ()
    assert item.evaluation == "judged_same"


def test_an_identity_without_a_judgment_is_pending_evaluation(tmp_path: Path) -> None:
    _root, layout = _merge_workspace(tmp_path)

    item = _listed(layout, _identity_row(layout, verdict=None))

    assert item.actions == (Action.KEEP_DISTINCT, Action.SKIP)
    assert item.withheld == (WithheldAction(Action.MERGE, "pending_evaluation"),)
    assert item.evaluation == "pending_evaluation"


@pytest.mark.parametrize("confidence", [0.5, 0.0], ids=["judged", "degraded"])
def test_an_identity_judged_uncertain_is_listed_as_judged_uncertain(
    tmp_path: Path, confidence: float
) -> None:
    """An UNCERTAIN verdict, including the fail-closed degrade of a malformed
    reply, is a judgment the model declined to make: it is listed as
    `judged_uncertain`, not as a group still waiting for evaluation."""
    _root, layout = _merge_workspace(tmp_path)

    item = _listed(
        layout, _identity_row(layout, verdict="uncertain", confidence=confidence)
    )

    assert item.actions == (Action.KEEP_DISTINCT, Action.SKIP)
    assert item.withheld == (WithheldAction(Action.MERGE, "judged_uncertain"),)
    assert item.evaluation == "judged_uncertain"


def test_an_identity_with_an_unknown_verdict_stays_pending_evaluation(
    tmp_path: Path,
) -> None:
    _root, layout = _merge_workspace(tmp_path)

    item = _listed(layout, _identity_row(layout, verdict="maybe"))

    assert item.withheld == (WithheldAction(Action.MERGE, "pending_evaluation"),)
    assert item.evaluation == "pending_evaluation"


def test_a_merge_on_an_uncertain_item_is_not_mergeable_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path, verdict="uncertain")
    version = DecisionVersion(
        proposal.decision_key, pq.payload_digest_for(proposal), "x"
    )
    before = _snapshot(root)
    recorder = _Recorder()

    outcome = _apply(root, layout, version, Action.MERGE, recorder)

    assert outcome == NotApplied(Action.MERGE, "not_mergeable")
    assert recorder.events == []
    assert _snapshot(root) == before


def test_an_identity_judged_different_withholds_the_merge(tmp_path: Path) -> None:
    _root, layout = _merge_workspace(tmp_path)

    item = _listed(layout, _identity_row(layout, verdict="different"))

    assert item.actions == (Action.KEEP_DISTINCT, Action.SKIP)
    assert item.withheld == (WithheldAction(Action.MERGE, "judged_different"),)
    assert item.evaluation == "judged_different"


def test_a_cross_type_identity_withholds_the_merge_and_names_the_command(
    tmp_path: Path,
) -> None:
    _root, layout = _merge_workspace(tmp_path)
    _typed_concept(layout, "concepts/e", "Other")

    item = _listed(layout, _identity_row(layout, ("concepts/a", "concepts/e")))

    assert item.actions == (Action.MERGE_GROUP, Action.KEEP_DISTINCT, Action.SKIP)
    assert item.withheld == (WithheldAction(Action.MERGE, "cross_type"),)
    assert "merge --include-cross-type" in item.resolves_via


def test_an_identity_of_more_than_two_members_withholds_the_merge(
    tmp_path: Path,
) -> None:
    _root, layout = _merge_workspace(tmp_path)

    item = _listed(
        layout, _identity_row(layout, ("concepts/a", "concepts/b", "concepts/c"))
    )

    assert item.actions == (Action.MERGE_GROUP, Action.KEEP_DISTINCT, Action.SKIP)
    assert item.withheld == (WithheldAction(Action.MERGE, "more_than_two_members"),)


def test_a_symmetric_relation_offers_relate_and_skip_only(tmp_path: Path) -> None:
    _root, layout = _relate_workspace(tmp_path)

    item = _listed(layout, _relation_row(layout, "related_to"))

    assert item.actions == (Action.RELATE, Action.SKIP)
    assert item.withheld == ()
    assert item.evaluation == "not_applicable"


def test_an_asymmetric_relation_also_offers_the_reversed_edge(tmp_path: Path) -> None:
    _root, layout = _relate_workspace(tmp_path)

    item = _listed(layout, _relation_row(layout, "part_of"))

    assert item.actions == (Action.RELATE, Action.RELATE_REVERSED, Action.SKIP)


def test_a_supersedes_relation_has_no_actions_and_resolves_via_relate(
    tmp_path: Path,
) -> None:
    _root, layout = _relate_workspace(tmp_path)

    item = _listed(layout, _relation_row(layout, "supersedes"))

    assert item.actions == ()
    assert item.withheld == ()
    assert item.resolves_via == f"openkos relate {_SRC} supersedes {_TGT}"


def test_a_relation_with_an_invalid_type_offers_nothing(tmp_path: Path) -> None:
    _root, layout = _relate_workspace(tmp_path)

    item = _listed(layout, _relation_row(layout, "   "))

    assert item.actions == ()
    assert item.withheld == (WithheldAction(Action.RELATE, "no_valid_suggestion"),)


def test_a_volatility_with_a_valid_tier_offers_set_tier_and_skip(
    tmp_path: Path,
) -> None:
    _root, layout = _tier_workspace(tmp_path)

    item = _listed(layout, _volatility_row())

    assert item.actions == (Action.SET_TIER, Action.SKIP)
    assert item.withheld == ()
    assert item.targets == ()
    assert item.fresh is True


def test_a_volatility_with_an_invalid_tier_offers_nothing(tmp_path: Path) -> None:
    _root, layout = _tier_workspace(tmp_path)

    item = _listed(layout, _volatility_row(tier="glacial"))

    assert item.actions == ()
    assert item.withheld == (WithheldAction(Action.SET_TIER, "no_valid_suggestion"),)


def test_a_contradiction_takes_the_finding_actions_and_names_where_it_resolves(
    tmp_path: Path,
) -> None:
    _root, layout = _merge_workspace(tmp_path)

    item = _listed(layout, _contradiction_row())

    assert item.actions == (
        Action.DECLINE,
        Action.RECONCILE,
        Action.SUPERSEDE,
        Action.SUPERSEDE_REVERSED,
        Action.SKIP,
    )
    assert item.withheld == ()
    for place in (
        "openkos contradictions --decline",
        "openkos reconcile",
        "openkos unmerge",
    ):
        assert place in item.resolves_via


def test_an_unreadable_revision_has_no_actions_and_names_its_producer(
    tmp_path: Path,
) -> None:
    _root, layout = _merge_workspace(tmp_path)

    item = _listed(layout, _revision_row())

    assert item.actions == ()
    assert item.resolves_via == "openkos revisions"


def test_a_watch_refusal_has_no_actions_and_names_ingest(tmp_path: Path) -> None:
    _root, layout = _merge_workspace(tmp_path)

    item = _listed(layout, _watch_row())

    assert item.actions == ()
    assert item.resolves_via.startswith("openkos ingest ")
    assert "big.md" in item.resolves_via


# -- staleness is marked, never hidden -------------------------------------- #


def test_an_identity_whose_member_was_edited_is_listed_and_marked_stale(
    tmp_path: Path,
) -> None:
    _root, layout = _merge_workspace(tmp_path)
    _enqueue(layout, _identity_row(layout))
    _edit(layout, _SURVIVOR)

    item = _only(layout)

    assert item.fresh is False
    assert item.actions == (Action.MERGE, Action.KEEP_DISTINCT, Action.SKIP)


def test_a_relation_whose_endpoint_was_edited_is_listed_and_marked_stale(
    tmp_path: Path,
) -> None:
    _root, layout = _relate_workspace(tmp_path)
    _enqueue(layout, _relation_row(layout))
    _edit(layout, _TGT)

    assert _only(layout).fresh is False


def test_a_volatility_whose_effective_tier_moved_is_listed_and_marked_stale(
    tmp_path: Path,
) -> None:
    _root, layout = _tier_workspace(tmp_path)
    _enqueue(layout, _volatility_row())
    _move_effective_tier(layout)

    assert _only(layout).fresh is False


# -- confidence is data, never a ranking or a trust signal ------------------ #


@pytest.mark.parametrize("confidences", [(0.1, 0.9), (0.9, 0.1)])
def test_the_list_order_does_not_depend_on_confidence(
    tmp_path: Path, confidences: tuple[float, float]
) -> None:
    _root, layout = _merge_workspace(tmp_path)
    _concept(layout, "concepts/d", "Another.")
    low_high = [
        _identity_row(layout, _PAIR, confidence=confidences[0]),
        _identity_row(layout, ("concepts/c", "concepts/d"), confidence=confidences[1]),
    ]
    for row in low_high:
        _enqueue(layout, row)

    items = _list(layout)

    assert [item.decision_key for item in items] == [r.decision_key for r in low_high]
    assert [item.payload["adjudication"]["confidence"] for item in items] == list(  # type: ignore[index]
        confidences
    )


def test_no_field_presents_confidence_as_a_trust_or_recommendation() -> None:
    names = {field.name for field in dataclasses.fields(DecisionItem)}

    assert names == {
        "decision_key",
        "kind",
        "targets",
        "fresh",
        "evaluation",
        "actions",
        "withheld",
        "resolves_via",
        "payload",
        "payload_digest",
    }


# --------------------------------------------------------------------------- #
# The token path: preview_decision and apply_decision
# --------------------------------------------------------------------------- #


def _must_decision_preview(
    root: Path,
    layout: config.WorkspaceLayout,
    decision_key: str,
    action: Action,
    **kwargs: object,
) -> curate_decisions.DecisionPreview:
    result = curate_decisions.preview_decision(
        root,
        layout,
        decision_key,
        action,
        **kwargs,  # type: ignore[arg-type]
    )
    assert isinstance(result, curate_decisions.DecisionPreview)
    return result


def _apply(
    root: Path,
    layout: config.WorkspaceLayout,
    version: DecisionVersion,
    action: Action,
    recorder: _Recorder,
    **kwargs: object,
) -> curate_decisions.ApplyOutcome:
    return curate_decisions.apply_decision(
        root,
        layout,
        version,
        action,
        ports=recorder.ports(),
        observer=DecisionObserver(),
        surface="curate",
        **kwargs,  # type: ignore[arg-type]
    )


def _identity_seeded(
    tmp_path: Path, **row: object
) -> tuple[Path, config.WorkspaceLayout, pq.Proposal]:
    root, layout = _merge_workspace(tmp_path)
    proposal = _identity_row(layout, **row)  # type: ignore[arg-type]
    _enqueue(layout, proposal)
    return root, layout, proposal


def test_preview_decision_returns_the_matching_preview_and_its_version(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)

    result = _must_decision_preview(root, layout, proposal.decision_key, Action.MERGE)

    assert isinstance(result.preview, MergeDecisionPreview)
    assert result.version == curate_decisions.version_of(
        result.preview, layout=layout, payload_digest=pq.payload_digest_for(proposal)
    )
    assert result.version.decision_key == proposal.decision_key


def test_preview_decision_covers_relate_reversed_and_set_tier(tmp_path: Path) -> None:
    root, layout = _relate_workspace(tmp_path)
    relation = _relation_row(layout, "part_of")
    tier = _volatility_row()
    _enqueue(layout, relation)
    _enqueue(layout, tier)

    forward = _must_decision_preview(root, layout, relation.decision_key, Action.RELATE)
    reversed_ = _must_decision_preview(
        root, layout, relation.decision_key, Action.RELATE_REVERSED
    )
    set_tier = _must_decision_preview(root, layout, tier.decision_key, Action.SET_TIER)

    assert isinstance(forward.preview, RelateDecisionPreview)
    assert (forward.preview.source_id, forward.preview.target_id) == (_SRC, _TGT)
    assert isinstance(reversed_.preview, RelateDecisionPreview)
    assert (reversed_.preview.source_id, reversed_.preview.target_id) == (_TGT, _SRC)
    assert isinstance(set_tier.preview, TierDecisionPreview)
    assert (set_tier.preview.type_name, set_tier.preview.tier) == (_TYPE, "volatile")
    assert forward.version.content_digest != reversed_.version.content_digest


def test_preview_decision_refuses_what_the_item_does_not_offer(tmp_path: Path) -> None:
    root, layout, proposal = _identity_seeded(tmp_path, verdict=None)

    unjudged = curate_decisions.preview_decision(
        root, layout, proposal.decision_key, Action.MERGE
    )
    skipped = curate_decisions.preview_decision(
        root, layout, proposal.decision_key, Action.SKIP
    )
    wrong_kind = curate_decisions.preview_decision(
        root, layout, proposal.decision_key, Action.SET_TIER
    )

    assert unjudged == NotApplied(Action.MERGE, "not_mergeable")
    assert skipped == NotApplied(Action.SKIP, "skipped")
    assert wrong_kind == NotApplied(Action.SET_TIER, "not_offered")


def test_previewing_a_key_that_never_existed_is_stale_as_unknown(
    tmp_path: Path,
) -> None:
    root, layout = _merge_workspace(tmp_path)
    key = pq.stored_decision_key("identity", pq.identity_key(_PAIR))

    result = curate_decisions.preview_decision(root, layout, key, Action.MERGE)

    assert result == Stale(key, "unknown_key")


def _decline_every_row(layout: config.WorkspaceLayout) -> None:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        conn.execute(
            "UPDATE pending_items SET status = 'declined', resolution = 'declined'"
        )
        conn.commit()
    finally:
        conn.close()


def test_previewing_a_resolved_row_is_stale_as_resolved_not_unknown(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)
    _decline_every_row(layout)

    result = curate_decisions.preview_decision(
        root, layout, proposal.decision_key, Action.MERGE
    )

    assert result == Stale(proposal.decision_key, "row_resolved")


def test_applying_a_key_that_never_existed_is_stale_as_unknown_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)
    version = _must_decision_preview(
        root, layout, proposal.decision_key, Action.MERGE
    ).version
    fabricated = DecisionVersion(
        "identity:never-existed", version.payload_digest, version.content_digest
    )
    before = _snapshot(root)
    recorder = _Recorder()

    outcome = _apply(root, layout, fabricated, Action.MERGE, recorder)

    assert outcome == Stale("identity:never-existed", "unknown_key")
    assert recorder.commits == []
    assert _snapshot(root) == before


def test_a_queue_that_cannot_be_read_does_not_claim_the_key_never_existed(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)
    layout.findings_db_path.write_bytes(b"not a sqlite database" * 64)

    result = curate_decisions.preview_decision(
        root, layout, proposal.decision_key, Action.MERGE
    )

    assert result == Stale(proposal.decision_key, "row_resolved")


def test_the_policy_defaults_and_none_of_its_fields_enters_the_token(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)

    assert DecisionPolicy() == DecisionPolicy(
        no_reconcile=False, reconcile=False, now=None
    )
    plain = _must_decision_preview(root, layout, proposal.decision_key, Action.MERGE)
    forced = _must_decision_preview(
        root,
        layout,
        proposal.decision_key,
        Action.MERGE,
        policy=DecisionPolicy(reconcile=True, now=_NOW),
    )
    skipped = _must_decision_preview(
        root,
        layout,
        proposal.decision_key,
        Action.MERGE,
        policy=DecisionPolicy(no_reconcile=True),
    )

    assert isinstance(forced.preview, MergeDecisionPreview)
    assert forced.preview.reconcile_planned
    assert not plain.preview.reconcile_planned  # type: ignore[union-attr]
    assert plain.version == forced.version == skipped.version
    assert plain.version.token() == forced.version.token()


# -- apply: the stateless round trip ---------------------------------------- #


def test_a_caller_holding_only_the_token_key_and_action_commits_a_merge(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)
    issued = _must_decision_preview(root, layout, proposal.decision_key, Action.MERGE)
    held = (
        issued.version.decision_key,
        issued.version.payload_digest,
        issued.version.content_digest,
    )
    del issued  # nothing from the preview survives but the three strings
    recorder = _Recorder()

    outcome = curate_decisions.apply_decision(
        root,
        layout,
        DecisionVersion(*held),
        Action.MERGE,
        ports=recorder.ports(),
        observer=DecisionObserver(),
        surface="curate",
    )

    assert outcome == Applied(proposal.decision_key, Action.MERGE, "abc1234")
    assert not _absorbed_exists(layout)
    assert curate_queue.open_row_by_key(layout, proposal.decision_key) is None
    assert _row_resolution(layout, proposal)[0] == "applied"


def test_a_token_commits_a_relation_forward_and_reversed(tmp_path: Path) -> None:
    root, layout = _relate_workspace(tmp_path)
    proposal = _relation_row(layout, "part_of")
    _enqueue(layout, proposal)
    version = _must_decision_preview(
        root, layout, proposal.decision_key, Action.RELATE_REVERSED
    ).version

    outcome = _apply(root, layout, version, Action.RELATE_REVERSED, _Recorder())

    assert outcome == Applied(proposal.decision_key, Action.RELATE_REVERSED, "abc1234")
    assert _relations_of(layout, _TGT) != []
    assert _relations_of(layout, _SRC) == []
    assert _row_resolution(layout, proposal) == ("applied", "modified")


def test_a_token_sets_a_tier(tmp_path: Path) -> None:
    root, layout = _tier_workspace(tmp_path)
    proposal = _volatility_row()
    _enqueue(layout, proposal)
    version = _must_decision_preview(
        root, layout, proposal.decision_key, Action.SET_TIER
    ).version
    recorder = _Recorder()

    outcome = _apply(root, layout, version, Action.SET_TIER, recorder)

    assert outcome == Applied(proposal.decision_key, Action.SET_TIER, "abc1234")
    assert recorder.commits == [
        (("openkos.yaml",), f"openkos: set-volatility {_TYPE} -> volatile")
    ]
    assert _row_resolution(layout, proposal) == ("applied", "as_proposed")


def test_a_token_records_keep_distinct_and_resolves_the_row(tmp_path: Path) -> None:
    root, layout, proposal = _identity_seeded(tmp_path, verdict=None)
    version = _must_decision_preview(
        root, layout, proposal.decision_key, Action.KEEP_DISTINCT
    ).version
    recorder = _Recorder()

    outcome = _apply(root, layout, version, Action.KEEP_DISTINCT, recorder)

    assert outcome == Applied(proposal.decision_key, Action.KEEP_DISTINCT, "abc1234")
    assert recorder.commits == [
        (recorder.commits[0][0], "openkos: keep distinct concepts/a/concepts/b")
    ]
    assert curate_queue.open_row_by_key(layout, proposal.decision_key) is None
    assert _absorbed_exists(layout)


# -- apply: what is refused before anything is previewed -------------------- #


def test_a_merge_on_an_unjudged_item_is_not_mergeable_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path, verdict=None)
    version = DecisionVersion(
        proposal.decision_key, pq.payload_digest_for(proposal), "x"
    )
    before = _snapshot(root)
    recorder = _Recorder()

    outcome = _apply(root, layout, version, Action.MERGE, recorder)

    assert outcome == NotApplied(Action.MERGE, "not_mergeable")
    assert recorder.events == []
    assert _snapshot(root) == before


@pytest.mark.parametrize("withheld", ["different", "cross_type", "three_members"])
def test_every_withheld_merge_is_not_mergeable(tmp_path: Path, withheld: str) -> None:
    root, layout = _merge_workspace(tmp_path)
    _typed_concept(layout, "concepts/e", "Other")
    row = {
        "different": _identity_row(layout, verdict="different"),
        "cross_type": _identity_row(layout, ("concepts/a", "concepts/e")),
        "three_members": _identity_row(layout, (*_PAIR, "concepts/c")),
    }[withheld]
    _enqueue(layout, row)
    version = DecisionVersion(row.decision_key, pq.payload_digest_for(row), "x")

    outcome = _apply(root, layout, version, Action.MERGE, _Recorder())

    assert outcome == NotApplied(Action.MERGE, "not_mergeable")


@pytest.mark.parametrize(
    ("row", "action"),
    [
        (lambda layout: _relation_row(layout), Action.MERGE),
        (lambda layout: _relation_row(layout), Action.SET_TIER),
        (lambda layout: _relation_row(layout, "related_to"), Action.RELATE_REVERSED),
        (lambda layout: _relation_row(layout, "supersedes"), Action.RELATE),
        (lambda layout: _volatility_row(), Action.KEEP_DISTINCT),
        (lambda layout: _contradiction_row(), Action.MERGE),
        (lambda layout: _revision_row(), Action.MERGE),
        (lambda layout: _watch_row(), Action.KEEP_DISTINCT),
    ],
)
def test_an_action_outside_the_item_set_is_not_offered_and_writes_nothing(
    tmp_path: Path, row: Callable[[config.WorkspaceLayout], pq.Proposal], action: Action
) -> None:
    root, layout = _relate_workspace(tmp_path)
    _concept(layout, "concepts/c", "x")
    proposal = row(layout)
    _enqueue(layout, proposal)
    version = DecisionVersion(
        proposal.decision_key, pq.payload_digest_for(proposal), "x"
    )
    before = _snapshot(root)
    recorder = _Recorder()

    outcome = _apply(root, layout, version, action, recorder)

    assert outcome == NotApplied(action, "not_offered")
    assert recorder.events == []
    assert _snapshot(root) == before


def test_skip_writes_nothing_and_leaves_the_row_pending(tmp_path: Path) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)
    version = DecisionVersion(
        proposal.decision_key, pq.payload_digest_for(proposal), "x"
    )
    before = _snapshot(root)
    recorder = _Recorder()

    outcome = _apply(root, layout, version, Action.SKIP, recorder)

    assert outcome == NotApplied(Action.SKIP, "skipped")
    assert recorder.events == []
    assert _snapshot(root) == before
    assert _row_resolution(layout, proposal)[0] == "pending"


# -- apply: a stale token writes nothing ------------------------------------ #


def test_a_stale_token_writes_no_concept_no_queue_row_and_no_commit(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)
    version = _must_decision_preview(
        root, layout, proposal.decision_key, Action.MERGE
    ).version
    _edit(layout, _SURVIVOR)
    before = _snapshot(root)
    recorder = _Recorder()

    outcome = _apply(root, layout, version, Action.MERGE, recorder)

    assert outcome == Stale(proposal.decision_key, "content_changed")
    assert _snapshot(root) == before  # no concept, no findings.db row, no sidecar
    assert recorder.commits == []
    assert recorder.events == []


def test_a_row_resolved_since_the_token_is_stale_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)
    version = _must_decision_preview(
        root, layout, proposal.decision_key, Action.MERGE
    ).version
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        conn.execute(
            "UPDATE pending_items SET status = 'declined', resolution = 'declined'"
        )
        conn.commit()
    finally:
        conn.close()
    before = _snapshot(root)
    recorder = _Recorder()

    outcome = _apply(root, layout, version, Action.MERGE, recorder)

    assert outcome == Stale(proposal.decision_key, "row_resolved")
    assert recorder.commits == []
    assert _snapshot(root) == before


def test_a_row_superseded_since_the_token_is_stale_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)
    version = _must_decision_preview(
        root, layout, proposal.decision_key, Action.MERGE
    ).version
    _enqueue(layout, _identity_row(layout, note=" revised"))
    before = _snapshot(root)
    recorder = _Recorder()

    outcome = _apply(root, layout, version, Action.MERGE, recorder)

    assert outcome == Stale(proposal.decision_key, "row_superseded")
    assert recorder.commits == []
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


def test_a_row_whose_inputs_changed_is_stale_and_writes_nothing(tmp_path: Path) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)
    _edit(layout, _SURVIVOR)  # the row was computed against the unedited member
    before = _snapshot(root)
    recorder = _Recorder()

    # The preview agrees with the commit (#1475): no plan for a row it refuses.
    preview = curate_decisions.preview_decision(
        root, layout, proposal.decision_key, Action.MERGE
    )
    # A token issued over the current bytes (an older engine's preview) is
    # refused inside the section all the same.
    outcome = _apply(
        root, layout, _token(root, layout, proposal), Action.MERGE, recorder
    )

    assert preview == Stale(proposal.decision_key, "row_inputs_changed")
    assert outcome == Stale(proposal.decision_key, "row_inputs_changed")
    assert recorder.commits == []
    assert _snapshot(root) == before


def test_a_member_edited_after_a_keep_distinct_token_is_stale_before_the_section(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path, verdict=None)
    version = _must_decision_preview(
        root, layout, proposal.decision_key, Action.KEEP_DISTINCT
    ).version
    _edit(layout, _ABSORBED)
    before = _snapshot(root)
    recorder = _Recorder()

    outcome = _apply(root, layout, version, Action.KEEP_DISTINCT, recorder)

    assert outcome == Stale(proposal.decision_key, "content_changed")
    assert recorder.events == []
    assert _snapshot(root) == before


def test_a_keep_distinct_token_for_a_row_with_stale_inputs_is_stale(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path, verdict=None)
    _edit(layout, _ABSORBED)
    before = _snapshot(root)
    recorder = _Recorder()

    preview = curate_decisions.preview_decision(
        root, layout, proposal.decision_key, Action.KEEP_DISTINCT
    )
    version = curate_decisions.version_of(
        curate_decisions.KeepDistinctDecisionPreview(
            tuple(sorted(proposal.targets)), proposal.decision_key
        ),
        layout=layout,
        payload_digest=pq.payload_digest_for(proposal),
    )
    outcome = _apply(root, layout, version, Action.KEEP_DISTINCT, recorder)

    assert preview == Stale(proposal.decision_key, "row_inputs_changed")
    assert outcome == Stale(proposal.decision_key, "row_inputs_changed")
    assert recorder.commits == []
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


# -- row_inputs_changed on every commit path -------------------------------- #


def test_a_merge_commit_with_a_token_for_a_row_with_stale_inputs_is_stale(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _identity_seeded(tmp_path)
    _edit(layout, _ABSORBED)
    version = _token(root, layout, proposal)
    before = _snapshot(root)
    recorder = _MergeRecorder()

    outcome = _commit(
        root, layout, _must_preview(root, layout), recorder, expected=version
    )

    assert outcome == Stale(proposal.decision_key, "row_inputs_changed")
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


def test_a_relate_commit_with_a_token_for_a_row_with_stale_inputs_is_stale(
    tmp_path: Path,
) -> None:
    root, layout = _relate_workspace(tmp_path)
    proposal = _relation_row(layout)
    _enqueue(layout, proposal)
    _edit(layout, _TGT)
    version = _relate_token(root, layout, proposal)
    before = _snapshot(root)
    recorder = _RelateRecorder()

    outcome = _commit_relate(
        root, layout, _must_relate(root, layout), recorder, expected=version
    )

    assert outcome == Stale(proposal.decision_key, "row_inputs_changed")
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


def test_a_tier_commit_with_a_token_for_a_row_whose_default_moved_is_stale(
    tmp_path: Path,
) -> None:
    root, layout = _tier_workspace(tmp_path)
    proposal = _volatility_row()
    _enqueue(layout, proposal)
    _move_effective_tier(layout)  # the row proposed from "slow"; it is "static" now
    version = curate_decisions.version_of(
        _must_tier(layout),
        layout=layout,
        payload_digest=pq.payload_digest_for(proposal),
    )
    before = _snapshot(root)
    recorder = _TierRecorder()

    outcome = _commit_tier(root, layout, _must_tier(layout), recorder, expected=version)

    assert outcome == Stale(proposal.decision_key, "row_inputs_changed")
    assert recorder.events == ["enter", "exit"]
    assert _snapshot(root) == before


# -- no judgment ------------------------------------------------------------- #


def test_skip_and_listing_succeed_with_no_model_and_start_no_judging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.application import backends
    from openkos.resolution import adjudication

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("the surface must not judge or reach a model")

    monkeypatch.setattr(backends, "chat_client", _forbidden)
    monkeypatch.setattr(adjudication, "adjudicate_candidates", _forbidden)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")
    root, layout, proposal = _identity_seeded(tmp_path, verdict=None)

    item = _only(layout)
    outcome = _apply(
        root,
        layout,
        DecisionVersion(proposal.decision_key, pq.payload_digest_for(proposal), "x"),
        Action.SKIP,
        _Recorder(),
    )

    assert item.evaluation == "pending_evaluation"
    assert outcome == NotApplied(Action.SKIP, "skipped")
    assert _only(layout).evaluation == "pending_evaluation"  # still unjudged


@pytest.mark.parametrize("row", [_watch_row], ids=lambda f: f.__name__)
def test_a_kind_with_no_actions_reports_freshness_as_not_computed(
    tmp_path: Path, row: Callable[[], pq.Proposal]
) -> None:
    _root, layout = _merge_workspace(tmp_path)

    item = _listed(layout, row())

    assert item.actions == ()
    assert item.fresh is None  # never True: nothing was checked


def test_actionable_kinds_keep_a_computed_freshness(tmp_path: Path) -> None:
    _root, layout = _merge_workspace(tmp_path)
    _enqueue(layout, _identity_row(layout))

    assert _only(layout).fresh is True
    _edit(layout, _SURVIVOR)
    assert _only(layout).fresh is False


# --------------------------------------------------------------------------- #
# Consent and bulk eligibility as data (S4b)
# --------------------------------------------------------------------------- #

_KINDS = (
    "identity",
    "relation_type",
    "volatility",
    "contradiction",
    "revision",
    "watch_refusal",
)


def test_the_bulk_eligibility_table_is_pinned() -> None:
    table = curate_decisions.BULK_ELIGIBILITY

    assert dict(table) == {
        "identity": BulkEligibility.NEVER,
        "relation_type": BulkEligibility.SYMMETRIC_ONLY,
        "volatility": BulkEligibility.ALWAYS,
        "contradiction": BulkEligibility.NEVER,
        "revision": BulkEligibility.NEVER,
        "watch_refusal": BulkEligibility.NEVER,
    }
    assert set(table) == set(_KINDS)  # every kind decided, none omitted


def test_the_bulk_eligibility_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        curate_decisions.BULK_ELIGIBILITY["identity"] = (  # type: ignore[index]
            BulkEligibility.ALWAYS
        )


class _Terminal:
    """A TTY thunk that records how often the terminal was consulted."""

    def __init__(self, value: bool) -> None:
        self.value = value
        self.reads = 0

    def __call__(self) -> bool:
        self.reads += 1
        return self.value


@pytest.mark.parametrize(
    ("auto", "tty", "expected", "reads"),
    [
        (True, True, "granted", 0),  # --auto never looks at the terminal
        (True, False, "granted", 0),
        (False, True, "ask", 1),
        (False, False, "refused", 1),
    ],
)
def test_spend_consent_follows_auto_then_the_terminal(
    auto: bool, tty: bool, expected: str, reads: int
) -> None:
    terminal = _Terminal(tty)

    assert spend_consent(auto=auto, interactive=terminal) == expected
    assert terminal.reads == reads


@pytest.mark.parametrize(
    ("writes", "tty", "accepted", "unattended", "expected"),
    [
        (row, tty, accepted, unattended, expected)
        for row, tty, accepted, unattended, expected in [
            (False, False, False, False, "walk"),  # a read-only stage never refuses
            (False, True, False, False, "walk"),
            (True, True, False, False, "walk"),  # a terminal can ask per item
            (True, False, False, False, "refused"),  # a pipe has no channel
            (True, False, True, False, "walk"),  # --accept names the consent
            (True, False, False, True, "walk"),  # the unattended identity pass
            (True, False, True, True, "walk"),
            (True, True, True, True, "walk"),
        ]
    ],
)
def test_write_walk_consent_refuses_only_a_write_on_a_pipe_with_no_channel(
    writes: bool, tty: bool, accepted: bool, unattended: bool, expected: str
) -> None:
    terminal = _Terminal(tty)

    assert (
        write_walk_consent(
            writes=writes,
            interactive=terminal,
            accepted=accepted,
            unattended_identity=unattended,
        )
        == expected
    )
    assert terminal.reads == (1 if writes else 0)  # read-only stages never probe


@pytest.mark.parametrize(
    ("kind", "accepted", "asymmetric", "tty", "expected", "reads"),
    [
        # Not accepted: always ask, never look at the terminal.
        ("volatility", False, False, True, "ask", 0),
        ("relation_type", False, True, False, "ask", 0),
        # Accepted and bulk-eligible: the answer is bulk.
        ("volatility", True, False, True, "bulk", 0),
        ("volatility", True, False, False, "bulk", 0),
        ("relation_type", True, False, True, "bulk", 0),
        ("relation_type", True, False, False, "bulk", 0),
        # Accepted but exempt (an asymmetric relation): ask on a terminal,
        # withheld on a pipe, because there is no channel to ask on.
        ("relation_type", True, True, True, "ask", 1),
        ("relation_type", True, True, False, "withheld", 1),
        # The per-item exemption (#508) holds for an always-bulk kind too.
        ("volatility", True, True, True, "ask", 1),
        ("volatility", True, True, False, "withheld", 1),
        # A kind that is never bulk is never bulk, accepted or not.
        ("contradiction", True, False, True, "ask", 1),
        ("revision", True, False, False, "withheld", 1),
    ],
)
def test_item_consent_table(
    kind: str,
    accepted: bool,
    asymmetric: bool,
    tty: bool,
    expected: str,
    reads: int,
) -> None:
    terminal = _Terminal(tty)

    assert (
        item_consent(
            kind,  # type: ignore[arg-type]
            accepted=accepted,
            asymmetric=asymmetric,
            interactive=terminal,
        )
        == expected
    )
    assert terminal.reads == reads


@pytest.mark.parametrize(
    ("accepted", "asymmetric", "tty"),
    list(itertools.product((True, False), repeat=3)),
)
def test_identity_is_always_asked_whatever_is_set(
    accepted: bool, asymmetric: bool, tty: bool
) -> None:
    terminal = _Terminal(tty)

    assert (
        item_consent(
            "identity",
            accepted=accepted,
            asymmetric=asymmetric,
            interactive=terminal,
        )
        == "ask"
    )
    assert terminal.reads == 0  # the verdict is data; the terminal is not needed


@pytest.mark.parametrize(
    ("accepted", "tty"), list(itertools.product((True, False), repeat=2))
)
def test_an_asymmetric_relation_is_never_bulk(accepted: bool, tty: bool) -> None:
    verdict = item_consent(
        "relation_type",
        accepted=accepted,
        asymmetric=True,
        interactive=_Terminal(tty),
    )

    assert verdict in {"ask", "withheld"}


# -- public payload readers (the facade builds typed details from these) ----- #


def _relation_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_id": "people/ana",
        "target_id": "teams/core",
        "effective_source_id": "people/ana",
        "effective_target_id": "teams/core",
        "suggested_type": "member_of",
        "rationale": "Ana sits on the core team.",
    }
    payload.update(overrides)
    return payload


def test_relation_fields_reads_a_valid_payload_and_rejects_unusable_ones() -> None:
    assert curate_decisions.relation_fields(_relation_payload()) == (
        "people/ana",
        "teams/core",
        "people/ana",
        "teams/core",
        "member_of",
        "Ana sits on the core team.",
    )
    assert (
        curate_decisions.relation_fields(_relation_payload(suggested_type="")) is None
    )
    assert curate_decisions.relation_fields(_relation_payload(rationale=7)) is None
    assert curate_decisions.relation_fields({}) is None


def _identity_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "member_ids": ["people/ana", "people/anna"],
        "member_types": ["Person", "Person"],
        "okf_type": "Person",
        "tier": "high",
        "trigger": "title",
        "adjudication": {
            "verdict": "same",
            "confidence": 0.9,
            "rationale": "Same Ana.",
        },
    }
    payload.update(overrides)
    return payload


def test_identity_detail_fields_reads_a_judged_and_an_unjudged_payload() -> None:
    targets = ("t/one", "t/two")

    assert curate_decisions.identity_detail_fields(_identity_payload(), targets) == (
        ("people/ana", "people/anna"),
        "Person",
        "high",
        "same",
        "Same Ana.",
    )
    assert curate_decisions.identity_detail_fields(
        _identity_payload(adjudication=None), targets
    ) == (("people/ana", "people/anna"), "Person", "high", None, None)


def test_identity_detail_fields_falls_back_to_the_row_targets_for_members() -> None:
    targets = ("t/one", "t/two")

    fields = curate_decisions.identity_detail_fields(
        _identity_payload(member_ids="not-a-list"), targets
    )

    assert fields is not None
    assert fields[0] == targets


def test_identity_detail_fields_ignores_an_unknown_verdict_and_never_reads_confidence() -> (
    None
):
    fields = curate_decisions.identity_detail_fields(
        _identity_payload(adjudication={"verdict": "maybe", "confidence": 0.1}),
        ("a", "b"),
    )

    assert fields is not None
    assert fields[3:] == (None, None)
    assert len(fields) == 5  # no confidence slot


@pytest.mark.parametrize(
    "overrides", [{"okf_type": 3}, {"tier": "bogus"}, {"tier": None}]
)
def test_identity_detail_fields_is_none_for_an_unparsable_payload(
    overrides: dict[str, object],
) -> None:
    assert (
        curate_decisions.identity_detail_fields(
            _identity_payload(**overrides), ("a", "b")
        )
        is None
    )


def test_tier_detail_fields_reads_a_valid_payload_and_rejects_unusable_ones() -> None:
    payload: dict[str, object] = {
        "type_name": "Person",
        "current_default": "slow",
        "suggested_tier": "static",
        "rationale": "Names do not change.",
    }

    assert curate_decisions.tier_detail_fields(payload) == (
        "Person",
        "slow",
        "static",
        "Names do not change.",
    )
    assert (
        curate_decisions.tier_detail_fields({**payload, "suggested_tier": "daily"})
        is None
    )
    assert curate_decisions.tier_detail_fields({**payload, "rationale": None}) is None
    assert curate_decisions.tier_detail_fields({}) is None
