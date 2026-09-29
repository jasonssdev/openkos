"""Merge-ledger sidecar store: path mapping, read, two-phase write, and
crash recovery (design: "Relocate the merge ledger to `bundle/.state/
ledger/`"; ADR-0013; spec: Reversibility Ledger).

Every merge used to append its `MergeLedgerEntry` directly into the
survivor's OWN frontmatter under the `merged_from` key, growing that file
geometrically across merges. This module moves the ledger to a per-survivor
sidecar file under `bundle/.state/ledger/<concept_id>.ledger.okf`, written
and read only via `okf.dump_frontmatter`/`load_frontmatter` (ADR-0002
invariant 3, preserved literally -- the sidecar is a frontmatter document
with an empty body). `MergeLedgerEntry`'s own schema (v1/v2/v3) is
unchanged; this module only relocates where entries LIVE.

A leaf module: mirrors `bundle/relations.py`/`bundle/links.py` -- MUST NOT
import `openkos.graph` (canonical-layer rule, AGENTS.md:41).

Crash safety (design Decision 1): a merge write today is index -> log ->
touched rewrites -> survivor (V) -> absorbed delete (D). The sidecar adds a
new step S BEFORE V: a hash-bound intent marker,
`<concept_id>.ledger.okf.pending`, written via `fsio.write_atomic` (S1),
committed via `os.replace` (S2) only after V has landed. `recover` is a
TOTAL function of on-disk state -- no heuristic -- per this truth table:

| `.pending` | `sha256(survivor on disk)` | Verdict       | Repair              |
|------------|-----------------------------|---------------|----------------------|
| absent     | --                           | consistent    | none                 |
| present    | == `expected_survivor_sha256` | V landed, S2 torn | roll forward (promote pending) |
| present    | != (or survivor missing)    | V never landed | roll back (discard pending) |

`fsio.rename_two_step` does NOT apply here (see `fsio.py`'s own docstring):
it exists only for NFC/NFD canonically-equivalent renames on a
normalization-insensitive volume, and `pending` -> `ledger.okf` differ by a
literal ASCII suffix, never a canonical equivalence -- a direct
`os.replace` is a real rename on every volume.
"""

import dataclasses
import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

import yaml

from openkos import fsio
from openkos.model import okf

LEDGER_DIRNAME: Final = "ledger"
"""The subdirectory of `okf.STATE_DIRNAME` holding every survivor's ledger
sidecar, mirroring the concept's own hierarchical id."""

LEDGER_SUFFIX: Final = ".ledger.okf"
"""Never `.md` -- every existing inbound-reference/EXCLUDE walk is
`sorted(bundle_dir.rglob("*.md"))` (`cli/main.py:3841, 4438, 5172, 5444,
5959, 6542`; design Decision 2), so this suffix excludes the sidecar from
all six sites with zero edits there."""

PENDING_SUFFIX: Final = ".ledger.okf.pending"
"""The two-phase-write intent marker's suffix: same directory as the
committed sidecar, same container shape, plus `expected_survivor_sha256`
(design Decision 1)."""

LEDGER_SIDECAR_SCHEMA: Final = "openkos.merge_ledger_sidecar/v1"
"""The container's own `schema` key (design Decision 2) -- versioned
independently of `MergeLedgerEntry.schema` (v1/v2/v3), so a future
container-shape change never forces a matching entry-schema bump."""

RecoveryVerdict = Literal["none", "roll-forward", "roll-back"]
"""The three, and only three, outcomes `recover` can reach -- a TOTAL
function of on-disk state (design Decision 1's truth table), never a
heuristic."""

_SIDECAR_SKIP_ERRORS: Final = (OSError, ValueError, yaml.YAMLError)
"""The three, and only three, per-sidecar failure classes a bundle-wide
walk skips defensively (#562 review follow-up): unreadable bytes
(`OSError`), unparseable frontmatter (`yaml.YAMLError` out of
`okf.load_frontmatter`, which is neither `OSError` nor `ValueError`), and
entries that fail to decode (the fail-closed `ValueError`
`okf.decode_merge_ledger_entry` raises, e.g. on an unsupported schema
version). Deliberately NOT a bare `Exception`: a genuine programming error
in the walk body must stay visible instead of degrading into a silent
"nothing found". Shared by `find_absorber`, `scan_nesting_violations`, and
`bundle_wide_max_entries`, so the skip contract is defined exactly once."""


def survivor_sha256(survivor_text: str) -> str:
    """`sha256` of `survivor_text`'s UTF-8 bytes, hex-encoded -- the exact
    value `write_pending` binds into `expected_survivor_sha256` and
    `recover` re-derives from the on-disk survivor to decide the verdict."""
    return hashlib.sha256(survivor_text.encode("utf-8")).hexdigest()


