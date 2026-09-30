# Proposal: source-tag-sync — add a Source's tags to its existing derived concepts

## Intent

Refs #1093. Follows #1062 (preserve-source-frontmatter, ADR-0030), which
has shipped.

Since #1062, a Source's `tags` are its incoming frontmatter's tags unioned
with whatever is already on disk, and every derived concept created in the
SAME `ingest` run inherits them. When the Source's tags change later — the
user edits the raw file's frontmatter and re-ingests (a Source-only
rewrite), or edits the Source's tags by hand — the derived concepts that
already exist keep their creation-time tags. The ingestion spec says so
explicitly (scenario "An existing derived object's tags are unaffected by a
later Source tag change"), and ADR-0030's Consequences name "a re-sync verb
for tags" as follow-up work. Navigation, FTS, and embeddings (tags are part
of the embed header, `state/reindex.py::_compose_header`) then miss
concepts a tag should find.

Success: `openkos sync-tags <source-id>` (or `--all`) adds each Source's
current tags to the derived concepts it grounds, in one reviewable commit,
without ever removing a tag and without ever copying a Source's tags onto a
concept classified below it. A Source-only rewrite that adds tags points at
the verb.

## Scope

### In Scope

1. **New verb `openkos sync-tags (<source-id> | --all) [--auto]`**,
   `rich_help_panel="Maintain"`, guarded by the workspace lock. No LLM.
2. **Write set = `find_provenance_descendants` subset closure** of each
   root Source, minus the Source itself, minus any `type: Source` member —
   exactly the set `set-sensitivity` propagates to (ADR-0009).
3. **Union only** via the shipped `okf.union_tags` / `okf.normalize_tags`;
   stage a file only when the union adds something (ADR-0033).
4. **Fail-closed skips**: a descendant with a malformed `tags` value
   (WARNING, never rewritten); a descendant whose sensitivity ranks below
   the Source's (note naming `set-sensitivity`).
5. **Preview + shared confirm gate + `--auto`**, drift guard over staged
   descendants, root Sources, and `log.md` (exit 3), one `log.md` entry and
   one autocommit that carry no tag value, then the standard derived-store
   refresh.
6. **Ingest advisory**: when a Source-only rewrite's tag-union delta fires,
   one stderr line naming `openkos sync-tags sources/<slug>`.
7. **Application layer**: `prepare_sync_tags` / `sync_tags_core` in
   `application/lifecycle.py` (the `relate`/`set-volatility` shape), a pure
   resolver in `bundle/provenance.py` beside `resolve_source_raises`.
8. **ADR-0033** (union-never-remove + sensitivity floor), `docs/cli.md`
   entry.

### Out of Scope

- Removing tags downstream (ADR-0033 records why).
- Multi-Source descendants outside every single closure (the cited
  high-water mark ADR-0016 built for sensitivity has no tag analogue yet).
- Automatic sync on `ingest`, MCP exposure, a `lint`/`next` tag-drift
  finding.
- Per-tag provenance.

## Capabilities

### New Capabilities

- `tag-sync`: the `sync-tags` verb — targets, closure, union rule,
  fail-closed skips, sensitivity floor, gate, drift guard, log, commit.

### Modified Capabilities

- `ingestion`: "Converged Re-Ingest Source-Only Rewrite" gains the
  `sync-tags` advisory. The "unaffected by a later Source tag change"
  scenario stays true — `ingest` still never re-tags existing objects — and
  is not modified.

## Approach

Mirror `set-sensitivity`'s Source branch and `backfill-sensitivity`'s
single-sweep shape; stage in the application layer, write in the adapter
(ADR-0018: "services stage; adapters write").

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/bundle/provenance.py` | New | `resolve_source_tag_additions` (pure) |
| `src/openkos/model/okf.py` | New | `TagAddition` frozen dataclass (Path-free, like `DescendantRaise`) |
| `src/openkos/application/lifecycle.py` | New | `PreparedTagSync`, `prepare_sync_tags`, `sync_tags_core` |
| `src/openkos/cli/main.py` | New + Modified | `sync_tags_cmd`; ingest Source-only rewrite advisory |
| `docs/adr/0033-...md`, `docs/adr/README.md` | New | ADR-0033 (Proposed) |
| `docs/cli.md` | Modified | `openkos sync-tags` section |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Classified tag text leaks into a lower-classified concept | Med | Sensitivity floor skip; tag values kept out of `log.md` and the commit |
| Rewriting a hand-written, oddly shaped `tags` value | Low | Malformed-shape skip with WARNING |
| Stale plan overwrites a concurrent edit | Low | `_reject_drifted_targets` after the gate, Source included |
| Re-embedding cost on large sweeps | Low | Tags are in the embed header, so touched files re-embed once; the refresh is incremental and fail-open |
| ADR number collision with #1075 (claims ADR-0032) | Med | This change takes 0033; renumber at archive if needed |

## Rollback Plan

The verb is additive and new; revert its PR(s). Bundles already synced keep
the added tags (plain frontmatter values) and each run is one commit, so
`git revert <sha>` in the workspace undoes a specific sync.

## Dependencies

- None blocking. ADR-0033 numbering assumes #1075 lands ADR-0032 first.

## Success Criteria

- [ ] `sync-tags <source-id>` and `--all` add missing Source tags to closure
      descendants and never remove or reorder one.
- [ ] Malformed-tags and below-Source-sensitivity descendants are skipped
      with a named stderr line.
- [ ] One commit, one `log.md` entry, no tag value in either; exit 3 on drift.
- [ ] Source-only rewrite with a tag delta prints the advisory; others do not.
- [ ] Ruff, format check, MyPy, `pytest --cov` (90% branch), eval self-tests green.
