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
    path = ledger_path_for(concept_id, bundle_dir)
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
