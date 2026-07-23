"""OKF (Open Knowledge Format) adapter.

The one seam that knows the on-disk shape of OKF v0.1: frontmatter framing,
reserved filenames, and the conformance rules of §9. Nothing outside this
module parses or emits frontmatter, or reasons about reserved files
(AGENTS.md:41, docs/architecture.md:113).

All three §9 rules are implemented here: rules 1-2 walk every non-reserved
`.md` file (`_iter_docs`), and rule 3 walks the reserved files themselves
(`index.md`/`log.md`) to check their fixed structure per §6/§7/§11.
"""

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final

import frontmatter

from openkos.model.types import CLASSIFIABLE_TYPES as _CONCEPT_TYPES

OKF_VERSION: Final = "0.1"
"""The OKF version this engine targets and declares, per §11."""

RESERVED_FILENAMES: Final[frozenset[str]] = frozenset({"index.md", "log.md"})
"""§6/§7 give these a fixed structure; §9 rule 1 exempts them from frontmatter."""

_LOG_HEADING_RE: Final = re.compile(r"^## (.+)$", re.MULTILINE)
"""Every level-2 heading in a `log.md`, per §7. `### ` cannot false-match:
`^## ` requires a space in the 3rd position."""

_ISO_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
"""§7's date-heading format, checked for shape only -- not calendar-validated
(e.g. `2026-13-45` matches)."""

SENSITIVITY_ORDER: Final[tuple[str, str, str]] = ("public", "private", "confidential")
"""Least-to-most-restrictive sensitivity ordering (ADR-0003, KOM
docs/knowledge-object-model.md:255-272): a derived object is at least as
sensitive as its most sensitive source."""

RELATIONS_KEY: Final = "relations"
"""The optional frontmatter key holding a document's outbound typed edges
(spec: "`relations:` Frontmatter Field Shape"). Ordinary OKF data, per §4.1
tolerance -- logically placed after `provenance` and before `merged_from`
in a metadata dict literal, though `dump_frontmatter`'s YAML emission
always re-sorts keys alphabetically regardless of that insertion order."""

MERGED_FROM_KEY: Final = "merged_from"
"""The survivor frontmatter key holding the reversibility ledger (ADR-0002):
an ordinary OKF data key, not a new file type, per §4.1 tolerance."""

MERGE_LEDGER_SCHEMA_V1: Final = "openkos.merge_ledger/v1"
"""The `schema` value every pre-slice-2a `merged_from` entry carries -- a
durable on-disk contract (ADR-0002) that a future format change must
migrate rather than silently reinterpret. A V1 entry never carries
`relation_rewrites`; `decode_merge_ledger_entry` treats an absent key on
this schema as `[]` (design D1)."""

MERGE_LEDGER_SCHEMA_V2: Final = "openkos.merge_ledger/v2"
"""The `schema` value every `merged_from` entry written from slice 2a
onward carries (design D1; ADR-0005): the ONLY additive change from V1 is
the REQUIRED `relation_rewrites` key (whole-file third-party snapshots for
inbound typed-relation retargets). `plan_merge` always writes V2; the
reader accepts both V1 and V2 (spec: "Pre-slice-2a v1 ledger entry still
unmerges exactly")."""


def dump_frontmatter(metadata: dict[str, object], body: str = "") -> str:
    """Render `metadata` as a YAML frontmatter block over `body`, per §4.1."""
    post = frontmatter.Post(body)
    post.metadata = metadata
    return frontmatter.dumps(post) + "\n"


