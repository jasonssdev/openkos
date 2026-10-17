//! Supervision: keep one engine running for the life of the shell.
//!
//! One thread owns the engine. It starts it, watches it, restarts it after a
//! crash within a bounded budget, and stops it when asked. Everyone else reads
//! a snapshot of [`EngineState`].

use std::collections::VecDeque;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::engine::{self, EngineCommand, EngineEnv, RunningEngine, Token};

/// How many crashes are restarted, and how fast.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RestartPolicy {
    /// Restarts allowed inside `window`; the next failure is final.
    pub max_restarts: u32,
    pub window: Duration,
    pub initial_backoff: Duration,
    pub max_backoff: Duration,
}

impl Default for RestartPolicy {
    fn default() -> Self {
        Self {
            max_restarts: 3,
            window: Duration::from_secs(60),
            initial_backoff: Duration::from_millis(500),
            max_backoff: Duration::from_secs(4),
        }
    }
}

/// The failures inside the policy's window, and the decision each one gets.
#[derive(Debug)]
pub struct RestartBudget {
    policy: RestartPolicy,
    failures: VecDeque<Instant>,
}

impl RestartBudget {
    pub fn new(policy: RestartPolicy) -> Self {
        Self {
            policy,
            failures: VecDeque::new(),
        }
    }

    /// Record a failure at `now`: `Some(delay)` to restart after `delay`, or
    /// `None` when the budget is spent.
    pub fn on_failure(&mut self, now: Instant) -> Option<Duration> {
        while let Some(&first) = self.failures.front() {
            if now.saturating_duration_since(first) > self.policy.window {
                self.failures.pop_front();
            } else {
                break;
            }
        }
        if self.failures.len() >= self.policy.max_restarts as usize {
            return None;
        }
        self.failures.push_back(now);
        let doublings = u32::try_from(self.failures.len() - 1).unwrap_or(u32::MAX);
        let delay = self
            .policy
            .initial_backoff
            .checked_mul(2u32.saturating_pow(doublings))
            .unwrap_or(self.policy.max_backoff);
        Some(delay.min(self.policy.max_backoff))
    }
}

/// What the shell knows about its engine, as the web view reads it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum EngineState {
    Starting {
        attempt: u32,
    },
    Ready {
        attempt: u32,
        base_url: String,
        token: Token,
        pid: u32,
    },
    Restarting {
        attempt: u32,
        delay_ms: u64,
        reason: String,
        detail: String,
    },
    Failed {
        reason: String,
        detail: String,
    },
    /// No workspace is chosen yet, so no engine runs: the page offers to open
    /// or create one, and a requested restart starts it.
    NoWorkspace,
    Stopped,
}

/// Timings of one supervisor.
#[derive(Clone, Copy, Debug)]
pub struct SupervisorConfig {
    pub policy: RestartPolicy,
    pub ready_timeout: Duration,
    pub stop_grace: Duration,
    pub poll_interval: Duration,
}

impl Default for SupervisorConfig {
    fn default() -> Self {
        Self {
            policy: RestartPolicy::default(),
            // A development engine under `uv run` may sync its environment first.
            ready_timeout: Duration::from_secs(60),
            // Above the engine's own 5 s graceful-shutdown bound.
            stop_grace: Duration::from_secs(8),
            poll_interval: Duration::from_millis(100),
        }
    }
}

/// Why no engine could be located.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Unlocated {
    /// No workspace is chosen yet: not a failure, nothing to start.
    NoWorkspace,
    /// The engine cannot be formed; the detail says why.
    Failed(String),
}

/// Locates the engine afresh before each start.
pub type Locate = dyn Fn() -> Result<EngineCommand, Unlocated> + Send + Sync;

/// The extra environment of each start, read afresh before each one.
pub type Environ = dyn Fn() -> EngineEnv + Send + Sync;

/// What the supervision thread is told, and what it publishes, under one lock
/// so a request can never slip between a check and a wait.
struct Control {
    state: EngineState,
    /// A stop was asked for: the engine is stopped and never started again.
    stop: bool,
    /// A restart was asked for: the engine is replaced, outside the budget.
    restart: bool,
    /// The supervision thread is still deciding; false once it published a
    /// final state and is returning.
    running: bool,
}

