# Exploration: okf-import (MVP 5, second half)

Companion research (external, verified against the raw spec text) is in Engram
`sdd/okf-import/research`.

## Current state

- **OKF v0.2 is silent on combining bundles.** Verified against the raw text:
  no merging, namespaces, cross-bundle identity or sensitivity labels. It
  requires only `type` (§11) and tolerates broken links (§6.1 "MUST tolerate")
  and unknown keys. Each `sources[]` entry needs `resource` (§5.1), and `id`
  is optional, so foreign sources are usually URLs, not concept ids. A
  consumer SHOULD attempt best-effort consumption of an unknown
  `okf_version` (§12). The collision and identity policy is ours, and
  warrants an ADR.
- **The incoming-frontmatter path is for single source files, not bundles.**
  `okf.parse_incoming_frontmatter` (`src/openkos/model/okf.py:943`):
  - caps the block at 64KiB (:846) and the depth at 32 (:851);
  - rejects anchors and aliases (:893);
  - requires plain data that survives a round trip (:981-992);
  - never raises.

  It feeds ingest's `source_frontmatter` (:840) and the closed allow-list
  `lift_incoming_frontmatter` (:1089), which lifts tags and sensitivity only.
  ADR-0030 is the precedent: engine-owned keys (`type`, `status`, `provenance`,
  `sources`, `version`, `generated`, `verified`) are never lifted (:42), and
  sensitivity only raises, with unknown values failing closed to confidential
  (:40).
- **The bundle readers are unguarded.** `_iter_docs` (:3822),
  `check_conformance` (:4001), `survey_bundle` (:3897) and `concept_metadata`
  (:708) all use `frontmatter.loads` (`_parse_post` :678), which has no size
  cap, no alias ban, and TOML/JSON fence auto-detection. That is fine for our
  own bundle and unsafe for a foreign one, so import needs the guarded parser
  per file.
- **Everything downstream is single-bundle.** Candidate finding
  (`resolution/candidates.py:219`, :288, :501), merge
  (`application/merge_service.py:217`) and the attach lookup
  (`application/ingest.py:153`) each take one `bundle_dir`. Once foreign
  concepts are adopted into the bundle, cross-bundle resolution becomes
  ordinary within-bundle resolution.
- **Export is the closest sibling.** It is read-only (`_READ_ONLY_COMMANDS`,
  `cli/main.py:354`) and strips only `origin_key` and `merged_from`
  (`okf.py:2612`). It keeps provenance, and a Source's `resource: raw/<x>`
  dangles in the recipient.

## Affected areas

- `model/okf.py`: all foreign-format knowledge, meaning a bounded foreign
  reader and the foreign-to-adopted transform. `migrate_document` (:3337) can
  optionally be reused for v0.1 documents.
- `bundle/links.py`: the namespace rewrite must cover inline absolute, inline
  relative and reference-style links. `withhold_links` (:348) covers all three
  but only for withholding; `find_inbound_link_rewrites` (:92) does not.
- `application/import_service.py` (new): a narrow service (ADR-0018).
- `cli/main.py`: a locked verb with `commit_phase=True`, like merge (:8918). It
  must be classified, which MODIFIES the workspace-lock requirement "Every
  Command Is Classified".
- `sensitivity.py`: label stamping. `export_boundary` (:469) interacts with
  the anchor through the below-source rule.
- `okf.EXPORT_STRIPPED_KEYS` (:2612): any new machine-local key must be
  stripped on export.
- `state/pending_queue.py:59,80`: a new queue kind would change a SQL CHECK
  constraint on a derived store.
- Docs: `docs/cli.md`, `docs/okf-alignment.md:66`, `docs/roadmap.md:200-205`,
  `docs/knowledge-object-model.md:328`. Specs: a new `okf-import` spec and an
  ADR.

## Options

- **A. Ingest the bundle as sources.**
  - Keeps raw immutability, the ADR-0030 lift, ADR-0045 attach and ADR-0041
    versioning.
  - Loses structure and costs model calls.
  - `raw/` is flat and basename-only (`okf.py:217`), and directory ingest is
    non-recursive, so nested foreign directories collide.