def load_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the frontmatter block and body out of `text`, per §4.1."""
    post = frontmatter.loads(text)
    return post.metadata, post.content


def build_source_concept(
    *,
    title: str,
    description: str,
    resource: str,
    tags: list[str],
    timestamp: str,
    sensitivity: str,
    provenance: list[str],
    raw_content: str | None = None,
) -> str:
    """Build a conformant OKF Source concept document (D4/ingest-source-body D1).

    Plain dict -> `dump_frontmatter`, no pydantic: every field is
    engine-derived from trusted local inputs (workspace config, the source's
    filename, an injected clock, the raw path) rather than untrusted
    structured LLM output, so `check_conformance` (§9 rules 1-2: parseable
    frontmatter, non-empty `type`) is the only gate this slice needs.
    `description` is passed through verbatim -- callers MUST phrase it as an
    honest description of the source's embedding state (embedded verbatim,
    or could not be embedded), never claiming extraction/compilation
    occurred, matching this slice's scope.

    `raw_content` (ingest-source-body D1/D3) renders one of three body
    shapes, each honest about what happened: `raw_content` holding
    non-blank text embeds it verbatim under a `## Source content` heading;
    `None` (a decode failure) renders a short note that the content could
    not be embedded as text; blank/whitespace-only text renders a distinct
    "source is empty" note. All three end with `# Citations`.
    """
    metadata: dict[str, object] = {
        "type": "Source",
        "title": title,
        "description": description,
        "resource": resource,
        "tags": tags,
        "timestamp": timestamp,
        "status": "active",
        "version": 1,
        "freshness": "snapshot",
        "sensitivity": sensitivity,
        "provenance": provenance,
    }
    if raw_content is None:
        section = (
            "_Source content could not be embedded as text "
            "(binary or non-UTF-8); see the linked resource._\n\n"
        )
    elif not raw_content.strip():
        section = "_The source file is empty._\n\n"
    else:
        section = f"## Source content\n\n{raw_content}\n\n"
    body = f"# {title}\n\n{description}\n\n{section}# Citations\n"
    return dump_frontmatter(metadata, body)


def build_concept(
    *,
    type: str,
    title: str,
    description: str,
    body: str,
    provenance: list[str],
    sensitivity: str,
    timestamp: str,
    related_note: str = "source this was extracted from",
) -> str:
    """Build a conformant OKF derived-object document from LLM-extracted,
    UNTRUSTED fields (design: "Builder validation").

    Unlike `build_source_concept` (whose inputs are engine-derived and
    trusted, so it skips validation -- see its docstring), this builder is
    the fail-closed gate for `extraction.ExtractionResult` data: `type` MUST
    be a member of the closed classifiable vocabulary (see
    `openkos.model.types.CLASSIFIABLE_TYPES`, the single source of truth);
    `title`/`description` MUST be non-empty
    after stripping whitespace AND single-line (no embedded newlines, since
    each is a single Markdown/heading line); and `provenance` MUST be
    non-empty (a derived object always cites the Source it came from). Any
    violation raises `ValueError` rather than emitting a non-conformant or
    misleading document.

    `description` is a one-line lede; `body` follows it only when non-blank,
    so a blank body does not duplicate the description paragraph. A `## Related`
    section then backlinks every `provenance` entry -- each a Source concept-id
    path such as `sources/<slug>` -- using `related_note` as the trailing
    phrase (default: "source this was extracted from", today's ingest
    literal -- ingest never passes this kwarg, so its output stays
    byte-identical). A filed `query --save` answer passes a concept-to-concept
    phrasing instead (design: "Parameterize `## Related` wording (byte-identical
    ingest)"). `tags` is always `[]`: this slice has no tagging step.
    """
    if type not in _CONCEPT_TYPES:
        raise ValueError(f"type must be one of {sorted(_CONCEPT_TYPES)}, got {type!r}")
    if not title.strip():
        raise ValueError("title must be non-empty")
    if not description.strip():
        raise ValueError("description must be non-empty")
    if "\n" in title or "\r" in title:
        raise ValueError("title must not contain newlines")
    if "\n" in description or "\r" in description:
        raise ValueError("description must not contain newlines")
    if not provenance:
        raise ValueError("provenance must be non-empty for a derived object")

    metadata: dict[str, object] = {
        "type": type,
        "title": title,
        "description": description,
        "tags": [],
        "timestamp": timestamp,
        "status": "active",
        "version": 1,
        "freshness": "snapshot",
        "sensitivity": sensitivity,
        "provenance": provenance,
    }
    related = "\n".join(f"- [{ref}](/{ref}.md) — {related_note}" for ref in provenance)
    # `description` is a one-line lede; append `body` only when it adds content,
    # so a blank-body fallback does not render the description paragraph twice.
    lede = description if not body.strip() else f"{description}\n\n{body}"
    doc_body = f"# {title}\n\n{lede}\n\n## Related\n\n{related}\n"
    return dump_frontmatter(metadata, doc_body)


def _rank(value: object) -> int:
    """Rank a raw sensitivity `value` into `SENSITIVITY_ORDER`'s index space,
    failing closed on anything dirty (ADR-0003).

    A missing (`None`) or blank/whitespace-only string ranks as `private`
    (the config default floor, docs/knowledge-object-model.md's
    `default_sensitivity`). A string matching (after stripping) one of
    `SENSITIVITY_ORDER`'s canonical members ranks at its position. Anything
    else -- a non-string value (e.g. an `int`/`list` from dirty frontmatter)
    or an unrecognized string -- ranks as `confidential`, the most
    restrictive level: a security field must fail toward MORE restrictive,
    never less.
    """
    if value is None:
        return SENSITIVITY_ORDER.index("private")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return SENSITIVITY_ORDER.index("private")
        if stripped in SENSITIVITY_ORDER:
            return SENSITIVITY_ORDER.index(stripped)
    return SENSITIVITY_ORDER.index("confidential")


def combine_sensitivity(a: object, b: object) -> str:
    """Combine two sensitivity values into the more restrictive (max-rank)
    of the two, per ADR-0003's high-water-mark rule.

    Pure, deterministic, stdlib-only: no I/O. Always returns a canonical
    member of `SENSITIVITY_ORDER`, even when `a`/`b` are missing or
    malformed (`_rank` fails closed). This is the recompute step a merge
    invokes at build time -- the result is never a verbatim copy of either
    input's sensitivity.
    """
    return SENSITIVITY_ORDER[max(_rank(a), _rank(b))]


@dataclass(frozen=True)
class LinkRewrite:
    """One inbound-link rewrite performed (or to be reversed) by a merge
    (spec: Inbound-Link Rewrite; ADR-0002's `link_rewrites`).

    `file` is the bundle-relative path the rewrite happened in; `old_link`/
    `new_link` are the exact markdown link targets substituted -- the
    values `bundle/links.py` (U3) needs to bound its reversal to these
    specific recorded occurrences, never a blind replace-all.

    `offset` is the character offset, in the POST-merge `file` text, where
    THIS rewrite's `new_link` occurrence begins. It is the positional
    disambiguator `reverse_link_rewrites` needs: when a file links to BOTH
    the absorbed AND survivor concepts, after the merge there are TWO
    `](/survivor.md)`-shaped occurrences in that file (one just rewritten,
    one coincidentally pre-existing) and a target-string-only reverse
    cannot tell them apart -- it may revert the wrong one and break
    byte-parity. Reversing at the exact recorded `offset` instead removes
    the ambiguity entirely."""

    file: str
    old_link: str
    new_link: str
    offset: int


@dataclass(frozen=True)
class RelationRewrite:
    """One third-party file's whole-file pre-merge snapshot, recorded when
    that file's `relations:` targeted the absorbed id (design D1/D3; spec:
    "Third-party inbound relations retarget to the survivor").

    Unlike `LinkRewrite` (reversed at an exact character `offset`),
    `snapshot` is the file's FULL verbatim bytes immediately BEFORE this
    merge -- a `relations:` retarget/drop/dedupe has no stable
    disambiguating position analogous to a link occurrence, so
    `bundle/relations.py::reverse_relation_rewrites` always restores by
    ABSOLUTE whole-file overwrite, never offset math (design D4's
    overlapping-LIFO proof relies on this)."""

    file: str
    snapshot: str


@dataclass(frozen=True)
class MergeLedgerEntry:
    """One `merged_from` list entry: the FULL pre-merge snapshot set for one
    absorbed object (spec: Reversibility Ledger; ADR-0002).

    Round-trip parity is logically impossible from `absorbed_snapshot`
    alone -- provenance union, tag union, sensitivity high-water-mark, and
    freshness-most-recent are all lossy/non-invertible -- so every field
    below is required. `survivor_before` is the survivor's FULL verbatim
    bytes immediately prior to THIS merge's write, explicitly RETAINING any
    prior `merged_from` entries from earlier merges (it excludes ONLY this
    entry, which does not yet exist at snapshot time); it does NOT strip
    the whole `merged_from` key. This is what lets sequential pairwise
    merges reverse losslessly in LIFO order.

    `sensitivity_before` uses `""` (empty string) as the sentinel for
    "survivor had no `sensitivity` key at merge time" -- distinct from the
    canonical `public`/`private`/`confidential` values `SENSITIVITY_ORDER`
    defines.

    `relation_rewrites` (design D1, v2 addition) holds one whole-file
    snapshot per third-party file whose `relations:` were retargeted,
    dropped as a self-loop, or deduped by this merge. It defaults to `[]`
    so every pre-slice-2a (v1) construction of this dataclass -- including
    every existing test helper -- keeps working unchanged; `plan_merge`
    (task 2.10) always populates it explicitly and always writes
    `MERGE_LEDGER_SCHEMA_V2`."""

    schema: str
    merged_at: str
    absorbed_id: str
    absorbed_snapshot: str
    survivor_before: str
    index_before: str
    log_before: str
    link_rewrites: list[LinkRewrite]
    sensitivity_before: str
    sensitivity_after: str
    relation_rewrites: list[RelationRewrite] = field(default_factory=list)


def encode_merge_ledger_entry(entry: MergeLedgerEntry) -> dict[str, object]:
    """Turn one `MergeLedgerEntry` into a plain-dict shape safe for
    `dump_frontmatter` -- never hand-spliced YAML (ADR-0002).

    Fails closed (`ValueError`, correction batch finding 2) when `entry.schema
    == MERGE_LEDGER_SCHEMA_V1` and `entry.relation_rewrites` is non-empty: a
    V1 entry never carries `relation_rewrites` (see `MERGE_LEDGER_SCHEMA_V1`'s
    docstring), so a caller that constructs one WITH populated
    `relation_rewrites` anyway holds a self-contradictory entry --
    `decode_merge_ledger_entry`'s V1 branch unconditionally discards that
    key, so silently encoding it here would let it round-trip to `[]`
    without any signal. Raising here, rather than silently dropping the
    field to match, surfaces the construction bug at its source instead of
    at a much later, harder-to-trace decode."""
    if entry.schema == MERGE_LEDGER_SCHEMA_V1 and entry.relation_rewrites:
        raise ValueError(
            "a MERGE_LEDGER_SCHEMA_V1 entry must not carry relation_rewrites"
        )
    return {
        "schema": entry.schema,
        "merged_at": entry.merged_at,
        "absorbed_id": entry.absorbed_id,
        "absorbed_snapshot": entry.absorbed_snapshot,
        "survivor_before": entry.survivor_before,
        "index_before": entry.index_before,
        "log_before": entry.log_before,
        "link_rewrites": [
            {
                "file": lr.file,
                "old_link": lr.old_link,
                "new_link": lr.new_link,
                "offset": lr.offset,
            }
            for lr in entry.link_rewrites
        ],
        "sensitivity_before": entry.sensitivity_before,
        "sensitivity_after": entry.sensitivity_after,
        "relation_rewrites": [
            {"file": rr.file, "snapshot": rr.snapshot} for rr in entry.relation_rewrites
        ],
    }


def encode_merged_from(entries: list[MergeLedgerEntry]) -> list[dict[str, object]]:
    """Encode a full `merged_from` list (LIFO order preserved) for assignment
    onto a survivor's frontmatter metadata dict before `dump_frontmatter`."""
    return [encode_merge_ledger_entry(entry) for entry in entries]


