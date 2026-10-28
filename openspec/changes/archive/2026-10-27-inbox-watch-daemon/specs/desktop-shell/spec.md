# Desktop Shell Specification (delta)

## ADDED Requirements

### Requirement: The Shell Supervises The Inbox Watch's Daemon Only While The Person Turned It On

The shell MUST supervise `openkos daemon --stop-on-stdin-eof`, the whole daemon,
as a second sidecar run in the current workspace, and only while the person
turned the watch on for that workspace. Off MUST be the default: no daemon MUST
start because an `unattended.inbox` key exists. The person's choice MUST be
remembered per workspace in the shell's own configuration directory, never in a
workspace, and a choice that cannot be kept MUST start nothing. The daemon's
stdin MUST stay open for its life as its lifeline, so it stops gracefully when
the shell dies by any route; a stop MUST wait for the work in progress up to a
grace period and then kill the daemon's process group.

The daemon MUST stop when the app quits, when the person turns the watch off, and
when the workspace changes; after a change it MUST start again only where the
person turned the watch on. A daemon that exits unasked, cleanly or not, MUST be
restarted within the bounded restart budget the engine has, and when the budget is
spent the state MUST be failed with the daemon's last stderr lines, raised once
through the supervisor's failure callback. The shell MUST NOT start a login item.

#### Scenario: Nothing runs until the watch is turned on

- GIVEN a workspace with an `unattended.inbox` and no remembered choice
- WHEN the app launches
- THEN no daemon is started and the watch state is off

#### Scenario: Switching workspace follows the choice

- GIVEN a daemon running for workspace A, and a watch turned on for A only
- WHEN the person opens workspace B
- THEN A's daemon is stopped and none starts for B

#### Scenario: A killed shell leaves no daemon

- GIVEN a running daemon
- WHEN the shell's end of its stdin closes
- THEN the daemon stops by itself

### Requirement: A Stopped Watch Raises A Notification With Fixed Text

The shell MUST raise a macOS notification when the watch's daemon fails for good
(from the supervisor's failure callback, once) and, when the page asks through
`notify_watch_budget`, when the daemon paused on the AI model's limit. The text
MUST be fixed in the shell, in the words of `docs/decision-wording.md`, and MUST
NOT carry the daemon's own output, which is logged to stderr only. A refusal to
show it (notifications turned off for the app) MUST NOT be an error. The
notification plugin MUST be a dependency pinned to an exact version and MUST be
used only from Rust: no capability MUST give the web view a notification
permission, and `notify_watch_budget` MUST take no text from the page.

#### Scenario: A failure raises one notification without the daemon's words

- GIVEN a daemon that exits until its restart budget is spent
- WHEN the failure callback runs
- THEN one notification is shown and its text holds none of the daemon's output

#### Scenario: The page cannot choose the text

- GIVEN the page invokes `notify_watch_budget`
- WHEN the shell handles it
- THEN the fixed budget notice is shown
