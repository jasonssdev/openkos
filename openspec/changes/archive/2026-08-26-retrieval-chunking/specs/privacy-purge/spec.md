# Delta for Privacy Purge

## MODIFIED Requirements

### Requirement: Deferred-Reembed Warning On Success

Because a successful purge deletes `.openkos/vectors.db` without rebuilding
it (per the Index Cleanup requirement), `purge`'s success output MUST
include a warning stating that dense retrieval is degraded until the index
is rebuilt, and MUST instruct the user to run `openkos reindex`. This MUST
be message-only: `purge` MUST NOT prompt interactively and MUST NOT
auto-run `reindex` itself.

That warning MUST also disclose what the rebuild costs (issue #698).
`vectors.db` holds BOTH the `vector_meta` content-hash cache and the `meta`
embedding-model tag, so dropping the file drops both: the restore is a FULL
re-embed of every surviving document, one embedding call each (now one
embedding call per CHUNK, per the `embedding-chunking` capability), never
an incremental top-up. Because the tag lived in the dropped store, the
NEXT `openkos reindex` run finds NO stored tag at all and takes reindex's
corrected disclosure's "no embedding-model tag stored (fresh or dropped
store)" branch — it MUST NOT take the "embedding model changed" branch,
because there is no old tag left to compare against a new one. The warning
MUST pre-empt THAT exact wording, naming it explicitly, so an operator
reading reindex's next output does not mistake the absent-tag disclosure
for a configuration change they did not make.

Preserving the model tag alone would NOT make the rebuild incremental, and
MUST NOT be offered as if it would: the vectors themselves are gone, so
every document must be embedded again whatever the tag says. Only carrying
the SURVIVORS' vectors into a fresh database would deliver incrementality,
which changes the delete-and-rebuild erasure posture above and is out of
scope for this requirement.
(Previously: this requirement stated that `reindex` would additionally
report the drop as `embedding model changed (unset -> <model>)`, and that
the warning must pre-empt THAT wording. The reindex-command capability's
corrected disclosure retires the bare-tag-vs-bare-model comparison that
produced that false claim: an absent stored tag now takes the distinct
"no embedding-model tag stored (fresh or dropped store)" branch, never the
model-changed branch, so this warning must pre-empt the corrected wording
instead.)

#### Scenario: Successful purge warns about degraded dense retrieval

- GIVEN a successful `openkos purge <concept-id>` run
- WHEN the command prints its success output
- THEN the output includes a warning that dense retrieval is degraded, an
  instruction to run `openkos reindex`, the fact that the restore is a full
  re-embed, and states that the next `reindex` run will report no stored
  embedding-model tag — NOT a model change — because `purge` dropped the
  store that held it

#### Scenario: No interactive prompt or auto-reindex occurs

- GIVEN a successful purge that deleted `.openkos/vectors.db`
- WHEN the command completes
- THEN `purge` does not prompt for confirmation to reindex and does not
  invoke `reindex` itself

#### Scenario: Purge's pre-emptive quoting matches reindex's corrected wording

- GIVEN a successful purge whose warning pre-empts the next `reindex` run's
  disclosure
- WHEN that warning text is inspected
- THEN it quotes or paraphrases the "no embedding-model tag stored (fresh
  or dropped store)" wording, and does NOT quote the retired
  `embedding model changed (unset -> <model>)` wording
