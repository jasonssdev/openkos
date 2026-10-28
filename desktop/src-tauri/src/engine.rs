//! One engine process: the `openkos serve` handshake and its stop.
//!
//! The handshake is the engine's, not ours (`openkos.localapi.launch`): the
//! token goes in as the first line of stdin, and the engine answers with one
//! JSON line on stdout naming the loopback port once it is listening. Stdin then
//! stays open for the life of the engine, because EOF on it is the engine's
//! lifeline: it exits when the pipe closes, which is what happens when the shell
//! dies by any route, a force-quit included.

use std::collections::VecDeque;
use std::ffi::OsString;
use std::fmt;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;

/// The local API major version this shell speaks (`openkos.api.API_VERSION`).
pub const SUPPORTED_API_VERSION: u64 = 1;

/// The only host the engine may announce: the API is loopback-only.
pub const LOOPBACK_HOST: &str = "127.0.0.1";

/// How the engine is launched: a program, its arguments and a working
/// directory. Never carries the token.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EngineCommand {
    pub program: OsString,
    pub args: Vec<OsString>,
    pub cwd: Option<PathBuf>,
}

/// The per-launch bearer token. Its `Debug` never prints the secret.
#[derive(Clone, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct Token(String);

impl Token {
    /// A fresh token: 32 bytes from the operating system's generator, as hex.
    pub fn generate() -> Self {
        let mut bytes = [0u8; 32];
        getrandom::fill(&mut bytes).expect("the operating system's random generator");
        Self(bytes.iter().map(|b| format!("{b:02x}")).collect())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Token {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Token(<redacted>)")
    }
}

/// Extra environment variables for one engine process, such as the remote
/// backend's API key. Kept apart from [`EngineCommand`] because its values may
/// be secrets: its `Debug` names the variables, never their values.
#[derive(Clone, Default, PartialEq, Eq)]
pub struct EngineEnv(Vec<(String, String)>);

impl EngineEnv {
    /// Set `name` to `value`, replacing an earlier value of the same name.
    pub fn set(&mut self, name: &str, value: &str) {
        self.0.retain(|(existing, _)| existing != name);
        self.0.push((name.to_owned(), value.to_owned()));
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    pub fn names(&self) -> Vec<&str> {
        self.0.iter().map(|(name, _)| name.as_str()).collect()
    }
}

impl fmt::Debug for EngineEnv {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "EngineEnv({:?} = <redacted>)", self.names())
    }
}

/// What the engine's ready line announces.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Ready {
    pub host: String,
    pub port: u16,
    pub api_version: u64,
    pub base_path: String,
}

impl Ready {
    /// The URL every API path is relative to, e.g. `http://127.0.0.1:5123/v1`.
    pub fn base_url(&self) -> String {
        format!("http://{}:{}{}", self.host, self.port, self.base_path)
    }
}

/// Why a stdout line is not an acceptable ready line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReadyLineError {
    NotJson,
    NotReadyEvent,
    NotLoopback(String),
    BadPort,
    BadBasePath,
    UnsupportedApiVersion(u64),
}

impl fmt::Display for ReadyLineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NotJson => f.write_str("the ready line is not a JSON object"),
            Self::NotReadyEvent => f.write_str("the first line is not a ready event"),
            Self::NotLoopback(host) => {
                write!(f, "the engine announced a non-loopback host {host:?}")
            }
            Self::BadPort => f.write_str("the ready line carries no valid port"),
            Self::BadBasePath => f.write_str("the ready line carries no valid base path"),
            Self::UnsupportedApiVersion(v) => write!(
                f,
                "the engine speaks API version {v}; this shell speaks {SUPPORTED_API_VERSION}"
            ),
        }
    }
}

