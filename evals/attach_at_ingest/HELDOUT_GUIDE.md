# Guide for the held-out labeller (#1701, part 2)

You are writing a test set. Work from this file and `heldout_schema.py` only. Do not read
anything else under `evals/`, any other fixture, any harness, or any result, and do not
look for how the set will be used: your labels must not be shaped by it. If you have seen
such material, say so in your report and stop.

## The situation you are modelling

A person keeps a knowledge base. Each time they ingest a meeting transcript, a program
extracts **objects** from it, each with an OKF **type** and a **title**: for example a
`Decision` titled "Retain readings for 90 days", or a `Concept` titled "Alert thresholds".
The knowledge base already holds pages from earlier meetings (the **library**). Your job is
to invent a realistic library, then invent the objects a **later meeting** would produce
(the **candidates**), and for each candidate say what a careful person who knows the
subject would answer:

> Does this candidate name the same thing as one page that already exists? Which one?

The answer is either the Concept ID of exactly one library page (the candidate is the same
thing, said again, perhaps in other words) or **none** (it names something the library does
not hold, or something different from every page it resembles).

Invent the content yourself. Use any domain or several, at least two languages (at least a
fifth of the titles not in English), and the voice of a real meeting extraction: short noun
phrases for concepts, short imperative or declarative sentences for decisions. Do not copy
text from the repository.

## What counts as the same thing, and what does not

Judge by what the title *refers to*, as a person reading the two pages would, not by how
many words they share. The same words can name different things; different words can name
one thing.

**Same thing (give the page's id as `gold`):**

- `exact`: the same title said again (case, punctuation and spacing aside).
- `decision-restated`: a `Decision` that states the page's **current** position again, in
  other words, with the same chosen value.
- `concept-reworded`: a `Concept` (or other non-Decision object) reworded, reordered,
  pluralised or inflected, naming the same thing.
- `concept-expanded`: a shorter or longer title for the same thing.
- `unreachable`: the same thing under words that share nothing with the existing title
  (a synonym, a translation, a nickname).

**Different thing (`gold` is `None`):**

- `value-changed`: a `Decision` about the same subject as a library decision but with a
  **new chosen value** (a later meeting changed the answer). **A decision whose value
  changed is a new, distinct decision, never the same one.** The library keeps the older
  position as its own page, retired by the newer one.
- `value-added`: a decision that restates the current position and adds a condition or
  scope, so that it decides something more or other.
- `numeral-differs`: the same wording with a different number, version, year, quarter or
  phase.
- `part-whole`: a shorter or longer title that names the whole of which the page is a part,
  or a part of what the page names, rather than the page's own subject.
- `sibling-aspect`: another aspect of the same subject that deserves its own page.
- `ambiguous`: a title that fits two different library pages equally well, so no single page
  is right.
- `cross-series`: an identical title for a decision made by a **different working group** or
  series, which is a different decision.
- `exact-homonym`: an identical title for a different referent (two things with one name).
- `cross-type`: the same title as a library page of another OKF type.
- `deprecated-target`: a title that restates only a page that is retired (marked
  `deprecated`, or superseded), with no live page that it could mean.
- `imported-target`: a title that restates only an imported page (id under `imports/`), with
  no local page that it could mean.
- `unrelated`: a title that resembles nothing in the library.

**Not judged, only included (`gold` is `None`):**

- `event`: a candidate of type `Event` (a meeting or occurrence). Include recurring series
  (the same title on different dates), the same title again, the title without its date,
  and the date moved to the front.
- `person`: a candidate of type `Person`: the same name, with and without accents, with a
  middle name or surname added, or a namesake.

A candidate of type `Event` or `Person` must use these classes, and these classes only.

## How to make the set useful

A set of easy cases proves nothing. For every distinct class, write candidates that a hasty
reader could take for an existing page, **and** some that are plainly different. Aim for a
real mix of lengths and shapes: one-word titles, long titles, titles with numbers, titles in
a second language. Do not write the distinct candidates all in the same pattern. Give the
`same` classes the same variety. Vary which library page each candidate points at; no page
should be the answer to more than three candidates.

Build the library as it would look after several meetings:

- Decisions come in **chains**: an earlier decision, then a later one that supersedes it
  (set `supersedes` on the later page). Make at least six superseded pages, in chains of two
  to four. Only the latest page of a chain is "current"; `decision-restated` and
  `value-changed` candidates are about current pages.
- Include retired pages (`deprecated: True`) of types other than Decision, imported pages
  (id under `imports/`), `Event` pages for at least two recurring series, and `Person` pages.
- Every page needs a realistic title; a body of a sentence or two is enough.

Label each candidate on its own merits, one at a time. If you cannot decide whether two
titles name the same thing, change the candidate until you can; do not include doubtful
cases. Put a one-line reason in `note`.

## Minimum counts

The checker (`uv run python evals/attach_at_ingest/heldout_schema.py`) enforces these; more
is better, but not at the cost of care.

| What | Minimum |
|---|---|
| Library documents | 60 (at least 20 `Decision`, 6 superseded, 4 `Event`, 4 `Person`) |
| Candidates naming an existing page (the five "same" classes) | 45, of which at least 30 are not `exact` |
| Candidates naming nothing existing (the twelve "different" classes) | 70 |
| `event` and `person` candidates | 16 together |

Per class: `exact` 5, `decision-restated` 8, `concept-reworded` 10, `concept-expanded` 4,
`unreachable` 4, `value-changed` 8, `value-added` 8, `numeral-differs` 8, `part-whole` 8,
`sibling-aspect` 8, `ambiguous` 3, `cross-series` 2, `exact-homonym` 2, `cross-type` 2,
`deprecated-target` 3, `imported-target` 2, `unrelated` 6, `event` 8, `person` 8.

## What to deliver

One file, `evals/attach_at_ingest/attach_fixtures_heldout.py`, in the format documented at
the top of `heldout_schema.py`: a module that defines `LIBRARY` and `CANDIDATES` and imports
nothing. Check it with the command above until it prints `ok`. Do not edit
`heldout_schema.py`, do not add other files, and do not run anything else in the repository.

Your report should say how many candidates you wrote per class, anything you were unsure
about, and whether you read anything outside this guide and the schema.