def _decode_link_rewrite(raw: object) -> LinkRewrite:
    """Parse one `link_rewrites` list item back into a `LinkRewrite`, failing
    closed (`ValueError`) on anything malformed. `offset` is required (not
    defaulted to `0`) -- a ledger entry missing it must never be silently
    misread, since `reverse_link_rewrites` trusts it for exact positional
    reversal."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"link_rewrites entry must be a mapping, got {type(raw).__name__}"
        )
    try:
        return LinkRewrite(
            file=str(raw["file"]),
            old_link=str(raw["old_link"]),
            new_link=str(raw["new_link"]),
            offset=int(raw["offset"]),
        )
    except KeyError as exc:
        raise ValueError(f"link_rewrites entry missing field {exc}") from exc


def _decode_relation_rewrite(raw: object) -> RelationRewrite:
    """Parse one `relation_rewrites` list item back into a
    `RelationRewrite`, failing closed (`ValueError`) on anything malformed
    -- mirrors `_decode_link_rewrite`."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"relation_rewrites entry must be a mapping, got {type(raw).__name__}"
        )
    try:
        return RelationRewrite(file=str(raw["file"]), snapshot=str(raw["snapshot"]))
    except KeyError as exc:
        raise ValueError(f"relation_rewrites entry missing field {exc}") from exc


