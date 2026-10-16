"""Unit tests for committing an identity group
(`commit_merge_group_decision`, the `identity-groups-decision` change,
ADR-0063): N-1 pairwise steps under one commit section, one auto-commit, and
the group's row resolved `applied`.

Every test drives the real function over a real workspace with recording
fakes for the effects the service does not own (the auto-commit and the commit
section), never a mock of the function under test.
"""

import contextlib
import dataclasses
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest

from openkos import config, lock
from openkos.application import (
    curate_decisions,
    curate_queue,
    lock_wait,
    unmerge_service,
    write_gate,
)
from openkos.application import lifecycle as application_lifecycle
from openkos.application.curate_decisions import (
    Action,
    Applied,
    DecisionObserver,
    DecisionPorts,
    DecisionVersion,
    GroupSelection,
    MergeGroupDecisionPreview,
    NotApplied,
    Stale,
)
from openkos.bundle import ledger as bundle_ledger
from openkos.model import okf
from openkos.resolution.adjudication import Verdict
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.application.curation_support import CommitRecorder
from tests.unit.application.group_support import (
    A,
    B,
    C,
    D,
    enqueue,
    group_of,
    identity_row,
    make_workspace,
    selection_of,
    snapshot,
)


class _Recorder:
    """Records the commit port and the order of section entries, with a hook
    that fires as the section opens."""

    def __init__(
        self, *, sha: str | None = "abc1234", on_enter: Callable[[], None] | None = None
    ) -> None:
        self.events: list[str] = []
        self.commits: list[tuple[Sequence[str], str]] = []
        self.warnings: list[str] = []
        self._sha = sha
        self._on_enter = on_enter

    def autocommit(self, root: Path, paths: Sequence[str], message: str) -> str | None:
        self.events.append("autocommit")
        self.commits.append((tuple(paths), message))
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
        return DecisionPorts(
            autocommit=self.autocommit,
            commit_section=self.section,
            on_warning=self.warnings.append,
        )


class _Observer(DecisionObserver):
    def __init__(self) -> None:
        self.missing: list[tuple[str, str]] = []

    def merge_member_missing(self, survivor: str, absorbed: str) -> None:
        self.missing.append((survivor, absorbed))


@dataclasses.dataclass
class _Seed:
    root: Path
    layout: config.WorkspaceLayout
    proposal: pq.Proposal
    preview: MergeGroupDecisionPreview
    version: DecisionVersion

    @property
    def key(self) -> str:
        return self.proposal.decision_key


def _seed(
    tmp_path: Path,
    selection: GroupSelection,
    *,
    types: dict[str, str] | None = None,
    members: tuple[str, ...] = (A, B, C),
) -> _Seed:
    """A workspace, an open SAME row over `members` and the preview and
    version of `selection` against it."""
    root, layout = make_workspace(tmp_path, types or {m: "Concept" for m in members})
    proposal = identity_row(layout, members)
    enqueue(layout, proposal)
    preview = curate_decisions.preview_merge_group(
        root, layout, group_of(*members), verdict=Verdict.SAME, selection=selection
    )
    assert isinstance(preview, MergeGroupDecisionPreview), preview
    version = curate_decisions.version_of(
        preview, layout=layout, payload_digest=pq.payload_digest_for(proposal)
    )
    return _Seed(root, layout, proposal, preview, version)


def _commit(
    seed: _Seed,
    recorder: _Recorder,
    *,
    version: DecisionVersion | None = None,
    observer: DecisionObserver | None = None,
    ports: DecisionPorts | None = None,
) -> curate_decisions.ApplyOutcome:
    return curate_decisions.commit_merge_group_decision(
        seed.root,
        seed.layout,
        seed.preview,
        ports=ports or recorder.ports(),
        observer=observer or DecisionObserver(),
        surface="api",
        expected=version or seed.version,
    )


def _exists(seed: _Seed, concept_id: str) -> bool:
    return (seed.layout.bundle_dir / f"{concept_id}.md").exists()


