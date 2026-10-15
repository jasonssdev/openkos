"""The `cli/main.py` delegators over `application/identity_adjudication.py`
keep their old names and their patch seams (the `judging-application-service`
change, S1): a test that patches `_commit_section_for` or `_echo_stderr` still
intercepts, and the advisories render byte for byte from `PersistResult`."""

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos import config, lock
from openkos.cli import main
from openkos.model import okf
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict
from openkos.resolution.candidates import CandidateGroup, Tier

_MEMBERS = ("concepts/a", "concepts/b")


def _layout(root: Path) -> config.WorkspaceLayout:
    for member in _MEMBERS:
        path = root / "bundle" / f"{member}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            okf.dump_frontmatter({"type": "Concept", "title": member}, "Body.\n"),
            encoding="utf-8",
        )
    return config.WorkspaceLayout(root)


def _results() -> list[AdjudicatedCandidate]:
    group = CandidateGroup(
        okf_type="Concept", member_ids=_MEMBERS, tier=Tier.HIGH, trigger="alpha"
    )
    return [
        AdjudicatedCandidate(
            candidate=group, verdict=Verdict.SAME, confidence=0.9, rationale="Same."
        )
    ]


def test_the_persist_delegator_enters_the_section_the_cli_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    entered: list[Path] = []

    @contextlib.contextmanager
    def _section() -> Iterator[None]:
        yield

    def _section_for(root: Path):  # type: ignore[no-untyped-def]
        entered.append(root)
        return _section

    monkeypatch.setattr("openkos.cli.main._commit_section_for", _section_for)

    main._persist_adjudications(layout, _results(), include_confidential=False)

    assert entered == [tmp_path]


def test_the_persist_delegator_asks_for_no_section_when_there_is_nothing_to_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)

    def _unexpected(_root: Path) -> None:
        raise AssertionError("no section is needed for an empty batch")

    monkeypatch.setattr("openkos.cli.main._commit_section_for", _unexpected)

    main._persist_adjudications(layout, [], include_confidential=False)


def test_the_persist_delegator_renders_the_busy_advisory_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = _layout(tmp_path)

    class _Busy:
        def __enter__(self) -> None:
            raise lock.WorkspaceBusyError("held by pid 7")

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(
        "openkos.cli.main._commit_section_for", lambda _root: lambda: _Busy()
    )

    main._persist_adjudications(
        layout, _results(), include_confidential=False, surface="curate"
    )

    assert capsys.readouterr().err == (
        "openkos curate: warning -- the workspace is busy, so the adjudication "
        "verdicts were not persisted (held by pid 7); the next run will "
        "re-judge them.\n"
    )


def test_the_persist_delegator_renders_the_store_advisory_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = _layout(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr("openkos.state.adjudications.record_adjudications", _boom)

    main._persist_adjudications(layout, _results(), include_confidential=False)

    assert capsys.readouterr().err == (
        "openkos adjudicate: warning -- failed to persist adjudication verdicts "
        "(disk full); the next run will re-judge them.\n"
    )


def test_the_partition_delegator_warns_through_echo_stderr_unless_probing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    layout.findings_db_path.parent.mkdir(parents=True, exist_ok=True)
    layout.findings_db_path.write_bytes(b"this is not a sqlite database" * 50)
    echoed: list[str] = []
    monkeypatch.setattr("openkos.cli.main._echo_stderr", echoed.append)
    group = _results()[0].candidate

    main._partition_adjudication_serves(layout, [group], include_confidential=False)
    assert len(echoed) == 1
    assert echoed[0].startswith("openkos adjudicate: warning -- failed to read")

    main._partition_adjudication_serves(
        layout, [group], include_confidential=False, warn_on_failure=False
    )
    assert len(echoed) == 1


def test_the_old_names_still_resolve_on_cli_main() -> None:
    from openkos.application import identity_adjudication as ia

    assert main.AdjudicationServes is ia.AdjudicationServes
    assert callable(main._partition_adjudication_serves)
    assert callable(main._persist_adjudications)
    assert callable(main._reassemble_adjudications)
