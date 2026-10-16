# Delta for Desktop Shell

## MODIFIED Requirements

### Requirement: The Web View Reads The Engine State And Calls The Local API

The shell MUST expose the engine's state to the web view through one command,
`engine_state`, as a JSON object tagged by `state`: `starting` (with `attempt`),
`ready` (with `attempt`, `base_url`, `token` and `pid`), `restarting` (with
`attempt`, `delay_ms`, `reason` and `detail`), `failed` (with `reason` and
`detail`) or `stopped`. The page MUST show the engine's state in English and,
once per ready engine, MUST read the workspace status through the desktop
client created from that ready state (`GET <base_url>/status` with
`Authorization: Bearer <token>`) and show the workspace's folder, source count,
concept count and number of open decisions. When the read fails the page MUST
say so in English without showing the token. The page MUST write engine and
workspace values as text, never as markup. The page MUST be built from
`desktop/ui/` into `desktop/dist/`, the shell's `frontendDist`. The web view's
content security policy MUST restrict connections to the shell's IPC and
`http://127.0.0.1` ports.

(Previously: the page was a static file that called `GET <base_url>/status`
itself and was served from `desktop/ui/` with no build step.)

#### Scenario: The status page reads the workspace through the engine

- GIVEN the shell started the real engine of the checkout on a fresh workspace
- WHEN a request carries the web view's origin `tauri://localhost` and the token
- THEN the CORS preflight for `GET /v1/status` is answered 204
- AND `GET /v1/status` is answered 200 with the workspace's counts
- AND the same request without the token is answered 401

#### Scenario: The page words a failed read without the token

- GIVEN a ready engine whose status read fails with an error envelope, a body that is not the envelope, or no connection
- WHEN the page shows the failure
- THEN it shows an English sentence saying the status could not be read
- AND the sentence does not contain the token
