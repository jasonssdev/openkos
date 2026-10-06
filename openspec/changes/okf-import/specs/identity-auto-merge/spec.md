# Delta for Identity Auto-Merge

## MODIFIED Requirements

### Requirement: The Structural Class Predicate

A candidate group MUST be in the class if and only if all of the following
hold: its tier is HIGH; it has exactly two members; both members have the
same single concept type; that type is not one of the attach-excluded types
(`ATTACH_EXCLUDED_TYPES`, today Event and Person); neither member is an
imported concept (one adopted by `openkos import`); and one member's Concept
ID is the other's ID plus a `-N` ingest-time disambiguator (digits only, same
directory, the `is_suffix_family` relation, in either order). The predicate
MUST be structural only: it MUST NOT read a verdict or a confidence. Whether
a member is imported is a property of the concept's own recorded identity,
and the predicate MUST NOT depend on a model. Production and the
`evals/auto_merge` harness MUST share the one predicate; no second copy may
exist. A group the predicate refuses for an imported member MUST remain
visible to `duplicates`, `adjudicate`, `merge` and curate Identity's
per-item prompt, but, being out of class, it is neither merged
automatically nor offered through accept-recommended.
(Previously: the predicate had no imported-member condition, and stated that
it MUST NOT read a file.)

#### Scenario: A base/`-N` pair of one allowed type is in class

- GIVEN a HIGH-tier two-member group `concepts/foo` and `concepts/foo-2`,
  both of type `Concept`
- WHEN the predicate is evaluated
- THEN the group is in class

#### Scenario: Each structural disqualifier takes a group out of class

- GIVEN a group that is otherwise in class but differs in exactly one
  respect: a tier below HIGH; three members; two different concept types; an
  attach-excluded type (Event, then Person); or ids that are a `-a`/`-b`,
  `-1`/`-2` or unrelated-slug shape instead of base/`-N`
- WHEN the predicate is evaluated for each variant
- THEN every variant is out of class

#### Scenario: A group with an imported member is out of class

- GIVEN a HIGH-tier base/`-N` pair of one allowed type in which one member,
  then the other, then both are imported concepts
- WHEN the predicate is evaluated for each variant
- THEN every variant is out of class

#### Scenario: An imported group is still offered to a human

- GIVEN a HIGH-tier pair with an imported member that the predicate refuses
- WHEN curate Identity runs interactively
- THEN the group is prompted individually, and is not listed in any
  accept-recommended offer

#### Scenario: The predicate reads no verdict and no model

- GIVEN an in-class group
- WHEN the predicate is evaluated
- THEN it returns in class without a verdict, a confidence or a model call

#### Scenario: The eval harness uses the production predicate

- GIVEN the `evals/auto_merge` structural-class harness
- WHEN its `--self-test` runs
- THEN it exercises the production predicate and passes, and the harness
  defines no predicate of its own

### Requirement: The Recommended Set For Accept-Recommended

The set that Identity's accept-recommended answer may offer MUST be exactly
the groups that satisfy: the run is eligible; the group is in the class (so
no member is an imported concept); its
`cross_type_concern` is `None`; no member is confidential or LLM-blocked; and
the measured model judged it `same` in this run at any confidence. Confidence
below 0.90 MUST NOT exclude a group from the recommended set. A group outside
the set MUST NOT be offered through accept-recommended and MUST keep its
per-item prompt. A group with an imported member is outside the measured
population (ingest-time `-N` siblings) and is therefore outside the set,
whatever its verdict and confidence.
(Previously: the set was the in-class groups; the imported-member exclusion
was not stated, and is now explicit.)

#### Scenario: A lower-confidence in-class `same` group is recommended

- GIVEN an in-class group judged `same` at confidence 0.60 in an eligible run
- WHEN the recommended set is built
- THEN the group is in the set

#### Scenario: An out-of-class `same` group is not recommended

- GIVEN a three-member group, or a two-member Person pair, judged `same`
- WHEN the recommended set is built
- THEN the group is not in the set

#### Scenario: A non-`same` in-class group is not recommended

- GIVEN an in-class group judged `distinct` or `uncertain`
- WHEN the recommended set is built
- THEN the group is not in the set

#### Scenario: A confidential or LLM-blocked member excludes the group

- GIVEN an in-class group judged `same` with a confidential member
- WHEN the recommended set is built
- THEN the group is not in the set and keeps its per-item prompt

#### Scenario: An ineligible run has an empty set

- GIVEN a run whose model digest differs from the measured digest
- WHEN the recommended set is built
- THEN it is empty

#### Scenario: A `same` group with an imported member is not recommended

- GIVEN a HIGH-tier base/`-N` pair of one allowed type, such as
  `imports/acme/concepts/python` and `imports/acme/concepts/python-3`,
  judged `same` at high confidence in an eligible run
- WHEN the recommended set is built
- THEN the group is not in the set and keeps its per-item prompt

#### Scenario: A mixed local and imported group is not recommended

- GIVEN a `same` group with one local and one imported member
- WHEN the recommended set is built
- THEN the group is not in the set and keeps its per-item prompt
