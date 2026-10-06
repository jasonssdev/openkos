"""An imported group is still offered to a human in `curate` Identity, one item
at a time, and is neither merged by `--auto-merge` nor swept into the
accept-recommended answer (okf-import, #1314; ADR-0050 item 7).

The predicate and the planners are proven in
`tests/unit/application/test_import_entity_resolution.py`; this module proves
what only the command can: the offer's size, the per-item prompt, the
automatic pass's commits. No test reaches a model: the judge and the candidate
finder are the stubs of the sibling `--auto-merge` module.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.resolution.candidates import CandidateGroup, Tier
from tests.unit.cli.test_curate_accept_recommended import (
    _OFFER,
    _script,
    _walk_prompts,
)
from tests.unit.cli.test_curate_auto_merge import (
    _FOO,
    _SAME,
    _build,
    _commit_count,
    _present,
    _simulate_tty,
)

runner = CliRunner()

_BASE = "imports/demo/concepts/bar"
_IMPORTED = CandidateGroup(
    okf_type="Concept",
    member_ids=(_BASE, f"{_BASE}-2"),
    tier=Tier.HIGH,
    trigger="key",
    member_types=("Concept", "Concept"),
)
_VERDICTS = {_FOO.member_ids: _SAME, _IMPORTED.member_ids: _SAME}
_PROMPT = f"Merge {_BASE}-2 into {_BASE}"


def test_the_offer_covers_the_local_group_only_and_the_imported_one_keeps_its_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(tmp_path, monkeypatch, [_FOO, _IMPORTED], _VERDICTS)
    _simulate_tty(monkeypatch)
    asked = _script(monkeypatch, ["y", "s"])

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert asked[0] == _OFFER.format(n=1)
    assert not _present(root, "concepts/foo-2")
    assert _walk_prompts(asked) == [_PROMPT]
    assert _present(root, f"{_BASE}-2")
    assert _present(root, _BASE)


def test_a_human_can_accept_the_imported_group_in_its_per_item_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(tmp_path, monkeypatch, [_IMPORTED], _VERDICTS)
    _simulate_tty(monkeypatch)
    asked = _script(monkeypatch, ["y"])
    before = _commit_count(root)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert not any(a.startswith("Accept all") for a in asked)
    assert _walk_prompts(asked) == [_PROMPT]
    assert not _present(root, f"{_BASE}-2")
    assert _present(root, _BASE)
    assert _commit_count(root) == before + 1


def test_auto_merge_merges_the_local_pair_and_leaves_the_imported_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(tmp_path, monkeypatch, [_FOO, _IMPORTED], _VERDICTS)
    before = _commit_count(root)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert not _present(root, "concepts/foo-2")
    assert _present(root, f"{_BASE}-2")
    assert _present(root, _BASE)
    assert _commit_count(root) == before + 1


def test_auto_merge_alone_never_merges_an_imported_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _judge = _build(tmp_path, monkeypatch, [_IMPORTED], _VERDICTS)
    before = _commit_count(root)

    result = runner.invoke(app, ["curate", "--auto", "--auto-merge"])

    assert result.exit_code == 0, result.stderr
    assert _present(root, f"{_BASE}-2")
    assert _commit_count(root) == before
