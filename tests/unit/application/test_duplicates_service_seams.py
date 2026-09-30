"""Absence guards and service-level tests for the `duplicates` application-service
extraction (issue #1168), the `test_ingest_service_seams.py` pattern.

The names that moved off `openkos.cli.main` are deliberately never aliased
back, so a stale `monkeypatch.setattr("openkos.cli.main.<name>", ...)` raises
`AttributeError` under pytest's default `raising=True` instead of silently
patching a name nothing reads. The service tests run it with an explicit root
while the process sits in an unrelated directory, which is what proves it
never reads the current directory.
"""

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openkos import config
from openkos.application import duplicates_service
from openkos.bundle import decisions as bundle_decisions
from openkos.cli import main as cli_main
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier


@pytest.mark.parametrize("name", ["_validated_identity_members"])
def test_moved_duplicates_names_no_longer_live_on_cli_main(name: str) -> None:
    assert not hasattr(cli_main, name)


def test_the_moved_names_live_in_the_application_layer() -> None:
    assert callable(duplicates_service.report_duplicates)
    assert callable(duplicates_service.record_identity_ruling)
    assert callable(duplicates_service.list_kept_distinct)
    assert callable(duplicates_service.validated_identity_members)
    assert issubclass(
        duplicates_service.InvalidMembers, duplicates_service.DuplicatesRefused
    )


def test_duplicates_verb_is_a_thin_adapter_that_delegates_to_the_service() -> None:
    source = inspect.getsource(cli_main.duplicates)
    assert "duplicates_service.report_duplicates(" in source
    assert "duplicates_service.record_identity_ruling(" in source
    assert "application_pending" not in source
    assert "bundle_decisions" not in source
    assert "config.require_workspace(root)" not in source


def test_apply_identity_decision_is_an_adapter_over_the_service() -> None:
    """`_apply_identity_decision` stays on `cli.main` because the interactive
    walks (`adjudicate --apply`, `curate`) record a decline through it, but it
    must hand the write to the service rather than carry a second copy."""
    source = inspect.getsource(cli_main._apply_identity_decision)
    assert "duplicates_service.apply_identity_decision(" in source
    assert "write_identity_decisions" not in source


# -- The service, without the CLI -------------------------------------------


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "bundle").mkdir(parents=True)
    (root / "bundle" / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "bundle" / "log.md").write_text("# Log\n", encoding="utf-8")
    (root / "openkos.yaml").write_text("model: llama3.1\n", encoding="utf-8")
    return root


def _elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "unrelated-cwd"
    other.mkdir()
    monkeypatch.chdir(other)


def _group(*members: str, trigger: str = "t") -> CandidateGroup:
    return CandidateGroup(
        okf_type="Event", member_ids=members, tier=Tier.HIGH, trigger=trigger
    )


def test_a_non_workspace_root_is_refused_with_the_exact_text(tmp_path: Path) -> None:
    reason = config.require_workspace(tmp_path)
    expected = f"openkos duplicates: refusing to run -- {reason}."

    with pytest.raises(duplicates_service.NotAWorkspace) as report:
        duplicates_service.report_duplicates(
            tmp_path,
            include_deprecated=False,
            find_candidates_report=lambda *a, **k: CandidateGroupReport(),
        )
    with pytest.raises(duplicates_service.NotAWorkspace) as ruling:
        duplicates_service.record_identity_ruling(
            tmp_path, ["a/x", "a/y"], flag="--keep-distinct", target_state="declined"
        )
    with pytest.raises(duplicates_service.NotAWorkspace) as listing:
        duplicates_service.list_kept_distinct(tmp_path)

    assert report.value.message == expected
    assert ruling.value.message == expected
    assert listing.value.message == expected


def test_the_report_walks_the_explicit_root_and_forwards_the_deprecated_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _elsewhere(tmp_path, monkeypatch)
    seen: dict[str, object] = {}

    def _find(bundle_dir: Path, **kwargs: object) -> CandidateGroupReport:
        seen["bundle_dir"] = bundle_dir
        seen["kwargs"] = kwargs
        return CandidateGroupReport(
            groups=(_group("events/a", "events/b"),), produced=80, retained=1
        )

    outcome = duplicates_service.report_duplicates(
        root, include_deprecated=True, find_candidates_report=_find
    )

    assert seen["bundle_dir"] == root / "bundle"
    assert seen["kwargs"] == {"include_deprecated": True}
    assert [g.member_ids for g in outcome.groups] == [("events/a", "events/b")]
    assert outcome.suppressed == 0
    assert outcome.truncation_notice == "1 of 80 candidate group(s) shown (cap reached)"


def test_a_ruled_group_is_hidden_counted_and_its_truncation_accounting_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _elsewhere(tmp_path, monkeypatch)
    groups = (_group("events/a", "events/b"), _group("events/c", "events/d"))
    report = CandidateGroupReport(groups=groups, produced=9, retained=2)
    duplicates_service.record_identity_ruling(
        root, ["events/b", "events/a"], flag="--keep-distinct", target_state="declined"
    )

    outcome = duplicates_service.report_duplicates(
        root, include_deprecated=False, find_candidates_report=lambda *a, **k: report
    )

    assert [g.member_ids for g in outcome.groups] == [("events/c", "events/d")]
    assert outcome.suppressed == 1
    # The cap describes what the corpus PRODUCED, never what was hidden after.
    assert outcome.truncation_notice == "2 of 9 candidate group(s) shown (cap reached)"