/// Parse and validate the engine's ready line.
pub fn parse_ready_line(line: &str) -> Result<Ready, ReadyLineError> {
    let value: serde_json::Value =
        serde_json::from_str(line.trim()).map_err(|_| ReadyLineError::NotJson)?;
    let object = value.as_object().ok_or(ReadyLineError::NotJson)?;
    if object.get("event").and_then(|v| v.as_str()) != Some("ready") {
        return Err(ReadyLineError::NotReadyEvent);
    }
    let host = object.get("host").and_then(|v| v.as_str()).unwrap_or("");
    if host != LOOPBACK_HOST {
        return Err(ReadyLineError::NotLoopback(host.to_owned()));
    }
    let port = object
        .get("port")
        .and_then(|v| v.as_u64())
        .and_then(|p| u16::try_from(p).ok())
        .filter(|p| *p != 0)
        .ok_or(ReadyLineError::BadPort)?;
    let base_path = object
        .get("base_path")
        .and_then(|v| v.as_str())
        .filter(|p| p.starts_with('/'))
        .ok_or(ReadyLineError::BadBasePath)?;
    let api_version = object
        .get("api_version")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    if api_version != SUPPORTED_API_VERSION {
        return Err(ReadyLineError::UnsupportedApiVersion(api_version));
    }
    Ok(Ready {
        host: host.to_owned(),
        port,
        api_version,
        base_path: base_path.to_owned(),
    })
}

/// How many stderr lines are kept for diagnostics.
const STDERR_TAIL_LINES: usize = 40;

/// How often a blocking wait re-checks its deadline or cancellation.
const POLL: Duration = Duration::from_millis(20);

#[derive(Default)]
struct StderrTail(VecDeque<String>);

impl StderrTail {
    fn push(&mut self, line: String) {
        if self.0.len() == STDERR_TAIL_LINES {
            self.0.pop_front();
        }
        self.0.push_back(line);
    }

    fn text(&self) -> String {
        self.0.iter().map(|l| format!("{l}\n")).collect()
    }
}

/// Why an engine did not become ready.
#[derive(Debug)]
pub enum StartError {
    Spawn(std::io::Error),
    TokenHandover(std::io::Error),
    ExitedBeforeReady {
        status: ExitStatus,
        stderr: String,
    },
    BadReadyLine {
        error: ReadyLineError,
        stderr: String,
    },
    NotReady {
        waited: Duration,
        stderr: String,
    },
    Cancelled,
}

impl StartError {
    /// A short machine-readable reason, for the shell's state.
    pub fn reason(&self) -> &'static str {
        match self {
            Self::Spawn(_) => "spawn_failed",
            Self::TokenHandover(_) => "token_handover_failed",
            Self::ExitedBeforeReady { .. } => "exited_before_ready",
            Self::BadReadyLine { .. } => "bad_ready_line",
            Self::NotReady { .. } => "not_ready",
            Self::Cancelled => "cancelled",
        }
    }

    /// The human detail: the error and whatever the engine wrote to stderr.
    pub fn detail(&self) -> String {
        match self {
            Self::Spawn(e) => format!("the engine could not be started: {e}"),
            Self::TokenHandover(e) => format!("the token could not be handed over: {e}"),
            Self::ExitedBeforeReady { status, stderr } => {
                format!("the engine exited before it was ready ({status}).\n{stderr}")
            }
            Self::BadReadyLine { error, stderr } => format!("{error}.\n{stderr}"),
            Self::NotReady { waited, stderr } => format!(
                "the engine was not ready after {} s.\n{stderr}",
                waited.as_secs()
            ),
            Self::Cancelled => "the start was cancelled".to_owned(),
        }
    }
}

/// How a stop ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StopOutcome {
    /// The engine exited on its own after its stdin closed.
    Graceful,
    /// The grace period ran out and the engine's process group was killed.
    Killed,
    /// The engine had already exited before the stop.
    AlreadyExited,
}

/// A started, ready engine.
pub struct RunningEngine {
    child: Child,
    /// The lifeline: held open until [`RunningEngine::stop`] drops it.
    stdin: Option<ChildStdin>,
    ready: Ready,
    token: Token,
    stderr: Arc<Mutex<StderrTail>>,
}

impl RunningEngine {
    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    pub fn ready(&self) -> &Ready {
        &self.ready
    }

    pub fn token(&self) -> &Token {
        &self.token
    }

    /// The last lines the engine wrote to stderr.
    pub fn stderr_tail(&self) -> String {
        self.stderr.lock().expect("stderr lock").text()
    }

