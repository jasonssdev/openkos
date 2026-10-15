"""The identity serve, persist and reassemble helpers, moved out of
`cli/main.py` (the `judging-application-service` change, S1).

The CLI delegators keep every patched name; these tests pin the moved
behavior at its new home, with no terminal in sight: advisories come back as
data (`PersistResult`, an `on_warning` callback), never as printed lines.
"""

import ast
import contextlib
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import openkos
from openkos import config, lock
from openkos.application import identity_adjudication as ia
from openkos.application import pending as application_pending
from openkos.model import okf
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    Verdict,
    rubric_digest,
)
from openkos.resolution.candidates import CandidateGroup, Tier
from openkos.state import adjudications as store
from openkos.state import derived
from openkos.state import pending_queue as pq

_MEMBERS = ("concepts/a", "concepts/b")
_RUBRIC = rubric_digest()


def _layout(root: Path) -> config.WorkspaceLayout:
    (root / "bundle").mkdir(parents=True, exist_ok=True)
    return config.WorkspaceLayout(root)


def _concept(root: Path, concept_id: str, body: str = "Body.") -> None:
    path = root / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {"type": "Concept", "title": concept_id}, f"# {concept_id}\n\n{body}\n"
        ),
        encoding="utf-8",
    )


def _group(members: tuple[str, ...] = _MEMBERS) -> CandidateGroup:
    return CandidateGroup(
        okf_type="Concept", member_ids=members, tier=Tier.HIGH, trigger="alpha"
    )


def _result(
    verdict: Verdict = Verdict.SAME,
    members: tuple[str, ...] = _MEMBERS,
    rationale: str = "Judged.",
) -> AdjudicatedCandidate:
    return AdjudicatedCandidate(
        candidate=_group(members),
        verdict=verdict,
        confidence=0.9,
        rationale=rationale,
    )


def _stored(
    layout: config.WorkspaceLayout,
    *,
    members: tuple[str, ...] = _MEMBERS,
    verdict: str = "same",
    rationale: str = "Judged.",
    include_confidential: bool = False,
    rubric: str | None = _RUBRIC,
    digests: dict[str, str] | None = None,
) -> None:
    current = application_pending.current_finding_digest(layout.bundle_dir)
    pinned = digests if digests is not None else {m: current(m) or "" for m in members}
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        store.record_adjudications(
            conn,
            [
                store.Adjudication(
                    member_ids=members,
                    verdict=verdict,
                    confidence=0.8,
                    rationale=rationale,
                    include_confidential=include_confidential,
                    input_digests=tuple(
                        store.InputDigest(input_ref=ref, digest=digest)
                        for ref, digest in pinned.items()
                    ),
                    rubric_digest=rubric,
                )
            ],
        )
    finally:
        conn.close()


def _rows(layout: config.WorkspaceLayout) -> list[store.Adjudication]:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        return list(store.open_adjudications(conn))
    finally:
        conn.close()


@pytest.fixture
def layout(tmp_path: Path) -> config.WorkspaceLayout:
    lay = _layout(tmp_path)
    for member in _MEMBERS:
        _concept(tmp_path, member)
    return lay


def _partition(
    layout: config.WorkspaceLayout,
    groups: list[CandidateGroup],
    *,
    include_confidential: bool = False,
    warnings: list[str] | None = None,
) -> ia.AdjudicationServes:
    return ia.partition_adjudication_serves(
        layout,
        groups,
        include_confidential=include_confidential,
        on_warning=warnings.append if warnings is not None else None,
        surface="adjudicate",
    )


# --- partition_adjudication_serves ------------------------------------------


def test_partition_with_no_store_serves_nothing_and_did_not_read(
    layout: config.WorkspaceLayout,
) -> None:
    groups = [_group()]

    served = _partition(layout, groups)

    assert served == ia.AdjudicationServes({}, groups, 0, store_read=False)


def test_partition_serves_a_current_verdict_and_judges_the_rest(
    layout: config.WorkspaceLayout, tmp_path: Path
) -> None:
    _concept(tmp_path, "concepts/c")
    _concept(tmp_path, "concepts/d")
    _stored(layout, rationale="Same entity.")
    other = _group(("concepts/c", "concepts/d"))

    served = _partition(layout, [_group(), other])

    key = store.group_key_for(_MEMBERS)
    assert list(served.served) == [key]
    assert served.served[key].verdict is Verdict.SAME
    assert served.served[key].rationale == "Same entity."
    assert served.to_judge == [other]
    assert served.rubric_stale == 0
    assert served.store_read is True


