# Proposal: okf-import — adopt a foreign OKF bundle, as written, under its own namespace

MVP 5 (Interoperability), second deliverable, per `docs/roadmap.md`. The
sibling `okf-export` (ADR-0048) shipped the first deliverable. Exploration is
in `exploration.md`. The verified OKF v0.2 facts are in Engram
`sdd/okf-import/research`, and the owner decisions are in
`sdd/okf-import/decisions`.

## Intent

OpenKOS can now write a shareable OKF bundle, but it cannot read one. A
person who gets a bundle from a colleague, from Google's reference agent, or
from their own earlier export has no way to bring it into their workspace.
Re-ingesting each file as a source loses the structure, ids and links. It
costs a model call per document, and nested directories collide in the flat
`raw/`. Copying files into `bundle/` by hand skips the guards: foreign
frontmatter is parsed by the unguarded readers, labels are missing (every
gate then hides the document, or a stray `public` leaks at export), and
foreign links can resolve to unrelated local documents.

`openkos import <dir>` adopts a foreign OKF bundle as written:

- the same text, structure and links, with no model call;
- under a namespace that cannot collide with local concepts;
- labelled fail-closed;
- anchored to an engine-written import Source that records where the
  documents came from.

Duplicates against local knowledge are then resolved with the tools that
already exist: duplicates, adjudicate, merge and curate Identity. OKF itself
says nothing about combining bundles (research: no merging, namespaces,
cross-bundle identity or sensitivity in v0.2). The collision, trust and
identity policy is therefore an OpenKOS decision, recorded in a new ADR.

## Decisions

### Resolved by the owner (2026-10-05). Do not reopen.

1. **Fidelity.** Foreign concepts are adopted as written, with no model
   call. An engine-written import Source is the provenance anchor. They are
   not re-compiled as sources.
2. **Collisions.** Everything always lands under `imports/<namespace>/`,
   and internal links are rewritten into the namespace. Imports are never
   mixed with local paths. Duplicates are resolved later through curate and
   merge.
3. **Unlabelled documents** get the workspace `default_sensitivity`.
   `--sensitivity <level>` may raise the floor for one import, and never
   lowers it.
4. **Re-import is out of v1.** Importing into a namespace that already
   exists is refused, with a reason.

### Orchestrator defaults (the owner may revise them)

- **Labels.** Foreign labels only raise the floor, and an unknown value
  fails closed to `confidential` (the ADR-0030 rule). The effective label is
  the high-water mark of `default_sensitivity`, `--sensitivity` and the
  folded foreign label. *Consequence:* a foreign `public` document lands
  `private` in a stock workspace. Lowering it again is a human
  `set-sensitivity`.
- **Foreign trust keys.** Foreign `provenance`, `sources`, `status`,
  `generated` and `verified` are kept verbatim under one inert extension key
  (lossless, legal under §4.1). They are never read as local trust,
  lifecycle or provenance.
- **Entity resolution.** Imported concepts are not attach-at-ingest targets
  (ADR-0045) and are not admitted to the automatic merge class (ADR-0049)
  until a human merges them. They stay visible to duplicates, adjudicate,
  merge and curate Identity, which is how a human reconciles them. Reading
  of this default: `accept-recommended` is a per-group human answer, so it
  stays available.
- **Input** is a local directory only: no zip, tar or URL.
- **Round trip.** Importing a workspace's own export back into it works,
  because the namespace keeps the two copies apart.
- **Unattended surfaces.** Neither the daemon nor MCP imports (ADR-0037).
  Import is a human-invoked verb only.
- **Hostile input is bounded:**
  - the guarded incoming-frontmatter parser runs on every file;
  - path traversal and symlinks are refused;
  - per-file, total-size and file-count caps apply;
  - case-fold and Unicode-normalization (NFC/NFD) collisions inside the
    foreign tree are refused.
- **ADR.** A new ADR records the import policy. The design picks the next
  free number in `docs/adr/` (0050 when this was written).