def _absorbed_in_order(seed: _Seed, survivor: str) -> list[str]:
    return [
        entry.absorbed_id
        for entry in bundle_ledger.read_entries(survivor, seed.layout.bundle_dir)
    ]


def _edit(seed: _Seed, concept_id: str) -> None:
    path = seed.layout.bundle_dir / f"{concept_id}.md"
    path.write_text(path.read_text("utf-8") + "\nEdited meanwhile.\n", encoding="utf-8")


def _row_state(seed: _Seed) -> tuple[str, str | None]:
    conn = derived.open_derived_connection(seed.layout.findings_db_path)
    try:
        [item] = [i for i in pq.all_items(conn) if i.decision_key == seed.key]
    finally:
        conn.close()
    return item.status, item.resolution


# -- 2.1.1 the commit -------------------------------------------------------- #


def test_a_group_of_three_all_marked_merges_into_the_survivor_in_ascending_order(
    tmp_path: Path,
) -> None:
    seed = _seed(tmp_path, selection_of(B, A, B, C))

    outcome = _commit(seed, _Recorder())

    assert outcome == Applied(
        seed.key,
        Action.MERGE_GROUP,
        "abc1234",
        merged=((B, A), (B, C)),
        kept_distinct=(),
    )
    assert not _exists(seed, A)
    assert not _exists(seed, C)
    assert _exists(seed, B)
    assert _absorbed_in_order(seed, B) == [A, C]


# -- 2.1.2 byte parity ------------------------------------------------------- #


def test_unmerging_each_step_newest_first_restores_every_concept_byte_for_byte(
    tmp_path: Path,
) -> None:
    seed = _seed(tmp_path, selection_of(B, A, B, C))
    before = {
        concept_id: (seed.layout.bundle_dir / f"{concept_id}.md").read_bytes()
        for concept_id in (A, B, C)
    }
    _commit(seed, _Recorder())

    for absorbed in (C, A):
        unmerge_service.unmerge_concept(
            seed.root,
            B,
            absorbed,
            unmerge_service.UnmergePolicy(auto=True),
            ports=unmerge_service.UnmergePorts(autocommit=CommitRecorder()),
        )

    after = {
        concept_id: (seed.layout.bundle_dir / f"{concept_id}.md").read_bytes()
        for concept_id in (A, B, C)
    }
    assert after == before
    assert _absorbed_in_order(seed, B) == []


# -- 2.1.3 one commit -------------------------------------------------------- #


def test_one_auto_commit_holds_both_steps_and_the_ledger_with_the_group_message(
    tmp_path: Path,
) -> None:
    seed = _seed(tmp_path, selection_of(B, A, B, C))
    recorder = _Recorder()

    _commit(seed, recorder)

    [(paths, message)] = recorder.commits
    assert message == "openkos: merge group into concepts/b (concepts/a, concepts/c)"
    assert {
        "bundle/index.md",
        "bundle/log.md",
        f"bundle/{A}.md",
        f"bundle/{B}.md",
        f"bundle/{C}.md",
    } <= set(paths)
    assert any(p.startswith("bundle/.state/") for p in paths)
    assert len(paths) == len(set(paths))


def test_the_commit_message_helper_names_the_survivor_and_the_absorbed() -> None:
    from openkos.application import merge_service

    assert merge_service.merge_group_commit_message(B, (A, C)) == (
        "openkos: merge group into concepts/b (concepts/a, concepts/c)"
    )


# -- 2.1.4 staleness --------------------------------------------------------- #


def test_an_edit_to_an_unmarked_member_before_the_section_is_stale(
    tmp_path: Path,
) -> None:
    seed = _seed(tmp_path, selection_of(A, A, B))
    _edit(seed, C)
    before = snapshot(seed.root)
    recorder = _Recorder()

    outcome = _commit(seed, recorder)

    assert outcome == Stale(seed.key, "content_changed")
    assert recorder.events == []  # no section, no commit
    assert snapshot(seed.root) == before


