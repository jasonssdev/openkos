# Delta for MCP

## ADDED Requirements

### Requirement: `pending` Also Lists Open Queue Rows Through The Disclosure Gate

WHEN the pending-work queue exists, the `pending` tool's result MUST carry,
beside the existing `next_action` recommendation, the open queue rows --
kind, target ids, and the resolving command -- read without a lock and
without a model call. Every row MUST pass through the same disclosure gate
the existing recommendation passes through: a row with any subject that is
not fully disclosable MUST be withheld whole, and the result MUST NOT count
withheld rows. The tool MUST NOT change any row's status. WHEN the queue is
absent, the result MUST say it has not been computed, never that nothing is
pending.

#### Scenario: A row naming a confidential concept is withheld

- GIVEN an open identity row whose members include a concept not
  disclosable at the server's launch setting
- WHEN `pending()` returns
- THEN that row is absent and no count of withheld rows is present

#### Scenario: Listing does not claim a row

- GIVEN an open row
- WHEN `pending()` is called
- THEN the row's status is unchanged
