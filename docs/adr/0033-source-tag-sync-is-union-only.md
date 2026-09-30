---
type: Decision
title: "ADR-0033: Source tag sync is union-only and never tags below the Source's sensitivity"
description: sync-tags adds a Source's current tags to its provenance descendants and never removes a tag, because without per-tag provenance a synced tag cannot be told apart from a hand-added one; a descendant classified below its Source is not tagged, and tag values stay out of log.md and commits.
status: Accepted
date: 2026-09-30
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-30T00:00:00Z
sensitivity: public
---

# ADR-0033: Source tag sync is union-only and never tags below the Source's sensitivity

- **Status:** Accepted
- **Date:** 2026-09-30

## Context

ADR-0030 made a Source's incoming `tags` flow to the derived concepts created
in the same `ingest` run, and deferred "a re-sync verb for tags" for
concepts that already exist. Issue #1093 asks for that verb, modeled on
ADR-0009's `set-sensitivity` propagation: explicit, human-invoked,
reviewable, never a side effect of re-ingest.

Three forces shape it:

- **No per-tag provenance.** A concept's `tags` is a flat list of strings.
  Once a Source tag is copied onto a concept, nothing records that it came
  from the Source rather than from a human. OKF v0.2 carries `tags` as a
  plain list, and inventing a provenance sidecar per tag would be a
  format extension with its own migration cost.
- **Human curates, engine maintains.** ADR-0030 already refused to overwrite
  a Source's tags on re-ingest because it would discard human curation. The
  same argument applies one level down.
- **Tags are text derived from the Source.** A tag such as `diagnosis` on a
  confidential Source is itself confidential information. Tags feed FTS, the
  embedding header, MCP disclosure of the concept, `log.md`, and git history.
  ADR-0003's high-water mark says derived information must not sit below the
  classification of what it came from.

The choice is hard to reverse: once a bundle has been synced, the added tags
are indistinguishable from hand-added ones, so no later release can
retroactively apply a different (for example, subtractive) rule to them.

## Decision

1. **We sync by union only.** For each descendant, the new `tags` is
   `okf.union_tags(existing, source_tags)` — existing tags first, unchanged,
   then each missing Source tag. `sync-tags` never removes, reorders, or
   rewrites a tag. A tag removed from the Source stays on every descendant
   that already carries it; removing it there is a human edit.
2. **We never tag a descendant classified below its Source.** A descendant
   whose `sensitivity` ranks strictly below the Source's (fail-closed
   ranking, ADR-0003) is skipped and named, with `set-sensitivity` or
   `backfill-sensitivity` as the way to raise it first. `sync-tags` itself
   never changes a sensitivity.
3. **Tag values never enter shared history the verb writes.** The `log.md`
   entry and the commit message name the Source and a count, never a tag.
4. **The write set is `find_provenance_descendants`' subset closure**, the
   same one ADR-0009 uses, minus Source-typed members. A descendant with a
   malformed `tags` value is skipped with a warning, never rewritten.

## Consequences

- Easier: every run is monotone and idempotent; running it twice, or on
  `--all` after a single-Source run, never loses anything and never needs
  review of a deletion. Human-added tags are safe by construction.
- Easier: the sensitivity boundary holds without `sync-tags` taking on any
  sensitivity write; the existing verbs stay the single place that raises.
- Harder: a tag the user deliberately removed from a Source lingers on its
  descendants until removed by hand. The preview never mentions it, because
  the verb cannot know which descendant tags came from the Source.
- Harder: a descendant below its Source's sensitivity stays untagged until
  its sensitivity is fixed — two commands instead of one. That is the same
  order `lint`'s below-source finding already recommends.
- Harder: multi-Source descendants are outside every single closure and are
  not tagged; a cited-union analogue of ADR-0016 is possible follow-up work.
- A later subtractive or mirror mode needs per-tag provenance first, and a
  new ADR superseding this one.

## Alternatives considered

- **Mirror the Source's tags exactly (remove what the Source no longer
  has).** Deletes hand-added tags, since they cannot be told apart. Rejected.
- **Remove only tags that the Source once had.** Needs a history of the
  Source's tags per descendant that does not exist; git archaeology per run
  is fragile and slow. Rejected.
- **Record per-tag provenance in a new frontmatter extension.** Would allow a
  safe subtractive mode later, but invents structure OKF does not have for
  one verb's benefit, and needs a migration for every bundle. Deferred.
- **Raise the descendant's sensitivity as part of the sync.** Folds a
  security decision into a tagging verb and bypasses `set-sensitivity`'s
  review. Rejected.
- **Copy tags regardless of sensitivity.** Leaks classified text into
  lower-classified concepts, FTS, and MCP output. Rejected.
- **Sync automatically on re-ingest.** Rejected by ADR-0009 and ADR-0030's
  reasoning: a whole-bundle walk on every ingest and an unasked-for
  multi-file write.
