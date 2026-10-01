# Delta for Query Command

## ADDED Requirements

### Requirement: A Plain Query Never Waits On Or Refuses For The Workspace Lock

`openkos query` without `--save` MUST NOT acquire the workspace lock and
MUST NOT be refused because another process holds it. With `--save`, the
retrieval, answer generation, duplicate check, preview, and confirmation
MUST run without the lock, and the lock MUST be held only for the filing's
commit phase, which MUST re-validate the cited concepts whose sensitivity
decided the insight's level and refuse with exit `3` when any of them
changed (`workspace-lock`).

#### Scenario: A read answers while another process writes

- GIVEN another process holds the workspace lock
- WHEN `openkos query "<question>"` runs
- THEN it answers and exits as it would with the lock free

#### Scenario: A save refuses when a cited concept's level moved

- GIVEN `openkos query "<question>" --save` is waiting at its confirmation
  prompt, having computed the insight's level from cited concept `<id>`
- WHEN another process changes `<id>`'s sensitivity and the user confirms
- THEN the save exits `3` and files nothing
