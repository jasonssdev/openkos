//! The inbox watch: a second sidecar, `openkos daemon`, run for the current
//! workspace only while the person turned the watch on (ADR-0078).
//!
//! The watch is a job of the daemon and nothing else, so turning it on means
//! running the whole daemon: it also retries failed auto-commits and runs the
//! maintenance pass (Settings says so). This module reuses the engine's
//! process handling (own process group, stdin as the lifeline, kill on a
//! missed grace, restart within a bounded budget) but not its handshake: a
//! daemon announces nothing, so it counts as on once it is spawned.
//!
//! Off is the default and a daemon is never started because an `inbox:` key
//! exists. The person's choice is remembered per workspace in the shell's own
//! configuration directory ([`WatchChoice`]), so reopening the app resumes
//! exactly what was turned on, for exactly that workspace. The watch stops
//! when the app quits and when the workspace changes; it starts again on the
//! new workspace only if the person turned it on there.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::engine::{self, EngineCommand, EngineEnv, RunningDaemon};
use crate::supervisor::{RestartBudget, RestartPolicy, Unlocated};

/// What the shell knows about its daemon, as the web view reads it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum WatchState {
    /// Not running because the person has not turned it on, or turned it off.
    Off,
    Starting {
        attempt: u32,
    },
    On {
        attempt: u32,
        pid: u32,
    },
    Restarting {
        attempt: u32,
        delay_ms: u64,
        reason: String,
        detail: String,
    },
    /// The restart budget is spent, or the daemon could not be formed.
    Failed {
        reason: String,
        detail: String,
    },
    /// The watch is on but no workspace is chosen yet.
    NoWorkspace,
}

/// Timings of one watch supervisor.
#[derive(Clone, Copy, Debug)]
pub struct WatchConfig {
    pub policy: RestartPolicy,
    /// How long a stop waits for the work in progress (an import mid-commit)
    /// before the process group is killed.
    pub stop_grace: Duration,
    pub poll_interval: Duration,
}

impl Default for WatchConfig {
    fn default() -> Self {
        Self {
            policy: RestartPolicy::default(),
            stop_grace: Duration::from_secs(20),
            poll_interval: Duration::from_millis(250),
        }
    }
}

/// Locates the daemon afresh before each start.
pub type Locate = dyn Fn() -> Result<EngineCommand, Unlocated> + Send + Sync;

/// The extra environment of each start, read afresh before each one.
pub type Environ = dyn Fn() -> EngineEnv + Send + Sync;

/// Told, once, that the daemon ended for good: `(reason, detail)`. The shell
/// raises its notification from this.
pub type OnFailed = dyn Fn(&str, &str) + Send + Sync;

struct Control {
    state: WatchState,
    /// The person wants the watch on: the daemon is (re)started until false.
    wanted: bool,
    /// The supervision thread is still deciding.
    running: bool,
}

struct Shared {
    control: Mutex<Control>,
    changed: Condvar,
}

impl Shared {
    fn lock(&self) -> std::sync::MutexGuard<'_, Control> {
        self.control.lock().expect("control lock")
    }

    fn publish(&self, state: WatchState) {
        self.lock().state = state;
        self.changed.notify_all();
    }

    fn wanted(&self) -> bool {
        self.lock().wanted
    }

    /// Sleep up to `delay`, or until the watch is turned off. `true` when it
    /// was turned off.
    fn wait_unwanted(&self, delay: Duration) -> bool {
        let deadline = Instant::now() + delay;
        let mut guard = self.lock();
        loop {
            if !guard.wanted {
                return true;
            }
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return false;
            }
            guard = self
                .changed
                .wait_timeout(guard, left)
                .expect("control lock")
                .0;
        }
    }

    /// Publish a final state and end supervision.
    fn finish(&self, state: WatchState) {
        let mut control = self.lock();
        control.state = state;
        control.running = false;
        self.changed.notify_all();
    }
}

struct Launch {
    locate: Box<Locate>,
    environ: Box<Environ>,
    on_failed: Box<OnFailed>,
    config: WatchConfig,
}

