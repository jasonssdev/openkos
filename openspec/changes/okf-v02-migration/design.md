# Design: okf-v02-migration — adopt OKF v0.2 for concept frontmatter and migrate existing bundles

Refs #1064. Inputs: `proposal.md`, Engram `sdd/okf-v02-migration/explore`,
OKF v0.2 (§5.1 `sources`, §5.2 `generated`, §5.4 `status`, §7 actors,
§11 conformance, §12 versioning, §13.1 breaking changes). Decision record:
[ADR-0029](../../../docs/adr/0029-adopt-okf-v02-frontmatter.md).

Orchestrator decisions taken as final: **A** no engine write of
`status: deprecated` (deprecation stays computed from `supersedes`); **B** the
computed status shown by `list`, `concept_read` and MCP switches
`active` → `stable`; **C** `repair` leaves a non-empty hand-written
`# Citations` section in place and reports it. Also final: `generated: {by,
at}` replaces `timestamp`; migrated documents get `generated.by:
openkos/legacy` with `generated.at` equal to the old `timestamp`; `sources` is
generated from `provenance`, one way, never read back; readers accept both
shapes; `okf_version: "0.2"` flips in the same `repair` commit; per-claim
`[^id]` footnotes are deferred indefinitely.

## Technical Approach

Three layers, all format knowledge inside `src/openkos/model/okf.py`
(AGENTS.md: the OKF adapter is one seam):

1. **Readers first.** Two pure accessors — `generation_time(metadata)` and
   `declares_deprecated(metadata)` — become the only way any module reads a
   concept's generation time or own lifecycle status. They accept v0.1, v0.2
   and mixed documents. The computed display status becomes
   `stable | deprecated`.
2. **Builders emit v0.2.** The three document builders
   (`build_source_concept`, `build_concept`, `build_merged_document`) and the
   new `migrate_document` are the only code that *introduces* the v0.2 shape:
   `generated`, `status: stable`, and the `sources` projection. Every other
   writer carries frontmatter verbatim, as today; the one non-builder that
   changes `provenance` (`bundle/provenance.apply_provenance_rewrites`)
   *maintains* an existing `sources` key and never introduces one. No
   unrelated verb migrates a document as a side effect (proposal decision 3
   rejects lazy migration).
3. **`repair` migrates explicitly.** A pure per-document function
   (`okf.migrate_document`, bytes in → bytes out) plus a ledger migration
   (`bundle/ledger.migrate_sidecars_to_okf_v02`) are orchestrated by a new
   `application/repair.py` service. `repair` plans everything in memory,
   refuses before any write on anything it cannot migrate deterministically,
   writes sidecars, then documents, then `index.md` last, and lands one
   `openkos: repair (...)` commit.

Ordering across the chain keeps every intermediate `main` readable and every
user bundle safe: readers ship before writers; the migration library ships
before the verb that uses it.

## Architecture Decisions

### Decision 1: Merge ledger vs migration — `repair` migrates the ledger; `unmerge` is not changed

**Choice**: `repair` rewrites every merge-ledger sidecar entry with the same
pure migration it applies to live documents, in the same commit:

- every whole-document snapshot — `absorbed_snapshot`, `survivor_before`,
  each `relation_rewrites[].snapshot`, each `provenance_rewrites[].snapshot` —
  goes through `okf.migrate_document`; a `survivor_before` that embeds a
  pre-relocation `merged_from` list has those embedded entries migrated
  recursively by the same function, so `doctor`'s Check B (nested-prefix
  equality, `bundle/ledger.scan_nesting_violations`) still holds: equal
  inputs map to equal outputs;
- a V1–V4 entry's `index_before` whole-`index.md` snapshot has its
  `okf_version` set to `"0.2"` (its body is untouched), so a V1–V4 unmerge
  cannot put `okf_version: "0.1"` back and `_expected_post_merge_index_and_log`
  still reconstructs the current catalog; `log_before` is unchanged;
- every `link_rewrites[].offset` (a file-absolute offset into the
  *post-merge* text, `okf.LinkRewrite`) is shifted by the change in the
  position at which that file's body begins when its frontmatter is migrated,
  measured on the file's frontmatter **as it stood right after that merge**
  (see "Link-offset shift" below).

`unmerge`, `prepare_unmerge`, `reverse_relation_rewrites`,
`reverse_provenance_rewrites` and `reverse_link_rewrites` are not modified.
They keep restoring exactly the ledger's recorded bytes and keep their
fail-closed drift checks. One user-facing addition: when `unmerge` refuses on
drift and the bundle's `index.md` declares an `okf_version` other than
`okf.OKF_VERSION`, the refusal appends "this bundle predates OKF 0.2; run
`openkos repair` first".

**What "byte parity" means after a format migration** (the promise ADR-0002
makes, restated precisely in ADR-0029):

- For a merge recorded **after** the bundle was migrated, `unmerge` restores
  byte-identical pre-merge bytes. Unchanged.
- For a merge recorded **before** migration and reversed **after** it,
  `unmerge` restores `migrate_document(pre-merge bytes)` for every concept
  document the merge touched — exactly the bytes `repair` would have written
  for that document had the merge never happened. As an invariant, for every
  concept document `d` the merge touched:
  `unmerge(repair(merge(B)))[d] == repair(B)[d]`.
  Migration commutes with merge reversal. `index.md` and `log.md` keep their
  existing contracts (V5 surgical reversal; V1–V4 snapshot restore), with the
  `okf_version` flip applied to the snapshot.
