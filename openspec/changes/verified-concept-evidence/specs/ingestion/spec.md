# Ingestion Specification (delta)

## ADDED Requirements

### Requirement: A Verified Evidence Quote Counts As An Object's Evidence

A concept extraction item MAY carry an optional `evidence` string. The engine
MUST accept it as the object's evidence only when, after casefolding,
whitespace collapsing and trimming the quote's trailing sentence punctuation,
it is at least four words long and a substring of the equally normalized source
text. An object whose `evidence` is accepted MUST NOT be named by
`objects-without-evidence`. An absent, non-string, short or unverifiable
`evidence` MUST leave the check exactly as if the field were absent. The value
MUST NOT be written into the object's body, description or frontmatter, and a
non-string value MUST NOT reject the item.

#### Scenario: A paraphrasing object with a verbatim quote is not flagged

- GIVEN an extracted object whose body shares no line with the source
- AND its `evidence` is a line the source writes
- WHEN ingest computes the objects without evidence
- THEN the object is not named

#### Scenario: A quote the source does not contain changes nothing

- GIVEN an extracted object whose body shares no line with the source
- AND its `evidence` does not occur in the source
- WHEN ingest computes the objects without evidence
- THEN the object is named, as it is without the field

#### Scenario: A quote below four words verifies nothing

- GIVEN an object with `evidence` of two words that occur in the source
- THEN the object is judged by its written text alone

#### Scenario: The quote is not stored

- GIVEN an object whose `evidence` verified
- WHEN its concept document is written
- THEN the body, description and frontmatter are those it would have without
  the field