    /// `Some` once the engine has exited, without blocking.
    pub fn try_exited(&mut self) -> Option<ExitStatus> {
        self.child.try_wait().ok().flatten()
    }

    /// Close stdin (the engine's lifeline), wait up to `grace`, then kill the
    /// engine's whole process group and reap it.
    pub fn stop(mut self, grace: Duration) -> StopOutcome {
        if self.try_exited().is_some() {
            kill_group(&mut self.child);
            return StopOutcome::AlreadyExited;
        }
        drop(self.stdin.take());
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline {
            if self.try_exited().is_some() {
                // The engine is gone; anything it left in its group goes too.
                kill_group(&mut self.child);
                return StopOutcome::Graceful;
            }
            thread::sleep(POLL);
        }
        kill_group(&mut self.child);
        StopOutcome::Killed
    }
}

/// Spawn the engine, hand it a fresh token and wait for its ready line.
///
/// `cancelled` is polled while waiting, so a quit during start-up is not held
/// up for the whole `ready_timeout`.
pub fn start(
    command: &EngineCommand,
    ready_timeout: Duration,
    cancelled: &dyn Fn() -> bool,
) -> Result<RunningEngine, StartError> {
    start_with_env(command, &EngineEnv::default(), ready_timeout, cancelled)
}

/// [`start`], with `env` added to the environment the engine inherits. A
/// variable already in the shell's environment is replaced, never duplicated.
pub fn start_with_env(
    command: &EngineCommand,
    env: &EngineEnv,
    ready_timeout: Duration,
    cancelled: &dyn Fn() -> bool,
) -> Result<RunningEngine, StartError> {
    let mut process = Command::new(&command.program);
    process
        .args(&command.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(cwd) = &command.cwd {
        process.current_dir(cwd);
    }
    process.envs(env.0.iter().map(|(name, value)| (name, value)));
    // Its own process group, so a stop reaches every process the engine
    // started (`uv run` runs the Python engine as its child).
    #[cfg(unix)]
    std::os::unix::process::CommandExt::process_group(&mut process, 0);
    let mut child = process.spawn().map_err(StartError::Spawn)?;

    let stderr = Arc::new(Mutex::new(StderrTail::default()));
    if let Some(pipe) = child.stderr.take() {
        let sink = Arc::clone(&stderr);
        thread::spawn(move || {
            for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                sink.lock().expect("stderr lock").push(line);
            }
        });
    }
    let tail = |stderr: &Arc<Mutex<StderrTail>>| stderr.lock().expect("stderr lock").text();

    let token = Token::generate();
    let mut stdin = child.stdin.take().expect("piped stdin");
    if let Err(error) = writeln!(stdin, "{}", token.as_str()).and_then(|()| stdin.flush()) {
        kill_group(&mut child);
        return Err(StartError::TokenHandover(error));
    }

    // The first stdout line is the ready line; the rest is drained so a chatty
    // engine can never block on a full pipe.
    let (lines, first_line) = mpsc::channel::<Option<String>>();
    let stdout = child.stdout.take().expect("piped stdout");
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout).lines();
        let first = reader.next().and_then(Result::ok);
        let _ = lines.send(first);
        for _ in reader {}
    });

    let began = Instant::now();
    let line = loop {
        if cancelled() {
            kill_group(&mut child);
            return Err(StartError::Cancelled);
        }
        match first_line.recv_timeout(POLL) {
            Ok(Some(line)) => break line,
            Ok(None) | Err(RecvTimeoutError::Disconnected) => {
                // Stdout closed without a line: the engine is exiting.
                let status = child.wait().map_err(StartError::Spawn)?;
                kill_group(&mut child);
                wait_for_stderr(&stderr);
                return Err(StartError::ExitedBeforeReady {
                    status,
                    stderr: tail(&stderr),
                });
            }
            Err(RecvTimeoutError::Timeout) if began.elapsed() >= ready_timeout => {
                kill_group(&mut child);
                return Err(StartError::NotReady {
                    waited: ready_timeout,
                    stderr: tail(&stderr),
                });
            }
            Err(RecvTimeoutError::Timeout) => {}
        }
    };

    match parse_ready_line(&line) {
        Ok(ready) => Ok(RunningEngine {
            child,
            stdin: Some(stdin),
            ready,
            token,
            stderr,
        }),
        Err(error) => {
            kill_group(&mut child);
            Err(StartError::BadReadyLine {
                error,
                stderr: tail(&stderr),
            })
        }
    }
}

