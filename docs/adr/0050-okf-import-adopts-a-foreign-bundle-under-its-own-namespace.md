---
type: Decision
title: "ADR-0050: OKF import adopts a foreign bundle under its own namespace, labelled fail-closed, anchored per label, and outside automatic identity"
description: OKF import adopts a foreign bundle as written under one required namespace, labels every concept fail-closed at or above the workspace floor, anchors each effective label with one engine-written Source, keeps every foreign trust key inert, excludes imported concepts from automatic identity, and reads untrusted trees through a bounded reader that refuses hostile input with a named reason.
status: Accepted
date: 2026-10-05
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-05T00:00:00Z
sensitivity: public
---

# ADR-0050: OKF import adopts a foreign bundle under its own namespace, labelled fail-closed, anchored per label, and outside automatic identity

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

MVP 5 (Interoperability) ships OKF export first and import second. Export is a
projection of what the engine already trusts. Import is the opposite: it takes
a directory written by someone else, with no guarantee about its shape, its
names, its frontmatter or its intent, and writes it into a bundle whose every
reader assumes engine-written content.

That assumption is load-bearing in many places at once. Concept IDs are file
paths, so a foreign path can collide with a local concept. Links are
bundle-relative, so a foreign link can resolve to an unrelated local document.
The engine reads frontmatter keys as facts (`sensitivity`, `provenance`,
`sources`, `status`, `generated`, `verified`, `resource`, `relations`), so a
foreign value is trusted by default unless something stops it. The export
boundary (ADR-0048) withholds an object labelled below its provenance
ancestors, so an import that cites one source for everything either leaks or
withholds. Attach-at-ingest (ADR-0045) and the structural merge class
(ADR-0049) act without a human answer, so a foreign `-N` sibling a producer
chose would be merged on the engine's authority. YAML aliases and unbounded
trees are an amplification and exhaustion vector that ADR-0030 already
identified for a single ingested frontmatter block.

OKF itself decides what a consumer must tolerate (§11, §6.1, §12), and what it
does not decide, including what a consumer should trust, is left to the
engine. The decision is hard to reverse once namespaces, anchors and the
`imported` key exist in users' bundles and in their exports.

## Decision

1. **Fidelity.** A foreign concept is adopted as written. Import makes no model
   call and does no re-compilation, so it is deterministic and offline.
   Files that are not concept documents (non-`.md` files, dot-entries, the
   foreign `index.md` and `log.md`) are skipped and reported, never copied.
2. **Collision.** Everything lands under `imports/<namespace>/` inside
   `bundle/`. The namespace is required, has no default, and is an ASCII slug
   (lowercase letters, digits, single hyphens, at most 64 characters) so it has
   no case or normalization variants. An existing namespace is refused. There
   is no re-import: reverting the import commit and importing again is the
   undo.
3. **Links.** Every pointer site (inline, image, definition) is rewritten into
   the namespace on the delimiter rather than on any one recognizer's regex;
   relative links that escape the foreign root are clamped per RFC 3986
   section 5.2.4; code is rewritten too. A post-transform proof re-scans every
   pointer under every reading an engine recognizer can take, and refuses the
   whole import if any reading resolves outside the namespace.
4. **Sensitivity.** The effective label is the high-water mark of the
   workspace default, the raise-only `--sensitivity` flag, the folded foreign
   label (absent contributes nothing, an unknown or non-string value is
   `confidential`) and the configured per-type birth offset. It is never below
   `default_sensitivity`.
5. **Trust.** Every foreign key the engine reads is moved, verbatim, under one
   inert `imported` key. Machine-local keys (`origin_key`, `merged_from`) are
   dropped and counted. Relations of an engine-owned type are inert. A
   `resource` that names a path rather than a URL is inert, because it would
   otherwise name a local raw file. Nothing reads `imported` as a local fact.
