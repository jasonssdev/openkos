# Design: Settings — The AI Model And The Library

## Decisions

### D1. The change is text surgery on five top-level lines, verified by reading back

`openkos.yaml` is a committed, commented file, so the change never round-trips
it through a YAML serializer (that would drop every comment) and cannot reuse
`write_config` (exclusive create, template only). `config.set_model_settings`
edits only the column-0 lines `model:`, `embedding_model:`, `backend:`,
`base_url:` and `embedding_base_url:`, the same way `set_type_tier` edits its
block: an existing line keeps its separator and trailing comment and gets the
new value; a missing line is appended at the end; a value of `None` removes the
line. `backend: ollama` is the default, so an absent `backend:` line stays
absent when the target is `ollama`. A line it cannot edit confidently (two
lines for one key, a value followed by something that is not a comment) raises
`ValueError` and nothing is written.

The values are checked first by the existing validators (`validate_model`,
`validate_embedding_model`, and `_validate_base_url`, made public as
`validate_base_url`), and the rewritten text is then parsed by `parse_config`,
the body of `read_config` split from its file read, and its five fields compared
with the request. Every rule `read_config` enforces, including "an
`openai-compatible` backend needs a `base_url`" and "no API key in the file",
therefore holds for the written file by construction, and a shape the surgery
mis-edited fails before the write.

Rejected: a YAML round-trip (loses comments); a whole-file rewrite from the
template (loses every other setting); a new validator for URLs or tags (the
existing ones are the contract).

### D2. Two phases, keyed by a digest, one commit

The preview plans from the bytes of `openkos.yaml` and returns a
`ModelSettingsVersion` carrying the normalized change and a digest of the
change and those bytes, as the tier undo does (ADR-0067). The apply takes the
workspace lock once, plans again from the bytes it will write over, and writes
only when the digest matches; otherwise `ModelSettingsStale`. An `openkos.yaml`
with uncommitted edits is handled as for a tier (#1330): the commit holds only
this change applied to the committed file, and an uncommitted edit to one of
the five lines is refused (`Refused`) rather than swept into the commit.

### D3. No engine restart for a settings change

Every API operation reads `openkos.yaml` when it runs (`_wiring` builds each
client from the configuration just read), so the next ask or judging run uses
the new backend. Only the key needs a restart, because the engine reads its
environment once; the shell's `set_api_key`/`clear_api_key` already restart it
as a user request (ADR-0070). The screen therefore applies the settings first
and then, when a key was typed, sets the key, and reads the engine state again
afterwards.

### D4. "Not on this machine" uses the one locality authority

`ModelSettings.remote_hosts` lists the redacted display host of each resolved
endpoint (chat, then embedding when it differs) that
`llm.base.classify_backend_host` does not classify as local. Endpoints are
resolved by `backends.resolve_endpoint`, so `OLLAMA_HOST` counts for `ollama`
exactly as the client would use it. `is_local` is true exactly when the list is
empty. This is the verdict that decides whether confidential pages may be sent,
so the warning, the indicator and the send rule cannot disagree.

### D5. Search rebuild is reported, not performed

Stored vectors are tagged with `state.reindex.embedding_tag(model, backend)`.
When the tag differs before and after the change, the preview sets
`search_rebuild` and the screen says so. Rebuilding stays with the existing
maintenance path.

### D6. The screen

`desktop/ui/settings/` follows the setup screen's shape (ADR-0066): `model.ts`
is a DOM-free view model over injected ports (the API client, the key commands,
the workspace commands, the engine state), `wording.ts` holds every sentence,
`render.ts` draws from state with the design system's components. Turning a
cloud model on from a local setup shows the warning in a dialog with explicit
confirm and cancel; switching back to the computer needs no warning. The key
field is write-only: the screen shows "Key saved" or "No key" from
`api_key_status` and never reads a key back. The sidebar gets a quiet
"Settings" item at the bottom and, while `is_local` is false, a quiet line
"Cloud model on" that opens Settings.

## Flow

```
Screen            Local API                    Shell
  | GET settings/model  ->|                       |
  |<- ModelSettings ------|                       |
  | (cloud chosen, warning confirmed)             |
  | POST preview  ------->|                       |
  |<- preview + version --|                       |
  | POST apply(version) ->| lock, re-plan, write, |
  |<- changed ------------| commit                |
  | set_api_key(key) -------------------------->  | keychain, restart engine
  | engine_state ------------------------------>  | (new port and token)
  | GET settings/model (new client) ->|           |
```
