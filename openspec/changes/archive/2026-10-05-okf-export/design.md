# Design: okf-export

Refs #1301. ADR-0048. Inputs: `proposal.md`, the owner's decisions on #1301
(Q1 = C, Q2 = C, Q3 = C, Q4 = A), `docs/roadmap.md` (MVP 5),
`docs/knowledge-object-model.md` ("Sensitivity and access boundaries"),
ADR-0003, ADR-0008, ADR-0019, ADR-0028, ADR-0039, ADR-0042, the OKF v0.2
SPEC (§2, §3, §4.1, §5.1, §5.4, §6, §8, §9, §11, §12), and the code at
`593f9d1d` (0.5.0).

## Technical approach

```
openkos export <target> [--include-private] [--allow-below-source] [--auto]   (cli/main.py)
        |
        v
 target checks: absent or empty, outside workspace  ------------------ refuse, exit 1
        |
        v
 export_service.plan(workspace, include_private)                (application/export_service.py)
   1. one walk of bundle/ via okf._iter_docs  -> {path: bytes} snapshot (inputs)
   2. boundary = sensitivity.export_boundary(metadata, ...)       (fail-closed ALLOWED set + reasons)
   3. superseded = lifecycle.superseded_from_metadata(all docs)  (whole bundle, withheld included)
   4. per allowed doc:
        text = okf.export_document(text, allowed, superseded, complete)
                 (relations/provenance filter, sources re-projection,
                  strip origin_key/merged_from, deprecated-status projection)
        body = links.withhold_links(body, file, allowed)        (decision 1)
   5. index.md -> index.filter_index_for_export(text, allowed);
      log.md   -> log.render_export_log(date)                    (decision 3)
   -> ExportPlan(files, withheld-by-reason, status-drift count)
        |
        v
 preview (counts by reason, "prose outside links is not redacted") -> confirm unless --auto / review:false
        |
        v
 export_service.publish(plan, target)
   a. write every file under mkdtemp(prefix=".<target-name>.openkos-export-", dir=<target-parent>)
   b. okf.check_conformance(staging)          -- violation -> rm staging, exit 1
   c. leak_check(staging, withheld ids)       -- hit       -> rm staging, exit 1
   d. re-read every input; bytes differ       -- changed   -> rm staging, exit 3
   e. os.replace(staging, target)             (same parent => same filesystem)
```

## Decisions

### D1. A narrow service, not an engine entry point

`application/export_service.py` holds `plan_export` and `publish_export`,
synchronous, built on the internal services (ADR-0018, ADR-0039). The CLI
keeps parsing, the preview text, the confirm gate and exit codes. Every
frontmatter decision is a pure function in `model/okf.py` (the OKF seam);
link and catalog filtering are pure functions beside the existing scanners
in `bundle/links.py`, `bundle/index.py` and `bundle/log.py`. No MCP tool in
this change: MCP is a read surface, and an export tool would be a second,
separately-audited egress path.

### D2. The boundary is its own allowed-set predicate

`sensitivity.export_boundary(docs, *, include_private, allow_below_source)`
is pure over the metadata the service already read in its one walk (so the
bytes the drift check compares are the bytes the predicate judged), and
returns the ids that MAY leave plus a reason per withheld id, mirroring
`disclosable_concept_ids` (ADR-0028): an id the walk never reached —
an unreadable file, a file created after the walk — is withheld by
construction. It reuses the fail-closed rank (absent, blank, non-string and
unknown labels rank as `confidential`), so it does NOT use ADR-0003's
"absent ranks private" combine rule: a missing label is a doubtful signal
at a boundary, and fails closed. The rank threshold is `public` by default
and `private` under `--include-private`; there is no parameter that reaches
`confidential`. This is not a new policy: the KOM already says `private` is
"not exported or shared unless you explicitly choose to" and
`confidential` is "excluded from exports and sharing". The predicate also
returns, per withheld id, its reason, which the preview groups.

The below-source rule (decision 2, ADR-0048) is folded into the same
predicate: an allowed object whose own rank is below the maximum rank over
its transitive provenance ancestors is withheld unless `allow_below_source`.
The ancestor walk is a few lines over the same metadata map, kept in
`sensitivity.py` so the module stays a leaf that imports only `okf`. It
ranks ancestors with the fail-closed rank, skips `raw/` entries and ids
naming no document, and treats a malformed `provenance` as below-source.

Rationale for a separate predicate rather than reusing `blocks_disclosure`:
MCP's threshold is `confidential` with a launch opt-in that admits it; the
export threshold is `public`/`private` with no opt-in for `confidential`.
Folding them would give one function two policies, the duplication the
sensitivity module docstring argues against in the other direction.

### D3. Pointers are filtered by channel, then proven absent