def decode_merge_ledger_entry(raw: object) -> MergeLedgerEntry:
    """Parse one `merged_from` list item back into a `MergeLedgerEntry`,
    failing closed (`ValueError`) on any malformed or missing field -- a
    corrupt ledger entry must never be silently misread, since `unmerge`
    trusts it for byte-for-byte restoration.

    `schema` branches (design D1): V1 -> `relation_rewrites` defaults to
    `[]` regardless of whether the raw dict happens to carry that key (a
    genuine pre-slice-2a entry never has it at all -- spec: "Pre-slice-2a
    v1 ledger entry still unmerges exactly"); V2 -> the `relation_rewrites`
    key is REQUIRED, and its absence (or a malformed item within it) fails
    closed exactly like any other required V2 field; any other schema
    string is unsupported and rejected outright."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"merged_from entry must be a mapping, got {type(raw).__name__}"
        )
    try:
        schema = str(raw["schema"])
        relation_rewrites: list[RelationRewrite]
        if schema == MERGE_LEDGER_SCHEMA_V1:
            relation_rewrites = []
        elif schema == MERGE_LEDGER_SCHEMA_V2:
            relation_rewrites = [
                _decode_relation_rewrite(item) for item in raw["relation_rewrites"]
            ]
        else:
            raise ValueError(f"unsupported merged_from schema version: {schema!r}")
        link_rewrites = [_decode_link_rewrite(item) for item in raw["link_rewrites"]]
        return MergeLedgerEntry(
            schema=schema,
            merged_at=str(raw["merged_at"]),
            absorbed_id=str(raw["absorbed_id"]),
            absorbed_snapshot=str(raw["absorbed_snapshot"]),
            survivor_before=str(raw["survivor_before"]),
            index_before=str(raw["index_before"]),
            log_before=str(raw["log_before"]),
            link_rewrites=link_rewrites,
            sensitivity_before=str(raw["sensitivity_before"]),
            sensitivity_after=str(raw["sensitivity_after"]),
            relation_rewrites=relation_rewrites,
        )
    except KeyError as exc:
        raise ValueError(f"merged_from entry missing field {exc}") from exc
    except TypeError as exc:
        raise ValueError(f"merged_from entry malformed: {exc}") from exc


def decode_merged_from(metadata: dict[str, object]) -> list[MergeLedgerEntry]:
    """Read the `merged_from` ledger list off a survivor's `metadata`.

    Absent key returns `[]` (no prior merges). A present-but-non-list value
    fails closed (`ValueError`) -- a corrupt ledger key must never be
    silently ignored."""
    raw = metadata.get(MERGED_FROM_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"{MERGED_FROM_KEY!r} must be a list, got {type(raw).__name__}"
        )
    return [decode_merge_ledger_entry(item) for item in raw]


@dataclass(frozen=True)
class Relation:
    """One `relations:` list entry: a typed outbound edge from this document
    to `target` (spec: "`relations:` Frontmatter Field Shape"; design:
    SHAPE).

    `target` is the bundle-relative concept-id the edge points to, `.md`
    stripped -- byte-identical to how `provenance` (`sources/<slug>`) and
    `MergeLedgerEntry.absorbed_id` reference objects today (NOT a
    `/...md` link, NOT a bare slug). `type` is the edge's relation-type
    string: any non-empty, single-line value round-trips through this
    codec -- `model/relations.py::validate_relation_type`'s WARN-on-unknown
    gate is enforced by the `relate` CLI verb, not here; this layer only
    rejects an empty/whitespace value or one containing `\\n`/`\\r`."""

    target: str
    type: str


def _validate_relation_field(field_name: str, value: str) -> str:
    """Shared fail-closed guard for a `Relation` field: non-empty after
    stripping, and no embedded `\\n`/`\\r` (mirrors the existing index/log
    newline-injection guards -- spec: "Newline in target or type is
    rejected")."""
    if "\n" in value or "\r" in value:
        raise ValueError(f"relation {field_name} must not contain newlines")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"relation {field_name} must be non-empty")
    return stripped


def _normalize_relation_path(value: str) -> str:
    """Normalize a bundle-relative path-shaped string to its canonical
    relative-posix form (correction batch, finding 2): strip a leading `/`
    (a hand-authored target may mirror this codebase's own
    `[text](/id.md)` link style) and collapse redundant separators/`.`
    segments (e.g. `concepts//absorbed`, `./concepts/absorbed`) via
    `PurePosixPath`.

    Deliberately does NOT reject `..` traversal -- that is left as-is (it
    simply will not match any real node/target-id, same as today) rather
    than adding a new path-security layer in this batch; a future slice may
    add explicit rejection if a real need for one is found."""
    normalized = PurePosixPath(value.lstrip("/")).as_posix()
    return "" if normalized == "." else normalized