/// A started `openkos daemon` (ADR-0078). It has no handshake: it is running
/// once it is spawned, and it reports nothing on stdout the shell reads. Its
/// stdin is the lifeline, as for the engine: the daemon stops gracefully when
/// the pipe closes, which is what a shell that dies by any route does.
pub struct RunningDaemon {
    child: Child,
    stdin: Option<ChildStdin>,
    stderr: Arc<Mutex<StderrTail>>,
}

impl RunningDaemon {
    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    /// The last lines the daemon wrote to stderr.
    pub fn stderr_tail(&self) -> String {
        wait_for_stderr(&self.stderr);
        self.stderr.lock().expect("stderr lock").text()
    }

    /// `Some` once the daemon has exited, without blocking.
    pub fn try_exited(&mut self) -> Option<ExitStatus> {
        self.child.try_wait().ok().flatten()
    }

    /// Close stdin (the lifeline), wait up to `grace` for the work in progress
    /// to finish, then kill the daemon's whole process group and reap it.
    pub fn stop(mut self, grace: Duration) -> StopOutcome {
        if self.try_exited().is_some() {
            kill_group(&mut self.child);
            return StopOutcome::AlreadyExited;
        }
        drop(self.stdin.take());
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline {
            if self.try_exited().is_some() {
                kill_group(&mut self.child);
                return StopOutcome::Graceful;
            }
            thread::sleep(POLL);
        }
        kill_group(&mut self.child);
        StopOutcome::Killed
    }
}

/// Spawn the daemon with `env` added to its environment, in its own process
/// group. Its output is not read except for the tail of stderr, kept for the
/// failure report; its stdin stays open for its whole life.
pub fn start_daemon(
    command: &EngineCommand,
    env: &EngineEnv,
) -> Result<RunningDaemon, std::io::Error> {
    let mut process = Command::new(&command.program);
    process
        .args(&command.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    if let Some(cwd) = &command.cwd {
        process.current_dir(cwd);
    }
    process.envs(env.0.iter().map(|(name, value)| (name, value)));
    #[cfg(unix)]
    std::os::unix::process::CommandExt::process_group(&mut process, 0);
    let mut child = process.spawn()?;
    let stderr = Arc::new(Mutex::new(StderrTail::default()));
    if let Some(pipe) = child.stderr.take() {
        let sink = Arc::clone(&stderr);
        thread::spawn(move || {
            for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                sink.lock().expect("stderr lock").push(line);
            }
        });
    }
    let stdin = child.stdin.take();
    Ok(RunningDaemon {
        child,
        stdin,
        stderr,
    })
}

/// Why a one-shot engine command did not succeed.
#[derive(Debug)]
pub enum RunError {
    Spawn(std::io::Error),
    Exited { status: ExitStatus, stderr: String },
    TimedOut { waited: Duration, stderr: String },
}

impl RunError {
    /// The human detail: the error and whatever the command wrote to stderr.
    pub fn detail(&self) -> String {
        match self {
            Self::Spawn(e) => format!("the engine could not be started: {e}"),
            Self::Exited { status, stderr } => format!("the engine exited ({status}).\n{stderr}"),
            Self::TimedOut { waited, stderr } => format!(
                "the engine did not finish within {} s and was stopped.\n{stderr}",
                waited.as_secs()
            ),
        }
    }
}

