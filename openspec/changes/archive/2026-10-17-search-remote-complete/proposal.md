# Search stops reading as behind once only withheld pages remain (#1529)

With an AI model for search that is not on this machine, confidential pages are
never sent, so the stored embedding tag kept the previous model's value and
`search_index` reported `model_changed` forever; the app kept offering a
rebuild that could send nothing more.

Decision: the reindex run records a separate remote-complete tag in the vector
store's `meta` when the only pages left are withheld confidential ones. The
stored model tag is untouched (it still forces a full re-embed when an AI model
on this machine returns), and `search_index` does not report `model_changed`
for a configured tag equal to the remote-complete tag. No API field is added:
`SearchIndex` is unchanged within `API_VERSION` 1; the app's after-rebuild line
already says the withheld pages are found only by their words.

Specs: `reindex-command` (ADDED scenarios) and `stable-python-api` (MODIFIED
requirement), merged into the main specs.
