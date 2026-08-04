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
Source's own title and scope (a "twin") MUST NOT be produced — this adds
suppression without relaxing the floor below.

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
- THEN three objects are written: `Person` (`people/maria-salazar.md`),
  `Concept` (`concepts/stoicism.md`), `Decision`
  (`decisions/frame-the-essay-on-the-dichotomy-of-control.md`)

#### Scenario: Single-topic source still yields exactly one object

- GIVEN a source developing one subject only
- WHEN extraction runs
- THEN exactly one derived object is written

#### Scenario: A twin object is not produced

- GIVEN a candidate whose title/scope merely restate the Source's own
- WHEN extraction runs
- THEN that candidate is absent from the written derived objects

#### Scenario: Blank or unintelligible content still yields no objects

- GIVEN a source that is blank, boilerplate-only, or unintelligible
- WHEN extraction runs
- THEN zero objects are written and `ingest` degrades to Source-only,
  unchanged from before this change
