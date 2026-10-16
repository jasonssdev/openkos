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

use crate::engine::{self, EngineCommand, RunningEngine, Token};

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

/// Locates the engine afresh before each start.
pub type Locate = dyn Fn() -> Result<EngineCommand, String> + Send + Sync;

/// The published state, and whether a stop was asked for, under one lock so a
/// stop can never slip between a check and a wait.
struct Shared {
    state: Mutex<(EngineState, bool)>,
    changed: Condvar,
}

impl Shared {
    fn publish(&self, state: EngineState) {
        self.state.lock().expect("state lock").0 = state;
        self.changed.notify_all();
    }

    fn stopping(&self) -> bool {
        self.state.lock().expect("state lock").1
    }

    /// Sleep up to `delay`; `true` if a stop was asked for meanwhile.
    fn sleep_unless_stopped(&self, delay: Duration) -> bool {
        let deadline = Instant::now() + delay;
        let mut guard = self.state.lock().expect("state lock");
        while !guard.1 {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return false;
            }
            guard = self
                .changed
                .wait_timeout(guard, left)
                .expect("state lock")
                .0;
        }
        true
    }
}

/// The running supervision thread and the state it publishes.
pub struct Supervisor {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl Supervisor {
    /// Start supervising: the engine is started at once on its own thread.
    pub fn start(locate: Box<Locate>, config: SupervisorConfig) -> Self {
        let shared = Arc::new(Shared {
            state: Mutex::new((EngineState::Starting { attempt: 1 }, false)),
            changed: Condvar::new(),
        });
        let worker = Arc::clone(&shared);
        let thread = std::thread::Builder::new()
            .name("engine-supervisor".into())
            .spawn(move || supervise(&worker, &*locate, config))
            .expect("spawn the supervision thread");
        Self {
            shared,
            thread: Some(thread),
        }
    }

    /// The current state.
    pub fn state(&self) -> EngineState {
        self.shared.state.lock().expect("state lock").0.clone()
    }

    /// Wait until `accept` holds for the state, up to `timeout`.
    pub fn wait_for(
        &self,
        timeout: Duration,
        accept: impl Fn(&EngineState) -> bool,
    ) -> Option<EngineState> {
        let deadline = Instant::now() + timeout;
        let mut guard = self.shared.state.lock().expect("state lock");
        loop {
            if accept(&guard.0) {
                return Some(guard.0.clone());
            }
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return None;
            }
            guard = self
                .shared
                .changed
                .wait_timeout(guard, left)
                .expect("state lock")
                .0;
        }
    }

    /// Stop the engine and the supervision thread. Idempotent.
    pub fn shutdown(&mut self) {
        self.shared.state.lock().expect("state lock").1 = true;
        self.shared.changed.notify_all();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// The supervision loop: start, watch, restart within the budget, stop.
fn supervise(shared: &Shared, locate: &Locate, config: SupervisorConfig) {
    let mut budget = RestartBudget::new(config.policy);
    let mut attempt = 0u32;
    loop {
        if shared.stopping() {
            shared.publish(EngineState::Stopped);
            return;
        }
        attempt += 1;
        shared.publish(EngineState::Starting { attempt });
        let command = match locate() {
            Ok(command) => command,
            Err(detail) => {
                shared.publish(EngineState::Failed {
                    reason: "engine_not_located".into(),
                    detail,
                });
                return;
            }
        };
        let (reason, detail) =
            match engine::start(&command, config.ready_timeout, &|| shared.stopping()) {
                Ok(running) => match watch(shared, running, attempt, config) {
                    Some(exit) => exit,
                    None => {
                        shared.publish(EngineState::Stopped);
                        return;
                    }
                },
                Err(engine::StartError::Cancelled) => {
                    shared.publish(EngineState::Stopped);
                    return;
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
                if shared.sleep_unless_stopped(delay) {
                    shared.publish(EngineState::Stopped);
                    return;
                }
            }
            None => {
                shared.publish(EngineState::Failed { reason, detail });
                return;
            }
        }
    }
}

/// Publish a ready engine and watch it. `None` when it was stopped on request,
/// otherwise why it exited.
fn watch(
    shared: &Shared,
    mut running: RunningEngine,
    attempt: u32,
    config: SupervisorConfig,
) -> Option<(String, String)> {
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
            return Some(("exited".into(), detail));
        }
        if shared.sleep_unless_stopped(config.poll_interval) {
            running.stop(config.stop_grace);
            return None;
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
        let mut sup = Supervisor::start(fixed(counting(&dir)), fast(3));
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
        let mut sup = Supervisor::start(fixed(counting(&dir)), fast(3));
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
        let mut sup = Supervisor::start(fixed(fake::engine(script, &dir)), fast(2));
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
        let mut sup = Supervisor::start(
            Box::new(|| Err("the workspace is missing".to_owned())),
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
    fn shutdown_during_a_restart_delay_stops_promptly() {
        let dir = fake::scratch("sup-delay");
        let script = "IFS= read -r t; exit 3";
        let mut config = fast(3);
        config.policy.initial_backoff = Duration::from_secs(30);
        config.policy.max_backoff = Duration::from_secs(30);
        let mut sup = Supervisor::start(fixed(fake::engine(script, &dir)), config);
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