Structured channels are filtered where they are parsed: `relations:` via
`okf.decode_relations`/`encode_relations`, `provenance:` via the existing
provenance normalizer, `sources:` re-projected with `okf.project_sources`
(never edited directly — `sources` is a projection, and
`tests/unit/test_sources_key_guard.py` forbids reading it back). Body links
reuse `bundle/links.py`'s scanner, which already masks fenced code but
matches only the bundle-relative inline form (`_LINK_RE`, `[x](/y.md)`);
this change adds the relative inline form (`./y.md`, `../y.md`, resolved
against the referring file) and reference-style definitions
(`[x]: /y.md`) to the export's withholding function, without changing what
`merge`/`forget` match. Then the leak check scans every
staged byte for every withheld id's path forms. The filters are the
mechanism; the leak check is the proof, and it refuses rather than warns,
because an export cannot be recalled once shared. A code-fenced mention of a
withheld path therefore refuses the export: the user moves or edits that
text, which is rare and visible, and is preferred to a silent leak.

### D4. Keep §4.1 extensions; strip only what is machine-local or engine-internal

OKF §4.1 asks consumers to preserve unknown keys, and a future OpenKOS
import of its own export needs them (`relations`, `provenance`,
`sensitivity`, `freshness`, `generated`, `version`, `status_derived_from`).
Two keys are removed:

- `origin_key` is a 128-bit digest of the resolved absolute path of the
  ingested file (`okf.origin_key_for`) — meaningless on another machine by
  its own docstring, and a dictionary-attackable fingerprint of the local
  username and directory layout.
- `merged_from` is the pre-ADR-0013 in-frontmatter merge ledger, holding
  absorbed bodies that may have been written at a higher sensitivity than
  the survivor now carries (the reason `sensitivity.merged_content_blocked`
  exists). `repair` moves it to the `.state/ledger/` sidecar when it runs;
  export strips it whether or not `repair` has run.

A document with `ingest_pending` is withheld rather than stripped: its
compilation is incomplete. `source_frontmatter` (a Source's own incoming
frontmatter) is kept: it is the user's content, governed by the Source's
label like the body.

### D5. Byte-stable output

A document whose transformation is the identity is copied byte-for-byte
(same rule as `okf.apply_deprecation_export`, which returns the input text
object for UNCHANGED). A rewritten document is emitted with
`okf.dump_frontmatter`, whose key order is already deterministic. Files are
written in sorted path order; the only date in the output is the `log.md`
entry heading. So two exports of an unchanged workspace on the same day are
byte-identical — the property that lets someone diff exports.

### D6. Read-only, guarded by re-reading, not by the lock

The workspace-lock spec has no shared reader lock, and export writes
nothing inside the workspace, so it is read-only (`_READ_ONLY_COMMANDS`).
Consistency of the snapshot is guarded the way mutating verbs guard their
plan: every input's bytes are kept from the walk and re-read after staging;
any difference refuses with exit 3, the documented retry-safe refusal. A
torn snapshot can therefore never be published, and a daemon running in
the background is never blocked by an export.

### D7. Stage beside the target, publish by rename

Staging lives in the target's parent (`tempfile.mkdtemp`, dot-prefixed) so
the publish is a same-filesystem rename; an empty target directory is
removed just before the rename. A refused export removes its staging
directory; only a killed process can leave one behind, dot-prefixed and
never at the target. The target itself is either absent or complete. A target inside the workspace is refused because writing
there would put non-concept or duplicate concept files inside `bundle/` or
next to `raw/` (AGENTS.md: never put non-concept files inside `bundle/`).

### D8. Deprecated status is projected, never repaired

Export computes the superseded set over the WHOLE bundle (withheld
superseders included — a concept superseded by a confidential decision is
still deprecated, and saying so leaks no id) and applies
`okf.apply_deprecation_export` in memory. An incomplete walk never
withdraws (existing rule). Drift is reported with `openkos repair` as the
fix; export never writes it back (D6).

## Sequence: publish with the three gates

```
CLI            export_service            okf / links / sensitivity        filesystem
 |  plan()  ----> walk + snapshot ---------> exportable_concept_ids
 |               transform each doc -------> export_frontmatter / apply_deprecation_export / withhold_links
 |  <---------- ExportPlan
 |  preview + confirm
 |  publish() -> write staging --------------------------------------------> .<name>.openkos-export-<pid>/
 |               check_conformance(staging) -----> violations? -- yes --> rm staging, exit 1
 |               leak_check(staging)               hits?       -- yes --> rm staging, exit 1
 |               re-read inputs                    changed?    -- yes --> rm staging, exit 3
 |               os.replace(staging, target) -----------------------------> target/
 |  <---------- summary
```

## ADR gate

Evaluated against both conditions. The boundary thresholds come from the
KOM and ADR-0028 already; they are not a new decision. The owner's answers
to Q1 (link labels into withheld objects become `[withheld]`, a departure
from ADR-0028's "prose is not redacted" at this boundary) and Q2 (own label,
but below-source objects withheld unless `--allow-below-source`) are both
hard to reverse once exports have been shared, so they are recorded as
ADR-0048, status `Proposed`.

## Testing strategy

Strict TDD (`openspec/config.yaml`), runner `uv run pytest`. Unit tests per
pure function (predicate, frontmatter transform, link withholding, index
filter, export log); service tests in `tmp_path` workspaces; an e2e over a
copy of `examples/good-life-demo` asserting the exact output tree, §11
conformance, the absence of every withheld id, determinism, and an
unchanged workspace `git status`. A canary test mirrors ADR-0028's
enumeration guard: a confidential canary id, title and body marker appear
in no output byte under every flag combination. No test reaches an LLM.