- Anything the migration cannot keep coherent fails closed through the
  existing drift checks (unmerge refuses and writes nothing); it never
  restores a v0.1 document into a v0.2 bundle and never corrupts a file.

**Link-offset shift.** For entry `E` (merged at `t`) and a file `f` with
link rewrites in `E`, the frontmatter of `f` right after `E` is the
frontmatter of the earliest snapshot of `f` recorded by any ledger entry
(any sidecar) with `merged_at > t` — a later merge's `survivor_before`,
`absorbed_snapshot`, `relation_rewrites` or `provenance_rewrites` snapshot of
`f` — or, when there is none, `f`'s current text. The shift is
`body_start(migrate_document(x)) - body_start(x)` for that text `x`, where
`body_start` is the offset at which the unchanged body begins (measured on the
actual texts, never assumed from the YAML length, because a re-dump may also
normalize the whitespace between the closing `---` and the body). Offsets
below the old body start are never shifted. The index of snapshots is built
once, from the pre-migration ledger state, across all sidecars. When the
reconstruction is wrong (a manual edit landed between merges), the shifted
offset no longer points at `new_link`, and `reverse_link_rewrites` refuses —
the same outcome as today for any post-merge frontmatter edit.

**Alternatives considered**:

- *Normalize on the way back* (unmerge restores v0.1 bytes, then runs
  `migrate_document` on them). Rejected: it cannot fix the third-party drift
  checks (the reconstructed post-merge text is v0.1, the file on disk is
  v0.2) or the link offsets, so reversibility is lost for every merge that
  rewrote an inbound link, relation or provenance — the common case. It is
  also a lazy migration inside an unrelated verb, which decision 3 rejects.