def ledger_root(bundle_dir: Path) -> Path:
    """`bundle_dir/.state/ledger` -- the root every sidecar and pending
    marker lives under (design Decision 2)."""
    return bundle_dir / okf.STATE_DIRNAME / LEDGER_DIRNAME


def ledger_path_for(concept_id: str, bundle_dir: Path) -> Path:
    """The committed sidecar path for `concept_id` -- `okf.concept_path_for`
    generalized over `(root, suffix)` (task 1.1), reusing the SAME
    NFC/NFD-tolerant resolver `concept_id`-to-`.md`-path lookups use rather
    than inventing a second mapping (design Decision 2)."""
    return okf.concept_path_for(
        concept_id, ledger_root(bundle_dir), suffix=LEDGER_SUFFIX
    )


def pending_path_for(concept_id: str, bundle_dir: Path) -> Path:
    """The intent-marker path for `concept_id`, resolved by the same
    normalization-tolerant lookup as `ledger_path_for` -- same directory,
    same segment resolution, only the trailing suffix differs."""
    return okf.concept_path_for(
        concept_id, ledger_root(bundle_dir), suffix=PENDING_SUFFIX
    )


def read_entries(concept_id: str, bundle_dir: Path) -> list[okf.MergeLedgerEntry]:
    """Read every `MergeLedgerEntry` recorded for `concept_id`'s sidecar, in
    LIFO (append) order. No sidecar on disk (a survivor never merged, or a
    ledger not yet migrated) returns `[]` -- mirrors
    `okf.decode_merged_from`'s own "absent key -> no prior merges" contract,
    now applied to "absent file" instead."""
    path = ledger_path_for(concept_id, bundle_dir)
    if not path.is_file():
        return []
    metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    return okf.decode_merged_from(metadata)


def iter_ledgers(bundle_dir: Path) -> list[Path]:
    """Every committed sidecar under `bundle_dir`'s ledger root, sorted --
    the ONE shared INCLUDE-walk primitive `forget`/`purge`/the
    `set-sensitivity` sweep reuse (design Decision 3), so the walk is
    written exactly once. A missing ledger root (no merge has ever run)
    returns `[]` rather than raising."""
    root = ledger_root(bundle_dir)
    if not root.is_dir():
        return []
    return sorted(root.rglob(f"*{LEDGER_SUFFIX}"))


def find_absorber(concept_id: str, bundle_dir: Path) -> str | None:
    """Reverse lookup across every committed sidecar under `bundle_dir`:
    the `survivor_id` whose ledger carries an entry with `absorbed_id ==
    concept_id`, or `None` if no ledger records absorbing it (issue #562).

    Exists because an absorbed ex-survivor's OWN sidecar deliberately
    SURVIVES its absorption on disk (the merge deletes only the concept
    `.md` file, never the sidecar), so a chained merge -- `mid` absorbed
    `leaf`, then `top` absorbed `mid` -- leaves `mid`'s ledger fully
    recoverable but its concept file gone. `unmerge`'s "concept does not
    exist" refusal uses this lookup to name WHO absorbed the requested
    survivor and the exact command to run first, instead of a dead end.

    Read-only and pure over on-disk state: walks `iter_ledgers` (the ONE
    shared INCLUDE-walk primitive), so a missing ledger root returns
    `None`. Any sidecar this walk cannot use is skipped defensively --
    never raised over, never returned as an absorber: a missing or
    non-string `survivor_id` (mirroring `bundle_wide_max_entries`'
    posture), and equally one that is unreadable (`OSError`), carries
    unparseable frontmatter (`yaml.YAMLError` out of
    `okf.load_frontmatter`, which is neither `OSError` nor `ValueError`),
    or whose entries fail to decode (the fail-closed `ValueError`
    `okf.decode_merge_ledger_entry` raises on an unsupported schema
    version) -- exactly the `_SIDECAR_SKIP_ERRORS` classes, no broader.
    This lookup only decorates an already-refusing error path with a
    breadcrumb, so one broken unrelated ledger anywhere in the bundle must
    never replace that refusal's message -- let alone escape as a raw
    traceback (review finding, issue #562); `doctor` is where a broken
    sidecar gets diagnosed, not here."""
    for ledger_path in iter_ledgers(bundle_dir):
        try:
            metadata, _ = okf.load_frontmatter(ledger_path.read_text(encoding="utf-8"))
            survivor_id = metadata.get("survivor_id")
            if not isinstance(survivor_id, str) or not survivor_id:
                continue
            entries = okf.decode_merged_from(metadata)
        except _SIDECAR_SKIP_ERRORS:
            continue
        if any(entry.absorbed_id == concept_id for entry in entries):
            return survivor_id
    return None


