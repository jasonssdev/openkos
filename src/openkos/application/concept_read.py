"""`get`'s read core (mcp-read-surface, #1009, design Decision 4): resolves a
caller-supplied concept id, reads its frontmatter and body once, and returns
a curated, fixed field set -- never a frontmatter passthrough (mcp spec:
"`get` Returns A Curated Field Set, Never A Frontmatter Passthrough"). This
fails closed against a frontmatter key added later: only the fields named on
`ConceptRecord` can ever reach a caller through `get`.

`read_concept` performs no disclosure filtering of its own -- that is
`mcp/gate.py`'s job, at the adapter boundary (design Decision 2). This module
stays a plain, complete read, exactly like every other `application/*`
service: `mcp.gate` is the only caller allowed to decide what a `get` result
may disclose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from openkos import config, lifecycle, read_outcome
from openkos.application import lifecycle as application_lifecycle
from openkos.model import okf


class ConceptNotFound(Exception):
    """Raised when a caller-supplied concept id fails path-safety validation
    (an absolute id, a `..` segment, a reserved name, a symlink escaping the
    bundle) or does not resolve to a file on disk -- `application_lifecycle.
    resolve_concept_path`'s `ValueError`, re-raised under this module's own
    name so a caller never needs to catch a bare `ValueError` to recognize
    it (design Decision 4). This is the path-traversal defense for a
    caller-supplied id: no read of any kind is attempted before this check
    runs."""


@dataclass(frozen=True)
class ConceptRecord:
    """The curated field set `get` may ever return for a readable concept.

    `sensitivity` carries the RAW frontmatter value (unnormalized, `object`)
    -- for the gate only (design Decision 4): `mcp.gate.disclose_get` is the
    one place that decides what it means, echoing it back only when it is a
    recognized `okf.SENSITIVITY_ORDER` member."""

    concept_id: str
    sensitivity: object
    type: str | None
    title: str
    description: str
    status: Literal["active", "deprecated"]
    body: str
    relations: tuple[okf.Relation, ...]
    provenance: tuple[str, ...]
    not_run: tuple[read_outcome.NotRun, ...] = ()


@dataclass(frozen=True)
class UnreadableConcept:
    """The target resolved to a file that could not be read or whose
    frontmatter could not be parsed (ADR-0022: incompleteness is data, never
    an exception)."""

    concept_id: str


def _collapsed_title(metadata: dict[str, object]) -> str:
    raw_title = metadata.get("title")
    return " ".join(str(raw_title).split()) if raw_title else ""


def _provenance_entries(metadata: dict[str, object]) -> tuple[str, ...]:
    raw_provenance = metadata.get("provenance")
    if not isinstance(raw_provenance, list):
        return ()
    return tuple(entry for entry in raw_provenance if isinstance(entry, str))


def read_concept(
    layout: config.WorkspaceLayout, concept_id: str
) -> ConceptRecord | UnreadableConcept:
    """Resolve `concept_id` and return its curated record, or
    `UnreadableConcept` when its content could not be read or parsed.

    Raises `ConceptNotFound` for a caller-supplied id that fails
    path-safety validation or does not exist -- BEFORE any read is
    attempted (design Decision 4's path-traversal defense, mirroring
    `forget`'s Phase A gate). One `read_text` + `okf.load_frontmatter`, per
    design's "Exactly One" posture: an `OSError`, `UnicodeDecodeError`, or a
    frontmatter parse failure all return `UnreadableConcept` rather than
    raising.
    """
    try:
        concept_path, canonical_id = application_lifecycle.resolve_concept_path(
            layout.bundle_dir, concept_id
        )
    except ValueError as exc:
        raise ConceptNotFound(str(exc)) from exc

    try:
        text = concept_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return UnreadableConcept(concept_id=canonical_id)

    try:
        metadata, body = okf.load_frontmatter(text)
    except Exception:  # broad: a concurrent edit can corrupt frontmatter
        return UnreadableConcept(concept_id=canonical_id)

    not_run: list[read_outcome.NotRun] = []
    try:
        relations: tuple[okf.Relation, ...] = tuple(okf.decode_relations(metadata))
    except ValueError as exc:
        relations = ()
        not_run.append(read_outcome.NotRun(label="relations", reason=str(exc)))

    raw_type = metadata.get("type")
    deprecated = lifecycle.deprecated_concept_ids(layout.bundle_dir)
    status: Literal["active", "deprecated"] = (
        "deprecated" if canonical_id in deprecated else "active"
    )

    return ConceptRecord(
        concept_id=canonical_id,
        sensitivity=metadata.get("sensitivity"),
        type=raw_type if isinstance(raw_type, str) else None,
        title=_collapsed_title(metadata),
        description=str(metadata.get("description") or ""),
        status=status,
        body=body,
        relations=relations,
        provenance=_provenance_entries(metadata),
        not_run=tuple(not_run),
    )
