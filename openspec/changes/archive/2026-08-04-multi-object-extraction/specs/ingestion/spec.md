# Delta for Ingestion

## MODIFIED Requirements

### Requirement: Type Classification Prefers Specific Types Over the Entity Fallback

Extraction MUST classify each derived object's type using a closed
vocabulary of `{Concept, Entity, Place, Event, Procedure, Decision, Project,
Person, Organization}`. `Entity` is a fallback only, used when no more
specific type fits; `Concept` MUST be preferred whenever content describes
an idea, topic, theory, or framework — including one named after a person,
organization, or place. The rubric MUST apply PER CANDIDATE OBJECT, not per
source: the model MUST first identify the candidates a source contains, then
classify EACH independently — never answer "what is this document about" as
one question with one answer. A person, place, or organization merely
mentioned in passing is an attribute of a richer object, not an independent
target; extraction MUST prefer FEWER, RICHER objects over many shallow ones.

Extraction MUST decide MULTIPLICITY per source via a stated test: a source
developing several distinct subjects (a person, an idea, a choice) MUST
yield one object per subject; a source developing one subject MUST still
yield exactly one. A candidate whose title and scope merely restate its
Source's own title and scope (a "twin") MUST NOT be produced ALONGSIDE
another genuine candidate: when a source develops more than one distinct
subject, the twin is dropped and the genuine subjects are kept. A source
whose ONE genuine subject IS what its own title already names is not
redundant with anything and still yields that subject — the unconditional
form of this rule is unsatisfiable together with the floor below, since a
single-subject source's only object would then have to be suppressed. The
rule is enforced deterministically, after per-item validation
(`_drop_source_title_twins`), not by prompt wording alone: prompt wording
could not carry the unconditional rule at the 8B tier, and a clause naming
a concrete forbidden title measurably worsened the defect (priming).

The floor is unchanged: genuine, intelligible content MUST yield AT LEAST
ONE object; blank, boilerplate-only, or unintelligible content MUST still
yield `[]`.

(Previously: the rubric's shared framing asked what "the source" —
singular — is about, giving one answer by construction; no multiplicity
test or anti-twin rule existed.)

#### Scenario: Entity chosen only when no specific type fits

- GIVEN a fake backend that only plausibly fits a concrete artifact
- WHEN extraction runs
- THEN the object's `type` is `Entity`, not any more specific type

#### Scenario: Concept preferred when content fits

- GIVEN a fake backend describing an idea or framework
- WHEN extraction runs
- THEN the object's `type` is `Concept`

#### Scenario: Self-narrating decision classifies as Decision

- GIVEN a source narrating a choice, its rationale, alternatives, and status
- WHEN extraction runs
- THEN the object's `type` is `Decision`, not `Concept` or `Event`

#### Scenario: Ongoing goal-directed effort classifies as Project

- GIVEN a source about an ongoing, goal-directed effort, not one happening
- WHEN extraction runs
- THEN the object's `type` is `Project`, not `Event`

#### Scenario: Named entities in passing are not enumerated

- GIVEN a source about one happening that names attendees only in passing
- WHEN extraction runs
- THEN the result keeps the richer objects and adds no per-attendee Person

#### Scenario: Multi-topic source yields one object per distinct subject

- GIVEN `examples/good-life-demo/raw/call-with-maria-2026-07-14.txt`,
  which discusses a person, a philosophical correction, and a choice made
- WHEN `openkos ingest` completes
- THEN three objects are written: `Person` (`people/maria-salazar.md`) and
  two `Concept` objects, one for the philosophical correction (typically
  titled "Apatheia") and one for the choice made (typically titled
  "Dichotomy of Control")

Note: the reference bundle also declares a `Decision`
(`decisions/frame-the-essay-on-the-dichotomy-of-control.md`) for the choice
made. Three targeted prompt wordings over roughly 28 samples produced zero
`Decision` objects with the default model, which consistently renders that
choice as `Concept: Dichotomy of Control` instead — an 8B-tier limit,
tracked separately as model/fixture work (proposal assumption 4), not
required by this scenario.

#### Scenario: Single-topic source still yields exactly one object

- GIVEN a source developing one subject only
- WHEN extraction runs
- THEN exactly one derived object is written

#### Scenario: Single-subject source keeps the object its title already names

- GIVEN a source with exactly one genuine subject, and that subject is what
  the source's own title already names
- WHEN extraction runs
- THEN that one object is still written — the anti-twin rule below does not
  suppress a source's only genuine subject

#### Scenario: A twin object is not produced alongside a genuine candidate

- GIVEN a candidate whose title/scope merely restate the Source's own,
  alongside at least one other candidate that is a genuine, distinct
  subject
- WHEN extraction runs
- THEN the twin candidate is absent from the written derived objects and
  the genuine candidate(s) are kept

#### Scenario: Blank or unintelligible content still yields no objects

- GIVEN a source that is blank, boilerplate-only, or unintelligible
- WHEN extraction runs
- THEN zero objects are written and `ingest` degrades to Source-only,
  unchanged from before this change
