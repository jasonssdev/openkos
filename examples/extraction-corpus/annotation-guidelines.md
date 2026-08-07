# Annotation guideline v1

This document is versioned, and the version is part of its meaning: **v1 is
this file**. Ground truth is always read against a declared guideline version,
so the question "what did this ground-truth file mean when it was written?"
has a stable answer regardless of what the extraction pipeline does later.

**Scope.** This guideline governs subject-level ground truth in
`examples/extraction-corpus/ground-truth/` **and**
`evals/decision_extraction/ground_truth/`. Every type call, subject/facet
demotion, and out-of-scope filing in those files is made under the rules
below, not under an annotator's independent taxonomy.

## §1 Type rubric (frozen)

Type rubric frozen from `src/openkos/extraction/concept.py::_SYSTEM_PROMPT`
as of commit `8c640810544c63ca02699298d066d5d15dc5b4b3` (main, 2026-08-07).

The nine type definitions, verbatim:

- "Person": the candidate is ONE specific, named individual human -- their
  identity, role, work, or biography.
- "Organization": the candidate is ONE specific, named group, company,
  institution, team, or agency.
- "Place": the candidate is ONE specific, named geographic location or
  physical site -- a city, region, building, landmark, or venue -- treated AS
  a location.
- "Event": the candidate is ONE bounded, dated happening -- an occurrence
  tied to a specific time or span (a meeting, launch, battle, incident, or
  conference).
- "Procedure": the candidate is ONE repeatable how-to -- a method, protocol,
  recipe, or step-by-step process meant to be performed again.
- "Decision": the candidate is ONE choice that was made -- carrying its
  rationale, the alternatives considered, and its current status -- a
  self-contained decision record, not a general idea or a dated happening.
- "Project": the candidate is ONE ongoing effort defined by a goal and a
  timespan -- a multi-step undertaking spanning time toward that goal, not a
  single bounded happening or a repeatable how-to.
- "Concept": the source describes an idea, topic, theory, term, or framework
  -- INCLUDING one named after a person, organization, or place (a named
  method, system, principle, or law). A name borrowed from a person,
  organization, or place is a label, not the subject: classify by what the
  candidate is actually about, not by whose name it carries.
- "Entity": a fallback for a concrete tool, product, or artifact that is
  neither a who, a where, nor an idea -- Entity is never the first choice,
  only what remains when nothing else fits.

The tie-breaks that accompany them, verbatim:

Tie-breaks, applied in this order:

(1) Name vs. denoted concept -- e.g. "Toyota" the company is Organization,
but "Toyota Production System" is Concept; a person is Person, but a theory
named after them is Concept; a landmark IS its named place, but "Stockholm
Syndrome" is Concept, not Place; a general geographic idea (e.g. "urbanism")
is Concept, not one specific named site -- prefer Person, Organization, or
Place ONLY when the source centers on the individual, institution, or
location itself, otherwise choose Concept.

(2) Among specific named continuants, occurrents, and knowledge-work objects
(Person, Organization, Place, Event, Procedure, Decision, Project) -- pick
whichever the source centers on:

- A landmark or site named after a person or organization (e.g. a memorial)
  is "Place" ONLY if the source is about the physical site itself; if the
  source is about the honoree, choose Person or Organization instead.
- An organization sited at one location (a headquarters or campus) is
  "Organization" when the source centers on the group's identity or
  activity; choose "Place" only when the source centers on the site itself
  as a location.
- A source about a bounded, dated happening is "Event", not "Place" -- the
  place is merely where it occurred; choose "Place" only when the source is
  genuinely about the location itself as a site, not about what happened
  there.
- Among occurrents, "Event" is a single time-bound happening while
  "Procedure" is a repeatable how-to.
- A choice made with rationale, alternatives considered, and a current
  status is "Decision" -- distinct from "Concept" (a general idea, topic,
  theory, or framework, with no decision-record shape) and from "Event" (a
  dated happening with no rationale or alternatives weighed).
- An ongoing effort defined by a goal and a timespan is "Project" --
  distinct from "Event" (a single bounded happening) and from "Procedure"
  (a repeatable how-to meant to be performed again, not a one-time effort
  toward a goal).
- When Person and Organization are truly balanced, prefer "Organization"
  (the continuant that outlives individuals).

(3) Person, Organization, Place, and Concept all outrank "Entity" -- so do
"Event", "Procedure", "Decision", and "Project" -- Entity is the last
resort, used only when nothing else fits.

### Divergence rule

`_SYSTEM_PROMPT` is a **product fixture that evolves** — it changed four
times in the past week alone (#377 axes D2/D3/D4/4b). This guideline does
**not** track it automatically. If `_SYSTEM_PROMPT` diverges from §1, the
divergence is recorded explicitly here, and the guideline version is bumped
only if the change is adopted for annotation. Ground truth is always read
against a declared guideline version, so a rubric edit can never
retroactively change what a ground-truth file meant.

## §2 Person policy

One test decides every `Person` call in this corpus, the same one applied
everywhere else in it: **does the source DEVELOP the person, or merely
mention them?** A `Person` subject exists when the source builds knowledge
about an individual under a consistent handle; a name that merely occurs is
not a subject. Four cases:

1. **Elided or channel-labelled speakers (`A:`–`D:`) → NEVER `Person`.** A
   channel label carries no recoverable identity: "A said X" is provenance
   of the utterance, not knowledge about A. There is no handle under which
   cross-source accumulation could occur.

2. **Roles (Project Manager, Industrial Designer, …) → never `Person`s.**
   They are functions, not individuals — facets of the `Project` or `Event`
   they serve.

3. **A fictional or pseudonymous name the source DEVELOPS → YES, a
   `Person`.** Basis: the "representation, not truth" principle — the
   bundle represents what the source says; it does not verify civil
   identities. A consistent name the source develops IS the cross-source
   accumulation handle, real or not. Precedent: Maria Salazar is fictional
   and is the canonical `Person` of `examples/good-life-demo`. Fictionality
   is not part of the test.

4. **A name that merely escapes anonymization → passing mention, out of
   scope.** Example: `Dennie` in `TS3005b`, which survives the AMI corpus's
   name elision only as a spelling question, with no other word about the
   person.

## §3 Evaluation unit

Ground truth in this corpus counts **subjects, not mentions**. This is a
declared methodological choice, not an inference left to the reader.

Concrete example: AMI's own manual annotations mark 17 `Person` mentions in
`TS3005a` and 3 in `TS3005b`, while this corpus's ground truth lists zero
`Person` subjects in both files — and both are correct. Mention-level recall
against AMI's annotations would misread the extractor as "missing" 20
people; the unit here is the subject the source develops, and neither
transcript develops any individual (§2, cases 1 and 2). A score computed
against these files is a score over subjects; comparing it to a
mention-level number is a category error.
