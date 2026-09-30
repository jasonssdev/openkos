# Design: source-tag-sync — add a Source's tags to its existing derived concepts

Refs #1093. Proposal: `proposal.md`. Specs: `specs/tag-sync/spec.md` (new),
`specs/ingestion/spec.md` (MODIFIED). ADR: ADR-0033 (Proposed).

## Technical Approach

`sync-tags` is `set-sensitivity`'s Source branch with a different per-member
computation, plus `backfill-sensitivity`'s "every Source in one sweep" mode:

1. **Pure resolver (canonical layer).** `bundle/provenance.py` gains
   `resolve_source_tag_additions(files, *, source_id, source_tags,
   source_level)` beside `resolve_source_raises`. It walks
   `find_provenance_descendants(files, root_ids={source_id})`, drops the
   root and every `type: Source` member, and classifies each remaining
   member as staged, skipped-malformed, skipped-below-sensitivity, or
   already-complete. No I/O, no `Path`, no `openkos.graph` import (the
   module's own rule).
2. **Application service (ADR-0018).** `application/lifecycle.py` gains
   `PreparedTagSync`, `prepare_sync_tags(layout, source_id | None)` and
   `sync_tags_core(layout, prepared)`, shaped like
   `prepare_relate`/`relate_core` and
   `prepare_set_volatility`/`set_volatility_core`: Phase A reads one
   whole-bundle snapshot (`fsio.snapshot_read`, bytes + text), resolves the
   plan, renders the new `log.md` text, and returns typed data carrying a
   `boolean_confirmation("sync-tags")` and the drift baselines. It never
   echoes, prompts, or checks a TTY.
3. **CLI adapter.** `cli/main.py::sync_tags_cmd` parses arguments, renders
   the preview and stderr lines, drives the shared confirm precedence, calls
   `_reject_drifted_targets`, calls `sync_tags_core`, `_autocommit`s, and
   `_refresh_derived_after_write(verb="sync-tags")` — the cross-cutting
   write infrastructure ADR-0018 keeps in the adapter.
4. **Ingest advisory.** One `typer.echo(..., err=True)` in the Source-only
   rewrite preview block (`cli/main.py`, next to the `set-sensitivity`
   advisory, keyed on `source_plan.tags_added`).

## Architecture Decisions

### Decision 1: Reuse the subset closure, not reachability

| Option | Tradeoff | Decision |
|---|---|---|
| `find_provenance_descendants` (all provenance inside the closure) | Excludes multi-Source concepts; identical to `set-sensitivity`'s write set | **Chosen** |
| `provenance_reachable` (any provenance intersects) | Tags multi-Source concepts too, but a `query --save` citing five Sources would accumulate all five Sources' tags; diverges from every existing propagation verb | Rejected |

A merged survivor already carries the union of both members' tags
(`build_merged_document` unions list fields), so the excluded case is
mostly covered at merge time. Recorded as a Non-Goal in the spec.

### Decision 2: Union only, never remove (ADR-0033)

`okf.union_tags(existing, source_tags)`; the Source's tags pass through
`okf.normalize_tags` (the same normalization the incoming lift uses); the
descendant's existing list is used verbatim. See ADR-0033 for why removal
is unsafe without per-tag provenance.

### Decision 3: Malformed descendant `tags` is skipped, not normalized

`okf.normalize_tags` returns `()` for a bad shape, so blindly unioning would
overwrite a hand-written value with the Source's tags. The resolver instead
treats only "absent/None" and "list of `str`" as writable; everything else
(a bare string included — the engine always emits a list, so a string is
hand-written) is reported and left byte-unchanged.

### Decision 4: Sensitivity floor, not sensitivity write (ADR-0033)

`source_level = okf.combine_sensitivity(source_raw, "public")` gives the
Source's canonical fail-closed level. A member is skipped when
`okf.sensitivity_direction(member_raw, source_level) == "raise"`. A dirty
member value ranks at `private` or `confidential` through `_rank`, so it is
never *falsely* below except when missing (`private`) under a
`confidential` Source — which is exactly the case to protect. `_rank`
stays private; `sensitivity_direction` is its sanctioned public face.

### Decision 5: The Source is a drift baseline even though it is not written

The plan is a function of the Source's tags and sensitivity. If either
changes while the prompt waits, the staged bytes no longer express what the
user confirmed. `_reject_drifted_targets` compares bytes only; passing a
read-only path is legal (it never writes). With `--all`, every Source that
contributed at least one staged addition is included.

### Decision 6: No tag value in `log.md` or the commit (ADR-0033)

Log line: `**Sync-tags**: Added tags from [<source>](/<source>.md) to N
concept(s).`; with `--all`: `**Sync-tags**: Added tags from M Source(s) to
N concept(s).` Commit: `openkos: sync-tags <source-id>` or
`openkos: sync-tags --all`. The terminal preview (local only) does show the
tag values.

### Decision 7: Derived-store refresh re-embeds touched concepts

Unlike a sensitivity write, a tag write changes the embedding input
(`state/reindex.py::_compose_header` includes tags), so the vector stage of
`_refresh_derived_after_write` re-embeds each touched concept once through
the existing content-hash cache. No new code; the helper's docstring claim
that "a frontmatter-only write is a cache hit" is about sensitivity-style
writes and is corrected in the call-site comment, not the helper.

### Decision 8: The `--all` fold

Sources are enumerated by `type: Source` from the same snapshot, sorted by
concept id. Additions accumulate per member (`dict[str, list[str]]`,
insertion-ordered union); each member's content is rendered once from its
original metadata after all Sources are folded. A member that one Source
skips for sensitivity may still be tagged by another Source at or below its
level. Warnings for a malformed member print once, not once per Source.

### Decision 9: Help panel `Maintain`, verb scope `cli`

It repairs drift like `backfill-sensitivity`/`backfill-source-titles`
(both `Maintain`). Commits for the code are scoped `cli`, `bundle`, and
`docs`/`sdd` per AGENTS.md.

## Data Flow

```
sync-tags <id>|--all
  └─ CLI: validate args (exit 1) ─ require_workspace ─ read_config
       └─ application.prepare_sync_tags(layout, source_id)
            ├─ resolve_concept_path + type == Source check      (single)
            ├─ snapshot every non-reserved bundle .md (bytes, text)
            ├─ for each root Source (sorted):
            │    bundle.provenance.resolve_source_tag_additions(...)
            ├─ fold per member ─ okf.dump_frontmatter(meta with new tags)
            └─ render log.md text ─ PreparedTagSync(staged, skipped_*, baselines, confirmation)
       ├─ echo skipped lines (stderr) ─ empty staged → "nothing to sync", exit 0
       ├─ preview ─ confirm gate (auto / review:false / TTY / refuse exit 1)
       ├─ _reject_drifted_targets(staged + roots + log.md)       (exit 3)
       ├─ application.sync_tags_core: write staged (id order), then log.md
       ├─ _autocommit(one commit)
       └─ _refresh_derived_after_write(verb="sync-tags")
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/model/okf.py` | Modify | `TagAddition(concept_id, added, content)` frozen dataclass; `TagSkip(concept_id, reason)` |
| `src/openkos/bundle/provenance.py` | Modify | `resolve_source_tag_additions` |
| `src/openkos/application/lifecycle.py` | Modify | `PreparedTagSync`, `prepare_sync_tags`, `sync_tags_core` |
| `src/openkos/cli/main.py` | Modify | `sync_tags_cmd`; ingest advisory line |
| `tests/unit/bundle/test_provenance_tag_additions.py` | Create | resolver tests |
| `tests/unit/application/test_lifecycle.py` | Modify | prepare/core tests |
| `tests/unit/cli/test_sync_tags.py` | Create | verb tests |
| `tests/unit/cli/test_ingest.py` | Modify | advisory tests |
| `docs/cli.md` | Modify | `openkos sync-tags` section |
| `docs/adr/0033-…`, `docs/adr/README.md` | Create/Modify | ADR-0033 |

## Interfaces / Contracts

```python
# model/okf.py
@dataclass(frozen=True)
class TagAddition:
    concept_id: str
    added: tuple[str, ...]      # in union order, never empty
    content: str                # full re-rendered document

@dataclass(frozen=True)
class TagSkip:
    concept_id: str
    reason: Literal["malformed-tags", "below-source-sensitivity"]

# bundle/provenance.py
def resolve_source_tag_additions(
    files: Mapping[str, str], *, source_id: str,
    source_tags: Sequence[str], source_level: str,
) -> tuple[list[TagAddition], list[TagSkip]]: ...
# sorted by concept_id; excludes source_id and type: Source members

# application/lifecycle.py
@dataclass(frozen=True)
class PreparedTagSync:
    roots: tuple[str, ...]
    additions: tuple[okf.TagAddition, ...]
    skips: tuple[okf.TagSkip, ...]
    new_log_text: str
    baselines: Mapping[str, bytes]   # bundle-relative path -> bytes
    confirmation: BooleanConfirmation

def prepare_sync_tags(layout: config.WorkspaceLayout,
                      source_id: str | None, *, now: datetime) -> PreparedTagSync
def sync_tags_core(layout: config.WorkspaceLayout,
                   prepared: PreparedTagSync) -> list[str]   # landed paths
```

`sync_tags_core` raises a `SyncTagsWriteError(OSError)` carrying the landed
list on a mid-way failure, so the adapter can name what already landed
(matching `set-sensitivity`'s `landed` message).

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit (bundle) | closure, union order, malformed skip, sensitivity skip, Source-typed member skip, idempotence | literal expected values on in-memory `files` dicts |
| Unit (application) | single vs `--all` fold, log text has no tag value, baselines include roots, non-Source refusal | `tmp_path` workspace, no LLM |
| CLI | arg exclusivity, gate precedence, drift exit 3, one commit, refresh called once, advisory | `CliRunner`, git-initialized `tmp_path` |

Absence assertions (no commit, byte-unchanged file, no advisory) each carry
a precondition proving the thing existed or could have appeared. Tests that
pass on first run get a mutation task (tasks.md).

## Threat Matrix

No routing, shell, subprocess, or network boundary is added. The one
boundary crossed is sensitivity (tag text moving between concepts); Decision
4 and Decision 6 are the mitigations, and each has a RED test.

## Migration / Rollout

No migration. The verb is opt-in and additive; existing bundles are
unaffected until a user runs it.

## Open Questions

- [ ] Should `lint`/`next` report tag drift and recommend `sync-tags`?
      Deferred; would need a whole-bundle comparison like the below-source
      sensitivity finding.
- [ ] A cited-union mode for multi-Source concepts (ADR-0016 analogue).
      Deferred.
