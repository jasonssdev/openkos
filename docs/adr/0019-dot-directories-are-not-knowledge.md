---
type: Decision
title: "ADR-0019: Dot-directories are not knowledge"
description: init ships no app-specific .obsidian/ scaffolding, and the bundle *.md walk structurally excludes every dot-directory rather than naming one.
status: Accepted
date: 2026-09-14
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-14T00:00:00Z
sensitivity: public
---

# ADR-0019: Dot-directories are not knowledge

- **Status:** Accepted
- **Date:** 2026-09-14

## Context

Issue #984 asked whether `init` should ship a preconfigured `.obsidian/`
folder alongside a fresh bundle, so a first-time user who opens the bundle in
Obsidian gets a working setup out of the box. It is a genuinely cheap
first-run improvement, and it surfaced a real correctness gap along the way:
every `*.md` walk in this codebase (`okf._iter_docs` and eight sites that
built the identical `sorted(rglob("*.md"))` idiom inline) admits any
`.md` file under any directory, including a dot-directory, as a candidate
Knowledge Object. A hand-written or tool-written `.obsidian/*.md` file was
silently eligible to become a lint-reported "unparseable frontmatter"
finding, to be counted in document totals, and to perturb
`bundle_manifest_hash` -- exactly the failure mode this issue reproduced.

The tension is portability, and it is live, not hypothetical.
`docs/vision.md:97` and the README both frame this project explicitly against
Obsidian-coupled tooling: OKF is meant to be one markdown-plus-frontmatter
format any editor, any script, and any future tool can read, with no
application picked as privileged. Writing `.obsidian/` config into every
fresh bundle contradicts that framing directly -- it is application-specific
editor configuration, checked into a format whose entire premise is that no
application is required to read it. The rejected first-run improvement is
the cost of holding that line: a brand-new Obsidian user gets no automatic
folding, no automatic graph view, nothing beyond what any other editor gets.
Issue #983 already shipped the documented manual path
(`docs/user-journey.md#reading-the-bundle-in-an-editor`) that serves the same
user without the coupling -- a few copy-pasteable steps instead of a
zero-step default.

The second half is independent of the first and does not need the same
justification: dot-directories are, by the same convention every tool on
this stack already honors (`.git/`, `.obsidian/`, `.vscode/`, ...),
application or tooling configuration, never a concept a bundle author wrote.
The knowledge object model's own invariant -- "one markdown file = one OKF
concept" -- has never actually held for this walk; the `.state/` special
case that already existed papered over one instance of the gap (it happens
to carry no `.md` suffix) without stating the general rule. Issue #984 also
asked, as an open question, what should happen to a `.vscode/notes.md` --
naming `.obsidian/` alone would only have answered that question for one
application and left the same gap open for the next editor someone tries.

## Decision

We adopt two independent rules:

1. **`init` ships no `.obsidian/` scaffolding, and never will, for any
   third-party application.** No code is written to generate one. A user who
   wants an Obsidian-configured bundle follows the documented manual path
   from #983.
2. **Every `*.md` bundle walk excludes any file with a dot-directory
   component between the bundle root and itself**, structurally, by
   directory-name shape (`.` prefix) rather than by naming `.obsidian/`,
   `.vscode/`, or any other specific tool. `src/openkos/model/okf.py` gains
   one shared helper, `iter_bundle_markdown`, and both of `okf.py`'s own
   internal walks plus every external call site that previously built the
   identical `sorted(rglob("*.md"))` idiom inline now go through it. The
   test is against the path *relative to the bundle directory*, never the
   absolute path, so a workspace that itself happens to live under a
   dot-directory on a user's machine is unaffected -- only directory
   components *inside* the bundle are in scope. A dot-*file* directly at the
   bundle root (`bundle/.hidden.md`) is deliberately still walked; only
   directory components exclude.

A companion `lint` check, `dot-dir-markdown`, reports every dot-directory
holding `.md` files this exclusion now drops (other than `bundle/.state/`,
which keeps its own existing, more specific finding) -- naming the drop is
required precisely because the exclusion makes it silent otherwise.

The finding is per DIRECTORY, with a count and a few example paths, not per
file. The two halves of this decision constrain each other here: because we
now recommend opening `bundle/` in an editor, and an editor grows
dot-directories that fill with markdown -- Obsidian's `.obsidian/.trash/`
holds every note the user ever deleted -- one finding per file would make a
health report unreadable on every run, and would repeat a single fact ("this
directory is not knowledge") once per file that happens to sit in it. The
remedy is phrased about the directory for the same reason: "move this file
out" is the wrong instruction for a file the editor wrote and owns, and the
decision a human makes here is about the directory, once.

## Consequences

**Breaking behavior change:** any `.md` file a user had previously placed
under a dot-directory inside a bundle -- deliberately or by accident -- stops
being treated as part of the bundle. It disappears from `lint`'s document
counts, from `bundle_manifest_hash` (so a change limited to such a file no
longer triggers a reindex), and from the FTS/graph indexes and any retrieval
built on them. For anyone who was relying on the old, unintentional
admission, this is a loss of visibility, mitigated only by the new lint
finding surfacing it going forward -- there is no automatic migration, and a
user must move a real concept out of a dot-directory by hand if they want it
back.

Application-specific editor tooling remains entirely the user's own
responsibility to configure, forever -- this project accepts the ongoing
cost of every "why doesn't `init` just do X for me" request that names a
specific editor, in exchange for never coupling the portable format to one.

The dot-directory rule is now general and requires no future ADR to extend
to the next tool that adopts a dot-directory convention; `.vscode/`,
`.idea/`, or anything else is covered for free.

## Alternatives considered

- **Ship `.obsidian/` in `init`, or make it opt-in via a flag.** Rejected:
  even opt-in, it establishes Obsidian as the one application `init` knows
  about by name, which is the exact coupling the vision document argues
  against. The manual path from #983 costs the user a few minutes once;
  this would have cost the project's app-agnostic framing indefinitely.
- **Name `.obsidian/` (and maybe a short list of other known tool
  directories) explicitly in the walk exclusion, instead of a general
  dot-directory rule.** Rejected: this is exactly the shape issue #984's own
  open question warned against -- a named list needs a new entry (and a new
  release) for every future tool, and a bundle author's own
  `.private-notes/`-style convention would still slip through unless it
  happened to make the list. The general, structural rule needs no list and
  no future amendment.
- **Leave the walk unchanged and rely on the new lint check alone to warn
  users away from dot-directories.** Rejected: a non-gating warning does not
  stop a `.obsidian/*.md` file from silently perturbing
  `bundle_manifest_hash` and being sporadically counted or indexed depending
  on its frontmatter shape -- the correctness fix has to be in the walk
  itself, with the lint check as the required companion disclosure, not a
  substitute for it.