def _encode_container(
    survivor_id: str, entries: list[okf.MergeLedgerEntry]
) -> dict[str, object]:
    """The committed sidecar's own frontmatter shape (design Decision 2):
    reuses `okf.encode_merged_from` verbatim, so entry encoding is defined
    in exactly one place regardless of where entries are stored."""
    return {
        "schema": LEDGER_SIDECAR_SCHEMA,
        "survivor_id": survivor_id,
        "merged_from": okf.encode_merged_from(entries),
    }


def write_entries(
    concept_id: str,
    bundle_dir: Path,
    *,
    survivor_id: str,
    entries: list[okf.MergeLedgerEntry],
) -> Path:
    """(Re)write the committed sidecar to hold EXACTLY `entries`, replacing
    whatever it held before -- the primitive `unmerge` uses to pop the
    LIFO-tail entry off the sidecar, LAST among its writes, so a retry
    (the restores above are byte-identical on re-run) is a re-runnable
    no-op rather than a second pop (task 2.7). Unlike `write_pending`/
    `commit_pending`, this is a single `fsio.write_atomic` -- no intent
    marker -- because `unmerge`'s own restore sequence, not a hash-bound
    marker, is what makes a partial run safely retryable here. An empty
    `entries` list removes the sidecar file entirely (a survivor with no
    remaining merges keeps no empty ledger on disk); removing an
    already-absent sidecar is a no-op, not an error."""
    return rewrite_entries_at(
        ledger_path_for(concept_id, bundle_dir),
        survivor_id=survivor_id,
        entries=entries,
    )


def rewrite_entries_at(
    path: Path, *, survivor_id: str, entries: list[okf.MergeLedgerEntry]
) -> Path:
    """(Re)write the sidecar AT `path` to hold EXACTLY `entries`, using
    `survivor_id` only as container CONTENT -- never to derive the path.

    The privacy sweep walks real sidecar paths and MUST write each rewrite
    back to the path it WALKED, not to a path rebuilt from the (possibly
    drifted or hostile) `survivor_id` frontmatter field. Rebuilding it both
    lets a traversal id escape the bundle (arbitrary-file create/delete with
    the `.ledger.okf` suffix) AND silently writes a legit NFC/NFD-drifted
    sidecar to the wrong place while reporting the walked one as cleaned --
    for a privacy sweep, a silent scrub miss. `write_entries` is the
    id-addressed wrapper for callers that legitimately own the id. An empty
    `entries` list removes the file; removing an absent file is a no-op."""
    if not entries:
        if path.is_file():
            path.unlink()
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    container = _encode_container(survivor_id, entries)
    fsio.write_atomic(path, okf.dump_frontmatter(container))
    return path


def write_pending(
    concept_id: str,
    bundle_dir: Path,
    *,
    survivor_id: str,
    entries: list[okf.MergeLedgerEntry],
    expected_survivor_sha256: str,
) -> Path:
    """S1: `fsio.write_atomic` the FULL new container (every entry the
    committed sidecar will hold once this lands, including the new one) to
    `<concept_id>.ledger.okf.pending`, plus `expected_survivor_sha256` --
    available before any write, since it is `sha256(prepared.plan.
    merged_survivor)` (design Decision 1). Creates the ledger directory
    tree on demand (a survivor's first-ever merge has no sidecar directory
    yet)."""
    pending_path = pending_path_for(concept_id, bundle_dir)
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    container = _encode_container(survivor_id, entries)
    container["expected_survivor_sha256"] = expected_survivor_sha256
    fsio.write_atomic(pending_path, okf.dump_frontmatter(container))
    return pending_path


def commit_pending(concept_id: str, bundle_dir: Path) -> Path:
    """S2: `os.replace(pending, ledger.okf)` -- the commit. A single rename,
    deliberately: `fsio.rename_two_step` does not apply (module docstring),
    and re-encoding the container here would mean the committed bytes are
    not what S1 fsynced, reopening exactly the durability gap the
    two-phase scheme exists to close."""
    pending_path = pending_path_for(concept_id, bundle_dir)
    ledger_path = ledger_path_for(concept_id, bundle_dir)
    pending_path.replace(ledger_path)
    return ledger_path