def test_partition_refuses_a_row_from_another_rubric_and_counts_it(
    layout: config.WorkspaceLayout,
) -> None:
    _stored(layout, rubric="a-different-rubric")
    groups = [_group()]

    served = _partition(layout, groups)

    assert (served.served, served.to_judge, served.rubric_stale) == ({}, groups, 1)


def test_partition_counts_a_row_with_no_rubric_as_stale(
    layout: config.WorkspaceLayout,
) -> None:
    _stored(layout, rubric=None)

    assert _partition(layout, [_group()]).rubric_stale == 1


def test_partition_does_not_count_a_rubric_stale_for_other_refusals(
    layout: config.WorkspaceLayout, tmp_path: Path
) -> None:
    _stored(layout, include_confidential=True)

    served = _partition(layout, [_group()], include_confidential=False)

    assert served.served == {}
    assert served.rubric_stale == 0


def test_partition_degrades_an_unreadable_store_to_one_warning(
    layout: config.WorkspaceLayout,
) -> None:
    layout.findings_db_path.parent.mkdir(parents=True, exist_ok=True)
    layout.findings_db_path.write_bytes(b"this is not a sqlite database" * 50)
    warnings: list[str] = []
    groups = [_group()]

    served = _partition(layout, groups, warnings=warnings)

    assert served == ia.AdjudicationServes({}, groups, 0, store_read=False)
    assert len(warnings) == 1
    assert warnings[0].startswith(
        "openkos adjudicate: warning -- failed to read persisted adjudications ("
    )
    assert warnings[0].endswith("); judging every group fresh.")


def test_partition_is_silent_when_no_warning_callback_is_given(
    layout: config.WorkspaceLayout,
) -> None:
    layout.findings_db_path.parent.mkdir(parents=True, exist_ok=True)
    layout.findings_db_path.write_bytes(b"this is not a sqlite database" * 50)

    assert _partition(layout, [_group()]).store_read is False


# --- servable_verdict: each failed check refuses ----------------------------


def _verdict(
    layout: config.WorkspaceLayout,
    stored: store.Adjudication,
    *,
    include_confidential: bool | None = False,
    rubric: str = _RUBRIC,
    group: CandidateGroup | None = None,
) -> AdjudicatedCandidate | None:
    return ia.servable_verdict(
        stored,
        group or _group(),
        current_digest=application_pending.current_finding_digest(layout.bundle_dir),
        rubric=rubric,
        include_confidential=include_confidential,
    )


def _row(
    layout: config.WorkspaceLayout,
    *,
    members: tuple[str, ...] = _MEMBERS,
    verdict: str = "same",
    rationale: str = "Judged.",
    include_confidential: bool = False,
    rubric: str | None = _RUBRIC,
    digests: dict[str, str] | None = None,
) -> store.Adjudication:
    current = application_pending.current_finding_digest(layout.bundle_dir)
    pinned = digests if digests is not None else {m: current(m) or "" for m in members}
    return store.Adjudication(
        member_ids=members,
        verdict=verdict,
        confidence=0.8,
        rationale=rationale,
        include_confidential=include_confidential,
        input_digests=tuple(
            store.InputDigest(input_ref=ref, digest=digest)
            for ref, digest in pinned.items()
        ),
        rubric_digest=rubric,
    )


def test_servable_verdict_serves_a_row_that_passes_every_check(
    layout: config.WorkspaceLayout,
) -> None:
    served = _verdict(layout, _row(layout, rationale="Same entity."))

    assert served is not None
    assert (served.verdict, served.confidence, served.rationale) == (
        Verdict.SAME,
        0.8,
        "Same entity.",
    )


def test_servable_verdict_refuses_a_rubric_mismatch(
    layout: config.WorkspaceLayout,
) -> None:
    assert _verdict(layout, _row(layout, rubric="old")) is None


