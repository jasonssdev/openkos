"""The ingest bounded-context application service (ADR-0018), Slice 1 of
issue #918 (design: `openspec/changes/ingest-application-service/design.md`).

Slice 1 seeds this module with `DerivedPlan` and the collision-detection
helpers `_stage_derived_objects` uses while building its list of staged
derived objects -- moved verbatim from `cli/main.py`, with zero call-site
repoints (`main.py` imports them back under their original private names).
`main.py` continues to define and call `_stage_derived_objects` itself,
unchanged in behavior; later slices move that function, and the
plan-composition core around it, into this module.

This is the second module in the `application/` layer, following the
shipped `application/query.py`. Like it, this module imports nothing from
`openkos.cli`, `typer`, or `rich`, and binds no concrete LLM backend --
enforced by `tests/unit/application/test_layering.py`, generalized in this
change to scan every module under this directory rather than a single
hardcoded path.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from openkos.model import okf


@dataclass(frozen=True)
class DerivedPlan:
    """One validated derived object staged for Phase B write -- one entry
    per item in the list `stage_derived_objects` returns. The list itself,
    not this dataclass, carries the zero-to-N cardinality (design: bounded
    multi-object contract, D4); `[]` means every candidate was declined,
    dropped, or skipped, and `ingest` degrades to Source-only for this
    batch."""

    doc_type: str
    section: str
    link_dir: str
    slug: str
    title: str
    description: str
    path: Path
    content: str
    disambiguated_from: str | None = None
    """The original, colliding slug this plan was disambiguated away from
    -- `None` for the ordinary (no-collision) case. Set only when a
    foreign-source collision redirected this candidate to `<slug>-N`
    (design: Disambiguation loop, #131); Phase B uses it to emit the one
    audit `insert_log_entry` call for a disambiguated write."""

    type_alternative: str | None = None
    """The runner-up type the model also weighed (#401), or `None` when the
    classification was clear. Carried on the plan so the CALLER can
    aggregate one summary line per run (#566) -- the per-candidate stderr
    line fired on ~100% of extracted objects in real sessions and carried
    no signal. The durable record stays in the document's
    `type_alternative` frontmatter key, written by `build_concept` above,
    independent of this field."""

    sensitivity: str = ""
    """This plan's resolved birth-time `sensitivity` (issue #669, design
    D3) -- `config.type_birth_sensitivity`'s return value, already folded
    into `content`'s frontmatter above. Carried on the plan (rather than
    re-parsed from `content`) so the caller can build the run-summary
    advisory's `(type, resolved_level)` pairs the same way it already
    builds `alternative_pairs`."""

    type_floor_raised: bool = False
    """`True` when this object's resolved `sensitivity` is strictly above
    `stamp_sensitivity` because of the per-type offset mapping (issue #669,
    design D3) -- `resolved != base`, the same shape #569 already uses at
    `plan.sensitivity != cfg.default_sensitivity`. `False` on the common
    path (no offset configured for this type, or `base` already at or
    above the floor-plus-offset)."""


def collision_family(link_dir: Path, base_slug: str) -> list[Path]:
    """Return every file in `link_dir` belonging to `base_slug`'s collision
    family -- `<base_slug>.md` itself and every `<base_slug>-N.md` (N a
    positive integer) -- sorted ascending by `N` (the bare base slug sorts
    first). Matched via a REGEX anchored on the full filename stem
    (`^{base}(-\\d+)?$`), NEVER a glob, so an unrelated sibling like
    `<base>-word.md` never joins the family (design: Collision loop
    mechanics; #131).

    Both sides are NFC-normalized before matching (#414). `_slugify` emits
    NFC, but HFS+ (and some SMB mounts) rewrite a filename to NFD on write
    while APFS preserves whatever spelling it is handed, so `glob` can
    legitimately return the NFD stem of a file created under the NFC slug.
    Matching the raw stems would miss it -- while `derived_path.exists()`,
    which is normalization-INSENSITIVE on macOS, still reports True. The
    caller would then read an EMPTY family, misread the slug as belonging to
    a foreign source, and disambiguate to `<slug>-2` on every re-ingest
    until `write_exclusive` raised `FileExistsError`. Unreachable while
    slugs were ASCII (ASCII has no NFD form); reachable now that they carry
    accents.
    """
    if not link_dir.is_dir():
        return []
    base = unicodedata.normalize("NFC", base_slug)
    pattern = re.compile(rf"^{re.escape(base)}(?:-(\d+))?$")
    members: list[tuple[int, Path]] = []
    for path in link_dir.glob("*.md"):
        match = pattern.match(unicodedata.normalize("NFC", path.stem))
        if match is None:
            continue
        suffix_n = int(match.group(1)) if match.group(1) else 0
        members.append((suffix_n, path))
    members.sort(key=lambda item: item[0])
    return [path for _, path in members]


def family_owns_source(family: list[Path], source_slug: str) -> bool:
    """`True` if ANY member of `family` already carries THIS ingest's
    `sources/<source_slug>` provenance key -- the sole idempotency
    guarantee that a re-ingest never spawns a new disambiguated slug,
    including for a `<slug>-N` this source previously won (design:
    Idempotency Predicate; #131). A member whose frontmatter fails to read
    or parse is skipped, never raised -- the scan degrades per member,
    mirroring `okf._iter_docs`'s broad parse-failure tolerance."""
    provenance_key = f"sources/{source_slug}"
    for path in family:
        try:
            text = path.read_text(encoding="utf-8")
            metadata, _ = okf.load_frontmatter(text)
        except (OSError, UnicodeDecodeError):
            continue
        except Exception:  # noqa: S112 -- broad: malformed frontmatter degrades, never crashes
            continue
        provenance = metadata.get("provenance")
        if isinstance(provenance, list) and provenance_key in provenance:
            return True
    return False


def first_free_disambiguated_slug(
    family: list[Path], base_slug: str, reserved: set[str]
) -> str:
    """First free `<base_slug>-N` (N starting at 2) that is neither already
    on disk (a stem present in `family`) nor already claimed by an earlier
    candidate in THIS batch (`reserved`) -- deterministic, ascending scan
    (design: Collision loop mechanics -- batch-local `seen_slugs` guard;
    #131).

    On-disk stems are NFC-normalized before the comparison, for the same
    reason `collision_family` normalizes (#414): an NFD `<base>-2.md` must
    still count as taken, or this would hand back a name that already
    exists."""
    taken = {unicodedata.normalize("NFC", path.stem) for path in family} | reserved
    n = 2
    while f"{base_slug}-{n}" in taken:
        n += 1
    return f"{base_slug}-{n}"
