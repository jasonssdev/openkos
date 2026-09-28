# Delta for Sensitivity-Aware LLM

## ADDED Requirements

### Requirement: Disclosure To A Non-LLM Consumer Is Gated Fail-Closed

A second predicate, distinct from `blocks_llm_send`, MUST decide whether a
concept or bulk row set may be disclosed to a non-LLM consumer — a program
receiving a structured tool result rather than an LLM prompt. It MUST reuse
the same STRICT fail-closed rank `blocks_llm_send` applies: a value that is
absent, blank, unrecognized, unreadable, or unparseable counts as
confidential, exactly as an explicit `confidential` value does.

This predicate MUST take exactly one policy input — whether the consuming
surface's launch-time opt-in is enabled — and MUST NOT accept an
`include_confidential` or `local_exemption` parameter of any kind: those
are LLM-egress escapes, and neither carries an honest meaning for
disclosure to a non-LLM consumer. A per-value check and a set-producing
sibling for bulk rows MUST both exist and MUST apply the same rank.

#### Scenario: An explicit confidential value is withheld by default

- GIVEN a concept with `sensitivity: confidential`
- WHEN the disclosure predicate evaluates it with the launch opt-in off
- THEN it returns not-disclosable

#### Scenario: A resolved-confidential value is disclosable once the opt-in is on

- GIVEN a concept whose effective sensitivity resolves to confidential —
  whether by an explicit value, a missing field, or unparseable frontmatter
- WHEN the disclosure predicate evaluates it with the launch opt-in on
- THEN it returns disclosable

#### Scenario: Private and public values are always disclosable

- GIVEN concepts with `sensitivity: private` and `sensitivity: public`
- WHEN the disclosure predicate evaluates them, regardless of the opt-in
- THEN both return disclosable

#### Scenario: The predicate accepts no LLM-egress escape parameters

- GIVEN the disclosure predicate's signature
- WHEN it is inspected
- THEN it accepts no `include_confidential` and no `local_exemption`
  parameter

#### Scenario: The bulk sibling applies the identical rank

- GIVEN a mixed set of rows spanning public, private, and every fail-closed
  fallback condition
- WHEN the set-producing sibling evaluates them with the opt-in off
- THEN it returns exactly the disclosable subset the per-value check would
  return for each row individually

#### Scenario: An id with no walked document is withheld even under the opt-in

- GIVEN a concept id referenced by a relation or provenance entry, for
  which no corresponding document exists on disk to walk
- WHEN the set-producing sibling evaluates the walked set, with the launch
  opt-in on or off
- THEN that id is absent from the returned set in both cases, since there
  is no document behind it whose rank the opt-in could ever disclose
