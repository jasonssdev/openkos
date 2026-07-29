"""Single-pass bundle enumerator and type-filter vocabulary resolver backing
the `list` CLI verb (`openspec/changes/discover-concept-ids/design.md`,
decisions D1-D7).

`list_objects` is the ONLY consumer-facing entry point: one
`okf._iter_docs` walk derives id/link_dir/title/sensitivity/status/readable
for every row in a single pass -- no second walk, and no call to
`lifecycle.deprecated_concept_ids` (spec: "Exactly One Bundle Walk").
`resolve_link_dir` is the CLI's TYPE-filter vocabulary resolver, built from
`model.types.REGISTRY` directly (design D7 gotcha: NOT from
`types.TYPE_TO_LINK_DIR`, which is `llm_classifiable`-only and omits
`Source` -- reusing it would make `list sources` work while `list Source`
failed).

Canonical layer: imports only `model.okf` + `model.types` + stdlib, the same
import shape `bundle/index.py`, `bundle/merge.py`, and `bundle/relations.py`
already use (AGENTS.md layering -- the canonical layer never depends on the
derived layer: `retrieval`, `graph`, `memory`, `resolution`).
"""

from dataclasses import dataclass, replace
from pathlib import Path

from openkos.model import okf
from openkos.model.types import REGISTRY


@dataclass(frozen=True)
class BundleObject:
    """One row of `list_objects`'s result: everything the `list` CLI verb
    needs to print, derived in a single `okf._iter_docs` pass (design D2).
    """

    concept_id: str
    """`scan.path` relative to `bundle_dir`, extension-less, POSIX-separated
    -- the OKF concept id (identity is the path, never a frontmatter
    field)."""

    link_dir: str
    """First path segment of `concept_id`, derived STRUCTURALLY -- never
    from frontmatter `type` -- so it stays correct even for an unparseable
    document. `""` for a bundle-root document."""

    title: str
    """Whitespace-collapsed frontmatter title (`" ".join(str(raw).split())`
    so a hand-edited multiline YAML title cannot break the one-line-per-row
    output). `""` when absent or the document is unreadable."""

    sensitivity: str
    """A `okf.SENSITIVITY_ORDER` member when the frontmatter value is one,
    else the literal `"unknown"`. Deliberately NOT `blocks_llm_send`'s
    fail-closed answer and NOT `okf._rank`'s `private` default (design D2)
    -- this column reports what the document *says*; it gates nothing."""

    status: str
    """`"deprecated"` when the document's own `status` field says so, or
    when its id is the target of any other document's `supersedes` edge
    (design D4, replicating `lifecycle`'s pinned R2 fail-safe rule, no
    cycle-detection exemption); `"active"` otherwise."""

    readable: bool
    """`False` when the underlying `DocScan` carried a `read_error` or
    `parse_error` -- lets the CLI render `(unreadable)` distinctly from
    `(untitled)` (design D2/D5)."""


def list_objects(bundle_dir: Path) -> list[BundleObject]:
    """Enumerate every non-reserved `.md` file under `bundle_dir` into one
    `BundleObject` row each, in exactly one `okf._iter_docs` walk (design
    D3: this is the ONLY bundle-reading call in this function -- no second
    walk, no call to `lifecycle.deprecated_concept_ids`).

    Fail-visible, not fail-closed (design D5): a document that failed to
    read or parse is still listed -- its `concept_id`/`link_dir` come from
    its path and need no successful read -- with `title=""`,
    `sensitivity="unknown"`, `readable=False`. Nothing here raises.

    Status is derived in the same pass, then resolved in memory after the
    loop (design D4): each document's outbound `supersedes` edges
    (`okf.decode_relations`, `ValueError` on malformed `relations:` caught
    and dropped -- no crash, no edges for that document) are collected
    while walking; a row's final `status` is `"deprecated"` when its own
    frontmatter `status` was `"deprecated"` OR its id is the target of any
    non-self `supersedes` edge, else `"active"`. This intentionally
    replicates `lifecycle.deprecated_concept_ids`'s R2 fail-safe rule
    verbatim, including its no-cycle-detection exemption, rather than
    calling it -- calling it would be a second bundle walk.
    """
    own_status: dict[str, str] = {}
    rows_without_status: dict[str, BundleObject] = {}
    supersedes: set[tuple[str, str]] = set()

    for scan in okf._iter_docs(bundle_dir):
        # Concept-id/link_dir derivation is duplicated a third time here
        # (also spelled at `lifecycle.py:70` and `sensitivity.py:116`) --
        # deliberately deferred (design D2): extracting a shared
        # `okf.concept_id_for` helper is a follow-up only at a fourth call
        # site, or when the derivation itself changes -- not now.
        concept_id = scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
        link_dir = concept_id.split("/", 1)[0] if "/" in concept_id else ""

        if scan.read_error is not None or scan.parse_error is not None:
            own_status[concept_id] = ""
            rows_without_status[concept_id] = BundleObject(
                concept_id=concept_id,
                link_dir=link_dir,
                title="",
                sensitivity="unknown",
                status="active",  # placeholder, resolved after the loop
                readable=False,
            )
            continue

        meta = scan.metadata or {}

        raw_title = meta.get("title")
        title = " ".join(str(raw_title).split()) if raw_title else ""

        raw_sensitivity = str(meta.get("sensitivity") or "").strip()
        sensitivity = (
            raw_sensitivity if raw_sensitivity in okf.SENSITIVITY_ORDER else "unknown"
        )

        own_status[concept_id] = str(meta.get("status") or "")
        rows_without_status[concept_id] = BundleObject(
            concept_id=concept_id,
            link_dir=link_dir,
            title=title,
            sensitivity=sensitivity,
            status="active",  # placeholder, resolved after the loop
            readable=True,
        )

        try:
            relations = okf.decode_relations(meta)
        except ValueError:
            relations = []  # malformed relations: no edges, no crash
        for relation in relations:
            if relation.type == "supersedes" and relation.target != concept_id:
                supersedes.add((concept_id, relation.target))

    superseded = {target for _source, target in supersedes}

    # `rows_without_status` preserves the walk's insertion order, which is
    # already alphabetical (`okf._iter_docs` walks `sorted(rglob(...))`) --
    # no re-sort needed.
    return [
        replace(
            row,
            status=(
                "deprecated"
                if own_status.get(concept_id) == "deprecated"
                or concept_id in superseded
                else "active"
            ),
        )
        for concept_id, row in rows_without_status.items()
    ]


_LINK_DIRS: frozenset[str] = frozenset(ot.link_dir for ot in REGISTRY if ot.link_dir)
_NAME_TO_LINK_DIR: dict[str, str] = {
    ot.name: ot.link_dir for ot in REGISTRY if ot.link_dir
}
"""Built from `REGISTRY` directly -- NOT from `types.TYPE_TO_LINK_DIR`,
which is `llm_classifiable`-only and therefore omits `Source` (design D7
gotcha)."""


def resolve_link_dir(raw: str) -> str | None:
    """Resolve a `list` CLI TYPE-filter argument to its canonical
    `link_dir`: an exact canonical `link_dir` match first, then a
    case-sensitive `REGISTRY.name` alias, else `None` for anything
    unrecognized (design D7)."""
    if raw in _LINK_DIRS:
        return raw
    return _NAME_TO_LINK_DIR.get(raw)
