"""A judged contradiction candidate's input digests, and the write of its
verdict to `.openkos/findings.db`.

Moved out of `cli.curate` so the application layer can judge one pair (the
re-check of a changed finding, `finding_recheck`) through the very functions
`curate`, `contradictions` and the daemon use: one digest convention, one
write path. `cli.curate` re-exports the three public names.
"""

import contextlib
from collections.abc import Sequence
from pathlib import Path

from openkos import config
from openkos.application.lock_wait import CommitSection
from openkos.model import okf
from openkos.resolution.contradiction import (
    CandidatePlan,
    ContradictionVerdict,
    _CandidateSpec,
)
from openkos.state import derived, findings
from openkos.state.vectorstore import content_hash


def all_present(layout: config.WorkspaceLayout, concept_ids: Sequence[str]) -> bool:
    """Whether every concept still resolves to a document on disk: a judge holds
    no lock, so a page can be forgotten between the call and the write."""
    return all(
        okf.concept_path_for(concept_id, layout.bundle_dir).exists()
        for concept_id in concept_ids
    )


def finding_input_digests(
    bundle_dir: Path, spec: _CandidateSpec
) -> tuple[findings.InputDigest, ...]:
    """Per-input `(input_ref, sha256)` rows for one judged candidate
    (durable-pending-work design, Decision 2's input table):

    - **typed-edge** (`spec.merge_entry is None`): both concept files' raw
      bytes, plus the `relation_type` label string -- it is in the prompt
      (`resolution.contradiction._build_messages`), so re-labelling an edge
      must invalidate the verdict too. An unreadable concept file (a race
      with a later mutation) simply contributes no row for that input,
      mirroring `_load_doc`'s own degrade-not-crash posture -- this is a
      digest computation, not a re-judgment, and must never raise.
    - **merged-body** (`spec.merge_entry is not None`): the ledger entry's
      OWN `survivor_before`/`absorbed_snapshot` strings, digested in
      memory -- never a second file read, since those strings are the
      exact bytes the verdict was computed from."""
    if spec.merge_entry is not None:
        survivor_id, _ = spec.pair_ids
        entry = spec.merge_entry
        return (
            findings.InputDigest(
                f"{survivor_id}:survivor_before",
                content_hash(entry.survivor_before.encode("utf-8")),
            ),
            findings.InputDigest(
                f"{entry.absorbed_id}:absorbed_snapshot",
                content_hash(entry.absorbed_snapshot.encode("utf-8")),
            ),
        )

    digests: list[findings.InputDigest] = []
    for concept_id in spec.pair_ids:
        try:
            raw_bytes = okf.concept_path_for(concept_id, bundle_dir).read_bytes()
        except OSError:
            continue
        digests.append(findings.InputDigest(concept_id, content_hash(raw_bytes)))
    if spec.relation_type is not None:
        digests.append(
            findings.InputDigest(
                "relation_type", content_hash(spec.relation_type.encode("utf-8"))
            )
        )
    return tuple(digests)


def pin_finding_inputs(
    bundle_dir: Path, plan: CandidatePlan
) -> tuple[tuple[findings.InputDigest, ...], ...]:
    """Each planned candidate's input digests as they stand NOW, in plan order.

    Taken BEFORE the judging call, which holds no workspace lock (#1137), so
    `persist_findings` can tell a verdict about the content that was judged
    from one about content edited or forgotten while the model ran."""
    return tuple(finding_input_digests(bundle_dir, spec) for spec in plan.specs)


def persist_findings(
    layout: config.WorkspaceLayout,
    plan: CandidatePlan,
    verdicts: Sequence[ContradictionVerdict],
    *,
    judged_inputs: Sequence[tuple[findings.InputDigest, ...]] | None = None,
    commit_section: CommitSection = contextlib.nullcontext,
) -> None:
    """Record every judged verdict to `.openkos/findings.db` (pending-work
    spec: "Contradiction Findings Are Persisted With Provenance"; curate-
    command spec delta: "Persisting a finding is not a bundle write" -- this
    writes ONLY under `.openkos/`, never `bundle/`).

    `plan.specs`/`verdicts` share the SAME deterministic order
    (`find_contradictions`'s own contract), so `zip` pairs each verdict with
    the candidate it was judged from without re-deriving anything; a
    partial batch (#441) yields fewer `verdicts` than `specs`, and `zip`
    stops there -- exactly the already-judged prefix, never a mismatch.
    An empty `verdicts` list opens no connection at all.

    Public since #653: the `contradictions` verb persists its own fresh
    verdicts through this exact write path, so the store the two verbs
    share can never diverge in shape or digest convention.

    The persist is a commit phase (#1137), entered through `commit_section`.
    `judged_inputs` (`pin_finding_inputs`, taken before the judging call) is
    re-validated inside it: a verdict whose input was edited or forgotten
    meanwhile is DROPPED, never stored against content nobody judged, and no
    row names a concept that no longer exists. Without it the digests are
    read here, at persist time, as a caller holding the lock throughout
    always did."""
    if not verdicts:
        return
    with commit_section():
        batch: list[findings.Finding] = []
        for index, (spec, verdict) in enumerate(
            zip(plan.specs, verdicts, strict=False)
        ):
            digests = finding_input_digests(layout.bundle_dir, spec)
            if judged_inputs is not None and (
                tuple(judged_inputs[index]) != digests
                or not all_present(layout, spec.pair_ids)
            ):
                continue
            batch.append(
                findings.Finding(
                    pair_ids=verdict.pair_ids,
                    merged_absorbed_id=verdict.merged_absorbed_id,
                    verdict=verdict.verdict.value,
                    confidence=verdict.confidence,
                    rationale=verdict.rationale,
                    input_digests=digests,
                    conflicting_claims=verdict.conflicting_claims,
                )
            )
        if not batch:
            return
        conn = derived.open_derived_connection(layout.findings_db_path)
        try:
            findings.record_findings(conn, batch)
        finally:
            conn.close()