def _validate_relation_target(value: str) -> str:
    """Shared target-normalization guard: fail-closed field validation, then
    canonicalize the path shape (leading `/`, redundant separators) and
    strip a `.md` suffix (design: SHAPE), then re-check non-empty (a target
    that is non-empty only by virtue of its `.md` suffix, e.g. exactly
    ".md", must still be rejected).

    Shared by `encode_relation` and `decode_relation` so this normalization
    is symmetric on both sides of the codec: a stored non-canonical target
    (e.g. hand-edited with a leading `/`, or a `.md` suffix) always decodes
    to the same canonical form it would have been encoded to, keeping the
    codec round-trip stable and both `relate`'s idempotency dedup and the
    merge guard's/graph's raw string-equality target match correct
    regardless of how the `relations:` entry was produced."""
    target = _normalize_relation_path(
        _validate_relation_field("target", value)
    ).removesuffix(".md")
    if not target:
        raise ValueError("relation target must be non-empty")
    return target


def encode_relation(relation: Relation) -> dict[str, object]:
    """Turn one `Relation` into a plain-dict shape safe for
    `dump_frontmatter`, with `target`'s `.md` suffix stripped (design:
    SHAPE)."""
    target = _validate_relation_target(relation.target)
    rel_type = _validate_relation_field("type", relation.type)
    return {"target": target, "type": rel_type}


def encode_relations(relations: list[Relation]) -> list[dict[str, object]]:
    """Encode a full `relations:` list for assignment onto a document's
    frontmatter metadata dict before `dump_frontmatter`.

    Entries are SORTED by `(target, type)` (task 1.6) for deterministic
    re-emission and stable dedup, regardless of the order they were built
    in."""
    encoded = [encode_relation(relation) for relation in relations]
    return sorted(encoded, key=lambda entry: (entry["target"], entry["type"]))


def decode_relation(raw: object) -> Relation:
    """Parse one `relations:` list item back into a `Relation`, failing
    closed (`ValueError`) on anything malformed -- a corrupt or hand-edited
    `relations:` entry must never be silently misread."""
    if not isinstance(raw, dict):
        raise ValueError(f"relations entry must be a mapping, got {type(raw).__name__}")
    try:
        target = str(raw["target"])
        rel_type = str(raw["type"])
    except KeyError as exc:
        raise ValueError(f"relations entry missing field {exc}") from exc
    target = _validate_relation_target(target)
    rel_type = _validate_relation_field("type", rel_type)
    return Relation(target=target, type=rel_type)