- **B. Adopt as concepts.**
  - Keeps structure, ids and bodies.
  - Needs a collision policy, rewrites of links, relations and provenance, and
    a trust mapping for foreign keys.
- **C. B, plus an engine-written import Source as the provenance anchor**,
  with foreign frontmatter kept verbatim under one inert key.

Collision choices: always namespace under `imports/<ns>/`, keep ids and refuse
collisions, or prefix only on collision.

## Entity resolution

- **Available:** the HIGH/ACRONYM/LOW tiers (capped at 50 groups,
  `candidates.py:150`), adjudication, merge with ledger and unmerge, ADR-0049
  auto-merge, and ADR-0045 attach (Event and Person excluded).
- **Missing:** a marker for which side is foreign, and cost under the 50-group
  cap for large imports.
- **Side effect:** adopted concepts become attach targets and auto-merge
  candidates.

## Sensitivity

- **An absent label** ranks as private in `combine_sensitivity`
  (`okf.py:1664`), but every gate fails closed on absent:
  - `blocks_llm_send` (`sensitivity.py:112`);
  - `blocks_disclosure` (:299);
  - export's UNLABELLED reason.

  So the importer must stamp a label.
- **A single anchor Source** holding the maximum label would make
  lower-labelled siblings below-source at export (ADR-0048), so the anchor's
  granularity matters.

## Provenance and freshness

- **Record:**
  - the origin bundle's identity;
  - the foreign concept id and content digest;
  - the import time and engine version;
  - the foreign `okf_version` and `generated.by`;
  - the foreign `provenance`, `sources` and `verified`, kept inert.
- **Local provenance** points at the engine anchor, and `sources` is
  re-projected (`refresh_sources`, :1201).
- **Re-import (the ADR-0041 pattern):** the same digest is a no-op; a changed
  document needs a policy.

## Shape

- **Verb:** `openkos import <dir>` (with `--namespace` and `--auto`), for
  humans only. The daemon never imports (ADR-0037).
- **Phase A** runs with no lock: bounded read, plan, preview and confirm.
- **Phase B** runs under the lock: re-read, re-validate, write, and one
  autocommit.
- **Torn runs:** a marker like `ingest_pending` (`okf.py:196`).

## Phase split (advisory, about 400 authored lines each)

| Phase | Scope | Est. lines |
|---|---|---|
| 0 | Decisions, ADR, proposal, spec deltas | ~120 |
| 1 | Foreign-bundle reader in the OKF seam | ~350 |
| 2 | Foreign-to-adopted transform | ~400 |
| 3a | Service: plan, commit, drift guard, autocommit, torn-run marker | ~400 |
| 3b | CLI, lock classification, preview, e2e round trip | ~350 |
| 4 | Entity-resolution audit: attach and auto-merge exclusion | ~250 |
| 5 | Re-import policy (deferrable) | ~300 |
| 6 | Docs | ~80 |

## Recommendation

Option C: adopt deterministically under a mandatory namespace, with an
engine-written import Source as the provenance anchor and every foreign label
folded fail-closed. Ship no import-time entity resolution, because the
existing duplicates, adjudicate, merge and curate Identity work inside the
bundle. The first deliverable is phases 0-3b plus 6, with no re-import.

## Open product questions

1. The collision policy.
2. The fidelity model: adopt, ingest as sources, or both.
3. The provenance anchor's granularity, and its `resource`.
4. The label for unlabelled foreign documents.
5. Whether foreign labels below the local floor are trusted.
6. Re-import in v1.
7. Whether adopted concepts are attach or auto-merge targets.
8. Whether foreign `provenance`, `sources`, `status` and `verified` are kept
   inert or dropped.
9. The input forms for v1.
10. A round trip into the same workspace.
11. Unattended or MCP import (recommended: no).

## Risks

- The unguarded readers must never touch foreign input.
- A missed link form silently resolves to a different local document.
- Unstamped documents are invisible to MCP and export, and a mis-stamped
  `public` document leaks through export.
- New import keys can leak through export unless they are added to the strip
  list.
- The 50-group cap makes large imports slow to resolve.
- No real producer fixture exists in the repo yet.