def test_servable_verdict_refuses_a_row_with_no_rubric(
    layout: config.WorkspaceLayout,
) -> None:
    assert _verdict(layout, _row(layout, rubric=None)) is None


def test_servable_verdict_refuses_a_member_set_mismatch(
    layout: config.WorkspaceLayout,
) -> None:
    only_a = _row(layout, members=("concepts/a",))
    assert _verdict(layout, only_a) is None


def test_servable_verdict_refuses_a_stale_member_digest(
    layout: config.WorkspaceLayout,
) -> None:
    stale = _row(layout, digests={"concepts/a": "x", "concepts/b": "y"})
    assert _verdict(layout, stale) is None


def test_servable_verdict_refuses_a_row_with_no_digests(
    layout: config.WorkspaceLayout,
) -> None:
    assert _verdict(layout, _row(layout, digests={})) is None


def test_servable_verdict_refuses_a_confidential_bit_mismatch(
    layout: config.WorkspaceLayout,
) -> None:
    row = _row(layout, include_confidential=True)
    assert _verdict(layout, row, include_confidential=False) is None


def test_servable_verdict_ignores_the_bit_when_it_is_none(
    layout: config.WorkspaceLayout,
) -> None:
    row = _row(layout, include_confidential=True)
    assert _verdict(layout, row, include_confidential=None) is not None


def test_servable_verdict_refuses_a_verdict_outside_the_enum(
    layout: config.WorkspaceLayout,
) -> None:
    assert _verdict(layout, _row(layout, verdict="maybe")) is None


def test_servable_verdict_withdraws_a_self_refuting_same(
    layout: config.WorkspaceLayout,
) -> None:
    row = _row(
        layout,
        verdict="same",
        rationale="Two different instances of the same recurring event.",
    )

    served = _verdict(layout, row)

    assert served is not None
    assert served.verdict is Verdict.DIFFERENT


# --- servable_stored_verdicts (the daemon's read-only carry) ------------------


def test_stored_verdicts_carry_a_servable_latest_row(
    layout: config.WorkspaceLayout,
) -> None:
    _stored(layout, verdict="uncertain", rationale="Unclear.")

    (carried,) = ia.servable_stored_verdicts(layout, [_group()])

    assert (carried.candidate, carried.verdict, carried.rationale) == (
        _group(),
        Verdict.UNCERTAIN,
        "Unclear.",
    )


@pytest.mark.parametrize("include_confidential", [False, True])
def test_stored_verdicts_ignore_the_confidential_bit(
    layout: config.WorkspaceLayout, include_confidential: bool
) -> None:
    _stored(layout, include_confidential=include_confidential)

    assert [r.verdict for r in ia.servable_stored_verdicts(layout, [_group()])] == [
        Verdict.SAME
    ]


def test_stored_verdicts_carry_only_the_latest_row(
    layout: config.WorkspaceLayout,
) -> None:
    _stored(layout, verdict="same")
    _stored(layout, verdict="different")

    assert [r.verdict for r in ia.servable_stored_verdicts(layout, [_group()])] == [
        Verdict.DIFFERENT
    ]


def test_stored_verdicts_refuse_a_group_with_no_row(
    layout: config.WorkspaceLayout,
) -> None:
    _stored(layout)
    other = _group(("concepts/a", "concepts/c"))

    assert ia.servable_stored_verdicts(layout, [other]) == []


def test_stored_verdicts_refuse_another_rubric(
    layout: config.WorkspaceLayout,
) -> None:
    _stored(layout, rubric="not-this-build")

    assert ia.servable_stored_verdicts(layout, [_group()]) == []


def test_stored_verdicts_refuse_a_member_set_mismatch(
    layout: config.WorkspaceLayout,
) -> None:
    current = application_pending.current_finding_digest(layout.bundle_dir)
    _stored(layout, digests={"concepts/a": current("concepts/a") or ""})

    assert ia.servable_stored_verdicts(layout, [_group()]) == []


def test_stored_verdicts_refuse_an_edited_member(
    layout: config.WorkspaceLayout,
) -> None:
    _stored(layout)
    _concept(layout.root, "concepts/b", "Edited since it was judged.")

    assert ia.servable_stored_verdicts(layout, [_group()]) == []


