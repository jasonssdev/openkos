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

DECISIONS_SCHEMA: Final = "openkos.contradiction_decisions/v1"
"""The committed sidecar's own `schema` key, versioned independently of any
single `DecisionRecord`'s shape -- mirrors `bundle.ledger.
LEDGER_SIDECAR_SCHEMA`."""

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
    concept_id: str, records: list[DecisionRecord]
) -> dict[str, object]:
    return {
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
    path = decisions_path_for(concept_id, bundle_dir)
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
    """(Re)write `concept_id`'s committed sidecar to hold EXACTLY `records`,
    replacing whatever it held before -- mirrors `bundle.ledger.
    write_entries`'s full-replace contract. An empty `records` list removes
    the sidecar file entirely; removing an already-absent sidecar is a
    no-op, not an error.

    Written via `fsio.write_atomic`, over `okf.dump_frontmatter`'s output
    with an empty body (ADR-0002 invariant 3, preserved literally)."""
    path = decisions_path_for(concept_id, bundle_dir)
    if not records:
        if path.is_file():
            path.unlink()
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    container = _encode_container(concept_id, records)
    fsio.write_atomic(path, okf.dump_frontmatter(container, body=""))
    return path


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
