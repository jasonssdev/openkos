# Entity Resolution Specification (delta)

## ADDED Requirements

### Requirement: Near-Match Pairs Of One Subject Cluster Into One Group

`find_candidates` MUST fold the LOW-tier pairs of each OKF type into connected components and return ONE LOW-tier candidate group for every component of three to eight members, in place of that component's pairs. The edges are exactly the LOW pairs the pairwise pass produces: clustering MUST NOT add a similarity threshold, MUST NOT make any pair a candidate that was not, and MUST NOT run across OKF types. A cluster's `member_ids` MUST be sorted ascending and its `trigger` MUST be the weakest near-match score among its pairs, formatted to three decimals. A component of two members MUST remain the pair it was. A component of OKF type `Event` MUST remain its individual LOW pairs: the meetings of a recurring series are different things that share a near-identical title, so one group question over them would invite merging distinct meetings (the hazard the ingest attach exclusion guards; the set of such types is defined once in `model/types.py` and read by both). `Person` is not excluded from clustering: same-name people across meetings are the subject of the group question, and the person marks which pages are the same. A component of more than eight members, or one in which any two members are already asked about as a HIGH or ACRONYM candidate, or in which any two members stand ruled distinct by a person (a keep-distinct ruling not reopened), MUST remain its individual LOW pairs, so a cluster never asks a pair again that a person already ruled apart; the ruled pair itself is still returned as a pair, so its suppression is unchanged. The cap on candidate groups and its truncation notice count clusters as groups. Every pair that is a LOW candidate without clustering MUST lie inside the member set of exactly one returned group.

#### Scenario: A recurring subject is one question

- GIVEN four same-type pages whose titles chain into five LOW pairs
- WHEN `find_candidates` runs
- THEN one LOW group of the four pages is returned, and each of the five pairs lies inside it

#### Scenario: A component of two stays a pair

- GIVEN two same-type pages that form one LOW pair and no other
- WHEN `find_candidates` runs
- THEN a LOW group of exactly those two pages is returned

#### Scenario: A component above the bound stays as pairs

- GIVEN a connected set of nine same-type pages joined by LOW pairs
- WHEN `find_candidates` runs
- THEN no cluster is returned and each LOW pair is returned as its own group

#### Scenario: Pages of different types never cluster

- GIVEN two same-type pages that form a LOW pair and a third page of another type with a similar title
- WHEN `find_candidates` runs
- THEN only the same-type pair is returned

#### Scenario: A component holding an exact-title pair stays as pairs

- GIVEN a component that contains two pages whose titles are identical
- WHEN `find_candidates` runs
- THEN the HIGH group is returned and the LOW pairs are not merged into a cluster

#### Scenario: A pair ruled distinct is not asked again inside a cluster

- GIVEN a connected set of four same-type pages joined by LOW pairs, two of which a person ruled distinct
- WHEN `find_candidates` runs
- THEN no group of more than two pages contains both ruled pages, and the ruled pair is still returned as a pair

#### Scenario: Near-match Events stay pairs

- GIVEN three same-type `Event` pages whose titles chain into three LOW pairs
- WHEN `find_candidates` runs
- THEN three LOW pairs are returned and no group of more than two pages