6. **Provenance.** One engine-written anchor Source is written per effective
   label. Each document cites exactly the anchor of its own label, and an
   anchor names only documents at its label, so no import is below-source by
   construction and an anchor never discloses a higher-labelled title. No local
   path or directory name is recorded anywhere.
7. **Identity.** An imported concept (a Concept ID whose first segment is
   `imports`) is never an attach target, never in the structural automatic
   merge class and never in the accept-recommended batch, because it lies
   outside the population ADR-0049 measured. Humans reconcile imported
   duplicates per group, and in an ordered merge the local concept survives an
   imported one.
8. **Hostile input.** A bounded reader refuses tree and frontmatter hazards
   with a named reason and writes nothing. Its caps are code constants, not
   settings, in `model/okf.py`: `FOREIGN_MAX_FILE_BYTES` (8 MiB),
   `FOREIGN_MAX_TOTAL_BYTES` (256 MiB), `FOREIGN_MAX_DOCUMENTS` (10 000),
   `FOREIGN_MAX_ENTRIES` (50 000), `FOREIGN_MAX_DEPTH` (32),
   `FOREIGN_MAX_SEGMENT_BYTES` (255) and `FOREIGN_MAX_PATH_BYTES` (1024, for the
   namespaced path). Every frontmatter block goes through the guarded
   incoming-frontmatter parser of ADR-0030, never an unguarded loader. A
   document that is merely not conformant (unparseable YAML, no frontmatter, no
   `type`) is skipped and reported instead of refusing the import.
9. **Human-only.** Import is a human-invoked verb. No daemon, queue, inbox
   watch or MCP path reaches it (ADR-0037).

## Consequences

- Prompt injection inside foreign bodies reaches later model calls with the
  same exposure as ingested text; import does not sanitize prose.
- A large import meets the 50-group candidate cap in identity review and
  resolves at human pace, because imported groups never merge automatically.
- A stock `private` workspace raises a foreign `public` document to `private`.
  Lowering it is a deliberate `set-sensitivity`, and exporting a downgraded
  imported document needs `--allow-below-source` (ADR-0048), the friction that
  rule puts on every downgrade.
- Raising an imported document above its anchor leaves its title in a
  lower-labelled anchor body, the exposure `index.md` already has for any
  relabelled concept; export replaces such a link with `[withheld]`.
- The reader rejects whole trees for properties it cannot prove safe (links,
  special files, names, collisions). A bundle that trips one is fixed at its
  source and imported again.
- The bounded reader is dormant until the import verb lands; it adds no user
  surface of its own.
- Derived indexes are not refreshed by import (the embedding half needs a
  model); `reindex` or the staleness path catches up.

## Alternatives considered

- **Ingest foreign documents as sources.** Rejected: it re-compiles them
  through a model, so fidelity, determinism and the offline guarantee are lost
  for a bundle that is already structured.
- **Keep foreign IDs and refuse collisions.** Rejected: import would succeed or
  fail depending on the local bundle's contents.
- **Prefix only on collision.** Rejected: whether a document is namespaced
  would then depend on the local bundle at import time.
- **Trust foreign labels.** Rejected: a foreign `public` would lower the
  workspace floor, and an unlabelled document would fall back to the least
  restrictive reading.
- **One anchor at the maximum label.** Rejected: every lower-labelled document
  becomes below-source, the ADR-0048 trap.
- **One anchor at the minimum label.** Rejected: an anchor that records
  per-document facts would carry titles and ids of higher-labelled documents in
  a lower-labelled engine-written file.
- **No anchor.** Rejected: provenance is first-class, and a document with no
  cited source breaks the export boundary's ancestor walk.
- **A frontmatter or provenance-based imported predicate.** Rejected: the merge
  union gap-fills unknown keys and provenance into a local survivor, so the
  survivor would inherit "imported"; the Concept ID prefix is a property of
  identity that frontmatter cannot fake.
- **A pending marker instead of rename-last.** Rejected: it adds a third state
  to every anchor reader and still does not make the namespace directory
  atomic; publishing the directory by one final rename does.