def discard_pending(concept_id: str, bundle_dir: Path) -> None:
    """Unlink `<concept_id>.ledger.okf.pending` without touching the
    committed sidecar -- the roll-back repair (recovery truth table, row
    3)."""
    pending_path_for(concept_id, bundle_dir).unlink()


def iter_pending(bundle_dir: Path) -> list[Path]:
    """Every uncommitted `<concept_id>.ledger.okf.pending` marker under
    `bundle_dir`'s ledger root, sorted -- the read-only enumeration
    primitive `doctor`'s torn-write check (Check A, durable-derived-state
    slice 1b) uses. A missing ledger root returns `[]` rather than
    raising, mirroring `iter_ledgers`."""
    root = ledger_root(bundle_dir)
    if not root.is_dir():
        return []
    return sorted(root.rglob(f"*{PENDING_SUFFIX}"))


def scan_torn_writes(bundle_dir: Path) -> list[tuple[Path, RecoveryVerdict]]:
    """Read-only PREVIEW of what `recover` would decide for every pending
    marker under `bundle_dir`'s ledger root -- `doctor`'s Check A (check
    12, design Decision 5): the SAME hash-bound truth table `recover`
    implements, but this NEVER writes, replaces, or unlinks anything
    (doctor stays read-only per `openspec/specs/doctor-command/spec.md`;
    the repair verb, not doctor, performs the write).

    A marker whose `survivor_id` field is missing or non-string cannot be
    resolved to a survivor at all -- reported as `"roll-back"`-shaped
    (nothing to roll forward TO) rather than raised, mirroring the
    defensive-skip posture `cli._sweep_ledger_sidecars_for_ids` already
    uses for a malformed sidecar."""
    results: list[tuple[Path, RecoveryVerdict]] = []
    for pending_path in iter_pending(bundle_dir):
        metadata, _ = okf.load_frontmatter(pending_path.read_text(encoding="utf-8"))
        survivor_id = metadata.get("survivor_id")
        expected_sha256 = metadata.get("expected_survivor_sha256")

        actual_sha256: str | None = None
        if isinstance(survivor_id, str) and survivor_id:
            survivor_path = okf.concept_path_for(survivor_id, bundle_dir)
            if survivor_path.is_file():
                actual_sha256 = survivor_sha256(
                    survivor_path.read_text(encoding="utf-8")
                )

        if actual_sha256 is not None and actual_sha256 == expected_sha256:
            results.append((pending_path, "roll-forward"))
        else:
            results.append((pending_path, "roll-back"))
    return results


def scan_nesting_violations(bundle_dir: Path) -> list[tuple[str, int]]:
    """`doctor`'s Check B (check 13, design Decision 5): nested-prefix
    equality over every committed sidecar under `bundle_dir`.

    Entry *k*'s `survivor_before` snapshot, when it embeds ledger entries
    via the OLD, pre-relocation frontmatter `merged_from` key (a
    migration-era entry written BEFORE this slice), must decode back to
    EXACTLY the sidecar's own current entries `0..k-1`; any inequality is
    exactly #550 consequence 2 -- a later merge rewrote bytes inside an
    earlier embedded snapshot -- and is flagged.

    An entry whose `survivor_before` embeds NOTHING is silently skipped,
    not flagged: every entry created AFTER the ledger relocation has a
    `survivor_before` that never carried a `merged_from` key at all (it
    lives in this sidecar, not in frontmatter), so the corruption class
    this check exists for is structurally extinct there (design Decision
    5: "Check B is a migration-era check"). This also covers the single-
    entry case (`k` never reaches `1`) -- the design's documented honest
    false negative: nothing nested, so the check is blind to it.

    Returns `(survivor_id, entry_index)` pairs for every violation found.
    Read-only: never writes, modifies, or deletes anything. A sidecar this
    walk cannot read, parse, or decode (`_SIDECAR_SKIP_ERRORS`, #562 review
    follow-up) is skipped defensively so one broken file never takes down
    the whole scan -- with the documented honest false negative that the
    skipped sidecar's own entries go unchecked, the same trade
    `scan_unmigrated` already makes over an unreadable doc. Inside a
    decodable sidecar, an embedded snapshot that fails to parse or decode
    skips ONLY that entry index (focus-lens correction) -- a mid-loop
    failure never swallows a real violation at a later index."""
    violations: list[tuple[str, int]] = []
    for ledger_path in iter_ledgers(bundle_dir):
        try:
            metadata, _ = okf.load_frontmatter(ledger_path.read_text(encoding="utf-8"))
            survivor_id = metadata.get("survivor_id")
            if not isinstance(survivor_id, str) or not survivor_id:
                continue
            entries = okf.decode_merged_from(metadata)
        except _SIDECAR_SKIP_ERRORS:
            continue
        for k in range(1, len(entries)):
            try:
                embedded_metadata, _ = okf.load_frontmatter(entries[k].survivor_before)
                embedded_entries = okf.decode_merged_from(embedded_metadata)
            except _SIDECAR_SKIP_ERRORS:
                continue
            if not embedded_entries:
                continue
            if embedded_entries != entries[:k]:
                violations.append((survivor_id, k))
    return violations