/// The daemon supervisor: idle until [`WatchSupervisor::enable`].
pub struct WatchSupervisor {
    shared: Arc<Shared>,
    launch: Arc<Launch>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl WatchSupervisor {
    pub fn new(
        locate: Box<Locate>,
        environ: Box<Environ>,
        on_failed: Box<OnFailed>,
        config: WatchConfig,
    ) -> Self {
        Self {
            shared: Arc::new(Shared {
                control: Mutex::new(Control {
                    state: WatchState::Off,
                    wanted: false,
                    running: false,
                }),
                changed: Condvar::new(),
            }),
            launch: Arc::new(Launch {
                locate,
                environ,
                on_failed,
                config,
            }),
            thread: Mutex::new(None),
        }
    }

    /// Turn the watch on for the workspace the locator names now. A watch
    /// that is already running is left alone; one that ended in a failure
    /// starts over.
    pub fn enable(&self) {
        let mut thread = self.thread.lock().expect("thread lock");
        {
            let mut control = self.shared.lock();
            control.wanted = true;
            if control.running {
                return;
            }
            control.running = true;
            control.state = WatchState::Starting { attempt: 1 };
            self.shared.changed.notify_all();
        }
        if let Some(old) = thread.take() {
            let _ = old.join();
        }
        let shared = Arc::clone(&self.shared);
        let launch = Arc::clone(&self.launch);
        *thread = Some(
            std::thread::Builder::new()
                .name("watch-supervisor".into())
                .spawn(move || supervise(&shared, &launch))
                .expect("spawn the watch supervision thread"),
        );
    }

    /// Turn the watch off: the daemon is stopped (its work in progress
    /// finishes, within the grace) and not started again. Returns once it is
    /// gone. Idempotent.
    pub fn disable(&self) {
        let mut thread = self.thread.lock().expect("thread lock");
        {
            let mut control = self.shared.lock();
            control.wanted = false;
            self.shared.changed.notify_all();
        }
        if let Some(thread) = thread.take() {
            let _ = thread.join();
        }
        self.shared.lock().state = WatchState::Off;
    }

    /// The current state.
    pub fn state(&self) -> WatchState {
        self.shared.lock().state.clone()
    }

    /// Wait until `accept` holds for the state, up to `timeout`.
    pub fn wait_for(
        &self,
        timeout: Duration,
        accept: impl Fn(&WatchState) -> bool,
    ) -> Option<WatchState> {
        let deadline = Instant::now() + timeout;
        let mut guard = self.shared.lock();
        loop {
            if accept(&guard.state) {
                return Some(guard.state.clone());
            }
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return None;
            }
            guard = self
                .shared
                .changed
                .wait_timeout(guard, left)
                .expect("control lock")
                .0;
        }
    }
}

impl Drop for WatchSupervisor {
    fn drop(&mut self) {
        self.disable();
    }
}

fn fail(shared: &Shared, launch: &Launch, reason: String, detail: String) {
    shared.finish(WatchState::Failed {
        reason: reason.clone(),
        detail: detail.clone(),
    });
    (launch.on_failed)(&reason, &detail);
}

/// The supervision loop: start, watch, restart within the budget, stop.
fn supervise(shared: &Shared, launch: &Launch) {
    let config = launch.config;
    let mut budget = RestartBudget::new(config.policy);
    let mut attempt = 0u32;
    loop {
        if !shared.wanted() {
            shared.finish(WatchState::Off);
            return;
        }
        attempt += 1;
        shared.publish(WatchState::Starting { attempt });
        let command = match (launch.locate)() {
            Ok(command) => command,
            Err(Unlocated::NoWorkspace) => {
                shared.finish(WatchState::NoWorkspace);
                return;
            }
            Err(Unlocated::Failed(detail)) => {
                fail(shared, launch, "daemon_not_located".into(), detail);
                return;
            }
        };
        let (reason, detail) = match engine::start_daemon(&command, &(launch.environ)()) {
            Ok(running) => match watch(shared, running, attempt, config) {
                Ended::Exited(reason, detail) => (reason, detail),
                Ended::TurnedOff => {
                    shared.finish(WatchState::Off);
                    return;
                }
            },
            Err(error) => (
                "spawn_failed".to_owned(),
                format!("the daemon could not be started: {error}"),
            ),
        };
        match budget.on_failure(Instant::now()) {
            Some(delay) => {
                shared.publish(WatchState::Restarting {
                    attempt,
                    delay_ms: u64::try_from(delay.as_millis()).unwrap_or(u64::MAX),
                    reason,
                    detail,
                });
                if shared.wait_unwanted(delay) {
                    shared.finish(WatchState::Off);
                    return;
                }
            }
            None => {
                fail(shared, launch, reason, detail);
                return;
            }
        }
    }
}