def decode_relations(metadata: dict[str, object]) -> list[Relation]:
    """Read the `relations:` list off a document's `metadata`.

    Absent key returns `[]` (no relations -- spec: "Absent relations key is
    valid"). A present-but-non-list value fails closed (`ValueError`) -- a
    corrupt `relations:` key must never be silently ignored."""
    raw = metadata.get(RELATIONS_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{RELATIONS_KEY!r} must be a list, got {type(raw).__name__}")
    return [decode_relation(item) for item in raw]


def merge_relations(
    survivor_relations: list[Relation],
    absorbed_relations: list[Relation],
    *,
    survivor_id: str,
    absorbed_id: str,
) -> tuple[list[Relation], list[Relation], list[Relation]]:
    """Combine a survivor's and an absorbed object's `relations:` lists into
    the merged survivor's outbound edges (design D2; spec: "Reversible
    Typed-Relation Rewiring"): OUTBOUND move, SELF-LOOP drop, survivor-side
    DEDUPE -- the atomic pair with the guard removal (task 1.1-1.4). This is
    the ONLY place that computes the OUTBOUND-merge relation set; inbound
    third-party retargeting is a separate, later concern
    (`bundle/relations.py`, PR2).

    Every entry from `survivor_relations`, then every entry from
    `absorbed_relations`, is considered in turn (order-preserving, mirrors
    `_union_dedup`): an entry whose `target` equals `absorbed_id` is
    RETARGETED to `survivor_id` regardless of which side it came from -- the
    absorbed object's own edges move onto the survivor, and a survivor edge
    that already pointed at the soon-to-vanish absorbed id is redirected
    rather than left dangling.

    An entry is a RESULTING self-loop -- dropped, never emitted -- when its
    final target is `survivor_id` AND it came from the absorbed side (its
    source object is becoming the survivor, so any edge back at the
    survivor is now the survivor pointing at itself), OR it was retargeted
    from `absorbed_id` (the retarget itself produced the self-loop). A
    survivor-side entry that ALREADY targeted `survivor_id` before this
    merge (a pre-existing, unrelated self-loop) is left untouched -- that is
    not this merge's business to silently rewrite.

    An entry duplicating one already accepted into the merged list (by
    `(target, type)` equality) is a COLLISION -- dropped, reported, never
    duplicated.

    Returns `(merged, dropped_self_loops, deduped_collisions)`: the merged,
    order-preserving relation list (still to be re-emitted via
    `encode_relations` for its final `(target, type)` sort), plus the two
    non-silent drop reports a future preview/ledger consumes (PR3).
    """
    merged: list[Relation] = []
    dropped_self_loops: list[Relation] = []
    deduped_collisions: list[Relation] = []

    def _process(relation: Relation, *, from_absorbed: bool) -> None:
        was_retargeted = relation.target == absorbed_id
        retargeted = (
            Relation(target=survivor_id, type=relation.type)
            if was_retargeted
            else relation
        )
        if retargeted.target == survivor_id and (from_absorbed or was_retargeted):
            dropped_self_loops.append(retargeted)
            return
        if retargeted in merged:
            deduped_collisions.append(retargeted)
            return
        merged.append(retargeted)

    for relation in survivor_relations:
        _process(relation, from_absorbed=False)
    for relation in absorbed_relations:
        _process(relation, from_absorbed=True)

    return merged, dropped_self_loops, deduped_collisions


def _parse_timestamp(value: object) -> datetime | None:
    """Parse `value` as an ISO-8601 timestamp, returning `None` on anything
    unparseable (missing, non-string, or malformed) rather than raising --
    the freshness/timestamp merge rule fails closed to survivor-wins on any
    parse failure."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _absorbed_is_more_recent(
    survivor_timestamp: object, absorbed_timestamp: object
) -> bool:
    """True only if the absorbed side's `timestamp` is STRICTLY more recent
    than the survivor's. Fails closed to `False` (survivor wins) when either
    side is missing or unparseable, matching every other scalar's
    survivor-wins default -- and ALSO fails closed when both sides parse but
    are incomparable (one timezone-aware, one naive): stdlib `datetime`
    raises `TypeError` for that comparison rather than picking a winner, and
    this function must never assume a timezone to paper over it."""
    survivor_dt = _parse_timestamp(survivor_timestamp)
    absorbed_dt = _parse_timestamp(absorbed_timestamp)
    if survivor_dt is None or absorbed_dt is None:
        return False
    try:
        return absorbed_dt > survivor_dt
    except TypeError:
        return False


def _union_dedup(first: list[object], second: list[object]) -> list[object]:
    """Order-preserving dedup union: every item of `first`, then any item of
    `second` not already seen, each kept in first-seen order (spec:
    Frontmatter-Conflict Resolution, list fields).

    Uses equality-based `in` against the accumulated `result` list rather
    than a `set`, so this never calls `hash()` on an item -- a frontmatter
    list may hold UNHASHABLE items (e.g. a list of dicts, permitted by
    OKF's unknown-key tolerance), which would otherwise raise `TypeError`
    and crash a destructive merge on realistic input. These lists are tiny,
    so the resulting O(n^2) membership check is negligible. Every item of
    `first` is kept as-is (including any internal duplicates already
    present there); only `second`'s items are deduped against everything
    accumulated so far."""
    result: list[object] = list(first)
    for item in second:
        if item not in result:
            result.append(item)
    return result


def build_merged_document(
    survivor_metadata: dict[str, object],
    survivor_body: str,
    absorbed_metadata: dict[str, object],
    absorbed_body: str,
    absorbed_id: str,
    survivor_id: str,
) -> tuple[dict[str, object], str]:
    """Combine a survivor and an absorbed document into the merged survivor
    document's (metadata, body) -- the frontmatter-conflict, body-append,
    and OUTBOUND typed-relation rules (spec: Frontmatter-Conflict
    Resolution, Sensitivity High-Water-Mark Recomputation, Reversible
    Typed-Relation Rewiring). Does NOT touch the `merged_from` ledger --
    that is `bundle/merge.py::plan_merge`'s exclusive responsibility, so any
    pre-existing `merged_from` key on EITHER side is dropped here rather
    than propagated.

    Field-kind rules: a scalar present on both sides keeps the SURVIVOR's
    value; a scalar present on only one side fills the gap; a list-valued
    field (`tags`, `provenance`, or any other list) is unioned, deduped,
    order-preserving (survivor's items first); `sensitivity` is RECOMPUTED
    via `combine_sensitivity`, never copied; `freshness`+`timestamp` are
    taken TOGETHER from whichever side has the strictly more recent
    `timestamp` (`_absorbed_is_more_recent`), falling back to the
    survivor's own value when either timestamp is missing/unparseable.
    `relations:` is EXCLUDED from the generic list-union (which cannot tell
    a dangling `target: {absorbed_id}` edge or a resulting self-loop from
    any other list value) and instead computed via the dedicated
    `merge_relations` (design D2, `survivor_id` is required for its
    self-loop check): the merged document NEVER carries a relation
    targeting the now-absorbed id, nor a survivor->survivor self-loop
    introduced by this merge. An empty merged relation set omits the
    `relations:` key entirely, preserving "absent relations key is valid"
    through a merge with no edges on either side.

    Body: the survivor's body, then a delimited
    `## Merged content ({absorbed_id})` heading, then the absorbed body --
    an APPEND, never an overwrite, per the spec's "Successful merge"
    scenario.
    """
    merged: dict[str, object] = dict(survivor_metadata)
    merged.pop(MERGED_FROM_KEY, None)

    if _absorbed_is_more_recent(
        survivor_metadata.get("timestamp"), absorbed_metadata.get("timestamp")
    ):
        merged["timestamp"] = absorbed_metadata.get("timestamp")
        merged["freshness"] = absorbed_metadata.get("freshness")
    else:
        merged["timestamp"] = survivor_metadata.get("timestamp")
        merged["freshness"] = survivor_metadata.get("freshness")

    _SPECIAL_KEYS = (
        "sensitivity",
        "freshness",
        "timestamp",
        MERGED_FROM_KEY,
        RELATIONS_KEY,
    )
    for key, absorbed_value in absorbed_metadata.items():
        if key in _SPECIAL_KEYS:
            continue
        survivor_value = merged.get(key)
        if isinstance(absorbed_value, list) or isinstance(survivor_value, list):
            survivor_list = survivor_value if isinstance(survivor_value, list) else []
            absorbed_list = absorbed_value if isinstance(absorbed_value, list) else []
            merged[key] = _union_dedup(survivor_list, absorbed_list)
        elif key not in merged:
            merged[key] = absorbed_value
        # else: a scalar already present on the survivor wins -- no-op.

    merged["sensitivity"] = combine_sensitivity(
        survivor_metadata.get("sensitivity"), absorbed_metadata.get("sensitivity")
    )

    merged_relations, _dropped_self_loops, _deduped_collisions = merge_relations(
        decode_relations(survivor_metadata),
        decode_relations(absorbed_metadata),
        survivor_id=survivor_id,
        absorbed_id=absorbed_id,
    )
    if merged_relations:
        merged[RELATIONS_KEY] = encode_relations(merged_relations)
    else:
        merged.pop(RELATIONS_KEY, None)

    separator = f"\n\n## Merged content ({absorbed_id})\n\n"
    merged_body = survivor_body.rstrip("\n") + separator + absorbed_body
    if not merged_body.endswith("\n"):
        merged_body += "\n"

    return merged, merged_body


@dataclass(frozen=True)
class DocScan:
    """One `_iter_docs` result: a non-reserved `.md` file, scanned once.

    Exactly one of `metadata`, `read_error`, or `parse_error` is set (the
    other two are `None`) -- a successfully read AND parsed file has
    `metadata` populated (possibly `{}`) and both errors `None`; a file that
    could not be opened/decoded has `read_error` set and `metadata`/
    `parse_error` `None`; a file that was read but whose frontmatter did not
    parse has `parse_error` set and `metadata`/`read_error` `None`.
    """

    path: Path
    metadata: dict[str, object] | None
    read_error: OSError | UnicodeDecodeError | None
    parse_error: str | None


def _iter_docs(bundle_dir: Path) -> Iterator[DocScan]:
    """Walk every non-reserved `.md` file under `bundle_dir` exactly once (D2).

    `sorted(rglob("*.md"))` is the SAME walk `check_conformance` used before
    this refactor, so both `check_conformance` and `survey_bundle` (Phase 2)
    observe files in identical order. A file that cannot be opened or
    decoded yields a `DocScan` with `read_error` set instead of raising --
    `check_conformance` re-raises it (preserving its documented raise
    contract); `survey_bundle` degrades it to a finding (D3). A file whose
    frontmatter does not parse, or that has no parseable frontmatter block,
    yields `parse_error` set to the SAME message text `check_conformance`
    has always produced for that case.
    """
    for path in sorted(bundle_dir.rglob("*.md")):
        if path.name in RESERVED_FILENAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            yield DocScan(path, None, exc, None)
            continue
        try:
            post = frontmatter.loads(text)
        except Exception as exc:  # broad: any parse failure is a rule-1 violation
            yield DocScan(path, None, None, f"no parseable frontmatter ({exc})")
            continue
        if post.handler is None:
            yield DocScan(path, None, None, "no parseable frontmatter")
        else:
            yield DocScan(path, post.metadata, None, None)


@dataclass(frozen=True)
class BundleSurvey:
    """Counts and §9 findings for one `_iter_docs` pass over a bundle (Phase 2/D2).

    `findings` is a SUPERSET of `check_conformance`'s violations: it adds a
    per-file "unreadable" line for a `read_error` (D3), which
    `check_conformance` instead raises, PLUS one "unreadable directory" line
    per subdirectory `_iter_docs`'s `rglob` walk could not descend into (its
    `OSError` is silently swallowed by `scandir()`, per stdlib `glob`
    behavior). A file contributing a finding is counted as NEITHER a source
    nor a concept; an unreadable subdirectory's contents are unknown, so it
    affects no count at all -- only `findings`.
    """

    sources: int
    concepts: int
    findings: list[str]


def _walk_errors(bundle_dir: Path) -> list[OSError]:
    """Collect directory-scan `OSError`s that `_iter_docs`'s `rglob` walk
    would silently swallow, without yielding any file paths.

    `Path.rglob` never surfaces `scandir()` failures on a subdirectory it
    cannot descend into -- the subtree just vanishes from the walk with no
    signal. This walks the SAME tree with `os.walk`'s `onerror` hook solely
    to capture those errors as data (each has `.filename` set to the
    unreadable directory); `_iter_docs` and `check_conformance` are
    untouched and stay byte-identical.
    """
    errors: list[OSError] = []
    for _ in os.walk(bundle_dir, onerror=errors.append):
        pass
    return errors


def survey_bundle(bundle_dir: Path) -> BundleSurvey:
    """Survey `bundle_dir` for source/concept counts and §9-shaped findings (D2/D3).

    Consumes the SAME `_iter_docs` walk `check_conformance` uses, in one
    pass: `type == "Source"` counts as a source, any other non-empty `type`
    counts as a concept, and every read error, parse error, or missing/empty
    `type` becomes a finding instead of a count -- including a per-file read
    error, which `survey_bundle` degrades to a finding rather than raising
    (D3, Q3), unlike `check_conformance`. Directory-scan errors that
    `_iter_docs`'s walk silently drops (see `_walk_errors`) are appended as
    one finding per unreadable directory, sorted by path for determinism, so
    an unscanned subtree is never invisible to a caller reading `findings`
    alone -- it never affects `sources`/`concepts`, since that subtree's
    contents are unknown.
    """
    sources = 0
    concepts = 0
    findings: list[str] = []
    for scan in _iter_docs(bundle_dir):
        if scan.read_error is not None:
            findings.append(f"{scan.path}: unreadable ({scan.read_error})")
        elif scan.parse_error is not None:
            findings.append(f"{scan.path}: {scan.parse_error}")
        else:
            doc_type = (scan.metadata or {}).get("type")
            if not doc_type:
                findings.append(f"{scan.path}: missing non-empty 'type'")
            elif doc_type == "Source":
                sources += 1
            else:
                concepts += 1
    for walk_error in sorted(
        _walk_errors(bundle_dir), key=lambda exc: str(exc.filename)
    ):
        findings.append(f"{walk_error.filename}: unreadable directory ({walk_error})")
    return BundleSurvey(sources, concepts, findings)


def _has_frontmatter_fence(text: str) -> bool:
    """Detect a frontmatter block by FENCE PRESENCE, not parseability: `text`
    opens (after optional leading whitespace) with a `---` delimiter line and
    has a later closing `---` line.

    Deliberately does NOT reuse `_iter_docs`'s `frontmatter.loads` check
    (rule 1's "parseable frontmatter" mechanism): §6 forbids a nested
    `index.md` from carrying frontmatter AT ALL, so a malformed `---` block
    that fails to parse as YAML is still a frontmatter block for this rule,
    and must still be flagged.
    """
    lines = text.lstrip().splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    return any(line.strip() == "---" for line in lines[1:])


def _iter_reserved(bundle_dir: Path) -> Iterator[Path]:
    """Walk every reserved `.md` file (`index.md`/`log.md`) under
    `bundle_dir` exactly once, in the SAME `sorted(rglob("*.md"))` order
    `_iter_docs` uses -- but filtering IN `RESERVED_FILENAMES` instead of
    excluding them."""
    for path in sorted(bundle_dir.rglob("*.md")):
        if path.name in RESERVED_FILENAMES:
            yield path


def _check_reserved_structure(bundle_dir: Path) -> list[str]:
    """§9 rule 3: check the fixed structure of every reserved file.

    `index.md` (§6 + §11 root exception): any `index.md` other than the
    bundle-root one (`path.parent == bundle_dir`) MUST NOT carry a
    frontmatter block, detected by `_has_frontmatter_fence`.

    `log.md` (§7): every `## ` heading MUST match `_ISO_DATE_RE`
    (`YYYY-MM-DD`, format only -- not calendar-validated).

    Reads via `path.read_text(encoding="utf-8")`, so an unreadable or
    undecodable reserved file raises `OSError`/`UnicodeDecodeError`, matching
    `check_conformance`'s documented raise contract for candidate files.
    """
    violations: list[str] = []
    for path in _iter_reserved(bundle_dir):
        text = path.read_text(encoding="utf-8")
        if path.name == "index.md":
            if path.parent != bundle_dir and _has_frontmatter_fence(text):
                violations.append(f"{path}: index.md must not contain frontmatter")
        else:  # log.md -- `_iter_reserved` only yields the two reserved names
            for heading in _LOG_HEADING_RE.findall(text):
                if not _ISO_DATE_RE.match(heading):
                    violations.append(
                        f"{path}: log.md heading must be an ISO-8601 date "
                        f"(YYYY-MM-DD), got '## {heading}'"
                    )
    return violations


def check_conformance(bundle_dir: Path) -> list[str]:
    """Check §9 rules 1-3 against `bundle_dir`.

    Rules 1-2 walk every non-reserved `.md` file (`_iter_docs`), checking for
    parseable frontmatter with a non-empty `type`. Rule 3 additively walks
    the reserved files themselves (`_check_reserved_structure`), checking
    `index.md`'s frontmatter ban (with the §11 bundle-root exception) and
    `log.md`'s ISO-8601 date headings; its violations are appended after
    rules 1-2's.

    An additive `relations:` shape rule (spec: "OKF §9 Conformance --
    `relations:` Field Shape") runs alongside rules 1-2, gated on
    `scan.metadata` containing a `relations` key: a malformed shape (per
    `decode_relations`) is appended as a violation in the SAME
    `f"{path}: {message}"` form. It is a strict ADD-ON -- a document without
    a `relations:` key produces the exact same rules 1-2 output as before
    this rule existed (regression-guarded by
    `tests/unit/model/test_okf.py::test_check_conformance_byte_identical_when_relations_absent`).

    An empty list means conformant; a fresh, empty bundle passes vacuously
    because there are no `.md` files to violate any rule.
    May raise `OSError` or `UnicodeDecodeError` when a candidate file cannot
    be read or decoded -- those are inspection failures, never reported as
    conformance violations. Consumes the shared `_iter_docs` walk (D2) and
    re-raises `read_error` to preserve this exact contract; the rule 1-2
    portion of the output is byte-identical to the pre-refactor
    implementation (regression-guarded by
    `tests/unit/model/test_okf.py::test_check_conformance_round_trip_regression`).
    """
    violations: list[str] = []
    for scan in _iter_docs(bundle_dir):
        if scan.read_error is not None:
            raise scan.read_error
        if scan.parse_error is not None:
            violations.append(f"{scan.path}: {scan.parse_error}")
        elif not (scan.metadata or {}).get("type"):
            violations.append(f"{scan.path}: missing non-empty 'type'")
        elif RELATIONS_KEY in (scan.metadata or {}):
            try:
                decode_relations(scan.metadata or {})
            except ValueError as exc:
                violations.append(f"{scan.path}: {exc}")
    violations += _check_reserved_structure(bundle_dir)
    return violations