def scan_unmigrated(bundle_dir: Path) -> list[tuple[str, list[okf.MergeLedgerEntry]]]:
    """Every survivor whose OWN frontmatter still carries a `merged_from`
    key (pre-relocation, never migrated to a sidecar) -- the repair verb's
    migration source (task 3, PR#3). Walks `okf._iter_docs`, the SAME walk
    `fts.build_index`/`reindex` use; a doc `_iter_docs` could not read or
    parse is silently skipped, mirroring every other reader's degrade-not-
    crash posture over a transient per-doc failure.

    A committed sidecar under `bundle/.state/ledger/` is never a candidate
    here: it is not `.md`-suffixed, so `_iter_docs`'s `rglob("*.md")` walk
    never even reaches it (design Decision 2's free EXCLUDE)."""
    unmigrated: list[tuple[str, list[okf.MergeLedgerEntry]]] = []
    for scan in okf._iter_docs(bundle_dir):
        if scan.read_error is not None or scan.parse_error is not None:
            continue
        metadata = scan.metadata or {}
        if okf.MERGED_FROM_KEY not in metadata:
            continue
        concept_id = okf.concept_id_for(scan.path, bundle_dir)
        unmigrated.append((concept_id, okf.decode_merged_from(metadata)))
    return unmigrated


def bundle_wide_max_entries(bundle_dir: Path) -> int:
    """The LARGEST entry count any single survivor carries anywhere in the
    bundle -- migrated (committed sidecar) and unmigrated (frontmatter-
    embedded) ledgers counted together. `0` when no ledger of either kind
    exists.

    The repair verb's cross-survivor-pollution gate (design Decision 5):
    `merge_core`'s `other_files` (`cli/main.py:6542`) can rewrite bytes
    inside a THIRD survivor's embedded snapshot, a corruption Check B
    cannot see at every index -- so the migration gate is deliberately
    coarser than the check, refusing whenever ANY survivor bundle-wide
    carries 2 or more entries, regardless of which specific ledger a
    per-entry check would have flagged.

    A sidecar this walk cannot read, parse, or decode
    (`_SIDECAR_SKIP_ERRORS`, #562 review follow-up) is skipped defensively
    -- counted as absent -- so one broken file never takes down the gate;
    the unmigrated (frontmatter) side below already degrades the same way
    through `scan_unmigrated`'s per-doc skip."""
    counts: dict[str, int] = {}
    for ledger_path in iter_ledgers(bundle_dir):
        try:
            metadata, _ = okf.load_frontmatter(ledger_path.read_text(encoding="utf-8"))
            survivor_id = metadata.get("survivor_id")
            if isinstance(survivor_id, str) and survivor_id:
                counts[survivor_id] = len(okf.decode_merged_from(metadata))
        except _SIDECAR_SKIP_ERRORS:
            continue
    for concept_id, entries in scan_unmigrated(bundle_dir):
        counts[concept_id] = counts.get(concept_id, 0) + len(entries)
    return max(counts.values(), default=0)


def _apply_migrate_document(text: str) -> str:
    """Run `okf.migrate_document` once over `text` and return its resulting
    bytes: `Migrated.text` when a rule fired, `text` itself (untouched, no
    re-serialization) when `Unchanged`. Raises `ValueError` on `Refused` --
    the same "never guess" posture `migrate_document` itself documents
    (okf-v02-migration design.md Decision 9); the caller (Phase 6's
    `repair`) is expected to turn this into a whole-run refusal, never a
    partial migration."""
    result = okf.migrate_document(text)
    if isinstance(result, okf.Migrated):
        return result.text
    if isinstance(result, okf.Unchanged):
        return text
    raise ValueError(
        "migrate_sidecars_to_okf_v02: cannot migrate a whole-document "
        f"snapshot: {result.reason}"
    )