- *Both* (ledger migration plus normalize-on-restore as defense in depth).
  Rejected: after `repair` the ledger is already v0.2 and `migrate_document`
  is the identity on v0.2 input, so the extra path only ever acts in a state
  `repair` itself resolves (an older engine merging into a migrated bundle —
  re-running `repair` migrates those new entries, because detection is "does
  migration change the bytes"). It would hide rewrites inside `unmerge`.
- *Leave the ledger alone and document the loss.* Rejected: it silently
  downgrades ADR-0002's reversibility for every pre-migration merge with
  inbound rewrites.

**Rationale**: the ledger holds bundle documents, so a whole-bundle format
migration must include it; applying the same deterministic function keeps
every existing reversal and drift check correct without touching the
reversal code, and the commutation invariant is directly testable.

### Decision 2: `repair`'s cross-survivor pollution gate is scoped to ledger extraction

**Choice**: Gate 2 (`bundle_ledger.bundle_wide_max_entries(...) >= 2` → refuse
the whole run) is evaluated only when the run has pre-relocation ledgers to
extract (`scan_unmigrated` is non-empty). When it trips, it still refuses the
**whole** run, OKF migration included, with the existing message. When there
is nothing to extract, Gate 2 is not evaluated and the OKF phase proceeds.
Gate 1 (any `.pending` marker refuses the whole run) is unchanged and applies
to both phases.

**Alternatives considered**: keep Gate 2 unconditional (any survivor merged
twice would make its bundle permanently unmigratable, although the gate
protects only the verbatim extraction of embedded ledgers); drop Gate 2 for
the extraction too (re-opens the pollution risk the gate exists for).

**Rationale**: the gate's stated purpose is to stop a possibly polluted
embedded ledger from becoming a durable sidecar. OKF-migrating an existing
sidecar makes nothing more permanent: it is one git-revertible commit and a
deterministic function preserves Check B equalities. **Spec impact**: the
`entity-resolution-merge` requirement "Repair Verb Refuses On Any Sign Of
Cross-Survivor Pollution Risk" states the refusal unconditionally and needs a
MODIFIED delta (the spec phase owns it).

### Decision 3: The `sources` entry shape and its determinism

**Choice**: `okf.project_sources(provenance) -> list[dict[str, str]] | None`,
a pure function of the `provenance` value alone:

| `provenance` entry | projected entry |
| --- | --- |
| a Concept ID (`sources/call-with-maria-2026-07-14`, with or without a trailing `.md` or a leading `/`) | `{id: <normalized id>, resource: /<normalized id>.md}` |
| a workspace path under `raw/` (a Source's own provenance) | not projected |
| anything that is not a non-empty string | whole projection is `None` (see below) |

- `id` is the normalized Concept ID (one leading `/` and one trailing `.md`
  removed); `resource` is the bundle-relative path (§6.2) — the same form
  `build_concept` already writes in `## Related` (`/{ref}.md`).
- **No `title`.** A title would have to be read from the cited Source at write
  time, which makes the projection impure and lets it go stale whenever
  `backfill-source-titles` renames a Source — the parity invariant could then
  not be checked from the document alone.
- Order: `provenance` order, first occurrence wins on a duplicate `id`.
  `provenance` order is itself deterministic (builders write it in extraction
  order; merge and retarget are order-preserving unions).
- Key order inside an entry: `id`, then `resource` (as in the §5.1 example).
- Key placement: an existing `sources` key is replaced in place; otherwise it
  is inserted immediately after `provenance`. Deterministic placement is what
  makes migration commute with `apply_provenance_rewrites` (Decision 1).
- `None` (absent `provenance`, a non-list value, a non-string or empty entry,
  or no Concept-ID entry at all): the builder writes no `sources` key.
- A Source document therefore gets no `sources`: its only provenance is its
  raw original, which lives outside the bundle, and its `resource` field is
  already the one bridge to `raw/` (`tests/unit/test_canonical_example.py`).

`okf.refresh_sources(metadata)` is the maintenance form used by the one
non-builder provenance writer: when `sources` is present and the projection
is not `None`, replace it in place; when present and the projection is
`None`, remove it; when absent, do nothing.

**Alternatives considered**: `id` as the last path segment (collides across
type folders); projecting raw entries as `resource: raw/<name>` (a
workspace-relative path that a v0.2 consumer would resolve against the
document's own directory, and that `lint`/`status` already treat as dangling
provenance); including `title`.

**Rationale**: a pure, total, order-preserving projection is what makes the
parity test meaningful and the migration idempotent, and keys by `id` give
#1062 a stable join key for per-source signals (`author`, `last_modified`).

### Decision 4: Where `sources` is written — introduced by builders and `repair`, maintained by the provenance retarget, never read

**Choice**: the projection is applied in exactly these functions, all in
`model/okf.py` except the last: `build_concept` (introduces),
`build_source_concept` (always `None`, so nothing), `build_merged_document`
(introduces for the merged survivor), `migrate_document` (introduces), and
`bundle/provenance.apply_provenance_rewrites` (maintains via
`refresh_sources`). `dump_frontmatter` stays a format-neutral serializer.

**Alternatives considered**: projecting inside `dump_frontmatter` for any
metadata that has `provenance` (structurally impossible to bypass, but every
metadata-only re-dump — `set-sensitivity`, sensitivity propagation, relation
rewrites, title backfill — would then add `sources` to v0.1 documents: a lazy
migration hidden in unrelated commits, and a byte change in the forward
reconstructions `unmerge`'s drift checks compare against, so every
pre-upgrade merge with relation rewrites would refuse to reverse);
introducing `sources` from `apply_provenance_rewrites` too (the same
version-skew problem for provenance rewrites, before `repair` has run).

**Rationale**: with "introduce only in builders and `repair`, maintain only
when present", `apply_provenance_rewrites` produces byte-identical output to
today's for a v0.1 document, so a pre-upgrade merge stays reversible before
`repair` runs, and after `repair` its migrated snapshots carry `sources` that
the retarget keeps in step. Bypass risk is closed by tests, not by placement:
a guard pins the set of functions that store the `provenance` key, and a
second guard forbids reading `sources` outside `project_sources`/
`refresh_sources`.

### Decision 5: `generated.at` per write path

**Choice**: builders take `generated: okf.Generated` (a frozen dataclass
`by: str`, `at: str`) instead of `timestamp: str`; `generated.by` for engine
writes is `okf.engine_actor()` = `f"openkos/{version}"`, where `version` is the
installed distribution version (`importlib.metadata.version("openkos")`,
degrading to `0+unknown` exactly as `mcp/server.py` does).

| Write path | `generated` written |
| --- | --- |
| `ingest` (new Source, re-ingest, regenerate) and its derived objects | `{by: engine_actor(), at: now}` — same instant the caller passes as `timestamp` today |
| `query --save` (Insight) | `{by: engine_actor(), at: now}` |
| `merge` (`build_merged_document`, incl. #645 reconciliation) | the more recent side's `generated`, carried whole (see Decision 6) |
| `set-sensitivity`, sensitivity propagation/backfill, `backfill-source-titles`, relation writes, provenance retarget, `forget`/`purge` edits | unchanged — metadata or catalog edits are not content generation, as today for `timestamp` |
| `repair` migration | `{by: openkos/legacy, at: <old timestamp scalar text>}` |

`at` is a string. For migration, the value is the **source text** of the
legacy `timestamp` scalar, obtained with `yaml.compose` over the verbatim
frontmatter block (`split_frontmatter_verbatim`), because a hand-written
unquoted timestamp loads as a `datetime` whose `isoformat()` would rewrite
`Z` as `+00:00`. "Unchanged" means the string value is identical; YAML
quoting may differ.

**Alternatives considered**: builders computing the actor themselves (reads
distribution metadata inside a pure builder and makes every golden depend on
the installed version); a merge stamping `now` (changes today's semantics and
claims a generation event the merge did not perform).

**Rationale**: an explicit `Generated` argument keeps builders pure and
goldens deterministic (tests pass a fixed actor); `engine_actor()` is the
single patch point full-stream goldens pin, following the project rule that
goldens pin their environment rather than filter output.

### Decision 6: The dual reader

**Choice**, in `model/okf.py` only:

```python
def generation_time(metadata: Mapping[str, object]) -> datetime | None:
    if "generated" in metadata:          # present => authoritative, no fallback
        generated = metadata["generated"]
        if not isinstance(generated, Mapping):
            return None
        return _parse_instant(generated.get("at"))
    return _parse_instant(metadata.get("timestamp"))   # v0.2 §13.1 fallback

def _parse_instant(value: object) -> datetime | None:
    # datetime (YAML-resolved, unquoted) -> as is; str -> fromisoformat or None;
    # a bare date, or anything else -> None
```

- The fallback applies only when `generated` is **absent** (v0.2 §13.1 says
  exactly that). A document that carries `generated` is never second-guessed
  from a leftover `timestamp`.
- `datetime` values are accepted. The v0.2 spec's own examples write
  `at:` unquoted, which YAML resolves to a `datetime`; rejecting it would make
  every spec-style hand-written document unreadable. This is a deliberate
  widening over today's `_parse_timestamp` (which returned `None` for a
  `datetime`, so a hand-written unquoted `timestamp` always lost a merge
  comparison); aware-versus-naive comparisons still fail closed.
- `build_merged_document` replaces `_absorbed_is_more_recent` over
  `timestamp` with the same rule over `generation_time`, keeps `freshness`
  travelling with the winner, adds `generated` and `timestamp` to its special
  keys, and writes the winner's generation as v0.2: the winner's `generated`
  mapping verbatim, or `{by: openkos/legacy, at: <winner's timestamp value>}`
  when the winner is a v0.1 document. The merged survivor never carries
  `timestamp`. Survivor-wins stays the fail-closed default; when the winner
  has neither key, no `generated` is written.
- `declares_deprecated(metadata) -> bool` is `metadata.get("status") ==
  "deprecated"` — the exact comparison `lifecycle.py` and `bundle/listing.py`
  make today, now in one place. `active`, `stable`, `draft`, an absent key and
  any other value are not deprecating. (Absent means `stable`, v0.2 §5.4.)
- Status normalization on write: builders write `stable`; `migrate_document`
  and `build_merged_document` rewrite exactly `active` → `stable` and leave
  every other value (including `draft`, `deprecated`, and unknown strings)
  untouched. No engine path writes `deprecated` (decision A).

**Rationale**: two accessors and one normalization rule are the whole reader
surface; everything else keeps reading through them, so the format knowledge
stays in the seam.

### Decision 7: Computed display vocabulary is `stable | deprecated` (decision B)

**Choice**: `bundle/listing.BundleObject.status`,
`application/concept_read.ConceptRecord.status`
(`Literal["stable", "deprecated"]`) and the MCP concept payload report
`stable` where they report `active` today. The computation is unchanged:
`deprecated` when the document declares it or it is a non-self `supersedes`
target, otherwise `stable`. A hand-written `draft` is displayed as `stable`
in this change (retrieval already treats it as not deprecated); a tri-state
display is a follow-up (Open Questions).

**Rationale**: the column should use the vocabulary the user now sees in the
frontmatter. The value change is user-visible for scripts and MCP clients and
is called out in the CHANGELOG.

### Decision 8: `migrate_document` — the pure per-document migration

**Choice**: `okf.migrate_document(text: str) -> MigrationResult`, where
`MigrationResult` is one of `Unchanged`, `Migrated(text, changes)`, or
`Refused(reason)`. Rules, each independent:

| Rule | Condition | Rewrite |
| --- | --- | --- |
| R1 generated | `generated` absent and `timestamp` is a scalar | replace `timestamp` in place with `generated: {by: openkos/legacy, at: <scalar source text>}` |
| R1 (no-op) | `generated` present (with or without a leftover `timestamp`), or neither key | nothing; a leftover `timestamp` beside `generated` stays as an unknown key |
| R1 refusal | `generated` absent and `timestamp` is a mapping or list | `Refused("timestamp is not a scalar")` |
| R2 status | `status == "active"` | `status: stable` in place |
| R3 sources | `project_sources(provenance)` is not `None` and differs from the current `sources` | set via `refresh`/insert rule of Decision 3 |
| R4 citations | `type == "Source"` and the body ends with a bare `# Citations` heading (only whitespace after it) | remove that heading and the blank line before it; body ends with exactly one `\n` |
| report | a non-empty `# Citations` section anywhere, or a bare one that is not trailing | left in place; reported as `legacy_citations` (decision C) |

- Unparseable frontmatter or a missing frontmatter block →
  `Refused("unparseable frontmatter")`.
- When no rule fires the result is `Unchanged` and **the input bytes are
  returned untouched** — no re-serialization — so an already-v0.2 or
  hand-formatted document is never cosmetically rewritten, and detection is
  exactly "would migration change the bytes".
- When a rule fires, the frontmatter is re-serialized through
  `dump_frontmatter` (the same path every engine rewrite already takes) and
  the body is preserved byte-for-byte except R4.
- Properties required by tests: idempotent (`migrate(migrate(x)) ==
  migrate(x)`); equivalent to the new builders (`migrate(old_builder(args)) ==
  new_builder(args, generated=Generated("openkos/legacy", args.timestamp))`
  for every v0.1 golden); commutes with `apply_provenance_rewrites` and
  `apply_relation_rewrites` (`migrate(apply(s)) == apply(migrate(s))`).
- The new `build_source_concept` body ends
  `...{section.rstrip("\n")}\n` so R4's output and the builder's output agree.

**Rationale**: a pure bytes-in/bytes-out function is exhaustively testable,
reused unchanged for live documents and ledger snapshots, and its "Unchanged
returns the input" contract makes idempotency and mixed-bundle detection the
same fact.

### Decision 9: `repair` orchestration, detection, safety, report and commit

**Choice**: a new service `src/openkos/application/repair.py`
(ADR-0018) with `plan_repair(bundle_dir) -> RepairPlan | RepairRefusal` (pure
reads, no writes) and `apply_repair(root, plan) -> RepairOutcome`; the CLI keeps
parsing, printing and exit codes. The `@_guard_workspace_lock("repair")`
decorator and `config.require_workspace` check are unchanged, so no other
openkos verb runs concurrently.

Plan phase (every refusal happens here, before any write):

1. Gate 1: any `.pending` marker → refuse the whole run (unchanged text).
2. Ledger extraction: `scan_unmigrated`; if non-empty and
   `bundle_wide_max_entries >= 2` → refuse the whole run (Decision 2).
3. OKF scan: every non-reserved `.md` via `fsio.snapshot_read` (bytes kept as
   drift baselines) through `migrate_document`; any `Refused` → refuse the
   whole run, naming each concept id and reason (no override; "repair never
   guesses"). Every sidecar entry is dry-run through the ledger migration.
4. Bundle version: `index.md` present and its `okf_version != "0.2"`
   (absent included) → flip planned. A bundle with no `index.md` gets none
   (OKF §11 tolerates a missing index).
5. Nothing to do (no extraction, no document or sidecar whose bytes change,
   no flip) → `openkos repair: nothing to migrate -- no unmigrated merge
   ledger and no OKF 0.1 content found.` and exit 0, writing nothing.

Detection is therefore per artifact, not per bundle: a v0.1 bundle, a v0.2
bundle and a mixed one (partial manual migration, a hand-authored v0.2
document, a merge made by an older engine after migration) are all handled by
the same rule, and the `okf_version` flip happens only in a run that leaves
every document migrated.

Apply phase, in this order:

1. Reset-point note or warning (unchanged text).
2. `_reject_drifted_targets` against the plan's baselines (an editor outside
   openkos could have written since the plan read).
3. Ledger extraction writes (unchanged behavior), if any.
4. Sidecar OKF migration (`bundle_ledger.rewrite_entries_at`, one
   `fsio.write_atomic` per sidecar), computed from the post-extraction state.
5. Concept documents (`fsio.write_atomic` each).
6. `index.md` `okf_version` flip **last**: frontmatter re-rendered,
   body kept verbatim via `split_frontmatter_verbatim`.
7. One `_autocommit` over every touched path.
8. `_refresh_derived_after_write(layout, cfg, verb="repair")`, as
   `merge`/`unmerge` do, so derived stores do not report stale files.

Torn-write safety: a crash before step 6 leaves `okf_version: "0.1"` over a
partly migrated bundle — never a bundle that claims v0.2 while holding v0.1
documents; readers accept the mixed state; re-running `repair` completes it
(each artifact is idempotent); the printed reset point undoes it. A crash
between steps 4 and 5 can make a pre-migration merge temporarily
unreversible; `unmerge` fails closed and names `repair` (Decision 1).

Report (stdout, one line each, only lines that apply):

```
openkos repair: migrated 1 ledger to bundle/.state/ledger/.
openkos repair: migrated 7 documents to OKF 0.2 (generated: 7, status: 7, sources: 5, empty # Citations removed: 2).
openkos repair: migrated 3 merge-ledger sidecars to OKF 0.2.
openkos repair: okf_version 0.1 -> 0.2 in bundle/index.md.
openkos repair: left in place -- 1 document keeps a hand-written # Citations list (legacy, OKF 0.2 section 13.1): concepts/stoicism
```

Commit message: `openkos: repair (<parts>)` where `<parts>` joins, with `; `,
the applicable of `migrate N ledger(s) to bundle/.state/ledger/` (existing
wording) and `migrate M document(s) and K ledger sidecar(s) to OKF 0.2`.

The verb's `help=` text and docstring are updated to describe both
migrations. No confirm gate and no `--dry-run` are added (the verb has none
today; the reset point and single commit are the review surface; a
`--dry-run` is a candidate follow-up).

**Rationale**: reuses every guard `repair` already has, keeps the verb's
"refuse rather than guess" posture, and makes the write order carry the
bundle-version invariant.

### Decision 10: Where ADR-0029 ships and how the chain is cut

**Choice**: ADR-0029 is written now (status `Proposed`) and lands in slice 1's
PR, the first code PR of the chain. See Migration / Rollout for the slice
plan.

## Data Flow

Ingest / query --save (after slices 2a, 2b):

```
application/ingest, application/query
   │  okf.Generated(by=okf.engine_actor(), at=now)
   ▼
okf.build_concept / build_source_concept
   │  status: stable, generated, provenance, sources=project_sources(provenance)
   ▼
dump_frontmatter ──► fsio.write_atomic ──► bundle/<type>/<slug>.md
```

Merge:

```
survivor meta ─┐
               ├─► okf.build_merged_document ─► generation_time() picks winner
absorbed meta ─┘        │ generated (winner, v0.2 form), active->stable,
                        │ provenance union, sources=project_sources(...)
                        ▼
third-party files ─► bundle/provenance.apply_provenance_rewrites
                        │ refresh_sources (only if `sources` present)
                        ▼
                   ledger sidecar keeps verbatim pre-merge snapshots
```

Repair:

```
plan_repair(bundle_dir)
  ├─ Gate 1 (pending markers)            ── refuse whole run
  ├─ scan_unmigrated + Gate 2 (scoped)   ── refuse whole run
  ├─ migrate_document(each .md)          ── Refused => refuse whole run
  ├─ ledger dry run (snapshot index, offset shifts)
  └─ index.md okf_version check
        │
apply_repair(root, plan)
  drift guard ─► extraction ─► sidecars ─► documents ─► index.md (last)
        └─► _autocommit (one commit) ─► refresh derived stores
```

Unmerge after repair (unchanged code): restores the migrated snapshots;
third-party drift checks reconstruct from migrated snapshots, which commute
with the rewrite passes, so they match the migrated files on disk.

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `src/openkos/model/okf.py` | Modify | `OKF_VERSION = "0.2"`; `Generated`, `engine_actor()`, `LEGACY_ACTOR = "openkos/legacy"`, `SOURCES_KEY`; `generation_time`, `_parse_instant`, `declares_deprecated`; `project_sources`, `refresh_sources`; builders take `generated` and emit v0.2; `build_merged_document` generation rule; `migrate_document` + `MigrationResult`; §-reference comments renumbered |
| `src/openkos/lifecycle.py` | Modify | `deprecated_concept_ids` reads through `okf.declares_deprecated` |
| `src/openkos/bundle/listing.py` | Modify | reads through `okf.declares_deprecated`; computed status `stable`/`deprecated` |
| `src/openkos/application/concept_read.py` | Modify | `ConceptRecord.status: Literal["stable", "deprecated"]` |
| `src/openkos/mcp/gate.py` | Modify (value only) | payload `status` carries `stable`/`deprecated` from `ConceptRecord` |
| `src/openkos/bundle/provenance.py` | Modify | `apply_provenance_rewrites` calls `okf.refresh_sources` |
| `src/openkos/bundle/ledger.py` | Modify | `migrate_sidecars_to_okf_v02` (snapshot migration, recursive embedded entries, `index_before` flip, link-offset shift over a cross-sidecar snapshot index) |
| `src/openkos/application/repair.py` | Create | `plan_repair`, `apply_repair`, `RepairPlan`, `RepairRefusal`, `RepairOutcome` |
| `src/openkos/application/ingest.py`, `src/openkos/application/query.py` | Modify | pass `okf.Generated(...)` instead of `timestamp=` |
| `src/openkos/cli/main.py` | Modify | `repair` becomes thin over `application/repair.py`, new report/help/commit text; the calls into the `application_ingest` / `application_query` staging functions (~lines 5302, 5421, 5516, 15081), which pass `timestamp=` down to the `model/okf.py` builders today, pass `generated` instead; `unmerge` drift refusal hint |
| `src/openkos/bundle/index.py` | Unchanged code | writes `okf_version` from `OKF_VERSION` (only at `init`, via `bundle/bundle.py`) |
| `src/openkos/templates/agents.md.template` | Modify | v0.2 field set; no `# Citations` convention; `okf_version` / sections renumbered |
| `examples/good-life-demo/**` | Modify | regenerated by `repair` (slice 4a) |
| `tests/unit/model/fixtures/okf_v01_documents.json` | Create | frozen v0.1 builder outputs (moved from today's goldens) as migration inputs |
| `tests/unit/model/fixtures/okf_framing_goldens.json` | Modify | v0.2 goldens |
| `tests/unit/model/test_okf_v02_readers.py`, `test_okf_sources_projection.py`, `test_okf_migrate_document.py` | Create | reader, projection/parity, migration property tests |
| `tests/unit/test_sources_key_guard.py` | Create | AST guards (Testing Strategy) |
| `tests/unit/bundle/test_ledger_okf_migration.py` | Create | snapshot/offset/Check-B tests |
| `tests/unit/cli/test_repair.py` | Modify | OKF phase, scoped Gate 2, idempotency, report, single commit, merge→repair→unmerge |
| `docs/*.md`, `AGENTS.md` | Modify | slice 4b (v0.2 shape, section renumbering, numbered-citation drift) |
| `docs/adr/0029-adopt-okf-v02-frontmatter.md`, `docs/adr/README.md` | Create / Modify | ADR-0029, index row |

## Interfaces / Contracts

```python
# model/okf.py
OKF_VERSION: Final = "0.2"
LEGACY_ACTOR: Final = "openkos/legacy"
SOURCES_KEY: Final = "sources"

@dataclass(frozen=True)
class Generated:
    by: str   # an OKF §7 actor
    at: str   # ISO-8601 with explicit offset for engine writes

def engine_actor() -> str: ...                      # "openkos/<version>" | "openkos/0+unknown"
def generation_time(metadata: Mapping[str, object]) -> datetime | None: ...
def declares_deprecated(metadata: Mapping[str, object]) -> bool: ...
def project_sources(provenance: object) -> list[dict[str, str]] | None: ...
def refresh_sources(metadata: dict[str, object]) -> dict[str, object]: ...  # returns a copy

def build_source_concept(*, ..., generated: Generated, ...) -> str: ...   # replaces timestamp=
def build_concept(*, ..., generated: Generated, ...) -> str: ...          # replaces timestamp=

@dataclass(frozen=True)
class MigrationChanges:
    generated: bool
    status: bool
    sources: bool
    citations_removed: bool
    legacy_citations: bool      # a hand-written list was left in place

@dataclass(frozen=True)
class Unchanged:
    legacy_citations: bool

@dataclass(frozen=True)
class Migrated:
    text: str
    changes: MigrationChanges

@dataclass(frozen=True)
class Refused:
    reason: str

MigrationResult = Unchanged | Migrated | Refused
def migrate_document(text: str) -> MigrationResult: ...

# bundle/ledger.py
def migrate_sidecars_to_okf_v02(
    bundle_dir: Path, *, current_texts: Mapping[str, str]
) -> list[tuple[Path, str, list[okf.MergeLedgerEntry]]]: ...   # (path, survivor_id, entries) whose bytes change

# application/repair.py
@dataclass(frozen=True)
class RepairPlan: ...        # extraction list, document rewrites, sidecar rewrites, index flip, baselines, report counts
@dataclass(frozen=True)
class RepairRefusal:
    message: str
def plan_repair(bundle_dir: Path) -> RepairPlan | RepairRefusal: ...
def apply_repair(root: Path, plan: RepairPlan) -> RepairOutcome: ...
```

On-disk shape of a derived concept written by the engine after slices 2a/2b
(the YAML emitter chooses quoting and block style):

```yaml
type: Concept
title: Stoicism
description: ...
tags: []
generated:
  by: openkos/0.3.0
  at: '2026-09-29T10:00:00Z'
status: stable
version: 1
freshness: snapshot
sensitivity: private
provenance:
- sources/notes-on-the-enchiridion-2026-07-05
sources:
- id: sources/notes-on-the-enchiridion-2026-07-05
  resource: /sources/notes-on-the-enchiridion-2026-07-05.md
```

## Testing Strategy

Strict TDD is enabled for this repository: every behavior below starts as a
failing test.

| Layer | What to Test | Approach |
| --- | --- | --- |
| Unit | `generation_time` over `generated` str/datetime/malformed/absent × `timestamp` str/datetime/absent; `generated` present never falls back; aware vs naive fails closed | table-driven |
| Unit | `declares_deprecated` over `active`/`stable`/`draft`/absent/`deprecated`/non-string | table-driven |
| Unit | `project_sources`: Concept IDs with/without `.md` and leading `/`, `raw/` entries skipped, duplicates, non-list, non-string entry, empty → `None`; order and key order | table-driven |
| Unit | builders emit v0.2 (goldens regenerated; fixed `Generated`); Source body has no `# Citations` and ends with one `\n` | golden JSON, `check_conformance` on each |
| Unit | `build_merged_document`: v0.2×v0.2, v0.1×v0.2, v0.1×v0.1, unparseable/absent sides, freshness travels with winner, no `timestamp` in output, `active`→`stable`, `deprecated` kept, `sources` = projection of unioned provenance | table-driven |
| Unit | `migrate_document`: each rule alone and combined; `Unchanged` returns the identical string object's bytes; refusals; unquoted `timestamp` keeps its source text; non-trailing and non-empty `# Citations` left and reported | table-driven |
| Property | idempotency, builder equivalence (every entry of `okf_v01_documents.json`), commutation with `apply_provenance_rewrites`/`apply_relation_rewrites`/`apply_link_rewrites` | loops over fixtures |
| Parity | every document a builder writes, and every `apply_provenance_rewrites` output on a document with `sources`, satisfies `sources == project_sources(provenance)`; a bundle-wide sweep after an ingest→merge→repair e2e asserts it for every document that has `sources` | helper `assert_sources_parity(bundle_dir)` |
| Guard | AST scan of `src/openkos/**`: no `Subscript` load, `.get(...)`, or `in` test on `"sources"`/`okf.SOURCES_KEY` outside `project_sources`/`refresh_sources`/`migrate_document`; the set of functions that store the `provenance` key is pinned to the builders and `apply_provenance_rewrites` | both guards verified by mutation (add a forbidden read; remove the `refresh_sources` call) |
| Unit | ledger migration: all snapshot fields, V1–V4 `index_before` flip, recursive embedded `merged_from`, Check B still passes after migration, offset shift from current text and from a later snapshot, offsets below body start untouched | fixtures built by real `merge` calls in `tmp_path` |
| Integration | `repair` on v0.1, v0.2 and mixed bundles; refusal on `.pending`, on scoped Gate 2, on an unparseable document (writes nothing); second run prints nothing-to-migrate and creates no commit; exactly one commit whose file list equals the touched set; `okf_version` written last (crash injection before the flip leaves `0.1`) | CLI tests with `git` workspace fixtures |
| Integration | commutation: for each ledger schema V1–V5, `merge` → `repair` → `unmerge` yields concept documents equal to `repair` on the pre-merge bundle, and no v0.1-shaped document remains; `unmerge` refusal on an unrepaired bundle names `repair` | CLI tests |
| E2E | fresh `init` + `ingest` → `okf_version: "0.2"`, v0.2 shapes, `check_conformance` clean; `list`/MCP show `stable`; readers (retrieval, `list`, lint, graph, MCP) give equal results on a v0.1 bundle, its migrated twin, and a mixed bundle | existing e2e harness; full-stream goldens pin `okf.engine_actor` |
| Fixture | `examples/good-life-demo` passes the product's own `lint` and `status`; stays byte-identical to `repair` applied to the frozen v0.1 copy | `tests/unit/test_canonical_example.py` extension |

Before trusting any test that passes on its first run, mutate the line it
should catch (project practice); purge `__pycache__` between mutation runs.

## Threat Matrix

N/A — no new routing, shell, subprocess, VCS/PR automation,
executable-file classification, or process-integration boundary. `repair`
reuses the existing `_autocommit` unchanged; the only difference is a longer
list of bundle-relative paths, which comes from the bundle walk and is
passed as argv (no shell), as every other verb already does.

## Migration / Rollout

### Existing bundles

Explicit and user-initiated: `openkos repair` (Decision 9). Readers accept
unmigrated and mixed bundles indefinitely; nothing migrates lazily. Rollback
for a bundle is `git revert` of the single repair commit, or `git reset
--hard` to the printed reset point.

### Goldens and fixtures

- Slice 2a moves today's v0.1 builder goldens into
  `tests/unit/model/fixtures/okf_v01_documents.json` (frozen migration inputs)
  and regenerates `okf_framing_goldens.json` with a fixed `Generated`; tests
  that pin `status: active`/`timestamp:` bytes (about 80 occurrences across 12
  test files) are updated in the slice whose writer change moves them.
- Full-stream CLI goldens pin `okf.engine_actor` with a monkeypatch that first
  asserts the attribute exists (an unread monkeypatch fails silently).
- Slice 4a regenerates `examples/good-life-demo/` by running `openkos repair`
  on a scratch copy of the current fixture (the product migrating its own
  canonical example), then commits the result. A frozen copy of the v0.1
  fixture is kept as a test input so the example stays pinned to
  `repair(v0.1 fixture)`. `concepts/stoicism.md` keeps its hand-written
  `# Citations` list and becomes the pinned case for decision C; the docs
  describe it as hand-authored legacy content, not generator output. The
  example must pass the product's own `lint` and `status`, and
  `AGENTS.md` stays byte-identical to a fresh `init` (existing test).

### Slice plan (auto-chain, stacked to main, dependency order)

Forecasts are authored lines (additions + deletions), scaled by the repo's
~1.95x forecast-to-actual ratio; delta specs and generated goldens excluded.
The proposal's slice 3 is split three ways because the ledger migration is
real work the proposal did not size.

| # | Slice | Contents | Depends on | Forecast |
| --- | --- | --- | --- | --- |
| 1 | Readers + display | `generation_time`, `_parse_instant`, `declares_deprecated`; `lifecycle.py`/`listing.py` read through them; display `stable`; MCP value; ADR-0029 + index row | — | ~220-330 |
| 2a | Writers: generated + status | `Generated`, `engine_actor`, `LEGACY_ACTOR`; builders and call sites; `build_merged_document` generation rule; `OKF_VERSION = "0.2"`; frozen v0.1 fixture; goldens | 1 | ~250-390 |
| 2b | Writers: sources | `project_sources`, `refresh_sources`; builders and merge introduce; `apply_provenance_rewrites` maintains; Source body without `# Citations`; parity tests; both AST guards | 2a | ~250-390 |
| 3a | Migration function | `migrate_document`, `MigrationResult`; rule/refusal tables; idempotency, builder-equivalence and commutation properties | 2b | ~200-330 |
| 3b | Ledger migration | `migrate_sidecars_to_okf_v02`: snapshots, recursion, `index_before`, snapshot index, offset shift; Check B test | 3a | ~250-400 |
| 3c | `repair` verb | `application/repair.py`; CLI wiring, report, help, commit; scoped Gate 2; drift guard; derived refresh; `unmerge` hint; idempotency, crash-order and merge→repair→unmerge tests | 3b | ~300-400 |
| 4a | Fixture + template | regenerate `examples/good-life-demo`; `agents.md.template`; example `AGENTS.md`; fixture pin | 3c | ~150-290 |
| 4b | Docs + renumbering | `docs/okf-alignment.md`, `docs/knowledge-object-model.md`, other docs, repository `AGENTS.md`, comments and messages citing moved sections (§9→§11, §11→§12, reserved files §6/§7→§8/§9), numbered-citation drift | 2b (can run beside 3a-3c) | ~150-290 |

Total ~1,770-2,820. Every slice leaves `main` green and safe: before 3c
ships, no user bundle is rewritten; 3a and 3b are libraries with no caller.
Delta specs land with the slice whose behavior they describe (tasks phase
decides).

### Code rollback

Revert in reverse order. Reverting 2a/2b returns writers to v0.1 while
slice 1's readers still read v0.2 documents already written. Slice 1 is
reverted last and only if no v0.2 document exists anywhere, because
pre-slice-1 readers ignore `generated`.

## Open Questions

- [ ] **Spec gap (blocking for the `entity-resolution-merge` delta, not for
      design):** Decision 2 changes the unconditional Gate-2 refusal in
      "Repair Verb Refuses On Any Sign Of Cross-Survivor Pollution Risk".
      Recommendation: MODIFIED requirement — the refusal applies when the run
      has pre-relocation ledgers to extract; otherwise the OKF phase proceeds.
- [ ] **Source documents carry no `sources`.** Recommendation: keep (Decision
      3). #1062, which lifts `author`/`last_modified` of an incoming file,
      will need to decide how a Source records signals about its own raw
      original — most likely one `sources` entry derived from the Source's
      `resource`; that is #1062's decision, not this change's.
- [ ] **Pre-existing drift, not introduced here:** `ingest` writes
      `provenance: [raw/<name>]` on every Source, while the canonical example
      and its `AGENTS.md` say a Source carries no `provenance`, and the
      example's test forbids `raw/` provenance on any document.
      Recommendation: file separately; the projection is unaffected because
      `raw/` entries are not projected.
- [ ] **Pre-existing:** a Source's `resource: raw/<name>` is
      workspace-relative, which OKF §6.2 would resolve against the document's
      directory. Recommendation: address with full OKF export (MVP 3), not
      here.
- [ ] **`draft` display.** Recommendation: follow-up issue for a tri-state
      computed status; out of scope because no engine path writes `draft`.
- [ ] **`repair --dry-run`.** Recommendation: follow-up; the reset point and
      single commit are the review surface today, and the verb has no preview
      now.
- [ ] **Re-serialization of `datetime` values** (a hand-written unquoted
      `generated.at` carried through a merge is re-emitted by PyYAML as
      `2026-06-20 22:53:05+00:00`). Pre-existing for `timestamp`; not changed
      here.
