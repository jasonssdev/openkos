# OpenAI-Compatible Client Specification (delta)

## ADDED Requirements

### Requirement: Transient Chat Failures Are Retried Before Propagating

`chat(messages)` MUST retry a transient failure a bounded number of times with
exponential backoff before raising. A failure is transient exactly when it is
an HTTP 429, 500, 502, 503 or 504 response, or a transport failure that
occurred while connecting, before any response began (the connection was
refused or reset). The total number of attempts is `chat_retry_attempts`
(default 3, the initial attempt included); the pause after attempt `n` is
`chat_retry_backoff_base * 2 ** (n - 1)` seconds (default base 0.5). A retried
attempt that succeeds MUST be transparent to the caller. When the budget is
exhausted `chat()` MUST raise the same mapped error a single attempt would
have raised (`OpenAICompatibleError` for a status, `OpenAICompatibleUnavailable`
for a connect failure).

`chat()` MUST NOT retry: any other 4xx status; a redirect; a model-not-found
response; a generation cut off by the length cap; a malformed or unexpected
200 body; a request or connect timeout; or a failure while reading the response
body. Each of these MUST raise after exactly one attempt, because a timed-out
generation may still be running on the server and retrying it would multiply
the wall time of one call by the attempt count.

`chat()` MUST keep building everything as locals and writing nothing onto
`self`. No retry message, error text or log line MAY contain the API key.

#### Scenario: A 503 followed by a success is transparent

- GIVEN the server answers 503 on the first attempt and 200 on the second
- WHEN `chat(messages)` is called
- THEN it returns the assistant text, having paused once

#### Scenario: Exhausted budget raises the mapped error

- GIVEN the server answers 503 on every attempt
- WHEN `chat(messages)` is called with the default budget
- THEN it makes 3 attempts, pauses 0.5 s and 1.0 s, and raises
  `OpenAICompatibleError` naming status 503

#### Scenario: Each transient status is retried

- GIVEN the server answers 429, 500, 502, 503 or 504 and then succeeds
- WHEN `chat(messages)` is called
- THEN each status is retried and the call succeeds

#### Scenario: A connect-phase failure is retried

- GIVEN the connection is refused on the first attempt and succeeds later
- WHEN `chat(messages)` is called
- THEN it returns the assistant text

#### Scenario: A non-transient failure is not retried

- GIVEN the server answers 400, 401, 403, a model-not-found 404, a redirect,
  a length-capped 200, or a 200 with a malformed body
- WHEN `chat(messages)` is called
- THEN it raises after exactly one attempt and never pauses

#### Scenario: A timeout or a read-phase failure is not retried

- GIVEN the request times out, or the transport fails while the response body
  is being read
- WHEN `chat(messages)` is called
- THEN it raises `OpenAICompatibleUnavailable` after exactly one attempt

#### Scenario: The key never appears in a retried failure

- GIVEN an API key is configured and every attempt fails with 503
- WHEN `chat(messages)` raises after the budget
- THEN the error text does not contain the key

### Requirement: Retry-After Is Honoured And Capped

When a retryable HTTP response carries a `Retry-After` header whose value is a
non-negative integer number of seconds, `chat()` MUST pause that long instead
of the exponential backoff, capped at 30 seconds. A `Retry-After` that is
absent, negative, non-numeric or in HTTP-date form MUST be ignored, and the
exponential backoff applies.

#### Scenario: Retry-After seconds replace the backoff

- GIVEN the server answers 429 with `Retry-After: 2`, then succeeds
- WHEN `chat(messages)` is called
- THEN the single pause is 2 seconds

#### Scenario: A large Retry-After is capped

- GIVEN the server answers 503 with `Retry-After: 3600`, then succeeds
- WHEN `chat(messages)` is called
- THEN the single pause is 30 seconds

#### Scenario: A Retry-After in date form is ignored

- GIVEN the server answers 503 with `Retry-After: Wed, 21 Oct 2026 07:28:00 GMT`
- WHEN `chat(messages)` is called
- THEN the pause is the exponential backoff

## MODIFIED Requirements

### Requirement: Server Unavailable Raises A Typed Error

A connection refused or a request timeout MUST raise
`OpenAICompatibleUnavailable`, after the bounded retry described in
"Transient Chat Failures Are Retried Before Propagating" for a connect-phase
failure (a timeout is never retried). No raw `urllib.error.URLError`,
`socket.timeout`, or similar low-level exception MUST ever propagate to the
caller.

#### Scenario: Server not running raises OpenAICompatibleUnavailable

- GIVEN no server is reachable at the configured base URL
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleUnavailable` is raised once the retry budget is spent
  and no `URLError` escapes

#### Scenario: Request timeout raises OpenAICompatibleUnavailable

- GIVEN a configured, bounded request timeout
- WHEN the server does not respond within that timeout
- THEN `chat(messages)` raises `OpenAICompatibleUnavailable` after one attempt
  and does not hang indefinitely

### Requirement: Other Failures Raise A Generic Typed Error

Any other non-200 HTTP response, or a 200 response whose body is not valid
JSON or lacks the expected `choices[0].message.content` shape, MUST raise
`OpenAICompatibleError`. A 429, 500, 502, 503 or 504 response is raised only
after the bounded retry described in "Transient Chat Failures Are Retried
Before Propagating"; every other such response is raised after one attempt.

#### Scenario: Non-model-not-found server error raises OpenAICompatibleError

- GIVEN the server responds with a non-200 status not classified as a
  model-not-found response
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleError` is raised, carrying the server's error detail

#### Scenario: Malformed JSON response raises OpenAICompatibleError

- GIVEN the server responds 200 with a body that is not valid JSON or is
  missing `choices[0].message.content`
- WHEN `chat(messages)` is called
- THEN `OpenAICompatibleError` is raised rather than an unhandled parsing
  exception
