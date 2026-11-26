# Query Answer Specification (delta)

## ADDED Requirements

### Requirement: The Sufficiency Check May Run On Its Own Client

`answer` MUST accept an optional keyword `sufficiency_llm`. WHEN it is given
and `sufficiency_check` is enabled, the check MUST be sent to `sufficiency_llm`
and synthesis MUST still be sent to `llm`; both receive the same assembled
context. WHEN it is omitted, the check MUST run on `llm`, with behaviour
byte-identical to a call made before the parameter existed. `sufficiency_llm`
MUST NOT be called when the check is disabled or when no context was assembled.

WHEN the check runs on a `sufficiency_llm` and that client raises
`BackendModelNotFound`, `answer` MUST fail the check open — produce the answer,
set `sufficiency_degraded` to `True` and `sufficiency_model_missing` to `True` —
instead of raising. `BackendUnavailable` and `BackendEmbeddingDimensionMismatch`
MUST still propagate. WHEN the check shares the synthesis client,
`BackendModelNotFound` MUST still propagate, because the missing model is then
the answer model. `sufficiency_model_missing` MUST be `False` on every other
result.

#### Scenario: The check and the synthesis use different clients

- GIVEN a `sufficiency_llm` and a distinct `llm`
- WHEN `answer(..., sufficiency_check=True, sufficiency_llm=...)` is called
- THEN the check prompt is sent to `sufficiency_llm` only and the synthesis
  prompt to `llm` only

#### Scenario: A refusal by the check client never reaches synthesis

- GIVEN the `sufficiency_llm` replies with the refusal sentinel
- WHEN `answer(..., sufficiency_check=True, sufficiency_llm=...)` is called
- THEN `llm` is not called and `no_match_cause` is `"insufficient_context"`

#### Scenario: A check model that is not installed degrades the check

- GIVEN the `sufficiency_llm` raises `BackendModelNotFound`
- WHEN `answer(..., sufficiency_check=True, sufficiency_llm=...)` is called
- THEN the answer is produced, `sufficiency_degraded` and
  `sufficiency_model_missing` are `True`

#### Scenario: An unreachable check server stays fatal

- GIVEN the `sufficiency_llm` raises `BackendUnavailable`
- WHEN `answer(..., sufficiency_check=True, sufficiency_llm=...)` is called
- THEN `BackendUnavailable` propagates

#### Scenario: A missing shared model stays fatal

- GIVEN no `sufficiency_llm` and `llm` raises `BackendModelNotFound`
- WHEN `answer(..., sufficiency_check=True)` is called
- THEN `BackendModelNotFound` propagates