- **Commit disclosure.** Import joins the recovery-critical commit
  disclosure (with `forget`, `merge` and `curate`). Re-import is refused, so
  "revert this commit, then import again" is the documented undo.

### Left to design. Named here so they are decided, not missed.

- **The anchor Source's granularity and label.** One anchor that carries
  the import's maximum label would make every lower-labelled sibling
  below-source at export (ADR-0048), so they would be withheld unless the
  exporter passes `--allow-below-source`. The design must avoid that. Two
  ways to do it: one anchor per effective label, or an anchor that carries
  the minimum label.
- **The anchor's `resource`, path and content.** It must not record an
  absolute local path, because that path would leak at export. The content
  covers the origin identity, the per-document foreign id and digest, the
  import time, the engine version, the foreign `okf_version` and the
  `generated.by`.
- **The inert key's name and shape.** Whether any new key is machine-local,
  and so must join `okf.EXPORT_STRIPPED_KEYS`.
- **The link rewrite.** It covers bundle-absolute inline links, relative
  links that escape the foreign root, reference-style links and `relations:`
  targets. No rewritten pointer may resolve outside `imports/<namespace>/`.
  Relative links inside the subtree stay valid as written.
- **Atomic publish and torn runs.** Refusing an existing namespace must
  never strand a half-written one. The design chooses between
  stage-then-rename and a pending marker in the style of `ingest_pending`.
- **What is skipped and reported.** Non-`.md` files (OKF bundles may hold
  `viz.html` and `references/`), dot-directories, and the foreign
  `index.md`/`log.md`. The foreign root `index.md` is read for
  `okf_version`. Every skipped file is reported in the preview. `bundle/`
  stays pure OKF.
- **Version handling.** Best-effort reading of an unknown `okf_version`
  (§12), and optional reuse of `migrate_document` for v0.1 documents.
- **Derived caches.** Import makes no model call, so FTS and embeddings
  catch up through the existing derived-index staleness path or `reindex`.
- **The namespace argument.** Its validation (one slug segment) and its
  default, if any.
- **Survivor choice.** When a human merges a local concept with an imported
  one, which side survives, and therefore whether the result is still
  "imported".

## Scope

### In Scope

- A locked verb `openkos import <dir>` with `--namespace`,
  `--sensitivity` (raise-only) and `--auto`. It follows the preview,
  confirm and exit-code conventions of `docs/cli.md` and ADR-0042.
  - Phase A runs without the lock: a bounded read, then plan and preview.
  - Phase B runs under the lock (ADR-0036): re-read, re-validate, write, and
    one autocommit.
- A bounded foreign-bundle reader in the OKF seam (`model/okf.py`). It
  parses every file with the guarded parser, never `frontmatter.loads`, and
  refuses hostile trees with a named reason.
- A foreign-to-adopted transform in the OKF seam:
  - the label is folded and stamped;
  - foreign trust keys move under the inert key;
  - local `provenance` points at the anchor, and `sources` is re-projected
    from it (§5.1);
  - the Concept ID becomes `imports/<namespace>/<foreign id>`;
  - foreign `type` values are kept, including unknown ones (§11).
- A namespace link rewrite in `bundle/links.py` over every link form.
- The engine-written import anchor Source or Sources, one `index.md` update
  and one `**Import**` entry in `log.md`.
- A refusal, with a reason, when the namespace already exists.
- Exclusion of imported concepts from attach-at-ingest targets and from the
  automatic merge class.
- The command classified as locked, and the import commit added to the
  per-verb autocommit list and the recovery-critical disclosure.
