---
type: Decision
title: "ADR-0041: A source edited after import imports as a new version, and its supersession is proposed, not written"
description: The inbox watcher imports a watched file whose bytes changed as a new raw copy under the next free versioned name with a Source of its own, instead of refusing it into the queue; the supersedes relation that retires the earlier Source is enqueued as a relation_type row a person confirms with `openkos relate`, because writing it deprecates a Source; the same proposal is offered when a replacement is ingested for a Source with no extractable text. Amends ADR-0038 Decisions Three and Four.
status: Proposed
date: 2026-10-01
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-01T00:00:00Z
sensitivity: public
---

# ADR-0041: A source edited after import imports as a new version, and its supersession is proposed, not written

- **Status:** Proposed
- **Date:** 2026-10-01
- **Amends:** [ADR-0038](0038-a-watched-folder-is-an-external-inbox.md) (Decisions Three and Four only)
- **Issues:** [#1212](https://github.com/jasonssdev/openkos/issues/1212), [#1224](https://github.com/jasonssdev/openkos/issues/1224)

## Context

ADR-0038 chose that a watched file whose bytes changed after import is never
re-imported: the watcher enqueues one `watch_refusal` row and the human renames
the file or ingests it under a new name. It deferred source versioning because
versioning "decides Source identity, supersession direction (ADR-0025 forbids
taking temporal direction from a model, and a file's mtime is a weak signal),
and what happens to derived objects of the old version".

Editing a note in place is the most common event a watched folder sees, so every
edit became queue work (#1212). A second report (#1224) found the same gap from
the other side: a Source that ended `no-extractable-text` stays in the bundle
beside its replacement, with nothing linking the two.

Everything those two need already exists. The typed relation `supersedes` is in
the vocabulary, and `relate A supersedes B` exports `status: deprecated` on B
(ADR-0032), withdraws it on `unrelate`, and resolves a queue row over the same
pair (ADR-0037). Raw collision naming already allocates `<stem>-N<ext>` when a
basename is held. What was missing was a decision about the three questions
ADR-0038 named, and about whether the watcher may write the relation.

## Decision

**One. A changed watched file imports as a new version.** When the origin of a
file (its `origin_key`) matches a raw copy but its bytes differ, the watcher's
ingest policy (`IngestPolicy.version_changed`) copies the new bytes to the next
free name in the raw collision family (`notes.md`, then `notes-2.md`,
`notes-3.md`) and writes a Source of its own. The earlier raw copy and Source are
never touched, so raw immutability holds without exception and history is kept in
`raw/`, the bundle and git. A person's `ingest` keeps the refusal: only the
unattended policy sets the flag.

The versions of one file share its `origin_key`. Matching a changed file against
them picks the version whose bytes are identical, else the newest. So re-saving a
version that was already imported, or restoring its bytes, is a converged no-op;
a genuinely new set of bytes is judged against the newest version.

**Two. Direction comes from import order.** The new Source is the one that
supersedes. That is neither a model's judgment nor a modification time: it is the
order in which the engine imported two byte-distinct states of one file, the one
direction that is known without inference. This resolves the concern ADR-0038
raised against versioning.

**Three. The unattended run proposes the supersession; it does not write it.**
Marking the earlier Source superseded writes a relation and deprecates that
Source, which removes it from default retrieval. ADR-0037 Two lists "relation
types" among the consequential proposals an unattended run enqueues and never
applies, and Decision One of that ADR forbids the runner from calling a write
core that applies a consequential change. So the import lands unattended
(non-consequential, as before) and the relation is enqueued as a `relation_type`
pending-work row, produced by `source-supersession/1`, whose payload carries
`suggested_type: supersedes`, the newer Source as the source and the older as the
target. `openkos pending` names the exact command, `openkos relate <new>
supersedes <old>`; running it (or `curate`'s relation stage) is the human-facing
write, and the existing `relate` resolution moves the row to `applied`. Until
then both Sources are live, which is the safe state: nothing is hidden before a
person agrees.

The row is event-driven, not computed. A complete advisor pass over
`relation_type` cannot tell it is absent, so `retire_unseen` skips the producers
listed in `EVENT_DRIVEN_PRODUCERS`; it goes stale only through the existing digest
check when either Source changes.

**Four. A dead Source is superseded by the same mechanism.** When an ingest
(manual or watched) copies a file to a disambiguated raw name because the basename
is held by a different source, and a Source in that family ended
`no-extractable-text` and is not already deprecated, the replacement may supersede
it, provided the replacement itself has extractable text. A manual `ingest` prints
the exact `relate` command on stderr; the watcher enqueues the same row. Neither
writes it.

**Five. No configuration switch.** The refusal path is kept as a defensive
branch for rows an older engine left open, but the watcher no longer produces
it. A switch to restore it was considered and rejected: the conservative
behaviour is strictly noisier, and the queue row that replaces it keeps the
human in control of the one consequential step.

## Consequences

**Easier.** An edited note is imported and searchable without any action, and the
earlier version stays on disk and in the bundle. The one decision left to a
person, retiring the earlier Source, is a single command the queue names, and
declining to run it costs nothing but a duplicate. Replacing a dead Source after
fixing its encoding or format has the same one-command ending.

**Harder.** Every edit now costs an extraction (one model call budget per
version), and an inbox note edited often leaves a chain of Sources until a person
confirms each link. Derived objects extracted from an earlier version are not
revisited: they keep citing the Source they came from, and `reconcile`,
`contradictions` and the identity advisors see the new version's objects beside
the old ones, as they would for any two overlapping sources. Restoring an earlier
version's bytes imports nothing, so the newest Source can describe bytes the file
no longer holds; saving anything new resumes the chain from the newest version.
Unrelating a supersession withdraws the deprecated status as ADR-0032 already
specifies.

**Accepted risk.** A file replaced by an unrelated file at the same inbox path is
imported as a version of the old one and offered as its supersession. The proposal
is only a queue row, so the human can decline it, and the earlier Source is never
changed without that confirmation.

## Alternatives considered

**Write the relation unattended.** Closes the loop with no human step. Rejected:
it deprecates a Source without consent, which ADR-0037 reserves for a
human-facing path, and an opt-in class with a measured safe threshold (the
ADR-0034 model) has no measurement to stand on here.

**Record `supersedes` in the new Source's frontmatter at import.** One write, no
queue row. Rejected for the same reason: it is the same relation write, and it
would deprecate the earlier Source at ingest time.

**Keep refusing (ADR-0038 as written).** Safe, but every edit becomes queue work
and the change cannot take effect until the human renames the file.

**Overwrite `raw/`.** Rejected, as in ADR-0038: it violates raw immutability.

**A configuration switch between the two behaviours.** Rejected (Decision Five).