def _migrate_whole_document_snapshot(
    text: str,
    snapshot_events: dict[str, list[tuple[datetime, str]]],
    current_texts: Mapping[str, str],
) -> str:
    """Migrate one whole-document snapshot (`absorbed_snapshot`,
    `survivor_before`, or a `relation_rewrites`/`provenance_rewrites`
    entry's `snapshot`) via `okf.migrate_document`, then recurse into any
    embedded, pre-relocation `merged_from` list the snapshot itself carries
    (okf-v02-migration design.md Decision 1) -- a migration-era snapshot
    that still embeds ledger history has that history's OWN whole-document
    fields migrated the SAME way, by calling `_migrate_entry` (the
    identical function this module applies to a sidecar's own top-level
    entries), so `doctor`'s Check B (nested-prefix equality,
    `scan_nesting_violations`) keeps holding: equal inputs map to equal
    outputs regardless of nesting depth."""
    migrated = _apply_migrate_document(text)
    metadata, body = okf.load_frontmatter(migrated)
    if okf.MERGED_FROM_KEY not in metadata:
        return migrated
    embedded_entries = okf.decode_merged_from(metadata)
    migrated_embedded = [
        _migrate_entry(entry, snapshot_events, current_texts)
        for entry in embedded_entries
    ]
    if migrated_embedded == embedded_entries:
        return migrated
    new_metadata = dict(metadata)
    new_metadata[okf.MERGED_FROM_KEY] = okf.encode_merged_from(migrated_embedded)
    return okf.dump_frontmatter(new_metadata, body)


_OKF_VERSION_LINE_RE: Final = re.compile(r"(?m)^(okf_version:)[ \t]*(?:.*)$")
"""Matches a whole `okf_version: ...` frontmatter line -- the ONE targeted
substitution `_flip_index_okf_version` performs, never a `dump_frontmatter`
re-dump of the whole snapshot (task 5.4/design.md Decision 1: "its body is
untouched")."""


def _needs_okf_version_flip(index_text: str) -> bool:
    """`True` when `index_text` (a whole-`index.md` snapshot) declares an
    `okf_version` other than `okf.OKF_VERSION` -- the V1-V4 `index_before`
    flip's own precondition (design.md Decision 1: "when the snapshot's
    declared `okf_version` differs")."""
    metadata, _ = okf.load_frontmatter(index_text)
    return metadata.get("okf_version") != okf.OKF_VERSION


def _flip_index_okf_version(index_text: str) -> str:
    """Flip a V1-V4 `index_before` whole-`index.md` snapshot's declared
    `okf_version` to `okf.OKF_VERSION`, in place -- a targeted regex
    substitution over the raw frontmatter block only, never a
    `dump_frontmatter` re-dump of the whole document, so every OTHER
    frontmatter field's quoting and the body are left byte-for-byte
    untouched. Matches `dump_frontmatter`'s own single-quoted emission
    style for a version string, so a second run over an already-flipped
    snapshot is a byte-identical no-op."""
    block, body = okf.split_frontmatter_verbatim(
        index_text, label="migrate_sidecars_to_okf_v02"
    )
    new_block, count = _OKF_VERSION_LINE_RE.subn(
        rf"\1 '{okf.OKF_VERSION}'", block, count=1
    )
    if count == 0:
        raise ValueError(
            "migrate_sidecars_to_okf_v02: index_before snapshot has no "
            "okf_version field to flip"
        )
    return new_block + body


def _body_start(text: str) -> int:
    """The character offset at which `text`'s body begins -- the length of
    its verbatim frontmatter block, measured on the ACTUAL text (never
    assumed from YAML length, design.md Decision 1's own warning: a re-dump
    may normalize the whitespace between the closing `---` and the body)."""
    block, _ = okf.split_frontmatter_verbatim(text, label="migrate_sidecars_to_okf_v02")
    return len(block)


def _build_snapshot_index(
    sidecars: list[tuple[Path, str, list[okf.MergeLedgerEntry]]],
) -> dict[str, list[tuple[datetime, str]]]:
    """One pass over every sidecar's PRE-migration entries (`file ->
    [(merged_at, snapshot_text), ...]`), built once across the whole bundle
    (design.md Decision 1: "the index of snapshots is built once, from the
    pre-migration ledger state, across all sidecars"). Each entry
    contributes its survivor's own pre-THIS-merge bytes (`survivor_before`,
    keyed by the sidecar's `survivor_id`), the absorbed object's pre-merge
    bytes (`absorbed_snapshot`, keyed by `entry.absorbed_id`), and every
    third-party whole-file snapshot its `relation_rewrites`/
    `provenance_rewrites` recorded -- exactly the four snapshot kinds
    `_resolve_post_merge_text` searches for the EARLIEST later event.
    `link_rewrites` never contributes an event: a link rewrite only ever
    touches the BODY, never the frontmatter, so it can never change where a
    later snapshot's body begins."""
    events: dict[str, list[tuple[datetime, str]]] = {}
    for _, survivor_id, entries in sidecars:
        for entry in entries:
            when = datetime.fromisoformat(entry.merged_at)
            events.setdefault(f"{survivor_id}.md", []).append(
                (when, entry.survivor_before)
            )
            events.setdefault(f"{entry.absorbed_id}.md", []).append(
                (when, entry.absorbed_snapshot)
            )
            for relation_rewrite in entry.relation_rewrites:
                events.setdefault(relation_rewrite.file, []).append(
                    (when, relation_rewrite.snapshot)
                )
            for provenance_rewrite in entry.provenance_rewrites:
                events.setdefault(provenance_rewrite.file, []).append(
                    (when, provenance_rewrite.snapshot)
                )
    return events