def test_the_ruling_is_canonical_keyed_and_stamped_by_the_injected_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _elsewhere(tmp_path, monkeypatch)
    stamp = datetime(2031, 4, 5, 6, 7, 8, tzinfo=UTC)

    ruling = duplicates_service.record_identity_ruling(
        root,
        [" ./events//zulu ", "events/afg", "events/afg"],
        flag="--keep-distinct",
        target_state="declined",
        clock=lambda: stamp,
    )

    assert ruling.members == ("events/afg", "events/zulu")
    assert ruling.rel_path == "bundle/.state/decisions/events/afg.decisions.okf"
    stored = bundle_decisions.read_identity_decisions("events/afg", root / "bundle")
    assert [(r.member_ids, r.state, r.decided_at) for r in stored] == [
        (("events/afg", "events/zulu"), "declined", stamp.isoformat())
    ]
    assert not (root / ".openkos" / "findings.db").exists(), (
        "a ruling never needs a findings row behind it"
    )


def test_reopening_replaces_the_record_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _elsewhere(tmp_path, monkeypatch)
    for state in ("declined", "open", "declined"):
        duplicates_service.record_identity_ruling(
            root, ["events/a", "events/b"], flag="--x", target_state=state
        )

    stored = bundle_decisions.read_identity_decisions("events/a", root / "bundle")

    assert [(r.member_ids, r.state) for r in stored] == [
        (("events/a", "events/b"), "declined")
    ]


@pytest.mark.parametrize(
    ("members", "message"),
    [
        (
            ["events/only"],
            "openkos duplicates: refusing to run -- --keep-distinct needs at "
            "least two distinct concept ids; repeat the flag once per member.",
        ),
        (
            ["events/a", "events/a", "  "],
            "openkos duplicates: refusing to run -- --keep-distinct needs at "
            "least two distinct concept ids; repeat the flag once per member.",
        ),
        (
            ["/etc/passwd", "events/a"],
            "openkos duplicates: refusing to run -- --keep-distinct "
            "'/etc/passwd' must be a relative concept-id, not absolute.",
        ),
    ],
)
def test_invalid_members_are_refused_before_anything_is_written(
    members: list[str], message: str, tmp_path: Path
) -> None:
    root = _workspace(tmp_path)

    with pytest.raises(duplicates_service.InvalidMembers) as caught:
        duplicates_service.record_identity_ruling(
            root, members, flag="--keep-distinct", target_state="declined"
        )

    assert caught.value.message == message
    assert not (root / "bundle" / ".state").exists()


def test_kept_distinct_listing_is_sorted_by_key_and_hides_reopened_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _elsewhere(tmp_path, monkeypatch)
    for members in (("events/a", "events/b"), ("events/c", "events/d")):
        duplicates_service.record_identity_ruling(
            root, list(members), flag="--x", target_state="declined"
        )
    duplicates_service.record_identity_ruling(
        root, ["events/c", "events/d"], flag="--x", target_state="open"
    )
    duplicates_service.record_identity_ruling(
        root, ["events/e", "events/f"], flag="--x", target_state="declined"
    )

    listed = duplicates_service.list_kept_distinct(root)

    keys = [r.decision_key for r in listed]
    assert keys == sorted(keys)
    assert sorted(r.member_ids for r in listed) == [
        ("events/a", "events/b"),
        ("events/e", "events/f"),
    ]


def test_the_direct_writer_sorts_members_for_walks_that_bypass_validation(
    tmp_path: Path,
) -> None:
    """The interactive walks record a decline through `apply_identity_decision`
    without `validated_identity_members`, so the writer itself must key and
    own the ruling by the sorted members, whatever order it was handed."""
    root = _workspace(tmp_path)
    layout = config.WorkspaceLayout(root)

    rel_path = duplicates_service.apply_identity_decision(
        layout, ("events/zulu", "events/afg"), target_state="declined"
    )

    assert rel_path == "bundle/.state/decisions/events/afg.decisions.okf"
    (record,) = bundle_decisions.read_identity_decisions("events/afg", root / "bundle")
    assert record.member_ids == ("events/afg", "events/zulu")
    assert record.decision_key == bundle_decisions.identity_decision_key_for(
        ("events/afg", "events/zulu")
    )


def test_warnings_reach_the_callback_instead_of_a_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path)
    _elsewhere(tmp_path, monkeypatch)
    duplicates_service.record_identity_ruling(
        root, ["events/a", "events/b"], flag="--x", target_state="declined"
    )
    sidecar = bundle_decisions.decisions_path_for("events/a", root / "bundle")
    text = sidecar.read_text(encoding="utf-8")
    sidecar.write_text(
        text.replace(
            "schema:", "- decision_key: k\n  member_ids:\n  - only-one\nschema:", 1
        ),
        encoding="utf-8",
    )
    notes: list[str] = []

    listed = duplicates_service.list_kept_distinct(root, on_warning=notes.append)

    assert [r.member_ids for r in listed] == [("events/a", "events/b")]
    assert notes == [
        f"openkos: warning -- 1 malformed identity decision record(s) in "
        f"{sidecar}; those groups will be offered again."
    ]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
