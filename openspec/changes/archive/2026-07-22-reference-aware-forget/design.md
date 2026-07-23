# Design: Reference-Aware Forget + Tombstones (Gap #8 · S2a)

## Technical Approach

Keep `forget`'s existing non-transactional shape: Phase A computes everything
from ONE in-memory bundle snapshot, prints a preview, applies gates, then
Phase B writes atomically with the concept-file unlink LAST. This slice adds two
pure Phase-A computations over that single snapshot — an inbound-reference scan
and an outbound-`supersedes` scan — plus a new `--force` gate and a tombstone
log line. `merge` is the structural template: it already builds an `other_files`
whole-bundle snapshot once and feeds it to the same inbound scanners. No write
path (`links.py`, `relations.py`, `merge`) is modified.

## Architecture Decisions

### Decision: Dedicated detect-only helper, new `bundle/references.py`

**Choice**: New module `src/openkos/bundle/references.py` exposing
`find_inbound_references(files, *, target_id) -> list[InboundReference]`. It
REUSES the merge scanners internally (`find_inbound_link_rewrites`,
`find_inbound_relation_rewrites`), discarding their rewrite payloads and keeping
only presence + kind + relation type.
**Alternatives considered**: adding a `detect_only` flag to the merge scanners;
inlining the scan in `cli/main.py`.
**Rationale**: `forget` is destructive — minimize blast radius. A separate
detect module keeps merge's write-path signatures byte-untouched and gives a
clean, unit-testable seam. Placing it in `bundle/` matches `links.py`/
`relations.py` layering (canonical layer, no `graph` import).

Return shape:
```python
@dataclass(frozen=True)
class InboundReference:
    referrer_id: str            # file minus ".md" — the concept holding the ref
    kind: str                   # "link" | "relation" | "unverifiable"
    relation_type: str | None   # the typed edge's type when kind=="relation"
```
`kind == "unverifiable"` (added by the post-review CRITICAL fix) marks a file
whose frontmatter/`relations:` could not be parsed but whose text mentions the
target id: `find_inbound_references` runs its own fail-closed parse pass and
surfaces such a file (with `relation_type=None`) so `forget` refuses rather than
silently deleting a concept a malformed referrer may still point at. A malformed
file not mentioning the id is ignored, so unrelated corruption never blocks an
unrelated forget.
Internals: call `find_inbound_link_rewrites(files, absorbed_id=target_id,
survivor_id=target_id)` — `survivor_id=target_id` is a harmless placeholder (no
rewrite is ever applied; `new_link` is discarded) — emitting one `link` record
per rewrite. Call `find_inbound_relation_rewrites(files, absorbed_id=target_id,
survivor_id=target_id)`; for each returned file decode its snapshot
(`okf.decode_relations`) and emit one `relation` record per entry whose
`target == target_id`, carrying its `type` (satisfies the "names the relation
type" scenario, which `RelationRewrite` alone cannot). The target concept's own
file is EXCLUDED from `files` by the caller, so self-references never count.

### Decision: Tombstone line format

**Choice**: reuse `bundle_log.insert_log_entry` with a marked single-line entry:
```
**Tombstone** (HH:MM:SSZ): Removed [<title>](/<canonical_id>.md) (id: <canonical_id>).
```
`<title>` is read from the concept's frontmatter (`okf.load_frontmatter` →
`metadata["title"]`) BEFORE deletion; time is `now.strftime("%H:%M:%SZ")` (UTC).
The `## YYYY-MM-DD` section supplies the date; the inline time is the distinct
timestamp. `**Tombstone**` is the distinguishing marker vs. the old plain
`**Forget**` bullet. No `status`, no frontmatter, no leftover file.
**Rationale**: matches the existing `**Bold**:`-prefixed chronological bullet
style and the newline-injection guard already in `insert_log_entry`.

### Decision: Two orthogonal booleans, two decision points