def _resolve_post_merge_text(
    file: str,
    merged_at: str,
    snapshot_events: dict[str, list[tuple[datetime, str]]],
    current_texts: Mapping[str, str],
) -> str:
    """`file`'s frontmatter as it stood right after the merge recorded at
    `merged_at` (design.md Decision 1's "Link-offset shift"): the EARLIEST
    later snapshot event recorded for `file` (any sidecar, `merged_at`
    strictly greater), or `file`'s CURRENT text when no later event exists.
    `current_texts` MUST be keyed the same way `LinkRewrite.file` is
    (bundle-relative path, `.md` suffix included)."""
    merged_at_instant = datetime.fromisoformat(merged_at)
    candidates = [
        (when, text)
        for when, text in snapshot_events.get(file, [])
        if when > merged_at_instant
    ]
    if candidates:
        _, text = min(candidates, key=lambda candidate: candidate[0])
        return text
    return current_texts[file]


def _shift_link_rewrites(
    link_rewrites: list[okf.LinkRewrite],
    merged_at: str,
    snapshot_events: dict[str, list[tuple[datetime, str]]],
    current_texts: Mapping[str, str],
) -> list[okf.LinkRewrite]:
    """Shift every `link_rewrites[].offset` by the change in body-start
    position its target file's frontmatter undergoes when migrated
    (design.md Decision 1's "Link-offset shift"): `body_start(migrate_
    document(x)) - body_start(x)` for `x = _resolve_post_merge_text(...)`,
    applied only to offsets AT OR ABOVE the OLD body start -- an offset
    inside the body is never touched. Each distinct `file` is resolved and
    shifted at most once per call, since every rewrite for the same file
    shares the same `x` and the same shift."""
    shifted: list[okf.LinkRewrite] = []
    shift_cache: dict[str, tuple[int, int]] = {}
    for rewrite in link_rewrites:
        if rewrite.file not in shift_cache:
            old_text = _resolve_post_merge_text(
                rewrite.file, merged_at, snapshot_events, current_texts
            )
            old_body_start = _body_start(old_text)
            new_body_start = _body_start(_apply_migrate_document(old_text))
            shift_cache[rewrite.file] = (
                new_body_start - old_body_start,
                old_body_start,
            )
        shift, old_body_start = shift_cache[rewrite.file]
        if shift != 0 and rewrite.offset >= old_body_start:
            shifted.append(dataclasses.replace(rewrite, offset=rewrite.offset + shift))
        else:
            shifted.append(rewrite)
    return shifted


def _migrate_entry(
    entry: okf.MergeLedgerEntry,
    snapshot_events: dict[str, list[tuple[datetime, str]]],
    current_texts: Mapping[str, str],
) -> okf.MergeLedgerEntry:
    """Migrate ONE `MergeLedgerEntry` to OKF v0.2 (design.md Decision 1):
    every whole-document snapshot through `_migrate_whole_document_snapshot`
    (recursively covering any embedded pre-relocation `merged_from`), a
    V1-V4 `index_before` catalog snapshot's `okf_version` flipped in place
    (V5's is already `""` -- nothing to flip), and every `link_rewrites[].
    offset` shifted for its target file's frontmatter move. The SAME
    function migrates both a sidecar's own top-level entries and any
    embedded historical entry a snapshot carries (called back from
    `_migrate_whole_document_snapshot`), so Check B's nested-prefix
    equality is preserved by construction: identical inputs receive
    identical treatment regardless of nesting depth."""
    new_index_before = entry.index_before
    if entry.index_before and _needs_okf_version_flip(entry.index_before):
        new_index_before = _flip_index_okf_version(entry.index_before)
    return dataclasses.replace(
        entry,
        absorbed_snapshot=_migrate_whole_document_snapshot(
            entry.absorbed_snapshot, snapshot_events, current_texts
        ),
        survivor_before=_migrate_whole_document_snapshot(
            entry.survivor_before, snapshot_events, current_texts
        ),
        index_before=new_index_before,
        link_rewrites=_shift_link_rewrites(
            entry.link_rewrites, entry.merged_at, snapshot_events, current_texts
        ),
        relation_rewrites=[
            dataclasses.replace(
                relation_rewrite,
                snapshot=_migrate_whole_document_snapshot(
                    relation_rewrite.snapshot, snapshot_events, current_texts
                ),
            )
            for relation_rewrite in entry.relation_rewrites
        ],
        provenance_rewrites=[
            dataclasses.replace(
                provenance_rewrite,
                snapshot=_migrate_whole_document_snapshot(
                    provenance_rewrite.snapshot, snapshot_events, current_texts
                ),
            )
            for provenance_rewrite in entry.provenance_rewrites
        ],
    )