- The import-policy ADR, a `docs/cli.md` entry, and the shape-level doc
  updates (`docs/okf-alignment.md`, `docs/roadmap.md`,
  `docs/knowledge-object-model.md`'s import boundary).

### Out of Scope

- Re-importing, or updating an existing namespace (owner decision 4).
- Re-compiling foreign documents through the model, or import-time entity
  resolution.
- Archive or remote input: zip, tar or URL.
- Import from the daemon, the pending-work queue, the inbox watch or MCP.
- A new pending-queue kind. `state/pending_queue.py` and its CHECK
  constraint are untouched.
- Copying foreign non-concept files into the workspace (into `raw/` or
  `bundle/`).
- Interpreting foreign `verified` attestations, trust tiers or
  `status: deprecated` as local facts.
- A stable public Python API (MVP 6, ADR-0039). Import is a narrow
  `application/` service (ADR-0018).

## Capabilities

### New Capabilities

- `okf-import`, which covers:
  - the `openkos import` surface and its exit codes;
  - the bounded foreign reader and its refusals;
  - the namespace placement and the existing-namespace refusal;
  - the link rewrite invariant (nothing resolves outside the namespace);
  - label folding (the floor, raise-only, fail-closed);
  - preservation under the inert key;
  - the anchor provenance and the re-projected `sources`;
  - skipped-file reporting;
  - the Phase A/B split with its drift guard;
  - atomic publish;
  - the model-free guarantee.

### Modified Capabilities

- `workspace-lock`: "Every Command Is Classified" names `openkos import` as
  locked, with a commit phase.
- `workspace-autocommit`: "Post-Phase-B Commit Per Mutating Verb" adds
  `import` and its message format. "Commit Disclosure For The
  Recovery-Critical Verbs" adds `import` (an orchestrator default).
- `ingestion`: "An Extracted Candidate Attaches To An Existing Same-Type,
  Same-Key Concept" excludes imported concepts from the target set.
- `identity-auto-merge`: "The Structural Class Predicate" refuses a group
  with an imported member.
- `okf-export`: no delta is expected. The export boundary is unchanged, and
  imported concepts pass through it as ordinary concepts. A delta is added
  only if the design introduces a machine-local key that must join the strip
  list.
- `type-sensitivity-defaults`: this delta depends on open question 1. If
  per-type offsets apply to imported concepts, import becomes another birth
  seam that consults them.

## Approach

Option C from the exploration: deterministic adoption under a mandatory
namespace, plus an engine-written anchor Source. The boundary is built
before the verb.

1. **The reader** decides what may enter. It keeps all format knowledge in
   the OKF seam, as AGENTS.md requires.
2. **The transform** is a set of pure functions over a parsed document. It
   handles label, provenance, inert key and Concept ID, while the link
   rewrite lives in `bundle/links.py`.
3. **The service** (`application/import_service.py`) walks once, plans,
   previews, and then publishes under the lock with a drift guard, atomic
   placement and one commit.
4. **The entity-resolution exclusion** lands before the verb is reachable.
   No released build should let an imported concept become an attach target
   or an auto-merge candidate.
5. **The CLI verb** comes with an end-to-end round trip through `export`.
6. **Docs** come last.

The canonical layer never depends on the derived layer. Import writes only
canonical files. Derived stores catch up through their existing
reconstruction path.

## Slice plan (auto-chain, stacked to main, about 400 authored lines each)

| # | Slice | Est. lines | Done when |
|---|---|---|---|
| 0 | This planning PR: proposal, spec deltas, design, tasks, ADR (Proposed) | ~120 + artifacts | merged; ADR indexed |
| 1 | Bounded foreign reader in `model/okf.py`: guarded parse, traversal, symlink, size, count, case and NFD refusals, `okf_version` read, skip list | ~350 | each hostile shape refused with its own named reason (one test per guard) |
| 2 | Transform: label fold and stamp, inert key, anchor provenance, `sources` re-projection, namespaced Concept ID; link rewrite in `bundle/links.py` | ~400 | property: no rewritten pointer resolves outside the namespace |
| 3 | Service: plan, preview model, drift guard, atomic publish or torn-run handling, anchor, index and log, existing-namespace refusal | ~400 | service tests over fixtures; a refused run leaves the tree unchanged |
| 4 | Entity-resolution exclusion: attach target set and auto-merge class predicate | ~250 | an imported concept is never attached to and never auto-merged |
| 5 | CLI verb, lock classification, autocommit and disclosure, e2e round trip | ~350 | e2e green under a poisoned `OLLAMA_HOST` |
| 6 | Docs: `docs/cli.md`, `docs/okf-alignment.md`, `docs/roadmap.md`, KOM | ~80 | shape described, with no counts or "since" markers |

Slices 1 to 4 add no reachable surface, so each one merges safely on its
own. The user-visible verb appears only in slice 5, after the exclusion
(slice 4) is on `main`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/model/okf.py` | Modified | Bounded foreign reader and foreign-to-adopted transform, both in the seam; reuses `parse_incoming_frontmatter`, `combine_sensitivity`, `refresh_sources` and optionally `migrate_document` |
| `src/openkos/bundle/links.py` | Modified | Namespace rewrite over inline absolute, inline relative and reference-style links |
| `src/openkos/bundle/index.py`, `bundle/log.py` | Modified | Index entries for imported concepts and one `**Import**` log entry |
| `src/openkos/application/import_service.py` | New | Narrow use-case service: plan, preview, locked publish, commit |
| `src/openkos/application/ingest.py` | Modified | Attach target lookup excludes imported concepts |
| `src/openkos/application/auto_merge.py` | Modified | Class predicate refuses imported members |
| `src/openkos/sensitivity.py` | Possibly modified | Label stamping helper, if the design puts it here |
| `src/openkos/cli/main.py` | Modified | `import` verb, lock classification, preview, commit disclosure |
| `tests/unit/` (incl. `unit/e2e/`) | New | Hostile fixtures, transform properties, service, e2e round trip |
| `docs/adr/00NN-*.md`, `docs/adr/README.md` | New | Import-policy ADR |
| `docs/cli.md`, `docs/okf-alignment.md`, `docs/roadmap.md`, `docs/knowledge-object-model.md` | Modified | Shape-level docs |

## Principles Impact

- **Adopt OKF.** The input is any OKF bundle (v0.2, or v0.1 at best
  effort). Concept IDs stay "path minus `.md`" and links stay untyped. Every
  added key is a §4.1 extension. Import never rejects a bundle for what §11
  says consumers must tolerate.
- **Local-first.** Import reads a local directory only, makes no network
  call and no model call.
- **`raw/` outside the bundle; `bundle/` pure OKF.** Only concept documents
  enter `bundle/`. Non-concept foreign files are skipped and reported.
- **Immutable sources, living objects.** Adopted documents are living
  objects from the moment they are imported. Their original state is kept in
  the import commit and in the inert key.
- **Provenance first-class.** Every adopted document cites the anchor, and
  the anchor records its origin and digest.
- **Sensitivity.** Every document is labelled. Labels only raise, and
  unknown labels fail closed.
- **Human curates.** Import is human-invoked behind a preview and a confirm
  gate. Cross-bundle duplicates are merged only by a human.
- **Reconstructible.** No derived store is the source of truth. Import
  writes canonical files only.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| An unguarded reader (`frontmatter.loads`, `_iter_docs`) touches foreign input | Med | The reader is the only entry. A test asserts the import path never calls `_parse_post`. |
| A missed link form makes a foreign link resolve to an unrelated local document | Med | Every pointer is rewritten into the namespace, plus a post-transform scan that refuses the import if any pointer resolves outside the namespace |
| A single max-label anchor makes lower-labelled siblings below-source at export (ADR-0048) | High if ignored | A design-level requirement, plus an e2e that exports after import |
| A half-written namespace plus the existing-namespace refusal leaves the user stuck | Med | Atomic publish or a torn-run marker (design) |
| A new key leaks a local path through export | Low | No absolute path in any adopted or anchor field; strip-list review in design |
| Foreign `Source`-typed documents (non-`raw/` `resource`) trip engine code that assumes a raw-backed Source (lint, forget, purge, doctor) | Med | The design audits these consumers. The e2e runs `lint` and `status` after import. |
| Prompt injection in foreign bodies reaches later model calls (curate judges, query) | Med | Same exposure as ingested text. Sensitivity gates still apply. Disclosed in the ADR. |
| Large imports exceed the 50-group candidate cap and resolve slowly | Med | Accepted for v1 and disclosed. Resolution stays human-paced. |
| No real producer fixture in the repo | Med | Fixtures built from our own export plus one hand-written v0.2 bundle in the shape of the public samples, with an attribution note if adapted from Apache-2.0 samples |

## Rollback Plan

- **Code.** Each slice is an independent PR. Reverting slices 5 and 6
  removes the verb. Reverting slices 1 to 4 removes dormant code. Import adds
  no derived-store schema, no pending-queue kind and no config key, so no
  data migration is needed.
- **Data.** One import is exactly one commit that adds
  `imports/<namespace>/**`, the anchor or anchors, and `index.md`/`log.md`
  lines. While it is the latest commit, `git revert <sha>` removes it whole,
  and the commit disclosure says so. After later commits, the namespace can
  be removed with `forget`, applied per concept. Derived caches rebuild with
  `reindex`.
- **Policy.** The ADR stays `Proposed` until the change merges. If the
  policy is rejected before acceptance, it is withdrawn with the change.

## Dependencies

All of these are already shipped:

- `okf.parse_incoming_frontmatter`, `combine_sensitivity`,
  `refresh_sources`/`project_sources`, `check_conformance` and
  `migrate_document`;
- `bundle/links.py`'s link scanner;
- the workspace lock (ADR-0036) and `_autocommit`;
- `ATTACH_EXCLUDED_TYPES` and the auto-merge class predicate;
- `openkos export`, for the round-trip fixture.

There are no new third-party dependencies.

## Success Criteria

- [ ] Exporting `examples/good-life-demo` with `--include-private` and
      importing the result into a fresh workspace under `--namespace demo`
      yields every exported concept under `bundle/imports/demo/`. The result
      passes `okf.check_conformance`, and `lint` and `status` run clean of
      import-caused errors.
- [ ] The same export imported back into its origin workspace succeeds. No
      local concept file changes, and only `index.md` and `log.md` gain
      lines.
- [ ] Every link and `relations:` target in an imported document resolves
      inside `imports/<namespace>/`. Every local `provenance` id names an
      import anchor. A scan proves this, and the whole import is refused
      otherwise.
- [ ] Every imported document carries a recognized label of at least
      `default_sensitivity`. A foreign unknown label yields `confidential`,
      and `--sensitivity` never lowers a label.
- [ ] Foreign `provenance`, `sources`, `status`, `generated` and `verified`
      survive verbatim under the inert key. No local consumer reads them.
- [ ] The full import, end to end, runs under a poisoned `OLLAMA_HOST` with
      zero model calls.
- [ ] A second import into an existing namespace is refused with a named
      reason, and writes nothing.
- [ ] Each hostile fixture is refused with its own named reason, and the
      workspace is unchanged: traversal, symlink, oversize file, oversize
      total, too many files, alias bomb, case collision, NFD collision and a
      non-mapping root.
- [ ] An imported concept is never chosen as an attach target, and a group
      with an imported member is never admitted to `curate --auto-merge`.
- [ ] Exporting a workspace that contains a mixed-label import withholds no
      imported document merely because of its anchor's label.
- [ ] One import makes exactly one commit. While it is the latest commit,
      `git revert` restores the pre-import tree byte for byte.

## Open questions for the orchestrator (product)

1. **Do per-type sensitivity offsets (`type_sensitivity_defaults`) floor
   imported concepts?** Today they apply only at the `build_concept` birth
   seams, which adoption does not use. Without them, an imported `Person`
   lands at the workspace default while a locally compiled one is raised.
   Recommendation: yes. It is raise-only, consistent with the other label
   defaults, and adds one modified capability. Until it is answered, the
   `type-sensitivity-defaults` delta is paused. Everything else proceeds.