def test_an_altered_selection_is_stale_and_writes_nothing(tmp_path: Path) -> None:
    seed = _seed(tmp_path, selection_of(A, A, B))
    altered = dataclasses.replace(seed.version, selection=selection_of(A, A, B, C))
    before = snapshot(seed.root)
    recorder = _Recorder()

    outcome = _commit(seed, recorder, version=altered)

    assert outcome == Stale(seed.key, "content_changed")
    assert recorder.events == []
    assert snapshot(seed.root) == before


def test_a_resolved_row_is_stale_and_writes_nothing(tmp_path: Path) -> None:
    seed = _seed(tmp_path, selection_of(A, A, B))
    conn = derived.open_derived_connection(seed.layout.findings_db_path)
    try:
        conn.execute(
            "UPDATE pending_items SET status = 'declined', resolution = 'declined'"
        )
        conn.commit()
    finally:
        conn.close()
    before = snapshot(seed.root)
    recorder = _Recorder()

    outcome = _commit(seed, recorder)

    assert outcome == Stale(seed.key, "row_resolved")
    assert recorder.events == ["enter", "exit"]
    assert snapshot(seed.root) == before


def test_a_superseded_row_is_stale_and_writes_nothing(tmp_path: Path) -> None:
    seed = _seed(tmp_path, selection_of(A, A, B))
    enqueue(seed.layout, identity_row(seed.layout, (A, B, C), verdict="different"))
    before = snapshot(seed.root)
    recorder = _Recorder()

    outcome = _commit(seed, recorder)

    assert outcome == Stale(seed.key, "row_superseded")
    assert recorder.events == ["enter", "exit"]
    assert snapshot(seed.root) == before


def test_a_row_whose_inputs_changed_is_stale_and_writes_nothing(
    tmp_path: Path,
) -> None:
    root, layout = make_workspace(tmp_path)
    proposal = identity_row(layout, (A, B, C))
    enqueue(layout, proposal)
    path = layout.bundle_dir / f"{B}.md"
    path.write_text(path.read_text("utf-8") + "\nEdited.\n", encoding="utf-8")
    preview = curate_decisions.preview_merge_group(
        root,
        layout,
        group_of(A, B, C),
        verdict=Verdict.SAME,
        selection=selection_of(A, A, B),
    )
    assert isinstance(preview, MergeGroupDecisionPreview)
    version = curate_decisions.version_of(
        preview, layout=layout, payload_digest=pq.payload_digest_for(proposal)
    )
    seed = _Seed(root, layout, proposal, preview, version)
    before = snapshot(root)
    recorder = _Recorder()

    outcome = _commit(seed, recorder)

    assert outcome == Stale(seed.key, "row_inputs_changed")
    assert recorder.commits == []
    assert snapshot(root) == before


def test_a_replayed_token_after_a_commit_is_stale_as_resolved(tmp_path: Path) -> None:
    seed = _seed(tmp_path, selection_of(B, A, B, C))
    _commit(seed, _Recorder())
    before = snapshot(seed.root)
    recorder = _Recorder()

    outcome = _commit(seed, recorder)

    assert outcome == Stale(seed.key, "row_resolved")
    assert recorder.commits == []
    assert snapshot(seed.root) == before


# -- 2.1.5 inside the section ------------------------------------------------ #


def test_a_member_vanishing_as_the_section_opens_is_member_missing(
    tmp_path: Path,
) -> None:
    seed = _seed(tmp_path, selection_of(A, A, B))
    observer = _Observer()
    recorder = _Recorder(on_enter=lambda: (seed.layout.bundle_dir / f"{C}.md").unlink())
    before = snapshot(seed.root)

    outcome = _commit(seed, recorder, observer=observer)

    assert outcome == NotApplied(Action.MERGE_GROUP, "member_missing")
    assert observer.missing == [(A, C)]
    assert recorder.commits == []
    gone = seed.layout.bundle_dir / f"{C}.md"
    after = snapshot(seed.root)
    assert not gone.exists()
    assert after == {p: b for p, b in before.items() if p != gone}


