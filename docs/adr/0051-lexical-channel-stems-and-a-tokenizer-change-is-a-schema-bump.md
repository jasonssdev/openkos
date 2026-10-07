---
type: Decision
title: "ADR-0051: The lexical channel stems with the porter tokenizer, and a tokenizer change is a schema bump that reaches existing workspaces"
description: The FTS5 lexical index tokenizes with `porter unicode61` so a singular question matches a plural concept, and the store's schema version, not only the bundle's manifest hash, decides whether an existing index is current.
status: Accepted
date: 2026-10-06
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-06T00:00:00Z
sensitivity: public
---

# ADR-0051: The lexical channel stems with the porter tokenizer, and a tokenizer change is a schema bump that reaches existing workspaces

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

`fts.db` tokenized with plain `unicode61`: case folding and diacritic
removal, no stemming. A question phrased in the singular (`a hook and a
skill`) therefore matched a concept titled `Hooks` only through the other,
shared words, while the plural phrasing (`hooks and skills`) matched it
directly. The same comparison question was refused or answered depending only
on its wording (#1333). The cause is deterministic and sits in the lexical
channel; the dense channel and the sufficiency check were not changed.

Two constraints shape the fix. Bundles hold Spanish as well as English, and
the porter stemmer is English-only. And a derived store is reconstructible,
but nothing rebuilt it when only the code changed: `refresh_fts_index` returned
`unchanged` whenever the bundle's manifest hash matched, and that hash is a
property of the documents, so bumping `SCHEMA_VERSION` alone would have left
every existing index on the old tokenizer until some document happened to
change.

## Decision

We tokenize the FTS5 index with `porter unicode61`: the stemmer wraps
`unicode61`, so case folding and diacritic folding are unchanged, and the
same stemmer runs over the indexed text and the query, so an exact form of
any word, in any language, still matches itself.

`SCHEMA_VERSION` of `fts.db` becomes `"2"`. A store recorded under another
version is not current: `refresh_fts_index` rebuilds it even when the bundle is
unchanged, and `stale_derived_stores` (read by `query`, `status` and `next`)
reports it stale so the existing `reindex` advice appears. Other stores are
compared on their manifest hash only unless a caller names an expected version.

## Consequences

- A singular and a plural question reach the same lexical candidates, in
  English and, because both forms end in a plain `-s`/`-es`, in Spanish plurals
  too (`fuente`/`fuentes`, `reunión`/`reuniones`).
- Stemming broadens matches, so BM25 ranks shift. Measured over the harness
  corpus the cited sets did not change; over the small good-life demo one
  title query moved its own concept from rank 1 to rank 2 behind a Source
  that mentions the same stem more often, still inside the top five.
- The English stemmer applies English rules to Spanish words. We expect
  occasional conflation of unrelated Spanish forms; none appeared among the
  ~100 forms checked, but that is a spot check, not a measurement of Spanish
  retrieval quality, which has no harness.
- Existing workspaces rebuild `fts.db` on their next `reindex` (a derived,
  reconstructible cache, so nothing is lost), and `query` says so until then.
- Any later tokenizer change follows the same rule: bump the version.

## Alternatives considered

- **Expand the query with singular and plural variants.** No schema change,
  but it multiplies the OR terms per question, covers only the inflections we
  enumerate, and leaves documents and queries tokenized differently.
- **Leave the lexical channel alone and rely on the dense channel.** Rejected:
  the reported refusal is exactly the case where the lexical channel failed to
  contribute the plural concept.
- **A language-aware stemmer.** No second stemmer ships in SQLite's FTS5, and
  adding a dependency or a custom tokenizer is out of proportion for this fix.
