"""Operator decision sidecar store for pending contradiction findings
(pending-work design, Decisions 3-4; ADR-0014).

An irreplaceable human verdict on a machine proposal -- exactly one kind
extends ADR-0013's `bundle/.state/` mechanism, deliberately excluding the
findings themselves (`state.findings`, recomputable machine inference kept
out of `bundle/`). Reuses ADR-0013's storage shape verbatim: one file per
sorted-first concept id, mirroring `bundle.ledger.ledger_path_for`;
`okf.concept_path_for`'s `(root, suffix)` generalization for id-to-path
mapping; `okf.dump_frontmatter`/`load_frontmatter` for a frontmatter
container with an empty body (ADR-0002 invariant 3); and a non-`.md`
suffix (`.decisions.okf`) for free structural exclusion from every
`rglob("*.md")` EXCLUDE walk, already policed unmodified by
`lint.check_state_dir_contains_no_markdown`.

A single record is ids + verdict only -- no rationale, no body text -- which
is what keeps this store non-confidential (Decision 4).

Leaf module: mirrors `bundle/ledger.py`/`bundle/relations.py`/
`bundle/links.py` -- MUST NOT import `openkos.graph` (canonical-layer rule,
AGENTS.md:41; guarded by `tests/unit/bundle/test_layering.py`).

Deliberately UNWIRED to any CLI verb in this slice (tasks.md slicing
rationale, maintainer decision D6): no operator action can produce a
`bundle/.state/decisions/**` file through the shipped CLI yet -- only a
direct unit-test call to `write_decisions` can."""

import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from openkos import fsio
from openkos.model import okf

DECISIONS_DIRNAME: Final = "decisions"
"""The subdirectory of `okf.STATE_DIRNAME` holding every decision sidecar,
mirroring `bundle.ledger.LEDGER_DIRNAME`'s own precedent."""

DECISIONS_SUFFIX: Final = ".decisions.okf"
"""Never `.md` -- see this module's docstring and `bundle.ledger.
LEDGER_SUFFIX`'s own rationale, which applies identically here."""

DECISIONS_SCHEMA: Final = "openkos.decisions/v2"
"""The committed sidecar's own `schema` key, versioned independently of any
single record's shape -- mirrors `bundle.ledger.LEDGER_SIDECAR_SCHEMA`.

v2 (#797) admits a SECOND kind of human ruling into the same container:
identity decisions, under their own `identity_decisions` key. The bump is
honest rather than load-bearing -- no reader validates this string, so a v1
sidecar (which simply has no `identity_decisions` key) reads as zero identity
decisions, and a v2 sidecar's `decisions` list is shape-identical to v1."""

_DECISION_KEY_HEX_CHARS: Final = 32
"""Mirrors `model.okf._ORIGIN_KEY_HEX_CHARS` (Decision 3): 128 bits of the
digest, unambiguous for any realistic workspace."""

DecisionState = Literal["declined", "open"]


def decision_key_for(pair_ids: tuple[str, str], merged_absorbed_id: str | None) -> str:
    """The stable identity a decision is keyed on (Decision 3):
    `sha256("contradiction/v1\\n" + pair_ids[0] + "\\n" + pair_ids[1] + "\\n"
    + (merged_absorbed_id or ""))[:32]`.

    Never a findings row id -- a findings row is recomputed on every
    `curate` run and its row id is not stable across recomputation, so
    keying on it would silently evaporate every declination on the next
    run (proposal's Critical risk). `merged_absorbed_id` is mandatory in
    the digest, not optional: it is the SOLE discriminator between a
    typed-edge candidate and a merged-body candidate sharing the same
    `pair_ids` (`resolution.contradiction.ContradictionVerdict.
    merged_absorbed_id`'s own warning) -- `pair_ids` shape alone is not a
    safe stand-in."""
    payload = (
        "contradiction/v1\n"
        + pair_ids[0]
        + "\n"
        + pair_ids[1]
        + "\n"
        + (merged_absorbed_id or "")
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:_DECISION_KEY_HEX_CHARS]


