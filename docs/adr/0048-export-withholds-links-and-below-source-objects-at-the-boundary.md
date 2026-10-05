---
type: Decision
title: "ADR-0048: OKF export withholds link labels into withheld objects and objects labelled below their sources"
description: At the export boundary, a body link into a withheld object loses its target and its label, which becomes [withheld], and an object whose own sensitivity sits below its provenance high-water mark is withheld unless --allow-below-source is given.
status: Accepted
date: 2026-10-05
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-05T00:00:00Z
sensitivity: public
---

# ADR-0048: OKF export withholds link labels into withheld objects and objects labelled below their sources

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

`openkos export` (MVP 5, issue #1301, OpenSpec change `okf-export`) writes a
standalone OKF bundle for sharing. The knowledge object model already fixes
the thresholds: `public` leaves, `private` leaves only on an explicit choice,
`confidential` never leaves. Two questions the thresholds do not answer
become hard to reverse the moment an export has been shared, because a
shared bundle cannot be recalled.

The first is what happens to a body link from an exported object to a
withheld one. ADR-0028 settled the MCP boundary as "prose is not redacted":
a disclosable object's body is returned as written. At that boundary the
consumer is a local program on the same machine. An export is a file tree
meant to leave it. The engine writes link labels from the target's title —
`[Stoicism](/concepts/stoicism.md)` — and concept ids are title slugs, so a
verbatim body carries the withheld object's title twice.

The second is an object whose own label sits below the sensitivity of what
it was compiled from. The KOM's high-water-mark rule says a derived object
is at least as sensitive as its sources, and ADR-0008 lets a person lower a
label deliberately. Such an object exists either because a person chose to
lower it, or because a machine label went stale (`lint`'s
`below-source-sensitivity`). The label alone cannot tell the two apart.

## Decision

At the export boundary we remove a link into a withheld object, target and
label: the label becomes the fixed text `[withheld]`. Text outside links is
kept as written. This applies inline links in the bundle-relative and
relative forms and reference-style links; a mention inside a fenced code
block is caught by the export's byte scan, which refuses the whole export
rather than publishing it.

We decide exportability on an object's own label, and we additionally
withhold an object whose own label ranks below the highest label among its
provenance ancestors, unless the run is given `--allow-below-source`. The
preview lists every such object. An ancestor that cannot be read, or whose
label is missing or unrecognized, ranks as `confidential` for this test. A
provenance entry that names no document in the bundle contributes nothing.

## Consequences

Easier: an exported bundle carries no title or id of anything withheld
through a link, and the boundary needs no prose redaction to get there. A
stale machine label below its sources cannot leak silently, and a human
downgrade is still honored with one explicit flag, the same friction
ADR-0008 places on `--allow-downgrade`.

Harder: export edits bodies, so an exported document can differ from its
workspace copy in more than frontmatter, and the MCP and export boundaries
now treat link labels differently. A workspace with many deliberate
downgrades needs the flag on every export. Prose that names a withheld
object outside a link still leaves; the preview says so.

## Alternatives considered

- **Keep bodies verbatim (ADR-0028 parity).** Rejected: leaks the withheld
  object's title and id through every link into it.
- **Unlink but keep the label.** Rejected: removes the pointer but still
  ships the title.
- **Withhold every object that links to a withheld one.** Rejected:
  transitive, and in a real workspace most private objects would vanish.
- **Trust the own label only.** Rejected: a stale machine label below its
  source leaks with no friction.
- **Recompute the label from provenance.** Rejected: silently overrides
  every deliberate downgrade ADR-0008 permits.