def test_stored_verdicts_carry_nothing_and_create_nothing_without_a_store(
    layout: config.WorkspaceLayout,
) -> None:
    assert ia.servable_stored_verdicts(layout, [_group()]) == []
    assert not layout.findings_db_path.exists()


def test_stored_verdicts_carry_nothing_from_an_unreadable_store(
    layout: config.WorkspaceLayout,
) -> None:
    layout.findings_db_path.parent.mkdir(parents=True, exist_ok=True)
    layout.findings_db_path.write_bytes(b"this is not a sqlite database" * 50)

    assert ia.servable_stored_verdicts(layout, [_group()]) == []


def test_stored_verdicts_leave_the_store_untouched(
    layout: config.WorkspaceLayout,
) -> None:
    _stored(layout)
    before = _rows(layout)

    ia.servable_stored_verdicts(layout, [_group()])

    assert _rows(layout) == before


# --- reassemble_adjudications ------------------------------------------------


def test_reassemble_follows_candidate_order_and_prefers_served() -> None:
    first = _group(("concepts/a", "concepts/b"))
    second = _group(("concepts/c", "concepts/d"))
    third = _group(("concepts/e", "concepts/f"))
    served_second = _result(Verdict.SAME, second.member_ids)
    fresh_second = _result(Verdict.DIFFERENT, second.member_ids)
    fresh_first = _result(Verdict.UNCERTAIN, first.member_ids)

    rebuilt = ia.reassemble_adjudications(
        [first, second, third],
        {store.group_key_for(second.member_ids): served_second},
        [fresh_first, fresh_second],
    )

    assert rebuilt == [fresh_first, served_second]


# --- persist_adjudications ---------------------------------------------------


def _persist(
    layout: config.WorkspaceLayout,
    results: list[AdjudicatedCandidate],
    *,
    judged_digests: dict[str, str | None] | None = None,
    include_confidential: bool = False,
    commit_section: Callable[[], contextlib.AbstractContextManager[None]] = (
        contextlib.nullcontext
    ),
) -> ia.PersistResult:
    return ia.persist_adjudications(
        layout,
        results,
        include_confidential=include_confidential,
        judged_digests=judged_digests,
        commit_section=commit_section,
    )


def test_persist_with_no_results_writes_nothing(
    layout: config.WorkspaceLayout,
) -> None:
    assert _persist(layout, []) == ia.PersistResult(0, 0, None, None)
    assert not layout.findings_db_path.exists()


def test_persist_records_the_verdict_with_digests_and_rubric(
    layout: config.WorkspaceLayout,
) -> None:
    result = _persist(layout, [_result(Verdict.SAME, rationale="Same entity.")])

    assert result == ia.PersistResult(1, 0, None, None)
    (row,) = _rows(layout)
    assert (row.member_ids, row.verdict, row.rationale) == (
        _MEMBERS,
        "same",
        "Same entity.",
    )
    assert row.rubric_digest == _RUBRIC
    assert row.include_confidential is False
    assert {d.input_ref for d in row.input_digests} == set(_MEMBERS)


def test_persist_records_the_confidential_bit(
    layout: config.WorkspaceLayout,
) -> None:
    _persist(layout, [_result()], include_confidential=True)

    assert _rows(layout)[0].include_confidential is True


def test_persist_drops_a_result_whose_member_is_unreadable(
    layout: config.WorkspaceLayout,
) -> None:
    ghost = ("concepts/a", "concepts/ghost")

    result = _persist(layout, [_result(members=ghost)])

    assert result == ia.PersistResult(0, 1, None, None)
    assert not layout.findings_db_path.exists()


def test_persist_drops_a_result_whose_member_moved_since_it_was_judged(
    layout: config.WorkspaceLayout, tmp_path: Path
) -> None:
    current = application_pending.current_finding_digest(layout.bundle_dir)
    judged = {m: current(m) for m in _MEMBERS}
    _concept(tmp_path, "concepts/b", body="Edited after the judge read it.")

    result = _persist(layout, [_result()], judged_digests=judged)

    assert result == ia.PersistResult(0, 1, None, None)
    assert not layout.findings_db_path.exists()