def identity_decision_key_for(member_ids: Sequence[str]) -> str:
    """The stable identity an IDENTITY decision is keyed on (#797):
    `sha256("identity/v1\\n" + "\\n".join(sorted(member_ids)))[:32]`.

    The `identity/v1` prefix is what keeps this namespace disjoint from
    `decision_key_for`'s `contradiction/v1`. That matters more than it
    looks: "these two do not contradict each other" and "these two are not
    the same entity" are OPPOSITE human rulings that can both be made about
    the SAME pair, and a shared key would let one silently answer for the
    other.

    Members are SORTED into the digest because a candidate group is a set,
    not a sequence -- `adjudications.group_key_for` can skip the sort only
    because `CandidateGroup.member_ids` arrives pre-sorted; this key is
    reachable from operator-supplied ids too, so it sorts here.

    Never keyed on a candidate-group row id: a group is recomputed on every
    `duplicates`/`curate` run and its position is not stable, so keying on
    it would evaporate every ruling on the next run -- the same reasoning
    `decision_key_for` records."""
    payload = "identity/v1\n" + "\n".join(sorted(member_ids))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:_DECISION_KEY_HEX_CHARS]


@dataclass(frozen=True)
class IdentityDecisionRecord:
    """One operator ruling on one duplicate-candidate GROUP (#797): ids and
    verdict only, no rationale, so this store stays non-confidential for the
    same reason `DecisionRecord` does.

    `state="declined"` means "reviewed, keep distinct" -- the human looked at
    the group and refused the merge. It is deliberately the same vocabulary
    `DecisionRecord.state` uses: in both stores a decline is a human refusing
    a machine proposal.

    Separate from `DecisionRecord` rather than a `kind` field on it, for two
    reasons. A group carries N members where a contradiction carries exactly
    two (`_decode_record` REQUIRES a 2-item `pair_ids` and raises otherwise,
    so an identity row in that list would break the contradiction reader).
    And the two answer different questions, which is precisely the case for
    two keys rather than one discriminated key."""

    decision_key: str
    member_ids: tuple[str, ...]
    state: DecisionState
    decided_at: str


@dataclass(frozen=True)
class DecisionRecord:
    """One operator decision on one contradiction proposal (Decision 4):
    ids and verdict only -- no rationale, no body text."""

    decision_key: str
    pair_ids: tuple[str, str]
    merged_absorbed_id: str | None
    state: DecisionState
    decided_at: str


def decisions_root(bundle_dir: Path) -> Path:
    """`bundle_dir/.state/decisions` -- the root every decision sidecar
    lives under (Decision 4), mirroring `bundle.ledger.ledger_root`."""
    return bundle_dir / okf.STATE_DIRNAME / DECISIONS_DIRNAME


def decisions_path_for(concept_id: str, bundle_dir: Path) -> Path:
    """The committed sidecar path for `concept_id` -- the SAME
    NFC/NFD-tolerant resolver `okf.concept_path_for` uses for concept files
    and `bundle.ledger.ledger_path_for` reuses for the merge ledger,
    generalized to this store's `(root, suffix)` (Decision 4: "do not
    invent a second id-to-path mapping")."""
    return okf.concept_path_for(
        concept_id, decisions_root(bundle_dir), suffix=DECISIONS_SUFFIX
    )


def _encode_container(
    concept_id: str,
    records: list[DecisionRecord],
    identity_records: list[IdentityDecisionRecord],
) -> dict[str, object]:
    """Both kinds, each under its own key. `identity_decisions` is omitted
    entirely when empty so a workspace that never declined a merge keeps
    byte-identical sidecars to the pre-#797 ones."""
    container: dict[str, object] = {
        "schema": DECISIONS_SCHEMA,
        "concept_id": concept_id,
        "decisions": [
            {
                "decision_key": record.decision_key,
                "pair_ids": list(record.pair_ids),
                "merged_absorbed_id": record.merged_absorbed_id,
                "state": record.state,
                "decided_at": record.decided_at,
            }
            for record in records
        ],
    }
    if identity_records:
        container["identity_decisions"] = [
            {
                "decision_key": record.decision_key,
                "member_ids": list(record.member_ids),
                "state": record.state,
                "decided_at": record.decided_at,
            }
            for record in identity_records
        ]
    return container