enum Ended {
    /// It exited on its own: why, for the budget. A daemon that was not asked
    /// to stop never exits cleanly, so any exit is a failure.
    Exited(String, String),
    TurnedOff,
}

fn watch(shared: &Shared, mut running: RunningDaemon, attempt: u32, config: WatchConfig) -> Ended {
    shared.publish(WatchState::On {
        attempt,
        pid: running.pid(),
    });
    loop {
        if let Some(status) = running.try_exited() {
            let detail = format!("the daemon exited ({status}).\n{}", running.stderr_tail());
            running.stop(config.stop_grace);
            return Ended::Exited("exited".into(), detail);
        }
        if shared.wait_unwanted(config.poll_interval) {
            running.stop(config.stop_grace);
            return Ended::TurnedOff;
        }
    }
}

/// The file, in the shell's configuration directory, holding the choice.
pub const CHOICE_FILE: &str = "watch.json";

/// The workspaces the person turned the watch on for: one small JSON file in
/// the shell's own configuration directory, never inside a workspace.
pub struct WatchChoice {
    file: PathBuf,
    serial: Mutex<()>,
}

#[derive(Default, Deserialize, Serialize)]
struct ChoiceFile {
    workspaces: VecDeque<PathBuf>,
}

impl WatchChoice {
    pub fn new(config_dir: &Path) -> Self {
        Self {
            file: config_dir.join(CHOICE_FILE),
            serial: Mutex::new(()),
        }
    }

    fn read(&self) -> ChoiceFile {
        match std::fs::read_to_string(&self.file) {
            Ok(text) => serde_json::from_str(&text).unwrap_or_else(|_| {
                eprintln!(
                    "openkos-desktop: {} is malformed; the watch stays off",
                    self.file.display()
                );
                ChoiceFile::default()
            }),
            Err(_) => ChoiceFile::default(),
        }
    }

    /// Whether the person turned the watch on for `workspace`. A missing,
    /// unreadable or malformed file is off: nothing is started on a guess.
    pub fn is_on(&self, workspace: &Path) -> bool {
        self.read().workspaces.iter().any(|w| w == workspace)
    }

    /// Remember that the watch is `on` for `workspace`. Written to a temporary
    /// file and renamed, so a crash never leaves half a choice.
    pub fn set(&self, workspace: &Path, on: bool) -> std::io::Result<()> {
        let _serial = self.serial.lock().expect("watch choice lock");
        let mut file = self.read();
        file.workspaces.retain(|w| w != workspace);
        if on {
            file.workspaces.push_back(workspace.to_path_buf());
        }
        if let Some(dir) = self.file.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let body = serde_json::to_string(&file).map_err(std::io::Error::other)?;
        let partial = self.file.with_extension("json.partial");
        std::fs::write(&partial, body)?;
        std::fs::rename(&partial, &self.file)
    }
}

/// What the page learns about the watch: the daemon's state, and whether the
/// person turned the watch on for the current workspace (the daemon may be
/// stopped, failed or waiting while this stays true).
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct WatchStatus {
    #[serde(flatten)]
    pub state: WatchState,
    pub wanted: bool,
}

/// The watch as the shell uses it: the person's choice per workspace, and the
/// daemon that carries it out for the current one.
pub struct Watch {
    choice: WatchChoice,
    supervisor: WatchSupervisor,
}

impl Watch {
    pub fn new(choice: WatchChoice, supervisor: WatchSupervisor) -> Self {
        Self { choice, supervisor }
    }

    /// The person turns the watch `on` or off for `workspace`, the current
    /// one. The choice is written first, so a choice that cannot be kept
    /// starts nothing.
    pub fn turn(&self, workspace: &Path, on: bool) -> std::io::Result<()> {
        self.choice.set(workspace, on)?;
        if on {
            self.supervisor.enable();
        } else {
            self.supervisor.disable();
        }
        Ok(())
    }

    /// The current workspace is now `workspace` (or none), at launch or after
    /// the person opened another: the daemon of the previous one is stopped,
    /// and one starts on the new one only if the person turned the watch on
    /// there.
    pub fn follow_workspace(&self, workspace: Option<&Path>) {
        self.supervisor.disable();
        if let Some(workspace) = workspace
            && self.choice.is_on(workspace)
        {
            self.supervisor.enable();
        }
    }

