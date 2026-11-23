# Proposal: chat-transient-retry

Refs #1764 (part of #1770).

## Intent

`OpenAICompatibleClient.chat` makes exactly one attempt. Against a hosted API a
transient 429 or 503 is the normal failure mode, and a single one makes
`ingest` skip concept extraction and keep the Source only, exiting 0. Only
`embed()` retries. This gives `chat()` a bounded retry for transient failures
before it raises.

Success: a transient failure followed by a success is transparent to the
caller; when the budget is exhausted `chat()` raises the same mapped error as
today, so ingest's existing "keeping the Source only" degradation is unchanged.

## Scope

### In Scope

1. A bounded retry loop in `OpenAICompatibleClient.chat` with exponential
   backoff, on HTTP 429, 500, 502, 503 and 504, and on a connect-phase
   transport failure (the connection is refused or reset before any response).
2. `Retry-After` honoured when it is a non-negative integer number of seconds,
   capped at 30 seconds.
3. Constructor parameters `chat_retry_attempts` and
   `chat_retry_backoff_base`, reusing the existing `sleep` injection point.
4. The CHANGELOG entry.

### Out of Scope

- `OllamaClient` (local; this arc is about hosted backends).
- The exit-code question in #1764 (whether a run that skipped extraction after a
  backend failure should still exit 0). It stays open.
- A new `openkos.yaml` key and any new dependency.
- Parsing the HTTP-date form of `Retry-After`: it is treated as absent and the
  exponential backoff applies.

## Approach

`chat()` wraps one attempt (`_chat_once`) in a loop, the same shape as
`embed()`. A private marker exception carries the mapped error and the
server's `Retry-After` out of a retryable attempt; on exhaustion the mapped
error is raised unchanged. The loop reads its pause through a local
(`sleep = self._sleep`) and writes nothing onto `self`, so `chat()` keeps its
thread-safety contract.

## Deliberate deviation from the issue text

#1764 asks to retry "transport timeouts". This change does NOT retry a read
timeout, a failure while reading the response body, or a connect timeout. A
generation can legitimately run for minutes (`chat_timeout` is 600 s), so
retrying a timed-out request would multiply the worst-case wall time of one
extraction call by the attempt count, and a request the server already accepted
may still be running. Only a failure that proves no response began (connection
refused or reset while connecting) is retried.

## Risks

- Up to `chat_retry_attempts - 1` extra HTTP requests per call against a hosted
  API on a transient failure. Bounded (default 3 attempts) and the pauses are
  capped, so a hostile `Retry-After` cannot stall ingest beyond 30 s per pause.
- A 429 that signals a daily quota rather than a burst is retried and fails
  again; the cost is two short pauses, then the same error as today.