**Choice**: add `force: bool = typer.Option(False, "--force", ...)`. It gates
ONLY the inbound-reference refusal, at a NEW decision point placed after the
preview and before the existing confirm gate. The confirm gate
(`if not auto and cfg.review: TTY-confirm else non-TTY-refuse`) is unchanged.
```
preview → [gate 1: if inbound_refs and not force → refuse, exit 1]
        → [gate 2: existing confirm precedence: --auto / review / TTY]
        → Phase B
```
`--force` and `--auto` never read each other. `--force` on a non-TTY with
`review:true` and no refs still refuses at gate 2. `--force --auto` clears both.
Dangling inbound refs under `--force` are the accepted tradeoff (refuse-not-strip:
never mutate innocent third parties).

## Data Flow

```
_resolve_concept_path (path-safety FIRST)
   │
   ▼
Phase A (one snapshot, no writes)
   read concept_text ─┬─► decode_relations → outbound "supersedes" → Y list
   rglob bundle *.md ─┴─► other_files (excl. reserved + target)
                          └─► find_inbound_references → InboundReference[]
   build new_index_text, new_log_text (tombstone)
   ▼
preview (index/log/delete + inbound refs + "un-hides Y…")
   ▼
gate 1 (--force)  →  gate 2 (confirm)
   ▼
Phase B: write_atomic(index) → write_atomic(log) → remove_file(concept) LAST
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/bundle/references.py` | Create | `InboundReference` + `find_inbound_references`; reuses merge scanners detect-only |
| `src/openkos/cli/main.py` | Modify | `forget`: add `--force`; Phase A snapshot scan + supersedes scan; preview lines; gate 1; tombstone line; read title/relations from concept before delete |

Seams in `cli/main.py::forget` (~L794–921): signature gets `force`; after
`_resolve_concept_path`, read `concept_text = concept_path.read_text()` and build
`other_files` (mirroring merge's `rglob`/reserved/exclude-target loop);
`insert_log_entry` entry string becomes the tombstone; new gate 1 sits between
the preview `typer.echo`s and the existing `if not auto and cfg.review` block.

## Interfaces / Contracts

`find_inbound_references(files: Mapping[str, str], *, target_id: str) ->
list[InboundReference]` — pure, no I/O. Preview: one line per `InboundReference`
(`referrer_id` + kind/type); one resurrection line per `Y` ("removing X un-hides
Y — Y re-enters retrieval"). Empty lists → no lines.

## Efficiency

ONE `rglob` + one read per bundle file → one in-memory snapshot; both internal
scans iterate that map (no N re-reads, same cost profile as `merge`). Today's
`forget` does ZERO scan, so this adds an O(bundle-size) read+scan — the
deliberate, bounded cost of reference-awareness on the destructive path.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `find_inbound_references`: link, typed relation (type named), none, fenced-code link ignored, self-ref excluded | pure fixtures |
| Unit | tombstone line format + idempotent re-run keeps prior tombstone | `insert_log_entry` |
| Integration | refuse (non-zero, no write) on inbound link / relation; `--force` proceeds leaving dangling ref | CLI runner |
| Integration | gate orthogonality: `--force` alone still TTY-prompts; `--force`+`--auto` clears both; `--force` non-TTY still refuses at confirm gate | monkeypatch isatty/config |
| Integration | resurrection disclosure names Y; no `supersedes` → no line | outbound-edge fixture |
| Integration | path-safety still FIRST; partial-write window unchanged (delete LAST) | existing forget suites |

## Threat Matrix

N/A for new boundaries — no new routing, shell, subprocess, or VCS automation.
The pre-existing path-traversal-deletion control (`_resolve_concept_path`) stays
the FIRST Phase-A gate and MUST run before any snapshot read tied to
`concept_id`; verify tests keep this ordering.

## Migration / Rollout

No migration. Code-only change; existing plain `**Forget**` log lines remain
valid history. Revert is code-only.

## Open Questions

- [ ] None blocking. (`--force` dangling refs are an accepted, tested tradeoff;
      S2b descendant cascade is explicitly out of scope.)
