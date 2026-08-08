# Delta for Graph Projection

## MODIFIED Requirements

### Requirement: Node Identity Is The OKF Concept ID

Each node MUST be keyed by the OKF concept ID — the document's
bundle-relative path with the `.md` suffix removed, NFC-normalized — the same
identity `fts.py` and `forget` use. Because the id is NFC regardless of the
on-disk spelling, an edge whose `relations:` target is spelled NFC MUST match
a node whose filename a normalizing filesystem stored as NFD.
(Previously: the id was the raw relative path with no normalization, so a
node derived from an NFD filename could not be matched by an NFC-spelled
`relations:` target and the edge was dropped silently.)

#### Scenario: Node id matches the concept id convention

- GIVEN a concept document at `bundle/concepts/stoicism.md`
- WHEN the projection is built
- THEN the corresponding node's id is `concepts/stoicism`

#### Scenario: Typed edge survives a decomposed target filename

- GIVEN a target document whose filename is stored NFD and a source document
  whose `relations:` entry names it spelled NFC
- WHEN the projection is built
- THEN the typed edge is projected and the node id is the NFC spelling