def migrate_sidecars_to_okf_v02(
    bundle_dir: Path, *, current_texts: Mapping[str, str]
) -> list[tuple[Path, str, list[okf.MergeLedgerEntry]]]:
    """Migrate every committed merge-ledger sidecar under `bundle_dir` to
    OKF v0.2 shape (okf-v02-migration design.md Decision 1) -- the ledger
    holds bundle documents, so a whole-bundle format migration must include
    it. Pure: never reads a concept file from disk beyond what
    `current_texts` already supplies, and never writes anything -- the
    caller (Phase 6's `repair`) decides how and when to commit the result.

    `current_texts` maps every bundle-relative concept path (`.md` suffix
    included, matching `okf.LinkRewrite.file`) to its CURRENT full text; it
    is consulted only for a `link_rewrites` target file with no later
    ledger snapshot recording its post-merge frontmatter.

    Returns one `(sidecar_path, survivor_id, migrated_entries)` triple per
    sidecar whose migrated entries differ from what is on disk -- a
    sidecar already fully v0.2-shaped (a second run, or a bundle with no
    v0.1 history) is silently omitted, so "does this bundle need `repair`"
    is exactly "is this list non-empty" (mirrors `okf.migrate_document`'s
    own `Unchanged`-means-no-op contract).

    Raises `ValueError` if any whole-document snapshot cannot be migrated
    deterministically (`okf.migrate_document` refuses) or if an
    `index_before` snapshot has no `okf_version` field -- the same "never
    guess" posture `migrate_document` documents (design.md Decision 9): the
    caller refuses the WHOLE run rather than partially migrating a bundle.
    """
    sidecars: list[tuple[Path, str, list[okf.MergeLedgerEntry]]] = []
    for ledger_path in iter_ledgers(bundle_dir):
        metadata, _ = okf.load_frontmatter(ledger_path.read_text(encoding="utf-8"))
        survivor_id = str(metadata["survivor_id"])
        entries = okf.decode_merged_from(metadata)
        sidecars.append((ledger_path, survivor_id, entries))

    snapshot_events = _build_snapshot_index(sidecars)

    changed: list[tuple[Path, str, list[okf.MergeLedgerEntry]]] = []
    for ledger_path, survivor_id, entries in sidecars:
        migrated_entries = [
            _migrate_entry(entry, snapshot_events, current_texts) for entry in entries
        ]
        if migrated_entries != entries:
            changed.append((ledger_path, survivor_id, migrated_entries))
    return changed


def recover(concept_id: str, bundle_dir: Path) -> RecoveryVerdict:
    """Total function of on-disk state (design Decision 1's truth table, no
    heuristic): absent `.pending` is `"none"`; present with a matching
    `sha256(survivor on disk)` is `"roll-forward"` (V landed, only S2 was
    torn -- promote the pending container); present with anything else
    (mismatched hash, OR the survivor missing outright because V never
    landed) is `"roll-back"` (discard the pending container)."""
    pending_path = pending_path_for(concept_id, bundle_dir)
    if not pending_path.is_file():
        return "none"

    metadata, _ = okf.load_frontmatter(pending_path.read_text(encoding="utf-8"))
    expected_sha256 = metadata.get("expected_survivor_sha256")

    survivor_path = okf.concept_path_for(concept_id, bundle_dir)
    if survivor_path.is_file():
        actual_sha256 = survivor_sha256(survivor_path.read_text(encoding="utf-8"))
    else:
        actual_sha256 = None

    if actual_sha256 is not None and actual_sha256 == expected_sha256:
        commit_pending(concept_id, bundle_dir)
        return "roll-forward"

    discard_pending(concept_id, bundle_dir)
    return "roll-back"
