# Backend Selection Specification (delta)

## ADDED Requirements

### Requirement: The Chat Resolver Forwards A Usage Sink Only When Given

`application/backends.py::chat_client` MUST accept an optional `usage_sink` and,
WHEN it is given, pass it as the `usage_sink` keyword to whichever client
factory it dispatches to (`ollama` or `openai-compatible`). WHEN it is omitted
the factory MUST receive exactly the keyword arguments it received before this
option existed, so every other verb builds an identical client.

#### Scenario: A given sink reaches the factory

- GIVEN `chat_client(cfg, factories=..., usage_sink=sink)`
- WHEN the factory is called
- THEN it receives `usage_sink=sink`

#### Scenario: An omitted sink adds no keyword

- GIVEN `chat_client(cfg, factories=...)` without a sink
- WHEN the factory is called
- THEN its keyword arguments contain no `usage_sink`