struct Shared {
    control: Mutex<Control>,
    changed: Condvar,
}

/// Why a wait ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Wake {
    Elapsed,
    Stop,
    Restart,
}

impl Shared {
    fn lock(&self) -> std::sync::MutexGuard<'_, Control> {
        self.control.lock().expect("control lock")
    }

    fn publish(&self, state: EngineState) {
        self.lock().state = state;
        self.changed.notify_all();
    }

    fn stopping(&self) -> bool {
        self.lock().stop
    }

    /// Whether the current start should be abandoned: a stop or a restart.
    fn interrupted(&self) -> bool {
        let control = self.lock();
        control.stop || control.restart
    }

    /// Consume a pending restart request, if there is one.
    fn take_restart(&self) -> bool {
        std::mem::take(&mut self.lock().restart)
    }

    /// Sleep up to `delay`, or until a stop or a restart is asked for.
    fn wait(&self, delay: Duration) -> Wake {
        let deadline = Instant::now() + delay;
        let mut guard = self.lock();
        loop {
            if guard.stop {
                return Wake::Stop;
            }
            if guard.restart {
                return Wake::Restart;
            }
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Wake::Elapsed;
            }
            guard = self
                .changed
                .wait_timeout(guard, left)
                .expect("control lock")
                .0;
        }
    }

    /// Publish a final state and end supervision, unless a restart is pending
    /// and no stop is: then `false`, and supervision goes on.
    fn finish(&self, state: EngineState) -> bool {
        let mut control = self.lock();
        if control.restart && !control.stop {
            return false;
        }
        control.state = state;
        control.running = false;
        self.changed.notify_all();
        true
    }
}

/// Everything a supervision thread needs, kept so a restart after a final
/// failure can start a new one.
struct Launch {
    locate: Box<Locate>,
    environ: Box<Environ>,
    config: SupervisorConfig,
}