/// Run a one-shot engine command, such as `init`, to completion within
/// `timeout`. Its stdin is empty and not a terminal, so it never prompts; a
/// command still running at the deadline is killed with its process group.
pub fn run_once(command: &EngineCommand, timeout: Duration) -> Result<(), RunError> {
    let mut process = Command::new(&command.program);
    process
        .args(&command.args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    if let Some(cwd) = &command.cwd {
        process.current_dir(cwd);
    }
    #[cfg(unix)]
    std::os::unix::process::CommandExt::process_group(&mut process, 0);
    let mut child = process.spawn().map_err(RunError::Spawn)?;
    let stderr = Arc::new(Mutex::new(StderrTail::default()));
    if let Some(pipe) = child.stderr.take() {
        let sink = Arc::clone(&stderr);
        thread::spawn(move || {
            for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                sink.lock().expect("stderr lock").push(line);
            }
        });
    }
    let tail = |stderr: &Arc<Mutex<StderrTail>>| stderr.lock().expect("stderr lock").text();
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                if status.success() {
                    return Ok(());
                }
                wait_for_stderr(&stderr);
                return Err(RunError::Exited {
                    status,
                    stderr: tail(&stderr),
                });
            }
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
            _ => {
                kill_group(&mut child);
                return Err(RunError::TimedOut {
                    waited: timeout,
                    stderr: tail(&stderr),
                });
            }
        }
    }
}

/// Give the stderr reader a moment to reach EOF after the engine exited, so the
/// engine's last words reach the report. The reader holds the other reference
/// to the tail until its pipe closes.
fn wait_for_stderr(stderr: &Arc<Mutex<StderrTail>>) {
    let deadline = Instant::now() + Duration::from_millis(500);
    while Instant::now() < deadline && Arc::strong_count(stderr) > 1 {
        thread::sleep(Duration::from_millis(5));
    }
}