def test_persist_keeps_the_unmoved_result_and_drops_the_moved_one(
    layout: config.WorkspaceLayout, tmp_path: Path
) -> None:
    _concept(tmp_path, "concepts/c")
    _concept(tmp_path, "concepts/d")
    current = application_pending.current_finding_digest(layout.bundle_dir)
    judged = {m: current(m) for m in (*_MEMBERS, "concepts/c", "concepts/d")}
    _concept(tmp_path, "concepts/d", body="Edited.")

    result = _persist(
        layout,
        [_result(), _result(members=("concepts/c", "concepts/d"))],
        judged_digests=judged,
    )

    assert result == ia.PersistResult(1, 1, None, None)
    assert [row.member_ids for row in _rows(layout)] == [_MEMBERS]


def _seed_identity_row(layout: config.WorkspaceLayout) -> None:
    import json

    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.upsert_proposal(
            conn,
            pq.Proposal(
                kind="identity",
                key_body=pq.identity_key(_MEMBERS),
                producer="duplicates/1",
                payload=json.dumps({"member_ids": list(_MEMBERS)}),
                targets=_MEMBERS,
            ),
            commit_section=contextlib.nullcontext,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()


def _statuses(layout: config.WorkspaceLayout) -> list[tuple[str, str | None]]:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        return [(i.status, i.resolution) for i in pq.all_items(conn)]
    finally:
        conn.close()


def test_persist_resolves_the_open_identity_row_of_a_different_verdict(
    layout: config.WorkspaceLayout,
) -> None:
    _seed_identity_row(layout)

    _persist(layout, [_result(Verdict.DIFFERENT)])

    assert _statuses(layout) == [("declined", "declined")]


@pytest.mark.parametrize("verdict", [Verdict.SAME, Verdict.UNCERTAIN])
def test_persist_leaves_the_row_open_for_a_verdict_other_than_different(
    layout: config.WorkspaceLayout, verdict: Verdict
) -> None:
    _seed_identity_row(layout)

    _persist(layout, [_result(verdict)])

    assert _statuses(layout) == [("pending", None)]


def test_persist_reports_a_store_error_as_data(
    layout: config.WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr("openkos.state.adjudications.record_adjudications", _boom)

    result = _persist(layout, [_result()])

    assert result == ia.PersistResult(0, 0, "disk full", None)


def test_persist_reports_a_busy_workspace_as_data(
    layout: config.WorkspaceLayout,
) -> None:
    class _Busy:
        def __enter__(self) -> None:
            raise lock.WorkspaceBusyError("held by pid 1")

        def __exit__(self, *_exc: object) -> None:
            return None

    def _busy() -> contextlib.AbstractContextManager[None]:
        return _Busy()

    result = _persist(layout, [_result()], commit_section=_busy)

    assert result == ia.PersistResult(0, 0, None, "held by pid 1")
    assert not layout.findings_db_path.exists()


def test_persist_enters_the_commit_section_exactly_once(
    layout: config.WorkspaceLayout,
) -> None:
    entered: list[str] = []

    @contextlib.contextmanager
    def _section() -> Iterator[None]:
        entered.append("in")
        yield
        entered.append("out")

    _persist(layout, [_result()], commit_section=_section)

    assert entered == ["in", "out"]


# --- write-through (the judging run) -------------------------------------------


def _write_through(
    layout: config.WorkspaceLayout,
    results: list[AdjudicatedCandidate],
    *,
    judged_digests: dict[str, str | None] | None = None,
    commit_section: Callable[[], contextlib.AbstractContextManager[None]] = (
        contextlib.nullcontext
    ),
) -> ia.PersistResult:
    return ia.persist_adjudications(
        layout,
        results,
        include_confidential=False,
        judged_digests=judged_digests,
        commit_section=commit_section,
        write_through=True,
    )


def _seed_unjudged_row(layout: config.WorkspaceLayout) -> None:
    """The open `pending_evaluation` row a daemon pass leaves: no verdict, the
    current input digests."""
    from openkos.application import queue_producers

    proposal = queue_producers.identity_group_proposal(
        _group(),
        current_digest=application_pending.current_finding_digest(layout.bundle_dir),
        result=None,
    )
    assert proposal is not None
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.upsert_proposal(
            conn,
            proposal,
            commit_section=contextlib.nullcontext,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()


def _open_row_verdicts(layout: config.WorkspaceLayout) -> list[object]:
    import json

    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        return [
            (json.loads(item.payload)["adjudication"] or {}).get("verdict")
            for item in pq.all_items(conn)
            if item.status in ("pending", "claimed")
        ]
    finally:
        conn.close()


@pytest.mark.parametrize("verdict", [Verdict.SAME, Verdict.UNCERTAIN])
def test_write_through_amends_the_open_row_with_the_verdict(
    layout: config.WorkspaceLayout, verdict: Verdict
) -> None:
    _seed_unjudged_row(layout)
    assert _open_row_verdicts(layout) == [None]

    result = _write_through(layout, [_result(verdict)])

    assert result.persisted == 1
    assert _open_row_verdicts(layout) == [verdict.value]
    assert len(_statuses(layout)) == 1


def test_write_through_never_creates_a_row_for_a_group_with_none(
    layout: config.WorkspaceLayout,
) -> None:
    _seed_unjudged_row(layout)
    elsewhere = ("concepts/c", "concepts/d")
    for member in elsewhere:
        _concept(layout.root, member)

    _write_through(layout, [_result(Verdict.SAME, members=elsewhere)])

    assert _open_row_verdicts(layout) == [None]
    assert len(_statuses(layout)) == 1
    assert [row.member_ids for row in _rows(layout)] == [elsewhere]


def test_write_through_leaves_the_queue_absent_when_there_is_none(
    layout: config.WorkspaceLayout,
) -> None:
    result = _write_through(layout, [_result(Verdict.SAME)])

    assert result.persisted == 1
    assert _statuses(layout) == []


def test_write_through_declines_a_different_verdict_and_amends_nothing(
    layout: config.WorkspaceLayout,
) -> None:
    _seed_unjudged_row(layout)

    _write_through(layout, [_result(Verdict.DIFFERENT)])

    assert _statuses(layout) == [("declined", "declined")]


def test_write_through_skips_a_verdict_about_content_edited_during_the_call(
    layout: config.WorkspaceLayout,
) -> None:
    _seed_unjudged_row(layout)
    current = application_pending.current_finding_digest(layout.bundle_dir)
    judged = {member: current(member) for member in _MEMBERS}
    _concept(layout.root, "concepts/b", body="Edited during the call.")

    result = _write_through(layout, [_result(Verdict.SAME)], judged_digests=judged)

    assert result == ia.PersistResult(0, 1, None, None)
    assert _rows(layout) == []
    assert _open_row_verdicts(layout) == [None]


def test_write_through_runs_inside_the_one_commit_section(
    layout: config.WorkspaceLayout,
) -> None:
    _seed_unjudged_row(layout)
    entered: list[str] = []

    @contextlib.contextmanager
    def _section() -> Iterator[None]:
        # The workspace lock is not re-entrant: a second entry would be busy.
        assert "in" not in entered[-1:], "the section was entered twice"
        entered.append("in")
        yield
        entered.append("out")

    _write_through(layout, [_result(Verdict.SAME)], commit_section=_section)

    assert entered == ["in", "out"]
    assert _open_row_verdicts(layout) == ["same"]


# --- the module speaks only data ---------------------------------------------


def _module_tree() -> ast.Module:
    path = Path(openkos.__file__).parent / "application" / "identity_adjudication.py"
    return ast.parse(path.read_text(encoding="utf-8"))


def test_the_module_never_prints_or_reads_a_terminal() -> None:
    offenders: list[str] = []
    for node in ast.walk(_module_tree()):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = (
            target.attr
            if isinstance(target, ast.Attribute)
            else target.id
            if isinstance(target, ast.Name)
            else ""
        )
        if name in {"isatty", "echo", "print", "input"}:
            offenders.append(f"{name} at line {node.lineno}")
    assert offenders == []


def test_the_module_imports_no_terminal_or_adapter_library() -> None:
    banned = ("typer", "rich", "openkos.cli", "openkos.vcs")
    found: list[str] = []
    for node in ast.walk(_module_tree()):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        found += [
            n
            for n in names
            if n.split(".")[0] in {b.split(".")[0] for b in banned}
            and any(n == b or n.startswith(b + ".") for b in banned)
        ]
    assert found == []