def test_a_first_step_target_edited_after_the_token_check_is_drift(
    tmp_path: Path,
) -> None:
    seed = _seed(tmp_path, selection_of(A, A, B))
    recorder = _Recorder(on_enter=lambda: _edit(seed, A))
    before = snapshot(seed.root)

    with pytest.raises(write_gate.DriftDetected) as caught:
        _commit(seed, recorder)

    assert A in caught.value.message
    assert recorder.commits == []
    assert _exists(seed, B)
    assert _absorbed_in_order(seed, A) == []
    # Only the edit the test itself made differs from the snapshot.
    changed = {p for p, b in snapshot(seed.root).items() if before.get(p) != b}
    assert changed == {seed.layout.bundle_dir / f"{A}.md"}


# -- 2.1.6 the lock ---------------------------------------------------------- #


def test_the_commit_section_is_entered_once_for_a_group_of_four(
    tmp_path: Path,
) -> None:
    members = (A, B, C, D)
    seed = _seed(tmp_path, selection_of(A, *members), members=members)
    recorder = _Recorder()

    outcome = _commit(seed, recorder)

    assert isinstance(outcome, Applied)
    assert recorder.events.count("enter") == 1
    assert recorder.events == ["enter", "autocommit", "exit"]
    assert outcome.merged == ((A, B), (A, C), (A, D))


def test_a_held_lock_with_no_wait_writes_nothing(tmp_path: Path) -> None:
    seed = _seed(tmp_path, selection_of(B, A, B, C))
    ports = dataclasses.replace(
        _Recorder().ports(),
        commit_section=lock_wait.locked_commit_section(seed.root, wait_seconds=0),
    )
    before = snapshot(seed.root)

    with lock.workspace_lock(seed.root), pytest.raises(lock.WorkspaceBusyError):
        _commit(seed, _Recorder(), ports=ports)

    assert snapshot(seed.root) == before


# -- 2.1.7 later steps see the earlier steps' bytes -------------------------- #


def test_later_steps_are_prepared_against_what_the_earlier_steps_wrote(
    tmp_path: Path,
) -> None:
    seed = _seed(
        tmp_path,
        selection_of(A, A, B, C),
        types={A: "Person", B: "Organization", C: "Place"},
    )
    survivor_bytes_at_preview = (seed.layout.bundle_dir / f"{A}.md").read_bytes()
    previewed = [s.type_outcome for s in seed.preview.steps]

    _commit(seed, _Recorder())

    survivor = (seed.layout.bundle_dir / f"{A}.md").read_text("utf-8")
    metadata, body = okf.load_frontmatter(survivor)
    assert previewed == ["recorded", "discarded"]
    assert metadata["type"] == "Person"
    assert metadata[okf.TYPE_ALTERNATIVE_KEY] == "Organization"
    assert _absorbed_in_order(seed, A) == [B, C]
    # Both absorbed bodies survive: the second step did not overwrite the first.
    assert B in body
    assert C in body
    assert survivor.encode() != survivor_bytes_at_preview


def test_the_group_row_ends_applied_not_stale(tmp_path: Path) -> None:
    seed = _seed(tmp_path, selection_of(B, A, B, C))

    _commit(seed, _Recorder())

    assert _row_state(seed) == ("applied", "as_proposed")


def test_a_partial_selection_resolves_the_row_as_modified(tmp_path: Path) -> None:
    members = (A, B, C, D)
    seed = _seed(tmp_path, selection_of(A, A, B, C), members=members)

    _commit(seed, _Recorder())

    assert _row_state(seed) == ("applied", "modified")
    assert curate_queue.open_row_by_key(seed.layout, seed.key) is None


def test_merge_core_still_resolves_a_pairwise_row_without_a_group_row() -> None:
    import inspect

    parameter = inspect.signature(application_lifecycle.merge_core).parameters[
        "group_row"
    ]
    assert parameter.default is None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