/// Kill the engine's process group (or the engine alone where there are no
/// groups) and reap it.
fn kill_group(child: &mut Child) {
    #[cfg(unix)]
    {
        let pgid = child.id() as libc::pid_t;
        // The group outlives its leader while a member lives (and its id cannot
        // be reused while it does), so this reaches a grandchild even after the
        // engine itself has exited. With no member left it is a no-op.
        unsafe {
            libc::kill(-pgid, libc::SIGKILL);
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(test)]
pub(crate) mod fake {
    //! Fake engines: POSIX `sh` scripts that speak the real handshake.

    use super::EngineCommand;
    use std::ffi::OsString;
    use std::path::Path;

    pub const READY: &str =
        r#"{"event":"ready","host":"127.0.0.1","port":4242,"api_version":1,"base_path":"/v1"}"#;

    /// A script run as `sh -c <script> fake-engine <dir>`; `$1` is a scratch
    /// directory the script may write evidence into.
    pub fn engine(script: &str, dir: &Path) -> EngineCommand {
        EngineCommand {
            program: "sh".into(),
            args: vec![
                OsString::from("-c"),
                OsString::from(script),
                OsString::from("fake-engine"),
                dir.as_os_str().to_owned(),
            ],
            cwd: None,
        }
    }

    /// Reads the token, records it, announces ready, then lives until EOF.
    pub fn well_behaved(dir: &Path) -> EngineCommand {
        engine(
            &format!(
                "IFS= read -r t || exit 2; printf '%s' \"$t\" > \"$1/token\"; \
                 echo '{READY}'; cat > /dev/null"
            ),
            dir,
        )
    }

    pub fn scratch(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "openkos-desktop-{name}-{}-{:?}",
            std::process::id(),
            std::time::SystemTime::now()
        ));
        std::fs::create_dir_all(&dir).expect("scratch dir");
        dir
    }

    #[cfg(unix)]
    pub fn alive(pid: u32) -> bool {
        // Signal 0 probes for existence without delivering anything.
        unsafe { libc::kill(pid as libc::pid_t, 0) == 0 }
    }

    /// Poll until `pid` is gone, up to `limit`.
    #[cfg(unix)]
    pub fn gone_within(pid: u32, limit: std::time::Duration) -> bool {
        let deadline = std::time::Instant::now() + limit;
        while std::time::Instant::now() < deadline {
            if !alive(pid) {
                return true;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        !alive(pid)
    }

    pub fn read_pid(path: &Path, limit: std::time::Duration) -> u32 {
        let deadline = std::time::Instant::now() + limit;
        loop {
            if let Ok(text) = std::fs::read_to_string(path)
                && let Ok(pid) = text.trim().parse()
            {
                return pid;
            }
            assert!(
                std::time::Instant::now() < deadline,
                "no pid at {}",
                path.display()
            );
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::fake::{self, READY};
    use super::*;

    #[test]
    fn a_one_shot_command_runs_to_completion_with_no_terminal() {
        let dir = fake::scratch("once-ok");
        // A terminal or any readable input on stdin would make `init` prompt.
        let command = fake::engine(
            "[ -t 0 ] && exit 9; IFS= read -r x && exit 8; echo done > \"$1/ran\"",
            &dir,
        );
        run_once(&command, Duration::from_secs(5)).expect("succeeded");
        assert!(dir.join("ran").is_file());
    }

    #[test]
    fn a_failed_one_shot_command_reports_its_stderr() {
        let dir = fake::scratch("once-fail");
        let command = fake::engine("echo 'refusing to initialize' >&2; exit 1", &dir);
        let err = run_once(&command, Duration::from_secs(5)).expect_err("failed");
        assert!(matches!(err, RunError::Exited { .. }), "{err:?}");
        assert!(
            err.detail().contains("refusing to initialize"),
            "{}",
            err.detail()
        );
    }

    #[test]
    fn a_one_shot_command_past_its_deadline_is_killed() {
        let dir = fake::scratch("once-slow");
        let command = fake::engine("echo $$ > \"$1/pid\"; exec sleep 30", &dir);
        let started = Instant::now();
        let err = run_once(&command, Duration::from_millis(300)).expect_err("timed out");
        assert!(matches!(err, RunError::TimedOut { .. }), "{err:?}");
        assert!(started.elapsed() < Duration::from_secs(10));
        let pid = fake::read_pid(&dir.join("pid"), Duration::from_secs(5));
        assert!(fake::gone_within(pid, Duration::from_secs(5)));
    }

    const NEVER: &dyn Fn() -> bool = &|| false;
    const SECONDS_5: Duration = Duration::from_secs(5);

    #[test]
    fn a_token_is_64_hex_characters_and_fresh_each_time() {
        let a = Token::generate();
        let b = Token::generate();
        assert_eq!(a.as_str().len(), 64);
        assert!(a.as_str().bytes().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }

    #[test]
    fn a_token_never_prints_in_debug() {
        let token = Token::generate();
        assert!(!format!("{token:?}").contains(token.as_str()));
    }

    #[test]
    fn the_ready_line_yields_the_base_url() {
        let ready = parse_ready_line(READY).expect("valid");
        assert_eq!(ready.port, 4242);
        assert_eq!(ready.base_url(), "http://127.0.0.1:4242/v1");
    }

    #[test]
    fn unknown_ready_line_keys_are_ignored() {
        let line = r#"{"event":"ready","host":"127.0.0.1","port":1,"api_version":1,"base_path":"/v1","extra":true}"#;
        assert!(parse_ready_line(line).is_ok());
    }

    #[test]
    fn a_malformed_ready_line_is_refused() {
        let cases: [(&str, ReadyLineError); 9] = [
            ("starting...", ReadyLineError::NotJson),
            ("[1]", ReadyLineError::NotJson),
            (
                r#"{"event":"started","host":"127.0.0.1","port":1,"api_version":1,"base_path":"/v1"}"#,
                ReadyLineError::NotReadyEvent,
            ),
            (
                r#"{"event":"ready","host":"0.0.0.0","port":1,"api_version":1,"base_path":"/v1"}"#,
                ReadyLineError::NotLoopback("0.0.0.0".into()),
            ),
            (
                r#"{"event":"ready","host":"127.0.0.1","port":0,"api_version":1,"base_path":"/v1"}"#,
                ReadyLineError::BadPort,
            ),
            (
                r#"{"event":"ready","host":"127.0.0.1","port":70000,"api_version":1,"base_path":"/v1"}"#,
                ReadyLineError::BadPort,
            ),
            (
                r#"{"event":"ready","host":"127.0.0.1","port":1,"api_version":1,"base_path":"v1"}"#,
                ReadyLineError::BadBasePath,
            ),
            (
                r#"{"event":"ready","host":"127.0.0.1","port":1,"api_version":2,"base_path":"/v1"}"#,
                ReadyLineError::UnsupportedApiVersion(2),
            ),
            (
                r#"{"event":"ready","port":1,"api_version":1,"base_path":"/v1"}"#,
                ReadyLineError::NotLoopback(String::new()),
            ),
        ];
        for (line, expected) in cases {
            assert_eq!(parse_ready_line(line), Err(expected), "{line}");
        }
    }

    #[test]
    fn the_token_goes_over_stdin_and_the_ready_line_is_read() {
        let dir = fake::scratch("handshake");
        let command = fake::well_behaved(&dir);
        let engine = start(&command, SECONDS_5, NEVER).expect("ready");
        assert_eq!(engine.ready().port, 4242);
        let received = std::fs::read_to_string(dir.join("token")).expect("token file");
        assert_eq!(received, engine.token().as_str());
        assert!(
            command
                .args
                .iter()
                .all(|a| !a.to_string_lossy().contains(engine.token().as_str()))
        );
        assert_eq!(engine.stop(SECONDS_5), StopOutcome::Graceful);
    }

    #[test]
    fn the_extra_environment_reaches_the_engine_and_never_its_argv() {
        let dir = fake::scratch("env");
        let script = format!(
            "IFS= read -r t; printf '%s' \"${{OPENKOS_OPENAI_API_KEY-unset}}\" > \"$1/key\"; \
             echo '{READY}'; cat > /dev/null"
        );
        let command = fake::engine(&script, &dir);
        let mut env = EngineEnv::default();
        env.set("OPENKOS_OPENAI_API_KEY", "sk-first");
        env.set("OPENKOS_OPENAI_API_KEY", "sk-test-value");
        let engine = start_with_env(&command, &env, SECONDS_5, NEVER).expect("ready");
        let received = std::fs::read_to_string(dir.join("key")).expect("key file");
        assert_eq!(received, "sk-test-value");
        assert!(
            command
                .args
                .iter()
                .all(|a| !a.to_string_lossy().contains("sk-test-value"))
        );
        assert_eq!(engine.stop(SECONDS_5), StopOutcome::Graceful);
    }

    #[test]
    fn an_engine_env_never_prints_its_values() {
        let mut env = EngineEnv::default();
        env.set("OPENKOS_OPENAI_API_KEY", "sk-secret");
        let printed = format!("{env:?}");
        assert!(printed.contains("OPENKOS_OPENAI_API_KEY"), "{printed}");
        assert!(!printed.contains("sk-secret"), "{printed}");
        assert_eq!(env.names(), ["OPENKOS_OPENAI_API_KEY"]);
    }

    #[test]
    fn closing_stdin_stops_a_well_behaved_engine_gracefully() {
        let dir = fake::scratch("graceful");
        let engine = start(&fake::well_behaved(&dir), SECONDS_5, NEVER).expect("ready");
        let pid = engine.pid();
        assert_eq!(engine.stop(SECONDS_5), StopOutcome::Graceful);
        assert!(!fake::alive(pid));
    }

    #[test]
    fn an_engine_that_ignores_eof_is_killed_with_its_whole_group() {
        let dir = fake::scratch("stubborn");
        // Ignores stdin and SIGTERM, and has a child of its own, as `uv run`
        // has the Python process.
        let script = format!(
            "trap '' TERM; IFS= read -r t; sleep 300 & echo $! > \"$1/grandchild\"; \
             echo '{READY}'; while :; do sleep 1; done"
        );
        let engine = start(&fake::engine(&script, &dir), SECONDS_5, NEVER).expect("ready");
        let pid = engine.pid();
        let grandchild = fake::read_pid(&dir.join("grandchild"), SECONDS_5);
        assert_eq!(engine.stop(Duration::from_millis(200)), StopOutcome::Killed);
        assert!(!fake::alive(pid));
        assert!(
            fake::gone_within(grandchild, SECONDS_5),
            "grandchild survived"
        );
    }

    #[test]
    fn stopping_an_engine_that_already_exited_reports_it() {
        let dir = fake::scratch("exited");
        let script = format!("IFS= read -r t; echo '{READY}'; exit 0");
        let mut engine = start(&fake::engine(&script, &dir), SECONDS_5, NEVER).expect("ready");
        let deadline = std::time::Instant::now() + SECONDS_5;
        while engine.try_exited().is_none() {
            assert!(std::time::Instant::now() < deadline, "never exited");
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(engine.stop(SECONDS_5), StopOutcome::AlreadyExited);
    }

    #[test]
    fn an_engine_that_exits_before_ready_reports_its_stderr() {
        let dir = fake::scratch("refuses");
        let script =
            "IFS= read -r t; echo 'openkos serve: refusing to serve -- no workspace.' >&2; exit 1";
        let err = start(&fake::engine(script, &dir), SECONDS_5, NEVER)
            .err()
            .expect("refused");
        assert_eq!(err.reason(), "exited_before_ready");
        assert!(
            err.detail().contains("refusing to serve"),
            "{}",
            err.detail()
        );
    }

    #[test]
    fn a_bad_ready_line_is_refused_and_the_engine_stopped() {
        let dir = fake::scratch("badline");
        let script = "IFS= read -r t; echo $$ > \"$1/pid\"; echo 'hello'; cat > /dev/null";
        let err = start(&fake::engine(script, &dir), SECONDS_5, NEVER)
            .err()
            .expect("refused");
        assert_eq!(err.reason(), "bad_ready_line");
        let pid = fake::read_pid(&dir.join("pid"), SECONDS_5);
        assert!(fake::gone_within(pid, SECONDS_5), "engine left running");
    }

    #[test]
    fn an_engine_that_never_announces_times_out_and_is_stopped() {
        let dir = fake::scratch("silent");
        let script = "IFS= read -r t; echo $$ > \"$1/pid\"; cat > /dev/null";
        let err = start(
            &fake::engine(script, &dir),
            Duration::from_millis(300),
            NEVER,
        )
        .err()
        .expect("timed out");
        assert_eq!(err.reason(), "not_ready");
        let pid = fake::read_pid(&dir.join("pid"), SECONDS_5);
        assert!(fake::gone_within(pid, SECONDS_5), "engine left running");
    }

    #[test]
    fn a_cancelled_start_returns_promptly() {
        let dir = fake::scratch("cancel");
        let script = "IFS= read -r t; cat > /dev/null";
        let began = std::time::Instant::now();
        let err = start(
            &fake::engine(script, &dir),
            Duration::from_secs(30),
            &|| true,
        )
        .err()
        .expect("cancelled");
        assert_eq!(err.reason(), "cancelled");
        assert!(began.elapsed() < SECONDS_5);
    }

    #[test]
    fn a_missing_program_is_a_spawn_failure() {
        let command = EngineCommand {
            program: "openkos-desktop-no-such-program".into(),
            args: vec![],
            cwd: None,
        };
        let err = start(&command, SECONDS_5, NEVER)
            .err()
            .expect("spawn failed");
        assert_eq!(err.reason(), "spawn_failed");
    }

    #[test]
    fn stderr_tail_keeps_the_last_lines_only() {
        let dir = fake::scratch("stderr");
        let script = format!(
            "IFS= read -r t; i=0; while [ $i -lt 200 ]; do echo \"line $i\" >&2; i=$((i+1)); done; \
             echo '{READY}'; cat > /dev/null"
        );
        let engine = start(&fake::engine(&script, &dir), SECONDS_5, NEVER).expect("ready");
        let deadline = std::time::Instant::now() + SECONDS_5;
        while !engine.stderr_tail().contains("line 199") {
            assert!(std::time::Instant::now() < deadline, "stderr never drained");
            std::thread::sleep(Duration::from_millis(10));
        }
        let tail = engine.stderr_tail();
        assert!(!tail.contains("line 0\n"), "kept too much: {tail}");
        engine.stop(SECONDS_5);
    }
}
