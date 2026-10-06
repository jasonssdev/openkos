# Delta for Ingestion

## MODIFIED Requirements

### Requirement: An Extracted Candidate Attaches To An Existing Same-Type, Same-Key Concept

WHEN `attach_at_ingest` is enabled, `ingest` MUST, per staged candidate and
before the slug-collision path, look for an existing concept of the SAME OKF
`type` whose `title` has the same normalized key as the candidate's
(`resolution.normalize.normalize_key`, the key the exact-title Identity tier
uses). WHEN one or more such concepts exist, the candidate MUST NOT be
written to a `<slug>-N` slug and MUST NOT create a file; it MUST instead be
staged as an ATTACH to one existing concept. The attach target MUST keep its
Concept ID, `type`, `title` and `description`. WHEN several concepts match
(a pre-existing `base`/`-N` family), the target MUST be the un-suffixed id,
else the lowest `-N`.

The match is purely structural: it MUST NOT consult a model, a judge
confidence, an embedding, or an alias. A concept of a different OKF type MUST
NOT match. The OKF types `Event` and `Person` are EXCLUDED: a candidate of
either type MUST take the unchanged slug-collision path. A concept whose
effective status is deprecated MUST NOT be an attach target; WHEN it is the
only same-key match, the candidate MUST take the unchanged slug-collision
path. An imported concept (one adopted by `openkos import`) MUST NOT be an
attach target either; WHEN it is the only same-key match, the candidate MUST
take the unchanged slug-collision path and the imported concept MUST stay
byte-untouched. WHEN any same-key match already lists this ingest's
`sources/<slug>` in its `provenance`, the candidate MUST be skipped
create-only and every matching file left byte-untouched (the unchanged
same-source no-op), so a re-ingest never attaches twice and never bumps
`version` twice.
(Previously: imported concepts were not addressed, and would have been
attach targets once adopted into the bundle.)

#### Scenario: A foreign-source candidate attaches instead of forking

- GIVEN `concepts/model-context-protocol.md` of type `Concept` with
  `provenance: [sources/a]`
- WHEN source `b` yields a `Concept` titled "Model Context Protocol"
- THEN no `concepts/model-context-protocol-2.md` is written, and
  `concepts/model-context-protocol.md` lists both `sources/a` and
  `sources/b` in `provenance`

#### Scenario: Normalization decides the match, not the slug

- GIVEN an existing `Place` titled "Café Central"
- WHEN a candidate `Place` titled "Cafe central" is staged from another
  source
- THEN it attaches to the existing `Place`

#### Scenario: A different type does not match

- GIVEN an existing `Concept` titled "Atlas" and a candidate `Project`
  titled "Atlas"
- WHEN the candidate is staged
- THEN it is written as a new `projects/atlas.md` and the `Concept` is
  untouched

#### Scenario: Person and Event keep today's behavior

- GIVEN an existing `Person` titled "Ana Ruiz" with `provenance:
  [sources/a]` and a candidate `Person` titled "Ana Ruiz" from source `b`
- WHEN the candidate is staged
- THEN it is written to `people/ana-ruiz-2.md` exactly as before this
  requirement, and the existing file is untouched
- GIVEN the same shape with type `Event`
- WHEN the candidate is staged
- THEN it is likewise written to `events/<slug>-2.md`

#### Scenario: A deprecated concept is never an attach target

- GIVEN a `Concept` titled "Skills" that is effective-deprecated, and no
  other same-key `Concept`
- WHEN a candidate `Concept` titled "Skills" is staged from a new source
- THEN it is written to the first free `<slug>-N`, and the deprecated
  concept is untouched

#### Scenario: A pre-existing family attaches to its canonical member

- GIVEN `concepts/skill.md`, `concepts/skill-2.md` and `concepts/skill-3.md`
  all of type `Concept` with the same key, none citing source `d`
- WHEN source `d` yields a `Concept` titled "Skill"
- THEN `concepts/skill.md` is the attach target and `-2`/`-3` are untouched

#### Scenario: A source that already owns a family member is a no-op

- GIVEN `concepts/skill-2.md` lists `sources/c` in `provenance`, and
  `concepts/skill.md` does not
- WHEN source `c` is re-ingested with a re-extraction yielding "Skill"
- THEN no file is written and no `version` changes

#### Scenario: A cross-source homonym of a non-excluded type does attach

- GIVEN a `Project` titled "Atlas" from source `a`
- WHEN source `b` yields a `Project` titled "Atlas"
- THEN it attaches (the exclusion list is the only guard against homonyms;
  this scenario pins that the list, not a heuristic, decides)

#### Scenario: An imported concept is never an attach target

- GIVEN an imported `Concept` titled "Atlas" under `imports/acme/` and no
  local same-key `Concept`
- WHEN a local source yields a `Concept` titled "Atlas"
- THEN the candidate takes the slug-collision path (a new local file), and
  the imported concept is byte-identical to before

#### Scenario: A local match still wins over an imported one

- GIVEN a local `Concept` "Atlas" and an imported `Concept` "Atlas"
- WHEN a source yields a `Concept` titled "Atlas"
- THEN the candidate attaches to the local concept, never to the imported
  one