def _decode_identity_record(raw: dict[str, object]) -> IdentityDecisionRecord | None:
    """Decode one identity row, or `None` if it is malformed.

    Answers `None` rather than raising, unlike `_decode_record`: this store
    is read by `duplicates`, `status`, `next` and `curate` -- the commands
    an operator runs to understand a workspace that is already misbehaving
    -- and before this key existed they could not fail this way at all, so
    raising would ADD a failure mode to four working commands. A dropped
    row is a lost suppression, so the caller announces it."""
    member_ids_raw = raw.get("member_ids")
    if not isinstance(member_ids_raw, list) or len(member_ids_raw) < 2:
        return None
    if any(required not in raw for required in ("decision_key", "state", "decided_at")):
        return None
    return IdentityDecisionRecord(
        decision_key=str(raw["decision_key"]),
        member_ids=tuple(str(member) for member in member_ids_raw),
        state="declined" if raw["state"] == "declined" else "open",
        decided_at=str(raw["decided_at"]),
    )


def read_identity_decisions(
    concept_id: str, bundle_dir: Path
) -> list[IdentityDecisionRecord]:
    """Every `IdentityDecisionRecord` recorded under `concept_id`'s sidecar
    (#797). Absent file, or a v1 sidecar with no identity list, returns
    `[]` -- mirroring `read_decisions`' own absent-file contract."""
    return read_identity_decisions_at(decisions_path_for(concept_id, bundle_dir))


def read_identity_decisions_at(path: Path) -> list[IdentityDecisionRecord]:
    """The walked-path reader, `read_decisions_at`'s twin: the privacy sweep
    reads from the file actually on disk, never from a path rebuilt from a
    possibly-drifted `concept_id` frontmatter field."""
    if not path.is_file():
        return []
    metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    raw = metadata.get("identity_decisions")
    if not isinstance(raw, list):
        return []
    decoded = [
        _decode_identity_record(entry) for entry in raw if isinstance(entry, dict)
    ]
    dropped = sum(1 for record in decoded if record is None)
    if dropped:
        # Never silent: a dropped row is a human ruling lost, and losing it
        # quietly re-offers a merge they already refused.
        print(
            f"openkos: warning -- {dropped} malformed identity decision "
            f"record(s) in {path}; those groups will be offered again.",
            file=sys.stderr,
        )
    return [record for record in decoded if record is not None]


def write_identity_decisions(
    concept_id: str, bundle_dir: Path, *, records: list[IdentityDecisionRecord]
) -> Path:
    """(Re)write `concept_id`'s identity decisions to hold EXACTLY
    `records`, PRESERVING whatever contradiction decisions the same sidecar
    already holds.

    The preservation is the whole point of this being its own writer: the
    two kinds share a file so one privacy sweep covers both, which means a
    full-replace of one kind must never silently erase the other's
    rulings."""
    path = decisions_path_for(concept_id, bundle_dir)
    return rewrite_both_at(
        path,
        concept_id=concept_id,
        records=read_decisions_at(path),
        identity_records=records,
    )


def _decode_record(raw: dict[str, object]) -> DecisionRecord:
    pair_ids_raw = raw["pair_ids"]
    if not isinstance(pair_ids_raw, list) or len(pair_ids_raw) != 2:
        raise ValueError(
            f"malformed decision record: pair_ids must be a 2-item list, got {pair_ids_raw!r}"
        )
    return DecisionRecord(
        decision_key=str(raw["decision_key"]),
        pair_ids=(str(pair_ids_raw[0]), str(pair_ids_raw[1])),
        merged_absorbed_id=(
            None
            if raw.get("merged_absorbed_id") is None
            else str(raw["merged_absorbed_id"])
        ),
        state="declined" if raw["state"] == "declined" else "open",
        decided_at=str(raw["decided_at"]),
    )


def read_decisions(concept_id: str, bundle_dir: Path) -> list[DecisionRecord]:
    """Read every `DecisionRecord` recorded for `concept_id`'s sidecar. No
    sidecar on disk (no decision ever declined/reopened under this concept
    id) returns `[]` -- mirrors `bundle.ledger.read_entries`'s own
    "absent file" contract."""
    return read_decisions_at(decisions_path_for(concept_id, bundle_dir))


