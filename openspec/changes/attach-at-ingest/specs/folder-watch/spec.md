# Delta for Folder Watch

## ADDED Requirements

### Requirement: A New Version Revises The Concepts Its Predecessor Produced

WHEN a changed watched file is imported as a new version and its extraction
yields a candidate that matches a concept the previous version produced
(same OKF type, same normalized title key, type not excluded from attach),
the import MUST revise that concept through "An Extracted Candidate Attaches
To An Existing Same-Type, Same-Key Concept" and MUST NOT write a `<slug>-N`
copy of it. The watch MUST add no versioning-specific matching: the rule is
the ordinary attach rule, so the same concept is revised whether the second
Source is a new version or an unrelated file. The import MUST continue to
propose the supersession as a pending-work row and MUST NOT write the
relation. Concepts the new version no longer yields are left unchanged;
retiring them is the subject of "Effective Status Resolution"
(provenance orphans) in `status-aware-retrieval`.

#### Scenario: An edit revises its concepts and writes no copies

- GIVEN `notes.md` imported, producing a `Concept` "Agent Skills", and then
  edited so the new version also yields "Agent Skills"
- WHEN the watch imports the new version
- THEN `concepts/agent-skills.md` lists both Sources in `provenance` with
  `version: 2`, and no `concepts/agent-skills-2.md` exists

#### Scenario: A concept the edit adds is created

- GIVEN the same edit also yields a `Concept` the first version did not
- WHEN the watch imports the new version
- THEN that concept is created normally with the new Source as its only
  provenance

#### Scenario: The supersession is still only proposed

- GIVEN the edit above was imported
- WHEN the queue is read
- THEN one open `relation_type` row proposes the new Source supersedes the
  earlier one, and neither Source carries a `supersedes` relation

#### Scenario: A re-saved version converges

- GIVEN the new version was imported and attached
- WHEN the same bytes are saved again and settle
- THEN nothing is written and no model call is made
