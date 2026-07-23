"""Detect-only inbound-reference helper for `forget` (design: "Detect-only
helper -- NEW module `src/openkos/bundle/references.py`"; spec: "Inbound
Reference Detection").

`find_inbound_references` REUSES `bundle/links.py::find_inbound_link_rewrites`
and `bundle/relations.py::find_inbound_relation_rewrites` -- the exact same
pure Phase-A scanners `merge` uses -- but in a detect-only mode: each
scanner's rewrite payload (the `new_link`/whole-file `snapshot` a merge
would apply) is discarded here; only presence, referrer id, and reference
kind (+ the actual relation `type`, for a typed relation) survive. This
module adds NO new scanning logic of its own, and -- like `links.py`/
`relations.py` -- MUST NOT import `openkos.graph` (canonical-layer rule,
AGENTS.md:41).

Both scanners are called with `survivor_id=absorbed_id=target_id`: a
harmless placeholder for the link scanner (whose `new_link` payload is
discarded here anyway), and, for the relation scanner, the mechanism that
makes it exclude the target's own file from its scan (it always skips
`file_id in (survivor_id, absorbed_id)`, which collapses to just
`{target_id}` once both are equal). The link scanner has no equivalent
built-in exclusion, so `find_inbound_references` also applies its own
`referrer_id != target_id` filter as defense-in-depth, in case a caller
ever passes the target's own file in `files` by mistake -- the documented
CALLER contract is still to exclude it up front (mirroring `merge`'s own
`other_files` construction), so this filter is a backstop, not the primary
self-reference guard.

CRITICAL fail-open closed (bounded correction, resilience review): reused
UNMODIFIED, `find_inbound_relation_rewrites` silently `continue`s past any
file whose frontmatter/`relations:` fails to parse (its own docstring:
"SKIPPED rather than surfaced as a fail-closed refusal"). For a
non-destructive `merge`, that is a reasonable "hand-edited unrelated file
must never block an unrelated merge" tradeoff. For the destructive `forget`
verb, it means a referrer with malformed frontmatter but a `relations:`
entry that WOULD target the concept being forgotten is silently never
reported -- gate 1 sees zero inbound refs and `forget` deletes the concept,
leaving a dangling typed relation with no warning at all (spec: "Inbound
Reference Detection" MUST enumerate every inbound typed relation).

Rather than touching the shared scanner (out of scope; would also weaken
`merge`'s deliberately permissive skip), `find_inbound_references` runs its
OWN independent parse attempt over every file in `files` -- the same
`okf.load_frontmatter`/`okf.decode_relations` calls the relation scanner
itself makes -- and, for any file that raises, applies a PROPORTIONATE
fail-closed rule: only surface it (as a new `kind="unverifiable"` record)
if the target's canonical id appears as a raw substring of that file's
text. A malformed file that never even mentions the id cannot possibly
reference it, so it is silently ignored -- this is what keeps unrelated
bundle corruption from blocking every unrelated forget, while still closing
the fail-open on any file that plausibly does reference the target.
`InboundReference.relation_type` is `None` for this kind too (an
unverifiable file's actual relation type, if any, is by definition
unknown); existing consumers that only handled `"link"`/`"relation"` gain
one more `kind` value to branch on, nothing else about the dataclass shape
changes.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from openkos.bundle import links as bundle_links
from openkos.bundle import relations as bundle_relations
from openkos.model import okf


@dataclass(frozen=True)
class InboundReference:
    """One inbound reference (markdown link or typed relation) targeting a
    concept, surfaced by `find_inbound_references` for `forget`'s Phase A
    detection + preview (spec: "Inbound Reference Detection")."""

    referrer_id: str
    """The referencing concept's bundle-relative id (`.md` stripped)."""

    kind: Literal["link", "relation", "unverifiable"]
    """Which reference shape this is: a bundle-relative markdown link, a
    `relations:` frontmatter entry, or `"unverifiable"` -- a file whose
    frontmatter/`relations:` could not be parsed at all, but whose raw text
    mentions the target's canonical id (fail-closed backstop; see module
    docstring's "CRITICAL fail-open closed")."""

    relation_type: str | None
    """The relation's `type` string when `kind == "relation"`; `None` for a
    `kind == "link"` or `kind == "unverifiable"` record (neither carries a
    known type -- a markdown link never does, and an unverifiable file's
    actual relation type, if any, could not be determined)."""


def find_inbound_references(
    files: Mapping[str, str], *, target_id: str
) -> list[InboundReference]:
    """Pure detect-only scan: find every inbound markdown link and typed
    relation, across every file in `files` (bundle-relative path -> full
    text already in memory), that targets `target_id` -- never a write,
    never a rewrite payload (spec: "Inbound Reference Detection").

    Reuses `find_inbound_link_rewrites`/`find_inbound_relation_rewrites`
    (merge's own Phase-A scanners) with `absorbed_id=survivor_id=target_id`
    and discards each returned rewrite's payload, keeping only presence +
    kind. A typed-relation record's actual `type` is recovered by decoding
    that file's recorded pre-merge `snapshot` (a `RelationRewrite` alone
    carries no type) and keeping only entries whose `target == target_id`
    -- a file with `relations:` also targeting some OTHER id still only
    reports the one entry that actually matches.

    Also runs an INDEPENDENT parse pass over every file in `files` (see
    module docstring's "CRITICAL fail-open closed"): any file whose
    frontmatter/`relations:` fails to parse via `okf.load_frontmatter`/
    `okf.decode_relations` is fail-closed reported as a `kind=
    "unverifiable"` record -- but ONLY when `target_id` appears as a raw
    substring of that file's text (the proportionate rule: a malformed file
    that never mentions the target cannot reference it, so it is silently
    ignored rather than blocking an unrelated forget).

    Order: every link record (in `find_inbound_link_rewrites`'s own file/
    line order) first, then every relation record (in
    `find_inbound_relation_rewrites`'s file order, then that file's
    `relations:` list order), then every unverifiable record (in `files`
    iteration order) -- a caller needing a different combined order should
    sort explicitly.
    """
    found: list[InboundReference] = []

    link_rewrites = bundle_links.find_inbound_link_rewrites(
        files, absorbed_id=target_id, survivor_id=target_id
    )
    for rewrite in link_rewrites:
        found.append(
            InboundReference(
                referrer_id=rewrite.file.removesuffix(".md"),
                kind="link",
                relation_type=None,
            )
        )

    relation_rewrites = bundle_relations.find_inbound_relation_rewrites(
        files, absorbed_id=target_id, survivor_id=target_id
    )
    for relation_rewrite in relation_rewrites:
        metadata, _ = okf.load_frontmatter(relation_rewrite.snapshot)
        for relation in okf.decode_relations(metadata):
            if relation.target == target_id:
                found.append(
                    InboundReference(
                        referrer_id=relation_rewrite.file.removesuffix(".md"),
                        kind="relation",
                        relation_type=relation.type,
                    )
                )

    for file, text in files.items():
        try:
            metadata, _ = okf.load_frontmatter(text)
            okf.decode_relations(metadata)
        except Exception:  # fail-CLOSED backstop: ANY parse
            # failure on a file that mentions the target must be surfaced,
            # not silently skipped (the CRITICAL fail-open this closes);
            # unlike `find_inbound_relation_rewrites`'s identical broad
            # except, this branch reports rather than continues.
            if target_id in text:
                found.append(
                    InboundReference(
                        referrer_id=file.removesuffix(".md"),
                        kind="unverifiable",
                        relation_type=None,
                    )
                )

    return [ref for ref in found if ref.referrer_id != target_id]
