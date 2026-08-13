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

import hashlib
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