def read_decisions_at(path: Path) -> list[DecisionRecord]:
    """Read every `DecisionRecord` from the sidecar AT `path` -- the walked-
    path reader the privacy sweep and the declined-listing views use, so a
    record is read from the file actually on disk, never from a path rebuilt
    from a (possibly drifted or hostile) `concept_id` frontmatter field.
    `read_decisions` is the id-addressed wrapper for callers that legitimately
    own the id. Absent file returns `[]`."""
    if not path.is_file():
        return []
    metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    raw_decisions = metadata.get("decisions")
    if not isinstance(raw_decisions, list):
        return []
    return [_decode_record(raw) for raw in raw_decisions if isinstance(raw, dict)]


def write_decisions(
    concept_id: str, bundle_dir: Path, *, records: list[DecisionRecord]
) -> Path:
    """(Re)write `concept_id`'s CONTRADICTION decisions to hold EXACTLY
    `records` -- mirrors `bundle.ledger.write_entries`'s full-replace
    contract, scoped to this one kind. An empty `records` list removes the
    sidecar only when it holds no identity decisions either; removing an
    already-absent sidecar is a no-op, not an error.

    Since #797 the full replace is scoped to the `decisions` list rather
    than the whole file: identity decisions in the same sidecar are read
    back and rewritten untouched, so persisting one kind never erases the
    other's human rulings.

    Written via `fsio.write_atomic`, over `okf.dump_frontmatter`'s output
    with an empty body (ADR-0002 invariant 3, preserved literally)."""
    path = decisions_path_for(concept_id, bundle_dir)
    return rewrite_both_at(
        path,
        concept_id=concept_id,
        records=records,
        identity_records=read_identity_decisions_at(path),
    )


def rewrite_both_at(
    path: Path,
    *,
    concept_id: str,
    records: list[DecisionRecord],
    identity_records: list[IdentityDecisionRecord],
) -> Path:
    """The one writer both kinds go through: emits the container holding
    EXACTLY `records` and `identity_records`, and removes the file only
    when BOTH are empty.

    Every public writer funnels here so the "removing one kind must not
    delete a sidecar the other kind still needs" rule is enforced in a
    single place rather than re-derived per caller."""
    if not records and not identity_records:
        if path.is_file():
            path.unlink()
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    container = _encode_container(concept_id, records, identity_records)
    fsio.write_atomic(path, okf.dump_frontmatter(container, body=""))
    return path


def rewrite_decisions_at(
    path: Path, *, concept_id: str, records: list[DecisionRecord]
) -> Path:
    """(Re)write the sidecar AT `path` to hold EXACTLY `records`, using
    `concept_id` only as container CONTENT -- never to derive the path.

    The walked-path twin of `bundle.ledger.rewrite_entries_at`: the privacy
    sweep must write each rewrite back to the path it WALKED, not to a path
    rebuilt from the (possibly drifted or hostile) `concept_id` frontmatter
    field, which would let a traversal id escape the bundle and silently
    scrub the wrong file. `write_decisions` is the id-addressed wrapper. An
    empty `records` list removes the file -- unless the sidecar also holds
    identity decisions (#797), which are preserved and keep the file alive;
    removing an absent file is a no-op."""
    return rewrite_both_at(
        path,
        concept_id=concept_id,
        records=records,
        identity_records=read_identity_decisions_at(path),
    )


def iter_decisions(bundle_dir: Path) -> list[Path]:
    """Every committed decision sidecar under `bundle_dir`'s decisions
    root, sorted -- the ONE shared INCLUDE-walk primitive `purge`/`forget`
    reuse for their privacy sweep (Decision 4), mirroring `bundle.ledger.
    iter_ledgers`. A missing decisions root (no decision has ever been
    written) returns `[]` rather than raising."""
    root = decisions_root(bundle_dir)
    if not root.is_dir():
        return []
    return sorted(root.rglob(f"*{DECISIONS_SUFFIX}"))
