# Ingestion Specification (delta)

## ADDED Requirements

### Requirement: Asking For An Evidence Quote Is Opt-In

`openkos.yaml` MAY set `evidence_quote` to a boolean. Absent or null it MUST
default to false, and a non-boolean value MUST be refused when the
configuration is read. When true, concept extraction MUST send the concept
system prompt with the registered evidence-quote instruction and reply-schema
field, and the prompt MUST be exactly the text the pre-registered A/B measured.
When false or absent, the concept prompt MUST be the shipped prompt unchanged.
The setting MUST NOT alter the re-ask, decision-pass or participant-capture
prompts, and MUST NOT be enabled by backend choice.

#### Scenario: The setting defaults off

- GIVEN an `openkos.yaml` without `evidence_quote`
- WHEN a source is extracted
- THEN the concept system prompt is the shipped prompt, unchanged

#### Scenario: The setting asks for a verbatim quote

- GIVEN `evidence_quote: true`
- WHEN a source is extracted, whole or in chunk windows
- THEN every concept call sends the registered treatment, whose sha256 is the
  registered one
- AND the re-ask, decision-pass and participant-capture prompts are unchanged

#### Scenario: A non-boolean value is refused

- GIVEN `evidence_quote: 1`
- WHEN the configuration is read
- THEN reading fails naming the key and the expected type

#### Scenario: A backend does not enable it

- GIVEN `backend: openai-compatible` without `evidence_quote`
- WHEN a source is extracted
- THEN the shipped prompt is sent
