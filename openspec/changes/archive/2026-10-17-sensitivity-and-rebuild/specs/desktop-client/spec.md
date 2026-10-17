# Delta for desktop-client

## ADDED Requirements

### Requirement: The Client Sets A Page's Sensitivity And Rebuilds Search

The TypeScript client MUST offer `previewSensitivity`, `applySensitivity`,
`searchIndex`, `startSearchRebuild`, `searchRebuild` and `stopSearchRebuild`
over the routes of the same names, typed from the regenerated schema, and MUST
name the codes `page_not_found`, `sensitivity_unchanged`, `rebuild_active`,
`remote_not_allowed` and `search_unavailable`.

#### Scenario: Each method calls its route

- WHEN each method is called with a body
- THEN it sends that body to its route with the bearer token