/// The running supervision thread and the state it publishes.
pub struct Supervisor {
    shared: Arc<Shared>,
    launch: Arc<Launch>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl Supervisor {
    /// Start supervising: the engine is started at once on its own thread.
    pub fn start(locate: Box<Locate>, config: SupervisorConfig) -> Self {
        Self::start_with_env(locate, Box::new(EngineEnv::default), config)
    }

    /// [`Supervisor::start`], with `environ` read before every start for the
    /// extra environment of that engine process.
    pub fn start_with_env(
        locate: Box<Locate>,
        environ: Box<Environ>,
        config: SupervisorConfig,
    ) -> Self {
        let shared = Arc::new(Shared {
            control: Mutex::new(Control {
                state: EngineState::Starting { attempt: 1 },
                stop: false,
                restart: false,
                running: true,
            }),
            changed: Condvar::new(),
        });
        let launch = Arc::new(Launch {
            locate,
            environ,
            config,
        });
        let thread = spawn_supervision(&shared, &launch);
        Self {
            shared,
            launch,
            thread: Mutex::new(Some(thread)),
        }
    }

    /// Replace the engine with a fresh one, as the user asked, for example
    /// because its environment changed. Unlike a crash this never spends the
    /// restart budget: a running engine is stopped and started again, a
    /// backoff is cut short, and after a final failure supervision starts
    /// over. After a shutdown it does nothing.
    pub fn restart(&self) {
        let mut thread = self.thread.lock().expect("thread lock");
        {
            let mut control = self.shared.lock();
            if control.stop {
                return;
            }
            if control.running {
                control.restart = true;
                self.shared.changed.notify_all();
                return;
            }
            control.running = true;
            control.restart = false;
            control.state = EngineState::Starting { attempt: 1 };
            self.shared.changed.notify_all();
        }
        // The old thread published its final state and is returning.
        if let Some(old) = thread.take() {
            let _ = old.join();
        }
        *thread = Some(spawn_supervision(&self.shared, &self.launch));
    }

    /// The current state.
    pub fn state(&self) -> EngineState {
        self.shared.lock().state.clone()
    }

    /// Wait until `accept` holds for the state, up to `timeout`.
    pub fn wait_for(
        &self,
        timeout: Duration,
        accept: impl Fn(&EngineState) -> bool,
    ) -> Option<EngineState> {
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

    /// Stop the engine and the supervision thread. Idempotent, and callable
    /// from any thread: a second caller waits until the first one's stop is
    /// complete, because the join happens under the thread lock.
    pub fn shutdown(&self) {
        self.shared.lock().stop = true;
        self.shared.changed.notify_all();
        let mut thread = self.thread.lock().expect("thread lock");
        if let Some(thread) = thread.take() {
            let _ = thread.join();
        }
    }
}

fn spawn_supervision(shared: &Arc<Shared>, launch: &Arc<Launch>) -> JoinHandle<()> {
    let shared = Arc::clone(shared);
    let launch = Arc::clone(launch);
    std::thread::Builder::new()
        .name("engine-supervisor".into())
        .spawn(move || supervise(&shared, &launch))
        .expect("spawn the supervision thread")
}

/// The supervision loop: start, watch, restart within the budget, stop. A
/// requested restart goes round the loop again without touching the budget.
fn supervise(shared: &Shared, launch: &Launch) {
    let config = launch.config;
    let mut budget = RestartBudget::new(config.policy);
    let mut attempt = 0u32;
    loop {
        if shared.stopping() {
            shared.finish(EngineState::Stopped);
            return;
        }
        shared.take_restart();
        attempt += 1;
        shared.publish(EngineState::Starting { attempt });
        let command = match (launch.locate)() {
            Ok(command) => command,
            Err(unlocated) => {
                let state = match unlocated {
                    Unlocated::NoWorkspace => EngineState::NoWorkspace,
                    Unlocated::Failed(detail) => EngineState::Failed {
                        reason: "engine_not_located".into(),
                        detail,
                    },
                };
                if shared.finish(state) {
                    return;
                }
                continue;
            }
        };
        let env = (launch.environ)();
        let started = engine::start_with_env(&command, &env, config.ready_timeout, &|| {
            shared.interrupted()
        });
        let (reason, detail) = match started {
            Ok(running) => match watch(shared, running, attempt, config) {
                Ended::Exited(reason, detail) => (reason, detail),
                Ended::Stopped => {
                    shared.finish(EngineState::Stopped);
                    return;
                }
                Ended::Restart => continue,
            },
            Err(engine::StartError::Cancelled) => {
                if shared.stopping() {
                    shared.finish(EngineState::Stopped);
                    return;
                }
                continue;
            }
            Err(error) => (error.reason().to_owned(), error.detail()),
        };
        match budget.on_failure(Instant::now()) {
            Some(delay) => {
                shared.publish(EngineState::Restarting {
                    attempt,
                    delay_ms: u64::try_from(delay.as_millis()).unwrap_or(u64::MAX),
                    reason,
                    detail,
                });
                if shared.wait(delay) == Wake::Stop {
                    shared.finish(EngineState::Stopped);
                    return;
                }
            }
            None => {
                if shared.finish(EngineState::Failed { reason, detail }) {
                    return;
                }
            }
        }
    }
}

/// How a watched engine ended.
enum Ended {
    /// It exited on its own: why, for the budget.
    Exited(String, String),
    /// It was stopped because the shell is stopping.
    Stopped,
    /// It was stopped because a restart was asked for.
    Restart,
}

/// Publish a ready engine and watch it until it exits or is stopped.
fn watch(
    shared: &Shared,
    mut running: RunningEngine,
    attempt: u32,
    config: SupervisorConfig,
) -> Ended {
    shared.publish(EngineState::Ready {
        attempt,
        base_url: running.ready().base_url(),
        token: running.token().clone(),
        pid: running.pid(),
    });
    loop {
        if let Some(status) = running.try_exited() {
            let detail = format!("the engine exited ({status}).\n{}", running.stderr_tail());
            running.stop(config.stop_grace);
            return Ended::Exited("exited".into(), detail);
        }
        match shared.wait(config.poll_interval) {
            Wake::Elapsed => {}
            Wake::Stop => {
                running.stop(config.stop_grace);
                return Ended::Stopped;
            }
            Wake::Restart => {
                running.stop(config.stop_grace);
                return Ended::Restart;
            }
        }
    }
}

impl Drop for Supervisor {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(test)]
mod budget_tests {
    use super::*;

    fn policy() -> RestartPolicy {
        RestartPolicy {
            max_restarts: 3,
            window: Duration::from_secs(60),
            initial_backoff: Duration::from_millis(500),
            max_backoff: Duration::from_millis(1500),
        }
    }

    #[test]
    fn backoff_doubles_up_to_its_cap_then_the_budget_is_spent() {
        let mut budget = RestartBudget::new(policy());
        let t = Instant::now();
        assert_eq!(budget.on_failure(t), Some(Duration::from_millis(500)));
        assert_eq!(budget.on_failure(t), Some(Duration::from_millis(1000)));
        assert_eq!(budget.on_failure(t), Some(Duration::from_millis(1500)));
        assert_eq!(budget.on_failure(t), None);
    }

    #[test]
    fn failures_outside_the_window_are_forgotten() {
        let mut budget = RestartBudget::new(policy());
        let t = Instant::now();
        for _ in 0..3 {
            assert!(budget.on_failure(t).is_some());
        }
        let later = t + Duration::from_secs(61);
        assert_eq!(budget.on_failure(later), Some(Duration::from_millis(500)));
    }

    #[test]
    fn a_failure_exactly_at_the_window_edge_still_counts() {
        let mut budget = RestartBudget::new(policy());
        let t = Instant::now();
        for _ in 0..3 {
            assert!(budget.on_failure(t).is_some());
        }
        assert_eq!(budget.on_failure(t + Duration::from_secs(60)), None);
    }

    #[test]
    fn a_zero_restart_policy_never_restarts() {
        let mut budget = RestartBudget::new(RestartPolicy {
            max_restarts: 0,
            ..policy()
        });
        assert_eq!(budget.on_failure(Instant::now()), None);
    }

    #[test]
    fn the_ready_state_serialises_for_the_web_view() {
        let token = Token::generate();
        let state = EngineState::Ready {
            attempt: 1,
            base_url: "http://127.0.0.1:4242/v1".into(),
            token: token.clone(),
            pid: 7,
        };
        let json = serde_json::to_value(&state).expect("json");
        assert_eq!(json["state"], "ready");
        assert_eq!(json["base_url"], "http://127.0.0.1:4242/v1");
        assert_eq!(json["token"], token.as_str());
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use crate::engine::fake::{self, READY};
    use std::path::Path;

    fn fast(max_restarts: u32) -> SupervisorConfig {
        SupervisorConfig {
            policy: RestartPolicy {
                max_restarts,
                window: Duration::from_secs(60),
                initial_backoff: Duration::from_millis(20),
                max_backoff: Duration::from_millis(40),
            },
            ready_timeout: Duration::from_secs(5),
            stop_grace: Duration::from_secs(5),
            poll_interval: Duration::from_millis(10),
        }
    }

    fn fixed(command: EngineCommand) -> Box<Locate> {
        Box::new(move || Ok(command.clone()))
    }

    const WAIT: Duration = Duration::from_secs(10);

    fn is_ready(s: &EngineState) -> bool {
        matches!(s, EngineState::Ready { .. })
    }

    /// Announces ready and appends its pid to `$1/pids`, then lives until EOF.
    fn counting(dir: &Path) -> EngineCommand {
        fake::engine(
            &format!("IFS= read -r t; echo $$ >> \"$1/pids\"; echo '{READY}'; cat > /dev/null"),
            dir,
        )
    }

    fn pids(dir: &Path) -> Vec<u32> {
        std::fs::read_to_string(dir.join("pids"))
            .unwrap_or_default()
            .lines()
            .filter_map(|l| l.trim().parse().ok())
            .collect()
    }

    #[test]
    fn it_starts_the_engine_and_publishes_ready() {
        let dir = fake::scratch("sup-ready");
        let sup = Supervisor::start(fixed(counting(&dir)), fast(3));
        let state = sup.wait_for(WAIT, is_ready).expect("ready");
        let EngineState::Ready {
            base_url,
            attempt,
            pid,
            ..
        } = state
        else {
            unreachable!()
        };
        assert_eq!(base_url, "http://127.0.0.1:4242/v1");
        assert_eq!(attempt, 1);
        sup.shutdown();
        assert_eq!(sup.state(), EngineState::Stopped);
        assert!(!fake::alive(pid));
    }

    #[test]
    fn a_crashed_engine_is_restarted_with_a_fresh_token() {
        let dir = fake::scratch("sup-restart");
        let sup = Supervisor::start(fixed(counting(&dir)), fast(3));
        let EngineState::Ready { pid, token, .. } = sup.wait_for(WAIT, is_ready).expect("ready")
        else {
            unreachable!()
        };
        unsafe { libc::kill(pid as libc::pid_t, libc::SIGKILL) };
        let second = sup
            .wait_for(WAIT, |s| matches!(s, EngineState::Ready { attempt: 2, .. }))
            .expect("restarted");
        let EngineState::Ready {
            pid: pid2,
            token: token2,
            ..
        } = second
        else {
            unreachable!()
        };
        assert_ne!(pid, pid2);
        assert_ne!(token, token2);
        sup.shutdown();
        assert_eq!(pids(&dir).len(), 2);
    }

    #[test]
    fn restarts_are_bounded_then_the_state_is_failed() {
        let dir = fake::scratch("sup-bounded");
        let script = "echo x >> \"$1/starts\"; IFS= read -r t; echo 'boom' >&2; exit 3";
        let sup = Supervisor::start(fixed(fake::engine(script, &dir)), fast(2));
        let state = sup
            .wait_for(WAIT, |s| matches!(s, EngineState::Failed { .. }))
            .expect("failed");
        let EngineState::Failed { reason, detail } = state else {
            unreachable!()
        };
        assert_eq!(reason, "exited_before_ready");
        assert!(detail.contains("boom"), "{detail}");
        let starts = std::fs::read_to_string(dir.join("starts")).expect("starts");
        assert_eq!(starts.lines().count(), 3, "one start plus two restarts");
        sup.shutdown();
        assert!(matches!(sup.state(), EngineState::Failed { .. }));
    }

    #[test]
    fn a_locator_failure_is_final_and_spawns_nothing() {
        let sup = Supervisor::start(
            Box::new(|| Err(Unlocated::Failed("the workspace is missing".to_owned()))),
            fast(3),
        );
        let state = sup
            .wait_for(WAIT, |s| matches!(s, EngineState::Failed { .. }))
            .expect("failed");
        assert_eq!(
            state,
            EngineState::Failed {
                reason: "engine_not_located".into(),
                detail: "the workspace is missing".into(),
            }
        );
        sup.shutdown();
    }

    #[test]
    fn no_workspace_yet_is_its_own_state_and_spawns_nothing() {
        let sup = Supervisor::start(Box::new(|| Err(Unlocated::NoWorkspace)), fast(3));
        let state = sup
            .wait_for(WAIT, |s| matches!(s, EngineState::NoWorkspace))
            .expect("no workspace");
        assert_eq!(state, EngineState::NoWorkspace);
        assert_eq!(
            serde_json::to_value(&state).expect("json"),
            serde_json::json!({"state": "no_workspace"})
        );
        sup.shutdown();
        assert_eq!(sup.state(), EngineState::NoWorkspace);
    }

    #[test]
    fn a_requested_restart_after_no_workspace_starts_the_chosen_one() {
        let dir = fake::scratch("sup-chosen");
        let chosen = Arc::new(Mutex::new(false));
        let seen = Arc::clone(&chosen);
        let engine = fake::well_behaved(&dir);
        let sup = Supervisor::start(
            Box::new(move || {
                if *seen.lock().expect("chosen") {
                    Ok(engine.clone())
                } else {
                    Err(Unlocated::NoWorkspace)
                }
            }),
            fast(0),
        );
        sup.wait_for(WAIT, |s| matches!(s, EngineState::NoWorkspace))
            .expect("no workspace");
        *chosen.lock().expect("chosen") = true;
        sup.restart();
        sup.wait_for(WAIT, is_ready).expect("ready");
        sup.shutdown();
    }

    #[test]
    fn shutdown_during_a_restart_delay_stops_promptly() {
        let dir = fake::scratch("sup-delay");
        let script = "IFS= read -r t; exit 3";
        let mut config = fast(3);
        config.policy.initial_backoff = Duration::from_secs(30);
        config.policy.max_backoff = Duration::from_secs(30);
        let sup = Supervisor::start(fixed(fake::engine(script, &dir)), config);
        sup.wait_for(WAIT, |s| matches!(s, EngineState::Restarting { .. }))
            .expect("restarting");
        let began = Instant::now();
        sup.shutdown();
        assert!(began.elapsed() < Duration::from_secs(5));
        assert_eq!(sup.state(), EngineState::Stopped);
    }

    #[test]
    fn dropping_the_supervisor_stops_the_engine() {
        let dir = fake::scratch("sup-drop");
        let sup = Supervisor::start(fixed(counting(&dir)), fast(3));
        let EngineState::Ready { pid, .. } = sup.wait_for(WAIT, is_ready).expect("ready") else {
            unreachable!()
        };
        drop(sup);
        assert!(fake::gone_within(pid, WAIT));
    }

    #[test]
    fn a_shared_supervisor_is_read_and_shut_down_from_other_threads() {
        let dir = fake::scratch("sup-shared");
        let sup = Arc::new(Supervisor::start(fixed(counting(&dir)), fast(3)));
        let EngineState::Ready { pid, .. } = sup.wait_for(WAIT, is_ready).expect("ready") else {
            unreachable!()
        };
        let reader = Arc::clone(&sup);
        std::thread::spawn(move || reader.shutdown())
            .join()
            .expect("shutdown thread");
        assert_eq!(sup.state(), EngineState::Stopped);
        assert!(!fake::alive(pid));
        sup.shutdown();
    }

    fn ready_attempt(n: u32) -> impl Fn(&EngineState) -> bool {
        move |s| matches!(s, EngineState::Ready { attempt, .. } if *attempt == n)
    }

    #[test]
    fn a_requested_restart_replaces_the_engine_without_spending_the_crash_budget() {
        let dir = fake::scratch("sup-user-restart");
        // A budget of one crash: after two requested restarts, a crash must
        // still be restarted, which it is not if either request was counted.
        let sup = Supervisor::start(fixed(counting(&dir)), fast(1));
        let EngineState::Ready { pid, token, .. } = sup.wait_for(WAIT, is_ready).expect("ready")
        else {
            unreachable!()
        };
        sup.restart();
        let EngineState::Ready {
            pid: pid2,
            token: token2,
            ..
        } = sup.wait_for(WAIT, ready_attempt(2)).expect("restarted")
        else {
            unreachable!()
        };
        assert_ne!(pid, pid2);
        assert_ne!(token, token2);
        assert!(fake::gone_within(pid, WAIT), "the old engine was stopped");
        sup.restart();
        let EngineState::Ready { pid: pid3, .. } = sup
            .wait_for(WAIT, ready_attempt(3))
            .expect("restarted again")
        else {
            unreachable!()
        };
        unsafe { libc::kill(pid3 as libc::pid_t, libc::SIGKILL) };
        let after_crash = sup.wait_for(WAIT, |s| {
            matches!(
                s,
                EngineState::Ready { attempt: 4, .. } | EngineState::Failed { .. }
            )
        });
        assert!(
            matches!(after_crash, Some(EngineState::Ready { attempt: 4, .. })),
            "{after_crash:?}"
        );
        sup.shutdown();
        assert_eq!(pids(&dir).len(), 4);
    }

    #[test]
    fn each_start_reads_the_extra_environment_afresh() {
        let dir = fake::scratch("sup-env");
        let script = format!(
            "IFS= read -r t; printf '%s\\n' \"${{OPENKOS_OPENAI_API_KEY-unset}}\" >> \"$1/keys\"; \
             echo '{READY}'; cat > /dev/null"
        );
        let value = Arc::new(Mutex::new(None::<String>));
        let source = Arc::clone(&value);
        let environ: Box<Environ> = Box::new(move || {
            let mut env = EngineEnv::default();
            if let Some(key) = source.lock().expect("value").as_deref() {
                env.set("OPENKOS_OPENAI_API_KEY", key);
            }
            env
        });
        let sup = Supervisor::start_with_env(fixed(fake::engine(&script, &dir)), environ, fast(0));
        sup.wait_for(WAIT, ready_attempt(1)).expect("ready");
        *value.lock().expect("value") = Some("sk-new".into());
        sup.restart();
        sup.wait_for(WAIT, ready_attempt(2)).expect("restarted");
        *value.lock().expect("value") = None;
        sup.restart();
        sup.wait_for(WAIT, ready_attempt(3))
            .expect("restarted again");
        sup.shutdown();
        let keys = std::fs::read_to_string(dir.join("keys")).expect("keys");
        assert_eq!(
            keys.lines().collect::<Vec<_>>(),
            ["unset", "sk-new", "unset"]
        );
    }

    #[test]
    fn a_requested_restart_after_a_final_failure_supervises_again() {
        let dir = fake::scratch("sup-revive");
        let script =
            format!("IFS= read -r t; [ -f \"$1/ok\" ] || exit 3; echo '{READY}'; cat > /dev/null");
        let sup = Supervisor::start(fixed(fake::engine(&script, &dir)), fast(0));
        sup.wait_for(WAIT, |s| matches!(s, EngineState::Failed { .. }))
            .expect("failed");
        std::fs::write(dir.join("ok"), b"1").expect("marker");
        sup.restart();
        sup.wait_for(WAIT, is_ready)
            .expect("ready after the restart");
        sup.shutdown();
        assert_eq!(sup.state(), EngineState::Stopped);
    }

    #[test]
    fn a_requested_restart_cuts_a_crash_backoff_short() {
        let dir = fake::scratch("sup-cut-backoff");
        let script =
            format!("IFS= read -r t; [ -f \"$1/ok\" ] || exit 3; echo '{READY}'; cat > /dev/null");
        let mut config = fast(3);
        config.policy.initial_backoff = Duration::from_secs(30);
        config.policy.max_backoff = Duration::from_secs(30);
        let sup = Supervisor::start(fixed(fake::engine(&script, &dir)), config);
        sup.wait_for(WAIT, |s| matches!(s, EngineState::Restarting { .. }))
            .expect("restarting");
        std::fs::write(dir.join("ok"), b"1").expect("marker");
        let began = Instant::now();
        sup.restart();
        sup.wait_for(WAIT, is_ready).expect("ready");
        assert!(began.elapsed() < Duration::from_secs(5));
        sup.shutdown();
    }

    #[test]
    fn a_restart_requested_after_shutdown_starts_nothing() {
        let dir = fake::scratch("sup-restart-after-stop");
        let sup = Supervisor::start(fixed(counting(&dir)), fast(3));
        sup.wait_for(WAIT, is_ready).expect("ready");
        sup.shutdown();
        sup.restart();
        std::thread::sleep(Duration::from_millis(200));
        assert_eq!(sup.state(), EngineState::Stopped);
        assert_eq!(pids(&dir).len(), 1);
    }

    /// Not a test on its own: the body of the force-quit probe below, run in a
    /// child copy of this test binary that is then killed with SIGKILL.
    #[test]
    fn force_quit_holder() {
        let Some(dir) = std::env::var_os("OPENKOS_DESKTOP_FORCE_QUIT_DIR") else {
            return;
        };
        let dir = std::path::PathBuf::from(dir);
        let sup = Supervisor::start(fixed(counting(&dir)), fast(0));
        sup.wait_for(WAIT, is_ready).expect("ready");
        std::fs::write(dir.join("holder-ready"), b"1").expect("marker");
        loop {
            std::thread::sleep(Duration::from_secs(1));
        }
    }

    #[test]
    fn a_force_quit_shell_leaves_no_engine_behind() {
        let dir = fake::scratch("sup-force-quit");
        let mut holder = std::process::Command::new(std::env::current_exe().expect("exe"))
            .args([
                "--exact",
                "supervisor::tests::force_quit_holder",
                "--nocapture",
            ])
            .env("OPENKOS_DESKTOP_FORCE_QUIT_DIR", &dir)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .expect("holder");
        let deadline = Instant::now() + WAIT;
        while !dir.join("holder-ready").exists() {
            assert!(Instant::now() < deadline, "holder never became ready");
            std::thread::sleep(Duration::from_millis(20));
        }
        let engine = pids(&dir)[0];
        assert!(fake::alive(engine));
        holder.kill().expect("SIGKILL the holder");
        holder.wait().expect("reap the holder");
        assert!(fake::gone_within(engine, WAIT), "orphaned engine {engine}");
    }
}
