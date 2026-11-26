# Ingestion Specification (delta)

## ADDED Requirements

### Requirement: Ingest Reports The Tokens Each Source Consumed

For each source an `openkos ingest` run sends to a chat backend, the command
MUST print ONE line to STDOUT that starts with `  tokens:` (two spaces, then
`tokens:`), after that source's other output, whether the run is a single-file
ingest or one file of a batch. The line reports tokens only, never money.

`calls` counts chat responses received for that source, a length-capped
response and a response whose extraction was later discarded included, because
the backend bills them; a request that failed before any response is not a
call. `prompt`, `completion` and `total` are the sums of what those responses
reported, and `total` MUST be the backend's own total when it reported one (a
reasoning model's hidden tokens are in the total but not in the completion),
else prompt plus completion.

The line MUST read:

- `  tokens: {N} call[s], prompt {P}, completion {C}, total {T}` when every call
  reported a total;
- `  tokens: {R} of {N} calls reported, prompt {P}, completion {C}, total {T}`
  when only some did, summing only those that reported;
- `  tokens: not reported by the backend ({N} call[s])` when none did, and MUST
  NOT print zero counts.

The line MUST also be printed when extraction failed or the run ended
Source-only, and when the review was declined or the run was refused after
extraction, because those calls were made. It MUST NOT be printed when no chat
response was received (a converged re-ingest contacts no model). The command
MUST NOT write the counts to any file, to `.openkos/`, to the bundle or to
`log.md`, and MUST NOT change any request sent to a backend or any exit code.
Each ingested source has its own tally; a batch prints one line per source.

#### Scenario: A single ingest prints one line

- GIVEN a backend whose responses report prompt, completion and total tokens
- WHEN `openkos ingest <file> --auto` completes
- THEN stdout contains exactly one `  tokens:` line whose `calls`, `prompt`,
  `completion` and `total` are the sums over the calls made

#### Scenario: A batch prints one line per source

- GIVEN `openkos ingest <directory> --auto` over two files
- WHEN the run completes
- THEN stdout contains two `  tokens:` lines, one per source, each from its
  own calls

#### Scenario: A failed extraction still reports

- GIVEN the backend answered but its reply could not be used, so the source
  ended Source-only
- WHEN the run completes
- THEN the `  tokens:` line is printed for the calls made

#### Scenario: A backend that reports no usage is not shown as zeros

- GIVEN every response carried no usable counters
- WHEN the run completes
- THEN the line reads `  tokens: not reported by the backend (N calls)` and
  contains no `prompt`, `completion` or `total`

#### Scenario: Partial reporting is visible

- GIVEN the first response reported tokens and later ones did not
- WHEN the run completes
- THEN the line reads `{R} of {N} calls reported` and sums only the reported
  calls

#### Scenario: A declined review still reports

- GIVEN extraction ran and the user declined the proposed changes
- WHEN the command ends
- THEN the `  tokens:` line is printed and nothing was written

#### Scenario: No model contact prints nothing

- GIVEN a byte-identical re-ingest whose extraction already converged
- WHEN the run completes
- THEN no `  tokens:` line is printed
