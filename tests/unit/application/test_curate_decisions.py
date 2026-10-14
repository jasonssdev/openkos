"""Unit tests for `openkos.application.curate_decisions`, the curation
decision surface (the `curate-decisions-as-data` change, ADR-0053).

This module grows slice by slice. It holds the typed write failure, the
version token, the outcome types, the ports and observer, and the
keep-distinct and skip decisions. Every test drives the real function with
recording fakes for the effects the service does not own (the auto-commit and
the commit section), never with a mock of the function under test.
"""

import contextlib
import dataclasses
import inspect
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from openkos import config
from openkos.application import commit_phase, curate_decisions, write_gate
from openkos.application.curate_decisions import (
    Action,
    Applied,
    DecisionObserver,
    DecisionPorts,
    DecisionVersion,
    NotApplied,
    Stale,
)
from openkos.bundle import decisions as bundle_decisions

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