    pub fn status(&self, workspace: Option<&Path>) -> WatchStatus {
        WatchStatus {
            state: self.supervisor.state(),
            wanted: workspace.is_some_and(|w| self.choice.is_on(w)),
        }
    }

    /// Stop the daemon: the app is quitting.
    pub fn shutdown(&self) {
        self.supervisor.disable();
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use crate::engine::fake;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn fast(max_restarts: u32) -> WatchConfig {
        WatchConfig {
            policy: RestartPolicy {
                max_restarts,
                window: Duration::from_secs(60),
                initial_backoff: Duration::from_millis(20),
                max_backoff: Duration::from_millis(40),
            },
            stop_grace: Duration::from_secs(5),
            poll_interval: Duration::from_millis(10),
        }
    }

    const WAIT: Duration = Duration::from_secs(10);

    fn is_on(s: &WatchState) -> bool {
        matches!(s, WatchState::On { .. })
    }

    fn is_failed(s: &WatchState) -> bool {
        matches!(s, WatchState::Failed { .. })
    }

    /// A supervisor over `command`, with a counter of the failures it raised.
    fn supervised(
        command: EngineCommand,
        config: WatchConfig,
    ) -> (WatchSupervisor, Arc<AtomicUsize>, Arc<Mutex<Vec<String>>>) {
        let raised = Arc::new(AtomicUsize::new(0));
        let reasons = Arc::new(Mutex::new(Vec::new()));
        let (count, seen) = (Arc::clone(&raised), Arc::clone(&reasons));
        let sup = WatchSupervisor::new(
            Box::new(move || Ok(command.clone())),
            Box::new(EngineEnv::default),
            Box::new(move |reason, detail| {
                count.fetch_add(1, Ordering::SeqCst);
                seen.lock()
                    .expect("seen")
                    .push(format!("{reason}: {detail}"));
            }),
            config,
        );
        (sup, raised, reasons)
    }

    /// Records its pid, then lives until its stdin closes, as the real daemon
    /// does on the lifeline, and exits 0.
    fn lifeline(dir: &Path) -> EngineCommand {
        fake::engine("echo $$ >> \"$1/pids\"; cat > /dev/null; exit 0", dir)
    }

    fn pids(dir: &Path) -> Vec<u32> {
        std::fs::read_to_string(dir.join("pids"))
            .unwrap_or_default()
            .lines()
            .filter_map(|l| l.trim().parse().ok())
            .collect()
    }

    #[test]
    fn nothing_runs_until_the_watch_is_turned_on() {
        let dir = fake::scratch("watch-off");
        let (sup, _, _) = supervised(lifeline(&dir), fast(3));
        assert_eq!(sup.state(), WatchState::Off);
        assert!(pids(&dir).is_empty());
        sup.disable();
        assert_eq!(sup.state(), WatchState::Off);
    }

    #[test]
    fn turning_it_on_runs_the_daemon_and_turning_it_off_stops_it() {
        let dir = fake::scratch("watch-on-off");
        let (sup, raised, _) = supervised(lifeline(&dir), fast(3));
        sup.enable();
        let WatchState::On { pid, attempt } = sup.wait_for(WAIT, is_on).expect("on") else {
            unreachable!()
        };
        assert_eq!(attempt, 1);
        assert!(fake::alive(pid));
        sup.disable();
        assert_eq!(sup.state(), WatchState::Off);
        assert!(fake::gone_within(pid, WAIT));
        assert_eq!(raised.load(Ordering::SeqCst), 0, "a stop is not a failure");
    }

    #[test]
    fn turning_it_on_twice_runs_one_daemon() {
        let dir = fake::scratch("watch-twice");
        let (sup, _, _) = supervised(lifeline(&dir), fast(3));
        sup.enable();
        sup.wait_for(WAIT, is_on).expect("on");
        sup.enable();
        std::thread::sleep(Duration::from_millis(200));
        assert_eq!(pids(&dir).len(), 1);
        sup.disable();
    }

    #[test]
    fn turning_it_off_and_on_again_runs_a_fresh_daemon_on_the_current_workspace() {
        let dir = fake::scratch("watch-switch");
        let (sup, _, _) = supervised(lifeline(&dir), fast(3));
        sup.enable();
        let WatchState::On { pid, .. } = sup.wait_for(WAIT, is_on).expect("on") else {
            unreachable!()
        };
        sup.disable();
        sup.enable();
        let WatchState::On { pid: second, .. } = sup.wait_for(WAIT, is_on).expect("on again")
        else {
            unreachable!()
        };
        assert_ne!(pid, second);
        assert!(fake::gone_within(pid, WAIT));
        sup.disable();
    }

    #[test]
    fn a_crashed_daemon_is_restarted_without_telling_anyone() {
        let dir = fake::scratch("watch-crash");
        let (sup, raised, _) = supervised(lifeline(&dir), fast(3));
        sup.enable();
        let WatchState::On { pid, .. } = sup.wait_for(WAIT, is_on).expect("on") else {
            unreachable!()
        };
        unsafe { libc::kill(pid as libc::pid_t, libc::SIGKILL) };
        let second = sup
            .wait_for(WAIT, |s| matches!(s, WatchState::On { attempt: 2, .. }))
            .expect("restarted");
        let WatchState::On { pid: pid2, .. } = second else {
            unreachable!()
        };
        assert_ne!(pid, pid2);
        assert_eq!(raised.load(Ordering::SeqCst), 0);
        sup.disable();
    }

    #[test]
    fn restarts_are_bounded_then_the_failure_is_raised_once_with_the_daemons_last_words() {
        let dir = fake::scratch("watch-bounded");
        let script =
            "echo x >> \"$1/starts\"; echo 'openkos daemon: failed -- OSError.' >&2; exit 1";
        let (sup, raised, reasons) = supervised(fake::engine(script, &dir), fast(2));
        sup.enable();
        let WatchState::Failed { reason, detail } = sup.wait_for(WAIT, is_failed).expect("failed")
        else {
            unreachable!()
        };
        assert_eq!(reason, "exited");
        assert!(detail.contains("failed -- OSError"), "{detail}");
        let starts = std::fs::read_to_string(dir.join("starts")).expect("starts");
        assert_eq!(starts.lines().count(), 3, "one start plus two restarts");
        assert_eq!(raised.load(Ordering::SeqCst), 1);
        assert!(reasons.lock().expect("seen")[0].starts_with("exited: "));
        sup.disable();
        assert_eq!(
            sup.state(),
            WatchState::Off,
            "the person turning it off clears it"
        );
    }

    #[test]
    fn a_failed_watch_starts_over_when_turned_on_again() {
        let dir = fake::scratch("watch-retry");
        let (sup, _, _) = supervised(
            fake::engine("echo x >> \"$1/starts\"; exit 1", &dir),
            fast(0),
        );
        sup.enable();
        sup.wait_for(WAIT, is_failed).expect("failed");
        sup.enable();
        sup.wait_for(WAIT, is_failed).expect("failed again");
        let starts = std::fs::read_to_string(dir.join("starts")).expect("starts");
        assert_eq!(starts.lines().count(), 2);
        sup.disable();
    }

    #[test]
    fn a_daemon_that_exits_cleanly_unasked_is_still_a_failure() {
        let dir = fake::scratch("watch-clean-exit");
        let (sup, raised, _) = supervised(fake::engine("exit 0", &dir), fast(0));
        sup.enable();
        sup.wait_for(WAIT, is_failed).expect("failed");
        assert_eq!(raised.load(Ordering::SeqCst), 1);
        sup.disable();
    }

    #[test]
    fn no_workspace_yet_is_its_own_state_and_spawns_nothing() {
        let sup = WatchSupervisor::new(
            Box::new(|| Err(Unlocated::NoWorkspace)),
            Box::new(EngineEnv::default),
            Box::new(|_, _| panic!("not a failure")),
            fast(3),
        );
        sup.enable();
        assert_eq!(
            sup.wait_for(WAIT, |s| matches!(s, WatchState::NoWorkspace)),
            Some(WatchState::NoWorkspace)
        );
        sup.disable();
    }

    #[test]
    fn a_locator_failure_is_final_and_raised() {
        let raised = Arc::new(AtomicUsize::new(0));
        let count = Arc::clone(&raised);
        let sup = WatchSupervisor::new(
            Box::new(|| Err(Unlocated::Failed("the workspace is missing".to_owned()))),
            Box::new(EngineEnv::default),
            Box::new(move |_, _| {
                count.fetch_add(1, Ordering::SeqCst);
            }),
            fast(3),
        );
        sup.enable();
        let state = sup.wait_for(WAIT, is_failed).expect("failed");
        assert_eq!(
            state,
            WatchState::Failed {
                reason: "daemon_not_located".into(),
                detail: "the workspace is missing".into(),
            }
        );
        assert_eq!(raised.load(Ordering::SeqCst), 1);
        sup.disable();
    }

    #[test]
    fn turning_it_off_during_a_restart_delay_stops_promptly() {
        let dir = fake::scratch("watch-delay");
        let mut config = fast(3);
        config.policy.initial_backoff = Duration::from_secs(30);
        config.policy.max_backoff = Duration::from_secs(30);
        let (sup, _, _) = supervised(fake::engine("exit 3", &dir), config);
        sup.enable();
        sup.wait_for(WAIT, |s| matches!(s, WatchState::Restarting { .. }))
            .expect("restarting");
        let began = Instant::now();
        sup.disable();
        assert!(began.elapsed() < Duration::from_secs(5));
        assert_eq!(sup.state(), WatchState::Off);
    }

    #[test]
    fn dropping_the_supervisor_stops_the_daemon() {
        let dir = fake::scratch("watch-drop");
        let (sup, _, _) = supervised(lifeline(&dir), fast(3));
        sup.enable();
        let WatchState::On { pid, .. } = sup.wait_for(WAIT, is_on).expect("on") else {
            unreachable!()
        };
        drop(sup);
        assert!(fake::gone_within(pid, WAIT));
    }

    #[test]
    fn each_start_reads_the_extra_environment_afresh() {
        let dir = fake::scratch("watch-env");
        let script =
            "printf '%s\\n' \"${OPENKOS_OPENAI_API_KEY-unset}\" >> \"$1/keys\"; cat > /dev/null";
        let command = fake::engine(script, &dir);
        let key = Arc::new(Mutex::new(None::<String>));
        let read = Arc::clone(&key);
        let sup = WatchSupervisor::new(
            Box::new(move || Ok(command.clone())),
            Box::new(move || {
                let mut env = EngineEnv::default();
                if let Some(value) = read.lock().expect("key").as_deref() {
                    env.set("OPENKOS_OPENAI_API_KEY", value);
                }
                env
            }),
            Box::new(|_, _| {}),
            fast(3),
        );
        sup.enable();
        sup.wait_for(WAIT, is_on).expect("on");
        sup.disable();
        *key.lock().expect("key") = Some("sk-new".to_owned());
        sup.enable();
        sup.wait_for(WAIT, is_on).expect("on again");
        sup.disable();
        let keys = std::fs::read_to_string(dir.join("keys")).expect("keys");
        assert_eq!(keys.lines().collect::<Vec<_>>(), ["unset", "sk-new"]);
    }

    #[test]
    fn the_lifeline_ends_a_daemon_that_honours_it_and_the_grace_kills_one_that_does_not() {
        let dir = fake::scratch("watch-lifeline");
        let polite = engine::start_daemon(&lifeline(&dir), &EngineEnv::default()).expect("start");
        assert_eq!(
            polite.stop(Duration::from_secs(5)),
            engine::StopOutcome::Graceful
        );

        let stubborn = fake::engine(
            "echo $$ > \"$1/stubborn\"; trap '' TERM HUP; while :; do sleep 1; done",
            &dir,
        );
        let running = engine::start_daemon(&stubborn, &EngineEnv::default()).expect("start");
        let pid = fake::read_pid(&dir.join("stubborn"), WAIT);
        assert_eq!(
            running.stop(Duration::from_millis(200)),
            engine::StopOutcome::Killed
        );
        assert!(fake::gone_within(pid, WAIT));
    }

    #[test]
    fn the_states_serialise_for_the_web_view() {
        assert_eq!(
            serde_json::to_value(WatchState::Off).expect("json"),
            serde_json::json!({"state": "off"})
        );
        assert_eq!(
            serde_json::to_value(WatchState::On { attempt: 1, pid: 7 }).expect("json"),
            serde_json::json!({"state": "on", "attempt": 1, "pid": 7})
        );
        assert_eq!(
            serde_json::to_value(WatchState::NoWorkspace).expect("json"),
            serde_json::json!({"state": "no_workspace"})
        );
    }

    #[test]
    fn the_choice_is_off_until_the_person_turns_it_on_for_that_workspace() {
        let dir = fake::scratch("watch-choice");
        let choice = WatchChoice::new(&dir);
        let (a, b) = (Path::new("/work/a"), Path::new("/work/b"));
        assert!(!choice.is_on(a));
        choice.set(a, true).expect("set");
        assert!(choice.is_on(a));
        assert!(!choice.is_on(b), "another workspace stays off");
        assert!(WatchChoice::new(&dir).is_on(a), "it survives a restart");
        choice.set(a, true).expect("set again");
        choice.set(a, false).expect("unset");
        assert!(!choice.is_on(a));
    }

    #[test]
    fn a_malformed_choice_file_is_off() {
        let dir = fake::scratch("watch-choice-bad");
        std::fs::write(dir.join(CHOICE_FILE), "{not json").expect("write");
        assert!(!WatchChoice::new(&dir).is_on(Path::new("/work/a")));
    }

    fn watch_over(command: EngineCommand, dir: &Path) -> Watch {
        let (supervisor, _, _) = supervised(command, fast(3));
        Watch::new(WatchChoice::new(&dir.join("config")), supervisor)
    }

    #[test]
    fn the_person_turning_it_on_remembers_the_choice_and_runs_the_daemon() {
        let dir = fake::scratch("watch-turn");
        let watch = watch_over(lifeline(&dir), &dir);
        let ws = Path::new("/work/a");
        assert!(!watch.status(Some(ws)).wanted);
        watch.turn(ws, true).expect("turn on");
        watch.supervisor.wait_for(WAIT, is_on).expect("on");
        assert!(watch.status(Some(ws)).wanted);
        watch.turn(ws, false).expect("turn off");
        assert_eq!(watch.status(Some(ws)).state, WatchState::Off);
        assert!(!watch.status(Some(ws)).wanted);
    }

    #[test]
    fn a_choice_that_cannot_be_kept_starts_nothing() {
        let dir = fake::scratch("watch-unkept");
        // The configuration directory is a file, so the choice cannot be written.
        std::fs::write(dir.join("config"), "").expect("file");
        let watch = watch_over(lifeline(&dir), &dir);
        assert!(watch.turn(Path::new("/work/a"), true).is_err());
        assert_eq!(watch.status(None).state, WatchState::Off);
        assert!(pids(&dir).is_empty());
    }

    #[test]
    fn switching_workspace_stops_the_daemon_and_restarts_it_only_where_it_was_turned_on() {
        let dir = fake::scratch("watch-follow");
        let watch = watch_over(lifeline(&dir), &dir);
        let (a, b) = (Path::new("/work/a"), Path::new("/work/b"));
        watch.turn(a, true).expect("on for a");
        let WatchState::On { pid, .. } = watch.supervisor.wait_for(WAIT, is_on).expect("on") else {
            unreachable!()
        };
        watch.follow_workspace(Some(b));
        assert_eq!(watch.status(Some(b)).state, WatchState::Off);
        assert!(fake::gone_within(pid, WAIT), "a's daemon was stopped");
        watch.follow_workspace(Some(a));
        let WatchState::On { pid: again, .. } = watch
            .supervisor
            .wait_for(WAIT, is_on)
            .expect("on for a again")
        else {
            unreachable!()
        };
        assert_ne!(pid, again);
        watch.follow_workspace(None);
        assert_eq!(watch.status(None).state, WatchState::Off);
        assert!(fake::gone_within(again, WAIT));
    }

    #[test]
    fn launching_with_nothing_turned_on_starts_nothing() {
        let dir = fake::scratch("watch-launch");
        let watch = watch_over(lifeline(&dir), &dir);
        watch.follow_workspace(Some(Path::new("/work/a")));
        assert_eq!(watch.status(None).state, WatchState::Off);
        assert!(pids(&dir).is_empty());
    }

    #[test]
    fn the_status_flattens_the_state_beside_the_choice() {
        let status = WatchStatus {
            state: WatchState::On { attempt: 1, pid: 7 },
            wanted: true,
        };
        assert_eq!(
            serde_json::to_value(&status).expect("json"),
            serde_json::json!({"state": "on", "attempt": 1, "pid": 7, "wanted": true})
        );
    }
}
